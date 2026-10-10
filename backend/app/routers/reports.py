"""Report history (PRD 13.1 `reports`, 14.1): every generated export is recorded (who, when, format,
filters, size) and its file kept in S3 under reports/, so it can be listed and downloaded again.

GET /export (routers/events.py) and POST /reports/generate both go through `build` + `record`.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config, exports, storage
from ..auth import Principal, current_user
from ..db import get_db
from ..events import EventFilters, base_query, event_filters, iso, to_api
from ..models import Event, Report

router = APIRouter()
log = logging.getLogger("backend.reports")
MEDIA = {"csv": ("text/csv", "csv"), "geojson": ("application/geo+json", "geojson"),
         "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
         "pdf": ("application/pdf", "pdf")}
FORMAT = "^(pdf|xlsx|geojson|csv)$"


async def build(db: AsyncSession, format: str, f: EventFilters) -> dict:
    """The export file of the filtered events: {body, media, filename, n_events, n_total, filters, seconds}."""
    t0 = time.perf_counter()
    events = [to_api(r) for r in (await db.execute(f.apply(base_query()).limit(config.EXPORT_MAX_ROWS))).scalars()]
    total = (await db.execute(f.apply(select(func.count()).select_from(Event)))).scalar_one()
    fdict = {k: (iso(v) if isinstance(v, datetime) else v) for k, v in asdict(f).items()}
    if format == "csv":
        body = exports.to_csv(events)
    elif format == "geojson":
        body = exports.to_geojson(events)
    elif format == "xlsx":
        body = await asyncio.to_thread(exports.to_xlsx, events, fdict)
    else:
        body = await asyncio.to_thread(exports.to_pdf, events, fdict)
    media, ext = MEDIA[format]
    return {"body": body, "media": media, "filename": f"events_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.{ext}",
            "n_events": len(events), "n_total": total, "filters": fdict, "seconds": time.perf_counter() - t0}


async def record(db: AsyncSession, format: str, out: dict, user: Principal) -> Report | None:
    """Store the file in S3 and a reports row. Never fails the export: an S3 error leaves storage_key
    empty (listed, not downloadable); a database error is logged and gives None."""
    now = datetime.now(timezone.utc)
    rid = uuid.uuid4().hex
    key = f"reports/{now:%Y/%m}/{rid}_{out['filename']}"
    try:
        await asyncio.to_thread(storage.put_bytes, out["body"], key, out["media"])
    except Exception as e:  # noqa: BLE001
        log.warning("report %s not stored in S3: %s", rid, e)
        key = None
    rep = Report(id=rid, format=format, filename=out["filename"], content_type=out["media"],
                 filters={k: v for k, v in out["filters"].items() if v is not None}, session_id=out["filters"].get("session_id"),
                 n_events=out["n_events"], n_total=out["n_total"], size_bytes=len(out["body"]), storage_key=key,
                 seconds=round(out["seconds"], 3), by=user.username, role=user.role, at=now)
    try:
        db.add(rep)
        await db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("report %s not recorded: %s", rid, e)
        await db.rollback()
        return None
    return rep


def meta(r: Report) -> dict:
    return {"id": r.id, "format": r.format, "filename": r.filename, "content_type": r.content_type, "filters": r.filters,
            "session_id": r.session_id, "n_events": r.n_events, "n_total": r.n_total, "size_bytes": r.size_bytes,
            "seconds": r.seconds, "by": r.by, "role": r.role, "at": iso(r.at), "stored": r.storage_key is not None,
            "download_url": f"/api/reports/{r.id}"}


@router.post("/reports/generate", status_code=201)
async def generate(format: str = Query(..., pattern=FORMAT), f: EventFilters = Depends(event_filters),
                   db: AsyncSession = Depends(get_db), user: Principal = Depends(current_user)):
    """Like GET /export (same query), but returns the report record; download it from download_url."""
    rep = await record(db, format, await build(db, format, f), user)
    if rep is None:
        raise HTTPException(503, "report could not be recorded")
    return meta(rep)


@router.get("/reports")
async def list_reports(format: str | None = Query(None, pattern=FORMAT), by: str | None = None,
                       session_id: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                       db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    """Generated reports, newest first: {total, items}."""
    conds = [c for c, v in ((Report.format == format, format), (Report.by == by, by),
                            (Report.session_id == session_id, session_id)) if v]
    total = (await db.execute(select(func.count()).select_from(Report).where(*conds))).scalar_one()
    rows = (await db.execute(select(Report).where(*conds).order_by(Report.at.desc(), Report.id)
                             .offset(offset).limit(limit))).scalars()
    return {"total": total, "items": [meta(r) for r in rows]}


@router.get("/reports/{report_id}")
async def download_report(report_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    r = await db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, f"no report {report_id}")
    if r.storage_key is None:
        raise HTTPException(410, f"report {report_id} was not stored (S3 was unavailable when it was made)")
    try:
        obj = await asyncio.to_thread(storage.get, r.storage_key)
    except Exception:
        raise HTTPException(410, f"report file {r.storage_key} is gone from the store")
    return StreamingResponse(obj["Body"].iter_chunks(1 << 16), media_type=r.content_type, headers={
        "Content-Disposition": f'attachment; filename="{r.filename}"', "Content-Length": str(obj["ContentLength"]),
        "X-Report-Id": r.id})
