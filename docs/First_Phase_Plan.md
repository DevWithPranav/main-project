# Implementation Plan — Phase 1 (First Presentation)

## Scope

Build and demonstrate an aerial vehicle detection + violation-flagging pipeline covering:
- **No-parking zone violation**
- **Wrong-way driving**
- **Speeding**

Model: **YOLO26l** (Ultralytics, released Jan 2026 — native NMS-free end-to-end inference, 54.4 mAP on COCO, 6.2ms latency on T4 TensorRT).

---

## Stage 1 — Confirm the Detection Model

Verify YOLO26l can detect vehicles from aerial/top-down drone view once fine-tuned, before committing further effort.

**Box format decision:** axis-aligned boxes end-to-end, via `yolo26l.pt` (COCO-trained) on VisDrone2019-DET — matches the original plan. Oriented bounding boxes (OBB, `yolo26l-obb.pt` on DOTA) were evaluated first since aerial vehicles sit at arbitrary rotation angles and OBB fits them more tightly, but were dropped: Stage 3 tracking datasets (VisDrone-MOT / UAVDT) only have axis-aligned annotations, and DOTA (stills-only) doesn't carry forward into tracking at all. Running OBB just for Stage 1 would mean fine-tuning and validating two separate detectors on two unrelated datasets for no benefit past the single-frame demo — not worth the duplicated effort for a pipeline that needs to work end-to-end. The OBB exploration (scripts + results) is kept at `ml/detection/obb_exploration/` as a documented dead-end, since its numbers are still useful supporting evidence of model capability.

1. **Environment setup** — install `ultralytics` (YOLO26 support) + CUDA-enabled PyTorch (done — venv at repo root, Python 3.14, torch cu130, RTX 4060 Laptop GPU confirmed visible).
2. **Baseline zero-shot check** — run pretrained `yolo26l.pt` (COCO weights) on VisDrone2019-DET's val split (`ml/detection/zero_shot_baseline.py`). No leakage risk: `yolo26l.pt` is COCO-only, never trained on VisDrone, so this is a legitimate zero-shot number without needing to wait on real drone footage.
   - **Numeric mAP not meaningful pre-fine-tune.** Ultralytics' `val()` matches predictions to ground truth by raw class index, and COCO's ordering (`car`=2, `truck`=7, `bus`=5, ...) doesn't match VisDrone's (`car`=3, `truck`=5, `bus`=8, ...) — so it reports mAP50=0.034 despite visually correct detections (12-16 cars/image correctly spotted, plus trucks/motorcycles/persons). Qualitative detection quality is the real zero-shot signal here; a valid numeric mAP only becomes available after fine-tuning, once the model's head is retrained on VisDrone's own class order.
3. **Small-scale fine-tuning smoke test** — fine-tune `yolo26l.pt` on a small (~25 image) VisDrone train subset for a short run with early stopping (`ml/detection/prepare_visdrone_subset.py` + `ml/detection/finetune_smoke_test.py`). Purpose: prove the architecture *can* learn the domain, not final accuracy. Early stopping added after the OBB detour showed a tiny fine-tune set overfits past its best epoch within a handful of epochs.
4. **Decision checkpoint** — compare zero-shot vs. fine-tuned mAP and visual detections on held-out aerial images.
   - Clear improvement + reliable small-object aerial detection → YOLO26l confirmed, proceed to full training later.
   - Not clear → fall back to comparing YOLO26s / YOLO26m as alternatives.
   - **Result: confirmed.** Smoke test (25 train images, batch=8, imgsz=640, patience=5) produced a valid fine-tuned mAP50=0.121 / mAP50-95=0.085 on the full 548-image val split, up from an unmeasurable zero-shot baseline (see caveat above) but qualitatively solid zero-shot detections beforehand. That's real learning signal from a tiny subset in a handful of epochs — proceeded straight to full training (all 6,471 train images) rather than falling back to YOLO26s/m.
5. **Full training** — fine-tuned `yolo26l.pt` on the complete VisDrone2019-DET train split (`ml/detection/train_full.py`; 10-hour time cap, patience=20, batch=8, imgsz=640, workers=2). Final result on the full 548-image val split:

   | Class | mAP50 | mAP50-95 |
   |---|---|---|
   | **car** | 0.832 | 0.586 |
   | **bus** | 0.600 | 0.438 |
   | **van** | 0.481 | 0.338 |
   | **truck** | 0.426 | 0.288 |
   | **motor** | 0.542 | 0.254 |
   | pedestrian | 0.540 | 0.258 |
   | people | 0.416 | 0.170 |
   | bicycle | 0.220 | 0.098 |
   | tricycle | 0.333 | 0.187 |
   | awning-tricycle | 0.177 | 0.114 |
   | **all classes** | **0.457** | **0.273** |

   Inference speed: 11.8ms/image (~85 FPS) on the RTX 4060 Laptop GPU. Strong on the classes that matter most for violation detection (car/bus/van/truck); weaker on bicycle and awning-tricycle, which is typical for VisDrone (small, dense, often-occluded objects) and not a concern for this project's scope. Weights: `ml/data/results/full_train/train/weights/best.pt`.

**Compute note:** dev machine has an RTX 4060 Laptop GPU (8GB VRAM) with CUDA confirmed working — this doubles as both local dev and target training GPU, simpler than the original two-machine assumption. Watch for Windows-specific gotchas hit during setup: (1) `batch=-1` auto-batch can itself OOM on this card with larger models/image sizes — set a small fixed batch instead; (2) `workers>0` (default 8) can crash with a pagefile/commit-limit DLL error spawning dataloader subprocesses on this machine's 16GB RAM — `workers=2` struck a working balance between parallelism and stability for the full run.

---

## Stage 2 — Pick the Dataset(s)

Single-frame detection isn't sufficient for wrong-way and speeding (need trajectories over time); no-parking needs sustained stationary state over time too.

- **VisDrone2019-DET** — aerial stills, axis-aligned annotations (`car`/`van`/`truck`/`bus`/`motor`), used for Stage 1 detector benchmarking/fine-tuning.
- **VisDrone2019-MOT-val** (1.48GB, manually downloaded — only hosted on Google Drive/Baidu, no scriptable direct-download like DET had) — 7 real drone video sequences with axis-aligned MOT-format tracking annotations, used from Stage 3 onward for trajectory-based violations. Same dataset family as VisDrone2019-DET, so Stage 1's detector carries forward without a domain shift. Only the val split was needed (not train/test-challenge): ByteTrack (see Stage 3) is a classical, non-learned tracker, so no MOT-specific training step exists — the data is for validating tracking behavior, not training.
- **DOTAv1** — aerial stills with oriented vehicle annotations; evaluated for Stage 1 (see `ml/detection/obb_exploration/`) but not used going forward, since it doesn't have tracking-annotated sequences and OBB doesn't carry into Stage 3.
- **Known gap:** none of these public datasets include real drone telemetry (altitude/GSD/gimbal angle). Speeding detection therefore needs either a manually calibrated demo clip (assumed altitude/focal length/frame rate) or a synthetic-scale assumption clearly stated as a limitation in the presentation.

---

## Stage 3 — Tracking Layer

Add a multi-object tracker (DeepSORT or ByteTrack) on top of the confirmed detector to produce per-vehicle trajectories (position + velocity + heading over time) from the MOT dataset video sequences.

**Tracker decision: ByteTrack**, via Ultralytics' built-in `model.track()` — no separate DeepSORT integration needed, since Ultralytics ships ByteTrack (plus BoT-SORT and others) out of the box, reusing the Stage 1 detector directly. As a classical (non-learned) algorithm, it needs no training step, only real video to validate against (`ml/tracking/track_demo.py`).

Tested on a real, busy VisDrone2019-MOT-val intersection sequence (233 frames, `uav0000137_00458_v`, cars turning/queuing plus heavy bicycle/pedestrian cross-traffic — not an easy static scene). Result: **car/van/truck/bus tracking is stable** — the core group of vehicles present from frame 0 (ids 3, 4, 5, 12, 17, 35, 43...) kept the exact same IDs through to the last frame (232); new IDs appeared only for vehicles genuinely entering the intersection later, which is correct behavior, not drift.

`motor` (motorcycles/scooters) was tried and dropped from tracking: on the same sequence it produced severe ID churn (444+ IDs by frame 100 for ~10 visible motorcycles), caused by low, flickering detection confidence on small, frequently-occluded objects — every confidence dip below threshold breaks the track and a new ID gets assigned on re-detection. Not a concern for this project: none of the three violation types (no-parking, wrong-way, speeding) apply to motorcycles in scope.

---

## Stage 4 — Violation Logic (on top of trajectories)

| Violation | Logic |
|---|---|
| No-parking | Vehicle centroid inside zone polygon AND velocity below threshold sustained for a defined grace period |
| Wrong-way driving | Vehicle heading vector compared against a defined permitted road-direction vector; flagged past an angular deviation threshold |
| Speeding | Pixel displacement per frame × scale (GSD or calibrated equivalent) × frame rate → speed; flagged above the segment's speed limit for sustained frames |

---

## Stage 5 — Presentation Demo Package

- Annotated output video/notebook: detection → tracking → violation flags overlaid on aerial footage.
- Stage 1 benchmarking numbers (zero-shot vs. fine-tuned mAP, FPS on GPU) included as justification for the model choice — demonstrates rigor rather than an arbitrary pick.
- Clearly stated limitations (no real telemetry, small dataset subset, single demo clip for speeding calibration).

---

## Stage 6 — Simulation Integration (CARLA + AirSim)

Extends the confirmed pipeline (Stages 1-4) beyond real/benchmark footage into synthetic scenarios, directly addressing the "no real drone telemetry" gap flagged in Stage 2: a simulator knows exact altitude/GSD/gimbal angle and exact vehicle speed/position, so violation logic can be validated against ground truth instead of visual judgement alone.

- **CARLA** — scripted traffic scenarios with controllable vehicles, staging deliberate violations (wrong-way, no-parking, speeding past a set limit) with known ground-truth timing/positions for each. Matches the existing `simulation/carla_scripts/` and `simulation/violation_scenarios/` scaffold.
- **AirSim** — drone-mounted aerial camera simulation over the CARLA scenario (or a compatible environment), providing a realistic nadir/gimbal view with known camera altitude and angle — solving the GSD/scale problem that real footage in this project currently lacks.
- **Uses:**
  1. Precision validation of Stage 4's violation rules against known ground truth (exact speed, exact zone-crossing time), not just visual plausibility on real clips.
  2. Synthetic training data generation (`simulation/data_export/`) to expand beyond VisDrone's still/video coverage for edge cases (e.g. genuine no-parking-zone violations, which VisDrone's real footage doesn't happen to capture).
- **Status:** in progress — a real CarlaAir (CARLA+AirSim merged) distribution is set up and validated on the dev machine. Built: `simulation/carla_scripts/fly_and_capture.py` flies a drone on a fixed automatic waypoint path over CARLA traffic and records aerial footage; `ml/violation_engine/run_sim_validation.py` runs the Stage 1/3 detector+tracker against that footage (trajectory CSV + annotated video). See `simulation/README.md` for the two-environment (Python 3.10 sim / Python 3.14 ML) usage.
  - **First end-to-end run (2026-08-08, Town10HD, 180 frames):** pipeline connectivity confirmed working — capture, detection, tracking, CSV export all ran without errors. Results were poor (52/180 frames had any detection, 19 track IDs with the longest surviving only 22 frames) — root cause found: `fly_and_capture.py` never set the AirSim camera to a nadir (downward) pose, so every captured frame looked at the skyline/horizon instead of down at the road, starving the detector of visible vehicles. Fixed by adding a `simSetCameraPose` call (pitch −90°) right after connecting, before takeoff.
  - **Follow-up fixes (same day, before re-running):** (1) flight path was a guessed set of world coordinates not reliably tracking any road — replaced with `generate_road_path()`, which walks CARLA's Waypoint API along an actual road lane from a random spawn point, so the drone now always flies over real road geometry on any map; (2) detection was reading the re-encoded `flight.mp4`, a second lossy compression pass that could also drop/duplicate frames at the muxer level — `run_sim_validation.py` now runs detection directly on the captured `frames/*.jpg` sequence (saved at JPEG quality 100), with `flight.mp4` kept only for quick human review; (3) capture rate raised from 10 to 20 fps by default; (4) drone camera capture resolution raised from 1280×960 to 1920×1080 in AirSim's `settings.json`, and the recommended `StartCarlaAir.bat` launch now pins `--res 1920x1080 --quality Epic` explicitly. **Re-run pending** to get a real read on detection/tracking quality on synthetic footage — the domain-gap question (VisDrone-trained model vs. CARLA rendering) is still open until then.
  - **Custom/manual flight support added:** `fly_and_capture.py --waypoints-file` replays a trajectory recorded by manually flying once with CarlaAir's `record_drone.py`, as an alternative to the auto-generated road path.
  - **Live single-pipeline mode added:** `simulation/carla_scripts/live_fly_and_track.py` — manual keyboard-controlled flight, live detection/tracking, and a live annotated display window, all in one process (requires installing `ultralytics`+`torch` into the `carlaAir` conda env alongside `carla`/`airsim`, since this mode can't split simulate/detect across two environments the way the offline pipeline does).
  - **Live mode debugging (2026-08-08):** manual testing surfaced that AirSim's `simGetImages` RPC screenshot call — the same mechanism `fly_and_capture.py` uses successfully offline — is unreliable in this build once the drone is actually flying: capture worked fine grounded, then stalled forever the instant flight began, regardless of quality/resolution settings. Three targeted fixes (isolating the RPC connection per thread — a real bug, since `msgpackrpc` isn't thread-safe and was corrupting the shared connection; throttling the keyboard-control loop's command rate; PNG-compressing the image payload) each addressed a real, separate problem but none resolved the flight-triggered stall. Concluded the AirSim RPC image path itself can't be trusted under load in this build. **Resolution:** `live_fly_and_track.py` now acquires frames via a native CARLA `sensor.camera.rgb` attached directly to the drone's CARLA-side actor (`CarlaCameraGrabber`, push-based `camera.listen()`, no request/response round trip) instead of AirSim's screenshot RPC — proven viable since this same distribution's `examples/data_collector.py` already streams cameras from an actively-moving (autopilot-driving) actor without issue. AirSim is still used for flight control only. Not yet manually re-tested with this change.

---

## Status

| Stage | Status |
|---|---|
| 1 — Confirm detection model | **Done** — axis-aligned YOLO26l fine-tuned on full VisDrone2019-DET; mAP50=0.457 (car=0.832), ~85 FPS on RTX 4060 |
| 2 — Dataset selection | **Done** — VisDrone2019-DET (Stage 1) + VisDrone2019-MOT-val (Stage 3) |
| 3 — Tracking layer | **Done** — ByteTrack via Ultralytics `model.track()`; stable car/van/truck/bus IDs on real intersection footage |
| 4 — Violation logic | In progress — trajectory extraction done (`ml/violation_engine/extract_trajectories.py`, 8,584 rows on the demo sequence); rule implementation pending |
| 5 — Presentation package | **Mostly done** — `docs/Phase1_Presentation_Package.md` (benchmarks, limitations, demo assets) + `ml/violation_engine/make_demo_videos.py` (demo + ByteTrack-vs-final comparison videos). Violation-flag overlay pending Stage 4 |
| 6 — Simulation integration (CARLA + AirSim) | In progress — CarlaAir set up + validated; drone flight/capture script and sim-validation runner built; capture run + results review pending |
