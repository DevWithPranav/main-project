"""Violation analytics for the planner (Build Plan M8; PRD 21.1; Expected_Output 7.1).

  hotspots()       DBSCAN on event positions (map metres), separately per violation type.
                   PRD defaults: eps 50 m, min 10 events; >= 50 events = a major hotspot. A type with
                   fewer events than min_events cannot form a hotspot, and the result says so.
  trends()         event counts per time bin (default 1 h) and per type; hour of day when events
                   carry a wall-clock time ("at" / "time_utc"), else bins of the session clock.
  section_stats()  per lane and per road from kinematics rows (kinematics.csv): vehicles, density
                   (veh/km over the observed length), mean / 85th-percentile speed, share over the
                   limit, queues (stopped vehicles chained along the lane).

Usage:
    python ml/planning/analytics.py <violations.json> [--scene Town05.json] [--kinematics kinematics.csv] [--out a.json]
"""

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENGINE = REPO / "ml" / "violation_engine"

# PRD 21.1
EPS_M = 50.0
MIN_EVENTS = 10
MAJOR_EVENTS = 50  # Expected_Output 7.3: the PRD's ">= 50 events per cluster" criterion
COUNTED_STATUS = ("flagged", "needs_review")  # events.COUNTED_STATUS
STOP_KMH = 5.0  # a vehicle below this counts as stopped (queue)
QUEUE_GAP_M = 12.0  # stopped vehicles closer than this along a lane belong to the same queue
VEHICLE_LEN_M = 4.5

LANE_ROAD = re.compile(r"^r(\d+)_")


def counted(events: list[dict], statuses=COUNTED_STATUS) -> list[dict]:
    """Violation events with a counting status (suppressed queue / breakdown cases left out)."""
    return [e for e in events if e.get("kind", "violation") == "violation" and e.get("status") in statuses]


def road_of(lane_id: str | None, scene: dict | None = None) -> str | None:
    """Road id of a lane id: from the lane map if given, else parsed from 'r<road>_s<sec>_l<lane>'."""
    if not lane_id:
        return None
    if scene:
        for l in _lanes_by_id(scene).get(lane_id, []):
            return str(l.get("road_id"))
    m = LANE_ROAD.match(lane_id)
    return m.group(1) if m else None


_LANE_CACHE: dict[int, dict] = {}


def _lanes_by_id(scene: dict) -> dict:
    key = id(scene)
    if key not in _LANE_CACHE:
        d = defaultdict(list)
        for l in scene.get("lanes", []):
            d[l["id"]].append(l)
        _LANE_CACHE.clear()
        _LANE_CACHE[key] = d
    return _LANE_CACHE[key]


def lane_props(scene: dict | None, lane_id: str | None) -> dict | None:
    if not scene or not lane_id:
        return None
    ls = _lanes_by_id(scene).get(lane_id)
    return ls[0] if ls else None


# ---------------------------------------------------------------------------------------- DBSCAN

def dbscan(points: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """Labels (-1 = noise) of plain DBSCAN (Ester et al. 1996), Euclidean, min_samples counting the
    point itself (sklearn convention). Deterministic: points are visited in input order."""
    n = len(points)
    labels = np.full(n, -1, int)
    if n == 0:
        return labels
    tree = cKDTree(points)
    nbrs = tree.query_ball_point(points, r=eps)
    core = np.array([len(nb) >= min_samples for nb in nbrs])
    cid = 0
    for i in range(n):
        if labels[i] != -1 or not core[i]:
            continue
        labels[i] = cid
        stack = [i]
        while stack:
            j = stack.pop()
            if not core[j]:
                continue
            for k in nbrs[j]:
                if labels[k] == -1:
                    labels[k] = cid
                    if core[k]:
                        stack.append(k)
        cid += 1
    return labels


def _hull(pts: np.ndarray, pad: float = 5.0) -> list[list[float]]:
    """Convex hull polygon of the points, padded by `pad` m (a square around 1 point, a box around 2)."""
    import shapely
    geom = shapely.MultiPoint([tuple(p) for p in pts]).convex_hull.buffer(pad, quad_segs=2)
    return [[round(x, 2), round(y, 2)] for x, y in geom.exterior.coords]


def group_summary(evs: list[dict], scene: dict | None = None) -> dict:
    """Location and evidence of a set of events: centroid, polygon, radius, lanes, roads, period."""
    pts = np.array([[e["x"], e["y"]] for e in evs], float)
    c = pts.mean(axis=0)
    t0 = min(e.get("start_s", e.get("flag_s", 0.0)) for e in evs)
    t1 = max((e.get("end_s") if e.get("end_s") is not None else e.get("flag_s", 0.0)) for e in evs)
    lanes = Counter(e.get("lane_id") for e in evs if e.get("lane_id"))
    roads = Counter(r for r in (road_of(e.get("lane_id"), scene) for e in evs) if r)
    return {"event_ids": sorted(e["event_id"] for e in evs), "n": len(evs),
            "centroid": [round(float(c[0]), 2), round(float(c[1]), 2)],
            "radius_m": round(float(np.hypot(*(pts - c).T).max()), 1),
            "polygon": _hull(pts),
            "lane_ids": dict(sorted(lanes.items())), "road_ids": dict(sorted(roads.items())),
            "zone_ids": dict(sorted(Counter(e.get("zone_id") for e in evs if e.get("zone_id")).items())),
            "conditions": dict(sorted(Counter(e.get("condition") or "-" for e in evs).items())),
            "tags": dict(sorted(Counter(t for e in evs for t in e.get("tags", [])).items())),
            "mean_confidence": round(float(np.mean([e.get("confidence", 0.0) for e in evs])), 3),
            "statuses": dict(sorted(Counter(e.get("status") for e in evs).items())),
            "sessions": sorted({session_of(e) for e in evs}),
            "period": {"start_s": round(t0, 2), "end_s": round(t1, 2), "span_s": round(t1 - t0, 2)}}


def session_of(e: dict) -> str:
    return e.get("session_id") or e["event_id"].rsplit("-", 1)[0]


def hotspots(events: list[dict], eps_m: float = EPS_M, min_events: int = MIN_EVENTS,
             major_events: int = MAJOR_EVENTS, scene: dict | None = None, by: str = "type") -> dict:
    """DBSCAN per event type. Returns {"params", "by_type": {type: {n_events, hotspots, noise_event_ids, note}}}.
    Events must already be filtered (counted()). Sorting by event_id first makes labels independent of input order."""
    out = {"params": {"eps_m": eps_m, "min_events": min_events, "major_events": major_events, "by": by},
           "by_type": {}}
    groups = defaultdict(list)
    for e in events:
        groups[e[by]].append(e)
    for t in sorted(groups):
        evs = sorted(groups[t], key=lambda e: e["event_id"])
        res = {"n_events": len(evs), "hotspots": [], "noise_event_ids": [], "note": ""}
        if len(evs) < min_events:
            res["noise_event_ids"] = [e["event_id"] for e in evs]
            res["note"] = (f"{len(evs)} event(s) < min_events {min_events}: no hotspot possible "
                           f"(DBSCAN needs at least {min_events} events within {eps_m:g} m)")
            out["by_type"][t] = res
            continue
        labels = dbscan(np.array([[e["x"], e["y"]] for e in evs], float), eps_m, min_events)
        for k in sorted(set(labels) - {-1}):
            members = [e for e, l in zip(evs, labels) if l == k]
            h = group_summary(members, scene)
            h.update(type=t, tier="major_hotspot" if len(members) >= major_events else "hotspot")
            res["hotspots"].append(h)
        res["hotspots"].sort(key=lambda h: (-h["n"], h["event_ids"][0]))
        res["noise_event_ids"] = [e["event_id"] for e, l in zip(evs, labels) if l == -1]
        res["note"] = f"{len(res['hotspots'])} hotspot(s), {len(res['noise_event_ids'])} isolated event(s)"
        out["by_type"][t] = res
    return out


# ---------------------------------------------------------------------------------------- trends

def _wall_hour(e: dict) -> int | None:
    for k in ("at", "time_utc", "created_at"):
        v = e.get(k)
        if v:
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00")).hour
            except ValueError:
                pass
    return None


def trends(events: list[dict], bin_s: float = 3600.0) -> dict:
    """Counts per hour of day (wall-clock events) or per bin of the session clock (simulator time)."""
    hours = [_wall_hour(e) for e in events]
    if events and all(h is not None for h in hours):
        bins = defaultdict(Counter)
        for e, h in zip(events, hours):
            bins[h][e["type"]] += 1
        return {"clock": "hour_of_day", "bins": [{"hour": h, "count": sum(c.values()), "by_type": dict(c)}
                                                 for h, c in sorted(bins.items())]}
    bins = defaultdict(Counter)
    for e in events:
        bins[(session_of(e), int(e.get("flag_s", 0.0) // bin_s))][e["type"]] += 1
    span = defaultdict(list)
    for e in events:
        span[session_of(e)].append(e.get("flag_s", 0.0))
    spans = {s: round(max(v) - min(v), 1) for s, v in span.items()}
    note = ""
    if all(v < bin_s for v in spans.values()):
        note = f"every session spans < one bin ({bin_s:g} s): no trend can be read"
    return {"clock": "session_time_s", "bin_s": bin_s, "session_span_s": spans, "note": note,
            "bins": [{"session": s, "start_s": b * bin_s, "count": sum(c.values()), "by_type": dict(c)}
                     for (s, b), c in sorted(bins.items())]}


# ---------------------------------------------------------------------------------------- sections

def load_kinematics(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _scene_map(scene: dict):
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    from lane_map import SceneMap
    return SceneMap(scene)


def queue_lengths(s_stopped: list[float], gap_m: float = QUEUE_GAP_M) -> list[float]:
    """Queues along one lane from the positions s (m) of its stopped vehicles: vehicles closer than
    gap_m form one queue; length = span + one vehicle length."""
    if not s_stopped:
        return []
    s = sorted(s_stopped)
    out, a = [], s[0]
    for p, q in zip(s, s[1:]):
        if q - p > gap_m:
            out.append(p - a + VEHICLE_LEN_M)
            a = q
    out.append(s[-1] - a + VEHICLE_LEN_M)
    return out


def section_stats(rows: list[dict], scene: dict, stride_s: float = 1.0, stop_kmh: float = STOP_KMH,
                  scene_map=None) -> dict:
    """Per lane and per road: samples are taken every stride_s per track (rows are kinematics.csv).
    Density uses the observed length of the lane (span of matched positions, >= 10 m), since the
    camera sees only part of most lanes."""
    sm = scene_map or _scene_map(scene)
    last_bin = {}
    per_bin_lane = defaultdict(lambda: defaultdict(list))  # time bin -> lane -> [(s, speed, track)]
    lane_obj = {}
    for r in rows:
        tid, t = r["track_id"], float(r["time_s"])
        b = int(t // stride_s)
        if last_bin.get(tid) == b:
            continue
        last_bin[tid] = b
        z = r.get("road_z", "")
        m = sm.match(float(r["x"]), float(r["y"]), float(z) if z not in ("", None) else None)
        if m is None:
            continue
        lane_obj[m.lane.id] = m.lane
        per_bin_lane[b][m.lane.id].append((m.s, float(r["speed_kmh"]), tid))
    acc = defaultdict(lambda: {"speeds": [], "s": [], "tracks": set(), "counts": [], "stopped": [], "queues": []})
    bins = sorted(per_bin_lane)
    for b in bins:
        for lid, obs in per_bin_lane[b].items():
            a = acc[lid]
            a["speeds"] += [v for _, v, _ in obs]
            a["s"] += [s for s, _, _ in obs]
            a["tracks"] |= {t for _, _, t in obs}
            a["counts"].append(len(obs))
            st = [s for s, v, _ in obs if v < stop_kmh]
            a["stopped"].append(len(st))
            a["queues"].append(max(queue_lengths(st), default=0.0))
    n_bins = max(1, len(bins))
    lanes = {}
    for lid, a in sorted(acc.items()):
        lane = lane_obj[lid]
        sp = np.array(a["speeds"])
        obs_len = max(10.0, float(np.ptp(a["s"])) if a["s"] else 10.0)
        lim = lane.speed_limit_kmh
        # per-bin averages over all bins of the session (a lane with no vehicle in a bin counts as 0)
        lanes[lid] = {"road_id": lane.road_id, "samples": len(sp), "vehicles": len(a["tracks"]),
                      "observed_length_m": round(obs_len, 1),
                      "density_veh_per_km": round(sum(a["counts"]) / n_bins / (obs_len / 1000.0), 2),
                      "mean_speed_kmh": round(float(sp.mean()), 2),
                      "p85_speed_kmh": round(float(np.percentile(sp, 85)), 2),
                      "speed_limit_kmh": lim,
                      "share_over_limit": round(float((sp > lim).mean()), 3) if lim else None,
                      "mean_stopped": round(sum(a["stopped"]) / n_bins, 2),
                      "max_stopped": int(max(a["stopped"])),
                      "max_queue_m": round(max(a["queues"]), 1),
                      "bridge": lane.bridge, "ramp": lane.ramp, "junction": lane.junction,
                      "road_class": lane.road_class}
    roads = defaultdict(lambda: {"lanes": [], "vehicles": 0, "samples": 0, "speed_sum": 0.0, "max_queue_m": 0.0,
                                 "max_stopped": 0})
    for lid, s in lanes.items():
        r = roads[str(s["road_id"])]
        r["lanes"].append(lid)
        r["vehicles"] += s["vehicles"]
        r["samples"] += s["samples"]
        r["speed_sum"] += s["mean_speed_kmh"] * s["samples"]
        r["max_queue_m"] = max(r["max_queue_m"], s["max_queue_m"])
        r["max_stopped"] = max(r["max_stopped"], s["max_stopped"])
    road_out = {k: {"lanes": v["lanes"], "lane_vehicle_sum": v["vehicles"], "samples": v["samples"],
                    "mean_speed_kmh": round(v["speed_sum"] / v["samples"], 2) if v["samples"] else None,
                    "max_queue_m": v["max_queue_m"], "max_stopped": v["max_stopped"]}
                for k, v in sorted(roads.items())}
    return {"stride_s": stride_s, "stop_kmh": stop_kmh, "time_bins": len(bins), "lanes": lanes, "roads": road_out}


def analyse(events: list[dict], scene: dict | None = None, kinematics: list[dict] | None = None, **kw) -> dict:
    ev = counted(events)
    out = {"n_events": len(events), "n_counted": len(ev), "by_type": dict(sorted(Counter(e["type"] for e in ev).items())),
           "hotspots": hotspots(ev, scene=scene, **kw), "trends": trends(ev)}
    if kinematics and scene:
        out["sections"] = section_stats(kinematics, scene)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("events", type=Path)
    ap.add_argument("--scene", type=Path)
    ap.add_argument("--kinematics", type=Path)
    ap.add_argument("--eps", type=float, default=EPS_M)
    ap.add_argument("--min-events", type=int, default=MIN_EVENTS)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    scene = json.loads(a.scene.read_text()) if a.scene else None
    kin = load_kinematics(a.kinematics) if a.kinematics else None
    res = analyse(json.loads(a.events.read_text()), scene, kin, eps_m=a.eps, min_events=a.min_events)
    txt = json.dumps(res, indent=1)
    if a.out:
        a.out.write_text(txt)
    print(txt if len(txt) < 4000 else txt[:4000] + "\n...")


if __name__ == "__main__":
    main()
