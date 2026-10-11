"""Virtual traffic: a small microscopic simulator on the scene's lane map (Scenario_Builder_Plan.md, "virtual traffic").

Why it exists: the violation-engine replay only judges traffic that was recorded, so vehicles a planner adds (a spawned
group, a continuous flow, a jam, an accident) never moved. This runs vehicles along the lanes so they do, and so a plan can
be compared with the baseline on speed, queue, travel time, delay and throughput. It is NOT CARLA: a simplified model,
labelled "virtual simulation (simplified model, not CARLA)" wherever its numbers are shown.

Model (kept deliberately plain, all of it listed in `MODEL_NOTES`):
    lanes       the scene's driving lanes and their successor links (`next`); a vehicle follows one lane at a time
    following   Intelligent Driver Model (Treiber et al. 2000), one leader per vehicle, looked up along the route
    speeds      desired speed = lane limit x a per-vehicle factor, capped by the vehicle type
    routes      random walk over successors (or the shortest path to a destination), avoiding closed lanes
    no          lane changes, overtaking, junction conflicts (vehicles pass through each other in junctions), signals
Plans act through the compiled lane overrides (speed limit, restricted / closed lanes) and the traffic scenarios
(spawn group, flow, density / demand factor, jam, accident, breakdown, emergency vehicle, weather factor).

Reproducible: numpy RandomState(seed), fixed step. `compare` runs baseline and plan over several seeds and gives mean and
a 95 % interval (Student t) per metric.
"""

import fnmatch
import heapq
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for q in (HERE, REPO / "simulation" / "planning"):
    if str(q) not in sys.path:
        sys.path.insert(0, str(q))

import traffic_config as tc  # noqa: E402  (global traffic settings: presets, profiles, behaviour)

MODEL_NOTES = [
    "Simplified microscopic model (IDM car-following on the lane graph); not CARLA and not calibrated to this town's traffic.",
    "No lane changes or overtaking: a slow vehicle holds up the vehicles behind it.",
    "Signals are virtual: a plan's signal props stop vehicles on the lanes passing them (two phases with clearance, optional adaptive extension, stop signs); the town's own signals are not in the lane map. Turning movements are not controlled and vehicles still pass through each other inside junctions.",
    "Background traffic keeps a constant number of vehicles per km of lane (vehicles that leave the network are respawned elsewhere).",
    "Pedestrians exist only as crossing activity at pedestrian crossings (vehicles yield while someone is on the crossing); other pedestrian crowds are not simulated. Closures, barriers, cones and construction zones block the lane; accidents, breakdowns, custom objects and curb stops are stopped vehicles.",
]

# type: length m, width m, max accel m/s2, comfortable decel m/s2, time headway s, min gap m, max speed km/h, share of background
TYPES = {
    "car": (4.5, 1.9, 2.0, 3.0, 1.4, 2.0, 140, 0.84), "van": (5.3, 2.0, 1.6, 3.0, 1.5, 2.2, 110, 0.06),
    "truck": (9.0, 2.5, 1.1, 2.5, 1.8, 2.5, 80, 0.05), "bus": (11.0, 2.5, 1.2, 2.5, 1.6, 2.5, 70, 0.03),
    "motorcycle": (2.2, 0.8, 3.0, 4.0, 1.1, 1.5, 120, 0.0), "ambulance": (5.5, 2.1, 2.5, 4.0, 1.0, 2.0, 130, 0.0),
    "police": (5.0, 2.0, 2.5, 4.0, 1.0, 2.0, 130, 0.0), "fire": (9.5, 2.5, 1.5, 3.0, 1.2, 2.5, 100, 0.0),
}
WEATHER = {"clear": 1.0, "cloudy": 1.0, "rain": 0.92, "heavy_rain": 0.8, "fog": 0.75, "night": 0.92}  # desired-speed factor
EMERGENCY = {"ambulance", "police", "fire"}
VEH_COLOR = {"car": 0, "van": 1, "truck": 2, "bus": 3, "motorcycle": 4, "ambulance": 5, "police": 5, "fire": 5}
T_975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23, 11: 2.20, 12: 2.18, 13: 2.16, 14: 2.14, 15: 2.13,
         16: 2.12, 17: 2.11, 18: 2.10, 19: 2.09, 20: 2.09, 24: 2.06, 29: 2.05}
BG_TYPES = [(k, v[7]) for k, v in TYPES.items() if v[7] > 0]
LOOKAHEAD_M = 120.0
DEFAULTS = {"duration_s": 120.0, "warmup_s": 20.0, "dt": 0.2, "density_per_km": 7.0, "seeds": 5}


class Lane:
    __slots__ = ("i", "id", "xs", "ys", "cum", "length", "limit", "next", "junction", "closed", "allowed", "cap", "road", "vehs", "rclass", "segs")

    def __init__(self, i, d):
        pts = np.array(d["centreline"], dtype=float)
        seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
        self.i, self.id = i, d["id"]
        self.xs, self.ys = pts[:, 0], pts[:, 1]
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.length = float(self.cum[-1])
        self.limit = float(d.get("speed_limit_kmh") or 50.0)
        self.next: list[int] = []
        self.junction = bool(d.get("junction"))
        self.closed = False
        self.allowed: set[str] | None = None  # None = every vehicle type
        self.cap: float | None = None          # speed cap km/h on the whole lane
        self.segs: list[tuple[float, float, float, float | None]] = []  # (s0, s1, speed multiplier, cap km/h): warning signs, speed breakers
        self.road = d.get("road_id")
        self.rclass = "highway" if d.get("road_class") == "highway" else "urban"
        self.vehs: list["Veh"] = []

    def pos(self, s: float):
        s = min(max(s, 0.0), self.length)
        return float(np.interp(s, self.cum, self.xs)), float(np.interp(s, self.cum, self.ys))

    def heading(self, s: float) -> float:
        a, b = self.pos(max(s - 1.0, 0.0)), self.pos(min(s + 1.0, self.length))
        return math.atan2(b[1] - a[1], b[0] - a[0])

    def limit_at(self, s: float) -> float:
        """Speed limit (km/h) at arc length s with the lane cap and the sign / breaker segments applied."""
        lim = min(self.limit, self.cap) if self.cap else self.limit
        for s0, s1, mult, cap in self.segs:
            if s0 <= s <= s1:
                lim = min(lim * mult, cap if cap else lim)
        return lim

    def nearest_s(self, x: float, y: float):
        d = np.hypot(self.xs - x, self.ys - y)
        k = int(np.argmin(d))
        # refine on the polyline segments around the closest vertex
        best = (float(d[k]), float(self.cum[k]))
        for j in (k - 1, k):
            if 0 <= j < len(self.xs) - 1:
                ax, ay, bx, by = self.xs[j], self.ys[j], self.xs[j + 1], self.ys[j + 1]
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else min(max(((x - ax) * dx + (y - ay) * dy) / L2, 0.0), 1.0)
                px, py = ax + t * dx, ay + t * dy
                dist = math.hypot(x - px, y - py)
                if dist < best[0]:
                    best = (dist, float(self.cum[j] + t * math.sqrt(L2)))
        return best  # (distance, s)


class Veh:
    __slots__ = ("id", "kind", "lane", "s", "v", "L", "a", "b", "T", "s0", "v0", "vmax", "route", "dest", "t0", "user", "obstacle",
                 "until", "x0", "meter", "exited", "dist", "delay", "stopped_t", "group", "retire", "behav", "hold")

    def __init__(self, vid, kind, lane, s, v, rng, speed_mult=1.0, user=False, behaviour="normal", sf=None):
        L, _w, a, b, T, s0, vmax, _ = TYPES[kind]
        bh = tc.BEHAVIOUR.get(behaviour, tc.BEHAVIOUR["normal"])
        self.id, self.kind, self.lane, self.s, self.v = vid, kind, lane, s, v
        self.L, self.vmax = L, vmax / 3.6
        self.a, self.b, self.T, self.s0 = a * bh["a"], b * bh["b"], T * bh["T"], s0 * bh["s0"]
        f = (float(np.clip(rng.normal(1.0, 0.1), 0.8, 1.2)) if sf is None else sf) * bh["speed"] * (1.25 if kind in EMERGENCY else 1.0)
        self.v0 = f * speed_mult
        self.retire = False
        self.behav = behaviour
        self.hold: dict[int, float] = {}  # stop sign id -> time the vehicle came to a stop there (or -1 when done)
        self.route: list[int] = []
        self.dest: tuple[int, float] | None = None
        self.t0 = 0.0
        self.user = user
        self.obstacle = False
        self.until = math.inf
        self.exited = None
        self.dist = 0.0
        self.delay = 0.0
        self.group = None


CLEARANCE_S = 2.0       # all-red between the phases
STOP_HOLD_S = 3.0       # a vehicle waits this long at a stop sign
MAX_EXTENSION_S = 12.0  # an adaptive signal extends a green by at most this
SIGNAL_REACH_M = 14.0   # lanes passing within this distance of the signal position are controlled
STOP_BACK_M = 3.0       # the stop line is this far before the signal position


class Signal:
    """A virtual signal (or stop sign) at a position: its controlled lanes, each with a stop line and a phase group.
    Phases: A (the first lane's axis and the opposite direction), clearance, B (the crossing axis), clearance. Modes: normal,
    adaptive (extends a green while vehicles are approaching), off / flashing (no control), all_red, long_green (A always green),
    stop (a stop sign: every vehicle stops and waits STOP_HOLD_S)."""

    def __init__(self, sid: str, x: float, y: float, kind: str, cycle: float = 90.0, green: float = 40.0, adaptive: bool = False,
                 kinds: set[str] | None = None, hold_s: float = STOP_HOLD_S):
        self.id, self.x, self.y, self.kind = sid, x, y, kind
        self.cycle, self.green = float(cycle), float(green)
        self.mode = {"stop_sign": "stop", "bus_stop": "stop", "crossing": "crossing"}.get(kind) or ("adaptive" if adaptive else "normal")
        self.kinds = kinds              # vehicle types it applies to (bus stop: buses only); None = all
        self.hold_s = hold_s
        self.intervals: list[tuple[float, float]] = []  # pedestrian crossing: when pedestrians are on the crossing
        self.ip = 0
        self.active = False
        self.peds = 0
        self.heads: dict[int, tuple[float, str]] = {}  # lane index -> (stop line s, group A / B)
        self.state = "A"
        self.until = 0.0
        self.ext = 0.0
        self.log: list[tuple[float, str]] = []

    def b_green(self) -> float:
        return max(self.cycle - self.green - 2 * CLEARANCE_S, 5.0)

    def phase(self) -> str | None:
        """'A' / 'B' green group, None during clearance or all-red; 'ALL' = uncontrolled."""
        if self.mode in ("off", "flashing"):
            return "ALL"
        if self.mode == "all_red":
            return None
        if self.mode == "crossing":
            return None if self.active else "ALL"
        if self.mode == "long_green":
            return "A"
        return self.state if self.state in ("A", "B") else None

    def start(self, t: float):
        self.state, self.until, self.ext = "A", t + self.green, 0.0
        self.log.append((t, "A"))

    def update(self, t: float, world: "World"):
        if self.mode == "crossing":
            while self.ip < len(self.intervals) and t >= self.intervals[self.ip][1]:
                self.ip += 1
            now = self.ip < len(self.intervals) and self.intervals[self.ip][0] <= t < self.intervals[self.ip][1]
            if not self.log or now != self.active:
                self.active = now
                self.log.append((t, "R" if now else "ALL"))
            return
        if self.mode in ("off", "flashing", "stop", "all_red", "long_green"):
            if not self.log:
                self.log.append((t, {"off": "ALL", "flashing": "ALL", "stop": "ALL", "all_red": "R", "long_green": "A"}[self.mode]))
            return
        if not self.log:
            self.start(t)
        if t < self.until:
            return
        if self.mode == "adaptive" and self.state in ("A", "B") and self.ext < MAX_EXTENSION_S and world.demand_upstream(self, self.state):
            self.until += 1.0
            self.ext += 1.0
            return
        nxt = {"A": ("clr1", CLEARANCE_S), "clr1": ("B", self.b_green()), "B": ("clr2", CLEARANCE_S), "clr2": ("A", self.green)}[self.state]
        self.state, self.until, self.ext = nxt[0], t + nxt[1], 0.0
        self.log.append((t, self.state if self.state in ("A", "B") else "R"))


class World:
    def __init__(self, scene: dict, lane_patch: list[dict] | None = None, seed: int = 0, cfg: dict | None = None):
        self.rng = np.random.RandomState(seed)
        self.cfg = tc.normalize(cfg)
        self.spawned: dict[str, int] = {}
        self.stops = {"made": 0, "prevented": 0, "in_parking_zones": 0}
        self.no_stop_zones: list[tuple[Any, float]] = []   # (polygon, grace_s): a stop longer than the grace is not made there
        self.parking_zones: list[tuple[Any, float, float, tuple[float, float]]] = []  # (polygon, stops per hour, stay s, centre)
        self.signals: list[Signal] = []
        self.by_lane_signals: dict[int, list[Signal]] = {}
        self.bias_zones: list[tuple[float, float, float, dict]] = []  # (x, y, radius, vehicle mix) inside which new vehicles follow the mix
        drv = [l for l in scene.get("lanes", []) if (l.get("lane_type") or "driving") == "driving" and len(l.get("centreline", [])) >= 2]
        self.lanes = [Lane(i, d) for i, d in enumerate(drv)]
        self.idx = {l.id: l.i for l in self.lanes}
        for l, d in zip(self.lanes, drv):
            l.next = [self.idx[n] for n in d.get("next") or [] if n in self.idx]
        self.vehs: list[Veh] = []
        self.t = 0.0
        self.nid = 1
        self.log: list[tuple[float, list]] = []
        self.exits: list[dict] = []
        self.spawn_fail = 0
        self.apply_overrides(lane_patch or [])
        self.weather = 1.0

    # ------------------------------------------------------------- plan -> lanes
    def apply_overrides(self, patch: list[dict]):
        for o in patch:
            hit = [l for l in self.lanes if fnmatch.fnmatchcase(l.id, o["lane_id"])]
            for l in hit:
                if o.get("speed_limit_kmh") is not None:
                    l.limit = float(o["speed_limit_kmh"])
                r = o.get("restricted")
                if r == "restricted":
                    l.closed = True
                elif r in ("bus", "emergency"):
                    l.allowed = {"bus", "ambulance", "police", "fire"} if r == "bus" else set(EMERGENCY)
                elif "restricted" in o and r is None:
                    l.allowed, l.closed = None, False

    def nearest_lane(self, x, y, max_d=25.0, kinds=None):
        best = None
        for l in self.lanes:
            if l.closed or l.junction and False:
                continue
            if kinds and l.allowed is not None and not (l.allowed & set(kinds)):
                continue
            d, s = l.nearest_s(x, y)
            if d <= max_d and (best is None or d < best[0]):
                best = (d, l, s)
        return best

    # ------------------------------------------------------------- routing
    def can_enter(self, lane: Lane, kind: str) -> bool:
        return not lane.closed and (lane.allowed is None or kind in lane.allowed)

    def pick_next(self, v: Veh, lane: Lane) -> int | None:
        opts = [n for n in lane.next if self.can_enter(self.lanes[n], v.kind)]
        if not opts:
            return None
        live = [n for n in opts if self.lanes[n].next] or opts  # avoid dead ends when there is a choice
        return int(live[self.rng.randint(len(live))])

    def shortest_route(self, src: int, dst: int, kind: str) -> list[int] | None:
        if src == dst:
            return [dst]
        dist = {src: 0.0}
        prev = {}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst:
                break
            if d > dist.get(u, math.inf):
                continue
            for n in self.lanes[u].next:
                ln = self.lanes[n]
                if not self.can_enter(ln, kind):
                    continue
                nd = d + ln.length / max(ln.limit, 5.0)
                if nd < dist.get(n, math.inf):
                    dist[n], prev[n] = nd, u
                    heapq.heappush(pq, (nd, n))
        if dst not in prev:
            return None
        path, u = [dst], dst
        while u != src:
            u = prev[u]
            path.append(u)
        return path[::-1][1:]

    def blocked_ahead(self, v: Veh, lane: Lane) -> bool:
        """The lane has successors but none this vehicle may enter (a closure ahead), as opposed to a dead end."""
        return bool(lane.next) and not any(self.can_enter(self.lanes[n], v.kind) for n in lane.next)

    def extend_route(self, v: Veh):
        """Keep two lanes of route ahead of the vehicle (for the leader look-up and the turn choice)."""
        cur = v.route[-1] if v.route else v.lane
        while len(v.route) < 2:
            n = self.pick_next(v, self.lanes[cur])
            if n is None:
                break
            v.route.append(n)
            cur = n

    # ------------------------------------------------------------- spawning
    def free_at(self, lane: Lane, s: float, length: float, gap: float = 6.0) -> bool:
        for o in lane.vehs:
            if abs(o.s - s) < (o.L + length) / 2 + gap:
                return False
        return True

    # ------------------------------------------------------------- driver / vehicle draws
    def pick_behaviour(self, name: str | None = None) -> str:
        if name in tc.BEHAVIOUR:
            return name
        return tc.pick_weighted(self.rng, self.cfg["background"]["behaviour_mix"]) if sum(self.cfg["background"]["behaviour_mix"].values()) > 0 else "normal"

    def bg_speed_factor(self) -> float:
        bg = self.cfg["background"]
        return float(np.clip(self.rng.normal(bg["speed_factor_mean"], bg["speed_factor_sd"]), 0.5, 1.6))

    def pick_type(self, x: float | None = None, y: float | None = None, weights: dict | None = None) -> str:
        if weights is None and x is not None:
            for bx, by, br, mix in self.bias_zones:
                if math.hypot(x - bx, y - by) <= br:
                    weights = mix
                    break
        w = weights or self.cfg["background"]["mix"]
        return tc.pick_weighted(self.rng, w) if sum(w.values()) > 0 else "car"

    def build_route(self, src: int, kind: str, dest_xy, via_xy):
        """(route, dest) through the waypoint and to the destination (shortest paths); (None, None) when a leg has no route."""
        route, cur, dest = [], src, None
        for pt, final in ((via_xy, False), (dest_xy, True)):
            if pt is None:
                continue
            nl = self.nearest_lane(pt[0], pt[1], 60.0, [kind])
            if not nl:
                continue
            leg = self.shortest_route(cur, nl[1].i, kind)
            if leg is None:
                return None, None
            route += leg
            cur = nl[1].i
            if final:
                dest = (nl[1].i, nl[2])
        return (route or None), dest

    def spawn(self, kind: str, lane: Lane, s: float, v: float, user=False, dest_xy=None, speed_mult=1.0, group=None,
              behaviour: str | None = None, sf: float | None = None, via_xy=None) -> Veh | None:
        if not self.can_enter(lane, kind) and not user:
            return None
        if not self.free_at(lane, s, TYPES[kind][0]):
            self.spawn_fail += 1
            return None
        veh = Veh(self.nid, kind, lane.i, s, v, self.rng, speed_mult, user, self.pick_behaviour(behaviour), sf)
        self.nid += 1
        self.spawned[kind] = self.spawned.get(kind, 0) + 1
        veh.t0, veh.group = self.t, group
        if dest_xy is not None or via_xy is not None:
            route, dest = self.build_route(lane.i, kind, dest_xy, via_xy)
            if route:
                veh.route, veh.dest = route, dest
        if veh.dest is None and not veh.route:
            self.extend_route(veh)
        lane.vehs.append(veh)
        lane.vehs.sort(key=lambda o: o.s)
        self.vehs.append(veh)
        return veh

    def bg_lanes(self):
        """Lanes background traffic may use and their draw weights (length x road-class weight)."""
        rw = self.cfg["background"]["road_weight"]
        lanes = [l for l in self.lanes if not l.junction and not l.closed and l.length > 15 and l.allowed is None]
        w = np.array([l.length * rw.get(l.rclass, 1.0) for l in lanes], dtype=float)
        return lanes, w

    def background_total(self) -> int:
        """Vehicles the background density asks for at factor 1 (density x weighted lane km)."""
        bg = self.cfg["background"]
        if not bg["enabled"]:
            return 0
        lanes, w = self.bg_lanes()
        return int(round(bg["density_per_km"] * float(w.sum()) / 1000.0))

    def spawn_random_background(self, kind: str | None = None) -> Veh | None:
        lanes, w = self.bg_lanes()
        if not lanes or w.sum() <= 0:
            return None
        p = w / w.sum()
        for _ in range(12):
            l = lanes[int(self.rng.choice(len(lanes), p=p))]
            s = float(self.rng.uniform(5.0, max(l.length - 5.0, 6.0)))
            x, y = l.pos(s)
            k = kind or self.pick_type(x, y)
            v = self.spawn(k, l, s, l.limit / 3.6 * 0.6, sf=self.bg_speed_factor())
            if v is not None:
                return v
        return None

    def populate(self, density_per_km: float, centre=None, radius=None, factor=1.0, mix: dict | None = None, group=None):
        """Background vehicles: density_per_km x factor per km of lane (inside the radius when given), drawn from the config's mix."""
        lanes, w = self.bg_lanes()
        if centre is not None:
            keep = [i for i, l in enumerate(lanes) if l.nearest_s(centre[0], centre[1])[0] <= radius]
            lanes, w = [lanes[i] for i in keep], w[keep] if keep else np.array([])
        if not lanes or w.sum() <= 0:
            return 0
        n = int(round(density_per_km * factor * float(w.sum()) / 1000.0))
        p = w / w.sum()
        made = 0
        for _ in range(n * 3):
            if made >= n:
                break
            l = lanes[int(self.rng.choice(len(lanes), p=p))]
            if l.length < 12:
                continue
            s = float(self.rng.uniform(4.0, l.length - 4.0))
            k = self.pick_type(weights=mix)
            if self.spawn(k, l, s, l.limit / 3.6 * 0.7, sf=self.bg_speed_factor(), group=group) is not None:
                made += 1
        return made

    def retire_some(self, n: int):
        """Mark `n` background vehicles to leave at the end of their lane (the demand profile is falling)."""
        pool = [v for v in self.vehs if not v.user and not v.obstacle and v.group is None and not v.retire]
        for i in self.rng.permutation(len(pool))[:n]:
            pool[int(i)].retire = True

    def try_stop(self, x: float, y: float, dur: float, kind: str, in_zone: bool) -> bool:
        """A curb stop; refused (counted as prevented) inside a no-parking zone when it lasts longer than the zone's grace."""
        from shapely.geometry import Point as _Pt
        pt = _Pt(x, y)
        if any(poly.contains(pt) and dur > grace for poly, grace in self.no_stop_zones):
            self.stops["prevented"] += 1
            return False
        ok = self.stop_vehicle(x, y, dur, kind)
        if ok:
            self.stops["made"] += 1
            self.stops["in_parking_zones"] += int(in_zone)
        return ok

    def stop_vehicle(self, x: float, y: float, dur: float, kind: str = "car") -> bool:
        """A vehicle stops in the lane at (x, y) for `dur` s (a curb stop, double parking): it blocks the lane behind it."""
        return self.obstacle(x, y, self.t + dur, kind, 1) > 0

    def obstacle(self, x: float, y: float, until: float, kind="car", n=1, spacing=7.0) -> int:
        """Stopped vehicle(s) in the lane nearest to (x, y), `n` in a row backwards along the lane."""
        near = self.nearest_lane(x, y, 20.0)
        if not near:
            return 0
        _, lane, s = near
        made = 0
        for k in range(n):
            ss = s - k * spacing
            lane_k = lane
            if ss < 1.0:
                break
            ob = Veh(self.nid, kind, lane_k.i, ss, 0.0, self.rng)
            self.nid += 1
            ob.obstacle, ob.until, ob.t0 = True, until, self.t
            lane_k.vehs.append(ob)
            lane_k.vehs.sort(key=lambda o: o.s)
            self.vehs.append(ob)
            made += 1
        return made

    # ------------------------------------------------------------- signals
    def add_signal(self, sid: str, x: float, y: float, kind: str, cycle=90.0, green=40.0, adaptive=False, kinds=None, hold_s=STOP_HOLD_S,
                   ped_per_h: float = 0.0, cross_s: float = 9.0, horizon: float = 400.0) -> Signal | None:
        """Control the lanes that pass within SIGNAL_REACH_M of (x, y) with a stop line before the position; None when no lane does."""
        cand = []
        for l in self.lanes:
            if l.closed:
                continue
            d, s = l.nearest_s(x, y)
            if d <= SIGNAL_REACH_M and s - STOP_BACK_M >= 4.0:
                ang = math.degrees(l.heading(s)) % 180.0
                cand.append((d, l, s, ang))
        if not cand:
            return None
        ref = min(cand, key=lambda c: c[0])[3]
        sig = Signal(sid, x, y, kind, cycle, green, adaptive, kinds, hold_s)
        for d, l, s, ang in cand:
            diff = abs((ang - ref + 90.0) % 180.0 - 90.0)
            sig.heads[l.i] = (s - STOP_BACK_M, "A" if (kind != "traffic_signal" or diff < 40.0) else "B")
        if kind == "crossing" and ped_per_h > 0:
            t, ivs = 0.0, []
            while t < horizon:
                t += float(self.rng.exponential(3600.0 / ped_per_h))
                a, b = t, t + cross_s
                if ivs and a <= ivs[-1][1]:
                    ivs[-1] = (ivs[-1][0], max(ivs[-1][1], b))
                else:
                    ivs.append((a, b))
                sig.peds += 1
            sig.intervals = ivs
        self.signals.append(sig)
        for li in sig.heads:
            self.by_lane_signals.setdefault(li, []).append(sig)
        return sig

    def demand_upstream(self, sig: Signal, group: str) -> bool:
        """A vehicle of the green group is within 35 m before its stop line (or queued behind it): worth extending the green."""
        for li, (s_stop, g) in sig.heads.items():
            if g != group:
                continue
            for o in self.lanes[li].vehs:
                if 0.0 <= s_stop - o.s <= 35.0 and not o.obstacle:
                    return True
        return False

    def signal_gap(self, v: Veh) -> float:
        """Distance (m, bumper to stop line) to the nearest stop line ahead that this vehicle has to stop at, or inf."""
        best = math.inf
        offset = 0.0
        lanes_ahead = [v.lane] + list(v.route)
        for k, li in enumerate(lanes_ahead):
            if offset > LOOKAHEAD_M:
                break
            for sig in self.by_lane_signals.get(li, ()):
                s_stop, grp = sig.heads[li]
                ahead = offset + s_stop - (v.s if k == 0 else 0.0)
                if ahead < -0.5:
                    continue  # past the line
                if sig.kinds and v.kind not in sig.kinds:
                    continue
                if sig.mode == "stop":
                    done = v.hold.get(sig.id)
                    if done == -1.0:
                        continue
                    if ahead - v.L / 2 - 0.5 <= v.s0 + 1.3 and v.v < 0.4:  # standing at the line (bumper within the minimum gap of it)
                        t0 = v.hold.setdefault(sig.id, self.t)
                        if self.t - t0 >= sig.hold_s:
                            v.hold[sig.id] = -1.0
                            continue
                    best = min(best, max(ahead - v.L / 2 - 0.5, 0.05))
                    continue
                ph = sig.phase()
                if ph == "ALL" or ph == grp:
                    continue
                brake_dist = v.v * v.v / (2.0 * max(v.b, 1.0))
                if ahead < brake_dist * 0.8 and ahead < 25.0 and ph is None:
                    continue  # too close to stop comfortably when the clearance began: carry on
                best = min(best, max(ahead - v.L / 2 - 0.5, 0.05))
            offset += self.lanes[li].length - (v.s if k == 0 else 0.0) if k == 0 else self.lanes[li].length
        return best

    # ------------------------------------------------------------- dynamics
    def leader(self, v: Veh):
        """(gap m, leader speed m/s) of the nearest vehicle ahead, a closed lane, or a red signal / stop line that applies to v."""
        gap, lv = self.vehicle_leader(v)
        if self.signals:
            sg = self.signal_gap(v)
            if sg < gap:
                return sg, 0.0
        return gap, lv

    def vehicle_leader(self, v: Veh):
        """(gap m bumper to bumper, leader speed m/s) of the nearest vehicle ahead along the route, or the lane end of a closed lane."""
        lane = self.lanes[v.lane]
        ahead = math.inf
        lv = 0.0
        for o in lane.vehs:
            if o is not v and o.s > v.s:
                g = o.s - v.s - (o.L + v.L) / 2
                if g < ahead:
                    ahead, lv = g, o.v
        if ahead < LOOKAHEAD_M:
            return ahead, lv
        dist = lane.length - v.s - v.L / 2
        for li in v.route:
            if dist > LOOKAHEAD_M:
                break
            nl = self.lanes[li]
            if nl.closed or (nl.allowed is not None and v.kind not in nl.allowed):
                return max(dist, 0.1), 0.0  # a closed lane ahead is a wall at its start
            if nl.vehs:
                o = min(nl.vehs, key=lambda q: q.s)
                g = dist + o.s - (o.L + v.L) / 2
                return max(g, 0.1), o.v
            dist += nl.length
        if not v.route and self.blocked_ahead(v, lane) and dist < LOOKAHEAD_M:
            return max(dist, 0.1), 0.0  # every successor is closed: the lane end is a wall
        return math.inf, 0.0

    def step(self, dt: float):
        for sg in self.signals:
            sg.update(self.t, self)
        for v in self.vehs:
            if v.obstacle:
                continue
            lane = self.lanes[v.lane]
            limit = lane.limit_at(v.s) / 3.6
            v0 = max(min(limit * v.v0 * self.weather, v.vmax), 1.0)
            gap, lv = self.leader(v)
            free = 1.0 - (v.v / v0) ** 4
            if math.isinf(gap):
                acc = v.a * free
            else:
                sstar = v.s0 + max(0.0, v.v * v.T + v.v * (v.v - lv) / (2 * math.sqrt(v.a * v.b)))
                acc = v.a * (free - (sstar / max(gap, 0.1)) ** 2)
            v.v = max(0.0, v.v + max(acc, -9.0) * dt)
            ds = v.v * dt
            v.s += ds
            v.dist += ds
            v.delay += dt * (1.0 - min(v.v / max(limit, 1.0), 1.0))
        # lane transitions, exits
        for v in list(self.vehs):
            if v.obstacle:
                if self.t >= v.until:
                    self.lanes[v.lane].vehs.remove(v)
                    self.vehs.remove(v)
                continue
            lane = self.lanes[v.lane]
            if v.dest is not None and v.lane == v.dest[0] and v.s >= v.dest[1]:
                self.remove(v, "arrived")
                continue
            while v.s >= lane.length:
                if v.retire:
                    self.remove(v, "retired")
                    break
                if not v.route:
                    self.extend_route(v)
                nxt = v.route.pop(0) if v.route else None
                nl = self.lanes[nxt] if nxt is not None else None
                if nl is None or nl.closed or (nl.allowed is not None and v.kind not in nl.allowed):
                    if nl is None and not v.route and not self.blocked_ahead(v, lane):
                        self.remove(v, "left")
                    else:
                        v.s = lane.length - 0.2  # wait at the end of the lane
                        v.v = 0.0
                        if nxt is not None:
                            v.route.insert(0, nxt)
                    break
                lane.vehs.remove(v)
                v.s -= lane.length
                v.lane = nxt
                lane = nl
                lane.vehs.append(v)
                lane.vehs.sort(key=lambda o: o.s)
                self.extend_route(v)
        for l in self.lanes:
            if len(l.vehs) > 1:
                l.vehs.sort(key=lambda o: o.s)
        self.t += dt

    def remove(self, v: Veh, why: str):
        self.lanes[v.lane].vehs.remove(v)
        self.vehs.remove(v)
        v.exited = (self.t, why)
        self.exits.append({"id": v.id, "kind": v.kind, "t0": v.t0, "t1": self.t, "dist": v.dist, "delay": v.delay, "why": why, "user": v.user})

    def state(self):
        out = []
        for v in self.vehs:
            lane = self.lanes[v.lane]
            x, y = lane.pos(v.s)
            out.append((v.id, v.kind, x, y, v.v * 3.6, math.degrees(lane.heading(v.s)), v.v < 0.3 and not v.obstacle, v.obstacle, lane.limit))
        return out


# ---------------------------------------------------------------------------- a run

def centre_radius(plan: dict, pad: float = 80.0):
    pts = [(it["x"], it["y"]) for k in ("props", "rules", "scenarios") for it in plan.get(k) or [] if isinstance(it.get("x"), (int, float))]
    if not pts:
        return None, None
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    return (cx, cy), min(max(max(math.hypot(p[0] - cx, p[1] - cy) for p in pts) + pad, 80.0), 300.0)


def rate_factor(profile: str, frac: float) -> float:
    """Factor on a source's rate over its active window (frac 0..1)."""
    frac = min(max(frac, 0.0), 1.0)
    if profile == "ramp_up":
        return 0.2 + 1.4 * frac
    if profile == "ramp_down":
        return 1.6 - 1.4 * frac
    if profile == "pulse":
        return 1.0 + 0.9 * math.sin(4 * math.pi * frac)
    return 1.0


def _xy(p: dict, kx: str, ky: str):
    return (p.get(kx), p.get(ky)) if p.get(kx) is not None and p.get(ky) is not None else None


def run(scene: dict, plan: dict | None, seed: int = 0, duration_s: float | None = None, warmup_s: float | None = None,
        dt: float = DEFAULTS["dt"], density_per_km: float | None = None, keep_states: bool = False, region=None, traffic: dict | None = None) -> dict:
    """One virtual run. plan=None is the baseline (same seed, same background traffic, no plan items). `traffic` is the global
    custom-traffic configuration (traffic_config.py); `density_per_km` / `duration_s` / `warmup_s` given explicitly override it."""
    import plans
    plan = plan or {"props": [], "rules": [], "scenarios": []}
    cfg = tc.normalize(traffic if traffic is not None else plan.get("traffic"))
    if density_per_km is not None:
        cfg["background"]["density_per_km"] = float(density_per_km)
        cfg["background"]["enabled"] = density_per_km > 0
    duration_s = float(duration_s if duration_s is not None else cfg["duration_s"])
    warmup_s = float(warmup_s if warmup_s is not None else cfg["warmup_s"])
    comp = plans.compile_plan(plan, scene)
    sc_items = plan.get("scenarios") or []
    # lane patch: compiled overrides, minus the closures that the virtual model handles as stopped vehicles
    obstacle_kinds = {"accident", "breakdown", "congestion_jam"}
    skip_ids = {it["id"] for it in sc_items if it.get("kind") in obstacle_kinds}
    patch = [o for o in comp["changes"]["lane_overrides"] if not any(f"plan item {i} " in (o.get("note") or "") for i in skip_ids)]
    w = World(scene, patch, seed, cfg)
    for it in sc_items:
        if it.get("kind") == "weather":
            w.weather = WEATHER.get((it.get("params") or {}).get("preset", "rain"), 1.0)
    # props: virtual signals, stop signs, pedestrian crossings, bus stops, warning signs, speed breakers, parking, objects
    from shapely.geometry import Polygon as _Poly
    cross_kinds = {"pedestrian_crossing", "pedestrian_priority"}
    for it in list(plan.get("props") or []) + list(plan.get("rules") or []):
        k = it.get("kind")
        sp = plans.with_defaults(k or "", it.get("params"))
        if k in ("traffic_signal", "stop_sign"):
            w.add_signal(f"sig_{it['id']}", it["x"], it["y"], k, sp.get("cycle_s", 90), sp.get("green_s", 40), bool(sp.get("adaptive")))
        elif k in cross_kinds:
            across = float(sp.get("length_m", 8))
            w.add_signal(f"sig_{it['id']}", it["x"], it["y"], "crossing", ped_per_h=float(sp.get("ped_per_h", 240)), cross_s=across / 1.3 + 3.0, horizon=warmup_s + duration_s + 30.0)
        elif k == "bus_stop":
            w.add_signal(f"sig_{it['id']}", it["x"], it["y"], "bus_stop", kinds={"bus"}, hold_s=float(sp.get("dwell_s", 15)))
        elif k == "warning_sign":
            mult = {"general": 0.9, "pedestrians": 0.8, "school": 0.75, "road_works": 0.75, "curve": 0.8, "slippery": 0.85}.get(sp.get("text"), 0.9)
            for l in w.lanes:
                d, s_ = l.nearest_s(it["x"], it["y"])
                if d <= SIGNAL_REACH_M and s_ >= 8.0:
                    l.segs.append((max(s_ - 60.0, 0.0), s_, mult, None))
        elif k == "speed_breaker":
            for l in w.lanes:
                d, s_ = l.nearest_s(it["x"], it["y"])
                if d <= SIGNAL_REACH_M:
                    l.segs.append((max(s_ - 12.0, 0.0), s_ + 8.0, 1.0, float(sp.get("cap_kmh", 20))))
        elif k == "custom_object":
            half = float(sp.get("width_m", 2)) / 2 + 1.7
            for l in w.lanes:
                d, s_ = l.nearest_s(it["x"], it["y"])
                if d <= half and 2.0 <= s_ <= l.length - 2.0:
                    n_ob = max(int(round(float(sp.get("length_m", 2)) / 5.0)), 1)
                    w.obstacle(*l.pos(s_), math.inf, "truck" if float(sp.get("length_m", 2)) > 6 else "car", n_ob)
        elif k in ("no_parking_zone", "no_parking") or (k == "custom_zone_rule" and sp.get("zone_type") == "no_parking"):
            poly = _Poly(plans.rect_polygon(it["x"], it["y"], it.get("yaw_deg", 0.0), float(sp.get("length_m", 20)), float(sp.get("width_m", 4))))
            w.no_stop_zones.append((poly, float(sp.get("grace_s", 30))))
        elif k == "parking_zone":
            poly = _Poly(plans.rect_polygon(it["x"], it["y"], it.get("yaw_deg", 0.0), float(sp.get("length_m", 20)), float(sp.get("width_m", 4))))
            w.parking_zones.append((poly, float(sp.get("stops_per_h", 40)), float(sp.get("stay_s", 60)), (it["x"], it["y"])))
    for it in plan.get("rules") or []:
        if it.get("kind") == "signal_timing":
            sp = plans.with_defaults("signal_timing", it.get("params"))
            for sg in w.signals:
                if sg.kind == "traffic_signal" and math.hypot(sg.x - it["x"], sg.y - it["y"]) <= float(sp.get("radius_m", 40)):
                    sg.green = min(max(sg.green + float(sp.get("green_delta_s", 0)), 5.0), max(sg.cycle - 2 * CLEARANCE_S - 5.0, 6.0))
    for it in sc_items:
        if it.get("kind") == "signal_ops":
            sp = plans.with_defaults("signal_ops", it.get("params"))
            mode = {"flashing": "flashing", "off": "off", "all_red": "all_red", "fixed_long_green": "long_green"}.get(sp.get("mode"), "off")
            for sg in w.signals:
                if sg.kind == "traffic_signal" and math.hypot(sg.x - it["x"], sg.y - it["y"]) <= float(sp.get("radius_m", 60)):
                    sg.mode = mode
    # local demand zones: a speed factor on the limits inside, extra vehicles (optionally biased to a vehicle kind)
    zones = []
    for it in sc_items:
        if it.get("kind") in ("traffic_density", "peak_hour", "demand_surge", "special_event"):
            p = plans.with_defaults(it["kind"], it.get("params"))
            r = float(p.get("radius_m", 150))
            bias = {"trucks": {"truck": 70, "van": 20, "car": 10}, "buses": {"bus": 80, "car": 20}, "two_wheelers": {"motorcycle": 100}}.get(p.get("bias"))
            zones.append((it, p, r, bias))
            sfac = float(p.get("speed_factor", 1.0))
            if abs(sfac - 1.0) > 1e-6:
                for l in w.lanes:
                    if l.nearest_s(it["x"], it["y"])[0] <= r:
                        l.limit = max(l.limit * sfac, 5.0)
            if bias:
                w.bias_zones.append((it["x"], it["y"], r, bias))
    # background traffic; the demand profile moves the target population over the run
    base_n = w.background_total()
    prof = cfg["background"]["demand_profile"]
    if cfg["background"]["enabled"] and base_n:
        w.populate(cfg["background"]["density_per_km"], factor=tc.profile_factor(prof, 0.0))
    for it, p, r, bias in zones:
        extra = max(float(p.get("factor", 1.0)) - 1.0, 0.0)
        if extra and cfg["background"]["density_per_km"] > 0:
            w.populate(cfg["background"]["density_per_km"], (it["x"], it["y"]), r, extra, mix=bias, group="zone")
    # timed events
    events: list[tuple[float, str, dict]] = []
    for it in sc_items:
        k, p = it.get("kind"), plans.with_defaults(it.get("kind", ""), it.get("params"))
        sch = it.get("schedule") or {}
        t_on = float(sch.get("from_s") or 0.0) + warmup_s
        if k == "spawn_vehicles":
            for j in range(int(p.get("count", 1))):
                events.append((t_on + j * float(p.get("spacing_s", 1.5)), "spawn", {"it": it, "p": p, "j": j}))
        elif k == "pedestrian_crowd":
            pass  # not simulated
        elif k == "emergency_vehicle":
            events.append((t_on, "spawn", {"it": it, "p": {**p, "count": 1}, "j": 0}))
        elif k in ("accident", "breakdown", "congestion_jam"):
            n = int(p.get("lanes_blocked", 1)) if k == "accident" else (max(int(float(p.get("radius_m", 60)) / 8), 2) if k == "congestion_jam" else 1)
            events.append((t_on, "obstacle", {"it": it, "p": p, "n": n, "kind": "truck" if k == "breakdown" and p.get("vehicle") == "truck" else "car"}))
    flows = [(it, plans.with_defaults("traffic_flow", it.get("params"))) for it in sc_items if it.get("kind") == "traffic_flow"]
    events.sort(key=lambda e: e[0])
    region_c, region_r = region if region else centre_radius(plan)
    if region_c is None:
        region_c, region_r = (0.0, 0.0), 1e9
    states = []
    metrics_rows = []   # (t, id, x, y, v_kmh, heading, stopped) inside the region after warm-up
    ei = 0
    next_flow = [warmup_s + float((it.get("schedule") or {}).get("from_s") or 0.0) for it, _ in flows]
    emitted = [0] * len(flows)
    steps = int((warmup_s + duration_s) / dt)
    log_every = max(int(round(0.2 / dt)), 1)
    next_ctl = 0.0
    for n in range(steps):
        t = w.t
        while ei < len(events) and events[ei][0] <= t:
            _, kind, d = events[ei]
            ei += 1
            it, p = d["it"], d["p"]
            if kind == "spawn":
                vt = d.get("vt") or p.get("vehicle", "car")
                vt = vt if vt in TYPES else "car"
                near = w.nearest_lane(it["x"], it["y"], 30.0, [vt])
                if near:
                    _, lane, s = near
                    made = w.spawn(vt, lane, s, float(p.get("speed_kmh", 40)) / 3.6 * 0.5, user=True, dest_xy=_xy(p, "dest_x", "dest_y"), via_xy=_xy(p, "via_x", "via_y"),
                                   speed_mult=float(p.get("speed_kmh", 40)) / max(lane.limit, 1.0) if vt not in EMERGENCY else 1.0, group=it["id"],
                                   behaviour=p.get("behaviour") if p.get("behaviour") != "mixed" else "mixed")
                    if made is None and d.get("tries", 0) < 120:  # no room yet: try again in half a second (up to a minute)
                        events.append((t + 0.5, "spawn", {**d, "tries": d.get("tries", 0) + 1}))
                        events.sort(key=lambda e: e[0])
            elif kind == "obstacle":
                until = t + float(p.get("duration_s", 300))
                w.obstacle(it["x"], it["y"], until, d["kind"], d["n"])
        # continuous sources: rate x profile over their window, bursts of `platoon_size`, optional cap
        for fi, (it, p) in enumerate(flows):
            sch = it.get("schedule") or {}
            t_on = warmup_s + float(sch.get("from_s") or 0.0)
            t_off = (warmup_s + float(sch["to_s"])) if sch.get("to_s") is not None else warmup_s + duration_s
            cap = int(p.get("max_vehicles") or 0)
            if t_on <= t < t_off and t >= next_flow[fi] and (cap <= 0 or emitted[fi] < cap):
                frac = (t - t_on) / max(t_off - t_on, 1.0)
                nveh = max(int(p.get("platoon_size") or 1), 1)
                for j in range(nveh):
                    if cap > 0 and emitted[fi] >= cap:
                        break
                    mixname = p.get("mix", "type")
                    vt = p.get("vehicle", "car") if mixname in (None, "type") else tc.pick_weighted(w.rng, tc.SOURCE_MIX.get(mixname, tc.SOURCE_MIX["city"]))
                    events.append((t + 0.3 * j, "spawn", {"it": it, "p": p, "j": emitted[fi], "vt": vt}))
                    emitted[fi] += 1
                events.sort(key=lambda e: e[0])
                rate = max(float(p.get("vph", 600)) * rate_factor(p.get("profile", "constant"), frac), 20.0)
                next_flow[fi] = t + 3600.0 * nveh / rate
        w.step(dt)
        curb = cfg["background"].get("curb_stops_per_km_h", 0.0)
        if (curb > 0 and cfg["background"]["enabled"]) or w.parking_zones:
            if curb > 0 and cfg["background"]["enabled"]:
                lanes_c, wts = w.bg_lanes()
                if lanes_c and w.rng.rand() < curb * float(wts.sum()) / 1000.0 / 3600.0 * dt:
                    l = lanes_c[int(w.rng.choice(len(lanes_c), p=wts / wts.sum()))]
                    sp_ = float(w.rng.uniform(8.0, max(l.length - 8.0, 9.0)))
                    w.try_stop(*l.pos(sp_), float(np.clip(w.rng.lognormal(3.4, 0.5), 10.0, 300.0)), "van" if w.rng.rand() < 0.3 else "car", in_zone=False)
            for poly, per_h, stay, (zx, zy) in w.parking_zones:
                if w.rng.rand() < per_h / 3600.0 * dt:
                    near = w.nearest_lane(zx, zy, SIGNAL_REACH_M)
                    if near:
                        w.try_stop(*near[1].pos(near[2]), float(np.clip(w.rng.normal(stay, stay * 0.3), 8.0, 900.0)), "car", in_zone=True)
        # demand controller (every second): keep the background population at base x profile(t)
        if cfg["background"]["enabled"] and base_n and w.t >= next_ctl:
            next_ctl = w.t + 1.0
            frac = max(w.t - warmup_s, 0.0) / max(duration_s, 1.0)
            target = int(round(base_n * tc.profile_factor(prof, frac)))
            alive = sum(1 for v in w.vehs if not v.user and not v.obstacle and v.group is None and not v.retire)
            for _ in range(min(max(target - alive, 0), 4)):
                w.spawn_random_background()
            if alive > target:
                w.retire_some(alive - target)
        if n % log_every == 0:
            st = w.state()
            if keep_states and w.t >= warmup_s:  # the warm-up only settles the background traffic: not played back
                states.append((w.t - warmup_s, st))
            if w.t >= warmup_s:
                for vid, kind, x, y, vk, hd, stopped, obst, lim in st:
                    if math.hypot(x - region_c[0], y - region_c[1]) <= region_r:
                        metrics_rows.append((w.t - warmup_s, vid, x, y, vk, hd, stopped, obst, kind, lim))
    m = metrics(w, metrics_rows, region_c, region_r, duration_s, warmup_s, dt * log_every)
    sig_out = []
    for sg in w.signals:
        heads = [{"x": round(w.lanes[li].pos(ss)[0], 2), "y": round(w.lanes[li].pos(ss)[1], 2), "group": g} for li, (ss, g) in sg.heads.items()]
        tl = [[round(max(t - warmup_s, 0.0), 2), ph] for t, ph in sg.log if t >= warmup_s or ph != sg.log[-1][1]]
        timeline = [[0.0, next((ph for t, ph in reversed(sg.log) if t <= warmup_s), sg.log[0][1] if sg.log else "ALL")]] + [r for r in tl if r[0] > 0]
        sig_out.append({"id": sg.id, "kind": sg.kind, "mode": sg.mode, "x": sg.x, "y": sg.y, "heads": heads, "timeline": timeline, "pedestrian_groups": sg.peds if sg.kind == "crossing" else None})
    out = {"seed": seed, "metrics": m, "vehicles_total": w.nid - 1, "spawn_blocked": w.spawn_fail, "spawned": dict(w.spawned),
           "traffic": describe_cfg(cfg), "config": cfg, "signals": sig_out,
           "stops": dict(w.stops), "signals_unplaced": [it["id"] for it in (plan.get("props") or []) if it.get("kind") in ("traffic_signal", "stop_sign") and f"sig_{it['id']}" not in {sg.id for sg in w.signals}]}
    if keep_states:
        out["states"] = states
    return out


def describe_cfg(cfg: dict) -> str:
    return tc.describe(cfg)


def metrics(w: World, rows, centre, radius, duration_s, warmup_s, log_dt) -> dict:
    """Metrics inside the region over the measured period (after the warm-up), same definitions as sim_metrics where they exist."""
    import sim_metrics as sm
    moving = [r for r in rows if not r[7]]
    sp = np.array([r[4] for r in moving]) if moving else np.array([])
    ids = {r[1] for r in moving}
    n_frames = len({r[0] for r in rows}) or 1
    area_km2 = math.pi * (radius / 1000.0) ** 2 if radius < 1e8 else None
    st = [{"sim_time": r[0], "id": r[1], "x": r[2], "y": r[3], "yaw": r[5], "vx": r[4] / 3.6 * math.cos(math.radians(r[5])), "vy": r[4] / 3.6 * math.sin(math.radians(r[5])),
           "speed_kmh": r[4], "limit_kmh": r[9], "road_id": None} for r in rows]
    # vehicles that crossed into the region and out again (travel time), and entries per hour (throughput)
    first, last = {}, {}
    for r in moving:
        first.setdefault(r[1], r[0])
        last[r[1]] = r[0]
    through = [last[i] - first[i] for i in first if first[i] > 1.0 and last[i] < duration_s - 1.0]
    # delay: time lost against driving at the lane's limit, summed per vehicle-sample, per vehicle seen in the region
    lost = {}
    for r in moving:
        lost[r[1]] = lost.get(r[1], 0.0) + log_dt * (1.0 - min(r[4] / max(r[9], 1.0), 1.0))
    qs = sm.queue_stats([r for r in st if r["speed_kmh"] is not None]) if st else {"max_m": 0.0, "mean_m": 0.0}
    return {
        "mean_speed_kmh": round(float(sp.mean()), 2) if len(sp) else None,
        "p85_speed_kmh": round(float(np.percentile(sp, 85)), 2) if len(sp) else None,
        "vehicles_in_region": len(ids),
        "density_veh_per_km2": round(len(moving) / n_frames / area_km2, 1) if area_km2 else None,
        "throughput_veh_per_h": round(len(through) / max(duration_s, 1.0) * 3600.0, 0),
        "travel_time_s": round(float(np.mean(through)), 2) if through else None,
        "travel_time_vehicles": len(through),
        "queue_max_m": qs["max_m"], "queue_mean_m": qs["mean_m"],
        "stopped_share": round(sum(1 for r in rows if r[6] and not r[7]) / max(len(rows), 1), 3),
        "delay_s_per_vehicle": round(float(np.mean(list(lost.values()))), 2) if lost else None,
        "vehicles_completed": len([e for e in w.exits if e["t1"] >= warmup_s]),
    }


METRIC_LABELS = {
    "mean_speed_kmh": ("Average vehicle speed", "km/h", +1), "p85_speed_kmh": ("85th-percentile speed", "km/h", +1),
    "vehicles_in_region": ("Vehicles in the area", "", 0), "density_veh_per_km2": ("Traffic density", "veh/km²", 0),
    "throughput_veh_per_h": ("Traffic throughput (through the area)", "veh/h", +1), "travel_time_s": ("Average travel time through the area", "s", -1),
    "queue_max_m": ("Queue length (max)", "m", -1), "queue_mean_m": ("Queue length (mean)", "m", -1), "stopped_share": ("Share of vehicle-time stopped", "", -1),
    "delay_s_per_vehicle": ("Vehicle delay (time lost vs free flow)", "s", -1),
}


def ci95(vals: list[float]) -> dict:
    v = [x for x in vals if x is not None]
    n = len(v)
    if n == 0:
        return {"mean": None, "ci95": None, "n": 0}
    mean = float(np.mean(v))
    if n < 2:
        return {"mean": round(mean, 3), "ci95": None, "n": n}
    t = T_975.get(n - 1, 2.0 if n > 30 else T_975[max(k for k in T_975 if k <= n - 1)])
    return {"mean": round(mean, 3), "ci95": round(t * float(np.std(v, ddof=1)) / math.sqrt(n), 3), "n": n}


def compare(scene: dict, plan: dict, seeds: int = DEFAULTS["seeds"], progress=None, **kw) -> dict:
    """Baseline vs plan on the same seeds. Per metric: mean +- 95 % interval for both, the difference, and whether the
    intervals are apart ('different') or overlap ('no difference detected')."""
    region = centre_radius(plan)
    cfg = tc.normalize(kw.get("traffic") if kw.get("traffic") is not None else plan.get("traffic"))
    kw.setdefault("duration_s", cfg["duration_s"])
    kw.setdefault("warmup_s", cfg["warmup_s"])
    base_runs, plan_runs = [], []
    first_states = {}
    first_signals = []
    note = progress or (lambda *_: None)
    for s in range(seeds):
        note(f"Baseline, run {s + 1} of {seeds}", 2 * s, 2 * seeds)
        b = run(scene, None, s, keep_states=(s == 0), region=region, **kw)
        note(f"With the plan, run {s + 1} of {seeds}", 2 * s + 1, 2 * seeds)
        p = run(scene, plan, s, keep_states=(s == 0), region=region, **kw)
        if s == 0:
            first_states = {"baseline": b.pop("states"), "plan": p.pop("states")}
            first_signals = p.get("signals", [])
        base_runs.append(b)
        plan_runs.append(p)
    note("Comparing the runs", 2 * seeds, 2 * seeds)
    table = {}
    for k, (label, unit, better) in METRIC_LABELS.items():
        bs, ps = ci95([r["metrics"][k] for r in base_runs]), ci95([r["metrics"][k] for r in plan_runs])
        verdict = "n/a"
        if bs["mean"] is not None and ps["mean"] is not None:
            d = ps["mean"] - bs["mean"]
            apart = bs["ci95"] is not None and ps["ci95"] is not None and abs(d) > (bs["ci95"] + ps["ci95"])
            verdict = ("different" if apart else "no difference detected") if bs["ci95"] is not None else "one run: no interval"
            table[k] = {"label": label, "unit": unit, "better": better, "baseline": bs, "plan": ps, "diff": round(d, 3), "verdict": verdict,
                        "direction": ("better" if (d * better > 0) else "worse" if (d * better < 0) else "neutral") if apart else "none"}
        else:
            table[k] = {"label": label, "unit": unit, "better": better, "baseline": bs, "plan": ps, "diff": None, "verdict": verdict, "direction": "none"}
    return {"label": "virtual simulation (simplified model, not CARLA)", "seeds": seeds, "duration_s": kw.get("duration_s", DEFAULTS["duration_s"]),
            "warmup_s": kw.get("warmup_s", DEFAULTS["warmup_s"]), "region": {"centre": region[0], "radius_m": region[1]},
            "notes": MODEL_NOTES, "signals": first_signals, "metrics": table, "runs": {"baseline": [r["metrics"] for r in base_runs], "plan": [r["metrics"] for r in plan_runs]},
            "states": first_states}


def trajectories(states, every: int = 1, max_vehicles: int = 400) -> dict:
    """Replay format of the twin ({track_id: [[t, x, y, speed_kmh], ...]}) from a run's logged states; obstacles included, flagged by speed 0."""
    tr: dict[str, list] = {}
    for n, (t, st) in enumerate(states):
        if n % every:
            continue
        for vid, kind, x, y, vk, hd, stopped, obst, _lim in st:
            tr.setdefault(str(vid), []).append([round(t, 2), round(x, 2), round(y, 2), round(vk, 1)])
    if len(tr) > max_vehicles:
        tr = dict(list(tr.items())[:max_vehicles])
    return tr
