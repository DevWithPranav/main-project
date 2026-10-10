"""Replay a recorded CARLA flight through the live transport at real-time speed (Build Plan M4).

Stands in for simulation/carla_scripts/stream_flight.py when the simulator is not available:
same messages (transport.py), same per-frame pose and simulator time, read from the flight
folder written by record_flight.py (frames/, frame_times.csv, metadata.json). Frames are sent
as the JPEG files on disk (no re-encode) at the pace they were captured (frame_times.csv
time_s, the wall clock of the recording), or --speed times faster. capture_wall is stamped
when a frame is handed to the sender, so latency is measured as if it had just been rendered.

ground_z: the median vehicle height of the flight (ground_coords.flight_ground_z), as the
offline pipeline uses; the live streamer sends the same from the world's vehicles.

Usage:
    venv\\Scripts\\python.exe services/live/replay_source.py 20261009_201727
    venv\\Scripts\\python.exe services/live/replay_source.py 20261009_201727 --host 192.168.1.20 --speed 2
    venv\\Scripts\\python.exe services/live/replay_source.py 20261009_201727 --start 120 --duration 60
"""

import argparse
import bisect
import csv
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transport import DEFAULT_PORT, FrameSender  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FLIGHTS = REPO / "simulation" / "data_export" / "recorded_flights"


def flight_ground_z(flight: Path) -> float:
    """Same as ml/violation_engine/ground_coords.flight_ground_z (kept here: no numpy/scipy needed)."""
    p = flight / "vehicle_poses.csv"
    if not p.exists():
        return 0.0
    with open(p, newline="") as f:
        zs = [float(r["z"]) for r in csv.DictReader(f)]
    return statistics.median(zs) if zs else 0.0


MAX_POSE_GAP = 40  # sim ticks, as ground_coords.MAX_POSE_GAP


def poses_from_camera_log(flight: Path, rows: list[dict]) -> list[dict]:
    """Flights before 2026-10-03: no per-frame pose in frame_times.csv, only camera_poses.csv every
    few ticks. Interpolate each frame's pose there by carla_frame (as ground_coords.FlightCamera.pose,
    angles linear after unwrapping) and use the wall time as sim_time; frames further than
    MAX_POSE_GAP ticks from any logged pose are dropped (the offline pipeline gives them no position)."""
    p = flight / "camera_poses.csv"
    if not p.exists():
        raise SystemExit(f"{flight}: no per-frame camera pose in frame_times.csv and no camera_poses.csv")
    with open(p, newline="") as f:
        log = [r for r in csv.DictReader(f)]
    cfs = [int(r["carla_frame"]) for r in log]
    keys = ("x", "y", "z", "pitch", "yaw", "roll")
    vals = {k: [float(r[k]) for r in log] for k in keys}
    for k in ("pitch", "yaw", "roll"):  # unwrap so interpolation never goes the long way round
        v = vals[k]
        for i in range(1, len(v)):
            v[i] = v[i - 1] + ((v[i] - v[i - 1] + 180.0) % 360.0 - 180.0)
    out = []
    for r in rows:
        cf = int(r["carla_frame"])
        j = bisect.bisect_left(cfs, cf)
        near = [i for i in (j - 1, j) if 0 <= i < len(cfs)]
        if not near or min(abs(cf - cfs[i]) for i in near) > MAX_POSE_GAP:
            continue
        if j <= 0 or j >= len(cfs):
            i0 = i1 = near[0]
        else:
            i0, i1 = j - 1, j
        w = 0.0 if i1 == i0 else (cf - cfs[i0]) / (cfs[i1] - cfs[i0])
        r = dict(r, sim_time=r["time_s"])
        for k in keys:
            r[k] = vals[k][i0] + w * (vals[k][i1] - vals[k][i0])
        out.append(r)
    return out


def load(flight: Path) -> tuple[dict, list[dict]]:
    meta = json.loads((flight / "metadata.json").read_text())
    with open(flight / "frame_times.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"{flight}: empty frame_times.csv")
    if "yaw" not in rows[0]:
        rows = poses_from_camera_log(flight, rows)
        print(f"[replay] {flight.name}: no per-frame pose; interpolated from camera_poses.csv "
              f"({len(rows)} frames kept), wall time used as sim time")
    cam = meta.get("camera", {})
    hello = {"type": "hello", "source": "replay", "flight": flight.name, "map": meta.get("map", ""),
             "width": cam.get("width", 1920), "height": cam.get("height", 1080), "fov": cam.get("fov", 90.0),
             "ground_z": round(flight_ground_z(flight), 3)}
    return hello, rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", help="Flight folder, or its name under simulation/data_export/recorded_flights/")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--speed", type=float, default=1.0, help="Replay speed (1 = real time)")
    ap.add_argument("--pace", choices=["wall", "sim"], default="wall",
                    help="Pace by the recording's wall clock (time_s, default) or by simulator time")
    ap.add_argument("--start", type=float, default=0.0, help="Skip the first N seconds of the flight")
    ap.add_argument("--duration", type=float, default=None, help="Send only N seconds")
    ap.add_argument("--queue", type=int, default=2, help="Sender queue (oldest dropped when full)")
    args = ap.parse_args()

    flight = Path(args.flight)
    if not flight.is_dir():
        flight = FLIGHTS / args.flight
    hello, rows = load(flight)
    tcol = "time_s" if args.pace == "wall" else "sim_time"
    t0_rec = float(rows[0][tcol]) + args.start
    rows = [r for r in rows if float(r[tcol]) >= t0_rec and
            (args.duration is None or float(r[tcol]) < t0_rec + args.duration)]
    print(f"[replay] {flight.name}: {len(rows)} frames, {hello['width']}x{hello['height']}, "
          f"ground_z {hello['ground_z']} -> {args.host}:{args.port} at {args.speed}x")
    sender = FrameSender(args.host, args.port, hello, queue=args.queue)
    t_start = time.perf_counter()
    late = 0
    for i, r in enumerate(rows):
        due = (float(r[tcol]) - t0_rec) / args.speed
        wait = due - (time.perf_counter() - t_start)
        if wait > 0:
            time.sleep(wait)
        elif wait < -0.1:
            late += 1
        jpg = (flight / "frames" / f"{int(r['frame']):05d}.jpg").read_bytes()
        sender.send({"type": "frame", "frame": int(r["frame"]), "carla_frame": int(r["carla_frame"]),
                     "sim_time": float(r["sim_time"]),
                     "pose": [float(r[k]) for k in ("x", "y", "z", "pitch", "yaw", "roll")],
                     "capture_wall": time.time()}, jpg)
        if i % 500 == 0:
            print(f"[replay] {i}/{len(rows)} sent={sender.sent} dropped={sender.dropped} "
                  f"connected={sender.connected}", flush=True)
    sender.close()
    print(f"[replay] done: {len(rows)} frames, sent {sender.sent}, dropped at sender {sender.dropped}, "
          f"behind schedule {late}, {time.perf_counter() - t_start:.1f} s")


if __name__ == "__main__":
    main()
