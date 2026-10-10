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

Build Plan M2 adds the conditions that read road features (lane_map.py, road_features.py) as tags
on these types: lane A1/A3/A5/A6/A8, stopping B1/B2/B4/B5 from lane properties (Engine.road_zone),
wrong-way C2-C5, U-turns outside zones D2-D4 (UTurnAnywhereMonitor), speeding by class E4.
schemas/conditions.json maps each tag to its condition id (Expected_Output Section 4.2).

PRD thresholds given in frames assume 30 fps and are stored here in seconds (5 frames =
0.17 s, 10 frames = 0.33 s), so they hold at any frame rate.

The engine is frame-by-frame (step(t, frame, observations)) so the same monitors can run
live later; run_violations.py feeds it a recorded flight in time order.
"""

import fnmatch
import math
from collections import defaultdict, deque

from events import Event, EventLog
from lane_map import SOLID_LINES, Lane, LaneMatch, SceneMap, Zone, angle_diff_deg
from predicates import (HALF_LENGTH_M, HALF_WIDTH_M, PEOPLE_CLASSES, UNMARKED_LINES, Episode, Obs, TrackHistory, along_lane_mps, along_target,
                        against_lane_angle, front_point, lane_reachable, line_on_side, on_regular_lane,
                        queue_context, segments_cross, slow, speed_tolerance_kmh, stopped, straddle_overlap)
from schemas import condition_of

DEFAULTS = {
    "no_parking": {"min_s": 30.0, "max_kmh": 2.0, "close_gap_s": 2.0},
    "zebra_crossing": {"min_s": 10.0, "max_kmh": 2.0, "close_gap_s": 2.0},
    "highway_stop": {"min_s": 20.0, "max_kmh": 5.0, "close_gap_s": 2.0},
    # keep_s: a stopped car's detection can drop out for tens of seconds under a tree crown (2026-10-10
    # staging, Town03 spots 1 / 4: gaps of 10 s and 24 s, both 45 s no-parking acts missed at 10 s;
    # at 30 s both caught, no negative triggered, events vs oracle 32 -> 28 false)
    "place_memory": {"radius_m": 1.5, "keep_s": 30.0},
    "wrong_way": {"angle_deg": 150.0, "min_s": 5 / 30, "min_back_m": 5.0, "min_kmh": 5.0,
                  "confirm_gap_s": 0.3, "close_gap_s": 2.0},
    # anywhere: also U-turns outside no_u_turn zones (D2-D4), judged by what the path crossed;
    # window_s: the turn must complete within it; no_u_turn_junctions: lane-id globs of junctions
    # where U-turns are prohibited (D3), e.g. ["*"] for all
    "illegal_u_turn": {"turn_deg": 160.0, "flip_deg": 90.0, "anywhere": True, "window_s": 30.0,
                       "settle_s": 5.0, "no_u_turn_junctions": []},
    # class_limits_kmh: per-class limit cap (E4), from the profile's road.class_speed_limits_kmh.
    # min_track_age_s: on flight 20261009_201727 every pipeline speeding episode began on its track's
    # first frame, and two were start-up transients (59.9 km/h read for a 29.6 km/h car entering at
    # the frame corner; 39.7 decaying to 30 within a second)
    "speeding": {"min_s": 10 / 30, "sigmas": 2.0, "tolerance": "eu", "confirm_gap_s": 0.2, "close_gap_s": 2.0,
                 "class_limits_kmh": {}, "min_track_age_s": 1.0},
    # unsafe_*: A8, TTC to the lead / lag vehicle in the target lane; 2 s is the common risky/safe
    # threshold, 1 s high risk; closing speed > 0.5 m/s as SinD 2.0 (docs/Research_Notes.md, M2).
    # Judged in the unsafe_window_s after the centre crosses (the lane-change moment, as highD does):
    # over 3 s, natural CARLA traffic braking for a queue ahead read as unsafe (staged flight
    # 20261009_201727, oracle). Not below unsafe_min_kmh: zipper merging in a crawling queue.
    # min_below_s: the TTC must stay low that long (SinD 2.0: >= 3 frames; 0.1 s at 30 fps let
    # drone-position noise near zero gap through on that flight); overlap_margin_m: sideways body
    # overlap needed before "alongside" counts as a conflict
    # junction_*: A1, time between leaving one lane and entering the next one through a junction
    "lane_violation": {"straddle_s": 3.0, "min_overlap_m": 0.3, "min_kmh": 5.0, "stable_s": 0.5,
                       "exit_frac": 0.35, "confirm_gap_s": 0.5, "close_gap_s": 1.0,
                       "unsafe_ttc_s": 2.0, "high_risk_ttc_s": 1.0, "unsafe_window_s": 1.0, "min_closing_mps": 0.5,
                       "unsafe_min_kmh": 15.0, "min_below_s": 0.3, "overlap_margin_m": 0.3,
                       "junction_min_s": 1.0, "junction_max_s": 20.0,
                       "restricted_min_s": 1.0, "restricted_exempt": {"bus": ["bus"], "emergency": [], "restricted": []},
                       "shoulder_min_s": 3.0, "shoulder_min_kmh": 10.0},
    # sure_after_s: crossings this long into red get full margin; right at the change, tick timing
    # decides it, so the confidence is lower (R22: 95% of violations fall in the first 1.5 s)
    # enter_m / stop_kmh: CARLA's stop waypoints sit a few metres before where its traffic halts; on
    # staged flight 20261009_201727 (true positions) 38 cars waiting at red first stopped -1.0..7.9 m
    # past the line, the staged runners never stopped. So the front must get enter_m past the line
    # (into the junction) before the vehicle first stops (< stop_kmh), within confirm_s.
    "red_light": {"min_kmh": 5.0, "max_gap_s": 0.5, "sure_after_s": 1.0, "exempt_classes": [],
                  "enter_m": 10.0, "stop_kmh": 3.0, "confirm_s": 10.0},
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
                if o.lane is not None:  # where on the road it stopped: ramp / merge area (B4), bridge or tunnel (B5)
                    ep.data["ramp"] = ep.data.get("ramp", 0) + int(o.lane.lane.ramp is not None)
                    ep.data["bridge"] = ep.data.get("bridge", 0) + int(o.lane.lane.bridge or o.lane.lane.tunnel)
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
        if self.type == "highway_stop":
            if ep.data.get("bridge", 0) / n >= 0.5:
                ev.tags.append("on_bridge")
            if ep.data.get("ramp", 0) / n >= 0.5:
                ev.tags.append("ramp_area")
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
                self._open(ep, o).tags.extend(self.road_tags(o, ep, hist))
        for tid, ep in list(self.active.items()):
            if ep.expired(t):
                del self.active[tid]
                self._close(ep)

    @staticmethod
    def road_tags(o: Obs, ep: Episode, hist: TrackHistory) -> list[str]:
        """Which wrong-way condition (Expected_Output 4.2 C): wrong_way_entry (C2) if, just before
        going against the lane, the vehicle was in a junction or on another road (it entered from
        the prohibited end); divided_highway (C3), on_ramp (C4), one_way (C5) from the lane."""
        lane, tags = o.lane.lane, []
        before = [h for h in hist.obs.get(o.track_id, ()) if ep.start_t - 3.0 <= h.t < ep.start_t - 1e-6 and h.lane is not None]
        if before and (before[-1].lane.lane.junction or (lane.road_id is not None and before[-1].lane.lane.road_id != lane.road_id)):
            tags.append("wrong_way_entry")
        if lane.road_class == "highway":
            tags.append("divided_highway")
        if lane.ramp:
            tags.append("on_ramp")
        if lane.one_way:
            tags.append("one_way")
        return tags

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
        self.first_seen: dict[int, float] = {}

    def limit(self, o: Obs) -> tuple[float | None, str]:
        """The limit that applies and where it comes from: a speed zone overrides the lane's own limit
        (E3); a lower limit for the vehicle's class caps either (E4)."""
        lim, src = None, "lane"
        for z in o.zones_of("speed"):
            if "limit_kmh" in z.params:
                lim, src = float(z.params["limit_kmh"]), "zone"
                break
        lane = o.drive_lane or o.lane  # the limit of the lane it drives in, not of the oncoming one
        if lim is None and lane is not None and lane.lane.speed_limit_kmh:
            lim = float(lane.lane.speed_limit_kmh)
        cls_lim = self.p.get("class_limits_kmh", {}).get(o.cls)
        if lim is not None and cls_lim is not None and float(cls_lim) < lim:
            lim, src = float(cls_lim), "class"
        return lim, src

    def step(self, t, frame, obs, hist):
        p = self.p
        for o in obs:
            first = self.first_seen.setdefault(o.track_id, o.t)
            if o.t - first < p["min_track_age_s"]:
                continue  # a new track's speed rests on a few frames, often of a car cut by the frame edge
            lim, src = self.limit(o)
            if lim is None or not o.visible or o.lane is None:
                continue
            thr = lim + speed_tolerance_kmh(lim, p["tolerance"])
            lower = o.speed_kmh - p["sigmas"] * o.speed_sigma_kmh
            if lower <= thr:
                continue
            ep = self.active.get(o.track_id)
            if ep is None:
                ep = self.active[o.track_id] = Episode(o.t, o.frame, p["min_s"], p["confirm_gap_s"], p["close_gap_s"])
                ep.data.update(max_kmh=0.0, sigma=0.0, limit=lim, limit_source=src, thr=thr, max_lower=0.0)
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
        ev.value = {"max_speed_kmh": round(d["max_kmh"], 1), "limit_kmh": d["limit"], "limit_source": d["limit_source"],
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


class UTurnAnywhereMonitor(Monitor):
    """U-turns outside no_u_turn zones (Expected_Output 4.2 D2-D4). A U-turn: heading turns by
    >= turn_deg within window_s and the vehicle ends on a lane of the same road (or within
    SAME_PLACE_M without road ids) running opposite to the lane it started in. Legal unless:
      across_solid_line (D2)      mid-block, and the centre line it crossed is solid / double solid
      through_median (D4)         mid-block on a divided road (the start or end lane has median_left)
      at_prohibited_junction (D3) inside a junction whose lanes match params no_u_turn_junctions
    Turns in a no_u_turn zone are left to UTurnMonitor (D1); three-point turns are kept, suppressed."""
    type = "illegal_u_turn"
    SAME_PLACE_M = 40.0

    def __init__(self, log, params):
        super().__init__(log, params)
        self.samples: dict[int, deque] = {}  # track -> (t, dh, flipped, obs) over window_s
        self.last_h: dict[int, float] = {}
        self.pending: dict[int, dict] = {}  # turned; waiting to settle in the opposing lane
        self.quiet_until: dict[int, float] = {}

    def step(self, t, frame, obs, hist):
        p = self.p
        if not p.get("anywhere", True):
            return
        for o in obs:
            tid = o.track_id
            q = self.samples.setdefault(tid, deque())
            dh, flipped = 0.0, False
            if o.visible and not math.isnan(o.heading_deg):
                lh = self.last_h.get(tid)
                if lh is not None:
                    d = (o.heading_deg - lh + 180.0) % 360.0 - 180.0
                    flipped, dh = abs(d) > p["flip_deg"], (0.0 if abs(d) > p["flip_deg"] else d)
                self.last_h[tid] = o.heading_deg
            q.append((o.t, dh, flipped, o))
            while q and q[0][0] < o.t - p["window_s"]:
                q.popleft()
            if tid in self.pending:
                self._settle(tid, o)
            elif o.t >= self.quiet_until.get(tid, -1e9) and abs(sum(s[1] for s in q)) >= p["turn_deg"]:
                self._turned(tid, o, list(q))

    def _turned(self, tid: int, o: Obs, q: list) -> None:
        """Heading has turned round: find the lane it started in (the last regular-lane sample facing
        the other way) and wait for it to settle in the opposing lane."""
        start = next((s[3] for s in reversed(q) if on_regular_lane(s[3]) and not math.isnan(s[3].heading_deg)
                      and angle_diff_deg(s[3].heading_deg, o.heading_deg) > 150.0), None)
        self.quiet_until[tid] = o.t + self.p["window_s"]
        self.samples[tid].clear()
        if start is None:
            return
        turn = [s for s in q if s[0] >= start.t]
        if any(s[3].zones_of("no_u_turn") for s in turn):
            return  # UTurnMonitor's (D1)
        self.pending[tid] = {"start": start, "turn": turn, "until": o.t + self.p["settle_s"]}

    def _settle(self, tid: int, o: Obs) -> None:
        w = self.pending[tid]
        if o.t > w["until"]:
            del self.pending[tid]
            return
        w["turn"].append((o.t, 0.0, False, o))
        s = w["start"]
        if not on_regular_lane(o) or angle_diff_deg(s.lane.dir_deg, o.lane.dir_deg) <= 150.0:
            return
        del self.pending[tid]
        a, b = s.lane.lane, o.lane.lane
        same_road = (a.road_id == b.road_id) if a.road_id is not None and b.road_id is not None \
            else math.hypot(o.x - s.x, o.y - s.y) <= self.SAME_PLACE_M
        if not same_road:
            return  # e.g. two turns round a block onto a parallel street
        if a.id.split("_p")[0] == b.id.split("_p")[0]:
            return  # the same lane (or a piece of it) bends round: a hairpin (Town03 road 60), not a U-turn
        inside = [x[3] for x in w["turn"] if x[3].lane is not None]
        junction_lanes = {x.lane.lane.id for x in inside if x.lane.lane.junction}
        if inside and len([x for x in inside if x.lane.lane.junction]) / len(inside) >= 0.5:
            globs = self.p.get("no_u_turn_junctions", [])
            tag = "at_prohibited_junction" if any(fnmatch.fnmatchcase(j, g) for j in junction_lanes for g in globs) else None
        elif a.median_left or b.median_left:
            tag = "through_median"
        elif a.left_line in SOLID_LINES or b.left_line in SOLID_LINES:
            tag = "across_solid_line"
        else:
            tag = None
        if tag is None:
            return  # a legal U-turn
        ep = Episode(s.t, s.frame, 0.0, 0.0, 0.0)
        for x in w["turn"]:
            ep.hit(x[3])
        ev = self._open(ep, o)
        ev.lane_id = a.id
        ev.tags.append(tag)
        flips = sum(x[2] for x in w["turn"])
        if flips:
            ev.tags.append("three_point_turn")
            ev.status = "suppressed"
        ev.value = {"turn_deg": round(sum(x[1] for x in w["turn"]), 1), "from_lane": a.id, "to_lane": b.id,
                    "reversals": flips, "junction_lanes": sorted(junction_lanes)}
        ev.close(o.t, o.frame, margin=1.0, quality=ep.quality(), duration_score=1.0)

    def finish(self, t, frame):
        self.pending.clear()


# --- lane violation ---------------------------------------------------------------------------

class LaneViolationMonitor(Monitor):
    """Lane conditions (Expected_Output 4.2 A), each a tag on a lane_violation event:
      straddling (A4)             body >= min_overlap_m over a marked lane line for > straddle_s
      solid_line_crossing (A2)    a completed lane change across a solid / double-solid line
      lane_change_prohibited (A3) a completed lane change to a side the lane's lane_change forbids
      unsafe_lane_change (A8)     after a lane change, TTC < unsafe_ttc_s (or overlap) to the lead or
                                  lag vehicle in the target lane, closing > min_closing_mps
      wrong_lane_for_direction (A1) the lane after a junction can't be reached from the lane before
                                  it in the lane graph ("next" links): it turned from the wrong lane
      restricted_lane (A5)        in a lane configured bus / emergency / restricted, class not exempt
      shoulder_driving (A6)       moving along a shoulder lane for > shoulder_min_s
    Junction lanes are ignored except as the way between two lanes (A1)."""
    type = "lane_violation"

    def __init__(self, log, params, lane_index: dict | None = None):
        super().__init__(log, params)
        self.lane_index = lane_index or {}  # lane id -> Lane, for the lane graph (A1)
        self.straddle: dict[int, Episode] = {}
        self.lane_state: dict[int, dict] = {}  # track -> {lane, since, last_match, cand, cand_since, cand_first}
        self.use: dict[tuple, Episode] = {}  # (track, "restricted" | "shoulder") -> episode
        self.pending: list[dict] = []  # lane changes whose lead / lag gaps are still being watched (A8)

    def step(self, t, frame, obs, hist):
        p = self.p
        for o in obs:
            self._lane_use(o)
            if o.lane is not None and o.lane.lane.junction and o.track_id in self.lane_state:
                self.lane_state[o.track_id]["junction_t"] = o.t  # seen inside a junction (A1)
        for o in obs:
            if not o.visible or not on_regular_lane(o) or o.speed_kmh < p["min_kmh"]:
                continue
            self._lane_change(o, hist)
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
        for key, ep in list(self.use.items()):
            if ep.expired(t):
                del self.use[key]
                self._close_use(ep)
        self._watch_gaps(t, obs)

    def _instant(self, o: Obs, tag: str, value: dict, margin: float = 1.0) -> None:
        """A lane_violation event at one moment (a lane change)."""
        ep = Episode(o.t, o.frame, 0.0, 0.0, 0.0)
        ep.hit(o)
        ev = self._open(ep, o)
        ev.tags.append(tag)
        ev.value = value
        ev.close(o.t, o.frame, margin=margin, quality=ep.quality(), duration_score=1.0)

    def _lane_change(self, o: Obs, hist: TrackHistory) -> None:
        p = self.p
        st = self.lane_state.get(o.track_id)
        if st is None:
            self.lane_state[o.track_id] = {"lane": o.lane.lane.id, "since": o.t, "last": o.lane, "last_t": o.t, "cand": None}
            return
        if o.lane.lane.id == st["lane"] or o.lane.lane.id in st["last"].lane.next or st["lane"] in o.lane.lane.next:
            # same lane, or the next / previous piece of it (lanes are split at speed signs and where
            # they become a bridge): not a lane change
            st.update(lane=o.lane.lane.id, cand=None, last=o.lane, last_t=o.t)
            if not math.isnan(o.heading_deg) and abs(o.lane.d) < o.lane.lane.width / 4:
                st["heading"] = o.heading_deg  # while centred: before it starts to turn or drift out
            return
        if st["cand"] is None or st["cand"] != o.lane.lane.id:
            st.update(cand=o.lane.lane.id, cand_since=o.t, cand_obs=o, prev=st["last"], prev_since=st["since"],
                      prev_t=st["last_t"], prev_heading=st.get("heading"))
            return
        if o.t - st["cand_since"] < p["stable_s"]:
            return
        prev, co = st["prev"], st["cand_obs"]
        stable_before = st["cand_since"] - st["prev_since"] >= p["stable_s"]
        # seen inside a junction in between: it crossed the junction, not changed lanes, however far
        # it had drifted on the way in (Town03 dry run: 1.6 m off-centre at the junction entry)
        crossed_junction = st.get("junction_t", -1e9) > st["prev_t"]
        lateral = abs(prev.d) >= p["exit_frac"] * prev.lane.width and not crossed_junction
        # A3 / A8 are about changing between lanes of one direction; crossing a solid line (A2) counts
        # either way, e.g. over the double solid centre line into the opposing lane
        same_dir = angle_diff_deg(prev.dir_deg, o.lane.dir_deg) < 60.0
        if stable_before and lateral:
            side = "left" if prev.d > 0 else "right"
            line = line_on_side(prev, side)
            base = {"from_lane": prev.lane.id, "to_lane": o.lane.lane.id, "line": line, "side": side}
            # turned round = drove with the old lane and now heads with the opposing new one: a U-turn,
            # judged by the U-turn rules (D2 / D4), not a line crossing too. An overtake out heads
            # against the new lane; coming back from it, the car drove against the old lane: both A2.
            # (Headings relative to each lane: a slow U-turn is only half round when the change is confirmed.)
            ph = st.get("prev_heading")
            turned = (not same_dir and ph is not None and not math.isnan(o.heading_deg)
                      and angle_diff_deg(ph, prev.dir_deg) < 90.0 and angle_diff_deg(o.heading_deg, o.lane.dir_deg) < 90.0)
            if line in SOLID_LINES and not turned:
                self._instant(co, "solid_line_crossing", base)
            elif same_dir and not prev.lane.may_change(side):
                self._instant(co, "lane_change_prohibited", {**base, "lane_change": prev.lane.lane_change})
            if same_dir and co.speed_kmh >= p["unsafe_min_kmh"]:
                self._start_gap_watch(co, prev, o.lane.lane, hist)
        elif (crossed_junction and prev.lane.next
              and p["junction_min_s"] <= st["cand_since"] - st["prev_t"] <= p["junction_max_s"]
              and angle_diff_deg(prev.dir_deg, o.lane.dir_deg) < 150.0):  # turned round: the U-turn rules' case
            # left one lane through a junction and came out on another (A1)
            if not lane_reachable(prev.lane, o.lane.lane, self.lane_index, junction_only=True):
                self._instant(co, "wrong_lane_for_direction",
                              {"from_lane": prev.lane.id, "to_lane": o.lane.lane.id,
                               "from_lane_leads_to": list(prev.lane.next)}, margin=0.8)
        st.update(lane=o.lane.lane.id, since=st["cand_since"], last=o.lane, last_t=o.t, cand=None)

    # --- A5 restricted lane, A6 shoulder driving ----------------------------------------------

    def _lane_use(self, o: Obs) -> None:
        p = self.p
        if not o.visible or o.lane is None or o.lane.lane.junction:
            return
        lane = o.lane.lane
        conds = []
        if lane.restricted and o.cls not in p["restricted_exempt"].get(lane.restricted, []):
            conds.append(("restricted", p["restricted_min_s"]))
        # the centre inside the shoulder, not a car on its lane's edge matched to the shoulder
        if lane.lane_type == "shoulder" and o.speed_kmh >= p["shoulder_min_kmh"] and abs(o.lane.d) <= lane.width / 2:
            conds.append(("shoulder", p["shoulder_min_s"]))
        for kind, min_s in conds:
            ep = self.use.get((o.track_id, kind))
            if ep is None:
                ep = self.use[(o.track_id, kind)] = Episode(o.t, o.frame, min_s, p["confirm_gap_s"], p["close_gap_s"])
                ep.data.update(kind=kind, lane=lane.id, restricted=lane.restricted, max_kmh=0.0)
            ep.hit(o)
            ep.data["max_kmh"] = max(ep.data["max_kmh"], o.speed_kmh)
            if ep.ready():
                self._open(ep, o).tags.append("restricted_lane" if kind == "restricted" else "shoulder_driving")

    def _close_use(self, ep: Episode, at_end: bool = False) -> None:
        ev = ep.event
        if ev is None:
            return
        d = ep.data
        ev.value = {"lane": d["lane"], "duration_s": round(ep.duration, 2), "max_speed_kmh": round(d["max_kmh"], 1)}
        if d["kind"] == "restricted":
            ev.value["restricted"] = d["restricted"]
        if at_end:
            ev.tags.append("ongoing_at_end")
        ev.close(ep.last_true_t, ep.last_true_frame, margin=1.0, quality=ep.quality(),
                 duration_score=ep.duration / max(2 * ep.min_s, 1.0))

    # --- A8 unsafe lane change -----------------------------------------------------------------

    def _start_gap_watch(self, co: Obs, prev: LaneMatch, target: Lane, hist: TrackHistory) -> None:
        w = {"track": co.track_id, "target": target, "from_lane": prev.lane.id, "t0": co.t,
             "until": co.t + self.p["unsafe_window_s"], "first": co, "worst": None, "runs": {}}
        # backfill: the frames since the changer entered the target lane are already in the history
        frames = defaultdict(list)
        for q in hist.obs.values():
            for h in q:
                if h.t >= co.t - 1e-9:
                    frames[h.frame].append(h)
        for f in sorted(frames):
            self._gap_frame(w, frames[f])
        self.pending.append(w)

    def _watch_gaps(self, t: float, obs: list[Obs]) -> None:
        for w in list(self.pending):
            if obs and obs[0].t > w["first"].t + 1e-9 and t <= w["until"]:
                self._gap_frame(w, obs)
            if t > w["until"]:
                self.pending.remove(w)
                self._close_gap_watch(w)

    def _gap_frame(self, w: dict, frame_obs: list[Obs]) -> None:
        ch = next((o for o in frame_obs if o.track_id == w["track"]), None)
        if ch is None or ch.lane is None or ch.frame == w.get("last_frame"):
            return
        w["last_frame"] = ch.frame
        s_ch = along_target(ch, w["target"])
        if s_ch is None:
            return
        v_ch = along_lane_mps(ch)
        nearest = {}  # role -> (|ds|, other): only the vehicle directly ahead / behind in the target lane
        for o in frame_obs:
            if o.track_id == w["track"] or o.lane is None:
                continue
            s_o = along_target(o, w["target"])
            if s_o is None:
                continue
            ds = s_o - s_ch
            role = "lead" if ds > 0 else "lag"
            if role not in nearest or abs(ds) < nearest[role][0]:
                nearest[role] = (abs(ds), o)
        below = set()
        for role, (dist, o) in nearest.items():
            gap = dist - HALF_LENGTH_M.get(ch.cls, 2.3) - HALF_LENGTH_M.get(o.cls, 2.3)
            closing = (v_ch - along_lane_mps(o)) if role == "lead" else (along_lane_mps(o) - v_ch)
            if gap <= 0:
                # alongside: only a conflict if the bodies overlap sideways too, by more than position noise
                # (else side by side in two lanes, or edges just touching as the changer crosses the line)
                reach = HALF_WIDTH_M.get(ch.cls, 0.9) + HALF_WIDTH_M.get(o.cls, 0.9) - self.p["overlap_margin_m"]
                if abs(o.lane.d - ch.lane.d) >= reach:
                    continue
                ttc = 0.0
            elif closing > self.p["min_closing_mps"]:
                ttc = gap / closing
            else:
                continue
            if ttc >= self.p["unsafe_ttc_s"]:
                continue
            key = (role, o.track_id)
            below.add(key)
            since = w["runs"].setdefault(key, ch.t)
            # a TTC must stay below the threshold for min_below_s in a row (SinD 2.0 asks >= 3 frames)
            if ch.t - since >= self.p["min_below_s"] - 1e-9 and (w["worst"] is None or ttc < w["worst"]["ttc"]):
                w["worst"] = {"ttc": ttc, "gap": gap, "closing": closing, "role": role, "partner": o.track_id, "obs": ch}
        for key in [k for k in w["runs"] if k not in below]:
            del w["runs"][key]

    def _close_gap_watch(self, w: dict) -> None:
        worst = w["worst"]
        if worst is None or worst["ttc"] >= self.p["unsafe_ttc_s"]:
            return
        o = worst["obs"]
        ep = Episode(w["t0"], w["first"].frame, 0.0, 0.0, 0.0)
        ep.hit(w["first"])
        ep.hit(o)
        ev = self._open(ep, o)
        ev.tags.append("unsafe_lane_change")
        if worst["ttc"] < self.p["high_risk_ttc_s"]:
            ev.tags.append("high_risk")
        ev.value = {"from_lane": w["from_lane"], "to_lane": w["target"].id, "partner_track": worst["partner"],
                    "partner_role": worst["role"], "min_ttc_s": round(worst["ttc"], 2), "gap_m": round(worst["gap"], 2),
                    "closing_mps": round(worst["closing"], 2)}
        ev.close(o.t, o.frame, margin=(self.p["unsafe_ttc_s"] - worst["ttc"]) / self.p["unsafe_ttc_s"] + 0.5,
                 quality=ep.quality(), duration_score=1.0)

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
        for ep in self.use.values():
            self._close_use(ep, at_end=True)
        self.use.clear()
        for w in self.pending:
            self._close_gap_watch(w)
        self.pending.clear()


# --- red light (optional, CARLA only) -----------------------------------------------------------

class RedLightMonitor(Monitor):
    """The vehicle's front crosses a stop line in the line's approach direction while the line's
    signal is red, and goes on into the junction: its front gets enter_m past the line before
    the vehicle first stops. A car that stops with its nose just over the line is waiting at the
    light, not running it. Crossing on yellow is legal, and a car already past the line when it
    turns red never crosses it on red (PRD 4, edge case 1). One event per track and stop line;
    the value says how long the light had been red at the crossing. Emergency vehicles (PRD edge
    case 2) can be exempted by class, but the detector has no such class: CARLA's oracle run only."""
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
        self.pending: dict[tuple, dict] = {}  # (track, line) -> crossing on red, waiting to enter the junction
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
            self._follow(o)
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
                dirv = ln["dir"] or (move[0] / max(math.hypot(*move), 1e-9), move[1] / max(math.hypot(*move), 1e-9))
                self.pending[key] = {"line": ln, "dir": dirv, "obs": o, "red_for": max(0.0, o.t - self._t_of(st[1], o.t))}
        for key, pd in list(self.pending.items()):  # lost, or never got into the junction in time
            if t - pd["obs"].t > p["confirm_s"]:
                del self.pending[key]

    def _follow(self, o: Obs) -> None:
        """A crossing on red becomes a violation once the front is enter_m past the line; it is
        dropped if the vehicle stops first (waiting at the light with its nose over the line)."""
        p = self.p
        for key in [k for k in self.pending if k[0] == o.track_id]:
            pd = self.pending[key]
            if not o.visible or o.t <= pd["obs"].t - 1e-9:
                continue
            front = front_point(o)
            if o.speed_kmh < p["stop_kmh"] or front is None:
                del self.pending[key]
                continue
            ln, c = pd["line"], pd["obs"]
            past = (front[0] - ln["mid"][0]) * pd["dir"][0] + (front[1] - ln["mid"][1]) * pd["dir"][1]
            if past < p["enter_m"]:
                continue
            del self.pending[key]
            ep = Episode(c.t, c.frame, 0.0, 0.0, 0.0)
            ep.hit(c)
            ep.hit(o)
            ev = self._open(ep, o)  # location = where it is at the flag, like every other event
            ev.value = {"signal_id": ln["signal"], "stop_line": ln["id"], "red_for_s": round(pd["red_for"], 2),
                        "speed_kmh": round(c.speed_kmh, 1), "entered_m": round(past, 1),
                        "crossed_at": [round(c.x, 2), round(c.y, 2)]}
            ev.close(o.t, o.frame, margin=pd["red_for"] / p["sure_after_s"], quality=ep.quality(), duration_score=1.0)


# --- engine -----------------------------------------------------------------------------------

def default_monitors(log: EventLog, params: dict | None = None, stop_lines: list | None = None,
                     signals=None, lane_index: dict | None = None, scene: SceneMap | None = None) -> list[Monitor]:
    from zebra_pedestrians import DEFAULTS as PED_DEFAULTS, pedestrian_monitors  # imports this module: here, not on top
    P = {k: dict(v) for k, v in {**DEFAULTS, **PED_DEFAULTS}.items()}
    for k, v in (params or {}).items():
        P.setdefault(k, {}).update(v)
    pm = {"place_memory": P["place_memory"]}
    monitors = [
        StopInZoneMonitor(log, {**P["no_parking"], **pm}, "no_parking", "no_parking", stay_point=True,
                          queue_exempt=False, shoulder_breakdown=False),
        StopInZoneMonitor(log, {**P["zebra_crossing"], **pm}, "zebra_crossing", "crosswalk", stay_point=True,
                          queue_exempt=True, shoulder_breakdown=False),
        StopInZoneMonitor(log, {**P["highway_stop"], **pm}, "highway_stop", "highway", stay_point=False,
                          queue_exempt=True, shoulder_breakdown=True),
        WrongWayMonitor(log, P["wrong_way"]),
        UTurnMonitor(log, P["illegal_u_turn"]),
        UTurnAnywhereMonitor(log, P["illegal_u_turn"]),
        SpeedingMonitor(log, P["speeding"]),
        LaneViolationMonitor(log, P["lane_violation"], lane_index),
        RedLightMonitor(log, P["red_light"], stop_lines or [], signals),
    ]
    monitors += pedestrian_monitors(log, P, scene)  # F2 / F3 / F5 (Build Plan M3)
    return [m for m in monitors if P[m.type].get("enabled", True)]  # "enabled": false from a profile


class Engine:
    def __init__(self, scene: SceneMap, params: dict | None = None, prefix: str = "ev", signals=None,
                 disabled_conditions: set[str] | None = None):
        """signals: a signals.SignalLog (CARLA only) to enable the red-light rule on the scene's stop lines.
        disabled_conditions: condition ids (schemas/conditions.json) whose events run() drops."""
        self.scene = scene
        self.log = EventLog(prefix)
        self.monitors = default_monitors(self.log, params, scene.stop_lines, signals, scene.lane_by_id, scene)
        self.hist = TrackHistory()
        self.disabled_conditions = set(disabled_conditions or ())
        self._road_zones: dict[str, Zone] = {}
        self._level: dict[int, float] = {}  # track -> road height of its last lane match

    def observe(self, r: dict) -> Obs:
        """A kinematics row (kinematics.py) -> Obs with its lane match and zones."""
        h = r.get("heading_deg", "")
        o = Obs(frame=int(r["frame"]), t=float(r["time_s"]), track_id=int(r["track_id"]), cls=str(r["class"]),
                conf=float(r.get("conf", 1.0)), visible=bool(int(r.get("visible", 1))), x=float(r["x"]), y=float(r["y"]),
                vx=float(r["vx"]), vy=float(r["vy"]), speed_kmh=float(r["speed_kmh"]),
                speed_sigma_kmh=float(r.get("speed_sigma_kmh", 0.0)),
                heading_deg=float(h) if h not in ("", None) and not (isinstance(h, float) and math.isnan(h)) else math.nan)
        # the road level: the row's own height if it has one (oracle: true z), else the level of the
        # track's last match, so a car under a flyover keeps to the lower road
        z = r.get("road_z", "")
        z = float(z) if z not in ("", None) else self._level.get(o.track_id)
        o.lane = self.scene.match(o.x, o.y, z)
        o.drive_lane = o.lane
        if o.lane is not None and not math.isnan(o.heading_deg) and angle_diff_deg(o.heading_deg, o.lane.dir_deg) > 90.0:
            # on a centre line position noise can put a car in the oncoming lane; a real wrong-way
            # driver has no lane its way here and keeps its physical one
            o.drive_lane = self.scene.match_along(o.x, o.y, o.heading_deg, z=z) or o.lane
        if o.lane is not None and o.lane.lane.z is not None:
            self._level[o.track_id] = o.lane.lane.height_at(o.lane.s)
        o.zones = self.scene.zones_at(o.x, o.y)
        rz = self.road_zone(o)
        if rz is not None:
            o.zones.append(rz)
        return o

    def road_zone(self, o: Obs) -> Zone | None:
        """Illegal stopping (B1/B2/B4/B5) reads the road, not a drawn zone (Expected_Output 4.1): a lane
        of a highway-class road, a ramp or merge lane, or a bridge / tunnel acts as one "highway" zone
        per road. A drawn highway zone (site files, older maps) still works and takes precedence."""
        lane = o.lane.lane if o.lane is not None else None
        if lane is None or o.zones_of("highway"):
            return None
        if not ((lane.road_class == "highway" and not lane.junction) or lane.ramp or lane.bridge or lane.tunnel):
            return None
        zid = f"road_{lane.road_id if lane.road_id is not None else lane.id}"
        if zid not in self._road_zones:
            self._road_zones[zid] = Zone(zid, "highway", None, {"source": "lane_properties"})
        return self._road_zones[zid]

    def step(self, t: float, frame: int, obs: list[Obs]) -> None:
        # people (M3) go only to monitors that ask for them, and stay out of the vehicle history
        vehicles = [o for o in obs if o.cls not in PEOPLE_CLASSES]
        for o in vehicles:
            self.hist.add(o)
        for m in self.monitors:
            m.step(t, frame, obs if getattr(m, "sees_people", False) else vehicles, self.hist)

    def run(self, kin_rows: list[dict]) -> list[Event]:
        by_frame = defaultdict(list)
        for r in kin_rows:
            by_frame[(float(r["time_s"]), int(r["frame"]))].append(r)
        t = frame = 0
        for (t, frame) in sorted(by_frame):
            self.step(t, frame, [self.observe(r) for r in by_frame[(t, frame)]])
        for m in self.monitors:
            m.finish(t, frame)
        for e in self.log.events:  # tags such as possible_breakdown are only final once closed
            e.condition = condition_of({"type": e.type, "tags": e.tags, "value": e.value})
        return [e for e in self.log.events if e.condition not in self.disabled_conditions]
