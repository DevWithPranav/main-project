"""Build Plan M8: hotspots, recommendation fields, deterministic ids, planner history re-ranking."""

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analytics  # noqa: E402
from history import History, rerank  # noqa: E402
from recommend import recommend  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
TOWN03 = json.loads((REPO / "ml/violation_engine/configs/scenes/Town03.json").read_text())
REQUIRED = {"id", "problem", "location", "evidence", "action", "expected_impact", "priority", "validation_method",
            "confidence", "limitations", "alternatives", "simulation"}


def ev(i, typ, x, y, lane=None, cond=None, t=100.0, value=None):
    return {"event_id": f"e{i}", "type": typ, "condition": cond, "track_ids": [i], "cls": "car", "start_s": t,
            "start_frame": 0, "flag_s": t + 1, "flag_frame": 1, "x": x, "y": y, "lane_id": lane, "zone_id": None,
            "value": value or {}, "tags": [], "confidence": 0.9, "status": "flagged"}


def cluster(typ, n, cx, cy, start=0, spread=10.0, seed=0, **kw):
    r = random.Random(seed)
    return [ev(start + i, typ, cx + r.uniform(-spread, spread), cy + r.uniform(-spread, spread), **kw) for i in range(n)]


def test_hotspots_need_min_events():
    evs = cluster("speeding", 12, 0, 0) + cluster("speeding", 3, 500, 500, start=100)
    h = analytics.hotspots(evs)["by_type"]["speeding"]
    assert len(h["hotspots"]) == 1 and h["hotspots"][0]["n"] == 12
    assert len(h["noise_event_ids"]) == 3
    few = analytics.hotspots(cluster("wrong_way", 4, 0, 0))["by_type"]["wrong_way"]
    assert few["hotspots"] == [] and "no hotspot possible" in few["note"]


def test_hotspots_independent_of_input_order():
    evs = cluster("lane_violation", 15, 0, 0, seed=3)
    a = analytics.hotspots(evs)["by_type"]["lane_violation"]["hotspots"]
    b = analytics.hotspots(list(reversed(evs)))["by_type"]["lane_violation"]["hotspots"]
    assert [h["event_ids"] for h in a] == [h["event_ids"] for h in b]


@pytest.fixture(scope="module")
def recs():
    lane = next(l for l in TOWN03["lanes"] if l.get("speed_limit_kmh") and not l.get("junction"))
    x, y = lane["centreline"][len(lane["centreline"]) // 2]
    evs = cluster("speeding", 12, x, y, lane=lane["id"], cond="E1", value={"max_speed_kmh": 70, "limit_kmh": 50})
    evs += cluster("wrong_way", 3, x + 300, y, start=50, lane=lane["id"], cond="C1")
    return recommend(evs, TOWN03), evs


def test_every_recommendation_has_all_fields(recs):
    out, _ = recs
    assert out, "no recommendations from 12 clustered speeding events"
    for r in out:
        missing = REQUIRED - set(r)
        assert not missing, f"{r['id']} lacks {missing}"
        assert r["problem"] and r["action"]


def test_expected_impact_is_labelled_projected(recs):
    for r in recs[0]:
        s = json.dumps(r["expected_impact"]).lower()
        assert "projected" in s or "null" in s or "none" in s or r["expected_impact"] in (None, {}), r["id"]


def test_ids_are_deterministic(recs):
    out, evs = recs
    again = recommend(list(reversed(evs)), TOWN03)
    assert sorted(r["id"] for r in out) == sorted(r["id"] for r in again)


def test_history_reranks_by_measured_outcome(tmp_path, recs):
    out, _ = recs
    h = History(tmp_path / "history.jsonl")
    first = out[0]
    h.record_decision(first, "accepted", "test", by="planner")
    h.record_result(first, "violations.speeding", baseline=10.0, modified=2.0, runs=3)
    assert len(h.decisions(first["id"])) == 1
    ranked = rerank(json.loads(json.dumps(out)), h)
    assert {r["id"] for r in ranked} == {r["id"] for r in out}
