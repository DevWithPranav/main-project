"""WebSocket catch-up (PRD 29.3): a socket opened after events were stored gets the last 60 s of them."""

import time

from websockets.sync.client import connect

import json

from .test_api import SERVICE, anomaly, ws_url


def test_ws_catch_up(client, auth, server):
    sid = f"catch_{time.time_ns()}"
    tok = auth("officer")["Authorization"][7:]
    ids = []
    for n in range(3):
        r = client.post("/api/events", headers=SERVICE, json=anomaly(f"{sid}_{n}", sid))
        assert r.status_code == 201, r.text
        ids.append(r.json()["event_id"])
    with connect(ws_url(server, f"token={tok}&session_id={sid}")) as ws:  # default window: 60 s
        got, marker = [], None
        while marker is None:
            m = json.loads(ws.recv(timeout=5))
            if m["type"] == "event":
                assert m["catch_up"] is True
                got.append(m["event"]["event_id"])
            elif m["type"] == "catch_up":
                marker = m
        assert got == ids and marker["n"] == 3 and marker["window_s"] == 60.0
    with connect(ws_url(server, f"token={tok}&session_id={sid}&catch_up_s=0")) as ws:  # none asked for
        time.sleep(0.5)
        try:
            ws.recv(timeout=1)
            raise AssertionError("expected nothing")
        except TimeoutError:
            pass
    with connect(ws_url(server, f"token={tok}&session_id={sid}_other")) as ws:  # other session: marker only
        m = json.loads(ws.recv(timeout=5))
        assert m["type"] == "catch_up" and m["n"] == 0
