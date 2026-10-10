"""What-if for a planner's change (PRD Objective 4 + 21.3, Expected_Output 7.4 step "projected estimate").

A planner proposes a change in the twin: lane speed limits / restricted lanes (road.lane_overrides),
no-stopping / no-U-turn zones, and optionally a countermeasure over an area. Two answers, kept apart:

  replay       the violation engine re-run on the session's recorded traffic (kinematics.csv) with
               the change applied, against a baseline run on the same rows: "under this rule, these
               events". Measured, exact for the recorded traffic, but drivers keep driving as they
               did: their response to the change is not modelled.
  projection   the PRD 21.3 rule-based estimate: the historical events the countermeasure targets
               (types it addresses, inside the area / on the changed lanes) times the sourced factor
               from catalogue.py (published claims, never our measurement). "not quantified" when the
               catalogue has no factor. Always labelled "projected estimate".

Usage (the backend calls simulate(); CLI for checks):
    venv\\Scripts\\python.exe ml/planning/whatif.py <violations dir> --limit "r24_s0_l-1*=20"
    venv\\Scripts\\python.exe ml/planning/whatif.py <violations dir> --countermeasure speed_camera --area x0,y0,x1,y1
"""

import argparse
import copy
import csv
from dataclasses import asdict
import json
import sys
import time
from collections import Counter
from pathlib import Path

import shapely

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENGINE = REPO / "ml" / "violation_engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from catalogue import COUNTERMEASURES, projected  # noqa: E402

# which violation types each countermeasure addresses (recommend.RULES uses the same pairs)
TARGETS = {
    "speed_camera": ["speeding"], "average_speed_camera": ["speeding"], "speed_limit_review": ["speeding"],
    "traffic_calming": ["speeding"], "wrong_way_signage": ["wrong_way"], "one_way_barrier": ["wrong_way"],
    "median_closure": ["illegal_u_turn"], "no_u_turn_signage": ["illegal_u_turn"],
    "no_stopping_enforcement": ["no_parking", "highway_stop"], "stopping_bay": ["highway_stop", "no_parking"],
    "crossing_visibility": ["zebra_crossing"], "keep_clear_box": ["zebra_crossing"],
    "signal_timing": ["zebra_crossing", "highway_stop"], "signal_installation": ["red_light"],
    "marking_refresh": ["lane_violation"], "delineators": ["lane_violation"], "lane_use_signage": ["lane_violation"],
    "lane_allocation": ["lane_violation", "highway_stop"], "enforcement_schedule": [
        "speeding", "wrong_way", "illegal_u_turn", "no_parking", "highway_stop", "zebra_crossing", "lane_violation"],
}
COUNTED = {"flagged", "needs_review"}  # events.COUNTED_STATUS
NEAR_M = 60.0  # replay only tracks that come this close to a changed lane / zone (queues, neighbours included)
REPLAY_NOTE = ("Rule replay on the recorded traffic: the same vehicles making the same movements, judged under the "
               "changed rules. It shows what the rules would flag; how drivers would respond to the change is not modelled.")


def countermeasures() -> list[dict]:
    """The catalogue for the UI: key, name, the types it addresses, whether a sourced factor exists."""
    return [{"key": k, "name": c["name"], "targets": TARGETS.get(k, []), "quantified": c["reduction"] is not None,
             "source": c["source"], "url": c["url"]} for k, c in COUNTERMEASURES.items()]


def _path(p: str | None) -> Path | None:
    if not p or p == "None":
        return None
    q = Path(p.replace("\\", "/"))
    return q if q.is_absolute() else REPO / q


def load_run(vdir: Path) -> tuple[dict, dict | None, list[dict], list[dict]]:
    """(scene as the engine saw it, engine params, kinematics rows, events) of a run_violations.py folder."""
    from run_violations import extra_params, merge_zones
    summary = json.loads((vdir / "summary.json").read_text(encoding="utf-8"))
    zones = _path(summary.get("zones"))
    if (vdir / "scene.json").is_file():  # site runs save the scene they used (zones, learned flow included)
        scene = json.loads((vdir / "scene.json").read_text(encoding="utf-8"))
    else:
        scene = merge_zones(json.loads(_path(summary["scene"]).read_text(encoding="utf-8")), zones)
    params = extra_params(None, zones)
    with open(vdir / "kinematics.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    ev = json.loads((vdir / "violations.json").read_text(encoding="utf-8"))
    return scene, params, rows, ev["events"] if isinstance(ev, dict) else ev


def apply_changes(scene: dict, changes: dict) -> dict:
    """A copy of the scene with the planner's lane_overrides and zones (map metres) applied."""
    from road_features import apply_overrides
    s = copy.deepcopy(scene)
    apply_overrides(s, changes.get("lane_overrides") or [])  # raises on a lane id that matches nothing
    for i, z in enumerate(changes.get("zones") or []):
        s.setdefault("zones", []).append({"id": z.get("id") or f"whatif_{i}", "type": z["type"], "polygon": z["polygon"],
                                          **{k: v for k, v in z.items() if k not in ("id", "type", "polygon")}})
    return s


def _run(scene: dict, params: dict | None, rows: list[dict]) -> list[dict]:
    from lane_map import SceneMap
    from rules import Engine
    return [asdict(e) for e in Engine(SceneMap(scene), params, prefix="whatif").run(rows)]


def _summary(events: list[dict]) -> dict:
    c = [e for e in events if e.get("status") in COUNTED]
    return {"counted": len(c), "by_type": dict(Counter(e["type"] for e in c)),
            "by_condition": dict(Counter(e.get("condition") or "-" for e in c))}


def _key(e: dict) -> tuple:
    return (e.get("condition") or e["type"], tuple(sorted(e.get("track_ids") or [])))


def change_area(scene: dict, changes: dict, near_m: float = NEAR_M):
    """Where the change acts: changed lanes' centrelines and new zones, buffered by near_m (shapely)."""
    import fnmatch
    geoms = []
    for o in changes.get("lane_overrides") or []:
        geoms += [shapely.LineString(l["centreline"]) for l in scene.get("lanes", [])
                  if fnmatch.fnmatchcase(str(l["id"]), o["lane_id"]) and len(l.get("centreline", [])) >= 2]
    geoms += [shapely.Polygon(z["polygon"]) for z in changes.get("zones") or [] if len(z.get("polygon", [])) >= 3]
    return shapely.unary_union(geoms).buffer(near_m) if geoms else None


def near_rows(rows: list[dict], area) -> list[dict]:
    """Every row of each track that comes inside `area` (whole tracks, so a rule sees the full approach)."""
    if area is None:
        return rows
    shapely.prepare(area)
    keep = {r["track_id"] for r in rows if area.contains(shapely.Point(float(r["x"]), float(r["y"])))}
    return [r for r in rows if r["track_id"] in keep]


def replay(scene: dict, params: dict | None, rows: list[dict], changes: dict) -> dict:
    t0 = time.perf_counter()
    rows = near_rows(rows, change_area(scene, changes))
    base = _run(scene, params, rows)
    mod = _run(apply_changes(scene, changes), params, rows)
    bk = {_key(e): e for e in base if e.get("status") in COUNTED}
    mk = {_key(e): e for e in mod if e.get("status") in COUNTED}
    brief = lambda e: {"condition": e.get("condition"), "type": e["type"], "track_ids": e.get("track_ids"),  # noqa: E731
                       "flag_s": e.get("flag_s"), "x": e.get("x"), "y": e.get("y"), "value": e.get("value")}
    return {"label": "rule replay (measured on recorded traffic)", "note": REPLAY_NOTE,
            "baseline": _summary(base), "modified": _summary(mod),
            "added": [brief(mk[k]) for k in mk.keys() - bk.keys()], "removed": [brief(bk[k]) for k in bk.keys() - mk.keys()],
            "rows": len(rows), "tracks": len({r["track_id"] for r in rows}), "near_m": NEAR_M,
            "scope": f"vehicles that came within {NEAR_M:.0f} m of the change", "seconds": round(time.perf_counter() - t0, 1)}


def affected(events: list[dict], key: str, area: list | None = None, lane_globs: list[str] | None = None) -> list[dict]:
    """Counted historical events of the types `key` addresses, inside `area` (polygon, map metres) or on
    the lanes `lane_globs` match (fnmatch); everywhere when neither is given."""
    import fnmatch
    poly = shapely.Polygon(area) if area and len(area) >= 3 else None
    out = []
    for e in events:
        if e.get("status") not in COUNTED or e["type"] not in TARGETS.get(key, []):
            continue
        if poly is not None and not poly.contains(shapely.Point(float(e["x"]), float(e["y"]))):
            continue
        if lane_globs and not any(fnmatch.fnmatchcase(str(e.get("lane_id") or ""), g) for g in lane_globs):
            continue
        out.append(e)
    return out


def projection(events: list[dict], key: str, area: list | None, lane_globs: list[str] | None, period_s: float | None) -> dict:
    hit = affected(events, key, area, lane_globs)
    p = projected(key, len(hit), period_s)
    p["affected"] = {"events": len(hit), "by_condition": dict(Counter(e.get("condition") or "-" for e in hit)),
                     "event_ids": [e["event_id"] for e in hit][:200], "targets": TARGETS.get(key, [])}
    return p


def simulate(vdir: Path, changes: dict | None = None, countermeasure: str | None = None, area: list | None = None) -> dict:
    """Both answers for one session's violations folder. changes: {lane_overrides, zones}."""
    changes = changes or {}
    scene, params, rows, events = load_run(vdir)
    times = [float(r["time_s"]) for r in rows] if rows else []
    period = (max(times) - min(times)) if times else None
    out = {"session_dir": str(vdir), "period_s": round(period, 1) if period else None, "changes": changes}
    if changes.get("lane_overrides") or changes.get("zones"):
        out["replay"] = replay(scene, params, rows, changes)
    if countermeasure:
        if countermeasure not in COUNTERMEASURES:
            raise ValueError(f"unknown countermeasure {countermeasure!r}")
        globs = [o["lane_id"] for o in changes.get("lane_overrides") or []] or None
        out["projection"] = projection(events, countermeasure, area, None if area else globs, period)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("violations", type=Path)
    ap.add_argument("--limit", action="append", default=[], help="lane glob=km/h")
    ap.add_argument("--restrict", action="append", default=[], help="lane glob=bus|emergency")
    ap.add_argument("--zone", action="append", default=[], help="type:x0,y0,x1,y1 (axis-aligned box, map metres)")
    ap.add_argument("--countermeasure", default=None)
    ap.add_argument("--area", default=None, help="x0,y0,x1,y1 box for the countermeasure")
    a = ap.parse_args()
    box = lambda s: (lambda x0, y0, x1, y1: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]])(*map(float, s.split(",")))  # noqa: E731
    ov = [{"lane_id": s.split("=")[0], "speed_limit_kmh": float(s.split("=")[1])} for s in a.limit]
    ov += [{"lane_id": s.split("=")[0], "restricted": s.split("=")[1]} for s in a.restrict]
    zones = [{"type": z.split(":")[0], "polygon": box(z.split(":")[1])} for z in a.zone]
    res = simulate(a.violations, {"lane_overrides": ov, "zones": zones}, a.countermeasure, box(a.area) if a.area else None)
    if "replay" in res:
        r = res["replay"]
        print(f"[replay] {r['rows']} rows, {r['seconds']} s: counted {r['baseline']['counted']} -> {r['modified']['counted']}, "
              f"+{len(r['added'])} / -{len(r['removed'])}; by type {r['baseline']['by_type']} -> {r['modified']['by_type']}")
    if "projection" in res:
        p = res["projection"]
        print(f"[projection] {p['countermeasure']}: affected {p['affected']['events']} {p['affected']['by_condition']}, "
              f"before {p['before']}, after {p.get('projected_after')}, basis: {p['basis']}")


if __name__ == "__main__":
    main()
