"""Watchdog helpers (PRD 29.1) and a real restart of a hung / crashing child process."""

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "live"))

import watchdog  # noqa: E402


def test_stale_only_after_a_first_heartbeat():
    assert watchdog.stale(None, 100.0, 5.0) is False  # still starting: nothing enforced
    assert watchdog.stale({"wall": 98.0}, 100.0, 5.0) is False
    assert watchdog.stale({"wall": 90.0}, 100.0, 5.0) is True


def test_gap_log_format(tmp_path):
    rec = watchdog.log_gap(tmp_path / "g.jsonl", 1000.0, 1004.5, "no heartbeat for 5.2 s", 1)
    assert rec["gap"].startswith("pipeline gap: ") and " to " in rec["gap"] and rec["seconds"] == 4.5
    assert json.loads((tmp_path / "g.jsonl").read_text())["restart"] == 1


def test_restarts_a_hung_pipeline(tmp_path, monkeypatch):
    """A stand-in 'pipeline' that beats twice, hangs the first time, and exits 0 the second time."""
    fake = tmp_path / "pipeline.py"
    fake.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "a = sys.argv\n"
        "out = Path(a[a.index('--out') + 1]) / a[a.index('--session') + 1]\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "resumed = '--resume' in a\n"
        "for i in range(4):\n"
        "    (out / 'heartbeat.json').write_text(json.dumps({'wall': time.time()}))\n"
        "    time.sleep(0.3)\n"
        "if not resumed:\n"
        "    time.sleep(60)  # hang: no more heartbeats\n"
    )
    monkeypatch.setattr(watchdog, "HERE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["watchdog.py", "--timeout", "1.5", "--out", str(tmp_path / "res"), "--session", "s1"])
    t0 = time.time()
    watchdog.main()
    assert time.time() - t0 < 30
    gaps = [json.loads(line) for line in (tmp_path / "res" / "s1" / "pipeline_gaps.jsonl").read_text().splitlines()]
    assert len(gaps) == 1 and "no heartbeat" in gaps[0]["reason"] and gaps[0]["restart"] == 1
