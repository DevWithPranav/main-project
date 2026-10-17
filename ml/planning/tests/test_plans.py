"""Traffic plans (plans.py, plan_catalogue.py): locate, validate, conflicts, compile, risks, variants.

Run:  venv/Scripts/python.exe -m pytest ml/planning/tests/test_plans.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plan_catalogue as cat  # noqa: E402
import plans  # noqa: E402


def lane(i, road, sec, ln, x0, x1, y=0.0, limit=50, typ="driving", junction=False):
    return {"id": f"r{road}_s{sec}_l{ln}", "road_id": road, "centreline": [[x0, y], [x1, y]], "width_m": 3.5, "lane_type": typ,
            "speed_limit_kmh": limit, "junction": junction}


SCENE = {"scene": "T", "lanes": [
    lane(0, 1, 0, -1, 0, 200, 0.0), lane(1, 1, 0, -2, 0, 200, -3.5),            # two lanes, same direction, road 1 section 0
    lane(2, 1, 1, -1, 200, 400, 0.0), lane(3, 2, 0, -1, 100, 300, 60.0, junction=True),
    lane(4, 1, 0, 1, 0, 200, 3.5), lane(5, 1, 0, 2, 0, 200, 7.0, typ="shoulder"),
], "zones": [{"id": "cw", "type": "crosswalk", "polygon": [[100, -5], [108, -5], [108, 5], [100, 5]]}]}


def item(i, kind, x=50.0, y=0.0, **params):
    return {"id": i, "kind": kind, "x": x, "y": y, "yaw_deg": 0.0, "params": params}


def plan(**groups):
    return {"name": "p", "scene": "T", "props": groups.get("props", []), "rules": groups.get("rules", []), "scenarios": groups.get("scenarios", [])}


def codes(v, level):
    return {m["code"] for m in v[level]}


def test_catalogue_is_consistent():
    for k, s in cat.KINDS.items():
        assert s["group"] in cat.GROUPS and s["target"] in ("point", "lane", "area"), k
        assert s["replay"]["mode"].split(":")[0] in ("speed_limit", "restrict", "one_way", "closure", "zone", "none"), k
        assert s["carla"]["status"] in ("supported", "approximate", "unsupported"), k
        for q in s["params"]:
            assert q["type"] in ("number", "enum", "bool", "text") and (q["type"] != "enum" or q.get("options")), (k, q["key"])
    assert set(cat.catalogue()["kinds"]) == set(cat.KINDS)


def test_locate_finds_lanes_junction_and_zones():
    r = plans.locate(SCENE, 104.0, 1.0)
    assert r["on_road"] and r["nearest_lane"]["id"] in ("r1_s0_l-1", "r1_s0_l1") and r["inside_scene"]
    assert [z["type"] for z in r["zones"]] == ["crosswalk"]
    assert plans.locate(SCENE, 150, 60)["junction"] is True
    far = plans.locate(SCENE, 5000, 5000)
    assert not far["on_road"] and not far["inside_scene"]


def test_validate_params_position_and_names():
    ok = plan(rules=[item("a", "speed_limit", speed_kmh=30)])
    assert plans.validate(ok, SCENE)["ok"]
    bad = plan(rules=[item("a", "speed_limit", speed_kmh=500), item("b", "speed_limit", x=9000, y=9000, speed_kmh=30)])
    v = plans.validate(bad, SCENE)
    assert not v["ok"] and {"param", "outside_map"} <= codes(v, "errors")
    assert "name" in codes(plans.validate({**ok, "name": " "}, SCENE), "errors")
    assert "kind" in codes(plans.validate(plan(props=[item("z", "speed_limit")]), SCENE), "errors")  # a rule kind in props
    assert "duplicate_id" in codes(plans.validate(plan(rules=[item("a", "speed_limit"), item("a", "one_way")]), SCENE), "errors")
    sch = item("s", "speed_limit", speed_kmh=30)
    sch["schedule"] = {"from_s": 100, "to_s": 50}
    assert "schedule" in codes(plans.validate(plan(rules=[sch]), SCENE), "errors")


def test_prop_off_the_road_is_refused_for_lane_rules_and_warned_for_props():
    v = plans.validate(plan(rules=[item("r", "speed_limit", x=50, y=40, speed_kmh=30)], props=[item("p", "warning_sign", x=50, y=40)]), SCENE)
    assert "not_on_road" in codes(v, "errors") and "not_on_road" in codes(v, "warnings")


def test_speed_limit_conflicts_and_priority():
    a, b = item("a", "speed_limit", speed_kmh=30), item("b", "speed_limit", speed_kmh=60)
    v = plans.validate(plan(rules=[a, b]), SCENE)
    assert "speed_conflict" in codes(v, "conflicts") and not v["ok"]
    b["priority"] = 5
    v = plans.validate(plan(rules=[a, b]), SCENE)
    assert not v["conflicts"] and "speed_priority" in codes(v, "warnings")
    same = plans.validate(plan(rules=[item("a", "speed_limit", speed_kmh=30), item("b", "speed_limit", speed_kmh=30)]), SCENE)
    assert not same["conflicts"]


def test_closure_vs_bus_lane_and_bus_stop_conflicts():
    v = plans.validate(plan(props=[item("c", "barrier", length=1), item("bl", "bus_lane")]), SCENE)
    assert "closure_vs_reserved" in codes(v, "conflicts")
    v = plans.validate(plan(props=[item("bus", "bus_stop", x=60), item("np", "no_parking_zone", x=60, length_m=20, width_m=6)]), SCENE)
    assert "no_parking_vs_bus_stop" in codes(v, "conflicts")
    v = plans.validate(plan(props=[item("bl", "bus_lane"), item("el", "emergency_lane")]), SCENE)
    assert "restriction_conflict" in codes(v, "conflicts")


def test_closing_every_lane_warns():
    v = plans.validate(plan(scenarios=[item("rc", "road_closure", scope="section")]), SCENE)
    assert "closes_all_lanes" in codes(v, "warnings")


def test_compile_maps_what_the_replay_can_judge():
    p = plan(rules=[item("s", "speed_limit", speed_kmh=30, scope="section"), item("np", "no_parking", x=150, length_m=20, width_m=4, grace_s=10),
                    item("ow", "one_way", scope="lane"), item("sg", "signal_timing")],
             props=[item("sl", "speed_limit_sign", x=300, limit_kmh=40), item("cw", "pedestrian_crossing", x=30),
                    item("cone", "cones", x=120, length_m=10, width_m=3)],
             scenarios=[item("sp", "spawn_vehicles"), item("acc", "accident", x=60)])
    c = plans.compile_plan(p, SCENE)
    lo = {o["lane_id"]: o for o in c["changes"]["lane_overrides"]}
    assert lo["r1_s0_*"]["speed_limit_kmh"] == 30
    assert any(o.get("one_way") for o in c["changes"]["lane_overrides"])
    assert any(o.get("restricted") == "restricted" for o in c["changes"]["lane_overrides"])  # cones / accident closures
    zones = {z["type"]: z for z in c["changes"]["zones"]}  # props are compiled before rules
    assert set(zones) == {"no_parking", "crosswalk"} and zones["no_parking"]["grace_s"] == 10
    skipped = {s["kind"] for s in c["not_evaluated"]}
    assert {"signal_timing", "spawn_vehicles"} <= skipped
    assert all(s["reason"] for s in c["not_evaluated"])
    done = {e["kind"] for e in c["evaluated"]}
    assert {"speed_limit", "no_parking", "one_way", "speed_limit_sign", "pedestrian_crossing", "cones", "accident"} <= done


def test_compile_only_touches_driving_lanes_and_matching_lanes():
    c = plans.compile_plan(plan(props=[item("cone", "cones", x=50, y=7.0, length_m=4, width_m=1.0)]), SCENE)  # only the shoulder lane there
    assert not c["changes"]["lane_overrides"] and c["not_evaluated"][0]["reason"].startswith("no driving lane")


def test_lane_glob_scopes():
    assert plans.lane_glob("r46_s0_l-1_p2", "lane") == "r46_s0_l-1*"
    assert plans.lane_glob("r46_s0_l-1_p2", "section") == "r46_s0_*"
    assert plans.lane_glob("r46_s0_l-1_p2", "road") == "r46_*"


def test_rect_polygon_is_oriented_by_yaw():
    r = plans.rect_polygon(0, 0, 90, 10, 2)  # long side along +y
    ys = [p[1] for p in r]
    xs = [p[0] for p in r]
    assert max(ys) - min(ys) == pytest.approx(10) and max(xs) - min(xs) == pytest.approx(2)


def test_risks_are_marked_not_measured():
    r = plans.risk_checks(plan(props=[item("a", "traffic_signal", x=20), item("b", "traffic_signal", x=60)],
                               rules=[item("s", "speed_limit", x=120, speed_kmh=10)]), SCENE)
    texts = " ".join(x["message"] for x in r)
    assert "100" not in texts and "coordinated" in texts and "cut" in texts
    assert all(x["basis"] == plans.RISK_BASIS for x in r)


def test_alternatives_and_variants():
    p = plan(rules=[item("s", "speed_limit", speed_kmh=30)], props=[item("sig", "traffic_signal")])
    alts = plans.alternatives_for(p)
    assert any(a["for_item"] == "sig" for a in alts) and all(a["evidence"] == "preliminary" for a in alts)
    down = next(a for a in alts if a["for_item"] == "s" and "10 km/h less" in a["title"])
    v = plans.variant(p, down)
    assert v["rules"][0]["params"]["speed_kmh"] == 20 and p["rules"][0]["params"]["speed_kmh"] == 30  # original untouched
    add = next(a for a in alts if a["kind"] == "traffic_signal" and a["test"] and "add" in a["test"])
    assert any(r["kind"] == "custom_zone_rule" for r in plans.variant(p, add)["props"] + plans.variant(p, add)["rules"])
    assert plans.variant(p, next(a for a in alts if a["test"] is None)) is None


def test_analyze_without_traffic_is_preliminary_and_refuses_invalid_plans():
    good = plans.analyze(plan(rules=[item("s", "speed_limit", speed_kmh=30)]), SCENE, None)
    assert good["evidence"]["level"] == "preliminary" and good["pending_metrics"] and "replay" not in good
    bad = plans.analyze(plan(rules=[item("s", "speed_limit", speed_kmh=900)]), SCENE, None)
    assert bad["evidence"]["level"] == "insufficient data"
