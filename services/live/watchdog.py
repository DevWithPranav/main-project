"""Watchdog for the live pipeline (PRD 29.1): restarts it when it crashes or stops responding.

Runs services/live/pipeline.py as a subprocess with a fixed --session and watches the heartbeat file the
pipeline rewrites (<out>/<session>/heartbeat.json, at most every 0.5 s, from its main loop). No
heartbeat for --timeout seconds (default 5) after the first one, or an exit with an error code: the
process is killed and started again with --resume (same session folder, files appended; the frame source
reconnects by itself, transport.py). Each restart is logged as "pipeline gap: <start> to <end>" in
<out>/<session>/pipeline_gaps.jsonl. Before the first heartbeat (model loading, waiting for a source)
nothing is enforced except that the process must stay alive. A clean exit (code 0, e.g. --once) stops it.

Usage (same options as pipeline.py, they are passed through):
    venv/Scripts/python.exe services/live/watchdog.py --backend http://localhost:8000 [--timeout 5] [--max-restarts 20]
"""

import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LIVE_RESULTS = REPO / "ml" / "data" / "results" / "live"


def read_beat(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def stale(beat: dict | None, now: float, timeout: float) -> bool:
    """True when a heartbeat exists and is older than the timeout (no heartbeat yet is not stale)."""
    return beat is not None and now - float(beat["wall"]) > timeout


def log_gap(path: Path, start: float, end: float, reason: str, restarts: int) -> dict:
    fmt = lambda t: datetime.datetime.fromtimestamp(t).isoformat(timespec="seconds")  # noqa: E731
    rec = {"gap": f"pipeline gap: {fmt(start)} to {fmt(end)}", "start_wall": start, "end_wall": end,
           "seconds": round(end - start, 2), "reason": reason, "restart": restarts}
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], add_help=True)
    ap.add_argument("--timeout", type=float, default=5.0, help="seconds without a heartbeat before a restart (PRD 29.1: 5)")
    ap.add_argument("--max-restarts", type=int, default=20)
    ap.add_argument("--session", default=None)
    ap.add_argument("--out", type=Path, default=LIVE_RESULTS)
    args, passthrough = ap.parse_known_args()
    session = args.session or f"live_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    folder = args.out / session
    folder.mkdir(parents=True, exist_ok=True)
    beat_file, gaps_file = folder / "heartbeat.json", folder / "pipeline_gaps.jsonl"
    beat_file.unlink(missing_ok=True)  # a heartbeat left by an earlier run of this session would read as an instantly stale one
    cmd = [sys.executable, str(HERE / "pipeline.py"), *passthrough, "--session", session, "--out", str(args.out)]
    restarts, last_beat_wall, gap_start, reason, gap_reason = 0, None, None, "", ""
    while True:
        proc = subprocess.Popen(cmd + (["--resume"] if restarts else []))
        print(f"[watchdog] pipeline pid {proc.pid} (start {restarts})", flush=True)
        seen_beat = False
        while proc.poll() is None:
            time.sleep(0.5)
            beat = read_beat(beat_file)
            now = time.time()
            if beat and (not seen_beat or float(beat["wall"]) > (last_beat_wall or 0)):
                if gap_start is not None and float(beat["wall"]) > gap_start:  # first sign of life after a restart
                    print("[watchdog] " + log_gap(gaps_file, gap_start, float(beat["wall"]), gap_reason, restarts)["gap"], flush=True)
                    gap_start = None
                seen_beat, last_beat_wall = True, float(beat["wall"])
            if seen_beat and stale(beat, now, args.timeout):
                reason = f"no heartbeat for {now - float(beat['wall']):.1f} s"
                print(f"[watchdog] {reason}; killing pid {proc.pid}", flush=True)
                proc.kill()
                proc.wait()
                break
        code = proc.returncode
        if code == 0:
            print("[watchdog] pipeline finished", flush=True)
            return
        reason = reason or f"pipeline exited with code {code}"
        if restarts >= args.max_restarts:
            print(f"[watchdog] giving up after {restarts} restarts ({reason})", flush=True)
            sys.exit(1)
        gap_start, gap_reason = last_beat_wall or time.time(), reason
        restarts += 1
        print(f"[watchdog] restarting ({reason})", flush=True)
        reason = ""


if __name__ == "__main__":
    main()
