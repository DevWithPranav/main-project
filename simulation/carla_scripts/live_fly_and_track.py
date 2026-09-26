"""Stage 6 — manual flight + live detection/tracking in a single pipeline.

You fly the drone by hand (watching the CarlaUE4 window) while this script
simultaneously runs the Stage 1/3 detector+tracker on the live camera feed
and shows the annotated result in its own window, updating in real time.
Recording (raw frames + trajectory CSV) happens automatically in the
background while you fly — there's no separate "record then analyze" step.

Unlike fly_and_capture.py / run_sim_validation.py (which split simulate and
detect across two Python environments because the CARLA wheel is Python
3.10-only and ultralytics/torch live in the project's 3.14 venv), this
script needs both in the SAME process. Install ultralytics + a CUDA torch
build into the CarlaAir distribution's own `carlaAir` conda env first:

    conda activate carlaAir
    pip install torch --index-url https://download.pytorch.org/whl/cu121
    pip install ultralytics
    :: (two separate installs — pip install X Y --index-url <url> restricts
    :: BOTH packages to that index, and PyTorch's index doesn't host ultralytics)

Controls (same keymap as examples/fly_drone_keyboard.py):
    W/S         Forward / Backward
    A/D         Left / Right
    Space       Ascend
    Shift       Descend
    Q/E         Rotate left / right
    T           Takeoff
    L           Land
    ESC         Quit (in the CarlaAir window) — or press 'q' in the live
                detection window; either stops and saves.

Usage (with CarlaAir already running, e.g. `StartCarlaAir.bat Town10HD`):
    python live_fly_and_track.py
    python live_fly_and_track.py --vehicles 15
"""

import argparse
import csv
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
try:
    import torch
except ImportError:
    sys.exit("Need torch — run this in the CarlaAir conda env, not the main project venv")
try:
    from ultralytics import YOLO
except ImportError:
    sys.exit(
        "Need ultralytics in this env — see the module docstring for the "
        "one-time `pip install ultralytics torch ...` into the carlaAir conda env"
    )

from fly_and_capture import spawn_traffic, JPEG_SAVE_PARAMS

BEST_PT = Path(__file__).resolve().parents[2] / "ml" / "data" / "results" / "full_train" / "train" / "weights" / "best.pt"
DATA_EXPORT_DIR = Path(__file__).resolve().parents[1] / "data_export" / "live_flights"
VEHICLE_CLASS_IDS = [3, 4, 5, 8]  # car, van, truck, bus — matches ml/violation_engine/extract_trajectories.py


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
    frame-grabber loop corrupts the connection (BufferError / tornado
    iostream crashes) and silently kills whichever thread loses the race."""

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

        # OS key-repeat fires on_press repeatedly while held — debounce so a
        # long T/L press doesn't queue dozens of takeoff/land RPC calls.
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
        """Run in its own thread. AirSim marshals every RPC call (movement AND
        camera capture) onto UE4's single game thread, so a constant stream of
        movement commands can perpetually starve the much more expensive
        camera-capture request of a turn — the capture thread would then time
        out forever, even on the very first frame. Polls pressed keys at 10Hz
        for responsiveness, but only actually issues a command when the
        velocity/yaw changes, or periodically to refresh before the previous
        command's duration lapses — not on every tick."""
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
    directly to the drone's CARLA-side actor, instead of AirSim's
    simGetImages RPC screenshot call.

    Three fixes were tried for simGetImages stalling permanently the moment
    flight began (thread isolation, RPC throttling, PNG compression) and
    none of them held — the AirSim RPC image path itself is unreliable
    under this build once the drone is actually flying. CARLA's sensor
    streaming is push-based (the server sends frames as they render, no
    per-frame request/response round trip to time out) and this same
    distribution already proves it works fine on an actively-moving actor:
    examples/data_collector.py streams RGB/depth/segmentation cameras from
    a vehicle while it's autopilot-driving. AirSim is still used for flight
    control (KeyboardControl) — only image acquisition moved to CARLA."""

    def __init__(self, world, drone_actor, frames_dir: Path,
                 width: int = 1920, height: int = 1080, fov: float = 90.0):
        bp = world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        bp.set_attribute("fov", str(fov))
        bp.set_attribute("sensor_tick", "0.0")  # 0.0 = every render tick, the fastest CARLA can push frames
        # Small offset below the drone body, pitched straight down (nadir).
        nadir_transform = carla.Transform(carla.Location(x=0, y=0, z=-0.2), carla.Rotation(pitch=-90))
        self.camera = world.spawn_actor(bp, nadir_transform, attach_to=drone_actor,
                                         attachment_type=carla.AttachmentType.Rigid)

        self.frames_dir = frames_dir
        self.lock = threading.Lock()
        self.frame = None
        self.version = 0
        self.saved_count = 0
        self.camera.listen(self._on_image)

    def _on_image(self, image) -> None:
        arr = np.frombuffer(image.raw_data, dtype=np.uint8).reshape(image.height, image.width, 4)
        bgr = np.ascontiguousarray(arr[:, :, :3])  # CARLA raw_data is BGRA — drop alpha, already BGR

        # Save every frame CARLA renders right here, instead of in the main
        # loop — the main loop only ever looks at the latest frame (by
        # design, so inference doesn't lag further and further behind real
        # time), which would silently drop frames from the *saved* archive
        # too if saving happened there instead. This decouples "record
        # everything" from "detect/display whatever's freshest."
        with self.lock:
            self.frame = bgr
            self.version += 1
            idx = self.saved_count
            self.saved_count += 1
        cv2.imwrite(str(self.frames_dir / f"{idx:05d}.jpg"), bgr, JPEG_SAVE_PARAMS)

    def latest(self):
        with self.lock:
            return self.frame, self.version

    def stop(self) -> None:
        self.camera.stop()
        self.camera.destroy()


BOX_COLOR = (60, 220, 60)


def draw_boxes(frame, boxes) -> None:
    for track_id, cls_name, x1, y1, x2, y2, conf in boxes:
        p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
        cv2.rectangle(frame, p1, p2, BOX_COLOR, 2)
        label = f"#{track_id} {cls_name} {conf:.2f}"
        cv2.putText(frame, label, (p1[0], max(0, p1[1] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, BOX_COLOR, 1, cv2.LINE_AA)


class InferenceWorker:
    """Runs detection/tracking on its own thread, decoupled from capture.
    Each result is paired with the exact frame it was computed on (self.frame),
    not whatever the newest captured frame happens to be — the display loop
    shows that pair together, so boxes are always pixel-exact for what's on
    screen. This trades instantaneous video for correct boxes: display trails
    live capture by one inference pass, instead of live video with stale/
    extrapolated boxes drawn on top (tried and reverted — accurate boxes
    matter more here than shaving off the display lag, since flying is done
    by watching the separate CarlaUE4 window, not this one)."""

    def __init__(self, model, grabber, csv_writer, start_time: float):
        self.model = model
        self.grabber = grabber
        self.csv_writer = csv_writer
        self.start_time = start_time
        self.lock = threading.Lock()
        self.boxes: list = []
        self.frame = None
        self.version = 0
        self.processed_count = 0
        self.running = True
        # half=True (fp16) only works on a CUDA device — on CPU it raises,
        # which used to kill this thread silently (it's a daemon thread, so
        # the window keeps showing video with zero boxes forever, no crash).
        self.use_half = torch.cuda.is_available()
        if not self.use_half:
            print("[inference] no CUDA device found — running fp32 on CPU (slower); "
                  "check `conda activate carlaAir` has a CUDA torch build installed.")

    def loop(self) -> None:
        last_seen_version = 0
        while self.running:
            frame, version = self.grabber.latest()
            if frame is None or version == last_seen_version:
                time.sleep(0.005)
                continue
            last_seen_version = version

            try:
                results = self.model.track(
                    frame, tracker="bytetrack.yaml", classes=VEHICLE_CLASS_IDS,
                    persist=True, verbose=False, imgsz=640, half=self.use_half,
                )
            except Exception:
                import traceback
                traceback.print_exc()
                print("[inference] error above — skipping this frame, worker keeps running")
                continue
            result = results[0]
            boxes_out = []
            if result.boxes.id is not None:
                time_s = round(time.perf_counter() - self.start_time, 3)
                names = result.names
                for box, track_id in zip(result.boxes, result.boxes.id):
                    x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                    cx, cy, w, h = (float(v) for v in box.xywh[0])
                    cls_name = names[int(box.cls)]
                    conf = float(box.conf)
                    boxes_out.append((int(track_id), cls_name, x1, y1, x2, y2, conf))
                    self.csv_writer.writerow([
                        self.processed_count, time_s, int(track_id), cls_name,
                        round(cx, 1), round(cy, 1), round(w, 1), round(h, 1), round(conf, 3),
                    ])

            with self.lock:
                self.boxes = boxes_out
                self.frame = frame
                self.version += 1
            self.processed_count += 1

    def latest_boxes(self):
        with self.lock:
            return self.boxes

    def latest_boxes_and_frame(self):
        with self.lock:
            return self.boxes, self.frame, self.version


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--airsim-port", type=int, default=41451)
    ap.add_argument("--vehicles", type=int, default=15)
    ap.add_argument("--rpc-timeout", type=float, default=5.0,
                     help="Seconds before an AirSim RPC call (e.g. a camera capture stalled "
                          "by GPU contention) is treated as failed and retried, instead of "
                          "hanging (airsim's own default is 3600s, i.e. effectively forever)")
    ap.add_argument("--annotated-fps", type=float, default=15.0,
                     help="Playback fps baked into annotated.mp4. Inference (and so display) "
                          "runs at a variable, GPU-contention-dependent rate, not a fixed one — "
                          "this is a nominal encode rate, not a measured one, same simplification "
                          "fly_and_capture.py's flight.mp4 already makes for its (fixed) capture_fps.")
    args = ap.parse_args()

    if not BEST_PT.exists():
        raise SystemExit(f"{BEST_PT} not found — run ml/detection/train_full.py first.")

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

    print("Loading model...")
    model = YOLO(str(BEST_PT))

    grabber = CarlaCameraGrabber(world, drone_actor, frames_dir)

    kbd = KeyboardControl(args.airsim_port, rpc_timeout=args.rpc_timeout)
    kbd.start()
    control_thread = threading.Thread(target=kbd.control_loop, daemon=True)
    control_thread.start()

    print("=" * 50)
    print("  Live fly + detect + track")
    print("  W/S/A/D move, Space/Shift up/down, Q/E yaw, T takeoff, L land")
    print("  ESC (CarlaAir window) or 'q' (detection window) to stop and save")
    print("=" * 50)

    csv_path = out_dir / "trajectories.csv"
    csv_file = open(csv_path, "w", newline="")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow(["frame", "time_s", "track_id", "class", "cx", "cy", "w", "h", "conf"])

    start_time = time.perf_counter()
    worker = InferenceWorker(model, grabber, csv_writer, start_time)
    worker_thread = threading.Thread(target=worker.loop, daemon=True)
    worker_thread.start()

    window_name = "Live Detection"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    last_shown_version = 0
    annotated_path = out_dir / "annotated.mp4"
    annotated_writer = None  # lazily opened on the first shown frame, once we know its size

    try:
        while kbd.running:
            boxes, frame, version = worker.latest_boxes_and_frame()

            if frame is not None and version != last_shown_version:
                last_shown_version = version
                display = frame.copy()
                draw_boxes(display, boxes)
                cv2.imshow(window_name, display)

                if annotated_writer is None:
                    h, w = display.shape[:2]
                    annotated_writer = cv2.VideoWriter(
                        str(annotated_path), cv2.VideoWriter_fourcc(*"mp4v"), args.annotated_fps, (w, h))
                annotated_writer.write(display)
            else:
                time.sleep(0.005)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
    finally:
        kbd.running = False
        worker.running = False
        worker_thread.join(timeout=2.0)
        grabber.stop()
        csv_file.close()
        cv2.destroyAllWindows()
        if annotated_writer is not None:
            annotated_writer.release()

        print("\n[drone] landing ...")
        try:
            client.landAsync().join()
            client.armDisarm(False)
            client.enableApiControl(False)
        except Exception:
            pass
        kbd.stop()

        for v in vehicles:
            try:
                v.destroy()
            except Exception:
                pass

        metadata = {
            "map": map_name,
            "n_traffic_vehicles": len(vehicles),
            "n_frames_saved": grabber.saved_count,
            "n_frames_processed": worker.processed_count,
        }
        (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
        print(f"[done] {grabber.saved_count} frames saved, {worker.processed_count} processed, "
              f"trajectories -> {csv_path}, annotated video -> {annotated_path}")


if __name__ == "__main__":
    main()
