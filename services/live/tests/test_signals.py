"""Traffic lights in the live pipeline: transport (hello + never-dropped "signal" messages), the replay
source's schedule, the backend client's merged posts, and the replay timelines on the session time axis.

Run:  venv/Scripts/python.exe -m pytest services/live/tests/test_signals.py -q
"""

import csv
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "live"))
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))

import replay_source  # noqa: E402
from backend_client import BackendClient  # noqa: E402
from signals import LiveSignalLog, flight_timelines, live_timelines  # noqa: E402
from transport import FrameReceiver, FrameSender  # noqa: E402

LIGHTS = [{"id": "7", "actor_id": 70, "stop_lines": [{"id": "tl_7_0", "signal_id": "7", "dir": [1, 0], "line": [[0, -1.75], [0, 1.75]]}]},
          {"id": "8", "actor_id": 80, "stop_lines": [{"id": "tl_8_0", "signal_id": "8", "dir": [0, 1], "line": [[-1.75, 0], [1.75, 0]]}]}]


def wait(cond, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_signals_go_through_the_transport_and_are_never_dropped():
    rx = FrameReceiver("127.0.0.1", 0, mode="latest")
    hello = {"type": "hello", "width": 4, "height": 4, "map": "Town05",
             "signals": {"lights": LIGHTS, "states": [[100, 5.0, "7", "Green"], [100, 5.0, "8", "Red"]]}}
    tx = FrameSender("127.0.0.1", rx.port, hello, queue=1)
    try:
        for i in range(40):  # far more frames than the queue holds: frames get dropped, light changes must not
            tx.send({"type": "frame", "frame": i, "carla_frame": 100 + i, "sim_time": 5.0 + i * 0.05, "pose": [0] * 6, "capture_wall": time.time()}, b"x")
            if i % 10 == 5:
                tx.send_control({"type": "signal", "changes": [[100 + i, 5.0 + i * 0.05, "7", "Red" if i % 20 == 5 else "Green"]]})
        got = []
        assert wait(lambda: (got.extend(rx.take_signals()) or True) and sum(len(m["changes"]) for m in got) >= 6)
        assert got[0]["lights"] == LIGHTS and got[0]["changes"][0] == [100, 5.0, "7", "Green"]  # from the hello
        changes = [c for m in got[1:] for c in m["changes"]]
        assert [c[0] for c in changes] == [105, 115, 125, 135]
        assert rx.hello["signals"]["lights"] == LIGHTS
    finally:
        tx.close()
        rx.close()


def test_a_reconnect_sends_the_current_states_in_the_hello():
    rx = FrameReceiver("127.0.0.1", 0)
    tx = FrameSender("127.0.0.1", rx.port, {"type": "hello"}, queue=2)
    tx.set_hello({"type": "hello", "signals": {"lights": LIGHTS, "states": [[200, 9.0, "7", "Yellow"]]}})
    tx.send({"type": "frame", "frame": 0, "carla_frame": 200, "sim_time": 9.0, "pose": [0] * 6, "capture_wall": time.time()}, b"x")
    try:
        assert wait(lambda: rx.hello is not None)
        assert rx.hello["signals"]["states"] == [[200, 9.0, "7", "Yellow"]]
    finally:
        tx.close()
        rx.close()


def test_the_light_list_can_follow_a_plain_hello():
    """Live run 2026-10-14: the sender connected before the light list was in its hello. The list then comes as the
    first "signal" message; wait_signals lets the pipeline pick it up before it builds the engine."""
    rx = FrameReceiver("127.0.0.1", 0)
    tx = FrameSender("127.0.0.1", rx.port, {"type": "hello", "map": "Town05"}, queue=2)
    try:
        tx.send_control({"type": "signal", "lights": LIGHTS, "changes": [[300, 15.0, "7", "Green"]]})
        assert wait(lambda: rx.hello is not None)
        assert "signals" not in rx.hello
        assert rx.wait_signals(5.0)
        (m,) = rx.take_signals()
        assert m["lights"] == LIGHTS and m["changes"] == [[300, 15.0, "7", "Green"]]
    finally:
        tx.close()
        rx.close()


def test_replay_source_starts_from_the_state_at_the_first_frame():
    data = {"lights": LIGHTS, "changes": [[90, 4.5, "7", "Green"], [90, 4.5, "8", "Red"], [120, 6.0, "7", "Yellow"],
                                          [150, 7.5, "7", "Red"], [150, 7.5, "8", "Green"]]}
    states, later = replay_source.signal_schedule(data, 130)
    assert sorted(states) == sorted([[120, 6.0, "7", "Yellow"], [90, 4.5, "8", "Red"]])
    assert later == [[150, 7.5, "7", "Red"], [150, 7.5, "8", "Green"]]


class Backend:
    def __init__(self, fail_first: int = 0):
        self.got: list[dict] = []
        self.fail = fail_first
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                code = 200
                if self.path == "/api/live/signals":
                    if outer.fail > 0:
                        outer.fail -= 1
                        code = 503
                    else:
                        outer.got.append(body)
                self.send_response(code)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"


def test_backend_client_keeps_every_change_through_a_failed_post():
    be = Backend(fail_first=1)
    c = BackendClient(be.url)
    try:
        c.post_signals("s1", LIGHTS, [[1, 0.05, "7", "Green"]])
        time.sleep(0.3)  # the first post fails; these merge behind the retried one
        c.post_signals("s1", None, [[2, 0.10, "7", "Yellow"]])
        c.post_states([{"session_id": "s1", "track_id": 1}])  # vehicle batches never replace a light change
        c.post_signals("s1", None, [[3, 0.15, "7", "Red"]])
        assert wait(lambda: sum(len(b["changes"]) for b in be.got) == 3)
        assert be.got[0]["lights"] == LIGHTS
        assert [ch[3] for b in be.got for ch in b["changes"]] == ["Green", "Yellow", "Red"]
    finally:
        c.close()
        be.srv.shutdown()


def test_live_log_looks_states_up_by_sim_time():
    log = LiveSignalLog()
    for f in range(10):
        log.add_frame(f, 1.0 + f * 0.1)
    log.add_change("7", 1.0, "Green")
    log.add_change("7", 1.45, "Red")  # between frames 4 (1.4 s) and 5 (1.5 s)
    assert log.state_at("7", 4) == ("Green", 0)
    assert log.state_at("7", 5) == ("Red", 5)
    assert log.state_at("7", 9) == ("Red", 5)
    assert log.state_at("8", 5) is None


def test_flight_timelines_are_on_the_recording_time_axis(tmp_path):
    with open(tmp_path / "frame_times.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "time_s", "carla_frame", "sim_time"])
        for i in range(5):  # frames every 2 ticks from tick 100; the wall clock starts at 0.5 s
            w.writerow([i, 0.5 + i * 0.1, 100 + 2 * i, 10.0 + i * 0.1])
    (tmp_path / "traffic_lights.json").write_text(json.dumps({"lights": LIGHTS, "changes": [
        [95, 9.75, "7", "Green"], [103, 10.15, "7", "Red"], [104, 10.2, "7", "Red"], [120, 11.0, "7", "Green"]]}))
    out = flight_timelines(tmp_path)
    assert out["time_base"] == "time_s" and out["lights"] == LIGHTS
    # before the first frame -> the first frame; tick 103 -> the frame of tick 104; the repeat dropped; after the end dropped
    assert out["timelines"] == {"7": [[0.5, "Green"], [0.7, "Red"]]}
    assert flight_timelines(tmp_path / "nothing") is None
    live = live_timelines({"lights": LIGHTS, "changes": [[1, 3.0, "8", "Red"], [5, 4.0, "8", "Green"]]})
    assert live["timelines"] == {"8": [[3.0, "Red"], [4.0, "Green"]]} and live["time_base"] == "sim_time"
