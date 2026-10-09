"""Tests for the UIT-ADrone frame-level scorer (eval_uit_adrone.py, B5).

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_uit_adrone as ev  # noqa: E402


def event(a, b, etype="wrong_way", conf=0.9, status="flagged"):
    return {"type": etype, "status": status, "confidence": conf, "start_frame": a, "flag_frame": a, "end_frame": b}


class Score(unittest.TestCase):
    labels = np.r_[np.zeros(100), np.ones(50), np.zeros(100), np.ones(50)]  # two abnormal stretches

    def test_auc_ties_and_extremes(self):
        self.assertEqual(ev.auc(np.r_[np.zeros(5), np.ones(5)], np.r_[np.zeros(5), np.ones(5)]), 1.0)
        self.assertEqual(ev.auc(np.zeros(10), np.r_[np.zeros(5), np.ones(5)]), 0.5)
        self.assertIsNone(ev.auc(np.zeros(4), np.zeros(4)))

    def test_one_stretch_hit_one_false_alarm(self):
        r = ev.score_video(self.labels, [event(110, 139), event(10, 19, "no_parking")], set(ev.IN_SCOPE))
        self.assertEqual(r["abnormal_segments"], 2)
        self.assertEqual(r["segment_recall"], 0.5)
        self.assertEqual(r["event_precision"], 0.5)
        self.assertAlmostEqual(r["frame_precision"], 30 / 40)
        self.assertAlmostEqual(r["frame_recall"], 30 / 100)

    def test_suppressed_and_out_of_type_ignored(self):
        evs = [event(110, 139, status="suppressed"), event(110, 139, "speeding")]
        r = ev.score_video(self.labels, evs, set(ev.IN_SCOPE))
        self.assertEqual(r["events"], 0)
        self.assertEqual(r["frame_auc"], 0.5)

    def test_higher_confidence_ranks_higher(self):
        evs = [event(110, 139, conf=0.95), event(10, 19, conf=0.3)]
        self.assertGreater(ev.score_video(self.labels, evs, set(ev.IN_SCOPE))["frame_auc"], 0.6)


if __name__ == "__main__":
    unittest.main()
