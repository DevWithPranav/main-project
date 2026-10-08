"""Stage traffic violations in CARLA with exact truth (Violation Engine, Phase A step A6).

docs/Violation_Engine_Architecture.md, Section 6. Run while the drone hovers over a spot and
record_flight.py --labels is recording (traffic_flow.py may run too: it leaves these cars
alone, they carry role_name "violation_scenario"). One act at a time, each with a fresh car
that is moved **kinematically** (physics off, its pose set every tick along a planned path),
so speed, timing and position are exactly what was planned:

  no_parking       stop 45 s in a no-parking zone (PRD 17.3)        + negative: 15 s stop
  wrong_way        drive a lane against its direction at 30 km/h
  speeding         1.6x and 1.3x the lane limit                      + negative: 1.1x (inside tolerance)
  illegal_u_turn   U-turn inside a no-U-turn zone                    + negative: same U-turn outside any zone
  lane_violation   drive on the line between two same-direction lanes for 7 s
  zebra_crossing   stop 15 s on a crosswalk                          + negative: stop behind a queue
  highway_stop     stop 30 s in a highway lane (only where a lane has a limit >= 90 km/h)

Zones it needs (no-parking, no-U-turn) are created around the chosen spots. The scenario log
(JSON) holds them plus every act's actor id and carla frames; pass it to the engine and the
evaluation:
    python ml/violation_engine/run_violations.py <flight> --scene .../Town05.json --zones <log>
    python ml/violation_engine/eval_violations.py <pipeline dir> <oracle dir> --scenario <log>

Check the plan offline first (no simulator needed), then run it with CarlaAir up:
    python stage_violations.py --town Town05 --center -40 -137 --plan-only
    python stage_violations.py --center -40 -137 --out <flight folder>/scenario_log.json

Run in the carlaAir conda env.
"""

import argparse
import datetime
import json
import math
import random
import time
from pathlib import Path

import carla
import numpy as np

REPO = Path(__file__).resolve().parents[2]
XODR_DIR = REPO / "CarlaAir-v0.1.7-Windows11-x86_64" / "WindowsNoEditor" / "CarlaUE4" / "Content" / "Carla" / "Maps" / "OpenDrive"
SCENES_DIR = REPO / "ml" / "violation_engine" / "configs" / "scenes"
SCRIPTED_ROLE = "violation_scenario"
BLUEPRINT = "vehicle.tesla.model3"
DT = 0.05  # planned trajectory resolution (s)
GAP_S = 6.0  # pause between acts


# --- geometry ---------------------------------------------------------------------------------

def lane_run(wp, length: float, step: float = 1.0) -> list:
    """Waypoints along a lane from wp for `length` m, stopping before a junction."""
    out = [wp]
    while (len(out) - 1) * step < length:
        nxt = [n for n in out[-1].next(step) if not n.is_junction and n.lane_id * wp.lane_id > 0]
        if not nxt:
            return out
        out.append(nxt[0])
    return out


def xyz(wp) -> np.ndarray:
    l = wp.transform.location
    return np.array([l.x, l.y, l.z])


def lateral(wp, offset: float) -> np.ndarray:
    """A point `offset` m to the driver's right of the waypoint (negative = left)."""
    r = wp.transform.get_right_vector()
    return xyz(wp) + offset * np.array([r.x, r.y, 0.0])


def box(wp, along: float, half_width: float) -> list:
    """Rectangle around a waypoint: +-along m along the lane, +-half_width across."""
    f, r = wp.transform.get_forward_vector(), wp.transform.get_right_vector()
    c = xyz(wp)
    pts = []
    for a, b in ((-along, -half_width), (along, -half_width), (along, half_width), (-along, half_width)):
        pts.append([round(c[0] + a * f.x + b * r.x, 2), round(c[1] + a * f.y + b * r.y, 2)])
    return pts


class Trajectory:
    """Timed path: pieces of (points, speed m/s) and holds (seconds at the last point)."""

    def __init__(self):
        self.t, self.p, self.yaw = [0.0], [], []
        self.marks: dict[str, float] = {}

    def move(self, pts: list, speed: float, reverse: bool = False) -> "Trajectory":
        pts = [np.asarray(p, float) for p in pts]
        if not self.p:
            self.p.append(pts[0])
            self.yaw.append(_yaw(pts[0], pts[1], reverse))
        for a, b in zip(pts[:-1], pts[1:]):
            d = float(np.hypot(*(b - a)[:2]))
            if d < 1e-6:
                continue
            n = max(1, int(math.ceil(d / speed / DT)))
            y = _yaw(a, b, reverse)
            for k in range(1, n + 1):
                self.t.append(self.t[-1] + d / speed / n)
                self.p.append(a + (b - a) * k / n)
                self.yaw.append(y)
        return self

    def hold(self, seconds: float) -> "Trajectory":
        n = int(round(seconds / DT))
        for _ in range(n):
            self.t.append(self.t[-1] + DT)
            self.p.append(self.p[-1])
            self.yaw.append(self.yaw[-1])
        return self

    def mark(self, name: str) -> "Trajectory":
        self.marks[name] = self.t[-1]
        return self

    @property
    def duration(self) -> float:
        return self.t[-1]

    def at(self, t: float):
        k = int(np.clip(np.searchsorted(self.t, t), 1, len(self.t) - 1))
        a = (t - self.t[k - 1]) / max(self.t[k] - self.t[k - 1], 1e-9)
        a = float(np.clip(a, 0, 1))
        p = self.p[k - 1] + (self.p[k] - self.p[k - 1]) * a
        return p, self.yaw[k]


def _yaw(a, b, reverse=False) -> float:
    y = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    return y + 180.0 if reverse else y


def arc(c: np.ndarray, r: float, a0: float, a1: float, z: float, n: int = 24) -> list:
    return [np.array([c[0] + r * math.cos(a), c[1] + r * math.sin(a), z]) for a in np.linspace(a0, a1, n)]


# --- planning ---------------------------------------------------------------------------------

def lane_limits(town: str):
    """Nearest-lane speed limit lookup from the exported lane map (export_lane_map.py)."""
    path = SCENES_DIR / f"{town}.json"
    if not path.exists():
        raise SystemExit(f"{path} missing - run simulation/carla_scripts/export_lane_map.py {town} first")
    lanes = json.loads(path.read_text())["lanes"]
    pts = np.vstack([np.array(l["centreline"]) for l in lanes])
    lim = np.concatenate([[l["speed_limit_kmh"]] * len(l["centreline"]) for l in lanes])
    return lambda x, y: float(lim[int(np.argmin((pts[:, 0] - x) ** 2 + (pts[:, 1] - y) ** 2))])


def candidates(m, center, radius, length) -> list:
    """Start waypoints of straight, junction-free driving-lane runs of >= length m near center."""
    out = []
    for wp in m.generate_waypoints(4.0):
        if wp.is_junction or wp.lane_type != carla.LaneType.Driving:
            continue
        run = lane_run(wp, length)
        if len(run) - 1 < length:
            continue
        mid = xyz(run[len(run) // 2])
        if math.hypot(mid[0] - center[0], mid[1] - center[1]) <= radius:
            ends = xyz(run[0]), xyz(run[-1])
            if all(math.hypot(e[0] - center[0], e[1] - center[1]) <= radius * 1.2 for e in ends):
                out.append(run)
    return out


def plan(m, town: str, center, radius: float, seed: int) -> dict:
    rng = random.Random(seed)
    limit = lane_limits(town)
    runs = candidates(m, center, radius, 60.0)
    if not runs:
        raise SystemExit("No straight 60 m lane near the centre - move --center or raise --radius.")
    rng.shuffle(runs)
    used_roads = set()

    def take(pred=lambda r: True):
        for r in runs:
            if r[0].road_id not in used_roads and pred(r):
                used_roads.add(r[0].road_id)
                return r
        for r in runs:  # allow road reuse if nothing else is left
            if pred(r):
                return r
        return None

    acts, zones = [], []

    # no-parking: zone 16 m long around a spot 40 m along a lane; 45 s stop, then a 15 s negative
    r = take()
    spot = r[40]
    zones.append({"id": "np_staged", "type": "no_parking", "polygon": box(spot, 8.0, spot.lane_width / 2), "grace_s": 30})
    for hold, expected in ((45.0, True), (15.0, False)):
        tr = Trajectory().move([xyz(w) for w in r[:41]], 25 / 3.6).mark("start").hold(hold).mark("end")
        tr.move([xyz(w) for w in r[40:]], 25 / 3.6)
        acts.append({"type": "no_parking", "expected": expected, "traj": tr, "note": f"stop {hold:.0f} s",
                     "truth": ("start", "end")})

    # wrong-way: a full lane run driven backwards (car facing its motion)
    r = take()
    tr = Trajectory().mark("start").move([xyz(w) for w in r[::-1]], 30 / 3.6).mark("end")
    acts.append({"type": "wrong_way", "expected": True, "traj": tr, "note": "30 km/h against the lane",
                 "truth": ("start", "end")})

    # speeding: 1.6x, 1.3x (violations) and 1.1x (inside the 5 km/h tolerance: not a violation)
    r = take()
    lim = limit(*xyz(r[30])[:2])
    for f, expected in ((1.6, True), (1.3, True), (1.1, False)):
        tr = Trajectory().mark("start").move([xyz(w) for w in r], f * lim / 3.6).mark("end")
        acts.append({"type": "speeding", "expected": expected, "traj": tr,
                     "note": f"{f:.1f} x {lim:.0f} km/h", "truth": ("start", "end")})

    # illegal U-turn: needs a two-way road (left neighbour lane runs the other way)
    def two_way(run):
        left = run[25].get_left_lane()
        return left is not None and left.lane_type == carla.LaneType.Driving and left.lane_id * run[25].lane_id < 0
    nou_centre = None
    for in_zone in (True, False):
        if in_zone:
            r = take(two_way)
        else:  # the legal one must be clearly outside the no-U-turn zone, on another road
            r = take(lambda run: two_way(run) and nou_centre is not None
                     and math.hypot(*(xyz(run[25])[:2] - nou_centre)) > 30.0)
            if r is not None and math.hypot(*(xyz(r[25])[:2] - nou_centre)) <= 30.0:
                r = None
        if r is None:
            break
        nou_centre = xyz(r[25])[:2] if in_zone else nou_centre
        p, q = r[25], r[25].get_left_lane()
        a, b = xyz(p), xyz(q)
        c, rad = (a + b) / 2, float(np.hypot(*(b - a)[:2])) / 2
        start_ang = math.atan2(a[1] - c[1], a[0] - c[0])
        fwd = p.transform.get_forward_vector()
        # sweep towards the forward side: the turn bulges ahead of the car
        sweep = math.pi if (math.cos(start_ang + math.pi / 2) * fwd.x + math.sin(start_ang + math.pi / 2) * fwd.y) > 0 else -math.pi
        exit_run = lane_run(q, 25.0)
        tr = Trajectory().move([xyz(w) for w in r[:26]], 15 / 3.6).mark("start")
        tr.move(arc(c, max(rad, 1.5), start_ang, start_ang + sweep, a[2]), 10 / 3.6).mark("end")
        tr.move([xyz(w) for w in exit_run], 15 / 3.6)
        if in_zone:
            zones.append({"id": "nou_staged", "type": "no_u_turn",
                          "polygon": _merge_boxes(box(p, 12.0, p.lane_width / 2), box(q, 12.0, q.lane_width / 2))})
        acts.append({"type": "illegal_u_turn", "expected": in_zone, "traj": tr,
                     "note": "U-turn in no-U-turn zone" if in_zone else "U-turn with no zone (legal)",
                     "truth": ("start", "end")})

    # lane violation: drive on the line to a same-direction neighbour for 7 s at 30 km/h
    def has_neighbour(run):
        rn = run[5].get_right_lane()
        return (rn is not None and rn.lane_type == carla.LaneType.Driving and rn.lane_id * run[5].lane_id > 0
                and str(run[5].right_lane_marking.type) not in ("NONE", "Curb", "Grass"))
    r = take(has_neighbour)
    if r is not None:
        pts = [lateral(w, w.lane_width / 2) for w in r]
        tr = Trajectory().move([xyz(w) for w in r[:5]], 30 / 3.6).mark("start").move(pts[5:], 30 / 3.6).mark("end")
        acts.append({"type": "lane_violation", "expected": True, "traj": tr, "note": "straddling the lane line",
                     "truth": ("start", "end")})

    # zebra crossing: stop 15 s on a crosswalk near the centre; negative: stop behind a queue there
    cw = _crosswalk_near(m, center, radius)
    if cw is not None:
        wp_c, poly = cw
        approach = []
        w = wp_c
        for _ in range(30):
            prev = w.previous(1.0)
            if not prev:
                break
            w = prev[0]
            approach.append(w)
        approach = approach[::-1] + [wp_c]
        after = lane_run(wp_c, 20.0) if not wp_c.is_junction else [wp_c] + [n[0] for n in [wp_c.next(k) for k in range(2, 22, 2)] if n]
        tr = Trajectory().move([xyz(w) for w in approach], 20 / 3.6).mark("start").hold(15.0).mark("end")
        tr.move([xyz(w) for w in after], 20 / 3.6)
        acts.append({"type": "zebra_crossing", "expected": True, "traj": tr, "note": "15 s on the crossing",
                     "truth": ("start", "end"), "zone_polygon": poly})
        ahead = wp_c.next(7.0)
        if ahead:
            # the lead car starts 10 m further along the same approach (both spawn at once) and stops
            # 7 m past the crossing; the follower arrives behind it and has to wait on the crossing
            lead = Trajectory().move([xyz(w) for w in approach[10:]] + [xyz(ahead[0])], 20 / 3.6).hold(20.0)
            lead.move([xyz(n[0]) for n in [ahead[0].next(k) for k in range(2, 22, 2)] if n], 20 / 3.6)
            foll = Trajectory().move([xyz(w) for w in approach], 20 / 3.6).mark("start").hold(15.0).mark("end")
            foll.move([xyz(w) for w in after], 20 / 3.6)
            acts.append({"type": "zebra_crossing", "expected": False, "traj": foll, "lead": lead,
                         "note": "on the crossing behind a stopped car (queue)", "truth": ("start", "end")})

    # highway stop: only where a lane has a limit >= 90 km/h
    r = take(lambda run: limit(*xyz(run[30])[:2]) >= 90)
    if r is not None and limit(*xyz(r[30])[:2]) >= 90:
        tr = Trajectory().move([xyz(w) for w in r[:31]], 60 / 3.6).mark("start").hold(30.0).mark("end")
        tr.move([xyz(w) for w in r[30:]], 40 / 3.6)
        acts.append({"type": "highway_stop", "expected": True, "traj": tr, "note": "30 s stop in a highway lane",
                     "truth": ("start", "end")})
    return {"acts": acts, "zones": zones}


def _merge_boxes(a: list, b: list) -> list:
    """Bounding rectangle (axis-aligned) of two boxes - a no-U-turn zone over both directions."""
    pts = np.array(a + b)
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    return [[round(x0, 2), round(y0, 2)], [round(x1, 2), round(y0, 2)], [round(x1, 2), round(y1, 2)], [round(x0, 2), round(y1, 2)]]


def _crosswalk_near(m, center, radius):
    """(driving waypoint at the crosswalk centre, polygon) for the crosswalk nearest the centre."""
    pts = [(p.x, p.y, p.z) for p in m.get_crosswalks()]
    polys, cur = [], []
    for p in pts:
        if cur and math.hypot(p[0] - cur[0][0], p[1] - cur[0][1]) < 1e-3 and len(cur) >= 3:
            polys.append(cur)
            cur = []
        else:
            cur.append(p)
    best = None
    for poly in polys:
        c = np.mean(np.array(poly), axis=0)
        d = math.hypot(c[0] - center[0], c[1] - center[1])
        if d > radius:
            continue
        wp = m.get_waypoint(carla.Location(*c), project_to_road=True, lane_type=carla.LaneType.Driving)
        if wp is None or wp.transform.location.distance(carla.Location(*c)) > 2.0:
            continue
        if best is None or d < best[0]:
            best = (d, wp, [[round(p[0], 2), round(p[1], 2)] for p in poly])
    return None if best is None else (best[1], best[2])


# --- running ----------------------------------------------------------------------------------

def spawn(world, bp_lib, tr: Trajectory):
    bp = bp_lib.find(BLUEPRINT)
    bp.set_attribute("role_name", SCRIPTED_ROLE)
    p, yaw = tr.at(0.0)
    v = world.try_spawn_actor(bp, carla.Transform(carla.Location(p[0], p[1], p[2] + 2.0), carla.Rotation(yaw=yaw)))
    if v is not None:
        v.set_simulate_physics(False)
    return v


def run_act(world, bp_lib, act: dict) -> dict:
    trajs = [("main", act["traj"])] + ([("lead", act["lead"])] if "lead" in act else [])
    cars = {}
    for name, tr in trajs:
        v = spawn(world, bp_lib, tr)
        if v is None:
            for c in cars.values():
                c.destroy()
            return {"error": f"spawn failed ({name})"}
        cars[name] = v
    snap = world.wait_for_tick()
    t0 = snap.timestamp.elapsed_seconds
    frames = {}
    duration = max(tr.duration for _, tr in trajs)
    while True:
        snap = world.wait_for_tick()
        t = snap.timestamp.elapsed_seconds - t0
        for name, tr in trajs:
            p, yaw = tr.at(min(t, tr.duration))
            cars[name].set_transform(carla.Transform(carla.Location(p[0], p[1], p[2] + 0.02), carla.Rotation(yaw=yaw)))
        for m_name, m_t in act["traj"].marks.items():
            if m_name not in frames and t >= m_t:
                frames[m_name] = (snap.frame, round(snap.timestamp.elapsed_seconds, 3))
        if t >= duration:
            break
    out = {"actor_id": cars["main"].id, "frames": frames}
    if "lead" in cars:
        out["lead_actor_id"] = cars["lead"].id
    for c in cars.values():
        c.destroy()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--center", type=float, nargs=2, metavar=("X", "Y"), default=None,
                    help="Where to stage (default: under the drone)")
    ap.add_argument("--radius", type=float, default=45.0, help="Keep acts within this many m (stay in view)")
    ap.add_argument("--town", default=None, help="Town (default: the loaded map); needed with --plan-only")
    ap.add_argument("--only", nargs="*", default=None, help="Run only these violation types")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--plan-only", action="store_true", help="Plan from the town's .xodr offline; no simulator")
    ap.add_argument("--out", type=Path, default=None, help="Scenario log JSON (default: runs/<time>/scenario_log.json)")
    args = ap.parse_args()

    if args.plan_only:
        if not args.town or args.center is None:
            raise SystemExit("--plan-only needs --town and --center")
        m, world, town = carla.Map(args.town, (XODR_DIR / f"{args.town}.xodr").read_text()), None, args.town
        center = args.center
    else:
        client = carla.Client(args.host, args.port)
        client.set_timeout(20.0)
        world = client.get_world()
        m = world.get_map()
        town = args.town or m.name.split("/")[-1]
        if args.center is None:
            drone = next(iter(world.get_actors().filter("airsim.*")), None)
            if drone is None:
                raise SystemExit("No drone found - give --center X Y")
            loc = drone.get_location()
            center = (loc.x, loc.y)
        else:
            center = args.center

    p = plan(m, town, center, args.radius, args.seed)
    acts = [a for a in p["acts"] if not args.only or a["type"] in args.only]
    zones = list(p["zones"])
    print(f"[plan] {town} centre ({center[0]:.0f}, {center[1]:.0f}): {len(acts)} acts, {len(zones)} zones")
    for a in acts:
        print(f"   {a['type']:15s} {'violation' if a['expected'] else 'negative ':9s} {a['traj'].duration:5.1f} s  {a['note']}")

    out = args.out or Path(__file__).parent / "runs" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S") / "scenario_log.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    log = {"town": town, "center": list(center), "created": datetime.datetime.now().isoformat(timespec="seconds"),
           "plan_only": args.plan_only, "zones": zones, "violations": [], "negatives": [], "acts": []}
    if args.plan_only:
        # planned paths sampled at 10 Hz, so `run_violations.py --plan` can dry-run the rules on them
        for a in acts:
            pts = np.array(a["traj"].p)
            ts = np.arange(0.0, a["traj"].duration, 0.1)
            samples = [[round(float(t), 2)] + a["traj"].at(t)[0][:2].round(3).tolist() for t in ts]
            entry = {"type": a["type"], "expected": a["expected"], "note": a["note"],
                     "duration_s": round(a["traj"].duration, 1), "truth_s": [a["traj"].marks[k] for k in a["truth"]],
                     "path_start": pts[0, :2].round(1).tolist(), "path_end": pts[-1, :2].round(1).tolist(),
                     "samples": samples}
            if "lead" in a:
                entry["lead_samples"] = [[round(float(t), 2)] + a["lead"].at(t)[0][:2].round(3).tolist()
                                         for t in np.arange(0.0, a["lead"].duration, 0.1)]
            log["acts"].append(entry)
        out.write_text(json.dumps(log, indent=1))
        print(f"[plan] written (no actors) -> {out}")
        return

    bp_lib = world.get_blueprint_library()
    for i, a in enumerate(acts):
        print(f"[act {i + 1}/{len(acts)}] {a['type']} - {a['note']}", flush=True)
        res = run_act(world, bp_lib, a)
        entry = {"type": a["type"], "note": a["note"], **res}
        if "frames" in res:
            s, e = a["truth"]
            entry.update(start_frame=res["frames"][s][0], end_frame=res["frames"][e][0],
                         start_sim_s=res["frames"][s][1], end_sim_s=res["frames"][e][1])
        log["acts"].append(entry)
        if "error" not in res:
            (log["violations"] if a["expected"] else log["negatives"]).append(entry)
        out.write_text(json.dumps(log, indent=1))  # after every act, so a crash keeps what was done
        time.sleep(GAP_S)
    print(f"[done] {len(log['violations'])} violations, {len(log['negatives'])} negatives -> {out}")


if __name__ == "__main__":
    main()
