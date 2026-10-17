"""Every road attribute a planner can override per lane / road (profile road.lane_overrides,
road_features.apply_overrides): one test per attribute showing the condition it drives change with it,
on the same synthetic tracks as test_m2_conditions.py. Plus the schema of an override item and the
order against derive() (an override is never recomputed away).

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_map import SceneMap  # noqa: E402
from road_features import OVERRIDE_KEYS, ROAD_ATTRIBUTES, apply_overrides  # noqa: E402
from rules import Engine  # noqa: E402
from run_violations import road_scene  # noqa: E402
from schemas import conditions, profile_schema_errors  # noqa: E402
from test_m2_conditions import conds, lane_change, straight, u_turn  # noqa: E402
from test_rules import BASE_LANES, drive_stop_leave, kmh, track  # noqa: E402

AGAINST = (lambda t: (100 - kmh(30) * t, 0), 10.0)  # westbound in eastbound lane A
STOP = drive_stop_leave(60, 0, 25)  # 25 s stopped in lane A


def run(overrides, *tracks, lanes=BASE_LANES, vtype=None):
    sc = {"scene": "t", "lanes": copy.deepcopy(lanes), "zones": []}
    apply_overrides(sc, overrides)
    return conds(Engine(SceneMap(sc)).run([r for t in tracks for r in t]), vtype)


def on_a(**kw):
    return [{"lane_id": "A", **kw}]


class EachAttribute(unittest.TestCase):
    def test_speed_limit_kmh_drives_e1(self):
        car = track(straight(-100, 0, kmh(45), 6.0))
        self.assertEqual(run([], car, vtype="speeding"), [])
        self.assertEqual(run(on_a(speed_limit_kmh=30), car, vtype="speeding"), ["E1"])

    def test_restricted_drives_a5_and_null_clears(self):
        car = track(straight(-100, -3.5, kmh(40), 5.0))  # in lane C
        self.assertEqual(run([], car, vtype="lane_violation"), [])
        self.assertEqual(run([{"lane_id": "C", "restricted": "bus"}], car, vtype="lane_violation"), ["A5"])
        later_wins = [{"lane_id": "C", "restricted": "bus"}, {"lane_id": "C", "restricted": None}]
        self.assertEqual(run(later_wins, car, vtype="lane_violation"), [])

    def test_road_class_drives_b1_and_c3(self):
        self.assertEqual(run([], track(STOP), vtype="highway_stop"), [])
        self.assertEqual(run(on_a(road_class="highway"), track(STOP), vtype="highway_stop"), ["B1"])
        self.assertEqual(run(on_a(road_class="highway"), track(AGAINST), vtype="wrong_way"), ["C3"])
        hw = [dict(BASE_LANES[0], road_class="highway")] + BASE_LANES[1:]
        self.assertEqual(run(on_a(road_class="urban"), track(STOP), lanes=hw, vtype="highway_stop"), [])

    def test_lane_type_shoulder_drives_a6_and_b2(self):
        car = track(straight(-100, -3.5, kmh(40), 6.0))  # moving along lane C
        self.assertEqual(run([], car, vtype="lane_violation"), [])
        self.assertEqual(run([{"lane_id": "C", "lane_type": "shoulder"}], car, vtype="lane_violation"), ["A6"])
        self.assertEqual(run(on_a(road_class="highway"), track(STOP), vtype="highway_stop"), ["B1"])
        sc = {"lanes": copy.deepcopy(BASE_LANES)}
        apply_overrides(sc, on_a(road_class="highway", lane_type="shoulder"))
        ev = [(e.condition, e.status) for e in Engine(SceneMap(sc)).run(track(STOP)) if e.type == "highway_stop"]
        self.assertEqual(ev, [("B2", "possible_breakdown")])  # PRD 10: a shoulder stop may be a breakdown

    def test_lane_type_parking_is_not_checked_for_wrong_way(self):
        self.assertEqual(run([], track(AGAINST), vtype="wrong_way"), ["C1"])
        self.assertEqual(run(on_a(lane_type="parking"), track(AGAINST), vtype="wrong_way"), [])

    def test_lane_change_drives_a3(self):
        car = track(lane_change(0, -3.5))  # A -> C: to the right
        self.assertEqual(run([], car, vtype="lane_violation"), [])
        self.assertEqual(run(on_a(lane_change="none"), car, vtype="lane_violation"), ["A3"])
        self.assertEqual(run(on_a(lane_change="left"), car, vtype="lane_violation"), ["A3"])
        self.assertEqual(run(on_a(lane_change="right"), car, vtype="lane_violation"), [])

    def test_one_way_drives_c5(self):
        self.assertEqual(run([], track(AGAINST), vtype="wrong_way"), ["C1"])
        self.assertEqual(run(on_a(one_way=True), track(AGAINST), vtype="wrong_way"), ["C5"])
        ow = [dict(BASE_LANES[0], one_way=True)] + BASE_LANES[1:]
        self.assertEqual(run(on_a(one_way=False), track(AGAINST), lanes=ow, vtype="wrong_way"), ["C1"])

    def test_bridge_and_tunnel_drive_b5(self):
        self.assertEqual(run(on_a(bridge=True), track(STOP), vtype="highway_stop"), ["B5"])
        self.assertEqual(run(on_a(tunnel=True), track(STOP), vtype="highway_stop"), ["B5"])
        br = [dict(BASE_LANES[0], bridge=True)] + BASE_LANES[1:]
        self.assertEqual(run(on_a(bridge=False), track(STOP), lanes=br, vtype="highway_stop"), [])

    def test_ramp_drives_b4_and_c4_and_null_clears(self):
        self.assertEqual(run(on_a(ramp="on"), track(STOP), vtype="highway_stop"), ["B4"])
        self.assertEqual(run(on_a(ramp="off"), track(AGAINST), vtype="wrong_way"), ["C4"])
        rp = [dict(BASE_LANES[0], ramp="link")] + BASE_LANES[1:]
        self.assertEqual(run(on_a(ramp=None), track(STOP), lanes=rp, vtype="highway_stop"), [])

    def test_median_left_drives_d4(self):
        lanes = [dict(BASE_LANES[0], left_line="broken", road_id="1"), dict(BASE_LANES[2], left_line="broken", road_id="1")]
        car = track(u_turn(0, 3.5, 50, 1.75))
        self.assertEqual(run([], car, lanes=lanes, vtype="illegal_u_turn"), [])
        self.assertEqual(run([{"lane_id": "*", "median_left": True}], car, lanes=lanes, vtype="illegal_u_turn"), ["D4"])

    def test_every_key_is_described(self):
        known = {c["id"] for c in conditions()}
        self.assertEqual([a["key"] for a in ROAD_ATTRIBUTES], list(OVERRIDE_KEYS))
        for a in ROAD_ATTRIBUTES:
            self.assertTrue(set(a["drives"]) <= known, a["key"])


class Apply(unittest.TestCase):
    LANES = [{"id": "r1_s0_l1", "centreline": [[0, 0], [9, 0]], "speed_limit_kmh": 30, "median_left": True, "median_gap_m": 4.0},
             {"id": "r1_s0_l2", "centreline": [[0, 3], [9, 3]], "speed_limit_kmh": 30},
             {"id": "r2_s0_l1", "centreline": [[0, 9], [9, 9]], "speed_limit_kmh": 30}]

    def test_glob_later_wins_and_marks_changed_keys(self):
        sc = {"lanes": copy.deepcopy(self.LANES)}
        n = apply_overrides(sc, [{"lane_id": "r1_*", "road_class": "highway", "speed_limit_kmh": 30, "note": "x"},
                                 {"lane_id": "r1_s0_l1", "road_class": "urban", "median_left": False, "lane_type": "DRIVING"}],
                            mark=True)
        a, b, c = sc["lanes"]
        self.assertEqual(n, 3)
        self.assertEqual((a["road_class"], b["road_class"], a["lane_type"]), ("urban", "highway", "driving"))
        self.assertNotIn("median_gap_m", a)
        self.assertNotIn("note", a)
        self.assertEqual(a["overridden"], ["median_left", "road_class"])  # unchanged speed / lane_type not listed
        self.assertEqual(b["overridden"], ["road_class"])
        self.assertNotIn("overridden", c)

    def test_no_match_raises(self):
        with self.assertRaisesRegex(ValueError, "matches no lane"):
            apply_overrides({"lanes": copy.deepcopy(self.LANES)}, [{"lane_id": "r9_*", "bridge": True}])

    def test_applied_after_derive_in_site_mode(self):
        # derive() would make these highway (100 km/h) and one-way; the profile says otherwise
        lanes = [{"id": "L1", "road_id": "1", "centreline": [[0, 0], [100, 0]], "speed_limit_kmh": 100},
                 {"id": "L2", "road_id": "1", "centreline": [[0, -3.5], [100, -3.5]], "speed_limit_kmh": 100}]
        road = {"lane_overrides": [{"lane_id": "L*", "road_class": "urban", "one_way": False}]}
        sc = road_scene({"lanes": lanes}, SimpleNamespace(road=road), derive_features=True)
        self.assertEqual({(l["road_class"], l["one_way"]) for l in sc["lanes"]}, {("urban", False)})
        lane = SceneMap(sc).lane_by_id["L1"]
        self.assertEqual((lane.road_class, lane.one_way), ("urban", False))


class Schema(unittest.TestCase):
    def errs(self, item):
        return profile_schema_errors({"profile_version": 1, "name": "t", "road": {"lane_overrides": [item]}})

    def test_valid_items(self):
        for item in ({"lane_id": "r46_s0_l2", "speed_limit_kmh": 30}, {"lane_id": "r46_*", "restricted": None},
                     {"lane_id": "r46_s0_*", "road_class": "highway", "lane_type": "shoulder", "lane_change": "none",
                      "one_way": True, "bridge": True, "tunnel": False, "ramp": "on", "median_left": True, "note": "planner"},
                     {"lane_id": "x", "ramp": None}):
            self.assertEqual(self.errs(item), [], item)

    def test_invalid_items(self):
        for item in ({"speed_limit_kmh": 30}, {"lane_id": "x", "speed_limit_kmh": 0}, {"lane_id": "x", "road_class": "motorway"},
                     {"lane_id": "x", "lane_type": "sidewalk"}, {"lane_id": "x", "lane_change": "up"},
                     {"lane_id": "x", "one_way": "yes"}, {"lane_id": "x", "bridge": None}, {"lane_id": "x", "ramp": "exit"},
                     {"lane_id": "x", "road_class": None}, {"lane_id": "x", "width_m": 3}, {"lane_id": ""}):
            self.assertTrue(self.errs(item), item)


if __name__ == "__main__":
    unittest.main()
