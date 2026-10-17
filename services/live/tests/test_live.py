"""Tests for the live pipeline (Build Plan M4): transport framing, online kinematics, incremental engine.

Run:  venv\\Scripts\\python.exe -m pytest services/live/tests -q
"""

import math
import socket
import sys
import time
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))
sys.path.insert(0, str(REPO / "services" / "live"))

from events import COUNTED_STATUS  # noqa: E402
from kinematics import smooth_track  # noqa: E402
from lane_map import SceneMap  # noqa: E402
from online import IncrementalEngine, LiveCamera, OnlineKinematics  # noqa: E402
from rules import Engine  # noqa: E402
from schemas import event_errors  # noqa: E402
from transport import Decoder, FrameReceiver, FrameSender, pack, read_message  # noqa: E402

FPS = 25.0

LANES = [
    {"id": "A", "centreline": [[-300, 0], [300, 0]], "width_m": 3.5, "speed_limit_kmh": 50,
     "left_line": "solidsolid", "right_line": "broken"},
    {"id": "B", "centreline": [[300, 3.5], [-300, 3.5]], "width_m": 3.5, "speed_limit_kmh": 50,
     "left_line": "solidsolid", "right_line": "solid"},
]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --- transport ----------------------------------------------------------------------------------

def test_pack_roundtrip_in_arbitrary_chunks():
    msgs = [({"type": "hello", "width": 1920}, b""), ({"type": "frame", "frame": 1}, b"\xff\xd8" + bytes(5000)),
            ({"type": "frame", "frame": 2}, b"x" * 3), ({"type": "end"}, b"")]
    stream = b"".join(pack(h, p) for h, p in msgs)
    dec, got = Decoder(), []
    for i in range(0, len(stream), 7):  # 7-byte chunks: every header split somewhere
        got += dec.feed(stream[i:i + 7])
    assert got == msgs
    assert not dec.buf


def test_bad_magic_rejected():
    with pytest.raises(ValueError):
        Decoder().feed(b"XXXX" + bytes(8))


def test_read_message_over_socketpair():
    a, b = socket.socketpair()
    a.sendall(pack({"type": "frame", "frame": 7}, b"abc"))
    a.close()
    assert read_message(b) == ({"type": "frame", "frame": 7}, b"abc")
    assert read_message(b) is None


def test_sender_receiver_all_frames():
    port = free_port()
    rx = FrameReceiver("127.0.0.1", port, mode="all")
    tx = FrameSender("127.0.0.1", port, {"type": "hello", "width": 64}, queue=100)
    for i in range(20):
        tx.send({"type": "frame", "frame": i, "capture_wall": time.time()}, bytes([i]) * 10)
    tx.close()
    assert rx.wait_hello(5)["width"] == 64
    got = []
    while not (rx.ended and not rx.q):
        m = rx.get(2.0)
        if m:
            got.append(m[0]["frame"])
    assert got == list(range(20))
    rx.close()


def test_receiver_latest_wins_drops_old_frames():
    port = free_port()
    rx = FrameReceiver("127.0.0.1", port, mode="latest")
    tx = FrameSender("127.0.0.1", port, queue=100)
    for i in range(50):
        tx.send({"type": "frame", "frame": i, "capture_wall": time.time()}, b"z" * 1000)
    tx.close()
    deadline = time.time() + 5
    while not rx.ended and time.time() < deadline:
        time.sleep(0.01)
    m = rx.get(1.0)
    assert m[0]["frame"] == 49  # only the newest is kept
    assert rx.received == 50 and rx.dropped == 49
    rx.close()


def test_sender_drops_oldest_when_not_connected():
    tx = FrameSender("127.0.0.1", free_port(), queue=2, reconnect_s=0.05)  # nobody listening
    for i in range(5):
        tx.send({"type": "frame", "frame": i})
    assert tx.dropped == 3
    tx._stop = True


# --- online kinematics ----------------------------------------------------------------------------

def dets_for(t, x, y, tracker_id=1, conf=0.8, cls="car", edge_ok=True):
    return [{"tracker_id": tracker_id, "cls": cls, "conf": conf, "x": x, "y": y, "edge_ok": edge_ok}]


def drive(kin, xs, ys, ts, tracker_ids=None, emit=True):
    rows = []
    for i, t in enumerate(ts):
        tid = 1 if tracker_ids is None else tracker_ids[i]
        kin.update(i, float(t), dets_for(t, xs[i], ys[i], tid))
        if emit:
            rows += [r for _, _, rs in kin.emit(float(t)) for r in rs]
    rows += [r for _, _, rs in kin.flush() for r in rs]
    return rows


def noisy_line(v_mps=15.0, dur=6.0, seed=0, noise=0.12):
    rng = np.random.default_rng(seed)
    t = np.arange(0, dur, 1 / FPS) + 100.0
    x = -50 + v_mps * (t - t[0]) + rng.normal(0, noise, len(t))
    y = rng.normal(0, noise, len(t))
    return t, x, y


def test_filter_mode_equals_offline_forward_pass_and_fixedlag_inf_equals_rts():
    t, x, y = noisy_line()
    # with an infinite lag the fixed-lag smoother is the offline RTS smoother over the whole track
    kin = OnlineKinematics(lag_s=1e9, mode="fixedlag")
    rows = drive(kin, x, y, t)
    off = smooth_track(t, x, y, np.ones(len(t), bool))
    assert len(rows) == len(t)
    np.testing.assert_allclose([r["speed_kmh"] for r in rows], off["speed_kmh"], atol=0.02)
    np.testing.assert_allclose([r["x"] for r in rows], off["x"], atol=2e-3)


def test_fixedlag_is_causal_within_its_lag():
    """Rows emitted for time <= T - lag must not change when the data after T changes."""
    t, x, y = noisy_line(dur=6.0)
    x2 = x.copy()
    cut = len(t) // 2
    x2[cut:] += np.linspace(0, 30, len(t) - cut)  # a different future
    lag = 0.8
    out = []
    for xs in (x, x2):
        kin = OnlineKinematics(lag_s=lag)
        rows = []
        for i, tt in enumerate(t):
            kin.update(i, float(tt), dets_for(tt, xs[i], y[i]))
            rows += [r for _, _, rs in kin.emit(float(tt)) for r in rs]
        out.append({r["frame"]: r for r in rows if r["time_s"] <= t[cut - 1] - lag})
    assert out[0] and out[0] == out[1]


def test_online_speed_close_to_truth_and_fixedlag_beats_filter():
    t, x, y = noisy_line(v_mps=20.0, dur=8.0, seed=3)
    errs = {}
    for mode in ("filter", "fixedlag"):
        rows = drive(OnlineKinematics(mode=mode), x, y, t)
        sp = np.array([r["speed_kmh"] for r in rows if r["time_s"] > t[0] + 2.0])
        errs[mode] = float(np.median(np.abs(sp - 72.0)))
    assert errs["fixedlag"] < 1.5
    assert errs["fixedlag"] <= errs["filter"]


def test_short_track_not_confirmed():
    t, x, y = noisy_line(dur=0.6)
    kin = OnlineKinematics()
    assert drive(kin, x, y, t) == []
    assert kin.stats["rows_unconfirmed"] == len(t)


def test_id_switch_jump_splits_track():
    t, x, y = noisy_line(dur=4.0)
    x = x.copy()
    x[len(t) // 2:] += 25.0  # another vehicle 25 m away under the same tracker id
    rows = drive(OnlineKinematics(), x, y, t)
    ids = sorted({r["track_id"] for r in rows})
    assert ids == [1, 1001]


def test_relink_new_tracker_id_after_gap():
    t, x, y = noisy_line(dur=5.0)
    keep = (t < 102.0) | (t > 102.6)  # 0.6 s detection gap
    tids = np.where(t < 102.0, 1, 2)  # the tracker gives the vehicle a new id afterwards
    kin = OnlineKinematics()
    rows = drive(kin, x[keep], y[keep], t[keep], tracker_ids=list(tids[keep]))
    assert {r["src_track_id"] for r in rows} == {1}
    assert kin.stats["relinked"] == 1
    kin2 = OnlineKinematics(relink=False)
    rows2 = drive(kin2, x[keep], y[keep], t[keep], tracker_ids=list(tids[keep]))
    assert {r["src_track_id"] for r in rows2} == {1, 2}


def test_relink_refuses_far_away_track():
    t, x, y = noisy_line(dur=5.0)
    y = y.copy()
    y[t > 102.6] += 7.0  # two lanes over
    keep = (t < 102.0) | (t > 102.6)
    tids = np.where(t < 102.0, 1, 2)
    rows = drive(OnlineKinematics(), x[keep], y[keep], t[keep], tracker_ids=list(tids[keep]))
    assert {r["src_track_id"] for r in rows} == {1, 2}


def test_edge_rows_are_not_visible():
    t, x, y = noisy_line(dur=3.0)
    kin = OnlineKinematics()
    rows = []
    for i, tt in enumerate(t):
        kin.update(i, float(tt), dets_for(tt, x[i], y[i], edge_ok=i < len(t) - 10))
        rows += [r for _, _, rs in kin.emit(float(tt)) for r in rs]
    rows += [r for _, _, rs in kin.flush() for r in rs]
    assert all(r["visible"] == 0 for r in rows[-5:])  # > SUPPORT_S past the last full box
    assert all(r["visible"] == 1 for r in rows[5:-12])


def test_live_camera_nadir_projection():
    cam = LiveCamera(1920, 1080, 90.0, ground_z=0.0)
    cam.set_pose(5, (10.0, 20.0, 60.75, -90.0, 0.0, 0.0))
    g = cam.to_ground(5, np.array([960.0]), np.array([540.0]))
    np.testing.assert_allclose(g[0], [10.0, 20.0], atol=1e-6)
    assert cam.to_ground(6, np.array([960.0]), np.array([540.0])) is None


# --- incremental engine -------------------------------------------------------------------------

def speeding_rows(kmh=75.0, dur=10.0):
    t, x, y = noisy_line(v_mps=kmh / 3.6, dur=dur, seed=1, noise=0.1)
    x = x - 100
    return t, x, y


def test_incremental_engine_matches_batch_run_and_reports_open_close():
    t, x, y = speeding_rows()
    kin = OnlineKinematics()
    sc = SceneMap({"scene": "test", "lanes": LANES, "zones": []})
    ie = IncrementalEngine(Engine(sc, prefix="live"))
    all_rows, opened, closed, open_t = [], [], [], {}
    for i, tt in enumerate(t):
        kin.update(i, float(tt), dets_for(tt, x[i], y[i]))
        for te, fr, rows in kin.emit(float(tt)):
            all_rows += rows
            o, c = ie.step(te, fr, rows)
            for e in o:
                open_t[e.event_id] = tt
            opened += o
            closed += c
    for te, fr, rows in kin.flush():
        all_rows += rows
        o, c = ie.step(te, fr, rows)
        opened += o
        closed += c
    o, c = ie.finish()
    opened += o
    closed += c
    sp = [e for e in opened if e.type == "speeding"]
    assert len(sp) == 1 and sp[0].status in COUNTED_STATUS
    assert [e.event_id for e in closed if e.type == "speeding"] == [sp[0].event_id]
    # opened while the car is still driving, within the lag + the rule's own minimum time of its flag
    assert open_t[sp[0].event_id] - sp[0].flag_s <= kin.lag + 1e-6 + 1 / FPS
    # same rows through the batch Engine.run -> same events
    batch = Engine(SceneMap({"scene": "test", "lanes": LANES, "zones": []}), prefix="live").run(all_rows)
    assert [(e.type, e.flag_s, e.status) for e in batch] == [(e.type, e.flag_s, e.status) for e in closed]


def test_event_dict_passes_schema():
    from pipeline import event_dict
    t, x, y = speeding_rows()
    kin = OnlineKinematics()
    ie = IncrementalEngine(Engine(SceneMap({"scene": "test", "lanes": LANES, "zones": []}), prefix="live_x"))
    evs = []
    for i, tt in enumerate(t):
        kin.update(i, float(tt), dets_for(tt, x[i], y[i]))
        for te, fr, rows in kin.emit(float(tt)):
            o, _ = ie.step(te, fr, rows)
            evs += [event_dict(e, "live_x", provisional=True) for e in o]
    _, c = ie.finish()
    evs += [event_dict(e, "live_x", provisional=False) for e in c]
    assert evs and event_errors(evs) == []
    assert "provisional" in evs[0]["tags"] and "provisional" not in evs[-1]["tags"]
