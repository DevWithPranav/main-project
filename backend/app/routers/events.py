"""Events: list / one / create (live), review, stats, exports."""

import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, current_user, require
from ..db import get_db
from ..engine_bridge import condition_of, event_errors
from ..events import EventFilters, base_query, event_filters, iso, to_api, to_row
from ..live import hub
from . import reports
from ..models import Event, Review, Session

router = APIRouter()
HOTSPOT_CELL_M = 25.0  # hotspot grid cell; about one junction / road segment in CARLA towns


async def filtered(db: AsyncSession, f: EventFilters, limit: int | None = None, offset: int = 0) -> list[Event]:
    q = f.apply(base_query()).offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return list((await db.execute(q)).scalars())


@router.get("/events")
async def list_events(f: EventFilters = Depends(event_filters), limit: int = Query(200, ge=1, le=5000),
                      offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db),
                      _: Principal = Depends(current_user)):
    total = (await db.execute(f.apply(select(func.count()).select_from(Event)))).scalar_one()
    return {"total": total, "items": [to_api(r) for r in await filtered(db, f, limit, offset)]}


@router.get("/events/{event_id}")
async def get_event(event_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    r = await db.get(Event, event_id)
    if r is None:
        raise HTTPException(404, f"no event {event_id}")
    return to_api(r)


@router.post("/events", status_code=201)
async def create_event(event: dict = Body(...), db: AsyncSession = Depends(get_db),
                       user: Principal = Depends(require("OPERATOR", service=True))):
    """One event from the live pipeline. Needs session_id; an unknown session is created as live."""
    sid = event.get("session_id")
    if not sid:
        raise HTTPException(422, "session_id is required")
    if event.get("kind", "violation") == "violation" and "condition" not in event:
        event = {**event, "condition": condition_of(event)}
    errs = event_errors([event])
    if errs:
        raise HTTPException(422, "; ".join(errs[:10]))
    if await db.get(Event, event["event_id"]) is not None:
        raise HTTPException(409, f"event {event['event_id']} exists")
    s = await db.get(Session, sid)
    if s is None:
        s = Session(session_id=sid, name=f"Live session {sid}", source="live", started_at=datetime.now(timezone.utc),
                    t0_s=float(event.get("start_s", event.get("t_s", 0.0))), meta={"created_by": user.username})
        db.add(s)
        await db.flush()
    row = to_row(event, s)
    db.add(row)
    await db.commit()
    out = to_api(row)
    await hub.publish_event(out)
    return out


class ReviewReq(BaseModel):
    outcome: str
    note: str | None = None


@router.post("/events/{event_id}/review")
async def review_event(event_id: str, body: ReviewReq, db: AsyncSession = Depends(get_db),
                       user: Principal = Depends(current_user)):
    r = await db.get(Event, event_id)
    if r is None:
        raise HTTPException(404, f"no event {event_id}")
    allowed = {"MAINTENANCE", "ADMIN"} if r.kind == "anomaly" else {"OFFICER", "ADMIN"}
    if user.role not in allowed:
        raise HTTPException(403, f"role {user.role} may not review a {r.kind} (needs {', '.join(sorted(allowed))})")
    if body.outcome not in ("confirmed", "dismissed"):
        raise HTTPException(422, "outcome must be confirmed or dismissed")
    at = datetime.now(timezone.utc)
    rev = {"outcome": body.outcome, "by": user.username, "at": iso(at)}
    if body.note:
        rev["note"] = body.note
    db.add(Review(event_id=event_id, outcome=body.outcome, note=body.note, by=user.username, at=at))
    r.review_outcome, r.review = body.outcome, rev
    await db.commit()
    return to_api(r)


@router.get("/events/{event_id}/reviews")
async def review_history(event_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    rows = (await db.execute(select(Review).where(Review.event_id == event_id).order_by(Review.at))).scalars()
    return [{"outcome": r.outcome, "note": r.note, "by": r.by, "at": iso(r.at)} for r in rows]


@router.get("/stats")
async def stats(f: EventFilters = Depends(event_filters), db: AsyncSession = Depends(get_db),
                _: Principal = Depends(current_user)):
    rows = await filtered(db, f)
    by_hour = Counter(r.occurred_at.astimezone(timezone.utc).strftime("%H") for r in rows)
    cells: dict[tuple, list[Event]] = defaultdict(list)
    for r in rows:
        cells[(math.floor(r.x / HOTSPOT_CELL_M), math.floor(r.y / HOTSPOT_CELL_M))].append(r)
    hot = sorted(cells.values(), key=len, reverse=True)[:10]
    return {
        "total": len(rows),
        "by_type": dict(Counter(r.type for r in rows)),
        "by_condition": dict(Counter(r.condition or "none" for r in rows)),
        "by_status": dict(Counter(r.status for r in rows)),
        "by_review": dict(Counter(r.review_outcome or "none" for r in rows)),
        "by_hour": {f"{h:02d}": by_hour.get(f"{h:02d}", 0) for h in range(24)},
        "hotspots": [{"x": round(sum(e.x for e in c) / len(c), 2), "y": round(sum(e.y for e in c) / len(c), 2),
                      "cell_m": HOTSPOT_CELL_M, "count": len(c), "by_type": dict(Counter(e.type for e in c)),
                      "lane_ids": sorted({e.lane_id for e in c if e.lane_id})[:5]} for c in hot],
    }


@router.get("/export")
async def export(format: str = Query(..., pattern="^(pdf|xlsx|geojson|csv)$"),
                 f: EventFilters = Depends(event_filters), db: AsyncSession = Depends(get_db),
                 user: Principal = Depends(current_user)):
    """The file itself; also recorded in the report history (routers/reports.py: S3 + reports row)."""
    t0 = time.perf_counter()
    out = await reports.build(db, format, f)
    rep = await reports.record(db, format, out, user)
    return Response(out["body"], media_type=out["media"], headers={
        "Content-Disposition": f'attachment; filename="{out["filename"]}"', "X-Event-Count": str(out["n_events"]),
        "X-Event-Total": str(out["n_total"]), "X-Export-Seconds": f"{time.perf_counter() - t0:.3f}",
        "X-Report-Id": rep.id if rep else ""})
