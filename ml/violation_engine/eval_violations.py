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

Against the staged list, background traffic also breaks rules for real (CARLA jams, lane changes
across solid lines, a car through a long red). Those are not false alarms: an unmatched oracle
event of a non-scripted car, or a pipeline event matching one, is counted as "natural" and left
out of precision. Every other unmatched event is a false positive.

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
    """True position at time t: interpolated inside a track; up to max_dt before its first or after
    its last sample, extrapolated with the velocity there. The oracle keeps a vehicle only once it
    is VIEW_MARGIN_PX inside the frame, so the pipeline can flag a fast car entering the view
    before the oracle has it (staged flight 20261009_201727: a 49 km/h car flagged 0.5 s earlier;
    the nearest sample, used before, was 7 m away)."""
    best = None
    for tid in track_ids:
        if tid not in pos:
            continue
        ts, xy = pos[tid]
        if ts[0] <= t <= ts[-1]:
            return np.array([np.interp(t, ts, xy[:, 0]), np.interp(t, ts, xy[:, 1])])
        k, j = (0, 1) if t < ts[0] else (-1, -2)
        gap = abs(ts[k] - t)
        if gap > max_dt or (best is not None and gap >= best[0]):
            continue
        if len(ts) >= 2 and abs(ts[k] - ts[j]) > 1e-6:
            v = (xy[k] - xy[j]) / (ts[k] - ts[j])
            best = (gap, xy[k] + v * (t - ts[k]))
        else:
            best = (gap, xy[k])
    return None if best is None else best[1]


def actor_positions(flight: Path) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """actor id * 10 -> (sim times, xy) of every scripted/background vehicle on every logged tick,
    in view or not (vehicle_poses.csv; carla_frame -> simulator time via frame_times.csv)."""
    with open(flight / "frame_times.csv", newline="") as f:
        ft = list(csv.DictReader(f))
    cf = np.array([int(r["carla_frame"]) for r in ft])
    st = np.array([float(r["sim_time"]) for r in ft])
    per = defaultdict(list)
    with open(flight / "vehicle_poses.csv", newline="") as f:
        for r in csv.DictReader(f):
            per[int(r["id"])].append((float(np.interp(int(r["carla_frame"]), cf, st)), float(r["x"]), float(r["y"])))
    out = {}
    for aid, pts in per.items():
        a = np.array(sorted(pts))
        out[aid * 10] = (a[:, 0], a[:, 1:])
    return out


def scenario_truth(log_path: Path, oracle_pos: dict, key: str = "violations") -> list[dict]:
    """Staged violations (or, key="negatives", the negative acts) -> truth events; track ids = every
    oracle track of that actor (actor * 10 + part); condition = the act's (Build Plan M2 acts name one)."""
    log = json.loads(log_path.read_text())
    out = []
    for i, s in enumerate(log.get(key, [])):
        aid = int(s["actor_id"])
        tids = [t for t in oracle_pos if t // 10 == aid] or [aid * 10]
        # stage_violations.py logs simulator seconds (start_sim_s / end_sim_s), the oracle's time base
        start, end = s.get("start_s", s.get("start_sim_s")), s.get("end_s", s.get("end_sim_s"))
        for k, vtype in enumerate([s["type"]] + (s.get("also", []) if key == "violations" else [])):
            # "also": types the act raises by rule besides its own (an overtake over a solid centre
            # line is wrong-way driving too): expected events, not false alarms
            out.append({"event_id": f"staged-{key[:3]}-{i}" + (f"-{vtype}" if k else ""), "type": vtype,
                        "condition": s.get("condition") if k == 0 else None, "track_ids": tids, "start_s": start,
                        "end_s": end, "flag_s": s.get("flag_s", start), "status": "flagged"})
    return out


def per_condition(tp: list, fn: list) -> dict:
    """Staged acts that name a condition: detected as that condition / as another one / missed."""
    out = {}
    for p, g, _ in tp:
        if g.get("condition"):
            d = out.setdefault(g["condition"], {"detected": 0, "other_condition": 0, "missed": 0})
            d["detected" if p.get("condition") == g["condition"] else "other_condition"] += 1
    for g in fn:
        if g.get("condition"):
            out.setdefault(g["condition"], {"detected": 0, "other_condition": 0, "missed": 0})["missed"] += 1
    return dict(sorted(out.items()))


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


def score(pred: list[dict], truth: list[dict], truth_pos: dict, natural=None) -> dict:
    """natural(event) -> bool (staged scoring only): an unmatched event that is a real violation by
    background traffic, not a false alarm; reported apart, not in precision."""
    pred_c = [e for e in pred if e["status"] in COUNTED_STATUS]
    truth_c = [e for e in truth if e["status"] in COUNTED_STATUS]
    tp, fp, fn = match(pred_c, truth_c, truth_pos)
    nat = [e for e in fp if natural is not None and natural(e)]
    fp = [e for e in fp if not (natural is not None and natural(e))]
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
        if natural is not None:
            per[t]["natural"] = sum(1 for e in nat if e["type"] == t)
    return {"per_type": per, "per_condition": per_condition(tp, fn),
            "natural_events": [(e["event_id"], e["type"], e["track_ids"], e["flag_s"]) for e in nat],
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
    ap.add_argument("--flight", type=Path, default=None,
                    help="Staged flight folder: match the staged acts against each car's full true path "
                         "(vehicle_poses.csv), not only the oracle's in-view part")
    ap.add_argument("--ignore-types", nargs="*", default=["red_light"],
                    help="Violation types left out everywhere (default: red_light, out of scope since 2026-10-09)")
    ap.add_argument("--out", type=Path, default=None, help="Write the result JSON here (default: <pred>/eval.json)")
    args = ap.parse_args()
    ignore = set(args.ignore_types)

    def load_events(d: Path) -> list[dict]:
        return [e for e in json.loads((d / "violations.json").read_text()) if e["type"] not in ignore]

    oracle_pos = load_positions(args.oracle / "kinematics.csv")
    result = {"pred": str(args.pred), "oracle": str(args.oracle), "ignored_types": sorted(ignore),
              "vs_oracle": score(load_events(args.pred), load_events(args.oracle), oracle_pos)}
    if args.scenario:
        staged_pos = actor_positions(args.flight) if args.flight else oracle_pos
        staged = [s for s in scenario_truth(args.scenario, {} if args.flight else oracle_pos) if s["type"] not in ignore]
        log = json.loads(args.scenario.read_text())
        scripted = {int(a[k]) for a in log.get("acts", []) for k in ("actor_id", "lead_actor_id") if k in a}
        # an oracle event of background traffic (not a scripted car) is a real, unstaged violation
        oracle_ev = load_events(args.oracle)
        background = [e for e in oracle_ev if e["status"] in COUNTED_STATUS
                      and all(t // 10 not in scripted for t in e["track_ids"])]
        bg_ids = {e["event_id"] for e in background}
        result["oracle_vs_staged"] = score(oracle_ev, staged, staged_pos, natural=lambda e: e["event_id"] in bg_ids)  # L1
        # a pipeline event that matches such a background oracle event is natural too
        tp_bg, _, _ = match([e for e in load_events(args.pred) if e["status"] in COUNTED_STATUS], background, oracle_pos)
        pred_natural = {p["event_id"] for p, _, _ in tp_bg}
        result["pipeline_vs_staged"] = score(load_events(args.pred), staged, staged_pos,
                                             natural=lambda e: e["event_id"] in pred_natural)  # L2
        # negative acts (staged to look close to a violation but legal): any counted event of their type
        # on them is a false alarm the scenario was built to provoke
        negs = [s for s in scenario_truth(args.scenario, {} if args.flight else oracle_pos, "negatives") if s["type"] not in ignore]
        for name, d in (("oracle", args.oracle), ("pipeline", args.pred)):
            hit, _, _ = match([e for e in load_events(d) if e["status"] in COUNTED_STATUS], negs, staged_pos)
            result[f"{name}_on_negatives"] = {"negatives": len(negs), "triggered": len(hit),
                                              "events": [(p["event_id"], g["type"], g.get("condition")) for p, g, _ in hit]}
    out = args.out or args.pred / "eval.json"
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
