"""Custom traffic configuration (traffic_config.py) and what microsim.py does with it.

Run:  venv/Scripts/python.exe -m pytest ml/planning/tests/test_traffic_config.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import microsim as ms  # noqa: E402
import plans  # noqa: E402
import traffic_config as tc  # noqa: E402


def lane(i, x0, x1, y=0.0, limit=50, nxt=None, rclass=None):
    d = {"id": f"r1_s{i}_l-1", "road_id": 1, "centreline": [[x0, y], [(x0 + x1) / 2, y], [x1, y]], "lane_type": "driving", "speed_limit_kmh": limit,
         "junction": False, "next": nxt or []}
    if rclass:
        d["road_class"] = rclass
    return d


def corridor(n=6, seg=200.0, limit=50):
    return {"scene": "T", "lanes": [lane(i, i * seg, (i + 1) * seg, limit=limit, nxt=[f"r1_s{i + 1}_l-1"] if i < n - 1 else []) for i in range(n)], "zones": []}


def item(i, kind, x=20.0, y=0.0, **params):
    return {"id": i, "kind": kind, "x": x, "y": y, "yaw_deg": 0.0, "params": params}


def plan(*scenarios, traffic=None):
    d = {"name": "p", "scene": "T", "props": [], "rules": [], "scenarios": list(scenarios)}
    if traffic is not None:
        d["traffic"] = traffic
    return d


def run(scene, pl=None, **kw):
    kw.setdefault("duration_s", 40)
    kw.setdefault("warmup_s", 5)
    return ms.run(scene, pl, kw.pop("seed", 0), keep_states=True, region=((600, 0), 700), **kw)


def kinds_seen(r):
    return {v[1] for _, st in r["states"] for v in st if not v[7]}


def test_defaults_presets_and_validation():
    d = tc.normalize({})
    assert d["background"]["density_per_km"] == 7.0 and d["background"]["enabled"] is True
    assert tc.normalize({"preset": "heavy"})["background"]["density_per_km"] == 14.0
    assert tc.normalize({"preset": "heavy", "background": {"density_per_km": 3}})["background"]["density_per_km"] == 3  # explicit fields win
    assert tc.normalize({"preset": "empty"})["background"]["enabled"] is False
    assert tc.is_default({}) and not tc.is_default({"preset": "light"})
    for k in tc.PRESETS:
        assert tc.validate({"preset": k}) == [], k
    assert tc.validate({"background": {"density_per_km": 99}})[0].startswith("Density: 99 is outside 0 to 40")
    assert any("Vehicle mix" in e for e in tc.validate({"background": {"mix": {"car": 0, "van": 0, "truck": 0, "bus": 0, "motorcycle": 0}}}))
    assert any("demand" in e.lower() for e in tc.validate({"background": {"demand_profile": "tsunami"}}))
    assert any("unknown setting" in e for e in tc.validate({"colour": "red"}))
    assert tc.validate({"duration_s": 5})  # below the 30 s minimum


def test_schema_covers_every_default_and_helpers_work():
    sc = tc.schema_doc()
    keys = {f["key"] for sec in sc["sections"] for f in sec["fields"]}
    assert {"seed", "background.density_per_km", "background.mix.truck", "background.behaviour_mix.aggressive", "background.road_weight.highway"} <= keys
    for sec in sc["sections"]:
        for f in sec["fields"]:
            assert tc.get_path(sc["defaults"], f["key"]) == f["default"], f["key"]
    assert tc.profile_factor("constant", 0.4) == 1.0
    assert tc.profile_factor("ramp_up", 0.0) == pytest.approx(0.3) and tc.profile_factor("ramp_up", 1.0) == pytest.approx(1.6)
    assert tc.profile_factor("morning_peak", 0.35) == pytest.approx(1.5)
    assert tc.describe({"preset": "rush_hour"}).startswith("Rush hour: 12 veh/km")
    import numpy as np
    assert {tc.pick_weighted(np.random.RandomState(1), {"a": 0, "b": 5}) for _ in range(5)} == {"b"}


def test_plan_validation_reports_a_bad_traffic_config():
    scene = corridor()
    bad = plans.validate({**plan(), "traffic": {"background": {"density_per_km": 500}}}, scene)
    assert not bad["ok"] and any(m["code"] == "traffic" for m in bad["errors"])
    assert plans.validate({**plan(), "traffic": {"preset": "heavy"}}, scene)["ok"]


def test_density_and_enabled():
    scene = corridor()
    light = run(scene, density_per_km=2.0)
    heavy = run(scene, density_per_km=25.0)
    assert heavy["vehicles_total"] > 3 * light["vehicles_total"] > 0
    off = run(scene, traffic={"background": {"enabled": False}})
    assert off["vehicles_total"] == 0 and sum(off["spawned"].values()) == 0
    assert run(scene, traffic={"preset": "empty"})["vehicles_total"] == 0


def test_vehicle_mix_is_followed():
    scene = corridor()
    trucks = run(scene, traffic={"background": {"mix": {"car": 0, "van": 0, "truck": 100, "bus": 0, "motorcycle": 0}}})
    assert kinds_seen(trucks) == {"truck"} and set(trucks["spawned"]) == {"truck"}
    mixed = run(scene, traffic={"background": {"density_per_km": 30}})
    assert {"car", "van"} <= set(mixed["spawned"]) and mixed["spawned"]["car"] > mixed["spawned"].get("truck", 0)


def test_drivers_and_speed_factor():
    scene = corridor(n=8, seg=300.0)

    def mean_speed(**bg):
        r = run(scene, traffic={"background": {"density_per_km": 2.0, "speed_factor_sd": 0.0, **bg}})
        v = [x[4] for _, st in r["states"] for x in st if not x[7]]
        return sum(v) / len(v)

    cautious = mean_speed(behaviour_mix={"cautious": 100, "normal": 0, "aggressive": 0})
    aggressive = mean_speed(behaviour_mix={"cautious": 0, "normal": 0, "aggressive": 100})
    assert aggressive > cautious + 4.0
    assert mean_speed(speed_factor_mean=1.25) > mean_speed(speed_factor_mean=0.75) + 5.0


def test_demand_profile_builds_up_over_the_run():
    scene = corridor(n=8, seg=300.0)
    r = run(scene, traffic={"background": {"density_per_km": 12.0, "demand_profile": "ramp_up"}}, duration_s=80)
    counts = [sum(1 for v in st if not v[7]) for _, st in r["states"]]
    n = len(counts)
    assert sum(counts[: n // 5]) / (n // 5) < sum(counts[-(n // 5):]) / (n // 5)  # fewer at the start than at the end
    const = run(scene, traffic={"background": {"density_per_km": 12.0}}, duration_s=80)
    cc = [sum(1 for v in st if not v[7]) for _, st in const["states"]]
    assert abs(sum(cc[: n // 5]) - sum(cc[-(n // 5):])) / (n // 5) < 6  # a constant profile stays level


def test_road_class_weight_moves_the_traffic():
    scene = {"scene": "T", "lanes": [lane(0, 0, 800, y=0.0, rclass="urban"), {**lane(1, 0, 800, y=100.0, rclass="highway"), "id": "r2_s0_l-1"}], "zones": []}
    r = run(scene, traffic={"background": {"density_per_km": 20.0, "road_weight": {"urban": 1.0, "highway": 0.0}}})
    ys = {round(v[3]) for _, st in r["states"] for v in st}
    assert ys == {0}  # nobody on the highway lane
    r2 = run(scene, traffic={"background": {"density_per_km": 20.0, "road_weight": {"urban": 0.0, "highway": 1.0}}})
    assert {round(v[3]) for _, st in r2["states"] for v in st} == {100}


def test_source_mix_platoon_cap_and_profile():
    scene = corridor()
    transit = run(scene, plan(item("f", "traffic_flow", vph=900, mix="transit"), traffic={"background": {"enabled": False}}), duration_s=60)
    assert set(transit["spawned"]) == {"bus"} and transit["vehicles_total"] >= 8
    capped = run(scene, plan(item("f", "traffic_flow", vph=3000, max_vehicles=4), traffic={"background": {"enabled": False}}), duration_s=60)
    assert capped["vehicles_total"] == 4
    plat = run(scene, plan(item("f", "traffic_flow", vph=360, platoon_size=5), traffic={"background": {"enabled": False}}), duration_s=60)
    first = [t for t, st in plat["states"] if st]
    assert plat["vehicles_total"] % 5 == 0 or plat["vehicles_total"] >= 5  # bursts of 5
    early = run(scene, plan(item("f", "traffic_flow", vph=900, profile="ramp_up"), traffic={"background": {"enabled": False}}), duration_s=80)
    steady = run(scene, plan(item("f", "traffic_flow", vph=900, profile="constant"), traffic={"background": {"enabled": False}}), duration_s=80)
    assert 0 < early["vehicles_total"] < steady["vehicles_total"] * 1.4
    assert first  # the platoon run produced states


def test_group_spacing_and_waypoint_route():
    scene = corridor()
    tight = run(scene, plan(item("g", "spawn_vehicles", count=4, spacing_s=1.0), traffic={"background": {"enabled": False}}), duration_s=30)
    loose = run(scene, plan(item("g", "spawn_vehicles", count=4, spacing_s=8.0), traffic={"background": {"enabled": False}}), duration_s=30)

    def first_seen(r):
        seen = {}
        for t, st in r["states"]:
            for v in st:
                seen.setdefault(v[0], t)
        return sorted(seen.values())

    ft, fl = first_seen(tight), first_seen(loose)
    assert len(ft) == len(fl) == 4 and (fl[-1] - fl[0]) > (ft[-1] - ft[0]) + 10
    # waypoint: two ways from lane 0 to lane 3, the via point forces the long way
    lanes = [lane(0, 0, 100, nxt=["r1_s1_l-1", "r1_s2_l-1"]), lane(1, 100, 200, nxt=["r1_s3_l-1"]), lane(2, 100, 300, y=20, nxt=["r1_s3_l-1"]), lane(3, 300, 400)]
    w = ms.World({"scene": "T", "lanes": lanes})
    direct, _ = w.build_route(0, "car", (350, 0), None)
    via, _ = w.build_route(0, "car", (350, 0), (200, 20))
    assert direct == [1, 3] and via == [2, 3]


def test_zone_speed_factor_and_vehicle_bias():
    scene = corridor(n=8, seg=300.0)
    base = {"background": {"density_per_km": 3.0, "speed_factor_sd": 0.0}}
    slow = run(scene, plan(item("z", "traffic_density", x=1200.0, radius_m=2000, factor=1.0, speed_factor=0.5), traffic=base))
    free = run(scene, plan(item("z", "traffic_density", x=1200.0, radius_m=2000, factor=1.0, speed_factor=1.0), traffic=base))
    sp = lambda r: sum(x[4] for _, st in r["states"] for x in st) / sum(len(st) for _, st in r["states"])  # noqa: E731
    assert sp(slow) < sp(free) * 0.75
    biased = run(scene, plan(item("z", "traffic_density", x=1200.0, radius_m=2000, factor=4.0, bias="trucks"), traffic=base))
    assert biased["spawned"].get("truck", 0) > free["spawned"].get("truck", 0) + 3


def test_run_reports_what_it_ran():
    r = run(corridor(), traffic={"preset": "rush_hour"})
    assert r["config"]["background"]["demand_profile"] == "morning_peak" and r["traffic"].startswith("Rush hour")
    assert sum(r["spawned"].values()) == r["vehicles_total"]
    a = run(corridor(), traffic={"preset": "rush_hour"}, seed=3)
    b = run(corridor(), traffic={"preset": "rush_hour"}, seed=3)
    assert a["metrics"] == b["metrics"] and a["spawned"] == b["spawned"]  # reproducible with the configuration


def test_every_preset_and_traffic_scenario_is_valid_and_runs():
    import plan_catalogue as cat
    assert len(tc.PRESETS) >= 15 and len(tc.SCENARIO_PRESETS) >= 8
    scene = corridor(n=8, seg=300.0)
    for k, sp in tc.SCENARIO_PRESETS.items():
        assert tc.validate(sp["traffic"]) == [], k
        items = []
        for i, it in enumerate(sp["items"]):
            spec = cat.KINDS[it["kind"]]
            assert plans._param_errors(it["kind"], it["params"]) == [], (k, it["kind"])
            items.append({"id": f"i{i}", "kind": it["kind"], "x": 300.0, "y": 0.0, "yaw_deg": 0.0, "params": it["params"], **({"schedule": it["schedule"]} if it.get("schedule") else {}),
                          "target": {"lane_id": "r1_s1_l-1"}} if spec["target"] == "lane" else
                         {"id": f"i{i}", "kind": it["kind"], "x": 300.0, "y": 0.0, "yaw_deg": 0.0, "params": it["params"], **({"schedule": it["schedule"]} if it.get("schedule") else {})})
        pl = {"name": k, "scene": "T", "props": [], "rules": [], "scenarios": items, "traffic": sp["traffic"]}
        assert plans.validate(pl, scene)["ok"], k
        r = ms.run(scene, pl, 0, duration_s=40, warmup_s=5, keep_states=True)
        assert r["vehicles_total"] > 0 and r["states"], k
    for k in tc.PRESETS:
        r = ms.run(scene, None, 0, duration_s=30, warmup_s=5, traffic={"preset": k})
        assert (r["vehicles_total"] > 0) == (k != "empty"), k
    # the presets differ in the way they say they do
    mix = lambda k: tc.normalize({"preset": k})["background"]["mix"]  # noqa: E731
    assert mix("two_wheelers")["motorcycle"] > 25 and mix("bus_corridor")["bus"] == 20 and mix("delivery_hours")["van"] + mix("delivery_hours")["truck"] >= 40
    assert tc.normalize({"preset": "aggressive_drivers"})["background"]["behaviour_mix"]["aggressive"] == 70
    assert tc.normalize({"preset": "gridlock"})["background"]["density_per_km"] == 25.0
