"""Road-surface anomaly API tests (Build Plan M9) against the real PostGIS / Redis / S3.

Two synthetic processed flights in a temporary results folder (same town, 1 h apart), each with an
anomalies_*/anomalies.json as ml/pothole/detect_anomalies.py writes it, plus snapshots.

Usage:
    venv\\Scripts\\python.exe -m pytest backend/tests/test_anomalies.py -v
"""

import io
import json
import zipfile

import cv2
import numpy as np
import pytest

from backend.app import config

FA, FB = "20261010_090000", "20261010_100000"


def anomaly(eid: str, type_: str, x: float, y: float, score: float, band: str, area: float, snap: bool = True) -> dict:
    e = {"kind": "anomaly", "event_id": eid, "type": type_, "t_s": 12.5, "frame": 125, "x": x, "y": y,
         "lane_id": "r1_s0_l1", "zone_id": None, "severity_score": score, "severity_band": band, "area_sq_m": area,
         "recurrence_count": 1, "tags": [], "confidence": 0.8, "status": "flagged",
         "evidence": {"frame": 125, "telemetry": {"n_frames": 5}}}
    if snap:
        e["evidence"]["snapshot"] = f"snapshots/{eid}.jpg"
    return e


def write_flight(results, recordings, flight: str, events: list[dict]) -> None:
    adir = results / flight / config.TRACKER_RUN / "anomalies_20261010_120000"
    (adir / "snapshots").mkdir(parents=True)
    for e in events:
        if (e.get("evidence") or {}).get("snapshot"):
            cv2.imwrite(str(adir / e["evidence"]["snapshot"]), np.full((40, 40, 3), 90, np.uint8))
    (adir / "anomalies.json").write_text(json.dumps({"events": events, "meta": {}}), encoding="utf-8")
    (recordings / flight).mkdir(parents=True)
    (recordings / flight / "metadata.json").write_text(json.dumps({"map": "Carla/Maps/Town05"}))


@pytest.fixture(scope="module")
def flights(tmp_path_factory):
    root = tmp_path_factory.mktemp("m9")
    results, recordings = root / "results", root / "recordings"
    write_flight(results, recordings, FA, [
        anomaly(f"{FA}-anom-0000", "pothole", 10.0, 10.0, 0.8, "high", 1.2),
        anomaly(f"{FA}-anom-0001", "crack", 50.0, 50.0, 0.3, "low", 0.6)])
    bad = anomaly(f"{FB}-anom-0002", "pothole", 0.0, 0.0, 1.5, "high", 1.0, snap=False)  # severity > 1: schema error
    write_flight(results, recordings, FB, [
        anomaly(f"{FB}-anom-0000", "pothole", 11.0, 10.5, 0.7, "high", 1.0),  # same pothole as FA, 1.1 m away
        anomaly(f"{FB}-anom-0001", "waterlogging", 100.0, 0.0, 0.5, "medium", 12.0), bad])
    (results / "20261010_110000" / config.TRACKER_RUN).mkdir(parents=True)  # a flight with nothing processed
    old = config.RESULTS_DIR, config.RECORDINGS_DIR
    config.RESULTS_DIR, config.RECORDINGS_DIR = results, recordings
    yield results
    config.RESULTS_DIR, config.RECORDINGS_DIR = old


@pytest.fixture(scope="module")
def imported_ab(client, auth, flights):
    out = {}
    for f in (FA, FB):
        r = client.post("/api/sessions/import", headers=auth("operator"), json={"flight": f})
        assert r.status_code == 200, r.text
        out[f] = r.json()
    return out


def test_import_anomalies_only_flight(imported_ab):
    a, b = imported_ab[FA], imported_ab[FB]
    assert a["town"] == "Town05" and a["n_events"] == 2
    ia = a["import"]
    assert ia["violations_dir"] is None and ia["anomalies_dir"].endswith("anomalies_20261010_120000")
    assert ia["n_anomalies"] == 2 and ia["n_anomalies_rejected"] == 0 and ia["snapshots"] == 2  # snapshots_uploaded is 0 when S3 has them already
    ib = b["import"]
    assert ib["n_anomalies"] == 2 and ib["n_anomalies_rejected"] == 1 and b["n_events"] == 2
    assert ib["n_in_file"] == ib["n_imported"] + ib["n_rejected"]
    assert any("anom-0002" in err and "severity_score" in err for err in ib["errors"])


def test_nothing_to_import_is_404(client, auth, flights):
    r = client.post("/api/sessions/import", headers=auth("operator"), json={"flight": "20261010_110000"})
    assert r.status_code == 404
    r = client.post("/api/sessions/import", headers=auth("operator"), json={"flight": FA, "include_anomalies": False})
    assert r.status_code == 404  # no violations and anomalies switched off
    r = client.post("/api/sessions/import", headers=auth("operator"), json={"flight": FA, "anomalies_dir": "nope"})
    assert r.status_code == 404
    r = client.post("/api/sessions/import", headers=auth("operator"), json={"flight": FA, "anomalies_dir": "../x"})
    assert r.status_code == 422


def test_recurrence_across_sessions(client, auth, imported_ab):
    h = auth("officer")
    b0 = client.get(f"/api/events/{FB}-anom-0000", headers=h).json()
    assert b0["recurrence_count"] == 2  # FA saw the same pothole 1.1 m away, an hour earlier
    assert client.get(f"/api/events/{FB}-anom-0001", headers=h).json()["recurrence_count"] == 1
    assert client.get(f"/api/events/{FA}-anom-0000", headers=h).json()["recurrence_count"] == 1


def test_list_sorted_by_severity_and_filters(client, auth, imported_ab):
    h = auth("planner")
    r = client.get("/api/anomalies", headers=h).json()
    scores = [e["severity_score"] for e in r["items"]]
    assert r["total"] == 4 and scores == sorted(scores, reverse=True)
    assert {e["type"] for e in client.get("/api/anomalies?severity_band=high", headers=h).json()["items"]} == {"pothole"}
    assert client.get("/api/anomalies?min_severity=0.6", headers=h).json()["total"] == 2
    assert client.get(f"/api/anomalies?session_id={FA}&type=crack", headers=h).json()["total"] == 1
    ev = client.get("/api/events?kind=anomaly", headers=h).json()
    assert ev["total"] == 4
    snap = next(e for e in ev["items"] if e["event_id"] == f"{FA}-anom-0000")["evidence"]["snapshot_url"]
    assert client.get(snap).status_code == 200


def test_status_roles_and_history(client, auth, imported_ab):
    eid = f"{FA}-anom-0000"
    for user in ("officer", "operator", "planner"):
        assert client.patch(f"/api/anomalies/{eid}", headers=auth(user), json={"status": "reviewed"}).status_code == 403
    assert client.patch(f"/api/anomalies/{eid}", headers=auth("maintenance"), json={"status": "fixed"}).status_code == 422
    assert client.patch("/api/anomalies/nope", headers=auth("maintenance"), json={"status": "reviewed"}).status_code == 404
    r = client.patch(f"/api/anomalies/{eid}", headers=auth("maintenance"), json={"status": "reviewed"})
    assert r.status_code == 200 and r.json()["status"] == "reviewed"
    r = client.patch(f"/api/anomalies/{eid}", headers=auth("admin"),
                     json={"status": "work_order_issued", "note": "WO-17, crew B"})
    body = r.json()
    assert body["status"] == "work_order_issued"
    assert [(x["from_status"], x["to_status"], x["by"]) for x in body["status_history"]] == [
        ("flagged", "reviewed", "maintenance"), ("reviewed", "work_order_issued", "admin")]
    assert client.get(f"/api/anomalies/{eid}/history", headers=auth("officer")).json()[-1]["note"] == "WO-17, crew B"
    assert client.get("/api/anomalies?status=work_order_issued", headers=auth("officer")).json()["total"] == 1
    # the schema still accepts the stored event
    from backend.app.engine_bridge import event_errors
    e = client.get(f"/api/events/{eid}", headers=auth("officer")).json()
    assert event_errors([{k: v for k, v in e.items() if k not in ("occurred_at", "evidence")}]) == []


def test_reimport_keeps_status(client, auth, imported_ab):
    eid = f"{FA}-anom-0001"
    client.patch(f"/api/anomalies/{eid}", headers=auth("maintenance"), json={"status": "repaired"})
    r = client.post("/api/sessions/import", headers=auth("operator"), json={"flight": FA})
    assert r.status_code == 200
    assert client.get(f"/api/events/{eid}", headers=auth("officer")).json()["status"] == "repaired"


def test_anomaly_review_role(client, auth, imported_ab):
    eid = f"{FB}-anom-0001"
    assert client.post(f"/api/events/{eid}/review", headers=auth("officer"), json={"outcome": "confirmed"}).status_code == 403
    assert client.post(f"/api/events/{eid}/review", headers=auth("maintenance"), json={"outcome": "confirmed"}).status_code == 200


def test_inventory_groups_defects(client, auth, imported_ab):
    assert client.get("/api/anomalies/inventory", headers=auth("officer")).status_code == 403
    r = client.get("/api/anomalies/inventory", headers=auth("maintenance")).json()
    assert r["total"] == 3 and r["radius_m"] == 3.0  # the pothole seen twice is one defect
    pot = next(d for d in r["items"] if d["type"] == "pothole")
    assert pot["recurrence_count"] == 2 and pot["session_ids"] == [FA, FB]
    assert pot["latest_event_id"] == f"{FB}-anom-0000" and pot["max_severity_score"] == 0.8
    assert len(pot["severity_trend"]) == 2 and pot["snapshot_url"]
    assert pot["status"] == "work_order_issued"  # the most advanced status of its sightings
    scores = [d["severity_score"] for d in r["items"]]
    assert scores == sorted(scores, reverse=True)
    assert client.get("/api/anomalies/inventory?severity_band=medium", headers=auth("planner")).json()["total"] == 1
    one = client.get(f"/api/anomalies/{FA}-anom-0000", headers=auth("officer")).json()
    assert one["defect"]["recurrence_count"] == 2 and len(one["status_history"]) >= 2


def test_inventory_exports(client, auth, imported_ab):
    r = client.get("/api/anomalies/inventory?format=csv", headers=auth("maintenance"))
    assert r.status_code == 200 and r.headers["x-event-count"] == "3"
    lines = r.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("rank,defect_id,type") and len(lines) == 4
    r = client.get("/api/anomalies/inventory?format=xlsx", headers=auth("planner"))
    assert r.status_code == 200 and zipfile.is_zipfile(io.BytesIO(r.content))


def test_anomaly_stats(client, auth, imported_ab):
    r = client.get("/api/anomalies/stats", headers=auth("officer")).json()
    assert r["total"] == 4 and r["defects"] == 3
    assert r["by_type_band"]["pothole"]["high"] == 2
    assert sum(r["by_status"].values()) == 4
