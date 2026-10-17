import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim_metrics import conflicts, pair_ttc, queue_stats, speed_stats, travel_times  # noqa: E402


def st(i, t, x, y, yaw=0.0, v=10.0, lim=50.0, road=1):
    import math
    h = math.radians(yaw)
    return {"id": i, "sim_time": t, "x": x, "y": y, "yaw": yaw, "vx": v * math.cos(h), "vy": v * math.sin(h),
            "speed_kmh": v * 3.6, "limit_kmh": lim, "road_id": road}


def test_rear_end_ttc():
    # follower at 20 m/s, leader 14.5 m ahead (10 m gap) at 10 m/s -> TTC 1.0 s
    kind, t = pair_ttc(st(1, 0, 0, 0, v=20), st(2, 0, 14.5, 0, v=10))
    assert kind == "rear_end" and abs(t - 1.0) < 1e-6
    assert pair_ttc(st(1, 0, 0, 0, v=10), st(2, 0, 14.5, 0, v=10)) is None  # not closing
    assert pair_ttc(st(1, 0, 0, 0, v=20), st(2, 0, 14.5, 3.0, v=10)) is None  # next lane


def test_crossing_ttc():
    # A east at 10 m/s from (-10, 0), B north at 10 m/s from (0, -10): meet at the origin in 1 s
    kind, t = pair_ttc(st(1, 0, -10, 0, yaw=0, v=10), st(2, 0, 0, -10, yaw=90, v=10))
    assert kind == "crossing" and abs(t - 1.0) < 1e-6
    assert pair_ttc(st(1, 0, -30, 0, yaw=0, v=10), st(2, 0, 0, -30, yaw=90, v=10)) is None  # 3 s away


def test_conflict_episodes_count_once():
    rows = []
    for k, t in enumerate([0.0, 0.1, 0.2]):
        rows += [st(1, t, 0 + 2.0 * k, 0, v=20), st(2, t, 14.5 + 1.0 * k, 0, v=10)]
    rows += [st(1, 0.3, 100, 0, v=20), st(2, 0.3, 300, 0, v=10)]  # apart: episode ends
    c = conflicts(rows)
    assert c["count"] == 1 and c["by_kind"] == {"rear_end": 1} and c["min_ttc_s"] < 1.0


def test_speeding_needs_duration():
    rows = [st(1, t / 10, t, 0, v=20, lim=50) for t in range(20)]  # 72 km/h > 55 for 1.9 s
    rows += [st(2, t / 10, t, 5, v=20, lim=50) for t in range(5)]  # only 0.4 s
    s = speed_stats(rows)
    assert s["speeding_vehicles"] == 1 and s["over_limit_share"] == 1.0


def test_travel_time_only_through_traffic():
    rows = [st(1, t, -70 + 10 * t, 0) for t in range(15)]  # inside from t=2 (x=-50) to t=12 (x=50)
    rows += [st(2, t, 0, 0) for t in range(15)]  # inside the whole run: not counted
    tt = travel_times(rows, (0, 0), 50, 0, 14)
    assert tt["vehicles"] == 1 and tt["mean_s"] == 10.0


def test_queue_chain():
    rows = [st(i, 0, 7.0 * i, 0, v=0.0) for i in range(4)]  # 4 stopped cars 7 m apart
    rows.append(st(9, 0, 60, 0, v=0.0))  # far ahead: separate
    q = queue_stats(rows)
    assert q["max_m"] == 21.0 + 4.5
