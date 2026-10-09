# Expected System Output

**Autonomous Aerial Surveillance Framework: what the finished system must deliver**

| | |
|---|---|
| Version | 1.0, 2026-10-09 |
| Based on | The team's expected-output statement (2026-10-09) and the PRD (`docs/Product_Requirements_Document.md`, v2.0.0) |
| Status | Agreed target for completing the project. Where it differs from the PRD, the decision is recorded in Section 10 |
| Status tracking | `docs/main_project_tracker.md` |

---

## 1. Purpose

This document is the single description of what the finished platform does and what it outputs. It merges the team's expected-output statement with the PRD.

- **The PRD agrees with the statement:** the requirement stands as written here.
- **The statement asks for more than the PRD:** for example simulation-based validation of planner changes, or learning from planner actions. The statement wins and this document defines it.
- **The PRD asks for something the statement leaves out:** for example road-surface anomalies or PDF reports. The item is listed in Section 10 with its decision.
- **Status markers:** ✅ built and measured, 🟡 partly built, ❌ not started. Status is as of the date above. Measured numbers come from `docs/main_project_tracker.md`.

**Goal (unchanged from the statement):** go beyond detecting violations. Help city planners understand *why* traffic problems happen, try fixes in a digital environment, and evaluate those fixes before any real-world change.

---

## 2. System at a glance

```
 INPUTS                       CORE                              OUTPUTS / USERS
 ┌──────────────────┐  ┌───────────────────────────────┐  ┌──────────────────────────────┐
 │ CARLA live feed  │  │ Detection + tracking          │  │ Configuration Dashboard      │
 │ CARLA recordings │─▶│ Ground coordinates + speed    │─▶│  config, live view, evidence │
 │ External videos  │  │ Violation engine (6 types)    │  │ Digital Twin                 │
 └──────────────────┘  │ Events + evidence store       │  │  live map, hotspots, editing │
          ▲            │ Analytics (hotspots, trends)  │  │ Recommendation System        │
          │            └───────────────────────────────┘  │  proposals, validation runs  │
          │                                               └──────────────┬───────────────┘
          └──────── CARLA re-runs scenarios for planner changes ─────────┘
```

Three user-facing components, the same as in the statement:

1. **Configuration Dashboard:** configure the system, watch it live, review violations and evidence.
2. **Digital Twin System:** a map-aware virtual copy of the road network, showing traffic and violations, which planners can edit.
3. **City Planner Recommendation System:** turns violation patterns into proposals and validates them with CARLA before/after runs.

---

## 3. Inputs and modes

| Input | What it is | How positions and time are known | Status |
|---|---|---|---|
| **CARLA live feed** | Drone camera streamed while the simulation runs | Camera pose and simulator time logged per frame (exact) | ❌ live streaming. Today: record first, process after |
| **CARLA recording** | A recorded flight (frames, poses, vehicle truth, traffic-light states) | Same, plus true vehicle positions for evaluation | ✅ |
| **External video** | Real drone footage (downloaded or own drone) | Scene stabilisation + site calibration (scale / homography / DJI `.SRT`) and per-frame timestamps | ✅ offline (2 stock clips, 2 UIT-ADrone videos) |

The modes follow PRD §26, adjusted:

| Mode | Use | Status |
|---|---|---|
| Simulation (CARLA) | Development, staged violations, planner what-if runs | 🟡 recorded flights work; live mode missing |
| Live drone | Real drone stream | ❌ (no drone pipeline yet; external video files stand in) |
| Playback / review | Re-run a recorded flight or video and review its events | ✅ as scripts; no dashboard yet |
| Planning | Historical analysis and recommendations, no live feed | ❌ |
| Offline | Buffer events when the backend is down | ❌ |

**Real-time target** (PRD §27.5): an event reaches the dashboard **≤ 3 s** after it happens, and the twin updates vehicle positions at **10 Hz**. Today's pipeline is batch: about 15–20 min of processing per 6–8 min of footage on the RTX 4050.

**Machines** (Section 11, #4): development on one machine; the presentation runs on two (CarlaAir on one, detector + backend + dashboards on the other). The simulator side sends frames with pose and time over the network, so both setups use the same code.

---

## 4. Violation detection (core engine)

### 4.1 Principle: road features first, zones only where needed

Rules read **properties of the road** wherever possible: lane direction, lane type (driving / shoulder / parking / restricted), line types, lane-change permission, speed limit, junction, bridge and ramp flags. Zones (drawn polygons) are used only where a rule is about an *area* that the road map can't express: no-stopping zones, no-U-turn areas, restricted openings.

| Source of road properties | CARLA | External video |
|---|---|---|
| Lanes, direction, line types, lane-change permission, shoulder/parking, junction, speed limit | Exported automatically from the town's OpenDRIVE (`export_lane_map.py`) | Drawn once per site (`zone_tool.py` site file), or learned direction (`--learn-flow`) |
| Bridge / flyover | OpenDRIVE bridges (Town05 has 26) | Marked in the site file |
| Ramp / merge area | Derived from road connectivity (CARLA doesn't label ramps) | Marked in the site file |
| Restricted lane (bus / emergency) | **Configured per lane** in the scene config (CARLA towns have none) | Configured per lane in the site file |
| Crosswalks | OpenDRIVE crosswalks | Drawn in the site file |

### 4.2 Violation catalogue

Six violation types are in scope. Every event carries type, vehicle (track) ID, start / flag / end time, location, lane, value, confidence, status and evidence (Section 4.3).

**A. Lane violations** (road features, no zones)

| # | Condition | Detection rule | Status |
|---|---|---|---|
| A1 | Driving in the wrong lane | Vehicle travels in a lane not permitted for its direction | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| A2 | Crossing a solid lane line | Trajectory crosses a solid / double-solid boundary | ✅ |
| A3 | Illegal lane change | Lane change across a boundary whose lane-change permission forbids it | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| A4 | Driving on a lane divider | Vehicle body over a lane line beyond tolerance (0.3 m) for > 3 s | ✅ |
| A5 | Entering a restricted lane | Vehicle enters a lane configured as bus-only / emergency / restricted | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| A6 | Driving on the shoulder | Vehicle travels (moving) along a shoulder lane | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| A7 | Wrong-way driving | Same as C1 (counted once, under wrong-way) | ✅ |
| A8 | Unsafe lane change | Lane change with a conflict: gap or time-to-collision to a vehicle in the target lane below a threshold | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |

**B. Illegal stopping on highway / flyover** (road features; zone only for B3)

| # | Condition | Detection rule | Status |
|---|---|---|---|
| B1 | Stopping on the main carriageway | Stationary in an active traffic lane of a highway-class road (by speed limit / road class), not in a queue | ✅ from lane properties (highway class), no zone needed (M2) |
| B2 | Stopping on a highway shoulder | Stops on a shoulder lane where stopping is prohibited (alone on the shoulder: "possible breakdown") | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| B3 | Stopping in a no-stopping zone | Stops inside a configured no-stopping zone (zone) | ✅ (no-parking / no-stopping zone rule) |
| B4 | Stopping near an entrance or exit | Stops within a ramp or merge area | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| B5 | Stopping on a bridge or tunnel | Stops on a road section flagged bridge / tunnel | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| B6 | Extended stop | Any prohibited-location stop beyond its time threshold (PRD: 20 s highway, 30 s no-parking) | ✅ timing in place |

Traffic-jam stops are not violations (PRD edge case). They're tagged `queue` and kept for audit.

**C. Wrong-way driving** (road features)

| # | Condition | Detection rule | Status |
|---|---|---|---|
| C1 | Driving against traffic | Motion > 150° from the lane direction, ≥ 0.17 s and ≥ 5 m (PRD) | ✅ |
| C2 | Wrong-way entry | Enters a one-way road or carriageway from the prohibited end | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| C3 | Against traffic on a divided highway | C1 on highway-class lanes | ✅ |
| C4 | Wrong way on a ramp | C1 on ramp lanes | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| C5 | Wrong way in a one-way lane | C1 on one-way lanes | ✅ |

Not checked inside junctions: legal turns look like anything there (PRD edge case 2).

**D. Illegal U-turn** (road features; zones for D1 and D5)

| # | Condition | Detection rule | Status |
|---|---|---|---|
| D1 | U-turn in a prohibited zone | Heading change > 160° inside a no-U-turn zone, smooth arc (three-point turns tagged) | ✅ |
| D2 | U-turn across a solid line | The reversal crosses a solid / double-solid line | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| D3 | U-turn at a prohibited junction | U-turn inside a junction marked "no U-turn" | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending (junctions listed in the profile) |
| D4 | U-turn through a median barrier | The path crosses a median / non-traversable separator | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending |
| D5 | U-turn at a restricted opening | U-turn at a median opening configured as restricted (zone) | ✅ same as D1 with a zone over the opening |

**E. Speeding** (road features; zones only for special-limit areas)

| # | Condition | Detection rule | Status |
|---|---|---|---|
| E1 | Speed exceeds the limit | Speed minus 2σ above lane limit plus tolerance (EU: 5 km/h below 100, 5 % above) | ✅ |
| E2 | Sustained speeding | Above the limit ≥ 0.33 s (PRD 10 frames) | ✅ |
| E3 | Speeding in a restricted zone | A zone with a lower limit overrides the lane limit | ✅ |
| E4 | Speeding by vehicle category | Limit looked up per class (car / bus / truck) where limits differ | 🟡 built + unit-tested (M2, 2026-10-09); staged act pending (limits per class in the profile) |
| E5 | Repeated speeding | One event per continuous episode; episodes per vehicle counted | ✅ |

**F. Zebra-crossing violations** (crosswalk from the map or site file)

| # | Condition | Detection rule | Status |
|---|---|---|---|
| F1 | Stopping on a zebra crossing | Stationary on the crossing > 10 s (PRD), not in a queue | ✅ |
| F2 | Blocking a pedestrian crossing | Vehicle on the crossing while a pedestrian waits at it or is on it | ❌ needs pedestrians |
| F3 | Failure to yield | Vehicle passes through the crossing while a pedestrian with priority is on it or entering it | ❌ needs pedestrians |
| F4 | Parking on a zebra crossing | Left standing on the crossing (long stop, no queue) | ✅ (F1 with a longer threshold) |
| F5 | Obstructing pedestrian movement | Stopped vehicle in a pedestrian's path across the crossing | ❌ needs pedestrians |

F2, F3 and F5 need **pedestrian detection and tracking**, which the current 3-class detector doesn't do (see Section 11).

### 4.3 Evidence per violation

Each event stores:
- **Clip:** a 10 s evidence clip, 7 s before and 3 s after the flag, with an overlay and a banner. ✅
- **Snapshot:** the flag frame. ✅ (from the clip)
- **Trajectory:** position, speed and heading over the event. ✅ (`kinematics.csv`)
- **Timestamp and location:** simulator / video time, map coordinates, lane, zone. ✅
- **Telemetry:** camera pose for CARLA, calibration and registration status for real video. ✅
- **Confidence and status:** `flagged`, `needs_review`, `suppressed` (edge case, kept for audit) or `possible_breakdown`, plus tags such as `queue`, `estimated_speed` and `static_confirmed`. ✅
- **Review outcome:** a person confirms or dismisses each event (PRD §30.5 human-in-the-loop). ❌ no review UI yet.

### 4.4 Accuracy targets

| Item | Target (PRD) | Current measured value |
|---|---|---|
| Vehicle detection mAP@0.5 | ≥ 0.75 | 0.696 VisDrone val (3-class), 0.702 GT clip, 0.844 CARLA Town05 |
| Tracking continuity / ID switches | ≥ 90 % / ≤ 5 % | GT clip IDF1 0.881, 2 ID switches on 40 vehicles; staged flight: all 13 scripted cars tracked within 0.03–0.1 m of truth (wrong-way car 3 m) |
| Violation F1 per type | ≥ 0.70 (precision ≥ 0.85, recall ≥ 0.75) | Staged flight `20261009_201727`, pipeline vs staged: wrong-way, U-turn, zebra 1.0; lane 0.5; speeding 0.4; no-parking missed (spot at the frame edge). One staged act per type, so far below the PRD's 100 scenarios per type |
| Event-to-dashboard latency | ≤ 3 s | Not measurable yet (batch pipeline) |

---

## 5. Digital Twin System

### 5.1 Environment

| Requirement | How | Status |
|---|---|---|
| Recreate the road network: roads, lanes, junctions, medians, crossings, ramps | Built from the CARLA town's OpenDRIVE (the same source as the lane map); external sites from their site file | 🟡 lane map exists; no twin view yet |
| Directions, speed limits, restricted lanes, violation areas | Lane properties + configured zones (Section 4.1) | 🟡 |
| Mapped to the CARLA map and simulation data | **3D twin in CesiumJS** (decision #5). Twin coordinates = CARLA world metres placed at a configurable anchor point: CARLA towns carry no real-world location (geo-reference at lat 0 / lon 0). Real sites are anchored to their own GPS when calibrated | ❌ |
| Planners inspect and modify supported aspects | See Section 5.3 | ❌ |

### 5.2 Real-time visualisation

- Vehicles as markers updated at 10 Hz, with ID, class, speed and a trajectory trail. Colour: green normal, amber "rule timing this vehicle", red "in violation" (PRD §18.3). The overlay video already uses the same states.
- Violation pins at their location, filterable by type, date, status and confidence (PRD §18.4).
- Hotspot / heatmap layer and a list of problem road sections (PRD §18.5).
- Synchronised with the CARLA feed: the twin and the video show the same moment.

### 5.3 Planner editing, limited to what CARLA can actually simulate

| Change | Can CARLA simulate it? | How |
|---|---|---|
| Speed limit of a road / lane | Yes | Lane-map property + traffic-manager target speeds |
| Signal timing | Yes | Traffic-light timing via the CARLA API |
| Restricted lane, no-stopping / no-U-turn zone | Yes (rule-side) | Scene config; violations measured under the same traffic |
| Lane markings (solid ↔ broken), lane-change rules | Partly | Edited lane map; traffic follows OpenDRIVE lane-change permissions only if the map is regenerated |
| Road layout (lanes, one-way, closures) | Partly | Edited OpenDRIVE loaded as a generated world; no buildings or props in that mode |
| New traffic signs | Limited | Only as OpenDRIVE signals in a regenerated map |

Every change is checked against this table before it's accepted. Unsupported changes are refused with a reason. Every change is recorded: who, when, what changed, and why if given.

---

## 6. Configuration Dashboard

Combines PRD §19.1 (Monitoring Dashboard) with the configuration functions the statement asks for.

| Area | Must provide | Status |
|---|---|---|
| **Violation configuration** | Enable / disable each violation type and condition (Section 4.2); thresholds per rule (times, distances, angles, tolerances), as in today's `rules.DEFAULTS` / `--params` file | 🟡 config file only |
| **Model configuration** | Detector weights, input size, tracker and its config (`tracktrack_ours.yaml` etc.), enable / disable modules | 🟡 command-line only |
| **Road configuration** | Lane properties, restricted lanes, zones, speed limits per map / site | 🟡 JSON files + `zone_tool.py` |
| **Profiles** | Saved configuration per CARLA map, road type and scenario | ❌ |
| **Live monitoring** | CARLA live view with boxes, IDs, trajectories and speeds; processing status and system health (fps, latency, GPU) | ❌ (offline overlay videos exist) |
| **Violation management** | Event list (type, time, location, vehicle ID), evidence viewer, confirm / dismiss with a note, searchable history | ❌ (events exist as JSON / CSV with clips) |
| **Statistics** | Counts per type, trends over time, hotspot ranking | ❌ |

Access follows PRD §28 roles: OFFICER reviews events, OPERATOR runs sessions and sees system health, PLANNER edits the twin and runs simulations, ADMIN configures everything.

---

## 7. City Planner Recommendation System

### 7.1 What it analyses

Violations (type, frequency, severity, location), trajectories, density, speeds and congestion, road geometry, repeated hotspots, previous planner actions and their measured outcomes, before/after simulation results, and the PRD's planning priorities.

### 7.2 Recommendation types

Starts from the PRD §21.2 rule set and extends it to the statement's list:

| Area | Example trigger | Example proposal |
|---|---|---|
| Speed management | Speeding cluster on a segment | Review limit, signage, traffic calming |
| Lane management | > 50 % lane violations on a stretch (PRD) | Refresh markings, change lane allocation |
| Wrong-way prevention | > 40 % wrong-way at an entrance / ramp (PRD) | Signage, markings, physical barriers, one-way change |
| Illegal stopping | Stopping hotspot | Stopping restriction or a designated stopping bay |
| Pedestrian safety | Zebra violations cluster (F1–F5) | Move / mark the crossing, signals, protection |
| Intersections | U-turn / turning conflicts at a junction | Turn restrictions, signal timing, layout |
| Highway safety | U-turns at median openings, ramp stops | Close or restrict openings, ramp redesign |
| Traffic flow | Congestion, queues | Lane allocation, signal timing |

Recommendations are **proposals that need validation**, never automatic decisions.

### 7.3 Content of each recommendation

Problem · location (segment / lane / junction) · supporting evidence (counts, clips, trajectories, metrics) · proposed action · expected impact · priority (safety risk × frequency × severity × planning objectives) · validation method (scenario + metrics) · confidence and limitations · alternatives with trade-offs where there are several.

Hotspots come from DBSCAN on event locations (PRD §21.1: eps 50 m, min 10 events, per type). The PRD's "≥ 50 events per cluster" criterion applies.

### 7.4 Validation through the twin

1. Find a recurring issue from violation analytics.
2. Generate one or more proposals.
3. Show them to the planner.
4. The planner applies one to a twin configuration (only supported changes, Section 5.3).
5. Run CARLA: **baseline and modified scenario under the same conditions** (same town, traffic seed, vehicle count, weather, duration).
6. Compare metrics: violation frequency per type, conflict indicators (near-misses / low time-to-collision), average speed, travel time, queue length, pedestrian indicators where pedestrians are tracked.
7. Show the results, trade-offs and limitations.
8. The planner accepts, rejects or modifies.

The PRD's rule-based projection ("before 142 → projected ~35 per month", §21.3) is kept as a quick estimate shown *before* a simulation is run. It's always labelled "projected estimate".

### 7.5 Learning from planner actions

Recorded per recommendation: generated / shown / accepted / rejected / modified, configuration changes, planner rationale, baseline and post-change results, and the planner's own assessment.

This history adjusts the **ranking** of future proposals in similar situations, giving more weight to intervention types whose simulations showed measured improvement. A planner accepting a proposal is **not** treated as proof that it worked; only measured outcomes and explicit feedback count, and the uncertainty is always shown.

---

## 8. End-to-end workflow

1. CARLA runs the selected map and traffic scenario and streams the drone view. An external video can replace the live feed.
2. The twin loads the road environment from the map (or site file) plus its configuration.
3. Users configure violations, models, tracking and rules in the Configuration Dashboard.
4. The pipeline detects and tracks vehicles (and pedestrians, once added) and computes ground positions, speeds and headings.
5. The violation engine checks the configured conditions and creates an event when one holds.
6. Evidence is stored: clip, snapshot, trajectory, time, location, telemetry.
7. The dashboard shows live view, tracks, events, evidence, statistics and hotspots, and reviewers confirm or dismiss.
8. Analytics finds patterns: hotspots, trends, geometry, PRD priorities.
9. The recommendation system proposes interventions with evidence.
10. A planner applies a proposal to the twin.
11. CARLA runs baseline and modified scenarios, and the results are compared.
12. Decisions and measured outcomes are recorded and inform future recommendations.

---

## 9. Acceptance checklist

The project is complete when each item has been demonstrated, with the measured result logged in the tracker:

- [ ] All 34 conditions in Section 4.2 implemented or explicitly excluded, each with a unit test.
- [ ] Staged CARLA scenarios for every implemented condition, scored per type against the PRD targets (F1 ≥ 0.70, precision ≥ 0.85, recall ≥ 0.75), with negatives included.
- [ ] Each violation type also run on at least one external video, results reported (no PRD target for real footage).
- [ ] Live CARLA mode: events on the dashboard ≤ 3 s after they happen; twin at 10 Hz.
- [ ] Configuration Dashboard: enable / disable, thresholds, profiles, live view, event review, statistics.
- [ ] Digital twin: map view of the town, live vehicles, violation pins, hotspots; supported edits applied and logged.
- [ ] Recommendation system: at least one recommendation per in-scope type from real event data, each validated by a baseline-vs-modified CARLA run with metrics.
- [ ] Planner history recorded and used in ranking.
- [ ] Every event traceable from raw footage to dashboard, report or recommendation (PRD Objective 6).

---

## 10. Differences from the PRD (decision log)

| PRD item | Decision for this project | Date / source |
|---|---|---|
| 10 violation types | **6 types**: lane, illegal stopping (highway / flyover / no-stopping), wrong-way, U-turn, speeding, zebra, with the condition lists in Section 4.2 | Team statement 2026-10-09 |
| Red-light jumping (V4) | Out of scope. Code kept, off by default (`--red-light`) | User 2026-10-09 |
| Helmet-less riding, overloading (V8, V9) | Out of scope: motorbike class dropped in Stage 3; riders are a few pixels from 50–100 m | Tracker 2026-10-03 |
| No-parking zone (V1) | Kept as condition B3 (no-stopping zone) | This document |
| Zones are "the foundational layer" (PRD §18.2) | **Road features first, zones only where needed** (Section 4.1) | User 2026-10-09 |
| YOLOv8 + DeepSORT | YOLO26l retrained on 3 classes + TrackTrack (measured better on our GT clip) | Tracker 2026-10-02/03 |
| Altitude 80–120 m | Tested at about 50–70 m (CARLA 67.6 m, UIT-ADrone 50 m); higher altitudes not yet evaluated | Measured |
| CesiumJS twin on OSM / GPS | CARLA towns have no real-world location, so the twin works in map metres; GPS anchoring only for calibrated real sites | This document |
| What-if = rule-based projection | Kept as a quick estimate; **validation by CARLA baseline-vs-modified runs** added | Team statement |
| Recommendations from a fixed rule table | Rule table kept as a starting point; ranking adjusted by measured simulation outcomes and planner feedback | Team statement |
| Monitoring + Planning dashboards | Plus a **Configuration Dashboard** (merges Monitoring with system configuration) | Team statement |
| Road-surface anomalies (Objective 3) | **Kept** (Section 11, #2) | Team 2026-10-09 |
| Automated PDF / Excel / GeoJSON reports (Objective 5) | **Kept, on demand from the dashboard**; scheduled delivery dropped (Section 11, #3) | Team 2026-10-09 |
| CesiumJS twin on OSM / GPS → 3D twin | **3D**, CesiumJS with a configurable anchor, roads from OpenDRIVE (Section 11, #5) | Team 2026-10-09 |
| MinIO object store (PRD §13.3) | **SeaweedFS** (S3-compatible, Apache-2.0): MinIO no longer publishes free images. Same S3 API and bucket layout | Measured 2026-10-09 (pull refused on Docker Hub and quay.io) |
| Live drone mode (MAVLink, RTSP) | External video files stand in for real footage; no drone hardware pipeline | This document |

---

## 11. Decisions on the open questions (2026-10-09)

| # | Question | Decision |
|---|---|---|
| 1 | Pedestrian detection for F2, F3, F5 | **Two detectors now:** the retrained 3-class model stays for vehicles; the earlier 10-class model (`full_train`, pedestrian mAP50 0.540) runs alongside, for pedestrians only. **Later:** retrain one model with car / bus / truck + pedestrian |
| 2 | Road-surface anomalies (pothole track) | **Kept** as a deliverable (PRD Objective 3, F1 ≥ 0.65 per category). Anomalies appear as events on the twin and in the dashboard like violations |
| 3 | Automated reports | **Kept, on demand only:** the dashboard exports PDF (summary + hotspot map + recommendations), Excel (event tables) and GeoJSON (events, hotspots, recommendations) for any date range and area (PRD target ≤ 10 s). Scheduled / emailed reports dropped for now |
| 4 | Live mode architecture | **Single machine now** (simulator and detector share the RTX 4050). **Two machines for the presentation:** one runs CarlaAir, one runs the detector and the dashboards. So the live feed is sent over the network (frames + pose + time) from the start: the same code runs on one machine (localhost) or two |
| 5 | Twin technology | **3D.** The road network is built from the OpenDRIVE lane map in map metres. CesiumJS (PRD) is used with a configurable anchor point, because CARLA towns have no real-world location. No OSM buildings for CARLA towns (they would belong to the anchor's real city) |

---

## 12. What exists today

| Component | Built | Missing |
|---|---|---|
| Detection + tracking | YOLO26l (3 classes), TrackTrack, stitching, offline on recorded flights and videos | Live streaming; pedestrians |
| Geometry | CARLA exact pose projection; real-video stabilisation + calibration | GPS output for real sites |
| Violation engine | 7 rule families, place memory, queue context, static-vehicle check, learned flow direction, evidence clips, 59 unit tests | Conditions marked ❌ in Section 4.2 |
| Evaluation | Staged CARLA scenarios + oracle + scorer (natural events separated); highD / inD and UIT-ADrone tools | More staged repeats per type (PRD: 100 per type) |
| Backend (FastAPI / PostGIS / Redis / MinIO) | Nothing | All |
| Dashboards, digital twin, recommendation system | Nothing | All |
