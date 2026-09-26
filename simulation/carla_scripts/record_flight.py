"""Stage 6 — manual flight, recording only (no detection running during the
flight). Fly by hand while this script saves every rendered frame to disk;
detection/tracking happens afterward, in a separate pass, on 100% of what
was recorded (see ml/violation_engine/process_recorded_flight.py).

This exists because live_fly_and_track.py runs YOLO inference in the same
loop as capture — under GPU contention (UE4 rendering + inference sharing
one GPU) it can skip frames for detection, even though it still saves every
frame to disk. Recording with nothing else competing for the GPU guarantees
the later detect+track pass has zero gaps to skip in the first place.

Uses CARLA's native sensor.camera.rgb for capture (push-based streaming),
not AirSim's simGetImages RPC screenshot call — that RPC path is known to
stall permanently once the drone is airborne under this CarlaAir build (see
live_fly_and_track.py's CarlaCameraGrabber docstring for the full history).
AirSim is still used for flight control only.

Run in the CarlaAir distribution's own Python 3.10 conda env (needs `carla`
and `airsim`, not `torch`/`ultralytics` — this script never touches those).

Controls (same keymap as examples/fly_drone_keyboard.py):
    W/S         Forward / Backward
    A/D         Left / Right
    Space       Ascend
    Shift       Descend
    Q/E         Rotate left / right
    T           Takeoff
    L           Land
    ESC         Quit — stops and saves

Usage (with CarlaAir already running, e.g. `StartCarlaAir.bat Town10HD`):
    python record_flight.py
    python record_flight.py --vehicles 15
"""

import argparse
import json
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
    import airsim
except ImportError:
    sys.exit("Need airsim — run this in the CarlaAir conda env, not the main project venv")
try:
    import carla
except ImportError:
    sys.exit("Need carla — run this in the CarlaAir conda env, not the main project venv")
try:
    import pynput.keyboard as kb
except ImportError:
    sys.exit("Need pynput for keyboard control (pip install pynput)")

from fly_and_capture import spawn_traffic, JPEG_SAVE_PARAMS

DATA_EXPORT_DIR = Path(__file__).resolve().parents[1] / "data_export" / "recorded_flights"


def find_drone_actor(world):
    """Same lookup CarlaAir's own examples_record_demo/record_drone.py uses —
    the AirSim drone is mirrored into CARLA's world as a real actor with a
    live-updating transform, found by type_id rather than by name/role."""
    for actor in world.get_actors():
        if "drone" in actor.type_id.lower() or "airsim" in actor.type_id.lower():
            return actor
    return None


class KeyboardControl:
    """Manual WASD+yaw+altitude control, same keymap as fly_drone_keyboard.py.

    Uses its own AirSim RPC connection — airsim's msgpackrpc client is not
    thread-safe, so sharing one connection across the control loop and the
    frame-grabber loop corrupts the connection and silently kills whichever
    thread loses the race."""

    def __init__(self, airsim_port: int, speed: float = 5.0, yaw_rate: float = 45.0, rpc_timeout: float = 5.0):
        self.client = airsim.MultirotorClient(port=airsim_port, timeout_value=rpc_timeout)
        self.client.confirmConnection()
        self.speed = speed
        self.yaw_rate = yaw_rate
        self.keys_pressed: set = set()
        self.running = True
        self.airborne = False
        self.listener = kb.Listener(on_press=self._on_press, on_release=self._on_release)

    def start(self) -> None:
        self.listener.start()

    def stop(self) -> None:
        self.listener.stop()

    def _on_press(self, key) -> None:
        try:
            k = key.char
        except AttributeError:
            if key == kb.Key.space:
                self.keys_pressed.add(kb.Key.space)
            elif key in (kb.Key.shift, kb.Key.shift_l, kb.Key.shift_r):
                self.keys_pressed.add(kb.Key.shift)
            elif key == kb.Key.esc:
                self.running = False
            return

        if k == "t" and not self.airborne:
            self.airborne = True
            print("  Taking off...")
            self.client.takeoffAsync()
        elif k == "l" and self.airborne:
            self.airborne = False
            print("  Landing...")
            self.client.landAsync()
        elif k not in ("t", "l"):
            self.keys_pressed.add(k)

    def _on_release(self, key) -> None:
        try:
            self.keys_pressed.discard(key.char)
        except AttributeError:
            if key == kb.Key.space:
                self.keys_pressed.discard(kb.Key.space)
            elif key in (kb.Key.shift, kb.Key.shift_l, kb.Key.shift_r):
                self.keys_pressed.discard(kb.Key.shift)

    def control_loop(self) -> None:
        """Run in its own thread. Polls pressed keys at 10Hz but only issues
        a command when it changes, or periodically to refresh before the
        previous command's duration lapses — see live_fly_and_track.py's
        matching docstring for why a constant command stream would starve
        camera capture on this build's single UE4 game thread."""
        poll_interval = 0.1
        duration = 0.3
        last_cmd = None
        last_sent_at = 0.0

        while self.running:
            vx = vy = vz = yaw = 0.0
            if "w" in self.keys_pressed:
                vx = self.speed
            if "s" in self.keys_pressed:
                vx = -self.speed
            if "d" in self.keys_pressed:
                vy = self.speed
            if "a" in self.keys_pressed:
                vy = -self.speed
            if kb.Key.space in self.keys_pressed:
                vz = -self.speed
            if kb.Key.shift in self.keys_pressed:
                vz = self.speed
            if "q" in self.keys_pressed:
                yaw = -self.yaw_rate
            if "e" in self.keys_pressed:
                yaw = self.yaw_rate

            cmd = (vx, vy, vz, yaw)
            now = time.perf_counter()
            if cmd != last_cmd or (now - last_sent_at) >= duration * 0.8:
                try:
                    self.client.moveByVelocityBodyFrameAsync(vx, vy, vz, duration, yaw_mode=airsim.YawMode(True, yaw))
                    last_cmd = cmd
                    last_sent_at = now
                except Exception as e:
                    print(f"\n[control] RPC error, continuing: {e}")
            time.sleep(poll_interval)


class CarlaCameraGrabber:
    """Streams the drone's camera via a CARLA sensor.camera.rgb attached
    directly to the drone's CARLA-side actor — push-based, no per-frame
    request/response round trip to time out (unlike AirSim's simGetImages,
    which stalls once the drone is airborne on this build). Saves every
    frame CARLA renders, plus its arrival time relative to recording start,
    so process_recorded_flight.py can reconstruct accurate per-frame
    timestamps instead of assuming a fixed fps."""

    def __init__(self, world, drone_actor, frames_dir: Path, frame_times_path: Path,
                 start_time: float, width: int = 1920, height: int = 1080, fov: float = 90.0):
        bp = world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        bp.set_attribute("fov", str(fov))
        bp.set_attribute("sensor_tick", "0.0")  # 0.0 = every render tick, the fastest CARLA can push frames
        nadir_transform = carla.Transform(carla.Location(x=0, y=0, z=-0.2), carla.Rotation(pitch=-90))
        self.camera = world.spawn_actor(bp, nadir_transform, attach_to=drone_actor,
                                         attachment_type=carla.AttachmentType.Rigid)

        self.frames_dir = frames_dir
        self.start_time = start_time
        self.lock = threading.Lock()
        self.saved_count = 0
        self._times_file = open(frame_times_path, "w", newline="")
        self._times_file.write("frame,time_s\n")
        self.camera.listen(self._on_image)

    def _on_image(self, image) -> None:
        arr = np.frombuffer(image.raw_data, dtype=np.uint8).reshape(image.height, image.width, 4)
        bgr = np.ascontiguousarray(arr[:, :, :3])  # CARLA raw_data is BGRA — drop alpha, already BGR

        with self.lock:
            idx = self.saved_count
            self.saved_count += 1
            t = round(time.perf_counter() - self.start_time, 4)
            self._times_file.write(f"{idx},{t}\n")
        cv2.imwrite(str(self.frames_dir / f"{idx:05d}.jpg"), bgr, JPEG_SAVE_PARAMS)

    def stop(self) -> None:
        self.camera.stop()
        self.camera.destroy()
        self._times_file.close()


def build_review_video(frames_dir: Path, video_path: Path, nominal_fps: float) -> None:
    """Human-review-only mp4, same caveat as fly_and_capture.py's flight.mp4:
    a second, lossier re-encode at a nominal (not measured) fps. Detection
    always runs on the frames/ JPGs directly, never this file."""
    frame_paths = sorted(frames_dir.glob("*.jpg"))
    if not frame_paths:
        return
    first = cv2.imread(str(frame_paths[0]))
    h, w = first.shape[:2]
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), nominal_fps, (w, h))
    for p in frame_paths:
        writer.write(cv2.imread(str(p)))
    writer.release()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--airsim-port", type=int, default=41451)
    ap.add_argument("--vehicles", type=int, default=15)
    ap.add_argument("--rpc-timeout", type=float, default=5.0)
    ap.add_argument("--review-fps", type=float, default=15.0,
                     help="Nominal playback fps for the review-only flight.mp4 (actual capture rate varies)")
    args = ap.parse_args()

    carla_client = carla.Client(args.host, args.port)
    carla_client.set_timeout(15)
    world = carla_client.get_world()
    map_name = world.get_map().name
    print(f"[carla] connected, map={map_name}")
    vehicles = spawn_traffic(world, carla_client, args.vehicles)

    run_id = time.strftime("%Y%m%d_%H%M%S")
    out_dir = DATA_EXPORT_DIR / run_id
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    client = airsim.MultirotorClient(port=args.airsim_port, timeout_value=args.rpc_timeout)
    client.confirmConnection()
    client.enableApiControl(True)
    client.armDisarm(True)

    print("Locating drone actor in CARLA...")
    drone_actor = None
    for _ in range(20):
        drone_actor = find_drone_actor(world)
        if drone_actor is not None:
            break
        time.sleep(0.5)
    if drone_actor is None:
        raise SystemExit("Drone actor not found in CARLA. Is CarlaAir running with AirSim enabled?")
    print(f"[carla] drone actor: id={drone_actor.id} type={drone_actor.type_id}")

    start_time = time.perf_counter()
    grabber = CarlaCameraGrabber(world, drone_actor, frames_dir, out_dir / "frame_times.csv", start_time)

    kbd = KeyboardControl(args.airsim_port, rpc_timeout=args.rpc_timeout)
    kbd.start()
    control_thread = threading.Thread(target=kbd.control_loop, daemon=True)
    control_thread.start()

    print("=" * 50)
    print("  Manual flight recording (no live detection)")
    print("  W/S/A/D move, Space/Shift up/down, Q/E yaw, T takeoff, L land")
    print("  ESC to stop and save")
    print("  Watch the CarlaUE4 window while flying — this script has no display of its own")
    print("=" * 50)

    try:
        while kbd.running:
            sys.stdout.write(f"\r  Recording... {grabber.saved_count} frames saved")
            sys.stdout.flush()
            time.sleep(0.2)
    finally:
        print("\n[drone] landing ...")
        try:
            client.landAsync().join()
            client.armDisarm(False)
            client.enableApiControl(False)
        except Exception:
            pass
        kbd.stop()
        grabber.stop()

        for v in vehicles:
            try:
                v.destroy()
            except Exception:
                pass

        if grabber.saved_count == 0:
            raise SystemExit("No frames captured — check the drone took off and the camera attached correctly.")

        elapsed = time.perf_counter() - start_time
        avg_fps = grabber.saved_count / elapsed if elapsed > 0 else 0.0

        print(f"[capture] building review video from {grabber.saved_count} frames ...")
        build_review_video(frames_dir, out_dir / "flight.mp4", args.review_fps)

        metadata = {
            "map": map_name,
            "n_traffic_vehicles": len(vehicles),
            "n_frames_saved": grabber.saved_count,
            "elapsed_s": round(elapsed, 2),
            "avg_fps": round(avg_fps, 2),
        }
        (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
        print(f"[done] {grabber.saved_count} frames -> {frames_dir}")
        print(f"       frame_times.csv -> {out_dir / 'frame_times.csv'}")
        print(f"       metadata.json   -> {out_dir / 'metadata.json'}")


if __name__ == "__main__":
    main()
