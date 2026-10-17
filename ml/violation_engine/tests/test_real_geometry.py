"""Unit tests for real-footage geometry (real_geometry.py, Phase B1).

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from real_geometry import RealCamera, Site, parse_srt, srt_metres_per_px  # noqa: E402

SRT = """1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
2024-05-01 10:00:00.000
[iso: 100] [shutter: 1/1000.0] [fnum: 1.7] [ev: 0] [color_md: default] [focal_len: 24.00] [latitude: 52.123456] [longitude: 7.123456] [rel_alt: 80.000 abs_alt: 120.500] [ct: 5500] </font>

2
00:00:00,033 --> 00:00:00,066
<font size="28">FrameCnt: 2, DiffTime: 33ms
[iso: 100] [focal_len: 24.00] [latitude: 52.123457] [longitude: 7.123457] [rel_alt: 81.000 abs_alt: 121.500] </font>
"""


def make_site(tmp: Path, calibration: dict, **extra) -> Site:
    data = {"video": str(tmp / "clip.mp4"), "reference_frame": 0, "frame_size": [1920, 1080],
            "calibration": calibration, "lanes": [], "zones": [], **extra}
    p = tmp / "site.json"
    p.write_text(json.dumps(data))
    return Site(p)


class Calibration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_scale(self):
        s = make_site(self.tmp, {"method": "scale", "image_points": [[100, 500], [150, 500]], "length_m": 3.5})
        a, b = s.to_metres(np.array([[0.0, 0.0], [100.0, 0.0]]))
        self.assertAlmostEqual(math.hypot(*(b - a)), 7.0, places=6)  # 50 px = 3.5 m, so 100 px = 7 m
        self.assertTrue(s.left_handed)  # image axes kept (x right, y down)

    def test_homography_tilted_rectangle(self):
        # a 3.5 m x 10 m lane rectangle seen in perspective (far side narrower)
        img = [[900, 300], [1020, 300], [1100, 800], [820, 800]]
        world = [[0, 0], [3.5, 0], [3.5, 10], [0, 10]]
        s = make_site(self.tmp, {"method": "homography", "image_points": img, "world_points": world})
        np.testing.assert_allclose(s.to_metres(np.array(img, float)), world, atol=1e-6)
        np.testing.assert_allclose(s.to_px(np.array(world, float)), img, atol=1e-6)

    def test_mirrored_world_is_right_handed(self):
        img = [[0, 0], [100, 0], [100, 100], [0, 100]]
        world = [[0, 0], [10, 0], [10, -10], [0, -10]]  # y up: a mirror of the image axes
        s = make_site(self.tmp, {"method": "homography", "image_points": img, "world_points": world})
        self.assertFalse(s.left_handed)

    def test_srt(self):
        recs = parse_srt(SRT)
        self.assertEqual(len(recs), 2)
        self.assertAlmostEqual(recs[0]["rel_alt"], 80.0)
        self.assertAlmostEqual(recs[1]["t"], 0.033)
        mpp = srt_metres_per_px(recs, 90.0, 1920)  # 90 deg FOV at 80 m -> 160 m across 1920 px
        self.assertAlmostEqual(mpp, 160 / 1920, places=6)
        (self.tmp / "clip.SRT").write_text(SRT)
        s = make_site(self.tmp, {"method": "srt", "srt": "clip.SRT", "hfov_deg": 90.0})
        a, b = s.to_metres(np.array([[0.0, 0.0], [1920.0, 0.0]]))
        self.assertAlmostEqual(b[0] - a[0], 160.0, places=4)


class Camera(unittest.TestCase):
    def test_scene_map_round_trip(self):
        tmp = Path(tempfile.mkdtemp())
        s = make_site(tmp, {"method": "scale", "image_points": [[0, 0], [100, 0]], "length_m": 10.0})
        # the camera pans: frame f is shifted 5 px per frame to the right relative to the anchor
        Hs = np.array([[[1, 0, 5 * f], [0, 1, 0], [0, 0, 1]] for f in range(10)], float)
        np.savez(tmp / "scene_map.npz", H=Hs, failed=np.zeros(10, bool), anchor=np.array(0))
        cam = RealCamera(s, tmp / "scene_map.npz")
        g = cam.to_ground(4, np.array([100.0]), np.array([0.0]))  # pixel 100 in frame 4 = pixel 120 in frame 0
        self.assertAlmostEqual(g[0, 0], 12.0, places=6)
        uv = cam.to_pixels(4, np.array([[12.0, 0.0, 0.0]]))
        np.testing.assert_allclose(uv[0], [100.0, 0.0], atol=1e-6)


if __name__ == "__main__":
    unittest.main()
