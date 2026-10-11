"""Virtual traffic (microsim.py): car following, speed limits, closures, spawns, reproducibility, intervals.

Run:  venv/Scripts/python.exe -m pytest ml/planning/tests/test_microsim.py -q
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import microsim as ms  # noqa: E402


def lane(i, x0, x1, y=0.0, limit=50, nxt=None, typ="driving", junction=False):
    return {"id": f"r1_s{i}_l-1", "road_id": 1, "centreline": [[x0, y], [(x0 + x1) / 2, y], [x1, y]], "lane_type": typ, "speed_limit_kmh": limit,
            "junction": junction, "next": nxt or []}


def corridor(n=4, seg=150.0, limit=50):
    """A straight one-way road of n lanes of `seg` metres, each leading to the next; the last leads nowhere."""
    lanes = [lane(i, i * seg, (i + 1) * seg, limit=limit, nxt=[f"r1_s{i + 1}_l-1"] if i < n - 1 else []) for i in range(n)]
    return {"scene": "T", "lanes": lanes, "zones": []}


def world(scene, patch=None, seed=0):
    return ms.World(scene, patch, seed)


def test_free_vehicle_reaches_its_speed_and_keeps_the_limit():
    w = world(corridor(limit=36))
    lane0 = w.lanes[0]
    v = w.spawn("car", lane0, 5.0, 0.0)
    v.v0 = 1.0
    for _ in range(300):
        w.step(0.2)
    assert 9.0 < v.v <= 10.2  # limit 36 km/h = 10 m/s
    assert v.v0 * 10 >= v.v


def test_follower_keeps_a_gap_and_never_overlaps():
    w = world(corridor())
    lead = w.spawn("truck", w.lanes[0], 60.0, 5.0)
    lead.v0 = 0.5
    foll = w.spawn("car", w.lanes[0], 5.0, 14.0)
    foll.v0 = 1.2
    mind = 1e9
    for _ in range(400):
        w.step(0.2)
        if lead.exited or foll.exited:
            break
        gap = (lead.s - foll.s if lead.lane == foll.lane else 999) - (lead.L + foll.L) / 2
        mind = min(mind, gap)
    assert mind > 0.5  # the follower slowed instead of driving through the truck


def test_closed_lane_stops_traffic_before_it_and_a_queue_forms():
    scene = corridor()
    w = world(scene, [{"lane_id": "r1_s2_l-1", "restricted": "restricted"}])
    vs = [w.spawn("car", w.lanes[0], 5.0 + 12.0 * k, 8.0) for k in range(6)]
    for _ in range(900):
        w.step(0.2)
    assert all(v.exited is None for v in vs)
    assert all(w.lanes[v.lane].id != "r1_s2_l-1" for v in vs)  # nobody entered the closed lane
    assert max(v.s + w.lanes[v.lane].cum[0] for v in vs) <= 150.5
    assert sum(1 for v in vs if v.v < 0.3) >= 5  # the platoon stands at the closure


def test_open_road_vehicles_leave_at_the_end():
    w = world(corridor(n=2, seg=60.0))
    v = w.spawn("car", w.lanes[0], 5.0, 10.0)
    for _ in range(600):
        w.step(0.2)
    assert v.exited is not None and v.exited[1] == "left"


def test_speed_limit_override_slows_the_lane():
    base = world(corridor(), None)
    slow = world(corridor(), [{"lane_id": "r1_s1_l-1", "speed_limit_kmh": 18}])
    out = []
    for w in (base, slow):
        v = w.spawn("car", w.lanes[1], 10.0, 4.0)
        v.v0 = 1.0
        for _ in range(60):
            w.step(0.2)
        out.append(v.v)
    assert out[1] < out[0] - 2.0 and out[1] <= 5.2


def test_bus_lane_keeps_cars_out_but_not_buses():
    w = world(corridor(), [{"lane_id": "r1_s1_l-1", "restricted": "bus"}])
    assert not w.can_enter(w.lanes[1], "car") and w.can_enter(w.lanes[1], "bus")
    car = w.spawn("car", w.lanes[0], 100.0, 8.0)
    for _ in range(300):
        w.step(0.2)
    assert w.lanes[car.lane].id != "r1_s1_l-1"


def test_shortest_route_goes_around_a_closed_lane():
    # two parallel ways from lane 0 to lane 3: via 1 (short) or via 2 (long)
    lanes = [lane(0, 0, 100, nxt=["r1_s1_l-1", "r1_s2_l-1"]), lane(1, 100, 200, nxt=["r1_s3_l-1"]),
             {**lane(2, 100, 300, y=20, nxt=["r1_s3_l-1"])}, lane(3, 300, 400)]
    w = world({"scene": "T", "lanes": lanes})
    assert w.shortest_route(0, 3, "car") == [1, 3]
    w.lanes[1].closed = True
    assert w.shortest_route(0, 3, "car") == [2, 3]
    w.lanes[2].closed = True
    assert w.shortest_route(0, 3, "car") is None


def plan_with(*scenarios, props=(), rules=()):
    return {"name": "p", "scene": "T", "props": list(props), "rules": list(rules), "scenarios": list(scenarios)}


def test_run_is_reproducible_and_seed_dependent():
    scene = corridor(n=6, seg=200.0)
    a = ms.run(scene, None, 3, duration_s=40, warmup_s=5, density_per_km=20, region=((600, 0), 300))
    b = ms.run(scene, None, 3, duration_s=40, warmup_s=5, density_per_km=20, region=((600, 0), 300))
    c = ms.run(scene, None, 4, duration_s=40, warmup_s=5, density_per_km=20, region=((600, 0), 300))
    assert a["metrics"] == b["metrics"]
    assert a["metrics"] != c["metrics"] or a["vehicles_total"] != c["vehicles_total"]


def test_spawned_group_appears_and_moves():
    scene = corridor(n=6, seg=200.0)
    plan = plan_with({"id": "s1", "kind": "spawn_vehicles", "x": 20.0, "y": 0.0, "params": {"count": 4, "vehicle": "bus", "speed_kmh": 30}})
    r = ms.run(scene, plan, 0, duration_s=40, warmup_s=5, density_per_km=0, keep_states=True, region=((300, 0), 400))
    kinds = {v[1] for _, st in r["states"] for v in st}
    assert "bus" in kinds and r["vehicles_total"] >= 4
    # the buses advance along the road
    with_bus = [st for _, st in r["states"] if any(v[1] == "bus" for v in st)]
    first = {v[0]: v[2] for v in with_bus[len(with_bus) // 4] if v[1] == "bus"}
    last = {v[0]: v[2] for v in with_bus[-1] if v[1] == "bus"}
    moved = [last[i] - first[i] for i in first if i in last]
    assert moved and max(moved) > 50.0


def test_flow_source_emits_at_the_rate():
    scene = corridor(n=6, seg=200.0)
    plan = plan_with({"id": "f1", "kind": "traffic_flow", "x": 10.0, "y": 0.0, "params": {"vph": 720, "vehicle": "car", "speed_kmh": 40}})
    r = ms.run(scene, plan, 0, duration_s=60, warmup_s=5, density_per_km=0, region=((500, 0), 600))
    assert 8 <= r["vehicles_total"] <= 14  # 720 veh/h = 1 every 5 s over 60 s -> about 12


def test_accident_blocks_the_lane_and_traffic_queues_behind_it():
    scene = corridor(n=6, seg=200.0)
    acc = plan_with({"id": "a1", "kind": "accident", "x": 450.0, "y": 0.0, "params": {"lanes_blocked": 1, "duration_s": 300}},
                    {"id": "s1", "kind": "spawn_vehicles", "x": 20.0, "y": 0.0, "params": {"count": 6, "vehicle": "car", "speed_kmh": 50}})
    r = ms.run(scene, acc, 0, duration_s=90, warmup_s=5, density_per_km=0, keep_states=True, region=((300, 0), 400))
    final = r["states"][-1][1]
    movers = [v for v in final if not v[7]]
    assert sum(1 for v in movers if v[6]) >= 4  # stopped behind the accident
    assert max(v[2] for v in movers) < 452.0   # nobody drove through it


def test_compare_gives_intervals_and_flags_real_differences():
    scene = corridor(n=6, seg=200.0)
    plan = plan_with(rules=[{"id": "r1", "kind": "speed_limit", "x": 300.0, "y": 0.0, "params": {"speed_kmh": 15, "scope": "road"}, "target": {"lane_id": "r1_s1_l-1"}}])
    res = ms.compare(scene, plan, seeds=4, duration_s=40, warmup_s=5, density_per_km=25)
    m = res["metrics"]["mean_speed_kmh"]
    assert m["baseline"]["n"] == 4 and m["baseline"]["ci95"] is not None and m["plan"]["mean"] < m["baseline"]["mean"]
    assert m["verdict"] in ("different", "no difference detected") and res["label"].startswith("virtual simulation")
    assert res["states"]["baseline"] and res["states"]["plan"] and res["notes"]
    tr = ms.trajectories(res["states"]["plan"])
    assert tr and all(len(p[0]) == 4 for p in list(tr.values())[:3])


def test_ci95_uses_student_t():
    assert ms.ci95([1.0]) == {"mean": 1.0, "ci95": None, "n": 1}
    r = ms.ci95([10.0, 12.0, 11.0, 13.0, 9.0])
    assert r["mean"] == 11.0 and r["ci95"] == pytest.approx(2.78 * np.std([10, 12, 11, 13, 9], ddof=1) / np.sqrt(5), abs=0.01)
    assert ms.ci95([None, None]) == {"mean": None, "ci95": None, "n": 0}


def test_every_catalogue_kind_has_a_virtual_entry():
    import plan_catalogue as cat
    for k, v in cat.KINDS.items():
        assert v["virtual"]["status"] in ("supported", "approximate", "unsupported") and v["virtual"]["how"], k
    assert cat.KINDS["traffic_flow"]["virtual"]["status"] == "supported"
