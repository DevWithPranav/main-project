"""Report history (PRD 13.1 `reports`, 14.1): every export is recorded and its file kept in S3 for re-download."""

import os

FLIGHT = os.getenv("TEST_FLIGHT", "20261009_201727")


def test_export_is_recorded_and_downloadable(client, auth, imported):
    r = client.get("/api/export", headers=auth("officer"), params={"format": "csv", "session_id": FLIGHT})
    assert r.status_code == 200 and r.headers["x-report-id"]
    rid = r.headers["x-report-id"]
    items = client.get("/api/reports", headers=auth("planner"), params={"session_id": FLIGHT}).json()["items"]
    rep = next(i for i in items if i["id"] == rid)
    assert rep["format"] == "csv" and rep["by"] == "officer" and rep["role"] == "OFFICER" and rep["stored"]
    assert rep["size_bytes"] == len(r.content) and rep["n_events"] == int(r.headers["x-event-count"])
    assert rep["filters"] == {"session_id": FLIGHT} and rep["filename"].endswith(".csv")
    d = client.get(f"/api/reports/{rid}", headers=auth("maintenance"))
    assert d.status_code == 200 and d.content == r.content
    assert d.headers["content-type"].startswith("text/csv") and rep["filename"] in d.headers["content-disposition"]
    assert client.get("/api/reports/nope", headers=auth("officer")).status_code == 404
    assert client.get(f"/api/reports/{rid}").status_code == 401


def test_generate_formats_and_filters(client, auth, imported):
    for fmt in ("pdf", "xlsx", "geojson"):
        r = client.post("/api/reports/generate", headers=auth("planner"),
                        params={"format": fmt, "session_id": FLIGHT, "status": "flagged,needs_review"})
        assert r.status_code == 201, r.text
        m = r.json()
        assert m["format"] == fmt and m["by"] == "planner" and m["filters"]["status"] == "flagged,needs_review"
        body = client.get(m["download_url"], headers=auth("officer")).content
        assert len(body) == m["size_bytes"] > 0
    assert body.startswith(b"{")  # geojson
    assert client.post("/api/reports/generate", headers=auth("planner"), params={"format": "doc"}).status_code == 422
    lst = client.get("/api/reports", headers=auth("officer"), params={"format": "pdf", "by": "planner", "limit": 1}).json()
    assert lst["total"] >= 1 and len(lst["items"]) == 1 and lst["items"][0]["format"] == "pdf"
    allr = client.get("/api/reports", headers=auth("officer")).json()["items"]
    assert [i["at"] for i in allr] == sorted((i["at"] for i in allr), reverse=True)  # newest first


def test_report_actions_audited(client, auth, imported):
    client.get("/api/reports", headers=auth("officer"))
    rid = client.post("/api/reports/generate", headers=auth("officer"), params={"format": "csv", "session_id": FLIGHT}).json()["id"]
    client.get(f"/api/reports/{rid}", headers=auth("officer"))
    got = {(i["action"], i["resource_id"]) for i in
           client.get("/api/audit", headers=auth("admin"), params={"user_id": "officer", "limit": 200}).json()["items"]}
    assert ("report.download", rid) in got and ("report.generate.csv", FLIGHT) in got and ("reports.list", None) in got
