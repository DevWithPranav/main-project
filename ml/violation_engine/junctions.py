"""Junction outlines and approaches from a lane map (scene JSON), numpy only.

A junction lane carries "junction_id" (the OpenDRIVE junction, written by
simulation/carla_scripts/export_lane_map.py). build(scene) adds one entry per junction:

    {"id": "J<id>", "centre": [x, y], "polygon": [[x, y], ...], "lanes": [junction lane ids],
     "approaches": [{"id": "J<id>_a<k>", "road_id": "12", "lanes": [incoming lane ids],
                     "x": ..., "y": ..., "heading_deg": ..., "stop_line": [[x, y], [x, y]]}]}

polygon   the area the junction lanes cover, traced by rays from the centre (every
          RAY_STEP_DEG): the farthest point of each ray still on a junction lane. Junction
          lanes overlap, so their own ribbons draw as criss-crossing wedges; this is the
          one surface for the whole junction.
approach  the driving lanes of one road and direction that lead into the junction; the stop
          line runs across their ends (the junction's edge), x / y is its middle and
          heading_deg the driving direction there (CARLA world axes, atan2(dy, dx)).
"""

import math
import re
from collections import defaultdict

import numpy as np

RAY_STEP_DEG = 2.0
RAY_SAMPLE_M = 0.25
RAY_GAP_M = 0.75  # a ray stops at the first gap in the coverage longer than this
LANE_NUM = re.compile(r"_l(-?\d+)")


def _segments(lanes: list[dict]):
    """Every centreline segment of these lanes: start (M, 2), end (M, 2), half width (M,)."""
    a, b, hw = [], [], []
    for l in lanes:
        c = np.asarray(l["centreline"], dtype=float)
        if len(c) < 2:
            continue
        a.append(c[:-1])
        b.append(c[1:])
        hw.append(np.full(len(c) - 1, float(l.get("width_m") or 3.5) / 2))
    return np.concatenate(a), np.concatenate(b), np.concatenate(hw)


def _covered(pts: np.ndarray, a: np.ndarray, b: np.ndarray, hw: np.ndarray) -> np.ndarray:
    """Which points (N, 2) lie within half a lane width of a segment (in chunks: N x M distances)."""
    ab = b - a
    l2 = np.maximum((ab ** 2).sum(1), 1e-9)
    out = np.zeros(len(pts), dtype=bool)
    for i in range(0, len(pts), 2000):
        ap = pts[i:i + 2000, None, :] - a[None]
        t = np.clip((ap * ab[None]).sum(2) / l2[None], 0.0, 1.0)
        d = np.hypot(*(ap - t[..., None] * ab[None]).transpose(2, 0, 1))
        out[i:i + 2000] = (d <= hw[None] + 0.05).any(1)
    return out


def outline(lanes: list[dict], centre: np.ndarray) -> list[list[float]]:
    """Star-shaped polygon of the area the lanes cover, seen from the centre."""
    a, b, hw = _segments(lanes)
    reach = float(np.max(np.hypot(*(np.vstack([a, b]) - centre).T))) + float(hw.max()) + 1.0
    r = np.arange(0.0, reach, RAY_SAMPLE_M)
    ang = np.radians(np.arange(0.0, 360.0, RAY_STEP_DEG))
    dirs = np.stack([np.cos(ang), np.sin(ang)], 1)
    pts = centre + dirs[:, None, :] * r[None, :, None]  # (rays, samples, 2)
    cov = _covered(pts.reshape(-1, 2), a, b, hw).reshape(len(ang), len(r))
    gap = int(round(RAY_GAP_M / RAY_SAMPLE_M))
    poly = []
    for k in range(len(ang)):
        hit = np.flatnonzero(cov[k])
        if not len(hit):
            continue
        end = hit[0]
        for h in hit[1:]:
            if h - end > gap:
                break
            end = h
        poly.append(np.round(centre + dirs[k] * r[end], 2).tolist())
    return poly


def _approach(jid: str, k: int, lanes: list[dict]) -> dict:
    """Stop line across the ends of the lanes leading into a junction."""
    ends, dirs, edges = [], [], []
    for l in lanes:
        c = np.asarray(l["centreline"], dtype=float)
        p, q = c[-1], c[-2]
        d = (p - q) / max(float(np.hypot(*(p - q))), 1e-9)
        n = np.array([-d[1], d[0]])
        w = float(l.get("width_m") or 3.5) / 2
        ends.append(p)
        dirs.append(d)
        edges += [p + n * w, p - n * w]
    d = np.mean(dirs, 0)
    d /= max(float(np.hypot(*d)), 1e-9)
    n = np.array([-d[1], d[0]])
    base = np.mean(ends, 0)
    off = [float(np.dot(e - base, n)) for e in edges]
    s0, s1 = base + n * min(off), base + n * max(off)
    mid = (s0 + s1) / 2
    return {"id": f"{jid}_a{k}", "road_id": lanes[0].get("road_id"), "lanes": [l["id"] for l in lanes],
            "x": round(float(mid[0]), 2), "y": round(float(mid[1]), 2),
            "heading_deg": round(math.degrees(math.atan2(d[1], d[0])), 1),
            "stop_line": [np.round(s0, 2).tolist(), np.round(s1, 2).tolist()]}


def build(scene: dict) -> list[dict]:
    """scene["junctions"] from the lanes' junction_id (replaces any already there); returns it."""
    lanes = scene.get("lanes", [])
    by_id = {l["id"]: l for l in lanes}
    members = defaultdict(list)
    for l in lanes:
        if l.get("junction") and l.get("junction_id") is not None and len(l.get("centreline", [])) >= 2:
            members[str(l["junction_id"])].append(l)
    incoming = defaultdict(lambda: defaultdict(list))  # junction -> (road, direction) -> lanes
    for l in lanes:
        if l.get("junction") or (l.get("lane_type") or "driving") != "driving" or len(l.get("centreline", [])) < 2:
            continue
        into = {str(by_id[n]["junction_id"]) for n in l.get("next", []) if n in by_id and by_id[n].get("junction_id") is not None}
        m = LANE_NUM.search(l["id"])
        side = (1 if int(m.group(1)) > 0 else -1) if m else 0
        for j in into:
            incoming[j][(l.get("road_id"), side)].append(l)
    out = []
    for jid in sorted(members, key=lambda s: (len(s), s)):
        jl = members[jid]
        pts = np.vstack([np.asarray(l["centreline"], dtype=float) for l in jl])
        groups = list(incoming[jid].values())
        tag = f"J{jid}"
        appr = [_approach(tag, 0, g) for g in groups]
        centre = pts.mean(0)  # the junction lanes' points (every ~1 m): the middle of the area they cover
        appr.sort(key=lambda a: math.atan2(a["y"] - centre[1], a["x"] - centre[0]))
        for k, a in enumerate(appr):
            a["id"] = f"{tag}_a{k}"
        out.append({"id": tag, "centre": np.round(centre, 2).tolist(), "polygon": outline(jl, centre),
                    "lanes": [l["id"] for l in jl], "approaches": appr})
    scene["junctions"] = out
    return out


def junction_at(scene: dict, x: float, y: float, radius: float = 0.0) -> dict | None:
    """The junction whose outline contains (x, y), else the nearest one whose centre is within radius."""
    best, best_d = None, math.inf
    for j in scene.get("junctions") or []:
        poly = j.get("polygon") or []
        if len(poly) >= 3 and _inside(poly, x, y):
            return j
        d = math.hypot(j["centre"][0] - x, j["centre"][1] - y)
        if d <= radius and d < best_d:
            best, best_d = j, d
    return best


def _inside(poly: list, x: float, y: float) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside
