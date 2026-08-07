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

1. **Environment setup** — install `ultralytics` (YOLO26 support) + CUDA-enabled PyTorch in the `ml/` venv; verify GPU visibility.
2. **Baseline zero-shot check** — run pretrained YOLO26l (COCO weights) on sample aerial/drone images. COCO already has `car`, `motorcycle`, `bus`, `truck` classes, but top-down aerial view is a large domain shift from COCO's mostly ground-level photos — expect partial detection, misses on small/top-down vehicles.
3. **Small-scale fine-tuning smoke test** — fine-tune YOLO26l on a small subset of an aerial vehicle dataset (VisDrone2019-DET: car/van/truck/bus/motor classes, top-down drone imagery) for a short run. Purpose: prove the architecture *can* learn the domain, not final accuracy.
4. **Decision checkpoint** — compare zero-shot vs. fine-tuned mAP and visual detections on held-out aerial images.
   - Clear improvement + reliable small-object aerial detection → YOLO26l confirmed, proceed to full training later.
   - Not clear → fall back to comparing YOLO26s / YOLO26m as alternatives.

**Compute note:** local dev GPU is RTX 4050 Laptop (6GB VRAM); target training GPU is RTX 4060 (access method TBD). Where each step actually runs is decided at execution time, not baked into this plan.

---

## Stage 2 — Pick the Dataset(s)

Single-frame detection isn't sufficient for wrong-way and speeding (need trajectories over time); no-parking needs sustained stationary state over time too.

- **VisDrone2019-DET** — aerial stills, used for Stage 1 detector benchmarking/fine-tuning.
- **VisDrone2019-MOT** or **UAVDT** — video sequences with vehicle tracking annotations, needed from Stage 3 onward for trajectory-based violations.
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
| 1 — Confirm detection model | Not started |
| 2 — Dataset selection | Not started |
| 3 — Tracking layer | Not started |
| 4 — Violation logic | Not started |
| 5 — Presentation package | Not started |
