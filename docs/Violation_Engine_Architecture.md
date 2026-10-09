# Violation Engine — Architecture and Plan (Objective 1, Stage 4)

**Version 2.3 — 2026-10-09.** Owner: Hussain (Stage 4). Prepared with Afif.
**Status:**
- **Phase A:** steps A1–A5 and A9 are done, including per-event evidence clips. A6–A8 (staged CARLA flight, F1 per type) wait for a CarlaAir session.
- **Phase B:** B1, B2 and B6 are built and tested. The B3 (highD/inD/rounD) and B5 (UIT-ADrone) tools are built and tested on synthetic data in the datasets' formats; running them needs the data.
- **Phase C:** red-light (V4) and learned flow direction are built. V4 still needs a live CarlaAir run.

Section 13 records what implementation changed or taught us (v2.2 and v2.3 additions at its end).

**What changed from v1.0:** scope grows from 3 to **7 violation types** (plus 1 optional), and the design covers real drone footage as well as CARLA (v2.0); v2.1 adds the implementation notes.

All examples marked *(illustrative)* are made up to explain an idea. All numbers marked *(measured)* come from our own runs.

---

## 1. The problem in one paragraph

The pipeline already gives us, for every frame, a box and a stable ID for every vehicle (`trajectories_final.csv`; TrackTrack + stitching, IDF1 0.881 on the GT clip). The violation engine turns those moving boxes into decisions such as:

- "car 17 stayed 41 s in the no-parking zone"
- "truck 52 drove 60 m the wrong way"
- "car 8 made a U-turn where it is not allowed"

To make those decisions we need four things the boxes don't give us:

1. **Real distances.** The data is in pixels, and the drone moves.
2. **Clean speed and heading.** Box centres jitter.
3. **Road knowledge.** Lane directions, line types, crossings, limits.
4. **Rules that look at time.** "For more than 30 s", not one frame.

---

## 2. Scope: which violations, and why

The PRD lists 10 violation types (Section 9.1). We take **7**: the ones that can be decided from **where a vehicle is and how it moves**. Those are exactly what a top-down drone sees well.

| PRD # | Violation | In scope? | Why |
|---|---|---|---|
| 1 | No-parking zone | **Yes** | Zone + stopped + time. Drones see parked cars very clearly. |
| 2 | Wrong-way driving | **Yes** | Movement direction vs lane direction. |
| 3 | Illegal U-turn | **Yes** | Heading change inside a zone; same building blocks. |
| 5 | Speeding | **Yes** | Metric speed + lane speed limit. |
| 6 | Lane violation | **Yes** | Position across a lane line + line type. |
| 7 | Zebra-crossing violation | **Yes** | Same as no-parking with a crossing polygon and 10 s. |
| 10 | Illegal stopping on highway/flyover | **Yes** | Same as no-parking with a highway zone and 20 s. |
| 4 | Red-light jumping | **Optional (CARLA only)** | Needs the signal state. CARLA gives it. From the air the lights face the drivers, not the drone, so real footage can't see them reliably. |
| 8 | Helmet-less riding | No | Needs a helmet classifier on heads only a few pixels wide; we also dropped the motorcycle class (Stage 3). |
| 9 | Two-wheeler overloading | No | Same reason: counting riders on a motorbike from 50–100 m up. |

Four of the seven (1, 7, 10 and partly 3) share one building block, "a vehicle is stopped / turning inside a zone". So the work is closer to 4 rule families than 7 separate rules.

---

## 3. What the research says

Sources: IEEE papers first, then CVF/ICCV, arXiv, datasets, CARLA docs, official/government reports and practitioner forums (GitHub issues, OpenCV and DJI forums). Full list in Section 14.

> **Note on sources.** Reddit blocks automated access entirely (both search and page fetch were refused), and Google Scholar shows a CAPTCHA. Instead, papers were found through IEEE Xplore search results and the OpenAlex index (same papers, with DOIs), and practitioner experience came from GitHub issues and the OpenCV and DJI forums. Reddit threads can be added later by hand if someone has links.

### 3.1 Geometry and accuracy (both CARLA and real)

| # | Finding | Source | What we do |
|---|---|---|---|
| R1 | Good drone pipelines reach **~20 cm position and ~1° heading** error using a Kalman filter on **metric** positions. | Sanchez Morales et al., IEEE IV 2020 [1]; Kruber et al., IEEE IV 2020 [2] | All rules in metres; Kalman filter for speed/heading. |
| R2 | Main error sources: pixel→ground mapping, **relief displacement** (up to 63 cm at 50 m altitude), time sync (20 ms → 0.25 m). | [1] | Project at box-centre height; log exact time per frame. |
| R3 | Real-footage georeferencing that works: stabilise to one master frame, then register it to an **orthophoto**, then to GPS (Geo-trax: ~700,000 trajectories, 20 intersections). | Fonod et al. (Geo-trax) [5] | Our `scene_map.py` already does step 1. Add an orthophoto / satellite-image registration step for real footage. |
| R4 | Homography breaks with **too few feature matches** (below ~30 inliers), on **sloped roads**, and when the box's reference point jumps (occlusion, shadows). | [5]; Roboflow speed-estimation guide and GitHub issues [20]; Llorca et al. survey (IET ITS 2021) [21] | Keep our inlier check; refuse to compute speed on frames with failed registration; treat shadows/occlusion as low-quality frames. |
| R5 | **DJI drones save telemetry** next to the video (`.SRT` file): GPS, `rel_alt` (height above take-off), and on newer models gimbal angles and focal length. | DJI SRT format docs [22] | For real DJI footage: automatic metres-per-pixel from altitude + focal length, with no manual calibration. Caveat: `rel_alt` is height above the **take-off point**, not above the road. |
| R6 | OpenCV's `CAP_PROP_FPS` assumes a **constant frame rate**. Phone/screen recordings and some exports are variable-frame-rate, so frame index ÷ fps gives wrong times. | OpenCV forum + Issue #23403 [23] | For real video, read each frame's own timestamp (PTS via ffprobe), never frame/fps. |

### 3.2 Turning positions into clean motion

| # | Finding | Source | What we do |
|---|---|---|---|
| R7 | Smoothing the **whole track forward and backward** beats frame differences: RTS smoother [1], Gaussian smoothing [5], wavelet denoising (IEEE T-ITS) [3]. | [1], [3], [5] | Kalman + RTS smoother (offline); Kalman only (live, later). |
| R8 | Map each position onto the **road's own coordinates** (Frenet: *s* = distance along road, *d* = sideways offset). Curves become straight lines. | Chen et al., IEEE T-ITS 2021 [3] | Wrong-way = *s* goes down; lane violation = *d* crosses a line; U-turn = direction flips. |
| R9 | Drone speed accuracy in the field: **±3 km/h / 2%** vs RTK-GNSS cars (150–500 m altitude); 0.97 mph mean error in a California study. | Shan et al., Sensors 2021 [14]; arXiv 2506.11239 [19] | Our target: ≤ 3 km/h error. Speeding uses a tolerance. |
| R10 | "Is it stopped?" is more robust as **"stayed inside a small circle for T seconds"** (stay-point) than "speed < X". **Data dropout hurts more than noise.** | Staypoint benchmark 2026 [13] | `stopped` = stay-point test; bridge short gaps. |

### 3.3 Road knowledge (maps)

| # | Finding | Source | What we do |
|---|---|---|---|
| R11 | Lane-level maps (Lanelet2) store per lane: centreline, direction, speed limit, **line types**, crossings. | Poggenhans et al., IEEE ITSC 2018 [6] | One small "lane map" JSON per scene with the same ideas. |
| R12 | **CARLA exposes all of it**: lane direction (waypoint yaw), `lane_type` (Driving, Shoulder, Parking…), `left/right_lane_marking.type` (Solid, Broken, SolidSolid…), `lane_change` permission, `is_junction`, speed-limit signs (type 274), `get_crosswalks()`, traffic-light state and stop lines (`get_stop_waypoints()`). | CARLA Python API [18] | An exporter writes the lane map for each town automatically, giving exact truth for every rule. |
| R13 | Real datasets ship **real maps**: highD/inD/rounD/exiD include Lanelet2 + OpenDRIVE maps and a `speedLimit` per recording. CitySim ships SUMO/**CARLA** road networks of its real sites. | Krajewski et al., IEEE ITSC 2018 [16]; Bock et al., IEEE IV 2020 [17]; levelXdata format doc [24]; CitySim [25] | Test the rules on real trajectories with real maps. Rebuild a real CitySim site in CARLA. |
| R14 | Lane markings, crosswalks, stop lines and even no-parking markings can be **segmented from aerial images** (SkyScapes: 12 marking classes incl. crosswalk, stop-line, no-parking zone). | Azimi et al., ICCV 2019 [26] | Later: semi-automatic lane maps for real footage. Phase 1: manual clicking. |
| R15 | Without a map, the normal driving direction can be **learned from the traffic** (orientation mixture per area), then used to find wrong-way drivers. | Monteiro et al., IEEE ICIP 2007 [9] | Fallback for real footage with no map. |

### 3.4 Writing the rules

| # | Finding | Source | What we do |
|---|---|---|---|
| R16 | Traffic rules become exact and testable when written as **predicates + time conditions** (temporal logic); predicates are reusable across rules. | Maierhofer et al., IEEE IV 2020 [7] and IEEE IV 2022 [8] | A shared predicate library; each violation = a few lines combining them. |
| R17 | Wrong-way systems need **temporal validation** (several frames in a row) before alarming; one sustained-direction rule: opposite for ≥ 80% of the observed time. | [9]; Rahman et al., IEEE TENSYMP 2020 [10]; Mahi et al., IEEE STI 2023 [11]; wrong-way cycling (arXiv 2405.07293) [27] | Minimum time **and** minimum distance driven the wrong way. |
| R18 | Parking detectors fail mainly through **tracking loss / ID switches**. A drone that pans away and back must **re-find the same car** (ATG-PVD uses visual SLAM re-localisation). | Akhawaji et al., IEEE AICCSA 2017 [12]; ATG-PVD, ECCV-W 2020 [28] | Dwell time stored per **place** in scene-map coordinates, so it survives ID switches and camera revisits. |
| R19 | The AI City Challenge winners for **stalled vehicles** used **background modelling** on stabilised video (a car that stops becomes part of the background), not only tracking. 2021 winner: F1 0.95. | AI City Challenge 2020/2021 (CVPR-W) [29], [30] | A second, independent "stopped vehicle" signal from a background model, used to confirm stop-type events. |
| R20 | Zebra crossings: flag vehicles **stopped on the crossing > 10 s**. Main difficulty: heavy congestion (queues cover the crossing). | UAV surveillance system (arXiv 2509.04624) [31]; zebra-crossing study [32] | `queue_context` predicate: if traffic ahead is also stopped, tag "queue" instead of violation (PRD edge case). |
| R21 | Lane-change detection from trajectories uses the **angle/position relative to the lane line**; aerial pipelines first build a lane coordinate reference. | [3]; lane-change warning study (PMC 2022) [33] | Lane violation = straddle > 3 s (PRD) or a lane change across a solid line. |
| R22 | Red-light running: **95% of violations happen in the first 1.5 s of red**; distance to the stop line at yellow onset is the key variable. | Red-light running studies [34] | If we add red-light (CARLA only): evaluate the crossing moment relative to the red onset with exact sim time. |

### 3.5 Real-world practice

| # | Finding | Source | What we do |
|---|---|---|---|
| R23 | **Spain's DGT uses drones** (39 drones; 120 m altitude) to catch traffic offences. A trained operator reviews every case and **each fine includes the video/photo evidence**. | DGT reports [35] | Every event carries an evidence clip and goes to **human review** (PRD review workflow). The engine proposes; a person confirms. |
| R24 | Legal speed checks subtract a **tolerance** (EU: 5 km/h below 100 km/h, 5% above; UK: 10% + 2 mph). | [15] | Speeding tolerance built in and configurable. |

### 3.6 Datasets we can test on

| Dataset | Real/sim | What it gives us | Limits |
|---|---|---|---|
| **Our CARLA flights + staged scenarios** | Sim | Exact truth for all 7 types (+ red-light) | Simulation look |
| **UIT-ADrone** (IEEE JSTARS 2023) [36] | **Real drone video**, 3 roundabouts in Vietnam, 6.5 h | Labelled anomalies incl. **driving in opposite direction, illegal turns, illegal parking on street** | Mostly motorbikes; we track car/bus/truck only, so only the car events count |
| **highD / inD / rounD / exiD** [16], [17], [24] | Real drone trajectories (no raw video) | Real maps (Lanelet2), speed limits → natural **speeding**, lane changes, false-alarm rates | Trajectories only; tests Layers 3–7, not the detector |
| **CitySim** [25] | Real drone trajectories + **CARLA/SUMO model of the same sites** | Rebuild a real site in CARLA and stage violations there | Trajectories, not all raw video |
| **Our 2 real clips** (stock highway, roundabout) | Real video | End-to-end check; we hand-label violation events | Small, no telemetry |

---

## 4. Architecture at a glance

The same seven layers serve CARLA and real footage. Only Layer 2 (geometry) and the source of Layer 4 (maps) differ.

```
 Layer 1  PERCEPTION (done)       frames -> detector -> TrackTrack -> stitching
                                  trajectories_final.csv (pixels)
            |
 Layer 2  GEOMETRY                pixels -> metres
            |   CARLA ............ camera pose every frame (exact)
            |   real, DJI ........ .SRT altitude + focal length + scene map
            |   real, no telemetry scene map + orthophoto/satellite match, or known-length calibration
            |   time ............. sim_time (CARLA) / frame PTS (real), never frame/fps
            |
 Layer 3  STATE ESTIMATION        Kalman + RTS smoother per track
            |                     x, y, speed, heading (+ uncertainty), visibility flag
            |
 Layer 4  SCENE KNOWLEDGE         lane map: lanes (direction, limit, line types, lane type),
            |   CARLA ............ exported from OpenDRIVE automatically
            |   real ............. clicked once / OSM tags / learned flow direction
            |                     zones: no-parking, crossings, no-U-turn, highway, stop lines
            |                     map matching -> lane, s (along), d (sideways)
            |
 Layer 5  RULE MONITORS           shared predicates + time conditions
            |                     7 violation monitors (+ red-light, CARLA only)
            |   cross-check ...... background-model "static vehicle" signal (R19)
            |
 Layer 6  EVENTS                  one event per episode, confidence, evidence clip,
            |                     status: flagged -> needs_review / confirmed
            |
 Layer 7  OUTPUTS                 violations.csv/json, overlay video, (later) backend API
```

---

## 5. Layer by layer

### 5.1 Layer 1 — Perception (done)

```
frame,time_s,track_id,class,cx,cy,w,h,conf
812,30.41,17,car,1104.5,622.0,61.0,118.0,0.91
```

### 5.2 Layer 2 — Geometry: pixels to metres

**Why.** At 67 m altitude (Town05 test flight) one pixel ≈ 7 cm on the ground. If the drone drifts 2 m, every parked car seems to move 30 pixels.

**CARLA (exact).** A ray from the camera through each box centre, intersected with a plane at box-centre height. *(Measured, `ground_coords.py --check`, 20,683 boxes on 3 flights, against CARLA's true vehicle centres: median error 0.11–0.12 m, 95% within 0.74–0.88 m.)* That's comparable to the 19 cm reported in [1]. New recordings save the camera pose and sim time on **every** frame (`record_flight.py`, changed). Old flights interpolate the pose, which is fine for parking but too noisy for speed (p95 3.1 m when the drone turns).

**Real footage — three options, best first:**

| Option | Needs | Expected quality | Example |
|---|---|---|---|
| A. **Telemetry** | DJI `.SRT` file with altitude + focal length (+ gimbal) | Good if the road is level with the take-off point | A drone at `rel_alt` 80 m, camera looking straight down with a 24 mm-equivalent lens (about 74° wide), 4K video → ground width ≈ 120 m → about 3 cm/px *(illustrative)* |
| B. **Orthophoto / satellite match** | A geo-referenced top-down image of the site (own mapping flight or public orthophoto) | Best; Geo-trax method [5] | Scene map's first frame registered to the orthophoto → every track in GPS / metres |
| C. **Known-length calibration** | Something of known size in view | OK; state it as an estimate | A lane is 3.5 m wide and spans 52 px → 6.7 cm/px *(illustrative)* |

Time on real video comes from each frame's **presentation timestamp (PTS)**, read once with `ffprobe` (R6).

### 5.3 Layer 3 — State estimation

- **Kalman filter + RTS smoother** per track (constant-acceleration model). Measurement noise comes from the measured projection error (σ ≈ 0.15 m in CARLA; estimated per clip for real footage).
- **Visibility filter:** boxes touching the frame edge, and frames where registration failed, are not used as measurements (R4, PRD "partial occlusion").
- **Output per frame:** `x, y, speed_kmh, heading_deg, speed_sigma_kmh, visible`.
- **Target:** speed error ≤ 3 km/h median (R9) and heading error ≤ 5° above 10 km/h, measured against CARLA truth.

### 5.4 Layer 4 — Scene knowledge

**Lane map + zones, one JSON per scene:**

```json
{
  "scene": "Town05_junction_A",
  "coords": "carla_world_m",
  "lanes": [
    {"id": "r12_l-1", "centreline": [[-120.0, 1.8], [-80.0, 1.8], [-40.0, 1.9]],
     "width_m": 3.5, "lane_type": "driving", "speed_limit_kmh": 30, "junction": false,
     "left_line": "solid", "right_line": "broken"}
  ],
  "zones": [
    {"id": "np_bank",   "type": "no_parking", "polygon": [[-62,5],[-48,5],[-48,8],[-62,8]], "grace_s": 30},
    {"id": "zebra_3",   "type": "crosswalk",  "polygon": [[-30,-2],[-26,-2],[-26,9],[-30,9]]},
    {"id": "nou_1",     "type": "no_u_turn",  "polygon": [[10,-5],[30,-5],[30,12],[10,12]]},
    {"id": "hw_bridge", "type": "highway",    "polygon": [[200,-8],[420,-8],[420,8],[200,8]], "grace_s": 20}
  ],
  "stop_lines": [{"id": "tl_7", "line": [[-31,-2],[-31,9]], "signal_id": 7}]
}
```

**Where it comes from:**
- **CARLA:** `export_lane_map.py` reads the OpenDRIVE network with one waypoint per metre. That gives the direction, lane type, line types and junction flag (R12). Speed limits come from the type-274 signs, crossings from `get_crosswalks()`, and stop lines from the traffic lights. It runs once per town with no manual work, and the same data is the truth for evaluation.
- **Real footage:** a small click tool on the first (stabilised) frame draws lanes with their direction, crossings, and no-parking/no-U-turn areas. Where the footage is geo-referenced, OpenStreetMap tags (`oneway`, `maxspeed`) can pre-fill directions and limits. If no direction is known, it's learned from normal traffic (R15).

**Map matching rule:** a vehicle is assigned to a lane by **position only, never by heading**. Otherwise a wrong-way car would be matched to the opposite lane, which it agrees with, and never flagged.

### 5.5 Layer 5 — Shared predicates

Every violation is built from these. Thresholds default to the PRD values, are stored in seconds (PRD frames ÷ 30 fps), and can be overridden per zone or lane.

| Predicate | True when | Default |
|---|---|---|
| `in_zone(Z)` | Smoothed position inside polygon Z | — |
| `stopped` | All positions of the last 3 s lie within a 1 m circle (stay-point, R10) | 3 s, 1 m |
| `slow(v)` | Speed < v km/h | PRD: 2 (parking/zebra), 5 (highway) |
| `on_lane(L)` | Matched to lane L, within its width, L not a junction lane | — |
| `against_lane` | Velocity vs lane direction > 150° and speed > 5 km/h | PRD 150° |
| `over_limit` | (speed − 2σ) > limit + tolerance | 5 km/h < 100, 5% above (R24) |
| `straddling(line)` | Vehicle footprint overlaps a lane line | footprint from box + heading |
| `changed_lane_across(type)` | Lane id changed and the line crossed is of `type` (e.g. solid) | — |
| `heading_change(zone)` | Total heading change since entering the zone | — |
| `queue_context` | The vehicle ahead in the same lane (within 10 m) is also stopped, or all lanes at that point are stopped | — |
| `static_confirmed` | Background model also sees a static vehicle at that spot (R19) | — |

### 5.6 Layer 5 — The violation monitors

Each entry gives: **rule**, **edge cases**, **example**, **truth in CARLA**, **real-footage notes**.

#### V1 — No-parking (PRD 1)

- **Rule:** `in_zone(no_parking) AND stopped` for ≥ 30 s (PRD grace, configurable per zone). Gaps ≤ 2 s don't reset the timer. Place memory: if a stopped track ends and a new one appears within 1.5 m within 10 s, it keeps the timer (R18).
- **Edge cases:**
  - Delivery stop shorter than the grace period: no event.
  - Car in a marked parking lane (`lane_type = Parking`): never in a no-parking zone.
- **Example** *(illustrative):* A taxi stops in front of the bank at 0 s. At 16 s a bus hides it and the tracker switches ID 40 → 77; place memory keeps the timer. An event opens at 30 s and closes when the taxi leaves at 47 s.
- **Truth (CARLA):** scripted car held 45 s (PRD 17.3); true poses.
- **Real footage:** the drone may pan away and come back. The place memory lives in scene-map coordinates, so the timer continues when the same spot is seen again, if the car is still there (R18).

#### V2 — Wrong-way (PRD 2)

- **Rule:** `on_lane(L) AND against_lane` for ≥ 0.17 s (PRD 5 frames) **and** ≥ 5 m driven backwards along *s*.
- **Edge cases:**
  - Reversing 2 m into a parking space: too short, no event (PRD edge case 1).
  - Not checked on junction lanes. Decided on the exit lane (PRD edge case 2).
- **Example** *(illustrative):* A car leaves a side street, enters a one-way road the wrong way, and its *s* drops 42 → 35 m in 0.9 s at 28 km/h, at 176° to the lane. A wrong-way event opens.
- **Truth (CARLA):** scripted car along reversed waypoints (the autopilot refuses to drive wrong-way); ScenarioRunner's `WrongLaneTest` as a second check [18].
- **Real footage:** lane direction from the clicked map, OSM `oneway`, or learned flow. UIT-ADrone has real "driving in opposite direction" events for testing [36].

#### V3 — Illegal U-turn (PRD 3)

- **Rule:** the vehicle enters a `no_u_turn` zone, its heading changes > 160° (PRD) **inside** the zone, and it leaves going the opposite way.
- **Edge cases:**
  - A legal U-turn zone next to it: only the arc inside the restricted polygon counts (PRD edge case 1).
  - A **three-point turn**: it contains reversing segments (speed along the heading goes negative), so it's filtered by an "arc smoothness" check: no reversing and at most one direction of turning (PRD edge case 2).
- **Example** *(illustrative):* At a median opening marked no-U-turn, car 23 enters heading east (90°), sweeps left in 6 s, and leaves heading west (268°): change 178°, one smooth arc, so an event opens. A car that does the same with two reversing moves is tagged "three-point turn" and not flagged.
- **Truth (CARLA):** scripted 180° arc waypoints inside the zone (PRD 17.3).
- **Real footage:** "illegal left/right turn" events in UIT-ADrone [36] are the closest real test set.

#### V5 — Speeding (PRD 5)

- **Rule:** `on_lane(L) AND over_limit` (limit from lane L) for ≥ 0.33 s (PRD 10 frames). Only frames with a full box count.
- **Edge cases:** the PRD's "GPS drift / spike" case is handled by the smoother; partial occlusion by the visibility filter.
- **Example** *(illustrative):* In a 50 km/h lane, a car at 58.0 ± 1.0 km/h has a lower bound of 56.0 > 55 (limit + tolerance), so it's flagged. A car at 54 km/h is not proven over, so no event.
- **Truth (CARLA):** scripted cars at 1.1× and 1.5× the limit (PRD SIM-V05: 10% and 50% over); true speeds from poses + sim time.
- **Real footage:** only as good as the scale (Section 5.2). Events from options C/A are marked "estimated speed". highD's real trajectories with `speedLimit` give natural speeding cases for testing Layers 3–7 [16].

#### V6 — Lane violation (PRD 6)

- **Rule** (two sub-checks):
  1. **Straddling:** `straddling(any line)` for > 3 s (PRD), i.e. driving on the line.
  2. **Crossing a solid line:** `changed_lane_across(solid or double solid)`, flagged when the change completes. The PRD's "different thresholds per line type" are handled this way: broken lines allow brief crossing, solid lines don't.
- **Edge cases:** a normal lane change across a broken line takes 2–4 s and is not flagged. Straddling during a lane change is allowed up to the 3 s limit.
- **Example** *(illustrative):* A car drifts onto the dashed line and stays there for 5 s while looking for its exit, so a straddling event opens. Another car moves left across a double solid line before the junction, so a crossing event opens when it settles in the new lane.
- **Truth (CARLA):** `left/right_lane_marking.type` and `lane_change` from OpenDRIVE (R12); scripted path across the line (PRD 17.3).
- **Real footage:** needs the line types in the map (clicked; later SkyScapes-style segmentation, R14). The hardest violation for real footage: position error (0.1–0.8 m) is a real fraction of a 3.5 m lane. **Planned check:** measure the false-alarm rate on highD's real lane changes first.

#### V7 — Zebra-crossing violation (PRD 7)

- **Rule:** `in_zone(crosswalk) AND slow(2) AND stopped` for > 10 s **and not** `queue_context`.
- **Edge cases:**
  - Forced stop in a traffic jam: tagged "queue", not flagged (PRD edge case 1).
  - The PRD's motorcycle lane-splitting case is not relevant here (no motorcycle class).
- **Example** *(illustrative):* At a red light, a van stops with its middle on the zebra while the lane ahead is empty, and stays 14 s, so an event opens. In a jam where the car ahead is also stopped, the same situation is tagged "queue".
- **Truth (CARLA):** crosswalk polygons from `get_crosswalks()`; scripted stop on the crossing (PRD 17.3).
- **Real footage:** crossings are very visible from above (white stripes), so clicking them takes seconds. SkyScapes has a crosswalk class for automatic detection later [26].

#### V10 — Illegal stopping on highway/flyover (PRD 10)

- **Rule:** `in_zone(highway) AND slow(5)` for > 20 s (PRD) **and not** `queue_context`.
- **Edge cases:**
  - Traffic jam: no violation (PRD).
  - A vehicle stopped **alone on the shoulder** (`lane_type = Shoulder`): tagged "possible breakdown" instead of a standard violation (PRD edge case 2).
- **Example** *(illustrative):* On a flyover a car stops in the right lane, other traffic passes it, and after 20 s an event opens. Another car stops on the hard shoulder with no queue, so it's tagged "possible breakdown".
- **Truth (CARLA):** a highway town (e.g. Town04) with a scripted stop (PRD 17.3).
- **Real footage:** our stock highway clip; the zone is the carriageway polygon.

#### V4 — Red-light jumping (PRD 4) — optional, CARLA only

- **Rule:** the vehicle's front crosses the stop line while the signal is red. A vehicle already past the line at the change is not flagged (PRD edge case 1). Emergency vehicles are excluded by class/blueprint (PRD edge case 2).
- **Truth and input (CARLA):** signal state logged every tick from `TrafficLight.get_state()`, stop lines from `get_stop_waypoints()` (R12). R22 says most violations happen in the first 1.5 s of red, so exact sim time matters.
- **Real footage:** out of scope. The lights face the drivers, and inferring the phase from queue behaviour is a research project of its own.

### 5.7 Layer 6 — Events

One event per **episode**: it opens when the time condition is met and closes when the predicates have been false for > 2 s.

```json
{"event_id": "Town05A-0007", "type": "speeding", "track_id": 8, "class": "car",
 "lane_id": "r12_l-1", "zone_id": null,
 "start_s": 41.20, "flag_s": 41.53, "end_s": 44.90,
 "value": {"max_speed_kmh": 71.4, "limit_kmh": 50, "speed_sigma_kmh": 1.1, "speed_source": "carla_pose"},
 "location": {"x": -63.2, "y": 1.9},
 "evidence": {"frame": 1112, "clip": "events/Town05A-0007.mp4"},
 "tags": [], "confidence": 0.93, "status": "flagged"}
```

- The fields match the PRD's `ViolationEvent`, so the backend (Objective 9) can store events as they are.
- **Confidence:** margin past the threshold (in σ) × track quality (detector confidence, share of visible frames) × duration margin × `static_confirmed` bonus for stop-type rules. It is calibrated on CARLA. Events below the PRD minimum (e.g. 0.90 for no-parking) are marked `needs_review`.
- **Human review** (R23): every event has a 10 s evidence clip. Like the DGT system, the engine **proposes** and a person **confirms**.
- **Tags** such as `queue`, `possible_breakdown`, `three_point_turn` and `estimated_speed` keep the borderline cases visible instead of silently dropping them.

### 5.8 Layer 7 — Outputs

- `kinematics.csv` — per vehicle per frame: x, y, speed, heading, lane, s, d, visible
- `violations.csv` / `violations.json` — events
- `events/*.mp4` — evidence clips
- `annotated_violations.mp4` — zones, lanes and speeds drawn, with red boxes while an event is open (closes the Stage 5 overlay item)

---

## 6. Evaluation

| Level | Input | Truth | Question | Target |
|---|---|---|---|---|
| **L0 unit tests** | Synthetic tracks per rule: e.g. "stop 35 s / 25 s", "±0.3 m jitter", "ID switch at 16 s", "reverse 2 m", "three-point turn", "4 s straddle", "queue on zebra" | By construction | Is the rule logic right? | 100% pass |
| **L1 oracle (CARLA)** | True vehicle positions + exported lane map | Staged-scenario log + rules on truth | Do the rules catch what we staged? | F1 ≥ 0.95 per type |
| **L1 oracle (real)** | highD/inD/rounD trajectories + their Lanelet2 maps | Speed limit + maps | False alarms per hour on real traffic; natural speeding / lane changes | Low false alarms; reported |
| **L2 pipeline (CARLA)** | Our detector → tracker → engine | Same as L1 | End-to-end quality | **F1 ≥ 0.70 per type (PRD)** |
| **L2 pipeline (real)** | Our 2 real clips + UIT-ADrone car events | Hand-labelled events | Does it work on real footage? | Reported per type; no PRD target yet |

**Matching:** a detected event is correct if it has the same type, belongs to the same vehicle (within 2 m), and overlaps the true event in time (± 1 s).
**Also reported:** speed error (km/h), flag delay (s), false events per 10 min.

**Staged CARLA scenarios** (`simulation/violation_scenarios/stage_violations.py`), with the drone hovering:

| Scenario | Staging | Repeats/flight |
|---|---|---|
| No-parking (+ short-stop negative) | Hold 45 s / stop 15 s and leave | 3 + 3 |
| Wrong-way | Scripted reversed path | 3 |
| U-turn (+ three-point-turn negative) | Scripted arc in no-U-turn zone | 3 + 2 |
| Speeding | 1.1× and 1.5× the limit | 4 |
| Lane violation | Straddle 5 s; cross a solid line | 2 + 2 |
| Zebra (+ queue negative) | Stop 15 s on crossing; queue across it | 3 + 2 |
| Highway stop (+ breakdown, jam negatives) | Highway town, stop 30 s; shoulder stop; jam | 2 + 1 + 1 |
| Red-light (optional) | Cross on red, 0.5–3 s after onset | 4 |
| Normal traffic | `traffic_flow.py` | always |

The script writes the scenario log (vehicle, type, start and end), which makes the truth exact. Weather/night variants follow PRD SIM-V01 to V05.

**Hand-labelled real events:** in CVAT, mark the start/end time + track for every violation in the two real clips (same tool we used for GT boxes).

---

## 7. Files

```
ml/violation_engine/
  ground_coords.py          L2  CARLA pose projection (draft, tested)
  real_geometry.py          L2  DJI .SRT / orthophoto / known-length calibration; PTS times
  kinematics.py             L3  Kalman + RTS, visibility filter
  lane_map.py               L4  load maps/zones, map matching, Frenet s/d
  predicates.py             L5  shared predicates
  rules.py                  L5  7 monitors (+ red_light)
  static_vehicles.py        L5  static-vehicle cross-check (B6)
  signals.py                L5  traffic-signal states + stop lines for V4
  flow_map.py               L4  learned flow direction (Phase C)
  events.py                 L6  episodes, confidence, evidence field
  run_violations.py         runner (L2-L7)
  eval_violations.py        L1/L2 evaluation
  levelx.py                 L1 real: highD / inD / rounD (B3)
  eval_uit_adrone.py        L2 real: UIT-ADrone frame-level scoring (B5)
  render_violations.py      overlay video; --clips: evidence clips per event
  zone_tool.py              click tool for real-footage lanes/zones
  tests/                    L0 unit tests
  configs/scenes/*.json     lane maps per scene
simulation/carla_scripts/
  export_lane_map.py        OpenDRIVE -> lane map JSON (+ crosswalks)
  record_flight.py          logs sim time + camera pose every frame, traffic-light states + stop lines
simulation/violation_scenarios/
  stage_violations.py       stages all scenarios + writes the scenario log
```

---

## 8. Work plan

**Phase A — core and CARLA (Phase 1 deliverable)**

| Step | Work | Done when | CARLA? |
|---|---|---|---|
| A1 | Geometry + per-frame pose/time logging | median ≤ 0.2 m, p95 ≤ 1 m — **met (0.1 / 0.8 m)** | No |
| A2 | Kalman + RTS | speed ≤ 3 km/h, heading ≤ 5° vs truth | No |
| A3 | Lane map export (lanes, lines, crossings, limits, stop lines) + matching | ≥ 95% of true positions on the right lane | **Yes** |
| A4 | Predicates + V1, V2, V5 + L0 tests | tests pass | No |
| A5 | V3, V6, V7, V10 + L0 tests | tests pass | No |
| A6 | Staged scenarios (all types) | one flight per scenario group, with logs | **Yes** |
| A7 | L1 oracle (CARLA) | F1 ≥ 0.95 per type | No |
| A8 | L2 pipeline (CARLA) | **F1 ≥ 0.70 per type (PRD)** | No |
| A9 | Events, evidence clips, overlay video, tracker/presentation update | demo shows flags — **done (clips 2026-10-08)** | No |

**Phase B — real footage**

| Step | Work | Done when |
|---|---|---|
| B1 | PTS time base + real geometry options A/B/C | scale error checked on a known length (e.g. lane width) |
| B2 | Zone/lane click tool | lane map for both real clips |
| B3 | L1 oracle on highD/inD (request access) | false-alarm rates reported — **tool built (`levelx.py`); needs the data** |
| B4 | Hand-label events in the real clips; L2 real | per-type results reported |
| B5 | UIT-ADrone car events | per-type results reported — **scorer built (`eval_uit_adrone.py`); labels are frame-level, so results are frame-level** |
| B6 | Background-model cross-check | measured effect on stop-type precision — **built; measured on CARLA truth (Section 13)** |

**Phase C — optional:** red-light (CARLA) — **built, live run pending**; learned flow direction — **built**; SkyScapes-style automatic marking detection — not started.

A4/A5 and A2 can run in parallel; A3 and A6 can share one CarlaAir session.

---

## 9. Risks

| Risk | Effect | Handling |
|---|---|---|
| Old flights store wall-clock time | Wrong km/h | New flights log `sim_time`; check old ones against true speeds |
| Variable frame rate in real video | Wrong km/h | Use PTS per frame (R6) |
| No telemetry on real clips | Speeds are estimates | Options A/B/C; tag `estimated_speed`; say so in the presentation |
| `rel_alt` is above the take-off point | Wrong scale if the road is higher/lower | Prefer option B; cross-check with a known length |
| ID switch during a long stop | Missed parking/zebra/highway events | Place memory + background cross-check |
| Drone pans away and back (real) | Dwell timer lost | Place memory in scene-map coordinates (R18) |
| Lane-level precision for V6 | False lane violations | Footprint + 3 s rule; test false alarms on highD first |
| Queues on crossings / highways | False zebra/highway events | `queue_context` → tag, not flag |
| Junctions | False wrong-way | No wrong-way check on junction lanes |
| Feature-poor real frames (water, sand, blur) | Registration fails → bad positions | Skip those frames for speed; keep for stop-type rules only if the drone hovered |
| Few natural violations | Can't measure F1 | Staged CARLA scenarios + UIT-ADrone |

---

## 10. Decisions taken

1. **7 violation types:** 1, 2, 3, 5, 6, 7, 10. Red-light is optional (CARLA only). Helmet and overloading are out of scope.
2. CARLA first (Phase A), then real footage (Phase B), on the same engine.
3. Rules work in metres and seconds only. Geometry is the only part that differs between CARLA and real footage.
4. CARLA lane maps come from OpenDRIVE automatically. Real maps are clicked once, pre-filled from OSM when possible.
5. PRD thresholds are the defaults, stored in seconds and overridable.
6. Every event goes to human review with an evidence clip.

## 11. Open questions for the team

1. Speeding tolerance: EU style (5 km/h / 5%) or none?
2. Do our real clips come with DJI `.SRT` files? If the source drone is ours, record with SRT captions switched on.
3. Request highD/inD/rounD access (free for non-commercial research) and download UIT-ADrone?
4. Is a vehicle blocking a crossing in a queue a violation for us, or only a tag (PRD says tag)?
5. Who hand-labels violation events in the real clips (B4)?

---

## 12. Glossary (simple words)

- **Frenet coordinates:** position described as "how far along the road" (*s*) and "how far left/right of the lane centre" (*d*).
- **RTS smoother:** a Kalman filter run forwards, then corrected backwards using later frames, which gives smoother speed.
- **Stay-point:** a vehicle counts as stopped if all its positions over a few seconds fit in a small circle.
- **Orthophoto:** a top-down photo corrected so that distances on it are true to scale.
- **PTS:** the timestamp stored for each video frame.
- **Episode:** one continuous violation, from its start to its end. One episode = one event.

---

## 13. Implementation notes (v2.1, 2026-10-04)

**What exists:**
- **Code (`ml/violation_engine/`):** `ground_coords.py`, `real_geometry.py`, `kinematics.py`, `lane_map.py`, `predicates.py`, `rules.py`, `events.py`, `run_violations.py`, `eval_violations.py`, `render_violations.py`, `zone_tool.py`.
- **Tests:** `tests/` (28 unit tests).
- **Simulator scripts:** `simulation/carla_scripts/export_lane_map.py` and `simulation/violation_scenarios/stage_violations.py`.
- **Not built yet:** the background-model cross-check (`static_vehicles.py`, B6) and evidence clips per event.

**Lessons found by checking against ground truth.** Each of these was measured, fixed and logged in the tracker:

| # | Problem | Fix |
|---|---|---|
| 1 | The Kalman gate rejected good data once the filter drifted, so the track ran away (speeds up to 243 km/h) | Accept after 3 rejections in a row; no gate in a track's first 0.5 s |
| 2 | CARLA's world axes are **left-handed**, so "left of the lane" came out mirrored (9 false solid-line crossings) | `left_handed` flag in the scene; real sites derive it from the calibration |
| 3 | Post-processing's gap-filled rows (straight lines in pixels while the camera moves), interpolated camera poses on old flights, and boxes cut by the frame edge were used as measurements | They are not measurements; the filter predicts through them |
| 4 | ID switches between two cars looked like 68–170 km/h speeding or wrong-way | Cut a track where consecutive measurements, at least 0.2 s apart, need > 150 km/h or > 15 m/s² |
| 5 | One parked car with two track IDs at once, or IDs switching, split the dwell timer | Stop-type timers belong to the **spot** (1.5 m), not the track |
| 6 | Rows extrapolated before a track's first or after its last real measurement gave nonsense speeds | Only rows with a real measurement within 0.4 s on both sides count for the moving-vehicle rules (`visible`) |
| 7 | CARLA speed signs say "mph" but mean km/h, and their orientation labels don't match the lanes they serve | Values used as km/h; a sign's side taken from its position |

**Result on 3.5 min of normal Town05 traffic (full pipeline):** false events went from 784 to 0. One of two natural crosswalk stops was caught; the other car was tracked in only 40% of its frames.

**Real footage as built (Phase B1/B2):**
- **Site file:** one per clip (`configs/sites/*.json`, drawn with `zone_tool.py`). It holds the calibration (`scale`, `homography` or `srt`), `ref_point` (0.5 for a camera looking down, ~0.75 for a tilted one), lanes and zones in reference-frame pixels, and a measured `speed_sigma_kmh`.
- **Pixel to metres:** `scene_map.py` cancels camera motion, then the calibration maps to metres. Time comes from each frame's own timestamp.
- **Calibration check:** the median car length (`real_geometry.py --check`) should be ~4–5 m.
- **Speeds from real footage are estimates** and are tagged `estimated_speed`. Registration drift adds error the smoother can't see, so each site states it (`speed_sigma_kmh`) and the speeding rule must hold despite it. Example: on the highway clip a parked van reads ~5.5 km/h while the drone flies fast, so 6 km/h is added there.
- No-parking from a fast-moving drone is unreliable for the same reason. Parking enforcement needs a hovering drone, as Spain's DGT operates.

### v2.2 additions (2026-10-08)

**Evidence clips (Layer 6/7).** `render_violations.py --clips` writes one clip per event to `<violations>/events/<event_id>.mp4`: 7 s before the flag to 3 s after, the full overlay, and a banner with the event, its status, confidence and tags. It records `evidence: {frame, clip}` in `violations.json` and `.csv`. Tested on Town05 flight `20261002_001635` (1 clip, 267 frames) and on the real highway clip (3 clips).
- Bug found and fixed on the way: the overlay pinned event labels on gap-filled (`interp`) boxes. Those are straight lines in pixels while the camera moves, and on the Town05 zebra event they sat 600 px from the car. They are now drawn plain, and the spot ring marks the car.

**Static-vehicle cross-check (B6, `static_vehicles.py`).**
- **Method:** a 7 m ground patch around the event spot, rectified per frame from the camera pose (CARLA) or the scene map (real), sampled every 0.5 s. It's compared with background views of the same spot before and after the event window. The "after" side is needed because a lost track can end an event while the car is still there.
- **Choices, each made after looking at failures on the real frames:**
  - The background is the *nearest* view, not a median: the moving drone makes buildings, poles and trees lean differently from view to view.
  - Background views where the pipeline tracked a vehicle within 3 m are dropped (queues at crossings).
  - On flights before 2026-10-03, only exact-pose frames are used: interpolated poses shift road markings inside the patch.
- **Effect on events:** confirmed adds +0.1 confidence; contradicted sends a flagged event to `needs_review`. Unknown changes nothing.
- **Measured** (`--check`, Town05 flight `20261002_001635`, D_OCC 12), calibrated and tested on the same flight because it's the only one with truth:
  - True stationary vehicles (98 usable): 85% confirmed, 8% contradicted.
  - Empty driving-lane spots (125 usable): 80% contradicted, 14% falsely confirmed.
  - All 3 real stop events on that flight (1 pipeline, 2 oracle) are confirmed. The pipeline zebra event's confidence goes 0.57 → 0.67; it stays `needs_review` (PRD minimum 0.85).
  - Remaining misses: the spot always occupied (queues, parking rows), or the car hidden under a building's lean or an overpass. Both are cases where review is the right outcome.

**Red light (V4, CARLA only).**
- **Recording:** `record_flight.py` writes `traffic_lights.json` for every flight: each light's state changes per tick, and its stop lines from CARLA's `get_stop_waypoints()`, with the approach direction.
- **Rule:** `signals.py` looks states up by frame, so there is no time base to reconcile. `RedLightMonitor` flags a vehicle whose front crosses a stop line in the approach direction while the light is red. Yellow and "already past at the change" are not flagged. Confidence is lower within the first second of red, where tick timing decides.
- **Staging:** `stage_violations.py` stages it (red 1.5 s and 3 s before the line; negative: red 1.5 s after passing). In Town05 the OpenDRIVE signal sits on the junction's internal road, so the offline plan walks back to the junction entrance.
- **Results:** 6 unit tests pass; the Town05 dry run is 13/13 acts as expected, with measured red time 1.6 s and 3.1 s for the planned 1.5 s and 3.0 s. A live run still needs CarlaAir.

**highD / inD / rounD (B3, `levelx.py`).**
- **What it does:** loads the levelXdata formats. For highD, lanes come from the recording's lane-marking positions: left-handed image axes, edges solid, inner lines dashed, the recording's speed limit. For inD/rounD, lanes come from the Lanelet2 `.osm` (lat/lon → UTM − recording origin), and overlapping lanelets count as junctions. The rest of the engine runs unchanged.
- **Reports:** counted events per hour; for highD, speeding scored against the dataset's own speeds, and solid-line crossings (all should be false alarms).
- **Tested** on synthetic recordings in both formats (6 tests). Running it needs dataset access.

**UIT-ADrone (B5, `eval_uit_adrone.py`).**
- The dataset's anomaly labels are frame-level only (one `.npy` of 0/1 per test video, no type, no vehicle). It's 51 videos, 30 fps, public on Google Drive.
- **Scores:** frame AUC (frame score = max confidence of covering events), frame P/R/F1, event precision and segment recall.
- Its 10 anomaly types include pedestrian, sidewalk, goods and motorbike-fall cases we don't detect, so recall has a ceiling well below 1.
- Running it needs a site file (`zone_tool.py`) per roundabout and our pipeline on the test videos.

**Learned flow direction (Phase C, `flow_map.py`, `run_violations.py --learn-flow`).**
- **Method:** each track votes once per 4 m cell with its heading there. A cell with ≥ 5 tracks and ≥ 80% within 45° becomes a one-way lane; mixed cells become junction lanes.
- **Measured** on Town05 `20261002_001635`: the learned direction agrees within 30° with the OpenDRIVE lane direction in **38/38** one-way cells from true positions and **6/6** from the pipeline.
- **Coverage is the limit:** 3.5 min gives 80 cells (oracle) / 24 (pipeline), and the 30 s real highway clip from a moving drone gives 1. It needs longer hovering footage.
- Learned lanes raised no wrong-way false alarm on that flight.
- **Side effect:** lane-based context changes. One of the 2 oracle zebra stops became a `queue` tag. Use `--learn-flow` only where there is no lane map.

### v2.3 additions (2026-10-09, Build Plan M0 and M1)

**Event schema.** `schemas/event.schema.json` is the contract between the engine, the backend and the dashboards. Every event now carries:
- `kind`: `violation`, or `anomaly` for road-surface events (M9).
- `condition`: the Expected_Output §4.2 condition it stands for, from `schemas/conditions.json`. `Engine.run` sets it after all monitors have finished, because some tags (`possible_breakdown`) are only final when the event closes. Today: lane `straddling` → A4, `solid_line_crossing` → A2; highway stop → B1, or B2 with `possible_breakdown`; no-parking → B3; wrong-way → C1; U-turn → D1; speeding → E1, or E3 when the limit came from a speed zone (new `value.limit_source`); zebra → F1; red light → none (out of scope).

**Road features (Build Plan M1).** Lanes now carry the road properties the rules will read (Expected_Output §4.1): `road_id`, `next`, `lane_change`, `z`, `bridge`, `tunnel`, `ramp`, `road_class`, `one_way`, `median_left` / `median_gap_m`, `restricted` (`lane_map.py` docstring, `schemas/scene.schema.json`). CARLA values come from `export_lane_map.py`; derived ones from `road_features.py`, which never overwrites a value already set (site file, OpenDRIVE, profile override). Choices made on the data:
- Road class is a property of the road: a lane is highway if any non-junction lane of its OpenDRIVE road has the highway limit, since Town04's motorway loop carries 60 and 30 km/h signs.
- A ramp must reach a highway lane of another road through at least one junction lane. Without that, the 60 km/h stretch before a 90 sign on the highway road itself read as an on-ramp.
- Bridge = OpenDRIVE `<bridge>` and ≥ 2 m up, or ≥ 5 m up. Town05 tags ground-level ring stretches (z 0.0 m) as bridges.

**Profiles.** `profiles.py` loads a configuration profile (`schemas/profile.schema.json`, files in `configs/profiles/`). It produces the same params dict as `--params`, plus `enabled` per type: `default_monitors` leaves out a monitor whose type is off, and `Engine(disabled_conditions=...)` drops events of turned-off conditions. Thresholds a profile leaves out keep `rules.DEFAULTS`. Red light is off in every profile unless turned on. Re-running staged flight `20261009_201727` with `--profile town05` gives the same 30 events as without it.

## 14. References

**IEEE**

1. E. Sanchez Morales, F. Kruber, M. Botsch, B. Huber, A. García Higuera, "Accuracy Characterization of the Vehicle State Estimation from Aerial Imagery," *IEEE IV*, 2020. doi:10.1109/IV47402.2020.9304705
2. F. Kruber, E. Sanchez Morales, S. Chakraborty, M. Botsch, "Vehicle Position Estimation with Aerial Imagery from Unmanned Aerial Vehicles," *IEEE IV*, 2020. doi:10.1109/IV47402.2020.9304794
3. X. Chen, Z. Li, Y. Yang, L. Qi, R. Ke, "High-Resolution Vehicle Trajectory Extraction and Denoising From Aerial Videos," *IEEE T-ITS*, 2021. doi:10.1109/TITS.2020.3003782
4. R. Feng, C. Fan, Z. Li, X. Chen, "Mixed Road User Trajectory Extraction From Moving Aerial Videos Based on CNN Detection," *IEEE Access*, 2020. doi:10.1109/ACCESS.2020.2976890
6. F. Poggenhans et al., "Lanelet2: A High-Definition Map Framework for the Future of Automated Driving," *IEEE ITSC*, 2018. doi:10.1109/ITSC.2018.8569929
7. S. Maierhofer, A.-K. Rettinger, E. C. Mayer, M. Althoff, "Formalization of Interstate Traffic Rules in Temporal Logic," *IEEE IV*, 2020. doi:10.1109/IV47402.2020.9304549
8. S. Maierhofer, P. Moosbrugger, M. Althoff, "Formalization of Intersection Traffic Rules in Temporal Logic," *IEEE IV*, 2022. doi:10.1109/IV51971.2022.9827153
9. G. Monteiro, M. Ribeiro, J. Marcos, J. Batista, "Wrongway Drivers Detection Based on Optical Flow," *IEEE ICIP*, 2007. doi:10.1109/ICIP.2007.4379785
10. Z. Rahman, A. M. Ami, M. A. Ullah, "A Real-Time Wrong-Way Vehicle Detection Based on YOLO and Centroid Tracking," *IEEE TENSYMP*, 2020. doi:10.1109/TENSYMP50017.2020.9230463
11. A. B. S. Mahi, F. S. Eshita, T. Helaly, "An Automated System for Wrong-Way Vehicle Detection using YOLO and DeepSORT," *IEEE STI*, 2023. doi:10.1109/STI59863.2023.10465068
12. R. Akhawaji, M. Sedky, A.-H. Soliman, "Illegal Parking Detection Using Gaussian Mixture Model and Kalman Filter," *IEEE/ACS AICCSA*, 2017. doi:10.1109/AICCSA.2017.212
16. R. Krajewski, J. Bock, L. Kloeker, L. Eckstein, "The highD Dataset," *IEEE ITSC*, 2018. doi:10.1109/ITSC.2018.8569552
17. J. Bock et al., "The inD Dataset," *IEEE IV*, 2020. doi:10.1109/IV47402.2020.9304839
36. T. M. Tran, T. N. Vu, T. Nguyen, K. T. T. M. Nguyen, "UIT-ADrone: A Novel Drone Dataset for Traffic Anomaly Detection," *IEEE J. Selected Topics in Applied Earth Observations and Remote Sensing (JSTARS)*, 2023. doi:10.1109/JSTARS.2023.3285905 · dataset: https://uit-together.github.io/datasets/UIT-ADrone/
29. M. Naphade, S. Wang, D. C. Anastasiu, Z. Tang, M.-C. Chang et al., "The 4th AI City Challenge," *IEEE/CVF CVPR Workshops*, 2020. doi:10.1109/CVPRW50498.2020.00321

**CVF / ICCV / CVPR workshops**

26. S. M. Azimi et al., "SkyScapes — Fine-Grained Semantic Understanding of Aerial Scenes," *ICCV*, 2019. https://openaccess.thecvf.com/content_ICCV_2019/html/Azimi_SkyScapes__Fine-Grained_Semantic_Understanding_of_Aerial_Scenes_ICCV_2019_paper.html
30. Y. Zhao, W. Wu, Y. He, Y. Li, X. Tan et al., "Good Practices and A Strong Baseline for Traffic Anomaly Detection" (AI City 2021 anomaly track), *CVPR Workshops* 2021 / arXiv:2105.03827. https://arxiv.org/abs/2105.03827
28. "ATG-PVD: Ticketing Parking Violations on A Drone," *ECCV Workshops*, 2020. https://arxiv.org/abs/2008.09305

**Other papers and datasets**

5. R. Fonod et al., "Advanced computer vision for extracting georeferenced vehicle trajectories from drone imagery" (Geo-trax), *Transportation Research Part C*, 2025 / arXiv:2411.02136. https://arxiv.org/abs/2411.02136
13. "Staypoint Detection from Noisy Trajectory Data [Experiment Paper]," arXiv:2607.19312, 2026. https://arxiv.org/abs/2607.19312
14. D. Shan et al., "Extracting Key Traffic Parameters from UAV Video with On-Board Vehicle Data Validation," *Sensors* 21(16), 2021. doi:10.3390/s21165620
19. "Enhanced Vehicle Speed Detection Considering Lane Recognition Using Drone Videos in California," arXiv:2506.11239, 2025.
21. D. Fernández Llorca et al., "Vision-based vehicle speed estimation: A survey," *IET Intelligent Transport Systems*, 2021. doi:10.1049/itr2.12079
24. levelXdata, highD/exiD dataset format (recordingMeta `speedLimit`, Lanelet2/OpenDRIVE maps). https://levelxdata.com/wp-content/uploads/2024/03/exiD-Format_2_1.pdf
25. O. Zheng et al., "CitySim: A Drone-Based Vehicle Trajectory Dataset for Safety-Oriented Research and Digital Twins," arXiv:2208.11036. https://arxiv.org/abs/2208.11036
27. "Fast Wrong-way Cycling Detection in CCTV Videos: Sparse Sampling is All You Need," arXiv:2405.07293.
31. "UAV-Based Intelligent Traffic Surveillance System: Real-Time Vehicle Detection, Classification, Tracking, and Behavioral Analysis," arXiv:2509.04624, 2025.
32. "Automatic Detection of Zebra Crossing Violation." https://www.researchgate.net/publication/233385948_Automatic_Detection_of_Zebra_Crossing_Violation
33. "Research on Vehicle Lane Change Warning Method Based on Deep Learning Image Processing," *Sensors*, 2022. https://pmc.ncbi.nlm.nih.gov/articles/PMC9100057/
34. "Estimation of red-light running frequency using high-resolution traffic and signal data," *Accident Analysis & Prevention*, 2017. https://sciencedirect.com/science/article/abs/pii/S0001457517301082

**Tools, docs, practice**

15. Speed-camera tolerances: France/EU https://www.connexionfrance.com/practical/what-is-the-margin-of-tolerance-for-speed-cameras-in-france/154518 · UK https://www.drivingmasters.uk/kb/tolerances
18. CARLA Python API (waypoints, lane markings, lane types, crosswalks, traffic lights, landmarks) https://carla.readthedocs.io/en/latest/python_api/ · ScenarioRunner criteria https://scenario-runner.readthedocs.io/en/latest/openscenario_support/
20. Roboflow, "How to Estimate Speed with Computer Vision" and supervision GitHub discussions on calibration points. https://blog.roboflow.com/estimate-speed-computer-vision/ · https://github.com/roboflow/supervision/discussions/1335
22. DJI SRT telemetry format reference. https://callmarcus.com/guides/dji-srt-format/ · DJI forum "SRT Accuracy" https://forum.dji.com/thread-278598-1-1.html
23. OpenCV forum, frame timestamps (CAP_PROP_POS_MSEC issues) https://forum.opencv.org/t/extract-timestamp-of-each-frame-in-a-video-cap-prop-pos-msec-incorrect-timing-issues/5393 · VFR issue https://github.com/opencv/opencv/issues/23403
35. Spain DGT traffic drones. https://dronedj.com/2021/08/13/spanish-police-flying-drones-against-driving-offenses-on-summer-jammed-roads/ · https://inspain.news/39-drones-check-for-road-violations-in-spain/ · https://canarianweekly.com/posts/dgt-traffic-drones-video
