"""Catalogue of what a planner can add to a traffic plan (Scenario_Builder_Plan.md Section 3 and 4).

One entry per kind: group (prop / rule / scenario), label, parameters (the UI builds its form from them and
plans.validate checks them), where it acts ("point": at the clicked position, "lane": a lane / road section / road,
"area": a rectangle at the position), how the violation-engine REPLAY can judge it (`replay`), and what CARLA can do
with it (`carla`, Phase 2). Elements the replay cannot judge are kept in the plan but never produce numbers.

`replay.mode` values (ml/planning/plans.py compile):
    speed_limit        lanes get speed_limit_kmh                       (lane_overrides)
    restrict:<v>       lanes get restricted = bus | emergency | restricted
    one_way            lanes get one_way = true
    closure            lanes under the footprint / radius get restricted = "restricted" (approximation: vehicles using
                       the closed lane are flagged; traffic does not re-route in a replay)
    zone:<type>        a rectangle zone of an engine zone type (no_parking, crosswalk, no_u_turn, highway, speed)
    none               not evaluated by the replay (reason in replay.note)
"""

NUM, ENUM, BOOL, TEXT = "number", "enum", "bool", "text"

SCOPE = {"key": "scope", "label": "Applies to", "type": ENUM, "options": ["lane", "section", "road"], "default": "lane",
         "help": "this lane only, the whole road section, or the whole road (both directions)"}


def p(key, label, type_=NUM, default=None, min_=None, max_=None, unit=None, options=None, help_=None):
    d = {"key": key, "label": label, "type": type_, "default": default}
    for k, v in (("min", min_), ("max", max_), ("unit", unit), ("options", options), ("help", help_)):
        if v is not None:
            d[k] = v
    return d


BEHAVIOUR_OPTS = ["normal", "cautious", "aggressive", "mixed"]
MIX_OPTS = ["type", "city", "cars", "freight", "transit", "two_wheelers", "commercial"]
ZONE_SPEED = p("speed_factor", "Speed against the limit inside", NUM, 1.0, 0.3, 1.5, "x", help_="0.6 = 40 % slower inside the zone")
ZONE_BIAS = p("bias", "Extra vehicles are mostly", ENUM, "none", options=["none", "trucks", "buses", "two_wheelers"])
VIA = [p("via_x", "Via x (waypoint)", NUM, None, help_="the route goes through this point first"), p("via_y", "Via y (waypoint)", NUM, None)]

RECT = [p("length_m", "Length", NUM, 20, 2, 500, "m"), p("width_m", "Width", NUM, 4, 1, 100, "m")]

C_OK = lambda how: {"status": "supported", "how": how}  # noqa: E731
C_APPROX = lambda how: {"status": "approximate", "how": how}  # noqa: E731
C_NO = lambda how: {"status": "unsupported", "how": how}  # noqa: E731
R = lambda mode, note="": {"mode": mode, "note": note}  # noqa: E731
NOT_REPLAYED = "Needs a simulation: hypothetical vehicles / signal behaviour have no recorded traffic to replay."

KINDS: dict[str, dict] = {
    # ------------------------------------------------------------------ props
    "traffic_signal": {"group": "prop", "label": "Traffic signal / intelligent light", "target": "point",
        "params": [p("cycle_s", "Cycle", NUM, 90, 20, 240, "s"), p("green_s", "Green", NUM, 40, 5, 200, "s"),
                   p("adaptive", "Adaptive (extends green with demand)", BOOL, False)],
        "replay": R("none", NOT_REPLAYED), "carla": C_NO("a new signal cannot be created at run time; needs an OpenDRIVE signal in a regenerated map, or a virtual stop-and-go approximation")},
    "stop_sign": {"group": "prop", "label": "Stop sign", "target": "point", "params": [],
        "replay": R("none", "The engine has no stop-sign rule."), "carla": C_APPROX("virtual stop line in the runner")},
    "speed_limit_sign": {"group": "prop", "label": "Speed limit sign", "target": "point",
        "params": [p("limit_kmh", "Limit", NUM, 50, 10, 130, "km/h"), p("reach_m", "Lanes within", NUM, 15, 5, 60, "m", help_="lanes whose centreline passes within this distance of the sign")],
        "replay": R("speed_limit", "Applied to the lanes near the sign."), "carla": C_OK("traffic-manager target speed + sign prop")},
    "warning_sign": {"group": "prop", "label": "Warning sign", "target": "point",
        "params": [p("text", "Warning", ENUM, "general", options=["general", "pedestrians", "school", "road_works", "curve", "slippery"])],
        "replay": R("none", "A warning has no rule the engine can judge."), "carla": C_OK("static sign prop (visual only)")},
    "barrier": {"group": "prop", "label": "Road barrier", "target": "area", "params": RECT[:1] + [p("width_m", "Width", NUM, 2, 0.5, 20, "m")],
        "replay": R("closure", "Approximation: lanes under the barrier become restricted."), "carla": C_OK("static barrier props along the footprint (verify blueprint names with list_blueprints)")},
    "cones": {"group": "prop", "label": "Traffic cones", "target": "area", "params": [p("length_m", "Row length", NUM, 10, 1, 200, "m"), p("width_m", "Width", NUM, 2, 0.5, 20, "m"), p("spacing_m", "Spacing", NUM, 2, 0.5, 10, "m")],
        "replay": R("closure", "Approximation: lanes under the cones become restricted."), "carla": C_OK("cone props at the spacing (verify blueprint names)")},
    "barricade": {"group": "prop", "label": "Barricade", "target": "area", "params": [p("length_m", "Length", NUM, 4, 1, 100, "m"), p("width_m", "Width", NUM, 1, 0.3, 20, "m")],
        "replay": R("closure", "Approximation: lanes under the barricade become restricted."), "carla": C_OK("barricade props (verify blueprint names)")},
    "pedestrian_crossing": {"group": "prop", "label": "Pedestrian crossing", "target": "area",
        "params": [p("length_m", "Across the road", NUM, 8, 3, 40, "m"), p("width_m", "Along the road", NUM, 4, 2, 10, "m")],
        "replay": R("zone:crosswalk", "Crossing zone: the engine flags vehicles that fail to give way."), "carla": C_APPROX("crossing markings are map data; pedestrians can be spawned to use it")},
    "speed_breaker": {"group": "prop", "label": "Speed breaker", "target": "area",
        "params": [p("width_m", "Width", NUM, 4, 1, 12, "m"), p("height_cm", "Height", NUM, 10, 3, 20, "cm"), p("cap_kmh", "Approach speed", NUM, 20, 5, 50, "km/h")],
        "replay": R("none", "Drivers slowing for a breaker are a behaviour change; not replayable."), "carla": C_APPROX("speed cap near the breaker in the traffic manager")},
    "parking_zone": {"group": "prop", "label": "Parking zone", "target": "area", "params": RECT,
        "replay": R("none", "Allowed parking creates no violation."), "carla": C_APPROX("rule-side zone; parked vehicles can be spawned")},
    "no_parking_zone": {"group": "prop", "label": "No-parking zone", "target": "area", "params": RECT + [p("grace_s", "Grace before flag", NUM, 30, 0, 600, "s", help_="short = no stopping")],
        "replay": R("zone:no_parking"), "carla": C_APPROX("rule-side zone (the engine judges it); vehicles are not blocked")},
    "bus_stop": {"group": "prop", "label": "Bus stop", "target": "area", "params": [p("length_m", "Length", NUM, 12, 5, 40, "m"), p("width_m", "Width", NUM, 3, 2, 8, "m")],
        "replay": R("none", "Stopping at a bus stop is allowed; no rule to judge."), "carla": C_OK("stop prop + buses that stop there")},
    "bus_lane": {"group": "prop", "label": "Dedicated bus lane", "target": "lane", "params": [SCOPE],
        "replay": R("restrict:bus"), "carla": C_APPROX("traffic-manager lane rules; other vehicles only approximately kept out")},
    "emergency_lane": {"group": "prop", "label": "Emergency vehicle priority lane", "target": "lane", "params": [SCOPE],
        "replay": R("restrict:emergency"), "carla": C_APPROX("lane reserved in the runner; emergency vehicle with lights")},
    "construction_zone": {"group": "prop", "label": "Road construction zone", "target": "area",
        "params": [p("length_m", "Length", NUM, 30, 5, 500, "m"), p("width_m", "Width", NUM, 4, 1, 40, "m")],
        "replay": R("closure", "Approximation: lanes under the zone become restricted."), "carla": C_OK("construction props + blocked lane")},
    "custom_object": {"group": "prop", "label": "Custom object", "target": "area",
        "params": [p("name", "Name", TEXT, "object"), p("length_m", "Length", NUM, 2, 0.1, 100, "m"), p("width_m", "Width", NUM, 2, 0.1, 100, "m"), p("height_m", "Height", NUM, 2, 0.1, 50, "m")],
        "replay": R("none", "A free object has no traffic rule."), "carla": C_OK("box prop of that size (visual / collision)")},
    # ------------------------------------------------------------------ rules
    "speed_limit": {"group": "rule", "label": "Speed limit", "target": "lane", "params": [p("speed_kmh", "Limit", NUM, 50, 5, 130, "km/h"), SCOPE],
        "replay": R("speed_limit"), "carla": C_OK("traffic-manager target speed on those lanes")},
    "no_parking": {"group": "rule", "label": "No parking / no stopping", "target": "area", "params": RECT + [p("grace_s", "Grace before flag", NUM, 30, 0, 600, "s", help_="short = no stopping")],
        "replay": R("zone:no_parking"), "carla": C_APPROX("rule-side zone")},
    "lane_restriction": {"group": "rule", "label": "Lane restriction", "target": "lane", "params": [p("allowed", "Reserved for", ENUM, "bus", options=["bus", "emergency", "restricted"]), SCOPE],
        "replay": R("restrict:bus", "mode follows the 'Reserved for' value"), "carla": C_APPROX("traffic-manager lane rules")},
    "one_way": {"group": "rule", "label": "One-way traffic", "target": "lane", "params": [SCOPE],
        "replay": R("one_way", "Vehicles driving against the lane direction are flagged (wrong way)."), "carla": C_APPROX("cannot change OpenDRIVE lane directions live")},
    "restricted_entry": {"group": "rule", "label": "Restricted vehicle entry", "target": "lane", "params": [p("vehicle", "Vehicles kept out", ENUM, "truck", options=["truck", "bus", "car", "motorcycle"]), SCOPE],
        "replay": R("restrict:restricted", "Approximation: the lane is restricted for everyone in the replay (the engine has no per-class entry rule)."), "carla": C_APPROX("traffic-manager lane rules")},
    "signal_timing": {"group": "rule", "label": "Signal timing change", "target": "point",
        "params": [p("green_delta_s", "Green time change", NUM, 10, -30, 60, "s"), p("radius_m", "Signals within", NUM, 40, 5, 200, "m")],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("TrafficLight.set_green_time for signals within the radius")},
    "pedestrian_priority": {"group": "rule", "label": "Pedestrian priority", "target": "area",
        "params": [p("length_m", "Across the road", NUM, 8, 3, 40, "m"), p("width_m", "Along the road", NUM, 4, 2, 10, "m")],
        "replay": R("zone:crosswalk", "Vehicles that do not give way in the zone are flagged."), "carla": C_APPROX("pedestrians spawned to cross; yielding is up to the traffic manager")},
    "emergency_priority": {"group": "rule", "label": "Emergency vehicle priority", "target": "lane", "params": [SCOPE],
        "replay": R("restrict:emergency"), "carla": C_APPROX("other vehicles yield only approximately")},
    "time_restriction": {"group": "rule", "label": "Time-based restriction (uses the schedule)", "target": "lane",
        "params": [p("restriction", "Restriction", ENUM, "bus", options=["bus", "emergency", "restricted"]), SCOPE],
        "replay": R("restrict:bus", "The replay applies it for the whole recorded session; the schedule is stored but not applied."), "carla": C_OK("switched on / off at the schedule")},
    "custom_zone_rule": {"group": "rule", "label": "Custom violation condition (zone)", "target": "area",
        "params": RECT + [p("zone_type", "Condition", ENUM, "no_parking", options=["no_parking", "no_u_turn", "crosswalk", "highway", "speed"]),
                          p("grace_s", "Grace (no_parking)", NUM, 30, 0, 600, "s"), p("limit_kmh", "Limit (speed)", NUM, 30, 5, 130, "km/h")],
        "replay": R("zone:custom"), "carla": C_APPROX("rule-side zone")},
    # ------------------------------------------------------------------ scenarios
    "spawn_vehicles": {"group": "scenario", "label": "Spawn vehicles / group", "target": "point",
        "params": [p("count", "Vehicles", NUM, 5, 1, 200), p("vehicle", "Type", ENUM, "car", options=["car", "van", "truck", "bus", "motorcycle", "ambulance", "pedestrian"]),
                   p("speed_kmh", "Target speed", NUM, 40, 5, 130, "km/h"), p("spacing_s", "Gap between departures", NUM, 1.5, 0.3, 30, "s"),
                   p("behaviour", "Drivers", ENUM, "normal", options=BEHAVIOUR_OPTS, help_="cautious: long gaps; aggressive: short gaps, over the limit; mixed: the background mix"),
                   p("dest_x", "Destination x", NUM, None), p("dest_y", "Destination y", NUM, None)] + VIA,
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("spawn at the position, route to the destination")},
    "traffic_flow": {"group": "scenario", "label": "Custom traffic flow (continuous source)", "target": "point",
        "params": [p("vph", "Vehicles per hour", NUM, 600, 30, 3600, "veh/h"), p("mix", "Vehicle mix", ENUM, "type", options=MIX_OPTS, help_="'type' uses the single type below; the others draw each vehicle from a mix"),
                   p("vehicle", "Type (when mix is 'type')", ENUM, "car", options=["car", "van", "truck", "bus", "motorcycle", "ambulance"]),
                   p("speed_kmh", "Target speed", NUM, 40, 5, 130, "km/h"),
                   p("profile", "Rate over the active window", ENUM, "constant", options=["constant", "ramp_up", "ramp_down", "pulse"]),
                   p("platoon_size", "Vehicles per burst (platoon)", NUM, 1, 1, 20), p("max_vehicles", "Stop after (0 = never)", NUM, 0, 0, 2000),
                   p("behaviour", "Drivers", ENUM, "normal", options=BEHAVIOUR_OPTS),
                   p("dest_x", "Destination x", NUM, None), p("dest_y", "Destination y", NUM, None)] + VIA,
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("a spawner like generate_traffic: vehicles at the rate, routed to the destination")},
    "traffic_density": {"group": "scenario", "label": "Traffic density / demand", "target": "area", "params": [p("radius_m", "Radius", NUM, 150, 20, 1000, "m"), p("factor", "Demand factor", NUM, 1.5, 0.1, 4), ZONE_SPEED, ZONE_BIAS],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("vehicle count in the area scaled by the factor")},
    "congestion_jam": {"group": "scenario", "label": "Traffic jam", "target": "area", "params": [p("radius_m", "Radius", NUM, 60, 10, 500, "m"), p("duration_s", "Duration", NUM, 300, 30, 3600, "s")],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("stopped vehicles at the position")},
    "peak_hour": {"group": "scenario", "label": "Peak-hour congestion", "target": "area", "params": [p("radius_m", "Radius", NUM, 300, 20, 2000, "m"), p("factor", "Demand factor", NUM, 2, 1, 4), p("duration_s", "Duration", NUM, 600, 60, 7200, "s"), ZONE_SPEED, ZONE_BIAS],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("demand raised over the duration")},
    "bottleneck": {"group": "scenario", "label": "Bottleneck", "target": "lane", "params": [p("lanes_closed", "Lanes closed", NUM, 1, 1, 4), SCOPE],
        "replay": R("closure", "Approximation: the lanes become restricted."), "carla": C_OK("blocked lanes + props")},
    "demand_surge": {"group": "scenario", "label": "Unexpected traffic surge", "target": "area", "params": [p("radius_m", "Radius", NUM, 200, 20, 2000, "m"), p("factor", "Demand factor", NUM, 2.5, 1, 5), p("ramp_s", "Ramp-up", NUM, 60, 5, 600, "s"), ZONE_SPEED, ZONE_BIAS],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("spawn rate raised after the ramp")},
    "road_closure": {"group": "scenario", "label": "Road / lane closure", "target": "lane", "params": [SCOPE],
        "replay": R("closure", "Approximation: the lanes become restricted; traffic does not re-route in a replay."), "carla": C_OK("blocked lanes; the traffic manager re-routes new vehicles only")},
    "accident": {"group": "scenario", "label": "Accident", "target": "point", "params": [p("lanes_blocked", "Lanes blocked", NUM, 1, 1, 4), p("duration_s", "Duration", NUM, 600, 30, 7200, "s")],
        "replay": R("closure", "Approximation: the lane at the position becomes restricted."), "carla": C_OK("crashed vehicles + warning props")},
    "breakdown": {"group": "scenario", "label": "Vehicle breakdown", "target": "point", "params": [p("vehicle", "Type", ENUM, "car", options=["car", "van", "truck", "bus"]), p("duration_s", "Duration", NUM, 300, 30, 3600, "s")],
        "replay": R("closure", "Approximation: the lane at the position becomes restricted."), "carla": C_OK("stopped vehicle in the lane")},
    "signal_ops": {"group": "scenario", "label": "Signal operation change", "target": "area", "params": [p("radius_m", "Signals within", NUM, 60, 5, 300, "m"), p("mode", "Mode", ENUM, "flashing", options=["flashing", "off", "all_red", "fixed_long_green"])],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("traffic-light state control")},
    "diversion": {"group": "scenario", "label": "Alternative route / diversion", "target": "point", "params": [p("dest_x", "Diverted to x", NUM, None), p("dest_y", "Diverted to y", NUM, None), p("share_pct", "Share of vehicles", NUM, 50, 5, 100, "%")],
        "replay": R("none", NOT_REPLAYED), "carla": C_APPROX("routes of spawned vehicles only")},
    "emergency_vehicle": {"group": "scenario", "label": "Emergency vehicle run", "target": "point", "params": [p("vehicle", "Type", ENUM, "ambulance", options=["ambulance", "police", "fire"]), p("speed_kmh", "Speed", NUM, 70, 20, 120, "km/h"), p("dest_x", "Destination x", NUM, None), p("dest_y", "Destination y", NUM, None)],
        "replay": R("none", NOT_REPLAYED), "carla": C_APPROX("vehicle with lights; others yield approximately")},
    "special_event": {"group": "scenario", "label": "Special event demand", "target": "area", "params": [p("radius_m", "Radius", NUM, 250, 20, 2000, "m"), p("factor", "Demand factor", NUM, 3, 1, 6), p("duration_s", "Duration", NUM, 1800, 60, 14400, "s"), ZONE_SPEED, ZONE_BIAS],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("demand + pedestrians around the position")},
    "pedestrian_crowd": {"group": "scenario", "label": "Pedestrian crowd", "target": "point", "params": [p("count", "Pedestrians", NUM, 20, 1, 300), p("dest_x", "Walk to x", NUM, None), p("dest_y", "Walk to y", NUM, None)],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("walkers spawned with the AI controller")},
    "weather": {"group": "scenario", "label": "Weather", "target": "point", "params": [p("preset", "Weather", ENUM, "rain", options=["clear", "cloudy", "rain", "heavy_rain", "fog", "night"])],
        "replay": R("none", NOT_REPLAYED), "carla": C_OK("carla.WeatherParameters preset")},
}

GROUPS = ("prop", "rule", "scenario")

# What the virtual traffic simulator (microsim.py) does with each kind; the rest is "not simulated" and says so.
V = lambda st, how: {"status": st, "how": how}  # noqa: E731
VIRTUAL = {
    "speed_limit": V("supported", "lane speed limit"), "speed_limit_sign": V("supported", "limit on the lanes near the sign"),
    "barrier": V("supported", "the lane is blocked"), "cones": V("supported", "the lane is blocked"), "barricade": V("supported", "the lane is blocked"),
    "construction_zone": V("supported", "the lanes under it are blocked"), "road_closure": V("supported", "lanes blocked, vehicles route around where the map allows"),
    "bottleneck": V("supported", "lanes blocked"), "accident": V("supported", "stopped vehicles in the lane for the duration"),
    "breakdown": V("supported", "a stopped vehicle in the lane for the duration"), "congestion_jam": V("supported", "a row of stopped vehicles"),
    "speed_breaker": V("approximate", "speed cap on the lane"), "bus_lane": V("supported", "only buses / emergency vehicles may enter"),
    "emergency_lane": V("supported", "only emergency vehicles may enter"), "lane_restriction": V("approximate", "lane closed to other vehicle types"),
    "restricted_entry": V("approximate", "lane closed to everyone"), "emergency_priority": V("approximate", "lane reserved for emergency vehicles"),
    "time_restriction": V("approximate", "applied for the whole run"),
    "spawn_vehicles": V("supported", "vehicles spawned one by one at the position, routed to the destination"),
    "traffic_flow": V("supported", "continuous source at the rate"), "emergency_vehicle": V("approximate", "faster vehicle; others do not yield"),
    "traffic_density": V("supported", "extra background vehicles in the radius"), "peak_hour": V("supported", "extra background vehicles in the radius"),
    "demand_surge": V("supported", "extra background vehicles in the radius"), "special_event": V("supported", "extra background vehicles in the radius"),
    "weather": V("approximate", "desired speeds scaled"),
}
VIRTUAL.update({
    "traffic_signal": V("supported", "virtual signal: stop lines on the lanes passing it, two phases with clearance, optional adaptive green"),
    "stop_sign": V("supported", "vehicles stop at the line for 3 s"), "signal_timing": V("supported", "changes the green of virtual signals within the radius"),
    "signal_ops": V("supported", "flashing / off, all red or a fixed long green on virtual signals within the radius"),
})
NOT_SIM = V("unsupported", "not simulated by the virtual model")
for _k, _v in KINDS.items():
    _v["virtual"] = VIRTUAL.get(_k, NOT_SIM)
CLOSURE_KINDS = [k for k, v in KINDS.items() if v["replay"]["mode"] == "closure"]

# ready-made starting points (Section 7E); each item is {group, kind, params, x_offset, y_offset} relative to the clicked position
TEMPLATES = {
    "rush_hour": {"label": "Rush-hour congestion", "items": [{"group": "scenario", "kind": "peak_hour", "params": {"factor": 2, "duration_s": 900}}]},
    "road_closure": {"label": "Road closure", "items": [{"group": "scenario", "kind": "road_closure", "params": {"scope": "section"}}]},
    "accident_response": {"label": "Accident response", "items": [{"group": "scenario", "kind": "accident", "params": {"lanes_blocked": 1, "duration_s": 600}},
                                                                  {"group": "prop", "kind": "cones", "params": {"length_m": 20}}]},
    "emergency_access": {"label": "Emergency vehicle access", "items": [{"group": "prop", "kind": "emergency_lane", "params": {"scope": "section"}},
                                                                         {"group": "scenario", "kind": "emergency_vehicle", "params": {}}]},
    "construction_diversion": {"label": "Construction diversion", "items": [{"group": "prop", "kind": "construction_zone", "params": {"length_m": 40}},
                                                                            {"group": "rule", "kind": "speed_limit", "params": {"speed_kmh": 30, "scope": "section"}}]},
    "special_event": {"label": "Special-event traffic", "items": [{"group": "scenario", "kind": "special_event", "params": {}},
                                                                    {"group": "scenario", "kind": "pedestrian_crowd", "params": {"count": 40}}]},
}


def catalogue() -> dict:
    """For GET /api/plans/catalogue."""
    return {"kinds": {k: {**v, "key": k} for k, v in KINDS.items()}, "groups": list(GROUPS), "templates": TEMPLATES,
            "evidence_levels": ["preliminary", "replay-validated", "simulation-validated", "insufficient data"]}
