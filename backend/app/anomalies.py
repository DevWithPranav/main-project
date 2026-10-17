"""Road-surface anomalies across sessions (Build Plan M9, PRD road_anomalies + FAQ on duplicates).

Each session keeps its own anomaly events (one per defect per flight, already de-duplicated over
frames by ml/pothole/detect_anomalies.py). A physical defect seen on several flights is grouped
here, the PRD way: same type, within DEDUP_RADIUS_M (PRD FAQ: ST_DWithin, default 3 m), in the same
map frame (town; sessions without a town have their own frame, e.g. a video's image metres).

    defects()      groups -> the condition inventory (GET /api/anomalies/inventory)
    recurrence()   for an imported session: 1 + earlier sessions that saw the same defect
                   (the event's recurrence_count, PRD "existing defect -> UPDATE recurrence_count")
"""

import math
from collections import defaultdict

from .events import iso, to_api
from .models import Event, Session

DEDUP_RADIUS_M = 3.0  # PRD FAQ: ST_DWithin default 3 m radius plus type match
STATUSES = ("flagged", "reviewed", "work_order_issued", "repaired")  # schemas/event.schema.json anomaly.status
BANDS = ("low", "medium", "high")


def frame_key(s: Session) -> str:
    return f"town:{s.town}" if s.town else f"session:{s.session_id}"


def _group(rows: list[tuple[Event, Session]], radius: float) -> list[list[tuple[Event, Session]]]:
    """Greedy single-link groups by (map frame, type) within radius, oldest event first."""
    rows = sorted(rows, key=lambda r: (r[0].occurred_at, r[0].event_id))
    groups: list[list[tuple[Event, Session]]] = []
    index: dict[tuple, list[int]] = defaultdict(list)
    for e, s in rows:
        key = (frame_key(s), e.type)
        hit = None
        for gi in index[key]:
            if any(math.hypot(e.x - o.x, e.y - o.y) <= radius for o, _ in groups[gi]):
                hit = gi
                break
        if hit is None:
            index[key].append(len(groups))
            groups.append([(e, s)])
        else:
            groups[hit].append((e, s))
    return groups


def defect_status(g: list[tuple[Event, Session]]) -> str:
    """The most advanced maintenance status of the defect's sightings; but a sighting made after the
    repaired one (the defect is back) makes it "flagged" again."""
    rank = {s: i for i, s in enumerate(STATUSES)}
    best = max(g, key=lambda r: rank.get(r[0].status, 0))[0]
    if best.status == "repaired" and any(e.occurred_at > best.occurred_at and e.status == "flagged" for e, _ in g):
        return "flagged"
    return best.status


def defects(rows: list[tuple[Event, Session]], radius: float = DEDUP_RADIUS_M) -> list[dict]:
    """The condition inventory: one item per physical defect, highest current severity first."""
    out = []
    for g in _group(rows, radius):
        latest_e, latest_s = g[-1]
        sessions = sorted({s.session_id for _, s in g})
        first = min(e.occurred_at for e, _ in g)
        last = max(e.occurred_at for e, _ in g)
        api_latest = to_api(latest_e)
        out.append({
            "defect_id": g[0][0].event_id,
            "type": latest_e.type,
            "town": latest_s.town,
            "x": round(sum(e.x for e, _ in g) / len(g), 2),
            "y": round(sum(e.y for e, _ in g) / len(g), 2),
            "severity_score": latest_e.data.get("severity_score"),
            "severity_band": latest_e.data.get("severity_band"),
            "max_severity_score": max(e.data.get("severity_score", 0.0) for e, _ in g),
            "area_sq_m": latest_e.data.get("area_sq_m"),
            "status": defect_status(g),
            "confidence": latest_e.confidence,
            "first_detected_at": iso(first),
            "last_seen_at": iso(last),
            "recurrence_count": len(sessions),
            "session_ids": sessions,
            "event_ids": [e.event_id for e, _ in g],
            "latest_event_id": latest_e.event_id,
            "snapshot_url": (api_latest.get("evidence") or {}).get("snapshot_url"),
            "severity_trend": [{"session_id": s.session_id, "at": iso(e.occurred_at),
                                "severity_score": e.data.get("severity_score")} for e, s in g],
        })
    out.sort(key=lambda d: (-(d["severity_score"] or 0.0), d["defect_id"]))
    return out


def recurrence(new: list[dict], session: Session, prior: list[tuple[Event, Session]],
               radius: float = DEDUP_RADIUS_M) -> dict[str, int]:
    """event_id -> 1 + number of earlier sessions (same map frame) with a same-type anomaly within
    radius of it. `prior`: anomaly rows of other sessions."""
    key = frame_key(session)
    out = {}
    for e in new:
        seen = {s.session_id for o, s in prior
                if s.session_id != session.session_id and frame_key(s) == key and o.type == e["type"]
                and s.started_at <= session.started_at and math.hypot(o.x - e["x"], o.y - e["y"]) <= radius}
        out[e["event_id"]] = 1 + len(seen)
    return out
