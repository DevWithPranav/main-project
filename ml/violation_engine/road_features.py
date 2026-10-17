"""Road features derived from a lane map (Build Plan M1, Expected_Output Section 4.1).

Adds the per-lane properties the rules read, from the lanes' geometry and links. Only keys
a lane doesn't already have are filled, so a value set by hand (site file) or read from
OpenDRIVE (export_lane_map.py) always wins:

  road_class   "highway" if the lane's limit >= highway_kmh or another non-junction lane of the
               same road has it (a lower speed sign doesn't make a motorway urban), else "urban"
  one_way      True if every driving lane of the same road_id runs the same way (needs road_id)
  ramp         "on" / "off" / "link" / null: a non-highway lane that leads into (on) or comes
               from (off) a highway lane through junction lanes, or such a junction lane (link).
               Topology + speed limit, as Foretellix classifies highway entries/exits
               (docs/Research_Notes.md, M1); needs "next" links
  median_left  True if, beyond the lane's left edge, there is non-driving space and then
               opposing traffic within MEDIAN_SEARCH_M: a divided road. median_gap_m = the
               space between the lane's edge and the opposing lane's edge
  restricted   null unless set (OpenDRIVE "restricted" lanes, site file or profile override)

apply_overrides() applies a profile's road.lane_overrides (any OVERRIDE_KEYS attribute by lane id
or glob, e.g. "r37_*"). numpy only: export_lane_map.py runs it in the simulator env too.

Usage:
    python ml/violation_engine/road_features.py <scene.json> [--highway-kmh 90]   # summary
"""

import argparse
import fnmatch
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HIGHWAY_KMH = 90.0  # CARLA's limits are 30/60/90; Town04's highway loop is the 90 class
OPPOSING_DEG = 120.0  # directions further apart than this are opposing traffic
SAME_DEG = 60.0
MEDIAN_SEARCH_M = 30.0  # widest separator looked for; Town04's divided highway is well inside it
PROBE_STEP_M = 0.5
STATION_M = 10.0  # probe spacing along a lane
RAMP_DEPTH = 4  # junction lanes allowed between a ramp and the highway
GRID_M = 8.0
_READ_AS = {"lane_type": "driving", "lane_change": "both", "road_class": "urban", "bridge": False, "tunnel": False,
            "median_left": False}  # a missing key reads as this in lane_map.SceneMap
# what a profile's road.lane_overrides may set (schemas/profile.schema.json), served by the backend's
# GET /api/road/attributes; drives: condition ids (schemas/conditions.json) whose outcome it changes, read off rules.py
ROAD_ATTRIBUTES = [
    {"key": "speed_limit_kmh", "label": "Speed limit (km/h)", "type": "number", "min_exclusive": 0, "drives": ["E1", "E4"],
     "how": "SpeedingMonitor: the lane's limit (a speed zone, E3, replaces it; a lower class limit caps it, E4)"},
    {"key": "road_class", "label": "Road class", "type": "enum", "values": ["urban", "highway"], "drives": ["B1", "B2", "C3"],
     "how": "highway (outside junctions): stopping there is a highway stop (B1; B2 on a shoulder); wrong way is C3"},
    {"key": "lane_type", "label": "Lane type", "type": "enum", "values": ["driving", "shoulder", "parking"],
     "drives": ["A6", "B2", "A1", "A2", "A3", "A4", "A8", "C1", "C2", "C3", "C4", "C5", "D2", "D3", "D4"],
     "how": "shoulder: moving on it is A6, stopping on it on a highway is B2 (status possible_breakdown); only driving lanes are checked for "
            "lane changes / straddling (A1-A4, A8), wrong way (C1-C5) and U-turns (D2-D4)"},
    {"key": "lane_change", "label": "Lane change allowed", "type": "enum", "values": ["none", "left", "right", "both"],
     "drives": ["A3"], "how": "a lane change to a side not allowed here is A3"},
    {"key": "restricted", "label": "Restricted lane", "type": "enum", "values": ["bus", "emergency", "restricted", None],
     "drives": ["A5"], "how": "a vehicle class not exempt (lane_violation.restricted_exempt) in the lane is A5; null clears"},
    {"key": "one_way", "label": "One-way road", "type": "bool", "drives": ["C5"],
     "how": "wrong way in a one-way lane is C5 (else C1)"},
    {"key": "bridge", "label": "Bridge", "type": "bool", "drives": ["B5"], "how": "stopping on it is a highway stop, B5"},
    {"key": "tunnel", "label": "Tunnel", "type": "bool", "drives": ["B5"], "how": "stopping in it is a highway stop, B5"},
    {"key": "ramp", "label": "Ramp", "type": "enum", "values": ["on", "off", "link", None], "drives": ["B4", "C4"],
     "how": "stopping on it is a highway stop, B4; wrong way on it is C4; null clears"},
    {"key": "median_left", "label": "Median on the left", "type": "bool", "drives": ["D4"],
     "how": "a mid-block U-turn from or into the lane goes through the median, D4"},
]
OVERRIDE_KEYS = tuple(a["key"] for a in ROAD_ATTRIBUTES)


def _dirs(c: np.ndarray) -> np.ndarray:
    seg = np.diff(c, axis=0)
    return np.degrees(np.arctan2(seg[:, 1], seg[:, 0]))


def _adiff(a, b):
    return np.abs((np.asarray(a) - b + 180.0) % 360.0 - 180.0)


class _SegIndex:
    """Uniform grid over every lane segment: which lanes contain a point (|offset| <= width/2)."""

    def __init__(self, lanes: list[dict]):
        a, b, owner, width = [], [], [], []
        for i, l in enumerate(lanes):
            c = np.asarray(l["centreline"], float)
            a.append(c[:-1]); b.append(c[1:])
            owner += [i] * (len(c) - 1)
            width += [float(l.get("width_m", 3.5))] * (len(c) - 1)
        self.a, self.b = np.concatenate(a), np.concatenate(b)
        self.owner, self.half = np.array(owner), np.array(width) / 2
        self.dir = np.degrees(np.arctan2(*(self.b - self.a)[:, ::-1].T))
        self.cells = defaultdict(list)
        lo = np.minimum(self.a, self.b) - self.half[:, None]
        hi = np.maximum(self.a, self.b) + self.half[:, None]
        for k, (l0, h0) in enumerate(zip(np.floor(lo / GRID_M).astype(int), np.floor(hi / GRID_M).astype(int))):
            for gx in range(l0[0], h0[0] + 1):
                for gy in range(l0[1], h0[1] + 1):
                    self.cells[(gx, gy)].append(k)

    def candidates(self, pts: np.ndarray) -> np.ndarray:
        keys = {tuple(k) for k in np.floor(pts / GRID_M).astype(int)}
        return np.array(sorted({k for key in keys for k in self.cells.get(key, ())}), int)

    def hits(self, pts: np.ndarray, cand: np.ndarray) -> np.ndarray:
        """(len(pts), len(cand)) bool: point inside that segment's lane strip."""
        a, b = self.a[cand], self.b[cand]
        ab = b - a
        L2 = np.maximum((ab * ab).sum(1), 1e-12)
        t = np.clip(((pts[:, None, :] - a[None]) * ab[None]).sum(2) / L2[None], 0, 1)
        foot = a[None] + ab[None] * t[..., None]
        return np.hypot(*(pts[:, None, :] - foot).transpose(2, 0, 1)) <= self.half[cand][None]


def _driving(l: dict) -> bool:
    return str(l.get("lane_type", "driving")).lower() == "driving"


def _median(lanes: list[dict], i: int, idx: _SegIndex, left_handed: bool) -> tuple[bool, float | None]:
    l = lanes[i]
    c = np.asarray(l["centreline"], float)
    half = float(l.get("width_m", 3.5)) / 2
    seg_len = np.hypot(*np.diff(c, axis=0).T)
    cum = np.r_[0, np.cumsum(seg_len)]
    if cum[-1] < 4.0:
        return False, None
    votes, gaps = Counter(), []
    offs = np.arange(half + 0.25, half + MEDIAN_SEARCH_M, PROBE_STEP_M)
    for s in np.arange(min(2.0, cum[-1] / 2), cum[-1] - 1.0, STATION_M):
        k = min(int(np.searchsorted(cum, s, side="right") - 1), len(c) - 2)
        u = (c[k + 1] - c[k]) / max(seg_len[k], 1e-9)
        p = c[k] + u * (s - cum[k])
        n = np.array([u[1], -u[0]]) if left_handed else np.array([-u[1], u[0]])  # the driver's left
        pts = p + offs[:, None] * n
        cand = idx.candidates(pts)
        cand = cand[idx.owner[cand] != i]
        if not len(cand):
            votes["none"] += 1
            continue
        h = idx.hits(pts, cand)
        lane_dir = math.degrees(math.atan2(u[1], u[0]))
        result = "none"
        for j in range(len(offs)):
            segs = cand[h[j]]
            drv = [q for q in segs if _driving(lanes[idx.owner[q]]) and not lanes[idx.owner[q]].get("junction")]
            if not drv:
                continue
            d = _adiff(idx.dir[drv], lane_dir)
            if (d < SAME_DEG).any():
                result = "same"  # another lane of our direction: not the innermost lane
            elif (d > OPPOSING_DEG).any():
                result = "adjacent" if j == 0 else "median"
                if result == "median":  # our edge is at half; the opposing edge lies within the last probe step
                    gaps.append(max(offs[j] - PROBE_STEP_M / 2 - half, 0.0))
            else:
                continue
            break
        votes[result] += 1
    decided = votes["median"] + votes["adjacent"] + votes["same"]
    if decided and votes["median"] > decided / 2:
        return True, round(float(np.median(gaps)), 1)
    return False, None


def _one_way(lanes: list[dict]) -> dict[str, bool]:
    by_road = defaultdict(list)
    for l in lanes:
        if l.get("road_id") is not None and _driving(l) and not l.get("junction"):
            by_road[str(l["road_id"])].append(np.asarray(l["centreline"], float))
    out = {}
    for road, cs in by_road.items():
        mids = [(c[len(c) // 2], _dirs(c)[min(len(c) // 2, len(c) - 2)]) for c in cs]
        opposing = False
        for pa, da in mids:
            for c in cs:
                k = int(np.argmin(np.hypot(*(c - pa).T)))
                if _adiff(_dirs(c)[min(k, len(c) - 2)], da) > OPPOSING_DEG:
                    opposing = True
        out[road] = not opposing
    return out


def _ramps(lanes: list[dict]) -> dict[str, str]:
    by_id = {l["id"]: l for l in lanes}
    nxt = {l["id"]: [n for n in l.get("next", []) if n in by_id] for l in lanes}
    prv = defaultdict(list)
    for a, ns in nxt.items():
        for b in ns:
            prv[b].append(a)
    hw = lambda i: by_id[i].get("road_class") == "highway" and not by_id[i].get("junction")  # noqa: E731

    def reach(start: str, graph) -> list[str] | None:
        """Junction-lane path (>= 1 lane) from start to a highway lane of another road, or None.
        A lower-limit piece of the highway road itself (before a speed sign) is not a ramp."""
        frontier, seen = [(start, [])], {start}
        for _ in range(RAMP_DEPTH + 1):
            nf = []
            for cur, path in frontier:
                for n in graph[cur]:
                    if n in seen:
                        continue
                    seen.add(n)
                    if hw(n):
                        if path and by_id[n].get("road_id") != by_id[start].get("road_id"):
                            return path
                        continue
                    if by_id[n].get("junction"):
                        nf.append((n, path + [n]))
            frontier = nf
        return None

    out = {}
    for l in lanes:
        i = l["id"]
        if l.get("junction") or not _driving(l) or hw(i):
            continue
        for kind, graph in (("on", nxt), ("off", prv)):
            path = reach(i, graph)
            if path is not None:
                out.setdefault(i, kind)
                for j in path:
                    out.setdefault(j, "link")
    changed = True  # a ramp road split into pieces (speed sign, bridge): the whole chain is the ramp
    while changed:
        changed = False
        for l in lanes:
            i = l["id"]
            if i in out or l.get("junction") or not _driving(l) or hw(i):
                continue
            for n in nxt[i] + prv[i]:
                if out.get(n) in ("on", "off") and by_id[n].get("road_id") == l.get("road_id"):
                    out[i] = out[n]
                    changed = True
                    break
    return out


def derive(scene: dict, highway_kmh: float = HIGHWAY_KMH) -> dict:
    """Fill road_class, one_way, ramp, median_left / median_gap_m and restricted on every lane
    that doesn't have them yet. In place; returns the scene."""
    lanes = scene.get("lanes", [])
    lanes[:] = [l for l in lanes if len(l.get("centreline", [])) >= 2]
    if not lanes:
        return scene
    left_handed = bool(scene.get("left_handed", str(scene.get("coords", "")).startswith("carla")))
    fast = lambda l: l.get("speed_limit_kmh") is not None and float(l["speed_limit_kmh"]) >= highway_kmh  # noqa: E731
    # road class belongs to the road, not to its posted limit: Town04's motorway loop has 60 and
    # even 30 km/h signs on some stretches, which are still motorway
    hw_roads = {str(l["road_id"]) for l in lanes if l.get("road_id") is not None and fast(l) and not l.get("junction")}
    for l in lanes:
        on_hw_road = l.get("road_id") is not None and str(l["road_id"]) in hw_roads and not l.get("junction")
        l.setdefault("road_class", "highway" if fast(l) or on_hw_road else "urban")
        l.setdefault("restricted", None)
    ow = _one_way(lanes)
    for l in lanes:
        if "one_way" not in l and l.get("road_id") is not None and str(l["road_id"]) in ow:
            l["one_way"] = ow[str(l["road_id"])]
    if any("next" in l for l in lanes):
        rm = _ramps(lanes)
        for l in lanes:
            l.setdefault("ramp", rm.get(l["id"]))
    idx = _SegIndex(lanes)
    for i, l in enumerate(lanes):
        if "median_left" in l:
            continue
        if _driving(l) and not l.get("junction"):
            l["median_left"], gap = _median(lanes, i, idx, left_handed)
            if gap is not None:
                l["median_gap_m"] = gap
        else:
            l["median_left"] = False
    return scene


def apply_overrides(scene: dict, overrides: list[dict], mark: bool = False) -> int:
    """A profile's road.lane_overrides: set any OVERRIDE_KEYS attribute on the lanes whose id matches
    (exact or glob); later entries win; null clears restricted / ramp; note is ignored. Run after
    derive(), which fills only missing keys, so an override is never recomputed away (nothing else
    caches these: SceneMap / rules read them per lane). Returns the number of (entry, lane) matches;
    raises if an entry matches no lane. mark: each lane whose values changed gets "overridden": [keys]."""
    n, changed = 0, defaultdict(set)
    for o in overrides or []:
        hit = [l for l in scene.get("lanes", []) if fnmatch.fnmatchcase(str(l["id"]), o["lane_id"])]
        if not hit:
            raise ValueError(f"lane override {o['lane_id']!r} matches no lane in scene {scene.get('scene', '')!r}")
        for l in hit:
            for k in OVERRIDE_KEYS:
                if k not in o:
                    continue
                v = o[k].lower() if isinstance(o[k], str) else o[k]
                if l.get(k, _READ_AS.get(k)) != v:
                    changed[l["id"]].add(k)
                l[k] = v
                if k == "median_left" and not v:
                    l.pop("median_gap_m", None)  # no median: no gap
            n += 1
    if mark:
        for l in scene.get("lanes", []):
            if l["id"] in changed:
                l["overridden"] = sorted(changed[l["id"]])
    return n


def summary(scene: dict) -> dict:
    lanes = scene.get("lanes", [])
    drv = [l for l in lanes if _driving(l) and not l.get("junction")]
    return {"lanes": len(lanes), "driving_non_junction": len(drv),
            "road_class": dict(Counter(l.get("road_class") for l in lanes)),
            "one_way": dict(Counter(l.get("one_way") for l in drv)),
            "ramp": dict(Counter(l.get("ramp") for l in lanes if l.get("ramp"))),
            "median_left": sum(bool(l.get("median_left")) for l in lanes),
            "bridge": sum(bool(l.get("bridge")) for l in lanes),
            "lane_change": dict(Counter(l.get("lane_change") for l in drv)),
            "restricted": dict(Counter(l.get("restricted") for l in lanes if l.get("restricted"))),
            "max_z_m": max((max(l["z"]) for l in lanes if l.get("z")), default=None)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scene", type=Path)
    ap.add_argument("--highway-kmh", type=float, default=HIGHWAY_KMH)
    args = ap.parse_args()
    scene = derive(json.loads(args.scene.read_text()), args.highway_kmh)
    print(json.dumps(summary(scene), indent=1))


if __name__ == "__main__":
    main()
