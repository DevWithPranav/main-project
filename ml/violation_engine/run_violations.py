"""Run the violation engine on a recorded CARLA flight (Violation Engine, Layers 2-7).

docs/Violation_Engine_Architecture.md. Two sources of tracks:

  pipeline (default)  our detector + tracker output (trajectories_final.csv): pixels ->
                      ground metres (ground_coords.py) -> Kalman + RTS (kinematics.py)
  --oracle            CARLA's true vehicle centres (vehicle_poses.csv), kept only while the
                      vehicle is inside the camera's view, same smoother. This is evaluation
                      level L1: rules on perfect perception.

Then map matching + the seven monitors (rules.py) -> events.

Outputs in --out (default: <trajectories folder>/violations/ or <flight>/violations_oracle/):
    kinematics.csv, violations.json, violations.csv, summary.json

Usage:
    python ml/violation_engine/run_violations.py <flight dir> --scene ml/violation_engine/configs/scenes/Town05.json
    python ml/violation_engine/run_violations.py <flight dir> --scene ... --oracle
    python ml/violation_engine/run_violations.py <flight dir> --scene ... --zones extra_zones.json
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from events import COUNTED_STATUS, write_events
from ground_coords import FlightCamera, add_world_columns, true_centres
from kinematics import compute, smooth_track, write_rows
from lane_map import SceneMap
from rules import Engine

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "recorded_flight_validation"
ORACLE_MEAS_SIGMA = 0.1  # true positions, but stamped with wall-clock times that jitter (~0.02-0.05 s per tick)
VIEW_MARGIN_PX = 20
DEFAULT_SITE_SPEED_SIGMA_KMH = 3.0  # real footage, unless the site file measured its own


def pipeline_rows(flight: Path, traj: Path, cam: FlightCamera) -> list[dict]:
    with open(traj, newline="") as f:
        header = next(csv.reader(f))
    if "wx" not in header or "pose_exact" not in header:
        n, missing = add_world_columns(traj, cam)
        print(f"[ground] wx, wy added to {n} rows ({missing} without a camera pose)")
    return compute(traj, cam.W, cam.H)


def oracle_rows(flight: Path, cam: FlightCamera) -> list[dict]:
    """True vehicle centres while inside the camera view, as kinematics rows (track_id = actor id)."""
    actors = json.loads((flight / "actors.json").read_text())
    cf_frame = {cf: fr for fr, cf in cam.frame_cf.items()}
    cfs = np.array(sorted(cf_frame))
    with open(flight / "frame_times.csv", newline="") as f:
        ft = list(csv.DictReader(f))
    tcol = "sim_time" if ft and "sim_time" in ft[0] else "time_s"
    ft_cf = np.array([int(r["carla_frame"]) for r in ft])
    ft_t = np.array([float(r[tcol]) for r in ft])
    per_actor = defaultdict(list)
    for c, cents in true_centres(flight).items():
        k = int(np.abs(cfs - c).argmin())
        frame = int(cf_frame[int(cfs[k])])
        if not cents:
            continue
        ids = list(cents)
        xyz = np.array([cents[i] for i in ids])
        uv = cam.to_pixels(frame, xyz)
        if uv is None:
            continue
        inside = ((uv[:, 0] >= VIEW_MARGIN_PX) & (uv[:, 0] <= cam.W - VIEW_MARGIN_PX) &
                  (uv[:, 1] >= VIEW_MARGIN_PX) & (uv[:, 1] <= cam.H - VIEW_MARGIN_PX))
        t = float(np.interp(c, ft_cf, ft_t))
        for i, aid in enumerate(ids):
            if inside[i]:
                per_actor[aid].append((t, frame, xyz[i, 0], xyz[i, 1]))
    rows = []
    for aid, pts in per_actor.items():
        pts.sort()
        a = np.array(pts)
        # split where the vehicle left the view for > 2 s: a new "track", like the real pipeline
        breaks = np.flatnonzero(np.diff(a[:, 0]) > 2.0) + 1
        for part, seg in enumerate(np.split(a, breaks)):
            if len(seg) < 3:
                continue
            k = smooth_track(seg[:, 0], seg[:, 2], seg[:, 3], np.ones(len(seg), bool), meas_sigma=ORACLE_MEAS_SIGMA)
            cls = actor_class(actors.get(aid, {}))
            for i in range(len(seg)):
                h = k["heading_deg"][i]
                rows.append({"frame": int(seg[i, 1]), "time_s": round(float(seg[i, 0]), 4),
                             "track_id": int(aid) * 10 + part, "class": cls, "conf": 1.0, "visible": 1,
                             "x": round(float(k["x"][i]), 3), "y": round(float(k["y"][i]), 3),
                             "vx": round(float(k["vx"][i]), 3), "vy": round(float(k["vy"][i]), 3),
                             "speed_kmh": round(float(k["speed_kmh"][i]), 2),
                             "speed_sigma_kmh": round(float(k["speed_sigma_kmh"][i]), 2),
                             "heading_deg": "" if math.isnan(h) else round(float(h), 1)})
    rows.sort(key=lambda r: (r["time_s"], r["track_id"]))
    return rows


def plan_rows(plan: dict, gap_s: float = 6.0) -> tuple[list[dict], dict[int, dict]]:
    """Kinematics rows from a stage_violations.py --plan-only log: every act's planned path
    (10 Hz samples) played one after the other. Returns rows and track_id -> act."""
    rows, owner, t0 = [], {}, 0.0
    for i, act in enumerate(plan["acts"]):
        for j, key in enumerate(("samples", "lead_samples")):
            if key not in act:
                continue
            a = np.array(act[key])
            tid = (i + 1) * 10 + j
            owner[tid] = {**{k: v for k, v in act.items() if not k.endswith("samples")}, "act": i, "role": "lead" if j else "main"}
            t = a[:, 0] + t0
            k = smooth_track(t, a[:, 1], a[:, 2], np.ones(len(a), bool), meas_sigma=ORACLE_MEAS_SIGMA)
            for n in range(len(a)):
                h = k["heading_deg"][n]
                rows.append({"frame": int(round(t[n] * 10)), "time_s": round(float(t[n]), 3), "track_id": tid,
                             "class": "car", "conf": 1.0, "visible": 1, "x": round(float(k["x"][n]), 3),
                             "y": round(float(k["y"][n]), 3), "vx": round(float(k["vx"][n]), 3),
                             "vy": round(float(k["vy"][n]), 3), "speed_kmh": round(float(k["speed_kmh"][n]), 2),
                             "speed_sigma_kmh": round(float(k["speed_sigma_kmh"][n]), 2),
                             "heading_deg": "" if math.isnan(h) else round(float(h), 1)})
        t0 += act["duration_s"] + gap_s
    rows.sort(key=lambda r: (r["time_s"], r["track_id"]))
    return rows, owner


def plan_report(events, owner: dict) -> list[dict]:
    """Per planned act: did the rule of its type fire (counted), as the plan expects?"""
    out = []
    for tid, act in owner.items():
        if act["role"] != "main":
            continue
        got = [e for e in events if tid in e.track_ids and e.type == act["type"]]
        counted = [e for e in got if e.status in COUNTED_STATUS]
        other = sorted({e.type for e in events if tid in e.track_ids and e.type != act["type"] and e.status in COUNTED_STATUS})
        out.append({"act": act["act"], "type": act["type"], "note": act["note"], "expected": act["expected"],
                    "detected": bool(counted), "ok": bool(counted) == act["expected"] and not other,
                    "statuses": [f"{e.status}{'(' + ','.join(e.tags) + ')' if e.tags else ''}" for e in got],
                    "other_types": other})
    return out


def run_site(args) -> None:
    """Real footage: real_geometry.py (scene map + site calibration + frame timestamps) -> same engine."""
    from real_geometry import RealCamera, Site, add_columns, frame_times
    if args.trajectories is None:
        raise SystemExit("--site needs --trajectories (the clip's trajectories_final.csv, scene_map.npz next to it)")
    site = Site(args.site)
    cam = RealCamera(site, args.trajectories.with_name("scene_map.npz"))
    stats = add_columns(args.trajectories, cam, frame_times(site.video))
    rows = compute(args.trajectories, site.W, site.H)
    # the smoother's sigma only knows the box noise; registration drift and the calibration add a
    # systematic error it cannot see (measured per clip, e.g. on a parked car) - fold it in so a
    # speeding flag must hold up despite it
    sys_kmh = float(site.data.get("speed_sigma_kmh", DEFAULT_SITE_SPEED_SIGMA_KMH))
    for r in rows:
        r["speed_sigma_kmh"] = round(math.hypot(r["speed_sigma_kmh"], sys_kmh), 2)
    out = args.out or args.trajectories.parent / "violations"
    out.mkdir(parents=True, exist_ok=True)
    write_rows(rows, out / "kinematics.csv")
    scene_data = merge_zones(site.scene(), args.zones)
    (out / "scene.json").write_text(json.dumps(scene_data))
    scene = SceneMap(scene_data)
    events = Engine(scene, json.loads(args.params.read_text()) if args.params else None, prefix=args.site.stem).run(rows)
    for e in events:  # no telemetry: every speed here rests on the site calibration
        if e.type == "speeding" and site.data["calibration"]["method"] != "srt":
            e.tags.append("estimated_speed")
    write_events(events, out)
    summary = {"site": str(args.site), "source": str(args.trajectories), "calibration": site.data["calibration"]["method"],
               "rows": len(rows), "tracks": len({r["track_id"] for r in rows}), "speed_sigma_kmh_added": sys_kmh,
               "frames_unregistered": stats["frames_unregistered"],
               "rows_on_a_lane": round(sum(scene.match(r["x"], r["y"]) is not None for r in rows) / max(len(rows), 1), 3),
               "events_by_type_status": {f"{t}/{s}": n for (t, s), n in sorted(Counter((e.type, e.status) for e in events).items())}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    print(f"[violations] {len(events)} events -> {out}")


def actor_class(a: dict) -> str:
    base = str(a.get("base_type", "")).lower()
    return {"truck": "truck", "bus": "bus"}.get(base, "car")


def merge_zones(scene: dict, extra: Path | None) -> dict:
    if extra:
        add = json.loads(extra.read_text())
        scene = dict(scene)
        scene["zones"] = scene.get("zones", []) + add.get("zones", [])
        scene["stop_lines"] = scene.get("stop_lines", []) + add.get("stop_lines", [])
    return scene


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path, nargs="?", default=None, help="Recorded flight folder")
    ap.add_argument("--scene", type=Path, default=None, help="Lane map JSON (export_lane_map.py); not with --site")
    ap.add_argument("--site", type=Path, default=None,
                    help="Real footage: a site file (zone_tool.py); needs --trajectories of that clip")
    ap.add_argument("--plan", type=Path, default=None,
                    help="Dry run: a stage_violations.py --plan-only log (planned paths, its zones); no flight")
    ap.add_argument("--zones", type=Path, default=None, help="Extra zones JSON (no-parking, no-U-turn, ...)")
    ap.add_argument("--trajectories", type=Path, default=None,
                    help="Tracks CSV (default: recorded_flight_validation/<flight>/tracktrack_ours/trajectories_final.csv)")
    ap.add_argument("--oracle", action="store_true", help="Use CARLA's true vehicle positions instead (L1)")
    ap.add_argument("--params", type=Path, default=None, help="JSON overriding rules.DEFAULTS")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.plan:
        plan = json.loads(args.plan.read_text())
        rows, owner = plan_rows(plan)
        scene = SceneMap(merge_zones(json.loads(args.scene.read_text()), args.plan))
        events = Engine(scene, json.loads(args.params.read_text()) if args.params else None, prefix="plan").run(rows)
        out = args.out or args.plan.parent / "plan_check"
        out.mkdir(parents=True, exist_ok=True)
        write_rows(rows, out / "kinematics.csv")
        write_events(events, out)
        report = plan_report(events, owner)
        (out / "plan_report.json").write_text(json.dumps(report, indent=1))
        for r in report:
            print(f"{'OK  ' if r['ok'] else 'FAIL'} {r['type']:15s} expected={'yes' if r['expected'] else 'no ':3s} "
                  f"detected={'yes' if r['detected'] else 'no ':3s} {r['note']}  {r['statuses']} {r['other_types'] or ''}")
        print(f"[plan check] {sum(r['ok'] for r in report)}/{len(report)} acts as expected -> {out}")
        return
    if args.site:
        run_site(args)
        return
    if args.flight is None:
        ap.error("give a flight folder (or --plan / --site)")

    cam = FlightCamera(args.flight)
    if args.oracle:
        rows = oracle_rows(args.flight, cam)
        out = args.out or args.flight / "violations_oracle"
        source = "oracle (vehicle_poses.csv)"
    else:
        traj = args.trajectories or RESULTS_DIR / args.flight.name / "tracktrack_ours" / "trajectories_final.csv"
        rows = pipeline_rows(args.flight, traj, cam)
        out = args.out or traj.parent / "violations"
        source = str(traj)
    out.mkdir(parents=True, exist_ok=True)
    write_rows(rows, out / "kinematics.csv")

    scene = SceneMap(merge_zones(json.loads(args.scene.read_text()), args.zones))
    params = json.loads(args.params.read_text()) if args.params else None
    engine = Engine(scene, params, prefix=args.flight.name)
    events = engine.run(rows)
    write_events(events, out)

    matched = sum(1 for r in rows if scene.match(r["x"], r["y"]) is not None) if len(rows) < 200000 else None
    summary = {"flight": args.flight.name, "source": source, "scene": str(args.scene), "zones": str(args.zones),
               "rows": len(rows), "tracks": len({r["track_id"] for r in rows}),
               "rows_on_a_lane": round(matched / len(rows), 3) if matched is not None and rows else None,
               "events_by_type_status": {f"{t}/{s}": n for (t, s), n in sorted(Counter((e.type, e.status) for e in events).items())},
               "counted": dict(Counter(e.type for e in events if e.status in COUNTED_STATUS))}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    print(f"[violations] {len(events)} events -> {out}")


if __name__ == "__main__":
    main()
