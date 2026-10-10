"""Export a CARLA town's road network as a violation-engine lane map (Stage 4, Layer 4).

docs/Violation_Engine_Architecture.md, Section 5.4. Reads the town's OpenDRIVE file
**offline** (carla.Map(name, xodr) - no simulator needed) and writes the scene JSON that
ml/violation_engine/lane_map.py loads:

  lanes      one per (road, section, lane): centreline every --step m in the driving
             direction, width, lane type (driving / shoulder / parking), junction flag,
             left/right line type (CARLA LaneMarkingType, seen in the driving direction),
             speed limit
  zones      crosswalk polygons (Map.get_crosswalks), and with --highway-kmh a "highway"
             zone over every non-junction lane with a limit >= that value

Road features (Build Plan M1), per lane:
  road_id, next   the OpenDRIVE road and the lanes that follow this one (lane graph)
  lane_change     CARLA's permission to leave the lane: none / left / right / both
  z               road height at every centreline point (OpenDRIVE elevation)
  bridge, tunnel  bridge: inside an OpenDRIVE <bridge> s-range and >= BRIDGE_MIN_Z_M up, or
                  >= FLYOVER_Z_M up (flyover); tunnel: inside a <tunnel> s-range. A lane is
                  split where this changes, like at a speed sign
  ramp, restricted  from OpenDRIVE lane types (onRamp/entry, offRamp/exit, restricted) when the
                  map has them, otherwise derived (road_features.py)
then road_features.derive() adds road_class, one_way, ramp and median_left (see its docstring).

Speed limits: CARLA's speed signs (OpenDRIVE type 274) report e.g. "30 mph", but CARLA's
limits are km/h (30/60/90, as Vehicle.get_speed_limit returns), so the value is used as
km/h. A sign sets the limit of its road for the lanes driving in its orientation; lanes
without a sign take it from the lane before them (followed along the road graph), else
DEFAULT_LIMIT_KMH.

No-parking, no-U-turn and stop-line entries are scene-specific: the scenario script
(simulation/violation_scenarios/stage_violations.py) or a person adds them.

Run in the carlaAir conda env (it has the carla package):
    python simulation/carla_scripts/export_lane_map.py Town05
    python simulation/carla_scripts/export_lane_map.py Town05 --center -40 -137 --radius 200 --highway-kmh 90
"""

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import carla
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))
from road_features import HIGHWAY_KMH, derive, summary  # noqa: E402  (numpy only, shared with the engine)

XODR_DIR = REPO / "CarlaAir-v0.1.7-Windows11-x86_64" / "WindowsNoEditor" / "CarlaUE4" / "Content" / "Carla" / "Maps" / "OpenDrive"
OUT_DIR = REPO / "ml" / "violation_engine" / "configs" / "scenes"
DEFAULT_LIMIT_KMH = 30.0
SIDE_LANE_TYPES = {carla.LaneType.Shoulder: "shoulder", carla.LaneType.Parking: "parking"}
# bridge = a raised structure. Town05 tags whole ground-level ring stretches as <bridge> (roads 36,
# 2034, 2035, 2343, 2344 all at z 0.0 m, measured 2026-10-09), so a tag counts only where the road is
# >= BRIDGE_MIN_Z_M up; untagged road >= FLYOVER_Z_M up (about one vehicle clearance) counts too
BRIDGE_MIN_Z_M = 2.0
FLYOVER_Z_M = 5.0
RAMP_LANE_TYPES = {carla.LaneType.OnRamp: "on", carla.LaneType.Entry: "on",
                   carla.LaneType.OffRamp: "off", carla.LaneType.Exit: "off"}


def lane_key(wp) -> tuple:
    return (wp.road_id, wp.section_id, wp.lane_id)


def collect(m: carla.Map, step: float) -> dict[tuple, list]:
    """Driving waypoints every `step` m, plus the shoulder/parking lanes next to them."""
    lanes = defaultdict(list)
    for wp in m.generate_waypoints(step):
        lanes[lane_key(wp)].append(wp)
        for get in (wp.get_right_lane, wp.get_left_lane):
            side = get()
            while side is not None and side.lane_type in SIDE_LANE_TYPES:
                lanes[lane_key(side)].append(side)
                side = side.get_right_lane() if get == wp.get_right_lane else side.get_left_lane()
    return lanes


def order_driving(wps: list) -> list:
    """Waypoints sorted along the lane's own driving direction (the waypoint yaw)."""
    wps = sorted({round(w.s, 2): w for w in wps}.values(), key=lambda w: w.s)
    if len(wps) >= 2:
        p = np.array([[w.transform.location.x, w.transform.location.y] for w in wps])
        seg = np.diff(p, axis=0)
        yaw = np.radians([w.transform.rotation.yaw for w in wps[:-1]])
        if (seg[:, 0] * np.cos(yaw) + seg[:, 1] * np.sin(yaw)).sum() < 0:
            wps = wps[::-1]
    return wps


def marking(m) -> str:
    return str(m.type).split(".")[-1].lower() if m is not None else "none"


def sign_limits(m: carla.Map) -> dict[tuple, list]:
    """(road_id, lane-id sign) -> [(s, km/h)] of the speed signs on that road. A sign applies to
    the lanes on the side of the road it stands next to (the sign of its nearest lane's id):
    Town05 shows CARLA's orientation labels run opposite to the lanes they serve, so the
    position is used, not the label."""
    out = defaultdict(list)
    for lm in m.get_all_landmarks_of_type("274"):
        wp = m.get_waypoint(lm.transform.location)
        out[(lm.road_id, 1 if wp.lane_id > 0 else -1)].append((float(lm.s), float(lm.value)))
    return out


def limits_along(wps: list, key: tuple, signs: dict) -> list:
    """Speed limit at each waypoint of a lane (driving order): the last sign passed on this road
    in the driving direction, or None before the first one (inherited from the lane before)."""
    side = 1 if key[2] > 0 else -1
    road_signs = signs.get((key[0], side), [])
    forward = len(wps) < 2 or wps[-1].s >= wps[0].s
    out = []
    for w in wps:
        passed = [(s, v) for s, v in road_signs if (s <= w.s + 1e-6 if forward else s >= w.s - 1e-6)]
        out.append((max(passed)[1] if forward else min(passed)[1]) if passed else None)
    return out


def structures(xodr_text: str) -> dict[int, list]:
    """road id -> [(s0, s1, "bridge" | "tunnel")] from the OpenDRIVE <objects> of each road."""
    out = defaultdict(list)
    for road in ET.fromstring(xodr_text).iter("road"):
        for kind in ("bridge", "tunnel"):
            for b in road.iter(kind):
                s0, length = float(b.get("s", 0)), float(b.get("length", 0))
                if length > 0:
                    out[int(road.get("id"))].append((s0, s0 + length, kind))
    return out


def on_structure(w, spans: list, kind: str) -> bool:
    return any(k == kind and s0 - 1e-6 <= w.s <= s1 + 1e-6 for s0, s1, k in spans)


def polygon_strip(centre: np.ndarray, width: float) -> list:
    """Polygon around a polyline, width/2 to each side."""
    seg = np.diff(centre, axis=0)
    seg = np.vstack([seg, seg[-1:]])
    n = np.stack([-seg[:, 1], seg[:, 0]], 1) / np.maximum(np.hypot(seg[:, 0], seg[:, 1]), 1e-9)[:, None]
    left, right = centre + n * width / 2, centre - n * width / 2
    return np.round(np.vstack([left, right[::-1]]), 2).tolist()


def crosswalks(m: carla.Map) -> list:
    """Map.get_crosswalks() is a flat point list; each polygon ends by repeating its first point."""
    pts = [(p.x, p.y) for p in m.get_crosswalks()]
    polys, cur = [], []
    for p in pts:
        if cur and math.hypot(p[0] - cur[0][0], p[1] - cur[0][1]) < 1e-3 and len(cur) >= 3:
            polys.append(cur)
            cur = []
        else:
            cur.append(p)
    if len(cur) >= 3:
        polys.append(cur)
    return [[[round(x, 2), round(y, 2)] for x, y in poly] for poly in polys]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("town", help="Town name, e.g. Town05 (reads <town>.xodr)")
    ap.add_argument("--xodr", type=Path, default=None, help="OpenDRIVE file (default: CarlaAir's Maps/OpenDrive/<town>.xodr)")
    ap.add_argument("--step", type=float, default=1.0, help="Centreline point spacing (m)")
    ap.add_argument("--center", type=float, nargs=2, default=None, metavar=("X", "Y"), help="Keep only lanes near here")
    ap.add_argument("--radius", type=float, default=250.0)
    ap.add_argument("--highway-kmh", type=float, default=None, help="Add highway zones over lanes with a limit >= this")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    xodr = args.xodr or XODR_DIR / f"{args.town}.xodr"
    text = xodr.read_text()
    m = carla.Map(args.town, text)
    raw = collect(m, args.step)
    signs = sign_limits(m)
    spans = structures(text)

    # one lane entry per run of constant speed limit and bridge/tunnel state (a lane is split
    # where a sign is passed or a structure begins or ends)
    pieces = {}  # (key, i) -> lane dict; i = 0.. along the driving direction
    n_pieces, succ = {}, defaultdict(set)
    for key, wps in raw.items():
        wps = order_driving(wps)
        if len(wps) < 2:
            continue
        c = np.array([[w.transform.location.x, w.transform.location.y] for w in wps])
        if args.center is not None and np.min(np.hypot(c[:, 0] - args.center[0], c[:, 1] - args.center[1])) > args.radius:
            continue
        z = np.array([w.transform.location.z for w in wps])
        lim = limits_along(wps, key, signs)
        struct = [(bool((on_structure(w, spans.get(key[0], []), "bridge") and zi >= BRIDGE_MIN_Z_M) or zi >= FLYOVER_Z_M),
                   on_structure(w, spans.get(key[0], []), "tunnel")) for w, zi in zip(wps, z)]
        sig = list(zip(lim, struct))
        cuts = [0] + [i for i in range(1, len(wps)) if sig[i] != sig[i - 1]] + [len(wps)]
        n_pieces[key] = len(cuts) - 1
        for p, (a, b) in enumerate(zip(cuts[:-1], cuts[1:])):
            a0 = max(a - 1, 0)  # share the boundary point so pieces join up
            mid = wps[(a + b) // 2]
            lane = {
                "id": f"r{key[0]}_s{key[1]}_l{key[2]}" + (f"_p{p}" if len(cuts) > 2 else ""),
                "road_id": str(key[0]),
                "centreline": np.round(c[a0:b], 2).tolist(),
                "z": np.round(z[a0:b], 2).tolist(),
                "width_m": round(float(np.median([w.lane_width for w in wps[a:b]])), 2),
                "lane_type": SIDE_LANE_TYPES.get(mid.lane_type, "driving"), "junction": bool(mid.is_junction),
                "left_line": marking(mid.left_lane_marking), "right_line": marking(mid.right_lane_marking),
                "lane_change": Counter(str(w.lane_change).lower() for w in wps[a:b]).most_common(1)[0][0],
                "speed_limit_kmh": lim[a],
                "bridge": struct[a][0], "tunnel": struct[a][1],
            }
            if mid.lane_type in RAMP_LANE_TYPES:
                lane["ramp"] = RAMP_LANE_TYPES[mid.lane_type]
            if mid.lane_type == carla.LaneType.Restricted:
                lane["restricted"] = "restricted"
            pieces[(key, p)] = lane
        for nxt in wps[-1].next(args.step * 2):
            succ[key].add(lane_key(nxt))
    pieces = {k: v for k, v in pieces.items() if len(v["centreline"]) >= 2}
    for (key, p), lane in pieces.items():  # lane graph: the next piece of this lane, else the first piece of each successor
        nxt = [pieces[(key, p + 1)]["id"]] if (key, p + 1) in pieces else \
            [pieces[(k2, 0)]["id"] for k2 in sorted(succ[key]) if (k2, 0) in pieces]
        lane["next"] = nxt

    # a lane's first piece without a sign inherits the limit at the end of the lane before it
    for _ in range(100):
        changed = False
        for key, n in n_pieces.items():
            last = pieces.get((key, n - 1))
            if last is None or last["speed_limit_kmh"] is None:
                continue
            for k2 in succ[key]:
                first = pieces.get((k2, 0))
                if first is not None and first["speed_limit_kmh"] is None:
                    first["speed_limit_kmh"] = last["speed_limit_kmh"]
                    changed = True
        if not changed:
            break
    for lane in pieces.values():
        lane["speed_limit_kmh"] = lane["speed_limit_kmh"] or DEFAULT_LIMIT_KMH
    lanes = pieces

    zones = []
    for i, poly in enumerate(crosswalks(m)):
        if args.center is None or any(math.hypot(x - args.center[0], y - args.center[1]) <= args.radius for x, y in poly):
            zones.append({"id": f"crosswalk_{i}", "type": "crosswalk", "polygon": poly, "source": "opendrive"})
    if args.highway_kmh:
        for lane in lanes.values():
            if not lane["junction"] and lane["speed_limit_kmh"] >= args.highway_kmh:
                zones.append({"id": f"highway_{lane['id']}", "type": "highway", "source": "opendrive",
                              "polygon": polygon_strip(np.array(lane["centreline"]), lane["width_m"])})

    out = args.out or OUT_DIR / f"{args.town}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    scene = {"scene": args.town, "coords": "carla_world_m", "source": str(xodr.name),
             "note": "speed sign values used as km/h (CARLA labels them mph)",
             "lanes": list(lanes.values()), "zones": zones, "stop_lines": []}
    derive(scene, args.highway_kmh or HIGHWAY_KMH)
    out.write_text(json.dumps(scene))
    print(f"[road features] {json.dumps(summary(scene))}")
    kinds = defaultdict(int)
    for lane in lanes.values():
        kinds[lane["lane_type"] + (" (junction)" if lane["junction"] else "")] += 1
    limits = defaultdict(int)
    for lane in lanes.values():
        limits[lane["speed_limit_kmh"]] += 1
    print(f"[lane map] {len(lanes)} lanes {dict(kinds)}, limits {dict(limits)}, {len(zones)} zones -> {out}")


if __name__ == "__main__":
    main()
