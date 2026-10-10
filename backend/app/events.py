"""Event rows <-> API objects, and the filters shared by /events, /stats and /export."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Query
from geoalchemy2.elements import WKTElement
from sqlalchemy import Select, and_, func, or_, select

from .models import Event, Session
from .storage import file_url

STRIP = ("session_id", "review")  # stored in their own columns / table


def to_row(ev: dict, session: Session) -> Event:
    """An Event row from a schema-valid event dict (already validated)."""
    data = {k: v for k, v in ev.items() if k not in STRIP}
    kind = data.get("kind", "violation")
    t = data["t_s"] if kind == "anomaly" else data["flag_s"]
    return Event(event_id=data["event_id"], session_id=session.session_id, kind=kind, type=data["type"],
                 condition=data.get("condition"), status=data["status"], cls=data.get("cls"),
                 confidence=float(data["confidence"]), t_s=float(t),
                 occurred_at=session.started_at + timedelta(seconds=max(0.0, float(t) - (session.t0_s or 0.0))),
                 lane_id=data.get("lane_id"), zone_id=data.get("zone_id"), x=float(data["x"]), y=float(data["y"]),
                 geom=WKTElement(f"POINT({float(data['x'])} {float(data['y'])})", srid=0), data=data)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_api(row: Event) -> dict:
    """The event as API.md has it: the schema fields + session_id + review, evidence keys as URLs."""
    out = dict(row.data)
    out["session_id"] = row.session_id
    if row.review:
        out["review"] = row.review
    ev = dict(out.get("evidence") or {})
    for k in ("clip", "snapshot"):
        if ev.get(f"{k}_key"):
            ev[f"{k}_url"] = file_url(ev[f"{k}_key"])
    out["evidence"] = ev
    out["occurred_at"] = iso(row.occurred_at)
    return out


def _csv(v: str | None) -> list[str] | None:
    return [s.strip() for s in v.split(",") if s.strip()] if v else None


@dataclass
class EventFilters:
    session_id: str | None = None
    type: str | None = None
    condition: str | None = None
    status: str | None = None
    review: str | None = None
    kind: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    bbox: str | None = None

    def apply(self, q: Select) -> Select:
        conds = []
        for col, val in ((Event.session_id, self.session_id), (Event.type, self.type),
                         (Event.condition, self.condition), (Event.status, self.status), (Event.kind, self.kind)):
            vals = _csv(val)
            if vals:
                conds.append(col.in_(vals))
        if self.review:
            r = _csv(self.review)
            bad = set(r) - {"none", "confirmed", "dismissed"}
            if bad:
                raise HTTPException(422, f"review must be none, confirmed or dismissed, not {', '.join(bad)}")
            parts = [Event.review_outcome.in_([x for x in r if x != "none"])]
            if "none" in r:
                parts.append(Event.review_outcome.is_(None))
            conds.append(or_(*parts))
        if self.since:
            conds.append(Event.occurred_at >= _utc(self.since))
        if self.until:
            conds.append(Event.occurred_at <= _utc(self.until))
        if self.bbox:
            try:
                x0, y0, x1, y1 = (float(v) for v in self.bbox.split(","))
            except ValueError:
                raise HTTPException(422, "bbox must be x0,y0,x1,y1 (map metres)")
            env = func.ST_MakeEnvelope(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), 0)
            conds.append(func.ST_Intersects(Event.geom, env))
        return q.where(and_(*conds)) if conds else q


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def event_filters(session_id: str | None = None, type: str | None = None, condition: str | None = None,
                  status: str | None = None, review: str | None = Query(None, description="none, confirmed, dismissed"),
                  kind: str | None = None, since: datetime | None = None, until: datetime | None = None,
                  bbox: str | None = Query(None, description="x0,y0,x1,y1 in map metres")) -> EventFilters:
    return EventFilters(session_id, type, condition, status, review, kind, since, until, bbox)


def base_query() -> Select:
    return select(Event).order_by(Event.occurred_at, Event.event_id)
