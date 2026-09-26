# Phase 1 Presentation Package — Aerial Vehicle Detection & Tracking

Stage 5 deliverable of `docs/First_Phase_Plan.md`: demo assets, benchmark numbers that justify the model and tracker choices, and the limitations stated up front.

Status: **detection + tracking demo complete; violation-flag overlay pending Stage 4** (no-parking / wrong-way / speeding rules are not implemented yet — see Section 5).

---

## 1. Pipeline

```
Drone footage (CARLA recorded flight / VisDrone)
   -> YOLO26l detector, fine-tuned on VisDrone2019-DET   (car / van / truck / bus)
   -> BoT-SORT-ReID tracker (appearance + camera-motion compensation)
   -> Offline tracklet stitching (re-links IDs broken by sudden camera moves)
   -> Per-vehicle trajectories CSV (frame, time, id, class, cx, cy, w, h, conf)
   -> [Stage 4, pending] violation rules: no-parking, wrong-way, speeding
```

Run: `python ml/violation_engine/process_recorded_flight.py <run_id>` (tracking + stitching), then `python ml/violation_engine/make_demo_videos.py <run_id>` (demo videos; needs a `--tracker bytetrack` run too for the comparison).

## 2. Demo assets

Local only (`ml/data/results/` is gitignored); regenerate with the commands above. Flight `20260920_194932` (CARLA Town10HD, manually flown, 15.4 fps, 1920×1080).

| File | Shows |
|---|---|
| `ml/data/results/presentation/20260920_194932/demo.mp4` | Final pipeline: boxes + stable vehicle IDs, HUD with vehicles in view and unique vehicles so far (1280×720, 572 frames, blank frames removed) |
| `ml/data/results/presentation/20260920_194932/comparison.mp4` | ByteTrack baseline (left) vs final pipeline (right) on identical frames; the unique-ID counters end at **147 vs 43** |
| `ml/data/results/tracking_demo/tracked/` | Tracking on real VisDrone2019-MOT footage (`uav0000137_00458_v`) |

Good moments to show: **~t = 22 s** (busy intersection, six cars tracked with steady IDs after a fast turn onto the scene); **~t = 24–26 s** (grey van, left side, keeps its ID through a detection gap that the tracker alone had split into two IDs).

## 3. Benchmarks

### 3.1 Detection — why YOLO26l fine-tuned on VisDrone

| Model | mAP50 | Note |
|---|---|---|
| COCO-pretrained YOLO26l, zero-shot | 0.034 | Not a real signal: COCO and VisDrone number their classes differently, so correct boxes score as wrong classes |
| Fine-tune smoke test (25 images) | 0.121 | Confirmed the model learns before committing ~7 h of GPU time |
| **Fine-tuned on full VisDrone2019-DET, all 10 classes** | **0.457** | mAP50-95 0.273 |
| **Same model, the 4 in-scope classes (car/van/truck/bus)** | **0.585** | mAP50-95 0.413 — the relevant number for this project |

Per in-scope class (mAP50): car 0.832 · bus 0.600 · van 0.481 · truck 0.426.

Speed: **11.8 ms/image (~85 FPS)** on an RTX 4060 Laptop GPU (8 GB) — comfortably real-time.

Why the in-scope number: VisDrone has 10 classes, but pedestrians, bicycles, tricycles and motorcycles play no part in the three Phase 1 violations. Averaging over them understates the model on the task it's used for.

### 3.2 Tracking — why BoT-SORT-ReID + stitching

Same recorded flight, same detector, only the tracking changes. Fewer unique IDs for the same traffic = fewer ID switches (one vehicle wrongly split into several IDs).

| Tracker | Unique vehicle IDs | Ultralytics-reported time/frame |
|---|---|---|
| ByteTrack (tuned for sim footage) | 147 | 22.0 ms |
| BoT-SORT-ReID (stock thresholds) | 54 | 26.9 ms |
| BoT-SORT-ReID, tuned (`proximity_thresh` 0.2, `track_buffer` 90) | 48 | not timed separately (same components) |
| **+ offline tracklet stitching** | **43** | + one offline pass |

- **ByteTrack → BoT-SORT (−63% IDs):** the drone camera is always moving; ByteTrack matches on box overlap only, BoT-SORT adds an appearance model and cancels camera motion. Confirmed by the team visually reviewing both annotated videos.
- **Stitching (−10% more):** during sudden camera moves the detector briefly loses vehicles, and BoT-SORT only consults appearance when the returning box already overlaps its prediction. Stitching re-links those fragments using camera-corrected position, size, class and colour. All 5 links on this flight were checked by eye; a stricter cut-off was added after one link couldn't be confirmed, because merging two vehicles is worse than leaving one split.
- **What's left is mostly correct:** most remaining new IDs appear when a fast turn brings new road into view — genuinely new vehicles (checked at frames 330 vs 346).
- Real footage: on VisDrone2019-MOT sequence `uav0000137_00458_v` (233 frames, busy intersection), the vehicles present from the first frame keep the same IDs to the last frame.

Approaches tried and rejected (evidence in `docs/main_project_tracker.md`, Section 2.3): loosening ByteTrack thresholds (no gain), ORB camera-motion compensation (crashes on low-texture frames), SIFT (~18× slower, no real gain).

## 4. Limitations

**Detection**
- In-scope mAP50 is **0.585, below the PRD target of 0.75**. Truck (0.426) and van (0.481) are weakest; a 4-class retrain is planned (the last run also stopped before its own stop conditions triggered).
- Trained on real VisDrone imagery, demoed on CARLA renders. It transfers well in practice, but no mAP has been measured on CARLA footage.
- Motorcycles are excluded from tracking (severe ID churn on small, flickering detections); none of the Phase 1 violations need them.

**Tracking**
- Quality is judged by **unique-ID counts and visual review, not a ground-truth metric**. `ml/violation_engine/eval_tracking.py` (MOTA / IDF1 / ID switches) exists but has no hand-labelled clip to score against yet. Fewer IDs is consistent with fewer switches but cannot by itself rule out a wrong merge.
- Stitching thresholds were calibrated on **one flight**; they need confirming on a second recording.
- The demo flight is a manually flown CARLA recording; 327 of 899 frames were blank (before takeoff and after the drone descended through the road) and are cut from the demo.

**Scope / not yet built**
- **No violation detection yet** — Stage 4 rules are pending, so the demo shows detection and tracking only.
- **Speeding needs real scale.** Speed = pixel displacement × ground sampling distance × frame rate, and the public datasets carry no drone telemetry (altitude, focal length, gimbal angle). Speeding will rely on CARLA's known camera setup or a manually calibrated clip, stated as an assumption.
- Phase 1 covers 3 of the PRD's 10 violation types.

## 5. What completes this package

- [ ] Stage 4 rules write per-frame violation flags; overlay them in `make_demo_videos.py` (`draw_tracks()`), e.g. red box + label for a flagged vehicle.
- [ ] One violation example per type in the demo (CARLA can stage them with known ground truth).
- [ ] Optional before presenting: 4-class detector retrain (update Section 3.1), and a hand-labelled clip so Section 3.2 can quote MOTA/IDF1 instead of ID counts.
