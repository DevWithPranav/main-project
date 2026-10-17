# How the system works: technical guide

**Vision-Based Autonomous Surveillance Framework for Traffic Monitoring Using Aerial Videos**

This guide explains, from the camera to the planner's screen, what every part of the system does, which
algorithm it uses, with which parameters, and how the parts talk to each other. Every parameter quoted
here is the value in the code at the time of writing; the file that holds it is named next to it, so
it can be checked or changed.

Contents

1. [The big picture](#1-the-big-picture)
2. [Data acquisition: CARLA, the drone and the recorded flight](#2-data-acquisition-carla-the-drone-and-the-recorded-flight)
3. [Vehicle detection (YOLO26l)](#3-vehicle-detection-yolo26l)
4. [Multi-object tracking (TrackTrack)](#4-multi-object-tracking-tracktrack)
5. [Track post-processing (offline)](#5-track-post-processing-offline)
6. [Pixels to metres: ground projection](#6-pixels-to-metres-ground-projection)
7. [Smoothing: Kalman filter and RTS smoother](#7-smoothing-kalman-filter-and-rts-smoother)
8. [Road knowledge: lane maps, zones and map matching](#8-road-knowledge-lane-maps-zones-and-map-matching)
9. [The violation engine](#9-the-violation-engine)
10. [Events, confidence, status and evidence](#10-events-confidence-status-and-evidence)
11. [The live pipeline: CARLA to backend in real time](#11-the-live-pipeline-carla-to-backend-in-real-time)
12. [The backend (FastAPI, PostGIS, Redis, SeaweedFS)](#12-the-backend-fastapi-postgis-redis-seaweedfs)
13. [How the backend feeds the frontend live](#13-how-the-backend-feeds-the-frontend-live)
14. [The dashboard (frontend)](#14-the-dashboard-frontend)
15. [How the digital twin is built](#15-how-the-digital-twin-is-built)
16. [Analytics, hotspots and the recommendation system](#16-analytics-hotspots-and-the-recommendation-system)
17. [What-if and traffic plans](#17-what-if-and-traffic-plans)
18. [How the parts are evaluated](#18-how-the-parts-are-evaluated)
19. [Glossary](#19-glossary)
20. [File map](#20-file-map)

---

## 1. The big picture

The system turns aerial video into **reviewable traffic-violation events** and **planning advice**.
It has six layers (the architecture diagram, Figure 4.2 of the report):

```
Layer 1  Data acquisition    CARLA simulation (CarlaAir = CARLA + AirSim) / live drone camera stream
Layer 2  Detection pipeline  YOLO26l detection -> TrackTrack tracking -> pixel-to-map + smoothing + lane matching
Layer 3  Violation engine    7 rule monitors over time -> events + evidence clips
Layer 4  Backend             FastAPI (REST + WebSocket), PostgreSQL/PostGIS, Redis, SeaweedFS (S3)
Layer 5  Analysis/planning   DBSCAN hotspots -> recommendation engine; on-demand exports
Layer 6  Dashboards + twin   Monitoring dashboard -> CesiumJS digital twin -> planning dashboard -> planner
```

The same detector, tracker settings and rule engine run in **two modes**:

| Mode | Input | How it runs | Output reaches the backend by |
|---|---|---|---|
| **Offline (recorded flight)** | A flight saved to disk by `record_flight.py` | Whole flight at once: detect + track every frame, then stitch, post-process, smooth forwards **and backwards**, run the rules | `POST /api/sessions/import` (the backend reads the result folder) |
| **Live** | Frames streamed over TCP by `stream_flight.py` | Frame by frame: detect + track, forward-only filter with a 1 s lag, rules stepped per frame | The pipeline POSTs states, events, frames and evidence to the backend as they happen |

The offline mode is the more accurate one (it can look at the future of every track); the live mode
answers within about one to two seconds.

---

## 2. Data acquisition: CARLA, the drone and the recorded flight

### 2.1 CarlaAir

**CarlaAir** (folder `CarlaAir-v0.1.7-Windows11-x86_64/`) is a prebuilt simulator that runs **CARLA**
(road traffic, towns, traffic lights, OpenDRIVE road networks) and **AirSim** (the drone) in one Unreal
Engine process. The `carla` Python package talks to the traffic side (port 2000 by default) and the
`airsim` package to the drone (port 41451). It is used, not developed, by this project.

### 2.2 The camera

A `sensor.camera.rgb` is attached to the drone looking **straight down (pitch −90°, "nadir")**. Typical
settings are 1920 × 1080 for recordings. Because the camera's world pose is known from the simulator
on every frame, each pixel can later be turned into a ground position in metres exactly (Section 6).

### 2.3 What a recorded flight contains

`simulation/carla_scripts/record_flight.py` writes one folder per flight under
`simulation/data_export/recorded_flights/<YYYYMMDD_HHMMSS>/`:

| File | Content | Used for |
|---|---|---|
| `frames/*.jpg` | Every RGB frame | Detection input (the pipeline always reads frames, never a re-encoded video) |
| `frame_times.csv` | Per frame: frame number, CARLA frame, **simulator time**, camera pose (x, y, z, pitch, yaw, roll) | Time base for speeds; projection to the ground |
| `flight.mp4` | Preview video | Viewing only: its header frame rate is nominal, not real |
| `vehicle_poses.csv` | True position of every vehicle per frame | Ground truth for evaluation (speed, zones) |
| `traffic_lights.json` | Every light's state changes and stop lines | Red-light rule, twin replay |
| `metadata.json` | Map name, settings | Import, scene selection |
| `seg/`, `depth/`, `autolabel/` (optional) | Instance segmentation, depth, automatic labels | Building detector training data |

**Simulator time, not wall-clock time.** Speeds must be computed from the time that passed in the
simulation. The GPU does not render at a constant rate, so "frame number ÷ fps" would be wrong. The
evaluation clip, for example, has a nominal 15 fps in its mp4 header but really averages 23.04 fps.

### 2.4 Live streaming from CARLA

`simulation/carla_scripts/stream_flight.py` attaches the same camera and, instead of saving frames,
sends them over the network to the detector (Section 11). `--record` additionally saves the stream as a
normal recorded flight so a live run can be re-processed offline and compared.

### 2.5 Staged violations

`simulation/violation_scenarios/stage_violations.py` drives vehicles kinematically through planned
"acts" (a car parks 45 s in a no-parking zone, a car drives a lane backwards, a car stops on a crossing,
...) plus **negative** acts that must not be flagged (a 15 s delivery stop, a legal three-point turn,
a queue across a crossing). It writes a **scenario log** with the vehicle, type, start and end of every
act: the exact truth for scoring the rules. `highway_violations.py` does the same on a highway map
(speeders at set multiples of the limit, stopped vehicles, shoulder stops).

---

## 3. Vehicle detection (YOLO26l)

### 3.1 What a detector does

An object detector takes one image and returns a list of **bounding boxes**, each with a **class**
(car, bus, truck) and a **confidence** between 0 and 1. It knows nothing about earlier frames.

### 3.2 The model

- **YOLO26l** (Ultralytics), the "large" variant of the YOLO26 family. YOLO ("You Only Look Once") is a
  single-pass convolutional detector: one forward pass predicts boxes and classes for the whole image.
  YOLO26 has an **NMS-free head**: it is trained to output one box per object, so the classic
  "non-maximum suppression" step is not needed in principle.
- **Classes:** 3: `car` (vans, SUVs and pickups are counted as cars), `bus`, `truck`.
- **Training:** two sessions on an NVIDIA GeForce RTX 4060 Laptop GPU (8 GB):
  1. Base model: VisDrone2019-DET (6,471 images, 10 classes), 640 px, batch 8, 80 epochs (early stop,
     best epoch 60), 7 h 18 min.
  2. Retrained model: 13,719 images from VisDrone-DET, VisDrone-VID, UAVDT and CARLA, 3 classes, 960 px,
     batch 4, 47 epochs (early stop, best epoch 32), about 18.5 h. Motion-blur augmentation
     (Albumentations, 7–25 px kernels, 20% of images) was added for drone motion.
- **Weights used:** `ml/data/results/retrain_v1/train/weights/best.pt`.

### 3.3 Settings at inference (`ml/violation_engine/process_recorded_flight.py`)

| Setting | Value | Why |
|---|---|---|
| `DETECT_CONF` | **0.1** | Ultralytics' default (~0.25) drops weak detections of small or partly hidden vehicles, which breaks tracks. The tracker decides later which low-score boxes to trust. |
| Input size (`imgsz`) | **640** for the tracking evaluation clip; 960 used on some test flights and live | 640 scored best on the evaluation clip (vehicles there are large; enlarging pushed them out of the size range the model learned). |
| Classes kept | car, van, truck, bus (by name) | Any detector's weights work. |

### 3.4 Class-agnostic duplicate removal (`dedupe_boxes`)

Despite the NMS-free head, the model often returns **the same vehicle twice with different classes**
(for example `car` and `van`). The tracker would match one box and start a short-lived new ID from the
other. So before tracking, the system runs **class-agnostic non-maximum suppression** with IoU 0.5
(`DEDUPE_IOU`):

- **IoU (Intersection over Union)** = area where two boxes overlap ÷ area they cover together.
  1.0 = identical boxes, 0 = no overlap.
- **NMS:** sort boxes by confidence; keep the best; delete every other box whose IoU with it is above
  0.5; repeat with the next remaining box. "Class-agnostic" = boxes of different classes also suppress
  each other.

On the real roundabout video this one step reduced the number of track IDs from 3,730 to 714.

---

## 4. Multi-object tracking (TrackTrack)

### 4.1 What a tracker does

A tracker links the detections of consecutive frames so that **one physical vehicle keeps one ID**. Its
output is a **trajectory** per vehicle: `(frame, time, track_id, class, box centre cx/cy, width,
height, confidence)`. Every rule about time ("stopped for 30 s", "drove 5 m backwards") needs this.

### 4.2 The common building blocks (all trackers compared)

1. **Motion prediction.** Each track carries a Kalman filter on its box (position, size and their
   velocities). Before matching, every track's box is predicted into the new frame.
2. **Camera-motion compensation (GMC).** The drone itself moves, so all boxes shift together.
   `gmc_method: sparseOptFlow` estimates that global shift from sparse optical flow between frames and
   applies it to the predictions.
3. **Association.** A cost matrix between predicted tracks and new detections (based on IoU and other
   cues) is solved as an assignment problem: each detection goes to at most one track.
4. **Two confidence levels (from ByteTrack).** High-score boxes (≥ `track_high_thresh`) are matched
   first; low-score boxes (between `track_low_thresh` and `track_high_thresh`) are used only in a
   second pass to keep **existing** tracks alive (a vehicle partly hidden by a tree scores low but is
   still the same vehicle). Low-score boxes never start new tracks.
5. **Track lifecycle.** Unmatched tracks are kept "lost" for `track_buffer` frames and can be re-found;
   after that they are deleted. Unmatched high-score detections may start new tracks.

### 4.3 TrackTrack

**TrackTrack** (CVPR 2025, built into Ultralytics 8.4) adds two ideas that suit this footage:

- **Track-aware initialisation (TAI):** a new track is **not** started on a box that overlaps an existing
  track by more than `tai_thr`. This blocks exactly the failure seen here: duplicate boxes and snow /
  roof "blobs" next to a real vehicle spawning extra IDs.
- **Weighted association from the track's point of view:** the matching cost combines IoU
  (`iou_weight`), appearance (`reid_weight`, unused here because `with_reid: False`), detection
  confidence (`conf_weight`) and motion direction (`angle_weight`), and a looser re-binding pass
  (`lost_match_thr`) re-attaches tracks that were lost for a while.

Configuration: `ml/violation_engine/tracktrack_ours.yaml`

| Parameter | Value (stock) | Meaning |
|---|---|---|
| `track_high_thresh` | 0.15 (0.6) | First-pass detection score. Lowered because these vehicles score 0.1–0.5. |
| `track_low_thresh` | 0.05 (0.25) | Second-pass floor. |
| `new_track_thresh` | 0.35 (0.7) | Minimum score to start a track. 0.15/0.25/0.35 scored the same on the evaluation clip; 0.35 also blocks faint blobs. |
| `track_buffer` | 90 (30) | Frames a lost track is kept (about 3–4 s). |
| `match_thresh` | 0.7 | Matching gate. |
| `lost_match_thr` | 0.9 (off) | Looser re-binding for still-lost tracks. |
| `iou_weight` / `reid_weight` / `conf_weight` / `angle_weight` | 0.5 / 0.5 / 0.1 / 0.1 (angle stock 0.05) | Association cost weights; angle raised for small, fast objects. |
| `tai_thr` | 0.45 (0.55) | TAI overlap limit, lower for crowded scenes. |
| `min_track_len` | 3 | Frames before a track is reported. |
| `gmc_method` | sparseOptFlow | Camera-motion compensation. |
| `with_reid` | False | Appearance model off: measured to add nothing on this footage. |

### 4.4 Why TrackTrack was chosen

On the labelled CARLA clip (1,987 frames, 40 vehicles, same detector and post-processing for every
tracker): TrackTrack had the highest IDF1 (0.871), the fewest identity switches (2) and no
false-positive tracks; ByteTrack had 14 switches, BoT-SORT 3–4. With the retrained detector the final
pipeline scores HOTA 0.639, IDF1 0.881, MOTA 0.826, 2 switches, 40 identities for 40 vehicles.

---

## 5. Track post-processing (offline)

Run after tracking on recorded flights, in this order.

### 5.1 Tracklet stitching (`stitch_tracklets.py`)

During a sudden camera move the detector briefly loses vehicles and the tracker gives them a new ID
when they come back. Offline, the whole flight is known, so a second pass re-links "track A ended,
track B started shortly after at the predicted place":

1. **Position:** A's last position is carried forward through the per-frame camera motion (the same
   sparse optical-flow GMC) plus A's own recent velocity (measured over its last `EDGE_FRAMES` = 5
   frames). It must land near B's first box.
2. **Size and class:** similar box size; same or commonly-confused class (`car`/`van`).
3. **Appearance:** HSV colour histograms of the box crops must match (distance ≤ `max_app_dist` 0.4).
4. **Gap:** up to `max_gap` 90 frames; a moving vehicle's predicted path is trusted for up to
   `MOVING_MAX_GAP_S` = 3 s.

Candidate pairs are linked **greedily by lowest combined cost**; each track gets at most one
predecessor and one successor, so chains A → B → C are allowed. Output: `trajectories_stitched.csv`.

### 5.2 Class voting, short-track removal, gap filling (`postprocess_tracks.py`)

| Step | Rule | Parameter |
|---|---|---|
| **D2 class voting** | The detector classifies each frame independently, so a vehicle flickers car ↔ truck. Each track gets the **confidence-weighted majority class** over its life, written to every row (the per-frame class is kept as `raw_class`). | `CLASS_MAP` van → car |
| **D4 false-positive removal** | Drop tracks shorter than 1 s or with mean confidence below 0.3. Never filtered by "stationary": parked cars matter. | `MIN_DURATION_S` 1.0, `MIN_MEAN_CONF` 0.3 |
| **D3 gap filling** | Inside a kept track, gaps up to 1 s get linearly interpolated boxes (`conf` 0, `interp` = 1), so a briefly lost vehicle doesn't blink out. On the evaluation clip: MOTA +1.6 to +2.3 points, precision unchanged. | `GAP_FILL_S` 1.0 |

Output: `trajectories_final.csv` and `track_summary.csv` (one row per track, kept/dropped and why).

Interpolated rows are **not** used as measurements later (they are straight lines in pixels while the
camera moves); the smoother predicts through them instead.

---

## 6. Pixels to metres: ground projection

`ml/violation_engine/ground_coords.py`. Rules need metres and seconds; pixels mean nothing when the
drone moves (at 67 m altitude one pixel is about 7 cm, and a 2 m drift moves every parked car by ~30 px).

### 6.1 Ray–plane intersection (flat ground)

For each box centre:

1. Build a **ray** from the camera centre through that pixel, using the camera's field of view, image
   size and its world pose (position + pitch/yaw/roll) for that frame.
2. Intersect the ray with the horizontal plane `z = ground_z`.
   - `ground_z` = median height of the vehicles in the flight + `BOX_CENTRE_Z` (seen from straight
     above, a box centre is about half way up the vehicle, not on the road).
3. The intersection `(wx, wy)` is the vehicle's position in **CARLA world metres**.

Measured against CARLA's true vehicle centres (20,683 boxes, 3 flights): **median error 0.11–0.12 m,
95% within 0.74–0.88 m**.

### 6.2 Road-surface projection (bridges and flyovers)

A flat plane is wrong for raised roads: a road 10 m up seen from 67.6 m lands ~17% further from the
nadir, about one lane off. With a lane map that has heights, `RoadSurface` **rasterises lane heights on
a 0.5 m grid** (top and bottom surface where roads overlap) and `to_ground_on_roads()` intersects each
ray with height levels every 0.2 m, keeping the **highest level where the ray lands on a road of that
height** (the deck hides what is below). On a flight over an elevated ring, raised-road error fell from
4.91 m to 0.26 m median.

### 6.3 Camera pose per frame

New recordings log the pose on every frame. Older flights logged it only every few ticks; between those,
position is interpolated linearly and **rotation by slerp** (spherical interpolation of quaternions).
Euler angles cannot be interpolated here because at pitch −90° yaw and roll are degenerate ("gimbal
lock"). Interpolated frames are marked `pose_exact = 0` and are not used as measurements when exact
frames exist.

---

## 7. Smoothing: Kalman filter and RTS smoother

`ml/violation_engine/kinematics.py`. Positions jitter by ~0.1 m; at ~27 fps a frame-to-frame difference
would give ±10 km/h of speed noise. The system estimates position, velocity and heading from the
**whole track** instead.

### 7.1 The model

One **constant-acceleration (CA) model per axis** (x and y separately). State per axis:

```
state  s = [p, v, a]            position (m), velocity (m/s), acceleration (m/s²)
F(dt)  = | 1  dt  dt²/2 |       transition over dt seconds (dt from the real simulator time)
         | 0  1   dt    |
         | 0  0   1     |
Q(dt)  = q · | dt⁵/20  dt⁴/8  dt³/6 |     process noise: "white-noise jerk" (jerk = change of acceleration)
             | dt⁴/8   dt³/3  dt²/2 |     q = JERK_Q = 2.0 m²/s⁵
             | dt³/6   dt²/2  dt    |
H      = [1, 0, 0]              only position is measured
R      = σ²,  σ = MEAS_SIGMA_M = 0.15 m   (the measured projection error)
```

### 7.2 Kalman filter (forward pass)

For every frame in time order:

1. **Predict:** `s⁻ = F s`, `P⁻ = F P Fᵀ + Q` (P = state uncertainty).
2. **Gate:** if the measurement is more than `GATE_SIGMA` = 5 standard deviations from the prediction,
   skip it (a one-frame box glitch). After `MAX_REJECTS` = 3 rejections in a row the next one is
   accepted anyway, so the filter cannot run away on extrapolation. No gate during a track's first
   `WARMUP_S` = 0.5 s.
3. **Update:** Kalman gain `K = P⁻Hᵀ (H P⁻ Hᵀ + R)⁻¹`; `s = s⁻ + K (z − H s⁻)`; `P = (I − K H) P⁻`.

Rows that are not measurements (box touching the frame edge within `EDGE_PX` = 2 px, gap-filled rows,
interpolated poses) are **predicted through**, not used to update.

### 7.3 RTS smoother (backward pass)

The **Rauch–Tung–Striebel** smoother runs backwards from the last frame and corrects each filtered
state using what happened later:

```
C_k   = P_k Fᵀ (P⁻_{k+1})⁻¹
s̃_k  = s_k + C_k (s̃_{k+1} − s⁻_{k+1})
```

So the speed at frame k uses frames before **and after** k. This is only possible offline; the live
pipeline uses a fixed-lag version (Section 11.3).

### 7.4 Track splitting (ID-switch protection)

A track is cut into pieces (new IDs) where:

- two consecutive measurements are more than `SPLIT_GAP_S` = 2 s apart, or
- moving between them would need more than `SPLIT_SPEED_KMH` = 150 km/h, or an acceleration above
  `SPLIT_ACCEL_MPS2` = 15 m/s² (velocities taken over at least `SPLIT_MIN_DT_S` = 0.2 s, with
  `SPLIT_SLACK_MPS` = 5 m/s allowance for noise).

Without this, an ID switch between two cars looked like 68–170 km/h speeding or wrong-way driving.

### 7.5 Outputs (`kinematics.csv`)

Per vehicle per frame: `x, y, vx, vy, speed_kmh, speed_sigma_kmh, heading_deg, visible`.

- `heading_deg = atan2(vy, vx)`, left blank below `HEADING_MIN_KMH` = 3 km/h (the direction of a
  nearly stopped car is noise).
- `speed_sigma_kmh` = the filter's own speed uncertainty, used by the speeding rule.
- `visible = 1` only for a full box with a real measurement within `SUPPORT_S` = 0.4 s on both sides.
  The moving-vehicle rules (speeding, wrong-way, lane, U-turn) trust only those rows.

Measured on a Town05 test flight: speed error median 1.4 km/h (95th percentile 5.8 km/h), heading error
median 0.5°; parked cars read 0.22 km/h median.

---

## 8. Road knowledge: lane maps, zones and map matching

### 8.1 Lane map export (`simulation/carla_scripts/export_lane_map.py`)

Every CARLA town has an **OpenDRIVE** (`.xodr`) file: the standard XML description of roads, lanes,
markings, junctions and signals. The exporter reads it **offline** (`carla.Map(name, xodr)`, no running
simulator) and writes one JSON per town to `ml/violation_engine/configs/scenes/<Town>.json`:

| Element | What is stored |
|---|---|
| **Lanes** | One per (road, section, lane): centreline points every metre **in the driving direction**, width, lane type (driving / shoulder / parking), junction flag, left/right line type (solid, broken, solidsolid, ...), speed limit, `road_id`, `next` (successor lanes = the lane graph), `lane_change` permission, height `z` per point, `bridge`, `tunnel`, `ramp`, `road_class` (highway/urban), `one_way`, `median_left`, `restricted` |
| **Junctions** | Outline polygon, centre, member lanes, approaches with a stop line across each |
| **Zones** | Crosswalk polygons (`Map.get_crosswalks()`); optionally highway zones |
| **Stop lines** | From the traffic lights' `get_stop_waypoints()` |

CARLA's speed signs (OpenDRIVE type 274) say "mph" but the values are km/h (30/60/90); they are used as
km/h. Lanes are split where a speed sign or a bridge starts. `road_features.py` derives what OpenDRIVE
does not say directly (road class, one-way, ramps, medians) without overwriting anything already set.

### 8.2 Zones

A **zone** is a polygon in map metres with a type the rules understand:

| Zone type | Used by | Extra parameters |
|---|---|---|
| `no_parking` | No-parking rule (also "no stopping" with a short grace) | `grace_s` (wait before flagging) |
| `crosswalk` | Zebra-crossing rule | — |
| `highway` | Highway-stop rule | `grace_s` |
| `speed` | Speeding (overrides the lane's limit) | `limit_kmh` |
| `no_u_turn` | U-turn rule | — |

Zones come from the lane map (crosswalks), from a scenario log (staged tests), or are drawn by a planner
in the dashboard and saved through `POST /api/zones` (Section 12.7).

**Coordinates:** CARLA's world axes are **left-handed** (seen from above, +y points to the right of +x).
The scene says `"coords": "carla_world_m"` and the engine flips the sign of sideways offsets, otherwise
"left of the lane" comes out mirrored (that bug once produced 9 false solid-line crossings).

### 8.3 Map matching (`lane_map.py`)

For every smoothed position the engine finds the lane the vehicle is in:

- **Candidates:** lanes whose centreline is within half the lane width + `MATCH_MARGIN_M` = 0.75 m
  (allowance for position noise), found quickly with a k-d tree over centreline points.
- **Choice:** the lane with the smallest sideways offset |d|.
- **Position only, never heading.** A wrong-way car must be matched to the lane it is physically in;
  matching by heading would put it in the opposite lane, which it agrees with, and it would never be
  flagged.
- **Road level:** with heights, only lanes within `LEVEL_TOL_M` = 2.5 m of the vehicle's road level
  count, so a car under a flyover is not matched to the deck above.
- **Drive lane:** if the physical lane runs against the vehicle's heading (more than 90° off), the
  engine also looks for the nearest lane running its way (`match_along`). Speeding reads the limit of
  that lane, so a car on a centre line does not get the oncoming lane's limit.

**Frenet coordinates:** each match also gives **s** (distance along the lane) and **d** (sideways offset
from the centre). Curves become straight lines: wrong-way = s decreasing; lane violation = d crossing a
line.

---

## 9. The violation engine

`ml/violation_engine/rules.py`, `predicates.py`, `events.py`. Thresholds are in **seconds and metres**
(the requirement's frame counts assume 30 fps: 5 frames = 0.17 s, 10 frames = 0.33 s), so they hold at
any frame rate. All defaults are in `rules.DEFAULTS` and can be overridden by a configuration profile.

### 9.1 How the engine runs

```
for each frame, in time order:
    obs = [observe(row) for row in that frame]     # Obs = vehicle + lane match + zones it is in
    history.add(obs)                               # last 10 s of every track
    for monitor in monitors:
        monitor.step(t, frame, obs, history)       # each rule updates its open episodes
at the end: every monitor closes its episodes; each event gets its condition id
```

The engine is **frame-by-frame**, so the same code runs offline (`run_violations.py` feeds a recorded
flight) and live (`online.IncrementalEngine` feeds it one frame at a time).

**Obs** (one vehicle in one frame): frame, time, track id, class, confidence, visible, x, y, vx, vy,
speed, speed sigma, heading, lane match, drive lane, zones.

**Road-derived highway zone:** lanes of a highway-class road, ramps, bridges and tunnels automatically
act as one "highway" zone per road, so illegal-stopping rules work without a drawn zone.

### 9.2 Shared predicates (`predicates.py`)

| Predicate | True when | Parameters |
|---|---|---|
| `stopped` | **Stay-point test:** every position of the last 3 s lies within 1 m of their mean (a track younger than 3 s but ≥ 1 s old is tested over its whole life). More robust to box jitter than a speed test. | `STOP_WINDOW_S` 3, `STOP_RADIUS_M` 1, `STOP_MIN_AGE_S` 1 |
| `slow(v)` | Speed below v km/h | 2 (parking, zebra), 5 (highway) |
| `queue_context` | A slow (< 5 km/h) vehicle is directly ahead in the same lane within 10 m (sideways within 2 m), or, without a lane, any slow vehicle within 8 m. The stop is explained by traffic. | `QUEUE_AHEAD_M` 10, `QUEUE_SIDE_M` 2, `QUEUE_NEAR_M` 8 |
| `speed_tolerance_kmh` | Enforcement tolerance, EU practice: 5 km/h below 100 km/h, 5% above | `tolerance: "eu"` |
| `straddle_overlap` | The vehicle's footprint (box + heading, half-widths car 0.9 m, truck/bus 1.25 m) overlaps a lane line | `min_overlap_m` 0.3 |
| `against_lane_angle` | Angle between the velocity and the lane direction | — |

### 9.3 The episode: how "for N seconds" works

Every rule uses an **Episode** state machine (`predicates.Episode`):

```
condition first true  -> pending (start time recorded)
pending, true for >= min_s           -> CONFIRMED: the event opens (flag time)
pending, false for > confirm_gap_s   -> discarded
confirmed, false for > close_gap_s   -> the event closes (end time)
```

Short interruptions are tolerated (a car hidden for a moment does not reset its timer). The episode
also counts frames, the sum of detection confidences and how many frames had a full box, which give the
event's **track quality** = mean confidence × share of visible frames.

### 9.4 The seven monitors

**1. No-parking (`StopInZoneMonitor`, type `no_parking`)**
- Rule: inside a `no_parking` zone, slow (< 2 km/h) **and** stopped (stay-point), for ≥ 30 s (or the
  zone's `grace_s`).
- **Place memory:** the dwell timer belongs to the **spot** (radius 1.5 m), not to a track ID. Any
  stopped observation within 1.5 m of a spot extends that spot's timer whatever its ID, so an ID switch,
  two IDs on one parked car, or a car lost under a tree crown and re-found keep one timer. A spot is
  kept for `keep_s` = 30 s without sightings. It closes when one of its tracks is seen moving away,
  when another vehicle drives through it (≥ 8 km/h, beyond 1 m past its radius), or when a new track
  drives into it after a gap.
- Output value: `dwell_s`, `grace_s`.

**2. Zebra crossing (`StopInZoneMonitor`, type `zebra_crossing`)**
- Rule: inside a `crosswalk` zone, slow (< 2 km/h) and stopped, for > 10 s, and **not** in a queue.
- If at least half the episode's frames were in queue context, the event is tagged `queue` and its
  status set to `suppressed` (kept for audit, not flagged).
- Pedestrian-aware variants (blocking, failure to yield; conditions F2/F3/F5) use a second detector for
  people (`zebra_pedestrians.py`, live `people.py`) when a crosswalk is in view.

**3. Highway stop (`StopInZoneMonitor`, type `highway_stop`)**
- Rule: inside a highway zone (drawn or road-derived), < 5 km/h for > 20 s, not in a queue.
- A vehicle stopped alone on the **shoulder** is tagged `possible_breakdown` (its own status, not a
  standard violation). Tags `on_bridge`, `ramp_area` when most of the episode was there.

**4. Wrong-way (`WrongWayMonitor`)**
- Rule: on a regular (non-junction) lane, moving > 5 km/h at more than **150°** to the lane direction,
  for ≥ 0.17 s (5 frames) **and** ≥ 5 m driven backwards along s.
- Not checked on junction lanes (turning paths cross each other); decided on the exit lane.
- Conditions: C1 against traffic, C2 wrong-way entry (just came from a junction or another road), C3 on
  a divided highway, C4 on a ramp, C5 in a one-way lane.

**5. Speeding (`SpeedingMonitor`)**
- **Limit:** a speed zone's `limit_kmh` overrides the lane's own limit (condition E3); a lower limit for
  the vehicle's class caps either (E4).
- **Threshold:** `thr = limit + tolerance` (5 km/h below 100, 5% above).
- **Proven over the limit:** the **lower bound** `speed − 2σ` (σ = the smoother's speed uncertainty)
  must exceed `thr`, for ≥ 0.33 s (10 frames). Only `visible` rows count, and not during a track's
  first 1 s (start-up transients at the frame edge).
- Example: limit 50 → thr 55. A car at 58.0 ± 1.0 km/h has lower bound 56.0 > 55: flagged. A car at
  54 km/h is not proven over: no event.
- Output value: `max_speed_kmh`, `limit_kmh`, `limit_source` (lane/zone/class), `threshold_kmh`,
  `speed_sigma_kmh`.

**6. Illegal U-turn (`UTurnMonitor`, `UTurnAnywhereMonitor`)**
- In a `no_u_turn` zone: heading change > **160°** inside the zone, as one smooth arc (no reversing
  segments, one turning direction), leaving the opposite way. A three-point turn (has reversing) is
  tagged, not flagged.
- Anywhere (`anywhere: true`): a ≥ 160° turn completed within 30 s, ending on an opposing lane of the
  same road; illegal only across a solid centre line (D2), at a junction listed as no-U-turn (D3) or on
  a divided road through the median (D4). Turning twice around a block onto a parallel street is not a
  U-turn.

**7. Lane violation (`LaneViolationMonitor`)**
- **Straddling (A4):** footprint overlapping a lane line by ≥ 0.3 m for > 3 s while moving > 5 km/h.
- **Solid-line crossing (A2):** the lane changed and the line crossed was solid or double solid
  (flagged when the change completes).
- **Illegal lane change (A3):** leaving a lane whose `lane_change` forbids that side.
- **Wrong lane through a junction (A1):** the lane taken does not lead where the vehicle went.
- **Restricted lane / shoulder (A5/A6):** driving in a `restricted` (bus/emergency) lane ≥ 1 s, or on
  the shoulder ≥ 3 s above 10 km/h; buses exempt from bus lanes.
- **Unsafe lane change (A8):** at the lane-change moment (within 1 s after the centre crosses), the
  time-to-collision (TTC) to the nearest vehicle ahead or behind in the target lane is below 2 s for
  ≥ 0.3 s (below 1 s = high risk), or the bodies overlap sideways; not below 15 km/h (zipper merging).

**Optional 8th: red light (`RedLightMonitor`, off by default, CARLA only)**
- The vehicle's front (centre + half-length: car 2.3 m, bus 6 m) crosses a stop line in the approach
  direction while that light is red, and gets 10 m into the junction before it first stops. Yellow and
  "already past the line when it turned red" are not flagged; confidence is lower within the first
  second of red.

### 9.5 Conditions: the finer labels

Each event also carries a **condition id** from `schemas/conditions.json`, chosen from its type and tags
after it closes (highest priority wins when several apply):

| Family | Ids |
|---|---|
| A — lane use | A1 wrong lane through junction, A2 solid line, A3 illegal lane change, A4 on a divider, A5 restricted lane, A6 shoulder, A8 unsafe lane change |
| B — stopping | B1 highway, B2 highway shoulder, B3 no-stopping zone, B4 near an entrance/exit, B5 bridge or tunnel |
| C — wrong way | C1 against traffic, C2 wrong-way entry, C3 divided highway, C4 ramp, C5 one-way lane |
| D — U-turn | D1 prohibited zone, D2 across a solid line, D3 prohibited junction, D4 through a median |
| E — speeding | E1 over the limit, E3 in a speed zone, E4 by vehicle category |
| F — crossings | F1 stopping on a zebra, F2 blocking a crossing, F3 failure to yield, F5 obstructing pedestrians |

A configuration profile can turn conditions off; their events are then dropped.

---

## 10. Events, confidence, status and evidence

### 10.1 The event record (`schemas/event.schema.json`)

```json
{"event_id": "20261010_120643-0007", "type": "speeding", "condition": "E1", "kind": "violation",
 "track_ids": [8], "cls": "car", "start_s": 41.20, "flag_s": 41.53, "end_s": 44.90,
 "lane_id": "r12_s0_l-1", "zone_id": null, "x": -63.2, "y": 1.9,
 "value": {"max_speed_kmh": 71.4, "limit_kmh": 50, "threshold_kmh": 55, "speed_sigma_kmh": 1.1},
 "confidence": 0.93, "status": "flagged", "tags": [],
 "evidence": {"frame": 1112, "clip": "events/....mp4", "snapshot": "..."}}
```

- `start_s`: when the condition first held; `flag_s`: when it was confirmed (event opens);
  `end_s`: when it stopped holding. All in the session's simulator time.
- `x, y`: map metres (CARLA world). In the database they are a PostGIS point with SRID 0 (a local
  metric frame, not latitude/longitude).

### 10.2 Confidence

At close, `events.Event.close` computes:

```
confidence = 0.4 · clip(margin) + 0.3 · clip(quality) + 0.3 · clip(duration_score)      clip = to [0, 1]
```

| Term | Meaning | Stop-type rules | Speeding |
|---|---|---|---|
| `margin` | How far past the threshold | 1.0 | `(max lower bound − thr) / max(3σ, 1.5)` |
| `quality` | Track quality = mean detector confidence × share of frames with a full box | same | same |
| `duration_score` | How long beyond the minimum | `dwell / (2 × grace)` | `duration / max(2 × min_s, 1)` |

### 10.3 Status

| Status | Meaning |
|---|---|
| `flagged` | Confidence ≥ the minimum for its type |
| `needs_review` | Below the minimum: kept and shown to the reviewer |
| `suppressed` | An edge case the requirements say not to flag (queue, three-point turn); kept for audit |
| `possible_breakdown` | Highway stop alone on the shoulder |

Minimum confidence per type (`events.MIN_CONFIDENCE`): no-parking 0.90, wrong-way 0.85, U-turn 0.82,
speeding 0.85, lane 0.80, zebra 0.85, highway stop 0.88, red light 0.92.

After this, a **human** reviews: an officer marks the event `confirmed` or `dismissed` (Section 12.5).
The system proposes; a person decides.

### 10.4 Evidence

- **Offline:** `render_violations.py --clips` writes one mp4 per event, from 7 s before the flag to 3 s
  after, with zones, lanes, speeds and a banner (event, status, confidence, tags).
- **Live:** `services/live/evidence.py` keeps the last 14 s of processed frames; at the flag it saves a
  **snapshot** (frame nearest the flag time) and, 4 s later, a **clip** from 6 s before to 4 s after,
  both redrawn with the event's vehicles marked red. Clips are **VP8 WebM** (the codec browsers play).
- Overlay colours: **green** = no rule active, **orange** = a rule is timing the vehicle ("checking"),
  **red** = flagged.

---

## 11. The live pipeline: CARLA to backend in real time

### 11.1 The frame transport (`services/live/transport.py`)

One **TCP** connection, Python standard library only (it must run in both the simulator's Python 3.10
environment and the main one). Every message is:

```
MAGIC "AVF1" (4 bytes) | header length (uint32 big-endian) | payload length (uint32 BE) | header JSON | payload
```

| Message | Header | Payload |
|---|---|---|
| `hello` (first) | width, height, fov, ground_z, map, source; optionally every traffic light with its stop lines and current state | — |
| `frame` | frame, carla_frame, **sim_time**, **pose [x, y, z, pitch, yaw, roll]**, capture_wall (for latency) | JPEG bytes |
| `signal` | traffic-light changes `[carla_frame, sim_time, id, state]` | — |
| `end` | — | — |

- The **detector listens** (default `0.0.0.0:5555`); the source (`stream_flight.py`, or
  `replay_source.py` for a recorded flight) **connects** and reconnects if the link drops. The same code
  works on one machine (`127.0.0.1`) or two (Wi-Fi or a direct Ethernet cable).
- **Latest-frame-wins:** the sender keeps a short queue and drops the oldest frames; the receiver reads
  every message off the socket at line rate but keeps only the newest frame. A slow GPU therefore never
  builds up delay; frames are skipped instead. Traffic-light messages are never dropped.

### 11.2 The pipeline loop (`services/live/pipeline.py`)

For each newest frame:

1. **Detect + track:** same weights, confidence, de-duplication and TrackTrack configuration as offline,
   with `persist=True` so track state carries across calls.
2. **Ground coordinates:** `online.LiveCamera` projects every box with the **pose streamed with that
   frame** (flat ground, or the road surface from the lane map).
3. **Online kinematics** (11.3).
4. **Rules:** `online.IncrementalEngine` = the same `rules.Engine`, stepped one frame at a time; it
   reports events when they **open** (flag time) and when they **close**.
5. **Outputs to the backend** through `backend_client.py` (11.4): vehicle states, events, raw and
   annotated frames, traffic lights, evidence.
6. **Local files:** `ml/data/results/live/<session>/` gets `events.jsonl`, `kinematics.csv`, `tracks.csv`,
   `violations.json`, `summary.json` (fps, per-stage milliseconds, latency, drops, GPU use),
   `evidence/`.

Extras:
- **Zone watcher** (`zone_watch.py`): loads the backend's zone file for the map
  (`backend/data/zones/<map>.json`) at start and checks it every 2 s; a zone a planner saves, edits or
  deletes in the dashboard applies to the running engine from the next frame, without a restart.
- **People** (`people.py`, `--people auto`): while a crosswalk is near the view, a second detector
  (pedestrian/people classes) with its own tracker feeds the zebra rules F2/F3/F5.

Measured (replay, detector alone on the GPU): 13.1 fps processed, detect + track median 52 ms, event
latency from the flagged frame's capture to the dashboard WebSocket median 1.19 s, max 1.71 s.

### 11.3 Online kinematics (`ml/violation_engine/online.py`)

Offline, the RTS smoother looks backwards from the end of each track; live, the end is unknown. So:

- **Forward Kalman filter** per track, same model, noise, gate, warm-up and split tests as Section 7.
- **Fixed-lag smoothing:** rows go to the engine `LAG_S` = **1 s late**. Within that window the RTS
  pass runs over the last second only (`--kin fixedlag`), or the plain filtered state is used
  (`--kin filter`). The lag also lets the pipeline apply the offline track filters (≥ 1 s long, mean
  confidence ≥ 0.3) and the "measurement on both sides" visibility test.
- **Online re-linking** (replaces offline stitching): a new tracker ID that starts within
  `RELINK_RADIUS_M` = 2.5 m (in ground metres, growing by 30% of the distance the lost track would have
  driven) of where a recently lost track (≤ 3 s) is predicted to be continues that track. Lost tracks
  are forgotten after 10 s.

### 11.4 Talking to the backend (`services/live/backend_client.py`)

A background thread drains a queue and POSTs with the **service token**; the detector loop never waits
for the network.

| What | Endpoint | Behaviour |
|---|---|---|
| Vehicle states | `POST /api/live/state` (≤ `--state-hz`) | Only the newest batch is kept: a stale position is useless |
| Event opens | `POST /api/events` | Sent at flag time with tag `provisional`, confidence 0 |
| Event closes | `PUT /api/live/events/{id}` | Final status, confidence, end time; replaces the provisional one |
| Evidence | `POST /api/live/events/{id}/evidence?kind=snapshot|clip` | Retried every 2 s up to 30 times (the backend answers 404 until the event exists) |
| Traffic lights | `POST /api/live/signals` | Never replaced; merged and retried after 1 s |
| Frames | `POST /api/live/frame?kind=raw|annotated` | Newest only; sent last |

**If the backend is down**, event posts are buffered in order (up to 10,000), retried with exponential
backoff **1, 2, 4, 8, 16, 30 s**, optionally spooled to disk so a restart loses nothing, and flushed when
the backend answers again. A post the backend rejects as bad data (4xx other than 408/429) is dropped
with a log line rather than retried forever.

---

## 12. The backend (FastAPI, PostGIS, Redis, SeaweedFS)

`backend/app/`; the full contract is `backend/API.md`. Start: `docker compose up -d`, then
`venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000`.

### 12.1 Services and what each stores

| Service | Container | Stores |
|---|---|---|
| **FastAPI** (Python, async) | runs on the host | REST API under `/api`, WebSocket `/api/ws/live` |
| **PostgreSQL + PostGIS** | `postgis/postgis:16-3.4`, port 5432 | Tables `users`, `sessions`, `tracks`, `events` (geometry Point, SRID 0), `reviews`, `profiles` (versioned), `zones` (Polygon) + `zone_versions`, `recommendations`, `planner_history`, `scenarios`, `plans` + versions/runs/reports, `reports`, `audit_log` |
| **Redis** | `redis:7.4-alpine`, port 6379, no persistence | Live vehicle state, pub/sub channels, traffic lights, refresh tokens and token deny-lists |
| **SeaweedFS** (S3-compatible) | `chrislusf/seaweedfs:3.97`, port 9000 | Bucket `evidence`: clips, snapshots, recorded reports |

Database access is **SQLAlchemy 2 async + asyncpg**, with **GeoAlchemy2** for the geometry columns. On
startup (`main.py` lifespan) the app creates the tables, seeds the dev users and the configuration
profiles, ensures the S3 bucket exists and starts the Redis listener.

### 12.2 Authentication and roles (`auth.py`)

- `POST /api/auth/login` → a short-lived **access JWT** (HS256, 15 min) and a single-use **refresh
  token** (12 h). The client sends `Authorization: Bearer <access_token>` and renews with
  `POST /api/auth/refresh` before expiry (each refresh token works once).
- Logout puts the access token's id on a Redis deny-list for its remaining life. Disabling a user,
  changing their role or resetting their password sets a cut-off time: older tokens stop working.
- **Roles:** `OFFICER` (reviews violations), `OPERATOR` (imports sessions), `PLANNER` (zones, profiles,
  recommendation decisions), `MAINTENANCE`, `ADMIN` (users, everything). Reads need any role.
- The live pipeline authenticates with a **service token** (env `SERVICE_TOKEN`).
- Passwords are stored as bcrypt hashes. Every request is written to the audit log.

### 12.3 Importing a recorded flight (`POST /api/sessions/import`, `ingest.py`)

Body `{flight, violations_dir?, scene?, profile?}`:

1. Read `<results>/<flight>/tracktrack_ours/<violations_dir>/violations.json` (default: the newest
   `violations*` folder).
2. **Validate every event** against `schemas/event.schema.json`; rejects are counted and reported.
3. Upload evidence clips and snapshots to S3 under `evidence/<flight>/`; mp4 clips are transcoded to WebM
   in the background.
4. Load `kinematics.csv` as tracks, **downsampled to ≤ 5 Hz** for the replay.
5. Create or replace the session (session id = flight folder name). Re-importing replaces events and
   tracks but keeps reviews of events that still exist.

### 12.4 Events, statistics, exports

- `GET /api/events`: filters `session_id, type, condition, status, review, kind, since, until,
  bbox=x0,y0,x1,y1, limit, offset` (comma lists allowed); ordered by `occurred_at` (session start +
  event time). `bbox` uses the PostGIS geometry.
- `GET /api/stats`: `{total, by_type, by_condition, by_status, by_review, by_hour, hotspots}` with the
  same filters. **Hotspots here = the top 10 cells of a 25 m grid** (Section 16.1).
- `GET /api/export?format=pdf|xlsx|geojson|csv`: the same filters; CSV, GeoJSON (coordinates in map
  metres, with a note saying so), XLSX (openpyxl), PDF (ReportLab). Capped at 10,000 events (PDF 6.97 s,
  XLSX 3.30 s for 10,000). Every export is also stored in S3 and recorded in the report history.

### 12.5 Review

`POST /api/events/{id}/review` `{outcome: confirmed|dismissed, note?}` (OFFICER or ADMIN). Every review is
kept (`GET /api/events/{id}/reviews`).

### 12.6 Configuration profiles

A **profile** (`schemas/profile.schema.json`) holds rule thresholds, which violation types and conditions
are on, class speed limits, and **lane overrides** (`road.lane_overrides`: per lane id or glob such as
`r46_*`, change `speed_limit_kmh`, `restricted`, `road_class`, `lane_type`, `lane_change`, `one_way`,
`bridge`, `tunnel`, `ramp`, `median_left`). `PUT /api/profiles/{name}` validates and stores a **new
version** with who/when/why; history is kept. `GET /api/scenes/{town}?profile=` returns the lane map with
that profile's overrides applied, using the engine's own code.

### 12.7 Zones

`POST/PUT/DELETE /api/zones` (PLANNER/ADMIN). Geometry is validated (simple polygon, 3–500 vertices,
1 m² to 1 km²). Every change bumps the zone's `version` and appends a `zone_versions` row. **After each
change the backend rewrites `backend/data/zones/<scene>.json`** with the scene's active zones in the
engine's `--zones` format: the live pipeline's zone watcher picks it up within 2 s, and offline runs can
pass it with `--zones`.

### 12.8 Files and signed URLs

Evidence and reports are served by `GET /api/files/{key}`. URLs handed to the browser are **signed**
(`?exp=<unix time>&sig=<HMAC-SHA256>`, valid 60–65 min) so a `<video>` tag can load them without a
token header. HTTP `Range` requests are supported (seeking in videos).

### 12.9 Health

`GET /api/health` → `{db, redis, s3}` each `"ok"` or an error, plus the background transcoding queue.
These are the DB / REDIS / S3 badges on the Live page.

---

## 13. How the backend feeds the frontend live

### 13.1 The path of one live vehicle state

```
pipeline --POST /api/live/state--> FastAPI
    FastAPI: SET live:{session}:{track} (Redis, expires after 2 s)
             PUBLISH live:{session}  {"type":"vehicles","items":[...]}
Redis pub/sub --> one listener task per backend process (live.py Hub._listen)
    --> every connected WebSocket that wants that session (≤ 10 Hz per socket; newest batch wins)
browser --> Live page / twin redraws the vehicles
```

### 13.2 The path of one event

```
pipeline --POST /api/events--> FastAPI: validate, store in PostGIS, PUBLISH "events"
    --> every socket immediately (events are never dropped or merged)
later: PUT /api/live/events/{id} (final) --> stored, published again (clients de-duplicate by event_id)
evidence upload --> stored in S3, event updated, pushed again
```

### 13.3 The WebSocket protocol (`WS /api/ws/live?session_id=&catch_up_s=&token=`)

| Message | When |
|---|---|
| `{"type":"vehicles","items":[...]}` | Vehicle states, at most 10 Hz. A new socket first gets the states still in Redis. |
| `{"type":"event","event":{...}}` | Every event create/update. |
| `{"type":"signals", ...}` | Traffic-light changes; on connect one snapshot per session with each light's recent changes. |
| `{"type":"event",...,"catch_up":true}` then `{"type":"catch_up","window_s":60,"n":N}` | **Catch-up:** right after connecting, events stored in the last `catch_up_s` seconds (max 60) are re-sent, so a browser that reconnects after a short drop misses nothing. |

Vehicle state fields: `{session_id, track_id, cls, x, y, speed_kmh, heading_deg, lane_id, state
("ok"|"checking"|"flagged"), t_s}`. A bad token closes the socket with code 1008.

**Why Redis in the middle:** the pipeline posts to one HTTP endpoint and does not need to know which
browsers are open; Redis pub/sub fans the message out, and the 2-second expiry means a stopped pipeline
leaves no stale vehicles on screen.

### 13.4 Live video

The pipeline posts the newest raw frame (what the model got) and the newest annotated frame (boxes,
IDs, speeds, rule state) as JPEGs; the backend keeps only the newest per session and kind, in memory.
`GET /api/live/video?kind=raw|annotated` streams them as **MJPEG** (`multipart/x-mixed-replace`), which a
plain `<img>` tag can display. `GET /api/live/sources` lists sessions that sent a frame in the last 10 s.

### 13.5 Client side (`frontend/src/api/index.ts`)

- The API client renews the access token a minute before it expires, so media links, the WebSocket and
  the twin keep working.
- The WebSocket reconnects with backoff 1, 2, 4 ... up to 10 s, and on reconnect asks for a catch-up
  window equal to the time it was away (plus 2 s, max 60 s).

---

## 14. The dashboard (frontend)

`frontend/`: **React 19 + TypeScript + Vite**, **Mantine** UI and charts, **TanStack Query** for API
caching, React Router. Development server on port 5173; requests to `/api` are proxied to the backend.

| Page | What it does | Main APIs |
|---|---|---|
| **Login** | Role-aware login | `/auth/login` |
| **Live** | Session picker (live or replay of an imported session), OPEN state, vehicle count, colour key; **camera footage (model input)** and **model output** side by side; event feed; DB/Redis/S3 health badges | WebSocket, `/live/video`, `/live/sources`, `/health` |
| **Violations** | Filterable table (type, condition, status, review, session, date/time); evidence drawer with the clip and snapshot; review buttons | `/events`, `/files`, `/events/{id}/review` |
| **Sessions** | List and import processed flights | `/sessions`, `/sessions/import` |
| **Configuration** | Profiles (thresholds, enabled rules) with versioned saves and history; zones | `/profiles`, `/zones`, `/road/attributes` |
| **Recommendations** | Each recommendation with its evidence, priority, confidence, limitations and alternatives; accept / reject / modify with a rationale; history | `/recommendations`, `/recommendations/{id}/decision`, `/planner/history` |
| **3D Twin** | The CesiumJS digital twin (Section 15) | `/scenes`, `/sessions/{id}/trajectories`, `/events`, `/zones`, WebSocket |

Some sections exist in the code but are hidden in the current UI (feature-gated): the Statistics route
(redirects to Live), the selected-lane editor, the Configuration road-configuration tab and the traffic
scenario builder section.

---

## 15. How the digital twin is built

`frontend/src/twin/` (CesiumJS). Mounted by `pages/Twin.tsx`; it uses the dashboard's login and API
client.

### 15.1 Coordinates (`coords.ts`)

Three frames are involved:

```
CARLA world metres (left-handed)  ->  local East-North-Up metres (right-handed)  ->  Cesium world (ECEF)
      east  =  x
      north = -y        (the flip makes the frame right-handed, so the town is not mirrored)
      up    =  z
```

ENU is anchored at a latitude/longitude (default **0°, 0°**, the geo-reference CARLA writes into its
OpenDRIVE files: CARLA towns have no real location). Cesium's `Transforms.eastNorthUpToFixedFrame` turns
local ENU into Earth-centred coordinates (ECEF) for rendering. A real site would pass its surveyed
anchor (`?anchor=lat,lon,h`).

### 15.2 Roads from the lane map (`laneGeometry.ts`)

The twin downloads the lane map (`GET /api/scenes/{town}`, optionally with a profile's overrides) and
builds **triangle meshes** in JavaScript, no external 3D model:

- **Lane ribbons:** each lane becomes a strip along its centreline, offset left and right by half its
  width, **at its real height** (lane `z`), so bridges and flyovers stand up in 3D. Colour by class
  (highway, ramp, junction, shoulder, bridge).
- **Markings** from the line types: solid, broken (3 m dash, 6 m gap), double (two lines 0.15 m apart),
  0.15 m wide.
- **Medians**, **junction outlines**, **stop lines** (0.4 m), **crosswalks**.
- **Bridge piers:** one every 30 m under lanes at least 2 m high.
- Layers are lifted by a few centimetres (driving 5 cm, markings 9 cm, ...) to avoid flicker
  (z-fighting) where they overlap.
- All ~900 lanes are **batched** into a few Cesium `Primitive`s with per-instance colours, which keeps
  it fast. The globe and imagery are hidden: no internet map is needed.

**Town objects** (`GET /api/scenes/{town}/objects`): buildings, trees, poles, fences, rail, bridges as
boxes (trees as ellipsoids) at their true position, size and rotation, exported from CARLA's environment
objects.

### 15.3 Vehicles

- **Live:** from the WebSocket `vehicles` messages, drawn at `(x, y)` on the road surface, coloured by
  state (green ok, amber checking, red flagged), with a speed label and a 3 s trail.
- **Replay** (`replay.ts`): trajectories of an imported session (`[t_s, x, y, speed_kmh]` at ≤ 5 Hz) are
  sampled at the slider time. A vehicle's colour is derived from the events: **amber** from `start_s` to
  `flag_s` (a rule is timing it), **red** from `flag_s` to `end_s` + 3 s (so short events stay visible),
  green otherwise. The replay can be synced to the dashboard's video (`?t=` or a seek message).

### 15.4 Overlays

| Layer | Definition |
|---|---|
| **Violation pins** | One pin per event at its `(x, y)`, coloured by type |
| **Hotspots** | Same as `/api/stats`: events binned on a **25 m grid**, top **10** cells, centre = mean event position; computed in the browser so live events appear without a request |
| **Problem sections** | The **8 lanes with the most events**, highlighted |
| **Zones** | From `/api/zones`, drawn as coloured polygons; planners can draw, edit and delete them on the map (saved through the API, versioned) |
| **Traffic lights** | Each light's state at the time of the vehicles on screen (from the signal timelines) |

### 15.5 Planner edits

Selecting a lane shows its attributes; a planner can change speed limit, restricted use and other
attributes and save them as a **new version of a configuration profile** with a note. The engine then
uses those overrides on the next run.

---

## 16. Analytics, hotspots and the recommendation system

`ml/planning/`. Called by the backend (`GET /api/recommendations?session_id=`), with the session's
events and lane map.

### 16.1 "Hotspot" means three different things

| Name | Where | Definition |
|---|---|---|
| **Grid hotspot** | `GET /api/stats`, the twin | Events counted in 25 m × 25 m cells; the 10 cells with most events. Simple, fast, shows "where events pile up". |
| **DBSCAN hotspot** | Planning (`analytics.hotspots`) | A density cluster of **at least 10 events of one type** within chains of **50 m** (DBSCAN, below). |
| **Major hotspot** | Planning | A DBSCAN hotspot with **≥ 50 events**. |
| **Emerging pattern** | Planning | Leftover events of one type on one road, grouped with a smaller DBSCAN (**≥ 2 events**): shown with lower confidence. |
| **Problem section** | Twin | One of the 8 lanes with the most events. |

### 16.2 DBSCAN (`analytics.dbscan`)

**Density-Based Spatial Clustering of Applications with Noise** (Ester et al., 1996), implemented here
directly (k-d tree for neighbour search, deterministic order):

1. For each event position, find its neighbours within **eps = 50 m** (the point itself counts).
2. A point with **≥ min_samples = 10** neighbours is a **core point**.
3. Start a cluster at an unvisited core point; add all its neighbours; from every neighbour that is also
   a core point, keep expanding. Stop when no new points are reachable.
4. Repeat. Points reachable from no core point are **noise** (label −1).

Why DBSCAN: no need to know the number of clusters in advance, clusters can have any shape (along a
road), and isolated events are left out as noise instead of being forced into a cluster. It is run
**separately per violation type**, on **counted** events only (`flagged` or `needs_review`).

### 16.3 Other analytics

- **Trends:** event counts per hour (wall-clock hour when events carry one, else session-clock bins).
- **Section statistics** (from `kinematics.csv`, per lane and per road): vehicles seen, density
  (vehicles per km of observed length), mean and **85th-percentile speed** (the speed 85% of vehicles
  do not exceed, a standard traffic-engineering measure), share over the limit, **queues** = stopped
  vehicles (< 5 km/h) chained along a lane with gaps under 12 m.

### 16.4 How a recommendation is made (`recommend.py`)

1. **Group** events: DBSCAN hotspots per type; leftovers as emerging patterns; plus **flow groups**
   from queues (≥ 5 stopped vehicles, ≥ 25 m).
2. **Context** for each group: road class, ramps, bridges/tunnels, nearby crossings (within 30 m),
   limits, dominant condition id, share of the area's events.
3. **Rule matching:** the first matching rule of the group's area gives the problem text, the action,
   the countermeasure and alternatives:

| Area | Rules (id: when it applies) |
|---|---|
| Speed management | SPD-HWY (highway), SPD-VUL (near crossings or in a speed zone), SPD-URB (otherwise) |
| Wrong-way prevention | WW-RAMP (ramp), WW-DIV (divided road), WW-ONEWAY (otherwise; one-way conversion with separation if wrong-way > 40% of the area's events) |
| U-turns | UT-MEDIAN, UT-JUNCTION, UT-ZONE |
| Illegal stopping | STOP-BRIDGE, STOP-RAMP, STOP-HWY, STOP-ZONE (no-parking) |
| Pedestrian safety | ZEB-BLOCK, ZEB-YIELD |
| Lane management | LANE-UNSAFE, LANE-SOLID, LANE-WRONG, LANE-MARKING |
| Intersections | RL-SIGNAL (red light) |
| Traffic flow | FLOW-QUEUE |

4. **Projected impact** from the **countermeasure catalogue** (`catalogue.py`): speed cameras, average
   speed cameras, limit review, traffic calming, wrong-way signage, one-way barriers, median closure,
   no-U-turn signage, no-stopping enforcement, stopping bays, crossing visibility, keep-clear boxes,
   signal timing/installation, marking refresh, delineators, lane-use signage, lane allocation,
   enforcement schedules. Each number is a **published rule-of-thumb** (a crash-modification factor
   from the literature), shown as "projected", never as our measurement. A countermeasure without a
   sourced number shows none.

5. **Priority** (0–100):

```
priority = 100 × severity × frequency × evidence quality × tier × planning objective

severity   design weight per type: wrong-way 1.0, red light 0.9, speeding 0.8, U-turn 0.8, zebra 0.75,
           highway stop 0.7, lane 0.6, no-parking 0.4, flow 0.5 (+0.1 near a crossing / on a bridge)
frequency  min(1, 0.3 + 0.7 · ln(1+n) / ln(1+50))       n = events in the group (log-scaled to 50)
quality    mean confidence of the group's events
tier       major hotspot 1.0, hotspot 0.9, flow 0.6, emerging 0.5
objective  planner's weight for the type (default 1.0)
band       high ≥ 50, medium ≥ 25, low otherwise
```

The explanation of every factor is returned with the score.

6. **Confidence** of the recommendation:
   `mean event confidence × tier × (1 − 0.3 × share needing review) × (1.0 if ≥ 2 sessions else 0.85)`;
   high ≥ 0.7, medium ≥ 0.45.

7. **Limitations, alternatives, validation method** and a **simulation block** (the change the CARLA
   validation runner can apply, or why it cannot be simulated) are attached. The id is deterministic
   (hash of rule, type, roads and the centroid on an eps grid), so the same hotspot keeps the same id.

### 16.5 Decisions and learning from outcomes (`history.py`)

Planners accept, reject or modify a recommendation with a rationale (`POST
/api/recommendations/{id}/decision`). Measured results of validation runs are also recorded. Future
priorities are re-weighted **only by measured outcomes**: per rule, the mean relative change of the
primary metric (negative = improvement), shrunk towards zero by `n / (n + 2)`, can move a priority by at
most ±50%. **Accepting a proposal changes nothing by itself**: acceptance is not proof that it worked.

### 16.6 Validation in CARLA

`simulation/planning/validate_recommendation.py` runs a baseline and a modified CARLA scenario
(synchronous mode, seeded traffic, same spawns per seed) and scores both with the same engine;
`sim_metrics.py` measures speeds, speeding vehicles, near-conflicts (time-to-collision < 1.5 s), travel
time and queues. Full before/after validation of all recommendations has not been completed.

---

## 17. What-if and traffic plans

These are in the code; the traffic scenario builder section is currently hidden in the UI.

- **What-if** (`ml/planning/whatif.py`, `POST /api/scenarios`): a planner proposes changes (lane limits,
  restricted lanes, zones, a countermeasure over an area). Two answers, kept apart:
  - **Replay (measured):** the engine re-run on the session's recorded traffic with the change, against
    a baseline on the same rows: "under this rule, these events". Drivers do not react to the change.
  - **Projection (estimate):** the events the countermeasure targets × its sourced factor.
- **Traffic plans** (`ml/planning/plans.py`, `/api/plans/*`): a plan = props, rules and scenarios placed
  on the map; compiled to engine changes; analysed as observed / replay / risks / pending.
- **Virtual traffic** (`ml/planning/microsim.py`): a small microscopic simulator on the lane map using
  the **Intelligent Driver Model** (Treiber et al. 2000) for car following; no lane changes or junction
  conflicts. Several seeds give a mean and 95% interval per metric. Labelled "simplified model, not
  CARLA" wherever shown.

---

## 18. How the parts are evaluated

| Part | Tool | Metric | Truth |
|---|---|---|---|
| Detector | `ml/detection/detection_report.py` | Precision, recall, mAP@0.5, mAP@0.5:0.95, per-class AP | Labelled test images |
| Tracker | `ml/violation_engine/eval_tracking.py`, `run_experiment.py` | IDF1, MOTA, ID switches (py-motmetrics); HOTA, DetA, AssA, LocA (TrackEval) | CARLA clip labelled in CVAT (5,259 boxes, 40 vehicles) |
| Ground projection | `ground_coords.py --check` | Position error (m) | CARLA true vehicle centres |
| Kinematics | `kinematics.py --check` | Speed and heading error | CARLA true motion |
| Zone membership | `eval_zone_membership.py` | Share of samples in the right zone | True positions |
| Violations | `eval_violations.py` | Per-type precision, recall, F1. A detected event matches a true one if same type, within 3 m and within ±1 s | Staged scenario log; "oracle" = the engine on true positions |

**Metric definitions**
- **mAP@0.5:** mean over classes of the area under the precision–recall curve, a detection counting as
  correct at IoU ≥ 0.5. **mAP@0.5:0.95** averages that over IoU thresholds 0.5 to 0.95.
- **MOTA:** 1 − (misses + false positives + ID switches) ÷ ground-truth boxes.
- **IDF1:** F1 score of correctly identified detections: how well IDs are kept over time.
- **HOTA:** geometric mean of detection accuracy (DetA) and association accuracy (AssA), averaged over
  localisation thresholds.
- **ID switch:** a ground-truth vehicle's matched track ID changes.
- **Oracle:** the rules run on true positions; it separates rule errors from perception errors.

---

## 19. Glossary

| Term | Meaning |
|---|---|
| **AirSim** | Microsoft's drone/vehicle simulator plugin for Unreal Engine; the drone side of CarlaAir |
| **Bounding box** | Rectangle around an object in an image: centre (cx, cy), width, height |
| **CARLA** | Open-source driving simulator: towns, traffic, lights, OpenDRIVE maps |
| **CarlaAir** | Prebuilt CARLA + AirSim in one Unreal Engine process |
| **Checking** | A rule is timing a vehicle (condition true, minimum time not reached yet); shown amber |
| **Condition id** | Finer label of an event (A1–F5), from its type and tags |
| **Confidence (detector)** | The detector's score for a box, 0–1 |
| **Confidence (event)** | 0.4 margin + 0.3 track quality + 0.3 duration |
| **CVAT** | Computer Vision Annotation Tool, used to label the tracking ground truth |
| **DBSCAN** | Density-based clustering; clusters = dense areas, sparse points = noise |
| **Dwell time** | How long a vehicle has stayed stopped at a spot |
| **ECEF** | Earth-Centred Earth-Fixed coordinates, used by Cesium |
| **ENU** | Local East-North-Up metric frame at an anchor point |
| **Episode** | One continuous occurrence of a rule's condition; one episode = one event |
| **Evidence clip** | Short video around the flag time with the vehicle marked |
| **Fixed-lag smoother** | A smoother that waits a fixed time (1 s) for later frames before outputting |
| **Flag time** | When an event is confirmed (condition held for the minimum time) |
| **Frenet (s, d)** | Position along a lane (s) and sideways from its centre (d) |
| **GMC** | Global motion compensation: cancelling the camera's own motion |
| **Grace period** | Allowed stop time before a stop is a violation (30 s no-parking, 10 s zebra, 20 s highway) |
| **Ground truth (GT)** | Correct answers used to measure accuracy |
| **IDM** | Intelligent Driver Model, a car-following model |
| **IoU** | Intersection over Union of two boxes |
| **Kalman filter** | Recursive estimator combining a motion model and noisy measurements |
| **Lane map / scene** | JSON of a town's lanes, zones, junctions and stop lines |
| **Latest-frame-wins** | Drop older frames so a slow consumer never falls behind |
| **Map matching** | Finding the lane a position belongs to |
| **MJPEG** | A stream of JPEG images over one HTTP response; shown by an `<img>` tag |
| **Nadir camera** | Camera looking straight down |
| **NMS** | Non-maximum suppression: removing overlapping duplicate boxes |
| **OpenDRIVE** | Standard file format for road networks (`.xodr`) |
| **Oracle run** | The rules on true positions, to separate rule errors from perception errors |
| **Place memory** | Dwell timers stored per spot (1.5 m), surviving ID switches |
| **PostGIS** | PostgreSQL extension for geometry (points, polygons, spatial queries) |
| **Profile** | Versioned configuration: thresholds, enabled rules, lane overrides |
| **Pub/sub** | Publish/subscribe messaging: senders publish to a channel, every subscriber receives it |
| **Queue context** | The stop is explained by a stopped vehicle just ahead |
| **RTS smoother** | Rauch–Tung–Striebel: backward pass over a Kalman filter's output |
| **Service token** | Fixed token the live pipeline uses to post to the backend |
| **Session** | One processed flight or live run; the unit events are grouped by |
| **Simulator time** | Time inside CARLA; the time base for every speed and duration |
| **Slerp** | Spherical linear interpolation of rotations |
| **SRID 0** | PostGIS spatial reference "none": plain local metres, not latitude/longitude |
| **Stay-point** | Stopped = all positions over the last 3 s within a 1 m circle |
| **Stitching** | Re-linking a track that was split into several IDs |
| **TAI** | Track-aware initialisation (TrackTrack): no new track on a box overlapping an existing one |
| **Track / trajectory** | One vehicle's positions over time under one ID |
| **TTC** | Time to collision: gap ÷ closing speed |
| **VP8 / WebM** | Open video codec / container that browsers play |
| **WebSocket** | Persistent two-way connection the server uses to push live updates |
| **Zone** | Polygon with a rule type (no-parking, crosswalk, highway, speed, no-U-turn) |

---

## 20. File map

```
simulation/
  carla_scripts/record_flight.py        record frames + pose + sim time + truth
  carla_scripts/stream_flight.py        live: camera -> TCP -> detector
  carla_scripts/export_lane_map.py      OpenDRIVE -> lane map JSON
  violation_scenarios/                  staged violations + scenario logs
  planning/validate_recommendation.py   CARLA before/after runs
ml/detection/                           training, dataset building, detection report
ml/violation_engine/
  process_recorded_flight.py            detection + tracking of a flight (+ dedupe)
  tracktrack_ours.yaml                  tracker configuration
  stitch_tracklets.py, postprocess_tracks.py   offline track repair
  ground_coords.py                      pixels -> metres
  kinematics.py                         Kalman + RTS
  lane_map.py, road_features.py         scene knowledge, matching
  predicates.py, rules.py, events.py    the violation engine
  run_violations.py                     offline runner
  render_violations.py                  overlay video + evidence clips
  online.py                             live versions (fixed lag, re-linking, incremental engine)
  eval_*.py                             evaluation tools
  configs/scenes/, configs/profiles/    lane maps, profiles
services/live/
  transport.py                          TCP frame protocol
  pipeline.py                           live detector service
  backend_client.py                     posting to the backend (buffering, backoff)
  evidence.py, zone_watch.py, people.py snapshots/clips, live zones, pedestrians
ml/planning/
  analytics.py                          DBSCAN hotspots, trends, section stats
  recommend.py, catalogue.py, history.py    recommendations
  whatif.py, plans.py, microsim.py      what-if, plans, virtual traffic
backend/app/                            FastAPI app (routers/, live.py, ingest.py, exports.py, storage.py, auth.py)
backend/API.md                          API contract
frontend/src/pages/                     dashboard pages
frontend/src/twin/                      CesiumJS digital twin
schemas/                                event, scene, profile schemas; conditions.json
docker-compose.yml                      PostGIS, Redis, SeaweedFS
```
