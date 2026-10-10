"""Violation events (Violation Engine, Layer 6).

docs/Violation_Engine_Architecture.md, Section 5.7. One event per episode: it opens when
a rule's time condition is met (flag time) and closes when the condition has stayed false
long enough. Fields mirror the PRD's ViolationEvent so the backend can store them as is.

status:
    flagged           confidence >= the PRD minimum for its type
    needs_review      below that minimum (kept, shown to the reviewer)
    suppressed        an edge case the PRD says not to flag (queue, three-point turn); kept for audit
    possible_breakdown  highway stop alone on the shoulder (PRD 10, edge case 2)
"""

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# PRD Section 9.1 confidence thresholds
MIN_CONFIDENCE = {"no_parking": 0.90, "wrong_way": 0.85, "illegal_u_turn": 0.82, "speeding": 0.85,
                  "lane_violation": 0.80, "zebra_crossing": 0.85, "highway_stop": 0.88, "red_light": 0.92}
COUNTED_STATUS = {"flagged", "needs_review"}  # statuses that count as a detected violation in evaluation


@dataclass
class Event:
    event_id: str
    type: str
    track_ids: list[int]
    cls: str
    start_s: float
    start_frame: int
    flag_s: float
    flag_frame: int
    end_s: float | None = None
    end_frame: int | None = None
    lane_id: str | None = None
    zone_id: str | None = None
    x: float = 0.0
    y: float = 0.0
    value: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "flagged"
    evidence: dict = field(default_factory=dict)  # {"frame", "clip"}: filled by render_violations.py --clips
    condition: str | None = None  # Expected_Output 4.2 id (schemas/conditions.json), set by Engine.run
    kind: str = "violation"  # schemas/event.schema.json: "violation" or "anomaly"

    def close(self, t: float, frame: int, margin: float, quality: float, duration_score: float) -> None:
        """Set the end and the confidence: a weighted mean of how far past the threshold (margin),
        track quality and how much longer than the minimum time, all 0..1. To be calibrated on CARLA."""
        self.end_s, self.end_frame = round(t, 3), frame
        self.confidence = round(0.4 * _clip(margin) + 0.3 * _clip(quality) + 0.3 * _clip(duration_score), 3)
        if self.status == "flagged" and self.confidence < MIN_CONFIDENCE.get(self.type, 0.0):
            self.status = "needs_review"


def _clip(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


class EventLog:
    def __init__(self, prefix: str = "ev"):
        self.prefix = prefix
        self.events: list[Event] = []

    def open(self, **kw) -> Event:
        ev = Event(event_id=f"{self.prefix}-{len(self.events) + 1:04d}", **kw)
        self.events.append(ev)
        return ev


CSV_COLS = ["event_id", "type", "condition", "status", "confidence", "track_ids", "cls", "start_s", "flag_s", "end_s",
            "start_frame", "flag_frame", "end_frame", "lane_id", "zone_id", "x", "y", "value", "tags", "evidence"]


def write_events(events: list[Event], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "violations.json"
    js.write_text(json.dumps([asdict(e) for e in events], indent=1))
    return js, _write_csv([asdict(e) for e in events], out_dir / "violations.csv")


def _write_csv(events: list[dict], cs: Path) -> Path:
    with open(cs, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for d in events:
            d = dict(d)
            d["track_ids"] = " ".join(map(str, d["track_ids"]))
            d["value"] = json.dumps(d["value"])
            d["tags"] = " ".join(d["tags"])
            d["evidence"] = d.get("evidence", {}).get("clip", "")
            w.writerow({k: d.get(k) for k in CSV_COLS})
    return cs


def rewrite_events(out_dir: Path, events: list[dict]) -> None:
    """Write events loaded from violations.json (dicts) back to violations.json / .csv."""
    (out_dir / "violations.json").write_text(json.dumps(events, indent=1))
    _write_csv(events, out_dir / "violations.csv")


def attach_evidence(out_dir: Path, evidence: dict[str, dict]) -> None:
    """Write evidence (event_id -> {"frame", "clip"}) into an existing violations.json / .csv."""
    events = json.loads((out_dir / "violations.json").read_text())
    for d in events:
        if d["event_id"] in evidence:
            d["evidence"] = evidence[d["event_id"]]
    rewrite_events(out_dir, events)
