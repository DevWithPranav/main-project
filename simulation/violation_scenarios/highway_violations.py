"""Highway violations in scripted highway traffic on Town04, with a CARLA-truth report.

Every car on the stretch is driven by this script (HandDriver: pure pursuit on the lane, gap keeping, lane
changes), none by the Traffic Manager. CarlaAir's TM cannot steer above ~60 km/h, only sees 30 km/h limits,
ignores lane offsets, runs cars into guardrails and cut into the speeders (~40 contacts per 5 min run,
2026-10-11), so the background is scripted too:

  slow traffic         --slow-cars at a fixed --slow-kmh each, below the limit; they keep right and only
                       pass a car that holds them below 0.7 x their speed
  E1  speeders         --speed-tiers x the posted limit (default 1.03 / 1.2 / 1.4 / 1.6: 93 / 108 / 126 /
                       144 km/h in a 90; 1.03 is inside the tolerance, a negative). Behind a slow car they
                       slow to its speed and keep the gap, pass through a lane that is clear (gap scaled by
                       the closing speed), then return right once 1.5 s clear of the car they passed
  B1  stoppers         cruise at 63 km/h; on a highway lane (no junction in the next 80 m, 150 m of stretch past
                       it, lane clear 40 m ahead / 60 m behind): brake to rest, hold with hazards for --hold-s,
                       drive on; repeats. The others pass it like a slow car
  B2  shoulder stops   the same from the outer lane next to a >= 3 m shoulder, steered onto it at 20 km/h
                       first and back into the lane after

Cars run like a conveyor: all are spawned parked at start-up and launched together; a car that leaves the
stretch is teleported back to the upstream end at speed (recycled, not respawned: each spawn froze CarlaAir
~0.6 s), so each speeder makes a fresh E1 pass every ~15 s. 2026-10-14 run 7: 115 E1 in 5 min, 1 contact.

The default stretch is the northbound straight on Town04's east side (lanes 3-6, x ~ -6..-17, y -218..207):
425 m at 90 km/h, 4 lanes, a 3.5 m outer shoulder, ~200 m bends at both ends. Road 45 (the old default) is
a 180 m radius arc, where a 3 m/s2 bend cap holds any car to ~84 km/h: no room for high speed. The
southbound side drops to 60 and 30 km/h near y -40 in the scene and is left out.

Every vehicle in the world is judged at 10 Hz with CARLA's true positions and the engine's thresholds
(ml/violation_engine/rules.py DEFAULTS), against the engine's own Town04 scene
(configs/scenes/Town04.json: posted limits, highway class, shoulder lanes):

  E1  above limit + tolerance (5 km/h below 100, 5% above) for >= 1/3 s, after the car is 1 s old
  B   below 5 km/h for >= 20 s on a highway lane (or ramp / bridge), not at a red light; B2 when >= half
      of it on a shoulder (the engine's status: possible_breakdown), queue_suppressed when >= half of it
      behind another stopped car

Outputs in ml/data/results/highway_violations/<timestamp>/: violations.csv, report.md, summary.json,
collisions.csv, tracks.csv (every car at 10 Hz).

Run with CarlaAir up (Medium quality) and Town04 loaded, without traffic_flow.py (docs/Highway_Violations.md):
    python highway_violations.py --duration 300
Ctrl+C ends early; the report is written either way and the script's cars are removed.
"""

import argparse
import csv
import json
import math
import random
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import carla

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "ml" / "violation_engine" / "configs" / "scenes" / "Town04.json"
OUT_ROOT = REPO / "ml" / "data" / "results" / "highway_violations"
ROLE = "violation_scenario"  # traffic_flow.py never adopts or removes cars with this role (slow cars carry it too)
BUS_BLUEPRINTS = {"vehicle.mitsubishi.fusorosa"}
SLOW_MODELS = {"vehicle.micro.microlino", "vehicle.bmw.isetta", "vehicle.carlamotors.carlacola",
               "vehicle.carlamotors.firetruck", "vehicle.ford.ambulance", "vehicle.mercedes.sprinter",
               "vehicle.volkswagen.t2", "vehicle.volkswagen.t2_2021"}  # not given to speeders
NOMINAL_LIMIT_KMH = 90.0  # the stretch's posted limit, for spawn speeds

# rules.DEFAULTS
E1_MIN_S, E1_MIN_AGE_S, E1_CONFIRM_GAP_S, E1_CLOSE_GAP_S = 10 / 30, 1.0, 0.2, 2.0
STOP_MIN_S, STOP_MAX_KMH, STOP_CLOSE_GAP_S = 20.0, 5.0, 2.0
QUEUE_AHEAD_M, QUEUE_LATERAL_M = 12.0, 1.8
SAMPLE_S = 0.1
SPAWN_CLEAR_M = 10.0
SPAWN_TTC_S = 4.0  # a new car must not close on (or be closed on by) a car in its lane faster than this
FOLLOW_S, FOLLOW_M = 2.5, 15.0  # following gap at the lead's speed: 2.5 s + 15 m
COMFORT_DECEL = 3.0  # m/s2: approach a slower lead so as to reach the gap at its speed
TTC_BRAKE_S = 2.0  # time to collision with the lead below this: full brake
LOOK_M = 150.0  # how far along its lane a driver looks for cars
LC_S = 3.5  # a lane change blends sideways over this long (3.5 m at 110 km/h: over ~110 m)
LC_COOLDOWN_S = 3.0  # between one car's lane changes
LC_TTC_S = 4.0  # a lane change needs this time to collision with cars ahead / behind in the target lane
WHEELBASE_M = 2.9
CROSS_TRACK_GAIN, CROSS_TRACK_MAX_DEG = 1.5, 5.0
LATERAL_MPS2 = 3.0  # bend speed cap (4.0 let a 95 km/h car drift 1.7 m outwards)
CURVE_DECEL, CURVE_STEP_M = 3.0, 20.0  # brake for a bend up to 180 m ahead
PULL_OVER_KMH = 20.0
STOP_END_CLEAR_M = 150.0  # B1 / B2 stop only where the stretch goes on this far past them
CRUISE_FACTOR = 0.7  # B1 / B2 cars cruise at 63 km/h in a 90
STUCK_KMH = 5.0
STUCK_RESPAWN_S = 10.0
OFF_STRETCH_S = 2.0  # a car off the stretch's lanes (a ramp, a crossing road) this long is recycled: inside the
                     # radius it otherwise wandered the ramps for the whole run (2026-10-14 run 6)
CRASH_STOP_WINDOW_S = 10.0
COLLISION_DEDUP_S = 3.0  # the sensor reports every tick of a contact: one incident per pair per this gap
HAZARDS = carla.VehicleLightState(int(carla.VehicleLightState.LeftBlinker) | int(carla.VehicleLightState.RightBlinker))
LANE_TYPES = carla.LaneType.Driving | carla.LaneType.Shoulder


def tolerance_kmh(limit: float) -> float:
    return 5.0 if limit < 100 else 0.05 * limit


def kmh(v) -> float:
    vel = v.get_velocity()
    return 3.6 * math.hypot(vel.x, vel.y)


def dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def yaw_diff(a, b) -> float:
    return abs((a.transform.rotation.yaw - b.transform.rotation.yaw + 180) % 360 - 180)


def on_main(wp, scene) -> bool:
    lane = scene.lane(wp)
    return lane is not None and not lane.get("ramp") and lane["road_class"] == "highway"


def mainline(cands, ref, scene):
    """The branch at a fork that stays on the highway mainline: not a ramp or a non-highway lane, then the
    same lane id, then the least turn. (Preferring Scene.no_stopping lanes picked the exit ramps r779 / r1076
    off the east straight's lane 6, which are tagged ramp and so count as no-stopping while the mainline
    junction lanes do not: slow cars left the highway or spun out, and curve_cap braked lane-6 speeders to
    17 km/h for the ramp's bend, 2026-10-14 run 1.)"""
    def key(c):
        lane = scene.lane(c)
        off_main = lane is None or bool(lane.get("ramp")) or lane["road_class"] != "highway"
        return off_main, c.lane_id != ref.lane_id, yaw_diff(c, ref)
    return min(cands, key=key)


def smooth(s: float) -> float:
    s = max(0.0, min(1.0, s))
    return s * s * (3 - 2 * s)


class Scene:
    """The engine's lane properties, looked up by CARLA's road / section / lane ids. export_lane_map.py
    splits a lane into pieces (id suffix _p0, _p1, ...) where it is cut: the nearest piece is taken
    (an exact-id lookup missed all of road 45's -1..-5 side, 2026-10-11)."""

    def __init__(self, path: Path, m):
        self.pieces: dict[str, list] = {}
        for l in json.loads(path.read_text())["lanes"]:
            self.pieces.setdefault(l["id"].split("_p")[0], []).append(l)
        self.m = m

    def lane(self, wp):
        ls = self.pieces.get(f"r{wp.road_id}_s{wp.section_id}_l{wp.lane_id}")
        if not ls:
            return None
        if len(ls) == 1:
            return ls[0]
        x, y = wp.transform.location.x, wp.transform.location.y
        return min(ls, key=lambda l: min((p[0] - x) ** 2 + (p[1] - y) ** 2 for p in l["centreline"][::5]))  # every 5 m is enough to tell pieces apart

    def at(self, loc):
        wp = self.m.get_waypoint(loc, project_to_road=True, lane_type=LANE_TYPES)
        if wp is None:
            return None, None
        return wp, self.lane(wp)

    @staticmethod
    def no_stopping(lane) -> bool:  # rules.Engine.road_zone
        return lane is not None and ((lane["road_class"] == "highway" and not lane["junction"])
                                     or bool(lane.get("ramp")) or bool(lane.get("bridge")) or bool(lane.get("tunnel")))


def wide_shoulder(wp):
    r = wp.get_right_lane()
    return r if r is not None and r.lane_type == carla.LaneType.Shoulder and r.lane_width >= 3.0 else None


# --- lanes and the cars on them ----------------------------------------------------------------

class Ctx:
    """State the drivers share, refreshed at 10 Hz: lane_of (vehicle id -> (road, lane, offset right of the
    lane centre)), goal_of (vehicle id -> (road, lane) it is changing into), stats (driver event counts)."""

    def __init__(self):
        self.lane_of: dict[int, tuple] = {}
        self.goal_of: dict[int, tuple] = {}
        self.stats: Counter = Counter()


_KEYS: dict = {}


def path_keys(wp, ahead_m: float, behind_m: float = 0.0, step: float = 10.0) -> set:
    """The (road, lane) keys a car in wp's lane passes through within [-behind_m, ahead_m], forks included.
    Cached per 5 m of lane: every driver calls it every tick."""
    ck = (wp.road_id, wp.section_id, wp.lane_id, int(wp.s // 5), ahead_m, behind_m)
    keys = _KEYS.get(ck)
    if keys is None:
        keys = {(wp.road_id, wp.lane_id)}
        d = step
        while d <= ahead_m:
            keys |= {(w.road_id, w.lane_id) for w in wp.next(d)}
            d += step
        d = step
        while d <= behind_m:
            keys |= {(w.road_id, w.lane_id) for w in wp.previous(d)}
            d += step
        _KEYS[ck] = keys
    return keys


def lane_cars(loc, fwd, keys: set, vehicles, ctx: Ctx, self_id) -> list:
    """(along, distance, speed m/s) of every car on the lanes in keys, along > 0 ahead. Lanes come from the
    map (ctx.lane_of), not from the straight-line sideways offset: on road 45's arc a car 40 m ahead in the
    same lane sits 2.7 m off the heading line, so the geometric test missed real leads and the speeders ran
    into them (2026-10-11 run 5: 35 of 40 E1 collisions with a car ahead). A car changing into one of the
    lanes counts as on it, and a car more than 1 m off its lane centre (straddling, or a wreck turned
    sideways) also blocks the neighbour lane it leans into."""
    out = []
    for o in vehicles:
        if o.id == self_id or o.id not in ctx.lane_of:
            continue
        road, lane, off = ctx.lane_of[o.id]
        on = (road, lane) in keys or ctx.goal_of.get(o.id) in keys
        if not on and abs(off) > 1.0 and lane != 0:
            toward = lane + (1 if lane > 0 else -1) * (1 if off > 0 else -1)  # off > 0: right of its lane
            on = toward != 0 and (road, toward) in keys
        if not on:
            continue
        ol, ov = o.get_location(), o.get_velocity()
        dx, dy = ol.x - loc.x, ol.y - loc.y
        out.append((dx * fwd.x + dy * fwd.y, math.hypot(dx, dy), math.hypot(ov.x, ov.y)))
    return out


def path_gap(loc, fwd, keys: set, vehicles, ctx: Ctx, self_id, behind_m: float, ahead_m: float):
    """Nearest car ahead (distance, its speed m/s) on the lanes in keys, and whether [-behind_m, ahead_m]
    of them is free."""
    lead, clear = None, True
    for along, d, u in lane_cars(loc, fwd, keys, vehicles, ctx, self_id):
        if along < 0:
            if d < behind_m:
                clear = False
            continue
        if d < ahead_m:
            clear = False
        if lead is None or d < lead[0]:
            lead = (d, u)
    return lead, clear


def follow_speed(desired: float, lead) -> float:
    """Speed to drive behind lead (distance, speed): brake at COMFORT_DECEL so as to reach the gap
    (FOLLOW_S x its speed + FOLLOW_M) at its speed, and inside the gap close down towards it."""
    if lead is None:
        return desired
    d, u = lead
    gap = FOLLOW_S * u + FOLLOW_M
    if d >= gap:
        return min(desired, math.sqrt(u * u + 2 * COMFORT_DECEL * (d - gap)))
    return min(desired, u * max(0.0, (d - 8.0) / (gap - 8.0)))


_CURVE: dict = {}


def curve_cap(wp, scene) -> float:
    """Highest speed from which the car can brake at CURVE_DECEL down to every bend's LATERAL_MPS2 speed
    within 180 m ahead (the old 40 m look let a 144 km/h car reach a 200 m bend at full speed)."""
    ck = (wp.road_id, wp.section_id, wp.lane_id, int(wp.s // 5))
    cap = _CURVE.get(ck)
    if cap is None:
        cap, w = math.inf, wp
        for i in range(10):
            nxt = w.next(CURVE_STEP_M)
            if not nxt:
                break
            n = mainline(nxt, w, scene)
            turn = math.radians(yaw_diff(n, w))
            if turn > 1e-3:
                vc = math.sqrt(LATERAL_MPS2 * CURVE_STEP_M / turn)
                cap = min(cap, math.sqrt(vc * vc + 2 * CURVE_DECEL * i * CURVE_STEP_M))
            w = n
        _CURVE[ck] = cap
    return cap


def warm_caches(m, scene, centre, radius: float) -> int:
    """Fill the path_keys / curve_cap caches for every driving lane near the stretch before any car moves.
    Filled lazily, they stalled the main loop for 1-5.6 s while cars were spawned, and every car kept its
    last steering through the stall: the mass swerves and spin-outs of 2026-10-14 runs 1-3."""
    n = 0
    for wp in m.generate_waypoints(2.5):
        if wp.lane_type == carla.LaneType.Driving and dist(wp.transform.location, centre) <= radius + 30.0:
            path_keys(wp, LOOK_M)
            path_keys(wp, LOOK_M, LOOK_M)
            curve_cap(wp, scene)
            n += 1
    return n


def lane_offset(wp, loc) -> float:
    rv, c = wp.transform.get_right_vector(), wp.transform.location
    return (loc.x - c.x) * rv.x + (loc.y - c.y) * rv.y


def lane_wp(wp, lane_id):
    """The driving lane lane_id at wp: its own, or a neighbour (a car mid lane change is in either)."""
    for w in (wp, wp.get_left_lane(), wp.get_right_lane()):
        if w is not None and w.lane_id == lane_id and w.lane_type == carla.LaneType.Driving:
            return w
    return None


def pursue(v, aim, target_mps: float, max_steer: float, scene, shift_now: float = 0.0, shift_ahead: float = 0.0,
           bias: float = 0.0, hard: bool = False) -> None:
    """Pure pursuit along aim's lane, shift_* metres right of its centre (a lane change in progress), at
    target_mps (P throttle + bias / brake; at 0 it brakes to rest; hard: full brake). At a fork, the
    mainline branch."""
    tf = v.get_transform()
    loc, vel = tf.location, v.get_velocity()
    sp = math.hypot(vel.x, vel.y)
    ld = max(6.0, 0.9 * sp)
    nxt = aim.next(ld)
    if not nxt:
        v.apply_control(carla.VehicleControl(throttle=0.0, brake=0.5))
        return
    branch = mainline(nxt, aim, scene)
    tp = branch.transform.location
    if shift_ahead:
        rv = branch.transform.get_right_vector()
        tp = carla.Location(tp.x + rv.x * shift_ahead, tp.y + rv.y * shift_ahead, tp.z)
    alpha = math.atan2(tp.y - loc.y, tp.x - loc.x) - math.radians(tf.rotation.yaw)
    alpha = math.atan2(math.sin(alpha), math.cos(alpha))
    delta = math.degrees(math.atan2(2 * WHEELBASE_M * math.sin(alpha), ld))
    # cross-track term (Stanley): pure pursuit alone understeered at 95 km/h on road 45's bend, drifted 1.7 m
    # outwards and sideswiped a B1 car stopped in the next lane (2026-10-11 run 10)
    off = lane_offset(aim, loc) - shift_now
    delta -= max(-CROSS_TRACK_MAX_DEG, min(CROSS_TRACK_MAX_DEG, math.degrees(math.atan2(CROSS_TRACK_GAIN * off, sp + 1.0))))
    steer = max(-1.0, min(1.0, delta / max_steer))
    if hard:
        ctl = carla.VehicleControl(throttle=0.0, steer=steer, brake=1.0)
    else:
        err = target_mps - sp
        # throttle off at 0.5 m/s over: off at 1.0, cars speeding back up after a slow lead overshot by up to
        # 2.6 km/h, and the 1.03 x tier (2.3 km/h under the E1 trigger) was flagged twice (2026-10-14 run 8)
        throttle = max(0.0, min(1.0, 0.35 + 0.15 * err + bias)) if err > -0.5 and target_mps > 0.0 else 0.0
        brake = 1.0 if target_mps <= 0.0 and sp < 2.0 else max(0.0, min(1.0, -0.2 * err)) if err < -1.5 else 0.0
        ctl = carla.VehicleControl(throttle=throttle, steer=steer, brake=brake)
    v.apply_control(ctl)
    return ctl, off


# --- drivers -----------------------------------------------------------------------------------

class HandDriver:
    """A car driven by this script, not the TM. CarlaAir's TM is not fit for it: it steered to full lock
    above ~60 km/h (2026-10-11: |steer| > 0.5 on 20-65% of ticks, every planted speeder wrecked), its cars
    only ever see a 30 km/h limit, vehicle_lane_offset does nothing, and even at 25 km/h the TM drove
    planted B1 / B2 cars into the guardrail (16 times in one 5 min run).

    drive() runs every tick: pure pursuit on the lane (0.5 m max error at 110 km/h on road 45's arc), at
    the desired speed (fixed, or factor x the scene's posted limit), held down by the lead (follow_speed),
    the bends ahead (curve_cap) and a full brake below TTC_BRAKE_S. plan() runs at 10 Hz and starts lane
    changes: a pass when the lead holds the car below pass_below x its desired speed and a neighbour lane
    is clear and faster, and (keep_right) a return right when that lane is clear. A lane change blends
    sideways over LC_S and turns back if the target lane stops being clear in its first half."""

    def __init__(self, actor, kind: str, args, scene, now: float, ctx: Ctx, factor: float = 1.0,
                 fixed_mps: float | None = None, can_pass: bool = True, pass_below: float = 0.92,
                 keep_right: bool = False):
        self.actor, self.kind, self.args, self.scene, self.ctx = actor, kind, args, scene, ctx
        self.factor, self.fixed_mps = factor, fixed_mps
        self.can_pass, self.pass_below, self.keep_right = can_pass, pass_below, keep_right
        self.state, self.t_state, self.slow_since = "cruise", now, None
        self.max_steer = actor.get_physics_control().wheels[0].max_steer_angle
        self.lc = None  # lane change in progress: {to, from (lane ids), side (+1 right), t0, why}
        self.last_lc = now
        self.lead = None
        self.target = 0.0
        self.limit = None
        self.bias, self.t_last = 0.0, now
        self.hard = False
        self.dbg = None  # --debug: the last drive_lane() decision
        self.main_wp = None  # track(): the last mainline waypoint
        self.off_since = None  # when it left the stretch's lanes (main loop)
        actor.set_autopilot(False)

    def set_state(self, s: str, now: float):
        self.state, self.t_state = s, now

    def track(self, loc):
        """The car's lane waypoint, kept on the mainline: near an exit's gore get_waypoint() returns the ramp
        lane for a car still in lane 6, and the car then followed the ramp (2026-10-14 run 5: slow cars on
        ramp r1076 at 30 km/h). Off the mainline, the last mainline waypoint is advanced instead, as long as
        the car is within 3 m of it."""
        wp = self.scene.m.get_waypoint(loc)
        if wp is None:
            return None
        if on_main(wp, self.scene):
            self.main_wp = wp
            return wp
        if self.main_wp is not None:
            nxt = self.main_wp.next(max(0.1, dist(self.main_wp.transform.location, loc)))
            if nxt:
                c = mainline(nxt, self.main_wp, self.scene)
                if on_main(c, self.scene) and dist(c.transform.location, loc) < 3.0:
                    self.main_wp = c
                    return c
        return wp

    def reset(self, now: float):
        """A fresh pass (launched, or recycled to the upstream end): no lane change, cruising."""
        self.main_wp, self.off_since = None, None
        self.ctx.goal_of.pop(self.actor.id, None)
        self.lc, self.last_lc, self.lead, self.hard = None, now, None, False
        self.bias, self.t_last, self.slow_since = 0.0, now, None
        self.set_state("cruise", now)
        self.actor.set_light_state(carla.VehicleLightState.NONE)

    def desired(self) -> float:
        return self.fixed_mps if self.fixed_mps is not None else (self.limit or NOMINAL_LIMIT_KMH) * self.factor / 3.6

    def count(self, what: str):
        self.ctx.stats[f"{self.kind} {what}"] += 1

    # lane changes

    def start_lc(self, now, wp, nb, side: int, why: str):
        self.lc = {"to": nb.lane_id, "from": wp.lane_id, "side": side, "t0": now, "why": why}
        self.ctx.goal_of[self.actor.id] = (nb.road_id, nb.lane_id)
        self.actor.set_light_state(carla.VehicleLightState.RightBlinker if side > 0 else carla.VehicleLightState.LeftBlinker)
        self.count(f"{why} started")

    def end_lc(self, now, how: str):
        self.count(f"{self.lc['why']} {how}")
        self.lc = None
        self.last_lc = now
        self.ctx.goal_of.pop(self.actor.id, None)
        self.actor.set_light_state(carla.VehicleLightState.NONE)

    def usable(self, nb, wp) -> bool:
        """nb is a same-direction driving lane that goes on next to wp's for 100 m (not an exit lane)."""
        if (nb is None or nb.lane_type != carla.LaneType.Driving or nb.lane_id * wp.lane_id <= 0
                or nb.is_junction or wp.is_junction):
            return False
        a, b = nb.next(100.0), wp.next(100.0)
        return bool(a) and bool(b) and min(dist(x.transform.location, y.transform.location) for x in a for y in b) < 1.6 * nb.lane_width

    def lane_lead(self, nb, loc, fwd, vehicles):
        ahead = [(d, u) for along, d, u in lane_cars(loc, fwd, path_keys(nb, LOOK_M), vehicles, self.ctx, self.actor.id)
                 if along > 3.0]
        return min(ahead) if ahead else None

    def lane_safe(self, nb, loc, fwd, sp, vehicles, behind_s: float, ttc_s: float) -> bool:
        """No car in nb too close ahead / behind, or closing in less than ttc_s. A car behind needs
        behind_s x its own speed of room (1.5 s when returning in front of the car just passed)."""
        for along, _, u in lane_cars(loc, fwd, path_keys(nb, LOOK_M, LOOK_M), vehicles, self.ctx, self.actor.id):
            if along >= 0:
                if along < max(10.0, 0.5 * sp) or (sp > u and (along - 6.0) / (sp - u) < ttc_s):
                    return False
            elif -along < max(10.0, behind_s * u) or (u > sp and (-along - 6.0) / (u - sp) < ttc_s):
                return False
        return True

    def plan(self, now, vehicles):
        if self.state != "cruise":
            return
        tf = self.actor.get_transform()
        loc, fwd = tf.location, tf.get_forward_vector()
        vel = self.actor.get_velocity()
        sp = math.hypot(vel.x, vel.y)
        wp = self.track(loc)
        if wp is None:
            return
        if self.lc is not None:  # turn back if the target lane stopped being clear in the first half
            s = (now - self.lc["t0"]) / LC_S
            tw = lane_wp(wp, self.lc["to"])
            if tw is not None and s < 0.5 and not self.lane_safe(tw, loc, fwd, sp, vehicles, 0.5, 2.5):
                self.count(f"{self.lc['why']} aborted")
                self.lc = {"to": self.lc["from"], "from": self.lc["to"], "side": -self.lc["side"],
                           "t0": now - (1.0 - s) * LC_S, "why": "abort"}
                origin = lane_wp(wp, self.lc["to"])
                if origin is not None:
                    self.ctx.goal_of[self.actor.id] = (origin.road_id, origin.lane_id)
            return
        if now - self.last_lc < LC_COOLDOWN_S or wp.is_junction:
            return
        desired = self.desired()
        cur = follow_speed(desired, self.lead)
        if self.can_pass and self.lead is not None and self.lead[1] < self.pass_below * desired:
            best = None
            for side, nb in ((-1, wp.get_left_lane()), (1, wp.get_right_lane())):  # left first: pass on the left
                if not self.usable(nb, wp):
                    continue
                gain = follow_speed(desired, self.lane_lead(nb, loc, fwd, vehicles))
                if gain > cur + 2.0 and (best is None or gain > best[0]) and self.lane_safe(nb, loc, fwd, sp, vehicles, 0.8, LC_TTC_S):
                    best = (gain, side, nb)
            if best is not None:
                self.start_lc(now, wp, best[2], best[1], "pass")
                return
        if self.keep_right:
            nb = wp.get_right_lane()
            if self.usable(nb, wp):
                nl = self.lane_lead(nb, loc, fwd, vehicles)
                if (nl is None or nl[0] > FOLLOW_S * sp + FOLLOW_M + 30.0) and self.lane_safe(nb, loc, fwd, sp, vehicles, 1.5, LC_TTC_S):
                    self.start_lc(now, wp, nb, 1, "return")

    def drive_lane(self, now, vehicles):
        v = self.actor
        tf = v.get_transform()
        loc, fwd = tf.location, tf.get_forward_vector()
        vel = v.get_velocity()
        sp = math.hypot(vel.x, vel.y)
        dt, self.t_last = max(0.0, now - self.t_last), now
        wp = self.track(loc)
        if wp is None:
            return
        lane = self.scene.lane(wp)
        if lane is not None and lane.get("speed_limit_kmh") is not None:
            self.limit = float(lane["speed_limit_kmh"])
        desired = self.desired()
        aim, shift_now, shift_ahead, s = wp, 0.0, 0.0, 1.0
        if self.lc is not None:
            tw = lane_wp(wp, self.lc["to"])
            if tw is None:  # the target lane ended: stay where it is
                self.end_lc(now, "lost")
            else:
                aim = tw
                s = (now - self.lc["t0"]) / LC_S
                if s >= 1.0:
                    self.end_lc(now, "done")
                else:
                    self.ctx.goal_of[v.id] = (tw.road_id, tw.lane_id)
                    side, w = self.lc["side"], tw.lane_width
                    shift_now = -side * w * (1.0 - smooth(s))
                    shift_ahead = -side * w * (1.0 - smooth(s + max(6.0, 0.9 * sp) / max(sp, 1.0) / LC_S))
        keys = path_keys(aim, LOOK_M)
        if self.lc is not None and s < 0.5 and wp.lane_id != aim.lane_id:  # still mostly in the old lane
            keys = keys | path_keys(wp, LOOK_M)
        ahead = [(d, u) for along, d, u in lane_cars(loc, fwd, keys, vehicles, self.ctx, v.id) if along > 3.0]
        self.lead = min(ahead) if ahead else None
        target = min(follow_speed(desired, self.lead), curve_cap(aim, self.scene))
        hard = (self.lead is not None and sp > self.lead[1] + 0.5
                and (self.lead[0] - 6.0) / (sp - self.lead[1]) < TTC_BRAKE_S)
        if hard and not self.hard:
            self.count("emergency_brake")
        self.hard, self.target = hard, target
        err = target - sp
        if not hard and abs(err) < 3.0:  # integral: P alone settled 5-10 km/h short at 100+ km/h
            self.bias = max(-0.2, min(0.4, self.bias + 0.04 * err * dt))
        out = pursue(v, aim, target, self.max_steer, self.scene, shift_now, shift_ahead, self.bias, hard)
        if self.args.debug and out is not None:
            ctl, off = out
            self.dbg = {"kmh": round(sp * 3.6, 1), "desired": round(desired * 3.6, 1), "target": round(target * 3.6, 1),
                        "cap": round(min(curve_cap(aim, self.scene), 99.0) * 3.6, 1),
                        "lead_d": round(self.lead[0], 1) if self.lead else "", "lead_kmh": round(self.lead[1] * 3.6, 1) if self.lead else "",
                        "hard": int(hard), "lc": f"{self.lc['why']}->{self.lc['to']}" if self.lc else "",
                        "lc_s": round(s, 2) if self.lc else "", "road": wp.road_id, "lane": wp.lane_id, "aim": f"{aim.road_id}/{aim.lane_id}",
                        "wp_z": round(wp.transform.location.z, 1), "z": round(loc.z, 1),
                        "yaw_err": round((tf.rotation.yaw - wp.transform.rotation.yaw + 180) % 360 - 180, 1),
                        "shift": round(shift_now, 2), "off": round(off, 2), "steer": round(ctl.steer, 3),
                        "throttle": round(ctl.throttle, 2), "brake": round(ctl.brake, 2), "bias": round(self.bias, 3)}

    def drive(self, now, vehicles):
        self.drive_lane(now, vehicles)

    def step(self, now, scene, vehicles):
        self.track_stuck(now)
        self.plan(now, vehicles)

    def track_stuck(self, now):
        """A car crawling while it should be driving (after a crash, or in a jam) is respawned by main():
        below STUCK_KMH, not 1 km/h, so a car creeping in a queue is replaced before it can stand 20 s and
        pass for a B1 stop (2026-10-11 run 9: 4 speeders logged as B1 in the west-end pile-up)."""
        self.slow_since = (self.slow_since or now) if self.state == "cruise" and kmh(self.actor) < STUCK_KMH else None


class SlowCar(HandDriver):
    """Background traffic: a fixed speed below the limit, passes only a car that holds it below 0.7 x its
    speed (a stopped B1 car, a much slower car), and keeps right, so the left lanes are the passing lanes.
    (Spread evenly over all four lanes, the slow cars held the speeders behind them 85% of the time,
    2026-10-14 run 3.)"""

    def __init__(self, actor, args, scene, now, ctx, mps: float):
        super().__init__(actor, "S", args, scene, now, ctx, fixed_mps=mps, pass_below=0.7, keep_right=True)


class Speeder(HandDriver):
    """E1: tier x the posted limit, passing slower cars and returning right."""

    def __init__(self, actor, args, scene, now, ctx, tier: float):
        super().__init__(actor, "E1", args, scene, now, ctx, factor=tier, keep_right=True)
        self.tier = tier


class Stopper(HandDriver):
    """B1 / B2: cruise at CRUISE_FACTOR x the limit (63 km/h in a 90), then every --cooldown-s stop for
    --hold-s with hazards on where the road forbids it. B1 stops in its lane; B2 (which keeps to the outer
    lane, no passing) first steers onto the >= 3 m shoulder at 20 km/h, and back into the lane after."""

    def __init__(self, actor, kind: str, args, scene, now: float, ctx: Ctx):
        super().__init__(actor, kind, args, scene, now, ctx, factor=CRUISE_FACTOR, can_pass=(kind == "B1"))
        self.next_stop = now + random.uniform(15.0, 45.0)
        self.stops = 0

    def drive(self, now: float, vehicles):
        v = self.actor
        if self.state == "cruise":
            self.drive_lane(now, vehicles)
            return
        if self.state == "hold":
            v.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True))
            return
        loc = v.get_location()
        wp = self.scene.m.get_waypoint(loc, project_to_road=True, lane_type=LANE_TYPES)
        if wp is None:
            return
        age = now - self.t_state
        on_shoulder = wp.lane_type == carla.LaneType.Shoulder
        if self.state == "braking":  # B1: to rest in its lane
            if kmh(v) < 0.7:
                self.set_state("hold", now)
                return
            pursue(v, wp, 0.0, self.max_steer, self.scene)
        elif self.state in ("pull_over", "settle"):  # B2: onto the shoulder, then to rest on it
            aim = wp if on_shoulder else (wide_shoulder(wp) or wp)
            if self.state == "pull_over" and ((on_shoulder and abs(lane_offset(wp, loc)) < 0.7) or age > 10.0):
                v.set_light_state(HAZARDS)
                self.set_state("settle", now)
            if self.state == "settle" and kmh(v) < 0.7:
                self.set_state("hold", now)
                return
            pursue(v, aim, 0.0 if self.state == "settle" else PULL_OVER_KMH / 3.6, self.max_steer, self.scene)
        elif self.state == "rejoin":  # B2: back into the outer lane
            lane = wp.get_left_lane() if on_shoulder else wp
            if lane is None or lane.lane_type != carla.LaneType.Driving:
                lane = wp
            if (not on_shoulder and abs(lane_offset(wp, loc)) < 0.6) or age > 10.0:
                self.resume(now)
                return
            pursue(v, lane, PULL_OVER_KMH / 3.6, self.max_steer, self.scene)

    def resume(self, now):
        self.actor.set_light_state(carla.VehicleLightState.NONE)
        self.stops += 1
        self.next_stop = now + self.args.cooldown_s
        self.set_state("cruise", now)
        print(f"[viol] {self.kind} car {self.actor.id} rejoins traffic (stop {self.stops})", flush=True)

    def lane_clear(self, wp, others, ahead_m: float, behind_m: float) -> bool:
        tf = self.actor.get_transform()
        _, clear = path_gap(tf.location, tf.get_forward_vector(), path_keys(wp, ahead_m, behind_m), others,
                            self.ctx, self.actor.id, behind_m, ahead_m)
        return clear

    def step(self, now: float, scene: Scene, others):
        v = self.actor
        self.track_stuck(now)
        self.plan(now, others)
        age = now - self.t_state
        if self.state == "cruise":
            if self.lc is not None:
                return
            wp, lane = scene.at(v.get_location())
            if now < self.next_stop or wp is None or lane is None or wp.is_junction or wp.lane_type != carla.LaneType.Driving:
                return
            if not Scene.no_stopping(lane) or kmh(v) < 20:
                return
            # all of the next 80 m, not just its end: braking from 63 km/h, 2 of 6 stops came to rest on r775, the
            # mainline's junction piece at the exit fork (y ~ -90), which is not a no-stopping lane (2026-10-14 run 8)
            ahead, w = None, wp
            for _ in range(8):
                nxt = w.next(10.0)
                if not nxt:
                    return
                w = mainline(nxt, w, scene)
                if w.is_junction or not Scene.no_stopping(scene.lane(w)):
                    return
                ahead = [w]
            # and the stretch must go on STOP_END_CLEAR_M past the stop: at its downstream end (y > 80) drivers weave by
            # up to 3 m at 100+ km/h (also without stopped cars), and stops there were hit by weaving cars or left a queue
            # in their path (2026-10-14 runs 10-11: 2 stopped cars hit). Kept south, no stopped car was hit (run 15); the
            # weave itself still causes 0-5 contacts per 10 min with stoppers on, all at y 117-168 (Highway_Violations.md)
            for _ in range(int((STOP_END_CLEAR_M - 80.0) // 10)):
                nxt = w.next(10.0)
                if not nxt:
                    return
                w = mainline(nxt, w, scene)
            cx, cy = self.args.center
            if dist(w.transform.location, carla.Location(cx, cy, 0.0)) > self.args.radius:
                return
            if not self.lane_clear(wp, others, 40.0, 60.0):
                return
            if self.kind == "B2":
                if wide_shoulder(wp) is None or wide_shoulder(ahead[0]) is None:
                    return
                v.set_light_state(carla.VehicleLightState(int(carla.VehicleLightState.RightBlinker)))
                self.set_state("pull_over", now)
            else:
                v.set_light_state(HAZARDS)
                self.set_state("braking", now)
            print(f"[viol] {self.kind} car {v.id} starts its stop at ({v.get_location().x:.0f}, {v.get_location().y:.0f})", flush=True)
        elif self.state == "hold" and age >= self.args.hold_s:
            if self.kind == "B2":
                v.set_light_state(carla.VehicleLightState(int(carla.VehicleLightState.LeftBlinker)))
                self.set_state("rejoin", now)  # drive() steers it back into the outer lane
            else:
                self.resume(now)


def spawn_speed(wp, want: float, actors, ctx: Ctx, planned: dict | None = None):
    """The speed (<= want) a car can be spawned at in wp's lane without closing on the car ahead faster
    than SPAWN_TTC_S allows, or None if a car behind would close on it too fast / anything is too near.
    planned: speeds of parked cars that are about to be launched."""
    loc, fwd = wp.transform.location, wp.transform.get_forward_vector()
    keys = path_keys(wp, LOOK_M, LOOK_M)
    near = []
    for o in actors:
        ol = o.get_location()
        if dist(loc, ol) < SPAWN_CLEAR_M:
            return None
        dx, dy = ol.x - loc.x, ol.y - loc.y
        along = dx * fwd.x + dy * fwd.y
        if o.id in ctx.lane_of:
            road, lane, _ = ctx.lane_of[o.id]
            on = (road, lane) in keys or ctx.goal_of.get(o.id) in keys
        else:  # spawned since the last 10 Hz sample
            on = abs(dx * fwd.y - dy * fwd.x) < 2.5 and abs(along) < LOOK_M
        if on:
            ov = o.get_velocity()
            near.append((along, planned[o.id] if planned and o.id in planned else math.hypot(ov.x, ov.y)))
    v0 = want
    for along, u in near:
        if along >= 0:  # along >= 15 + SPAWN_TTC_S x (v0 - u) + 0.5 v0, and no harder braking than following needs
            v0 = min(v0, (along - 15.0 + SPAWN_TTC_S * u) / (SPAWN_TTC_S + 0.5), follow_speed(want, (along, u)))
    for along, u in near:
        if along < 0 and -along < 15.0 + max(0.0, u - v0) * SPAWN_TTC_S + 0.5 * u:
            return None
    return v0


# --- truth monitor -----------------------------------------------------------------------------

class Monitor:
    def __init__(self, scene: Scene, run_dir: Path, t0: float, ctx: Ctx, last_crash: dict, planted: dict, label):
        self.scene, self.t0, self.ctx, self.last_crash = scene, t0, ctx, last_crash
        self.planted, self.label = planted, label
        self.first_seen: dict[int, float] = {}
        self.meta: dict[int, str] = {}
        self.speed_ep: dict[int, dict] = {}
        self.stop_ep: dict[int, dict] = {}
        self.events: list[dict] = []
        self.tracks_f = open(run_dir / "tracks.csv", "w", newline="")
        self.tracks = csv.writer(self.tracks_f)
        self.tracks.writerow(["t", "id", "type", "role", "x", "y", "z", "yaw", "kmh", "lane", "lane_type", "limit_kmh"])

    def _event(self, vid, rule, ep, status, detail, at_end=False):
        self.events.append({"vehicle_id": vid, "model": self.meta[vid], "planted": vid in self.planted, "rule": rule,
                            "status": status, "start_s": round(ep["start"] - self.t0, 1), "end_s": round(ep["last"] - self.t0, 1),
                            "duration_s": round(ep["last"] - ep["start"], 1), **detail,
                            "x": round(ep["x"], 1), "y": round(ep["y"], 1), "lane": ep["lane"],
                            "ongoing_at_end": at_end})

    def _close_speed(self, vid, ep, at_end=False):
        if ep["last"] - ep["start"] >= E1_MIN_S:
            self._event(vid, "E1", ep, "confirmed", {"max_kmh": round(ep["max"], 1), "limit_kmh": ep["limit"]}, at_end)

    def _close_stop(self, vid, ep, at_end=False):
        if ep["last"] - ep["start"] < STOP_MIN_S:
            return
        n = max(ep["n"], 1)
        rule = "B2" if ep["shoulder"] / n >= 0.5 else "B4" if ep["ramp"] / n >= 0.5 else "B5" if ep["bridge"] / n >= 0.5 else "B1"
        crash = self.last_crash.get(vid)
        status = ("after_crash" if crash is not None and ep["start"] - CRASH_STOP_WINDOW_S <= crash <= ep["last"] else
                  "queue_suppressed" if ep["queue"] / n >= 0.5 else
                  "possible_breakdown" if rule == "B2" else "confirmed")
        self._event(vid, rule, ep, status, {"max_kmh": None, "limit_kmh": None}, at_end)

    def sample(self, t: float, vehicles):
        info = []
        for v in vehicles:
            vid = v.id
            if vid not in self.meta:
                self.meta[vid] = v.type_id
                self.first_seen[vid] = t
            tf = v.get_transform()
            loc, sp = tf.location, kmh(v)
            wp, lane = self.scene.at(loc)
            if wp is not None:
                rv, c = wp.transform.get_right_vector(), wp.transform.location
                self.ctx.lane_of[vid] = (wp.road_id, wp.lane_id, (loc.x - c.x) * rv.x + (loc.y - c.y) * rv.y)
            lane_id = lane["id"] if lane else ""
            ltype = lane["lane_type"] if lane else ""
            limit = float(lane["speed_limit_kmh"]) if lane and lane.get("speed_limit_kmh") is not None else None
            self.tracks.writerow([f"{t:.2f}", vid, self.meta[vid], self.label(vid), f"{loc.x:.2f}", f"{loc.y:.2f}",
                                  f"{loc.z:.2f}", f"{tf.rotation.yaw:.1f}", f"{sp:.1f}", lane_id, ltype, limit or ""])
            info.append((v, vid, loc, tf.get_forward_vector(), sp, wp, lane, limit))

        live = set()
        for v, vid, loc, fwd, sp, wp, lane, limit in info:
            live.add(vid)
            # E1
            fast = (limit is not None and ltype_driving(lane) and sp > limit + tolerance_kmh(limit)
                    and t - self.first_seen[vid] >= E1_MIN_AGE_S)
            ep = self.speed_ep.get(vid)
            if ep is not None:
                gap = E1_CLOSE_GAP_S if ep["last"] - ep["start"] >= E1_MIN_S else E1_CONFIRM_GAP_S
                if t - ep["last"] > gap:
                    self._close_speed(vid, ep)
                    ep = self.speed_ep[vid] = None
            if fast:
                if ep is None:
                    ep = self.speed_ep[vid] = {"start": t, "last": t, "max": sp, "limit": limit,
                                               "x": loc.x, "y": loc.y, "lane": lane["id"]}
                ep["last"], ep["max"] = t, max(ep["max"], sp)
            # B1 / B2 / B4 / B5
            stopped = sp < STOP_MAX_KMH and Scene.no_stopping(lane)
            if stopped and v.is_at_traffic_light() and v.get_traffic_light_state() in (
                    carla.TrafficLightState.Red, carla.TrafficLightState.Yellow):
                stopped = False
            ep = self.stop_ep.get(vid)
            if ep is not None and t - ep["last"] > STOP_CLOSE_GAP_S:
                self._close_stop(vid, ep)
                ep = self.stop_ep[vid] = None
            if stopped:
                if ep is None:
                    ep = self.stop_ep[vid] = {"start": t, "last": t, "n": 0, "shoulder": 0, "queue": 0, "ramp": 0,
                                              "bridge": 0, "x": loc.x, "y": loc.y, "lane": lane["id"]}
                ep["last"] = t
                ep["n"] += 1
                ep["shoulder"] += int(lane["lane_type"] == "shoulder")
                ep["ramp"] += int(bool(lane.get("ramp")))
                ep["bridge"] += int(bool(lane.get("bridge") or lane.get("tunnel")))
                ep["queue"] += int(self._queued(vid, loc, fwd, info))
        for d, close in ((self.speed_ep, self._close_speed), (self.stop_ep, self._close_stop)):
            for vid in [i for i, e in d.items() if e is not None and i not in live]:  # destroyed mid-episode
                close(vid, d.pop(vid))

    def reset(self, vid, t: float):
        """A recycled car starts a new pass: its open episodes end where it left the stretch, and it is new
        to the E1 age check again."""
        for d, close in ((self.speed_ep, self._close_speed), (self.stop_ep, self._close_stop)):
            ep = d.pop(vid, None)
            if ep is not None:
                close(vid, ep)
        self.first_seen[vid] = t

    @staticmethod
    def _queued(vid, loc, fwd, info) -> bool:
        for _, oid, oloc, _, osp, _, _, _ in info:
            if oid == vid or osp >= STOP_MAX_KMH:
                continue
            dx, dy = oloc.x - loc.x, oloc.y - loc.y
            along, lat = dx * fwd.x + dy * fwd.y, abs(dx * fwd.y - dy * fwd.x)
            if 0 < along < QUEUE_AHEAD_M and lat < QUEUE_LATERAL_M:
                return True
        return False

    def finish(self):
        for vid, ep in self.speed_ep.items():
            if ep is not None:
                self._close_speed(vid, ep, at_end=True)
        for vid, ep in self.stop_ep.items():
            if ep is not None:
                self._close_stop(vid, ep, at_end=True)
        self.tracks_f.close()
        self.events.sort(key=lambda e: e["start_s"])


def ltype_driving(lane) -> bool:
    return lane is not None and lane["lane_type"] == "driving"


# --- crashes -----------------------------------------------------------------------------------

class CrashLog:
    """A collision sensor on every vehicle, background traffic too. A stop that begins within
    CRASH_STOP_WINDOW_S after the car was in a collision is a wreck, not a parking violation: it is
    reported as after_crash and not counted (2026-10-11 run 6: 43 background "B2" stops, a control run
    without planted cars had none; the cars were knocked across lanes onto the shoulder)."""

    def __init__(self, world, label):
        self.world, self.label = world, label
        self.bp = world.get_blueprint_library().find("sensor.other.collision")
        self.bp.set_attribute("role_name", ROLE)
        self.sensors: dict[int, object] = {}
        self.last_crash: dict[int, float] = {}
        self.last_pair: dict[tuple, float] = {}
        self.rows: list[dict] = []

    def ensure(self, vehicles):
        live = {v.id for v in vehicles}
        for v in vehicles:
            if v.id not in self.sensors:
                s = self.world.try_spawn_actor(self.bp, carla.Transform(), attach_to=v)
                if s is not None:
                    s.listen(lambda e, vid=v.id: self.on_hit(vid, e))
                    self.sensors[v.id] = s
        for vid in [i for i in self.sensors if i not in live]:
            self.drop(vid)

    def drop(self, vid):
        s = self.sensors.pop(vid, None)
        if s is not None:
            try:
                s.stop()
                s.destroy()
            except RuntimeError:
                pass

    def on_hit(self, vid, e):
        other = e.other_actor
        oid = other.id if other is not None else 0
        t = e.timestamp
        self.last_crash[vid] = t
        key = (min(vid, oid), max(vid, oid)) if other is not None and other.type_id.startswith("vehicle.") else (vid, oid)
        if t - self.last_pair.get(key, -1e9) < COLLISION_DEDUP_S:  # the sensor fires every tick of a contact
            self.last_pair[key] = t
            return
        self.last_pair[key] = t
        imp = e.normal_impulse
        self.rows.append({"t": round(t, 2), "vehicle_id": vid, "who": self.label(vid), "other_id": oid,
                          "other": other.type_id if other is not None else "",
                          "other_who": self.label(oid) if other is not None and other.type_id.startswith("vehicle.") else "static",
                          "impulse": round(math.sqrt(imp.x ** 2 + imp.y ** 2 + imp.z ** 2), 1)})

    def close(self):
        for vid in list(self.sensors):
            self.drop(vid)


# --- report ------------------------------------------------------------------------------------

def write_report(run_dir: Path, mon: Monitor, violators: dict, tier_of: dict, passes: Counter, crash: CrashLog,
                 stats: Counter, t0: float, t1: float, args):
    collisions = crash.rows
    fields = ["vehicle_id", "model", "planted", "rule", "status", "start_s", "end_s", "duration_s", "max_kmh",
              "limit_kmh", "x", "y", "lane", "ongoing_at_end"]
    with open(run_dir / "violations.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(mon.events)
    with open(run_dir / "collisions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "vehicle_id", "who", "other_id", "other", "other_who", "impulse"])
        w.writeheader()
        w.writerows(collisions)

    minutes = max((t1 - t0) / 60.0, 1e-6)
    ev = mon.events
    counted = [e for e in ev if e["status"] not in ("queue_suppressed", "after_crash")]
    rules = ("E1", "B1", "B2", "B4", "B5")
    mine = {vid: [e for e in counted if e["vehicle_id"] == vid] for vid in violators}
    bg = [e for e in counted if not e["planted"]]
    planted_crashes = [c for c in collisions if c["vehicle_id"] in violators or c["other_id"] in violators]
    other_crashes = len(collisions) - len(planted_crashes)
    e1_planted = [e for e in counted if e["planted"] and e["rule"] == "E1"]

    tiers = []
    for tier in sorted(set(tier_of.values())):
        cars = [v for v, t in tier_of.items() if t == tier]
        evs = [e for e in e1_planted if tier_of.get(e["vehicle_id"]) == tier]
        tiers.append({"tier": tier, "target_kmh": round(tier * NOMINAL_LIMIT_KMH), "cars": len(cars),
                      "passes": sum(passes[v] for v in cars), "e1_events": len(evs),
                      "max_kmh": max((e["max_kmh"] for e in evs), default=None),
                      "crashes": sum(1 for c in planted_crashes if c["vehicle_id"] in cars or c["other_id"] in cars)})

    summary = {"duration_s": round(t1 - t0, 1), "vehicles_seen": len(mon.meta), "events": len(ev),
               "e1_planted_per_min": round(len(e1_planted) / minutes, 2),
               "planted": [{"vehicle_id": v, "model": m, "planted_as": k, "tier": tier_of.get(v),
                            "events": {r: sum(1 for e in mine[v] if e["rule"] == r) for r in rules if any(e["rule"] == r for e in mine[v])}}
                           for v, (k, m) in violators.items()],
               "tiers": tiers, "driver_stats": dict(stats),
               "background_counted_by_rule": {r: sum(1 for e in bg if e["rule"] == r) for r in rules},
               "collisions": len(collisions), "collisions_with_planted_car": len(planted_crashes), "args": vars(args)}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    def detail(e):
        d = (f"max {e['max_kmh']:.0f} km/h in a {e['limit_kmh']:.0f} zone" if e["rule"] == "E1"
             else f"stopped {e['duration_s']:.0f} s" + (" on the shoulder" if e["rule"] == "B2" else " in a lane"))
        return d + (" (still going at the end)" if e["ongoing_at_end"] else "")

    n_kind = {k: sum(1 for kk, _ in violators.values() if kk == k) for k in ("E1", "B1", "B2")}
    hit = {k: sum(1 for v, (kk, _) in violators.items() if kk == k and any(e["rule"] == k for e in mine[v])) for k in n_kind}
    cx, cy = args.center
    background = (f"{args.slow_cars} scripted slow cars at {args.slow_kmh[0]:.0f}-{args.slow_kmh[1]:.0f} km/h"
                  if args.slow_cars else "Traffic Manager traffic (traffic_flow.py)")
    L = [f"# Highway violations - Town04, {run_dir.name}", "",
         f"{minutes:.1f} min of traffic within {args.radius:.0f} m of ({cx:.0f}, {cy:.0f}), heading {args.heading}, "
         f"{len(mon.meta)} vehicles seen. Background: {background}. "
         f"Judged from CARLA's true positions with the engine's thresholds.", "",
         "## Planted violators", "",
         "| Rule | Planted cars | Cars that committed it | Events |", "|---|---|---|---|"]
    for k, name in (("E1", "speeding"), ("B1", "stop in a highway lane"), ("B2", "stop on the shoulder")):
        L.append(f"| {k} {name} | {n_kind[k]} | {hit[k]} | {sum(1 for v, (kk, _) in violators.items() if kk == k for e in mine[v] if e['rule'] == k)} |")
    L += ["", f"({args.speeders} E1, {args.stoppers} B1 and {args.shoulder_stoppers} B2 cars. A car that leaves the "
          "stretch or gets stuck is teleported back to the upstream end at speed: a new pass.)", "",
          f"E1 events by planted speeders: {len(e1_planted)} ({len(e1_planted) / minutes:.1f} per minute).", "",
          "| Speed tier | Target | Cars | Passes | E1 events | Highest km/h | Collisions |", "|---|---|---|---|---|---|---|"]
    for t in tiers:
        neg = " (inside tolerance: should be 0)" if t["tier"] * NOMINAL_LIMIT_KMH <= NOMINAL_LIMIT_KMH + tolerance_kmh(NOMINAL_LIMIT_KMH) else ""
        L.append(f"| {t['tier']:.2f} x{neg} | {t['target_kmh']} km/h | {t['cars']} | {t['passes']} | {t['e1_events']} | "
                 f"{t['max_kmh'] if t['max_kmh'] is not None else '-'} | {t['crashes']} |")
    L += ["", "Driver events (lane changes: `pass` to get past a slower car, `return` back to the right, `abort` "
          "a change turned back because the target lane stopped being clear):", "",
          "| Event | Count |", "|---|---|"]
    L += [f"| {k} | {n} |" for k, n in sorted(stats.items())] or ["| - | 0 |"]
    L += ["", "| Car | Model | Planted as | Rule | Status | Start (s) | Detail | Where (x, y) |", "|---|---|---|---|---|---|---|---|"]
    for e in ev:
        if e["planted"] and e["status"] not in ("queue_suppressed", "after_crash"):
            tier = f" {tier_of[e['vehicle_id']]:.2f}x" if e["vehicle_id"] in tier_of else ""
            L.append(f"| {e['vehicle_id']} | {e['model'].split('.', 1)[-1]} | {violators[e['vehicle_id']][0]}{tier} | {e['rule']} | "
                     f"{e['status']} | {e['start_s']:.0f} | {detail(e)} | ({e['x']:.0f}, {e['y']:.0f}) |")
    L += ["", "## Background traffic", ""]
    if args.slow_cars:
        L += ["Violations by the scripted slow cars. They never drive above the limit and only stop behind a stopped "
              "car, so anything here points at a driver bug or a crash.", ""]
    else:
        L += ["Violations by the Traffic Manager's own cars. In CarlaAir these are mostly the TM misbehaving rather than "
              "real driving: its cars scrape guardrails and pile up at merges, and some stall on the shoulder. Treat "
              "them as noise, not as staged violations.", ""]
    L += ["| Rule | Events | Cars |", "|---|---|---|"]
    L += [f"| {r} | {sum(1 for e in bg if e['rule'] == r)} | {len({e['vehicle_id'] for e in bg if e['rule'] == r})} |"
          for r in rules if any(e["rule"] == r for e in bg)] or ["| - | 0 | 0 |"]
    n_crash_stop = sum(1 for e in ev if e["status"] == "after_crash")
    n_queue = sum(1 for e in ev if e["status"] == "queue_suppressed")
    L += ["", f"Not counted: {n_crash_stop} stops right after a collision (`after_crash`, a wreck is not illegal "
          f"parking) and {n_queue} stops in a queue behind another stopped car (`queue_suppressed`).", "",
          "## Collisions", "",
          f"{len(collisions)} contacts in all (one row per contact): {len(planted_crashes)} involving a planted car, "
          f"{other_crashes} between background cars or with the road side.", "",
          "| Car | Hit | Count |", "|---|---|---|"]
    pairs = {}
    for c in collisions:
        a, b = c["who"], c["other_who"]
        if c["other_id"] in violators and c["vehicle_id"] not in violators:
            a, b = b, a
        if b == "static":
            b = c["other"].split(".", 1)[-1]  # static.guardrail -> guardrail, traffic.speed_limit.90 -> speed_limit.90
        pairs[(a, b)] = pairs.get((a, b), 0) + 1
    L += [f"| {a} | {b} | {n} |" for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1])] or ["| - | - | 0 |"]
    L += ["", "Files: violations.csv (every event, every status), collisions.csv, tracks.csv (every car at 10 Hz), summary.json."]
    (run_dir / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


# --- main --------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--center", type=float, nargs=2, default=(-10.0, -10.0), metavar=("X", "Y"),
                    help="Default: the middle of Town04's east straight (425 m at 90 km/h). Road 45's arc: -311.6 436")
    ap.add_argument("--radius", type=float, default=210.0,
                    help="Cars that leave it are respawned at the upstream end; 210 m ends just before the bends "
                         "at both ends of the east straight (road 45: 230)")
    ap.add_argument("--heading", default="90",
                    help="Only lanes heading this way (yaw degrees, +-30), or 'any'. 90 = northbound on the east "
                         "straight; the southbound side drops to 60 / 30 km/h in the scene")
    ap.add_argument("--slow-cars", type=int, default=12,
                    help="Scripted background cars; 0 = use traffic_flow.py's TM cars. 18-20 filled all four lanes "
                         "at ~60 km/h and the speeders spent 85%% of the time stuck behind them")
    ap.add_argument("--slow-kmh", type=float, nargs=2, default=(50.0, 80.0), metavar=("MIN", "MAX"))
    ap.add_argument("--speeders", type=int, default=8)
    ap.add_argument("--speed-tiers", type=float, nargs="+", default=(1.03, 1.2, 1.4, 1.6),
                    help="Speeders' speed / posted limit, given out in turn. 1.03 (93 km/h in a 90) is a negative, "
                         "inside the 5 km/h tolerance; 1.05 (94.5) overshot to 95-96 and was flagged (run 6)")
    ap.add_argument("--stoppers", type=int, default=0, help="B1 cars")
    ap.add_argument("--shoulder-stoppers", type=int, default=0, help="B2 cars")
    ap.add_argument("--hold-s", type=float, default=40.0, help="How long a stop lasts (the rule needs 20 s)")
    ap.add_argument("--cooldown-s", type=float, default=60.0, help="Driving time between one car's stops")
    ap.add_argument("--duration", type=float, default=300.0, help="Seconds; 0 = until Ctrl+C")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--keep", action="store_true", help="Leave the script's cars in the world at the end")
    ap.add_argument("--debug", action="store_true", help="Write every driver's decisions at 10 Hz to debug.csv")
    args = ap.parse_args()
    random.seed(args.seed)

    client = carla.Client(args.host, args.port)
    client.set_timeout(20.0)
    world = client.get_world()
    m = world.get_map()
    if "Town04" not in m.name:
        raise SystemExit(f"Map is {m.name}; load Town04 first (load_map.py Town04)")
    scene = Scene(SCENE, m)
    cx, cy = args.center
    centre = carla.Location(cx, cy, 0.0)
    heading = None if args.heading == "any" else float(args.heading)

    def heading_ok(wp) -> bool:
        return heading is None or abs((wp.transform.rotation.yaw - heading + 180) % 360 - 180) <= 30.0

    spots = []  # highway driving lanes in the radius, where cars (re)spawn
    for wp in m.generate_waypoints(4.0):
        lane = scene.lane(wp)
        if (wp.lane_type == carla.LaneType.Driving and not wp.is_junction and lane and lane["road_class"] == "highway"
                and lane.get("speed_limit_kmh") == NOMINAL_LIMIT_KMH and heading_ok(wp)
                and dist(wp.transform.location, centre) <= args.radius * 0.85):
            spots.append(wp)

    def upstream(wp) -> bool:  # far from the centre and heading towards it: the start of the stretch
        loc, fwd = wp.transform.location, wp.transform.get_forward_vector()
        return dist(loc, centre) > 0.45 * args.radius and (centre.x - loc.x) * fwd.x + (centre.y - loc.y) * fwd.y > 0

    up_spots = [wp for wp in spots if upstream(wp)]
    outer = [wp for wp in spots if wide_shoulder(wp) is not None]
    if not spots or not up_spots or ((args.stoppers or args.shoulder_stoppers) and not outer):
        raise SystemExit("No usable 90 km/h highway lanes near --center (check --center / --radius / --heading)")
    print(f"[viol] {len(spots)} highway spawn spots ({len(up_spots)} upstream, {len(outer)} next to a shoulder)")
    t_warm = time.time()
    n_warm = warm_caches(m, scene, centre, args.radius)
    print(f"[viol] lane caches warmed for {n_warm} waypoints in {time.time() - t_warm:.1f} s", flush=True)

    bps = [bp for bp in world.get_blueprint_library().filter("vehicle.*")
           if int(bp.get_attribute("number_of_wheels")) == 4 and bp.id not in BUS_BLUEPRINTS]
    fast_bps = [bp for bp in bps if bp.id not in SLOW_MODELS
                and (not bp.has_attribute("base_type") or bp.get_attribute("base_type").as_str().lower() in ("car", ""))] or bps
    ctx = Ctx()
    violators: dict = {}  # live drivers: id -> SlowCar | Speeder | Stopper
    planted: dict[int, tuple] = {}  # every planted violator ever: id -> (rule, model)
    traffic: set[int] = set()  # every slow car ever
    tier_of: dict[int, float] = {}
    tier_turn = [0]

    def label(vid) -> str:
        return planted[vid][0] if vid in planted else "slow" if vid in traffic else "background"

    old = [a for a in world.get_actors().filter("vehicle.*") if a.attributes.get("role_name") == ROLE]
    old += [a for a in world.get_actors().filter("sensor.other.collision")
            if a.parent is None or a.attributes.get("role_name") == ROLE or a.parent.attributes.get("role_name") == ROLE]
    if old:  # left over from a run that was killed (stage_violations.py uses the same role: not alongside it)
        client.apply_batch([carla.command.DestroyActor(a.id) for a in old])
        print(f"[viol] removed {len(old)} script cars / their sensors left from an earlier run")
        time.sleep(1.0)
    others = [a for a in world.get_actors().filter("vehicle.*") if a.attributes.get("role_name") != ROLE]
    if others and args.slow_cars:
        print(f"[viol] warning: {len(others)} other vehicles in the world (traffic_flow.py running?). With --slow-cars "
              f"the stretch is meant to hold only this script's cars", flush=True)

    planned: dict[int, float] = {}  # speeds of cars spawned but not launched yet (start-up only)
    passes: Counter = Counter()  # vehicle id -> passes along the stretch (launches + recycles)

    def right_rank(wp) -> int:  # same-direction driving lanes to its right: 0 = the outer lane
        n, r = 0, wp.get_right_lane()
        while r is not None and r.lane_type == carla.LaneType.Driving and r.lane_id * wp.lane_id > 0 and n < 8:
            n, r = n + 1, r.get_right_lane()
        return n

    rank = {(wp.road_id, wp.lane_id, round(wp.s)): right_rank(wp) for wp in spots}

    def find_spot(pool, want: float, actors, skip_id=None, kind: str = "E1"):
        """A clear spot in pool and the speed to start at there. Violators take the spot with the most room
        (a recycled speeder put behind a slow car braked hard at once and wasted its pass, run 5); slow cars
        a random one, in the two outer lanes 4 times in 5, so the inner lanes stay free for passing.
        Every spot is tried: with the caches warm (warm_caches) this is cheap."""
        actors = [a for a in actors if a.id != skip_id]
        ok = []
        for wp in random.sample(pool, len(pool)):
            v0 = spawn_speed(wp, min(want, curve_cap(wp, scene)), actors, ctx, planned)
            if v0 is not None and v0 >= 0.5 * want:
                ok.append((v0, wp))
        if not ok:
            return None, None
        if kind == "S":
            outer_ok = [c for c in ok if rank.get((c[1].road_id, c[1].lane_id, round(c[1].s)), 0) <= 1]
            v0, wp = random.choice(outer_ok if outer_ok and random.random() < 0.8 else ok)
        else:
            v0, wp = max(ok, key=lambda c: c[0])
        return wp, v0

    def launch(v, wp, v0: float):  # at speed: it joins moving traffic
        fwd = wp.transform.get_forward_vector()
        v.set_target_velocity(carla.Vector3D(fwd.x * v0, fwd.y * v0, 0.0))
        v.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        passes[v.id] += 1

    def spawn(kind: str, now: float, tier: float | None = None, at_start: bool = False):
        """A new car: at start-up (parked until all are spawned, launched together), or to replace a car that
        was destroyed. Each spawn (car + collision sensor) froze CarlaAir for ~0.6 s with every car driving
        blind (2026-10-14 run 4: 182 spawns in 3 min, all 65 loop gaps > 0.5 s at a spawn), so cars that leave
        the stretch are recycled (teleported), not respawned."""
        if kind == "E1" and tier is None:
            tier = args.speed_tiers[tier_turn[0] % len(args.speed_tiers)]
            tier_turn[0] += 1
        pool = outer if kind == "B2" else spots if at_start else up_spots
        want = (tier * NOMINAL_LIMIT_KMH if kind == "E1" else random.uniform(*args.slow_kmh) if kind == "S"
                else CRUISE_FACTOR * NOMINAL_LIMIT_KMH) / 3.6
        wp, v0 = find_spot(pool, want, list(world.get_actors().filter("vehicle.*")), kind=kind)
        if wp is None:
            print(f"[viol] no clear spot for a {kind} car", flush=True)
            return None
        bp = random.choice(fast_bps if kind == "E1" else bps)
        if bp.has_attribute("color"):
            bp.set_attribute("color", random.choice(bp.get_attribute("color").recommended_values))
        bp.set_attribute("role_name", ROLE)
        v = world.try_spawn_actor(bp, carla.Transform(wp.transform.location + carla.Location(z=0.3), wp.transform.rotation))
        if v is None:
            return None
        if kind == "S":
            vi = SlowCar(v, args, scene, now, ctx, want)
            traffic.add(v.id)
        else:
            vi = Speeder(v, args, scene, now, ctx, tier) if kind == "E1" else Stopper(v, kind, args, scene, now, ctx)
            planted[v.id] = (kind, v.type_id)
            if kind == "E1":
                tier_of[v.id] = tier
        violators[v.id] = vi
        if at_start:
            vi.wp0, planned[v.id] = wp, v0
        else:
            launch(v, wp, v0)
        return vi

    def recycle(vi, t: float) -> bool:
        """Teleport a car that left the stretch (or got stuck) to a clear upstream spot, at speed: a new pass."""
        v = vi.actor
        wp, v0 = find_spot(outer if vi.kind == "B2" else up_spots, vi.desired(), vehicles, skip_id=v.id, kind=vi.kind)
        if wp is None:
            return False  # tried again at the next sample; it drives on meanwhile
        v.set_transform(carla.Transform(wp.transform.location + carla.Location(z=0.3), wp.transform.rotation))
        launch(v, wp, v0)
        vi.reset(t)
        mon.reset(v.id, t)
        ctx.lane_of[v.id] = (wp.road_id, wp.lane_id, 0.0)
        return True

    def remove(vi):
        crash.drop(vi.actor.id)
        ctx.goal_of.pop(vi.actor.id, None)
        vi.actor.destroy()
        violators.pop(vi.actor.id, None)

    def tier_of_vi(vi):
        return vi.tier if isinstance(vi, Speeder) else None

    run_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    now = world.get_snapshot().timestamp.elapsed_seconds
    crash = CrashLog(world, label)
    mon = Monitor(scene, run_dir, now, ctx, crash.last_crash, planted, label)
    # spread along the stretch, parked; violators first (a speeder needs the most room ahead), then slow cars
    # fitted around them (2026-10-14 run 5: placed after 18 slow cars, only 2 of 8 speeders found room)
    for kind, n in (("E1", args.speeders), ("B1", args.stoppers), ("B2", args.shoulder_stoppers)):
        for _ in range(n):
            spawn(kind, now, at_start=True)
    for _ in range(args.slow_cars):
        spawn("S", now, at_start=True)
    n_slow = sum(1 for vi in violators.values() if vi.kind == "S")
    for _ in range(30):  # settle on the ground (~0.7 s), then the sensors, all before anything moves
        world.wait_for_tick(10.0)
    vehicles = list(world.get_actors().filter("vehicle.*"))
    crash.ensure(vehicles)
    for _ in range(10):
        world.wait_for_tick(10.0)
    now = world.get_snapshot().timestamp.elapsed_seconds
    for vi in violators.values():
        vi.reset(now)
        launch(vi.actor, vi.wp0, planned[vi.actor.id])
    planned.clear()
    print(f"[viol] launched {n_slow} slow cars and {len(violators) - n_slow} violators: "
          + ", ".join(f"{vi.kind}{'' if tier_of_vi(vi) is None else f'@{tier_of_vi(vi)}'}" for vi in violators.values() if vi.kind != "S"))
    print(f"[viol] writing to {run_dir}")
    mon.t0 = now

    dbg_f = dbg_w = None
    if args.debug:
        dbg_f = open(run_dir / "debug.csv", "w", newline="")
        dbg_w = csv.DictWriter(dbg_f, fieldnames=[
            "t", "id", "kind", "tier", "state", "kmh", "desired", "target", "cap", "lead_d", "lead_kmh", "hard", "lc", "lc_s",
            "road", "lane", "aim", "wp_z", "z", "yaw_err", "shift", "off", "steer", "throttle", "brake", "bias"])
        dbg_w.writeheader()
    t0 = now
    last_sample = last_status = -1e9
    ticks, t_prev, max_gap = 0, None, 0.0  # max_gap: the longest the cars went without new controls
    waiting = 0
    try:
        while True:
            snap = world.wait_for_tick(10.0)
            t = snap.timestamp.elapsed_seconds
            ticks += 1
            if t_prev is not None:
                max_gap = max(max_gap, t - t_prev)
            t_prev = t
            for vi in list(violators.values()):  # every tick: 10 Hz is ~4 m per update at 144 km/h
                if vi.actor.is_alive:
                    vi.drive(t, vehicles)
            if t - last_sample < SAMPLE_S:
                continue
            last_sample = t
            vehicles = list(world.get_actors().filter("vehicle.*"))
            crash.ensure(vehicles)
            mon.sample(t, vehicles)
            if dbg_w is not None:
                for vi in violators.values():
                    if vi.dbg is not None:
                        dbg_w.writerow({"t": round(t - t0, 2), "id": vi.actor.id, "kind": vi.kind,
                                        "tier": tier_of_vi(vi) or "", "state": vi.state, **vi.dbg})
                        vi.dbg = None
            waiting, recycled = 0, False
            for vi in list(violators.values()):
                if not vi.actor.is_alive:
                    violators.pop(vi.actor.id, None)
                    ctx.goal_of.pop(vi.actor.id, None)
                    spawn(vi.kind, t, tier_of_vi(vi))
                    continue
                vi.step(t, scene, vehicles)
                loc = vi.actor.get_location()
                stuck = vi.slow_since is not None and t - vi.slow_since > STUCK_RESPAWN_S
                wp_now = vi.track(loc)  # mainline-held: passing an exit's gore is not straying
                on_stretch = wp_now is not None and on_main(wp_now, scene) and heading_ok(wp_now)
                vi.off_since = None if on_stretch else (vi.off_since or t)
                strayed = vi.off_since is not None and t - vi.off_since > OFF_STRETCH_S
                if vi.state == "cruise" and (stuck or strayed or dist(loc, centre) > args.radius):
                    # one per sample: a teleport only shows in the next tick's positions, and two cars recycled in
                    # the same sample were put on the same spot (2026-10-14 run 6: 3 overlaps, each a pile-up)
                    if not recycled and recycle(vi, t):
                        recycled = True
                        why = "stuck" if stuck else "strayed" if strayed else None
                        if why:
                            print(f"[viol] {vi.kind} car {vi.actor.id} {why} - recycled", flush=True)
                            ctx.stats[f"{vi.kind} {why}_recycled"] += 1
                    else:
                        waiting += 1
            if t - last_status >= 15.0:
                n_e1 = sum(1 for e in mon.events if e["rule"] == "E1" and e["planted"])
                fps = ticks / max(t - last_status, 1e-6) if last_status > 0 else 0.0
                last_status, ticks = t, 0
                states = ", ".join(f"{vi.kind}:{vi.state}" for vi in violators.values() if vi.kind in ("B1", "B2"))
                print(f"[viol] t+{t - t0:.0f}s  {len(vehicles)} vehicles, {n_e1} E1, {len(mon.events)} closed events, "
                      f"{len(crash.rows)} collisions, {waiting} waiting to recycle, loop {fps:.0f} Hz, longest gap {max_gap:.2f} s"
                      f" | {states}", flush=True)
                max_gap = 0.0
            if args.duration and t - t0 >= args.duration:
                break
    except KeyboardInterrupt:
        print("\n[viol] stopped")
    t1 = world.get_snapshot().timestamp.elapsed_seconds
    mon.finish()
    crash.close()
    if dbg_f is not None:
        dbg_f.close()
    write_report(run_dir, mon, planted, tier_of, passes, crash, ctx.stats, t0, t1, args)
    if not args.keep:
        for vi in list(violators.values()):
            remove(vi)
    print(f"[viol] report: {run_dir / 'report.md'}")


if __name__ == "__main__":
    main()
