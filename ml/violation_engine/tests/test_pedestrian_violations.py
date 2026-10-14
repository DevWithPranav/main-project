"""Pedestrian-crossing loop (simulation/violation_scenarios/pedestrian_violations.py): the schedule, the
station search on a real map (offline, no simulator) and the Runner's clock, against stand-in actors.

Needs the `carla` package (the carlaAir env), so the main venv skips it:
    C:\\Users\\AFIF\\.conda\\envs\\carlaAir\\python.exe -m unittest ml/violation_engine/tests/test_pedestrian_violations.py -v
"""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "simulation" / "violation_scenarios"))
try:
    import carla  # noqa: F401
    import pedestrian_violations as pv
    from stage_violations import SCENES_DIR, XODR_DIR, Trajectory
except ImportError:
    pv = None


class FakeActor:
    _ids = iter(range(100, 10_000))

    def __init__(self):
        self.id = next(self._ids)
        self.poses = []

    def set_transform(self, tf):
        self.poses.append((tf.location.x, tf.location.y, tf.location.z))


def straight(x0: float, x1: float, speed: float, hold_mid: float | None = None, y: float = 0.0) -> Trajectory:
    """A car along y = const. With hold_mid it stops half way for that long (marks start / end around the
    stop, like an F1 act); without, start / end mark the whole drive (like an F3 pass)."""
    xm = (x0 + x1) / 2
    tr = Trajectory().move([np.array([x0, y, 0.0]), np.array([xm, y, 0.0])], speed).mark("start")
    if hold_mid is None:
        return tr.move([np.array([xm, y, 0.0]), np.array([x1, y, 0.0])], speed).mark("end")
    tr.hold(hold_mid).mark("end")
    return tr.move([np.array([xm, y, 0.0]), np.array([x1, y, 0.0])], speed)


def walker_traj(y0: float, y1: float, x: float = 10.0) -> Trajectory:
    return Trajectory().move([np.array([x, y0, 0.0]), np.array([x, y1, 0.0])], 1.4)


def act(cond: str, expected: bool, car: Trajectory, walker=None) -> dict:
    return {"type": "zebra_crossing", "condition": cond, "expected": expected, "traj": car,
            "walkers": [walker] if walker is not None else [], "note": f"{cond} {expected}", "truth": ("start", "end")}


@unittest.skipIf(pv is None, "needs the carla package (carlaAir env)")
class TestRounds(unittest.TestCase):
    def test_every_act_once_per_round_in_a_seeded_order(self):
        acts = [{"n": i} for i in range(4)]
        r1 = pv.rounds(acts, 3, random.Random(1))
        self.assertEqual(len(r1), 12)
        for k in range(3):
            self.assertEqual(sorted(a["n"] for a in r1[4 * k:4 * k + 4]), [0, 1, 2, 3])
        self.assertEqual([a["n"] for a in r1], [a["n"] for a in pv.rounds(acts, 3, random.Random(1))])


@unittest.skipIf(pv is None, "needs the carla package (carlaAir env)")
class TestRunner(unittest.TestCase):
    def setUp(self):
        self.car, self.walker = FakeActor(), FakeActor()
        wp = SimpleNamespace(transform=SimpleNamespace(location=SimpleNamespace(x=0.0, y=0.0, z=0.0)))
        acts = [act("F3", True, straight(0, 20, 8.0), walker_traj(-4, 4)),
                act("F1", True, straight(0, 20, 8.0, hold_mid=12.0)),
                act("F1", False, straight(0, 20, 8.0, hold_mid=6.0))]
        self.st = SimpleNamespace(id=7, wp=wp, acts=acts, centre=[0.0, 0.0])
        self.log = {"violations": [], "negatives": [], "acts": []}
        self.out = Path(tempfile.mkdtemp()) / "scenario_log.json"

    def run_clock(self, seconds: float, gap_s: float = 6.0, dt: float = 0.05):
        r = pv.Runner(self.st, self.car, self.walker, random.Random(0), 1.0, gap_s)
        t, frame = 0.0, 0
        while t < seconds or r.act is not None:  # on, then until the act in progress has ended and been parked
            snap = SimpleNamespace(frame=frame, timestamp=SimpleNamespace(elapsed_seconds=t))
            r.tick(snap, self.log, self.out)
            t, frame = t + dt, frame + 1
        return r

    def test_acts_are_logged_in_the_stager_format_with_truth_frames(self):
        self.run_clock(120.0)
        self.assertGreaterEqual(len(self.log["acts"]), 3)
        for e in self.log["acts"]:
            self.assertEqual(e["actor_id"], self.car.id)
            self.assertLess(e["start_sim_s"], e["end_sim_s"])
            self.assertLessEqual(e["start_frame"], e["end_frame"])
            self.assertEqual(e["station"], 7)
        self.assertEqual(len(self.log["violations"]) + len(self.log["negatives"]), len(self.log["acts"]))
        self.assertTrue(all(e["expected"] for e in self.log["violations"]))
        self.assertFalse(any(e["expected"] for e in self.log["negatives"]))
        self.assertEqual(json.loads(self.out.read_text())["acts"], json.loads(json.dumps(self.log["acts"])))  # written after every act

    def test_the_stop_lasts_what_the_plan_says(self):
        self.run_clock(120.0)
        held = {round(e["end_sim_s"] - e["start_sim_s"], 0) for e in self.log["acts"] if e["condition"] == "F1"}
        self.assertEqual(held, {12.0, 6.0})

    def test_walker_ids_only_on_walker_acts(self):
        self.run_clock(120.0)
        for e in self.log["acts"]:
            self.assertEqual("walker_actor_ids" in e, e["condition"] == "F3")

    def test_the_gap_between_acts_and_the_parking(self):
        self.run_clock(120.0, gap_s=6.0)
        acts = sorted(self.log["acts"], key=lambda e: e["start_sim_s"])
        for a, b in zip(acts, acts[1:]):
            # the act ends when its longest path ends (the car's, after its truth end), the next waits gap_s more
            self.assertGreaterEqual(b["start_sim_s"] - a["end_sim_s"], 6.0)
        self.assertGreater(self.car.poses[-1][0], 2000.0)  # parked far away and below the road
        self.assertLess(self.car.poses[-1][2], -50.0)
        self.assertGreater(self.walker.poses[-1][0], 2000.0)
        moving = [p for p in self.car.poses if p[0] < 2000.0]
        self.assertTrue(moving)
        self.assertTrue(all(-1e-6 <= p[0] <= 20.0 + 1e-6 for p in moving))  # on the planned path, never anywhere else

    def test_a_station_without_walker_acts_never_moves_a_walker(self):
        self.st.acts = [a for a in self.st.acts if a["condition"] == "F1"]
        r = pv.Runner(self.st, self.car, None, random.Random(0), 1.0, 6.0)
        t = 0.0
        while t < 60.0:
            r.tick(SimpleNamespace(frame=int(t * 20), timestamp=SimpleNamespace(elapsed_seconds=t)), self.log, self.out)
            t += 0.05
        self.assertGreaterEqual(len(self.log["acts"]), 2)
        self.assertNotIn("walker_actor_ids", self.log["acts"][0])


@unittest.skipIf(pv is None, "needs the carla package (carlaAir env)")
class TestTown05Stations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import carla
        if not (XODR_DIR / "Town05.xodr").exists():
            raise unittest.SkipTest("Town05.xodr not found")
        cls.m = carla.Map("Town05", (XODR_DIR / "Town05.xodr").read_text())
        cls.scene = json.loads((SCENES_DIR / "Town05.json").read_text())
        cls.stations = pv.find_stations(cls.m, cls.scene)

    def test_every_station_is_a_crosswalk_of_the_scene_that_the_lane_spans(self):
        self.assertGreater(len(self.stations), 20)
        zc = [np.mean(np.array(z["polygon"]), axis=0) for z in self.scene["zones"] if z["type"] == "crosswalk"]
        for s in self.stations:
            c = np.array(s.info["centre"])
            self.assertLess(min(np.hypot(*(c - q)) for q in zc), 1.0)
            self.assertGreaterEqual(s.info["approach_m"], 20)

    def test_stations_are_ranked_best_first(self):
        scores = [s.score() for s in self.stations]
        self.assertEqual(scores, sorted(scores))

    def test_f1_acts_hold_over_and_under_the_ten_second_rule(self):
        viol, neg = pv.f1_acts(self.stations[0])
        self.assertTrue(viol["expected"])
        self.assertFalse(neg["expected"])
        hold = lambda a: a["traj"].marks["end"] - a["traj"].marks["start"]  # noqa: E731
        self.assertGreater(hold(viol), 10.0)  # rules.DEFAULTS zebra_crossing min_s
        self.assertLess(hold(neg), 10.0)
        for a in (viol, neg):
            self.assertEqual(a["walkers"], [])

    def test_station_acts_tag_the_station_and_filter_by_condition(self):
        s = self.stations[0]
        only_f1 = pv.station_acts(s, ["F1"])
        self.assertEqual({a["condition"] for a in only_f1}, {"F1"})
        both = pv.station_acts(s, ["F3", "F1"])
        self.assertEqual({a["condition"] for a in both}, {"F3", "F1"})
        self.assertTrue(all(a["station"] == s.id and f"[station {s.id}]" in a["note"] for a in both))
        self.assertEqual(sum(a["expected"] for a in both), 2)  # one violation each; one negative each


if __name__ == "__main__":
    unittest.main()
