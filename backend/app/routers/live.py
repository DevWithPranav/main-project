"""Live vehicle state and camera frames in (service token), live event updates, and the live
WebSocket and MJPEG video out."""

import asyncio
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, bearer, current_user, principal, require
from ..db import get_db
from ..engine_bridge import condition_of, event_errors
from ..events import to_api, to_row
from ..live import hub
from ..models import Event, Session
from .events import create_event

router = APIRouter()


class VehicleState(BaseModel):
    session_id: str
    track_id: int
    cls: str | None = None
    x: float
    y: float
    speed_kmh: float | None = None
    heading_deg: float | None = None
    lane_id: str | None = None
    state: str = "ok"
    t_s: float

    def model_post_init(self, _):
        if self.state not in ("ok", "checking", "flagged"):
            raise ValueError("state must be ok, checking or flagged")


@router.post("/live/state")
async def live_state(states: list[VehicleState] = Body(...), _: Principal = Depends(require(service=True))):
    if not states:
        raise HTTPException(422, "empty list")
    n = await hub.publish_states([s.model_dump() for s in states])
    return {"received": n}


# columns an update replaces; review_* and created_at stay (a review of the provisional event is kept)
_UPDATED = ("kind", "type", "condition", "status", "cls", "confidence", "t_s", "occurred_at", "lane_id", "zone_id",
            "x", "y", "geom", "data")


@router.put("/live/events/{event_id}")
async def live_event_update(event_id: str, event: dict = Body(...), db: AsyncSession = Depends(get_db),
                            user: Principal = Depends(require(service=True))):
    """Final version of a live event. The pipeline POSTs /api/events when an event is flagged
    (provisional) and PUTs here when it closes (final status, confidence, end); POST /api/events
    answers 409 for an existing event_id. Creates the event if the first POST was lost."""
    if event.get("event_id") != event_id:
        raise HTTPException(422, "event_id in the body must match the URL")
    row = await db.get(Event, event_id)
    if row is None:
        return await create_event(event, db, user)
    if event.get("session_id") != row.session_id:
        raise HTTPException(409, f"event {event_id} belongs to session {row.session_id}")
    if event.get("kind", "violation") == "violation" and "condition" not in event:
        event = {**event, "condition": condition_of(event)}
    errs = event_errors([event])
    if errs:
        raise HTTPException(422, "; ".join(errs[:10]))
    new = to_row(event, await db.get(Session, row.session_id))
    for col in _UPDATED:
        setattr(row, col, getattr(new, col))
    await db.commit()
    out = to_api(row)
    await hub.publish_event(out)
    return out


# --- live camera frames: the pipeline posts the newest raw and annotated JPEG; pages watch them as MJPEG ---

FRAME_KINDS = ("raw", "annotated")
FRAME_MAX = 2_000_000  # bytes; a 960 px JPEG is ~100 kB
_frames: dict[tuple[str, str], dict] = {}  # (session, kind) -> {jpg, seq, t_s, wall}; newest only
_frame_cv = asyncio.Condition()


@router.post("/live/frame", status_code=204)
async def live_frame(request: Request, session_id: str, kind: str = "raw", t_s: float | None = None,
                     _: Principal = Depends(require(service=True))):
    """Body: one JPEG. Only the newest frame per session and kind is kept (a stale frame is useless)."""
    if kind not in FRAME_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(FRAME_KINDS)}")
    jpg = await request.body()
    if not jpg.startswith(b"\xff\xd8") or len(jpg) > FRAME_MAX:
        raise HTTPException(422, f"body must be a JPEG under {FRAME_MAX} bytes")
    async with _frame_cv:
        old = _frames.get((session_id, kind))
        _frames[(session_id, kind)] = {"jpg": jpg, "seq": (old["seq"] + 1) if old else 1, "t_s": t_s, "wall": time.time()}
        _frame_cv.notify_all()


@router.get("/live/sources")
async def live_sources(_: Principal = Depends(current_user)):
    """Sessions that sent a frame in the last 10 s, newest first."""
    now = time.time()
    out: dict[str, dict] = {}
    for (sid, kind), f in _frames.items():
        if now - f["wall"] < 10:
            o = out.setdefault(sid, {"session_id": sid, "kinds": [], "t_s": f["t_s"], "age_s": round(now - f["wall"], 2)})
            o["kinds"].append(kind)
            o["age_s"] = min(o["age_s"], round(now - f["wall"], 2))
    return sorted(out.values(), key=lambda o: o["age_s"])


@router.get("/live/video")
async def live_video(request: Request, session_id: str, kind: str = "raw", token: str | None = None):
    """MJPEG stream (multipart/x-mixed-replace) of a session's live frames, for an <img>. Needs a token
    (bearer header or ?token=, as <img> can't send headers); checked when the stream opens."""
    if kind not in FRAME_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(FRAME_KINDS)}")
    if await principal(bearer(request) or token) is None:
        raise HTTPException(401, "live video needs a token", headers={"WWW-Authenticate": "Bearer"})
    key = (session_id, kind)

    async def gen():
        seq = 0
        while not await request.is_disconnected():
            async with _frame_cv:
                try:
                    await asyncio.wait_for(_frame_cv.wait_for(lambda: (_frames.get(key) or {}).get("seq", 0) != seq), 5)
                except asyncio.TimeoutError:
                    continue  # nothing new: check the client is still there
                f = _frames[key]
            seq = f["seq"]
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(f["jpg"])).encode()
                   + b"\r\n\r\n" + f["jpg"] + b"\r\n")

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, token: str | None = None, session_id: str | None = None):
    if await principal(token) is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
        return
    await ws.accept()
    try:
        await hub.serve(ws, session_id)
    except WebSocketDisconnect:
        pass
