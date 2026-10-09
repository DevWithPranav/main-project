"""Scene knowledge: lane map, zones and map matching (Violation Engine, Layer 4).

docs/Violation_Engine_Architecture.md, Section 5.4. One JSON file per scene, in the same
metric coordinates as the trajectories (CARLA world metres, or real-footage metres):

    {"scene": "...", "coords": "carla_world_m",
     "lanes": [{"id": "r12_l-1", "centreline": [[x, y], ...], "width_m": 3.5,
                "lane_type": "driving", "speed_limit_kmh": 30, "junction": false,
                "left_line": "solid", "right_line": "broken"}],
     "zones": [{"id": "np_bank", "type": "no_parking", "polygon": [[x, y], ...], "grace_s": 30}],
     "stop_lines": [{"id": "tl_7", "line": [[x, y], [x, y]], "signal_id": 7}]}

A lane's driving direction is the order of its centreline points. "left"/"right" are
seen in that driving direction, by a driver. CARLA's world axes are left-handed, so
"coords": "carla_world_m" (or "left_handed": true) flips the sign of the sideways offset d. Line types follow CARLA's LaneMarkingType in lower case
(solid, broken, solidsolid, solidbroken, brokensolid, brokenbroken, bottsdots, grass,
curb, other, none). Zone types: no_parking, crosswalk, no_u_turn, highway, speed.

Road features (Build Plan M1, road_features.py), all optional: road_id, next (successor lane
ids), lane_change (none / left / right / both: may a vehicle leave the lane to that side),
z (road height per centreline point), bridge, tunnel, ramp (on / off / link), road_class
(highway / urban), one_way, median_left (+ median_gap_m), restricted (bus / emergency /
restricted). A lane without them reads as an ordinary urban lane.

Map matching is by **position only**, never by heading: a wrong-way car must be matched
to the lane it is physically in, not to the opposite lane it agrees with.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from scipy.spatial import cKDTree

MATCH_MARGIN_M = 0.75  # a point up to this far outside a lane's edge still matches it (position noise)
SOLID_LINES = {"solid", "solidsolid"}  # mixed lines (solidbroken/brokensolid) depend on the side: treated as crossable


@dataclass
class Lane:
    id: str
    centreline: np.ndarray  # (N, 2)
    width: float
    lane_type: str = "driving"
    speed_limit_kmh: float | None = None
    junction: bool = False
    left_line: str = "none"
    right_line: str = "none"
    road_id: str | None = None
    next: tuple[str, ...] = ()
    lane_change: str = "both"
    z: np.ndarray | None = None  # (N,) road height at each centreline point
    bridge: bool = False
    tunnel: bool = False
    ramp: str | None = None
    road_class: str = "urban"
    one_way: bool | None = None
    median_left: bool = False
    median_gap_m: float | None = None
    restricted: str | None = None

    def __post_init__(self):
        seg = np.diff(self.centreline, axis=0)
        self.seg_len = np.hypot(seg[:, 0], seg[:, 1])
        self.cum = np.r_[0.0, np.cumsum(self.seg_len)]
        self.length = float(self.cum[-1])

    def height_at(self, s: float) -> float | None:
        """Road height s metres along the lane (None without z)."""
        return None if self.z is None else float(np.interp(s, self.cum, self.z))

    def may_change(self, side: str) -> bool:
        """May a vehicle leave this lane to its "left" / "right"? (CARLA waypoint lane_change)"""
        return self.lane_change in ("both", side)


@dataclass
class LaneMatch:
    lane: Lane
    s: float  # metres along the lane from its first point
    d: float  # signed sideways offset from the centreline, + = left of the driving direction
    dir_deg: float  # lane direction at that point (atan2 convention, same as heading_deg)

    @property
    def dir(self) -> np.ndarray:
        a = math.radians(self.dir_deg)
        return np.array([math.cos(a), math.sin(a)])


@dataclass
class Zone:
    id: str
    type: str
    polygon: shapely.Polygon
    params: dict


class SceneMap:
    def __init__(self, data: dict):
        self.scene = data.get("scene", "")
        self.coords = data.get("coords", "")
        # CARLA/Unreal world axes are left-handed (seen from above, +y is to the right of +x), so
        # "left of the driving direction" is the negative side of the usual cross product there
        self.left_handed = bool(data.get("left_handed", self.coords.startswith("carla")))
        self.lanes = [self._lane(l) for l in data.get("lanes", []) if len(l["centreline"]) >= 2]
        self.lane_by_id = {l.id: l for l in self.lanes}
        self.zones = []
        for z in data.get("zones", []):
            params = {k: v for k, v in z.items() if k not in ("id", "type", "polygon")}
            self.zones.append(Zone(str(z["id"]), str(z["type"]), shapely.Polygon(z["polygon"]), params))
        for z in self.zones:
            shapely.prepare(z.polygon)
        self.stop_lines = data.get("stop_lines", [])
        # every lane segment, for nearest-segment search
        a, b, owner, k = [], [], [], []
        for li, lane in enumerate(self.lanes):
            for j in range(len(lane.centreline) - 1):
                a.append(lane.centreline[j])
                b.append(lane.centreline[j + 1])
                owner.append(li)
                k.append(j)
        self._a = np.array(a).reshape(-1, 2)
        self._b = np.array(b).reshape(-1, 2)
        self._owner = np.array(owner, dtype=int)
        self._k = np.array(k, dtype=int)
        self._tree = cKDTree((self._a + self._b) / 2) if len(a) else None
        self._max_half_seg = float(np.max(np.hypot(*(self._b - self._a).T)) / 2) if len(a) else 0.0
        self._max_half_width = max((l.width / 2 for l in self.lanes), default=0.0)

    @staticmethod
    def _lane(l: dict) -> Lane:
        z = l.get("z")
        return Lane(id=str(l["id"]), centreline=np.asarray(l["centreline"], float), width=float(l.get("width_m", 3.5)),
                    lane_type=str(l.get("lane_type", "driving")).lower(),
                    speed_limit_kmh=l.get("speed_limit_kmh"), junction=bool(l.get("junction", False)),
                    left_line=str(l.get("left_line", "none")).lower(),
                    right_line=str(l.get("right_line", "none")).lower(),
                    road_id=None if l.get("road_id") is None else str(l["road_id"]),
                    next=tuple(str(n) for n in l.get("next", [])),
                    lane_change=str(l.get("lane_change") or "both").lower(),
                    z=np.asarray(z, float) if z is not None and len(z) == len(l["centreline"]) else None,
                    bridge=bool(l.get("bridge", False)), tunnel=bool(l.get("tunnel", False)), ramp=l.get("ramp"),
                    road_class=str(l.get("road_class") or "urban"), one_way=l.get("one_way"),
                    median_left=bool(l.get("median_left", False)), median_gap_m=l.get("median_gap_m"),
                    restricted=l.get("restricted"))

    @classmethod
    def load(cls, path: Path) -> "SceneMap":
        return cls(json.loads(Path(path).read_text()))

    def match(self, x: float, y: float) -> LaneMatch | None:
        """The lane this point lies in (smallest |d| among lanes it is inside of), or None."""
        if self._tree is None:
            return None
        radius = self._max_half_seg + self._max_half_width + MATCH_MARGIN_M
        idx = self._tree.query_ball_point([x, y], radius)
        if not idx:
            return None
        idx = np.array(idx)
        a, b = self._a[idx], self._b[idx]
        ab = b - a
        L2 = np.maximum((ab * ab).sum(1), 1e-12)
        t = np.clip(((np.array([x, y]) - a) * ab).sum(1) / L2, 0.0, 1.0)
        foot = a + ab * t[:, None]
        L = np.sqrt(L2)
        dvec = np.array([x, y]) - foot
        cross = (ab[:, 0] * dvec[:, 1] - ab[:, 1] * dvec[:, 0]) / L  # + = left of the segment direction
        if self.left_handed:
            cross = -cross
        dist = np.hypot(dvec[:, 0], dvec[:, 1])
        best = None
        for i in np.argsort(dist):
            lane = self.lanes[self._owner[idx[i]]]
            if dist[i] > lane.width / 2 + MATCH_MARGIN_M:
                continue
            # a point beyond the end of a segment is only "on" it if this is the lane's first/last segment
            if best is None or dist[i] < best[0] - 1e-9:
                k = self._k[idx[i]]
                s = float(lane.cum[k] + t[i] * lane.seg_len[k])
                best = (dist[i], LaneMatch(lane, s, float(cross[i]), math.degrees(math.atan2(ab[i, 1], ab[i, 0]))))
        return best[1] if best else None

    def zones_at(self, x: float, y: float, types: set[str] | None = None) -> list[Zone]:
        p = shapely.Point(x, y)
        return [z for z in self.zones if (types is None or z.type in types) and z.polygon.contains(p)]


def angle_diff_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two directions, 0..180."""
    return abs((a - b + 180.0) % 360.0 - 180.0)
