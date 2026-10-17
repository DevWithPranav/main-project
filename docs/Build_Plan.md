# Build Plan

**From today's system to the expected output (`docs/Expected_Output.md`)**

| | |
|---|---|
| Version | 1.0, 2026-10-09 |
| Target | `docs/Expected_Output.md` (Sections 4–9, decisions in Section 11) |
| Progress | Logged in `docs/main_project_tracker.md` after every task |

---

## 1. Where we start

**Built:**
- Detection and tracking, offline: YOLO26l with 3 classes, TrackTrack and stitching.
- Ground coordinates: exact for CARLA; calibrated for real video.
- The violation engine: 15 of 34 conditions built, 6 partly.
- Evidence clips, a staged-scenario stager and the scorer.
- 59 unit tests.

**Not built:** live streaming, pedestrians, the backend, the three dashboards and the twin, the recommendation system, and the road-surface anomaly model.

The work splits into **four tracks** that can run side by side once milestone M0 is done:

| Track | Milestones | Mostly |
|---|---|---|
| **Rules** | M1 → M2 → M3 | Violation engine, lane map, stager, evaluation |
| **Live** | M4 | Streaming detection + tracking + engine |
| **Platform** | M5 → M6 → M7 | Backend, Configuration Dashboard, 3D twin |
| **Planning** | M8 | Analytics, recommendations, CARLA validation runs |
| ~~**Surface**~~ | ~~M9~~ | ~~Pothole / crack / waterlogging / debris model~~ (dropped 2026-10-10) |

M10 brings everything together for the final evaluation and the two-machine presentation.

Size: **S** = a few days, **M** = about a week, **L** = two weeks or more, for one person. These are rough guides, not commitments.

---

## 2. Milestones

### M0 — Foundations (S) · prerequisite for every track

- **Event schema:** one JSON schema for violation and anomaly events, matching the PRD `ViolationEvent` (§13) and today's `events.py` fields (type, track IDs, times, location, lane, zone, value, tags, confidence, status, evidence).
- **Configuration schema:**
  - Violation types and conditions on/off, with thresholds (today's `rules.DEFAULTS`).
  - Model settings.
  - Road configuration.
  - **Profiles** per map / site / scenario.
- **Repository layout:** `backend/`, `frontend/`, `services/` (live pipeline) next to `ml/` and `simulation/`.
- **Local services:** `docker-compose` with PostgreSQL + PostGIS, Redis and MinIO.

**Done when:** the schemas are validated by tests, `docker compose up` starts the three services, and the current engine writes events that pass the schema.

### M1 — Road features in the lane map (M) · Rules track

Every rule in Section 4.2 that reads road properties needs them in the scene. The exporter (`export_lane_map.py`) and the site files (`zone_tool.py`) gain:

| Attribute | CARLA source | Real-video source |
|---|---|---|
| Lane-change permission (left / right / both / none) | Waypoint `lane_change` | Site file |
| Road height per centreline point | OpenDRIVE elevation (Town05 up to 10 m) | Not needed (flat) |
| Bridge flag | OpenDRIVE `<bridge>` (Town05: 26) | Site file |
| Ramp / merge flag | Derived: single-lane roads joining or leaving a highway-class road | Site file |
| Highway class | Speed limit ≥ configured value | Site file |
| Restricted lane (bus / emergency) | Scene config | Site file |
| Median / separator | OpenDRIVE `median` lanes and curbs | Site file |
| One-way | Lanes of a road in one direction only | Site file / learned flow |

**Side benefit:** using road height in ground projection fixes the flat-road assumption on flyovers (`ground_coords.py` uses one flat plane today).

**Done when:** `Town05.json` and `Town04.json` carry the new attributes, a unit test checks each one, and `zone_tool.py` can set them.

### M2 — Remaining vehicle conditions (L) · Rules track

| Rule | Conditions | How |
|---|---|---|
| Lane | A1 wrong lane for direction, A3 illegal lane change, A5 restricted lane, A6 shoulder driving, A8 unsafe lane change | Lane direction / permission / type checks; A8 uses gap and time-to-collision to the vehicle in the target lane |
| Illegal stopping | B1 main carriageway, B2 shoulder, B4 ramp / merge, B5 bridge | Replace the "highway zone" with lane properties (highway class, shoulder, ramp, bridge); keep B3 no-stopping zones |
| Wrong-way | C2 wrong-way entry, C4 on a ramp | One-way / ramp lanes |
| U-turn | D2 across a solid line, D3 at a prohibited junction, D4 through a median, D5 at a restricted opening | Line types, junction config, median lanes |
| Speeding | E4 by vehicle class | Limit per class in the config |

**Fixes found on staged flight `20261009_201727`, done in this milestone:**
- The stager must keep acts inside the camera frame, using the 16:9 footprint and the drone's heading. The no-parking act was missed because its spot sat at the frame edge.
- Explain the 1.3× speeding match and the 2 extra lane events.

**Done when:** each new condition has unit tests and a staged CARLA act (plus a negative). A second staged session scores every vehicle condition per type.

### M3 — Pedestrians and zebra conditions F2, F3, F5 (L) · Rules track

- **Detector:** run the earlier 10-class model (`full_train`) alongside the vehicle model, keeping only pedestrian / people (decision #1).
- **Tracking:** track people (a second tracker instance) and give them ground coordinates.
- **Recording:** `record_flight.py` also logs walker poses, for truth.
- **Rules:** F2 blocking (vehicle on the crossing while a pedestrian waits or crosses), F3 failure to yield (vehicle passes while a pedestrian with priority is on or entering the crossing), F5 obstructing (stopped vehicle in a pedestrian's path).
- **Staging:** add acts with CARLA walkers on crosswalks.

**Measure:** pedestrian detection recall and precision on CARLA walkers at about 67 m. People are a few pixels tall from there, so the result decides how far these rules can be trusted.

**Done when:** F2, F3 and F5 are scored on staged acts, and the pedestrian detection numbers are logged.

### M4 — Live pipeline (L) · Live track

- **Simulator side:** a streamer next to `record_flight.py` sends frames + camera pose + simulator time over the network (decision #4). Recording to disk stays optional.
- **Detector side:** a service receives frames and runs detection → tracking → ground coordinates → **online** kinematics → the engine, frame by frame. Today's smoother looks forward and backward in time; live needs a forward-only filter. Stitching becomes online re-linking.
- **Output:** events and vehicle states are published to the backend as they happen.
- **Two setups, same code:** one machine (localhost) now, two machines for the presentation.

**Measure:** event-to-dashboard latency (target ≤ 3 s), frames per second, and the GPU share with CarlaAir on one RTX 4050. Also measure how much worse online speeds and IDs are than offline.

**Done when:** a live CARLA session shows events arriving within the target, measured on one machine and on two.

### M5 — Backend (M) · Platform track

- **API:** FastAPI (PRD §14) for events, evidence, review (confirm / dismiss with a note), configuration and profiles, scenes / sites, sessions, statistics and exports.
- **Storage:** PostGIS for events, tracks, zones, configs, recommendations and planner history; Redis for live vehicle state (TTL); MinIO for clips and snapshots.
- **Live:** WebSockets for live vehicles (10 Hz) and new events.
- **Access:** JWT login with the roles of PRD §28 (OFFICER, OPERATOR, PLANNER, ADMIN; MAINTENANCE for anomalies).
- **Exports:** PDF, Excel and GeoJSON on demand (decision #3, target ≤ 10 s).

**Done when:** the API tests pass, a recorded flight's events load in with their clips, and an export of a date range is produced.

### M6 — Configuration Dashboard (L) · Platform track

React / Next.js (PRD §12):
- **Configuration:** violation on/off and thresholds, model and tracker settings, module on/off, road configuration, profiles.
- **Live monitoring:** CARLA video with boxes, IDs, trajectories and speeds; processing status and system health.
- **Violation management:** event list, evidence viewer (clip, snapshot, trajectory), review, searchable history.
- **Statistics:** counts per type, trends, hotspot ranking, exports.

**Done when:** a reviewer can run a whole session from the dashboard: pick a profile, watch live, review the events, export a report.

### M7 — 3D Digital Twin (L) · Platform track

CesiumJS (decision #5):
- **Roads:** built from the lane map, as lanes, markings, crossings, medians and bridges in 3D using road heights from M1. Placed at a configurable anchor point; no OSM buildings for CARLA towns.
- **Live layer:** vehicles at 10 Hz (green / amber / red), trails, speeds; violation pins; heatmap and problem sections; synchronised with the video.
- **Planner editing:** only the changes CARLA can simulate (Expected Output §5.3): speed limits, signal timing, restricted lanes and zones; lane markings and layout through a regenerated OpenDRIVE world. Each change is validated, then logged (who, when, what, why).

**Done when:** the twin shows a live session in sync with the video, and a planner edit is applied, logged, and picked up by the engine.

### M8 — Recommendation system (L) · Planning track

- **Analytics:**
  - Hotspots with DBSCAN per type (eps 50 m, min 10 events).
  - Trends, density, speeds and queues per road section.
- **Recommendations:**
  - Rules per area (Expected Output §7.2).
  - Each recommendation carries all fields: problem, location, evidence, action, expected impact, priority, validation method, confidence and limitations, alternatives.
  - A "projected estimate" shown before simulation.
- **Validation runner:**
  - Runs the same CARLA scenario as baseline and as modified: same town, traffic seed, vehicle count, weather and duration.
  - **Prerequisite:** traffic generation must become reproducible (fixed seed, synchronous mode). Today `traffic_flow.py` is random.
  - Compares violation counts, conflict indicators (time-to-collision), speeds, travel time and queues.
- **Planner history:** accepted / rejected / modified, rationale, results. Used to rank future proposals by **measured** outcomes.

**Done when:** at least one recommendation per in-scope type comes from event data, and each is validated by a baseline-vs-modified run with its metrics shown to the planner.

### M9 — Road-surface anomalies (L) · Surface track — **DROPPED (team, 2026-10-10)**

Road-surface anomalies (PRD Objective 3) are out of the project. The plan below is kept for the record only; the code written
for it (`ml/pothole/`, backend anomaly routes) stays in the repo, unused, and the UI shows violations only.


- **Model:** continue the pothole track (decision #2, tracker §3). Write `ml/pothole/modify_model.py` (DSConv + SimAM in YOLO26l-seg), train it, and score each category against F1 ≥ 0.65 (PRD §27.4).
- **Integration:** anomaly events with a severity score, de-duplicated by location, shown on the twin and the dashboard like violations; maintenance status per the PRD.

**Done when:** each anomaly category is scored, and anomalies appear in the dashboard and on the twin.

### M10 — Final evaluation and presentation (M)

- **Staged evaluation at scale:** the PRD asks for 100 scripted scenarios per type. Batch the stager over several spots, towns (Town05, Town04 for highway / ramps) and seeds.
- **Real videos:** every violation type on at least one external video, results reported.
- **Acceptance checklist:** Expected Output §9, each item with its measured result in the tracker.
- **Presentation:** a rehearsal on two machines.

---

## 3. Order and dependencies

```
M0 ──┬─▶ M1 ─▶ M2 ─▶ M3 ───────────────────────────┐
     ├─▶ M4 (live) ────────────────────────────────┤
     ├─▶ M5 (backend) ─┬─▶ M6 (dashboard) ─────────┼─▶ M10
     │                 └─▶ M7 (twin) ─▶ M8 (recs) ─┤
     └─▶ M9 (surface) ─────────────────────────────┘
```

- **M1 first in the Rules track:** M2's rules, M7's 3D roads and M8's validation all read the new road attributes.
- **M8 needs M7:** planners apply changes in the twin, plus reproducible CARLA traffic.
- **M4, M5 and M9 can start right after M0,** in parallel with the Rules track.

Suggested first sequence for one person: **M0 → M1 → M2** (the rules are closest to done, and every later demo needs them), then M5 → M4 → M6 → M7 → M8, with M3 and M9 fitted in where the team has a second person free.

---

## 4. Risks

| Risk | Effect | Handling |
|---|---|---|
| One RTX 4050 runs CarlaAir and the detectors live | Low frame rate, latency over 3 s | Measure in M4; lower the detector input size or skip frames for live; the presentation uses two machines |
| Pedestrians are a few pixels tall from 50–70 m | F2, F3, F5 unreliable | Measure in M3 before relying on them; fly lower over crossings; later a combined retrained model |
| CARLA traffic not reproducible | Before/after differences come from randomness, not the change | Fixed seeds and synchronous mode (M8 prerequisite); repeat runs and report the spread |
| Road layout edits need a regenerated OpenDRIVE world | No buildings or props in that world; looks different from the town | Allowed only for layout edits; labelled in the twin |
| 100 staged scenarios per type | Many simulator hours | Batch staging unattended (M10), several acts per flight |
| Online tracking is worse than offline | More ID switches, noisier speeds live | Measure the gap in M4; keep the offline path for evidence and evaluation |
| Real videos need a site file per location | Manual work per site | `zone_tool.py` + learned direction; anything that can't be seen (bus lane, bridge) is marked once |
