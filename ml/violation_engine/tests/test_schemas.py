"""Build Plan M0: the event and profile schemas, the condition catalogue and profiles in the engine.

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from events import write_events  # noqa: E402
from profiles import (PROFILE_DIR, default_profile, disabled_conditions, engine_params,  # noqa: E402
                      load_profile, profile_errors)
from rules import DEFAULTS, Engine  # noqa: E402
from schemas import condition_of, conditions, event_errors, load  # noqa: E402
from test_rules import BASE_LANES, drive_stop_leave, kmh, rect, scene, track  # noqa: E402

SPEEDER = (lambda t: (-150 + kmh(70) * t, 0), 10.0)
WRONG_WAY = (lambda t: (100 - kmh(30) * t, 0), 10.0)


def anomaly(**kw):
    return {"kind": "anomaly", "event_id": "an-0001", "type": "pothole", "t_s": 12.5, "frame": 300, "x": 10.0,
            "y": -3.0, "severity_score": 0.7, "severity_band": "high", "confidence": 0.8, "status": "flagged", **kw}


class SchemaFiles(unittest.TestCase):
    def test_schemas_are_valid_json_schema(self):
        for name in ("event.schema.json", "profile.schema.json"):
            jsonschema.Draft202012Validator.check_schema(load(name))

    def test_34_conditions(self):
        ids = [c["id"] for c in conditions()]
        self.assertEqual(len(ids), 34)
        self.assertEqual(len(set(ids)), 34)
        per_group = {g: sum(c["group"] == g for c in conditions()) for g in load("conditions.json")["types"]}
        self.assertEqual(per_group, {"lane": 8, "illegal_stopping": 6, "wrong_way": 5, "u_turn": 5, "speeding": 5, "zebra": 5})

    def test_condition_engine_types_exist(self):
        for c in conditions():
            if c["engine_type"]:
                self.assertIn(c["engine_type"], DEFAULTS, c["id"])


class EventSchema(unittest.TestCase):
    def events(self, *tracks, sc=None):
        evs = Engine(sc or scene()).run([r for t in tracks for r in t])
        with tempfile.TemporaryDirectory() as d:
            write_events(evs, Path(d))
            return json.loads((Path(d) / "violations.json").read_text())

    def test_engine_output_passes(self):
        out = self.events(track(SPEEDER, tid=1), track(WRONG_WAY, tid=2))
        self.assertLessEqual({"speeding", "wrong_way"}, {e["type"] for e in out})
        self.assertEqual(event_errors(out), [])

    def test_engine_sets_conditions(self):
        out = {e["type"]: e for e in self.events(track(SPEEDER, tid=1), track(WRONG_WAY, tid=2))}
        self.assertEqual(out["speeding"]["condition"], "E1")
        self.assertEqual(out["wrong_way"]["condition"], "C1")
        self.assertEqual(out["speeding"]["kind"], "violation")

    def test_speed_zone_is_e3(self):
        sc = scene(zones=[{"id": "school", "type": "speed", "polygon": rect(-200, 200, -2, 2), "limit_kmh": 30}])
        fn = (lambda t: (-150 + kmh(45) * t, 0), 10.0)
        ev = [e for e in self.events(track(fn), sc=sc) if e["type"] == "speeding"]
        self.assertEqual([e["condition"] for e in ev], ["E3"])

    def test_shoulder_stop_is_b2(self):
        lanes = BASE_LANES + [{"id": "S", "centreline": [[-300, -6.5], [300, -6.5]], "width_m": 2.5,
                               "lane_type": "shoulder", "speed_limit_kmh": 50}]
        sc = scene(lanes, [{"id": "hw", "type": "highway", "polygon": rect(-300, 300, -10, 10)}])
        ev = [e for e in self.events(track(drive_stop_leave(60, -6.5, 25)), sc=sc) if e["type"] == "highway_stop"]
        self.assertEqual([e["condition"] for e in ev], ["B2"])
        self.assertEqual(event_errors(ev), [])

    def test_condition_of_tags(self):
        self.assertEqual(condition_of({"type": "lane_violation", "tags": ["straddling"]}), "A4")
        self.assertEqual(condition_of({"type": "lane_violation", "tags": ["solid_line_crossing"]}), "A2")
        self.assertEqual(condition_of({"type": "no_parking", "tags": []}), "B3")
        self.assertIsNone(condition_of({"type": "red_light", "tags": []}))

    def test_old_events_without_kind_pass(self):
        e = self.events(track(SPEEDER))[0]
        del e["kind"], e["condition"]
        self.assertEqual(event_errors([e]), [])

    def test_bad_events_fail(self):
        e = self.events(track(SPEEDER))[0]
        self.assertTrue(event_errors([{**e, "status": "approved"}]))
        self.assertTrue(event_errors([{**e, "confidence": 1.5}]))
        self.assertTrue(event_errors([{**e, "condition": "Z9"}]))
        self.assertTrue(event_errors([{k: v for k, v in e.items() if k != "flag_s"}]))
        self.assertTrue(event_errors([{**e, "review": {"outcome": "confirmed", "by": "officer1", "at": "yesterday"}}]))
        self.assertEqual(event_errors([{**e, "review": {"outcome": "dismissed", "by": "officer1",
                                                         "at": "2026-10-09T12:00:00Z", "note": "queue"}}]), [])

    def test_anomaly_events(self):
        self.assertEqual(event_errors([anomaly(area_sq_m=0.4, recurrence_count=2)]), [])
        self.assertTrue(event_errors([anomaly(type="speeding")]))
        self.assertTrue(event_errors([{k: v for k, v in anomaly().items() if k != "severity_score"}]))
        self.assertTrue(event_errors([anomaly(status="needs_review")]))


class Profiles(unittest.TestCase):
    def test_default_file_matches_code(self):
        """configs/profiles/default.json is generated by `profiles.py default`; regenerate it if this fails."""
        self.assertEqual(json.loads((PROFILE_DIR / "default.json").read_text()), default_profile())

    def test_shipped_profiles_valid(self):
        files = sorted(PROFILE_DIR.glob("*.json"))
        self.assertGreaterEqual(len(files), 2)
        for f in files:
            self.assertEqual(profile_errors(json.loads(f.read_text())), [], f.name)

    def test_load_by_name(self):
        self.assertEqual(load_profile(Path("town05"))["applies_to"]["map"], "Town05")

    def test_errors_caught(self):
        base = {"profile_version": 1, "name": "x"}
        self.assertTrue(profile_errors({**base, "violations": {"speeding": {"params": {"min_secs": 1}}}}))
        self.assertTrue(profile_errors({**base, "violations": {"helmet": {"enabled": True}}}))
        self.assertTrue(profile_errors({**base, "conditions": {"A9": False}}))
        self.assertTrue(profile_errors({**base, "road": {"scene": "ml/violation_engine/configs/scenes/Nowhere.json"}}))
        self.assertTrue(profile_errors({**base, "model": {"conf": 2}}))
        self.assertTrue(profile_errors({"name": "x"}))
        self.assertEqual(profile_errors(base), [])

    def test_engine_params_shape(self):
        p = engine_params({"profile_version": 1, "name": "x",
                           "violations": {"speeding": {"enabled": True, "params": {"sigmas": 1.5}}}})
        self.assertEqual(p["speeding"], {"sigmas": 1.5, "enabled": True})
        self.assertFalse(p["red_light"]["enabled"])  # out of scope unless turned on

    def test_type_off_removes_monitor(self):
        prof = {"profile_version": 1, "name": "x", "violations": {"speeding": {"enabled": False}}}
        evs = Engine(scene(), engine_params(prof)).run(track(SPEEDER) + track(WRONG_WAY, tid=2))
        self.assertNotIn("speeding", {e.type for e in evs})
        self.assertIn("wrong_way", {e.type for e in evs})

    def test_threshold_from_profile(self):
        prof = {"profile_version": 1, "name": "x", "violations": {"no_parking": {"params": {"min_s": 20.0}}}}
        sc = scene(zones=[{"id": "np", "type": "no_parking", "polygon": rect(50, 70, -1.75, 1.75)}])
        stop25 = track(drive_stop_leave(60, 0, 25))
        self.assertEqual([e for e in Engine(sc).run(stop25) if e.type == "no_parking"], [])
        self.assertEqual(len([e for e in Engine(sc, engine_params(prof)).run(stop25) if e.type == "no_parking"]), 1)

    def test_condition_off_drops_events(self):
        sc = scene(zones=[{"id": "school", "type": "speed", "polygon": rect(-200, 200, -2, 2), "limit_kmh": 30}])
        fn = (lambda t: (-150 + kmh(45) * t, 0), 10.0)
        prof = {"profile_version": 1, "name": "x", "conditions": {"E3": False}}
        self.assertEqual(disabled_conditions(prof), {"E3"})
        evs = Engine(sc, engine_params(prof), disabled_conditions=disabled_conditions(prof)).run(track(fn))
        self.assertEqual([e for e in evs if e.type == "speeding"], [])
        evs = Engine(scene(), engine_params(prof), disabled_conditions={"E3"}).run(track(SPEEDER))
        self.assertEqual([asdict(e)["condition"] for e in evs if e.type == "speeding"], ["E1"])


if __name__ == "__main__":
    unittest.main()
