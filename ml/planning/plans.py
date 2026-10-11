"""Traffic plans (Scenario_Builder_Plan.md): locate a map position, validate a plan, compile it to violation-engine
changes, check risks, and analyse it on a session's recorded traffic.

A plan is plain JSON: {name, description, scene, session_id, objectives[], props[], rules[], scenarios[]}; every item has
{id, kind, x, y, yaw_deg?, params{}, target{lane_id?}, schedule{from_s,to_s}?, priority?, note?}. Kinds and parameters:
plan_catalogue.py. Pure functions on dicts (scene = the lane-map JSON), no I/O except `analyze`, so it runs in the
backend and in tests.

What `analyze` returns is split by where each number comes from (never mixed):
    observed   recorded traffic around the plan (simulation/planning/sim_metrics.py on the session's kinematics)
    replay     the violation engine on the same traffic with the compiled change (ml/planning/whatif.py), measured
    risks      rule-of-thumb checks, marked "not measured"
    pending    metrics that need a CARLA run and have none yet
"""

import fnmatch
import math
import re
import sys
from pathlib import Path

import shapely
from shapely.geometry import LineString, Point, Polygon

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for q in (HERE, REPO / "ml" / "violation_engine", REPO / "simulation" / "planning"):
    if str(q) not in sys.path:
        sys.path.insert(0, str(q))

from plan_catalogue import GROUPS, KINDS  # noqa: E402

GROUP_KEY = {"prop": "props", "rule": "rules", "scenario": "scenarios"}
LANE_ID = re.compile(r"^r(-?\d+)_s(\d+)_l(-?\d+)(_p\d+)?$")
NEAR_LANE_M = 15.0          # a road prop / lane rule must be this close to a lane
OVERLAP_M = 1.0
SCENE_MARGIN_M = 60.0
RISK_BASIS = "rule of thumb (not measured)"
PENDING_METRICS = ["average speed after", "average travel time after", "traffic density after", "queue length after",
                   "intersection waiting time", "throughput", "vehicle delay", "road utilisation", "safety indicators after",
                   "congestion level after"]  # need a CARLA baseline-vs-modified run (Phase 2)

_index_cache: dict = {}


# ---------------------------------------------------------------------------- geometry helpers

def items_of(plan: dict):
    """(group, item) for every prop, rule and scenario."""
    for g in GROUPS:
        for it in plan.get(GROUP_KEY[g]) or []:
            yield g, it


def with_defaults(kind: str, params: dict | None) -> dict:
    spec = KINDS.get(kind, {"params": []})
    out = {s["key"]: s.get("default") for s in spec["params"]}
    out.update(params or {})
    return out


def rect_polygon(x: float, y: float, yaw_deg: float, length: float, width: float) -> list[list[float]]:
    """Rectangle centred on (x, y), `length` along the heading yaw_deg (CARLA yaw: 0 = +x, 90 = +y)."""
    h = math.radians(yaw_deg or 0.0)
    ux, uy, vx, vy = math.cos(h), math.sin(h), -math.sin(h), math.cos(h)
    hl, hw = length / 2, width / 2
    return [[round(x + sx * hl * ux + sy * hw * vx, 3), round(y + sx * hl * uy + sy * hw * vy, 3)]
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def lane_glob(lane_id: str, scope: str) -> str:
    """The lane_overrides lane_id for a lane and a scope: this lane (+ its pieces), its road section, its whole road."""
    m = LANE_ID.match(lane_id)
    if not m or scope not in ("section", "road"):
        return (lane_id.split("_p")[0] + "*") if m else lane_id
    return f"r{m.group(1)}_s{m.group(2)}_*" if scope == "section" else f"r{m.group(1)}_*"


class SceneIndex:
    def __init__(self, scene: dict):
        self.scene = scene
        self.lanes = [l for l in scene.get("lanes", []) if len(l.get("centreline", [])) >= 2]
        self.lines = [LineString(l["centreline"]) for l in self.lanes]
        self.tree = shapely.STRtree(self.lines)
        pts = [c for l in self.lanes for c in (l["centreline"][0], l["centreline"][-1])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self.bbox = (min(xs), min(ys), max(xs), max(ys)) if pts else (0, 0, 0, 0)
        self.by_id = {l["id"]: l for l in self.lanes}

    def near(self, x: float, y: float, radius: float):
        pt = Point(x, y)
        idx = self.tree.query(pt.buffer(radius))
        out = [(float(self.lines[i].distance(pt)), self.lanes[i]) for i in idx]
        return sorted([o for o in out if o[0] <= radius], key=lambda t: t[0])

    def matching(self, glob: str) -> list[dict]:
        return [l for l in self.lanes if fnmatch.fnmatchcase(str(l["id"]), glob)]

    def under(self, geom) -> list[dict]:
        shapely.prepare(geom)
        return [self.lanes[i] for i in self.tree.query(geom, predicate="intersects")]


def index_of(scene: dict) -> SceneIndex:
    key = (scene.get("scene"), len(scene.get("lanes", [])), id(scene))
    if key not in _index_cache:
        if len(_index_cache) > 4:
            _index_cache.clear()
        _index_cache[key] = SceneIndex(scene)
    return _index_cache[key]


def heading_deg(l: dict, x: float, y: float) -> float:
    c = l["centreline"]
    line = LineString(c)
    d = line.project(Point(x, y))
    a, b = line.interpolate(max(d - 1, 0)), line.interpolate(min(d + 1, line.length))
    return round(math.degrees(math.atan2(b.y - a.y, b.x - a.x)), 1)


# ---------------------------------------------------------------------------- locate

def locate(scene: dict, x: float, y: float, radius: float = 40.0, plan: dict | None = None) -> dict:
    """What is at a map position: nearest lanes, junction, existing zones and plan items nearby."""
    ix = index_of(scene)
    near = ix.near(x, y, radius)
    lanes = [{"id": l["id"], "road_id": l.get("road_id"), "distance_m": round(d, 1), "lane_type": l.get("lane_type"),
              "speed_limit_kmh": l.get("speed_limit_kmh"), "junction": bool(l.get("junction")), "bridge": bool(l.get("bridge")),
              "tunnel": bool(l.get("tunnel")), "road_class": l.get("road_class"), "heading_deg": heading_deg(l, x, y)}
             for d, l in near[:8]]
    zones = []
    for z in scene.get("zones", []):
        poly = z.get("polygon")
        if poly and len(poly) >= 3:
            d = float(Polygon(poly).distance(Point(x, y)))
            if d <= radius:
                zones.append({"id": z.get("id"), "type": z.get("type"), "distance_m": round(d, 1)})
    items = []
    if plan:
        for g, it in items_of(plan):
            d = math.hypot(it.get("x", 1e9) - x, it.get("y", 1e9) - y)
            if d <= radius:
                items.append({"id": it.get("id"), "group": g, "kind": it.get("kind"), "distance_m": round(d, 1)})
    on_road = bool(lanes) and lanes[0]["distance_m"] <= NEAR_LANE_M
    return {"x": round(x, 2), "y": round(y, 2), "scene": scene.get("scene"), "on_road": on_road,
            "inside_scene": ix.bbox[0] - SCENE_MARGIN_M <= x <= ix.bbox[2] + SCENE_MARGIN_M and ix.bbox[1] - SCENE_MARGIN_M <= y <= ix.bbox[3] + SCENE_MARGIN_M,
            "nearest_lane": lanes[0] if lanes else None, "lanes": lanes,
            "junction": any(l["junction"] for l in lanes if l["distance_m"] <= 25),
            "zones": zones, "plan_items": items,
            "signals": "traffic-light positions are not part of the lane map; shown only when a recorded session carries them"}


# ---------------------------------------------------------------------------- compile (plan -> engine changes)

def _lanes_for(ix: SceneIndex, it: dict, p: dict, spec: dict) -> list[dict]:
    """The driving lanes an item acts on."""
    kind, target = it["kind"], spec["target"]
    scope = p.get("scope", "lane")
    lane_id = (it.get("target") or {}).get("lane_id")
    if not lane_id and target == "lane":
        n = ix.near(it["x"], it["y"], NEAR_LANE_M)
        lane_id = n[0][1]["id"] if n else None
    if target == "lane":
        return [l for l in ix.matching(lane_glob(lane_id, scope))] if lane_id else []
    if kind in ("accident", "breakdown"):
        return [l for _, l in ix.near(it["x"], it["y"], 4.0)]
    if target == "area" or "length_m" in p:
        poly = Polygon(rect_polygon(it["x"], it["y"], it.get("yaw_deg", 0.0), float(p.get("length_m", 10)), float(p.get("width_m", 4))))
        return ix.under(poly)
    return [l for _, l in ix.near(it["x"], it["y"], float(p.get("reach_m", NEAR_LANE_M)))]


def compile_plan(plan: dict, scene: dict) -> dict:
    """{changes: {lane_overrides, zones}, evaluated: [...], not_evaluated: [...]} for ml/planning/whatif.py."""
    ix = index_of(scene)
    lane_ov, zones, ev, skipped = [], [], [], []
    for g, it in items_of(plan):
        kind = it.get("kind")
        spec = KINDS.get(kind)
        if spec is None:
            skipped.append({"item_id": it.get("id"), "kind": kind, "reason": "unknown kind"})
            continue
        mode = spec["replay"]["mode"]
        p = with_defaults(kind, it.get("params"))
        base = {"item_id": it.get("id"), "kind": kind, "group": g}
        if mode == "none":
            skipped.append({**base, "reason": spec["replay"]["note"]})
            continue
        note = spec["replay"]["note"]
        if mode.startswith("zone:"):
            ztype = p["zone_type"] if mode == "zone:custom" else mode.split(":")[1]
            poly = rect_polygon(it["x"], it["y"], it.get("yaw_deg", 0.0), float(p.get("length_m", 10)), float(p.get("width_m", 4)))
            z = {"id": f"plan_{it['id']}", "type": ztype, "polygon": poly}
            if ztype == "no_parking":
                z["grace_s"] = float(p.get("grace_s", 30))
            if ztype == "speed":
                z["limit_kmh"] = float(p.get("limit_kmh", 30))
            zones.append(z)
            ev.append({**base, "what": f"{ztype} zone {p.get('length_m', '?')} x {p.get('width_m', '?')} m", "note": note})
            continue
        lanes = [l for l in _lanes_for(ix, it, p, spec) if (l.get("lane_type") or "driving") == "driving"]
        if not lanes:
            skipped.append({**base, "reason": "no driving lane under / at this item"})
            continue
        scope = p.get("scope", "lane")
        lane_id = (it.get("target") or {}).get("lane_id") or lanes[0]["id"]
        if spec["target"] == "lane":
            globs = [lane_glob(lane_id, scope)]
        else:
            globs = sorted({lane_glob(l["id"], "lane") for l in lanes})
        if mode == "speed_limit":
            v = float(p.get("limit_kmh", p.get("speed_kmh", 50)))
            what = f"speed limit {v:g} km/h on {len(lanes)} lane(s)"
            patch = {"speed_limit_kmh": v}
        elif mode.startswith("restrict:"):
            v = p.get("allowed") or p.get("restriction") or mode.split(":")[1]
            if kind == "restricted_entry":
                v = "restricted"
            what = f"lane reserved for {v} on {len(lanes)} lane(s)"
            patch = {"restricted": v}
        elif mode == "one_way":
            what, patch = f"one-way on {len(lanes)} lane(s)", {"one_way": True}
        else:  # closure
            what, patch = f"{len(lanes)} lane(s) closed (approximated as restricted)", {"restricted": "restricted"}
        for gl in globs:
            lane_ov.append({"lane_id": gl, **patch, "note": f"plan item {it.get('id')} ({kind})"})
        ev.append({**base, "what": what, "lanes": len(lanes), "note": note})
    return {"changes": {"lane_overrides": lane_ov, "zones": zones}, "evaluated": ev, "not_evaluated": skipped}


# ---------------------------------------------------------------------------- validation

def _param_errors(kind: str, params: dict) -> list[str]:
    spec = KINDS[kind]
    errs = []
    known = {s["key"]: s for s in spec["params"]}
    for k, v in (params or {}).items():
        s = known.get(k)
        if s is None:
            errs.append(f"unknown parameter {k!r}")
            continue
        if v is None:
            continue
        t = s["type"]
        if t == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                errs.append(f"{s['label']}: must be a number")
            elif ("min" in s and v < s["min"]) or ("max" in s and v > s["max"]):
                errs.append(f"{s['label']}: {v:g} is outside {s.get('min', '-')} to {s.get('max', '-')}{(' ' + s['unit']) if s.get('unit') else ''}")
        elif t == "enum":
            if v not in s.get("options", []):
                errs.append(f"{s['label']}: must be one of {', '.join(map(str, s['options']))}")
        elif t == "bool":
            if not isinstance(v, bool):
                errs.append(f"{s['label']}: must be yes or no")
        elif t == "text" and not isinstance(v, str):
            errs.append(f"{s['label']}: must be text")
    return errs


def _msg(level, it, code, message):
    return {"level": level, "item_id": it.get("id") if it else None, "code": code, "message": message}


def validate(plan: dict, scene: dict) -> dict:
    """{ok, errors, warnings, conflicts, infos}: each a list of {level, item_id, code, message}."""
    ix = index_of(scene)
    out = {"errors": [], "warnings": [], "conflicts": [], "infos": []}
    if plan.get("traffic") is not None:
        import traffic_config as tc
        for m in tc.validate(plan["traffic"]):
            out["errors"].append(_msg("error", None, "traffic", f"Custom traffic: {m}"))
    if not str(plan.get("name") or "").strip():
        out["errors"].append(_msg("error", None, "name", "the plan needs a name"))
    seen_ids = set()
    comp_lanes: list[tuple[dict, set[str], dict]] = []  # (item, lane ids, compiled patch) for conflict checks
    for g, it in items_of(plan):
        kind = it.get("kind")
        if it.get("id") in seen_ids:
            out["errors"].append(_msg("error", it, "duplicate_id", f"two items share the id {it.get('id')}"))
        seen_ids.add(it.get("id"))
        spec = KINDS.get(kind)
        if spec is None or spec["group"] != g:
            out["errors"].append(_msg("error", it, "kind", f"{kind!r} is not a known {g} kind"))
            continue
        x, y = it.get("x"), it.get("y")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
            out["errors"].append(_msg("error", it, "position", "the item needs a map position (x, y)"))
            continue
        for e in _param_errors(kind, it.get("params")):
            out["errors"].append(_msg("error", it, "param", f"{spec['label']}: {e}"))
        sch = it.get("schedule")
        if sch and sch.get("from_s") is not None and sch.get("to_s") is not None and sch["to_s"] <= sch["from_s"]:
            out["errors"].append(_msg("error", it, "schedule", "the schedule must end after it starts"))
        if not (ix.bbox[0] - SCENE_MARGIN_M <= x <= ix.bbox[2] + SCENE_MARGIN_M and ix.bbox[1] - SCENE_MARGIN_M <= y <= ix.bbox[3] + SCENE_MARGIN_M):
            out["errors"].append(_msg("error", it, "outside_map", f"({x:.0f}, {y:.0f}) is outside the {scene.get('scene')} map"))
            continue
        near = ix.near(x, y, NEAR_LANE_M)
        if kind not in ("weather", "custom_object") and not near and spec["target"] in ("lane", "area", "point"):
            lvl = "errors" if spec["target"] == "lane" or g == "rule" else "warnings"
            out[lvl].append(_msg(lvl[:-1], it, "not_on_road", f"{spec['label']}: no lane within {NEAR_LANE_M:.0f} m of the position"))
        if g != "scenario" or spec["replay"]["mode"] != "none":
            p = with_defaults(kind, it.get("params"))
            lanes = [l for l in _lanes_for(ix, it, p, spec) if (l.get("lane_type") or "driving") == "driving"] if near or spec["target"] == "area" else []
            mode = spec["replay"]["mode"]
            if mode in ("speed_limit", "one_way", "closure") or mode.startswith("restrict:"):
                comp_lanes.append((it, {l["id"] for l in lanes}, {"mode": mode, "p": p, "lanes": lanes}))
    # same position twice
    its = [it for _, it in items_of(plan) if isinstance(it.get("x"), (int, float))]
    for i, a in enumerate(its):
        for b in its[i + 1:]:
            if a.get("kind") == b.get("kind") and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < OVERLAP_M:
                out["warnings"].append(_msg("warning", b, "overlap", f"{KINDS.get(b['kind'], {}).get('label', b['kind'])} sits on top of {a.get('id')}"))
    # conflicts between items on the same lanes
    for i, (a, la, ca) in enumerate(comp_lanes):
        for b, lb, cb in comp_lanes[i + 1:]:
            common = la & lb
            if not common:
                continue
            ma, mb = ca["mode"], cb["mode"]
            ta, tb = KINDS[a["kind"]]["label"], KINDS[b["kind"]]["label"]
            if ma == mb == "speed_limit":
                va, vb = float(ca["p"].get("limit_kmh", ca["p"].get("speed_kmh", 0))), float(cb["p"].get("limit_kmh", cb["p"].get("speed_kmh", 0)))
                if va != vb:
                    pa, pb = a.get("priority", 0), b.get("priority", 0)
                    if pa == pb:
                        out["conflicts"].append(_msg("conflict", b, "speed_conflict", f"{ta} {va:g} km/h ({a.get('id')}) and {tb} {vb:g} km/h ({b.get('id')}) apply to the same {len(common)} lane(s) with equal priority; give one a higher priority"))
                    else:
                        out["warnings"].append(_msg("warning", b, "speed_priority", f"two speed limits on the same lanes: the higher priority ({a.get('id') if pa > pb else b.get('id')}) wins"))
            elif "closure" in (ma, mb) and (ma.startswith("restrict:") or mb.startswith("restrict:")) and not ({ma, mb} == {"closure"}):
                out["conflicts"].append(_msg("conflict", b, "closure_vs_reserved", f"{ta} closes lanes that {tb} reserves ({len(common)} shared lane(s))"))
            elif ma.startswith("restrict:") and mb.startswith("restrict:"):
                va = ca["p"].get("allowed") or ca["p"].get("restriction") or ma.split(":")[1]
                vb = cb["p"].get("allowed") or cb["p"].get("restriction") or mb.split(":")[1]
                if va != vb:
                    out["conflicts"].append(_msg("conflict", b, "restriction_conflict", f"lane reserved for {va} ({a.get('id')}) and for {vb} ({b.get('id')})"))
    # no-parking zone over a bus stop
    stops = [it for _, it in items_of(plan) if it.get("kind") == "bus_stop"]
    for _, it in items_of(plan):
        if KINDS.get(it.get("kind"), {}).get("replay", {}).get("mode") == "zone:no_parking" or (it.get("kind") == "custom_zone_rule" and (it.get("params") or {}).get("zone_type") == "no_parking"):
            p = with_defaults(it["kind"], it.get("params"))
            zp = Polygon(rect_polygon(it["x"], it["y"], it.get("yaw_deg", 0.0), float(p.get("length_m", 10)), float(p.get("width_m", 4))))
            for s in stops:
                sp = with_defaults("bus_stop", s.get("params"))
                if zp.intersects(Polygon(rect_polygon(s["x"], s["y"], s.get("yaw_deg", 0.0), float(sp["length_m"]), float(sp["width_m"])))):
                    out["conflicts"].append(_msg("conflict", it, "no_parking_vs_bus_stop", f"the no-parking zone covers the bus stop {s.get('id')}"))
    # information / warnings that need the map
    for it, lanes_ids, c in comp_lanes:
        lanes = c["lanes"]
        if c["mode"] == "speed_limit" and lanes:
            new = float(c["p"].get("limit_kmh", c["p"].get("speed_kmh", 0)))
            old = [l.get("speed_limit_kmh") for l in lanes if l.get("speed_limit_kmh")]
            if old and all(abs(o - new) < 0.5 for o in old):
                out["infos"].append(_msg("info", it, "no_change", f"the map already has {new:g} km/h on these lanes"))
            elif old:
                out["infos"].append(_msg("info", it, "limit_change", f"changes the map limit {min(old):g}-{max(old):g} km/h to {new:g} km/h on {len(lanes)} lane(s)"))
        if c["mode"] == "closure" and lanes:
            sections = {}
            for l in lanes:
                m = LANE_ID.match(l["id"])
                if m:
                    sections.setdefault((m.group(1), m.group(2)), set()).add(l["id"].split("_p")[0])
            for (r, s), closed in sections.items():
                allin = {l["id"].split("_p")[0] for l in ix.matching(f"r{r}_s{s}_*") if (l.get("lane_type") or "driving") == "driving" and l["centreline"]}
                same_dir = {i for i in allin if i.split("_l")[1].startswith("-") == next(iter(closed)).split("_l")[1].startswith("-")}
                if same_dir and closed >= same_dir:
                    out["warnings"].append(_msg("warning", it, "closes_all_lanes", f"closes every lane of road {r} section {s} in that direction: nothing left to drive on"))
    out["ok"] = not out["errors"] and not out["conflicts"]
    return out


# ---------------------------------------------------------------------------- risks and alternatives (rule of thumb)

def risk_checks(plan: dict, scene: dict) -> list[dict]:
    ix = index_of(scene)
    risks = []
    sig = [it for _, it in items_of(plan) if it.get("kind") == "traffic_signal"]
    for i, a in enumerate(sig):
        for b in sig[i + 1:]:
            d = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if d < 100:
                risks.append({"item_id": b["id"], "level": "warning", "basis": RISK_BASIS,
                              "message": f"two signals only {d:.0f} m apart need coordinated timing, otherwise queues from one block the other"})
    for g, it in items_of(plan):
        kind, p = it.get("kind"), with_defaults(it.get("kind"), it.get("params"))
        spec = KINDS.get(kind)
        if not spec or not isinstance(it.get("x"), (int, float)):
            continue
        near = ix.near(it["x"], it["y"], NEAR_LANE_M)
        if kind in ("speed_limit", "speed_limit_sign") and near:
            new = float(p.get("limit_kmh", p.get("speed_kmh", 0)))
            old = near[0][1].get("speed_limit_kmh")
            if old and old - new > 30:
                risks.append({"item_id": it["id"], "level": "warning", "basis": RISK_BASIS,
                              "message": f"a {old - new:g} km/h cut ({old:g} to {new:g}) is large: expect low compliance and more speeding flags unless enforced"})
        if spec["replay"]["mode"] == "closure":
            if any(l.get("junction") for _, l in ix.near(it["x"], it["y"], 30)):
                risks.append({"item_id": it["id"], "level": "warning", "basis": RISK_BASIS,
                              "message": "a closure next to a junction can back traffic up into it and block cross traffic (bottleneck)"})
        if kind in ("no_parking_zone", "no_parking") and any(z.get("type") == "crosswalk" and Polygon(z["polygon"]).distance(Point(it["x"], it["y"])) < 15
                                                             for z in scene.get("zones", []) if len(z.get("polygon") or []) >= 3):
            risks.append({"item_id": it["id"], "level": "info", "basis": RISK_BASIS,
                          "message": "a crosswalk is nearby: a no-stopping rule here also improves its visibility, check it does not push parking onto the crossing"})
        if kind == "pedestrian_crossing" and near and (near[0][1].get("speed_limit_kmh") or 0) > 50:
            risks.append({"item_id": it["id"], "level": "warning", "basis": RISK_BASIS,
                          "message": f"crossing on a {near[0][1]['speed_limit_kmh']:g} km/h road: consider a lower limit or a raised table with it"})
        if kind == "traffic_signal" and near and near[0][1].get("road_class") == "highway":
            risks.append({"item_id": it["id"], "level": "warning", "basis": RISK_BASIS,
                          "message": "a signal on a highway section creates sudden stops at speed (rear-end risk)"})
    return risks


ALTERNATIVES = {
    "traffic_signal": [
        {"title": "Adjust the signal timing instead of adding a signal", "benefit": "keeps flow on the main road, no new stop-go cycle", "risk": "cross traffic may wait longer", "tradeoff": "cheaper, less safe for crossing pedestrians", "test": None},
        {"title": "Adaptive signal", "benefit": "green time follows demand, lower delay at uneven demand", "risk": "needs detectors; behaves like a fixed signal at low demand", "tradeoff": "higher cost", "test": None},
        {"title": "Restrict a turning movement (no-U-turn zone)", "benefit": "removes the conflicting turn without a signal", "risk": "diverts those vehicles to other roads", "tradeoff": "may increase demand on nearby roads", "test": {"add": {"group": "rule", "kind": "custom_zone_rule", "params": {"zone_type": "no_u_turn", "length_m": 30, "width_m": 12}}}},
    ],
    "speed_limit": [
        {"title": "Smaller change first (10 km/h less)", "benefit": "higher compliance, fewer new speeding flags", "risk": "smaller safety gain", "tradeoff": "gradual", "test": {"param_delta": {"speed_kmh": -10}, "from_item": True}},
        {"title": "Same limit one step higher (10 km/h more)", "benefit": "less friction with the road design", "risk": "less speed reduction", "tradeoff": "compare violations in the replay", "test": {"param_delta": {"speed_kmh": 10}, "from_item": True}},
    ],
    "speed_limit_sign": [{"title": "Add a speed breaker before the sign", "benefit": "physical speed reduction", "risk": "noise, emergency vehicle delay", "tradeoff": "cost", "test": None}],
    "no_parking_zone": [
        {"title": "Shorter grace (no stopping)", "benefit": "more flags, stronger deterrence", "risk": "flags short legitimate stops", "tradeoff": "compare flagged events", "test": {"param_set": {"grace_s": 5}, "from_item": True}},
        {"title": "Longer grace (no parking)", "benefit": "fewer false flags", "risk": "weaker deterrence", "tradeoff": "compare flagged events", "test": {"param_set": {"grace_s": 120}, "from_item": True}},
    ],
    "no_parking": [
        {"title": "Shorter grace (no stopping)", "benefit": "more flags, stronger deterrence", "risk": "flags short legitimate stops", "tradeoff": "compare flagged events", "test": {"param_set": {"grace_s": 5}, "from_item": True}},
    ],
    "road_closure": [{"title": "Close one lane instead of the section", "benefit": "keeps a route open", "risk": "bottleneck at the closure", "tradeoff": "needs CARLA to compare delay", "test": {"param_set": {"scope": "lane"}, "from_item": True}}],
    "construction_zone": [{"title": "Lower the speed limit through the works", "benefit": "safer for workers", "risk": "queue grows upstream", "tradeoff": "needs CARLA for delay", "test": {"add": {"group": "rule", "kind": "speed_limit", "params": {"speed_kmh": 30, "scope": "section"}}}}],
    "pedestrian_crossing": [{"title": "Add a no-parking zone at the crossing", "benefit": "keeps sight lines clear", "risk": "displaces parking", "tradeoff": "check nearby supply", "test": {"add": {"group": "rule", "kind": "no_parking", "params": {"length_m": 20, "width_m": 4, "grace_s": 5}}}}],
}


def alternatives_for(plan: dict) -> list[dict]:
    out, seen = [], set()
    for g, it in items_of(plan):
        for a in ALTERNATIVES.get(it.get("kind"), []):
            key = (it.get("id"), a["title"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"for_item": it.get("id"), "kind": it.get("kind"), **a, "evidence": "preliminary",
                        "evidence_note": "rule of thumb; test it (replay) to get a measured violation comparison" if a["test"] else "rule of thumb; needs a CARLA run to quantify"})
    return out


def variant(plan: dict, alt: dict) -> dict | None:
    """The plan with an alternative's `test` patch applied (a copy), or None when it has no testable patch."""
    import copy
    t = alt.get("test")
    if not t:
        return None
    new = copy.deepcopy(plan)
    new["name"] = f"{plan.get('name', 'plan')} / {alt['title']}"
    if "add" in t:
        src = next((it for _, it in items_of(plan) if it.get("id") == alt["for_item"]), None)
        if src is None:
            return None
        g = t["add"]["group"]
        new.setdefault(GROUP_KEY[g], []).append({"id": f"alt_{len(new.get(GROUP_KEY[g], [])) + 1}", "kind": t["add"]["kind"], "x": src["x"], "y": src["y"],
                                                "yaw_deg": src.get("yaw_deg", 0.0), "params": dict(t["add"].get("params", {})), "target": dict(src.get("target") or {})})
        return new
    for g, it in items_of(new):
        if it.get("id") == alt["for_item"]:
            params = it.setdefault("params", {})
            for k, v in t.get("param_set", {}).items():
                params[k] = v
            for k, v in t.get("param_delta", {}).items():
                params[k] = with_defaults(it["kind"], params).get(k, 0) + v
            return new
    return None


# ---------------------------------------------------------------------------- observed baseline and analysis

def center_radius(plan: dict, pad: float = 60.0) -> tuple[tuple[float, float], float]:
    pts = [(it["x"], it["y"]) for _, it in items_of(plan) if isinstance(it.get("x"), (int, float))]
    if not pts:
        return (0.0, 0.0), 0.0
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    r = max(math.hypot(p[0] - cx, p[1] - cy) for p in pts) + pad
    return (cx, cy), min(max(r, 60.0), 250.0)


def observed_metrics(rows: list[dict], center, radius: float) -> dict:
    """Traffic around the plan on recorded kinematics rows, with sim_metrics (same definitions as the CARLA runs)."""
    import sim_metrics as sm
    st = []
    for r in rows:
        try:
            st.append({"sim_time": float(r["time_s"]), "id": int(float(r["track_id"])), "x": float(r["x"]), "y": float(r["y"]),
                       "yaw": float(r.get("heading_deg") or 0.0), "vx": float(r.get("vx") or 0.0), "vy": float(r.get("vy") or 0.0),
                       "speed_kmh": float(r.get("speed_kmh") or 0.0), "limit_kmh": math.nan, "road_id": None})
        except (ValueError, TypeError):
            continue
    if not st:
        return {"error": "no rows"}
    t0, t1 = min(r["sim_time"] for r in st), max(r["sim_time"] for r in st)
    area = sm._in_area(st, center, radius)
    ids = {r["id"] for r in area}
    dur = max(t1 - t0, 1e-9)
    frames = len({r["sim_time"] for r in area})
    area_km2 = math.pi * (radius / 1000.0) ** 2
    sp = sm.speed_stats(area)
    return {"label": "observed on the recorded traffic (before the change)", "centre": [round(center[0], 1), round(center[1], 1)], "radius_m": round(radius, 1),
            "duration_s": round(t1 - t0, 1), "vehicles": len(ids), "samples": len(area),
            "mean_speed_kmh": sp.get("mean_kmh"), "p85_speed_kmh": sp.get("p85_kmh"),
            "density_veh_per_km2": round(len(area) / frames / area_km2, 1) if frames else None,
            "flow_veh_per_h": round(len(ids) / dur * 3600, 0),
            "queue": sm.queue_stats(area), "travel_time": sm.travel_times(st, center, radius, t0, t1),
            "conflicts_ttc": sm.conflicts(area)}


def analyze(plan: dict, scene: dict, vdir: Path | None, progress=None) -> dict:
    """Validation, compile summary, risks, alternatives and (with a session's recorded traffic) observed + replay."""
    note = progress or (lambda *_: None)
    note("Checking the plan", 0, 4)
    val = validate(plan, scene)
    comp = compile_plan(plan, scene)
    out = {"validation": val, "compiled": {"evaluated": comp["evaluated"], "not_evaluated": comp["not_evaluated"],
                                           "changes": comp["changes"]},
           "risks": risk_checks(plan, scene), "alternatives": alternatives_for(plan), "pending_metrics": PENDING_METRICS}
    if not val["ok"]:
        out["evidence"] = {"level": "insufficient data", "why": "the plan has errors or conflicts: fix them before it is analysed"}
        return out
    if vdir is None:
        out["evidence"] = {"level": "preliminary", "why": "no session with recorded traffic was given: only rule-of-thumb checks"}
        return out
    import csv
    import whatif
    note("Reading the recorded traffic", 1, 4)
    with open(vdir / "kinematics.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    (cx, cy), rad = center_radius(plan)
    note("Measuring the baseline around the plan", 2, 4)
    out["observed"] = observed_metrics(rows, (cx, cy), rad)
    ch = comp["changes"]
    if ch["lane_overrides"] or ch["zones"]:
        note("Replaying the violation engine on the recorded traffic (the longest step)", 3, 4)
        res = whatif.simulate(vdir, ch)
        out["replay"] = res["replay"]
        rep = res["replay"]
        out["best_case"] = {"label": "best case on the recorded traffic: violations the change removes", "removed": rep["removed"],
                            "count": len(rep["removed"]), "note": "drivers keep driving as recorded; their response to the change is not modelled"}
        out["worst_case"] = {"label": "worst case on the recorded traffic: new violations the change creates", "added": rep["added"],
                             "count": len(rep["added"]), "note": "closures are approximated as restricted lanes; traffic does not re-route in a replay"}
        out["evidence"] = {"level": "replay-validated", "why": f"violation engine re-run on {rep['rows']} recorded rows ({rep['tracks']} vehicles) with the change; "
                                                           "metrics that need driver response are not simulated yet"}
    else:
        out["evidence"] = {"level": "preliminary", "why": "nothing in the plan changes a rule the replay can judge; the observed baseline is shown, impact needs a CARLA run"}
    return out
