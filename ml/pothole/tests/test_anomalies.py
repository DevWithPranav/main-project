"""Anomaly pipeline pieces (Build Plan M9): tiling, edge cases, projection, area, severity, dedup, events.

Run:  python -m unittest discover -s ml/pothole/tests -v
"""

import math
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "ml" / "violation_engine"))
import anomalies as A  # noqa: E402
from detect_anomalies import FixedGSD, Lanes, RoadLevel  # noqa: E402
from ground_coords import FlightCamera, RoadSurface, ue_matrix  # noqa: E402
from schemas import event_errors  # noqa: E402

ALT = 67.6  # staged flights' altitude


def nadir_camera(alt: float = ALT) -> FlightCamera:
    cam = FlightCamera.__new__(FlightCamera)
    cam.W, cam.H = 1920, 1080
    cam.f = cam.W / (2 * math.tan(math.radians(90.0) / 2))
    m = ue_matrix((100.0, -50.0, alt), (-90.0, 0.0, 0.0))
    cam.frame_pose = {0: (m[:3, 3], m[:3, :3])}
    cam.ground_z = 0.75
    return cam


def road_level(scene: dict | None = None, alt: float = ALT) -> RoadLevel:
    p = RoadLevel.__new__(RoadLevel)
    p.cam, p.plane_z = nadir_camera(alt), 0.0
    p.surface = RoadSurface.from_scene(scene) if scene else None
    return p


def road_texture(h: int = 200, w: int = 200, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    g = np.clip(120 + rng.normal(0, 12, (h, w)), 0, 255)
    return np.stack([g, g * 1.02, g * 1.05], axis=-1).clip(0, 255).astype(np.uint8)  # grey asphalt, BGR


def disc(h: int, w: int, cx: int, cy: int, r: int) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    cv2.circle(m, (cx, cy), r, 1, -1)
    return m.astype(bool)


class Tiling(unittest.TestCase):
    def test_tiles_cover_frame_full_size(self):
        t = A.tiles(1920, 1080, 640, 0.2)
        cover = np.zeros((1080, 1920), bool)
        for x0, y0, x1, y1 in t:
            self.assertEqual((x1 - x0, y1 - y0), (640, 640))
            cover[y0:y1, x0:x1] = True
        self.assertTrue(cover.all())
        self.assertEqual(len(t), 8)  # 4 columns x 2 rows
        self.assertEqual(A.tiles(500, 400, 640, 0.2), [(0, 0, 500, 400)])  # frame smaller than a tile

    def test_merge_split_object_and_duplicates(self):
        h, w = 100, 200
        whole = disc(h, w, 100, 50, 20)
        left, right = whole.copy(), whole.copy()
        left[:, 100:] = False
        right[:, :100] = False
        dets = [{"cls": "pothole", "conf": 0.6, "mask": left, "tile": (0, 0)},
                {"cls": "pothole", "conf": 0.8, "mask": right, "tile": (100, 0)},
                {"cls": "pothole", "conf": 0.5, "mask": whole, "tile": (0, 0)},  # whole-frame pass, same object
                {"cls": "crack", "conf": 0.7, "mask": whole, "tile": (0, 0)},  # other class: kept apart
                {"cls": "pothole", "conf": 0.9, "mask": disc(h, w, 20, 20, 5), "tile": (0, 0)}]  # separate object
        out = A.merge_tile_masks(dets)
        pots = [d for d in out if d["cls"] == "pothole"]
        self.assertEqual(len(pots), 2)
        big = max(pots, key=lambda d: d["mask"].sum())
        self.assertTrue(np.array_equal(big["mask"], whole))
        self.assertAlmostEqual(big["conf"], 0.8)
        self.assertEqual(sum(d["cls"] == "crack" for d in out), 1)


class EdgeCases(unittest.TestCase):
    def test_cast_shadow_is_flagged(self):
        img = road_texture()
        m = disc(200, 200, 100, 100, 30)
        shaded = img.copy()
        shaded[m] = (shaded[m] * 0.5).astype(np.uint8)  # a shadow scales brightness, keeps colour + texture
        f = A.shadow_features(shaded, m)
        self.assertLess(f["brightness"], 0.6)
        self.assertTrue(A.looks_like_shadow(f), f)

    def test_pothole_is_not_a_shadow(self):
        img = road_texture()
        m = disc(200, 200, 100, 100, 30)
        rng = np.random.default_rng(1)
        hole = np.clip(np.stack([50 + rng.normal(0, 25, m.sum()), 60 + rng.normal(0, 25, m.sum()),
                                 80 + rng.normal(0, 25, m.sum())], -1), 0, 255)  # darker, brownish, rough
        img[m] = hole.astype(np.uint8)
        f = A.shadow_features(img, m)
        self.assertFalse(A.looks_like_shadow(f), f)

    def test_shadow_features_need_a_ring(self):
        self.assertIsNone(A.shadow_features(road_texture(50, 50), np.ones((50, 50), bool)))

    def test_waterlogged_overlap(self):
        p = disc(100, 100, 50, 50, 10)
        self.assertTrue(A.waterlogged(p, [disc(100, 100, 52, 50, 15)]))
        self.assertFalse(A.waterlogged(p, [disc(100, 100, 80, 80, 8)]))
        self.assertFalse(A.waterlogged(p, []))

    def test_plausible_size(self):
        self.assertTrue(A.plausible_size("pothole", 1.0))
        self.assertFalse(A.plausible_size("pothole", 482.0))  # a whole intersection (measured failure mode)
        self.assertFalse(A.plausible_size("pothole", 0.001))
        self.assertTrue(A.plausible_size("waterlogging", 400.0))


class Projection(unittest.TestCase):
    def test_nadir_centre_and_scale(self):
        p = road_level()
        g = p(0, np.array([960.0, 960.0 + 96]), np.array([540.0, 540.0]))
        np.testing.assert_allclose(g[0], [100.0, -50.0], atol=1e-6)  # straight below the camera
        self.assertAlmostEqual(float(np.hypot(*(g[1] - g[0]))), ALT * 96 / 960, places=6)  # GSD = alt / f

    def test_round_trip_with_to_pixels(self):
        p = road_level()
        u, v = np.array([200.0, 1500.0, 960.0]), np.array([100.0, 900.0, 300.0])
        g = p(0, u, v)
        uv = p.cam.to_pixels(0, np.c_[g, np.zeros(len(g))])
        np.testing.assert_allclose(uv, np.c_[u, v], atol=1e-6)

    def test_road_heights_used(self):
        """A pothole on a 10 m flyover: the flat plane puts it further out than the deck does."""
        scene = {"lanes": [{"id": "fly", "centreline": [[0, -50], [200, -50]], "z": [10.0, 10.0], "width_m": 120.0},
                           {"id": "street", "centreline": [[0, 100], [10, 100]], "z": [0.0, 0.0], "width_m": 3.5}]}
        flat, deck = road_level(), road_level(scene)
        u, v = np.array([1700.0]), np.array([540.0])
        r_flat = float(np.hypot(*(flat(0, u, v)[0] - [100, -50])))
        r_deck = float(np.hypot(*(deck(0, u, v)[0] - [100, -50])))
        self.assertAlmostEqual(r_deck / r_flat, (ALT - 10.0) / ALT, places=3)

    def test_area_from_projection(self):
        """A 30 px disc at 67.6 m: pixel count x GSD^2, within 1%."""
        p = road_level()
        m = disc(1080, 1920, 960, 540, 30)
        out = A.mask_outline(m)
        a = A.mask_area_m2(m, p(0, out[:, 0], out[:, 1]), out)
        gsd = ALT / 960
        self.assertAlmostEqual(a / (m.sum() * gsd * gsd), 1.0, delta=0.01)

    def test_fixed_gsd(self):
        m = np.zeros((50, 50), bool)
        m[10:20, 10:20] = True
        out = A.mask_outline(m)
        self.assertAlmostEqual(A.mask_area_m2(m, FixedGSD(0.1)(0, out[:, 0], out[:, 1]), out), 1.0, places=6)
        np.testing.assert_allclose(A.polygon_centroid(FixedGSD(0.1)(0, out[:, 0], out[:, 1])), [1.45, -1.45], atol=1e-9)

    def test_polygon_helpers(self):
        sq = np.array([[0, 0], [2, 0], [2, 2], [0, 2]], float)
        self.assertEqual(A.polygon_area(sq), 4.0)
        np.testing.assert_allclose(A.polygon_centroid(sq), [1, 1])

    def test_lane_lookup(self):
        lanes = Lanes({"lanes": [{"id": "a", "centreline": [[0, 0], [100, 0]], "width_m": 3.5}]})
        self.assertEqual(lanes.lookup(50, 1.0), ("a", True))
        self.assertEqual(lanes.lookup(50, 2.5), (None, True))  # shoulder: on the road, in no lane
        self.assertEqual(lanes.lookup(50, 10.0), (None, False))


class Severity(unittest.TestCase):
    def test_bounds_bands_and_monotonic(self):
        prev = -1.0
        for area in (0.0, 0.05, 0.2, 0.5, 1.0, 3.0, 50.0):
            s, b = A.severity("pothole", area)
            self.assertTrue(0.0 <= s <= 1.0)
            self.assertGreaterEqual(s, prev)
            self.assertEqual(b, A.band(s))
            prev = s
        self.assertEqual(A.severity("pothole", 0.05)[1], "low")
        self.assertEqual(A.severity("pothole", 2.0)[1], "high")

    def test_depth_proxy_and_class_weights(self):
        self.assertGreater(A.severity("pothole", 0.3, darkness=0.8)[0], A.severity("pothole", 0.3, darkness=0.1)[0])
        self.assertGreater(A.severity("crack", 1.0, pattern="alligator")[0], A.severity("crack", 1.0, pattern="linear")[0])
        self.assertGreater(A.severity("pothole", 100.0)[0], A.severity("crack", 100.0)[0])  # class weighting

    def test_crack_pattern(self):
        line = np.zeros((100, 100), np.uint8)
        cv2.line(line, (5, 5), (95, 90), 1, 2)
        self.assertEqual(A.crack_pattern(line.astype(bool)), "linear")
        curve = np.zeros((100, 100), np.uint8)
        cv2.polylines(curve, [np.array([[5, 50], [30, 45], [60, 55], [95, 48]], np.int32)], False, 1, 2)
        self.assertEqual(A.crack_pattern(curve.astype(bool)), "linear")
        net = np.zeros((100, 100), np.uint8)  # alligator: a net of thin cracks over a patch
        for k in range(10, 95, 15):
            cv2.line(net, (k, 10), (k + 5, 90), 1, 1)
            cv2.line(net, (10, k), (90, k + 4), 1, 1)
        self.assertEqual(A.crack_pattern(net.astype(bool)), "alligator")


def sighting(frame, x, y, cls="pothole", conf=0.8, area=0.5, tags=()):
    return A.Sighting(frame, frame * 0.1, cls, conf, x, y, area, A.severity(cls, area)[0], tuple(tags),
                      np.array([[x - 0.3, y - 0.3], [x + 0.3, y - 0.3], [x + 0.3, y + 0.3]]))


class Dedup(unittest.TestCase):
    def test_same_defect_over_frames_is_one_event(self):
        d = A.Deduper()
        rng = np.random.default_rng(0)
        for f in range(20):  # one pothole, 0.3 m position noise; a second one 6 m away
            d.add(sighting(f, 10 + rng.normal(0, 0.3), 5 + rng.normal(0, 0.3)))
            d.add(sighting(f, 16 + rng.normal(0, 0.3), 5 + rng.normal(0, 0.3)))
        self.assertEqual(len(d.events()), 2)

    def test_type_must_match(self):
        d = A.Deduper(min_frames=1)
        d.add(sighting(0, 0, 0, "pothole"))
        d.add(sighting(1, 0.5, 0, "waterlogging", area=5.0))
        self.assertEqual(len(d.events()), 2)

    def test_min_frames_drops_flicker(self):
        d = A.Deduper(min_frames=3)
        d.add(sighting(0, 0, 0))
        d.add(sighting(0, 0.2, 0))  # same frame twice still counts once
        d.add(sighting(1, 0, 0.1))
        self.assertEqual(d.events(), [])
        d.add(sighting(2, 0.1, 0))
        self.assertEqual(len(d.events()), 1)

    def test_large_defect_reach(self):
        """A 400 m^2 pool seen partly: centroids 8 m apart are still one pool."""
        d = A.Deduper(min_frames=1)
        d.add(sighting(0, 0, 0, "waterlogging", area=400.0))
        d.add(sighting(1, 8, 0, "waterlogging", area=300.0))
        self.assertEqual(len(d.events()), 1)

    def test_event_is_schema_valid(self):
        d = A.Deduper()
        for f in range(4):
            d.add(sighting(f, 1 + 0.1 * f, 2, conf=0.5 + 0.1 * f, tags=("possible_shadow",) if f < 2 else ()))
        c = d.events()[0]
        e = A.to_event(c, "flight-anom-0000", lane_id="r1", snapshot="snapshots/flight-anom-0000.jpg")
        self.assertEqual(event_errors([e]), [])
        self.assertEqual((e["kind"], e["type"], e["frame"], e["status"]), ("anomaly", "pothole", 3, "flagged"))
        self.assertEqual(e["tags"], ["possible_shadow"])  # on half the sightings
        self.assertEqual(e["evidence"]["telemetry"]["n_frames"], 4)
        self.assertAlmostEqual(e["x"], float(c.xy[0]), places=2)
        self.assertIn(e["severity_band"], ("low", "medium", "high"))


if __name__ == "__main__":
    unittest.main()
