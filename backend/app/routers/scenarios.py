"""What-if scenarios (PRD Objective 4, 21.3; Expected_Output 7.4): a planner's change replayed on a
session's recorded traffic and projected with the sourced countermeasure factors (ml/planning/whatif.py).

POST runs in the background (a full-flight replay takes about a minute) and returns at once with
status "running"; GET /scenarios/{id} has the result. Every scenario is kept, so several proposals for
the same road can be compared side by side.
"""

import asyncio
import importlib
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..auth import Principal, current_user, require
from ..db import SessionLocal, get_db
from ..models import Scenario, Session

router = APIRouter()
log = logging.getLogger("backend.scenarios")
_tasks: set[asyncio.Task] = set()  # keep references so running scenarios aren't garbage-collected


def whatif():
    return importlib.import_module("ml.planning.whatif")


class Change(BaseModel):
    lane_overrides: list[dict] = Field(default_factory=list)  # [{lane_id (glob), speed_limit_kmh?, restricted?}]
    zones: list[dict] = Field(default_factory=list)  # [{type: no_parking | no_u_turn, polygon: [[x, y], ...] map metres}]


class ScenarioReq(BaseModel):
    session_id: str
    name: str = Field(min_length=1, max_length=256)
    changes: Change = Field(default_factory=Change)
    countermeasure: str | None = None
    area: list[list[float]] | None = None  # polygon (map metres) the countermeasure covers


def out(s: Scenario, full: bool = True) -> dict:
    d = {"id": s.id, "name": s.name, "session_id": s.session_id, "by": s.by, "at": s.at.isoformat(),
         "status": s.status, "request": s.request, "error": s.error}
    r = s.result or {}
    d["summary"] = {k: v for k, v in {
        "replay_counted": [r["replay"]["baseline"]["counted"], r["replay"]["modified"]["counted"]] if "replay" in r else None,
        "replay_added": len(r["replay"]["added"]) if "replay" in r else None,
        "replay_removed": len(r["replay"]["removed"]) if "replay" in r else None,
        "projection_before": r["projection"]["before"]["events"] if "projection" in r else None,
        "projection_after": r["projection"].get("projected_after") if "projection" in r else None,
    }.items() if v is not None}
    if full:
        d["result"] = r
    return d


def violations_dir(s: Session):
    vdir = (s.meta or {}).get("violations_dir")
    if not vdir:
        return None
    p = config.REPO / vdir
    return p if (p / "kinematics.csv").is_file() and (p / "summary.json").is_file() else None


@router.get("/scenarios/countermeasures")
async def countermeasures(_: Principal = Depends(current_user)):
    return whatif().countermeasures()


@router.post("/scenarios", status_code=202)
async def create(body: ScenarioReq, db: AsyncSession = Depends(get_db), user: Principal = Depends(require("PLANNER"))):
    s = await db.get(Session, body.session_id)
    if s is None:
        raise HTTPException(404, f"no session {body.session_id}")
    vdir = violations_dir(s)
    if vdir is None:
        raise HTTPException(422, "this session has no recorded kinematics to replay (live sessions and old imports)")
    ch = body.changes
    if not (ch.lane_overrides or ch.zones or body.countermeasure):
        raise HTTPException(422, "nothing to simulate: give lane_overrides, zones or a countermeasure")
    if body.countermeasure and body.countermeasure not in {c["key"] for c in whatif().countermeasures()}:
        raise HTTPException(422, f"unknown countermeasure {body.countermeasure!r}")
    for z in ch.zones:
        if z.get("type") not in ("no_parking", "no_u_turn") or len(z.get("polygon") or []) < 3:
            raise HTTPException(422, "zones: type no_parking or no_u_turn and a polygon of >= 3 points")
    sc = Scenario(id="scn-" + uuid.uuid4().hex[:12], name=body.name, session_id=body.session_id, by=user.username,
                  at=datetime.now(timezone.utc), request=body.model_dump(), status="running")
    db.add(sc)
    await db.commit()

    async def run() -> None:
        try:
            res = await asyncio.to_thread(whatif().simulate, vdir, ch.model_dump(), body.countermeasure, body.area)
            status, err = "done", None
        except Exception as e:  # noqa: BLE001 - shown to the planner (e.g. a lane id that matches nothing)
            log.warning("scenario %s failed: %s", sc.id, e)
            res, status, err = None, "failed", str(e)
        async with SessionLocal() as db2:
            row = await db2.get(Scenario, sc.id)
            row.status, row.result, row.error = status, res, err
            await db2.commit()

    t = asyncio.create_task(run())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return out(sc, full=False)


@router.get("/scenarios")
async def list_(session_id: str | None = None, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    q = select(Scenario).order_by(Scenario.at.desc())
    if session_id:
        q = q.where(Scenario.session_id == session_id)
    return [out(s, full=False) for s in (await db.execute(q)).scalars()]


@router.get("/scenarios/{sid}")
async def get(sid: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    s = await db.get(Scenario, sid)
    if s is None:
        raise HTTPException(404, f"no scenario {sid}")
    return out(s)
