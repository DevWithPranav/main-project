"""Recommendations (ml/planning, M8) and the planner's decision history."""

import asyncio
import hashlib
import importlib
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..auth import Principal, current_user, require
from ..db import get_db
from ..events import iso, to_api
from ..models import Event, PlannerHistory, Recommendation, Session

router = APIRouter()
log = logging.getLogger("backend.planning")


def recommender():
    """ml.planning.recommend.recommend(events, scene) if M8 has landed, else None."""
    try:
        mod = importlib.import_module("ml.planning.recommend")
        fn = getattr(mod, "recommend", None)
        return fn if callable(fn) else None
    except Exception as e:  # noqa: BLE001 - not built yet, or broken: no recommendations
        log.info("ml.planning.recommend not available: %s", e)
        return None


def rec_id(r: dict) -> str:
    rid = r.get("id") or r.get("rec_id") or r.get("recommendation_id")
    return str(rid) if rid else "rec-" + hashlib.sha1(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()[:12]


@router.get("/recommendations")
async def recommendations(session_id: str | None = None, db: AsyncSession = Depends(get_db),
                          _: Principal = Depends(current_user)):
    fn = recommender()
    if fn is None:
        return []
    q = select(Event)
    if session_id:
        q = q.where(Event.session_id == session_id)
    events = [to_api(r) for r in (await db.execute(q)).scalars()]
    scene = None
    if session_id and (s := await db.get(Session, session_id)) and s.scene:
        p = config.SCENE_DIR / f"{s.scene}.json"
        if p.is_file():
            scene = json.loads(p.read_text(encoding="utf-8"))
    try:
        recs = await asyncio.to_thread(fn, events, scene)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"ml.planning.recommend failed: {e}")
    out = []
    for r in recs or []:
        r = {**r, "id": rec_id(r)}
        await db.merge(Recommendation(id=r["id"], session_id=session_id, data=json.loads(json.dumps(r, default=str))))
        out.append(r)
    await db.commit()
    return out


class Decision(BaseModel):
    decision: str
    rationale: str | None = None


@router.post("/recommendations/{rid}/decision")
async def decide(rid: str, body: Decision, db: AsyncSession = Depends(get_db),
                 user: Principal = Depends(require("PLANNER"))):
    if body.decision not in ("accepted", "rejected", "modified"):
        raise HTTPException(422, "decision must be accepted, rejected or modified")
    rec = await db.get(Recommendation, rid)
    h = PlannerHistory(recommendation_id=rid, decision=body.decision, rationale=body.rationale, by=user.username,
                       at=datetime.now(timezone.utc), recommendation=rec.data if rec else None)
    db.add(h)
    await db.commit()
    return hist_out(h)


def hist_out(h: PlannerHistory) -> dict:
    return {"id": h.id, "recommendation_id": h.recommendation_id, "decision": h.decision, "rationale": h.rationale,
            "by": h.by, "at": iso(h.at), "recommendation": h.recommendation, "validation": h.validation}


@router.get("/planner/history")
async def history(db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    rows = (await db.execute(select(PlannerHistory).order_by(PlannerHistory.at.desc()))).scalars()
    return [hist_out(h) for h in rows]
