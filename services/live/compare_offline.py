"""Online vs offline gap of the live pipeline (Build Plan M4 "Measure").

simulate: runs the online stages (online.py: re-linking, forward/fixed-lag kinematics, incremental
    engine) on an offline tracker output (trajectories.csv of process_recorded_flight.py), CPU only.
    Isolates what the online kinematics + engine cost, with detection and tracking held equal.
compare: matches a live (or simulated) session's events and speeds to the offline run's.
    events: same type, flag time within --tol-s (2 s), and track overlap (the two tracks within
    3 m of each other on >= half of their common frames around the event); recall / precision of
    the counted events (flagged + needs_review). Also the flag delay (live flag_s - offline flag_s).
    speed: live rows vs offline rows on the same frame, nearest position < 2 m, both visible.
    truth: kinematics.check of both against CARLA's true vehicle motion (flights with vehicle_poses).

Usage:
    venv\\Scripts\\python.exe services/live/compare_offline.py simulate 20261009_201727 --zones <scenario_log.json> --out <dir>
    venv\\Scripts\\python.exe services/live/compare_offline.py compare <live session dir> --flight 20261009_201727 \\
        --offline ml/data/results/recorded_flight_validation/20261009_201727/tracktrack_ours/violations_m2m
"""

import argparse
import csv
import json
import sys
import time
from argparse import Namespace
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from events import COUNTED_STATUS  # noqa: E402

FLIGHTS = REPO / "simulation" / "data_export" / "recorded_flights"
OFFLINE = REPO / "ml" / "data" / "results" / "recorded_flight_validation"
TRACK_MATCH_M = 3.0


def load_kin(path: Path) -> dict[int, list[dict]]:
    """frame -> rows (numbers parsed)."""
    out = defaultdict(list)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out[int(r["frame"])].append({"track_id": int(r["track_id"]), "t": float(r["time_s"]), "x": float(r["x"]),
                                         "y": float(r["y"]), "speed": float(r["speed_kmh"]),
                                         "visible": int(r["visible"])})
    return out


def track_positions(kin: dict[int, list[dict]], tids: set[int], t0: float, t1: float) -> dict[int, tuple]:
    out = {}
    for fr, rows in kin.items():
        for r in rows:
            if r["track_id"] in tids and t0 <= r["t"] <= t1:
                out[fr] = (r["x"], r["y"])
    return out


def tracks_overlap(live_kin, off_kin, le: dict, oe: dict) -> bool:
    t0 = min(le["start_s"], oe["start_s"]) - 1.0
    t1 = max(le["flag_s"], oe["flag_s"]) + 1.0
    a = track_positions(live_kin, set(le["track_ids"]), t0, t1)
    b = track_positions(off_kin, set(oe["track_ids"]), t0, t1)
    common = set(a) & set(b)
    if len(common) < 3:
        return False
    near = sum(np.hypot(a[f][0] - b[f][0], a[f][1] - b[f][1]) <= TRACK_MATCH_M for f in common)
    return near >= 0.5 * len(common)


def match_events(live: list[dict], off: list[dict], live_kin, off_kin, tol_s: float) -> dict:
    pairs, used = [], set()
    for oi, oe in enumerate(off):
        best = None
        for li, le in enumerate(live):
            if li in used or le["type"] != oe["type"] or abs(le["flag_s"] - oe["flag_s"]) > tol_s:
                continue
            if not tracks_overlap(live_kin, off_kin, le, oe):
                continue
            d = abs(le["flag_s"] - oe["flag_s"])
            if best is None or d < best[0]:
                best = (d, li)
        if best is not None:
            used.add(best[1])
            pairs.append((oi, best[1]))
    return {"pairs": pairs, "unmatched_offline": [i for i in range(len(off)) if i not in {p[0] for p in pairs}],
            "unmatched_live": [i for i in range(len(live)) if i not in used]}


def event_report(live: list[dict], off: list[dict], live_kin, off_kin, tol_s: float) -> dict:
    lc = [e for e in live if e["status"] in COUNTED_STATUS]
    oc = [e for e in off if e["status"] in COUNTED_STATUS]
    m = match_events(lc, oc, live_kin, off_kin, tol_s)
    delays = [lc[li]["flag_s"] - oc[oi]["flag_s"] for oi, li in m["pairs"]]
    by_type = defaultdict(lambda: {"offline": 0, "live": 0, "matched": 0})
    for e in oc:
        by_type[e["type"]]["offline"] += 1
    for e in lc:
        by_type[e["type"]]["live"] += 1
    for oi, _ in m["pairs"]:
        by_type[oc[oi]["type"]]["matched"] += 1
    n = len(m["pairs"])
    return {"tol_s": tol_s, "offline_counted": len(oc), "live_counted": len(lc), "matched": n,
            "recall_vs_offline": round(n / len(oc), 3) if oc else None,
            "precision_vs_offline": round(n / len(lc), 3) if lc else None,
            "flag_delay_s": {"median": round(float(np.median(delays)), 2), "max": round(float(np.max(delays)), 2),
                             "min": round(float(np.min(delays)), 2)} if delays else None,
            "by_type": dict(by_type),
            "missed_offline": [{k: oc[i][k] for k in ("event_id", "type", "condition", "flag_s", "track_ids")}
                               for i in m["unmatched_offline"]],
            "extra_live": [{k: lc[i][k] for k in ("event_id", "type", "condition", "flag_s", "track_ids")}
                           for i in m["unmatched_live"]]}


def speed_report(live_kin, off_kin) -> dict:
    d_all, d_mov = [], []
    pairs_tracks = defaultdict(set)
    for fr, lrows in live_kin.items():
        orows = [r for r in off_kin.get(fr, []) if r["visible"]]
        if not orows:
            continue
        ox = np.array([[r["x"], r["y"]] for r in orows])
        for r in lrows:
            if not r["visible"]:
                continue
            d = np.hypot(ox[:, 0] - r["x"], ox[:, 1] - r["y"])
            j = int(d.argmin())
            if d[j] > 2.0:
                continue
            diff = r["speed"] - orows[j]["speed"]
            d_all.append(diff)
            if orows[j]["speed"] > 5.0:
                d_mov.append(diff)
            pairs_tracks[orows[j]["track_id"]].add(r["track_id"])
    q = lambda a, p: round(float(np.percentile(np.abs(a), p)), 2) if len(a) else None  # noqa: E731
    frag = [len(v) for v in pairs_tracks.values()]
    return {"rows_matched": len(d_all),
            "abs_diff_kmh_all": {"median": q(d_all, 50), "p95": q(d_all, 95)},
            "abs_diff_kmh_moving": {"rows": len(d_mov), "median": q(d_mov, 50), "p95": q(d_mov, 95),
                                    "bias": round(float(np.median(d_mov)), 2) if d_mov else None},
            "live_ids_per_offline_track": {"mean": round(float(np.mean(frag)), 2) if frag else None,
                                           "max": int(max(frag)) if frag else None}}


def truth_report(kin_csv: Path, raw_csv: Path, flight: Path) -> dict:
    from kinematics import check
    rows = []
    with open(kin_csv, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"frame": int(r["frame"]), "time_s": float(r["time_s"]), "track_id": int(r["track_id"]),
                         "x": float(r["x"]), "y": float(r["y"]), "speed_kmh": float(r["speed_kmh"]),
                         "visible": int(r["visible"]),
                         "heading_deg": float(r["heading_deg"]) if r["heading_deg"] not in ("", None) else ""})
    return check(rows, raw_csv, flight)


def compare(args) -> dict:
    live_dir = args.session
    flight = FLIGHTS / args.flight
    off_dir = args.offline or OFFLINE / args.flight / "tracktrack_ours" / "violations_m2m"
    live = json.loads((live_dir / "violations.json").read_text())
    off = json.loads((off_dir / "violations.json").read_text())
    live_kin, off_kin = load_kin(live_dir / "kinematics.csv"), load_kin(off_dir / "kinematics.csv")
    out = {"live": str(live_dir), "offline": str(off_dir),
           "events": event_report(live, off, live_kin, off_kin, args.tol_s),
           "speed_vs_offline": speed_report(live_kin, off_kin),
           "tracks": {"live_track_ids": len({r["track_id"] for rs in live_kin.values() for r in rs}),
                      "offline_track_ids": len({r["track_id"] for rs in off_kin.values() for r in rs}),
                      "frames_live": len(live_kin), "frames_offline": len(off_kin)}}
    if not args.no_truth and (flight / "vehicle_poses.csv").exists():
        print("[compare] speed vs CARLA truth (kinematics.check) ...")
        raw_off = off_dir / "trajectories_world.csv"
        out["truth_live"] = truth_report(live_dir / "kinematics.csv", live_dir / "tracks.csv", flight)
        out["truth_offline"] = truth_report(off_dir / "kinematics.csv", raw_off, flight)
    return out


def simulate(args) -> Path:
    """The online stages over an offline tracker output, frame by frame (no GPU)."""
    from ground_coords import FlightCamera
    from kinematics import edge_mask
    from online import LAG_S, IncrementalEngine, OnlineKinematics
    from pipeline import build_engine, event_dict
    flight = FLIGHTS / args.flight
    traj = args.trajectories or OFFLINE / args.flight / "tracktrack_ours" / "trajectories.csv"
    cam = FlightCamera(flight)
    with open(flight / "frame_times.csv", newline="") as f:
        sim = {int(r["frame"]): float(r["sim_time"]) for r in csv.DictReader(f)}
    meta = json.loads((flight / "metadata.json").read_text())
    ns = Namespace(profile=args.profile, params=None, scene=args.scene, zones=args.zones, flat_ground=False)
    engine, surface, _ = build_engine(ns, meta.get("map", ""), args.out.name)
    kin = OnlineKinematics(lag_s=LAG_S if args.lag is None else args.lag, mode=args.kin, relink=not args.no_relink)
    ie = IncrementalEngine(engine)
    by_frame = defaultdict(list)
    with open(traj, newline="") as f:
        for r in csv.DictReader(f):
            by_frame[int(r["frame"])].append(r)
    args.out.mkdir(parents=True, exist_ok=True)
    kin_f = open(args.out / "kinematics.csv", "w", newline="")
    trk_f = open(args.out / "tracks.csv", "w", newline="")
    tw = csv.writer(trk_f)
    tw.writerow(["frame", "time_s", "track_id", "tracker_id", "class", "cx", "cy", "w", "h", "conf", "wx", "wy", "pose_exact"])
    kw, final, n_open = None, {}, 0
    frames = sorted(sim)[::args.skip]
    t_start = time.perf_counter()

    def feed(batches):
        nonlocal kw, n_open
        for te, fr, rows in batches:
            if rows:
                if kw is None:
                    kw = csv.DictWriter(kin_f, fieldnames=list(rows[0]))
                    kw.writeheader()
                kw.writerows(rows)
            o, c = ie.step(te, fr, rows)
            n_open += len(o)
            for e in c:
                final[e.event_id] = event_dict(e, args.out.name, False)

    for fr in frames:
        rs, t = by_frame.get(fr, []), sim[fr]
        dets = []
        if rs:
            b = np.array([[float(r[k]) for k in ("cx", "cy", "w", "h")] for r in rs])
            g = cam.to_ground_on_roads(fr, b[:, 0], b[:, 1], surface) if surface is not None else cam.to_ground(fr, b[:, 0], b[:, 1])
            if g is not None:
                ok = edge_mask(b[:, 0], b[:, 1], b[:, 2], b[:, 3], cam.W, cam.H)
                for i, r in enumerate(rs):
                    dets.append({"tracker_id": int(r["track_id"]), "cls": r["class"], "conf": float(r["conf"]),
                                 "x": float(g[i, 0]), "y": float(g[i, 1]), "edge_ok": bool(ok[i])})
        kin.update(fr, t, dets)
        for d, r in zip(dets, rs):
            tw.writerow([fr, t, kin.id_map.get(d["tracker_id"], -1), d["tracker_id"], d["cls"], r["cx"], r["cy"], r["w"],
                         r["h"], d["conf"], round(d["x"], 3), round(d["y"], 3), 1])
        feed(kin.emit(t))
    feed(kin.flush())
    _, c = ie.finish()
    for e in c:
        final[e.event_id] = event_dict(e, args.out.name, False)
    kin_f.close()
    trk_f.close()
    (args.out / "violations.json").write_text(json.dumps(list(final.values()), indent=1))
    el = time.perf_counter() - t_start
    (args.out / "summary.json").write_text(json.dumps({
        "mode": "simulate", "trajectories": str(traj), "frames": len(frames), "skip": args.skip, "kin": args.kin,
        "lag_s": kin.lag, "relink": not args.no_relink, "events_final": len(final), "events_opened": n_open,
        "kinematics_stats": dict(kin.stats), "cpu_ms_per_frame_online_stages": round(1000 * el / len(frames), 2)}, indent=1))
    print(f"[simulate] {len(frames)} frames, {len(final)} events, {1000 * el / len(frames):.1f} ms/frame -> {args.out}")
    return args.out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate")
    s.add_argument("flight")
    s.add_argument("--trajectories", type=Path, default=None)
    s.add_argument("--scene", type=Path, default=None)
    s.add_argument("--zones", type=Path, default=None)
    s.add_argument("--profile", type=Path, default=None)
    s.add_argument("--kin", choices=["fixedlag", "filter"], default="fixedlag")
    s.add_argument("--lag", type=float, default=None)
    s.add_argument("--skip", type=int, default=1)
    s.add_argument("--no-relink", action="store_true")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--compare", type=Path, default=None, help="Also compare; write the report to this JSON")
    s.add_argument("--offline", type=Path, default=None)
    s.add_argument("--no-truth", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("session", type=Path)
    c.add_argument("--flight", required=True)
    c.add_argument("--offline", type=Path, default=None)
    c.add_argument("--tol-s", type=float, default=2.0)
    c.add_argument("--no-truth", action="store_true")
    c.add_argument("--out", type=Path, default=None, help="Write the report JSON here (default: <session>/compare.json)")
    args = ap.parse_args()
    if args.cmd == "simulate":
        simulate(args)
        if not args.compare:
            return
        args.session, args.tol_s = args.out, 2.0
        report = compare(args)
        args.compare.write_text(json.dumps(report, indent=1))
    else:
        report = compare(args)
        (args.out or args.session / "compare.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in report if k in ("events", "speed_vs_offline", "tracks")}, indent=1)[:4000])


if __name__ == "__main__":
    main()
