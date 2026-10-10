"""Road-surface ground projection (ground_coords.RoadSurface, Build Plan M2).

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ground_coords import BOX_CENTRE_Z, FlightCamera, RoadSurface, ue_matrix  # noqa: E402

ALT = 67.6  # the staged flight's altitude
# a flyover 10 m up along y = 20, and a street at ground level along y = 23.5 and y = 30 (along x).
# On the flat plane a car on the flyover lands ~3.5 m out, in the y = 23.5 street lane. A car on the
# y = 30 street is visible past the deck's edge (its ray is at y 25.5 at deck height)
SCENE = {"lanes": [
    {"id": "fly", "centreline": [[-100, 20], [100, 20]], "z": [10.0, 10.0], "width_m": 3.5},
    {"id": "street", "centreline": [[-100, 23.5], [100, 23.5]], "z": [0.0, 0.0], "width_m": 3.5},
    {"id": "street2", "centreline": [[-100, 30], [100, 30]], "z": [0.0, 0.0], "width_m": 3.5},
    {"id": "under", "centreline": [[0, -100], [0, 100]], "z": [0.0, 0.0], "width_m": 3.5},  # passes under the flyover
]}


def nadir_camera() -> FlightCamera:
    cam = FlightCamera.__new__(FlightCamera)
    cam.W, cam.H = 1920, 1080
    cam.f = cam.W / (2 * math.tan(math.radians(90.0) / 2))
    m = ue_matrix((0.0, 0.0, ALT), (-90.0, 0.0, 0.0))
    cam.frame_pose = {0: (m[:3, 3], m[:3, :3])}
    cam.ground_z = BOX_CENTRE_Z
    return cam


class RoadSurfaceGrid(unittest.TestCase):
    def test_heights_and_overlaps(self):
        s = RoadSurface(SCENE)
        top, low = s.heights(np.array([50.0, 0.0, 50.0, 60.0]), np.array([20.0, 20.0, 23.5, 60.0]))
        self.assertAlmostEqual(top[0], 10.0)
        self.assertEqual((top[1], low[1]), (10.0, 0.0))  # the flyover over the street "under"
        self.assertAlmostEqual(top[2], 0.0)
        self.assertTrue(np.isnan(top[3]))  # off the roads

    def test_flat_maps_need_no_surface(self):
        flat = {"lanes": [{"id": "a", "centreline": [[0, 0], [10, 0]], "z": [0.1, 0.2]}]}
        self.assertIsNone(RoadSurface.from_scene(flat))
        self.assertIsNone(RoadSurface.from_scene({"lanes": [{"id": "a", "centreline": [[0, 0], [10, 0]]}]}))
        self.assertIsNotNone(RoadSurface.from_scene(SCENE))


class Projection(unittest.TestCase):
    def setUp(self):
        self.cam, self.surface = nadir_camera(), RoadSurface(SCENE)

    def pixel_of(self, x, y, road_z):
        return self.cam.to_pixels(0, np.array([[x, y, road_z + BOX_CENTRE_Z]]))[0]

    def test_car_on_the_flyover(self):
        u, v = self.pixel_of(30.0, 20.0, 10.0)
        flat = self.cam.to_ground(0, np.array([u]), np.array([v]))[0]
        road = self.cam.to_ground_on_roads(0, np.array([u]), np.array([v]), self.surface)[0]
        self.assertGreater(abs(flat[1] - 20.0), 3.0)  # flat plane: about one lane out, onto the street
        np.testing.assert_allclose(road, [30.0, 20.0], atol=0.3)

    def test_car_on_the_street_stays(self):
        u, v = self.pixel_of(30.0, 30.0, 0.0)
        road = self.cam.to_ground_on_roads(0, np.array([u]), np.array([v]), self.surface)[0]
        np.testing.assert_allclose(road, [30.0, 30.0], atol=0.3)

    def test_street_under_the_deck_edge_is_hidden(self):
        # a car at y = 23.5 on the street can't be seen: its ray passes through the deck (y 21.26 at
        # deck height), so a box there is a car on the flyover
        u, v = self.pixel_of(30.0, 23.5, 0.0)
        road = self.cam.to_ground_on_roads(0, np.array([u]), np.array([v]), self.surface)[0]
        self.assertLess(abs(road[1] - 20.0), 1.75)

    def test_under_the_flyover_takes_the_top_surface(self):
        # straight below the camera the flyover hides the street: the visible car is the one on top
        u, v = self.pixel_of(0.0, 20.0, 10.0)
        road = self.cam.to_ground_on_roads(0, np.array([u]), np.array([v]), self.surface)[0]
        np.testing.assert_allclose(road, [0.0, 20.0], atol=0.3)

    def test_off_road_falls_back_to_the_plane(self):
        u, v = self.pixel_of(60.0, 60.0, 0.0)
        road = self.cam.to_ground_on_roads(0, np.array([u]), np.array([v]), self.surface)[0]
        flat = self.cam.to_ground(0, np.array([u]), np.array([v]))[0]
        np.testing.assert_allclose(road, flat)


if __name__ == "__main__":
    unittest.main()
