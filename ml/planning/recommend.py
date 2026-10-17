"""City-planner recommendations from violation events (Build Plan M8; Expected_Output 7.2-7.3; PRD 21.2).

recommend(events, scene=None, kinematics=None) -> list of recommendation dicts, each with
    id, problem, location, evidence, action, expected_impact, priority, validation_method,
    confidence, limitations, alternatives, simulation   (+ rule_id, type, tier, area, generated_by)

How:
  1. counted events (flagged / needs_review) -> DBSCAN hotspots per type (analytics.hotspots, PRD eps 50 m,
     min 10 events). Events left over (noise, or a type below min events) are grouped per road with a
     smaller DBSCAN (min 2 events): "emerging" patterns, shown with lower confidence and that limitation.
  2. kinematics (optional) -> queues per road -> "traffic flow" groups.
  3. each group -> the first matching rule of its area (RULES): problem, action, alternatives,
     catalogue entry for the projected impact (catalogue.py, sourced), and the CARLA change the
     validation runner can apply (simulation block; Expected_Output 5.3 decides what is simulable).
  4. priority = 100 x severity x frequency x evidence quality x tier x planning objective (explained),
     optionally re-weighted by measured outcomes of earlier decisions (history.py).

Recommendations are proposals that need validation, never decisions (Expected_Output 7.2).
Ids are deterministic: a hash of rule, type, roads and the centroid snapped to an eps grid.

Usage:
    python ml/planning/recommend.py --events <violations.json> [...] --scene ml/violation_engine/configs/scenes/Town05.json \
        [--kinematics <kinematics.csv> ...] [--history ml/data/results/planning/history.jsonl] --out recs.json
"""

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analytics  # noqa: E402
from catalogue import COUNTERMEASURES, projected  # noqa: E402

VERSION = "m8-1"
REPO = HERE.parents[1]

PARAMS = {
    "eps_m": analytics.EPS_M,
    "min_events": analytics.MIN_EVENTS,
    "major_events": analytics.MAJOR_EVENTS,
    "emerging": True,  # also group events below the hotspot threshold
    "min_emerging": 2,  # events of one type on one road within eps
    "queue_min_vehicles": 5,  # flow rule: max stopped vehicles in a lane chain
    "queue_min_m": 25.0,
    "near_crossing_m": 30.0,  # speeding "near a crossing": vulnerable-user area
    "wrong_way_share": 0.40,  # PRD 21.2
    "lane_share": 0.50,  # PRD 21.2
    "objectives": {},  # planning objective weight per type, e.g. {"zebra_crossing": 1.3}
    "scenario": {"vehicles": 60, "radius_m": 150.0, "duration_s": 300.0, "warmup_s": 20.0, "seed": 7,
                 "seeds": [7, 11, 23], "weather": "ClearNoon", "dt": 0.05},
}

# Design weights (not measured): relative harm of each type, per PRD 9 severity ordering; configurable.
SEVERITY = {"wrong_way": 1.0, "red_light": 0.9, "speeding": 0.8, "illegal_u_turn": 0.8, "zebra_crossing": 0.75,
            "highway_stop": 0.7, "lane_violation": 0.6, "no_parking": 0.4, "flow": 0.5}
TIER = {"major_hotspot": 1.0, "hotspot": 0.9, "emerging": 0.5, "flow": 0.6}
TIER_PLAIN = {"major_hotspot": "a major hotspot", "hotspot": "a hotspot", "emerging": "an early pattern", "flow": "a traffic queue"}

AREA = {"speeding": "Speed management", "lane_violation": "Lane management", "wrong_way": "Wrong-way prevention",
        "highway_stop": "Illegal stopping", "no_parking": "Illegal stopping", "zebra_crossing": "Pedestrian safety",
        "illegal_u_turn": "Intersections / highway safety", "red_light": "Intersections", "flow": "Traffic flow"}


# ------------------------------------------------------------------------------------------ context

def _dist_poly(poly: list, x: float, y: float) -> float:
    import shapely
    return float(shapely.Polygon(poly).distance(shapely.Point(x, y)))


def context(group: dict, gtype: str, all_events: list[dict], scene: dict | None, sections: dict | None,
            params: dict) -> dict:
    """What the rules look at: lane attributes, zones, conditions, share of this type in the area."""
    lanes = [analytics.lane_props(scene, l) for l in group.get("lane_ids", {})]
    lanes = [l for l in lanes if l]
    cx, cy = group["centroid"]
    reach = group.get("radius_m", 0.0) + params["eps_m"]
    near = [e for e in all_events if math.hypot(e["x"] - cx, e["y"] - cy) <= reach]
    share = group["n"] / len(near) if near else 1.0
    crossings = []
    if scene:
        for z in scene.get("zones", []):
            if z.get("type") == "crosswalk" and _dist_poly(z["polygon"], cx, cy) <= params["near_crossing_m"] + group.get("radius_m", 0.0):
                crossings.append(z["id"])
    conds = Counter({k: v for k, v in group.get("conditions", {}).items()})
    sec = {}
    if sections:
        sec = {l: sections["lanes"][l] for l in group.get("lane_ids", {}) if l in sections["lanes"]}
    limits = sorted({l.get("speed_limit_kmh") for l in lanes if l.get("speed_limit_kmh")})
    return {"type": gtype, "lanes": lanes, "conditions": conds, "dominant": conds.most_common(1)[0][0] if conds else None,
            "tags": Counter(group.get("tags", {})),
            "bridge": any(l.get("bridge") for l in lanes), "tunnel": any(l.get("tunnel") for l in lanes),
            "ramp": any(l.get("ramp") for l in lanes), "junction": any(l.get("junction") for l in lanes),
            "median": any(l.get("median_left") for l in lanes),
            "highway": any(l.get("road_class") == "highway" for l in lanes),
            "limits": limits, "crossings": crossings, "share_in_area": round(share, 3),
            "events_in_area": len(near), "sections": sec}


# ------------------------------------------------------------------------------------------ rules
# Each rule: id, types it applies to, when(ctx) -> bool, cm (catalogue key of the primary action),
# alts (catalogue keys), problem(ctx, g) / action(ctx, g) -> str, sim(ctx, g, scene) -> simulation change.

def _lanes(g):
    return sorted(g.get("lane_ids", {}))


def _roads(g):
    return sorted(g.get("road_ids", {}))


def _where(ctx, g):
    feats = [f for f, on in (("bridge", ctx["bridge"]), ("ramp", ctx["ramp"]), ("junction", ctx["junction"]),
                             ("highway", ctx["highway"])) if on]
    return f"road {', '.join(_roads(g)) or '?'}" + (f" ({', '.join(feats)})" if feats else "")


def _cars(n) -> str:
    return f"{n} car" if n == 1 else f"{n} cars"


def _max_speed(g, events):
    vals = [(e.get("value") or {}).get("max_speed_kmh") for e in events if e["event_id"] in set(g["event_ids"])]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def sim_speed_compliance(ctx, g, scene):
    return {"kind": "speed_compliance", "lanes": _lanes(g), "road_ids": _roads(g), "polygon": g["polygon"],
            "max_over_limit_pct": 0.0,
            "carla": "Traffic Manager: while a vehicle is on these roads its vehicle_percentage_speed_difference is "
                     "clamped to >= 0 (no vehicle drives above the lane limit): models effective enforcement"}


def sim_speed_limit(ctx, g, scene):
    lim = ctx["limits"][0] if ctx["limits"] else 30.0
    new = max(20.0, lim - 10.0)
    return {"kind": "speed_limit", "lanes": _lanes(g), "road_ids": _roads(g), "polygon": g["polygon"],
            "from_kmh": lim, "to_kmh": new,
            "carla": "Traffic Manager: on these roads vehicle_percentage_speed_difference is shifted so the target is "
                     f"{new:g} instead of {lim:g} km/h; the lane map's limit is changed for the modified run's engine"}


def sim_none(reason):
    def f(ctx, g, scene):
        return {"kind": "none", "reason": reason}
    return f


def sim_zone(ztype):
    def f(ctx, g, scene):
        return {"kind": "zone", "zone": {"id": f"rec_{ztype}", "type": ztype, "polygon": g["polygon"]},
                "carla": "rule-side only (Expected_Output 5.3): the zone is added to the engine's scene for the "
                         "modified run; CARLA autopilot traffic does not react to it"}
    return f


def sim_remove_stoppers(ctx, g, scene):
    return {"kind": "remove_stopped_vehicles", "positions": g.get("positions", []), "lanes": _lanes(g),
            "carla": "baseline: one parked vehicle (hand brake, autopilot off) per event position for the whole run; "
                     "modified: none (models full compliance with the no-stopping restriction). Measures what the "
                     "illegal stops cost in queues, travel time and conflicts"}


def sim_no_lane_change(ctx, g, scene):
    return {"kind": "no_lane_change", "lanes": _lanes(g), "road_ids": _roads(g), "polygon": g["polygon"],
            "carla": "Traffic Manager: auto_lane_change(vehicle, False) while a vehicle is on these roads (models a "
                     "solid line that is respected); the lane map's lines are set to solid for the modified run"}


def sim_signal_timing(ctx, g, scene):
    return {"kind": "signal_timing", "center": g["centroid"], "radius_m": 60.0, "green_delta_s": 5.0,
            "carla": "traffic lights within radius_m of the centre: set_green_time(+green_delta_s) "
                     "(skipped with a warning if there is no light)"}


RULES = [
    # --- speed management
    {"id": "SPD-HWY", "types": {"speeding"}, "when": lambda c: c["highway"], "cm": "speed_camera",
     "alts": ["average_speed_camera", "speed_limit_review"],
     "problem": lambda c, g: f"{_cars(g['n'])} went over the speed limit on a fast road ({_where(c, g)})",
     "action": lambda c, g: "Put up speed cameras (at one spot, or average-speed cameras over a stretch) with warning signs before them",
     "sim": sim_speed_compliance},
    {"id": "SPD-VUL", "types": {"speeding"}, "when": lambda c: bool(c["crossings"]) or c["dominant"] == "E3",
     "cm": "traffic_calming", "alts": ["speed_camera", "speed_limit_review"],
     "problem": lambda c, g: f"{_cars(g['n'])} went over the speed limit near a pedestrian crossing ({_where(c, g)})",
     "action": lambda c, g: "Slow cars down before the crossing with speed humps or a raised crossing, and lower the speed limit "
                            f"({'/'.join(f'{x:g}' for x in c['limits']) or '?'} km/h) by 10 km/h",
     "sim": sim_speed_limit},
    {"id": "SPD-URB", "types": {"speeding"}, "when": lambda c: True, "cm": "speed_camera",
     "alts": ["traffic_calming", "speed_limit_review"],
     "problem": lambda c, g: f"{_cars(g['n'])} went over the speed limit ({_where(c, g)})",
     "action": lambda c, g: "Put up a speed camera with signs; if cars are still too fast, lower the limit or add speed humps",
     "sim": sim_speed_compliance},
    # --- wrong-way prevention
    {"id": "WW-RAMP", "types": {"wrong_way"}, "when": lambda c: c["ramp"] or "C4" in c["conditions"],
     "cm": "wrong_way_signage", "alts": ["one_way_barrier"],
     "problem": lambda c, g: f"{_cars(g['n'])} drove the wrong way at a slip road ({_where(c, g)})",
     "action": lambda c, g: "Put WRONG WAY and NO ENTRY signs on both sides of the end of the slip road, and arrows on the road",
     "sim": sim_none("the simulator cannot add new signs, and its cars never drive the wrong way")},
    {"id": "WW-DIV", "types": {"wrong_way"}, "when": lambda c: c["highway"] or "C3" in c["conditions"],
     "cm": "wrong_way_signage", "alts": ["one_way_barrier"],
     "problem": lambda c, g: f"{_cars(g['n'])} drove against the traffic on a divided road ({_where(c, g)})",
     "action": lambda c, g: "Put wrong-way signs and arrows where cars enter this road, and close the gaps in the middle of the road",
     "sim": sim_none("the simulator's cars never drive the wrong way, so there is nothing to compare")},
    {"id": "WW-ONEWAY", "types": {"wrong_way"}, "when": lambda c: True, "cm": "wrong_way_signage", "alts": ["one_way_barrier"],
     "problem": lambda c, g: f"{_cars(g['n'])} drove the wrong way ({_where(c, g)}); that is {c['share_in_area']:.0%} of all problems here",
     "action": lambda c, g: ("Make the road one-way with a physical barrier (more than 40 % of the problems here are wrong-way driving)"
                             if c["share_in_area"] > PARAMS["wrong_way_share"] else
                             "Put ONE WAY and NO ENTRY signs with arrows"),
     "sim": sim_none("the simulator's cars never drive the wrong way, so there is nothing to compare")},
    # --- U-turns
    {"id": "UT-MEDIAN", "types": {"illegal_u_turn"}, "when": lambda c: c["median"] or "D4" in c["conditions"],
     "cm": "median_closure", "alts": ["no_u_turn_signage"],
     "problem": lambda c, g: f"{_cars(g['n'])} made a U-turn through a gap in the middle of the road ({_where(c, g)})",
     "action": lambda c, g: "Close the gap in the middle of the road with a barrier or posts, and give drivers a legal place to turn further on",
     "sim": sim_zone("no_u_turn")},
    {"id": "UT-JUNCTION", "types": {"illegal_u_turn"}, "when": lambda c: c["junction"] or "D3" in c["conditions"],
     "cm": "no_u_turn_signage", "alts": ["signal_timing", "median_closure"],
     "problem": lambda c, g: f"{_cars(g['n'])} made a U-turn at a junction where it is not allowed ({_where(c, g)})",
     "action": lambda c, g: "Put NO U-TURN signs on the roads into the junction; if it has traffic lights, add a separate light for turning",
     "sim": sim_zone("no_u_turn")},
    {"id": "UT-ZONE", "types": {"illegal_u_turn"}, "when": lambda c: True, "cm": "no_u_turn_signage",
     "alts": ["median_closure"],
     "problem": lambda c, g: f"{_cars(g['n'])} made an illegal U-turn ({_where(c, g)})",
     "action": lambda c, g: "Put up NO U-TURN signs, paint a solid line down the middle, and check that drivers follow it",
     "sim": sim_zone("no_u_turn")},
    # --- illegal stopping
    {"id": "STOP-BRIDGE", "types": {"highway_stop"}, "when": lambda c: c["bridge"] or c["tunnel"] or "B5" in c["conditions"],
     "cm": "no_stopping_enforcement", "alts": ["stopping_bay"],
     "problem": lambda c, g: f"{_cars(g['n'])} stopped on a bridge or in a tunnel ({_where(c, g)})",
     "action": lambda c, g: "Ban stopping on the bridge: signs at both ends, red lines on the road, and a camera that spots "
                            "stopped cars and warns other drivers on an electronic sign",
     "sim": sim_remove_stoppers},
    {"id": "STOP-RAMP", "types": {"highway_stop"}, "when": lambda c: c["ramp"] or "B4" in c["conditions"],
     "cm": "no_stopping_enforcement", "alts": ["stopping_bay"],
     "problem": lambda c, g: f"{_cars(g['n'])} stopped near a highway entrance or exit ({_where(c, g)})",
     "action": lambda c, g: "Ban stopping around the entrance or exit, and check its design (is the joining lane long enough, can drivers see well)",
     "sim": sim_remove_stoppers},
    {"id": "STOP-HWY", "types": {"highway_stop"}, "when": lambda c: True, "cm": "no_stopping_enforcement",
     "alts": ["stopping_bay"],
     "problem": lambda c, g: f"{_cars(g['n'])} stopped on the highway ({_where(c, g)})",
     "action": lambda c, g: "Ban stopping here and enforce it, with a warning on an electronic sign; if cars keep stopping, build an emergency stopping place",
     "sim": sim_remove_stoppers},
    {"id": "STOP-ZONE", "types": {"no_parking"}, "when": lambda c: True, "cm": "no_stopping_enforcement",
     "alts": ["stopping_bay"],
     "problem": lambda c, g: f"{_cars(g['n'])} stopped in a no-parking area ({_where(c, g)})",
     "action": lambda c, g: "Enforce the no-parking rule, or make a proper stopping place if people really need to stop here",
     "sim": sim_remove_stoppers},
    # --- pedestrian safety
    {"id": "ZEB-BLOCK", "types": {"zebra_crossing"}, "when": lambda c: c["dominant"] in ("F1", "F2", "F4", None),
     "cm": "keep_clear_box", "alts": ["signal_timing", "crossing_visibility"],
     "problem": lambda c, g: f"{_cars(g['n'])} stopped on a zebra crossing ({_where(c, g)})",
     "action": lambda c, g: "Paint KEEP CLEAR or yellow box markings over the crossing and move the stop line back; "
                            "check whether the traffic lights further on make queues back up onto the crossing",
     "sim": sim_signal_timing},
    {"id": "ZEB-YIELD", "types": {"zebra_crossing"}, "when": lambda c: True, "cm": "crossing_visibility",
     "alts": ["signal_timing"],
     "problem": lambda c, g: f"{_cars(g['n'])} did not let people cross at a zebra crossing ({_where(c, g)})",
     "action": lambda c, g: "Make the crossing easier to see: brighter markings, warning signs before it, a raised crossing or pedestrian lights",
     "sim": sim_signal_timing},
    # --- lane management
    {"id": "LANE-UNSAFE", "types": {"lane_violation"}, "when": lambda c: c["dominant"] in ("A8", "A3"),
     "cm": "marking_refresh", "alts": ["delineators", "speed_camera"],
     "problem": lambda c, g: f"{_cars(g['n'])} changed lanes in an unsafe or banned way ({_where(c, g)})",
     "action": lambda c, g: "Paint a solid line (no lane changes) along this stretch and refresh the markings; "
                            "add signs earlier so drivers pick their lane in time",
     "sim": sim_no_lane_change},
    {"id": "LANE-SOLID", "types": {"lane_violation"}, "when": lambda c: c["dominant"] == "A2",
     "cm": "delineators", "alts": ["marking_refresh"],
     "problem": lambda c, g: f"{_cars(g['n'])} crossed a solid line ({_where(c, g)})",
     "action": lambda c, g: "Put flexible posts (or a small kerb) along the solid line and repaint it",
     "sim": sim_none("the simulator's cars already keep to solid lines, and a barrier would need a new map")},
    {"id": "LANE-WRONG", "types": {"lane_violation"}, "when": lambda c: c["dominant"] == "A1",
     "cm": "lane_use_signage", "alts": ["marking_refresh"],
     "problem": lambda c, g: f"{_cars(g['n'])} were in the wrong lane for where they were going ({_where(c, g)})",
     "action": lambda c, g: "Paint lane arrows and put lane signs over the road before the split",
     "sim": sim_none("lane signs can only be added with a new map")},
    {"id": "LANE-MARKING", "types": {"lane_violation"}, "when": lambda c: True, "cm": "marking_refresh",
     "alts": ["delineators", "lane_allocation"],
     "problem": lambda c, g: f"{_cars(g['n'])} broke lane rules "
                             f"({_where(c, g)})" + (f"; that is {c['share_in_area']:.0%} of all problems here"
                                                     if c["share_in_area"] > PARAMS["lane_share"] else ""),
     "action": lambda c, g: "Repaint the lane markings" + (" and add rumble strips" if c["highway"] else "") +
                            "; if cars keep driving over the lines, look at the lane widths and how the lanes are used",
     "sim": sim_no_lane_change},
    # --- red light (rule off by default)
    {"id": "RL-SIGNAL", "types": {"red_light"}, "when": lambda c: True, "cm": "signal_timing",
     "alts": ["signal_installation"],
     "problem": lambda c, g: f"{_cars(g['n'])} went through a red light ({_where(c, g)})",
     "action": lambda c, g: "Check how long the yellow and all-red lights last, and make the lights easier to see",
     "sim": sim_signal_timing},
    # --- traffic flow (from kinematics)
    {"id": "FLOW-QUEUE", "types": {"flow"}, "when": lambda c: True, "cm": "lane_allocation",
     "alts": ["signal_timing", "no_stopping_enforcement"],
     "problem": lambda c, g: f"Traffic backed up on {_where(c, g)}: up to {_cars(g.get('max_stopped') or 0)} waiting, "
                             f"a queue of {g.get('max_queue_m')} m",
     "action": lambda c, g: ("Check the traffic light timing at the next junction" if c["junction"] else
                             "Remove what causes the queue (stopped or broken-down cars), then check how the lanes are used"),
     "sim": lambda c, g, s: sim_signal_timing(c, g, s) if c["junction"] else
     {"kind": "none", "reason": "there is no traffic light at this queue, and changing the lanes needs a new map; "
                               "if cars stop on this road, test that recommendation first"}},
]

ZONE_GROUPED = {"zebra_crossing", "no_parking"}

SUPPORT = {"speed_compliance": "yes", "speed_limit": "yes", "remove_stopped_vehicles": "yes", "no_lane_change": "yes",
           "signal_timing": "partial", "zone": "rule_side", "none": "no"}

PRIMARY_METRIC = {"speeding": "violations.speeding", "lane_violation": "conflicts_ttc", "wrong_way": "violations.wrong_way",
                  "illegal_u_turn": "violations.illegal_u_turn", "highway_stop": "queue.max_m",
                  "no_parking": "queue.max_m", "zebra_crossing": "violations.zebra_crossing",
                  "red_light": "violations.red_light", "flow": "travel_time.mean_s"}


PLAIN_METRIC = {"speeding": "speeding cars", "lane_violation": "near-misses", "wrong_way": "wrong-way cars",
                "illegal_u_turn": "illegal U-turns", "highway_stop": "long queues", "no_parking": "long queues",
                "zebra_crossing": "problems at the crossing", "red_light": "red-light runs", "flow": "delays"}


def pick_rule(gtype: str, ctx: dict) -> dict:
    for r in RULES:
        if gtype in r["types"] and r["when"](ctx):
            return r
    raise KeyError(f"no rule for {gtype}")


# ------------------------------------------------------------------------------------------ groups

def groups_from_events(events: list[dict], scene: dict | None, params: dict) -> tuple[list[dict], dict]:
    hs = analytics.hotspots(events, eps_m=params["eps_m"], min_events=params["min_events"],
                            major_events=params["major_events"], scene=scene)
    by_id = {e["event_id"]: e for e in events}
    groups = []
    for t, res in hs["by_type"].items():
        for h in res["hotspots"]:
            groups.append({**h, "type": t})
        if not params["emerging"]:
            continue
        rest = [by_id[i] for i in res["noise_event_ids"]]
        per_road = {}
        for e in rest:
            # zone-bound types group by their zone (a crossing spans several roads), the rest by road
            key = (e.get("zone_id") if t in ZONE_GROUPED and e.get("zone_id") else
                   analytics.road_of(e.get("lane_id"), scene) or "-")
            per_road.setdefault(key, []).append(e)
        for road in sorted(per_road):
            evs = sorted(per_road[road], key=lambda e: e["event_id"])
            if len(evs) < params["min_emerging"]:
                continue
            labels = analytics.dbscan(np.array([[e["x"], e["y"]] for e in evs], float), params["eps_m"],
                                      params["min_emerging"])
            for k in sorted(set(labels) - {-1}):
                members = [e for e, l in zip(evs, labels) if l == k]
                g = analytics.group_summary(members, scene)
                g.update(type=t, tier="emerging")
                groups.append(g)
    for g in groups:
        g["positions"] = [[by_id[i]["x"], by_id[i]["y"]] for i in g["event_ids"]]
    return groups, hs


def groups_from_flow(sections: dict | None, params: dict) -> list[dict]:
    if not sections:
        return []
    out = []
    for road, r in sections["roads"].items():
        if r["max_stopped"] < params["queue_min_vehicles"] or r["max_queue_m"] < params["queue_min_m"]:
            continue
        lanes = {l: sections["lanes"][l]["samples"] for l in r["lanes"]}
        out.append({"type": "flow", "tier": "flow", "event_ids": [], "n": 0, "road_ids": {road: len(lanes)},
                    "lane_ids": lanes, "zone_ids": {}, "conditions": {}, "tags": {}, "mean_confidence": None,
                    "statuses": {}, "sessions": [], "period": None, "max_stopped": r["max_stopped"],
                    "max_queue_m": r["max_queue_m"], "mean_speed_kmh": r["mean_speed_kmh"]})
    return out


def _flow_geometry(g: dict, scene: dict | None) -> None:
    """Centroid / polygon of a flow group from its lanes' centrelines (it has no events)."""
    pts = []
    for l in g["lane_ids"]:
        lp = analytics.lane_props(scene, l)
        if lp:
            pts += lp["centreline"]
    pts = np.array(pts or [[0.0, 0.0]], float)
    c = pts.mean(axis=0)
    g["centroid"] = [round(float(c[0]), 2), round(float(c[1]), 2)]
    g["radius_m"] = round(float(np.hypot(*(pts - c).T).max()), 1)
    g["polygon"] = analytics._hull(pts, pad=4.0)
    g["positions"] = []


# ------------------------------------------------------------------------------------------ fields

def rec_id(rule_id: str, gtype: str, g: dict, eps: float) -> str:
    gx, gy = (math.floor(v / eps) for v in g["centroid"])
    key = f"{rule_id}|{gtype}|{','.join(_roads(g))}|{gx},{gy}"
    return "rec-" + hashlib.sha1(key.encode()).hexdigest()[:12]


def priority(gtype: str, g: dict, ctx: dict, params: dict) -> dict:
    sev = SEVERITY.get(gtype, 0.5)
    bumps = []
    if gtype == "highway_stop" and (ctx["bridge"] or ctx["tunnel"]):
        sev, bumps = min(1.0, sev + 0.1), bumps + ["+0.1: on a bridge or in a tunnel, nowhere safe to stop"]
    if gtype == "speeding" and ctx["crossings"]:
        sev, bumps = min(1.0, sev + 0.1), bumps + ["+0.1: near a pedestrian crossing"]
    n = g["n"] if gtype != "flow" else g.get("max_stopped", 0)
    freq = min(1.0, 0.3 + 0.7 * math.log1p(n) / math.log1p(params["major_events"]))
    qual = g["mean_confidence"] if g.get("mean_confidence") is not None else 0.7
    tier = TIER[g["tier"]]
    obj = params["objectives"].get(gtype, 1.0)
    score = round(100 * sev * freq * qual * tier * obj, 1)
    band = "high" if score >= 50 else "medium" if score >= 25 else "low"
    return {"score": score, "band": band,
            "components": {"severity": round(sev, 2), "frequency": round(freq, 3), "evidence_quality": round(qual, 3),
                           "tier": tier, "objective": obj},
            "explanation": (f"Score = 100 x how serious ({sev:.2f}{'; ' + '; '.join(bumps) if bumps else ''}) x how often "
                            f"({freq:.2f}, {n} {'cases' if gtype != 'flow' else 'waiting cars'}) x how sure ({qual:.2f}) x "
                            f"hotspot size ({tier}) x planner weight ({obj}) = {score}. How serious each kind of problem is "
                            "is a set value, not a measured one.")}


def confidence(g: dict, ctx: dict) -> dict:
    if g["tier"] == "flow":
        return {"score": 0.5, "band": "low", "basis": ["queue measured from the tracked cars, in one stretch of video"]}
    q = g["mean_confidence"]
    nr = g["statuses"].get("needs_review", 0) / max(1, g["n"])
    sess = len(g["sessions"])
    score = q * TIER[g["tier"]] * (1 - 0.3 * nr) * (1.0 if sess >= 2 else 0.85)
    basis = [f"the system was {q:.0%} sure on average", TIER_PLAIN.get(g["tier"], g["tier"]),
             f"{nr:.0%} still need a person to check them", f"seen in {sess} recording{'' if sess == 1 else 's'}"]
    return {"score": round(score, 3), "band": "high" if score >= 0.7 else "medium" if score >= 0.45 else "low",
            "basis": basis}


def limitations(g: dict, ctx: dict, sim: dict, params: dict) -> list[str]:
    out = []
    if g["tier"] == "emerging":
        out.append(f"Only {g['n']} cases (a hotspot needs {params['min_events']} within {params['eps_m']:g} m): "
                   "something to watch, not a confirmed hotspot")
    if g["tier"] in ("hotspot", "emerging") and g["n"] < params["major_events"]:
        out.append(f"Fewer than {params['major_events']} cases here, the number for a major hotspot")
    if g.get("period") and g["period"]["span_s"] < 3600:
        out.append(f"All of this comes from {g['period']['span_s'] / 60:.0f} minutes of video, so we cannot see a trend over hours or days")
    if len(g.get("sessions", [])) == 1:
        out.append("Seen in one recording only, so we do not know yet if it keeps happening")
    if any("staged" in z for z in g.get("zone_ids", {})):
        out.append("Some of these were staged on purpose in the simulator, not real traffic")
    if g.get("statuses", {}).get("needs_review"):
        out.append(f"{g['statuses']['needs_review']} of these still need a person to check them")
    if sim["kind"] == "none":
        out.append("This change cannot be tried in the simulator: " + sim.get("reason", ""))
    elif SUPPORT[sim["kind"]] == "rule_side":
        out.append("In the simulator test only the rules change; the simulated drivers do not react to it")
    out.append("The expected effect is a rough guide from studies elsewhere, not a forecast for this place")
    return out


def alternatives(keys: list[str], ctx: dict) -> list[dict]:
    trade = {
        "average_speed_camera": "covers a whole stretch, so drivers do not just brake at one camera; costs more",
        "speed_limit_review": "cheap, but does little unless it is enforced or the road is changed",
        "traffic_calming": "works by itself; slows ambulances too, and is not for highways",
        "speed_camera": "known to work at the spot; only helps near the camera, some people dislike it",
        "one_way_barrier": "makes the problem impossible; changes access for everyone",
        "median_closure": "makes the turn impossible; longer trips, and drivers need a legal place to turn",
        "no_u_turn_signage": "cheap and quick; only works if drivers obey or are checked",
        "signal_timing": "no building work; may move the delay to other roads",
        "stopping_bay": "gives a proper place to stop; needs space, not possible on most bridges",
        "no_stopping_enforcement": "direct; needs patrols or cameras",
        "crossing_visibility": "known to help people crossing; does not stop queues backing up",
        "delineators": "physically stops cars; needs upkeep, gets hit by cars",
        "marking_refresh": "cheap; helps little if the road layout is the problem",
        "lane_allocation": "adds room for traffic; needs a traffic study first",
        "signal_installation": "big effect where there are no lights; costs money and adds waiting",
        "keep_clear_box": "cheap; only works if it is enforced",
        "lane_use_signage": "cheap; helps less with drivers who do not know the road",
    }
    return [{"action": COUNTERMEASURES[k]["name"], "countermeasure": k, "trade_off": trade.get(k, ""),
             "impact_source": COUNTERMEASURES[k]["source"]} for k in keys]


def validation(gtype: str, g: dict, sim: dict, scene: dict | None, params: dict) -> dict:
    sc = dict(params["scenario"])
    sc.update(town=(scene or {}).get("scene"), center=g["centroid"])
    metrics = ["how often each rule is broken", "near-misses (cars less than 1.5 s from hitting each other)",
               "average and high speeds in the area", "time to drive through the area", "how long the queues get"]
    if sim["kind"] == "none":
        how = ("This cannot be tried in the simulator. Instead, film the same place with the drone before and after "
               "the change, for the same length of time, and compare how often it happens per hour")
    else:
        how = (f"Run the simulator twice in the same place: once as it is now and once with the change. Do this "
               f"{len(sc['seeds'])} times, with {sc['vehicles']} cars, for {sc['duration_s'] / 60:g} minutes each")
    return {"method": how, "scenario": sc, "metrics": metrics, "primary_metric": PRIMARY_METRIC[gtype],
            "success_criterion": f"It works if there are fewer {PLAIN_METRIC[gtype]} with the change in every run, "
                                 "and traffic does not get slower or more dangerous",
            "runner": "venv_sim\\Scripts\\python.exe simulation\\planning\\validate_recommendation.py "
                      "--recs <recommendations.json> --id <id>"}


def evidence(g: dict, ctx: dict, events_by_id: dict) -> dict:
    clips = [events_by_id[i].get("evidence", {}).get("clip") for i in g["event_ids"]]
    return {"event_ids": g["event_ids"], "count": g["n"], "by_condition": g.get("conditions", {}),
            "by_status": g.get("statuses", {}), "tags": g.get("tags", {}), "period": g.get("period"),
            "sessions": g.get("sessions", []), "mean_event_confidence": g.get("mean_confidence"),
            "share_of_events_in_area": ctx["share_in_area"], "events_in_area": ctx["events_in_area"],
            "clips": [c for c in clips if c], "sections": ctx["sections"],
            **({"queue": {"max_stopped": g["max_stopped"], "max_queue_m": g["max_queue_m"],
                          "mean_speed_kmh": g["mean_speed_kmh"]}} if g["tier"] == "flow" else {})}


def link_flow_to_stops(recs: list[dict]) -> None:
    """A queue on a road that also has an illegal-stopping recommendation is probably caused by it:
    say so on the flow recommendation (and point to the other one)."""
    stops = [r for r in recs if r["type"] in ("highway_stop", "no_parking")]
    for r in recs:
        if r["type"] != "flow":
            continue
        rel = [s["id"] for s in stops if set(s["location"]["road_ids"]) & set(r["location"]["road_ids"])]
        if rel:
            r["evidence"]["related_recommendations"] = rel
            r["limitations"].insert(0, "This queue may be caused by the stopped cars in " + ", ".join(rel) +
                                    ": test that recommendation first")


def _sections(kinematics, scene):
    if kinematics is None or scene is None:
        return None
    if isinstance(kinematics, dict) and "lanes" in kinematics:
        return kinematics
    if isinstance(kinematics, (str, Path)):
        kinematics = analytics.load_kinematics(Path(kinematics))
    return analytics.section_stats(kinematics, scene)


def recommend(events: list[dict], scene: dict | None = None, kinematics=None, params: dict | None = None,
              history=None) -> list[dict]:
    """Recommendations for the counted events (see module docstring). kinematics: kinematics rows, a
    kinematics.csv path, or a precomputed analytics.section_stats() dict. history: a history.History
    (or its JSONL path) whose measured outcomes re-weight the priority."""
    p = {**PARAMS, **(params or {})}
    ev = analytics.counted(events)
    by_id = {e["event_id"]: e for e in ev}
    sections = _sections(kinematics, scene)
    groups, _ = groups_from_events(ev, scene, p)
    for g in groups_from_flow(sections, p):
        _flow_geometry(g, scene)
        groups.append(g)
    recs = []
    for g in groups:
        gtype = g["type"]
        ctx = context(g, gtype, ev, scene, sections, p)
        rule = pick_rule(gtype, ctx)
        sim_change = rule["sim"](ctx, g, scene)
        sim = {"supported": SUPPORT[sim_change["kind"]], "town": (scene or {}).get("scene"), "change": sim_change,
               "scenario": {**p["scenario"], "center": g["centroid"]}}
        period = g["period"]["span_s"] if g.get("period") else None
        recs.append({
            "id": rec_id(rule["id"], gtype, g, p["eps_m"]),
            "rule_id": rule["id"], "type": gtype, "area": AREA.get(gtype, gtype), "tier": g["tier"],
            "status": "proposed",
            "problem": rule["problem"](ctx, g),
            "location": {"scene": (scene or {}).get("scene"), "lane_ids": _lanes(g), "road_ids": _roads(g),
                         "zone_ids": sorted(g.get("zone_ids", {})), "centroid": g["centroid"],
                         "radius_m": g.get("radius_m"), "polygon": g["polygon"],
                         "features": {k: ctx[k] for k in ("bridge", "tunnel", "ramp", "junction", "median", "highway")},
                         "speed_limits_kmh": ctx["limits"], "nearby_crossings": ctx["crossings"]},
            "evidence": evidence(g, ctx, by_id),
            "action": {"countermeasure": rule["cm"], "summary": rule["action"](ctx, g),
                       "catalogue_name": COUNTERMEASURES[rule["cm"]]["name"]},
            "expected_impact": projected(rule["cm"], g["n"] if gtype != "flow" else g.get("max_stopped", 0), period),
            "priority": priority(gtype, g, ctx, p),
            "validation_method": validation(gtype, g, sim_change, scene, p),
            "confidence": confidence(g, ctx),
            "limitations": limitations(g, ctx, sim_change, p),
            "alternatives": alternatives(rule["alts"], ctx),
            "simulation": sim,
            "generated_by": {"module": "ml/planning/recommend.py", "version": VERSION,
                             "params": {k: p[k] for k in ("eps_m", "min_events", "major_events", "min_emerging")}},
        })
        if gtype == "speeding":
            ms = _max_speed(g, ev)
            if ms is not None:
                recs[-1]["evidence"]["max_speed_kmh"] = ms
    link_flow_to_stops(recs)
    # same id twice (two groups snapped to one grid cell): keep both, suffix in stable order
    seen = Counter()
    for r in sorted(recs, key=lambda r: (r["id"], r["evidence"]["event_ids"])):
        seen[r["id"]] += 1
        if seen[r["id"]] > 1:
            r["id"] = f"{r['id']}-{seen[r['id']]}"
    if history is not None:
        from history import History, rerank
        h = history if isinstance(history, History) else History(Path(history))
        recs = rerank(recs, h)
    recs.sort(key=lambda r: (-r["priority"]["score"], r["id"]))
    return recs


# ------------------------------------------------------------------------------------------ CLI

def load_events(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        evs = json.loads(Path(p).read_text())
        session = Path(p).parents[2].name if len(Path(p).parents) > 2 else ""
        for e in evs:
            e = dict(e)
            e.setdefault("session_id", e["event_id"].rsplit("-", 1)[0] or session)
            out.append(e)
    return out


def load_kinematics_multi(paths: list[Path]) -> list[dict]:
    """Rows of several kinematics.csv files, track ids made unique per file (they restart at 1)."""
    rows = []
    for i, p in enumerate(paths):
        for r in analytics.load_kinematics(Path(p)):
            r["track_id"] = f"{i}:{r['track_id']}"
            rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, nargs="+", required=True, help="violations.json files")
    ap.add_argument("--scene", type=Path, default=None, help="lane map JSON")
    ap.add_argument("--kinematics", type=Path, nargs="*", default=[], help="kinematics.csv files (flow rule, sections)")
    ap.add_argument("--history", type=Path, default=None, help="planner history JSONL (re-ranks by measured outcomes)")
    ap.add_argument("--params", type=Path, default=None, help="JSON overriding PARAMS")
    ap.add_argument("--no-emerging", action="store_true", help="hotspots only (PRD thresholds)")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    params = json.loads(a.params.read_text()) if a.params else {}
    if a.no_emerging:
        params["emerging"] = False
    scene = json.loads(a.scene.read_text()) if a.scene else None
    events = load_events(a.events)
    kin = load_kinematics_multi(a.kinematics) if a.kinematics else None
    recs = recommend(events, scene, kin, params=params, history=a.history)
    ana = analytics.hotspots(analytics.counted(events), scene=scene,
                             **{k: v for k, v in {**PARAMS, **params}.items() if k in ("eps_m", "min_events", "major_events")})
    doc = {"generated": date.today().isoformat(), "version": VERSION,
           "inputs": {"events": [str(p) for p in a.events], "scene": str(a.scene) if a.scene else None,
                      "kinematics": [str(p) for p in a.kinematics]},
           "summary": {"events": len(events), "counted": len(analytics.counted(events)),
                       "recommendations": len(recs), "by_rule": dict(Counter(r["rule_id"] for r in recs)),
                       "by_tier": dict(Counter(r["tier"] for r in recs)),
                       "by_band": dict(Counter(r["priority"]["band"] for r in recs)),
                       "simulable": dict(Counter(r["simulation"]["supported"] for r in recs)),
                       "hotspot_notes": {t: v["note"] for t, v in ana["by_type"].items()}},
           "recommendations": recs}
    txt = json.dumps(doc, indent=1)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(txt)
        print(f"[recommend] {len(recs)} recommendations -> {a.out}")
    print(json.dumps(doc["summary"], indent=1))
    for r in recs:
        print(f"  {r['id']} {r['priority']['score']:5.1f} {r['priority']['band']:6s} {r['tier']:9s} {r['rule_id']:12s} "
              f"{r['simulation']['supported']:9s} {r['problem']}")


if __name__ == "__main__":
    main()
