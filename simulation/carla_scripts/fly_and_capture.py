"""Stage 6 — fly a drone on an automatic path over CARLA traffic and capture
aerial RGB footage for the detector+tracker to be tested against.

The flight path follows an actual road lane (via CARLA's Waypoint API,
`generate_road_path`) starting from a random spawn point, rather than a
guessed set of world coordinates — that guarantees the drone flies over real
road geometry regardless of which map is loaded. The camera is set to a
nadir (straight down) pose before takeoff; AirSim's camera "0" defaults to
forward-facing, which otherwise points every captured frame at the horizon
instead of the road.

Run in the CarlaAir distribution's own Python 3.10 conda env (see
simulation/README.md for setup) — this script imports `carla` and `airsim`
from CarlaAir-v0.1.7-Windows11-x86_64/, which are not installed in the
project's main venv. It writes captured frames as JPGs plus a review-only
mp4; detection/tracking runs separately in the main venv, directly on the
JPGs (see ml/violation_engine/run_sim_validation.py).

Usage (with CarlaAir already running, e.g. `StartCarlaAir.bat Town10HD`):
    python fly_and_capture.py
    python fly_and_capture.py --vehicles 15 --altitude 45 --waypoints 10
"""

import argparse
import json
import math
import random
import sys
import threading
import time
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("Need opencv-python (pip install opencv-python)")
try:
    import carla
except ImportError:
    sys.exit("Need carla — run this in the CarlaAir conda env, not the main project venv")
try:
    import airsim
except ImportError:
    sys.exit("Need airsim — run this in the CarlaAir conda env, not the main project venv")

DATA_EXPORT_DIR = Path(__file__).resolve().parents[1] / "data_export" / "sim_flights"
JPEG_SAVE_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 100]


def generate_road_path(world, carla_module, n_waypoints: int = 8, step: float = 18.0, start_location=None) -> list[tuple[float, float]]:
    """Walk forward along an actual road lane, using CARLA's Waypoint API, so
    the flight path follows real road geometry instead of a guessed set of
    world coordinates (which previously produced a path that didn't reliably
    track any road).

    start_location, if given, anchors the path to that point (e.g. an actual
    spawned vehicle's location) instead of a purely random map spawn point.
    A random spawn point is a location a vehicle COULD start from, but on
    maps like Town10HD plenty of those are on quiet roads along the coast —
    picking one at random repeatedly produced flights over an empty
    beachfront promenade with no traffic anywhere in view. Anchoring to a
    vehicle that's actually spawned there guarantees the flight starts where
    a vehicle really is."""
    m = world.get_map()
    if start_location is None:
        start_location = random.choice(m.get_spawn_points()).location
    wp = m.get_waypoint(start_location, project_to_road=True, lane_type=carla_module.LaneType.Driving)

    path = [(wp.transform.location.x, wp.transform.location.y)]
    for _ in range(n_waypoints - 1):
        nxt = wp.next(step)
        if not nxt:
            break
        wp = nxt[0]
        path.append((wp.transform.location.x, wp.transform.location.y))
    return path


def load_recorded_waypoints(path: Path, min_spacing: float = 10.0) -> list[tuple[float, float]]:
    """Load a custom path from a trajectory JSON recorded by CarlaAir's
    examples_record_demo/record_drone.py — fly it manually once (e.g. with
    examples/fly_drone_keyboard.py) while record_drone.py captures it, then
    replay that same path automatically here on every subsequent run.

    Recorded trajectories sample at ~20Hz, far denser than useful for
    point-to-point moveToPositionAsync waypoints, so consecutive points
    closer than min_spacing meters are collapsed down to one.
    """
    data = json.loads(path.read_text())
    if data.get("type") != "drone":
        raise SystemExit(f"{path} is not a drone trajectory (type={data.get('type')!r})")

    waypoints = []
    last = None
    for frame in data["frames"]:
        t = frame["transform"]
        x, y = t["x"], t["y"]
        if last is None or math.hypot(x - last[0], y - last[1]) >= min_spacing:
            waypoints.append((x, y))
            last = (x, y)
    return waypoints


def spawn_traffic(world, client, count: int):
    bp_lib = world.get_blueprint_library()
    vehicle_bps = [bp for bp in bp_lib.filter("vehicle.*")
                   if int(bp.get_attribute("number_of_wheels")) == 4]
    spawn_points = world.get_map().get_spawn_points()
    random.shuffle(spawn_points)

    vehicles = []
    for sp in spawn_points[:count]:
        bp = random.choice(vehicle_bps)
        if bp.has_attribute("color"):
            bp.set_attribute("color", random.choice(bp.get_attribute("color").recommended_values))
        v = world.try_spawn_actor(bp, sp)
        if v:
            vehicles.append(v)
    time.sleep(1)
    for v in vehicles:
        v.set_autopilot(True)
    print(f"[traffic] spawned {len(vehicles)}/{count} vehicles")
    return vehicles


def decode_rgb(response) -> np.ndarray | None:
    if response.width <= 0:
        return None
    buf = np.frombuffer(response.image_data_uint8, dtype=np.uint8)
    n = response.height * response.width
    channels = len(buf) // n
    img = buf.reshape(response.height, response.width, channels)
    if channels >= 3:
        img = img[:, :, [2, 1, 0]]
    return np.ascontiguousarray(img[:, :, :3])


def fly_and_capture(
    waypoints_xy: list[tuple[float, float]],
    altitude: float,
    speed: float,
    capture_fps: float,
    out_dir: Path,
    airsim_port: int,
) -> tuple[Path, dict]:
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    client = airsim.MultirotorClient(port=airsim_port)
    client.confirmConnection()
    client.enableApiControl(True)
    client.armDisarm(True)

    # Camera "0" defaults to forward-facing on the drone body; without this the
    # captured footage looks at the horizon/skyline instead of down at traffic,
    # which starved the detector of vehicles in the first test run of this script.
    nadir_pose = airsim.Pose(airsim.Vector3r(0, 0, 0), airsim.to_quaternion(math.radians(-90), 0, 0))
    client.simSetCameraPose("0", nadir_pose)

    print(f"[drone] takeoff, climbing to {altitude}m ...")
    client.takeoffAsync().join()
    client.moveToZAsync(-altitude, 5).join()

    # Capture runs on its own thread at a fixed rate, independent of how long
    # each moveToPositionAsync().join() call below takes to reach a waypoint.
    frame_paths: list[Path] = []
    capture_client = airsim.MultirotorClient(port=airsim_port)
    capture_client.confirmConnection()
    capturing = threading.Event()
    capturing.set()

    def capture_loop():
        capture_interval = 1.0 / capture_fps
        while capturing.is_set():
            start = time.perf_counter()
            resp = capture_client.simGetImages([airsim.ImageRequest("0", airsim.ImageType.Scene, False, False)])
            img = decode_rgb(resp[0]) if resp else None
            if img is not None:
                frame_path = frames_dir / f"{len(frame_paths):05d}.jpg"
                cv2.imwrite(str(frame_path), img, JPEG_SAVE_PARAMS)
                frame_paths.append(frame_path)
            elapsed = time.perf_counter() - start
            time.sleep(max(0.0, capture_interval - elapsed))

    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()

    for i, (x, y) in enumerate(waypoints_xy):
        yaw = None
        if i + 1 < len(waypoints_xy):
            nx, ny = waypoints_xy[i + 1]
            yaw = math.degrees(math.atan2(ny - y, nx - x))
        yaw_mode = airsim.YawMode(False, yaw) if yaw is not None else airsim.YawMode(False, 0)

        client.moveToPositionAsync(
            x, y, -altitude, speed,
            drivetrain=airsim.DrivetrainType.MaxDegreeOfFreedom,
            yaw_mode=yaw_mode,
        ).join()
        print(f"[drone] reached waypoint {i + 1}/{len(waypoints_xy)} ({x:.0f}, {y:.0f})")

    capturing.clear()
    capture_thread.join(timeout=2.0)

    print("[drone] landing ...")
    client.moveToZAsync(-3, 3).join()
    client.landAsync().join()
    client.armDisarm(False)
    client.enableApiControl(False)

    if not frame_paths:
        raise SystemExit("No frames captured — check AirSim camera config and connection.")

    # This mp4 is for human review only (e.g. quickly scrubbing the flight).
    # Detection/tracking runs directly on the frames/ JPGs (see
    # ml/violation_engine/run_sim_validation.py) to avoid a second lossy
    # re-encode degrading what the detector sees, and to guarantee every
    # captured frame is used rather than whatever the video muxer keeps.
    first = cv2.imread(str(frame_paths[0]))
    h, w = first.shape[:2]
    video_path = out_dir / "flight.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), capture_fps, (w, h))
    for p in frame_paths:
        writer.write(cv2.imread(str(p)))
    writer.release()
    print(f"[capture] {len(frame_paths)} frames -> {video_path}")

    metadata = {
        "waypoints_xy": waypoints_xy,
        "altitude_m": altitude,
        "speed_mps": speed,
        "capture_fps": capture_fps,
        "n_frames": len(frame_paths),
    }
    return video_path, metadata


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--airsim-port", type=int, default=41451)
    ap.add_argument("--vehicles", type=int, default=15)
    ap.add_argument("--altitude", type=float, default=45.0)
    ap.add_argument("--speed", type=float, default=6.0)
    ap.add_argument("--capture-fps", type=float, default=20.0)
    ap.add_argument("--waypoints", type=int, default=8, help="Number of road waypoints to fly through (auto-generated path)")
    ap.add_argument("--waypoint-step", type=float, default=18.0, help="Meters along the road between waypoints (auto-generated path)")
    ap.add_argument("--waypoints-file", type=Path, default=None,
                     help="Path to a drone trajectory JSON recorded with CarlaAir's "
                          "examples_record_demo/record_drone.py — flies that exact "
                          "custom path instead of auto-generating one from the road network")
    ap.add_argument("--min-spacing", type=float, default=10.0,
                     help="Minimum meters between waypoints loaded from --waypoints-file")
    args = ap.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(15)
    world = client.get_world()
    map_name = world.get_map().name
    print(f"[carla] connected, map={map_name}")

    if args.waypoints_file:
        vehicles = spawn_traffic(world, client, args.vehicles)
        waypoints_xy = load_recorded_waypoints(args.waypoints_file, min_spacing=args.min_spacing)
        if len(waypoints_xy) < 2:
            raise SystemExit(f"{args.waypoints_file} produced fewer than 2 waypoints — "
                              f"try a smaller --min-spacing.")
        print(f"[path] {len(waypoints_xy)} waypoints loaded from {args.waypoints_file}")
    else:
        # Spawn traffic first (scattered across the map's spawn points), then
        # anchor the drone's path to wherever traffic actually clustered
        # densest — a purely random spawn point for the path itself was
        # landing repeatedly on Town10HD's quiet beachfront road with no
        # traffic anywhere nearby (see generate_road_path's docstring).
        vehicles = spawn_traffic(world, client, args.vehicles)
        if not vehicles:
            raise SystemExit("No traffic vehicles spawned — cannot anchor a flight path to them.")

        def n_neighbors(v, radius: float = 80.0) -> int:
            loc = v.get_location()
            return sum(
                1 for o in vehicles
                if o.id != v.id and math.hypot(o.get_location().x - loc.x, o.get_location().y - loc.y) <= radius
            )

        anchor_vehicle = max(vehicles, key=n_neighbors)
        anchor_location = anchor_vehicle.get_location()
        print(f"[path] anchoring to vehicle cluster near ({anchor_location.x:.0f}, {anchor_location.y:.0f}) "
              f"({n_neighbors(anchor_vehicle)} nearby vehicles)")

        # Retry a few times: a spawn point on a short dead-end road can produce
        # a path too short to be useful, so fall back to a different anchor
        # until one yields enough waypoints (or give up and use what we have).
        candidates = sorted(vehicles, key=lambda v: -n_neighbors(v))
        waypoints_xy = []
        for v in candidates[:5]:
            waypoints_xy = generate_road_path(
                world, carla, n_waypoints=args.waypoints, step=args.waypoint_step, start_location=v.get_location()
            )
            if len(waypoints_xy) >= max(2, args.waypoints // 2):
                break
        if len(waypoints_xy) < 2:
            raise SystemExit("Could not generate a usable road path from this map's waypoint graph.")
        print(f"[path] {len(waypoints_xy)} road waypoints, {args.waypoint_step}m apart")

    run_id = time.strftime("%Y%m%d_%H%M%S")
    out_dir = DATA_EXPORT_DIR / run_id

    try:
        video_path, metadata = fly_and_capture(
            waypoints_xy=waypoints_xy,
            altitude=args.altitude,
            speed=args.speed,
            capture_fps=args.capture_fps,
            out_dir=out_dir,
            airsim_port=args.airsim_port,
        )
        metadata["map"] = map_name
        metadata["n_traffic_vehicles"] = len(vehicles)
        (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
        print(f"[done] video={video_path}  metadata={out_dir / 'metadata.json'}")
    finally:
        for v in vehicles:
            try:
                v.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    main()
