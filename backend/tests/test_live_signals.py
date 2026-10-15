"""Traffic lights of a live CARLA source: POST /api/live/signals -> Redis + WebSocket (a socket that connects
mid-run gets every light's current state first), and GET /api/sessions/{id}/signals for the replay."""

import json
import time

from websockets.sync.client import connect

from backend.app import config

from .test_api import SERVICE, anomaly, ws_url

LIGHTS = [{"id": "7", "actor_id": 70, "stop_lines": [{"id": "tl_7_0", "signal_id": "7", "dir": [1, 0], "line": [[0, -1.75], [0, 1.75]]}]},
          {"id": "8", "actor_id": 80, "stop_lines": [{"id": "tl_8_0", "signal_id": "8", "dir": [0, 1], "line": [[-1.75, 0], [1.75, 0]]}]}]


def recv_signals(ws, n=1, secs=5.0):
    out, end = [], time.time() + secs
    while len(out) < n and time.time() < end:
        try:
            m = json.loads(ws.recv(timeout=max(0.1, end - time.time())))
        except TimeoutError:
            break
        if m["type"] == "signals":
            out.append(m)
    return out


def test_live_signals_need_the_service_token_and_valid_changes(client, auth):
    body = {"session_id": "sig_x", "lights": LIGHTS, "changes": [[1, 0.1, "7", "Green"]]}
    assert client.post("/api/live/signals", headers=auth("operator"), json=body).status_code == 403
    assert client.post("/api/live/signals", headers=SERVICE, json={"session_id": "sig_x", "changes": [[1, "7"]]}).status_code == 422
    assert client.post("/api/live/signals", headers=SERVICE, json={"session_id": "sig_x"}).status_code == 422


def test_live_signals_reach_the_socket_and_a_late_socket_gets_the_current_state(client, auth, server):
    sid = f"sig_{time.time_ns()}"
    tok = auth("officer")["Authorization"][7:]
    r = client.post("/api/live/signals", headers=SERVICE, json={"session_id": sid, "lights": LIGHTS,
                                                                   "changes": [[100, 5.0, "7", "Green"], [100, 5.0, "8", "Red"]]})
    assert r.status_code == 200 and r.json() == {"lights": 2, "changes": 2}
    with connect(ws_url(server, f"token={tok}&session_id={sid}&catch_up_s=0")) as ws:
        (snap,) = recv_signals(ws)
        assert snap["snapshot"] is True and snap["lights"] == LIGHTS
        assert sorted(snap["changes"]) == [[5.0, "7", "Green"], [5.0, "8", "Red"]]
        # a change while connected; an older one sent late must not overwrite the newer state
        client.post("/api/live/signals", headers=SERVICE, json={"session_id": sid, "changes": [[160, 8.0, "7", "Yellow"]]})
        client.post("/api/live/signals", headers=SERVICE, json={"session_id": sid, "changes": [[140, 7.0, "7", "Red"]]})
        got = recv_signals(ws, 2)
        assert [m["changes"] for m in got] == [[[8.0, "7", "Yellow"]], [[7.0, "7", "Red"]]]
        assert all(m["session_id"] == sid and m["lights"] is None for m in got)
    with connect(ws_url(server, f"token={tok}&session_id={sid}&catch_up_s=0")) as ws:  # recent history, in time order
        (snap,) = recv_signals(ws)
        assert sorted(snap["changes"]) == [[5.0, "7", "Green"], [5.0, "8", "Red"], [7.0, "7", "Red"], [8.0, "7", "Yellow"]]
    for k in range(12):  # only the last few changes per light are kept
        client.post("/api/live/signals", headers=SERVICE, json={"session_id": sid, "changes": [[200 + k, 10.0 + k, "8", "Green" if k % 2 else "Red"]]})
    with connect(ws_url(server, f"token={tok}&session_id={sid}&catch_up_s=0")) as ws:
        (snap,) = recv_signals(ws)
        eight = [c for c in snap["changes"] if c[1] == "8"]
        assert len(eight) == 8 and eight[-1] == [21.0, "8", "Green"]
    with connect(ws_url(server, f"token={tok}&session_id={sid}_other&catch_up_s=0")) as ws:
        assert recv_signals(ws, secs=1.0) == []  # another session's socket: nothing


def test_session_signals_for_the_replay(client, auth, tmp_path, monkeypatch):
    sid = f"sigsess_{time.time_ns()}"
    assert client.post("/api/events", headers=SERVICE, json=anomaly(f"{sid}_e", sid)).status_code == 201  # makes the live session
    h = auth("officer")
    assert client.get(f"/api/sessions/{sid}/signals", headers=h).json() == {"lights": [], "timelines": {}, "time_base": None}
    monkeypatch.setattr(config, "LIVE_RESULTS_DIR", tmp_path)  # the server runs in this process
    (tmp_path / sid).mkdir()
    (tmp_path / sid / "traffic_lights.json").write_text(json.dumps(
        {"lights": LIGHTS, "changes": [[100, 5.0, "7", "Green"], [130, 6.5, "7", "Red"], [131, 6.55, "7", "Red"]]}))
    r = client.get(f"/api/sessions/{sid}/signals", headers=h)
    assert r.status_code == 200
    assert r.json() == {"lights": LIGHTS, "timelines": {"7": [[5.0, "Green"], [6.5, "Red"]]}, "time_base": "sim_time"}
    assert client.get("/api/sessions/no_such_session/signals", headers=h).status_code == 404
