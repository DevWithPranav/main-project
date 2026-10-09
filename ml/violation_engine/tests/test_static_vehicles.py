"""L0 unit tests for the static-vehicle cross-check (static_vehicles.py, B6).

Synthetic ground patches: road texture, with or without a car-sized dark block in the middle,
fed straight to verdict() / apply() (no frames, no camera).

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import static_vehicles as sv  # noqa: E402

FPS = 10.0
N = int(round(sv.PATCH_M / sv.RES_M))


def road(rng) -> np.ndarray:
    return np.clip(120 + rng.normal(0, 3, (N, N)), 0, 255).astype(np.uint8)


def car(rng) -> np.ndarray:
    p = road(rng)
    c, hl, hw = N // 2, int(2.2 / sv.RES_M), int(0.9 / sv.RES_M)
    p[c - hl:c + hl, c - hw:c + hw] = 40  # dark car, 4.4 x 1.8 m
    p[c - hl + 3:c - hl + 8, c - hw + 2:c + hw - 2] = 200  # windscreen highlight
    return p


def probe(before: list, window: list, after: list) -> dict:
    """Patches sampled every 0.5 s: before | (gap) | window | (gap) | after."""
    step = int(0.5 * FPS)
    gap = int(sv.BG_MARGIN_S * FPS) + step
    frames, patches, f = [], [], 0
    for part, ps in (("before", before), ("window", window), ("after", after)):
        if part == "window":
            f += gap
            f0 = f
        if part == "after":
            f1 = f - step
            f += gap
        for p in ps:
            frames.append(f)
            patches.append(p)
            f += step
    return {"x": 0.0, "y": 0.0, "f0": f0, "f1": f1, "frames": frames, "patches": patches}


class TestVerdict(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(0)

    def test_car_on_empty_road_confirmed(self):
        p = probe([road(self.rng) for _ in range(8)], [car(self.rng) for _ in range(10)], [road(self.rng) for _ in range(8)])
        self.assertEqual(sv.verdict(p, FPS)["static"], "confirmed")

    def test_empty_road_contradicted(self):
        p = probe([road(self.rng) for _ in range(8)], [road(self.rng) for _ in range(10)], [road(self.rng) for _ in range(8)])
        self.assertEqual(sv.verdict(p, FPS)["static"], "contradicted")

    def test_car_still_there_after_the_event(self):
        # tracker lost the car, event closed early: the "after" views show the same car
        p = probe([road(self.rng) for _ in range(8)], [car(self.rng) for _ in range(10)], [car(self.rng) for _ in range(8)])
        v = sv.verdict(p, FPS)
        self.assertEqual(v["static"], "confirmed")
        self.assertEqual(v["reference"], "before")

    def test_no_reference_unknown(self):
        p = probe([], [car(self.rng) for _ in range(10)], [])
        self.assertEqual(sv.verdict(p, FPS)["static"], "unknown")

    def test_busy_background_views_ignored(self):
        # other cars queued on the spot before (the tracker saw them): only the free views count
        before = [car(self.rng) for _ in range(6)] + [road(self.rng) for _ in range(6)]
        p = probe(before, [car(self.rng) for _ in range(10)], [])
        p["blocked"] = np.array([True] * 6 + [False] * 6 + [False] * 10)
        self.assertEqual(sv.verdict(p, FPS)["static"], "confirmed")


class TestApply(unittest.TestCase):
    def event(self, conf, status="needs_review", etype="zebra_crossing"):
        return {"event_id": "e1", "type": etype, "confidence": conf, "status": status, "tags": [], "value": {}}

    def test_confirmed_bonus_can_flag(self):
        e = self.event(0.80)
        sv.apply([e], {"e1": {"static": "confirmed"}})
        self.assertAlmostEqual(e["confidence"], 0.90)
        self.assertEqual(e["status"], "flagged")
        self.assertIn("static_confirmed", e["tags"])

    def test_contradicted_goes_to_review(self):
        e = self.event(0.95, "flagged")
        sv.apply([e], {"e1": {"static": "contradicted"}})
        self.assertEqual(e["status"], "needs_review")

    def test_suppressed_untouched(self):
        e = self.event(0.95, "suppressed")
        sv.apply([e], {"e1": {"static": "confirmed"}})
        self.assertEqual(e["status"], "suppressed")

    def test_rerun_starts_from_original_confidence(self):
        e = self.event(0.80)
        sv.apply([e], {"e1": {"static": "confirmed"}})
        sv.apply([e], {"e1": {"static": "confirmed"}})
        self.assertAlmostEqual(e["confidence"], 0.90)
        self.assertEqual(e["tags"].count("static_confirmed"), 1)


if __name__ == "__main__":
    unittest.main()
