"""Validate one recommendation in CARLA: baseline vs modified, same scenario (Build Plan M8).

Reproducible traffic (CARLA docs, Traffic Manager determinism): synchronous world at a fixed dt,
synchronous Traffic Manager with set_random_device_seed(seed), hybrid physics off, Python RNG
seeded, spawn points chosen in a fixed order, the same vehicle blueprints and the same per-vehicle
speed offsets in both variants. Several seeds per variant give the spread.

Each run is written like a recorded flight (vehicle_poses.csv, actors.json, frame_times.csv,
camera_poses.csv with a virtual nadir camera over the area, metadata.json), so
`run_violations.py --oracle` scores it with the same engine as everything else; plus
vehicle_states.csv for the traffic metrics (sim_metrics.py: speeds, TTC conflicts, travel time,
queues).

Changes per recommendation `simulation.kind` (ml/planning/recommend.py):
    speed_compliance   on the target roads no vehicle drives above the limit (offset clamped >= 0)
    speed_limit        on the target roads the target speed is to_kmh instead of from_kmh
    no_lane_change     auto lane change off on the target roads (baseline: some random lane changes)
    remove_stopped_vehicles  baseline: a parked vehicle at each event position; modified: none
    signal_timing      lights within radius_m: green time + green_delta_s
    zone               rule-side only: same traffic, the zone is added to the modified run's scene
    none               not simulable: refused

Usage (venv_sim, CarlaAir running):
    python simulation/planning/validate_recommendation.py --recs recs.json --id rec-184bd3a9a9c5 --dry-run
    python simulation/planning/validate_recommendation.py --recs recs.json --id rec-184bd3a9a9c5 --seeds 7 --duration 120
"""

import argparse
import csv
import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "simulation" / "carla_scripts"))
from sim_metrics import run_metrics  # noqa: E402

OUT_ROOT = REPO / "simulation" / "data_export" / "validation"
MAIN_PY = REPO / "venv" / "Scripts" / "python.exe"
SCENES = REPO / "ml" / "violation_engine" / "configs" / "scenes"
LOG_EVERY_S = 0.1
SUPPORTED = {"speed_compliance", "speed_limit", "no_lane_change", "remove_stopped_vehicles", "signal_timing", "zone"}


def load_rec(path: Path, rec_id: str) -> dict:
    data = json.loads(path.read_text())
    recs = data if isinstance(data, list) else data.get("recommendations", [])
    for r in recs:
        if r["id"] == rec_id:
            sim = r["simulation"]
            if "change" in sim:  # recommend.py nests the change: {supported, town, change: {kind, ...}}
                r["simulation"] = {**sim["change"], "supported": sim.get("supported"), "town": sim.get("town"),
                                   **({"reason": sim["reason"]} if "reason" in sim else {})}
            return r
    raise SystemExit(f"{rec_id} not in {path}")


def scenario(rec: dict, a) -> dict:
    sc = dict(rec["validation_method"]["scenario"] if isinstance(rec.get("validation_method"), dict)
              else rec.get("validation", {}).get("scenario", {}))
    for k in ("vehicles", "radius_m", "duration_s", "warmup_s", "dt"):
        v = getattr(a, k)
        if v is not None:
            sc[k] = v
    if a.seeds:
        sc["seeds"] = a.seeds
    sc["town"] = a.town or sc.get("town") or rec["simulation"].get("town")
    return sc


# ---------------------------------------------------------------- simulator side (venv_sim)

class Run:
    """One variant x seed in the simulator; writes a flight-like folder."""

    def __init__(self, client, rec, sc, seed, variant, out: Path):
        import carla
        self.carla, self.client, self.rec, self.sc = carla, client, rec, sc
        self.seed, self.variant, self.out = seed, variant, out
        self.sim = rec["simulation"]
        self.world = client.get_world()
        self.map = self.world.get_map()
        self.tm = client.get_trafficmanager(8000)
        self.center = sc["center"]
        self.spawned, self.parked = [], []

    def setup(self):
        carla, w = self.carla, self.world
        s = w.get_settings()
        self.old = (s.synchronous_mode, s.fixed_delta_seconds)
        s.synchronous_mode, s.fixed_delta_seconds = True, self.sc["dt"]
        w.apply_settings(s)
        self.tm.set_synchronous_mode(True)
        self.tm.set_random_device_seed(self.seed)
        self.tm.set_hybrid_physics_mode(False)
        random.seed(self.seed)
        rng = random.Random(self.seed)
        from record_flight import apply_weather
        apply_weather(w, self.sc.get("weather"), None)
        for a in w.get_actors().filter("vehicle.*"):  # clean start (the drone is not a vehicle.* actor)
            a.destroy()
        w.tick()
        cx, cy = self.center
        pts = [p for p in self.map.get_spawn_points() if math.hypot(p.location.x - cx, p.location.y - cy) <= self.sc["radius_m"]]
        pts.sort(key=lambda p: (round(p.location.x, 1), round(p.location.y, 1)))
        rng.shuffle(pts)
        bps = sorted((b for b in w.get_blueprint_library().filter("vehicle.*") if int(b.get_attribute("number_of_wheels")) == 4),
                     key=lambda b: b.id)
        self.offsets = {}
        for p in pts[: self.sc["vehicles"]]:
            bp = rng.choice(bps)
            if bp.has_attribute("color"):
                bp.set_attribute("color", rng.choice(bp.get_attribute("color").recommended_values))
            v = w.try_spawn_actor(bp, p)
            off = rng.uniform(-30.0, 10.0)  # negative = faster than the limit; same draw in both variants
            if v is None:
                continue
            v.set_autopilot(True, self.tm.get_port())
            self.tm.vehicle_percentage_speed_difference(v, off)
            self.tm.distance_to_leading_vehicle(v, 2.5)
            self.offsets[v.id] = off
            self.spawned.append(v)
        k = self.sim["kind"]
        if k == "no_lane_change":
            for v in self.spawned:
                try:
                    self.tm.random_left_lanechange_percentage(v, 5.0)
                    self.tm.random_right_lanechange_percentage(v, 5.0)
                except AttributeError:
                    pass
        if k == "remove_stopped_vehicles" and self.variant == "baseline":
            for x, y in self.sim.get("positions", [])[:10]:
                wp = self.map.get_waypoint(carla.Location(x=x, y=y), project_to_road=True)
                tf = wp.transform
                tf.location.z += 0.3
                v = w.try_spawn_actor(rng.choice(bps), tf)
                if v is not None:
                    v.apply_control(carla.VehicleControl(hand_brake=True))
                    self.parked.append(v)
        if k == "signal_timing" and self.variant == "modified":
            for tl in w.get_actors().filter("traffic.traffic_light"):
                loc = tl.get_location()
                if math.hypot(loc.x - cx, loc.y - cy) <= self.sim.get("radius_m", 60.0):
                    tl.set_green_time(tl.get_green_time() + self.sim.get("green_delta_s", 5.0))
        w.tick()

    def on_target(self, v, wp) -> bool:
        roads = {str(r) for r in self.sim.get("road_ids", [])}
        return str(wp.road_id) in roads

    def control(self, v, wp):
        """Per-tick modified-variant behaviour on the target roads."""
        k, off = self.sim["kind"], self.offsets.get(v.id, 0.0)
        if self.variant != "modified" or k not in ("speed_compliance", "speed_limit", "no_lane_change"):
            return
        on = self.on_target(v, wp)
        if k == "speed_compliance":
            self.tm.vehicle_percentage_speed_difference(v, max(off, 0.0) if on else off)
        elif k == "speed_limit":
            f, t = self.sim.get("from_kmh", 50.0), self.sim.get("to_kmh", 40.0)
            self.tm.vehicle_percentage_speed_difference(v, (100.0 - (100.0 - off) * t / f) if on else off)
        elif k == "no_lane_change":
            self.tm.auto_lane_change(v, not on)

    def run(self):
        w, sc = self.world, self.sc
        dt = sc["dt"]
        every = max(1, round(LOG_EVERY_S / dt))
        warm, total = round(sc["warmup_s"] / dt), round(sc["duration_s"] / dt)
        alt = sc["radius_m"] * 16 / 9 + 10.0  # virtual nadir camera: the short side covers the radius
        cx, cy = self.center
        self.out.mkdir(parents=True, exist_ok=True)
        fp = open(self.out / "vehicle_poses.csv", "w", newline="")
        fs = open(self.out / "vehicle_states.csv", "w", newline="")
        ft = open(self.out / "frame_times.csv", "w", newline="")
        fc = open(self.out / "camera_poses.csv", "w", newline="")
        wp_, ws, wt, wc = csv.writer(fp), csv.writer(fs), csv.writer(ft), csv.writer(fc)
        wp_.writerow(["carla_frame", "id", "x", "y", "z", "pitch", "yaw", "roll"])
        ws.writerow(["sim_time", "id", "x", "y", "yaw", "vx", "vy", "speed_kmh", "limit_kmh", "road_id"])
        wt.writerow(["frame", "time_s", "carla_frame", "sim_time", "x", "y", "z", "pitch", "yaw", "roll"])
        wc.writerow(["carla_frame", "x", "y", "z", "pitch", "yaw", "roll"])
        frame = 0
        t_wall = time.time()
        for i in range(warm + total):
            snap_frame = w.tick()
            vs = [v for v in self.spawned if v.is_alive]
            if i % 10 == 0 or i >= warm:
                for v in vs:
                    if i % every == 0 or i % 10 == 0:
                        self.control(v, self.map.get_waypoint(v.get_location()))
            if i < warm or i % every:
                continue
            snap = w.get_snapshot()
            t = snap.timestamp.elapsed_seconds
            for v in vs + self.parked:
                if not v.is_alive:
                    continue
                tf, vel = v.get_transform(), v.get_velocity()
                l, r = tf.location, tf.rotation
                wp_.writerow([snap_frame, v.id, f"{l.x:.4f}", f"{l.y:.4f}", f"{l.z:.4f}", f"{r.pitch:.4f}", f"{r.yaw:.4f}", f"{r.roll:.4f}"])
                wpt = self.map.get_waypoint(l)
                lim = v.get_speed_limit() if v in vs else ""
                ws.writerow([f"{t:.3f}", v.id, f"{l.x:.3f}", f"{l.y:.3f}", f"{r.yaw:.2f}", f"{vel.x:.3f}", f"{vel.y:.3f}",
                             f"{3.6 * math.hypot(vel.x, vel.y):.2f}", lim, wpt.road_id if wpt else ""])
            cam = [f"{cx:.4f}", f"{cy:.4f}", f"{alt:.4f}", "-90.0", "0.0", "0.0"]
            wt.writerow([frame, f"{frame * LOG_EVERY_S:.4f}", snap_frame, f"{t:.4f}", *cam])
            wc.writerow([snap_frame, *cam])
            frame += 1
        for f in (fp, fs, ft, fc):
            f.close()
        from record_flight import snapshot_vehicles
        known: dict = {}
        snapshot_vehicles(w, known)
        (self.out / "actors.json").write_text(json.dumps({str(k): v for k, v in known.items()}, indent=1))
        (self.out / "metadata.json").write_text(json.dumps({
            "map": self.map.name, "camera": {"width": 1920, "height": 1080, "fov": 90.0, "pitch": -90.0},
            "validation": {"rec_id": self.rec["id"], "variant": self.variant, "seed": self.seed, "scenario": sc,
                           "simulation": self.sim, "spawned": len(self.spawned), "parked": len(self.parked),
                           "frames": frame, "wall_s": round(time.time() - t_wall, 1)}}, indent=1))
        print(f"  [{self.variant} seed {self.seed}] {len(self.spawned)} vehicles (+{len(self.parked)} parked), "
              f"{frame} logged frames, {time.time() - t_wall:.0f} s wall")

    def teardown(self):
        for v in self.spawned + self.parked:
            if v.is_alive:
                v.destroy()
        self.tm.set_synchronous_mode(False)
        s = self.world.get_settings()
        s.synchronous_mode, s.fixed_delta_seconds = self.old
        self.world.apply_settings(s)


def simulate_one(rec, sc, seed: int, variant: str, out: Path):
    import carla
    client = carla.Client("localhost", 2000)
    client.set_timeout(180)
    run = Run(client, rec, sc, seed, variant, out)
    try:
        run.setup()
        run.run()
    finally:
        run.teardown()


def simulate(rec, sc, out_dir: Path, variants, argv: list[str]):
    """Each variant x seed in its own process: reusing one CARLA client after switching the world
    back to asynchronous mode aborts libcarla in get_settings() on this build (seen 2026-10-10)."""
    import carla
    client = carla.Client("localhost", 2000)
    client.set_timeout(180)
    if not client.get_world().get_map().name.endswith(sc["town"]):
        print(f"[sim] loading {sc['town']} (use simulation/carla_scripts/load_map.py first if this times out)")
        client.load_world(sc["town"])
        time.sleep(5)
    del client
    for seed in sc["seeds"]:
        for variant in variants:
            cmd = [sys.executable, "-u", __file__, *argv, "--one", variant, str(seed), "--out", str(out_dir)]
            r = subprocess.run(cmd)
            if r.returncode != 0:
                raise SystemExit(f"run {variant} seed {seed} failed (exit {r.returncode})")


# ---------------------------------------------------------------- scoring (engine via the main venv)

def score(rec, sc, out_dir: Path, profile: str) -> dict:
    scene = SCENES / f"{sc['town']}.json"
    zones = None
    if rec["simulation"]["kind"] == "zone":
        zones = out_dir / "rec_zone.json"
        zones.write_text(json.dumps({"zones": [rec["simulation"]["zone"]]}))
    runs = {}
    for d in sorted(p for p in out_dir.iterdir() if p.is_dir() and (p / "vehicle_states.csv").exists()):
        variant = d.name.split("_s")[0]
        cmd = [str(MAIN_PY), str(REPO / "ml/violation_engine/run_violations.py"), str(d), "--oracle",
               "--scene", str(scene), "--profile", profile, "--out", str(d / "violations_oracle")]
        if zones and variant == "modified":
            cmd += ["--zones", str(zones)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        evs = json.loads((d / "violations_oracle" / "violations.json").read_text())
        evs = evs if isinstance(evs, list) else evs.get("events", [])
        by_type: dict = {}
        for e in evs:
            if e.get("status") in ("flagged", "needs_review"):
                by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        m = run_metrics(d / "vehicle_states.csv", sc["center"], sc["radius_m"])
        m["violations"] = by_type
        runs[d.name] = {"variant": variant, **m}
    return runs


def metric(run: dict, name: str):
    v = run
    for k in name.split("."):
        v = v.get(k, 0) if isinstance(v, dict) else None
    if isinstance(v, dict):
        v = v.get("count")
    return v


def verdict(rec, runs: dict) -> dict:
    primary = (rec.get("validation_method") or {}).get("primary_metric") if isinstance(rec.get("validation_method"), dict) else None
    primary = primary or "conflicts_ttc"
    seeds = sorted({n.split("_s")[1] for n in runs})
    rows, better = [], 0
    for s in seeds:
        b, m = runs.get(f"baseline_s{s}"), runs.get(f"modified_s{s}")
        if not b or not m:
            continue
        vb, vm = metric(b, primary) or 0, metric(m, primary) or 0
        rows.append({"seed": s, "baseline": vb, "modified": vm,
                     "conflicts": [metric(b, "conflicts_ttc"), metric(m, "conflicts_ttc")],
                     "travel_time_mean_s": [b["travel_time"]["mean_s"], m["travel_time"]["mean_s"]]})
        better += vm < vb
    return {"primary_metric": primary, "per_seed": rows,
            "result": ("improved in every seed" if rows and better == len(rows) else
                       "no consistent improvement" if rows else "no paired runs"),
            "note": "measured in CARLA with autopilot traffic; see the recommendation's limitations"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recs", type=Path, required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--seeds", type=int, nargs="*")
    ap.add_argument("--town")
    ap.add_argument("--vehicles", type=int)
    ap.add_argument("--radius-m", dest="radius_m", type=float)
    ap.add_argument("--duration", dest="duration_s", type=float)
    ap.add_argument("--warmup", dest="warmup_s", type=float)
    ap.add_argument("--dt", type=float)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    ap.add_argument("--score-only", action="store_true", help="re-score existing runs without the simulator")
    ap.add_argument("--one", nargs=2, metavar=("VARIANT", "SEED"), help=argparse.SUPPRESS)  # internal: one run
    a = ap.parse_args()

    rec = load_rec(a.recs, a.id)
    kind = rec["simulation"]["kind"]
    sc = scenario(rec, a)
    out_dir = a.out or OUT_ROOT / a.id
    plan = {"rec": rec["id"], "problem": rec.get("problem"), "kind": kind, "scenario": sc, "out": str(out_dir),
            "runs": [f"{v}_s{s}" for s in sc["seeds"] for v in ("baseline", "modified")]}
    if kind not in SUPPORTED:
        raise SystemExit(f"{a.id}: simulation kind '{kind}' is not simulable ({rec['simulation'].get('reason', '')})")
    if a.one:
        variant, seed = a.one[0], int(a.one[1])
        simulate_one(rec, sc, seed, variant, out_dir / f"{variant}_s{seed}")
        return
    print(json.dumps(plan, indent=1))
    if a.dry_run:
        return
    if not a.score_only:
        argv = list(sys.argv[1:])
        for flag in ("--out",):  # simulate() passes its own
            if flag in argv:
                i = argv.index(flag)
                del argv[i:i + 2]
        simulate(rec, sc, out_dir, ["baseline", "modified"], argv)
    runs = score(rec, sc, out_dir, a.profile)
    report = {"recommendation": rec["id"], "scenario": sc, "runs": runs, "verdict": verdict(rec, runs)}
    (out_dir / "validation_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["verdict"], indent=1))
    print(f"-> {out_dir / 'validation_report.json'}")


if __name__ == "__main__":
    main()
