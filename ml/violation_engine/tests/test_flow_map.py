"""Tests for the learned traffic-flow direction (flow_map.py, Phase C).

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flow_map  # noqa: E402
from lane_map import SceneMap  # noqa: E402
from rules import Engine  # noqa: E402
from test_rules import kmh, track  # noqa: E402


def east(tid, t0=0.0):
    return track((lambda t: (-40 + kmh(30) * t, 0.5), 9.0), tid=tid, t0=t0)


class Flow(unittest.TestCase):
    def rows(self, n_east, wrong=True):
        rows = [r for i in range(n_east) for r in east(i + 1, t0=2.0 * i)]
        if wrong:
            rows += track((lambda t: (35 - kmh(30) * t, 0.5), 9.0), tid=99, t0=40.0)
        return rows

    def test_learns_the_traffic_direction(self):
        flow = flow_map.learn(self.rows(8))
        one_way = [f for f in flow.values() if f["one_way"]]
        self.assertGreater(len(one_way), 10)
        self.assertTrue(all(abs(f["dir_deg"]) < 10 for f in one_way))  # eastbound, the wrong-way car outvoted

    def test_too_few_tracks_learns_nothing(self):
        self.assertEqual(flow_map.learn(self.rows(3, wrong=False)), {})

    def test_wrong_way_on_learned_lanes(self):
        rows = self.rows(8)
        sc = SceneMap({"scene": "flow", "lanes": flow_map.flow_lanes(flow_map.learn(rows)), "zones": []})
        ww = {t for e in Engine(sc).run(rows) if e.type == "wrong_way" for t in e.track_ids}
        self.assertEqual(ww, {99})

    def test_two_way_cell_is_junction(self):
        rows = self.rows(5, wrong=False)
        rows += [r for i in range(5) for r in track((lambda t: (35 - kmh(30) * t, 0.5), 9.0), tid=50 + i, t0=60.0 + 2 * i)]
        flow = flow_map.learn(rows)
        self.assertTrue(flow and not any(f["one_way"] for f in flow.values()))

    def test_two_way_road_in_neighbouring_cells_is_not_one_way(self):
        # eastbound at y = 0.5, westbound at y = -3.5: the two lanes land in different 4 m cells, so
        # each cell alone looks one-way; the opposite traffic next door makes them a two-way road
        rows = self.rows(6, wrong=False)
        rows += [r for i in range(6) for r in track((lambda t: (35 - kmh(30) * t, -3.5), 9.0), tid=50 + i, t0=60.0 + 2 * i)]
        flow = flow_map.learn(rows)
        self.assertTrue(flow)
        self.assertFalse(any(f["one_way"] for f in flow.values()))
        sc = SceneMap({"scene": "flow", "lanes": flow_map.flow_lanes(flow), "zones": []})
        self.assertFalse([e for e in Engine(sc).run(rows) if e.type == "wrong_way"])


if __name__ == "__main__":
    unittest.main()
