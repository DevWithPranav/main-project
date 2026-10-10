"""Sessions: list, one, import of a processed recorded flight, trajectories."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config, ingest
from ..auth import Principal, current_user, require
from ..db import get_db
from ..events import iso, to_row
from ..models import Event, Session, Track

router = APIRouter()


def session_out(s: Session, n_events: int) -> dict:
    return {"session_id": s.session_id, "name": s.name, "source": s.source, "town": s.town, "flight": s.flight,
            "started_at": iso(s.started_at), "n_events": n_events, "profile": s.profile, "scene": s.scene}


async def n_events(db: AsyncSession, sid: str) -> int:
    return (await db.execute(select(func.count()).select_from(Event).where(Event.session_id == sid))).scalar_one()


@router.get("/sessions")
async def list_sessions(db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    counts = dict((await db.execute(select(Event.session_id, func.count()).group_by(Event.session_id))).all())
    rows = (await db.execute(select(Session).order_by(Session.started_at.desc()))).scalars()
    return [session_out(s, counts.get(s.session_id, 0)) for s in rows]


@router.get("/sessions/{sid}")
async def get_session(sid: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    s = await db.get(Session, sid)
    if s is None:
        raise HTTPException(404, f"no session {sid}")
    out = session_out(s, await n_events(db, sid))
    out["import"] = s.meta or {}
    return out


@router.get("/sessions/{sid}/trajectories")
async def trajectories(sid: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    if await db.get(Session, sid) is None:
        raise HTTPException(404, f"no session {sid}")
    rows = (await db.execute(select(Track.track_id, Track.points).where(Track.session_id == sid))).all()
    return {str(tid): pts for tid, pts in rows}


class ImportReq(BaseModel):
    flight: str
    violations_dir: str | None = None
    scene: str | None = None
    profile: str | None = None


@router.post("/sessions/import")
async def import_flight(body: ImportReq, db: AsyncSession = Depends(get_db),
                        user: Principal = Depends(require("OPERATOR"))):
    t_start = datetime.now(timezone.utc)
    fdir = ingest.flight_dir(body.flight)
    vdir = ingest.pick_violations_dir(fdir, body.violations_dir)
    raw = ingest.load_events(vdir)
    events, errors = ingest.validate(raw)
    clips = await asyncio.to_thread(ingest.upload_evidence, events, vdir, body.flight)
    tracks, t0 = await asyncio.to_thread(ingest.load_tracks, vdir)
    if t0 is None:
        t0 = min((e.get("start_s", e.get("t_s", 0.0)) for e in events), default=0.0)
    town = ingest.town_of(body.flight)
    scene = body.scene or (town if town and (config.SCENE_DIR / f"{town}.json").is_file() else None)
    meta = {"violations_dir": vdir.relative_to(config.REPO).as_posix() if config.REPO in vdir.parents else str(vdir),
            "n_in_file": len(raw), "n_imported": len(events), "n_rejected": len(raw) - len(events),
            "errors": errors[:50], "clips_uploaded": clips,
            "clips": sum(bool((e.get("evidence") or {}).get("clip_key")) for e in events),
            "n_tracks": len(tracks), "imported_by": user.username, "imported_at": t_start.isoformat()}
    s = await db.get(Session, body.flight)
    if s is None:
        s = Session(session_id=body.flight)
        db.add(s)
    s.name, s.source, s.town, s.flight = f"{town or 'CARLA'} flight {body.flight}", "carla", town, body.flight
    s.started_at, s.t0_s, s.profile, s.scene, s.meta = ingest.flight_start(body.flight), t0, body.profile, scene, meta
    await db.flush()
    # Re-import replaces the events (reviews of events that are still there are kept) and tracks.
    keep = {e["event_id"] for e in events}
    old = {r.event_id: r for r in (await db.execute(select(Event).where(Event.session_id == s.session_id))).scalars()}
    taken = set((await db.execute(select(Event.event_id).where(Event.event_id.in_(keep),
                                                                Event.session_id != s.session_id))).scalars())
    for eid, r in old.items():
        if eid not in keep:
            await db.delete(r)
    for e in events:
        if e["event_id"] in taken:
            meta["n_rejected"] += 1
            meta["errors"].append(f"{e['event_id']}: event_id already used by another session")
            continue
        row = to_row(e, s)
        if e["event_id"] in old:
            prev = old[e["event_id"]]
            row.review_outcome, row.review = prev.review_outcome, prev.review
            await db.merge(row)
        else:
            db.add(row)
    meta["n_imported"] = len(events) - len(taken)
    await db.execute(delete(Track).where(Track.session_id == s.session_id))
    db.add_all(Track(session_id=s.session_id, track_id=tid, cls=t["cls"], points=t["points"]) for tid, t in tracks.items())
    s.meta = dict(meta)
    await db.commit()
    out = session_out(s, await n_events(db, s.session_id))
    out["import"] = meta
    return out
