"""Stager line-of-sight check (simulation/violation_scenarios/occlusion.py, Build Plan M2): road
points a structure hides from the drone camera. Spot 3 of the 2026-10-10 session sat under Town03's
elevated railway and scored 0/12.

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "simulation" / "violation_scenarios"))
from occlusion import Occluder, rotation_matrix  # noqa: E402

LABELS = ["RailTrack", "Buildings", "Bridge", "Poles", "TrafficLight", "Vegetation"]
CAM = (0.0, 0.0, 67.6)  # camera 67.6 m over the origin, as at a staging spot


def obj(label, c, e, yaw=0.0, pitch=0.0, roll=0.0):
    return {"l": LABELS.index(label), "c": list(c), "e": list(e), "r": [pitch, yaw, roll]}


class TestOcclusion(unittest.TestCase):
    def test_deck_hides_the_road_under_it_only(self):
        # a railway deck 10-13 m up, 20 m wide, running along y at x = 20
        occ = Occluder([obj("RailTrack", (20, 0, 11.5), (10, 50, 1.5))], LABELS)
        h = occ.hidden([(20, 0, 0), (20, 30, 0), (-20, 0, 0), (0, 0, 0)], CAM)
        self.assertEqual(h.tolist(), [True, True, False, False])

    def test_parallax_behind_a_tall_building(self):
        # 30 m tall building between a street at x = 50 and the camera; the same street on the
        # camera's side of the building is visible
        occ = Occluder([obj("Buildings", (40, 0, 15), (4, 20, 15))], LABELS)
        self.assertTrue(occ.hidden([(50, 0, 0)], CAM)[0])
        self.assertFalse(occ.hidden([(30, 0, 0)], CAM)[0])
        self.assertFalse(occ.hidden([(50, 0, 0)], (60, 0, 67.6))[0])  # camera over the street

    def test_car_on_a_bridge_deck_is_seen_inside_a_building_is_not(self):
        deck = obj("Bridge", (0, 0, 9.0), (5, 40, 2.0))  # box 7-11 m: a car on the 9 m deck has its roof inside
        self.assertFalse(Occluder([deck], LABELS).hidden([(0, 10, 9.0)], CAM)[0])
        self.assertTrue(Occluder([deck], LABELS).hidden([(0, 10, 0.0)], CAM)[0])  # road under the bridge
        roof = obj("Buildings", (0, 0, 9.0), (5, 40, 2.0))
        self.assertTrue(Occluder([roof], LABELS).hidden([(0, 10, 9.0)], CAM)[0])

    def test_yaw_rotates_the_box(self):
        # extents (along x 30, along y 3): yaw 90 turns it to run along y
        occ0 = Occluder([obj("RailTrack", (0, 20, 12), (30, 3, 1))], LABELS)
        occ90 = Occluder([obj("RailTrack", (0, 20, 12), (30, 3, 1), yaw=90)], LABELS)
        p = [(20, 20, 0)]  # 20 m along x from the box centre
        self.assertTrue(occ0.hidden(p, (20, 20, 67.6))[0])
        self.assertFalse(occ90.hidden(p, (20, 20, 67.6))[0])
        self.assertTrue(occ90.hidden([(0, 40, 0)], (0, 40, 67.6))[0])

    def test_rotation_matches_carla_vectors(self):
        np.testing.assert_allclose(rotation_matrix(0, 90, 0)[:, 0], [0, 1, 0], atol=1e-12)  # forward
        np.testing.assert_allclose(rotation_matrix(0, 90, 0)[:, 1], [-1, 0, 0], atol=1e-12)  # right (left-handed)
        np.testing.assert_allclose(rotation_matrix(90, 0, 0)[:, 0], [0, 0, 1], atol=1e-12)  # pitch up

    def test_negative_extents_are_sizes(self):
        occ = Occluder([obj("RailTrack", (0, 0, 12), (-10, -10, -1))], LABELS)
        self.assertTrue(occ.hidden([(5, 5, 0)], CAM)[0])

    def test_thin_and_oversized_objects_ignored(self):
        objs = [obj("Poles", (0, 0, 4), (0.2, 0.2, 4)),
                obj("TrafficLight", (0, 0, 4), (6, 0.5, 4)),  # mast arm over the lanes: mostly air
                obj("RailTrack", (0, 0, 12), (48, 71, 1.4))]  # curved rail corner: box spans the inside
        self.assertFalse(Occluder(objs, LABELS).hidden([(0, 0, 0)], CAM)[0])

    def test_vegetation_off_by_default_and_ellipsoid_when_on(self):
        tree = [obj("Vegetation", (0, 0, 12), (7, 7, 12))]
        self.assertFalse(Occluder(tree, LABELS).hidden([(0, 0, 0)], CAM)[0])
        on = Occluder(tree, LABELS, vegetation=True)
        self.assertTrue(on.hidden([(0, 0, 0)], CAM)[0])
        # a ray through the box's corner region but outside the inscribed ellipsoid
        self.assertFalse(on.hidden([(6.5, 6.5, 22.0)], (6.5, 6.5, 67.6))[0])

    def test_near_keeps_only_objects_that_can_matter(self):
        occ = Occluder([obj("RailTrack", (20, 0, 11.5), (10, 50, 1.5)),
                        obj("Buildings", (500, 500, 10), (5, 5, 10))], LABELS)
        sub = occ.near((-50, -50), (50, 50))
        self.assertEqual(sub.labels, ["RailTrack"])
        self.assertTrue(sub.hidden([(20, 0, 0)], CAM)[0])

    def test_session_spot_3_is_under_the_town03_railway(self):
        occ = Occluder.for_town("Town03")
        if occ is None:
            self.skipTest("Town03_objects.json not exported")
        # lane points of the spot-3 acts (session 2026-10-10, flight 20261010_124843): road under
        # the elevated railway at x ~ -146; spot 1's no-parking stop (-9.7, 95.6) is clear
        cam = (-130.1, -38.8, 67.6)
        self.assertTrue(occ.hidden([(-146.4, -40.0, 0.0)], cam)[0])
        self.assertFalse(occ.hidden([(-9.7, 95.6, 0.0)], (-30.1, 111.2, 67.6))[0])


if __name__ == "__main__":
    unittest.main()
