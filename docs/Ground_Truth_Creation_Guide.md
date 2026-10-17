# Ground Truth Creation Guide

Status: v1 (2026-09-26). For: team members preparing the tracking ground truth. Part of `docs/Vehicle_Detection_Tracking_Improvement_Plan.md` (Section 7-E, Phase 2).

**Goal:** one **1-minute, 1080p** video clip where every vehicle has a correct box in every frame and keeps **one fixed ID for the whole minute**, labelled `car`, `bus` or `truck`. Every improvement in the plan is scored against this file, so its quality limits how well we can measure anything.

---

## 0. Workflow at a glance

```
1. Choose / record footage     (Section 2)   real drone video, downloaded clip, or CARLA
2. Make the 1080p clip         (Section 3)   ffmpeg: trim, scale, constant fps, verify, checksum
3. Pre-annotate                (Section 4)   our pipeline's output -> CVAT import (saves ~70% of the work)
4. Label in CVAT               (Sections 5-6) pass 1 boxes, pass 2 identities
5. Quality control             (Section 8)   manual review + automatic checks (+ optional agreement test)
6. Export and deliver          (Section 9)   video + MOT 1.1 + CVAT XML + gt_info.json
```

Rough effort: 1–2 h preparing the clip, **4–8 h labelling with pre-annotations** (15–25 h without), 1–2 h review.

---

## 1. What makes a good ground-truth clip

The clip has to contain the problems we want to measure. A minute of smooth traffic makes every change look pointless.

| Must have | Why |
|---|---|
| **Parked vehicles** | Measures ID stability for stationary vehicles and the future no-parking logic |
| **Moving traffic**, ideally through an intersection or roundabout | Normal tracking, occlusions between vehicles |
| **At least one sudden camera move** (fast pan or jerk) | The main cause of our ID switches today |
| **The camera panning away and coming back** to the same area | Measures whether parked cars get their old ID back (map-based linking) |
| 20–40 vehicles in view on average | Enough to measure; 100+ multiplies labelling time |

| Nice to have | Why |
|---|---|
| Snow on the ground or roofs | Measures snow false positives (we don't train on snow, so we must at least measure them) |
| A bus and a truck | Rare classes; without them their scores are meaningless |
| Vehicles partly hidden by trees or bridges | Occlusion handling |

| Avoid | Why |
|---|---|
| Frames from footage used for training | Inflates the scores. Also: the GT clip is **never** used for training later |
| Blank, black or title frames at the start | Wasted frames, can confuse the frame numbering |
| Heavy compression, watermarks, digital zoom | Small vehicles become unrecognisable |
| Tilted (oblique) views where vehicles overlap heavily | Our pipeline is built for near-top-down views |

---

## 2. Getting the source footage

### 2.1 Option A — record your own drone footage (best)

| Setting | Recommended | Why |
|---|---|---|
| Resolution | **4K (3840×2160) at 30 fps**, downscaled to 1080p afterwards (Section 3) | Downscaling 4K gives a sharper 1080p than recording 1080p directly |
| Frame rate | 30 fps (25 is fine) | 60 fps doubles the labelling work for little gain |
| Gimbal | Straight down (−90°) or close to it (−70° to −90°) | Matches VisDrone-style training data; fewer overlaps |
| Altitude | 40–80 m | Vehicles ~20–60 px at 1080p: realistic for our use case, still labellable |
| Shutter speed | Fast: **1/500 s or faster** (use auto exposure with a shutter limit, or no ND filter) | Reduces motion blur during camera moves |
| Colour profile | Normal (not D-Log / flat / HDR) | The detector was trained on normal-looking footage |
| Bit rate | Highest available (e.g. 100–150 Mbps) | Fewer compression artefacts on small vehicles |
| Digital zoom, stabilisation crop | Off / normal | Keep the real field of view |
| Flight plan | ~20 s hover over the scene → a slow pan → **one quick pan or jerk** → pan away → **return** to the first view | Covers every "must have" in Section 1 in one minute |

Write down the timestamps of the camera moves while flying — they go into `gt_info.json` (Section 9).

### 2.2 Option B — use downloaded footage

Only use videos whose licence allows use in the project (stock sites with a free licence, Creative Commons, or with permission). Keep the source URL and licence in `gt_info.json`.

Install `yt-dlp` (and ffmpeg, Section 3.1):

```powershell
winget install yt-dlp.yt-dlp
```

Download the best available quality (4K if available — downscaling it ourselves gives a better 1080p than the site's own 1080p version):

```powershell
# List the available formats first
yt-dlp -F "https://www.youtube.com/watch?v=VIDEO_ID"

# Best video stream, no audio (we don't need audio), saved as mp4/mkv
yt-dlp -f "bv*" -o "source.%(ext)s" "https://www.youtube.com/watch?v=VIDEO_ID"

# If only 1080p is wanted directly (prefers H.264 for compatibility)
yt-dlp -f "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]" -o "source_1080p.%(ext)s" "https://www.youtube.com/watch?v=VIDEO_ID"
```

Note: the current roundabout test video is only 1280×720, so it **cannot** be the 1080p ground truth unless a higher-resolution original is found.

### 2.3 Option C — record in CARLA with better traffic

CARLA footage is useful as a **second** ground truth (and CARLA can label itself — Section 7.3), but it doesn't replace the real-footage clip: the detector behaves differently on renders.

How traffic works today: the CarlaAir launcher (`StartCarlaAir.bat` → `CarlaAir.ps1`) **auto-starts `auto_traffic.py`** with 30 vehicles + 50 pedestrians by default, and `record_flight.py` adds its own 15 vehicles on top. Both place vehicles at random spawn points **across the whole map**, so few end up in view (we measured ~2.4 vehicles per frame). To get denser, cleaner traffic, let the launcher spawn all of it and switch the recorder's own spawning off.

**Launcher options** (`StartCarlaAir.bat --help`): `MAP` (Town01–05, Town10HD, and `_Opt` variants), `--res WxH` (window size only), `--quality Low|Medium|High|Epic` (default Epic), `--traffic-vehicles N` (default 30), `--traffic-walkers N` (default 50), `--no-traffic`, `--kill`, `--log`.

- The **recorded frames are always 1920×1080** (fixed in `record_flight.py`'s camera), whatever `--res` is. A smaller window (`--res 1280x720`) leaves more GPU for the recording camera → higher capture fps.
- `--quality` **does** affect the recorded frames (shadows, anti-aliasing, textures). Use **Epic** (closest to real footage); drop to **High** only if the capture rate falls below ~10 fps (check `avg_fps` in the run's `metadata.json`). Avoid Medium/Low for ground truth — flatter renders are easier for the detector than real footage and make scores look better than they are.
- Pedestrians aren't labelled and cost CPU, so set `--traffic-walkers 0`.

**Step 1 — start CarlaAir with 80 vehicles.** From the CarlaAir folder (`CarlaAir-v0.1.7-Windows11-x86_64`), in PowerShell:

```powershell
.\StartCarlaAir.bat Town10HD --res 1280x720 --quality Epic --traffic-vehicles 80 --traffic-walkers 0
```

Map choice: `Town10HD` dense urban (what we used so far), `Town03` has a **roundabout** (closest to our real test video), `Town05` wide multi-lane intersections. Wait until it prints `CarlaAir is ready.` Traffic log: `traffic.err.log` in the same folder (should say `Vehicles: 80/80 spawned`).

**Step 2 — record, with the recorder's own spawning off.** In a second terminal (Anaconda Prompt, or PowerShell with conda initialised):

```powershell
conda activate carlaAir
cd D:\Main-Project\main-project\simulation\carla_scripts
python record_flight.py --vehicles 0
```

Fly with W/A/S/D, Space/Shift (up/down), Q/E (rotate), `T` take off, `L` land, **Esc** stop and save — or with a **gamepad** (detected automatically; left stick move, right stick rotate, **RT climb, LT descend**, hold **LB for slow, smooth pans**, RB fast, A take off, B land, Start stop and save). The analog sticks and LB slow mode give much smoother footage than the keyboard's full-speed steps. Check a non-Xbox controller's button numbering with `python gamepad_control.py` first. The output lands in `simulation/data_export/recorded_flights/<run_id>/` (`frames/`, `frame_times.csv`, `metadata.json`, review-only `flight.mp4`).

**Step 3 — stop CarlaAir** when done (also stops the traffic process):

```powershell
.\StartCarlaAir.bat --kill
```

**Advanced — traffic concentrated where the drone films, plus parked cars.** Spawning 80 vehicles over the whole map still wastes most of them. To use this, start CarlaAir with `--no-traffic` (or `--traffic-vehicles 0`) and run the snippet in its own terminal instead. The snippet below spawns them only near a chosen point, adds parked cars, fixes the weather and makes the run repeatable. It is **not yet a script in the repo and hasn't been run** — test it, then we can turn it into `simulation/carla_scripts/spawn_scene_traffic.py`.

```python
import random
import carla

client = carla.Client("localhost", 2000)
client.set_timeout(15)
world = client.get_world()
cmap = world.get_map()

random.seed(42)
tm = client.get_trafficmanager(8000)
tm.set_random_device_seed(42)                    # same behaviour every run
tm.set_global_distance_to_leading_vehicle(2.5)   # metres between cars
tm.global_percentage_speed_difference(-10)       # drive 10% above the limit (negative = faster)
tm.set_hybrid_physics_mode(True)

# Where the drone will film. Get it by hovering there and printing the drone's location:
#   print([a.get_location() for a in world.get_actors() if "drone" in a.type_id.lower()])
center = carla.Location(x=0.0, y=0.0, z=0.0)
RADIUS = 150.0

def is_vehicle(bp, kinds=("car", "van", "truck", "bus")):
    return bp.has_attribute("base_type") and bp.get_attribute("base_type").as_str().lower() in kinds

bps = [bp for bp in world.get_blueprint_library().filter("vehicle.*") if is_vehicle(bp)]
car_bps = [bp for bp in bps if is_vehicle(bp, ("car",))]

# Moving traffic: spawn points near the filmed area only
points = [p for p in cmap.get_spawn_points() if p.location.distance(center) < RADIUS]
random.shuffle(points)
moving = []
for sp in points[:60]:
    v = world.try_spawn_actor(random.choice(bps), sp)
    if v:
        v.set_autopilot(True, tm.get_port())
        moving.append(v)

# Parked cars: on parking / shoulder lanes near the area, physics off so they never move
side = [w for w in cmap.generate_waypoints(3.0)
        if w.lane_type in (carla.LaneType.Parking, carla.LaneType.Shoulder)
        and w.transform.location.distance(center) < RADIUS]
parked = []
for w in side[::3][:15]:
    t = w.transform
    t.location.z += 0.3
    v = world.try_spawn_actor(random.choice(car_bps), t)
    if v:
        v.set_simulate_physics(False)
        parked.append(v)

# Optional: a few rule-breakers for the future violation logic
for v in moving[:3]:
    tm.ignore_lights_percentage(v, 100)          # runs red lights
for v in moving[3:6]:
    tm.vehicle_percentage_speed_difference(v, -60)  # 60% over the limit

world.set_weather(carla.WeatherParameters.ClearNoon)  # short shadows; try CloudyNoon too
print(f"moving: {len(moving)}, parked: {len(parked)}")
# Leave this script running (e.g. input("Enter to remove traffic")), then destroy the actors.
```

Notes:
- If `side` is empty, the map has few parking/shoulder lanes — pick a car park and place parked cars there by hand-chosen transforms.
- Some maps have parked cars **baked into the map**. They look real but are not actors, so CARLA's automatic ground truth (Section 7.3) can't see them. On `_Opt` map variants they can be removed with `world.unload_map_layer(carla.MapLayer.ParkedVehicles)` and replaced by spawned ones.
- `base_type` exists on newer CARLA versions (0.9.13+). If the check filters out everything, fall back to `number_of_wheels == 4` as in `fly_and_capture.py`.

**Flying for ground truth:** follow the flight plan in Section 2.1 — hover, slow pan, one quick move, away and back — at 40–60 m altitude, for a little over a minute. For labelling, use the saved `frames/` JPEGs, **not** `flight.mp4` (that is a lossy re-encode at a nominal frame rate; see Section 3.6).

---

## 3. Preparing the 1080p clip with ffmpeg

### 3.1 Install ffmpeg (Windows)

```powershell
winget install Gyan.FFmpeg
# open a new terminal, then check:
ffmpeg -version
```

### 3.2 Inspect the source

```powershell
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate,nb_frames -show_entries format=duration -of default=nw=1 source.mp4
```

Check:
- `width`/`height`: 3840×2160 (downscale) or 1920×1080 (keep).
- `r_frame_rate` vs `avg_frame_rate`: if they differ, the video has a **variable frame rate** (common on phones and some drones) → convert to constant (the commands below do this).
- `bit_rate`: very low (under ~5 Mbps for 1080p) means heavy compression — prefer a better source.

### 3.3 Cut and convert to the final 1080p clip (recommended, frame-accurate)

Replace `00:01:30` with the start of your chosen minute.

**From 4K (or any size larger than 1080p, 16:9):**

```powershell
ffmpeg -ss 00:01:30 -i source.mp4 -t 60 -vf "fps=30,scale=1920:1080:flags=lanczos" -c:v libx264 -preset slow -crf 12 -pix_fmt yuv420p -an clip_1080p.mp4
```

**From 1080p (already the right size):**

```powershell
ffmpeg -ss 00:01:30 -i source.mp4 -t 60 -vf "fps=30" -c:v libx264 -preset slow -crf 12 -pix_fmt yuv420p -an clip_1080p.mp4
```

**From a 4:3 drone video (e.g. 4000×3000)** — crop to 16:9 instead of stretching:

```powershell
ffmpeg -ss 00:01:30 -i source.mp4 -t 60 -vf "fps=30,scale=1920:-2:flags=lanczos,crop=1920:1080" -c:v libx264 -preset slow -crf 12 -pix_fmt yuv420p -an clip_1080p.mp4
```

What the options do:

| Option | Meaning |
|---|---|
| `-ss … -t 60` | Start time, 60 s duration. `-ss` before `-i` is fast and, because we re-encode, still frame-accurate |
| `fps=30` | Constant 30 fps (fixes variable frame rate; also turns 60 fps into 30). Use `fps=25` for 25 fps sources |
| `scale=1920:1080:flags=lanczos` | High-quality downscale |
| `-crf 12 -preset slow` | Near-lossless H.264 (18 is "visually lossless"; 12 is safer for tiny vehicles) |
| `-pix_fmt yuv420p` | Plays everywhere, including CVAT and OpenCV |
| `-an` | Drop audio |

If the source is interlaced (rare for drones; combing lines on moving cars), add `yadif,` at the start of the `-vf` chain.

### 3.4 Quick cut without re-encoding (only if the source is already a clean 1080p 30 fps constant-rate file)

```powershell
ffmpeg -ss 00:01:30 -i source.mp4 -t 60 -c copy -an -avoid_negative_ts make_zero clip_1080p.mp4
```

This keeps the original quality but can only cut at keyframes, so the start may shift by up to a few seconds. If in doubt, use 3.3.

### 3.5 Verify the result

```powershell
# resolution, fps, exact frame count (should be ~1800 at 30 fps)
ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=width,height,avg_frame_rate,nb_read_frames -of default=nw=1 clip_1080p.mp4

# the frame count OpenCV sees must match (our pipeline decodes with OpenCV)
python -c "import cv2; c=cv2.VideoCapture('clip_1080p.mp4'); n=0
while c.read()[0]: n+=1
print(n)"

# fingerprint: proves later that the labels belong to exactly this file
certutil -hashfile clip_1080p.mp4 SHA256
```

**From this point on, the file is frozen.** Never re-encode, resize or trim it again — the labels are pixel coordinates in this exact file. If it has to change, the labelling has to be redone.

### 3.6 Frames instead of a video (CARLA, or if CVAT has trouble with the video)

```powershell
# video -> numbered frames, 0-based to match our pipeline's frame index
mkdir frames
ffmpeg -i clip_1080p.mp4 -start_number 0 -q:v 1 frames/%06d.jpg

# CARLA frames -> a video (only for review / CVAT; our pipeline reads the JPEGs directly)
ffmpeg -framerate 15 -start_number 0 -i frames/%05d.jpg -c:v libx264 -crf 12 -pix_fmt yuv420p flight_review.mp4
```

CARLA recordings have an irregular capture rate (see `frame_times.csv`), so for CARLA label the **frames** (zip them and upload the zip to CVAT as images), not a video.

---

## 4. Pre-annotations from our pipeline

Correcting our tracker's output is much faster than drawing ~30 boxes × 1,800 frames from scratch. Deleting a wrong box is faster than drawing a missing one, so the pre-annotations are tuned for **high recall** (low confidence, large input size).

```powershell
# main project venv
python ml/violation_engine/process_video.py "clip_1080p.mp4" --imgsz 1920
```

Then convert `ml/data/results/video_validation/clip_1080p/botsort/trajectories_stitched.csv` to a CVAT-importable MOT 1.1 zip — **`export_cvat.py` is W4's Phase 1 task and not written yet**; until it exists, send the clip to Afif and the pre-annotations will be generated for you.

In CVAT: task → **Actions → Upload annotations → MOT 1.1** → choose the zip.

**Warning — pre-annotation bias.** If labellers accept what's already there, the ground truth inherits our tracker's mistakes and our scores look better than they really are. Be most suspicious of: vehicles with no box at all (we missed them), one vehicle whose ID changes (we switched), and boxes on snow, roofs or shadows (false positives). Pass 2 in Section 5.3 exists for this.

---

## 5. Labelling in CVAT

### 5.1 Which CVAT

- **app.cvat.ai** (online, free tier): quickest to start; fine for one clip.
- **Self-hosted CVAT** (Docker, `docker compose up -d` from the CVAT repo): no upload limits, data stays local, and allows AI helpers (auto-annotation with our YOLO model via Nuclio). Worth it only if the team will label more clips later.

### 5.2 Task setup

1. **Create project** with labels: `car`, `bus`, `truck`, `ignore`.
   - Optional attribute on `car`/`bus`/`truck`: `parked` (checkbox) — lets us report parked-vehicle results separately.
2. **Create task** → upload `clip_1080p.mp4` (or the frames zip). Advanced settings:
   - **Image quality: 95–100** (the default 70 blurs small vehicles in the editor; it doesn't change the labels, only what you see).
   - Frame step: 1. Start/stop frame: leave empty (whole clip).
   - Segment size: whole clip in one job if one person labels; otherwise ~600 frames per job with **overlap 30** (1 s) so IDs can be joined.
3. Upload the pre-annotations (Section 4).

### 5.3 Two-pass workflow (standard for tracking ground truth)

**Pass 1 — boxes (detection).** Goal: every vehicle has a correct box.
- Use **track mode** (draw with "Track", not "Shape"): draw the box on a keyframe, jump ~10–15 frames, adjust it → CVAT **interpolates** the frames in between. Add extra keyframes where the vehicle turns, speeds up, or the camera moves suddenly (interpolation is linear, so camera jerks need a keyframe on each side).
- When a vehicle leaves the frame or is fully hidden, mark the track **"outside"** on that frame (don't delete it); when it reappears, switch "outside" off on the same track → same ID.
- Delete false positives (snow, roofs, shadows, road markings).

**Pass 2 — identities.** Goal: one vehicle = one track for the whole minute. Watch **one vehicle at a time** from its first to its last frame:
- Two tracks for one vehicle (ID switch) → **merge** them.
- One track jumping between two vehicles (swap) → **split** it at the swap and merge each piece with the right vehicle.
- Tracks that **start or end in the middle of the frame** (not at the edge) are the usual suspects — check every one.

Useful controls (check CVAT's Help → shortcuts, they vary by version): `N` draw/repeat shape, `F` / `D` next / previous frame, `M` merge mode, `Alt+M` split. Use the timeline and "Show all interpolated frames" to see where a track is interpolated.

---

## 6. Labelling rules

Agree these **before** starting and don't change them halfway.

| Situation | Rule |
|---|---|
| Box size | Tight around the vehicle body (mirrors can be ignored). **Exclude the shadow** — the most common mistake in top-down footage |
| Car, van, minivan, SUV, pickup | `car` |
| Bus, minibus | `bus` |
| Truck, lorry, tractor-trailer (one box for the whole articulated vehicle), large delivery truck | `truck` |
| Motorcycle, bicycle, pedestrian | Not labelled |
| Vehicle at the frame edge | Label if **≥ 50% visible**; box only the visible part |
| Tiny vehicle (< ~8 px wide, e.g. far in the distance) | Not labelled; draw an `ignore` region over the area instead |
| Dense car park / area too crowded to label reliably | `ignore` region |
| Briefly hidden (tree, bridge, sign, another vehicle) | Same track: "outside" while fully hidden, box the visible part while partly hidden |
| Leaves the view and comes back | Same ID **only if certain** (unique colour, trailer, roof rack…). Otherwise a new ID. A wrong merge is worse than a missed one |
| Parked car covered in snow | Still a `car` — label it |
| Snow pile, roof, container, shadow that looks like a vehicle | **Not labelled** — this is exactly the false positive we want to count |
| Heavy blur during a camera move | Keep the track going through the blur if you can see where the vehicle is |
| Class unclear | Choose the most likely class once for the **whole track** — don't change class mid-track |

---

## 7. Common and advanced methods

### 7.1 Tools

| Tool | Type | Good for | Notes |
|---|---|---|---|
| **CVAT** (recommended) | Web, free/open source | Video tracks with interpolation, merge/split, MOT 1.1 import/export, team review | The standard choice for MOT ground truth; our importer targets its export |
| **DarkLabel** | Windows desktop, free | Fast MOT video labelling with built-in tracker propagation and interpolation; exports MOT format | Popular in the MOT research community; good if one person labels offline |
| **X-AnyLabeling** | Desktop, open source | AI-assisted labelling: YOLO auto-labelling, SAM / SAM 2 segment-and-track | Good for propagating a box forward automatically; check its MOT export before relying on it |
| **Supervisely**, **Encord**, **V7** | Web, commercial (free tiers vary) | Video labelling with automatic object tracking and review workflows | Overkill for one clip; useful if labelling becomes a regular task |
| **Label Studio** | Web, open source | Video object tracking with interpolation | Weaker video tracking tools than CVAT |
| **Roboflow** | Web | Image datasets (detector training frames, plan item A3) | Not built for persistent-ID video tracking |

### 7.2 Techniques

1. **Model-assisted pre-labelling (human in the loop).** Our tracker labels first; people correct (Section 4). The most common way to cut labelling time. Tune the model for **recall** (low confidence, 1920 input, optionally SAHI) because deleting is faster than drawing.
2. **Keyframes + interpolation.** Label every 10–15 frames plus at every change of motion; the tool fills the rest. Standard in CVAT, DarkLabel and Supervisely.
3. **Tracker / segmentation propagation.** Click a vehicle once and let a single-object tracker or **SAM 2** follow it through the video (CVAT AI tools on recent/self-hosted versions, X-AnyLabeling). Very fast for isolated vehicles; still needs checking at occlusions and camera jerks.
4. **Separate detection and identity passes** (Section 5.3). Mixing them is where ID errors hide.
5. **Track-level attributes.** Class and `parked` are set once per track, not per frame — faster, and the class can't flicker in the ground truth.
6. **Ignore regions and visibility flags** (as in the MOT17 benchmark). Areas nobody can label reliably shouldn't count as errors for the tracker.
7. **Inter-annotator agreement.** Two people label the same 5–10 s independently; score one against the other with `eval_tracking.py`. The result is the **noise floor**: differences between trackers smaller than the human disagreement are not meaningful. Recommended for the paper.
8. **Trajectory plots for review.** Plot each ID's path (in stabilised map coordinates once W3's `scene_map.py` exists). A path that teleports or zig-zags shows a swap or a bad box faster than watching video.
9. **Versioned, frozen ground truth.** Keep `gt v1` fixed with the video's checksum. Any correction becomes `v1.1`, and the baseline is re-scored — otherwise results before and after the fix can't be compared.

### 7.3 Advanced: automatic ground truth from CARLA

CARLA knows the exact position and ID of every vehicle actor, so a simulated clip can be labelled **perfectly and automatically** — no manual work, perfect IDs. This is the usual way simulation is used for tracking evaluation. It complements, not replaces, the real-footage ground truth. Not implemented yet (optional task for W4 in Phase 2–3). Method:

1. Attach a second camera to the drone next to the RGB camera: **`sensor.camera.instance_segmentation`** (CARLA 0.9.14+), same position, resolution and FOV. Each pixel's red channel is the semantic class and its green+blue channels encode the **actor ID**.
2. For every frame: for each vehicle actor ID found in the image, take the bounding rectangle of its pixels → a box that already accounts for occlusion (only visible parts). Skip actors with too few visible pixels.
3. ID = the CARLA actor ID (constant for the whole run — a perfect ground-truth ID). Class from the blueprint's `base_type` (`car`/`van` → `car`, `bus`, `truck`).
4. Write rows in our CSV schema, frame-aligned with the RGB frames.
5. Caveat: map-baked parked cars are not actors (Section 2.3) — remove them or mark them `ignore`.

Older CARLA versions without the instance camera: project each actor's 3D `bounding_box` into the image with the camera matrix, and use a depth camera to drop occluded vehicles.

---

## 8. Quality control before delivery

**Manual review (reviewer who didn't label that part):**
- Play at 0.25× with IDs shown; follow each ID from start to end.
- 10 random frames: count vehicles on screen vs boxes. Any miss → check neighbouring frames.
- Every track that starts or ends away from the frame edge: justified (occlusion, parked car revealed) or an error?
- Each camera move: do all IDs survive it?

**Automatic checks** (W4 will script these as part of `import_gt.py`):
- Boxes jumping more than a vehicle length between consecutive frames (swap or bad keyframe).
- Two boxes overlapping heavily on one vehicle (duplicate).
- Boxes far smaller or larger than the clip's median vehicle size.
- Frames with no boxes, missing frame numbers.
- IDs that vanish and reappear (check they're genuine).
- Class changes inside a track (shouldn't happen with track-level class).

**Optional — agreement test** (Section 7.2, point 7) on a 5–10 s segment.

---

## 9. Export and delivery

Export from CVAT **twice**: *Export task dataset → MOT 1.1* (what we score) and *→ CVAT for video 1.1* (keeps keyframes and attributes, so the labels can be edited later).

Delivery folder (shared drive; video and labels are too large for git — only `gt_info.json` and the checksum go into the repo):

```
ml/data/eval/gt_1080p/
  clip_1080p.mp4            the exact labelled video (frozen)
  clip_1080p.sha256         output of certutil -hashfile ... SHA256
  gt_mot11.zip              CVAT export, MOT 1.1
  gt_cvat_video.zip         CVAT export, CVAT for video 1.1
  gt_info.json              description (template below)
```

W4 then generates `gt.csv` (our schema) and a QA report from these.

`gt_info.json` template:

```json
{
  "version": "1.0",
  "video": "clip_1080p.mp4",
  "sha256": "<from certutil>",
  "source": "own drone flight | download (URL) | CARLA run id",
  "licence": "own footage | <licence of the downloaded video>",
  "resolution": "1920x1080",
  "fps": 30,
  "n_frames": 1800,
  "frame_numbering": "MOT export is 1-based; our CSV is 0-based",
  "start_time_in_source": "00:01:30",
  "classes": ["car", "bus", "truck"],
  "class_rules": "van/SUV/pickup -> car; minibus -> bus",
  "contains": {"snow": true, "parked_vehicles": true, "sudden_camera_move": true, "pan_away_and_return": true},
  "camera_moves_s": [[21.0, 23.5], [40.0, 44.0]],
  "labelled_by": ["name1", "name2"],
  "reviewed_by": ["name3"],
  "notes": "anything unusual: ignore regions, uncertain re-identifications, ..."
}
```

---

## 10. Splitting the work and time

| Setup | Split | Time (with pre-annotations) |
|---|---|---|
| 1 labeller + 1 reviewer (recommended) | Labeller does passes 1+2; reviewer does Section 8 | ~6–8 h + ~2 h |
| 2 labellers + 1 reviewer | Split by time (0–30 s, 30–60 s, jobs overlapping by 1 s); **one person** joins IDs across the boundary | ~4 h each + ~2 h |
| Split by area of the frame | **Not recommended** — vehicles cross between areas and IDs break | — |

## 11. Common mistakes

| Mistake | Consequence | Avoid by |
|---|---|---|
| Re-encoding or resizing the video after labelling | Boxes no longer line up; every score wrong | Freeze the file + checksum (Section 3.5) |
| Including shadows in boxes | Boxes too large; detection scored as wrong | Rule in Section 6 |
| Accepting pre-annotations without checking | Scores inflated; our tracker's errors look correct | Pass 2 + review focusing on misses and IDs |
| Deleting a track when a vehicle is hidden instead of marking "outside" | A false ID switch in the ground truth | Use "outside" |
| Guessing re-identifications | Wrong merges in the ground truth | Same ID only if certain |
| Changing rules halfway | Inconsistent labels | Agree Section 6 first |
| Using the GT clip (or its video) for training later | Inflated scores | Record the source in `gt_info.json`; W1 excludes it |

## 12. References

- CVAT documentation (tracks, interpolation, MOT import/export): https://docs.cvat.ai
- MOT benchmark format and ignore/visibility conventions (MOT17): https://motchallenge.net/instructions
- DarkLabel: https://github.com/darkpgmr/DarkLabel
- X-AnyLabeling: https://github.com/CVHub520/X-AnyLabeling
- SAM 2 (video segmentation and propagation): https://github.com/facebookresearch/sam2
- CARLA Traffic Manager: https://carla.readthedocs.io/en/latest/adv_traffic_manager
- CARLA sensors (instance segmentation camera): https://carla.readthedocs.io/en/latest/ref_sensors
- ffmpeg documentation: https://ffmpeg.org/ffmpeg.html
- yt-dlp format selection: https://github.com/yt-dlp/yt-dlp#format-selection
- Project plan: `docs/Vehicle_Detection_Tracking_Improvement_Plan.md` (Section 7-E, Phase 2)
