"""Score detected violation events against the truth (Violation Engine, evaluation L1/L2).

docs/Violation_Engine_Architecture.md, Section 6. Truth comes from the oracle run
(run_violations.py --oracle: the same rules on CARLA's true vehicle positions) and, for
staged flights, from the scenario log (simulation/violation_scenarios/stage_violations.py),
which lists the violations that were staged on purpose.

A detected event matches a true event when
  - the type is the same,
  - their time spans overlap, with TIME_TOL_S of slack at both ends, and
  - the true vehicle is within DIST_TOL_M of the detected event's location at its flag time
    (positions from the oracle's kinematics.csv, so moving vehicles are compared at the same moment).
One-to-one, greedy by smallest distance. Only events whose status counts (flagged,
needs_review) take part; suppressed / possible_breakdown are reported separately.

Usage:
    python ml/violation_engine/eval_violations.py <pipeline violations dir> <oracle violations dir>
    python ml/violation_engine/eval_violations.py <pipeline dir> <oracle dir> --scenario <flight>/scenario_log.json
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from events import COUNTED_STATUS

TIME_TOL_S = 1.0
DIST_TOL_M = 3.0


def load_events(d: Path) -> list[dict]:
    return json.loads((d / "violations.json").read_text())


def load_positions(kin_csv: Path) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """track_id -> (times, xy) from a kinematics.csv."""
    per = defaultdict(list)
    with open(kin_csv, newline="") as f:
        for r in csv.DictReader(f):
            per[int(r["track_id"])].append((float(r["time_s"]), float(r["x"]), float(r["y"])))
    out = {}
    for tid, pts in per.items():
        a = np.array(sorted(pts))
        out[tid] = (a[:, 0], a[:, 1:])
    return out


def position_at(pos: dict, track_ids: list[int], t: float, max_dt: float = 1.0):
    best = None
    for tid in track_ids:
        if tid not in pos:
            continue
        ts, xy = pos[tid]
        k = int(np.abs(ts - t).argmin())
        if abs(ts[k] - t) <= max_dt and (best is None or abs(ts[k] - t) < best[0]):
            best = (abs(ts[k] - t), xy[k])
    return None if best is None else best[1]


def scenario_truth(log_path: Path, oracle_pos: dict) -> list[dict]:
    """Staged violations -> truth events; track ids = every oracle track of that actor (actor * 10 + part)."""
    log = json.loads(log_path.read_text())
    out = []
    for i, s in enumerate(log.get("violations", [])):
        aid = int(s["actor_id"])
        tids = [t for t in oracle_pos if t // 10 == aid]
        out.append({"event_id": f"staged-{i}", "type": s["type"], "track_ids": tids, "start_s": s["start_s"],
                    "end_s": s["end_s"], "flag_s": s.get("flag_s", s["start_s"]), "status": "flagged"})
    return out


def match(pred: list[dict], truth: list[dict], truth_pos: dict) -> tuple[list, list, list]:
    pairs = []
    for i, p in enumerate(pred):
        for j, g in enumerate(truth):
            if p["type"] != g["type"]:
                continue
            if p["start_s"] > g["end_s"] + TIME_TOL_S or g["start_s"] > p["end_s"] + TIME_TOL_S:
                continue
            gxy = position_at(truth_pos, g["track_ids"], p["flag_s"])
            if gxy is None:
                continue
            d = math.hypot(gxy[0] - p["x"], gxy[1] - p["y"])
            if d <= DIST_TOL_M:
                pairs.append((d, i, j))
    used_p, used_g, tp = set(), set(), []
    for d, i, j in sorted(pairs):
        if i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            tp.append((pred[i], truth[j], d))
    fp = [p for i, p in enumerate(pred) if i not in used_p]
    fn = [g for j, g in enumerate(truth) if j not in used_g]
    return tp, fp, fn


def score(pred: list[dict], truth: list[dict], truth_pos: dict) -> dict:
    pred_c = [e for e in pred if e["status"] in COUNTED_STATUS]
    truth_c = [e for e in truth if e["status"] in COUNTED_STATUS]
    tp, fp, fn = match(pred_c, truth_c, truth_pos)
    types = sorted({e["type"] for e in pred_c + truth_c})
    per = {}
    for t in types:
        a = sum(1 for p, _, _ in tp if p["type"] == t)
        b = sum(1 for e in fp if e["type"] == t)
        c = sum(1 for e in fn if e["type"] == t)
        prec = a / (a + b) if a + b else None
        rec = a / (a + c) if a + c else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if (a + b) and (a + c) else None)
        per[t] = {"tp": a, "fp": b, "fn": c, "precision": _r(prec), "recall": _r(rec), "f1": _r(f1),
                  "flag_delay_s_median": _r(np.median([p["flag_s"] - g["flag_s"] for p, g, _ in tp if p["type"] == t]))
                  if a else None}
    return {"per_type": per,
            "fp_events": [(e["event_id"], e["type"], e["track_ids"], e["flag_s"]) for e in fp],
            "fn_events": [(e["event_id"], e["type"], e["track_ids"], e["flag_s"]) for e in fn],
            "not_counted_pred": dict(Counter(f"{e['type']}/{e['status']}" for e in pred if e["status"] not in COUNTED_STATUS))}


def _r(v):
    return None if v is None else round(float(v), 3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pred", type=Path, help="Folder with the pipeline's violations.json")
    ap.add_argument("oracle", type=Path, help="Folder with the oracle's violations.json + kinematics.csv")
    ap.add_argument("--scenario", type=Path, default=None, help="scenario_log.json of a staged flight")
    ap.add_argument("--out", type=Path, default=None, help="Write the result JSON here (default: <pred>/eval.json)")
    args = ap.parse_args()

    oracle_pos = load_positions(args.oracle / "kinematics.csv")
    result = {"pred": str(args.pred), "oracle": str(args.oracle),
              "vs_oracle": score(load_events(args.pred), load_events(args.oracle), oracle_pos)}
    if args.scenario:
        staged = scenario_truth(args.scenario, oracle_pos)
        result["oracle_vs_staged"] = score(load_events(args.oracle), staged, oracle_pos)  # L1
        result["pipeline_vs_staged"] = score(load_events(args.pred), staged, oracle_pos)  # L2
    out = args.out or args.pred / "eval.json"
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
