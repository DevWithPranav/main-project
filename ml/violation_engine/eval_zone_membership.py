"""Zone membership accuracy (PRD 27.6): does the pipeline put a vehicle inside a zone when CARLA's truth does?

For every pipeline row (kinematics.csv of a pipeline run, `visible` rows only) the vehicle's CARLA truth position at that
same frame (vehicle_poses.csv, via frame_times.csv, interpolated in time between logged frames) is found: the nearest true vehicle within --match-m. Then, per zone
polygon, "inside" is decided twice (pipeline position, true position) and compared. Samples that cannot matter are far
from every zone and would only inflate the figure, so the headline number counts only RELEVANT samples: truth or pipeline
inside the zone, or truth within --near m of it. Also reported: all matched samples, the confusion counts, the position
error, and how many rows found no true vehicle. Samples are rows (a parked car adds one per frame): this is a per-sample
accuracy, not a per-event one (that is eval_violations.py).

Usage:
    venv/Scripts/python.exe ml/violation_engine/eval_zone_membership.py <flight dir> <pipeline kinematics.csv> \
        --zones <flight>/scenario_log.json [--zones more.json] [--near 10] [--match-m 3] [--out report.json]
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import contains_xy, distance, points
from shapely.geometry import Polygon

TARGET = 0.98  # PRD 27.6


def load_zones(paths: list[Path]) -> list[dict]:
    zones = []
    for p in paths:
        d = json.loads(p.read_text(encoding="utf-8"))
        items = d.get("zones", d) if isinstance(d, dict) else d
        for z in items:
            if z.get("polygon") and len(z["polygon"]) >= 3:
                zones.append({"id": z.get("id") or z.get("name") or f"zone{len(zones)}", "type": z.get("type", "?"),
                              "poly": Polygon(z["polygon"])})
    return zones


def truth_by_frame(flight: Path, frames: set[int], max_gap: int = 10) -> dict[int, np.ndarray]:
    """True vehicle positions at each pipeline frame. vehicle_poses.csv is not logged for every frame, so each true
    vehicle's path is interpolated linearly between its two logged CARLA frames around the wanted one (only when they are
    at most `max_gap` CARLA frames apart, so a vehicle that left the logged set is not extrapolated)."""
    ft = pd.read_csv(flight / "frame_times.csv").set_index("frame")["carla_frame"]
    cf_of = {int(f): int(ft[f]) for f in frames if f in ft.index}
    vp = pd.read_csv(flight / "vehicle_poses.csv", usecols=["carla_frame", "id", "x", "y"]).sort_values(["id", "carla_frame"])
    paths = {int(i): (g.carla_frame.to_numpy(), g.x.to_numpy(), g.y.to_numpy()) for i, g in vp.groupby("id")}
    out = {}
    for f, c in cf_of.items():
        pts = []
        for cfs, xs, ys in paths.values():
            k = int(np.searchsorted(cfs, c))
            if k < len(cfs) and cfs[k] == c:
                pts.append((xs[k], ys[k]))
            elif 0 < k < len(cfs) and cfs[k] - cfs[k - 1] <= max_gap:
                w = (c - cfs[k - 1]) / (cfs[k] - cfs[k - 1])
                pts.append((xs[k - 1] + w * (xs[k] - xs[k - 1]), ys[k - 1] + w * (ys[k] - ys[k - 1])))
        if pts:
            out[f] = np.array(pts)
    return out


def evaluate(flight: Path, kin: Path, zone_files: list[Path], near_m: float, match_m: float) -> dict:
    zones = load_zones(zone_files)
    rows = pd.read_csv(kin)
    if "visible" in rows:
        rows = rows[rows.visible == 1]
    truth = truth_by_frame(flight, set(rows.frame.astype(int)))
    px, py, tx, ty, err = [], [], [], [], []
    unmatched = no_truth_frame = 0
    for f, g in rows.groupby("frame"):
        t = truth.get(int(f))
        if t is None:
            no_truth_frame += len(g)
            continue
        for x, y in g[["x", "y"]].to_numpy():
            d = np.hypot(t[:, 0] - x, t[:, 1] - y)
            j = int(np.argmin(d))
            if d[j] > match_m:
                unmatched += 1
                continue
            px.append(x); py.append(y); tx.append(t[j, 0]); ty.append(t[j, 1]); err.append(float(d[j]))
    px, py, tx, ty = map(np.array, (px, py, tx, ty))
    tp_pts = points(tx, ty)
    per_zone, tot = [], {"relevant": 0, "agree": 0, "all": len(px), "all_agree": 0}
    for z in zones:
        in_t = contains_xy(z["poly"], tx, ty)
        in_p = contains_xy(z["poly"], px, py)
        near = distance(z["poly"], tp_pts) <= near_m
        rel = in_t | in_p | near
        agree = in_t == in_p
        c = {"TP": int((in_t & in_p & rel).sum()), "FP": int((~in_t & in_p & rel).sum()),
             "FN": int((in_t & ~in_p & rel).sum()), "TN": int((~in_t & ~in_p & rel).sum())}
        n = int(rel.sum())
        per_zone.append({"zone": z["id"], "type": z["type"], "relevant_samples": n, **c,
                         "accuracy_relevant": round(float(agree[rel].mean()), 4) if n else None,
                         "samples_truth_inside": int(in_t.sum())})
        tot["relevant"] += n; tot["agree"] += int(agree[rel].sum()); tot["all_agree"] += int(agree.sum())
    return {"flight": flight.name, "pipeline_kinematics": str(kin), "zones": len(zones), "near_m": near_m, "match_m": match_m,
            "pipeline_rows": int(len(rows)), "matched_samples": int(len(px)), "unmatched_rows": unmatched,
            "rows_in_frames_without_truth": no_truth_frame,
            "position_error_m": {"median": round(float(np.median(err)), 3), "p95": round(float(np.percentile(err, 95)), 3)} if err else None,
            "per_zone": per_zone,
            "accuracy_relevant": round(tot["agree"] / tot["relevant"], 4) if tot["relevant"] else None,
            "accuracy_all_matched": round(tot["all_agree"] / (tot["all"] * max(len(zones), 1)), 4) if tot["all"] else None,
            "relevant_samples": tot["relevant"], "target": TARGET,
            "target_met": bool(tot["relevant"] and tot["agree"] / tot["relevant"] >= TARGET)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path)
    ap.add_argument("kinematics", type=Path)
    ap.add_argument("--zones", type=Path, action="append", required=True)
    ap.add_argument("--near", type=float, default=10.0)
    ap.add_argument("--match-m", type=float, default=3.0)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    res = evaluate(a.flight, a.kinematics, a.zones, a.near, a.match_m)
    txt = json.dumps(res, indent=1)
    print(txt)
    if a.out:
        a.out.write_text(txt, encoding="utf-8")


if __name__ == "__main__":
    main()
