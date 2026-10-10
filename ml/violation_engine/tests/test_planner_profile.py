"""A planner's edit saved from the twin (road.lane_overrides + road.extra_zones in a profile, twin/src/planEdits.ts)
reaches the engine (Expected_Output 9 item 6). End-to-end run (2026-10-10, flight 20261009_201727): 30 -> 20 km/h on
r46_s0_l2 and a no-stopping box gave +11 E1 on that lane and +1 B3 in the box, nothing removed."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lane_map import SceneMap  # noqa: E402
from rules import Engine  # noqa: E402
from run_violations import road_scene  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
