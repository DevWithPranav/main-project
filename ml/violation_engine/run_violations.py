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
    python ml/violation_engine/run_violations.py <flight dir> --profile town05   # configs/profiles/town05.json
    python ml/violation_engine/run_violations.py --site <site.json> --trajectories <clip>/trajectories_final.csv --learn-flow

Pedestrians (Build Plan M3, zebra conditions F2 / F3 / F5): pipeline runs add the people tracked by
process_recorded_flight.py --people (people_trajectories.csv next to the trajectories, or --people);
oracle runs add CARLA's walkers (walker_poses.csv, record_flight.py); --plan runs the planned walkers.
Their track ids are offset by pedestrians.PEOPLE_ID_OFFSET.

The optional red-light rule (signals.py) runs only with --red-light, on flights with traffic_lights.json
(record_flight.py). It is off by default: left out of scope for now (2026-10-09).
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from events import COUNTED_STATUS, write_events
from ground_coords import BOX_CENTRE_Z, FlightCamera, RoadSurface, add_world_columns, true_centres
from kinematics import compute, smooth_track, write_rows
from lane_map import SceneMap
from pedestrians import ENGINE_CLASS as PEDESTRIAN, PEOPLE_ID_OFFSET, offset_rows
from profiles import REPO, disabled_conditions, engine_params, load_profile
from road_features import HIGHWAY_KMH, apply_overrides, derive
from rules import Engine
from signals import SignalLog

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "recorded_flight_validation"
ORACLE_MEAS_SIGMA = 0.1  # true positions, but stamped with wall-clock times that jitter (~0.02-0.05 s per tick)
VIEW_MARGIN_PX = 20
DEFAULT_SITE_SPEED_SIGMA_KMH = 3.0  # real footage, unless the site file measured its own


def pipeline_rows(flight: Path, traj: Path, cam: FlightCamera, scene_data: dict | None = None,
                  out: Path | None = None) -> list[dict]:
    with open(traj, newline="") as f:
        header = next(csv.reader(f))
    surface = RoadSurface.from_scene(scene_data) if scene_data is not None else None
    if surface is not None and out is not None:
        # boxes onto the road itself (raised roads: flyovers, ramps); written next to the events, the
        # tracker's own CSV is left as it is
        if "time_wall_s" not in header and use_sim_time(flight, traj):
            print("[time] time_s = simulator time (frame_times.csv sim_time); wall clock kept as time_wall_s")
        world = out / "trajectories_world.csv"
        n, missing = add_world_columns(traj, cam, surface, out_path=world)
        print(f"[ground] road-surface positions for {n} rows ({missing} without a camera pose) -> {world}")
        return compute(world, cam.W, cam.H)
    if "wx" not in header or "pose_exact" not in header:
        n, missing = add_world_columns(traj, cam)
        print(f"[ground] wx, wy added to {n} rows ({missing} without a camera pose)")
    if "time_wall_s" not in header and use_sim_time(flight, traj):
        print("[time] time_s = simulator time (frame_times.csv sim_time); wall clock kept as time_wall_s")
    return compute(traj, cam.W, cam.H)


def people_rows(flight: Path, people_csv: Path, cam: FlightCamera, out: Path) -> list[dict]:
    """People tracked by the second detector (pedestrians.py) -> kinematics rows, ids offset. Projected
    onto the flat road plane: people stand on pavements and kerbs, where the road surface model has no
    height (a 0.15 m kerb moves a nadir projection by < 0.15 m)."""
    with open(people_csv, newline="") as f:
        header = next(csv.reader(f), [])
    if "time_wall_s" not in header:
        use_sim_time(flight, people_csv)
    world = out / "people_world.csv"
    n, missing = add_world_columns(people_csv, cam, out_path=world)
    rows = offset_rows(compute(world, cam.W, cam.H))
    print(f"[people] {n} detections ({missing} without a camera pose) -> {len({r['track_id'] for r in rows})} tracks")
    return rows


def use_sim_time(flight: Path, traj: Path) -> bool:
    """Flights from 2026-10-03 on log each frame's simulator time. Use it as time_s, like the oracle
    and the scenario log do (Architecture R2): wall-clock stamps of the frames jitter (frame-to-frame
    sim/wall ratio 0.6-1.7 on flight 20261009_201727), which is noise on every speed, and their
    origin differs, so pipeline and oracle events could not be compared in time. In place."""
    with open(flight / "frame_times.csv", newline="") as f:
        ft = list(csv.DictReader(f))
    if not ft or "sim_time" not in ft[0]:
        return False
    sim = {int(r["frame"]): r["sim_time"] for r in ft}
    with open(traj, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return False
    for r in rows:
        r["time_wall_s"] = r["time_s"]
        r["time_s"] = sim.get(int(r["frame"]), r["time_s"])
    with open(traj, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return True


def true_walkers(flight: Path) -> dict[int, dict[int, np.ndarray]]:
    """carla_frame -> {walker id: position (3,)} from walker_poses.csv (record_flight.py, M3); the
    walker's location is its box centre (~0.9 m up), like a vehicle box centre. {} if not recorded."""
    path = flight / "walker_poses.csv"
    out = defaultdict(dict)
    if path.exists():
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                out[int(r["carla_frame"])][int(r["id"])] = np.array([float(r["x"]), float(r["y"]), float(r["z"])])
    return out


def oracle_rows(flight: Path, cam: FlightCamera) -> list[dict]:
    """True vehicle centres while inside the camera view, as kinematics rows (track_id = actor id);
    walkers too when the flight logged them (class pedestrian, ids offset)."""
    actors = json.loads((flight / "actors.json").read_text())
    rows = _oracle_rows(flight, cam, true_centres(flight), lambda aid: actor_class(actors.get(aid, {})), BOX_CENTRE_Z)
    walkers = true_walkers(flight)
    if walkers:
        w = _oracle_rows(flight, cam, walkers, lambda aid: PEDESTRIAN, 0.9)
        for r in w:
            r["track_id"] += PEOPLE_ID_OFFSET
        print(f"[oracle] {len({r['track_id'] for r in w})} walker tracks in view (walker_poses.csv)")
        rows = sorted(rows + w, key=lambda r: (r["time_s"], r["track_id"]))
    return rows


def _oracle_rows(flight: Path, cam: FlightCamera, centres: dict, cls_of, centre_z: float) -> list[dict]:
    cf_frame = {cf: fr for fr, cf in cam.frame_cf.items()}
    cfs = np.array(sorted(cf_frame))
    with open(flight / "frame_times.csv", newline="") as f:
        ft = list(csv.DictReader(f))
    tcol = "sim_time" if ft and "sim_time" in ft[0] else "time_s"
    ft_cf = np.array([int(r["carla_frame"]) for r in ft])
    ft_t = np.array([float(r[tcol]) for r in ft])
    per_actor = defaultdict(list)
    for c, cents in centres.items():
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
                per_actor[aid].append((t, frame, xyz[i, 0], xyz[i, 1], xyz[i, 2]))
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
            cls = cls_of(aid)
            for i in range(len(seg)):
                h = k["heading_deg"][i]
                rows.append({"frame": int(seg[i, 1]), "time_s": round(float(seg[i, 0]), 4),
                             "track_id": int(aid) * 10 + part, "class": cls, "conf": 1.0, "visible": 1,
                             "x": round(float(k["x"][i]), 3), "y": round(float(k["y"][i]), 3),
                             "vx": round(float(k["vx"][i]), 3), "vy": round(float(k["vy"][i]), 3),
                             "speed_kmh": round(float(k["speed_kmh"][i]), 2),
                             "speed_sigma_kmh": round(float(k["speed_sigma_kmh"][i]), 2),
                             "heading_deg": "" if math.isnan(h) else round(float(h), 1),
                             # the road under it (box centre minus BOX_CENTRE_Z): picks the right level
                             # where roads cross over each other
                             "road_z": round(float(seg[i, 4]) - centre_z, 2)})
    rows.sort(key=lambda r: (r["time_s"], r["track_id"]))
    return rows


PLAN_GAP_S = 6.0


def plan_offsets(plan: dict, gap_s: float = PLAN_GAP_S) -> list[float]:
    """Start time of each act in a --plan dry run (acts played one after the other)."""
    out, t0 = [], 0.0
    for act in plan["acts"]:
        out.append(t0)
        t0 += act["duration_s"] + gap_s
    return out


def plan_rows(plan: dict, gap_s: float = PLAN_GAP_S) -> tuple[list[dict], dict[int, dict]]:
    """Kinematics rows from a stage_violations.py --plan-only log: every act's planned path
    (10 Hz samples) played one after the other. Returns rows and track_id -> act."""
    rows, owner = [], {}
    for i, (act, t0) in enumerate(zip(plan["acts"], plan_offsets(plan, gap_s))):
        # walkers (M3): one track each, class pedestrian, ids offset as the pipeline's people
        paths = [("samples", act.get("samples")), ("lead_samples", act.get("lead_samples"))] +             [("walker", w) for w in act.get("walker_samples", [])]
        for j, (key, samples) in enumerate(paths):
            if samples is None:
                continue
            a = np.array(samples)
            walker = key == "walker"
            tid = (i + 1) * 10 + j + (PEOPLE_ID_OFFSET if walker else 0)
            owner[tid] = {**{k: v for k, v in act.items() if not k.endswith("samples")}, "act": i,
                          "role": "walker" if walker else ("lead" if j else "main")}
            t = a[:, 0] + t0
            k = smooth_track(t, a[:, 1], a[:, 2], np.ones(len(a), bool), meas_sigma=ORACLE_MEAS_SIGMA)
            for n in range(len(a)):
                h = k["heading_deg"][n]
                rows.append({"frame": int(round(t[n] * 10)), "time_s": round(float(t[n]), 3), "track_id": tid,
                             "class": PEDESTRIAN if walker else (act.get("cls", "car") if j == 0 else "car"),
                             "conf": 1.0, "visible": 1,
                             "x": round(float(k["x"][n]), 3),
                             "y": round(float(k["y"][n]), 3), "vx": round(float(k["vx"][n]), 3),
                             "vy": round(float(k["vy"][n]), 3), "speed_kmh": round(float(k["speed_kmh"][n]), 2),
                             "speed_sigma_kmh": round(float(k["speed_sigma_kmh"][n]), 2),
                             "heading_deg": "" if math.isnan(h) else round(float(h), 1)})
    rows.sort(key=lambda r: (r["time_s"], r["track_id"]))
    return rows, owner


def plan_report(events, owner: dict) -> list[dict]:
    """Per planned act: did the rule of its type fire (counted), as the plan expects? An act that names
    a condition (Expected_Output 4.2) must be detected as that condition."""
    out = []
    for tid, act in owner.items():
        if act["role"] != "main":
            continue
        got = [e for e in events if tid in e.track_ids and e.type == act["type"]]
        counted = [e for e in got if e.status in COUNTED_STATUS]
        also = set(act.get("also", []))  # other types the act raises by rule (an overtake is wrong-way too)
        other = sorted({e.type for e in events if tid in e.track_ids and e.type != act["type"] and e.type not in also
                        and e.status in COUNTED_STATUS})
        cond = act.get("condition")
        right = (not cond or any(e.condition == cond for e in counted)) if act["expected"] else True
        out.append({"act": act["act"], "type": act["type"], "condition": cond, "note": act["note"],
                    "expected": act["expected"], "detected": bool(counted),
                    "ok": bool(counted) == act["expected"] and right and not other,
                    "statuses": [f"{e.condition}:{e.status}{'(' + ','.join(e.tags) + ')' if e.tags else ''}" for e in got],
                    "other_types": other})
    return out


def run_site(args, params: dict | None, disabled: set[str]) -> None:
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
    scene_data = road_scene(merge_zones(site.scene(), args.zones), args, derive_features=True)
    if args.learn_flow:
        scene_data = with_learned_flow(scene_data, rows)
    (out / "scene.json").write_text(json.dumps(scene_data))
    scene = SceneMap(scene_data)
    events = Engine(scene, extra_params(params, args.zones), prefix=args.site.stem, disabled_conditions=disabled).run(rows)
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


def with_learned_flow(scene_data: dict, rows: list[dict]) -> dict:
    """Lanes learned from the traffic itself (flow_map.py) in place of the map's lanes; zones and
    stop lines are kept. For footage with no lane map: only the wrong-way rule gets a direction."""
    from flow_map import flow_lanes, learn
    lanes = flow_lanes(learn(rows))
    print(f"[flow] {len(lanes)} learned cells, {sum(not l['junction'] for l in lanes)} one-way")
    return {**scene_data, "lanes": lanes}


def actor_class(a: dict) -> str:
    base = str(a.get("base_type", "")).lower()
    return {"truck": "truck", "bus": "bus"}.get(base, "car")


def engine_config(args) -> tuple[dict | None, set[str]]:
    """--profile (types and conditions on/off, thresholds, modules, road files), then --params on top.
    The CLI wins: --scene / --zones / --red-light / --learn-flow given on the command line are kept."""
    params, disabled = {}, set()
    args.road = {}
    if args.profile:
        prof = load_profile(args.profile)
        params, disabled = engine_params(prof), disabled_conditions(prof)
        mods, road = prof.get("modules", {}), prof.get("road", {})
        args.road = road
        args.red_light = args.red_light or mods.get("red_light", False)
        args.learn_flow = args.learn_flow or mods.get("learn_flow", False)
        for k in ("scene", "zones"):  # not road.site: that would switch a flight run into site mode
            if getattr(args, k) is None and road.get(k):
                setattr(args, k, REPO / road[k])
        print(f"[profile] {prof['name']}: off = "
              f"{sorted([t for t, v in params.items() if v.get('enabled') is False] + sorted(disabled)) or 'none'}")
    for t, v in (json.loads(args.params.read_text()) if args.params else {}).items():
        params.setdefault(t, {}).update(v)
    if args.red_light:
        params.setdefault("red_light", {})["enabled"] = True
    return params or None, disabled


def road_scene(scene: dict, args, derive_features: bool = False) -> dict:
    """The profile's lane overrides (restricted lanes, limits) on the scene; with derive_features
    (site files) also the road features a CARLA export already carries (road_features.py)."""
    if derive_features:
        derive(scene, args.road.get("highway_min_limit_kmh", HIGHWAY_KMH))
    n = apply_overrides(scene, args.road.get("lane_overrides", []))
    if n:
        print(f"[profile] lane overrides applied to {n} lanes")
    extra = args.road.get("extra_zones", [])  # zones drawn by a planner in the 3D twin (M7)
    if extra:
        scene["zones"] = scene.get("zones", []) + extra
        print(f"[profile] {len(extra)} extra zone(s) from the profile")
    return scene


def merge_zones(scene: dict, extra: Path | None) -> dict:
    """Zones and stop lines of an extra file (a scenario log, or hand-drawn zones) added to the scene;
    its lane_overrides (e.g. a lane the stager marked bus-only) applied to the scene's lanes."""
    if extra:
        add = json.loads(extra.read_text())
        scene = dict(scene)
        scene["zones"] = scene.get("zones", []) + add.get("zones", [])
        scene["stop_lines"] = scene.get("stop_lines", []) + add.get("stop_lines", [])
        if add.get("lane_overrides"):
            scene["lanes"] = [dict(l) for l in scene.get("lanes", [])]
            print(f"[zones] lane overrides from {extra.name}: {apply_overrides(scene, add['lane_overrides'])} lanes")
    return scene


def extra_params(params: dict | None, extra: Path | None) -> dict | None:
    """Rule settings a scenario log carries for its acts (engine_params: e.g. the junction where a
    staged U-turn is prohibited, a truck speed limit), on top of the profile / --params."""
    add = json.loads(extra.read_text()).get("engine_params", {}) if extra else {}
    if not add:
        return params
    out = {k: dict(v) for k, v in (params or {}).items()}
    for t, v in add.items():
        out.setdefault(t, {}).update(v)
    print(f"[zones] rule settings from {extra.name}: {add}")
    return out


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
    ap.add_argument("--params", type=Path, default=None, help="JSON overriding rules.DEFAULTS (applied after --profile)")
    ap.add_argument("--profile", type=Path, default=None,
                    help="Configuration profile (profiles.py): a file, or a name in configs/profiles/")
    ap.add_argument("--red-light", action="store_true",
                    help="Also run the optional red-light rule (CARLA flights with traffic_lights.json; off by default)")
    ap.add_argument("--learn-flow", action="store_true",
                    help="Replace the lane map's lanes by directions learned from the traffic (flow_map.py)")
    ap.add_argument("--flat-ground", action="store_true",
                    help="Project boxes onto a flat plane even when the lane map has road heights (the pre-M2 behaviour)")
    ap.add_argument("--people", type=Path, default=None,
                    help="People tracks CSV (default: people_trajectories.csv next to the trajectories, if there)")
    ap.add_argument("--no-people", action="store_true", help="Leave pedestrians out (no F2 / F3 / F5)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    params, disabled = engine_config(args)

    if args.plan:
        plan = json.loads(args.plan.read_text())
        rows, owner = plan_rows(plan)
        scene = SceneMap(road_scene(merge_zones(json.loads(args.scene.read_text()), args.plan), args))
        signals = SignalLog.from_plan(plan, plan_offsets(plan)) if args.red_light else None
        events = Engine(scene, extra_params(params, args.plan), prefix="plan", signals=signals,
                        disabled_conditions=disabled).run(rows)
        out = args.out or args.plan.parent / "plan_check"
        out.mkdir(parents=True, exist_ok=True)
        write_rows(rows, out / "kinematics.csv")
        write_events(events, out)
        report = plan_report(events, owner)
        (out / "plan_report.json").write_text(json.dumps(report, indent=1))
        for r in report:
            print(f"{'OK  ' if r['ok'] else 'FAIL'} {r['type']:15s} {r['condition'] or '':3s} expected={'yes' if r['expected'] else 'no ':3s} "
                  f"detected={'yes' if r['detected'] else 'no ':3s} {r['note']}  {r['statuses']} {r['other_types'] or ''}")
        print(f"[plan check] {sum(r['ok'] for r in report)}/{len(report)} acts as expected -> {out}")
        return
    if args.site:
        run_site(args, params, disabled)
        return
    if args.flight is None:
        ap.error("give a flight folder (or --plan / --site)")

    cam = FlightCamera(args.flight)
    scene_data = road_scene(merge_zones(json.loads(args.scene.read_text()), args.zones), args)
    if args.oracle:
        out = args.out or args.flight / "violations_oracle"
        out.mkdir(parents=True, exist_ok=True)
        rows = oracle_rows(args.flight, cam)
        source = "oracle (vehicle_poses.csv)"
    else:
        traj = args.trajectories or RESULTS_DIR / args.flight.name / "tracktrack_ours" / "trajectories_final.csv"
        out = args.out or traj.parent / "violations"
        out.mkdir(parents=True, exist_ok=True)
        rows = pipeline_rows(args.flight, traj, cam, None if args.flat_ground else scene_data, out)
        source = str(traj)
        people = args.people or traj.parent / "people_trajectories.csv"
        if not args.no_people and people.exists():
            rows = sorted(rows + people_rows(args.flight, people, cam, out), key=lambda r: (r["time_s"], r["track_id"]))
            source += f" + {people}"
    write_rows(rows, out / "kinematics.csv")

    if args.learn_flow:
        scene_data = with_learned_flow(scene_data, rows)
    # red light (optional, off unless --red-light): recorded signal states + CARLA's stop lines
    signals = SignalLog.from_flight(args.flight) if args.red_light else None
    if signals is not None:
        signals, flight_lines = signals
        have = {sl["id"] for sl in scene_data.get("stop_lines", [])}
        scene_data["stop_lines"] = scene_data.get("stop_lines", []) + [sl for sl in flight_lines if sl["id"] not in have]
    scene = SceneMap(scene_data)
    engine = Engine(scene, extra_params(params, args.zones), prefix=args.flight.name, signals=signals,
                    disabled_conditions=disabled)
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
