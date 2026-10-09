"""Build Plan M2: the vehicle conditions that read road features (A1, A3, A5, A6, A8, B1/B2/B4/B5
from lane properties, C2-C5, D2-D4, E4). One positive and one negative scenario per condition,
synthetic tracks through the same smoother, map matching and monitors as real data.

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from events import COUNTED_STATUS  # noqa: E402
from lane_map import SceneMap  # noqa: E402
from rules import Engine  # noqa: E402
from test_rules import BASE_LANES, drive_stop_leave, kmh, piecewise, rect, track  # noqa: E402


def sc(lanes, zones=()):
    return SceneMap({"scene": "test", "lanes": lanes, "zones": list(zones)})


def run(scene, *tracks, params=None):
    return Engine(scene, params).run([r for t in tracks for r in t])


def conds(events, vtype=None):
    return sorted(e.condition for e in events if e.status in COUNTED_STATUS and (vtype is None or e.type == vtype))


def lane_change(y0, y1, v=kmh(40), x0=-80.0):
    return piecewise((4, lambda t: (x0 + v * t, y0)),
                     (3, lambda t: (x0 + v * (4 + t), y0 + (y1 - y0) * t / 3)),
                     (4, lambda t: (x0 + v * (7 + t), y1)))


def straight(x0, y, v, dur):
    return (lambda t: (x0 + v * t, y), dur)


class LaneConditions(unittest.TestCase):
    def test_a3_lane_change_where_not_permitted(self):
        lanes = [dict(BASE_LANES[0], lane_change="left"), BASE_LANES[1], BASE_LANES[2]]  # A: may only go left
        self.assertEqual(conds(run(sc(lanes), track(lane_change(0, -3.5))), "lane_violation"), ["A3"])

    def test_a3_permitted_change_is_fine(self):
        lanes = [dict(BASE_LANES[0], lane_change="both"), BASE_LANES[1], BASE_LANES[2]]
        self.assertEqual(conds(run(sc(lanes), track(lane_change(0, -3.5))), "lane_violation"), [])

    def test_a8_cut_in_ahead_of_a_faster_car(self):
        # the changer's centre enters lane C at t = 5.5 s, x = -18.9; a 60 km/h car is 10 m behind it there
        lag = track(straight(-18.9 - 10 - kmh(60) * 5.5, -3.5, kmh(60), 9.0), tid=2)
        ev = [e for e in run(sc(BASE_LANES), track(lane_change(0, -3.5)), lag) if e.type == "lane_violation"]
        a8 = [e for e in ev if e.condition == "A8"]
        self.assertEqual(len(a8), 1)
        self.assertEqual((a8[0].track_ids, a8[0].value["partner_track"], a8[0].value["partner_role"]), ([1], 2, "lag"))
        self.assertLess(a8[0].value["min_ttc_s"], 2.0)

    def test_a2_over_the_double_solid_into_the_opposing_lane(self):
        ev = run(sc(BASE_LANES), track(lane_change(0, 3.5, v=kmh(20), x0=-40)))
        self.assertEqual(conds(ev, "lane_violation"), ["A2"])

    def test_a2_out_and_back_counts_both_crossings(self):
        # the staged act on flight 20261009_201727: an overtake over the double solid centre line and back
        v = kmh(20)
        fn = piecewise((4, lambda t: (-60 + v * t, 0)), (3, lambda t: (-60 + v * (4 + t), 3.5 * t / 3)),
                       (5, lambda t: (-60 + v * (7 + t), 3.5)), (3, lambda t: (-60 + v * (12 + t), 3.5 - 3.5 * t / 3)),
                       (4, lambda t: (-60 + v * (15 + t), 0)))
        ev = run(sc(BASE_LANES), track(fn))
        self.assertEqual(conds(ev, "lane_violation"), ["A2", "A2"])

    def test_a8_alongside_in_the_next_lane_is_not_a_conflict(self):
        # a car level with the changer but staying centred in lane C: the changer only reaches the line
        changer = track(piecewise((4, lambda t: (-80 + kmh(40) * t, 0)),
                                  (3, lambda t: (-80 + kmh(40) * (4 + t), -2.0 * t / 3)),
                                  (4, lambda t: (-80 + kmh(40) * (7 + t), -2.0))))
        beside = track(straight(-80, -5.0, kmh(40), 11.0), tid=2)
        ev = [e for e in run(sc(BASE_LANES), changer, beside) if e.condition == "A8"]
        self.assertEqual(ev, [])

    def test_a8_safe_gap(self):
        lag = track(straight(-18.9 - 30 - kmh(40) * 5.5, -3.5, kmh(40), 9.0), tid=2)  # 30 m behind, same speed
        self.assertEqual(conds(run(sc(BASE_LANES), track(lane_change(0, -3.5)), lag), "lane_violation"), [])

    def test_a5_car_in_bus_lane(self):
        lanes = [BASE_LANES[0], dict(BASE_LANES[1], restricted="bus"), BASE_LANES[2]]
        self.assertEqual(conds(run(sc(lanes), track(straight(-100, -3.5, kmh(40), 5.0))), "lane_violation"), ["A5"])

    def test_a5_bus_in_bus_lane_is_fine(self):
        lanes = [BASE_LANES[0], dict(BASE_LANES[1], restricted="bus"), BASE_LANES[2]]
        self.assertEqual(conds(run(sc(lanes), track(straight(-100, -3.5, kmh(40), 5.0), cls="bus")), "lane_violation"), [])

    SHOULDER = {"id": "S", "centreline": [[-300, -6.5], [300, -6.5]], "width_m": 2.5, "lane_type": "shoulder",
                "speed_limit_kmh": 50}

    def test_a6_driving_on_the_shoulder(self):
        ev = run(sc(BASE_LANES + [self.SHOULDER]), track(straight(-100, -6.5, kmh(40), 6.0)))
        self.assertEqual(conds(ev, "lane_violation"), ["A6"])

    def test_a6_stopped_on_the_shoulder_is_not_driving(self):
        ev = run(sc(BASE_LANES + [self.SHOULDER]), track(drive_stop_leave(0, -6.5, 10, v=kmh(8), x_start=-6)))
        self.assertEqual(conds(ev, "lane_violation"), [])


class WrongLaneForDirection(unittest.TestCase):
    """Two approach lanes into a junction (x 0..20): A (y 0) leads straight on to SA and SB;
    B (y -3.5) is a right-turn-only lane, its junction lane JB leads to RB (southwards)."""

    LANES = [
        {"id": "A", "centreline": [[-300, 0], [0, 0]], "width_m": 3.5, "next": ["JA"]},
        {"id": "B", "centreline": [[-300, -3.5], [0, -3.5]], "width_m": 3.5, "next": ["JB"]},
        {"id": "JA", "centreline": [[0, 0], [20, 0]], "width_m": 3.5, "junction": True, "next": ["SA", "SB"]},
        {"id": "JB", "centreline": [[0, -3.5], [8, -8], [10, -20]], "width_m": 3.5, "junction": True, "next": ["RB"]},
        {"id": "SA", "centreline": [[20, 0], [300, 0]], "width_m": 3.5, "next": []},
        {"id": "SB", "centreline": [[20, -3.5], [300, -3.5]], "width_m": 3.5, "next": []},
        {"id": "RB", "centreline": [[10, -20], [10, -300]], "width_m": 3.5, "next": []},
    ]

    def test_a1_straight_on_from_the_turn_lane(self):
        ev = run(sc(self.LANES), track(straight(-80, -3.5, kmh(30), 14.0)))
        self.assertEqual(conds(ev, "lane_violation"), ["A1"])

    def test_a1_straight_on_from_the_through_lane(self):
        ev = run(sc(self.LANES), track(piecewise((10, lambda t: (-80 + kmh(30) * t, 0)),
                                                 (6, lambda t: (3.3 + kmh(30) * t, -3.5 * min(t / 2, 1))))))
        self.assertEqual(conds(ev, "lane_violation"), [])


class StoppingFromLaneProperties(unittest.TestCase):
    def stop(self, **lane_kw):
        lanes = [dict(BASE_LANES[0], **lane_kw), BASE_LANES[1], BASE_LANES[2]]
        return conds(run(sc(lanes), track(drive_stop_leave(60, 0, 25))), "highway_stop")

    def test_b1_highway_lane_without_a_drawn_zone(self):
        self.assertEqual(self.stop(road_class="highway", road_id="1"), ["B1"])

    def test_b4_ramp(self):
        self.assertEqual(self.stop(ramp="on", road_id="2"), ["B4"])

    def test_b5_bridge(self):
        self.assertEqual(self.stop(bridge=True, road_id="3"), ["B5"])

    def test_urban_lane_is_not_a_highway_stop(self):
        self.assertEqual(self.stop(road_id="4"), [])


class WrongWayKinds(unittest.TestCase):
    AGAINST = (lambda t: (100 - kmh(30) * t, 0), 10.0)  # westbound in eastbound lane A

    def kind(self, lanes):
        return conds(run(sc(lanes), track(self.AGAINST)), "wrong_way")

    def test_c1_plain(self):
        self.assertEqual(self.kind(BASE_LANES), ["C1"])

    def test_c3_highway(self):
        self.assertEqual(self.kind([dict(BASE_LANES[0], road_class="highway")] + BASE_LANES[1:]), ["C3"])

    def test_c4_ramp(self):
        self.assertEqual(self.kind([dict(BASE_LANES[0], ramp="off")] + BASE_LANES[1:]), ["C4"])

    def test_c5_one_way(self):
        self.assertEqual(self.kind([dict(BASE_LANES[0], one_way=True)] + BASE_LANES[1:]), ["C5"])

    def test_c2_entry_from_a_junction(self):
        lanes = [dict(BASE_LANES[0], centreline=[[-300, 0], [100, 0]], road_id="1"),
                 {"id": "J", "centreline": [[100, 0], [130, 0]], "width_m": 3.5, "junction": True, "road_id": "9"}]
        fn = (lambda t: (140 - kmh(30) * t, 0), 12.0)  # comes out of the junction into A, against it
        self.assertEqual(conds(run(sc(lanes), track(fn)), "wrong_way"), ["C2"])


def u_turn(y_from, y_to, x_turn, r, v=kmh(15)):
    """Drive east along y_from to x_turn, half circle to the left (+y, standard axes) of radius r
    centred on (x_turn, (y_from+y_to)/2), back west along y_to."""
    cy = (y_from + y_to) / 2
    arc = math.pi * r / v
    return piecewise((40 / v, lambda t: (x_turn - 40 + v * t, y_from)),
                     (arc, lambda t: (x_turn + r * math.sin(v * t / r), cy - r * math.cos(v * t / r))),
                     (40 / v, lambda t: (x_turn - v * t, y_to)))


class UTurnKinds(unittest.TestCase):
    def test_d4_through_the_median(self):
        lanes = [{"id": "E", "centreline": [[-300, 0], [300, 0]], "width_m": 3.5, "road_id": "1", "median_left": True,
                  "left_line": "solid"},
                 {"id": "W", "centreline": [[300, 8], [-300, 8]], "width_m": 3.5, "road_id": "1", "median_left": True,
                  "left_line": "solid"}]
        ev = run(sc(lanes), track(u_turn(0, 8, 50, 4)))
        self.assertEqual(conds(ev, "illegal_u_turn"), ["D4"])
        self.assertEqual(conds(ev, "lane_violation"), [])  # a U-turn, not also a solid-line crossing (A2)

    def test_legal_u_turn_across_a_broken_centre_line(self):
        lanes = [dict(BASE_LANES[0], left_line="broken", road_id="1"), dict(BASE_LANES[2], left_line="broken", road_id="1")]
        self.assertEqual(conds(run(sc(lanes), track(u_turn(0, 3.5, 50, 1.75))), "illegal_u_turn"), [])

    def test_d2_across_the_double_solid_line(self):
        lanes = [dict(BASE_LANES[0], road_id="1"), dict(BASE_LANES[2], road_id="1")]
        self.assertEqual(conds(run(sc(lanes), track(u_turn(0, 3.5, 50, 1.75))), "illegal_u_turn"), ["D2"])

    JUNCTION = [{"id": "E", "centreline": [[-300, 0], [100, 0]], "width_m": 3.5, "road_id": "1", "left_line": "broken"},
                {"id": "W", "centreline": [[100, 8], [-300, 8]], "width_m": 3.5, "road_id": "1", "left_line": "broken"},
                {"id": "J1", "centreline": [[100, 4], [140, 4]], "width_m": 14.0, "junction": True, "road_id": "50"}]

    def junction_turn(self):
        v = kmh(15)
        return track(piecewise((40 / v, lambda t: (60 + v * t, 0)), (8 / v, lambda t: (100 + v * t, 0)),
                               (math.pi * 4 / v, lambda t: (108 + 4 * math.sin(v * t / 4), 4 - 4 * math.cos(v * t / 4))),
                               (48 / v, lambda t: (108 - v * t, 8))))

    def test_d3_at_a_prohibited_junction(self):
        ev = run(sc(self.JUNCTION), self.junction_turn(), params={"illegal_u_turn": {"no_u_turn_junctions": ["J*"]}})
        self.assertEqual(conds(ev, "illegal_u_turn"), ["D3"])

    def test_junction_u_turn_allowed_by_default(self):
        self.assertEqual(conds(run(sc(self.JUNCTION), self.junction_turn()), "illegal_u_turn"), [])

    def test_junction_u_turn_is_not_also_a_wrong_lane(self):
        lanes = [dict(self.JUNCTION[0], next=["J1"]), dict(self.JUNCTION[1], next=[]), dict(self.JUNCTION[2], next=["E"])]
        ev = run(sc(lanes), self.junction_turn(), params={"illegal_u_turn": {"no_u_turn_junctions": ["J*"]}})
        self.assertEqual(conds(ev), ["D3"])  # turned round: the U-turn rule's case, not A1 too


class QueueAcrossLanePieces(unittest.TestCase):
    def test_queue_on_the_next_piece_suppresses_a_zebra_stop(self):
        # the crossing sits at the end of piece P1; the car ahead waits on P2, just past it
        lanes = [{"id": "P1", "centreline": [[-300, 0], [104, 0]], "width_m": 3.5, "next": ["P2"]},
                 {"id": "P2", "centreline": [[104, 0], [300, 0]], "width_m": 3.5, "next": []}]
        zones = [{"id": "z", "type": "crosswalk", "polygon": rect(98, 103, -1.75, 1.75)}]
        car = track(drive_stop_leave(101, 0, 15), tid=1)
        ahead = track(drive_stop_leave(108, 0, 18, x_start=-30), tid=2)
        self.assertEqual(conds(run(sc(lanes, zones), car, ahead), "zebra_crossing"), [])


class SpeedLimitOfTheDrivingLane(unittest.TestCase):
    # A eastbound 90 km/h, B westbound 30 km/h, centre line at y = 1.75
    LANES = [dict(BASE_LANES[0], speed_limit_kmh=90), BASE_LANES[1], dict(BASE_LANES[2], speed_limit_kmh=30)]

    def test_on_the_centre_line_the_oncoming_limit_does_not_apply(self):
        # eastbound at 70, centre 0.2 m over the line: physically matched to B (Town05 ring road 37)
        ev = run(sc(self.LANES), track(straight(-200, 1.95, kmh(70), 8.0), noise=0.05))
        self.assertEqual(conds(ev, "speeding"), [])

    def test_a_new_track_must_settle_first(self):
        # 70 in a 30 lane, but in view only 0.9 s: its speed rests on too few frames (min_track_age_s)
        ev = run(sc(self.LANES), track((lambda t: (200 - kmh(70) * t, 3.5), 0.9)))
        self.assertEqual(conds(ev, "speeding"), [])

    def test_in_its_own_30_lane_it_is_speeding(self):
        ev = run(sc(self.LANES), track((lambda t: (200 - kmh(70) * t, 3.5), 8.0)))
        self.assertEqual(conds(ev, "speeding"), ["E1"])


class UnderAFlyover(unittest.TestCase):
    # a flyover (30 km/h, 11 m up) along x crosses over a ground road (90 km/h) along y at the origin
    LANES = [{"id": "fly", "centreline": [[-200, 0.4], [200, 0.4]], "z": [11.0, 11.0], "width_m": 3.5, "speed_limit_kmh": 30},
             {"id": "ground", "centreline": [[0, -200], [0, 200]], "z": [0.0, 0.0], "width_m": 3.5, "speed_limit_kmh": 90}]

    def test_match_by_level(self):
        sm = sc(self.LANES)
        # 1.5 m off the ground road's centreline, 0.1 m off the deck's: plan view alone picks the deck
        self.assertEqual(sm.match(1.5, 0.3).lane.id, "fly")
        self.assertEqual(sm.match(1.5, 0.3, z=0.0).lane.id, "ground")
        self.assertEqual(sm.match(1.5, 0.3, z=11.0).lane.id, "fly")
        self.assertEqual(sm.match(0.0, 50.0, z=11.0).lane.id, "ground")  # no lane at that level: nearest

    def test_car_under_the_flyover_keeps_its_road(self):
        ev = run(sc(self.LANES), track((lambda t: (0.0, -120 + kmh(70) * t), 7.0)))
        self.assertEqual(conds(ev, "speeding"), [])

    def test_car_on_the_flyover_is_speeding(self):
        ev = run(sc(self.LANES), track((lambda t: (-120 + kmh(70) * t, 0.4), 7.0)))
        self.assertEqual(conds(ev, "speeding"), ["E1"])


class SpeedByClass(unittest.TestCase):
    LANES = [dict(BASE_LANES[0], speed_limit_kmh=90), BASE_LANES[1], BASE_LANES[2]]
    PARAMS = {"speeding": {"class_limits_kmh": {"truck": 60}}}

    def test_e4_truck_over_its_class_limit(self):
        ev = run(sc(self.LANES), track(straight(-200, 0, kmh(75), 10.0), cls="truck"), params=self.PARAMS)
        self.assertEqual(conds(ev, "speeding"), ["E4"])
        self.assertEqual([e.value["limit_kmh"] for e in ev if e.type == "speeding"], [60.0])

    def test_e4_car_at_the_same_speed_is_fine(self):
        ev = run(sc(self.LANES), track(straight(-200, 0, kmh(75), 10.0), cls="car"), params=self.PARAMS)
        self.assertEqual(conds(ev, "speeding"), [])


if __name__ == "__main__":
    unittest.main()
