"""Staged acts with CARLA walkers on a crosswalk (Build Plan M3: zebra conditions F2, F3, F5).

Used by stage_violations.py (plan(): planning; run_act(): walkers moved like the scripted cars,
physics off and the pose set every tick, so the walker is exactly where it was planned; the
truth is logged by record_flight.py in walker_poses.csv). Rules: ml/violation_engine/zebra_pedestrians.py.

Acts, one car + one walker each, at the crosswalk nearest the drone (the F1 act's):

    F3  failure to yield   the walker crosses at 1.4 m/s; the car drives through at 30 km/h when
                           the walker is 2 m short of the car's side                     (violation)
    F3  yields             same walker; the car stops 2 m before the crossing until the walker
                           is 3 m past its lane, then goes                               (negative)
    F2  blocking           the car stops 6 s on the crossing (under F1's 10 s) while the walker
                           waits at the kerb; the walker crosses after it has gone      (violation)
    F2  nobody waiting     same stop, the walker stands 12 m along the pavement          (negative)
    F5  obstructing        the car stops 9.5 s on the crossing; the walker, waiting on the crossing
                           3.5 m from its side, walks up to 0.6 m from it, waits (up to 4 s) and
                           is back before the car leaves
                                                                                         (violation)

Walking speed 1.4 m/s: typical free walking speed (MUTCD signal timing assumes 1.07-1.2 m/s for
slower walkers). Geometry: s across the road along the crossing (0 = the car's lane centre),
w along the road (0 = the crossing's middle).

Usage: imported by stage_violations.py (carlaAir conda env).
"""

import math

import numpy as np

WALK_MPS = 1.4
CAR_KMH = 30.0
WALKER_Z = 0.95  # a CARLA walker's location is its box centre, ~0.93 m above its feet
WALKER_BLUEPRINT = "walker.pedestrian.0001"
WAIT_OFF_M = 1.2  # waiting spot beyond the crossing's end (inside the rules' 2.5 m waiting area)
CAR_HALF_WIDTH = 0.95  # Tesla Model 3
FRONT_M = 2.3


class Crossing:
    """A CARLA crosswalk polygon seen from the lane through it (waypoint wp_c at its centre)."""

    def __init__(self, wp_c, poly):
        tf = wp_c.transform
        f, r = tf.get_forward_vector(), tf.get_right_vector()
        self.f, self.r = np.array([f.x, f.y]), np.array([r.x, r.y])
        loc = tf.location
        self.o = np.array([loc.x, loc.y])
        self.z = loc.z
        d = np.array(poly)[:, :2] - self.o
        s, w = d @ self.r, d @ self.f
        self.s0, self.s1, self.w0, self.w1 = float(s.min()), float(s.max()), float(w.min()), float(w.max())
        self.wm = (self.w0 + self.w1) / 2

    def point(self, s: float, w: float | None = None) -> np.ndarray:
        p = self.o + s * self.r + (self.wm if w is None else w) * self.f
        return np.array([p[0], p[1], self.z])


def _walker(Trajectory, cr: Crossing, legs: list) -> "Trajectory":
    """legs: ("stand", s, seconds[, w]) or ("walk", s_to[, w]); starts standing at the first leg's s."""
    first = legs[0]
    p0 = cr.point(first[1], first[3] if len(first) > 3 else None)
    tr = Trajectory().move([p0, p0 + np.array([1e-3 * cr.r[0], 1e-3 * cr.r[1], 0.0])], WALK_MPS)
    for leg in legs:
        w = leg[3] if leg[0] == "stand" and len(leg) > 3 else (leg[2] if leg[0] == "walk" and len(leg) > 2 else None)
        if leg[0] == "stand":
            if math.hypot(*(tr.p[-1][:2] - cr.point(leg[1], w)[:2])) > 1e-2:
                tr.move([cr.point(leg[1], w)], WALK_MPS)
            tr.hold(leg[2])
        else:
            tr.move([cr.point(leg[1], w)], WALK_MPS)
    return tr


def plan_pedestrian_acts(wp_c, poly, view, Trajectory, xyz) -> list[dict]:
    """The five acts above at the crosswalk (wp_c, poly) (stage_violations._crosswalk_near); acts whose
    walker would leave the camera view are dropped with a message."""
    cr = Crossing(wp_c, poly)
    if not (cr.s0 < 0 < cr.s1):
        print("[plan] pedestrian acts: the crossing does not span the car's lane - skipped")
        return []
    # walk from the nearer end, so the walker reaches the car's lane early
    side = -1 if -cr.s0 <= cr.s1 else 1
    kerb = (cr.s0 - WAIT_OFF_M) if side < 0 else (cr.s1 + WAIT_OFF_M)
    far = (cr.s1 + WAIT_OFF_M) if side < 0 else (cr.s0 - WAIT_OFF_M)
    near_side = side * CAR_HALF_WIDTH  # the car's side facing the walker (s)

    approach, w = [], wp_c
    for _ in range(30):
        prev = w.previous(1.0)
        if not prev:
            break
        w = prev[0]
        approach.append(w)
    approach = approach[::-1] + [wp_c]
    if len(approach) < 20:
        print("[plan] pedestrian acts: under 20 m of lane before the crossing - skipped")
        return []
    after = [wp_c] + [n[0] for n in [wp_c.next(k) for k in range(2, 26, 2)] if n]
    A = [xyz(x) for x in approach]
    B = [xyz(x) for x in after]
    v = CAR_KMH / 3.6
    approach_len = len(A) - 1  # 1 m steps

    def through(t_meet):
        """The car's centre passes wp_c at t_meet: holds at the start of its approach until then."""
        tr = Trajectory().move(A[:2], v)
        tr.hold(max(0.0, t_meet - approach_len / v - tr.duration))
        tr.move(A[2:-8], v).mark("start").move(A[-8:] + B[1:6], v).mark("end").move(B[6:], v)
        return tr

    def stop_on(hold_s, t_arrive=2.0):
        tr = Trajectory().move(A[:2], v).hold(max(0.0, t_arrive - approach_len / v))
        tr.move(A[2:], v).mark("start").hold(hold_s).mark("end").move(B[1:], v)
        return tr

    acts = []
    walk_to = lambda s_from, s_to: abs(s_to - s_from) / WALK_MPS  # noqa: E731

    # F3: walker sets off at t = 1 s; the car meets it 2 m short of the car's side
    s_meet = near_side + side * 2.0
    t_meet = 1.0 + walk_to(kerb, s_meet)
    t_meet = max(t_meet, approach_len / v + 1.0)
    t_go = t_meet - walk_to(kerb, s_meet)
    crossing = _walker(Trajectory, cr, [("stand", kerb, t_go), ("walk", far), ("stand", far, 2.0)])
    acts.append({"type": "zebra_crossing", "condition": "F3", "expected": True, "traj": through(t_meet),
                 "walkers": [crossing], "note": "drives through while a pedestrian crosses", "truth": ("start", "end")})

    # F3 negative: stops with its front 2 m before the crossing until the walker is 3 m past its lane
    d_stop = int(round(-cr.w0 + 2.0 + FRONT_M)) if cr.w0 < 0 else int(round(2.0 + FRONT_M))
    t_clear = t_go + walk_to(kerb, -near_side - side * 3.0)
    tr = Trajectory().move(A[:2], v).move(A[2:len(A) - 1 - d_stop], v).mark("start")
    tr.hold(max(0.5, t_clear - tr.duration)).move(A[len(A) - 1 - d_stop:] + B[1:6], v).mark("end").move(B[6:], v)
    acts.append({"type": "zebra_crossing", "condition": "F3", "expected": False, "traj": tr,
                 "walkers": [_walker(Trajectory, cr, [("stand", kerb, t_go), ("walk", far), ("stand", far, 2.0)])],
                 "note": "stops and lets the pedestrian cross", "truth": ("start", "end")})

    # F2: 6 s on the crossing while the walker waits at the kerb; it crosses once the car has gone
    car = stop_on(6.0)
    t_leave = car.marks["end"] + 3.0
    acts.append({"type": "zebra_crossing", "condition": "F2", "expected": True, "traj": car,
                 "walkers": [_walker(Trajectory, cr, [("stand", kerb, t_leave), ("walk", far)])],
                 "note": "6 s on the crossing while a pedestrian waits", "truth": ("start", "end")})
    acts.append({"type": "zebra_crossing", "condition": "F2", "expected": False, "traj": stop_on(6.0),
                 "walkers": [_walker(Trajectory, cr, [("stand", kerb, car.duration, cr.wm - 12.0)])],
                 "note": "6 s on the crossing, pedestrian 12 m along the pavement", "truth": ("start", "end")})

    # F5: 9.5 s on the crossing (under F1's 10 s); the walker sets off when the car stops, waits 0.6 m
    # from its side (up to 4 s), then is back at the kerb 0.5 s before the car leaves, so the car's
    # departure is not also a failure to yield
    # The walker starts already on the crossing, in the next lane 3.5 m from the car's side (beyond
    # zebra_pedestrians conflict_m 3 m, so the car arriving is not a failure to yield): from the kerb
    # the walk there and back took the whole 9.5 s (Town03 spot 1, 2026-10-10)
    car = stop_on(9.5)
    s_block = near_side + side * 0.6
    s_wait = near_side + side * 3.5
    if not (cr.s0 < s_wait < cr.s1):
        s_wait = kerb
    t0 = car.marks["start"] + 0.5
    wait = min(4.0, car.marks["end"] - 0.5 - t0 - 2 * walk_to(s_wait, s_block))
    if wait < 1.5:  # zebra_pedestrians obstruct_min_s 1.0 plus margin
        print(f"[plan] F5: only {wait:.1f} s at the car's side before it leaves - act skipped")
    ped = _walker(Trajectory, cr, [("stand", s_wait, t0), ("walk", s_block), ("stand", s_block, max(wait, 0.0)),
                                   ("walk", s_wait), ("stand", s_wait, 3.0)])
    if wait >= 1.5:
        acts.append({"type": "zebra_crossing", "condition": "F5", "expected": True, "traj": car, "walkers": [ped],
                     "note": "stopped in a crossing pedestrian's path", "truth": ("start", "end")})

    kept = []
    for a in acts:
        pts = np.array([w_tr.at(t)[0] for w_tr in a["walkers"] for t in np.arange(0.0, w_tr.duration, 0.5)])
        if view.contains(pts):
            kept.append(a)
        else:
            print(f"[plan] dropped {a['condition']} ({a['note']}): its pedestrian leaves the camera view")
    return kept


# --- running (CARLA) ------------------------------------------------------------------------------

def spawn_walkers(world, bp_lib, trajs: list) -> list:
    """Walkers at their trajectories' start, physics off (moved by set_pose); [] if one fails."""
    import carla
    bps = bp_lib.filter(WALKER_BLUEPRINT) or bp_lib.filter("walker.pedestrian.*")
    out = []
    for tr in trajs:
        bp = bps[0]
        if bp.has_attribute("is_invincible"):
            bp.set_attribute("is_invincible", "true")
        if bp.has_attribute("role_name"):
            bp.set_attribute("role_name", "violation_scenario")
        p, yaw = tr.at(0.0)
        w = world.try_spawn_actor(bp, carla.Transform(carla.Location(p[0], p[1], p[2] + WALKER_Z + 0.3), carla.Rotation(yaw=yaw)))
        if w is None:
            for x in out:
                x.destroy()
            return []
        w.set_simulate_physics(False)
        out.append(w)
    return out


def set_pose(walker, tr, t: float) -> None:
    import carla
    p, yaw = tr.at(min(t, tr.duration))
    walker.set_transform(carla.Transform(carla.Location(p[0], p[1], p[2] + WALKER_Z), carla.Rotation(yaw=yaw)))
