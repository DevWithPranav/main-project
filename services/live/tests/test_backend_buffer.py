"""BackendClient buffers events while the backend / database is down and flushes them in order (PRD 29.2, Mode 5)."""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "live"))

import backend_client  # noqa: E402
from backend_client import BackendClient  # noqa: E402


class Flaky:
    """A backend whose event routes answer `status` until told otherwise; records what it stored."""

    def __init__(self):
        self.status = 503
        self.got: list[tuple[str, str]] = []
        self.stored: set[str] = set()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self, method):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                code = outer.status
                if code == 200 and method == "POST" and body.get("event_id") in outer.stored:
                    code = 409
                if code == 200:
                    outer.stored.add(body.get("event_id"))
                    outer.got.append((method, body.get("event_id")))
                self.send_response(code)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            do_POST = lambda self: self._do("POST")  # noqa: E731
            do_PUT = lambda self: self._do("PUT")  # noqa: E731

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"


def ev(n):
    return {"event_id": f"e{n}", "type": "illegal_u_turn"}


def wait(cond, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_buffers_while_down_and_flushes_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_client, "BACKOFF_S", (0.1, 0.1))
    srv = Flaky()
    spool = tmp_path / "spool.jsonl"
    c = BackendClient(srv.url, spool_path=spool)
    for n in range(3):
        c.post_event(ev(n), "open")
    assert wait(lambda: c.degraded and c.buffered == 3)
    assert spool.exists() and len(spool.read_text().splitlines()) == 3
    c.post_event(ev(0), "close")  # a later event queues behind the buffered ones
    srv.status = 200  # the backend comes back
    assert wait(lambda: not c.degraded and c.buffered == 0 and len(srv.got) == 4)
    assert srv.got == [("POST", "e0"), ("POST", "e1"), ("POST", "e2"), ("PUT", "e0")]
    assert wait(lambda: not spool.exists())
    c.close()


def test_spool_survives_a_restart_and_409_counts_as_delivered(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_client, "BACKOFF_S", (0.1, 0.1))
    srv = Flaky()
    spool = tmp_path / "spool.jsonl"
    c1 = BackendClient(srv.url, spool_path=spool)
    c1.post_event(ev(7), "open")
    assert wait(lambda: c1.buffered == 1)
    c1.close(timeout=0.2)  # stopped while the backend is still down
    assert spool.exists()
    srv.stored.add("e7")  # it had in fact arrived once before: a replay answers 409
    srv.status = 200
    c2 = BackendClient(srv.url, spool_path=spool)  # new run picks the spool up
    assert wait(lambda: c2.buffered == 0 and not spool.exists())
    assert c2.dropped == 0
    c2.close()


def test_bad_data_is_dropped_not_retried_forever(monkeypatch):
    monkeypatch.setattr(backend_client, "BACKOFF_S", (0.1,))
    srv = Flaky()
    srv.status = 422
    c = BackendClient(srv.url)
    c.post_event(ev(1), "open")
    assert wait(lambda: c.dropped == 1)
    assert c.buffered == 0 and not c.degraded
    c.close()


def test_replay_events_file_is_idempotent(tmp_path):
    import replay_events
    f = tmp_path / "events.jsonl"
    recs = [{"phase": "open", "event": {**ev(1), "status": "provisional"}}, {"phase": "close", "event": {**ev(1), "status": "confirmed"}},
            {"phase": "open", "event": {**ev(2), "status": "suppressed"}},  # never closed: not sent
            {"phase": "open", "event": {**ev(3), "status": "provisional"}}]
    f.write_text("".join(json.dumps(r) + "\n" for r in recs))
    srv = Flaky()
    srv.status = 503
    assert replay_events.replay(f, srv.url)["complete"] is False  # backend down: nothing lost, reported
    srv.status = 200
    res = replay_events.replay(f, srv.url)
    assert res["complete"] and res["records"] == 3 and res["delivered"] == 3
    again = replay_events.replay(f, srv.url)  # second run: POST answers 409 (fine), PUT replaces
    assert again["complete"] and again["refused"] == 0
    assert srv.got[:3] == [("POST", "e1"), ("PUT", "e1"), ("POST", "e3")]
