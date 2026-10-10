"""Live drone feed for the detector service (Build Plan M4, decision #4): CARLA camera -> network.

The simulator side of the live pipeline. Attaches the same nadir sensor.camera.rgb as
record_flight.py to the drone and sends every rendered frame, with the camera's own world pose and
the simulator time, to services/live/pipeline.py over services/live/transport.py (one TCP
connection, length-prefixed JPEG + JSON header, latest-frame-wins). The detector may run on this
machine (localhost) or another one (presentation): only --to changes.

The hello message carries the camera (width, height, fov), the map and ground_z, the road height
the pipeline projects pixels onto: the median z of the world's vehicles (as ground_coords
flight_ground_z does from vehicle_poses.csv), or --ground-z.

The drone is flown by someone else: fly_drone.py / start_carla_and_fly.bat, or goto_spot.py to
hold a spot. Esc (global hotkey) or Ctrl+C stops the stream; the drone is left as it is.

--record also saves the stream as a recorded flight (frames/, frame_times.csv with per-frame
pose + sim time, metadata.json; no seg / labels), so the same minutes can be run through the
offline pipeline afterwards and the two compared (services/live/compare_offline.py).

Run in the CarlaAir conda env (needs carla, cv2, pynput; the transport is standard library only):
    python simulation/carla_scripts/stream_flight.py                              # detector on this PC
    python simulation/carla_scripts/stream_flight.py --to 192.168.1.20            # detector PC
    python simulation/carla_scripts/stream_flight.py --vehicles 40 --record --jpeg-quality 85
"""

import argparse
import json
import statistics
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

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "live"))
from transport import DEFAULT_PORT, FrameSender  # noqa: E402

from fly_and_capture import spawn_traffic  # noqa: E402
from record_flight import DATA_EXPORT_DIR, EscStop, apply_weather, camera_mount, find_drone_actor  # noqa: E402

STATUS_EVERY_S = 2.0


def world_ground_z(world) -> float | None:
    """Median z of the vehicles in the world (their actor origin, as vehicle_poses.csv logs it)."""
    zs = [v.get_location().z for v in world.get_actors().filter("vehicle.*")]
    return statistics.median(zs) if zs else None


class StreamCamera:
    """sensor.camera.rgb on the drone; each image -> JPEG -> FrameSender (never blocks the sensor
    thread for long: the sender queue drops the oldest frame when the network or detector lags)."""

    def __init__(self, world, drone, sender: FrameSender, width: int, height: int, fov: float, pitch: float,
                 quality: int, record_dir: Path | None):
        bp = world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        bp.set_attribute("fov", str(fov))
        bp.set_attribute("sensor_tick", "0.0")
        self.sender = sender
        self.params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        self.lock = threading.Lock()
        self.count = 0
        self.encode_ms: list[float] = []
        self.t0 = time.perf_counter()
        self.record_dir = record_dir
        self._times = None
        if record_dir is not None:
            (record_dir / "frames").mkdir(parents=True, exist_ok=True)
            self._times = open(record_dir / "frame_times.csv", "w", newline="")
            self._times.write("frame,time_s,carla_frame,sim_time,x,y,z,pitch,yaw,roll\n")
        self.camera = world.spawn_actor(bp, camera_mount(pitch), attach_to=drone,
                                        attachment_type=carla.AttachmentType.Rigid)
        self.camera.listen(self._on_image)

    def _on_image(self, image) -> None:
        wall = time.time()
        t_enc = time.perf_counter()
        arr = np.frombuffer(image.raw_data, dtype=np.uint8).reshape(image.height, image.width, 4)
        ok, jpg = cv2.imencode(".jpg", np.ascontiguousarray(arr[:, :, :3]), self.params)
        if not ok:
            return
        jpg = jpg.tobytes()
        tf = image.transform
        pose = [round(v, 4) for v in (tf.location.x, tf.location.y, tf.location.z,
                                      tf.rotation.pitch, tf.rotation.yaw, tf.rotation.roll)]
        with self.lock:
            idx = self.count
            self.count += 1
            self.encode_ms.append((time.perf_counter() - t_enc) * 1000)
            if self._times is not None:
                t = round(time.perf_counter() - self.t0, 4)
                self._times.write(f"{idx},{t},{image.frame},{image.timestamp:.4f},{','.join(map(str, pose))}\n")
        self.sender.send({"type": "frame", "frame": idx, "carla_frame": int(image.frame),
                          "sim_time": round(float(image.timestamp), 4), "pose": pose, "capture_wall": wall}, jpg)
        if self.record_dir is not None:
            (self.record_dir / "frames" / f"{idx:05d}.jpg").write_bytes(jpg)

    def stop(self) -> None:
        self.camera.stop()
        self.camera.destroy()
        if self._times is not None:
            self._times.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost", help="CARLA host")
    ap.add_argument("--port", type=int, default=2000, help="CARLA port")
    ap.add_argument("--to", default="127.0.0.1", help="Detector service host (services/live/pipeline.py)")
    ap.add_argument("--to-port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--vehicles", type=int, default=0, help="Spawn this much traffic (0: traffic_flow.py does it)")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--pitch", type=float, default=-90.0)
    ap.add_argument("--jpeg-quality", type=int, default=90)
    ap.add_argument("--queue", type=int, default=2, help="Sender queue (oldest frame dropped when full)")
    ap.add_argument("--ground-z", type=float, default=None,
                    help="Road height for ground projection (default: median z of the world's vehicles)")
    ap.add_argument("--weather", default=None)
    ap.add_argument("--sun-altitude", type=float, default=None)
    ap.add_argument("--session", default=None, help="Session name sent to the detector (default: a timestamp)")
    ap.add_argument("--record", action="store_true",
                    help="Also save the stream as a recorded flight under simulation/data_export/recorded_flights/")
    args = ap.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(15)
    world = client.get_world()
    map_name = world.get_map().name
    print(f"[carla] connected, map={map_name}")
    vehicles = spawn_traffic(world, client, args.vehicles) if args.vehicles > 0 else []
    weather = apply_weather(world, args.weather, args.sun_altitude)

    drone = None
    for _ in range(20):
        drone = find_drone_actor(world)
        if drone is not None:
            break
        time.sleep(0.5)
    if drone is None:
        raise SystemExit("Drone actor not found in CARLA. Is CarlaAir running with AirSim enabled?")

    ground_z = args.ground_z if args.ground_z is not None else world_ground_z(world)
    if ground_z is None:
        ground_z = 0.0
        print("[ground] no vehicles in the world yet: ground_z 0.0 (pass --ground-z, or start traffic first)")
    session = args.session or time.strftime("%Y%m%d_%H%M%S")
    record_dir = DATA_EXPORT_DIR / session if args.record else None
    hello = {"type": "hello", "source": "carla", "flight": session, "map": map_name, "width": args.width,
             "height": args.height, "fov": args.fov, "ground_z": round(ground_z, 3)}
    sender = FrameSender(args.to, args.to_port, hello, queue=args.queue)
    cam = StreamCamera(world, drone, sender, args.width, args.height, args.fov, args.pitch,
                       args.jpeg_quality, record_dir)
    stop = EscStop()
    stop.start()
    print(f"[stream] session {session}: {args.width}x{args.height} -> {args.to}:{args.to_port}, "
          f"ground_z {ground_z:.2f} m. Esc or Ctrl+C stops")

    t0 = time.perf_counter()
    last_n, last_t = 0, t0
    try:
        while stop.running:
            time.sleep(STATUS_EVERY_S)
            now = time.perf_counter()
            fps = (cam.count - last_n) / (now - last_t)
            last_n, last_t = cam.count, now
            enc = statistics.median(cam.encode_ms[-200:]) if cam.encode_ms else 0.0
            print(f"[stream] {cam.count} frames, {fps:.1f} fps, sent {sender.sent}, dropped {sender.dropped}, "
                  f"encode {enc:.1f} ms, connected={sender.connected}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop.stop()
        cam.stop()
        sender.close()
        for v in vehicles:
            try:
                v.destroy()
            except Exception:
                pass
        elapsed = time.perf_counter() - t0
        print(f"[stream] done: {cam.count} frames in {elapsed:.1f} s, sent {sender.sent}, "
              f"dropped at sender {sender.dropped}")
        if record_dir is not None and cam.count:
            meta = {"map": map_name, "n_traffic_vehicles": len(vehicles), "n_frames_saved": cam.count,
                    "elapsed_s": round(elapsed, 2), "avg_fps": round(cam.count / elapsed, 2) if elapsed else 0.0,
                    "labels": False, "streamed": True, "ground_z": round(ground_z, 3),
                    "camera": {"width": args.width, "height": args.height, "fov": args.fov, "pitch": args.pitch},
                    "weather_preset": args.weather, "weather": weather}
            (record_dir / "metadata.json").write_text(json.dumps(meta, indent=2))
            print(f"[stream] recorded flight -> {record_dir}")


if __name__ == "__main__":
    main()
