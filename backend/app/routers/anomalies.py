"""Road-surface anomalies (Build Plan M9; PRD /anomalies routes): list, condition inventory (+ work-order
export), one anomaly, maintenance status updates with history."""

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import anomalies as an
from ..auth import Principal, current_user, require
from ..db import get_db
from ..events import iso, to_api
from ..models import AnomalyStatus, Event, Session

router = APIRouter()


def _csv(v: str | None) -> list[str] | None:
    return [s.strip() for s in v.split(",") if s.strip()] if v else None


async def anomaly_rows(db: AsyncSession, session_id: str | None = None, type: str | None = None,
                       status: str | None = None) -> list[tuple[Event, Session]]:
    conds = [Event.kind == "anomaly"]
    for col, val in ((Event.session_id, session_id), (Event.type, type), (Event.status, status)):
        vals = _csv(val)
        if vals:
            conds.append(col.in_(vals))
    q = select(Event, Session).join(Session, Session.session_id == Event.session_id).where(and_(*conds))
    return [(e, s) for e, s in (await db.execute(q)).all()]


def _band_ok(score: float | None, band: str | None, bands: list[str] | None, min_sev: float | None) -> bool:
    if bands and band not in bands:
        return False
    return min_sev is None or (score is not None and score >= min_sev)


@router.get("/anomalies")
async def list_anomalies(session_id: str | None = None, type: str | None = None, status: str | None = None,
                         severity_band: str | None = Query(None, description="low,medium,high"),
                         min_severity: float | None = Query(None, ge=0, le=1),
                         limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0),
                         db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    """Anomaly events (one per defect per session), highest severity first."""
    bands = _csv(severity_band)
    rows = [e for e, _ in await anomaly_rows(db, session_id, type, status)
            if _band_ok(e.data.get("severity_score"), e.data.get("severity_band"), bands, min_severity)]
    rows.sort(key=lambda e: (-(e.data.get("severity_score") or 0.0), e.event_id))
    return {"total": len(rows), "items": [to_api(e) for e in rows[offset:offset + limit]]}


INV_COLUMNS = ["rank", "defect_id", "type", "town", "x", "y", "severity_score", "severity_band", "max_severity_score",
               "area_sq_m", "status", "recurrence_count", "first_detected_at", "last_seen_at", "latest_event_id",
               "snapshot_url"]


def _inventory_xlsx(items: list[dict]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Work orders"
    ws.append(INV_COLUMNS)
    for i, d in enumerate(items, 1):
        ws.append([i] + [d.get(c) for c in INV_COLUMNS[1:]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _inventory_csv(items: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(INV_COLUMNS)
    for i, d in enumerate(items, 1):
        w.writerow([i] + [d.get(c) for c in INV_COLUMNS[1:]])
    return buf.getvalue().encode("utf-8-sig")


@router.get("/anomalies/inventory")
async def inventory(type: str | None = None, status: str | None = None,
                    severity_band: str | None = Query(None, description="low,medium,high"),
                    min_severity: float | None = Query(None, ge=0, le=1), town: str | None = None,
                    format: str = Query("json", pattern="^(json|csv|xlsx)$"),
                    db: AsyncSession = Depends(get_db), _: Principal = Depends(require("MAINTENANCE", "PLANNER"))):
    """Severity-ranked condition inventory: one item per physical defect over all sessions (same type
    within 3 m, PRD). Filters apply to each defect's latest sighting. format=csv|xlsx: the work-order list."""
    items = an.defects(await anomaly_rows(db, type=type))
    bands, statuses, towns = _csv(severity_band), _csv(status), _csv(town)
    items = [d for d in items if _band_ok(d["severity_score"], d["severity_band"], bands, min_severity)
             and (not statuses or d["status"] in statuses) and (not towns or d["town"] in towns)]
    if format == "json":
        return {"total": len(items), "radius_m": an.DEDUP_RADIUS_M, "items": items}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    body, media = ((_inventory_csv(items), "text/csv") if format == "csv" else
                   (_inventory_xlsx(items), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    return Response(body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="work_orders_{stamp}.{format}"', "X-Event-Count": str(len(items))})


@router.get("/anomalies/stats")
async def anomaly_stats(session_id: str | None = None, db: AsyncSession = Depends(get_db),
                        _: Principal = Depends(current_user)):
    """Counts for the maintenance dashboard: by type x band, by status, defects (cross-session)."""
    rows = await anomaly_rows(db, session_id)
    by = {}
    for e, _ in rows:
        t = by.setdefault(e.type, {b: 0 for b in an.BANDS})
        b = e.data.get("severity_band")
        if b in t:
            t[b] += 1
    status = {s: sum(e.status == s for e, _ in rows) for s in an.STATUSES}
    return {"total": len(rows), "by_type_band": by, "by_status": status, "defects": len(an.defects(rows)),
            "mean_severity": round(sum(e.data.get("severity_score", 0.0) for e, _ in rows) / len(rows), 4) if rows else None}


async def _anomaly(db: AsyncSession, event_id: str) -> Event:
    r = await db.get(Event, event_id)
    if r is None or r.kind != "anomaly":
        raise HTTPException(404, f"no anomaly {event_id}")
    return r


async def _history(db: AsyncSession, event_id: str) -> list[dict]:
    rows = (await db.execute(select(AnomalyStatus).where(AnomalyStatus.event_id == event_id)
                             .order_by(AnomalyStatus.at, AnomalyStatus.id))).scalars()
    return [{"from_status": h.from_status, "to_status": h.to_status, "note": h.note, "by": h.by, "at": iso(h.at)} for h in rows]


@router.get("/anomalies/{event_id}")
async def get_anomaly(event_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    """One anomaly + its status history + the defect it belongs to (other sessions' sightings)."""
    r = await _anomaly(db, event_id)
    out = to_api(r)
    out["status_history"] = await _history(db, event_id)
    rows = await anomaly_rows(db, type=r.type)
    out["defect"] = next((d for d in an.defects(rows) if event_id in d["event_ids"]), None)
    return out


class StatusReq(BaseModel):
    status: str
    note: str | None = None


@router.patch("/anomalies/{event_id}")
async def set_status(event_id: str, body: StatusReq, db: AsyncSession = Depends(get_db),
                     user: Principal = Depends(require("MAINTENANCE"))):
    """Maintenance status: flagged | reviewed | work_order_issued | repaired (any order: a repair can
    be reopened). Recorded with who / when / note."""
    if body.status not in an.STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(an.STATUSES)}")
    r = await _anomaly(db, event_id)
    at = datetime.now(timezone.utc)
    db.add(AnomalyStatus(event_id=event_id, from_status=r.status, to_status=body.status, note=body.note,
                         by=user.username, at=at))
    r.status = body.status
    r.data = {**r.data, "status": body.status}
    await db.commit()
    out = to_api(r)
    out["status_history"] = await _history(db, event_id)
    return out


@router.get("/anomalies/{event_id}/history")
async def status_history(event_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    await _anomaly(db, event_id)
    return await _history(db, event_id)
