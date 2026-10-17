"""Tests for eval_zone_membership.py (PRD 27.6): synthetic flight with one vehicle crossing a zone.

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_zone_membership as zm  # noqa: E402


class ZoneMembership(unittest.TestCase):
    def make(self, tmp: Path, pipe_offset: float):
        # truth logged every 2nd CARLA frame (interpolated in between); the vehicle drives +x at 1 m per frame
        (tmp / "frame_times.csv").write_text("frame,time_s,carla_frame\n" + "".join(f"{f},{f * 0.1},{100 + f}\n" for f in range(21)))
        (tmp / "vehicle_poses.csv").write_text("carla_frame,id,x,y\n" + "".join(f"{100 + f},7,{float(f)},0.0\n" for f in range(0, 21, 2)))
        (tmp / "zones.json").write_text(json.dumps({"zones": [{"id": "z", "type": "no_parking", "polygon": [[8, -2], [12, -2], [12, 2], [8, 2]]}]}))
        kin = tmp / "kin.csv"
        kin.write_text("frame,x,y,visible\n" + "".join(f"{f},{f + pipe_offset},0.0,1\n" for f in range(21)))
        return kin

    def test_perfect_pipeline_scores_one(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            res = zm.evaluate(tmp, self.make(tmp, 0.0), [tmp / "zones.json"], near_m=10.0, match_m=3.0)
            self.assertEqual(res["accuracy_relevant"], 1.0)
            self.assertEqual(res["position_error_m"]["median"], 0.0)
            self.assertTrue(res["target_met"])

    def test_shifted_pipeline_is_penalised(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            res = zm.evaluate(tmp, self.make(tmp, 2.0), [tmp / "zones.json"], near_m=10.0, match_m=3.0)
            z = res["per_zone"][0]
            self.assertLess(res["accuracy_relevant"], 0.98)
            self.assertGreater(z["FP"] + z["FN"], 0)
            self.assertFalse(res["target_met"])


if __name__ == "__main__":
    unittest.main()
