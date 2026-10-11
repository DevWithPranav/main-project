"""Custom traffic configuration (Scenario_Builder_Plan.md, virtual traffic): everything a planner can set about the traffic that
the virtual simulator (microsim.py) runs on the map, with or without a plan.

A config is a plain dict (stored in the plan document as `traffic`, or sent on its own to POST /api/plans/traffic):

    {preset, seed, duration_s, warmup_s,
     background: {enabled, density_per_km, demand_profile, road_weight{urban, highway}, mix{car, van, truck, bus, motorcycle},
                  speed_factor_mean, speed_factor_sd, behaviour_mix{cautious, normal, aggressive}}}

Sources (continuous flows), vehicle groups, local demand zones, incidents and weather are plan items (plan_catalogue.py kinds
`traffic_flow`, `spawn_vehicles`, `traffic_density` ..., accident, breakdown, congestion_jam, weather); this module holds what is global.

`SCHEMA` describes every field (type, range, unit, help) so the UI draws the form from it; `normalize` fills defaults and
`validate` returns the problems. Nothing here simulates: microsim.py reads the result.
"""

import copy
import math

import numpy as np

VEHICLE_TYPES = ["car", "van", "truck", "bus", "motorcycle"]
BEHAVIOURS = ["cautious", "normal", "aggressive"]

# multipliers on the car-following parameters of a driver (microsim.Veh): time headway T, max acceleration a, comfortable
# deceleration b, minimum gap s0, and the desired speed against the limit
BEHAVIOUR = {
    "cautious": {"T": 1.35, "a": 0.8, "b": 0.9, "s0": 1.3, "speed": 0.92},
    "normal": {"T": 1.0, "a": 1.0, "b": 1.0, "s0": 1.0, "speed": 1.0},
    "aggressive": {"T": 0.7, "a": 1.3, "b": 1.15, "s0": 0.8, "speed": 1.12},
}

# demand over the run: (fraction of the measured period, factor on the background density); linear between the points
PROFILES = {
    "constant": [(0.0, 1.0), (1.0, 1.0)],
    "morning_peak": [(0.0, 0.55), (0.35, 1.5), (0.7, 1.2), (1.0, 0.8)],
    "evening_peak": [(0.0, 0.8), (0.5, 1.5), (1.0, 1.0)],
    "event_surge": [(0.0, 0.7), (0.4, 0.7), (0.5, 2.0), (0.8, 2.0), (1.0, 1.0)],
    "night_lull": [(0.0, 0.5), (1.0, 0.5)],
    "ramp_up": [(0.0, 0.3), (1.0, 1.6)],
    "ramp_down": [(0.0, 1.6), (1.0, 0.3)],
}
PROFILE_LABELS = {"constant": "Constant", "morning_peak": "Morning peak", "evening_peak": "Evening peak", "event_surge": "Event surge",
                  "night_lull": "Night lull", "ramp_up": "Building up", "ramp_down": "Easing off"}

# vehicle mixes for a source (shares, any scale)
SOURCE_MIX = {
    "city": {"car": 84, "van": 6, "truck": 5, "bus": 3, "motorcycle": 2},
    "cars": {"car": 100},
    "freight": {"truck": 55, "van": 30, "car": 15},
    "transit": {"bus": 100},
    "two_wheelers": {"motorcycle": 100},
    "commercial": {"van": 50, "truck": 30, "car": 20},
}

DEFAULTS = {
    "preset": "moderate", "seed": 0, "duration_s": 120.0, "warmup_s": 20.0,
    "background": {
        "enabled": True, "density_per_km": 7.0, "demand_profile": "constant", "road_weight": {"urban": 1.0, "highway": 1.0},
        "mix": dict(SOURCE_MIX["city"]), "speed_factor_mean": 1.0, "speed_factor_sd": 0.10,
        "behaviour_mix": {"cautious": 20, "normal": 60, "aggressive": 20},
    },
}

# presets override parts of the defaults
PRESETS = {
    "empty": {"label": "Empty roads", "help": "No background traffic: only what you add yourself", "background": {"enabled": False}},
    "light": {"label": "Light traffic", "help": "3 vehicles per km of lane", "background": {"density_per_km": 3.0}},
    "moderate": {"label": "Moderate traffic", "help": "7 vehicles per km of lane (the default)", "background": {}},
    "heavy": {"label": "Heavy traffic", "help": "14 vehicles per km, more trucks", "background": {"density_per_km": 14.0, "mix": {"car": 78, "van": 8, "truck": 8, "bus": 4, "motorcycle": 2}}},
    "rush_hour": {"label": "Rush hour", "help": "12 per km building to a peak, more assertive drivers",
                  "background": {"density_per_km": 12.0, "demand_profile": "morning_peak", "behaviour_mix": {"cautious": 10, "normal": 55, "aggressive": 35}}},
    "night": {"label": "Night", "help": "2.5 per km, cautious drivers, slightly under the limits",
              "background": {"density_per_km": 2.5, "demand_profile": "night_lull", "speed_factor_mean": 0.95, "mix": {"car": 90, "van": 4, "truck": 5, "bus": 0, "motorcycle": 1},
                             "behaviour_mix": {"cautious": 40, "normal": 50, "aggressive": 10}}},
    "freight_heavy": {"label": "Freight corridor", "help": "10 per km, a quarter trucks and vans",
                      "background": {"density_per_km": 10.0, "mix": {"car": 55, "van": 18, "truck": 22, "bus": 3, "motorcycle": 2}}},
    "event_day": {"label": "Event day", "help": "Demand surges mid-run (about double)", "background": {"density_per_km": 9.0, "demand_profile": "event_surge"}},
    "school_run": {"label": "School run", "help": "Morning peak, cautious drivers, more two-wheelers",
                   "background": {"density_per_km": 9.0, "demand_profile": "morning_peak", "mix": {"car": 78, "van": 6, "truck": 2, "bus": 6, "motorcycle": 8},
                                  "behaviour_mix": {"cautious": 45, "normal": 45, "aggressive": 10}, "speed_factor_mean": 0.95}},
    "delivery_hours": {"label": "Delivery hours", "help": "Vans and trucks make up about 40 % of the traffic",
                       "background": {"density_per_km": 8.0, "mix": {"car": 52, "van": 24, "truck": 16, "bus": 6, "motorcycle": 2}}},
    "bus_corridor": {"label": "Bus corridor", "help": "One vehicle in five is a bus",
                     "background": {"density_per_km": 8.0, "mix": {"car": 70, "van": 5, "truck": 3, "bus": 20, "motorcycle": 2}}},
    "two_wheelers": {"label": "Two-wheeler heavy", "help": "A third of the traffic is motorcycles",
                     "background": {"density_per_km": 10.0, "mix": {"car": 58, "van": 4, "truck": 2, "bus": 3, "motorcycle": 33},
                                    "behaviour_mix": {"cautious": 15, "normal": 50, "aggressive": 35}, "speed_factor_sd": 0.14}},
    "calm_drivers": {"label": "Calm drivers", "help": "Mostly cautious, a little under the limits",
                     "background": {"behaviour_mix": {"cautious": 70, "normal": 28, "aggressive": 2}, "speed_factor_mean": 0.93, "speed_factor_sd": 0.06}},
    "aggressive_drivers": {"label": "Aggressive drivers", "help": "Short gaps, over the limits (a worst case for speeding and rear-end risk)",
                           "background": {"behaviour_mix": {"cautious": 3, "normal": 27, "aggressive": 70}, "speed_factor_mean": 1.15, "speed_factor_sd": 0.12}},
    "tourist_weekend": {"label": "Tourist weekend", "help": "Slow, cautious drivers unfamiliar with the roads; evening peak",
                        "background": {"density_per_km": 8.0, "demand_profile": "evening_peak", "speed_factor_mean": 0.88, "speed_factor_sd": 0.12,
                                       "behaviour_mix": {"cautious": 55, "normal": 40, "aggressive": 5}}},
    "gridlock": {"label": "Gridlock", "help": "25 vehicles per km: expect long queues", "background": {"density_per_km": 25.0}},
    "ghost_town": {"label": "Almost empty", "help": "1 vehicle per km, for checking a single change", "background": {"density_per_km": 1.0}},
}

# ready-made traffic scenarios: a traffic configuration PLUS plan items placed at the builder location (sources, groups, zones,
# incidents). Each item is {group, kind, params, schedule?}; the UI adds them in one step at the clicked position.
SCENARIO_PRESETS = {
    "stadium_exit": {"label": "Stadium exit", "help": "Event-day demand, a platoon source at the exit, a slow dense zone around it",
                     "traffic": {"preset": "event_day"},
                     "items": [{"kind": "traffic_flow", "params": {"vph": 1500, "mix": "city", "profile": "ramp_up", "platoon_size": 4, "speed_kmh": 35, "behaviour": "mixed"}},
                               {"kind": "special_event", "params": {"radius_m": 250, "factor": 2.5, "speed_factor": 0.7, "duration_s": 600}}]},
    "freight_run": {"label": "Freight run", "help": "Truck platoons through a corridor with a heavy-vehicle zone",
                    "traffic": {"preset": "delivery_hours"},
                    "items": [{"kind": "traffic_flow", "params": {"vph": 420, "mix": "freight", "platoon_size": 3, "speed_kmh": 50}},
                              {"kind": "traffic_density", "params": {"radius_m": 200, "factor": 1.6, "bias": "trucks"}}]},
    "bus_priority": {"label": "Bus service", "help": "A bus every 15 s in a bus-heavy corridor",
                     "traffic": {"preset": "bus_corridor"},
                     "items": [{"kind": "traffic_flow", "params": {"vph": 240, "mix": "transit", "speed_kmh": 40, "behaviour": "cautious"}}]},
    "rush_hour_crash": {"label": "Crash at rush hour", "help": "Rush-hour demand and a blocked lane for 10 minutes",
                        "traffic": {"preset": "rush_hour"},
                        "items": [{"kind": "accident", "params": {"lanes_blocked": 1, "duration_s": 600}, "schedule": {"from_s": 20}}]},
    "ambulance_run": {"label": "Ambulance through traffic", "help": "Heavy traffic and an ambulance driving through it",
                      "traffic": {"preset": "heavy"},
                      "items": [{"kind": "emergency_vehicle", "params": {"vehicle": "ambulance", "speed_kmh": 80}, "schedule": {"from_s": 15}}]},
    "road_works_slowdown": {"label": "Road works slowdown", "help": "A slow zone and a broken-down truck at the works",
                            "traffic": {"preset": "moderate"},
                            "items": [{"kind": "traffic_density", "params": {"radius_m": 150, "factor": 1.3, "speed_factor": 0.5}},
                                      {"kind": "breakdown", "params": {"vehicle": "truck", "duration_s": 300}}]},
    "truck_convoy": {"label": "Truck convoy", "help": "Eight trucks leave together two seconds apart",
                     "traffic": {"preset": "light"},
                     "items": [{"kind": "spawn_vehicles", "params": {"count": 8, "vehicle": "truck", "speed_kmh": 55, "spacing_s": 2.0, "behaviour": "cautious"}}]},
    "school_zone": {"label": "School zone", "help": "A slow zone with extra two-wheelers around a school",
                    "traffic": {"preset": "school_run"},
                    "items": [{"kind": "traffic_density", "params": {"radius_m": 120, "factor": 1.8, "speed_factor": 0.55, "bias": "two_wheelers"}}]},
    "night_freight": {"label": "Night freight", "help": "Quiet night traffic with a truck source and aggressive-free drivers",
                      "traffic": {"preset": "night"},
                      "items": [{"kind": "traffic_flow", "params": {"vph": 240, "mix": "freight", "speed_kmh": 60, "behaviour": "cautious"}}]},
    "rush_hour_flow": {"label": "Commuter inflow", "help": "A building commuter stream into a rush-hour network",
                       "traffic": {"preset": "rush_hour"},
                       "items": [{"kind": "traffic_flow", "params": {"vph": 1200, "mix": "city", "profile": "ramp_up", "speed_kmh": 45, "behaviour": "mixed"}}]},
}


def f(key, label, type_, default, min_=None, max_=None, unit=None, options=None, help_=None, step=None):
    d = {"key": key, "label": label, "type": type_, "default": default}
    for k, v in (("min", min_), ("max", max_), ("unit", unit), ("options", options), ("help", help_), ("step", step)):
        if v is not None:
            d[k] = v
    return d


SCHEMA = [
    {"id": "run", "title": "Run", "help": "How the virtual traffic is simulated", "fields": [
        f("seed", "Random seed", "number", 0, 0, 999, help_="the same seed gives the same traffic every time", step=1),
        f("duration_s", "Length of the run", "number", 120.0, 30, 300, "s", step=10), f("warmup_s", "Warm-up (not shown)", "number", 20.0, 5, 60, "s", step=5)]},
    {"id": "background", "title": "Background traffic", "help": "The ordinary traffic on every road of the map", "fields": [
        f("background.enabled", "Background traffic", "bool", True),
        f("background.density_per_km", "Density", "number", 7.0, 0, 40, "veh per km of lane", step=0.5),
        f("background.demand_profile", "Demand over the run", "enum", "constant", options=list(PROFILES), help_="how the density changes during the run"),
        f("background.road_weight.urban", "Urban roads weight", "number", 1.0, 0, 3, "x", step=0.1), f("background.road_weight.highway", "Highways weight", "number", 1.0, 0, 3, "x", step=0.1)]},
    {"id": "mix", "title": "Vehicle mix", "help": "Shares of the background traffic (any scale, they are normalised)", "fields": [
        f(f"background.mix.{t}", t.replace("_", " ").capitalize(), "number", DEFAULTS["background"]["mix"].get(t, 0), 0, 100, "%", step=1) for t in VEHICLE_TYPES]},
    {"id": "drivers", "title": "Driver behaviour", "help": "How closely drivers follow the limits and each other", "fields": [
        f("background.speed_factor_mean", "Typical speed against the limit", "number", 1.0, 0.5, 1.5, "x limit", step=0.01, help_="1.0 = at the limit, 1.1 = 10 % over"),
        f("background.speed_factor_sd", "Spread between drivers", "number", 0.10, 0, 0.4, "", step=0.01),
        f("background.behaviour_mix.cautious", "Cautious drivers", "number", 20, 0, 100, "%", step=1, help_="long gaps, gentle, a little under the limit"),
        f("background.behaviour_mix.normal", "Normal drivers", "number", 60, 0, 100, "%", step=1),
        f("background.behaviour_mix.aggressive", "Aggressive drivers", "number", 20, 0, 100, "%", step=1, help_="short gaps, hard braking and acceleration, over the limit")]},
]


def deep_merge(base: dict, patch: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize(cfg: dict | None) -> dict:
    """The config with every default filled in; a `preset` name applies that preset first, explicit fields win."""
    cfg = cfg or {}
    base = deep_merge(DEFAULTS, {"background": PRESETS.get(cfg.get("preset", ""), {}).get("background", {})})
    return deep_merge(base, {k: v for k, v in cfg.items() if v is not None})


def get_path(cfg: dict, key: str):
    cur = cfg
    for part in key.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def validate(cfg: dict | None) -> list[str]:
    """Problems with a (normalised) config, as sentences a planner can act on."""
    c = normalize(cfg)
    errs = []
    for sec in SCHEMA:
        for fld in sec["fields"]:
            v = get_path(c, fld["key"])
            lab = fld["label"]
            if fld["type"] == "number":
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    errs.append(f"{lab}: must be a number")
                elif v < fld["min"] or v > fld["max"]:
                    errs.append(f"{lab}: {v:g} is outside {fld['min']:g} to {fld['max']:g}{(' ' + fld['unit']) if fld.get('unit') else ''}")
            elif fld["type"] == "enum" and v not in fld["options"]:
                errs.append(f"{lab}: must be one of {', '.join(fld['options'])}")
            elif fld["type"] == "bool" and not isinstance(v, bool):
                errs.append(f"{lab}: must be yes or no")
    bg = c["background"]
    if not errs and bg["enabled"]:
        if sum(bg["mix"].values()) <= 0:
            errs.append("Vehicle mix: at least one vehicle type needs a share above 0")
        if sum(bg["behaviour_mix"].values()) <= 0:
            errs.append("Driver behaviour: at least one kind of driver needs a share above 0")
        if bg["road_weight"]["urban"] + bg["road_weight"]["highway"] <= 0:
            errs.append("Road weights: urban and highway cannot both be 0")
    unknown = set(cfg or {}) - set(DEFAULTS) if cfg else set()
    if unknown:
        errs.append(f"unknown setting(s): {', '.join(sorted(unknown))}")
    return errs


def profile_factor(name: str, frac: float) -> float:
    pts = PROFILES.get(name, PROFILES["constant"])
    return float(np.interp(min(max(frac, 0.0), 1.0), [p[0] for p in pts], [p[1] for p in pts]))


def pick_weighted(rng, weights: dict[str, float]) -> str:
    keys = [k for k, w in weights.items() if w > 0]
    p = np.array([weights[k] for k in keys], dtype=float)
    return keys[int(rng.choice(len(keys), p=p / p.sum()))]


def is_default(cfg: dict | None) -> bool:
    return normalize(cfg) == normalize({})


def describe(cfg: dict | None) -> str:
    """One line for the UI / report: what this traffic is."""
    c = normalize(cfg)
    bg = c["background"]
    if not bg["enabled"]:
        return "no background traffic"
    pr = PRESETS.get(c.get("preset", ""), {}).get("label")
    return f"{pr or 'custom'}: {bg['density_per_km']:g} veh/km, {PROFILE_LABELS.get(bg['demand_profile'], bg['demand_profile']).lower()} demand, seed {c['seed']}"


def schema_doc() -> dict:
    """For GET /api/plans/traffic/schema."""
    return {"sections": SCHEMA, "defaults": normalize({}), "presets": {k: {"label": v["label"], "help": v["help"], "config": normalize({"preset": k})} for k, v in PRESETS.items()},
            "scenario_presets": {k: {"label": v["label"], "help": v["help"], "traffic": v["traffic"], "items": v["items"]} for k, v in SCENARIO_PRESETS.items()},
            "profiles": {k: {"label": PROFILE_LABELS[k], "points": v} for k, v in PROFILES.items()}, "source_mixes": SOURCE_MIX, "behaviours": BEHAVIOUR,
            "vehicle_types": VEHICLE_TYPES}
