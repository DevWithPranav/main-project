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
  red_light        through a stop line at 30 km/h, red 1.5 s and 3 s before   + negative: red 1.5 s after
                   (optional, CARLA only; the light is switched by the script, all lights frozen meanwhile)

Zones it needs (no-parking, no-U-turn) are created around the chosen spots. The scenario log
(JSON) holds them plus every act's actor id and carla frames; pass it to the engine and the
evaluation:
    python ml/violation_engine/run_violations.py <flight> --scene .../Town05.json --zones <log>
    python ml/violation_engine/eval_violations.py <pipeline dir> <oracle dir> --scenario <log>

Acts are placed inside the camera's 16:9 ground footprint (View: altitude, heading, 90 deg FOV),
--margin m from its edge; an act whose scored part would leave it is dropped with a message.

Check the plan offline first (no simulator needed), then run it with CarlaAir up:
    python stage_violations.py --town Town05 --center -40 -137 --altitude 67.6 --yaw 0 --plan-only
    python stage_violations.py --out <flight folder>/scenario_log.json   # centre, height, heading from the drone

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
FRONT_M = 2.3  # Tesla Model 3 centre to front bumper (predicates.HALF_LENGTH_M["car"])
SPEED_IN_VIEW_S = 2.5  # rules.DEFAULTS speeding: min_track_age_s 1.0 + min_s 0.33, plus margin


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


class View:
    """The drone camera's ground footprint: nadir camera (record_flight.camera_mount, pitch -90, no
    yaw offset), so the image width runs across the drone's heading and the height along it.
    Half-extents: altitude * tan(hfov/2) across, times H/W along (67.6 m, 90 deg, 16:9: 67.6 x 38.0 m).
    margin_m keeps acts clear of the frame edge, where boxes are cut and their centres are wrong
    (flight 20261009_201727: the no-parking spot at the edge was missed; the stager used a circle)."""

    def __init__(self, center, yaw_deg: float, altitude_m: float, hfov_deg: float = 90.0,
                 width: int = 1920, height: int = 1080, margin_m: float = 8.0):
        self.c = np.asarray(center[:2], float)
        a = math.radians(yaw_deg)
        self.fwd, self.across = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
        self.half_across = altitude_m * math.tan(math.radians(hfov_deg) / 2)
        self.half_along = self.half_across * height / width
        self.margin = margin_m
        self.yaw, self.altitude = yaw_deg, altitude_m

    def contains(self, xy) -> bool:
        """Every point (one (x, y) or many) inside the footprint, margin_m from its edge."""
        d = np.atleast_2d(np.asarray(xy, float))[:, :2] - self.c
        return bool(np.all(np.abs(d @ self.fwd) <= self.half_along - self.margin)
                    and np.all(np.abs(d @ self.across) <= self.half_across - self.margin))

    def describe(self) -> dict:
        return {"center": self.c.round(1).tolist(), "yaw_deg": round(self.yaw, 1), "altitude_m": round(self.altitude, 1),
                "half_along_m": round(self.half_along, 1), "half_across_m": round(self.half_across, 1),
                "margin_m": self.margin}


def truth_points(a: dict) -> np.ndarray:
    """The planned positions of an act between its truth marks (the part that is scored)."""
    tr = a["traj"]
    t0, t1 = (tr.marks[k] for k in a["truth"])
    return np.array([tr.at(t)[0][:2] for t in np.arange(t0, t1 + 1e-9, 0.25)])


# --- planning ---------------------------------------------------------------------------------

def lane_props(town: str):
    """Nearest-lane lookup (x, y) -> lane dict of the exported lane map (export_lane_map.py): speed
    limit and the M1 road features (road_class, ramp, bridge, ...)."""
    path = SCENES_DIR / f"{town}.json"
    if not path.exists():
        raise SystemExit(f"{path} missing - run simulation/carla_scripts/export_lane_map.py {town} first")
    lanes = [l for l in json.loads(path.read_text())["lanes"] if l.get("lane_type", "driving") == "driving"]
    pts = np.vstack([np.array(l["centreline"]) for l in lanes])
    owner = np.concatenate([[i] * len(l["centreline"]) for i, l in enumerate(lanes)])
    return lambda x, y: lanes[int(owner[int(np.argmin((pts[:, 0] - x) ** 2 + (pts[:, 1] - y) ** 2))])]


def candidates(m, view: View, length, in_view_m: int = 40) -> list:
    """Straight, junction-free driving-lane runs of >= length m whose first in_view_m metres (the
    part most acts are scored on) lie inside the camera view. Acts that drive the whole run check
    the rest themselves."""
    out = []
    for wp in m.generate_waypoints(4.0):
        if wp.is_junction or wp.lane_type != carla.LaneType.Driving:
            continue
        run = lane_run(wp, length)
        if len(run) - 1 >= length and view.contains([xyz(w) for w in run[:in_view_m + 1]]):
            out.append(run)
    return out


def plan(m, town: str, view: View, seed: int, world=None) -> dict:
    rng = random.Random(seed)
    props = lane_props(town)
    limit = lambda x, y: float(props(x, y)["speed_limit_kmh"])  # noqa: E731
    urban = lambda w: props(*xyz(w)[:2]).get("road_class", "urban") == "urban" and not props(*xyz(w)[:2]).get("ramp")  # noqa: E731
    whole = lambda run: view.contains([xyz(w) for w in run])  # noqa: E731
    one_limit = lambda run: len({limit(*xyz(w)[:2]) for w in run[::5]}) == 1  # noqa: E731
    center, radius = view.c, float(np.hypot(view.half_along, view.half_across))
    runs = candidates(m, view, 60.0)
    if not runs:
        raise SystemExit("No straight 60 m lane inside the camera view - move the drone, fly higher or lower --margin.")
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

    # no-parking: zone 16 m long around a spot 40 m along an urban lane (on a highway-class road the
    # stop is also illegal stopping, B1); 45 s stop, then a 15 s negative
    r = take(lambda run: urban(run[40]))
    if r is None:
        print("[plan] no urban lane in view: no-parking act skipped (a stop on a highway lane is illegal stopping anyway)")
    else:
        spot = r[40]
        zones.append({"id": "np_staged", "type": "no_parking", "polygon": box(spot, 8.0, spot.lane_width / 2), "grace_s": 30})
        for hold, expected in ((45.0, True), (15.0, False)):
            tr = Trajectory().move([xyz(w) for w in r[:41]], 25 / 3.6).mark("start").hold(hold).mark("end")
            tr.move([xyz(w) for w in r[40:]], 25 / 3.6)
            acts.append({"type": "no_parking", "expected": expected, "traj": tr, "note": f"stop {hold:.0f} s",
                         "truth": ("start", "end")})

    # wrong-way: a full lane run driven backwards (car facing its motion)
    r = take(whole) or take()
    tr = Trajectory().mark("start").move([xyz(w) for w in r[::-1]], 30 / 3.6).mark("end")
    acts.append({"type": "wrong_way", "expected": True, "traj": tr, "note": "30 km/h against the lane",
                 "truth": ("start", "end")})

    # speeding: 1.6x, 1.3x the limit (violations) and the limit + half the enforcement tolerance (EU:
    # 5 km/h below 100, 5% above; not a violation). Each in view for >= SPEED_IN_VIEW_S (the engine
    # needs a track 1 s old plus 0.33 s over the limit), on one speed limit all along, or the
    # negative turns into a violation where the limit drops
    def speed_run(lim_kmh):
        """A run on one limit lim_kmh, in view long enough for 1.6x that limit (a road not used by
        another act if there is one, as take() does)."""
        need = int(math.ceil(1.6 * lim_kmh / 3.6 * SPEED_IN_VIEW_S))
        ok = [run[:need + 1] for run in candidates(m, view, need, in_view_m=need)
              if one_limit(run[:need + 1]) and limit(*xyz(run[0])[:2]) == lim_kmh]
        best = next((run for run in ok if run[0].road_id not in used_roads), ok[0] if ok else None)
        if best is not None:
            used_roads.add(best[0].road_id)
        return best
    r = None
    for lim_try in sorted({limit(*xyz(run[0])[:2]) for run in runs}):  # lower limits need shorter runs
        r = speed_run(lim_try)
        if r is not None:
            break
    if r is None:
        print(f"[plan] no lane in view long enough for {SPEED_IN_VIEW_S:.1f} s at 1.6 x its limit: speeding acts skipped")
    else:
        lim = limit(*xyz(r[len(r) // 2])[:2])
        tol = 5.0 if lim < 100 else 0.05 * lim
        for kmh, expected, note in ((1.6 * lim, True, f"1.6 x {lim:.0f} km/h"), (1.3 * lim, True, f"1.3 x {lim:.0f} km/h"),
                                    (lim + tol / 2, False, f"{lim:.0f} + {tol / 2:.1f} km/h (inside the tolerance)")):
            tr = Trajectory().mark("start").move([xyz(w) for w in r], kmh / 3.6).mark("end")
            acts.append({"type": "speeding", "expected": expected, "traj": tr, "note": note, "truth": ("start", "end")})

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
    r = take(lambda run: has_neighbour(run) and whole(run)) or take(has_neighbour)
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

    # red light (optional, CARLA only): through a stop line at 30 km/h; the light turns red 1.5 s /
    # 3 s before the front reaches the line (violations, R22's typical range), or 1.5 s after the car
    # has passed it (negative: already past the line at the change, PRD 4 edge case 1)
    appr = _signal_approach(m, world, center, radius)
    if appr is not None:
        sid, stop, back, ahead = appr
        v = 30 / 3.6
        for lead_s, expected in ((1.5, True), (3.0, True), (-1.5, False)):
            tr = Trajectory().move([xyz(w) for w in back] + [xyz(stop)], v).mark("start")
            t_front = tr.duration - FRONT_M / v  # front bumper on the line
            tr.move([xyz(w) for w in ahead], v).mark("end")
            note = f"red {lead_s:.1f} s before the line" if lead_s > 0 else f"red {-lead_s:.1f} s after passing"
            acts.append({"type": "red_light", "expected": expected, "traj": tr, "note": note,
                         "truth": ("start", "end"), "signal": {"id": sid, "red_at_s": round(t_front - lead_s, 2)},
                         "stop_line": _stop_line(sid, stop)})

    # the crosswalk / signal searches look within the footprint's circle: keep only acts whose scored
    # part stays inside the 16:9 view
    kept = []
    for a in acts:
        if view.contains(truth_points(a)):
            kept.append(a)
        else:
            print(f"[plan] dropped {a['type']} ({a['note']}): it leaves the camera view (margin {view.margin:.0f} m)")
    return {"acts": kept, "zones": zones}


def _signal_approach(m, world, center, radius):
    """(signal id, stop waypoint, 40 m straight approach, 25 m on through the junction) for the
    traffic-light-controlled lane nearest the centre. Live: CARLA's own stop waypoints (the ones
    record_flight.py logs); --plan-only: the OpenDRIVE signal's position on each lane it controls."""
    best = None
    for lm in m.get_all_landmarks_of_type("1000001"):
        if world is not None:
            tl = world.get_traffic_light(lm)
            wps = tl.get_stop_waypoints() if tl is not None else []
        else:
            # CARLA puts the signal on the junction's internal road; the stop line is where that
            # lane enters the junction (as get_stop_waypoints() gives it live), so walk back out
            wps = []
            for a, b in lm.get_lane_validities():
                for lane in range(a, b + 1):
                    wp = m.get_waypoint_xodr(lm.road_id, lane, lm.s) if lane != 0 else None
                    for _ in range(60):
                        if wp is None or not wp.is_junction:
                            break
                        prev = wp.previous(0.5)
                        wp = prev[0] if prev else None
                    if wp is not None and not wp.is_junction and wp.lane_type == carla.LaneType.Driving:
                        wps.append(wp)
        for wp in wps:
            c = xyz(wp)
            d = math.hypot(c[0] - center[0], c[1] - center[1])
            if d > radius or (best is not None and d >= best[0]):
                continue
            back, w = [], wp
            for _ in range(40):
                prev = [p for p in w.previous(1.0) if not p.is_junction]
                if not prev:
                    break
                w = prev[0]
                back.append(w)
            if len(back) < 40:
                continue
            ahead, w = [], wp
            for _ in range(25):  # the straightest successor, through the junction
                nxt = w.next(1.0)
                if not nxt:
                    break
                yaw = wp.transform.rotation.yaw
                w = min(nxt, key=lambda n: abs((n.transform.rotation.yaw - yaw + 180) % 360 - 180))
                ahead.append(w)
            best = (d, (lm.id, wp, back[::-1], ahead))
    return None if best is None else best[1]


def _stop_line(sid: str, wp) -> dict:
    """Stop line across the lane at a stop waypoint, as record_flight.py writes it (for --plan-only dry runs)."""
    c, r, f = wp.transform.location, wp.transform.get_right_vector(), wp.transform.get_forward_vector()
    h = wp.lane_width / 2
    return {"id": f"tl_{sid}_plan", "signal_id": sid, "dir": [round(f.x, 4), round(f.y, 4)],
            "line": [[round(c.x - h * r.x, 2), round(c.y - h * r.y, 2)], [round(c.x + h * r.x, 2), round(c.y + h * r.y, 2)]]}


def _traffic_light(world, sid: str):
    return next((tl for tl in world.get_actors().filter("traffic.traffic_light") if tl.get_opendrive_id() == sid), None)


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
    light = None
    if "signal" in act:  # green (frozen) until red_at_s, then red; freeze() holds every light in the town
        light = _traffic_light(world, act["signal"]["id"])
        if light is None:
            for c in cars.values():
                c.destroy()
            return {"error": f"traffic light {act['signal']['id']} not found"}
        light.set_state(carla.TrafficLightState.Green)
        light.freeze(True)
    snap = world.wait_for_tick()
    t0 = snap.timestamp.elapsed_seconds
    frames = {}
    duration = max(tr.duration for _, tr in trajs)
    try:
        while True:
            snap = world.wait_for_tick()
            t = snap.timestamp.elapsed_seconds - t0
            for name, tr in trajs:
                p, yaw = tr.at(min(t, tr.duration))
                cars[name].set_transform(carla.Transform(carla.Location(p[0], p[1], p[2] + 0.02), carla.Rotation(yaw=yaw)))
            for m_name, m_t in act["traj"].marks.items():
                if m_name not in frames and t >= m_t:
                    frames[m_name] = (snap.frame, round(snap.timestamp.elapsed_seconds, 3))
            if light is not None and "red" not in frames and t >= act["signal"]["red_at_s"]:
                light.set_state(carla.TrafficLightState.Red)
                frames["red"] = (snap.frame, round(snap.timestamp.elapsed_seconds, 3))
            if t >= duration:
                break
    finally:
        if light is not None:
            light.freeze(False)
    out = {"actor_id": cars["main"].id, "frames": frames}
    if light is not None:
        out["signal"] = {**act["signal"], "red_frame": frames.get("red", (None,))[0]}
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
    ap.add_argument("--altitude", type=float, default=None,
                    help="Camera height above the road (default: the drone's; --plan-only: 67.6, flight 20261009_201727)")
    ap.add_argument("--yaw", type=float, default=None, help="Drone heading in degrees (default: the drone's; --plan-only: 0)")
    ap.add_argument("--margin", type=float, default=8.0, help="Keep acts this many m inside the camera footprint's edge")
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
        altitude = args.altitude if args.altitude is not None else 67.6
        yaw = args.yaw if args.yaw is not None else 0.0
    else:
        client = carla.Client(args.host, args.port)
        client.set_timeout(20.0)
        world = client.get_world()
        m = world.get_map()
        town = args.town or m.name.split("/")[-1]
        drone = next(iter(world.get_actors().filter("airsim.*")), None)
        if drone is None and (args.center is None or args.altitude is None or args.yaw is None):
            raise SystemExit("No drone found - give --center X Y, --altitude and --yaw")
        tf = drone.get_transform() if drone is not None else None
        center = args.center if args.center is not None else (tf.location.x, tf.location.y)
        if args.altitude is not None:
            altitude = args.altitude
        else:  # height above the road under the drone
            road = m.get_waypoint(tf.location, project_to_road=True)
            altitude = tf.location.z - (road.transform.location.z if road is not None else 0.0)
        yaw = args.yaw if args.yaw is not None else tf.rotation.yaw

    view = View(center, yaw, altitude, margin_m=args.margin)
    print(f"[view] {view.describe()}")
    p = plan(m, town, view, args.seed, world)
    acts = [a for a in p["acts"] if not args.only or a["type"] in args.only]
    zones = list(p["zones"])
    print(f"[plan] {town} centre ({center[0]:.0f}, {center[1]:.0f}): {len(acts)} acts, {len(zones)} zones")
    for a in acts:
        print(f"   {a['type']:15s} {'violation' if a['expected'] else 'negative ':9s} {a['traj'].duration:5.1f} s  {a['note']}")

    out = args.out or Path(__file__).parent / "runs" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S") / "scenario_log.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    log = {"town": town, "center": list(center), "view": view.describe(),
           "created": datetime.datetime.now().isoformat(timespec="seconds"),
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
            if "signal" in a:  # the dry run's signal schedule and stop line (live runs: record_flight.py logs both)
                entry["signal"] = a["signal"]
                if all(sl["id"] != a["stop_line"]["id"] for sl in log.setdefault("stop_lines", [])):
                    log["stop_lines"].append(a["stop_line"])
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
