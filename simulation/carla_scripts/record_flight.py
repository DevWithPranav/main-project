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

A gamepad works at the same time (proportional sticks and triggers; layout
in gamepad_control.py): left stick forward/strafe, right stick yaw, RT climb,
LT descend, LB slow / RB fast, A takeoff, B land, Start stop and save. Keyboard
movement keys take priority over the sticks while held.

Usage (with CarlaAir already running, e.g. `StartCarlaAir.bat Town10HD`):
    python record_flight.py
    python record_flight.py --vehicles 15
    python record_flight.py --no-gamepad
    python record_flight.py --vehicles 0 --labels   # + seg/ and actors.json for automatic labels

Flying and recording in separate processes (smoother controls with --labels, since the
camera callbacks can't starve the control loop): start_carla_and_fly.bat (or fly_drone.py)
flies the drone; then record with
    python record_flight.py --vehicles 0 --labels --no-control
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

    def __init__(self, airsim_port: int, speed: float = 5.0, yaw_rate: float = 45.0, rpc_timeout: float = 5.0,
                 use_gamepad: bool = True, allow_quit: bool = True):
        self.client = airsim.MultirotorClient(port=airsim_port, timeout_value=rpc_timeout)
        self.client.confirmConnection()
        self.speed = speed
        self.yaw_rate = yaw_rate
        self.use_gamepad = use_gamepad
        # False in fly_drone.py: there Esc / Start belong to the separate recorder, not the flight controls
        self.allow_quit = allow_quit
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
            elif key == kb.Key.esc and self.allow_quit:
                self.running = False
            return

        if k == "t":
            self.take_off()
        elif k == "l":
            self.land()
        else:
            self.keys_pressed.add(k)

    def take_off(self) -> None:
        if not self.airborne:
            self.airborne = True
            print("  Taking off...")
            self.client.takeoffAsync()

    def land(self) -> None:
        if self.airborne:
            self.airborne = False
            print("  Landing...")
            self.client.landAsync()

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
        # 1.0 s, not 0.3: with --labels the extra seg camera slows AirSim's RPC (it runs on
        # UE4's game thread), and a 0.3 s command often expired before the refresh landed,
        # so the drone moved in short bursts. A changed command (e.g. stick released) is
        # still sent immediately, so this doesn't add overrun.
        duration = 1.0
        last_cmd = None
        last_sent_at = 0.0

        gamepad = None
        if self.use_gamepad:
            # built here, not in __init__: pygame must be used from the thread that initialised it
            from gamepad_control import BUTTON_A, BUTTON_B, BUTTON_START, Gamepad
            gamepad = Gamepad()

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

            if gamepad is not None:
                (fwd, right, down, turn), pressed = gamepad.read()
                if BUTTON_A in pressed:
                    self.take_off()
                if BUTTON_B in pressed:
                    self.land()
                if BUTTON_START in pressed and self.allow_quit:
                    self.running = False
                if (vx, vy, vz, yaw) == (0.0, 0.0, 0.0, 0.0):  # keyboard wins while a movement key is held
                    vx, vy, vz, yaw = (fwd * self.speed, right * self.speed, down * self.speed,
                                       turn * self.yaw_rate)

            cmd = (vx, vy, vz, yaw)
            now = time.perf_counter()
            if cmd != last_cmd or (now - last_sent_at) >= duration * 0.8:
                try:
                    # moveByVelocityBodyFrameAsync returns a Future; AirSim's RPC server appears to
                    # track each outstanding "Async" call until the client consumes it (.join()).
                    # This loop used to fire-and-forget one every ~0.8-1s forever, even at rest —
                    # confirmed (2026-10-01) that alone is enough to degrade AirSim's RPC server to
                    # fully unresponsive within 15-20s, with nothing else running. Joining it (with
                    # its own short timeout, separate from the move's 1 s duration) clears it
                    # immediately instead of leaving it outstanding.
                    future = self.client.moveByVelocityBodyFrameAsync(vx, vy, vz, duration,
                                                                       yaw_mode=airsim.YawMode(True, yaw))
                    future.join()  # takes no arguments — msgpackrpc.future.Future.join(self) only
                    last_cmd = cmd
                    last_sent_at = now
                except Exception as e:
                    print(f"\n[control] RPC error, continuing: {e}")
            time.sleep(poll_interval)


def camera_mount(pitch: float = -90.0):
    """Camera pose relative to the drone: just under it, pointing forward and tilted down by
    `pitch` (-90 = straight down, -45 = oblique). RGB and seg cameras must share it exactly."""
    return carla.Transform(carla.Location(x=0, y=0, z=-0.2), carla.Rotation(pitch=pitch))


# weather presets usable with --weather (carla.WeatherParameters attributes), plus two made up here
WEATHER_EXTRA = {
    "Fog": dict(cloudiness=60.0, fog_density=35.0, fog_distance=20.0, sun_altitude_angle=40.0),
    "Night": dict(cloudiness=10.0, sun_altitude_angle=-30.0),
}


def apply_weather(world, name: str | None, sun_altitude: float | None) -> dict:
    """Set the world's weather from a preset (e.g. ClearNoon, CloudySunset, WetNoon, MidRainyNoon,
    Fog, Night), optionally overriding the sun altitude; returns the resulting parameters."""
    if name:
        if name in WEATHER_EXTRA:
            w = world.get_weather()
            for k, v in WEATHER_EXTRA[name].items():
                setattr(w, k, v)
        elif hasattr(carla.WeatherParameters, name):
            w = getattr(carla.WeatherParameters, name)
        else:
            presets = sorted(n for n in dir(carla.WeatherParameters) if n[0].isupper()) + list(WEATHER_EXTRA)
            raise SystemExit(f"Unknown --weather {name}. Choose from: {', '.join(presets)}")
        world.set_weather(w)
    if sun_altitude is not None:
        w = world.get_weather()
        w.sun_altitude_angle = sun_altitude
        world.set_weather(w)
    w = world.get_weather()
    return {k: round(getattr(w, k), 2) for k in ("cloudiness", "precipitation", "precipitation_deposits",
                                                  "wetness", "fog_density", "fog_distance",
                                                  "sun_altitude_angle", "sun_azimuth_angle")}


def set_vehicle_lights(world, on: bool) -> None:
    """Headlights/position lights on every vehicle, for dusk and night flights."""
    state = carla.VehicleLightState(carla.VehicleLightState.Position | carla.VehicleLightState.LowBeam) if on \
        else carla.VehicleLightState.NONE
    for v in world.get_actors().filter("vehicle.*"):
        try:
            v.set_light_state(state)
        except Exception:
            pass


class EscStop:
    """--no-control: only listens for Esc (global hotkey) to stop the recording."""

    def __init__(self):
        self.running = True
        self.listener = kb.Listener(on_press=self._on_press)

    def _on_press(self, key) -> None:
        if key == kb.Key.esc:
            self.running = False

    def start(self) -> None:
        self.listener.start()

    def stop(self) -> None:
        self.listener.stop()


class CarlaCameraGrabber:
    """Streams the drone's camera via a CARLA sensor.camera.rgb attached
    directly to the drone's CARLA-side actor — push-based, no per-frame
    request/response round trip to time out (unlike AirSim's simGetImages,
    which stalls once the drone is airborne on this build). Saves every
    frame CARLA renders, plus its arrival time relative to recording start,
    so process_recorded_flight.py can reconstruct accurate per-frame
    timestamps instead of assuming a fixed fps."""

    def __init__(self, world, drone_actor, frames_dir: Path, frame_times_path: Path,
                 start_time: float, width: int = 1920, height: int = 1080, fov: float = 90.0, pitch: float = -90.0):
        bp = world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        bp.set_attribute("fov", str(fov))
        bp.set_attribute("sensor_tick", "0.0")  # 0.0 = every render tick, the fastest CARLA can push frames
        self.camera = world.spawn_actor(bp, camera_mount(pitch), attach_to=drone_actor,
                                         attachment_type=carla.AttachmentType.Rigid)

        self.frames_dir = frames_dir
        self.start_time = start_time
        self.lock = threading.Lock()
        self.saved_count = 0
        self._times_file = open(frame_times_path, "w", newline="")
        # carla_frame pairs each frame with its seg/ image; sim_time and the camera's world pose on
        # every frame let ml/violation_engine/ground_coords.py project pixels to metres without
        # interpolating between the sparser seg-tick poses in camera_poses.csv
        self._times_file.write("frame,time_s,carla_frame,sim_time,x,y,z,pitch,yaw,roll\n")
        self.camera.listen(self._on_image)

    def _on_image(self, image) -> None:
        arr = np.frombuffer(image.raw_data, dtype=np.uint8).reshape(image.height, image.width, 4)
        bgr = np.ascontiguousarray(arr[:, :, :3])  # CARLA raw_data is BGRA — drop alpha, already BGR
        tf = image.transform
        pose = ",".join(f"{v:.4f}" for v in (tf.location.x, tf.location.y, tf.location.z,
                                              tf.rotation.pitch, tf.rotation.yaw, tf.rotation.roll))

        with self.lock:
            idx = self.saved_count
            self.saved_count += 1
            t = round(time.perf_counter() - self.start_time, 4)
            self._times_file.write(f"{idx},{t},{image.frame},{image.timestamp:.4f},{pose}\n")
        cv2.imwrite(str(self.frames_dir / f"{idx:05d}.jpg"), bgr, JPEG_SAVE_PARAMS)

    def stop(self) -> None:
        self.camera.stop()
        self.camera.destroy()
        self._times_file.close()


class SegCameraGrabber:
    """--labels: a sensor.camera.instance_segmentation with exactly the RGB
    camera's size, FOV and mount, saved losslessly as seg/<carla_frame>.png.
    R = semantic tag, G + 256*B = the object's instance id (low 16 bits of the
    actor id). ml/detection/carla_autolabel.py pairs these with frames/ via
    frame_times.csv's carla_frame column and turns them into boxes."""

    SENSOR = "sensor.camera.instance_segmentation"

    def __init__(self, world, drone_actor, seg_dir: Path, width: int = 1920, height: int = 1080, fov: float = 90.0,
                 interval: float = 0.2, pitch: float = -90.0):
        bp_lib = world.get_blueprint_library()
        if not bp_lib.filter(self.SENSOR):
            raise SystemExit(f"This CARLA build has no {self.SENSOR} (needs CARLA >= 0.9.14)")
        bp = bp_lib.find(self.SENSOR)
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        bp.set_attribute("fov", str(fov))
        # not every tick: a second 1080p render per tick halves the sim rate and starves AirSim's
        # control RPC; training keeps <= 1 frame/s anyway
        bp.set_attribute("sensor_tick", str(interval))
        self.camera = world.spawn_actor(bp, camera_mount(pitch), attach_to=drone_actor,
                                         attachment_type=carla.AttachmentType.Rigid)
        self.seg_dir = seg_dir
        self.saved_count = 0
        self.poses: dict[int, tuple] = {}  # carla_frame -> camera world pose, for projecting vehicle 3D boxes
        self.camera.listen(self._on_image)

    def _on_image(self, image) -> None:
        arr = np.frombuffer(image.raw_data, dtype=np.uint8).reshape(image.height, image.width, 4)
        cv2.imwrite(str(self.seg_dir / f"{image.frame}.png"), np.ascontiguousarray(arr[:, :, :3]),
                    [cv2.IMWRITE_PNG_COMPRESSION, 1])  # PNG: ids must survive exactly, JPEG would corrupt them
        tf = image.transform
        self.poses[image.frame] = (tf.location.x, tf.location.y, tf.location.z,
                                   tf.rotation.pitch, tf.rotation.yaw, tf.rotation.roll)
        self.saved_count += 1

    def stop(self) -> None:
        self.camera.stop()
        self.camera.destroy()

    def write_poses(self, path: Path) -> None:
        with open(path, "w", newline="") as f:
            f.write("carla_frame,x,y,z,pitch,yaw,roll\n")
            for fr in sorted(self.poses):
                f.write(f"{fr}," + ",".join(f"{v:.4f}" for v in self.poses[fr]) + "\n")


class DepthCameraGrabber(SegCameraGrabber):
    """--labels: a sensor.camera.depth with the same size, FOV, mount and interval as the seg
    camera, saved losslessly as depth/<carla_frame>.png (CARLA's 24-bit encoding:
    depth_m = (R + 256*G + 65536*B) / (2^24 - 1) * 1000). The seg camera sees through
    alpha-masked surfaces (tree leaves, gaps in the rail bridge), the depth camera does not,
    so carla_autolabel.py drops seg pixels that have something closer in front of them."""

    SENSOR = "sensor.camera.depth"


class VehiclePoseLogger:
    """--labels: every vehicle's world pose on every simulator tick (world.on_tick),
    so carla_autolabel.py can project each vehicle's full 3D box into the seg frame
    of the same tick and measure how much of it is actually visible.

    The vehicle list is **not** fixed at construction: it grows as new vehicles appear, so a
    background traffic tool (focus_traffic.py --watch) recycling vehicles mid-recording doesn't
    leave orphans with no pose data. A vehicle spawned and destroyed entirely between two
    metadata scans (default every ~2s) would still be missed — for a flight recorded with
    --watch, still check autolabel/check.mp4 for any vehicle carla_autolabel.py couldn't match
    to an actor or a map object (shown as an unmatched "P#" with a low --ignore-vehicles-pct
    lifetime, i.e. it existed only briefly)."""

    def __init__(self, world, known_vehicles: dict, rescan_every: float = 2.0):
        self.world = world
        self.known = known_vehicles  # id -> {type_id, base_type, bbox}; shared with the caller, grown in place
        self.ids: set[int] = set(int(k) for k in known_vehicles)
        self.frames: dict[int, list] = {}
        self.rescan_every = rescan_every
        self._last_scan = 0.0
        self._cb = world.on_tick(self._on_tick)

    def _rescan(self) -> None:
        for a in self.world.get_actors().filter("vehicle.*"):
            if a.id not in self.ids:
                self.ids.add(a.id)
                self.known[a.id] = {"type_id": a.type_id, "base_type": a.attributes.get("base_type", ""),
                                    "bbox": _bbox_dict(a.bounding_box)}

    def _on_tick(self, snapshot) -> None:
        now = snapshot.timestamp.elapsed_seconds
        if now - self._last_scan >= self.rescan_every:
            self._last_scan = now
            self._rescan()
        rows = []
        for vid in self.ids:
            s = snapshot.find(vid)
            if s is None:
                continue
            tf = s.get_transform()
            rows.append((vid, tf.location.x, tf.location.y, tf.location.z,
                         tf.rotation.pitch, tf.rotation.yaw, tf.rotation.roll))
        self.frames[snapshot.frame] = rows

    def stop_and_write(self, path: Path, keep_frames: set) -> None:
        self.world.remove_on_tick(self._cb)
        with open(path, "w", newline="") as f:
            f.write("carla_frame,id,x,y,z,pitch,yaw,roll\n")
            for fr in sorted(keep_frames & set(self.frames)):
                for vid, *p in self.frames[fr]:
                    f.write(f"{fr},{vid}," + ",".join(f"{v:.4f}" for v in p) + "\n")


def _bbox_dict(bb) -> dict:
    return {"loc": [bb.location.x, bb.location.y, bb.location.z],
            "ext": [bb.extent.x, bb.extent.y, bb.extent.z],
            "rot": [bb.rotation.pitch, bb.rotation.yaw, bb.rotation.roll]}


def snapshot_vehicles(world, known: dict) -> None:
    """Record id -> blueprint/base_type/3D box for every vehicle actor in the world
    (ours and auto_traffic.py's), so carla_autolabel.py can apply the class
    rules (vans/pickups -> car) per instance and project the full box. Called at
    start and end; a vehicle destroyed mid-flight keeps its entry from the earlier call.
    The box is relative to the actor (loc/rot in the actor's frame)."""
    for a in world.get_actors().filter("vehicle.*"):
        known[a.id] = {"type_id": a.type_id, "base_type": a.attributes.get("base_type", ""),
                       "bbox": _bbox_dict(a.bounding_box)}


def map_vehicles(world) -> list:
    """Parked vehicles baked into the map are not actors, but CARLA lists them as
    environment objects with world-space 3D boxes. carla_autolabel.py matches
    them to seg instances by projection (their seg instance ids are unrelated)."""
    out = []
    for name in ("Car", "Truck", "Bus"):
        label = getattr(carla.CityObjectLabel, name, None)
        if label is None:
            continue
        for obj in world.get_environment_objects(label):
            out.append({"id": obj.id, "name": obj.name, "label": name.lower(), "bbox": _bbox_dict(obj.bounding_box)})
    return out


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
    ap.add_argument("--no-gamepad", action="store_true", help="Keyboard only; don't read a game controller")
    ap.add_argument("--labels", action="store_true",
                    help="Also record an instance-segmentation camera (seg/) and the vehicle list (actors.json) "
                         "for automatic labels; process with ml/detection/carla_autolabel.py")
    ap.add_argument("--label-interval", type=float, default=0.2,
                    help="Seconds between seg frames with --labels (0 = every tick; slows the sim and the controls)")
    ap.add_argument("--pitch", type=float, default=-90.0,
                    help="Camera tilt: -90 straight down (default), -45 to -70 oblique. Fixed for the whole recording")
    ap.add_argument("--weather", default=None,
                    help="Weather preset set at the start: ClearNoon, CloudyNoon, ClearSunset, CloudySunset, WetNoon, "
                         "WetCloudyNoon, MidRainyNoon, SoftRainNoon, Fog, Night, ... (default: leave as is)")
    ap.add_argument("--sun-altitude", type=float, default=None,
                    help="Override the sun altitude in degrees (e.g. 10 for long shadows, -5 dusk, -30 night)")
    ap.add_argument("--no-autolabel", action="store_true",
                    help="With --labels: don't run carla_autolabel.py (labels + check video) after saving")
    ap.add_argument("--no-control", action="store_true",
                    help="Record only: the drone is flown by fly_drone.py in its own process. Esc stops "
                         "recording; the drone is not landed or disarmed")
    args = ap.parse_args()

    carla_client = carla.Client(args.host, args.port)
    carla_client.set_timeout(15)
    world = carla_client.get_world()
    map_name = world.get_map().name
    print(f"[carla] connected, map={map_name}")
    vehicles = spawn_traffic(world, carla_client, args.vehicles)
    weather = apply_weather(world, args.weather, args.sun_altitude)
    lights_on = weather["sun_altitude_angle"] < 5
    if lights_on:
        set_vehicle_lights(world, True)
    print(f"[weather] {args.weather or 'unchanged'}: sun {weather['sun_altitude_angle']} deg, "
          f"fog {weather['fog_density']}, rain {weather['precipitation']}, vehicle lights {'on' if lights_on else 'off'}")

    run_id = time.strftime("%Y%m%d_%H%M%S")
    out_dir = DATA_EXPORT_DIR / run_id
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    client = None
    if not args.no_control:
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
    grabber = CarlaCameraGrabber(world, drone_actor, frames_dir, out_dir / "frame_times.csv", start_time,
                                 pitch=args.pitch)
    seg_grabber = None
    depth_grabber = None
    pose_logger = None
    known_vehicles: dict = {}
    if args.labels:
        (out_dir / "seg").mkdir(exist_ok=True)
        (out_dir / "depth").mkdir(exist_ok=True)
        snapshot_vehicles(world, known_vehicles)
        baked = map_vehicles(world)
        (out_dir / "map_vehicles.json").write_text(json.dumps(baked, indent=1))
        pose_logger = VehiclePoseLogger(world, known_vehicles)
        # spawned back to back so both usually fire on the same ticks; the converter also
        # accepts a depth frame a tick or two off
        seg_grabber = SegCameraGrabber(world, drone_actor, out_dir / "seg", interval=args.label_interval,
                                       pitch=args.pitch)
        depth_grabber = DepthCameraGrabber(world, drone_actor, out_dir / "depth", interval=args.label_interval,
                                           pitch=args.pitch)
        print(f"[labels] instance-segmentation + depth cameras on, {len(known_vehicles)} vehicles + "
              f"{len(baked)} map-baked vehicles in the world")

    if args.no_control:
        kbd = EscStop()
    else:
        kbd = KeyboardControl(args.airsim_port, rpc_timeout=args.rpc_timeout, use_gamepad=not args.no_gamepad)
        threading.Thread(target=kbd.control_loop, daemon=True).start()
    kbd.start()

    print("=" * 50)
    print("  Manual flight recording (no live detection)")
    if args.no_control:
        print("  Recording only — fly with fly_drone.py. ESC (or Ctrl+C here) stops and saves")
    else:
        print("  W/S/A/D move, Space/Shift up/down, Q/E yaw, T takeoff, L land")
        print("  ESC to stop and save")
        if not args.no_gamepad:
            print("  Gamepad: L-stick move, R-stick yaw, RT up, LT down, LB slow, RB fast, A takeoff, B land, Start stop")
    print("  Watch the CarlaUE4 window while flying — this script has no display of its own")
    print("=" * 50)

    try:
        while kbd.running:
            sys.stdout.write(f"\r  Recording... {grabber.saved_count} frames saved")
            sys.stdout.flush()
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if client is not None:
            print("\n[drone] landing ...")
            try:
                client.landAsync().join()
                client.armDisarm(False)
                client.enableApiControl(False)
            except Exception:
                pass
        else:
            print("\n[recording] stopped — drone left as it is (fly_drone.py still has control)")
        kbd.stop()
        grabber.stop()
        if seg_grabber is not None:
            seg_grabber.stop()
            depth_grabber.stop()
            seg_grabber.write_poses(out_dir / "camera_poses.csv")
            pose_logger.stop_and_write(out_dir / "vehicle_poses.csv", set(seg_grabber.poses))
            snapshot_vehicles(world, known_vehicles)
            (out_dir / "actors.json").write_text(json.dumps({str(k): v for k, v in known_vehicles.items()}, indent=1))
            print(f"[labels] {seg_grabber.saved_count} seg + {depth_grabber.saved_count} depth frames, "
                  f"{len(known_vehicles)} vehicles -> actors.json")

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
            "labels": bool(args.labels),
            "camera": {"width": 1920, "height": 1080, "fov": 90.0, "pitch": args.pitch},
            "weather_preset": args.weather,
            "weather": weather,
            "vehicle_lights": lights_on,
        }
        (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
        print(f"[done] {grabber.saved_count} frames -> {frames_dir}")
        print(f"       frame_times.csv -> {out_dir / 'frame_times.csv'}")
        print(f"       metadata.json   -> {out_dir / 'metadata.json'}")

        if args.labels and not args.no_autolabel:
            # carla_autolabel.py needs only cv2 + numpy, which this env has too
            autolabel = Path(__file__).resolve().parents[2] / "ml" / "detection" / "carla_autolabel.py"
            print(f"[labels] building labels + check video ({autolabel.name}) ...")
            import subprocess
            res = subprocess.run([sys.executable, str(autolabel), str(out_dir)])
            if res.returncode == 0:
                print(f"[labels] check video -> {out_dir / 'autolabel' / 'check.mp4'}")
            else:
                print(f"[labels] carla_autolabel.py failed (exit {res.returncode}); run it by hand on {out_dir}")


if __name__ == "__main__":
    main()
