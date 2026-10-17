# Simulation integration (Stage 6)

Validates the Stage 1/3 detector+tracker against synthetic CARLA/AirSim aerial
footage before Stage 4 (violation logic) is built.

## Layout

- `CarlaAir-v0.1.7-Windows11-x86_64/` (repo root, gitignored) — the CARLA+AirSim
  simulator distribution. Multi-GB packaged UE4 build, not source; never commit it.
- `carla_scripts/fly_and_capture.py` — flies a drone automatically along an
  actual road (via CARLA's Waypoint API, not a guessed path), nadir camera,
  and records the aerial view as JPG frames + a review-only mp4.
- `carla_scripts/live_fly_and_track.py` — single-pipeline mode: manual
  keyboard flight + live detection/tracking + a live annotated window, all
  at once. See "Live mode" below.
- `data_export/sim_flights/<run_id>/` (gitignored) — output of each
  `fly_and_capture.py` run: `frames/*.jpg` (what detection/tracking runs on),
  `flight.mp4` (human review only — a second, lossier re-encode), `metadata.json`.
- `data_export/live_flights/<run_id>/` (gitignored) — output of each
  `live_fly_and_track.py` run: `frames/*.jpg`, `trajectories.csv`, `metadata.json`.
- `violation_scenarios/` — reserved for Stage 4 (not started).

## Two environments

The CARLA Python wheel bundled in the distribution is Python 3.10 only, while
the project's main venv (ultralytics/torch) is Python 3.14 — they can't share
one environment. The simulate+capture step and the detect+track step run
separately, in different environments, connected only by the video file on disk.
This also avoids running the GPU-heavy UE4 simulator and GPU-heavy YOLO
inference at the same time.

1. **Sim env** (Python 3.10, `carla` + `airsim`): set up once via
   `CarlaAir-v0.1.7-Windows11-x86_64\SetupEnv.bat` (creates a conda env named
   `carlaAir`). Used only to run `fly_and_capture.py`.
2. **Main env**: the repo's existing venv, used for everything under `ml/`.

## Usage

```bat
:: 1. Start the simulator (from CarlaAir-v0.1.7-Windows11-x86_64\), at Epic
::    quality and 1080p window resolution for maximum visual clarity.
StartCarlaAir.bat Town10HD --res 1920x1080 --quality Epic

:: 2. Capture a flight (in the carlaAir conda env)
conda activate carlaAir
python simulation\carla_scripts\fly_and_capture.py

:: 3. Run detection+tracking on the captured footage (in the main venv)
venv\Scripts\activate
python ml\violation_engine\run_sim_validation.py
```

Step 3 writes trajectory CSV + an annotated tracking video to
`ml/data/results/sim_validation/<run_id>/`, reusing
`ml/violation_engine/extract_trajectories.py` and the same YOLO26l weights
(`ml/data/results/full_train/train/weights/best.pt`) used on real VisDrone
footage in Stages 1 and 3.

## Rendering & footage resolution

Two separate things control visual clarity, both raised to 1080p-class:

- **Simulator rendering quality** — `StartCarlaAir.bat`'s `--quality` flag
  (`Low`/`Medium`/`High`/`Epic`) controls UE4 texture/LOD/shadow detail;
  defaults to `Epic` already, but pass it explicitly (see the command above)
  since it's what determines how sharp/detailed the world looks regardless
  of camera resolution. `--res 1920x1080` sets the simulator window itself.
- **Drone camera capture resolution** — set in AirSim's `settings.json`
  (`%USERPROFILE%\Documents\AirSim\settings.json`, and the template shipped
  at `CarlaAir-v0.1.7-Windows11-x86_64\AirSimConfig\settings.json`), under
  `Vehicles.SimpleFlight.Cameras."0".CaptureSettings` — this is what
  `fly_and_capture.py`'s camera "0" actually captures at, independent of the
  simulator window size. Both files' camera `"0"` (and `front_center`) are
  set to `1920x1080`. `fly_and_capture.py` doesn't hardcode a resolution
  anywhere — it just saves whatever AirSim returns, so this setting is the
  single place controlling footage size.

## Custom flight paths

By default `fly_and_capture.py` auto-generates a path by walking an actual
road lane from a random spawn point (`--waypoints`/`--waypoint-step` control
its length/spacing). There's no graphical waypoint-map editor in this
CarlaAir distribution — to fly a specific custom route instead, record it by
flying manually once, then replay that exact path automatically on every run:

```bat
:: In the carlaAir conda env, from CarlaAir-v0.1.7-Windows11-x86_64\
:: Terminal 1 — fly the drone by hand (WASD/mouse), matching the desired route
python examples\fly_drone_keyboard.py

:: Terminal 2 — capture the flight to a trajectory JSON (press Enter to start/stop)
python examples_record_demo\record_drone.py
:: -> trajectories/drone_<timestamp>_01.json

:: Then replay that path automatically for every capture run:
python simulation\carla_scripts\fly_and_capture.py --waypoints-file CarlaAir-v0.1.7-Windows11-x86_64\trajectories\drone_<timestamp>_01.json
```

`--min-spacing` (default 10m) collapses the ~20Hz recorded samples down to
a sparser set of `moveToPositionAsync` waypoints — lower it for a tighter,
more literal replay of the recorded route; raise it for a smoother flight
along the same general shape.

## Live mode: manual flight + live detection in one window

`live_fly_and_track.py` runs simulate and detect in a single process, so
this one needs `ultralytics` + a CUDA `torch` installed into the `carlaAir`
conda env too (one-time, alongside the existing `carla`/`airsim` wheels —
see the script's module docstring for the exact `pip install` command).
This is different from `fly_and_capture.py` / `run_sim_validation.py`,
which deliberately keep simulate and detect in separate environments/processes.

```bat
conda activate carlaAir
python simulation\carla_scripts\live_fly_and_track.py
```

Fly with the same keymap as `examples/fly_drone_keyboard.py`
(W/S/A/D + Space/Shift + Q/E, T to take off, L to land). A second window
("Live Detection") shows the drone's camera feed live with YOLO+ByteTrack
boxes and track IDs drawn on it as you fly. ESC or `q` stops and saves
`frames/*.jpg` + `trajectories.csv` + `metadata.json` to
`simulation/data_export/live_flights/<run_id>/` — same CSV schema as
`ml/violation_engine/extract_trajectories.py`.

**Capture rate:** the camera sensor has `sensor_tick=0.0` (CARLA's fastest
setting — a frame every render tick, no artificial polling interval).
Every frame CARLA renders gets saved to `frames/*.jpg` regardless of
inference speed (saving happens in the capture callback itself, not the
main loop). The live window and `trajectories.csv`, on the other hand,
only ever show/log the *latest* available frame — if inference can't keep
up with the capture rate, older in-between frames are skipped there (by
design, so the display doesn't lag further and further behind real time),
but they're still on disk in `frames/`. `metadata.json` records both
`n_frames_saved` (every rendered frame) and `n_frames_processed` (frames
that got a detection pass) so you can see the gap.

**Smoothness — display and detection run on separate threads.** The first
version processed and displayed a frame in the same loop, so the live
window only updated as fast as inference could run — and choppily, since
inference time is irregular here (UE4 rendering shares the GPU with YOLO).
`InferenceWorker` now runs detection continuously on its own thread; the
main loop, independently, always shows the *current* raw frame at full
capture rate (smooth motion) with whatever boxes `InferenceWorker` most
recently produced drawn on top. Those boxes can lag the current frame by
however long the last inference pass took (usually well under a second,
enough to occasionally look slightly behind a fast-moving vehicle), but the
picture itself never waits on inference — no `--infer-every-n-frames`-style
throttle needed anymore, since display and detection no longer share a loop.

**Why image capture uses CARLA, not AirSim:** the first version used
AirSim's `simGetImages` RPC screenshot call for the camera feed, the same
way `fly_and_capture.py` does. It reliably stalled forever the moment
flight physics started (fine grounded, broken the instant you take off) —
survived three separate fixes (isolating the RPC connection per thread,
throttling the control loop's command rate, PNG-compressing the payload)
before it became clear the AirSim RPC image path itself is unreliable
under this build once the drone is actually flying, not any of those
specific causes. The fix was to stop using it: `CarlaCameraGrabber` attaches
a native `sensor.camera.rgb` directly to the drone's CARLA-side actor and
streams frames via `camera.listen()` — push-based, no request/response
round trip to time out. This distribution's own `examples/data_collector.py`
already proves that path handles an actively-moving actor fine (it streams
RGB/depth/segmentation from an autopilot-driving vehicle). AirSim is still
used for flight control (`KeyboardControl`) — only image acquisition moved
to CARLA.
