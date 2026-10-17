"""Evidence of live events: POST /api/live/events/{id}/evidence (snapshot JPEG / WebM clip from the live
pipeline) -> S3 + evidence.<kind>_key on the event, served as a signed URL, and kept when the event closes."""

import time

from .test_api import SERVICE

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 200 + b"\xff\xd9"
WEBM = b"\x1a\x45\xdf\xa3" + b"\0" * 200


def violation(eid: str, sid: str) -> dict:
    return {"event_id": eid, "type": "no_parking", "track_ids": [1], "cls": "car", "start_s": 932.8, "start_frame": 10338,
            "flag_s": 942.9, "flag_frame": 10648, "end_s": None, "end_frame": None, "lane_id": None, "zone_id": "z_test",
            "x": 272.6, "y": -198.2, "value": {}, "tags": ["provisional"], "confidence": 0.0, "status": "flagged",
            "evidence": {}, "condition": "B3", "kind": "violation", "session_id": sid}


def post_evidence(client, eid, kind, body, headers=SERVICE):
    ctype = "image/jpeg" if kind == "snapshot" else "video/webm"
    return client.post(f"/api/live/events/{eid}/evidence?kind={kind}", headers={**headers, "Content-Type": ctype}, content=body)


def test_evidence_needs_the_service_token_a_known_kind_the_right_file_and_the_event(client, auth):
    sid = f"evid_{time.time_ns()}"
    eid = f"{sid}-0001"
    assert post_evidence(client, eid, "snapshot", JPEG, auth("operator")).status_code == 403
    assert post_evidence(client, eid, "snapshot", JPEG).status_code == 404  # the event's own post has not landed
    assert client.post("/api/events", headers=SERVICE, json=violation(eid, sid)).status_code == 201
    assert post_evidence(client, eid, "video", WEBM).status_code == 422
    assert post_evidence(client, eid, "snapshot", WEBM).status_code == 422  # not a JPEG
    assert post_evidence(client, eid, "clip", JPEG).status_code == 422      # not a WebM


def test_snapshot_and_clip_are_stored_served_and_kept_when_the_event_closes(client, auth):
    sid = f"evid_{time.time_ns()}"
    eid = f"{sid}-0001"
    assert client.post("/api/events", headers=SERVICE, json=violation(eid, sid)).status_code == 201
    r = post_evidence(client, eid, "snapshot", JPEG)
    assert r.status_code == 200, r.text
    ev = r.json()["evidence"]
    assert ev["snapshot_key"] == f"evidence/{sid}/{eid}_snap.jpg" and ev["snapshot_url"].startswith("/api/files/")
    f = client.get(ev["snapshot_url"])
    assert f.status_code == 200 and f.content == JPEG and f.headers["content-type"] == "image/jpeg"

    # the close replaces the event: the snapshot must stay, and a clip arriving after the close is added
    final = {**violation(eid, sid), "tags": [], "confidence": 0.9, "end_s": 960.0, "end_frame": 11200}
    r = client.put(f"/api/live/events/{eid}", headers=SERVICE, json=final)
    assert r.status_code == 200 and r.json()["evidence"]["snapshot_key"] == ev["snapshot_key"]
    r = post_evidence(client, eid, "clip", WEBM)
    assert r.status_code == 200
    got = client.get(f"/api/events/{eid}", headers=auth("officer")).json()
    assert got["confidence"] == 0.9 and got["evidence"]["clip_key"] == f"evidence/{sid}/{eid}.webm"
    assert got["evidence"]["snapshot_key"] == ev["snapshot_key"]
    f = client.get(got["evidence"]["clip_url"])
    assert f.status_code == 200 and f.content == WEBM and f.headers["content-type"] == "video/webm"
