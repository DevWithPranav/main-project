"""Evidence of live events: the recorder's snapshot and clip around the flag (evidence.py), and the backend
client's upload with retry while the event is not stored yet (backend_client.post_evidence).

Run:  venv/Scripts/python.exe -m pytest services/live/tests/test_evidence.py -q
"""

import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "live"))
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))

import backend_client  # noqa: E402
from backend_client import BackendClient  # noqa: E402
from evidence import EvidenceRecorder  # noqa: E402
from pipeline import STATE_BGR, draw_boxes, frame_jpegs  # noqa: E402

W, H = 320, 180
BOX = (160.0, 90.0, 60.0, 40.0)  # cx, cy, w, h in source pixels


def frames(rec, t0, t1, dt=0.1, state="checking"):
    img = np.full((H, W, 3), 90, np.uint8)
    t = t0
    while t <= t1 + 1e-9:
        boxes = [(*BOX, "3 car", state)]
        jpgs = frame_jpegs(img, boxes, W)
        rec.add_frame(round(t, 3), jpgs["raw"], boxes, [3], 1.0)
        t += dt


def wait(cond, secs=10.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_snapshot_at_the_flag_and_clip_around_it_with_the_vehicle_marked(tmp_path):
    ready = []
    rec = EvidenceRecorder(tmp_path, draw_boxes, on_ready=lambda *a: ready.append(a), pre_s=6.0, post_s=4.0)
    frames(rec, 0.0, 10.0)
    assert rec.flag("s-0001", 10.0, [3]) is True
    assert rec.flag("s-0001", 10.0, [3]) is False  # once per event (its open and its close both call it)
    assert wait(lambda: len(ready) == 1)
    assert ready[0][:2] == ("s-0001", "snapshot")
    snap = cv2.imread(str(ready[0][2]))
    x0 = int(BOX[0] - BOX[2] / 2)
    # the box edge is drawn in the flagged colour, though the car was only "checking" when the frame came in
    assert np.abs(snap[int(BOX[1]), x0].astype(int) - STATE_BGR["flagged"]).max() < 60
    frames(rec, 10.1, 13.8)
    time.sleep(0.3)
    assert len(ready) == 1  # the clip waits until the stream is post_s past the flag
    frames(rec, 13.9, 14.2)
    assert wait(lambda: len(ready) == 2)
    eid, kind, path = ready[1]
    assert (eid, kind, path.name) == ("s-0001", "clip", "s-0001.webm")
    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    assert 95 <= n <= 105  # 4 s .. 14 s at 10 frames/s
    rec.finish()
    assert rec.made == {"snapshot": 1, "clip": 1} and rec.failed == 0


def test_finish_writes_the_clips_still_waiting_and_late_flags_clip_at_once(tmp_path):
    ready = []
    rec = EvidenceRecorder(tmp_path, draw_boxes, on_ready=lambda *a: ready.append(a))
    assert rec.flag("x", 1.0, [3]) is False  # no frame yet
    frames(rec, 0.0, 20.0)
    rec.flag("late", 12.0, [3])   # flagged at its close: the frames after the flag are already buffered
    assert wait(lambda: sorted(k for _, k, _ in ready) == ["clip", "snapshot"])
    rec.flag("open", 19.0, [3])   # the stream stops 1 s after this flag
    rec.finish()
    assert sorted((e, k) for e, k, _ in ready) == [("late", "clip"), ("late", "snapshot"), ("open", "clip"), ("open", "snapshot")]


class Handler(BaseHTTPRequestHandler):
    seen: list = []
    not_found_left = 2

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.seen.append((self.path, self.headers["Content-Type"], body))
        code = 200
        if Handler.not_found_left > 0:  # the event's own post has not landed yet
            Handler.not_found_left -= 1
            code = 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


def test_upload_is_retried_until_the_event_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(backend_client, "EVIDENCE_RETRY_S", 0.05)
    Handler.seen, Handler.not_found_left = [], 2
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    clip = tmp_path / "s-0001.webm"
    clip.write_bytes(b"\x1a\x45\xdf\xa3webm")
    bc = BackendClient(f"http://127.0.0.1:{srv.server_port}")
    try:
        bc.post_evidence("s-0001", "clip", clip)
        assert wait(lambda: bc.evidence_ok == 1)
        assert len(Handler.seen) == 3 and bc.evidence_failed == 0
        path, ctype, body = Handler.seen[-1]
        assert path == "/api/live/events/s-0001/evidence?kind=clip" and ctype == "video/webm" and body == clip.read_bytes()
    finally:
        bc.close(2.0)
        srv.shutdown()
