"""Live camera frames (POST /api/live/frame -> MJPEG GET /api/live/video) and the raw / annotated session videos."""

import os
import threading
import time

import cv2
import httpx
import numpy as np

SERVICE = {"Authorization": "Bearer dev-service-token"}
FLIGHT = os.getenv("TEST_FLIGHT", "20261009_201727")


def jpeg(value: int) -> bytes:
    return cv2.imencode(".jpg", np.full((54, 96, 3), value, np.uint8))[1].tobytes()


def test_frame_post_checks(client, auth):
    q = {"session_id": "cam_checks", "kind": "raw"}
    assert client.post("/api/live/frame", params=q, headers=auth("operator"), content=jpeg(10)).status_code == 403
    assert client.post("/api/live/frame", params=q, headers=SERVICE, content=b"not a jpeg").status_code == 422
    assert client.post("/api/live/frame", params={**q, "kind": "x"}, headers=SERVICE, content=jpeg(10)).status_code == 422
    assert client.get("/api/live/video", params=q).status_code == 401
    assert client.get("/api/live/video", params={**q, "token": "junk"}).status_code == 401


def test_frames_stream_as_mjpeg(client, auth, server):
    sid = f"cam_{time.time_ns()}"
    for kind, v in (("raw", 50), ("annotated", 60)):
        r = client.post("/api/live/frame", params={"session_id": sid, "kind": kind, "t_s": 1.5}, headers=SERVICE, content=jpeg(v))
        assert r.status_code == 204
    src = next(s for s in client.get("/api/live/sources", headers=auth("officer")).json() if s["session_id"] == sid)
    assert sorted(src["kinds"]) == ["annotated", "raw"] and src["t_s"] == 1.5

    tok = auth("officer")["Authorization"][7:]
    later = jpeg(200)
    got: list[bytes] = []
    with httpx.stream("GET", f"{server}/api/live/video", params={"session_id": sid, "kind": "raw", "token": tok}, timeout=10) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("multipart/x-mixed-replace")
        # a frame posted while the page watches reaches it
        threading.Timer(0.3, lambda: httpx.post(f"{server}/api/live/frame", params={"session_id": sid, "kind": "raw"},
                                                headers=SERVICE, content=later)).start()
        buf = b""
        for chunk in r.iter_bytes():
            buf += chunk
            while b"\r\n\r\n" in buf:
                head, rest = buf.split(b"\r\n\r\n", 1)
                n = int(head.split(b"Content-Length: ")[1].split(b"\r\n")[0])
                if len(rest) < n:
                    break
                got.append(rest[:n])
                buf = rest[n + 2:]
            if len(got) >= 2:
                break
    assert got[0] == jpeg(50) and got[1] == later  # the newest frame first, then the next one


def test_session_video_kinds(client, auth, imported):
    h = auth("officer")
    assert client.get(f"/api/sessions/{FLIGHT}/video", headers=h, params={"kind": "nope"}).status_code == 422
    for kind in ("raw", "annotated"):
        r = client.get(f"/api/sessions/{FLIGHT}/video", headers=h, params={"kind": kind})
        assert r.status_code == 200
        d = r.json()
        assert d["status"] in ("none", "ready", "encoding")
        if d["status"] == "none":
            assert "available" in d
    assert client.post(f"/api/sessions/{FLIGHT}/video", headers=h, params={"kind": "raw"}).status_code == 403  # OPERATOR+
