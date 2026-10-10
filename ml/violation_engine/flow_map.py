"""Learned traffic-flow direction (Violation Engine, Phase C; Architecture R15).

docs/Violation_Engine_Architecture.md, Section 5.4: real footage with no lane map (and no
OSM `oneway`) still needs a lane direction for the wrong-way rule. Following Monteiro et al.
(IEEE ICIP 2007), the normal direction is learned from the traffic itself:

  - the ground is cut into CELL_M square cells
  - every track moving faster than MIN_KMH votes once per cell it drives through, with its mean
    heading there (one vote per track, so one wrong-way car can't outvote the traffic)
  - a cell with votes from >= MIN_TRACKS tracks, DOMINANT of them within ANGLE_DEG of one
    direction, becomes a one-way "lane" (a CELL_M segment along that direction, CELL_M wide)
  - a cell where traffic goes several ways (intersection, roundabout entry, a two-way road seen
    at this resolution) becomes a junction lane: matched, but never checked for wrong way
  - so does a cell with more than MAX_OPPOSITE tracks going the opposite way (> OPPOSITE_DEG) in
    its 3x3 neighbourhood: the other half of a two-way road. Without it, a two-way street whose
    lanes fall in neighbouring cells (position noise of a moving oblique camera, sparse traffic)
    learned one-way cells and flagged the oncoming traffic: 7 of 8 wrong-way events on the snowy
    roundabout clip, each with 13-34 opposite tracks around its cell (2026-10-10). A real
    wrong-way driver on a one-way road adds 1 opposite track, so it is still caught. Real footage
    only (two_way_check): with CARLA's exact positions each cell sits on one lane and the plain rule
    is right (flight 20261009_201727 vs the Town05 map: 84 one-way cells, 0 wrong; with the check
    36, 0 wrong), so the check would only cost coverage there

The result is a list of lane entries for lane_map.SceneMap, so the existing monitors run on it
unchanged; lines are "none" (unknown), so only direction-based rules use it (wrong way).

--check compares the learned directions with a real lane map (e.g. CARLA's), cell by cell.

Usage:
    python ml/violation_engine/flow_map.py <kinematics.csv> --check --scene ml/violation_engine/configs/scenes/Town05.json
    python ml/violation_engine/flow_map.py <kinematics.csv> --out learned_lanes.json
    (run_violations.py --site ... --learn-flow uses it directly)
"""

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from lane_map import SceneMap, angle_diff_deg

CELL_M = 4.0
MIN_KMH = 10.0
MIN_TRACKS = 5
DOMINANT = 0.8
ANGLE_DEG = 45.0  # a vote counts for a direction within this angle of it
OPPOSITE_DEG = 135.0  # a track going this far off a cell's direction goes the opposite way
MAX_OPPOSITE = 2  # opposite tracks tolerated in a cell's 3x3 neighbourhood (wrong-way drivers)
AGREE_DEG = 30.0  # --check: learned and mapped direction agree within this


def circ_mean(deg: np.ndarray) -> float:
    r = np.radians(deg)
    return math.degrees(math.atan2(np.sin(r).mean(), np.cos(r).mean()))


def learn(rows: list[dict], cell: float = CELL_M, two_way_check: bool = True) -> dict[tuple, dict]:
    """(i, j) cell -> {"dir_deg", "tracks", "share", "one_way"} from kinematics rows."""
    per = defaultdict(lambda: defaultdict(list))  # cell -> track -> headings
    for r in rows:
        h = r.get("heading_deg", "")
        if h in ("", None) or str(r.get("visible", "1")) not in ("1", "True") or float(r["speed_kmh"]) < MIN_KMH:
            continue
        x, y = float(r["x"]), float(r["y"])
        per[(math.floor(x / cell), math.floor(y / cell))][int(r["track_id"])].append(float(h))
    mean = {c: {tid: circ_mean(np.array(hs)) for tid, hs in tracks.items()} for c, tracks in per.items()}
    out = {}
    for c, tracks in per.items():
        votes = np.array(list(mean[c].values()))
        if len(votes) < MIN_TRACKS:
            continue
        best_share, best_dir = 0.0, 0.0
        for v in votes:  # candidate directions: the votes themselves
            near = np.abs((votes - v + 180) % 360 - 180) <= ANGLE_DEG
            if near.mean() > best_share:
                best_share, best_dir = float(near.mean()), circ_mean(votes[near])
        opposite = {tid for di in (-1, 0, 1) for dj in (-1, 0, 1)
                    for tid, h in mean.get((c[0] + di, c[1] + dj), {}).items()
                    if abs((h - best_dir + 180) % 360 - 180) > OPPOSITE_DEG}
        out[c] = {"dir_deg": round(best_dir, 1), "tracks": len(votes), "share": round(best_share, 2),
                  "opposite_nearby": len(opposite),
                  "one_way": best_share >= DOMINANT and (not two_way_check or len(opposite) <= MAX_OPPOSITE)}
    return out


def flow_lanes(flow: dict, cell: float = CELL_M) -> list[dict]:
    lanes = []
    for (i, j), f in flow.items():
        cx, cy = (i + 0.5) * cell, (j + 0.5) * cell
        a = math.radians(f["dir_deg"])
        dx, dy = math.cos(a) * cell / 2, math.sin(a) * cell / 2
        lanes.append({"id": f"flow_{i}_{j}", "centreline": [[round(cx - dx, 2), round(cy - dy, 2)], [round(cx + dx, 2), round(cy + dy, 2)]],
                      "width_m": cell, "lane_type": "driving", "junction": not f["one_way"], "speed_limit_kmh": None,
                      "left_line": "none", "right_line": "none", "learned": f})
    return lanes


def check(flow: dict, scene: SceneMap, cell: float = CELL_M) -> dict:
    """Learned one-way cells vs the lane map's direction at the cell centre (non-junction driving lanes)."""
    agree, disagree, mixed_on_one_way, n_one_way = 0, [], 0, 0
    for (i, j), f in flow.items():
        m = scene.match((i + 0.5) * cell, (j + 0.5) * cell)
        if m is None or m.lane.junction or m.lane.lane_type != "driving":
            continue
        if not f["one_way"]:
            mixed_on_one_way += 1
            continue
        n_one_way += 1
        d = angle_diff_deg(f["dir_deg"], m.dir_deg)
        if d <= AGREE_DEG:
            agree += 1
        else:
            disagree.append({"cell": [i, j], "learned": f["dir_deg"], "map": round(m.dir_deg, 1), "tracks": f["tracks"]})
    return {"cells_learned": len(flow), "one_way_cells_on_map_lanes": n_one_way,
            "agree": agree, "agree_frac": round(agree / n_one_way, 3) if n_one_way else None,
            "disagree": len(disagree), "disagree_examples": disagree[:10],
            "mixed_cells_on_map_lanes": mixed_on_one_way}


def read_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kinematics", type=Path, help="kinematics.csv (run_violations.py output)")
    ap.add_argument("--check", action="store_true", help="Compare with --scene's lane directions")
    ap.add_argument("--scene", type=Path, default=None)
    ap.add_argument("--cell", type=float, default=CELL_M)
    ap.add_argument("--out", type=Path, default=None, help="Write the learned lanes as a scene JSON")
    args = ap.parse_args()
    flow = learn(read_rows(args.kinematics), args.cell)
    print(f"[flow] {len(flow)} cells, {sum(f['one_way'] for f in flow.values())} one-way")
    if args.check:
        if args.scene is None:
            ap.error("--check needs --scene")
        res = check(flow, SceneMap(json.loads(args.scene.read_text())), args.cell)
        print(json.dumps(res, indent=1))
        (args.kinematics.parent / "flow_check.json").write_text(json.dumps(res, indent=1))
    if args.out:
        args.out.write_text(json.dumps({"scene": "learned_flow", "lanes": flow_lanes(flow, args.cell), "zones": [],
                                        "stop_lines": []}))
        print(f"[flow] -> {args.out}")


if __name__ == "__main__":
    main()
