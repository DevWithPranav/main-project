"""PRD 28.4: read the append-only audit log (ADMIN). There is no write, update or delete route."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, require
from ..db import get_db
from ..models import AuditLog

router = APIRouter()


@router.get("/audit")
async def audit(user_id: str | None = None, action: str | None = None, resource_id: str | None = None,
                since: datetime | None = None, until: datetime | None = None,
                limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                db: AsyncSession = Depends(get_db), _: Principal = Depends(require("ADMIN"))):
    """Newest first. action may end in * (e.g. export.*)."""
    conds = []
    if user_id:
        conds.append(AuditLog.user_id == user_id)
    if action:
        conds.append(AuditLog.action.like(action.replace("*", "%")))
    if resource_id:
        conds.append(AuditLog.resource_id == resource_id)
    if since:
        conds.append(AuditLog.at >= since)
    if until:
        conds.append(AuditLog.at <= until)
    total = (await db.execute(select(func.count()).select_from(AuditLog).where(*conds))).scalar_one()
    rows = (await db.execute(select(AuditLog).where(*conds).order_by(AuditLog.id.desc()).limit(limit).offset(offset))).scalars()
    return {"total": total, "items": [{"id": r.id, "at": r.at.isoformat(), "user_id": r.user_id, "role": r.role, "action": r.action,
                                       "method": r.method, "path": r.path, "resource_id": r.resource_id,
                                       "ip_address": r.ip_address, "result": r.result} for r in rows]}
