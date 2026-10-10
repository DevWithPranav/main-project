"""Import a processed recorded flight as one session (POST /api/sessions/import).

Reads <RESULTS_DIR>/<flight>/<TRACKER_RUN>/<violations_dir>/:
    violations.json            events (schemas/event.schema.json); each is validated, rejects counted
    events/*.mp4, snapshots    evidence written by render_violations.py --clips -> S3 evidence/<flight>/
    kinematics.csv             per-frame x, y, speed_kmh (else trajectories_world.csv wx, wy) -> tracks
and <RECORDINGS_DIR>/<flight>/metadata.json for the map. violations_dir defaults to the newest
violations* folder that has a violations.json.
"""

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from . import config, storage
from .engine_bridge import condition_of, event_errors


def flight_dir(flight: str) -> Path:
    if not flight or any(c in flight for c in "/\\") or flight.startswith("."):
        raise HTTPException(422, "flight must be a flight folder name, e.g. 20261009_201727")
    d = config.RESULTS_DIR / flight / config.TRACKER_RUN
    if not d.is_dir():
        raise HTTPException(404, f"no processed flight at {d}")
    return d


def pick_violations_dir(fdir: Path, name: str | None) -> Path:
    if name:
        if any(c in name for c in "/\\") or name.startswith("."):
            raise HTTPException(422, "violations_dir must be a folder name inside the flight's results")
        d = fdir / name
        if not (d / "violations.json").is_file():
            raise HTTPException(404, f"no violations.json in {d}")
        return d
    cands = [d for d in fdir.glob("violations*") if (d / "violations.json").is_file()]
    if not cands:
        raise HTTPException(404, f"no violations*/violations.json under {fdir}")
    return max(cands, key=lambda d: (d / "violations.json").stat().st_mtime)


def flight_start(flight: str) -> datetime:
    """Folder names are the local recording time (YYYYmmdd_HHMMSS); fall back to now."""
    try:
        return datetime.strptime(flight[:15], "%Y%m%d_%H%M%S").astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def town_of(flight: str) -> str | None:
    meta = config.RECORDINGS_DIR / flight / "metadata.json"
    if meta.is_file():
        m = json.loads(meta.read_text(encoding="utf-8")).get("map")
        return m.rsplit("/", 1)[-1] if m else None
    return None


def load_events(vdir: Path) -> list[dict]:
    data = json.loads((vdir / "violations.json").read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("events", data.get("violations", []))
    return data


def validate(events: list[dict]) -> tuple[list[dict], list[str]]:
    """Schema-valid events (condition filled in when the engine left it out) and the error lines."""
    ok, errors = [], []
    for e in events:
        if isinstance(e, dict) and e.get("kind", "violation") == "violation" and "condition" not in e:
            e = {**e, "condition": condition_of(e)}
        errs = event_errors([e])
        if errs:
            errors += errs
        else:
            ok.append(e)
    return ok, errors


def upload_evidence(events: list[dict], vdir: Path, prefix: str) -> int:
    """Upload clip / snapshot files named in evidence to S3; record their keys. Returns uploads."""
    n = 0
    for e in events:
        ev = e.get("evidence") or {}
        for k in ("clip", "snapshot"):
            rel = ev.get(k)
            if not rel or "://" in rel:
                continue
            p = (vdir / rel).resolve()
            if not p.is_file() or vdir.resolve() not in p.parents:
                continue
            key = f"{prefix}/{e['event_id']}{p.suffix}" if k == "clip" else f"{prefix}/{e['event_id']}_snap{p.suffix}"
            n += storage.upload(p, key)
            ev[f"{k}_key"] = key
        e["evidence"] = ev
    return n


def _f(v: str) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def load_tracks(vdir: Path) -> tuple[dict[int, dict], float | None]:
    """{track_id: {"cls", "points": [[t_s, x, y, speed_kmh], ...]}} at <= 5 Hz, and the first time_s."""
    kin, world = vdir / "kinematics.csv", vdir / "trajectories_world.csv"
    if kin.is_file():
        path, xk, yk, sk = kin, "x", "y", "speed_kmh"
    elif world.is_file():
        path, xk, yk, sk = world, "wx", "wy", None
    else:
        return {}, None
    tracks: dict[int, dict] = {}
    last: dict[int, float] = {}
    t0 = None
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t, x, y = _f(r.get("time_s")), _f(r.get(xk)), _f(r.get(yk))
            if t is None:
                continue
            t0 = t if t0 is None else min(t0, t)
            if x is None or y is None or r.get("visible", "1") == "0":
                continue
            tid = int(float(r["track_id"]))
            if tid in last and t - last[tid] < config.TRAJ_MIN_DT_S - 1e-6:
                continue
            last[tid] = t
            s = _f(r.get(sk)) if sk else None
            tr = tracks.setdefault(tid, {"cls": r.get("class"), "points": []})
            tr["points"].append([round(t, 3), round(x, 2), round(y, 2), None if s is None else round(s, 1)])
    if sk is None:  # speed from consecutive downsampled points
        for tr in tracks.values():
            p = tr["points"]
            for i in range(1, len(p)):
                dt = p[i][0] - p[i - 1][0]
                if dt > 0:
                    p[i][3] = round(math.hypot(p[i][1] - p[i - 1][1], p[i][2] - p[i - 1][2]) / dt * 3.6, 1)
    return tracks, t0
