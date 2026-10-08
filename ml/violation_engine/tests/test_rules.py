"""L0 unit tests for the violation engine (docs/Violation_Engine_Architecture.md, Section 6).

Synthetic tracks with position noise go through the same smoother (kinematics.py), map
matching (lane_map.py) and monitors (rules.py) as real data, one scenario per PRD case.

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from events import COUNTED_STATUS  # noqa: E402
from kinematics import smooth_track  # noqa: E402
from lane_map import SceneMap  # noqa: E402
from rules import Engine  # noqa: E402

FPS = 27.0
NOISE_M = 0.1

# Straight road along x. Lane A: eastbound, y = 0. Lane C: eastbound, y = -3.5 (right of A).
# Lane B: westbound, y = +3.5 (left of A, opposite direction). Limit 50 km/h.
BASE_LANES = [
    {"id": "A", "centreline": [[-300, 0], [300, 0]], "width_m": 3.5, "speed_limit_kmh": 50,
     "left_line": "solidsolid", "right_line": "broken"},
    {"id": "C", "centreline": [[-300, -3.5], [300, -3.5]], "width_m": 3.5, "speed_limit_kmh": 50,
     "left_line": "broken", "right_line": "solid"},
    {"id": "B", "centreline": [[300, 3.5], [-300, 3.5]], "width_m": 3.5, "speed_limit_kmh": 50,
     "left_line": "solidsolid", "right_line": "solid"},
]


def rect(x0, x1, y0, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def scene(lanes=None, zones=()):
    return SceneMap({"scene": "test", "lanes": lanes or BASE_LANES, "zones": list(zones)})


def track(points, tid=1, cls="car", t0=0.0, seed=0, noise=NOISE_M):
    """points: function t -> (x, y) over duration, given as (fn, duration_s); returns kinematics rows."""
    fn, dur = points
    rng = np.random.default_rng(seed + tid)
    t = t0 + np.arange(0, dur, 1 / FPS)
    xy = np.array([fn(tt - t0) for tt in t])
    x, y = xy[:, 0] + rng.normal(0, noise, len(t)), xy[:, 1] + rng.normal(0, noise, len(t))
    k = smooth_track(t, x, y, np.ones(len(t), bool))
    rows = []
    for i in range(len(t)):
        h = k["heading_deg"][i]
        rows.append({"frame": int(round(t[i] * FPS)), "time_s": round(float(t[i]), 4), "track_id": tid, "class": cls,
                     "conf": 0.9, "visible": 1, "x": k["x"][i], "y": k["y"][i], "vx": k["vx"][i], "vy": k["vy"][i],
                     "speed_kmh": k["speed_kmh"][i], "speed_sigma_kmh": k["speed_sigma_kmh"][i],
                     "heading_deg": "" if math.isnan(h) else h})
    return rows


def piecewise(*segments):
    """segments: (duration_s, fn(local_t) -> (x, y)); returns (fn, total duration)."""
    starts = np.cumsum([0] + [d for d, _ in segments])

    def fn(t):
        for (d, f), s in zip(segments, starts):
            if t < s + d:
                return f(t - s)
        d, f = segments[-1]
        return f(d)
    return fn, float(starts[-1])


def kmh(v):
    return v / 3.6


def run(sc, *rows_lists):
    rows = [r for rl in rows_lists for r in rl]
    return Engine(sc).run(rows)


def counted(events, vtype):
    return [e for e in events if e.type == vtype and e.status in COUNTED_STATUS]


def drive_stop_leave(x_stop, y, stop_s, v=kmh(30), x_start=-40.0):
    """Drive east along y, stop at x_stop for stop_s, drive on."""
    t_in = (x_stop - x_start) / v
    return piecewise((t_in, lambda t: (x_start + v * t, y)),
                     (stop_s, lambda t: (x_stop, y)),
                     (6.0, lambda t: (x_stop + v * t, y)))


class NoParking(unittest.TestCase):
    sc = scene(zones=[{"id": "np", "type": "no_parking", "polygon": rect(50, 70, -1.75, 1.75)}])

    def test_stop_35s_is_flagged(self):
        ev = counted(run(self.sc, track(drive_stop_leave(60, 0, 35))), "no_parking")
        self.assertEqual(len(ev), 1)
        self.assertGreaterEqual(ev[0].value["dwell_s"], 30)

    def test_stop_25s_is_not(self):
        self.assertEqual(counted(run(self.sc, track(drive_stop_leave(60, 0, 25))), "no_parking"), [])

    def test_jitter_still_stopped(self):
        ev = counted(run(self.sc, track(drive_stop_leave(60, 0, 40), noise=0.3)), "no_parking")
        self.assertEqual(len(ev), 1)

    def test_id_switch_keeps_timer(self):
        fn, _ = drive_stop_leave(60, 0, 40)
        t_arrive = 100 / kmh(30)
        first = track((fn, t_arrive + 20), tid=1)  # stopped 20 s, then the ID is lost ...
        second = track((lambda t: fn(t_arrive + 21.5 + t), 30.0), tid=2, t0=t_arrive + 21.5)  # ... back 1.5 s later
        ev = counted(run(self.sc, first, second), "no_parking")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].track_ids, [1, 2])
        self.assertIn("id_switch_bridged", ev[0].tags)

    def test_passing_car_no_event(self):
        fn = (lambda t: (-40 + kmh(30) * t, 0), 20.0)
        self.assertEqual(run(self.sc, track(fn)), [])


class WrongWay(unittest.TestCase):
    sc = scene()

    def test_driving_against_lane(self):
        fn = (lambda t: (100 - kmh(30) * t, 0), 10.0)  # westbound in eastbound lane A
        ev = counted(run(self.sc, track(fn)), "wrong_way")
        self.assertEqual(len(ev), 1)
        self.assertGreaterEqual(ev[0].value["distance_m"], 5)

    def test_reversing_2m_is_not(self):
        v = kmh(6)
        fn = piecewise((3, lambda t: (0, 0)), (2 / v, lambda t: (-v * t, 0)), (3, lambda t: (-2, 0)))
        self.assertEqual(counted(run(self.sc, track(fn)), "wrong_way"), [])

    def test_normal_driving_no_events(self):
        fn = (lambda t: (-100 + kmh(40) * t, 0), 15.0)
        self.assertEqual([e for e in run(self.sc, track(fn)) if e.status in COUNTED_STATUS], [])

    def test_junction_lane_not_checked(self):
        lanes = [dict(BASE_LANES[0], junction=True)]
        fn = (lambda t: (100 - kmh(30) * t, 0), 10.0)
        self.assertEqual(counted(run(scene(lanes), track(fn)), "wrong_way"), [])


class Speeding(unittest.TestCase):
    sc = scene()

    def test_70_in_50(self):
        fn = (lambda t: (-150 + kmh(70) * t, 0), 10.0)
        ev = counted(run(self.sc, track(fn)), "speeding")
        self.assertEqual(len(ev), 1)
        self.assertAlmostEqual(ev[0].value["max_speed_kmh"], 70, delta=3)

    def test_within_tolerance(self):
        fn = (lambda t: (-150 + kmh(54) * t, 0), 10.0)
        self.assertEqual(counted(run(self.sc, track(fn)), "speeding"), [])

    def test_speed_zone_overrides_lane_limit(self):
        sc = scene(zones=[{"id": "school", "type": "speed", "polygon": rect(-200, 200, -2, 2), "limit_kmh": 30}])
        fn = (lambda t: (-150 + kmh(45) * t, 0), 10.0)
        self.assertEqual(len(counted(run(sc, track(fn)), "speeding")), 1)


class UTurn(unittest.TestCase):
    sc = scene(zones=[{"id": "nou", "type": "no_u_turn", "polygon": rect(-20, 20, -2, 6)}])

    def test_u_turn_in_zone(self):
        v, r, cy = kmh(15), 1.75, 1.75  # from lane A (y=0) round to lane B (y=3.5)
        arc = math.pi * r / v
        fn = piecewise((15 / v, lambda t: (-15 + v * t, 0)),
                       (arc, lambda t: (r * math.sin(v * t / r), cy - r * math.cos(v * t / r))),
                       (15 / v, lambda t: (-v * t, 3.5)))
        ev = counted(run(self.sc, track(fn)), "illegal_u_turn")
        self.assertEqual(len(ev), 1)

    def test_u_turn_outside_zone_ignored(self):
        v, r = kmh(15), 1.75
        arc = math.pi * r / v
        fn = piecewise((10 / v, lambda t: (90 + v * t, 0)),
                       (arc, lambda t: (100 + r * math.sin(v * t / r), r - r * math.cos(v * t / r))),
                       (10 / v, lambda t: (100 - v * t, 3.5)))
        self.assertEqual(counted(run(self.sc, track(fn)), "illegal_u_turn"), [])

    def test_three_point_turn_not_flagged(self):
        v = kmh(8)
        fn = piecewise((10 / v, lambda t: (-10 + v * t, 0)),          # east
                       (4 / v, lambda t: (0, v * t)),                  # north (forward)
                       (1.5, lambda t: (0, 4)),                        # stop
                       (3 / v, lambda t: (v * t * 0.7, 4 - v * t * 0.7)),  # reverse (moves south-east)
                       (1.5, lambda t: (2.1, 1.9)),                    # stop
                       (10 / v, lambda t: (2.1 - v * t, 1.9 + min(v * t, 1.6))))  # west
        self.assertEqual(counted(run(self.sc, track(fn)), "illegal_u_turn"), [])


class LaneViolation(unittest.TestCase):
    sc = scene()

    def test_straddling_5s(self):
        fn = (lambda t: (-50 + kmh(40) * t, -1.6), 6.0)  # centre 0.15 m from the A/C line
        ev = counted(run(self.sc, track(fn)), "lane_violation")
        self.assertEqual(len(ev), 1)
        self.assertIn("straddling", ev[0].tags)

    def test_lane_change_across_broken_line_ok(self):
        v = kmh(40)
        fn = piecewise((4, lambda t: (-80 + v * t, 0)),
                       (3, lambda t: (-80 + v * (4 + t), -3.5 * t / 3)),
                       (4, lambda t: (-80 + v * (7 + t), -3.5)))
        self.assertEqual(counted(run(self.sc, track(fn)), "lane_violation"), [])

    def test_lane_change_across_solid_line(self):
        lanes = [dict(BASE_LANES[0], right_line="solid"), dict(BASE_LANES[1], left_line="solid"), BASE_LANES[2]]
        v = kmh(40)
        fn = piecewise((4, lambda t: (-80 + v * t, 0)),
                       (3, lambda t: (-80 + v * (4 + t), -3.5 * t / 3)),
                       (4, lambda t: (-80 + v * (7 + t), -3.5)))
        ev = counted(run(scene(lanes), track(fn)), "lane_violation")
        self.assertEqual(len(ev), 1)
        self.assertIn("solid_line_crossing", ev[0].tags)


class Zebra(unittest.TestCase):
    sc = scene(zones=[{"id": "z", "type": "crosswalk", "polygon": rect(100, 104, -1.75, 1.75)}])

    def test_stopped_on_crossing(self):
        ev = counted(run(self.sc, track(drive_stop_leave(102, 0, 15))), "zebra_crossing")
        self.assertEqual(len(ev), 1)

    def test_queue_is_suppressed(self):
        car = track(drive_stop_leave(102, 0, 15), tid=1)
        ahead = track(drive_stop_leave(108, 0, 18, x_start=-30), tid=2)
        evs = [e for e in run(self.sc, car, ahead) if e.type == "zebra_crossing"]
        self.assertEqual([e for e in evs if e.status in COUNTED_STATUS], [])
        self.assertTrue(any("queue" in e.tags for e in evs))


class HighwayStop(unittest.TestCase):
    zones = [{"id": "hw", "type": "highway", "polygon": rect(-300, 300, -10, 10)}]

    def test_stop_in_lane(self):
        ev = counted(run(scene(zones=self.zones), track(drive_stop_leave(60, 0, 25))), "highway_stop")
        self.assertEqual(len(ev), 1)

    def test_shoulder_is_possible_breakdown(self):
        lanes = BASE_LANES + [{"id": "S", "centreline": [[-300, -6.5], [300, -6.5]], "width_m": 2.5,
                               "lane_type": "shoulder", "speed_limit_kmh": 50}]
        evs = [e for e in run(scene(lanes, self.zones), track(drive_stop_leave(60, -6.5, 25))) if e.type == "highway_stop"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].status, "possible_breakdown")

    def test_short_stop_not_flagged(self):
        self.assertEqual(counted(run(scene(zones=self.zones), track(drive_stop_leave(60, 0, 12))), "highway_stop"), [])


if __name__ == "__main__":
    unittest.main()
