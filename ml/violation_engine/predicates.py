"""Shared building blocks of the violation rules (Violation Engine, Layer 5).

docs/Violation_Engine_Architecture.md, Section 5.5. Every violation monitor in rules.py is
a few of these predicates plus a time condition, so the same tested pieces serve all
violation types (and later ones).

Obs is one vehicle in one frame, after kinematics (kinematics.py) and map matching
(lane_map.py). TrackHistory keeps the last few seconds of every track for the
predicates that look back in time (stopped).
"""

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np

from lane_map import LaneMatch, Zone, angle_diff_deg

STOP_WINDOW_S = 3.0  # stay-point: all positions of the last 3 s ...
STOP_RADIUS_M = 1.0  # ... within 1 m of their centre
STOP_MIN_AGE_S = 1.0  # a younger track is tested over its whole life (>= 1 s)
QUEUE_AHEAD_M = 10.0  # a stopped vehicle this far ahead in the same lane = queue
QUEUE_NEAR_M = 8.0  # without a lane: any stopped vehicle this close
QUEUE_SLOW_KMH = 5.0
HISTORY_S = 10.0
HALF_WIDTH_M = {"car": 0.9, "van": 1.0, "truck": 1.25, "bus": 1.25}  # vehicle half-width for footprint tests
HALF_LENGTH_M = {"car": 2.3, "van": 2.5, "truck": 4.0, "bus": 6.0}  # centre to front bumper (red light)
UNMARKED_LINES = {"none", "curb", "grass", "other", ""}


@dataclass
class Obs:
    frame: int
    t: float
    track_id: int
    cls: str
    conf: float
    visible: bool
    x: float
    y: float
    vx: float
    vy: float
    speed_kmh: float
    speed_sigma_kmh: float
    heading_deg: float  # NaN below kinematics.HEADING_MIN_KMH
    lane: LaneMatch | None = None  # the lane it physically lies in (position only: wrong-way needs that)
    zones: list[Zone] = field(default_factory=list)
    drive_lane: LaneMatch | None = None  # the lane it drives in: `lane`, unless that one runs against its heading

    def zones_of(self, ztype: str) -> list[Zone]:
        return [z for z in self.zones if z.type == ztype]


class TrackHistory:
    """The last HISTORY_S seconds of observations per track, plus when each track was last seen."""

    def __init__(self):
        self.obs: dict[int, deque] = defaultdict(deque)
        self.last_seen: dict[int, float] = {}

    def add(self, o: Obs) -> None:
        q = self.obs[o.track_id]
        q.append(o)
        while q and q[0].t < o.t - HISTORY_S:
            q.popleft()
        self.last_seen[o.track_id] = o.t

    def previous(self, o: Obs) -> Obs | None:
        q = self.obs.get(o.track_id)
        return q[-2] if q is not None and len(q) >= 2 and q[-1] is o else None


# --- predicates on one observation -------------------------------------------------------

def stopped(hist: TrackHistory, o: Obs, window_s: float = STOP_WINDOW_S, radius_m: float = STOP_RADIUS_M,
            min_age_s: float = STOP_MIN_AGE_S) -> bool:
    """Stay-point test: every position of the last window_s (or of the whole track, if it is
    younger but at least min_age_s old) lies within radius_m of their centre. Robust to box
    jitter, unlike a speed test. The shorter window lets fragmented tracks of a parked car
    count; the stop-type monitors add the fragments up per spot."""
    q = hist.obs.get(o.track_id)
    if not q or o.t - q[0].t < min_age_s - 1e-6:
        return False
    pts = np.array([(p.x, p.y) for p in q if p.t >= o.t - window_s])
    c = pts.mean(axis=0)
    return bool(np.max(np.hypot(*(pts - c).T)) <= radius_m)


def slow(o: Obs, max_kmh: float) -> bool:
    return o.speed_kmh < max_kmh


def on_regular_lane(o: Obs, lane_types: tuple = ("driving",)) -> bool:
    """Matched to a lane that is not inside a junction (where turning legally looks like anything)."""
    return o.lane is not None and not o.lane.lane.junction and o.lane.lane.lane_type in lane_types


def against_lane_angle(o: Obs) -> float | None:
    """Angle between the vehicle's motion and its lane's direction (0 = with, 180 = against)."""
    if o.lane is None or math.isnan(o.heading_deg):
        return None
    return angle_diff_deg(o.heading_deg, o.lane.dir_deg)


def along_lane_mps(o: Obs) -> float:
    """Velocity component along the lane direction (m/s, negative = backwards)."""
    return float(o.vx * o.lane.dir[0] + o.vy * o.lane.dir[1]) if o.lane is not None else 0.0


def straddle_overlap(o: Obs) -> tuple[float, str]:
    """How far (m) the vehicle's body reaches over its lane's edge line, and which side."""
    if o.lane is None:
        return 0.0, ""
    half = HALF_WIDTH_M.get(o.cls, 0.9)
    over = abs(o.lane.d) + half - o.lane.lane.width / 2
    return over, ("left" if o.lane.d > 0 else "right")


def line_on_side(lane_match: LaneMatch, side: str) -> str:
    return lane_match.lane.left_line if side == "left" else lane_match.lane.right_line


def along_target(o: Obs, target) -> float | None:
    """o's distance along lane `target` (metres from its first point), when o is in that lane or in
    the lane piece just before / after it in the lane graph; else None. For gaps in a target lane."""
    lane = o.lane.lane if o.lane is not None else None
    if lane is None:
        return None
    if lane.id == target.id:
        return o.lane.s
    if lane.id in target.next:
        return target.length + o.lane.s
    if target.id in lane.next:
        return o.lane.s - lane.length
    return None


def lane_reachable(a, b, lanes_by_id: dict, max_hops: int = 8, max_m: float = 400.0) -> bool:
    """Can a vehicle on lane a reach lane b by following the lane graph ("next" links), within
    max_hops lanes and max_m metres? Lanes without links (sites) count as reachable: unknown."""
    if a.id == b.id or not a.next:
        return True
    frontier, seen = [(a, 0.0)], {a.id}
    for _ in range(max_hops):
        nf = []
        for lane, dist in frontier:
            for nid in lane.next:
                if nid == b.id:
                    return True
                n = lanes_by_id.get(nid)
                if n is None or nid in seen or dist + n.length > max_m:
                    continue
                seen.add(nid)
                nf.append((n, dist + n.length))
        frontier = nf
    return False


def queue_context(o: Obs, others: list[Obs]) -> bool:
    """Is this stop explained by traffic? True if a slow vehicle is directly ahead in the same
    lane (within QUEUE_AHEAD_M), or - without a lane - any slow vehicle within QUEUE_NEAR_M."""
    for p in others:
        if p.track_id == o.track_id or p.speed_kmh >= QUEUE_SLOW_KMH:
            continue
        if o.lane is not None and p.lane is not None and p.lane.lane.id == o.lane.lane.id:
            if 0.0 < p.lane.s - o.lane.s <= QUEUE_AHEAD_M:
                return True
        elif o.lane is None and math.hypot(p.x - o.x, p.y - o.y) <= QUEUE_NEAR_M:
            return True
    return False


def front_point(o: Obs) -> tuple[float, float] | None:
    """Front bumper position: centre + half the vehicle length along its motion (None when too slow
    to have a direction)."""
    v = math.hypot(o.vx, o.vy)
    if v < 0.5:
        return None
    half = HALF_LENGTH_M.get(o.cls, 2.3)
    return o.x + half * o.vx / v, o.y + half * o.vy / v


def segments_cross(p, q, a, b) -> bool:
    """Does segment p-q cross segment a-b (touching counts once: the end point q may lie on a-b)?"""
    def orient(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])
    d1, d2 = orient(a, b, p), orient(a, b, q)
    d3, d4 = orient(p, q, a), orient(p, q, b)
    return ((d1 > 0) != (d2 > 0) or d2 == 0) and d1 != 0 and (d3 > 0) != (d4 > 0)


def speed_tolerance_kmh(limit_kmh: float, mode: str = "eu") -> float:
    """Enforcement tolerance: EU practice 5 km/h below 100 km/h, 5% above; 'none' = 0."""
    if mode == "none":
        return 0.0
    return 5.0 if limit_kmh < 100 else 0.05 * limit_kmh


# --- time condition ------------------------------------------------------------------------

class Episode:
    """A condition that must hold for min_s before it counts (pending -> confirmed), with short
    interruptions tolerated: up to confirm_gap_s while pending, close_gap_s once confirmed."""

    def __init__(self, t: float, frame: int, min_s: float, confirm_gap_s: float, close_gap_s: float):
        self.start_t, self.start_frame = t, frame
        self.last_true_t, self.last_true_frame = t, frame
        self.min_s, self.confirm_gap_s, self.close_gap_s = min_s, confirm_gap_s, close_gap_s
        self.event = None  # set by the monitor when confirmed
        self.n = 0
        self.conf_sum = 0.0
        self.n_visible = 0
        self.data: dict = {}

    def hit(self, o: Obs) -> None:
        self.last_true_t, self.last_true_frame = o.t, o.frame
        self.n += 1
        self.conf_sum += o.conf
        self.n_visible += int(o.visible)

    @property
    def duration(self) -> float:
        return self.last_true_t - self.start_t

    @property
    def confirmed(self) -> bool:
        return self.event is not None

    def ready(self) -> bool:
        return self.event is None and self.duration >= self.min_s - 1e-9

    def expired(self, t: float) -> bool:
        return t - self.last_true_t > (self.close_gap_s if self.confirmed else self.confirm_gap_s)

    def quality(self) -> float:
        """Track quality 0..1: mean detector confidence x share of frames with a full box."""
        return (self.conf_sum / self.n) * (self.n_visible / self.n) if self.n else 0.0
