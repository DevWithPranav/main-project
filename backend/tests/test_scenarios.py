"""What-if scenarios (PRD 21.3, ml/planning/whatif.py) through the API: create, poll, compare."""

import time

import os

FLIGHT = os.getenv("TEST_FLIGHT", "20261009_201727")


def _wait(client, auth, sid, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = client.get(f"/api/scenarios/{sid}", headers=auth("planner")).json()
        if s["status"] != "running":
            return s
        time.sleep(2)
    raise AssertionError(f"scenario {sid} still running after {timeout} s")


def test_countermeasures(client, auth):
    cm = client.get("/api/scenarios/countermeasures", headers=auth("officer")).json()
    keys = {c["key"] for c in cm}
    assert {"speed_camera", "crossing_visibility", "no_stopping_enforcement"} <= keys
    assert all("targets" in c and "quantified" in c for c in cm)


def test_scenario_rules(client, auth, imported):
    body = {"session_id": FLIGHT, "name": "nothing"}
    assert client.post("/api/scenarios", headers=auth("officer"), json=body).status_code == 403
    assert client.post("/api/scenarios", headers=auth("planner"), json=body).status_code == 422  # nothing to simulate
    assert client.post("/api/scenarios", headers=auth("planner"),
                       json={**body, "countermeasure": "no_such_thing"}).status_code == 422
    assert client.post("/api/scenarios", headers=auth("planner"),
                       json={**body, "session_id": "nope", "countermeasure": "speed_camera"}).status_code == 404


def test_zone_scenario_replays_and_projects(client, auth, imported):
    ev = client.get("/api/events", headers=auth("planner"), params={"session_id": FLIGHT, "type": "zebra_crossing"}).json()["items"]
    x, y = ev[0]["x"], ev[0]["y"]
    box = [[x - 15, y - 15], [x + 15, y - 15], [x + 15, y + 15], [x - 15, y + 15]]
    r = client.post("/api/scenarios", headers=auth("planner"), json={
        "session_id": FLIGHT, "name": "no stopping around the crossing",
        "changes": {"zones": [{"type": "no_parking", "polygon": box}]},
        "countermeasure": "crossing_visibility", "area": box})
    assert r.status_code == 202, r.text
    s = _wait(client, auth, r.json()["id"])
    assert s["status"] == "done", s.get("error")
    rep, proj = s["result"]["replay"], s["result"]["projection"]
    print(f"\nscenario: replay {rep['baseline']['counted']} -> {rep['modified']['counted']} "
          f"(+{len(rep['added'])}/-{len(rep['removed'])}, {rep['tracks']} tracks, {rep['seconds']} s); "
          f"projection {proj['affected']['events']} affected, after {proj['projected_after']}")
    assert rep["label"].startswith("rule replay") and proj["label"] == "projected estimate"
    assert proj["affected"]["events"] >= 1 and proj["projected_after"] is not None  # crossing_visibility has a sourced factor
    listed = client.get("/api/scenarios", headers=auth("officer"), params={"session_id": FLIGHT}).json()
    assert any(x["id"] == s["id"] and "replay_counted" in x["summary"] for x in listed)


def test_bad_lane_fails_cleanly(client, auth, imported):
    r = client.post("/api/scenarios", headers=auth("planner"), json={
        "session_id": FLIGHT, "name": "typo", "changes": {"lane_overrides": [{"lane_id": "r99999_*", "speed_limit_kmh": 20}]}})
    s = _wait(client, auth, r.json()["id"])
    assert s["status"] == "failed" and "matches no lane" in s["error"]
