# Phase 1 Presentation Package — Aerial Vehicle Detection, Tracking & Violations

Stage 5 deliverable of `docs/First_Phase_Plan.md`: demo assets, the benchmark numbers that justify each model / tracker / rule choice, and the limitations stated up front.

**Status (2026-10-04):**
- **Detection and tracking:** done, with ground-truth scores.
- **Violation engine:** built for 7 PRD violation types and validated offline, with the overlay video working.
- **Still missing:** F1 per violation type on a staged CARLA flight (Section 5).

---

## 1. Pipeline

```
Drone footage (CARLA recorded flight / real video)
   -> YOLO26l detector, retrained on VisDrone + UAVDT + CARLA      (car / bus / truck)
   -> TrackTrack tracker + offline tracklet stitching + post-processing
   -> trajectories_final.csv (pixels)
   -> ground positions in metres (camera pose)          ground_coords.py
   -> Kalman + RTS smoothing: speed, heading            kinematics.py
   -> lane map (directions, limits, lines, crossings)   lane_map.py
   -> 7 violation rules -> events                       rules.py
   -> overlay video                                     render_violations.py
```

Run on a recorded CARLA flight:

```
python ml/violation_engine/process_recorded_flight.py <run_id> --weights ml/data/results/retrain_v1/train/weights/best.pt --imgsz 960
python ml/violation_engine/run_violations.py simulation/data_export/recorded_flights/<run_id> --scene ml/violation_engine/configs/scenes/Town05.json
python ml/violation_engine/render_violations.py simulation/data_export/recorded_flights/<run_id> --scene ml/violation_engine/configs/scenes/Town05.json --events-only
```

## 2. Demo assets

Local only (`ml/data/results/` is gitignored); regenerate with the commands above.

| File | Shows |
|---|---|
| `ml/data/results/recorded_flight_validation/20261002_001635/tracktrack_ours/violations/violations_events.mp4` | **Violation overlay**, Town05 test flight: crosswalk outlines projected from the map, every car with ID and speed, an amber "checking" phase, then a red **ON ZEBRA CROSSING 11 s** flag (~t = 31–43 s) |
| `ml/data/results/recorded_flight_validation/20261002_001635/tracktrack_ours/annotated_final.mp4` | Final tracking on the same flight (5,611 frames) |
| `ml/data/results/experiments/gt_1080p/20261003_070721_tracktrack_ours/annotated_final.mp4` | Tracking on the hand-labelled GT clip with the retrained detector |
| `ml/data/results/presentation/20260920_194932/comparison.mp4` | ByteTrack (left) vs BoT-SORT + stitching (right), unique-ID counters ending at 147 vs 43: why the tracker changed |

## 3. Benchmarks

### 3.1 Detection

| Model | mAP50 | Note |
|---|---|---|
| COCO-pretrained YOLO26l, zero-shot | 0.034 | Not a real signal: COCO and VisDrone number their classes differently |
| Fine-tuned on VisDrone2019-DET, all 10 classes | 0.457 | First model |
| Same model, in-scope classes only | 0.585 | The relevant number at the time |
| **Retrained, 3 classes, VisDrone + UAVDT + CARLA (2026-10-03)** | see below | Current model |

Retrained vs old model, both scored on the same 3 classes (`ml/detection/compare_detectors.py`):

| Test set | Old | Retrained |
|---|---|---|
| VisDrone-DET val | 0.672 | **0.696** |
| VisDrone test-dev | 0.666 | 0.674 |
| UAVDT val (night / fog) | 0.366 | 0.426 |
| CARLA Town05 test (never trained on) | 0.655 | **0.844** |
| Hand-labelled GT clip | 0.506 | **0.702** (truck 0.116 → 0.536) |

Speed: about 85 FPS for the first model on an RTX 4060 Laptop GPU (11.8 ms/image at 640). The retrain uses the same architecture.

### 3.2 Tracking (hand-labelled GT clip, 40 vehicles)

| Pipeline | IDF1 | MOTA | ID switches | False tracks | IDs (GT 40) |
|---|---|---|---|---|---|
| BoT-SORT baseline (old detector) | 0.852 | 0.804 | 3 | 7 | 48 |
| TrackTrack, tuned (old detector) | 0.874 | 0.816 | 2 | 0 | 39 |
| **TrackTrack + retrained detector** | **0.881** | **0.826** | **2** | 1 | **40** |

The earlier move from ByteTrack to BoT-SORT cut unique IDs on a moving-camera flight from 147 to 43 (comparison video). TrackTrack then beat BoT-SORT on the ground-truth metrics. Evidence for every step is in `docs/main_project_tracker.md`.

### 3.3 Violation engine

Seven PRD violation types: no-parking, wrong-way, illegal U-turn, speeding, lane violation, zebra crossing, highway stopping. The rules follow PRD Section 9.1 thresholds. Design and research: `docs/Violation_Engine_Architecture.md`.

| Check | Result |
|---|---|
| Pixel → ground position vs CARLA truth (3 flights, 20,683 boxes) | median 0.11 m, 95% within 0.8 m |
| Speed error, full pipeline, Town05 test flight | median 1.4 km/h, 95% within 5.8 km/h |
| Heading error above 10 km/h | median 0.5°, 95% within 6.4° |
| Parked cars' measured speed | median 0.2 km/h (they must read "stopped") |
| Lane matching vs CARLA's own lane | 99.1% correct outside junctions |
| Unit tests (one per PRD case and edge case) | 23 / 23 pass |
| Staged-scenario dry run on the real Town05 map | 10 / 10 acts as expected (7 violations caught, 3 negatives left alone) |
| **Normal traffic, 3.5 min, full pipeline** | **0 false events**; 1 of 2 real crosswalk stops caught |

The rules were checked against CARLA's true vehicle positions, which separates rule bugs from perception errors. Five bugs found that way were fixed:
- a run-away smoother
- mirrored left/right in CARLA's world axes
- gap-filled rows treated as measurements
- ID switches read as speeding
- duplicate tracks splitting a parking timer

Together they took the false events on normal traffic **from 784 to 0**.

## 4. Limitations

**Detection**
- VisDrone mAP50 is **0.696 on 3 classes, below the PRD target of 0.75**.
- Recall on some vehicles is still low. The one crosswalk stop the engine missed was a car the tracker covered in only 40% of its visible frames.

**Tracking**
- GT scores come from **one** hand-labelled clip; a second clip with sudden camera moves is expected from the team.

**Violations**
- **F1 per violation type is not measured yet.** It needs the staged CARLA flight (planned and dry-run, not yet recorded). The PRD target is F1 ≥ 0.70 per type.
- Highway stopping has unit tests, but no staged act yet (needs a flight over a highway).
- Speeds are exact on CARLA, because the camera pose is known. **Real footage has no telemetry yet**, so real-video speeds will be estimates (scale from DJI telemetry, an orthophoto, or a known length) until Phase B.
- Red-light jumping is optional and CARLA-only. Helmet-less riding and overloading are out of scope: rider heads are a few pixels wide from 50–100 m, and motorcycles are not tracked.

## 5. What completes this package

- [x] Violation overlay video (`render_violations.py`).
- [ ] Staged CARLA flight with one or more of each violation type, and F1 per type (Architecture Phase A, steps A6–A8).
- [ ] Highway-stop flight.
- [ ] Real-footage demo with estimated speeds and hand-checked events (Phase B).
