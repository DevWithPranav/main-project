"""A planner's edit saved from the twin (road.lane_overrides + road.extra_zones in a profile, twin/src/planEdits.ts)
reaches the engine (Expected_Output 9 item 6). End-to-end run (2026-10-10, flight 20261009_201727): 30 -> 20 km/h on
r46_s0_l2 and a no-stopping box gave +11 E1 on that lane and +1 B3 in the box, nothing removed."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lane_map import SceneMap  # noqa: E402
from rules import Engine  # noqa: E402
from run_violations import engine_config, merge_zones, road_scene  # noqa: E402
from test_rules import kmh, track  # noqa: E402


def scene():
    return {"scene": "t", "zones": [], "lanes": [
        {"id": "r1_s0_l-1", "centreline": [[-50, 0], [50, 0]], "width_m": 3.5, "speed_limit_kmh": 50.0, "lane_type": "driving",
         "junction": False, "left_line": "broken", "right_line": "solid"}]}


class PlannerProfile(unittest.TestCase):
    road = {"lane_overrides": [{"lane_id": "r1_s0_l-1*", "speed_limit_kmh": 20}],
            "extra_zones": [{"id": "twin_1", "type": "no_parking", "polygon": [[20, -3], [40, -3], [40, 3], [20, 3]]}]}

    def test_profile_edits_reach_the_scene(self):
        sc = road_scene(scene(), SimpleNamespace(road=self.road))
        self.assertEqual(sc["lanes"][0]["speed_limit_kmh"], 20)
        self.assertEqual([z["id"] for z in sc["zones"]], ["twin_1"])

    def test_engine_flags_under_the_edited_rules(self):
        rows = track((lambda t: (-40 + kmh(35) * t, 0.0), 6.0), tid=1)  # 35 km/h: legal at 50, speeding at 20
        rows += track((lambda t: (30.0, 0.0), 40.0), tid=2, t0=10.0)  # stopped 40 s inside the drawn zone
        base = {e.type for e in Engine(SceneMap(scene())).run(rows)}
        edit = {e.type for e in Engine(SceneMap(road_scene(scene(), SimpleNamespace(road=self.road)))).run(rows)}
        self.assertNotIn("speeding", base)
        self.assertNotIn("no_parking", base)
        self.assertIn("speeding", edit)
        self.assertIn("no_parking", edit)


class ProfileZoneFile(unittest.TestCase):
    """A profile's road.zones file (zones drawn in the dashboard, backend /api/zones) applies on top of a CLI
    --zones file, and once (not twice) without one. Run check (2026-10-11, flight 20261002_001635): a drawn
    no_parking zone flagged B3 events carrying its id next to the events of a --zones file zone."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.drawn = self.tmp / "Town.json"  # the backend's engine file shape
        self.drawn.write_text(json.dumps({"zones": [{"id": "z_api", "type": "no_parking", "grace_s": 3,
                                                     "polygon": [[20, -3], [40, -3], [40, 3], [20, 3]]}]}))
        self.cli = self.tmp / "cli.json"
        self.cli.write_text(json.dumps({"zones": [{"id": "cli_1", "type": "no_parking",
                                                   "polygon": [[-40, -3], [-20, -3], [-20, 3], [-40, 3]]}]}))
        prof = json.loads((Path(__file__).resolve().parents[1] / "configs" / "profiles" / "default.json").read_text())
        prof["road"] = {"zones": str(self.drawn)}  # absolute: REPO / it is itself
        self.profile = self.tmp / "p.json"
        self.profile.write_text(json.dumps(prof))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def args(self, zones):
        a = SimpleNamespace(profile=self.profile, params=None, scene=None, zones=zones, red_light=False, learn_flow=False)
        engine_config(a)
        return a

    def zone_ids(self, a):
        return [z["id"] for z in road_scene(merge_zones(scene(), a.zones), a)["zones"]]

    def test_with_a_cli_zones_file_both_apply(self):
        a = self.args(self.cli)
        self.assertEqual(a.zones, self.cli)
        self.assertEqual(self.zone_ids(a), ["cli_1", "z_api"])

    def test_without_one_the_profile_file_applies_once(self):
        a = self.args(None)
        self.assertEqual(a.zones, self.drawn)
        self.assertIsNone(getattr(a, "road_zones_file", None))
        self.assertEqual(self.zone_ids(a), ["z_api"])

    def test_events_carry_the_drawn_zone_id(self):
        a = self.args(self.cli)
        rows = track((lambda t: (30.0, 0.0), 40.0), tid=1)  # stopped 40 s in the drawn zone (grace 3 s)
        rows += track((lambda t: (-30.0, 0.0), 40.0), tid=2)  # and in the --zones one (rule default wait)
        ev = Engine(SceneMap(road_scene(merge_zones(scene(), a.zones), a))).run(rows)
        self.assertEqual({e.zone_id for e in ev if e.type == "no_parking"}, {"z_api", "cli_1"})


if __name__ == "__main__":
    unittest.main()
