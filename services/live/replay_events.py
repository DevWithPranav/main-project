"""Re-sends a live session's events.jsonl to the backend (PRD Mode 5: offline replay).

The pipeline writes every event it opens and closes to events.jsonl whether or not the backend was
reachable. After an outage (or a run with no --backend) this sends them: each event is created with
POST /api/events (409 = already stored, fine) and, if it was closed, replaced with its final form via
PUT /api/live/events/{id}. Safe to run twice. Events the backend refuses (4xx) are listed, not retried.

Usage:
    venv/Scripts/python.exe services/live/replay_events.py ml/data/results/live/<session>/events.jsonl --backend http://localhost:8000
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend_client import BackendClient  # noqa: E402


def load(path: Path) -> list[tuple[dict, str]]:
    """(event, phase) in file order; a suppressed provisional event is never sent, as in the pipeline."""
    out, closed = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["phase"] == "close":
            closed.add(r["event"]["event_id"])
        out.append((r["event"], r["phase"]))
    return [(e, ph) for e, ph in out if ph == "close" or e.get("status") != "suppressed" or e["event_id"] in closed]


def replay(path: Path, backend: str, token: str | None = None) -> dict:
    items = load(path)
    c = BackendClient(backend, token)
    sent_ok = 0
    for ev, phase in items:
        if c._send_event((ev, phase)):
            sent_ok += 1
        else:  # backend still down: stop, nothing is lost (the file stays)
            break
    c.close(timeout=0.1)
    return {"file": str(path), "records": len(items), "delivered": sent_ok, "refused": c.dropped,
            "complete": sent_ok == len(items), "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("events_jsonl", type=Path)
    ap.add_argument("--backend", default="http://localhost:8000")
    ap.add_argument("--token", default=None, help="service token (default: env SERVICE_TOKEN)")
    a = ap.parse_args()
    res = replay(a.events_jsonl, a.backend, a.token)
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["complete"] else 1)


if __name__ == "__main__":
    main()
