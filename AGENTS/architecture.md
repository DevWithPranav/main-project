# Architecture

This is the system-level view. The detailed design of the violation engine is in `docs/Violation_Engine_Architecture.md`. The product spec is `docs/Product_Requirements_Document.md`. Status lives in `docs/main_project_tracker.md`.

---

## 1. Objectives (from the PRD)

| # | Objective | State |
|---|---|---|
| 1 | Traffic violation detection (detector + tracker + rules) | In progress, the main focus |
| 3 | Road-surface anomalies (potholes, cracks, ...) | In progress (`ml/pothole/`) |
| 7 | Pixel → ground / GPS mapping | Partial (exact in CARLA; real footage in Phase B) |
| 8 | CARLA-based simulation and validation | In progress (`simulation/`) |
| 2, 4, 5, 6, 9 | Digital twin, urban planning, reporting, backend (FastAPI/PostGIS/Redis/MinIO) | Not started |

---

## 2. End-to-end data flow

```
   ┌──────────────────────── SIM ENV (Python 3.10) ────────────────────────┐
   │ CarlaAir simulator (CARLA + AirSim, UE4)                              │
   │   traffic_flow.py         spawn traffic                               │
   │   record_flight.py        manual drone flight, nadir camera           │
   │                           -> frames/*.jpg, camera pose + sim_time      │
   │                              per frame, vehicle_poses.csv (truth)      │
   │   stage_violations.py     scripted violations -> scenario_log.json     │
   │   export_lane_map.py      OpenDRIVE -> configs/scenes/<Town>.json      │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   │  files on disk only (no shared process)
   ┌───────────────────────────────▼─────── MAIN ENV (Python 3.12) ─────────┐
   │ Layer 1  PERCEPTION                                                    │
   │   YOLO26l retrained, 3 classes: car / bus / truck                      │
   │   -> TrackTrack (tracktrack_ours.yaml, Ultralytics 8.4)                │
   │   -> stitch_tracklets.py + postprocess_tracks.py                       │
   │   = trajectories_final.csv   (pixels, stable IDs)                      │
   │        process_recorded_flight.py (CARLA)  /  process_video.py (real)  │
   │                                                                        │
   │ Layer 2  GEOMETRY          ground_coords.py (CARLA: exact pose ray)    │
   │                            real_geometry.py / scene_map.py (real)      │
   │ Layer 3  STATE             kinematics.py  Kalman + RTS smoother        │
   │                            -> x, y, speed, heading per frame           │
   │ Layer 4  SCENE KNOWLEDGE   lane_map.py  lanes, zones, map matching     │
   │                            configs/scenes (CARLA), configs/sites (real)│
   │ Layer 5  RULES             predicates.py + rules.py  (7 monitors)      │
   │ Layer 6  EVENTS            events.py  one event per episode, status    │
   │ Layer 7  OUTPUTS           violations.json/csv, render_violations.py   │
   │                                                                        │
   │   run_violations.py drives Layers 2-7; eval_violations.py scores them  │
   └────────────────────────────────────────────────────────────────────────┘
```

The sim and main environments never run in the same process (except `live_fly_and_track.py`). The CARLA wheel needs Python 3.10, and the simulator and YOLO inference both compete for the GPU.

---

## 3. Components

### Perception (`ml/detection/`, `ml/violation_engine/`)
- **Detector:** YOLO26l, retrained (`retrain_v1`) on VisDrone + UAVDT + CARLA for car / bus / truck. Scripts: `build_retrain_dataset.py`, `train_retrain.py`, `compare_detectors.py`, `carla_autolabel.py`.
- **Tracker:** TrackTrack is the default; ByteTrack, BoT-SORT, Deep OC-SORT and FastTrack configs are kept for comparison. The `--tracker <name>` flag maps to `ml/violation_engine/<name>.yaml`.
- **Post-processing:** `detection_filters.py` (class gates, size filter), `stitch_tracklets.py` (re-link broken IDs), `postprocess_tracks.py`.
- **Evaluation:** `eval_tracking.py` (MOTA / IDF1 / HOTA), `run_experiment.py`, `regression.py`, with GT in `ml/data/eval/gt_1080p/`.

### Violation engine (`ml/violation_engine/`)
| Module | Job |
|---|---|
| `ground_coords.py` | pixels → world metres from per-frame camera pose (CARLA) |
| `real_geometry.py`, `scene_map.py`, `zone_tool.py` | real-footage scale, stabilisation, zone clicking |
| `kinematics.py` | Kalman + RTS smoothing → speed, heading, uncertainty |
| `lane_map.py` | load lane map + zones, match position → lane, s, d |
| `predicates.py` | reusable building blocks (stopped, in zone, against lane, straddle, ...) |
| `rules.py` | `Engine`: the 7 violation monitors built from predicates + time conditions |
| `events.py` | event records, statuses, writers |
| `static_vehicles.py` | pixel-based "is a car really standing there" check for stop-type events |
| `signals.py` | traffic-light states + stop lines (red-light rule, CARLA only) |
| `flow_map.py` | learned traffic direction per 4 m cell, for footage without a lane map |
| `run_violations.py` | CLI: flight → events (pipeline or `--oracle`; `--site` real; `--learn-flow`) |
| `eval_violations.py` | match events to the staged scenario → precision / recall / F1 |
| `levelx.py` | run the engine on highD / inD / rounD real trajectories (false alarms per hour) |
| `eval_uit_adrone.py` | frame-level scoring against UIT-ADrone anomaly labels |
| `render_violations.py` | overlay video; `--clips` writes a 10 s evidence clip per event |

**In-scope violations:** no-parking, wrong-way, illegal U-turn, speeding, lane violation, zebra crossing, highway stopping. Red-light is optional and CARLA-only (built). Helmet and overloading are out of scope.

### Simulation (`simulation/`)
- `carla_scripts/`: flying, recording, traffic, map loading, lane-map export, gamepad control.
- `violation_scenarios/stage_violations.py`: scripts each violation type in CARLA with a log that serves as ground truth.

### Pothole track (`ml/pothole/`)
- YOLO26l-seg baseline (`train_baseline.py`). Planned DSConv + SimAM modules (`modules.py`).

---

## 4. Evaluation levels

| Level | Input | Tests |
|---|---|---|
| L0 | Synthetic tracks (unit tests) | rule logic only |
| L1 | CARLA true positions (`--oracle`) | rules + geometry + smoothing on perfect perception |
| L2 | Our detector + tracker on CARLA | full pipeline vs exact truth |
| L3 | Real drone clips | end-to-end, hand-labelled events |

Target from the PRD: F1 ≥ 0.70 per violation type.

---

## 5. Key artefacts and where they live

| Artefact | Path |
|---|---|
| Detector weights | `ml/data/results/retrain_v1/train/weights/best.pt` (gitignored) |
| Base YOLO weights | `ml/models/*.pt` (gitignored) |
| Tracking GT clip | `ml/data/eval/gt_1080p/` |
| CARLA lane maps | `ml/violation_engine/configs/scenes/` |
| Real-site configs | `ml/violation_engine/configs/sites/` |
| Recorded flights + results | `ml/data/results/recorded_flight_validation/<flight>/` |
| Simulator | `CarlaAir-v0.1.7-Windows11-x86_64/` (gitignored) |

---

## 6. Planned (not built)

The plan is a backend (FastAPI + PostgreSQL/PostGIS + Redis + MinIO) that stores events, with a review workflow (flagged → needs_review → confirmed), a CesiumJS digital twin, reporting, and urban-planning analytics on top. Nothing downstream of the violation engine exists yet.
