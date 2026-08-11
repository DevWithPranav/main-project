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
   - **Result: confirmed.** Smoke test (25 train images, batch=8, imgsz=640, patience=5) produced a valid fine-tuned mAP50=0.121 / mAP50-95=0.085 on the full 548-image val split, up from an unmeasurable zero-shot baseline (see caveat above) but qualitatively solid zero-shot detections beforehand. That's real learning signal from a tiny subset in a handful of epochs — proceeding straight to full training (all 6,471 train images) rather than falling back to YOLO26s/m.

**Compute note:** dev machine has an RTX 4060 Laptop GPU (8GB VRAM) with CUDA confirmed working — this doubles as both local dev and target training GPU, simpler than the original two-machine assumption. Watch for two Windows-specific gotchas hit during setup: (1) `batch=-1` auto-batch can itself OOM on this card with larger models/image sizes — set a small fixed batch instead; (2) `workers>0` can crash with a pagefile-related DLL error spawning dataloader subprocesses — `workers=0` avoids it.

---

## Stage 2 — Pick the Dataset(s)

Single-frame detection isn't sufficient for wrong-way and speeding (need trajectories over time); no-parking needs sustained stationary state over time too.

- **VisDrone2019-DET** — aerial stills, axis-aligned annotations (`car`/`van`/`truck`/`bus`/`motor`), used for Stage 1 detector benchmarking/fine-tuning.
- **VisDrone2019-MOT** or **UAVDT** — video sequences with axis-aligned vehicle tracking annotations, needed from Stage 3 onward for trajectory-based violations. Same dataset family as VisDrone2019-DET, so Stage 1's detector carries forward without a domain shift.
- **DOTAv1** — aerial stills with oriented vehicle annotations; evaluated for Stage 1 (see `ml/detection/obb_exploration/`) but not used going forward, since it doesn't have tracking-annotated sequences and OBB doesn't carry into Stage 3.
- **Known gap:** none of these public datasets include real drone telemetry (altitude/GSD/gimbal angle). Speeding detection therefore needs either a manually calibrated demo clip (assumed altitude/focal length/frame rate) or a synthetic-scale assumption clearly stated as a limitation in the presentation.

---

## Stage 3 — Tracking Layer

Add a multi-object tracker (DeepSORT or ByteTrack) on top of the confirmed detector to produce per-vehicle trajectories (position + velocity + heading over time) from the MOT dataset video sequences.

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

## Status

| Stage | Status |
|---|---|
| 1 — Confirm detection model | Confirmed — axis-aligned YOLO26l on VisDrone2019-DET; smoke test showed real learning signal; full training in progress |
| 2 — Dataset selection | Not started |
| 3 — Tracking layer | Not started |
| 4 — Violation logic | Not started |
| 5 — Presentation package | Not started |
