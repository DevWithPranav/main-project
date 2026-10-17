"""Pedestrian-crossing violations on a loop: F3 failure to yield and F1 stopped on the crossing.

The repeated version of the zebra acts in stage_violations.py / pedestrian_acts.py. A few *stations* (a
crosswalk and the lane through it) each play their acts one after the other, forever, and the stations run
side by side, so an event comes every few seconds instead of once per flight. Per station, in a shuffled
order each round:

  F3  failure to yield   the car drives through at 30 km/h while a walker crosses (violation)
  F3  yields             the car stops 2 m short and lets the walker cross                 (negative)
  F1  stopped on it      the car stands 15 s on the crossing, nobody about               (violation)
  F1  short stop         the same for 6 s, under the 10 s rule                            (negative)
  F2 / F5 (--conditions F3 F1 F2 F5)  blocking / obstructing, from pedestrian_acts.py

Nothing is driven by the Traffic Manager. Cars and walkers are moved kinematically, as the stager does
(physics off, pose set every tick along a planned path), because F3 and F1 are judged to seconds and
centimetres. Truth is the plan: the scenario log has every act's actor id and carla frames, in the format
eval_violations.py --scenario reads (the engine is the judge: record a flight over the stations with
record_flight.py --labels, then run_violations.py --oracle). A station's car and walker are spawned once and
parked 3 km away and 100 m down between acts: every spawn froze CarlaAir ~0.6 s on the highway (2026-10-14),
and the oracle only follows vehicles inside the camera's view.

Offline (no simulator, carlaAir env): list the stations, then check the plan with the engine (main venv):
    python pedestrian_violations.py --town Town05 --list-stations
    python pedestrian_violations.py --town Town05 --plan-only --station-ids 3 17 --out plan/scenario_log.json
    python ml/violation_engine/run_violations.py --plan plan/scenario_log.json --scene ml/violation_engine/configs/scenes/Town05.json --profile town05
Live (CarlaAir up, Town05 loaded, drone over the stations):
    python pedestrian_violations.py --station-ids 3 17 --duration 600 --out <flight folder>/scenario_log.json

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

from pedestrian_acts import CAR_KMH, Crossing, plan_pedestrian_acts, set_pose, spawn_walkers
from stage_violations import (SCENES_DIR, XODR_DIR, Trajectory, crosswalk_polygons, plan_entry,
                              spawn, xyz)

# role_name of this script's car and walkers: not the stager's / highway_violations.py's "violation_scenario",
# since each script removes its own role's actors at start (both run side by side on Town04, 2026-10-15)
ZEBRA_ROLE = "zebra_scenario"
GAP_S = 6.0  # between acts at a station: longer than the stop rules' close_gap_s (2 s), so two stops never merge
STAGGER_S = 3.0  # between the first acts of different stations
APPROACH_M = 30  # lane before the crossing the car drives in from
APPROACH_KMH = 20.0  # F1: the stager's speed in to the stop
F1_HOLD_S, F1_SHORT_S = 15.0, 6.0  # rules.DEFAULTS zebra_crossing min_s is 10 s
SIGNAL_NEAR_M = 60.0  # a traffic light this close along the crossing's road makes it "signalised" (events go to review)
PARK_DX, PARK_DZ = 3000.0, -100.0
PED_CONDITIONS = ("F3", "F2", "F5")


class AnyView:
    """Stands in for stage_violations.View: nothing is dropped for leaving a camera footprint (the drone is
    put over the stations by hand)."""

    @staticmethod
    def contains(_pts) -> bool:
        return True


# --- stations ----------------------------------------------------------------------------------

class Station:
    """A crosswalk, the driving waypoint at its centre and what is around it."""

    def __init__(self, sid: int, wp, poly: list, info: dict):
        self.id, self.wp, self.poly, self.info = sid, wp, poly, info
        self.acts: list[dict] = []

    @property
    def centre(self):
        return self.info["centre"]

    def score(self) -> tuple:
        """Lower is better: unsignalised, no junction lane on the way in, none on the way out."""
        i = self.info
        return (i["signals"] > 0, i["approach_junction"] > 0, i["after_junction"], -i["approach_m"])


def find_stations(m, scene: dict | None) -> list[Station]:
    """Every crosswalk that a car in the lane through its centre crosses, with what the engine and the plan
    need to know: it must be a zone of the scene (the engine only judges crosswalks it has), and the car's lane
    must span it."""
    zones = [np.mean(np.array(z["polygon"]), axis=0) for z in (scene or {}).get("zones", []) if z["type"] == "crosswalk"]
    try:
        lights = m.get_all_landmarks_of_type("1000001")
    except RuntimeError:
        lights = []
    out = []
    for sid, poly3 in enumerate(crosswalk_polygons(m)):
        c = np.mean(np.array(poly3), axis=0)
        wp = m.get_waypoint(carla.Location(*c), project_to_road=True, lane_type=carla.LaneType.Driving)
        if wp is None or wp.transform.location.distance(carla.Location(*c)) > 2.0:
            continue
        poly = [[round(p[0], 2), round(p[1], 2)] for p in poly3]
        cr = Crossing(wp, poly)
        if not cr.s0 < 0 < cr.s1:
            continue
        if zones and min(math.hypot(c[0] - q[0], c[1] - q[1]) for q in zones) > 1.0:
            continue
        appr = approach_of(wp)
        after = after_of(wp)
        out.append(Station(sid, wp, poly, {
            "centre": [round(float(c[0]), 1), round(float(c[1]), 1)], "road": wp.road_id, "lane": wp.lane_id,
            "approach_m": len(appr) - 1, "approach_junction": sum(w.is_junction for w in appr),
            "after_junction": sum(w.is_junction for w in after[1:]), "centre_junction": bool(wp.is_junction),
            "signals": sum(1 for l in lights if str(l.road_id) == str(wp.road_id) and abs(l.s - wp.s) < SIGNAL_NEAR_M),
            "width_m": round(cr.s1 - cr.s0, 1)}))
    return sorted(out, key=lambda s: s.score())


def approach_of(wp_c) -> list:
    """Waypoints of the lane into the crossing, 1 m apart, ending at its centre."""
    out, w = [], wp_c
    for _ in range(APPROACH_M):
        prev = w.previous(1.0)
        if not prev:
            break
        w = prev[0]
        out.append(w)
    return out[::-1] + [wp_c]


def after_of(wp_c) -> list:
    return [wp_c] + [n[0] for n in [wp_c.next(k) for k in range(2, 22, 2)] if n]


def f1_acts(st: Station) -> list[dict]:
    """The crossing's F1 acts (the stager's: in at 20 km/h, centre of the car on the crossing's centre)."""
    A, B = [xyz(w) for w in approach_of(st.wp)], [xyz(w) for w in after_of(st.wp)]
    v = APPROACH_KMH / 3.6
    acts = []
    for hold, expected in ((F1_HOLD_S, True), (F1_SHORT_S, False)):
        tr = Trajectory().move(A, v).mark("start").hold(hold).mark("end").move(B, v)
        acts.append({"type": "zebra_crossing", "condition": "F1", "expected": expected, "traj": tr, "walkers": [],
                     "note": f"{hold:.0f} s on the crossing, nobody about", "truth": ("start", "end")})
    return acts


def station_acts(st: Station, conditions: list[str]) -> list[dict]:
    """The station's acts for the chosen conditions. Walker acts need >= 20 m of lane before the crossing and a
    lane that spans it (plan_pedestrian_acts says so and returns nothing otherwise)."""
    acts = []
    if "F1" in conditions:
        acts += f1_acts(st)
    if any(c in conditions for c in PED_CONDITIONS):
        acts += [a for a in plan_pedestrian_acts(st.wp, st.poly, AnyView, Trajectory, xyz) if a["condition"] in conditions]
    for a in acts:
        a["note"] = f"[station {st.id}] {a['note']}"
        a["station"] = st.id
    return acts


def rounds(acts: list[dict], n: int, rng: random.Random) -> list[dict]:
    """n rounds of the acts, each in its own random order (a violation is never always followed by its negative)."""
    out = []
    for _ in range(n):
        r = list(acts)
        rng.shuffle(r)
        out += r
    return out


# --- live --------------------------------------------------------------------------------------

def park(actor, home) -> None:
    actor.set_transform(carla.Transform(carla.Location(home[0] + PARK_DX, home[1], home[2] + PARK_DZ)))


class Runner:
    """One station on the clock: idle, then an act is on until its longest path ends, then idle for GAP_S."""

    def __init__(self, st: Station, car, walker, rng: random.Random, start_at: float, gap_s: float):
        """walker: None at a station with no walker act."""
        self.st, self.car, self.walker, self.rng, self.gap_s = st, car, walker, rng, gap_s
        self.home = (st.wp.transform.location.x, st.wp.transform.location.y, st.wp.transform.location.z)
        self.queue: list[dict] = []
        self.act, self.t0, self.frames = None, 0.0, {}
        self.next_at = start_at
        self.done = 0

    def tick(self, snap, log: dict, out: Path) -> None:
        t_now = snap.timestamp.elapsed_seconds
        if self.act is None:
            if t_now < self.next_at:
                return
            if not self.queue:
                self.queue = rounds(self.st.acts, 1, self.rng)
            self.act, self.t0, self.frames = self.queue.pop(0), t_now, {}
        a = self.act
        t = t_now - self.t0
        p, yaw = a["traj"].at(min(t, a["traj"].duration))
        self.car.set_transform(carla.Transform(carla.Location(p[0], p[1], p[2] + 0.02), carla.Rotation(yaw=yaw)))
        if self.walker is not None:
            if a["walkers"]:
                set_pose(self.walker, a["walkers"][0], t)
            else:
                park(self.walker, self.home)
        for name, m_t in a["traj"].marks.items():
            if name not in self.frames and t >= m_t:
                self.frames[name] = (snap.frame, round(t_now, 3))
        if t < max([a["traj"].duration] + [w.duration for w in a["walkers"]]):
            return
        s, e = a["truth"]
        entry = {"type": a["type"], "condition": a["condition"], "cls": "car", "also": [], "expected": a["expected"],
                 "note": a["note"], "station": self.st.id, "actor_id": self.car.id, "frames": self.frames,
                 "start_frame": self.frames[s][0], "end_frame": self.frames[e][0],
                 "start_sim_s": self.frames[s][1], "end_sim_s": self.frames[e][1]}
        if a["walkers"]:
            entry["walker_actor_ids"] = [self.walker.id]
        log["acts"].append(entry)
        log["violations" if a["expected"] else "negatives"].append(entry)
        out.write_text(json.dumps(log, indent=1))  # after every act, so a crash keeps what was done
        for x in (self.car, self.walker):
            if x is not None:
                park(x, self.home)
        self.act, self.next_at = None, t_now + self.gap_s
        self.done += 1


def summary(log: dict, minutes: float) -> list[str]:
    from collections import Counter
    c = Counter((e["condition"], "violation" if e["expected"] else "negative") for e in log["acts"])
    lines = [f"{len(log['acts'])} acts in {minutes:.1f} min ({len(log['acts']) / max(minutes, 1e-6):.1f} per minute)"]
    for cond in sorted({k[0] for k in c}):
        lines.append(f"  {cond}: {c[(cond, 'violation')]} violations, {c[(cond, 'negative')]} negatives")
    return lines


def run_live(args, m, town: str, stations: list[Station]) -> None:
    client = carla.Client(args.host, args.port)
    client.set_timeout(20.0)
    world = client.get_world()
    bp_lib = world.get_blueprint_library()
    rng = random.Random(args.seed)
    if town not in world.get_map().name:
        raise SystemExit(f"The loaded map is {world.get_map().name}, not {town}")
    old = [a for a in world.get_actors() if a.attributes.get("role_name") == ZEBRA_ROLE and a.type_id.startswith(("vehicle.", "walker."))]
    if old:
        client.apply_batch([carla.command.DestroyActor(a.id) for a in old])
        print(f"[ped] removed {len(old)} scripted actors left by an earlier run")
        time.sleep(1.0)
    others = [a for a in world.get_actors().filter("vehicle.*") if a.attributes.get("role_name") != ZEBRA_ROLE]
    for st in stations:
        near = [a for a in others if math.hypot(a.get_location().x - st.centre[0], a.get_location().y - st.centre[1]) < 80.0]
        if near:
            print(f"[ped] warning: {len(near)} other vehicles within 80 m of station {st.id}: they can pass through the staged acts "
                  f"(traffic_flow.py running?)", flush=True)

    log = {"town": town, "created": datetime.datetime.now().isoformat(timespec="seconds"), "plan_only": False, "live": True,
           "stations": [{"id": s.id, **s.info} for s in stations], "zones": [], "violations": [], "negatives": [], "acts": []}
    out = args.out or Path(__file__).parent / "runs" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S") / "scenario_log.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    runners, actors = [], []
    for i, st in enumerate(stations):
        first = st.acts[0]
        car = spawn(world, bp_lib, first["traj"], role=ZEBRA_ROLE)
        w_tr = next((a["walkers"][0] for a in st.acts if a["walkers"]), None)
        walkers = spawn_walkers(world, bp_lib, [w_tr], role=ZEBRA_ROLE) if w_tr is not None else []
        if car is None or (w_tr is not None and not walkers):
            for x in [car] + walkers + actors:
                if x is not None:
                    x.destroy()
            raise SystemExit(f"Could not spawn the actors of station {st.id} (something in the way?)")
        actors += [car] + walkers
        r = Runner(st, car, walkers[0] if walkers else None, rng, 0.0, args.gap_s)  # no walker at an F1-only station
        runners.append(r)
        for x in [car] + walkers:
            park(x, r.home)
    for _ in range(10):  # let the parking settle before the clock starts
        world.wait_for_tick()
    t_start = world.wait_for_tick().timestamp.elapsed_seconds
    for i, r in enumerate(runners):
        r.next_at = t_start + 1.0 + i * STAGGER_S
    print(f"[ped] {len(runners)} stations, {sum(len(r.st.acts) for r in runners)} acts, writing {out}", flush=True)
    last_status = t_start
    try:
        while True:
            snap = world.wait_for_tick(10.0)
            t = snap.timestamp.elapsed_seconds
            for r in runners:
                r.tick(snap, log, out)
            if t - last_status >= 15.0:
                last_status = t
                print(f"[ped] t+{t - t_start:.0f}s  {len(log['acts'])} acts done ("
                      + ", ".join(f"st{r.st.id}: {r.act['condition'] + ('' if r.act['expected'] else '-') if r.act else 'idle'}" for r in runners)
                      + ")", flush=True)
            if args.duration and t - t_start >= args.duration:
                break
    except KeyboardInterrupt:
        print("\n[ped] stopped")
    t_end = world.get_snapshot().timestamp.elapsed_seconds
    for x in actors:
        try:
            x.destroy()
        except RuntimeError:
            pass
    log["duration_s"] = round(t_end - t_start, 1)
    out.write_text(json.dumps(log, indent=1))
    print("\n".join(["[ped] done"] + summary(log, (t_end - t_start) / 60.0)))
    print(f"[ped] scenario log: {out}")


# --- main --------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--town", default=None, help="Town (default: the loaded map); needed offline")
    ap.add_argument("--station-ids", type=int, nargs="+", default=None, help="Crosswalk numbers to use (see --list-stations)")
    ap.add_argument("--stations", type=int, default=3, help="Without --station-ids: this many, the best-scored")
    ap.add_argument("--all-stations", action="store_true", help="Every usable crosswalk (--plan-only: to screen them)")
    ap.add_argument("--conditions", nargs="+", default=["F3", "F1"], choices=["F1", "F2", "F3", "F5"])
    ap.add_argument("--gap-s", type=float, default=GAP_S)
    ap.add_argument("--duration", type=float, default=600.0, help="Live: seconds of simulator time; 0 = until Ctrl+C")
    ap.add_argument("--rounds", type=int, default=1, help="--plan-only: each station's acts this many times, shuffled")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--list-stations", action="store_true", help="Offline: print the usable crosswalks and stop")
    ap.add_argument("--plan-only", action="store_true", help="Offline: write the acts as a scenario log for run_violations.py --plan")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.list_stations or args.plan_only:
        if not args.town:
            raise SystemExit("--list-stations / --plan-only need --town")
        town = args.town
        m = carla.Map(town, (XODR_DIR / f"{town}.xodr").read_text())
    else:
        client = carla.Client(args.host, args.port)
        client.set_timeout(20.0)
        m = client.get_world().get_map()
        town = args.town or m.name.split("/")[-1]
    scene_path = SCENES_DIR / f"{town}.json"
    scene = json.loads(scene_path.read_text()) if scene_path.exists() else None
    if scene is None:
        print(f"[ped] no scene file for {town} ({scene_path.name}): the engine will not know its crosswalks")
    stations = find_stations(m, scene)
    if not stations:
        raise SystemExit(f"No usable crosswalk in {town}")

    if args.list_stations:
        print(f"{town}: {len(stations)} usable crosswalks, best first (id centre road/lane | approach m, junction lanes in/out, lights near)")
        for s in stations:
            i = s.info
            print(f"  {s.id:3d}  ({i['centre'][0]:7.1f}, {i['centre'][1]:7.1f})  r{i['road']}/{i['lane']:<3d} | {i['approach_m']:2d} m, "
                  f"in {i['approach_junction']:2d} out {i['after_junction']:2d}, lights {i['signals']}, width {i['width_m']} m")
        return

    if args.station_ids:
        by_id = {s.id: s for s in stations}
        missing = [i for i in args.station_ids if i not in by_id]
        if missing:
            raise SystemExit(f"Crosswalks {missing} are not usable stations in {town} (see --list-stations)")
        chosen = [by_id[i] for i in args.station_ids]
    else:
        chosen = stations if args.all_stations else stations[:args.stations]
    for s in chosen:
        s.acts = station_acts(s, args.conditions)
    chosen = [s for s in chosen if s.acts]
    if not chosen:
        raise SystemExit("None of the stations has an act for these conditions")
    print(f"[ped] {town}: stations " + ", ".join(f"{s.id} ({len(s.acts)} acts)" for s in chosen))

    if args.plan_only:
        rng = random.Random(args.seed)
        acts = [a for s in chosen for a in rounds(s.acts, args.rounds, rng)]
        out = args.out or Path(__file__).parent / "runs" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S") / "scenario_log.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        log = {"town": town, "center": chosen[0].centre, "created": datetime.datetime.now().isoformat(timespec="seconds"),
               "plan_only": True, "zones": [], "violations": [], "negatives": [],
               "acts": [{**plan_entry(a), "station": a["station"]} for a in acts]}
        out.write_text(json.dumps(log, indent=1))
        print(f"[ped] {len(acts)} acts written (no actors) -> {out}")
        return
    run_live(args, m, town, chosen)


if __name__ == "__main__":
    main()
