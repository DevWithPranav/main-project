"""Props in the virtual traffic model (microsim.py): signals, stop signs, crossings, bus stops, warning signs, speed breakers,
parking / no-parking zones, custom objects. A crossing of two straight lanes at (0, 0): lane 0 runs along +x, lane 1 along +y.

Run:  venv/Scripts/python.exe -m pytest ml/planning/tests/test_virtual_props.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import microsim as ms  # noqa: E402
import plan_catalogue as cat  # noqa: E402
import plans  # noqa: E402
import junctions  # noqa: E402  (ml/violation_engine, on the path once plans is imported)

NO_BG = {"background": {"enabled": False}}


def cross_scene(limit=50):
    def ln(i, pts):
        return {"id": f"r{i}_s0_l-1", "road_id": i, "centreline": pts, "lane_type": "driving", "speed_limit_kmh": limit, "junction": False, "next": []}
    return {"scene": "T", "zones": [], "lanes": [ln(1, [[-300, 0], [-150, 0], [0, 0], [150, 0], [300, 0]]), ln(2, [[0, -300], [0, -150], [0, 0], [0, 150], [0, 300]])]}


def item(i, kind, x=0.0, y=0.0, yaw=0.0, **params):
    return {"id": i, "kind": kind, "x": x, "y": y, "yaw_deg": yaw, "params": params}


def flow(i, x, y, vph=900, **kw):
    return item(i, "traffic_flow", x, y, vph=vph, vehicle=kw.pop("vehicle", "car"), speed_kmh=kw.pop("speed_kmh", 45), **kw)


def plan(props=(), rules=(), scenarios=(), traffic=None):
    d = {"name": "p", "scene": "T", "props": list(props), "rules": list(rules), "scenarios": list(scenarios)}
    if traffic is not None:
        d["traffic"] = traffic
    return d


def run(pl, scene=None, **kw):
    kw.setdefault("duration_s", 120)
    kw.setdefault("warmup_s", 10)
    kw.setdefault("traffic", NO_BG)
    return ms.run(scene or cross_scene(), pl, kw.pop("seed", 0), keep_states=True, region=((0, 0), 400), **kw)


def phase_at(timeline, t):
    cur = timeline[0][1]
    for tt, ph in timeline:
        if tt <= t:
            cur = ph
    return cur


def crossings(r, axis):
    """Times at which vehicles of lane 0 (axis 'x') or lane 1 (axis 'y') pass the crossing point."""
    seen, out = {}, []
    for t, st in r["states"]:
        for v in st:
            if v[7]:
                continue
            on = abs(v[3]) < 1.5 if axis == "x" else abs(v[2]) < 1.5
            pos = v[2] if axis == "x" else v[3]
            if not on:
                continue
            prev = seen.get(v[0])
            if prev is not None and prev < 0.0 <= pos:
                out.append(t)
            seen[v[0]] = pos
    return out


def stopped_near(r, axis, lo=-12.0, hi=-1.0):
    n = 0
    for _, st in r["states"]:
        for v in st:
            on = abs(v[3]) < 1.5 if axis == "x" else abs(v[2]) < 1.5
            pos = v[2] if axis == "x" else v[3]
            if on and not v[7] and lo <= pos <= hi and v[4] < 1.0:
                n += 1
    return n


TWO_FLOWS = [flow("fa", -280, 0), flow("fb", 0, -280)]


def test_signal_gives_each_direction_its_own_green():
    r = run(plan(props=[item("s", "traffic_signal", cycle_s=40, green_s=14)], scenarios=TWO_FLOWS))
    sig = r["signals"][0]
    assert sig["kind"] == "traffic_signal" and len(sig["heads"]) == 2 and {h["group"] for h in sig["heads"]} == {"A", "B"}
    phases = [ph for _, ph in sig["timeline"]]
    assert phases[:4] == ["A", "R", "B", "R"] and len({ph for ph in phases}) == 3
    ca, cb = crossings(r, "x"), crossings(r, "y")
    assert len(ca) >= 5 and len(cb) >= 5
    # a lane-0 vehicle crosses in its own green (or just after it ends, when too close to stop), never in the other group's green
    wrong_a = [t for t in ca if phase_at(sig["timeline"], t) == "B"]
    wrong_b = [t for t in cb if phase_at(sig["timeline"], t) == "A"]
    assert not wrong_a and not wrong_b
    assert stopped_near(r, "x") > 20 and stopped_near(r, "y") > 20  # queues stand at the red
    free = run(plan(scenarios=TWO_FLOWS))
    assert len(crossings(free, "x")) > len(ca)  # without the signal more vehicles get through


def test_signal_timing_rule_changes_the_green_and_ops_modes_work():
    base = run(plan(props=[item("s", "traffic_signal", cycle_s=60, green_s=15)], scenarios=TWO_FLOWS))
    longer = run(plan(props=[item("s", "traffic_signal", cycle_s=60, green_s=15)], rules=[item("t", "signal_timing", green_delta_s=20, radius_m=40)], scenarios=TWO_FLOWS))
    assert len(crossings(longer, "x")) > len(crossings(base, "x"))
    allred = run(plan(props=[item("s", "traffic_signal")], scenarios=TWO_FLOWS + [item("o", "signal_ops", mode="all_red", radius_m=60)]))
    assert len(crossings(allred, "x")) <= 3 and len(crossings(allred, "y")) <= 3
    off = run(plan(props=[item("s", "traffic_signal")], scenarios=TWO_FLOWS + [item("o", "signal_ops", mode="flashing", radius_m=60)]))
    assert len(crossings(off, "x")) >= len(crossings(base, "x"))
    longg = run(plan(props=[item("s", "traffic_signal")], scenarios=TWO_FLOWS + [item("o", "signal_ops", mode="fixed_long_green", radius_m=60)]))
    assert len(crossings(longg, "x")) > 10 and len(crossings(longg, "y")) <= 3  # A always green, B never


def test_adaptive_signal_extends_the_green_while_vehicles_arrive():
    fixed = run(plan(props=[item("s", "traffic_signal", cycle_s=50, green_s=10, adaptive=False)], scenarios=[flow("fa", -280, 0, vph=1100)]))
    adapt = run(plan(props=[item("s", "traffic_signal", cycle_s=50, green_s=10, adaptive=True)], scenarios=[flow("fa", -280, 0, vph=1100)]))

    def a_durations(r):
        tl, out = r["signals"][0]["timeline"], []
        for (t0, ph), (t1, _) in zip(tl, tl[1:]):
            if ph == "A":
                out.append(t1 - t0)
        return out
    assert max(a_durations(adapt)) > max(a_durations(fixed)) + 3.0
    assert max(a_durations(adapt)) <= 10 + ms.MAX_EXTENSION_S + 1.5
    assert len(crossings(adapt, "x")) >= len(crossings(fixed, "x"))


def test_stop_sign_makes_vehicles_stop_and_wait():
    r = run(plan(props=[item("st", "stop_sign")], scenarios=[flow("fa", -280, 0, vph=240)]), duration_s=90)
    assert r["signals"][0]["kind"] == "stop_sign"
    assert stopped_near(r, "x", -8.0, -1.0) >= 12  # several vehicle-samples at standstill by the line (3 s hold)
    assert len(crossings(r, "x")) >= 3  # and they do carry on


def test_pedestrian_crossing_stops_vehicles_while_people_cross():
    busy = run(plan(props=[item("c", "pedestrian_crossing", ped_per_h=1500)], scenarios=[flow("fa", -280, 0, vph=600)]))
    sig = busy["signals"][0]
    assert sig["kind"] == "crossing" and sig["pedestrian_groups"] > 10 and any(ph == "R" for _, ph in sig["timeline"])
    assert stopped_near(busy, "x") > 10
    empty = run(plan(props=[item("c", "pedestrian_crossing", ped_per_h=0)], scenarios=[flow("fa", -280, 0, vph=600)]))
    assert stopped_near(empty, "x") == 0 and len(crossings(empty, "x")) > len(crossings(busy, "x"))
    rule = run(plan(rules=[item("p", "pedestrian_priority", ped_per_h=1500)], scenarios=[flow("fa", -280, 0, vph=600)]))
    assert rule["signals"][0]["kind"] == "crossing"


def test_bus_stop_holds_buses_not_cars():
    buses = run(plan(props=[item("b", "bus_stop", dwell_s=25)], scenarios=[flow("fa", -280, 0, vph=120, vehicle="bus")]), duration_s=100)
    cars = run(plan(props=[item("b", "bus_stop", dwell_s=25)], scenarios=[flow("fa", -280, 0, vph=120, vehicle="car")]), duration_s=100)
    assert stopped_near(buses, "x", -18.0, -1.0) >= 25 * 5 / 3  # each bus stands about 25 s at 5 samples per s
    assert stopped_near(cars, "x", -18.0, -1.0) == 0


def test_warning_sign_slows_traffic_before_it_and_breaker_caps_speed():
    def mean_v(r, lo, hi):
        v = [x[4] for _, st in r["states"] for x in st if abs(x[3]) < 1.5 and lo <= x[2] <= hi and not x[7]]
        return sum(v) / len(v)
    sc = [flow("fa", -280, 0, vph=240, speed_kmh=50)]
    base = run(plan(scenarios=sc), duration_s=80)
    warn = run(plan(props=[item("w", "warning_sign", text="school")], scenarios=sc), duration_s=80)
    assert mean_v(warn, -55, -5) < mean_v(base, -55, -5) * 0.85 and abs(mean_v(warn, 80, 200) - mean_v(base, 80, 200)) < 4
    brk = run(plan(props=[item("k", "speed_breaker", cap_kmh=15)], scenarios=sc), duration_s=80)
    assert mean_v(brk, -3, 6) < 22 and mean_v(brk, 100, 250) > 35


def test_parking_and_no_parking_zones_shape_curb_stops():
    parking = run(plan(props=[item("p", "parking_zone", length_m=30, width_m=4, stops_per_h=900, stay_s=40)]), duration_s=120)
    assert parking["stops"]["made"] > 10 and parking["stops"]["in_parking_zones"] == parking["stops"]["made"]
    assert any(v[7] for _, st in parking["states"] for v in st)  # stopped vehicles are in the lane
    curb = {"background": {"enabled": True, "density_per_km": 1.0, "curb_stops_per_km_h": 30.0}}
    free = run(plan(), traffic=curb, duration_s=300)
    banned = run(plan(props=[item("n", "no_parking_zone", x=0.0, y=0.0, length_m=600, width_m=8, grace_s=5)]), traffic=curb, duration_s=300)
    assert free["stops"]["made"] >= 2 and free["stops"]["prevented"] == 0
    assert banned["stops"]["prevented"] >= 2 and banned["stops"]["made"] < free["stops"]["made"]


def test_custom_object_blocks_the_lane_it_stands_in():
    r = run(plan(props=[item("o", "custom_object", x=-60.0, y=0.0, length_m=2, width_m=2)], scenarios=[flow("fa", -280, 0, vph=300)]), duration_s=90)
    assert max(v[2] for _, st in r["states"] for v in st if not v[7] and abs(v[3]) < 1.5) < -58.0  # nobody gets past it
    assert stopped_near(r, "x", -120.0, -64.0) > 20  # a queue forms behind it


def test_every_prop_is_simulated_by_the_virtual_model():
    props = [k for k, v in cat.KINDS.items() if v["group"] == "prop"]
    assert len(props) >= 16
    assert [k for k in props if cat.KINDS[k]["virtual"]["status"] == "unsupported"] == []
    for k in ("traffic_signal", "stop_sign", "pedestrian_crossing", "bus_stop", "warning_sign", "speed_breaker", "parking_zone", "no_parking_zone", "custom_object"):
        assert cat.KINDS[k]["virtual"]["status"] in ("supported", "approximate"), k
    assert cat.KINDS["signal_timing"]["virtual"]["status"] == "supported" and cat.KINDS["signal_ops"]["virtual"]["status"] == "supported"


def test_a_signal_far_from_any_lane_is_reported_not_silently_dropped():
    r = run(plan(props=[item("s", "traffic_signal", x=900.0, y=900.0)], scenarios=[flow("fa", -280, 0, vph=300)]), duration_s=40)
    assert r["signals"] == [] and r["signals_unplaced"] == ["s"]


def junction_scene():
    """The same crossing with a junction: lanes run in to its edge at +-10 m, junction lanes (junction_id "1") cross it."""
    def ln(lid, road, pts, nxt, junction=False):
        d = {"id": lid, "road_id": road, "centreline": pts, "lane_type": "driving", "speed_limit_kmh": 50, "junction": junction, "next": nxt, "width_m": 3.5}
        if junction:
            d["junction_id"] = "1"
        return d
    sc = {"scene": "T", "zones": [], "lanes": [
        ln("r1_s0_l-1", "1", [[-300, 0], [-150, 0], [-10, 0]], ["r3_s0_l-1"]),
        ln("r3_s0_l-1", "3", [[-10, 0], [0, 0], [10, 0]], ["r5_s0_l-1"], junction=True),
        ln("r5_s0_l-1", "5", [[10, 0], [150, 0], [300, 0]], []),
        ln("r2_s0_l-1", "2", [[0, -300], [0, -150], [0, -10]], ["r4_s0_l-1"]),
        ln("r4_s0_l-1", "4", [[0, -10], [0, 0], [0, 10]], ["r6_s0_l-1"], junction=True),
        ln("r6_s0_l-1", "6", [[0, 10], [0, 150], [0, 300]], [])]}
    junctions.build(sc)
    return sc


def test_junction_outline_and_approaches():
    sc = junction_scene()
    (j,) = sc["junctions"]
    assert j["id"] == "J1" and sorted(j["lanes"]) == ["r3_s0_l-1", "r4_s0_l-1"]
    by_road = {a["road_id"]: a for a in j["approaches"]}
    assert set(by_road) == {"1", "2"}  # the exits are not approaches
    a = by_road["1"]
    assert a["lanes"] == ["r1_s0_l-1"] and abs(a["heading_deg"]) < 1 and (a["x"], a["y"]) == (-10, 0)
    assert sorted(p[1] for p in a["stop_line"]) == [-1.75, 1.75] and all(p[0] == -10 for p in a["stop_line"])
    assert junctions.junction_at(sc, 0, 0)["id"] == "J1" and junctions.junction_at(sc, 0, 8)["id"] == "J1"
    assert junctions.junction_at(sc, 40, 40) is None and junctions.junction_at(sc, 8, 8) is None  # a corner off the lanes
    info = plans.locate(sc, 1, 1)["junction_info"]
    assert info["id"] == "J1" and len(info["approaches"]) == 2


def test_signal_on_every_junction_approach_is_one_controller():
    sc = junction_scene()
    appr = sc["junctions"][0]["approaches"]
    props = [{**item(f"s{k}", "traffic_signal", a["x"], a["y"], cycle_s=40, green_s=14), "target": {"junction_id": "J1", "approach_id": a["id"]}}
             for k, a in enumerate(appr)]
    r = run(plan(props=props, scenarios=TWO_FLOWS), scene=sc)
    assert len(r["signals"]) == 1 and r["signals_unplaced"] == []
    sig = r["signals"][0]
    assert {h["group"] for h in sig["heads"]} == {"A", "B"} and len(sig["heads"]) == 2
    assert all(abs(h["x"]) == 11 or abs(h["y"]) == 11 for h in sig["heads"])  # 1 m before the junction edge
    ca, cb = crossings(r, "x"), crossings(r, "y")
    assert len(ca) >= 5 and len(cb) >= 5
    gx = next(h["group"] for h in sig["heads"] if h["y"] == 0)  # the group of the x road (the first approach by angle is A)
    gy = "B" if gx == "A" else "A"
    # the centre is 11 m past the stop line: a vehicle over the line at the end of its green is there ~2 s later, so the
    # check is at 1 s before the crossing (it was then on the line or past it, in its own green or the clearance)
    assert not [t for t in ca if phase_at(sig["timeline"], t - 1.0) == gy] and not [t for t in cb if phase_at(sig["timeline"], t - 1.0) == gx]
    assert stopped_near(r, "x", -25.0, -9.0) > 20 and stopped_near(r, "y", -25.0, -9.0) > 20


def test_stop_sign_on_one_approach_controls_only_that_road():
    sc = junction_scene()
    a = next(a for a in sc["junctions"][0]["approaches"] if a["road_id"] == "1")
    st = {**item("st", "stop_sign", a["x"], a["y"]), "target": {"junction_id": "J1", "approach_id": a["id"]}}
    r = run(plan(props=[st], scenarios=TWO_FLOWS), scene=sc, duration_s=90)
    (sig,) = r["signals"]
    assert sig["kind"] == "stop_sign" and len(sig["heads"]) == 1 and sig["heads"][0]["y"] == 0
    assert stopped_near(r, "x", -25.0, -9.0) >= 12 and stopped_near(r, "y", -25.0, -9.0) == 0


def test_signals_on_one_junction_are_not_flagged_as_uncoordinated():
    sc = junction_scene()
    props = [{**item(f"s{k}", "traffic_signal", a["x"], a["y"]), "target": {"junction_id": "J1", "approach_id": a["id"]}}
             for k, a in enumerate(sc["junctions"][0]["approaches"])]
    assert not [r for r in plans.risk_checks(plan(props=props), sc) if "coordinated" in r["message"]]
    loose = [item("a", "traffic_signal", -10, 0), item("b", "traffic_signal", 0, -10)]  # two separate signals 14 m apart
    assert [r for r in plans.risk_checks(plan(props=loose), sc) if "coordinated" in r["message"]]
