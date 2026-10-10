"""Live vehicle state in (service token), live event updates, and the live WebSocket out."""

from fastapi import APIRouter, Body, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, principal_from_token, require
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


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, token: str | None = None, session_id: str | None = None):
    if principal_from_token(token) is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
        return
    await ws.accept()
    try:
        await hub.serve(ws, session_id)
    except WebSocketDisconnect:
        pass
