"""Zebra-crossing conditions that need pedestrians (Build Plan M3; Expected_Output 4.2 F2, F3, F5).

All three are zebra_crossing events, told apart by a tag (schemas/conditions.json):

    blocking          F2  a vehicle stands on the crossing (body >= min_depth_m into it, < block_kmh)
                          for >= block_min_s while a pedestrian waits at the crossing or is on it
    failure_to_yield  F3  a vehicle drives through the crossing (>= pass_kmh) while a pedestrian with
                          priority is on it near the vehicle's path, or stepping onto it towards that
                          path, for >= yield_min_s
    obstructing       F5  during a blocking stop, a pedestrian on the crossing reaches the vehicle's
                          body along the crossing (gap <= obstruct_m) for >= obstruct_min_s: the
                          stopped vehicle is in the pedestrian's path. Raises the stop's event to F5
                          (tag priority), or opens it if the F2 timer has not run out yet.

Geometry is in the crossing's own frame: s along the walking axis (across the road), w along the
road. The axis is the side of the crossing's minimum rotated rectangle most perpendicular to the lane
under its centre (a CARLA crossing at a junction is not always longer across the road than along
it); without a lane, its longer side. Vehicles are oriented rectangles (predicates HALF_LENGTH_M /
HALF_WIDTH_M) along their heading, else their last heading, else the lane, else straight across
the crossing's axis.

"Priority" follows the Uniform Vehicle Code 11-502(a) (docs/Research_Notes.md, M3): yield to a
pedestrian on the crossing on the vehicle's half of the road or approaching closely from the other
half. Without the road's centre line in crossing coordinates this reads as: the pedestrian is within
conflict_m of the vehicle's body along the crossing axis, or will be within lookahead_s at its
walking speed; a pedestrian already walking away who is clear_m past the body is clear. Signal
phases at signalised crossings are not known (CARLA does not log pedestrian lights): a pedestrian
crossing against a red man still counts, so events at signalised crossings are tagged
signalised_crossing when the zone says so and go to review.

Pedestrian rows are kinematics rows whose class is in predicates.PEOPLE_CLASSES; rules.Engine gives
them only to monitors with sees_people = True (here), so no vehicle rule ever sees a person.

Usage: registered by rules.default_monitors (params key "zebra_pedestrian"); tests in
tests/test_zebra_pedestrians.py.
"""

import math

import numpy as np
import shapely

from predicates import HALF_LENGTH_M, HALF_WIDTH_M, PEOPLE_CLASSES, Episode, Obs, queue_context
from rules import Monitor

DEFAULTS = {
    "zebra_pedestrian": {
        # where a pedestrian counts as at the crossing: on_margin_m around the painted area (position
        # noise of a few-pixel person ~0.3 m plus half a body), or in the waiting area wait_m beyond
        # either end (the kerb) and side_m to either side of it
        "on_margin_m": 0.5, "wait_m": 2.5, "side_m": 1.0,
        "wait_kmh": 3.0,  # waiting = standing or shuffling (normal walking is 4-5 km/h)
        "min_depth_m": 1.0,  # a vehicle's body this far into the crossing is on it (nose over the edge is not)
        # F2
        "block_kmh": 3.0, "block_min_s": 2.0,
        # F3: conflict_m ~ a lane width minus the vehicle half-width margin (UVC: the vehicle's half
        # of the road); lookahead_s: a 1.4 m/s walker covers 2.8 m (MUTCD design speed 1.07-1.2 m/s)
        "pass_kmh": 5.0, "conflict_m": 3.0, "lookahead_s": 2.0, "clear_m": 1.0,
        "entering_mps": 0.5, "away_mps": 0.3, "yield_min_s": 0.3,
        # F5
        "obstruct_m": 2.0, "obstruct_min_s": 1.0,
        "close_gap_s": 1.0, "max_dt_s": 0.5,
    }
}


class Crossing:
    """A crosswalk zone in its own frame: s along the walking axis, w along the road."""

    def __init__(self, zone, scene=None, on_margin_m: float = 0.5, wait_m: float = 2.5, side_m: float = 1.0):
        self.id, self.zone, self.poly = zone.id, zone, zone.polygon
        c = self.poly.centroid
        self.c = np.array([c.x, c.y])
        rect = np.array(self.poly.minimum_rotated_rectangle.exterior.coords)[:4]
        edges = [rect[1] - rect[0], rect[2] - rect[1]]
        axis = None
        if "axis" in zone.params:  # a site file may give the walking direction
            axis = np.asarray(zone.params["axis"], float)
        else:
            m = scene.match(c.x, c.y) if scene is not None else None
            if m is not None:
                axis = min(edges, key=lambda e: abs(np.dot(e / max(np.linalg.norm(e), 1e-9), m.dir)))
            else:
                axis = max(edges, key=np.linalg.norm)
        self.axis = axis / max(np.linalg.norm(axis), 1e-9)
        self.along = np.array([-self.axis[1], self.axis[0]])
        pts = np.array(self.poly.exterior.coords) - self.c
        s, w = pts @ self.axis, pts @ self.along
        self.s0, self.s1, self.w0, self.w1 = float(s.min()), float(s.max()), float(w.min()), float(w.max())
        self.on_poly = self.poly.buffer(on_margin_m)
        shapely.prepare(self.on_poly)
        self.wait_m, self.side_m = wait_m, side_m
        self.signalised = bool(zone.params.get("signalised", False))

    def sw(self, x: float, y: float) -> tuple[float, float]:
        d = np.array([x, y]) - self.c
        return float(d @ self.axis), float(d @ self.along)

    def on(self, x: float, y: float) -> bool:
        return self.on_poly.contains(shapely.Point(x, y))

    def waiting_end(self, x: float, y: float) -> int:
        """-1 / +1: in the waiting area beyond the s0 / s1 end; 0: not."""
        s, w = self.sw(x, y)
        if not (self.w0 - self.side_m <= w <= self.w1 + self.side_m):
            return 0
        if self.s0 - self.wait_m <= s < self.s0:
            return -1
        if self.s1 < s <= self.s1 + self.wait_m:
            return 1
        return 0


def footprint(o: Obs, heading_deg: float) -> shapely.Polygon:
    hl, hw = HALF_LENGTH_M.get(o.cls, 2.3), HALF_WIDTH_M.get(o.cls, 0.9)
    a = math.radians(heading_deg)
    f, r = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
    c = np.array([o.x, o.y])
    return shapely.Polygon([c + hl * f + hw * r, c - hl * f + hw * r, c - hl * f - hw * r, c + hl * f - hw * r])


class ZebraPedestrianMonitor(Monitor):
    """F2 / F3 / F5 per (vehicle, crossing) visit; see the module docstring."""
    type = "zebra_crossing"
    sees_people = True

    def __init__(self, log, params, scene=None):
        super().__init__(log, params)
        p = self.p
        zones = [z for z in (scene.zones if scene is not None else []) if z.type == "crosswalk"]
        self.crossings = [Crossing(z, scene, p["on_margin_m"], p["wait_m"], p["side_m"]) for z in zones]
        self.visits: dict[tuple, dict] = {}  # (vehicle track, crossing id) -> state
        self.heading: dict[int, float] = {}

    # -- per frame -------------------------------------------------------------------------------
    def step(self, t, frame, obs, hist):
        if not self.crossings:
            return
        people = [o for o in obs if o.cls in PEOPLE_CLASSES]
        vehicles = [o for o in obs if o.cls not in PEOPLE_CLASSES]
        for o in vehicles:
            if not math.isnan(o.heading_deg):
                self.heading[o.track_id] = o.heading_deg
        for cr in self.crossings:
            near = [q for q in people if cr.on(q.x, q.y) or cr.waiting_end(q.x, q.y)]
            for o in vehicles:
                if math.hypot(o.x - cr.c[0], o.y - cr.c[1]) > 40.0:
                    continue
                body = footprint(o, self._heading(o, cr))
                inter = body.intersection(cr.poly).area
                depth = inter / (2 * HALF_WIDTH_M.get(o.cls, 0.9))
                if depth < self.p["min_depth_m"]:
                    continue
                self._visit(cr, o, body, near, vehicles, t)
        for key, v in list(self.visits.items()):
            if t - v["last_t"] > self.p["close_gap_s"]:
                del self.visits[key]
                self._close(v)

    def _heading(self, o: Obs, cr: Crossing) -> float:
        if not math.isnan(o.heading_deg):
            return o.heading_deg
        if o.track_id in self.heading:
            return self.heading[o.track_id]
        if o.drive_lane is not None:
            return o.drive_lane.dir_deg
        return math.degrees(math.atan2(cr.along[1], cr.along[0]))

    def _visit(self, cr: Crossing, o: Obs, body, near: list[Obs], vehicles: list[Obs], t: float) -> None:
        p = self.p
        key = (o.track_id, cr.id)
        v = self.visits.get(key)
        if v is None:
            v = self.visits[key] = {"cr": cr, "first": o, "last_t": o.t, "block": None, "pass": None,
                                    "block_s": 0.0, "obstruct_s": 0.0, "yield_s": 0.0, "peds": set(),
                                    "ped_conf": [], "min_gap": math.inf, "max_kmh": 0.0, "queue": 0, "n_stop": 0,
                                    "obstruct_peds": set(), "yield_peds": set(), "yield_state": None,
                                    "n": 0, "conf": 0.0, "vis": 0, "last_frame": o.frame}
        dt = min(max(o.t - v["last_t"], 0.0), p["max_dt_s"])
        v["last_t"], v["last_frame"] = o.t, o.frame
        v["n"] += 1
        v["conf"] += o.conf
        v["vis"] += int(o.visible)
        v["max_kmh"] = max(v["max_kmh"], o.speed_kmh)
        b = np.array(body.exterior.coords)[:4] - cr.c
        bs = b @ cr.axis
        smin, smax = float(bs.min()), float(bs.max())
        if o.speed_kmh < p["block_kmh"]:
            self._blocking(v, cr, o, near, vehicles, smin, smax, dt)
        elif o.speed_kmh >= p["pass_kmh"]:
            self._passing(v, cr, o, near, smin, smax, dt)

    @staticmethod
    def _gap(s: float, smin: float, smax: float) -> tuple[float, float]:
        """Gap (m) from a pedestrian at s to the body's extent [smin, smax] along the axis, and the
        unit direction from the pedestrian towards the body (0 when inside it)."""
        if s < smin:
            return smin - s, 1.0
        if s > smax:
            return s - smax, -1.0
        return 0.0, 0.0

    def _blocking(self, v, cr, o, near, vehicles, smin, smax, dt) -> None:
        p = self.p
        present = [q for q in near if cr.on(q.x, q.y) or q.speed_kmh < p["wait_kmh"] or self._towards_crossing(cr, q)]
        if not present:
            return
        v["n_stop"] += 1
        v["queue"] += int(queue_context(o, vehicles))
        v["block_s"] += dt
        v["peds"].update(q.track_id for q in present)
        v["ped_conf"].extend(q.conf for q in present)
        obstructed = False
        for q in present:
            if not cr.on(q.x, q.y):
                continue
            s, _ = cr.sw(q.x, q.y)
            gap, toward = self._gap(s, smin, smax)
            vs = float(np.array([q.vx, q.vy]) @ cr.axis)
            if gap <= p["obstruct_m"] and not (toward and vs * toward < -p["away_mps"]):
                obstructed = True
                v["obstruct_peds"].add(q.track_id)
                v["min_gap"] = min(v["min_gap"], gap)
        if obstructed:
            v["obstruct_s"] += dt
        if v["block"] is None:
            if v["block_s"] >= p["block_min_s"] - 1e-9 or v["obstruct_s"] >= p["obstruct_min_s"] - 1e-9:
                v["block"] = self._event(v, o, "blocking")
        if v["block"] is not None and v["obstruct_s"] >= p["obstruct_min_s"] - 1e-9 and "obstructing" not in v["block"].tags:
            v["block"].tags.append("obstructing")

    def _towards_crossing(self, cr: Crossing, q: Obs) -> bool:
        end = cr.waiting_end(q.x, q.y)
        vs = float(np.array([q.vx, q.vy]) @ cr.axis)
        return end != 0 and -end * vs >= self.p["entering_mps"]

    def _passing(self, v, cr, o, near, smin, smax, dt) -> None:
        p = self.p
        hit = None
        for q in near:
            s, _ = cr.sw(q.x, q.y)
            gap, toward = self._gap(s, smin, smax)
            approach = float(np.array([q.vx, q.vy]) @ cr.axis) * toward  # > 0: walking towards the body's path
            if cr.on(q.x, q.y):
                state = "on"
                if approach < -p["away_mps"]:
                    conflict = gap <= p["clear_m"]
                else:
                    conflict = gap - max(approach, 0.0) * p["lookahead_s"] <= p["conflict_m"]
            elif self._towards_crossing(cr, q):
                state = "entering"
                conflict = approach > 0 and gap - approach * p["lookahead_s"] <= p["conflict_m"]
            else:
                continue
            if conflict and (hit is None or gap < hit[1]):
                hit = (q, gap, state)
        if hit is None:
            v["yield_s"] = 0.0  # the conflict must hold yield_min_s in a row
            return
        q, gap, state = hit
        v["yield_s"] += dt
        v["yield_peds"].add(q.track_id)
        v["ped_conf"].append(q.conf)
        v["min_gap"] = min(v["min_gap"], gap)
        if v["yield_state"] != "on":
            v["yield_state"] = state
        if v["pass"] is None and v["yield_s"] >= p["yield_min_s"] - 1e-9:
            v["pass"] = self._event(v, o, "failure_to_yield")

    def _event(self, v: dict, o: Obs, tag: str):
        first = v["first"]
        ep = Episode(first.t, first.frame, 0.0, 0.0, 0.0)
        ep.hit(first)
        ep.hit(o)
        ev = self._open(ep, o, zone_id=v["cr"].id)
        ev.tags.append(tag)
        if v["cr"].signalised:
            ev.tags.append("signalised_crossing")
        return ev

    # -- closing ---------------------------------------------------------------------------------
    def _close(self, v: dict, at_end: bool = False) -> None:
        p = self.p
        ped_q = float(np.mean(v["ped_conf"])) if v["ped_conf"] else 0.0
        for ev, tag in ((v["block"], "blocking"), (v["pass"], "failure_to_yield")):
            if ev is None:
                continue
            veh_q = (v["conf"] / v["n"]) * (v["vis"] / v["n"]) if v["n"] else 0.0
            quality = math.sqrt(veh_q * ped_q)  # both the vehicle and the pedestrian must be seen well
            gap = None if math.isinf(v["min_gap"]) else round(v["min_gap"], 2)
            if tag == "blocking":
                peds = v["peds"]
                ev.value = {"crossing": v["cr"].id, "blocking_s": round(v["block_s"], 2),
                            "obstructing_s": round(v["obstruct_s"], 2), "pedestrian_track_ids": sorted(peds),
                            "obstructed_track_ids": sorted(v["obstruct_peds"]), "min_gap_m": gap}
                if v["n_stop"] and v["queue"] / v["n_stop"] >= 0.5:
                    ev.tags.append("queue")  # still blocking: a vehicle may not stop on a crossing to queue
                margin = max(v["block_s"] / (2 * p["block_min_s"]), v["obstruct_s"] / (2 * p["obstruct_min_s"]))
            else:
                ev.value = {"crossing": v["cr"].id, "pedestrian_track_ids": sorted(v["yield_peds"]),
                            "pedestrian_state": v["yield_state"], "min_gap_m": gap,
                            "max_speed_kmh": round(v["max_kmh"], 1), "conflict_s": round(v["yield_s"], 2)}
                margin = 0.5 + (p["conflict_m"] - (v["min_gap"] if not math.isinf(v["min_gap"]) else p["conflict_m"])) / (2 * p["conflict_m"])
            if v["cr"].signalised:
                ev.status = "needs_review"
            if at_end:
                ev.tags.append("ongoing_at_end")
            ev.close(v["last_t"], v["last_frame"], margin=margin, quality=quality, duration_score=1.0)

    def finish(self, t, frame):
        for v in self.visits.values():
            self._close(v, at_end=True)
        self.visits.clear()


def pedestrian_monitors(log, P: dict, scene=None) -> list[Monitor]:
    if not P.get("zebra_pedestrian", {}).get("enabled", True):
        return []
    return [ZebraPedestrianMonitor(log, P["zebra_pedestrian"], scene)]
