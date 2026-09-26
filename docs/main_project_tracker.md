# Main Project Tracker

Living status tracker for the Autonomous Aerial Surveillance Framework, mapped against the PRD (`docs/Product_Requirements_Document.md`, v2.0.0), `docs/First_Phase_Plan.md`, and `docs/Second_Phase_plan.md`.

Last updated: 2026-09-26

---

## 1. PRD Objectives — Status Overview

| # | Objective | Status | Owner | Notes |
|---|---|---|---|---|
| 1 | Detect Violations in Traffic (YOLO + tracking + 10 violation types) | **In progress** | Afif (detection/tracking) + Hussain (violation rule logic) | See Section 2 |
| 2 | Digital Twin System (CesiumJS) | **Not started** | — | No frontend/twin work begun |
| 3 | Road Surface Anomaly Detection (pothole/crack/waterlogging/debris) | **In progress** | Afif | See Section 3 |
| 4 | Intelligent Urban Planning (DBSCAN + what-if simulator) | **Not started** | — | Depends on Objective 9 backend |
| 5 | Automated Analysis & Reporting | **Not started** | — | Depends on Objective 9 backend |
| 6 | Improved Road Safety / Infra Outcomes (status/traceability) | **Not started** | — | Depends on 1, 3, 9 |
| 7 | Geo-Spatial Coordinate Mapping (pixel → GPS) | **Partial** | — | Only exists via CARLA ground truth so far; no real-telemetry homography implementation |
| 8 | CARLA-Based Simulation & Validation | **In progress** | Afif | See Section 2.3 |
| 9 | Scalable Backend Infrastructure (FastAPI/PostGIS/Redis/MinIO) | **Not started** | — | No backend code yet |

---

## 2. Objective 1 — Vehicle Detection & Violation Logic

Source: `docs/First_Phase_Plan.md`

### 2.1 Stage Status

| Stage | Status | Detail |
|---|---|---|
| 1 — Confirm detection model | **Done** | Axis-aligned YOLO26l fine-tuned on full VisDrone2019-DET |
| 2 — Dataset selection | **Done** | VisDrone2019-DET (detection) + VisDrone2019-MOT-val (tracking) |
| 3 — Tracking layer | **ID-consistency work in progress (2026-09-26) — BoT-SORT-ReID adopted pending visual/ground-truth confirmation** | ByteTrack via Ultralytics `model.track()` — stable car/van/truck/bus IDs on real intersection footage; `motor` dropped (severe ID churn, out of scope for the 3 targeted violation types). ID switches observed on recorded-flight footage reopened this as active work; BoT-SORT-ReID now run head-to-head against ByteTrack on the same recorded flight — see `docs/Vehicle_Tracking_ID_Consistency_Plan.md` and Section 2.3 below |
| 4 — Violation logic (no-parking, wrong-way, speeding) | **In progress — assigned to Hussain** | Trajectory extraction done (`ml/violation_engine/extract_trajectories.py`, 8,584 rows on demo sequence); rule implementation itself pending |
| 5 — Presentation demo package | **Mostly done (2026-09-26) — violation-flag overlay pending Stage 4** | `docs/Phase1_Presentation_Package.md` (pipeline, detection + tracking benchmarks, limitations, demo asset list); `ml/violation_engine/make_demo_videos.py` builds `demo.mp4` (final pipeline + HUD) and `comparison.mp4` (ByteTrack vs BoT-SORT+stitching side by side, 147 vs 43 unique IDs) under `ml/data/results/presentation/<run_id>/`, skipping blank frames. Remaining: overlay Stage 4 violation flags once the rules exist |
| 6 — Simulation integration (CARLA + AirSim) | **In progress** | See 2.3 |

### 2.2 Detection Results (Stage 1)

Model: **YOLO26l**, fine-tuned on full VisDrone2019-DET train split (`ml/detection/train_full.py`; patience=20, batch=8, imgsz=640, workers=2, 10-hour cap).

| Class | mAP50 | mAP50-95 |
|---|---|---|
| car | 0.832 | 0.586 |
| bus | 0.600 | 0.438 |
| van | 0.481 | 0.338 |
| truck | 0.426 | 0.288 |
| motor | 0.542 | 0.254 |
| pedestrian | 0.540 | 0.258 |
| people | 0.416 | 0.170 |
| bicycle | 0.220 | 0.098 |
| tricycle | 0.333 | 0.187 |
| awning-tricycle | 0.177 | 0.114 |
| **All classes** | **0.457** | **0.273** |

Inference: 11.8ms/image (~85 FPS) on RTX 4060 Laptop GPU (8GB VRAM). Weights: `ml/data/results/full_train/train/weights/best.pt`.

**PRD success criterion (Section 5.3):** mAP ≥ 0.75 on aerial vehicle detection. Current all-class mAP50 = 0.457, below target.

**Scope-adjusted metric (2026-09-20):** the all-class average is misleading against this project's actual scope. Of VisDrone's 10 classes, only `car`, `van`, `truck`, `bus` are relevant to any of the PRD's 10 violation types — `motor` was already explicitly dropped from tracking in Stage 3 (severe ID churn, no violation type in Section 9.1 applies to motorcycles in this project's scope), and `pedestrian`/`people`/`bicycle`/`tricycle`/`awning-tricycle` were never part of the violation-detection use case at all. Re-aggregating over just the in-scope classes, using the same per-class numbers from the table above:

| Metric | car | van | truck | bus | **Scope-adjusted avg** |
|---|---|---|---|---|---|
| mAP50 | 0.832 | 0.481 | 0.426 | 0.600 | **0.585** |
| mAP50-95 | 0.586 | 0.338 | 0.288 | 0.438 | **0.413** |

Still below the PRD's 0.75 target, but a materially fairer number (0.585 vs. 0.457) and the correct one to track against Objective 1's success criterion going forward — the all-class figure conflates in-scope and out-of-scope classes. **Recommendation: use 0.585 (not 0.457) as this project's reported mAP against PRD Section 5.3, with the class-scoping rationale stated explicitly in any report/presentation.** Truck (0.426) and van (0.481) remain the weakest in-scope classes and are the primary lever for closing the remaining gap to 0.75 — see Next Actions.

#### mAP gap — near-term vs. advanced solutions

The near-term levers already queued (Next Actions #2 — drop irrelevant classes from training, check truck/van class balance, review `results.csv` for early stopping) are cheap and should be tried first. If they aren't enough to close 0.585 → 0.75, the following are the advanced options, roughly in ascending order of effort/risk:

- **Tiling / slicing inference (SAHI or similar).** VisDrone's truck/van instances are often small and distant in a full 1080p-class aerial frame — running inference on overlapping crops (tiles) instead of the full downsampled frame is a well-documented way to recover small/occluded-object recall in aerial detection without retraining the model architecture. Likely the single highest-leverage option here since it directly targets small-object recall, which is VisDrone's known weak point.
- **Higher training resolution.** Current training used `imgsz=640`; bumping to 960/1280 preserves more pixels-per-object for small trucks/vans at the cost of slower training/inference and more VRAM (8GB card is already tight — would likely need a smaller batch size to compensate).
- **Synthetic data augmentation via CARLA.** Once the CARLA pipeline (Objective 8) is validated, scripted scenarios with more truck/van density than VisDrone's real-world distribution could rebalance the training set directly rather than just reweighting the existing one.
- **Copy-paste / mosaic augmentation targeted at weak classes.** Oversample truck/van instances specifically (paste extra truck/van crops into training images) rather than blanket mosaic augmentation, which doesn't fix class imbalance on its own.
- **Ensemble or two-stage detection.** Run a class-specialized second pass (or a larger model) only on regions flagged as low-confidence truck/van, rather than upgrading the whole pipeline to a larger backbone — cheaper than a full YOLO26x retrain and avoids the latency hit on the classes already performing well (car).
- **Larger backbone (YOLO26x) — last resort.** Flagged in the original analysis as questionable payoff: VisDrone's difficulty is dominated by small-object/occlusion characteristics, not model capacity, so a bigger backbone is unlikely to move truck/van much on its own and costs real inference speed (currently 85 FPS). Only worth trying after tiling/resolution changes have been exhausted.

None of these have been started; they're documented here as the fallback path if the near-term levers plateau below 0.75.

Zero-shot baseline note: COCO-pretrained `yolo26l.pt` gave an unusable raw mAP (0.034) purely due to class-index mismatch between COCO and VisDrone ordering — qualitatively correct detections, not a real signal. Smoke test (25 images) confirmed real learning signal (mAP50=0.121) before committing to full training.

### 2.3 CARLA + AirSim Simulation Integration (Objective 8)

Status: **in progress** — nadir-fix re-run complete (2026-09-20); a real, confirmed domain-gap problem found in tracking continuity.

- `simulation/carla_scripts/fly_and_capture.py` — autonomous waypoint flight over CARLA traffic + aerial footage capture.
- `simulation/carla_scripts/live_fly_and_track.py` — manual keyboard-controlled flight with live detection/tracking display.
- `ml/violation_engine/run_sim_validation.py` — runs Stage 1/3 detector+tracker against captured footage, exports trajectory CSV + annotated video.
- **First end-to-end run (2026-08-08, Town10HD, 180 frames):** pipeline ran without errors but results were poor (52/180 frames had any detection, longest surviving track ID = 22 frames). Root cause: AirSim camera was never set to nadir pose — camera looked at the horizon, not down at the road.
- **Fixes applied (same day):** nadir camera pose (`simSetCameraPose`, pitch −90°); real road-following flight path via CARLA Waypoint API (`generate_road_path()`) instead of guessed coordinates; detection now reads raw `frames/*.jpg` instead of re-encoded `flight.mp4` to avoid double compression artifacts; capture rate 10→20fps; capture resolution 1280×960→1920×1080.

#### Re-run results (2026-09-20, Town10HD, run `20260920_125938`)

Setup: 14/15 traffic vehicles spawned, drone flew all 8 road-following waypoints at 45m altitude, 128 frames captured (nadir camera confirmed working — unlike the first run, footage actually looks down at the road).

**Detection (per-frame, no tracking):** roughly half the frames (~63–69/128, both inference passes agree) returned zero detections; the other half returned plausible car/van/truck counts (e.g. frame 82: "6 cars, 5 vans, 3 trucks" at 0.5–0.9 confidence) — so the detector does recognize VisDrone-style vehicle classes in CARLA's render style, but not consistently frame-to-frame. Detections were sparse and scattered for the first ~80 frames (single isolated hits with long gaps), then became dense and continuous from frame ~78 onward.

**Tracking (ByteTrack via `model.track()`):** severe failure. `trajectories.csv` has only **18 rows total** across the whole 128-frame flight, and no track ID appears in more than 3 consecutive frames — track IDs churn rapidly (178 → 319 → 338 → 395 → 400 → 402 → 414 → 418 → 427 → 432 → 438, i.e. ~11 unique IDs assigned for what should be a handful of continuously-visible vehicles). No track ID at all was formed for the first ~100 frames, despite the detector firing sporadically in that range — ByteTrack's continuity logic never got a long enough unbroken detection run to confirm a track until the dense-detection stretch starting ~frame 100.

**Initial conclusion (superseded below) — domain gap.** First read of the numbers alone (frame-by-frame detection log, no visual check) suggested a detector domain-gap problem. That read was wrong and has been corrected after actually looking at the frames (see below) — flagging this here rather than silently rewriting history.

**Corrected root cause (2026-09-20, after visual inspection of annotated frames) — bad flight path, not a domain gap.** Manually inspecting the raw/annotated frames (`ml/data/results/sim_validation/20260920_125938/annotated/00000.jpg`, `00010.jpg`, `00030.jpg`) shows frames 0 and 10 are **entirely beach and promenade — no road, no vehicles, nothing in view to detect**, and frame 30 has only a thin sliver of road at the frame edge (one vehicle visible there, uncaught — plausibly just an edge/size issue, not a recognition failure). By contrast, frame 82 — over an actual intersection — correctly detected and boxed 7 vehicles (cars/vans/trucks) with good confidence. So the detector works fine when a road with vehicles is actually in frame; the ~50% "no detections" frames are largely the drone's auto-generated path (`generate_road_path()`, random spawn point + CARLA waypoint walk) sending it along a route that runs beside Town10HD's beachfront for a long stretch, where the nadir camera looks down at sand and palm trees instead of a road. This is a **flight-path / data-collection problem, not a detector or tracker problem** — the ByteTrack-tuning and confidence-gate experiments above were testing the wrong layer, which is exactly why neither changed anything (there was no vehicle signal to recover in those frames in the first place). Tracking continuity in the frames that do have vehicles (e.g. the frame 82 stretch) still showed some churn and is worth re-examining once the flight path is fixed and produces a longer continuous stretch of real traffic — the earlier ByteTrack-tuning shouldn't be assumed to be the final word on tracking quality either, since it was evaluated on a mostly-empty flight.

Raw outputs: `simulation/data_export/sim_flights/20260920_125938/` (frames + flight.mp4), `ml/data/results/sim_validation/20260920_125938/` (trajectories.csv + annotated video).

- Live mode had a separate AirSim RPC stability bug (screenshot RPC stalls once the drone is airborne — traced to `msgpackrpc` not being thread-safe). Fixed by switching live mode to a native CARLA `sensor.camera.rgb` feed instead of AirSim's screenshot RPC. **Not yet manually re-tested with this change.**

#### BoT-SORT-ReID vs. ByteTrack head-to-head (2026-09-26)

Per `docs/Vehicle_Tracking_ID_Consistency_Plan.md` Phase 2, and a deliberate decision to skip Phase 1's ground-truth clip for now (see that doc's Section 6) and go straight to comparing trackers by eye/aggregate counts. Added `ml/violation_engine/botsort_sim.yaml` (BoT-SORT, `with_reid: true`, `gmc_method: sparseOptFlow`, same relaxed thresholds as `bytetrack_sim.yaml`) and a `--tracker {bytetrack,botsort}` flag on `process_recorded_flight.py` and `run_sim_validation.py`.

Ran both on the same real recorded flight (`simulation/data_export/recorded_flights/20260920_194932/`, 899 frames):

| Metric | ByteTrack | BoT-SORT-ReID |
|---|---|---|
| Trajectory rows | 853 | 1175 |
| Unique track IDs | 147 | 54 |

BoT-SORT tracked more boxes overall while producing far fewer unique IDs — the expected direction if the problem is ID switches (continuous detections consolidating onto fewer, more stable identities instead of fragmenting into new ones each time). Caveat: the run log showed repeated `WARNING not enough matching points` from the `sparseOptFlow` GMC step on a stretch of frames (likely low-texture/sky) — GMC was degraded there, so BoT-SORT's gain is probably coming from the ReID appearance model, not camera-motion compensation, for that stretch. Also, **without a ground-truth clip, 54 is not a proven-correct ID count** — it's consistent with fixed ID switches, but could also hide a wrongful merge (two different vehicles sharing one ID), which raw counts alone can't distinguish from a true fix.

Outputs: `ml/data/results/recorded_flight_validation/20260920_194932/bytetrack/` and `.../botsort/` (each has `trajectories.csv` + `annotated.mp4`).

**Decision: adopt BoT-SORT-ReID (`--tracker botsort`) as the default going forward for recorded-flight/sim validation**, on the strength of the aggregate numbers, while flagging the result as provisional until either (a) the two `annotated.mp4` outputs are visually spot-checked side by side for merges/switches, or (b) Phase 1's ground-truth clip is eventually produced and `eval_tracking.py` is run for a real MOTA/IDF1/ID-switch number. **Visual spot-check done by the team (2026-09-26): BoT-SORT output looks good, confirmed as the tracker going forward** — but IDs still get reassigned during sudden camera movement (next subsection).

#### ID switches during sudden camera movement (2026-09-26)

**Recording finding first:** 321 of the 899 frames in `20260920_194932` are blank grey with no scene in view (frames 1–44 at the start, and 621–898 after the drone descends onto/through the road around frame 615–620). Only frames ~45–620 contain real footage. Any whole-clip metric on this recording is diluted by 36% empty frames.

**GMC (camera-motion compensation) ruled out.** First hypothesis was that `sparseOptFlow` GMC fails on large inter-frame motion, so the config was switched to `orb`. That was wrong: a per-method diagnostic showed `sparseOptFlow` failed on exactly the 321 blank frames and **zero** real frames; `orb` failed on 324 (it crashes inside OpenCV when a frame has too few keypoints, and BoT-SORT silently falls back to no compensation); `sift` 279 failures at ~18× the cost (559 ms/frame). Reverted to `sparseOptFlow`.

**Actual mechanism (measured on frames 65–610, `botsort_sparseoptflow` output):** sudden-move windows (top-10% inter-frame camera shift, up to ~290 px/frame at 15.4 fps, plus 3 frames after) cover 18% of frames but account for 37% of new track IDs, and tracked boxes/frame drop from 2.38 to 1.11 inside them. The detector loses vehicles during the move (motion blur and/or vehicles leaving frame), and when a vehicle is re-detected Ultralytics BoT-SORT refuses to even consult ReID unless its box already overlaps the Kalman prediction by IoU ≥ `proximity_thresh` (`bot_sort.py`, `get_dists`) — so appearance can't re-link it and a new ID is issued.

**Config change + result:** `botsort_sim.yaml` now uses `proximity_thresh` 0.5→0.2 and `track_buffer` 60→90 (GMC back on `sparseOptFlow`). Whole-flight unique IDs 54→48; IDs born in sudden-move windows 20→17. A real but modest gain — the dominant cause (detections dropping during the move) isn't something tracker config can fix. Outputs: `.../20260920_194932/botsort/` (current), `botsort_sparseoptflow/` (the version reviewed), `botsort_orb/` (discarded experiment).

Remaining levers, in order of expected payoff: (1) offline tracklet stitching in `process_recorded_flight.py` — since recorded flights are processed offline, link a track that ends to one that starts a few frames later using camera-motion-compensated position + class + appearance, with no IoU requirement; (2) reduce the cause at capture — cap yaw/translation rate in `record_flight.py`'s keyboard control and fly more smoothly; (3) trim/skip blank frames (and fix whatever sends the drone through the road at the end of the flight).

#### Offline tracklet stitching (2026-09-26) — lever (1) implemented

`ml/violation_engine/stitch_tracklets.py`, run automatically at the end of `process_recorded_flight.py` (skip with `--no-stitch`; also runnable standalone on an existing result folder). Links a track that ends to one that starts ≤ 90 frames later when: the old track's last position, carried forward through per-frame camera motion (Ultralytics' `sparseOptFlow` GMC) plus the vehicle's own recent velocity, lands within `1.5×box size + 5 px/frame of gap` (cap 400 px) of the new track's first box; box size ratio 0.6–1.67; same class (car↔van allowed, the detector flickers between them); HSV colour-histogram distance ≤ 0.4; combined cost ≤ 1.0. Greedy lowest-cost matching, one predecessor/successor per track (chains allowed). Writes `trajectories_stitched.csv` (same schema, `track_id` rewritten to the first ID of each chain), `stitch_links.json` (every link with its gap/distance/appearance scores, for auditing), and `annotated_stitched.mp4`. Raw `trajectories.csv` is untouched, so nothing downstream changes unless it opts in to the stitched file.

- **Colour threshold calibrated on this flight** rather than guessed: same-vehicle start-vs-end crops median distance 0.30, co-visible (necessarily different) vehicles median 0.64; 0.4 keeps 73% of same-vehicle pairs and admits 10% of different-vehicle pairs before the position gate.
- **Every link inspected visually** (crop of old track's last box next to the new track's first box). First pass produced 6 links; 5 were clearly the same vehicle, 1 (id71→id108: 72-frame gap, old vehicle half cut off at the frame edge, distance 282/400 px) could not be confirmed — it was also the only link with cost > 1 (1.36 vs 0.31–0.71). Added the `max_cost` 1.0 cut-off, since a wrong merge (two vehicles sharing one ID) is worse for speed/dwell-time rules than a missed link. Final: **5 links, unique IDs 48 → 43**, all 5 visually confirmed.
- **Correction to the "IDs born near sudden moves" metric used above.** Stitching didn't reduce it (17 → 17), so each of those 17 births was checked for the best possible predecessor: almost all have none plausible (nearest ended track predicted 1,800–2,700 px away, outside the frame, and colour/size/class also mismatch). Visually confirmed at frames 330 vs 346: a fast ~90° yaw turn swings the camera from an empty street onto a different, busy intersection — the six IDs born at frames 345–347 are **genuinely new vehicles entering view**, correctly given new IDs, and they then keep those IDs for 38–99 frames. So the metric over-counted switches: a sudden pan brings new road (and new vehicles) into frame, which looks the same as an ID switch in raw counts. The real sudden-move ID switches found on this flight are the short-gap ones stitching now repairs.
- Also switched the `--tracker` default in `process_recorded_flight.py` and `run_sim_validation.py` from `bytetrack` to `botsort`, matching the team's decision.

#### Real-footage test and the duplicate-box bug (2026-09-26)

Tested the pipeline on real drone footage: `Urban Motion from the Sky Roundabout Traffic Drone Video.mp4` (1280×720, 30 fps, 8,749 frames, snowy roundabout, moving/rotating camera, ~36 small vehicles in view). New script `ml/violation_engine/process_video.py` runs the same tracking + stitching on any video file (tracking loop refactored into `run_tracking()` in `process_recorded_flight.py`; `stitch_tracklets.py` now reads frames from a video file or a frame folder, and its video labels scale with frame width and include a HUD).

- **Detector input size:** 1280 instead of 640 for this 720p video — finds ~16% more vehicles, the extra boxes visually confirmed as real (e.g. a yellow car and a silver car missed at 640), ~43 ms/frame.
- **Severe fragmentation found:** a 5 s clip gave 184 unique IDs for ~36 vehicles, and the first full-video run gave 3,730 IDs with 2,308 stitching merges — discarded.
- **Root cause: duplicate boxes, not the tracker.** YOLO26's NMS-free head returns ~10 overlapping box pairs per frame (~25% of all boxes), 94% of them the same vehicle under two classes (e.g. car + van). The tracker matches one and starts a short-lived new ID from the other. Stock ByteTrack and stock BoT-SORT fragment equally (~100 IDs on the clip), which is how config tuning was ruled out. Ultralytics' `agnostic_nms=True` and `end2end=False` have no effect on this model's output.
- **Fix:** class-agnostic NMS (IoU 0.5, keep highest confidence) between detector and tracker, `dedupe_boxes()` callback in `run_tracking()` — applies to CARLA and real footage alike. Roundabout clip: 113 → 45 IDs, tracks ≤ 10 frames 60 → 6, and stitching then needs 0 merges (vs 92). CARLA flight `20260920_194932`: 48 → 37 IDs, tracks ≤ 10 frames 20 → 11, same coverage.
- **Measurement mistake caught and corrected:** an intermediate conclusion ("stock thresholds fix it, 184 → 27 IDs", which led to a separate `botsort_real.yaml`) came from running several trackers in one Python process — only the first run in each process was valid, later runs inherited state. Re-measured with one fresh process per config; `botsort_real.yaml` was removed since de-duplication makes one config work on both sources.
- **Knock-on:** every CARLA tracking number earlier in this section and in `docs/Phase1_Presentation_Package.md` (48 / 43 IDs, the 5 stitching links, the demo videos) was produced **without** de-duplication and needs re-running.

#### Cheap-fix attempts (2026-09-20) — both ruled out

Tried, on the same captured footage (run `20260920_125938`, no CARLA re-launch needed):

1. **Loosened ByteTrack association** (`ml/violation_engine/bytetrack_sim.yaml`: `track_high_thresh` 0.25→0.15, `track_low_thresh` 0.1→0.05, `new_track_thresh` 0.25→0.15, `track_buffer` 30→60, `match_thresh` 0.8→0.6). Result: **slightly worse** — 16 trajectory rows (was 18), 13 unique track IDs (was 11).
2. **Lowered the detection confidence gate** (`model.track(..., conf=0.1)`, down from Ultralytics' ~0.25 default), stacked on top of fix 1. Result: **zero change** — identical 16 rows, 13 track IDs, and identical 132 "no detections" frame count to the very first run.

**Conclusion: this isn't a thresholding problem — confirmed correct, but for a different reason than first assumed.** Fix 2 having exactly zero effect rules out "detections exist but get filtered below threshold." At the time this was read as evidence of a detector domain gap; visual inspection (see the corrected root-cause note above) instead showed the "no detections" frames mostly have **no vehicles in view at all** (drone flying over a beach/promenade), so there was never a detection to threshold away. Tracker/confidence tuning was the wrong lever from the start here.

**`generate_road_path()` anchor-to-vehicle-cluster fix attempted and also failed (2026-09-20).** A fix was written (uncommitted, `fly_and_capture.py`) that spawns traffic first and anchors the path to whichever spawned vehicle has the most neighbors within 80m, instead of a purely random spawn point. Re-captured (`simulation/data_export/sim_flights/20260920_150205/`, 183 frames) and validated: **zero detections across all 183 frames** — worse than the original bug. Visually inspected frames 0/89/182 directly — every one is still beach waves or the beach-resort promenade with palm trees, no road or vehicle in view at any point in the flight. Root cause: the heuristic measures spawn-point/vehicle density, not proximity to an actual trafficked road network — Town10HD apparently has a cluster of `Driving`-type spawn points right in the beach-resort district, so "most nearby vehicles" can still land you there. This is a map-topology problem the heuristic doesn't account for (junction proximity, road classification), not a bug fixable by tuning the neighbor radius.

**Decision (2026-09-20): stop debugging the auto-path heuristic, use a manually-recorded flight path instead.** `fly_and_capture.py --waypoints-file <path>` already supports replaying a hand-flown route recorded once via the CarlaAir distribution's `examples\fly_drone_keyboard.py` + `examples_record_demo\record_drone.py` (see `simulation/README.md`, "Custom flight paths" section) — this sidesteps the anchoring bug entirely since a human picks the route, keeps the existing sim-env/main-env pipeline split intact (capture in the CarlaAir conda env, detect+track afterward in the main venv via `run_sim_validation.py`), and needs no new dependencies. Chosen over fixing `generate_road_path()`'s junction logic (higher effort, CARLA-map-specific, and not needed once a human verifies the route once).
- **Next action:** fly a manual route over a real Town10HD intersection with traffic, record it, then re-run `fly_and_capture.py --waypoints-file ...` and `run_sim_validation.py` to re-evaluate tracking continuity on a flight that's actually over traffic for its full duration. (The ByteTrack tuning done earlier in this section was evaluated on a mostly-empty flight and shouldn't be treated as conclusive either way — this re-run is also the first fair test of tracking quality.)
- `generate_road_path()`'s auto-path heuristic is left as-is/unused for now, not deleted — revisit only if fully automated (no manual flight step) capture becomes a requirement later.

### 2.4 Scope Gap

PRD Section 9.1 defines **10 violation types**. `First_Phase_Plan.md` only scopes 3 for Phase 1:
- No-parking zone violation
- Wrong-way driving
- Speeding

**Not yet scoped into any phase plan:**
- Illegal U-turn
- Red-light jumping
- Lane violation
- Zebra crossing violation
- Helmet-less riding (needs rider head-crop + helmet classifier, separate model)
- Two-wheeler overloading (needs passenger-count model)
- Illegal stopping on highway/flyover

---

## 3. Objective 3 — Road Surface Anomaly Detection (Pothole Track)

Source: `docs/Second_Phase_plan.md`

Base model: YOLO26l-seg. Architecture mods target: DSConv + SimAM + GELU (from arXiv 2505.04207). Dataset: PothRGBD (1,000 RGB-D images, YOLO-seg format), split 800/100/100.

| Phase | Status | Detail |
|---|---|---|
| 1 — Environment & baseline setup | **Done** | Stock YOLO26l-seg fine-tuned on PothRGBD (RGB only) |
| 2 — Architecture modification (DSConv+SimAM+GELU) | **In progress** | Modules coded (`ml/pothole/modules.py`: `DSConv`, `SimAM`, `C3k2WithSimAM`); model-surgery script (`modify_model.py`) to actually splice them into YOLO26l-seg's backbone/neck graph **not yet created**; no modified model trained |
| 3 — Training on PothRGBD (modified model) | **Not started** | Depends on Phase 2 |
| 4 — Domain gap check (aerial transfer) | **Not started** | |
| 5 — CARLA synthetic data generation | **Not started** | |
| 6 — CARLA integration (pothole pipeline) | **Not started** | |
| 7 — Edge case handling (waterlogging overlap, shadow filtering, GPS dedup) | **Not started** | |
| 8 — Validation & reporting | **Not started** | |

### 3.1 Baseline Results (Phase 1)

`ml/pothole/runs/baseline/`, single class `pothole`, imgsz=640, batch=4. Training ran 71 epochs (early stop, patience=20, no improvement after epoch 51). Best checkpoint = epoch 51.

| Metric | Box | Mask |
|---|---|---|
| Precision | 0.936 | 0.956 |
| Recall | 0.838 | 0.856 |
| mAP@50 | 0.923 | 0.927 |
| mAP@50-95 | 0.605 | 0.635 |

**PRD success criterion (Section 5.3):** Detection F1 ≥ 0.65 per anomaly category — **baseline already clears this comfortably** for the pothole class (only class trained so far; crack/waterlogging/debris not yet trained).

Reference paper (arXiv 2505.04207, different base model — YOLOv8, not directly comparable): 93.7% precision / 90.4% recall / 93.8% mAP@50.

### 3.2 Immediate Blocker

`modify_model.py` (model-surgery script) does not exist yet. Until it's written and DSConv/SimAM are actually spliced into the backbone/neck, Phases 3–8 cannot start.

---

## 4. Not-Started Pillars (Objectives 2, 4, 5, 9)

No work has begun on:
- **Digital Twin** (CesiumJS 3D visualization, OSM buildings, live vehicle markers, scenario builder)
- **Backend infrastructure** (FastAPI, PostgreSQL+PostGIS, Redis, MinIO, WebSocket fan-out, Celery)
- **Urban Planning Engine** (DBSCAN hotspot clustering, recommendation rule engine, what-if simulator)
- **Automated Reporting** (PDF/Excel/GeoJSON report generation, trend dashboards)

These are all downstream of Objective 9 (backend) — nothing here can start meaningfully until at least a minimal FastAPI + PostGIS layer exists to persist events.

---

## 5. Next Actions (Working Queue)

1. ~~Redefine Objective 1's detection mAP metric to the actual in-scope classes (car/van/truck/bus) instead of all 10 VisDrone classes, and re-report against the PRD's 0.75 target.~~ **Done (2026-09-20)** — scope-adjusted mAP50 = 0.585 (see Section 2.2). Still short of 0.75; truck/van are the weakest links.
2. **TODO — Retrain detector on a filtered 4-class (car/van/truck/bus) VisDrone set.** Not yet started.
   - **Why needed:** investigated the 0.585 mAP50 shortfall (2026-09-20) and found two concrete, fixable causes instead of a fundamental model-capacity problem:
     - `train_full.py` trains with `classes: null` (confirmed in `args.yaml`) — all 10 VisDrone classes are trained, including pedestrian (79,337 instances), motor (29,647), and people (27,059), each of which has *more* instances than van+truck+bus combined (24,956+12,875+5,926=43,757). Classification loss and the output head are splitting capacity across 6 classes this project doesn't use for any of the 10 PRD violation types.
     - Real class imbalance confirmed within the in-scope classes too: car (144,867 instances) outnumbers truck 11:1 and bus 24:1 in the VisDrone train split — consistent with truck (0.426) and bus (0.600) being the weakest in-scope mAP50 scores.
     - Reviewed `ml/data/results/full_train/train/results.csv`: best mAP50 (0.4569 all-class) was actually epoch 74, not the final logged epoch 80 — training continued 6 more epochs without improving, and the run stopped at epoch 80 despite **neither** configured stop condition being met (`patience=20` needs 20 epochs without improvement, only 6 had elapsed; `time=10` hour cap wasn't reached, epoch 80 landed at ~7.3h). Loss curves were still declining steadily with no plateau — the run was likely interrupted manually or crashed, not stopped by its own logic, and appears to have left real headroom on the table.
   - **Plan:** (a) build a filtered VisDrone copy with labels remapped to just car/van/truck/bus (ids 0–3) plus a matching `data.yaml`, without mutating the original dataset; (b) retrain with the same recipe (patience=20, time=10h, imgsz=640) on the filtered set, letting it actually reach a real stop condition this time; (c) re-evaluate mAP50 on car/van/truck/bus against the current 0.585 baseline.
   - **How to apply:** if this closes most of the gap to 0.75, the advanced options in Section 2.2 (tiling/SAHI, higher resolution, synthetic CARLA data) become unnecessary for now; if it doesn't, they're the fallback path.
3. ~~Re-run the CARLA/AirSim capture with the nadir-camera fix applied; evaluate detection/tracking quality on synthetic footage to close the Objective 8 domain-gap question.~~ **Done, root cause corrected (2026-09-20).** Initial read (detection log counts only) suggested a detector domain gap; ByteTrack tuning and confidence-gate changes were tried and had no effect. Visual inspection of the actual frames then showed the real cause: the auto-generated flight path drove the drone over Town10HD's beach/promenade for a large stretch, where there are no vehicles in view at all — not a model or tracker failure.
   **Superseded (2026-09-20):** the planned `--waypoints-file` replay approach (record a manual route, then have the drone re-fly it on autopilot) was replaced with a simpler, more reliable design — record the manual flight's actual footage directly, no replay step at all. New scripts: `simulation/carla_scripts/record_flight.py` (manual keyboard flight, CARLA-native nadir camera, no detection running during capture so zero frames are ever skipped) and `ml/violation_engine/process_recorded_flight.py` (runs detection+tracking on every recorded frame in one streaming pass, outputs `trajectories.csv` + a real annotated video). First real (non-beach) manual recording done successfully — detection confirmed working; tracking showed ID switches, which reopened Stage 3 (tracking layer) as active work — see item 7 below.
4. Write `ml/pothole/modify_model.py` to integrate DSConv + SimAM into YOLO26l-seg's backbone/neck, then train the modified model (Phase 2→3 of the pothole track).
5. Decide scope: which of the remaining 7 violation types (beyond no-parking/wrong-way/speeding) get built for this milestone vs. deferred.
6. ~~Presentation package for Objective 1 (Stage 5).~~ **Mostly done (2026-09-26)** — see `docs/Phase1_Presentation_Package.md`. Remaining: overlay violation flags once Stage 4 exists; refresh the numbers if the 4-class retrain (item 2) lands first.
7. **Vehicle-tracking ID consistency — BoT-SORT-ReID adopted (2026-09-26), visual spot-check still pending.** Researched current tracker options and UAV-specific state of the art; full writeup and phased plan in `docs/Vehicle_Tracking_ID_Consistency_Plan.md`. **Decision made to skip Phase 1's ground-truth-metric prerequisite and go straight to Phase 2** (see item 8 below for why that's flagged as a risk, not a completed step): added `ml/violation_engine/botsort_sim.yaml` (BoT-SORT, `with_reid: true`, `gmc_method: sparseOptFlow`) and a `--tracker {bytetrack,botsort}` flag on `process_recorded_flight.py`/`run_sim_validation.py`. Ran head-to-head on the same real recorded flight (`20260920_194932`, 899 frames): ByteTrack = 853 rows/147 unique IDs, BoT-SORT = 1175 rows/54 unique IDs — direction consistent with fewer ID switches, but not proof (could hide a wrongful merge). Visual spot-check done by the team — BoT-SORT confirmed. **Open issue: IDs reassigned during sudden camera movement** — root-caused and partly mitigated (unique IDs 54→48, see Section 2.3). Offline tracklet stitching **done** (unique IDs 48→43, 5 visually confirmed links; see Section 2.3). **Next steps:** (a) team spot-check of `botsort/annotated_stitched.mp4`; (b) validate stitching on a second, cleaner recording (no blank frames) before relying on its thresholds — they were calibrated on this one flight; (c) smoother capture in `record_flight.py` (cap yaw/translation rate) to reduce detection dropouts at the source; (d) decide whether Hussain's violation logic should read `trajectories_stitched.csv` instead of `trajectories.csv`. Phase 3 (research-grade UAV trackers, e.g. DroneMOT) only if these aren't enough. **Owner: Afif.**
8. **Deferred (not abandoned) — write a hand-labeled ground-truth clip for `ml/violation_engine/eval_tracking.py`.** `eval_tracking.py` itself is already written (ID-switch count / MOTA / IDF1 via `motmetrics`) but has never been run — there is no ground-truth file for it to score against yet. **Why this still matters even after adopting BoT-SORT in item 7:** the ByteTrack-vs-BoT-SORT comparison above used raw row/ID counts, which can't distinguish "ID switches actually fixed" from "two different vehicles wrongly merged into one ID" — both would show up as "fewer unique IDs." This is the same class of guess this project already got burned by once (untuned ByteTrack threshold experiments judged against unverified beach-flight footage, Section 2.3) — lower risk this time since BoT-SORT's numbers point the expected direction, but still not a confirmed number. **What it involves:** (a) pick a short (~10–15s) real recorded-flight clip with visible traffic; (b) hand-label it frame-by-frame (e.g. via CVAT) — manually draw a box per vehicle per frame and assign it a fixed ID, giving a ground-truth file in the same shape as `trajectories.csv`; (c) run `eval_tracking.py` against both ByteTrack's and BoT-SORT's `trajectories.csv` for that clip to get an actual ID-switch/MOTA/IDF1 number instead of an eyeballed or aggregate-count comparison. **Owner: Afif** — the hand-labeling step specifically needs a human watching the clip and can't be automated.

---

## 6. Change Log

| Date | Change |
|---|---|
| 2026-09-20 | Tracker created; captured current state of Objectives 1, 3, 7, 8 from `First_Phase_Plan.md` and `Second_Phase_plan.md`; flagged violation-rule-logic (Objective 1 Stage 4) as owned by Hussain, not Afif |
| 2026-09-20 | Redefined Objective 1's mAP metric to in-scope classes only (car/van/truck/bus): scope-adjusted mAP50 = 0.585 (mAP50-95 = 0.413), vs. previous all-class figure of 0.457. Still below PRD's 0.75 target; truck/van flagged as the primary levers to close the gap |
| 2026-09-20 | Re-ran CARLA/AirSim capture with nadir-camera fix (run `20260920_125938`, Town10HD, 128 frames). Detection transfers to CARLA's render style reasonably well; ByteTrack tracking does not — only 18 trajectory rows total, no track survives more than 3 consecutive frames. Domain gap (Objective 8) confirmed real and localized to tracking continuity, not raw detection |
| 2026-09-20 | Tried loosening ByteTrack thresholds and lowering the detection confidence gate — both had zero/negative effect. Initially misread as proof of a detector domain gap |
| 2026-09-20 | **Correction:** visually inspected the actual frames (user caught that raw/annotated frames should be checked directly) and found the real cause — the auto-generated flight path sent the drone over a beach/promenade for a large stretch of the flight, where no vehicles exist in frame at all. Not a detector or tracker failure. Root cause is now the flight-path generation logic (`generate_road_path()`), not the model |
| 2026-09-20 | Added TODO (Section 5, item 2) to retrain the detector on a filtered 4-class (car/van/truck/bus) VisDrone set. Root-caused the mAP shortfall: `classes: null` trains 6 irrelevant classes (pedestrian/motor/people alone outnumber van+truck+bus combined); confirmed real 11:1 (car:truck) and 24:1 (car:bus) imbalance within in-scope classes; and found the full training run stopped at epoch 80 without either configured stop condition (patience or time cap) actually being met, leaving likely headroom on the table |
| 2026-09-20 | Tried an anchor-to-vehicle-cluster fix for `generate_road_path()` (anchor to the spawned vehicle with the most neighbors within 80m, instead of a random spawn point). Re-captured and validated (`20260920_150205`, 183 frames): zero detections, still 100% beach/promenade on visual inspection — the fix failed because spawn-point density on Town10HD doesn't correlate with real trafficked roads. Decided to stop debugging the auto-path heuristic and instead hand-fly + record a verified route via the already-supported `--waypoints-file` flow; added as a TODO under Objective 1's Next Actions item 3, owned by Afif for the next CARLA session |
| 2026-09-20 | Superseded the `--waypoints-file` replay plan with a simpler design: record the manual flight's actual footage directly instead of replaying a recorded route on autopilot. Added `simulation/carla_scripts/record_flight.py` (manual flight, CARLA-native nadir camera, no live detection so zero frames are skipped) and `ml/violation_engine/process_recorded_flight.py` (detect+track every recorded frame in one pass, output trajectories.csv + a real annotated video, since Ultralytics only saves annotated images — not a video — for a frame-folder source) |
| 2026-09-20 | First real (non-beach) manual flight recorded and processed successfully — detection confirmed working on real traffic. Tracking (ByteTrack) showed ID switches (same physical vehicle assigned a new track ID mid-flight), reopening Objective 1 Stage 3 (tracking layer) as active work |
| 2026-09-20 | Researched multi-object tracking approaches (production trackers: ByteTrack/BoT-SORT/OC-SORT; UAV-specific state of the art: DroneMOT, AMOT, STCMOT) and wrote a phased implementation plan — `docs/Vehicle_Tracking_ID_Consistency_Plan.md`. Recommendation: add a real tracking-quality metric first (ID-switch count/MOTA/IDF1 via `motmetrics`, since eyeballing video isn't reliable enough to compare trackers), then switch to BoT-SORT-ReID (appearance embedding + camera-motion compensation, same `ultralytics` package, no new dependencies) before considering heavier research-grade UAV trackers. Added as Next Actions item 7, owned by Afif |
| 2026-09-20 | Added Next Actions item 8: write `ml/violation_engine/eval_tracking.py` + hand-label a short ground-truth clip (Phase 1 of item 7's plan). Called out explicitly because it's a prerequisite, not optional — without a hand-verified ground truth to score against, any before/after comparison between ByteTrack and BoT-SORT would be another eyeballed guess, repeating the same mistake already made once with untuned threshold experiments on unverified footage (Section 2.3) |
| 2026-09-26 | `ml/violation_engine/eval_tracking.py` (Phase 1's scoring script) found already written but never run — no ground-truth clip exists yet. Decision made to proceed straight to Phase 2 (BoT-SORT-ReID) without it, accepting the risk that any before/after comparison is judged by raw counts/eye rather than a confirmed metric |
| 2026-09-26 | Added `ml/violation_engine/botsort_sim.yaml` (BoT-SORT, `with_reid: true`, `gmc_method: sparseOptFlow`) and a `--tracker {bytetrack,botsort}` flag on `process_recorded_flight.py`/`run_sim_validation.py`. Ran both trackers on the same real recorded flight (`20260920_194932`, 899 frames): ByteTrack = 853 rows/147 unique IDs, BoT-SORT = 1175 rows/54 unique IDs. Adopted BoT-SORT-ReID as the new default for recorded-flight/sim validation, flagged as provisional pending a visual spot-check of the two annotated videos (or, later, a real ground-truth-based number) |
| 2026-09-26 | Team visually reviewed the annotated videos; BoT-SORT confirmed as the tracker going forward. Remaining issue reported: IDs reassigned during sudden camera movement |
| 2026-09-26 | Investigated sudden-movement ID switches (Section 2.3). Tried `gmc_method: orb` — worse (OpenCV crash on low-keypoint frames, silent fallback to no compensation on 36% of frames); reverted. Diagnostic showed GMC was never the cause (`sparseOptFlow` fails only on blank frames) and that 36% of the recording is blank. Real cause: detection dropout during sudden moves + BoT-SORT's IoU gate blocking ReID re-linking. Set `proximity_thresh` 0.5→0.2, `track_buffer` 60→90: unique IDs 54→48. Offline tracklet stitching identified as the next lever |
| 2026-09-26 | Implemented offline tracklet stitching (`ml/violation_engine/stitch_tracklets.py`, auto-run by `process_recorded_flight.py`). Calibrated colour threshold on the flight, visually checked every link, added a max-cost cut-off after one unconfirmable link. Result: 5 confirmed links, unique IDs 48→43. Found that most remaining "new IDs during sudden moves" are genuinely new vehicles brought into view by fast pans (verified at frames 330/346), not switches. Default `--tracker` switched to `botsort` |
| 2026-09-26 | Stage 5 (presentation package) built, except the violation-flag overlay which depends on Stage 4: `docs/Phase1_Presentation_Package.md` + `ml/violation_engine/make_demo_videos.py` (`demo.mp4`, `comparison.mp4` for flight `20260920_194932`, 572 usable frames, 327 blank frames skipped) |
| 2026-09-26 | Tested on real drone footage (roundabout video) via new `process_video.py` at imgsz 1280. Found YOLO26 returns ~25% duplicate boxes (same vehicle, two classes), causing heavy ID fragmentation on any tracker. Added class-agnostic NMS before tracking: roundabout clip 113→45 IDs, CARLA flight 48→37 IDs. Caught and corrected a flawed multi-run-per-process measurement along the way. Earlier CARLA/presentation numbers now stale (pre-fix) |
