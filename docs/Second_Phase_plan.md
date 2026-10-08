# Action Plan: Pothole Detection (YOLO26l-seg + DSConv/SimAM/GELU) + CARLA Integration

## Context
- Base model: YOLO26l-seg
- Dataset: PothRGBD (RGB-D, 1000 images, YOLO-seg format)
- Architecture mods: Dynamic Snake Convolution (DSConv), SimAM, GELU (from arXiv 2505.04207)
- Prior milestone: object detection (vehicles) implemented and working in CARLA
- Next milestone: pothole detection implemented and integrated into CARLA sim

---

## Phase 1 — Environment & Baseline Setup

- [x] Set up a separate working directory/branch for pothole model (keep vehicle detection pipeline untouched) — `ml/pothole/`
- [x] Install/confirm `ultralytics` version supports YOLO26l-seg
- [x] Load PothRGBD dataset, verify:
  - [x] Image/mask pairs are valid YOLO-seg format
  - [x] Split into train/val/test (`ml/pothole/prepare_dataset.py`, 80/10/10 → **800 / 100 / 100** images from the 1,000-image PothRGBD set at `PUBLIC POTHOLE DATASET/`)
  - [x] Confirm depth channel is present but will be set aside (not used for detection, only optionally for later severity heuristics) — RGB-only images copied into `ml/pothole/data/`, depth channel untouched
- [x] Smoke test — `ml/pothole/smoke_test.py` fine-tuned stock `yolo26l-seg.pt` on a 25-image subset (20 epochs, patience=5) to confirm a learning signal before committing to the full run
- [x] Train **stock YOLO26l-seg** on PothRGBD (RGB only) as a baseline — no architecture mods yet (`ml/pothole/train_baseline.py`)
  - [x] Record baseline precision / recall / mAP@50
  - [x] This baseline is your reference point to measure whether DSConv/SimAM/GELU actually help on YOUR data

**Baseline results** (`ml/pothole/runs/baseline/`, single class `pothole`, imgsz=640, batch=4): training ran 71 epochs before early stopping (patience=20, no improvement after epoch 51 — well under the 10-hour time cap). Best checkpoint (`weights/best.pt`) is epoch 51:

| Metric | Box | Mask |
|---|---|---|
| Precision | 0.936 | 0.956 |
| Recall | 0.838 | 0.856 |
| mAP@50 | 0.923 | 0.927 |
| mAP@50-95 | 0.605 | 0.635 |

Note: this is a stock, unmodified YOLO26l-seg baseline (no DSConv/SimAM/GELU yet) — see Phase 3's comparison note re: the reference paper's reported 93.7% precision / 90.4% recall / 93.8% mAP@50 (different base model, YOLOv8, not directly comparable).

---

## Phase 2 — Architecture Modification (DSConv + SimAM + GELU)

- [x] Pull SimAM reference implementation (ZjjConan/SimAM) — parameter-free, ~10 lines — implemented at `ml/pothole/modules.py::SimAM`
- [x] Pull/port DSConv reference implementation (Dynamic Snake Convolution paper repo) — implemented at `ml/pothole/modules.py::DSConv` (drop-in `Conv` replacement using `torchvision.ops.deform_conv2d` with a cumulative "snake" offset constraint); `C3k2WithSimAM` wrapper also implemented for in-place block swapping
- [ ] Modify YOLO26l-seg model YAML / backbone definition:
  - [ ] Replace Conv blocks in backbone + head with DSConv (skip the very first Conv layer)
  - [ ] Insert SimAM after each C2f-equivalent block in backbone + neck (skip the first block)
  - [x] Replace SiLU activation with GELU across all Conv blocks — done inside `DSConv` itself (`nn.GELU()` as its default activation), not yet applied to the untouched stock Conv blocks
- [ ] Sanity-check modified model:
  - [ ] Forward pass on dummy tensor — confirm no shape mismatches
  - [ ] Confirm gradient flow (quick 1-epoch overfit test on a tiny subset)

**Status:** module code (`DSConv`, `SimAM`, `C3k2WithSimAM`) is written and ready, but no model-surgery script exists yet to actually swap them into the YOLO26l-seg backbone/neck graph (the planned `modify_model.py` referenced in `modules.py`'s docstring hasn't been created), and no modified model has been trained — Phase 2 has not produced a trained checkpoint yet.

---

## Phase 3 — Training on PothRGBD

- [ ] Train modified YOLO26l-seg (DSConv+SimAM+GELU) on PothRGBD
- [ ] Use standard augmentations (mosaic, flip, HSV jitter, scale, translation)
- [ ] Track precision / recall / mAP@50 vs. Phase 1 baseline
- [ ] Run ablation (optional but useful for report/paper-style validation):
  - [ ] Baseline + DSConv only
  - [ ] Baseline + SimAM only
  - [ ] Baseline + GELU only
  - [ ] All combined
- [ ] Save best checkpoint (`best.pt`)

---

## Phase 4 — Domain Gap Check (Aerial Transfer)

- [ ] Evaluate PothRGBD-trained model on a small set of real aerial/drone frames (if available)
- [ ] Assess recall specifically — is it missing potholes from altitude/angle?
- [ ] If domain gap is significant:
  - [ ] Collect/label a small aerial pothole set (100–300 images) OR
  - [ ] Generate synthetic aerial pothole data in CARLA (see Phase 5) and fine-tune on it

---

## Phase 5 — CARLA Synthetic Data Generation (per PRD plan)

- [ ] Set up CARLA road mesh scenes matching your existing object-detection CARLA setup
- [ ] Composite pothole/crack/waterlogging texture patches onto CARLA road surfaces at randomized locations
- [ ] Auto-export composited region as ground-truth segmentation mask
- [ ] Capture frames from UAV-equivalent camera angle/altitude in CARLA (match your real drone's FOV/height)
- [ ] Merge synthetic CARLA pothole data with PothRGBD (RGB-only) for a combined training set
- [ ] Re-train / fine-tune modified YOLO26l-seg on combined dataset (PothRGBD + CARLA synthetic)

---

## Phase 6 — CARLA Integration (Pothole Detection Pipeline)

- [ ] Reuse existing CARLA object-detection integration pattern (camera sensor hookup, frame capture loop) from vehicle detection work
- [ ] Add pothole model as a **separate inference pass** (not merged into vehicle detection model)
- [ ] Run pothole segmentation model on CARLA camera feed frame-by-frame
- [ ] Overlay/visualize segmentation masks in CARLA viewer (sanity check)
- [ ] Implement severity scoring stub (per PRD):
  - [ ] Segmented area → real-world size via GSD (ground sample distance)
  - [ ] Shadow/edge contrast → depth-proxy heuristic
  - [ ] Bucket into Low / Medium / High severity

---

## Phase 7 — Edge Case Handling (per PRD spec)

- [ ] Waterlogged pothole handling:
  - [ ] Flag reduced confidence when waterlogging class overlaps pothole detection
- [ ] Shadow false-positive filtering:
  - [ ] Add texture/edge-consistency post-processing check to reject shadow-shaped false positives
- [ ] GPS/position-based deduplication:
  - [ ] If running multi-pass CARLA simulation, cluster detections by simulated GPS/position to avoid duplicate logging

---

## Phase 8 — Validation & Reporting

- [ ] Compare final model (DSConv+SimAM+GELU, PothRGBD+CARLA synthetic) against:
  - [ ] Stock YOLO26l-seg baseline (Phase 1)
  - [ ] Paper's reported numbers (93.7% precision / 90.4% recall / 93.8% mAP@50) — note: not a direct comparison since base model differs (YOLO26 vs YOLOv8), state this explicitly if used in report
- [ ] Document FPS / inference latency on target hardware
- [ ] Log results for PRD/report/documentation purposes

---

## Open Decisions / Risks to Flag

- [ ] Confirm whether real aerial pothole footage will be collected, or CARLA synthetic data is the primary aerial source
- [ ] Confirm target inference hardware (affects whether YOLO26l is feasible on edge device vs. needing a smaller variant)
- [ ] Decide whether depth-based severity (from PothRGBD depth channel) is worth preserving as an optional module for non-aerial/ground validation, even though the UAV won't have a depth sensor

---

## Status

| Phase | Status |
|---|---|
| 1 — Environment & baseline setup | **Done** — stock YOLO26l-seg fine-tuned on PothRGBD (800/100/100 split); box mAP50=0.923, mask mAP50=0.927 (epoch 51/71, `ml/pothole/runs/baseline/`) |
| 2 — Architecture modification (DSConv+SimAM+GELU) | In progress — `DSConv`/`SimAM`/`C3k2WithSimAM` implemented (`ml/pothole/modules.py`); model-surgery integration into YOLO26l-seg's graph and training not started |
| 3 — Training on PothRGBD (modified model) | Not started — depends on Phase 2 integration |
| 4 — Domain gap check (aerial transfer) | Not started |
| 5 — CARLA synthetic data generation | Not started |
| 6 — CARLA integration (pothole pipeline) | Not started |
| 7 — Edge case handling | Not started |
| 8 — Validation & reporting | Not started |