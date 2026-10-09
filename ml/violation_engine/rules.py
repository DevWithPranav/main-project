"""Violation monitors and the engine that runs them (Violation Engine, Layer 5).

docs/Violation_Engine_Architecture.md, Section 5.6. Seven PRD violation types:

    no_parking      (PRD 1)   in no_parking zone, stopped, >= 30 s
    wrong_way       (PRD 2)   against the lane > 150 deg, >= 0.17 s and >= 5 m backwards
    illegal_u_turn  (PRD 3)   heading change > 160 deg inside a no_u_turn zone, smooth arc
    speeding        (PRD 5)   speed - 2 sigma > limit + tolerance, >= 0.33 s
    lane_violation  (PRD 6)   straddling a lane line > 3 s, or a lane change across a solid line
    zebra_crossing  (PRD 7)   in crosswalk, stopped, > 10 s, not in a queue
    highway_stop    (PRD 10)  in highway zone, < 5 km/h, > 20 s, not in a queue
    red_light       (PRD 4)   front crosses a stop line while its signal is red (optional, CARLA
                              only: needs a SignalLog from signals.py)

PRD thresholds given in frames assume 30 fps and are stored here in seconds (5 frames =
0.17 s, 10 frames = 0.33 s), so they hold at any frame rate.

The engine is frame-by-frame (step(t, frame, observations)) so the same monitors can run
live later; run_violations.py feeds it a recorded flight in time order.
"""

import math
from collections import defaultdict

from events import Event, EventLog
from lane_map import SOLID_LINES, SceneMap
from predicates import (UNMARKED_LINES, Episode, Obs, TrackHistory, along_lane_mps, against_lane_angle,
                        front_point, line_on_side, on_regular_lane, queue_context, segments_cross, slow,
                        speed_tolerance_kmh, stopped, straddle_overlap)

DEFAULTS = {
    "no_parking": {"min_s": 30.0, "max_kmh": 2.0, "close_gap_s": 2.0},
    "zebra_crossing": {"min_s": 10.0, "max_kmh": 2.0, "close_gap_s": 2.0},
    "highway_stop": {"min_s": 20.0, "max_kmh": 5.0, "close_gap_s": 2.0},
    "place_memory": {"radius_m": 1.5, "keep_s": 10.0},
    "wrong_way": {"angle_deg": 150.0, "min_s": 5 / 30, "min_back_m": 5.0, "min_kmh": 5.0,
                  "confirm_gap_s": 0.3, "close_gap_s": 2.0},
    "illegal_u_turn": {"turn_deg": 160.0, "flip_deg": 90.0},
    "speeding": {"min_s": 10 / 30, "sigmas": 2.0, "tolerance": "eu", "confirm_gap_s": 0.2, "close_gap_s": 2.0},
    "lane_violation": {"straddle_s": 3.0, "min_overlap_m": 0.3, "min_kmh": 5.0, "stable_s": 0.5,
                       "exit_frac": 0.35, "confirm_gap_s": 0.5, "close_gap_s": 1.0},
    # sure_after_s: crossings this long into red get full margin; right at the change, tick timing
    # decides it, so the confidence is lower (R22: 95% of violations fall in the first 1.5 s)
    "red_light": {"min_kmh": 5.0, "max_gap_s": 0.5, "sure_after_s": 1.0, "exempt_classes": []},
}


class Monitor:
    type = ""

    def __init__(self, log: EventLog, params: dict):
        self.log, self.p = log, params

    def step(self, t: float, frame: int, obs: list[Obs], hist: TrackHistory) -> None:
        raise NotImplementedError

    def finish(self, t: float, frame: int) -> None:
        pass

    def _open(self, ep: Episode, o: Obs, **kw) -> Event:
        ep.event = self.log.open(type=self.type, track_ids=[o.track_id], cls=o.cls, start_s=round(ep.start_t, 3),
                                 start_frame=ep.start_frame, flag_s=round(o.t, 3), flag_frame=o.frame,
                                 x=round(o.x, 2), y=round(o.y, 2),
                                 lane_id=o.lane.lane.id if o.lane else None, **kw)
        return ep.event


# --- stopped inside a zone: no_parking, zebra_crossing, highway_stop -------------------------

class StopInZoneMonitor(Monitor):
    """Vehicle stopped inside a zone of `zone_type` for >= min_s (zone may override with grace_s).

    The dwell timer belongs to the **spot**, not to a track ID (place-based, Architecture
    R18): every stopped observation within radius_m of a spot in the same zone extends that
    spot's timer, whatever its track ID. So an ID switch, a duplicate track on the same car
    (seen on Town05 flight 20261002_001635: one car on a crosswalk carried two IDs at once,
    each below the 10 s grace on its own), or a car lost and re-found under a new ID all keep
    one timer. A spot closes when one of its tracks is seen again without being stopped there
    (the car left), or when nothing has been seen there for keep_s (the car is gone)."""

    def __init__(self, log, params, vtype: str, zone_type: str, stay_point: bool, queue_exempt: bool,
                 shoulder_breakdown: bool):
        super().__init__(log, params)
        self.type, self.zone_type = vtype, zone_type
        self.stay_point, self.queue_exempt, self.shoulder_breakdown = stay_point, queue_exempt, shoulder_breakdown
        self.mem_p = params.get("place_memory", DEFAULTS["place_memory"])
        self.spots: list[dict] = []  # {zone, x, y, ep, tracks, last_hit_t, last_frame}

    def _cond(self, o: Obs, hist: TrackHistory) -> bool:
        if not slow(o, self.p["max_kmh"]):
            return False
        return stopped(hist, o) if self.stay_point else True

    def _spot(self, zone, o: Obs) -> dict:
        best = None
        for s in self.spots:
            d = math.hypot(o.x - s["x"], o.y - s["y"])
            if s["zone"] == zone.id and d <= self.mem_p["radius_m"] and (best is None or d < best[0]):
                best = (d, s)
        if best is not None:
            return best[1]
        min_s = float(zone.params.get("grace_s", self.p["min_s"]))
        s = {"zone": zone.id, "x": o.x, "y": o.y, "tracks": [],
             "ep": Episode(o.t, o.frame, min_s, self.p["close_gap_s"], self.p["close_gap_s"]),
             "last_hit_t": o.t, "last_frame": None}
        self.spots.append(s)
        return s

    def step(self, t, frame, obs, hist):
        for o in obs:
            for z in o.zones_of(self.zone_type):
                if not self._cond(o, hist):
                    continue
                s = self._spot(z, o)
                ep = s["ep"]
                if o.track_id not in s["tracks"]:
                    s["tracks"].append(o.track_id)
                    if ep.event is not None:
                        ep.event.track_ids = list(s["tracks"])
                        if "id_switch_bridged" not in ep.event.tags:
                            ep.event.tags.append("id_switch_bridged")
                s["last_hit_t"] = o.t
                s["x"], s["y"] = 0.8 * s["x"] + 0.2 * o.x, 0.8 * s["y"] + 0.2 * o.y
                if s["last_frame"] == frame:  # a duplicate track on the same car: count the frame once
                    continue
                s["last_frame"] = frame
                ep.hit(o)
                ep.data["queue"] = ep.data.get("queue", 0) + int(queue_context(o, obs))
                ep.data["shoulder"] = ep.data.get("shoulder", 0) + int(o.lane is not None and o.lane.lane.lane_type == "shoulder")
                if ep.ready():
                    ev = self._open(ep, o, zone_id=z.id)
                    ev.track_ids = list(s["tracks"])
                    if len(ev.track_ids) > 1:
                        ev.tags.append("id_switch_bridged")
        for s in list(self.spots):
            idle = t - s["last_hit_t"]
            if idle <= s["ep"].close_gap_s:
                continue
            # one of its tracks is still around but no longer stopped here: the car has left
            left = any(hist.last_seen.get(tid, -1e9) > s["last_hit_t"] + 1e-6 for tid in s["tracks"])
            if left or idle > self.mem_p["keep_s"]:
                self.spots.remove(s)
                self._close(s["ep"])

    def _close(self, ep: Episode, at_end: bool = False) -> None:
        ev = ep.event
        if ev is None:
            return
        n = max(ep.n, 1)
        if self.queue_exempt and ep.data.get("queue", 0) / n >= 0.5:
            ev.tags.append("queue")
            ev.status = "suppressed"
        elif self.shoulder_breakdown and ep.data.get("shoulder", 0) / n >= 0.5:
            ev.tags.append("possible_breakdown")
            ev.status = "possible_breakdown"
        if at_end:
            ev.tags.append("ongoing_at_end")
        ev.value = {"dwell_s": round(ep.duration, 2), "grace_s": ep.min_s}
        ev.close(ep.last_true_t, ep.last_true_frame, margin=1.0, quality=ep.quality(),
                 duration_score=ep.duration / (2 * ep.min_s))

    def finish(self, t, frame):
        for s in self.spots:
            self._close(s["ep"], at_end=True)
        self.spots.clear()


# --- wrong way --------------------------------------------------------------------------------

class WrongWayMonitor(Monitor):
    type = "wrong_way"

    def __init__(self, log, params):
        super().__init__(log, params)
        self.active: dict[int, Episode] = {}

    def step(self, t, frame, obs, hist):
        p = self.p
        for o in obs:
            ang = against_lane_angle(o)
            if not (o.visible and on_regular_lane(o) and ang is not None and ang > p["angle_deg"]
                    and o.speed_kmh > p["min_kmh"]):
                continue
            ep = self.active.get(o.track_id)
            if ep is None:
                ep = self.active[o.track_id] = Episode(o.t, o.frame, p["min_s"], p["confirm_gap_s"], p["close_gap_s"])
                ep.data.update(back_m=0.0, prev_t=o.t, ang_sum=0.0)
            dt = min(o.t - ep.data["prev_t"], 0.5)
            ep.data["back_m"] += max(0.0, -along_lane_mps(o)) * dt
            ep.data["prev_t"] = o.t
            ep.data["ang_sum"] += ang
            ep.hit(o)
            if ep.ready() and ep.data["back_m"] >= p["min_back_m"]:
                self._open(ep, o)
        for tid, ep in list(self.active.items()):
            if ep.expired(t):
                del self.active[tid]
                self._close(ep)

    def _close(self, ep: Episode, at_end: bool = False) -> None:
        ev = ep.event
        if ev is None:
            return
        mean_ang = ep.data["ang_sum"] / max(ep.n, 1)
        ev.value = {"mean_angle_deg": round(mean_ang, 1), "distance_m": round(ep.data["back_m"], 1)}
        if at_end:
            ev.tags.append("ongoing_at_end")
        margin = min((mean_ang - self.p["angle_deg"]) / (180 - self.p["angle_deg"]), ep.data["back_m"] / (2 * self.p["min_back_m"]))
        ev.close(ep.last_true_t, ep.last_true_frame, margin, ep.quality(), ep.duration / max(2 * ep.min_s, 1.0))

    def finish(self, t, frame):
        for ep in self.active.values():
            self._close(ep, at_end=True)
        self.active.clear()


# --- speeding ---------------------------------------------------------------------------------

class SpeedingMonitor(Monitor):
    type = "speeding"

    def __init__(self, log, params):
        super().__init__(log, params)
        self.active: dict[int, Episode] = {}

    def limit(self, o: Obs) -> float | None:
        for z in o.zones_of("speed"):  # a speed zone overrides the lane's own limit
            if "limit_kmh" in z.params:
                return float(z.params["limit_kmh"])
        return float(o.lane.lane.speed_limit_kmh) if o.lane is not None and o.lane.lane.speed_limit_kmh else None

    def step(self, t, frame, obs, hist):
        p = self.p
        for o in obs:
            lim = self.limit(o)
            if lim is None or not o.visible or o.lane is None:
                continue
            thr = lim + speed_tolerance_kmh(lim, p["tolerance"])
            lower = o.speed_kmh - p["sigmas"] * o.speed_sigma_kmh
            if lower <= thr:
                continue
            ep = self.active.get(o.track_id)
            if ep is None:
                ep = self.active[o.track_id] = Episode(o.t, o.frame, p["min_s"], p["confirm_gap_s"], p["close_gap_s"])
                ep.data.update(max_kmh=0.0, sigma=0.0, limit=lim, thr=thr, max_lower=0.0)
            ep.hit(o)
            if o.speed_kmh > ep.data["max_kmh"]:
                ep.data.update(max_kmh=o.speed_kmh, sigma=o.speed_sigma_kmh)
            ep.data["max_lower"] = max(ep.data["max_lower"], lower)
            if ep.ready():
                self._open(ep, o)
        for tid, ep in list(self.active.items()):
            if ep.expired(t):
                del self.active[tid]
                self._close(ep)

    def _close(self, ep: Episode, at_end: bool = False) -> None:
        ev = ep.event
        if ev is None:
            return
        d = ep.data
        ev.value = {"max_speed_kmh": round(d["max_kmh"], 1), "limit_kmh": d["limit"],
                    "threshold_kmh": round(d["thr"], 1), "speed_sigma_kmh": round(d["sigma"], 2)}
        if at_end:
            ev.tags.append("ongoing_at_end")
        margin = (d["max_lower"] - d["thr"]) / max(3 * d["sigma"], 1.5)
        ev.close(ep.last_true_t, ep.last_true_frame, margin, ep.quality(), ep.duration / max(2 * ep.min_s, 1.0))

    def finish(self, t, frame):
        for ep in self.active.values():
            self._close(ep, at_end=True)
        self.active.clear()


# --- illegal U-turn ---------------------------------------------------------------------------

class UTurnMonitor(Monitor):
    """Heading change > turn_deg while inside a no_u_turn zone. Headings are summed in small
    steps; a jump > flip_deg between consecutive valid headings means the car stopped and
    reversed (three-point turn), which the PRD says not to flag (arc-smoothness filter)."""
    type = "illegal_u_turn"

    def __init__(self, log, params):
        super().__init__(log, params)
        self.state: dict[tuple, dict] = {}  # (track, zone) -> {cum, last_h, flips, start, ep, last_t}

    def step(self, t, frame, obs, hist):
        p = self.p
        seen = set()
        for o in obs:
            for z in o.zones_of("no_u_turn"):
                key = (o.track_id, z.id)
                seen.add(key)
                st = self.state.get(key)
                if st is None:
                    st = self.state[key] = {"cum": 0.0, "last_h": None, "flips": 0,
                                            "ep": Episode(o.t, o.frame, 0.0, 1.0, 1.0), "zone": z.id}
                ep = st["ep"]
                ep.hit(o)
                if o.visible and not math.isnan(o.heading_deg):
                    if st["last_h"] is not None:
                        dh = (o.heading_deg - st["last_h"] + 180.0) % 360.0 - 180.0
                        if abs(dh) > p["flip_deg"]:
                            st["flips"] += 1
                        else:
                            st["cum"] += dh
                    st["last_h"] = o.heading_deg
                if ep.event is None and abs(st["cum"]) >= p["turn_deg"]:
                    ev = self._open(ep, o, zone_id=z.id)
                    if st["flips"]:
                        ev.tags.append("three_point_turn")
                        ev.status = "suppressed"
        for key in [k for k in self.state if k not in seen]:  # left the zone (or lost)
            st = self.state[key]
            if t - st["ep"].last_true_t > 1.0:
                del self.state[key]
                self._close(st)

    def _close(self, st: dict, at_end: bool = False) -> None:
        ep = st["ep"]
        ev = ep.event
        if ev is None:
            return
        if st["flips"] and "three_point_turn" not in ev.tags:
            ev.tags.append("three_point_turn")
            ev.status = "suppressed"
        ev.value = {"turn_deg": round(st["cum"], 1), "reversals": st["flips"]}
        if at_end:
            ev.tags.append("ongoing_at_end")
        ev.close(ep.last_true_t, ep.last_true_frame, margin=(abs(st["cum"]) - self.p["turn_deg"]) / 20 + 0.5,
                 quality=ep.quality(), duration_score=1.0)

    def finish(self, t, frame):
        for st in self.state.values():
            self._close(st, at_end=True)
        self.state.clear()


# --- lane violation ---------------------------------------------------------------------------

class LaneViolationMonitor(Monitor):
    """(a) straddling: the body reaches >= min_overlap_m over a marked lane line for > straddle_s;
    (b) a completed lane change across a solid / double-solid line. Junction lanes are ignored."""
    type = "lane_violation"

    def __init__(self, log, params):
        super().__init__(log, params)
        self.straddle: dict[int, Episode] = {}
        self.lane_state: dict[int, dict] = {}  # track -> {lane, since, last_match, cand, cand_since, cand_first}

    def step(self, t, frame, obs, hist):
        p = self.p
        for o in obs:
            if not o.visible or not on_regular_lane(o) or o.speed_kmh < p["min_kmh"]:
                continue
            self._lane_change(o)
            over, side = straddle_overlap(o)
            line = line_on_side(o.lane, side)
            if over < p["min_overlap_m"] or line in UNMARKED_LINES:
                continue
            ep = self.straddle.get(o.track_id)
            if ep is None:
                ep = self.straddle[o.track_id] = Episode(o.t, o.frame, p["straddle_s"], p["confirm_gap_s"], p["close_gap_s"])
                ep.data.update(max_over=0.0, side=side, line=line)
            ep.hit(o)
            ep.data["max_over"] = max(ep.data["max_over"], over)
            if ep.ready():
                self._open(ep, o).tags.append("straddling")
        for tid, ep in list(self.straddle.items()):
            if ep.expired(t):
                del self.straddle[tid]
                self._close_straddle(ep)

    def _lane_change(self, o: Obs) -> None:
        p = self.p
        st = self.lane_state.get(o.track_id)
        if st is None:
            self.lane_state[o.track_id] = {"lane": o.lane.lane.id, "since": o.t, "last": o.lane, "cand": None}
            return
        if o.lane.lane.id == st["lane"]:
            st["cand"] = None
            st["last"] = o.lane
            return
        if st["cand"] is None or st["cand"] != o.lane.lane.id:
            st.update(cand=o.lane.lane.id, cand_since=o.t, cand_obs=o, prev=st["last"], prev_since=st["since"])
            return
        if o.t - st["cand_since"] < p["stable_s"]:
            return
        prev = st["prev"]
        stable_before = st["cand_since"] - st["prev_since"] >= p["stable_s"]
        lateral = abs(prev.d) >= p["exit_frac"] * prev.lane.width
        if stable_before and lateral:
            side = "left" if prev.d > 0 else "right"
            line = line_on_side(prev, side)
            if line in SOLID_LINES:
                co = st["cand_obs"]
                ep = Episode(co.t, co.frame, 0.0, 0.0, 0.0)
                ep.hit(co)
                ev = self._open(ep, co)
                ev.tags.append("solid_line_crossing")
                ev.value = {"from_lane": prev.lane.id, "to_lane": o.lane.lane.id, "line": line, "side": side}
                ev.close(co.t, co.frame, margin=1.0, quality=ep.quality(), duration_score=1.0)
        st.update(lane=o.lane.lane.id, since=st["cand_since"], last=o.lane, cand=None)

    def _close_straddle(self, ep: Episode, at_end: bool = False) -> None:
        ev = ep.event
        if ev is None:
            return
        ev.value = {"straddle_s": round(ep.duration, 2), "max_overlap_m": round(ep.data["max_over"], 2),
                    "side": ep.data["side"], "line": ep.data["line"]}
        if at_end:
            ev.tags.append("ongoing_at_end")
        ev.close(ep.last_true_t, ep.last_true_frame, margin=ep.data["max_over"] / (2 * self.p["min_overlap_m"]),
                 quality=ep.quality(), duration_score=ep.duration / (2 * ep.min_s))

    def finish(self, t, frame):
        for ep in self.straddle.values():
            self._close_straddle(ep, at_end=True)
        self.straddle.clear()


# --- red light (optional, CARLA only) -----------------------------------------------------------

class RedLightMonitor(Monitor):
    """The vehicle's front crosses a stop line in the line's approach direction while the line's
    signal is red. Crossing on yellow is legal, and a car already past the line when it turns
    red never crosses it on red (PRD 4, edge case 1). One event per track and stop line; the
    value says how long the light had been red. Emergency vehicles (PRD edge case 2) can be
    exempted by class, but the detector has no such class: CARLA's oracle run only."""
    type = "red_light"

    def __init__(self, log, params, stop_lines: list[dict], signals):
        super().__init__(log, params)
        self.signals = signals
        self.lines = []
        for sl in stop_lines:
            (ax, ay), (bx, by) = sl["line"][0], sl["line"][-1]
            d = sl.get("dir")
            self.lines.append({"id": str(sl["id"]), "a": (ax, ay), "b": (bx, by), "signal": str(sl["signal_id"]),
                               "dir": (float(d[0]), float(d[1])) if d else None,
                               "mid": ((ax + bx) / 2, (ay + by) / 2), "reach": math.hypot(bx - ax, by - ay) / 2 + 10.0})
        self.prev: dict[int, tuple] = {}  # track -> (front point, t)
        self.done: set = set()
        self.frame_t: dict[int, float] = {}

    def _t_of(self, frame: int, now_t: float) -> float:
        """Time of a frame this monitor has seen (the latest seen frame at or before it)."""
        if frame in self.frame_t:
            return self.frame_t[frame]
        earlier = [f for f in self.frame_t if f <= frame]
        return self.frame_t[max(earlier)] if earlier else now_t

    def step(self, t, frame, obs, hist):
        self.frame_t[frame] = t
        if not self.lines or self.signals is None:
            return
        p = self.p
        for o in obs:
            front = front_point(o)
            if front is None or not o.visible or o.speed_kmh < p["min_kmh"] or o.cls in p["exempt_classes"]:
                self.prev.pop(o.track_id, None)
                continue
            prev = self.prev.get(o.track_id)
            self.prev[o.track_id] = (front, o.t)
            if prev is None or o.t - prev[1] > p["max_gap_s"]:
                continue
            for ln in self.lines:
                key = (o.track_id, ln["id"])
                if key in self.done or math.hypot(front[0] - ln["mid"][0], front[1] - ln["mid"][1]) > ln["reach"]:
                    continue
                if not segments_cross(prev[0], front, ln["a"], ln["b"]):
                    continue
                move = (front[0] - prev[0][0], front[1] - prev[0][1])
                if ln["dir"] is not None and move[0] * ln["dir"][0] + move[1] * ln["dir"][1] <= 0:
                    continue  # crossing it the other way (leaving the junction)
                self.done.add(key)
                st = self.signals.state_at(ln["signal"], frame)
                if st is None or st[0] != "Red":
                    continue
                red_for = max(0.0, o.t - self._t_of(st[1], o.t))
                ep = Episode(o.t, o.frame, 0.0, 0.0, 0.0)
                ep.hit(o)
                ev = self._open(ep, o)
                ev.value = {"signal_id": ln["signal"], "stop_line": ln["id"], "red_for_s": round(red_for, 2),
                            "speed_kmh": round(o.speed_kmh, 1)}
                ev.close(o.t, o.frame, margin=red_for / p["sure_after_s"], quality=ep.quality(), duration_score=1.0)


# --- engine -----------------------------------------------------------------------------------

def default_monitors(log: EventLog, params: dict | None = None, stop_lines: list | None = None,
                     signals=None) -> list[Monitor]:
    P = {k: dict(v) for k, v in DEFAULTS.items()}
    for k, v in (params or {}).items():
        P.setdefault(k, {}).update(v)
    pm = {"place_memory": P["place_memory"]}
    return [
        StopInZoneMonitor(log, {**P["no_parking"], **pm}, "no_parking", "no_parking", stay_point=True,
                          queue_exempt=False, shoulder_breakdown=False),
        StopInZoneMonitor(log, {**P["zebra_crossing"], **pm}, "zebra_crossing", "crosswalk", stay_point=True,
                          queue_exempt=True, shoulder_breakdown=False),
        StopInZoneMonitor(log, {**P["highway_stop"], **pm}, "highway_stop", "highway", stay_point=False,
                          queue_exempt=True, shoulder_breakdown=True),
        WrongWayMonitor(log, P["wrong_way"]),
        UTurnMonitor(log, P["illegal_u_turn"]),
        SpeedingMonitor(log, P["speeding"]),
        LaneViolationMonitor(log, P["lane_violation"]),
        RedLightMonitor(log, P["red_light"], stop_lines or [], signals),
    ]


class Engine:
    def __init__(self, scene: SceneMap, params: dict | None = None, prefix: str = "ev", signals=None):
        """signals: a signals.SignalLog (CARLA only) to enable the red-light rule on the scene's stop lines."""
        self.scene = scene
        self.log = EventLog(prefix)
        self.monitors = default_monitors(self.log, params, scene.stop_lines, signals)
        self.hist = TrackHistory()

    def observe(self, r: dict) -> Obs:
        """A kinematics row (kinematics.py) -> Obs with its lane match and zones."""
        h = r.get("heading_deg", "")
        o = Obs(frame=int(r["frame"]), t=float(r["time_s"]), track_id=int(r["track_id"]), cls=str(r["class"]),
                conf=float(r.get("conf", 1.0)), visible=bool(int(r.get("visible", 1))), x=float(r["x"]), y=float(r["y"]),
                vx=float(r["vx"]), vy=float(r["vy"]), speed_kmh=float(r["speed_kmh"]),
                speed_sigma_kmh=float(r.get("speed_sigma_kmh", 0.0)),
                heading_deg=float(h) if h not in ("", None) and not (isinstance(h, float) and math.isnan(h)) else math.nan)
        o.lane = self.scene.match(o.x, o.y)
        o.zones = self.scene.zones_at(o.x, o.y)
        return o

    def step(self, t: float, frame: int, obs: list[Obs]) -> None:
        for o in obs:
            self.hist.add(o)
        for m in self.monitors:
            m.step(t, frame, obs, self.hist)

    def run(self, kin_rows: list[dict]) -> list[Event]:
        by_frame = defaultdict(list)
        for r in kin_rows:
            by_frame[(float(r["time_s"]), int(r["frame"]))].append(r)
        t = frame = 0
        for (t, frame) in sorted(by_frame):
            self.step(t, frame, [self.observe(r) for r in by_frame[(t, frame)]])
        for m in self.monitors:
            m.finish(t, frame)
        return self.log.events
