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
  road features    Build Plan M2, one act per condition that fits the view (plan_road_feature_acts):
                   A1 wrong lane through a junction, A3 across a mixed line's solid side, A5 car in a bus
                   lane, A6 on the shoulder, A8 cut-in (TTC 0.9 s), B1 / B4 / B5 stop on a highway / ramp /
                   bridge lane, C2 / C4 / C5 wrong way in from a junction / on a ramp / one-way, D2 U-turn
                   across a solid centre line, D3 at a no-U-turn junction, D4 through a median, E4 truck
                   over its class limit, each with a negative where one means something
  red_light        through a stop line at 30 km/h, red 1.5 s and 3 s before   + negative: red 1.5 s after
                   (optional, CARLA only; the light is switched by the script, all lights frozen meanwhile)

Zones it needs (no-parking, no-U-turn) are created around the chosen spots. The scenario log
(JSON) holds them plus every act's actor id and carla frames; pass it to the engine and the
evaluation:
    python ml/violation_engine/run_violations.py <flight> --scene .../Town05.json --zones <log>
    python ml/violation_engine/eval_violations.py <pipeline dir> <oracle dir> --scenario <log>

Acts are placed inside the camera's 16:9 ground footprint (View: altitude, heading, 90 deg FOV),
--margin m from its edge; an act whose scored part would leave it is dropped with a message.

Find drone spots that cover every condition, check a plan offline (no simulator needed), then
run it with CarlaAir up:
    python stage_violations.py --suggest --town Town04
    python stage_violations.py --town Town05 --center -40 -137 --altitude 67.6 --yaw 0 --plan-only --out plan/scenario_log.json
    python ml/violation_engine/run_violations.py --plan plan/scenario_log.json --scene ml/violation_engine/configs/scenes/Town05.json --profile town05
    python stage_violations.py --out <flight folder>/scenario_log.json   # centre, height, heading from the drone
Red-light acts only with --red-light (out of scope since 2026-10-09).

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
        elif np.hypot(*(pts[0] - self.p[-1])[:2]) > 1e-6:
            pts = [self.p[-1]] + pts  # drive from where the car is, never jump to the next piece's start
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
    cell = 10.0  # grid of centreline points: a query looks at its own and the 8 neighbouring cells
    grid: dict[tuple, list] = {}
    for k, (x, y) in enumerate(pts):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(k)

    def lookup(x, y):
        gx, gy = int(x // cell), int(y // cell)
        near = [k for dx in (-1, 0, 1) for dy in (-1, 0, 1) for k in grid.get((gx + dx, gy + dy), ())]
        idx = np.array(near) if near else np.arange(len(pts))
        return lanes[int(owner[idx[int(np.argmin((pts[idx, 0] - x) ** 2 + (pts[idx, 1] - y) ** 2))]])]
    return lookup


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


def plan(m, town: str, view: View, seed: int, world=None, red_light: bool = False) -> dict:
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
    # (the legal negative is now D2's: outside a zone a U-turn is legal only across a broken line)
    for in_zone in (True,):
        r = take(two_way)
        if r is None:
            break
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

    # highway stops (B1 / B4 / B5) and the other road-feature conditions: plan_road_feature_acts
    m2_acts, extras = plan_road_feature_acts(m, view, runs, take, props, limit, used_roads, zones)
    acts += m2_acts

    # red light (optional, CARLA only): through a stop line at 30 km/h; the light turns red 1.5 s /
    # 3 s before the front reaches the line (violations, R22's typical range), or 1.5 s after the car
    # has passed it (negative: already past the line at the change, PRD 4 edge case 1)
    appr = _signal_approach(m, world, center, radius) if red_light else None  # out of scope: only on request
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

    # A5 last: the bus-lane override holds for the whole session, so the lane must be one no other
    # act drives in (else their cars raise A5 too). Background traffic in it is natural A5.
    lane_of = lambda x, y: props(x, y)["id"].split("_p")[0]  # noqa: E731
    touched = {lane_of(*pt[:2]) for a in kept for tr in (a["traj"], a.get("lead")) if tr is not None for pt in tr.p[::5]}
    bus = next((run for run in runs if lane_of(*xyz(run[20])[:2]) not in touched
                and len({lane_of(*xyz(w)[:2]) for w in run[5:41:5]}) == 1), None)
    if bus is None:
        print("[plan] A5: no lane in view that no other act uses - act skipped")
    else:
        extras["lane_overrides"].append({"lane_id": props(*xyz(bus[20])[:2])["id"].split("_p")[0] + "*", "restricted": "bus"})
        for cls, expected in (("car", True), ("bus", False)):
            kept.append({"type": "lane_violation", "condition": "A5", "expected": expected, "cls": cls,
                         "traj": Trajectory().move([xyz(w) for w in bus[:6]], 30 / 3.6).mark("start")
                         .move([xyz(w) for w in bus[5:41]], 30 / 3.6).mark("end"),
                         "note": f"a {cls} in a bus lane", "truth": ("start", "end")})
    return {"acts": kept, "zones": zones, **{k: v for k, v in extras.items() if v}}


# --- Build Plan M2: one act (+ a negative where it means something) per road-feature condition ------
#
# Each act names the condition it should be detected as (Expected_Output 4.2), so a dry run
# (run_violations.py --plan) and the scorer check the condition, not only the type. Acts that need
# a rule setting carry it in the log: lane_overrides (a bus lane, A5) and engine_params (the junction
# where a U-turn is prohibited, D3; a truck speed limit, E4). Lane-change acts follow a cut-in as
# in NCAP-style tests (lane change over ~1.2 s; unsafe: TTC ~0.9 s; safe: same speed, 20 m gap).

CUTIN_TTC_S = 0.9
CHANGER_KMH, LAG_KMH = 30.0, 50.0


def _mark(m) -> str:
    return str(m.type).split(".")[-1].lower() if m is not None else "none"


def _offset_path(run, k0: int, k1: int, off0: float, off1: float) -> list:
    """Points along run, offset sideways (+ = right) from off0 to off1 between run[k0] and run[k1] (smooth)."""
    pts = []
    for k, w in enumerate(run):
        a = min(max((k - k0) / max(k1 - k0, 1), 0.0), 1.0)
        a = 3 * a * a - 2 * a ** 3
        pts.append(lateral(w, off0 + (off1 - off0) * a))
    return pts


def _neighbour(w, side: str):
    nb = w.get_right_lane() if side == "right" else w.get_left_lane()
    if nb is not None and nb.lane_type == carla.LaneType.Driving and nb.lane_id * w.lane_id > 0:
        return nb
    return None


def _exit_of(wp, max_steps: int = 80):
    """Follow a junction connector to the first non-junction waypoint: (exit waypoint, path points)."""
    pts, w = [], wp
    for _ in range(max_steps):
        pts.append(xyz(w))
        if not w.is_junction:
            return w, pts
        nxt = w.next(1.0)
        if not nxt:
            return None, pts
        w = nxt[0]
    return None, pts


def _lane_glob(w) -> str:
    return f"r{w.road_id}_s{w.section_id}_l{w.lane_id}*"


def plan_road_feature_acts(m, view: View, runs: list, take, props, limit, used_roads: set,
                           zones: list) -> tuple[list, dict]:
    """Acts for A1, A3, A5, A6, A8, B1, B4, B5, C2, C4, C5, D2, D3, D4, E4 that fit this view.
    Returns (acts, log extras: lane_overrides / engine_params)."""
    acts, extras = [], {"lane_overrides": [], "engine_params": {}}
    p = lambda w: props(*xyz(w)[:2])  # noqa: E731
    inview = lambda pts: view.contains(pts)  # noqa: E731
    skip = lambda cond, why: print(f"[plan] {cond}: {why} - act skipped")  # noqa: E731
    v_ch, v_lag = CHANGER_KMH / 3.6, LAG_KMH / 3.6

    def act(cond, vtype, expected, tr, note, **kw):
        acts.append({"type": vtype, "condition": cond, "expected": expected, "traj": tr, "note": note,
                     "truth": ("start", "end"), **kw})

    # A3: a lane change across a mixed line from its solid side (not permitted, but not a solid line:
    # A2 is for solid / double solid); negative: the same line crossed from its broken side
    def mixed(run):
        w = run[20]
        for side in ("right", "left"):
            nb = _neighbour(w, side)
            mark = _mark(w.right_lane_marking if side == "right" else w.left_lane_marking)
            ok = w.lane_change in ((carla.LaneChange.Right if side == "right" else carla.LaneChange.Left), carla.LaneChange.Both)
            if nb is not None and mark in ("solidbroken", "brokensolid") and not ok:
                return side
        return None
    r = take(lambda run: mixed(run) is not None)
    side = mixed(r) if r is not None else None
    if side is None:
        skip("A3", "no lane in view with a mixed line it may not cross")
    else:
        sgn = 1.0 if side == "right" else -1.0
        width = r[20].lane_width
        go = _offset_path(r[:46], 15, 30, 0.0, sgn * width)
        act("A3", "lane_violation", True, Trajectory().move(go[:16], v_ch).mark("start").move(go[15:], v_ch).mark("end"),
            f"lane change to the {side} across its solid side")
        back = _offset_path(r[:46], 15, 30, sgn * width, 0.0)
        act("A3", "lane_violation", False, Trajectory().move(back[:16], v_ch).mark("start").move(back[15:], v_ch).mark("end"),
            "the same line crossed from its broken side (permitted)")

    # A2: a lane change across a solid line to a same-direction lane, else an overtake over a solid
    # centre line (out into the opposing lane and back)
    def solid_side(run):
        w = run[20]
        for side in ("right", "left"):
            mark = _mark(w.right_lane_marking if side == "right" else w.left_lane_marking)
            if mark in ("solid", "solidsolid") and _neighbour(w, side) is not None:
                return side
        return None
    r = take(lambda run: solid_side(run) is not None)
    if r is not None:
        side = solid_side(r)
        sgn = 1.0 if side == "right" else -1.0
        go = _offset_path(r[:46], 15, 30, 0.0, sgn * r[20].lane_width)
        act("A2", "lane_violation", True, Trajectory().move(go[:16], v_ch).mark("start").move(go[15:], v_ch).mark("end"),
            f"lane change to the {side} across a solid line")
    else:
        r = take(lambda run: _mark(run[20].left_lane_marking) in ("solid", "solidsolid") and run[20].get_left_lane() is not None
                 and run[20].get_left_lane().lane_type == carla.LaneType.Driving)
        if r is None:
            skip("A2", "no solid lane line in view")
        else:
            w = -r[20].lane_width  # into the opposing lane on the left, then back
            path = [lateral(x, w * min(max((k - 10) / 8, 0), 1) * min(max((36 - k) / 8, 0), 1)) for k, x in enumerate(r[:46])]
            # out in the oncoming lane it is driving against it: a wrong-way event too, by rule
            act("A2", "lane_violation", True,
                Trajectory().move(path[:11], v_ch).mark("start").move(path[10:], v_ch).mark("end"),
                "overtake over the solid centre line and back", also=["wrong_way"])

    # A8: cut-in in front of a faster car (TTC ~0.9 s at the crossing); negative: 20 m gap, same speed
    r = take(lambda run: len(run) > 60 and _neighbour(run[5], "right") is not None
             and _mark(run[5].right_lane_marking) in ("broken", "brokenbroken")
             and inview([lateral(w, w.lane_width) for w in run[:61]] + [xyz(w) for w in run[:61]]))
    if r is None:
        skip("A8", "no two same-direction lanes with a broken line in view")
    else:
        width = r[5].lane_width
        ch = _offset_path(r[:61], 40, 50, 0.0, width)  # crosses the line at s = 45
        t_c = 15.0 / v_ch  # from s = 30 (start) to the crossing
        lane_pts = [lateral(w, width) for w in r[:61]]
        for expected, lag_v, gap, note in ((True, v_lag, CUTIN_TTC_S * (v_lag - v_ch), f"cut-in, TTC {CUTIN_TTC_S} s to a 50 km/h car"),
                                           (False, v_ch, 20.0, "lane change with a 20 m gap, same speed")):
            s_lag = 45.0 - 2 * FRONT_M - gap - lag_v * t_c  # lag start, so its bumper is `gap` behind at the crossing
            if s_lag < 0:
                continue
            k0 = int(round(s_lag))
            k_slow = min(int(45.0 - 2 * FRONT_M - gap + lag_v * 0.5), 60)  # brakes to the changer's speed 0.5 s after
            lag = Trajectory().move(lane_pts[k0:k_slow + 1], lag_v).move(lane_pts[k_slow:], v_ch)
            tr = Trajectory().move(ch[30:41], v_ch).mark("start").move(ch[40:56], v_ch).mark("end").move(ch[55:], v_ch)
            act("A8", "lane_violation", expected, tr, note, lead=lag)

    # A5 (a bus lane) is planned last, in plan(): the override holds for the whole session, so its
    # lane must be one no other act drives in

    # A6: driving along a full-width shoulder (25 km/h: below any limit here, so not speeding too);
    # negative: pulling over slowly and stopping briefly
    sh = None
    for run in runs:
        nb = run[10].get_right_lane()
        if nb is not None and nb.lane_type == carla.LaneType.Shoulder and nb.lane_width >= 2.5:
            pts = [lateral(w, (w.lane_width + nb.lane_width) / 2) for w in run[:41]]
            if inview(pts):
                sh = pts
                break
    if sh is None:
        skip("A6", "no shoulder >= 2.5 m wide in view")
    else:
        act("A6", "lane_violation", True, Trajectory().mark("start").move(sh, 25 / 3.6).mark("end"), "25 km/h on the shoulder")
        act("A6", "lane_violation", False, Trajectory().mark("start").move(sh[:12], 6 / 3.6).hold(5.0).mark("end"),
            "pulling over at 6 km/h, 5 s stop")

    # A1: through a junction from the wrong lane: the neighbour lane's connector to an exit this lane
    # does not lead to; negative: the same path from the neighbour lane (legal)
    found = None
    for run in runs:
        end = run[-1]
        if not end.next(3.0) or not end.next(3.0)[0].is_junction:
            continue
        own_exits = {(e.road_id, e.lane_id) for e, _ in (_exit_of(c) for c in end.next(3.0)) if e is not None}
        for side in ("left", "right"):
            nb = _neighbour(end, side)
            if nb is None:
                continue
            for c in nb.next(3.0):
                e, conn = _exit_of(c)
                if e is None or (e.road_id, e.lane_id) in own_exits:
                    continue
                out = lane_run(e, 20.0)
                approach = [xyz(w) for w in run[-31:]]
                nb_approach = [lateral(w, (1 if side == "right" else -1) * w.lane_width) for w in run[-31:]]
                if inview(approach + conn + [xyz(w) for w in out]):
                    found = (approach, nb_approach, conn, [xyz(w) for w in out])
                    break
            if found:
                break
        if found:
            break
    if found is None:
        skip("A1", "no junction in view where a lane does not lead where its neighbour does")
    else:
        approach, nb_approach, conn, out = found
        for start, expected, note in ((approach, True, "turns from the lane that doesn't lead there"),
                                      (nb_approach, False, "the same turn from the right lane")):
            act("A1", "lane_violation", expected,
                Trajectory().move(start, 20 / 3.6).mark("start").move(conn[2:] + out, 20 / 3.6).mark("end"), note)

    # B1 / B4 / B5: stops where the road itself forbids it (highway, ramp, bridge); negative: a 10 s stop
    def stop_on(cond, pred, what):
        cand = None
        for w in m.generate_waypoints(3.0):
            if w.lane_type != carla.LaneType.Driving or w.is_junction or not pred(p(w)):
                continue
            run = lane_run(w, 25.0)
            if len(run) > 20 and inview([xyz(x) for x in run]) and all(pred(p(x)) for x in run[10:21:5]):
                cand = run
                break
        if cand is None:
            skip(cond, f"no {what} lane in view")
            return
        for hold, expected in ((30.0, True), (10.0, False)):
            tr = Trajectory().move([xyz(w) for w in cand[:16]], 30 / 3.6).mark("start").hold(hold).mark("end")
            tr.move([xyz(w) for w in cand[15:]], 30 / 3.6)
            act(cond, "highway_stop", expected, tr, f"{hold:.0f} s stop on a {what} lane")
    stop_on("B1", lambda q: q.get("road_class") == "highway" and not q.get("ramp") and not q.get("bridge"), "highway")
    stop_on("B4", lambda q: q.get("ramp") in ("on", "off"), "ramp")
    stop_on("B5", lambda q: bool(q.get("bridge")), "bridge")

    # C2 / C4 / C5: wrong way by kind (C1 / C3 come from the general wrong-way act)
    def from_junction(run):  # the lane's end (seen against it: its start) touches a junction in view
        nxt = run[-1].next(8.0)
        return bool(nxt) and nxt[0].is_junction and inview([xyz(nxt[0])])
    r = take(from_junction)
    if r is None:
        skip("C2", "no lane in view that starts at a junction")
    else:
        into = [xyz(r[-1].next(8.0)[0])] + [xyz(w) for w in r[::-1][:41]]
        act("C2", "wrong_way", True, Trajectory().mark("start").move(into, 30 / 3.6).mark("end"),
            "out of the junction into a lane against its direction")
    for cond, pred, what in (("C4", lambda q: q.get("ramp") in ("on", "off"), "ramp"),
                             ("C5", lambda q: q.get("one_way") and q.get("road_class") == "urban" and not q.get("ramp"), "one-way")):
        cand = None
        for w in m.generate_waypoints(3.0):
            if w.lane_type == carla.LaneType.Driving and not w.is_junction and pred(p(w)):
                run = lane_run(w, 30.0)
                # all of it on that kind of lane (a ramp run ending on the highway reads as C3 there)
                if len(run) > 25 and inview([xyz(x) for x in run]) and all(pred(p(x)) for x in run[::3]):
                    cand = run
                    break
        if cand is None:
            skip(cond, f"no {what} lane in view")
        else:
            act(cond, "wrong_way", True, Trajectory().mark("start").move([xyz(w) for w in cand[::-1]], 30 / 3.6).mark("end"),
                f"against a {what} lane")

    # D2 / D4 and the legal negative: U-turns outside any zone, judged by what they cross
    def u_turn(run, q_wp):
        a, b = xyz(run[25]), xyz(q_wp)
        c, rad = (a + b) / 2, float(np.hypot(*(b - a)[:2])) / 2
        start_ang = math.atan2(a[1] - c[1], a[0] - c[0])
        fwd = run[25].transform.get_forward_vector()
        sweep = math.pi if (math.cos(start_ang + math.pi / 2) * fwd.x + math.sin(start_ang + math.pi / 2) * fwd.y) > 0 else -math.pi
        tr = Trajectory().move([xyz(w) for w in run[:26]], 15 / 3.6).mark("start")
        tr.move(arc(c, max(rad, 1.5), start_ang, start_ang + sweep, a[2]), 10 / 3.6).mark("end")
        tr.move([xyz(w) for w in lane_run(q_wp, 25.0)], 15 / 3.6)
        return tr
    def opposite(run):
        left = run[25].get_left_lane()
        return left if left is not None and left.lane_type == carla.LaneType.Driving and left.lane_id * run[25].lane_id < 0 else None
    def clear_of_zones(run):  # well away from any no-U-turn zone (that U-turn would be D1)
        c = xyz(run[25])[:2]
        return all(np.min(np.hypot(*(np.array(z["polygon"]) - c).T)) > 30.0 for z in zones if z["type"] == "no_u_turn")
    # a U-turn needs ~26 m of straight lane, not the 60 m runs: solid centre lines sit on the short
    # blocks between junctions (Town05: 572 solid-centre waypoints, none on a 60 m run in view)
    short_runs = candidates(m, view, 30.0, in_view_m=30)
    for cond, centre, expected, note in (("D2", ("solid", "solidsolid"), True, "U-turn across the solid centre line"),
                                         ("D2", ("broken", "brokenbroken"), False, "U-turn across a broken centre line (legal)")):
        r = next((run for run in short_runs if run[0].road_id not in used_roads and opposite(run) is not None
                  and _mark(run[25].left_lane_marking) in centre and not p(run[25]).get("median_left")
                  and clear_of_zones(run)), None)
        if r is not None:
            used_roads.add(r[0].road_id)
        if r is None:
            skip(cond, f"no two-way road with a {centre[0]} centre line in view")
        else:
            act(cond, "illegal_u_turn", expected, u_turn(r, opposite(r)), note)
    med = None
    for w in m.generate_waypoints(3.0):
        q = p(w)
        if w.lane_type != carla.LaneType.Driving or w.is_junction or not q.get("median_left"):
            continue
        run = lane_run(w, 30.0)
        if len(run) < 30:
            continue
        gap = float(q.get("median_gap_m") or 2.0)
        far = lateral(run[25], -(run[25].lane_width + gap))  # across the median (left = negative)
        wq = m.get_waypoint(carla.Location(*far), project_to_road=True, lane_type=carla.LaneType.Driving)
        if wq is None or abs((wq.transform.rotation.yaw - run[25].transform.rotation.yaw + 180) % 360 - 180) < 150:
            continue
        if inview([xyz(x) for x in run[:26]] + [xyz(wq)]):
            med = (run, wq)
            break
    if med is None:
        skip("D4", "no divided road in view")
    else:
        act("D4", "illegal_u_turn", True, u_turn(*med), "U-turn through the median")

    # D3: a U-turn inside a junction the log lists as no-U-turn; negative: at a junction not listed
    jturns = []  # (junction id, its connector road ids, path to the end of the turn, exit path)
    for run in runs:
        end = run[-1]
        q = end.get_left_lane()
        nxt = end.next(3.0)
        if (not nxt or not nxt[0].is_junction or q is None or q.lane_type != carla.LaneType.Driving
                or q.lane_id * end.lane_id > 0):
            continue
        jn = nxt[0].get_junction()
        a, b = xyz(end), xyz(q)
        fwd = end.transform.get_forward_vector()
        c = (a + b) / 2 + 6.0 * np.array([fwd.x, fwd.y, 0.0])  # the turn bulges 6 m into the junction
        rad = float(np.hypot(*(b - a)[:2])) / 2
        start_ang = math.atan2(a[1] - c[1], a[0] - c[0])
        sweep = math.pi if (math.cos(start_ang + math.pi / 2) * fwd.x + math.sin(start_ang + math.pi / 2) * fwd.y) > 0 else -math.pi
        into = [a + (c - (a + b) / 2) * k / 6 for k in range(1, 7)]
        loop = arc(c, max(rad, 1.5), start_ang, start_ang + sweep, a[2])
        back = [b + (c - (a + b) / 2) * k / 6 for k in range(6, 0, -1)]
        out = [xyz(w) for w in lane_run(q, 25.0)]
        path = [xyz(w) for w in run[-26:]] + into + loop + back
        if jn is not None and inview(path + out) and all(j[0] != jn.id for j in jturns):
            roads = sorted({wa.road_id for wa, wb in jn.get_waypoints(carla.LaneType.Driving)})
            jturns.append((jn.id, roads, path, out))
    if not jturns:
        skip("D3", "no junction in view to turn at")
    else:
        extras["engine_params"].setdefault("illegal_u_turn", {})["no_u_turn_junctions"] = [f"r{rd}_*" for rd in jturns[0][1]]
        cases = [(jturns[0], True, "(no U-turn)")] + ([(jturns[1], False, "(allowed)")] if len(jturns) > 1 else [])
        for (jid, _, path, out), expected, tag in cases:
            tr = Trajectory().move(path[:26], 15 / 3.6).mark("start").move(path[25:], 10 / 3.6).mark("end")
            tr.move(out, 15 / 3.6)
            act("D3", "illegal_u_turn", expected, tr, f"U-turn at junction {jid} {tag}")

    # E4: a truck over a class limit set below the lane's; negative: a car at the same speed
    need = int(math.ceil(60 / 3.6 * SPEED_IN_VIEW_S))
    cand = next((run[:need + 1] for run in candidates(m, view, need, in_view_m=need)
                 if len({limit(*xyz(w)[:2]) for w in run[:need + 1:5]}) == 1), None)
    if cand is None:
        skip("E4", "no lane in view long enough")
    else:
        lim = limit(*xyz(cand[0])[:2])
        cls_lim = round(0.6 * lim)
        kmh = min(cls_lim + 15.0, lim)
        extras["engine_params"].setdefault("speeding", {})["class_limits_kmh"] = {"truck": cls_lim}
        for cls, expected in (("truck", True), ("car", False)):
            act("E4", "speeding", expected, Trajectory().mark("start").move([xyz(w) for w in cand], kmh / 3.6).mark("end"),
                f"a {cls} at {kmh:.0f} km/h (lane {lim:.0f}, trucks {cls_lim})", cls=cls)
    return acts, extras


BASE_CONDITION = {"no_parking": "B3", "speeding": "E1", "illegal_u_turn": "D1", "zebra_crossing": "F1",
                  "lane_violation": "A4", "highway_stop": "B1"}


def act_condition(a: dict, props) -> str:
    """The condition an act stages: its own, or its type's (a wrong-way act on a highway lane is C3)."""
    if a.get("condition"):
        return a["condition"]
    if a["type"] == "wrong_way":
        mid = a["traj"].p[len(a["traj"].p) // 2]
        return "C3" if props(*mid[:2]).get("road_class") == "highway" else "C1"
    return BASE_CONDITION.get(a["type"], a["type"])


def suggest(m, town: str, altitude: float, step: float, margin: float) -> list[dict]:
    """Drone spots that together stage every condition: plan at every grid centre (step m apart, over
    the town's lanes) and heading 0 / 90, then pick spots greedily, most new conditions first."""
    import contextlib
    import io
    props = lane_props(town)
    pts = np.vstack([np.array(l["centreline"]) for l in json.loads((SCENES_DIR / f"{town}.json").read_text())["lanes"]])
    (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
    spots = []
    for cx in np.arange(x0 + step / 2, x1, step):
        for cy in np.arange(y0 + step / 2, y1, step):
            for yaw in (0.0, 90.0):
                view = View((cx, cy), yaw, altitude, margin_m=margin)
                with contextlib.redirect_stdout(io.StringIO()):
                    try:
                        p = plan(m, town, view, 0)
                    except SystemExit:
                        continue
                conds = sorted({act_condition(a, props) for a in p["acts"] if a["expected"]})
                if conds:
                    spots.append({"center": [round(float(cx), 1), round(float(cy), 1)], "yaw": yaw, "conditions": conds,
                                  "acts": len(p["acts"])})
    chosen, covered = [], set()
    while True:
        best = max(spots, key=lambda s: (len(set(s["conditions"]) - covered), s["acts"]), default=None)
        if best is None or not set(best["conditions"]) - covered:
            break
        chosen.append({**best, "new": sorted(set(best["conditions"]) - covered)})
        covered |= set(best["conditions"])
    return chosen


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

BLUEPRINTS = {"car": [BLUEPRINT],
              "bus": ["vehicle.mitsubishi.fusorosa"],
              "truck": ["vehicle.carlamotors.carlacola", "vehicle.carlamotors.european_hgv", "vehicle.carlamotors.firetruck"]}


def spawn(world, bp_lib, tr: Trajectory, cls: str = "car"):
    names = [n for n in BLUEPRINTS.get(cls, [BLUEPRINT]) if bp_lib.filter(n)]
    if not names:
        print(f"[spawn] no {cls} blueprint in this build ({BLUEPRINTS.get(cls)})")
        return None
    bp = bp_lib.find(names[0])
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
        v = spawn(world, bp_lib, tr, act.get("cls", "car") if name == "main" else "car")
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
    ap.add_argument("--red-light", action="store_true", help="Also stage red-light acts (out of scope since 2026-10-09)")
    ap.add_argument("--out", type=Path, default=None, help="Scenario log JSON (default: runs/<time>/scenario_log.json)")
    ap.add_argument("--suggest", action="store_true",
                    help="Offline: list the fewest drone spots (centre, heading) that stage every condition in --town")
    ap.add_argument("--step", type=float, default=50.0, help="--suggest grid spacing (m)")
    args = ap.parse_args()

    if args.suggest:
        if not args.town:
            raise SystemExit("--suggest needs --town")
        m = carla.Map(args.town, (XODR_DIR / f"{args.town}.xodr").read_text())
        alt = args.altitude if args.altitude is not None else 67.6
        chosen = suggest(m, args.town, alt, args.step, args.margin)
        print(f"[suggest] {args.town} at {alt:.1f} m: {len(chosen)} spots cover "
              f"{sorted({c for s in chosen for c in s['conditions']})}")
        for s in chosen:
            print(f"   --center {s['center'][0]} {s['center'][1]} --yaw {s['yaw']:.0f}   adds {s['new']}   ({s['acts']} acts)")
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({"town": args.town, "altitude_m": alt, "spots": chosen}, indent=1))
        return

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
    p = plan(m, town, view, args.seed, world, red_light=args.red_light)
    acts = [a for a in p["acts"] if not args.only or a["type"] in args.only]
    zones = list(p["zones"])
    print(f"[plan] {town} centre ({center[0]:.0f}, {center[1]:.0f}): {len(acts)} acts, {len(zones)} zones")
    for a in acts:
        print(f"   {a['type']:15s} {'violation' if a['expected'] else 'negative ':9s} {a['traj'].duration:5.1f} s  {a['note']}")

    out = args.out or Path(__file__).parent / "runs" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S") / "scenario_log.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    log = {"town": town, "center": list(center), "view": view.describe(),
           "created": datetime.datetime.now().isoformat(timespec="seconds"),
           "plan_only": args.plan_only, "zones": zones, "violations": [], "negatives": [], "acts": [],
           # rule settings the acts need (run_violations.py --zones <log> applies them)
           **{k: p[k] for k in ("lane_overrides", "engine_params") if k in p}}
    if args.plan_only:
        # planned paths sampled at 10 Hz, so `run_violations.py --plan` can dry-run the rules on them
        for a in acts:
            pts = np.array(a["traj"].p)
            ts = np.arange(0.0, a["traj"].duration, 0.1)
            samples = [[round(float(t), 2)] + a["traj"].at(t)[0][:2].round(3).tolist() for t in ts]
            entry = {"type": a["type"], "condition": a.get("condition"), "cls": a.get("cls", "car"),
                     "also": a.get("also", []), "expected": a["expected"], "note": a["note"],
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
        entry = {"type": a["type"], "condition": a.get("condition"), "cls": a.get("cls", "car"),
                 "also": a.get("also", []), "expected": a["expected"], "note": a["note"], **res}
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
