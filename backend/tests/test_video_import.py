"""Real-footage sessions (Expected_Output 3: external videos are a source like CARLA flights)."""

import os

from backend.app import config

CLIP = os.getenv("TEST_VIDEO_CLIP", "uavdt_M0603")


def test_import_real_clip(client, auth):
    if not (config.VIDEO_RESULTS_DIR / CLIP).is_dir():
        import pytest
        pytest.skip(f"{CLIP} not processed on this machine")
    r = client.post("/api/sessions/import", headers=auth("operator"), json={"flight": CLIP, "source": "video"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["source"] == "video" and s["town"] is None and s["name"] == f"Real video {CLIP}"
    imp = s["import"]
    assert imp["n_imported"] > 0 and imp["n_rejected"] == 0 and imp["n_tracks"] > 0
    ev = client.get("/api/events", headers=auth("officer"), params={"session_id": CLIP}).json()
    assert ev["total"] == imp["n_imported"]
    assert client.post("/api/sessions/import", headers=auth("operator"), json={"flight": CLIP, "source": "drone"}).status_code == 422
    assert client.post("/api/sessions/import", headers=auth("operator"), json={"flight": "no_such_clip", "source": "video"}).status_code == 404
