"""Build Plan M3: zebra-crossing conditions with pedestrians (F2 blocking, F3 failure to yield,
F5 obstructing; zebra_pedestrians.py). Synthetic vehicle and pedestrian tracks through the same
smoother, map matching and engine as real data, one positive and negatives per condition.

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from events import COUNTED_STATUS  # noqa: E402
from lane_map import SceneMap  # noqa: E402
from rules import Engine  # noqa: E402
from test_rules import BASE_LANES, drive_stop_leave, kmh, piecewise, rect, track  # noqa: E402
from zebra_pedestrians import Crossing  # noqa: E402

WALK = 1.4  # m/s
PED = 1001  # pedestrian track ids are offset from vehicles' (run_violations.PEOPLE_ID_OFFSET)

# the road of test_rules (lanes C, A, B at y = -3.5, 0, 3.5; edges at -5.25 / 5.25) with a 4 m
# crossing over all three lanes at x = 100..104: pedestrians walk along y
CROSSING = {"id": "zc", "type": "crosswalk", "polygon": rect(100, 104, -5.25, 5.25)}
SCENE = SceneMap({"scene": "test", "lanes": BASE_LANES, "zones": [CROSSING]})


def walk(y0, y1, t_start=0.0, x=102.0, hold_at_end=0.0, dur=None):
    """A pedestrian standing at y0 until t_start, walking to y1 at WALK, then standing."""
    d = abs(y1 - y0)
    sgn = 1 if y1 > y0 else -1
    segs = [(max(t_start, 1e-3), lambda t: (x, y0)), (d / WALK, lambda t: (x, y0 + sgn * WALK * t))]
    if hold_at_end:
        segs.append((hold_at_end, lambda t: (x, y1)))
    fn, total = piecewise(*segs)
    return fn, dur or total


def stand(y, dur, x=102.0):
    return (lambda t: (x, y)), dur


def ped(points, tid=PED, **kw):
    return track(points, tid=tid, cls="pedestrian", noise=0.15, **kw)


def zebra(*rows_lists):
    ev = Engine(SCENE).run([r for rl in rows_lists for r in rl])
    return sorted(e.condition for e in ev if e.type == "zebra_crossing" and e.status in COUNTED_STATUS), ev


class CrossingGeometry(unittest.TestCase):
    def test_axis_is_across_the_lane(self):
        cr = Crossing(SCENE.zones[0], SCENE)
        self.assertAlmostEqual(abs(cr.axis[1]), 1.0, places=6)  # walking axis along y, across the road
        self.assertEqual(cr.waiting_end(102, -6.5), -1)
        self.assertEqual(cr.waiting_end(102, 6.5), 1)
        self.assertEqual(cr.waiting_end(102, -9.0), 0)
        self.assertTrue(cr.on(102, 5.5))  # within the on-margin


class FailureToYield(unittest.TestCase):
    def test_f3_drives_through_while_pedestrian_is_on_the_crossing(self):
        # the pedestrian is at y = -2 (lane C, walking towards lane A) when the car's centre reaches x = 102
        t_meet = (-2 + 8) / WALK
        car = track((lambda t: (102 - kmh(30) * t_meet + kmh(30) * t, 0.0), 9.0))
        got, ev = zebra(car, ped(walk(-8, 8)))
        self.assertEqual(got, ["F3"])
        f3 = next(e for e in ev if e.condition == "F3")
        self.assertEqual(f3.track_ids, [1])
        self.assertEqual(f3.value["pedestrian_track_ids"], [PED])
        self.assertEqual(f3.value["pedestrian_state"], "on")

    def test_f3_pedestrian_stepping_off_the_kerb_towards_the_car(self):
        # standing at the far kerb, starts walking 0.5 s before the car arrives: entering, close to its lane
        t_meet = 6.0
        car = track((lambda t: (102 - kmh(30) * t_meet + kmh(30) * t, 0.0), 10.0))
        got, ev = zebra(car, ped(walk(-6.3, 6, t_start=t_meet - 0.6)))
        self.assertEqual(got, ["F3"])

    def test_yielding_car_is_fine(self):
        # stops before the crossing (front at 97.3 m) until the pedestrian has crossed its lane, then goes
        car = track(drive_stop_leave(95, 0, 7.0, x_start=70))
        got, _ = zebra(car, ped(walk(-8, 8)))
        self.assertEqual(got, [])

    def test_pedestrian_waiting_at_the_kerb_has_not_entered(self):
        car = track((lambda t: (60 + kmh(30) * t, 0.0), 12.0))
        got, _ = zebra(car, ped(stand(-6.5, 12.0)))
        self.assertEqual(got, [])

    def test_pedestrian_on_the_far_side_walking_away(self):
        # the pedestrian is in lane B, 3.5 m and more from the car's side and walking away: clear
        t_meet = (4.5 + 8) / WALK
        car = track((lambda t: (102 - kmh(30) * t_meet + kmh(30) * t, 0.0), 12.0))
        got, _ = zebra(car, ped(walk(-8, 8)))
        self.assertEqual(got, [])


class Blocking(unittest.TestCase):
    def test_f2_stopped_on_the_crossing_while_a_pedestrian_waits(self):
        car = track(drive_stop_leave(102, 0, 6.0, x_start=80))
        got, ev = zebra(car, ped(stand(-6.5, 16.0)))
        self.assertEqual(got, ["F2"])  # 6 s: under F1's 10 s
        f2 = next(e for e in ev if e.condition == "F2")
        self.assertGreaterEqual(f2.value["blocking_s"], 2.0)

    def test_f1_and_f2_on_a_long_stop_with_a_pedestrian(self):
        car = track(drive_stop_leave(102, 0, 14.0, x_start=80))
        got, _ = zebra(car, ped(stand(-6.5, 24.0)))
        self.assertEqual(got, ["F1", "F2"])

    def test_no_pedestrian_no_f2(self):
        got, _ = zebra(track(drive_stop_leave(102, 0, 6.0, x_start=80)))
        self.assertEqual(got, [])

    def test_stopped_before_the_crossing_is_fine(self):
        car = track(drive_stop_leave(96.5, 0, 8.0, x_start=80))  # front bumper at 98.8 m, 1.2 m short
        got, _ = zebra(car, ped(stand(-6.5, 16.0)))
        self.assertEqual(got, [])

    def test_pedestrian_far_from_the_crossing_does_not_count(self):
        car = track(drive_stop_leave(102, 0, 6.0, x_start=80))
        got, _ = zebra(car, ped(stand(-12.0, 16.0)))
        self.assertEqual(got, [])


class Obstructing(unittest.TestCase):
    def test_f5_pedestrian_walks_up_to_the_stopped_car(self):
        # the car stands on the crossing from t = 2.6 s to 12.1 s; the pedestrian sets off at t = 3 s,
        # reaches 0.6 m from its side (y = -1.5), waits there 4 s and is gone (round the car) before it
        # drives off (a pedestrian still beside it then would make the drive-off an F3 as well)
        car = track(drive_stop_leave(102, 0, 9.5, x_start=80))
        got, ev = zebra(car, ped(walk(-7, -1.5, t_start=3.0, hold_at_end=4.0)))
        self.assertEqual(got, ["F5"])
        f5 = next(e for e in ev if e.condition == "F5")
        self.assertIn("blocking", f5.tags)
        self.assertEqual(f5.value["obstructed_track_ids"], [PED])

    def test_pedestrian_on_the_crossing_far_from_the_car_is_only_f2(self):
        car = track(drive_stop_leave(102, 0, 6.0, x_start=80))
        got, _ = zebra(car, ped(stand(4.8, 16.0)))  # on the crossing, at the far edge of lane B
        self.assertEqual(got, ["F2"])


class PeopleStayOutOfVehicleRules(unittest.TestCase):
    def test_jogger_against_the_lane_is_not_wrong_way(self):
        jogger = ped((lambda t: (60 - kmh(9) * t, 0.0), 15.0))
        ev = Engine(SCENE).run(jogger)
        self.assertEqual([e for e in ev if e.status in COUNTED_STATUS], [])


if __name__ == "__main__":
    unittest.main()
