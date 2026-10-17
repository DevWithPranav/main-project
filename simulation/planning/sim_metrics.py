"""Traffic metrics of one validation run (Build Plan M8), from the logged vehicle states.

Input rows (vehicle_states.csv, one per vehicle per logged tick):
    sim_time, id, x, y, yaw, vx, vy, speed_kmh, limit_kmh, road_id
Only vehicles within `radius_m` of the area centre count.

    speed        mean and 85th-percentile speed (km/h); share of samples over the limit (+ tolerance)
    speeding     vehicles ever over the limit (+ tolerance) for >= min_s
    conflicts    TTC < ttc_s episodes per vehicle pair (FHWA SSAM default 1.5 s): rear-end (same
                 heading, follower closing in on a leader ahead within lateral_m) and crossing (paths
                 at > 30 deg, constant-velocity discs of radius 1.5 m that would touch within ttc_s);
                 an episode is a run of consecutive samples in conflict; min TTC per episode
    travel_time  per vehicle that enters and leaves the area during the run: time inside
    queue        per sample, stopped vehicles (< stop_kmh) chained along their heading with gaps
                 <= gap_m; queue length = chain extent + one car length; max and mean over time

Pure numpy, so it runs in both venvs and is unit-tested in simulation/planning/tests/.
"""

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

TTC_S = 1.5
CAR_M = 4.5


def load_states(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("sim_time", "x", "y", "yaw", "vx", "vy", "speed_kmh", "limit_kmh"):
            r[k] = float(r[k]) if r.get(k) not in (None, "") else math.nan
        r["id"] = int(r["id"])
    return rows


def _in_area(rows, center, radius_m):
    cx, cy = center
    return [r for r in rows if (r["x"] - cx) ** 2 + (r["y"] - cy) ** 2 <= radius_m ** 2]


def speed_stats(rows, tol_pct=10.0, min_s=1.0, dt=None) -> dict:
    if not rows:
        return {"mean_kmh": None, "p85_kmh": None, "over_limit_share": None, "speeding_vehicles": 0}
    v = np.array([r["speed_kmh"] for r in rows])
    lim = np.array([r["limit_kmh"] for r in rows])
    over = np.isfinite(lim) & (v > lim * (1 + tol_pct / 100.0))
    per = defaultdict(list)
    for r, o in zip(rows, over):
        per[r["id"]].append((r["sim_time"], bool(o)))
    n_veh = 0
    for pts in per.values():
        pts.sort()
        run_start = None
        for i, (t, o) in enumerate(pts):
            if o and run_start is None:
                run_start = t
            if run_start is not None and (not o or i == len(pts) - 1):
                end = t if o else pts[i - 1][0]
                if end - run_start >= min_s:
                    n_veh += 1
                    break
                run_start = None
    moving = v[v > 1.0]
    return {"mean_kmh": round(float(moving.mean()), 2) if len(moving) else None,
            "p85_kmh": round(float(np.percentile(moving, 85)), 2) if len(moving) else None,
            "over_limit_share": round(float(over.mean()), 4), "speeding_vehicles": n_veh}


def pair_ttc(a: dict, b: dict, ttc_s=TTC_S, lateral_m=1.5, radius_m=1.5) -> tuple[str, float] | None:
    """TTC of two vehicles at one instant, or None if no conflict within ttc_s."""
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    dist = math.hypot(dx, dy)
    if dist > 40.0:
        return None
    dh = abs((a["yaw"] - b["yaw"] + 180.0) % 360.0 - 180.0)
    if dh < 30.0:  # same direction: rear-end
        h = math.radians(a["yaw"])
        ux, uy = math.cos(h), math.sin(h)
        along, lat = dx * ux + dy * uy, -dx * uy + dy * ux
        if abs(lat) > lateral_m:
            return None
        lead, foll = (b, a) if along > 0 else (a, b)
        gap = abs(along) - CAR_M
        closing = (foll["vx"] * ux + foll["vy"] * uy) - (lead["vx"] * ux + lead["vy"] * uy)
        if gap <= 0 or closing <= 0.5:
            return None
        t = gap / closing
        return ("rear_end", t) if t < ttc_s else None
    if dh < 150.0:  # crossing paths: constant-velocity discs
        rvx, rvy = b["vx"] - a["vx"], b["vy"] - a["vy"]
        rv2 = rvx * rvx + rvy * rvy
        if rv2 < 0.25:
            return None
        t_star = -(dx * rvx + dy * rvy) / rv2
        if t_star <= 0 or t_star > ttc_s:
            return None
        mx, my = dx + rvx * t_star, dy + rvy * t_star
        return ("crossing", t_star) if math.hypot(mx, my) < 2 * radius_m else None
    return None


def conflicts(rows, ttc_s=TTC_S) -> dict:
    by_t = defaultdict(list)
    for r in rows:
        by_t[r["sim_time"]].append(r)
    times = sorted(by_t)
    active: dict[tuple, dict] = {}
    episodes = []
    for t in times:
        now = {}
        rs = by_t[t]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                c = pair_ttc(rs[i], rs[j], ttc_s)
                if c:
                    key = tuple(sorted((rs[i]["id"], rs[j]["id"])))
                    now[key] = c
        for key, (kind, ttc) in now.items():
            if key in active:
                active[key]["min_ttc_s"] = min(active[key]["min_ttc_s"], ttc)
            else:
                active[key] = {"pair": list(key), "kind": kind, "t": t, "min_ttc_s": ttc}
        for key in [k for k in active if k not in now]:
            episodes.append(active.pop(key))
    episodes += active.values()
    kinds = defaultdict(int)
    for e in episodes:
        kinds[e["kind"]] += 1
    return {"count": len(episodes), "by_kind": dict(kinds),
            "min_ttc_s": round(min((e["min_ttc_s"] for e in episodes), default=math.nan), 3) if episodes else None}


def travel_times(all_rows, center, radius_m, t0, t1, edge_s=1.0) -> dict:
    per = defaultdict(list)
    cx, cy = center
    for r in all_rows:
        per[r["id"]].append((r["sim_time"], (r["x"] - cx) ** 2 + (r["y"] - cy) ** 2 <= radius_m ** 2))
    out = []
    for pts in per.values():
        pts.sort()
        inside = [t for t, i in pts if i]
        if not inside:
            continue
        a, b = inside[0], inside[-1]
        if a - t0 > edge_s and t1 - b > edge_s:  # entered and left during the run
            out.append(b - a)
    return {"vehicles": len(out), "mean_s": round(float(np.mean(out)), 2) if out else None,
            "p85_s": round(float(np.percentile(out, 85)), 2) if out else None}


def queue_stats(rows, stop_kmh=5.0, gap_m=10.0) -> dict:
    by_t = defaultdict(list)
    for r in rows:
        if r["speed_kmh"] < stop_kmh:
            by_t[r["sim_time"]].append(r)
    lengths = []
    for rs in by_t.values():
        best = 0.0
        groups = defaultdict(list)
        for r in rs:
            groups[(r.get("road_id"), round(((r["yaw"] + 360) % 360) / 45))].append(r)
        for g in groups.values():
            h = math.radians(g[0]["yaw"])
            s = sorted(r["x"] * math.cos(h) + r["y"] * math.sin(h) for r in g)
            start = s[0]
            for a, b in zip(s, s[1:] + [math.inf]):
                if b - a > gap_m + CAR_M:
                    best = max(best, a - start + CAR_M)
                    start = b
        lengths.append(best)
    return {"max_m": round(max(lengths), 1) if lengths else 0.0,
            "mean_m": round(float(np.mean(lengths)), 1) if lengths else 0.0}


def run_metrics(states_csv: Path, center, radius_m, ttc_s=TTC_S) -> dict:
    rows = load_states(states_csv)
    if not rows:
        return {"error": "no states"}
    t0, t1 = min(r["sim_time"] for r in rows), max(r["sim_time"] for r in rows)
    area = _in_area(rows, center, radius_m)
    return {"duration_s": round(t1 - t0, 1), "samples": len(area),
            "speed": speed_stats(area), "conflicts_ttc": conflicts(area, ttc_s),
            "travel_time": travel_times(rows, center, radius_m, t0, t1), "queue": queue_stats(area)}
