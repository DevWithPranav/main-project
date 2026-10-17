"""Build Plan M1: road features in the lane map (road_features.py, lane_map.py, zone_tool.py,
export_lane_map.py output for Town04 / Town05).

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import copy
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lane_map import SceneMap  # noqa: E402
from road_features import apply_overrides, derive  # noqa: E402
from schemas import scene_errors  # noqa: E402
from zone_tool import lane_flags, parse_flags, set_lane_attrs  # noqa: E402

SCENES = Path(__file__).resolve().parents[1] / "configs" / "scenes"


def lane(id_, pts, road=None, limit=50, **kw):
    d = {"id": id_, "centreline": pts, "width_m": 3.5, "speed_limit_kmh": limit, **kw}
    if road is not None:
        d["road_id"] = road
    return d


def east(y, x0=0, x1=100):
    return [[x0, y], [x1, y]]


def west(y, x0=0, x1=100):
    return [[x1, y], [x0, y]]


class Median(unittest.TestCase):
    """Standard axes (site_m): the driver's left of an eastbound lane is +y."""

    def run_scene(self, lanes, coords="site_m"):
        return {l["id"]: l for l in derive({"coords": coords, "lanes": lanes})["lanes"]}

    def test_divided_road(self):
        # eastbound at y=0 (inner) and y=-3.5; westbound at y=8 and 11.5: 4.5 m between the edges
        out = self.run_scene([lane("E1", east(0)), lane("E2", east(-3.5)), lane("W1", west(8)), lane("W2", west(11.5))])
        self.assertTrue(out["E1"]["median_left"])
        self.assertAlmostEqual(out["E1"]["median_gap_m"], 4.5, delta=0.5)
        self.assertFalse(out["E2"]["median_left"])  # another eastbound lane on its left
        self.assertTrue(out["W1"]["median_left"])

    def test_undivided_road(self):
        out = self.run_scene([lane("E1", east(0)), lane("W1", west(3.5))])
        self.assertFalse(out["E1"]["median_left"])
        self.assertFalse(out["W1"]["median_left"])

    def test_shoulder_between_counts_as_separator(self):
        out = self.run_scene([lane("E1", east(0)), lane("S", east(3.25), lane_type="shoulder", width_m=3.0),
                              lane("W1", west(6.5))])
        self.assertTrue(out["E1"]["median_left"])

    def test_left_handed_carla_axes(self):
        # CARLA: the driver's left of an eastbound lane is -y
        out = self.run_scene([lane("E1", east(0)), lane("W1", west(-8))], coords="carla_world_m")
        self.assertTrue(out["E1"]["median_left"])
        out = self.run_scene([lane("E1", east(0)), lane("W1", west(8))], coords="carla_world_m")
        self.assertFalse(out["E1"]["median_left"])  # opposing traffic on its right: not a median

    def test_one_way_road(self):
        out = self.run_scene([lane("A1", east(0), road="1"), lane("A2", east(-3.5), road="1"),
                              lane("B1", east(20), road="2"), lane("B2", west(23.5), road="2"), lane("C", east(40))])
        self.assertTrue(out["A1"]["one_way"])
        self.assertFalse(out["B1"]["one_way"])
        self.assertNotIn("one_way", out["C"])  # no road_id: unknown


class RampsAndClass(unittest.TestCase):
    def scene(self):
        # highway road 1: piece P (60, before a sign) -> H1 (90) -> junction J1 -> off-ramp O (road 2) -> O2
        # on-ramp N (road 3) -> junction J2 -> H2 (road 4, 90); city street U (road 5) not linked
        return {"coords": "site_m", "lanes": [
            lane("P", east(0, -100, 0), road="1", limit=60, next=["H1"]),
            lane("H1", east(0, 0, 100), road="1", limit=90, next=["J1"]),
            lane("J1", [[100, 0], [120, -10]], road="9", limit=60, junction=True, next=["O"]),
            lane("O", [[120, -10], [200, -40]], road="2", limit=60, next=["O2"]),
            lane("O2", [[200, -40], [260, -60]], road="2", limit=30, next=[]),
            lane("N", [[300, -60], [380, -10]], road="3", limit=60, next=["J2"]),
            lane("J2", [[380, -10], [400, 0]], road="8", limit=60, junction=True, next=["H2"]),
            lane("H2", east(0, 400, 500), road="4", limit=90, next=[]),
            lane("U", east(80, 0, 100), road="5", limit=30, next=[]),
        ]}

    def test_ramps(self):
        out = {l["id"]: l for l in derive(self.scene())["lanes"]}
        self.assertEqual(out["O"]["ramp"], "off")
        self.assertEqual(out["O2"]["ramp"], "off")  # same ramp road, after a speed sign
        self.assertEqual(out["N"]["ramp"], "on")
        self.assertEqual(out["J1"]["ramp"], "link")
        self.assertEqual(out["J2"]["ramp"], "link")
        self.assertIsNone(out["P"]["ramp"])  # lower-limit piece of the highway road itself
        self.assertIsNone(out["U"]["ramp"])

    def test_road_class_follows_the_road(self):
        out = {l["id"]: l for l in derive(self.scene())["lanes"]}
        self.assertEqual(out["P"]["road_class"], "highway")  # 60 km/h sign on the highway road
        self.assertEqual(out["H2"]["road_class"], "highway")
        self.assertEqual(out["O"]["road_class"], "urban")
        self.assertEqual(out["J1"]["road_class"], "urban")

    def test_explicit_values_kept(self):
        sc = self.scene()
        sc["lanes"][3]["ramp"] = None  # marked "not a ramp" by hand
        sc["lanes"][8].update(road_class="highway", median_left=True, restricted="bus")
        out = {l["id"]: l for l in derive(sc)["lanes"]}
        self.assertIsNone(out["O"]["ramp"])
        self.assertEqual((out["U"]["road_class"], out["U"]["median_left"], out["U"]["restricted"]), ("highway", True, "bus"))

    def test_overrides(self):
        sc = self.scene()
        n = apply_overrides(sc, [{"lane_id": "H*", "speed_limit_kmh": 80}, {"lane_id": "U", "restricted": "bus"}])
        out = {l["id"]: l for l in sc["lanes"]}
        self.assertEqual(n, 3)
        self.assertEqual((out["H1"]["speed_limit_kmh"], out["H2"]["speed_limit_kmh"]), (80, 80))
        self.assertEqual(out["U"]["restricted"], "bus")
        with self.assertRaises(ValueError):
            apply_overrides(sc, [{"lane_id": "nope", "restricted": "bus"}])

    def test_schema(self):
        self.assertEqual(scene_errors(derive(self.scene())), [])
        bad = derive(self.scene())
        bad["lanes"][0]["ramp"] = "sideways"
        self.assertTrue(scene_errors(bad))


class LaneLoader(unittest.TestCase):
    def test_new_fields(self):
        sm = SceneMap({"lanes": [lane("A", [[0, 0], [10, 0], [20, 0]], road="7", z=[0, 5, 6], bridge=True,
                                      lane_change="right", ramp="on", road_class="highway", one_way=True,
                                      median_left=True, median_gap_m=4.0, restricted="bus", next=["B"])]})
        a = sm.lane_by_id["A"]
        self.assertEqual((a.road_id, a.next, a.ramp, a.road_class, a.restricted), ("7", ("B",), "on", "highway", "bus"))
        self.assertTrue(a.bridge and a.one_way and a.median_left)
        self.assertAlmostEqual(a.height_at(15), 5.5)
        self.assertTrue(a.may_change("right"))
        self.assertFalse(a.may_change("left"))
        self.assertEqual(sm.match(12, 0.5).lane.height_at(sm.match(12, 0.5).s), 5.2)

    def test_defaults_for_old_maps(self):
        a = SceneMap({"lanes": [lane("A", east(0))]}).lane_by_id["A"]
        self.assertEqual((a.lane_change, a.road_class, a.ramp, a.restricted, a.z), ("both", "urban", None, None, None))
        self.assertTrue(a.may_change("left") and a.may_change("right"))
        self.assertFalse(a.bridge or a.median_left)


class ZoneTool(unittest.TestCase):
    def site(self):
        return {"lanes": [{"id": "L1", "centreline_px": [[0, 0], [9, 9]]}, {"id": "L2", "centreline_px": [[0, 0], [9, 9]]},
                          {"id": "X", "centreline_px": [[0, 0], [9, 9]]}]}

    def test_parse_flags(self):
        self.assertEqual(parse_flags("bridge, bus,ramp_on"),
                         {"road_class": "urban", "bridge": True, "restricted": "bus", "ramp": "on"})
        self.assertEqual(parse_flags("highway,median,one_way"),
                         {"road_class": "highway", "median_left": True, "one_way": True})
        self.assertEqual(parse_flags(""), {"road_class": "urban"})
        with self.assertRaises(ValueError):
            parse_flags("flyover")

    def test_flags_round_trip(self):
        for text in ("bridge,bus", "highway,median,ramp_off", "tunnel,one_way,emergency"):
            self.assertEqual(sorted(lane_flags(parse_flags(text))), sorted(text.split(",")))

    def test_set_lane_attrs(self):
        site = self.site()
        self.assertEqual(set_lane_attrs(site, "L*", ["bridge=true", "lane_change=none", "speed_limit_kmh=80"]), ["L1", "L2"])
        self.assertEqual((site["lanes"][0]["bridge"], site["lanes"][1]["lane_change"], site["lanes"][1]["speed_limit_kmh"]),
                         (True, "none", 80.0))
        self.assertNotIn("bridge", site["lanes"][2])
        set_lane_attrs(site, "L1", ["restricted=bus"])
        set_lane_attrs(site, "L1", ["restricted=null"])
        self.assertIsNone(site["lanes"][0]["restricted"])

    def test_set_lane_attrs_errors(self):
        for glob, a in (("Q", ["bridge=true"]), ("L1", ["colour=red"]), ("L1", ["lane_change=sideways"]),
                        ("L1", ["bridge=maybe"]), ("L1", ["bridge"])):
            with self.assertRaises(ValueError, msg=f"{glob} {a}"):
                set_lane_attrs(self.site(), glob, a)


class CarlaMaps(unittest.TestCase):
    """The exported Town04 / Town05 lane maps (export_lane_map.py, 2026-10-09)."""

    @classmethod
    def setUpClass(cls):
        cls.maps = {t: json.loads((SCENES / f"{t}.json").read_text()) for t in ("Town04", "Town05")}

    def lanes(self, town, **match):
        return [l for l in self.maps[town]["lanes"] if all(l.get(k) == v for k, v in match.items())]

    def test_schema(self):
        for t, m in self.maps.items():
            self.assertEqual(scene_errors(m), [], t)

    def test_every_lane_has_the_features(self):
        keys = {"road_id", "next", "lane_change", "z", "bridge", "tunnel", "ramp", "road_class", "median_left", "restricted"}
        for t in self.maps:
            for l in self.lanes(t, lane_type="driving"):
                self.assertLessEqual(keys, set(l), f"{t} {l['id']}")
                self.assertEqual(len(l["z"]), len(l["centreline"]))

    def test_lane_change_permissions(self):
        for t in self.maps:
            got = Counter(l["lane_change"] for l in self.lanes(t, lane_type="driving", junction=False))
            self.assertLessEqual(set(got), {"none", "left", "right", "both"})
            self.assertGreater(got["none"], 0, t)  # some lanes forbid leaving them
            self.assertGreater(got["left"] + got["right"] + got["both"], got["none"], t)

    def test_heights(self):
        z = {t: max(max(l["z"]) for l in self.maps[t]["lanes"]) for t in self.maps}
        self.assertGreater(z["Town05"], 9.0)  # elevated ring, OpenDRIVE elevation up to 10.03 m
        self.assertGreater(z["Town04"], 10.0)  # flyover at the cloverleaf, 11.0 m

    def test_bridges_are_raised(self):
        for t in self.maps:
            br = self.lanes(t, bridge=True)
            self.assertTrue(br, t)
            others = [np.mean(l["z"]) for l in self.maps[t]["lanes"] if not l.get("bridge")]
            self.assertGreater(np.median([np.mean(l["z"]) for l in br]), np.median(others) + 3.0, t)

    def test_highway_and_ramps(self):
        for t in self.maps:
            self.assertTrue(self.lanes(t, road_class="highway", junction=False), t)
            ramps = Counter(l["ramp"] for l in self.maps[t]["lanes"] if l.get("ramp"))
            self.assertTrue(ramps["on"] and ramps["off"] and ramps["link"], f"{t} {ramps}")
            for l in self.maps[t]["lanes"]:  # a ramp is never part of the highway road itself
                if l.get("ramp") in ("on", "off"):
                    self.assertEqual(l["road_class"], "urban", f"{t} {l['id']}")

    def test_medians_on_divided_roads(self):
        for t in self.maps:
            med = self.lanes(t, median_left=True)
            self.assertTrue(med, t)
            self.assertTrue(all(not l["junction"] and l["lane_type"] == "driving" for l in med))
            self.assertGreater(sum(l["road_class"] == "highway" for l in med), len(med) / 3, t)

    def test_one_way_in_town04(self):
        self.assertTrue(self.lanes("Town04", one_way=True))

    def test_loads_and_matches(self):
        sm = SceneMap(copy.deepcopy(self.maps["Town05"]))
        hw = next(l for l in sm.lanes if l.road_class == "highway" and l.bridge and not l.junction)
        p = hw.centreline[len(hw.centreline) // 2]
        m = sm.match(*p)
        self.assertIsNotNone(m)
        self.assertGreater(m.lane.height_at(m.s), 3.0)


if __name__ == "__main__":
    unittest.main()
