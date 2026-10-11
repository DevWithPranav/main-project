"""net_check.py: the echo server and the measurement, on loopback."""

import socket
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "live"))

import net_check as nc  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_measure_on_loopback_finds_no_clock_offset_and_good_bandwidth():
    port, stop, ready = free_port(), threading.Event(), threading.Event()
    th = threading.Thread(target=nc.serve, args=(port, "127.0.0.1", stop, ready), daemon=True)
    th.start()
    assert ready.wait(5)
    r = nc.measure("127.0.0.1", port, mb=8, rounds=12)
    stop.set()
    assert abs(r["clock_offset_s"]) < 0.02 and r["rtt_ms"]["min"] < 50 and r["upload_mbit_s"] > 100
    v = nc.verdict(r)
    assert v[0].startswith("bandwidth OK") and any("clocks agree" in x for x in v)


def test_verdict_flags_a_slow_link_a_relay_and_clock_skew():
    bad = {"upload_mbit_s": 12.0, "rtt_ms": {"min": 90.0, "median": 120.0, "max": 200.0}, "clock_offset_ms": 1500.0, "clock_offset_s": 1.5}
    v = nc.verdict(bad)
    assert "TOO LOW" in v[0] and any("relayed" in x for x in v) and any("--clock-offset 1.5" in x for x in v)
    marginal = {"upload_mbit_s": 45.0, "rtt_ms": {"min": 5.0, "median": 6.0, "max": 9.0}, "clock_offset_ms": -3.0, "clock_offset_s": -0.003}
    assert "marginal" in nc.verdict(marginal)[0]


def test_unreachable_server_raises():
    import pytest
    with pytest.raises(OSError):
        nc.measure("127.0.0.1", free_port(), mb=1, rounds=3, timeout=1.0)
