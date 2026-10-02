# Vehicle Detection & Tracking — Improvement Plan

Status: **v4** (2026-09-27; v3 2026-09-26). Owner: Afif. Supersedes the open parts of `docs/Vehicle_Tracking_ID_Consistency_Plan.md` (Phases 1–2 of that plan are done).

Changes in v4 (2026-09-27): **Section 6 reorganised into a status-tracked work order with the detector retrain as the last step** (team decision). The retrain dataset now combines VisDrone-DET, VisDrone-VID, UAVDT and auto-labelled CARLA frames within a 24 h training budget (Stages 4-5).

Changes in v3 (team decisions):
- **No snow training.** Snow datasets (NVD, SWUAV) and snow hard-negative frames are dropped from the detector fine-tune. Snow false positives are handled after detection: confidence gates, tracker start thresholds, track-aware initialisation and track-level filtering (Section 7, "Snow without snow training").
- **Ground truth is provided by the team: one 1-minute clip at 1080p.** The team no longer labels the ground truth jointly in Phase 2; W4 imports and checks the delivered file instead.
- Added **Section 5: expected improvement**.

v2 introduced the 4 parallel workstreams and the phases from easy to hard.

**Progress (2026-09-27): Phase 1 done** — details and numbers in `docs/main_project_tracker.md`, Section 2.3, "Improvement Plan — Phase 1". Points that change later phases:
- Post-processing writes **`trajectories_final.csv`** (+ `track_summary.csv`); `trajectories_stitched.csv` stays stitching-only so each stage can be scored. The handoff file for violation logic becomes `trajectories_final.csv`.
- D4's mean-conf < 0.3 rule also drops some real, long-lived parked cars — set it on the GT in Phase 2 before relying on it.
- **C1:** the current BoT-SORT "ReID" never rejects on appearance (`appearance_thresh` 0.25 ⇒ any cosine similarity > −0.5 passes); it only loosens the IoU gate, so all encoders give identical IDs. Encoder comparisons need `appearance_thresh` near the stock 0.8.
- **W3 decision: Stabilo (SIFT, vehicles masked) + own keyframe chaining** — 2–9 px parked-car drift over the largest 30 s pan; Stabilo alone fails there.
- **W1:** Geo-trax detector weights are CC BY 4.0 (attribution), comparable to ours at ~2× speed — score it on the GT too. imgsz 1920 vs 1280 on CARLA: +10% boxes, 1.7× time.

**Progress (2026-09-27): Phase 2 started.** GT delivered and imported: CARLA flight `20260926_215405` (Town10HD, 1,987 frames ≈ 86 s at ~23 fps, 40 vehicles; no snow, not real footage) → `ml/data/eval/gt_1080p/`. `run_experiment.py` + extended `eval_tracking.py` done. **Measured baseline (final stage): IDF1 0.852, MOTA 0.806, 3 ID switches, det. P 0.923 / R 0.879, 41 IDs for 40 vehicles** — far better than Section 5 assumed, because this clip is easy; the Section 5 estimates stay for real footage. Details: tracker Section 2.3, "Phase 2: first measured baseline".

Scope: the problems reported after reviewing the BoT-SORT output on the roundabout drone video and CARLA flights — missed vehicles, snow detected as vehicles, unstable/duplicate IDs, ID switches on sudden camera moves, lost-and-reappeared vehicles getting new IDs, and a vehicle's class changing between frames.

---

## 1. Summary

BoT-SORT alone cannot meet the goal. Most of the remaining problems start **before** the tracker (detection quality) or need information the tracker never has (where a vehicle is in the scene, not just in the frame). The pipeline keeps an online tracker and adds stages around it:

```
Video
 └─ A. Detection      YOLO26l re-trained for our classes + our (non-snow) footage, 1280–1920 input,
 │                    class-agnostic NMS (done), per-class confidence gates, size sanity filter
 └─ B. Camera motion  per-frame GMC (done) + homography to a stabilised "scene map"
 └─ C. Tracker        BoT-SORT or TrackTrack, with ReID off or a real vehicle ReID model,
 │                    thresholds tuned on ground truth (higher bar to START a track)
 └─ D. Offline pass   class voting, short/low-confidence track removal,
 │                    map-based tracklet linking, gap filling
 └─ E. Evaluation     team-provided 1-min 1080p ground truth -> IDF1 / ID switches / MOTA / det. P-R
```

Realistic expectation: vehicles that stay in view, and parked vehicles the camera returns to, can keep one ID for the whole video. A moving vehicle that leaves the view and comes back much later will usually still get a new ID — at 15–30 px per vehicle there is not enough appearance detail for any current ReID model to recognise it reliably. Without snow training, snow false positives can be kept out of the *tracks*, but some will still appear as brief low-confidence *detections*.

## 2. What our own measurements already show

Details in `docs/main_project_tracker.md`, Section 2.3.

| Problem | Measured cause | Evidence |
|---|---|---|
| Duplicate / unstable IDs | YOLO26's NMS-free head returns the same vehicle twice under two classes; the second box starts a new track | ~10.6 overlapping pairs/frame (~25% of boxes), 94% cross-class. Class-agnostic NMS: roundabout clip 113 → 45 IDs; full video 3,730 → 714; CARLA 48 → 37. **Fixed.** |
| Tracker choice | Mattered much less than detection quality | Before the NMS fix, stock ByteTrack, stock BoT-SORT and our tuned configs all gave ~100 IDs on the same 5 s clip |
| New IDs after sudden camera moves | Detector loses vehicles during the move (blur / leaving frame); on return, BoT-SORT only consults ReID if the box already overlaps its prediction | CARLA: sudden-move windows = 18% of frames but 37% of new IDs; boxes/frame drop 2.38 → 1.11 inside them |
| Camera-motion compensation | `sparseOptFlow` works; alternatives worse | `sparseOptFlow` failed only on blank frames; `orb` crashed on 36% of frames; `sift` ~18× slower |
| Missed small vehicles | Input resolution | imgsz 1280 vs 640 on the (720p) roundabout: 35.5 vs 30.5 vehicles/frame, extra boxes visually real |
| Class changes between frames | Per-frame classification; car vs van is genuinely ambiguous from above | Visible on the same ID in the annotated video |
| ReID never did vehicle ReID | With YOLO26 (end-to-end head), `model: auto` silently falls back to `yolo26n-cls.pt`, a generic ImageNet classifier | `ultralytics/trackers/track.py`, `on_predict_start`; `yolo26n-cls.pt` appeared in the repo root at the first BoT-SORT run |
| Short tracks | Many IDs live under 1 s | Roundabout: requiring ≥ 1 s takes 695 IDs to 394 (not yet known how many of the dropped ones were real) |

## 3. Research findings used by this plan

| Finding | Why it matters | Where it's used |
|---|---|---|
| **Installed Ultralytics (8.4.116) ships ReID encoders** `yolo26{n,s,m,l,x}-reid.onnx` (448 px input, auto-download) and accepts **any ONNX/TorchScript model that outputs an embedding**. The docs don't say what they were trained on (person vs vehicle). | A real ReID model is a one-line config change to test. **Caveat found in `trackers/utils/reid.py`:** crops are only scaled to 0–1 — no ImageNet mean/std normalisation — so any custom model we export must do its normalisation inside the model graph, or its embeddings will be wrong. | Phase 1 / 3, W2 |
| **TrackTrack, Deep OC-SORT, OC-SORT and FastTrack are all built in** (`cfg/trackers/*.yaml`). TrackTrack's stock thresholds are high (`track_high_thresh` 0.6, `new_track_thresh` 0.7) and `with_reid: False`. Docs: lower `tai_thr` (~0.45) for crowded scenes, raise `angle_weight` for small/fast objects; Deep OC-SORT needs `gmc_method: sparseOptFlow` for moving cameras. | Stock TrackTrack would drop most of our detections (our vehicles score 0.1–0.5), so it must be re-scaled to our detector before a fair comparison. Its **track-aware initialisation** (no new track on a box overlapping an existing one) is also one of our snow defences now that snow isn't trained. | Phase 1–2, W2 |
| **Geo-trax** (MIT licence, `pip install geo-trax`) — an open pipeline for exactly our problem: drone video → detection → tracking → **stabilisation to a reference frame** → georeferencing to an orthophoto. Its stabiliser, **Stabilo**, is a separate library: homography to a reference frame, several feature detectors, and **masks vehicle boxes** during registration. | Our planned scene map (B2) is essentially Stabilo — reuse it instead of writing homography code from scratch. Limit: designed for *quasi-stationary* hovering drones; our pans move much further, so reference-frame chaining (keyframes) is still needed. | Phase 1–2, W3 |
| Geo-trax's own detector (YOLOv8s, 1920 px, 19k aerial images) uses **4 classes: car (incl. vans), bus, truck, motorcycle** at 0.95 mAP50. | Independent support for merging car + van (A1). Also a free second detector to compare against and to use for pre-labelling. Weights licence must be checked before use. | Phase 1, W1 |
| BoxMOT distributes `clip_vehicleid.pt` (CLIP-ReID trained on VehicleID). | The only ready-made *vehicle* ReID weights found — but VehicleID is ground-level front/rear views, not top-down, so transfer to aerial crops is uncertain. VRAI (aerial, 137k images) remains the best fine-tuning source. | Phase 3, W2 |
| UAV MOT papers repeatedly name **motion blur from fast camera moves** as the main cause of fragmented tracks. | Matches our measured detection drop during sudden moves (2.38 → 1.11 boxes/frame). Cheapest countermeasure: **motion-blur augmentation** in the detector fine-tune. | Phase 2–3, W1 |
| Snow datasets exist (Nordic Vehicle Dataset — UAV, snow, CC BY 4.0; SWUAV — severe-weather UAV). | **Not used — team decision (v3): no snow training.** Kept here only as the fallback if snow false positives remain unacceptable after Phase 4 (Phase 5). | Phase 5 (optional) |

## 4. Team split — 4 parallel workstreams

Each workstream owns its files, so members can work in parallel without merge conflicts. Owners are filled in by the team.

| # | Workstream | Owner | Owns (files) | Plan items |
|---|---|---|---|---|
| **W1** | **Detection** | _tbd_ | `ml/detection/*`, new `ml/detection/sample_frames.py`, datasets, detector weights | A1–A4, A6 |
| **W2** | **Tracker & ReID** | _tbd_ | `ml/violation_engine/*.yaml` (tracker configs), ReID models/exports | C1–C3 |
| **W3** | **Camera motion & re-linking** | _tbd_ | new `ml/violation_engine/scene_map.py`, `stitch_tracklets.py` | B2, D1 |
| **W4** | **Evaluation & post-processing** | _tbd_ | `eval_tracking.py`, new `postprocess_tracks.py`, `run_experiment.py`, ground-truth import, `run_tracking()` in `process_recorded_flight.py` | E, D2–D4 |

Why this split: W4 is the easiest to start and unblocks everyone else's measurement; W1 has the longest jobs (GPU training), so it starts data work immediately; W2 and W3 are independent (the tracker vs. what happens after it).

### Shared rules (agreed in Phase 0, apply to everyone)

- **Output schema v2** for `trajectories*.csv`: `frame, time_s, track_id, class, raw_class, cx, cy, w, h, conf, map_x, map_y`. New columns are optional — every reader must tolerate them missing. Hussain's violation logic reads the same file, so the schema is announced before it changes.
- **Ground truth**: the team-provided **1-minute 1080p clip** lives in `ml/data/eval/gt_1080p/` (`clip.mp4` + `gt.csv`). The pipeline always runs on **that exact video file** — boxes are compared in its pixel coordinates, so a re-encoded or resized copy would break the scores. The CARLA flight `20260920_194932` (also 1080p) is the secondary check; labelling a short CARLA segment is optional.
- **Experiment log**: every scored run appends one row to `ml/data/results/experiments.csv` (date, member, clip, one-line change, config path, IDF1, ID switches, MOTA, det. precision, det. recall, false-positive tracks, notes) and is summarised in `docs/main_project_tracker.md`, **failures included**.
- **One change per run, one fresh Python process per run** — a multi-run-per-process sweep has already given wrong results once in this project.
- **Every run writes `run_config.json`** next to its outputs (tracker yaml contents, detector weights, imgsz, conf, git commit).
- **Git**: each member works on a branch off `afif` (e.g. `afif-w1-detection`) and merges small, tested pieces back. Hotspot: `run_tracking()` in `process_recorded_flight.py` — W4 owns it; others ask W4 for a hook rather than editing it.
- **GPU**: W1's training runs take up to ~10 h on the 8 GB RTX 4060. Run them overnight or on a second machine/Colab, so the other workstreams can still run tracking experiments during the day.

## 5. Expected improvement

**These are estimates, not measurements.** No ground-truth score exists yet, so there is no baseline number to improve from. The ranges come from our own measurements where we have them (marked *measured*) and from published results on similar benchmarks otherwise. Published gains on VisDrone/MOT17 usually shrink on our footage (tiny vehicles, moving camera). The first Phase 2 run replaces this table with real numbers.

| Change | Problem it targets | Expected effect | Confidence |
|---|---|---|---|
| **Already done:** class-agnostic NMS | Duplicate boxes / IDs | Unique IDs 3,730 → 714 on the full roundabout (*measured*) | — (done) |
| D2 class voting | Class flicker | Class changes within a track: → **0** (by construction). Per-class counts become meaningful; per-track class accuracy depends on the majority being right | High |
| D4 short / low-confidence track removal | False tracks, flicker, snow blobs | Unique IDs on the roundabout **~695 → ~400** (*measured* for the ≥ 1 s rule); precision of *tracks* up clearly; risk: a few real, briefly-visible vehicles dropped (GT will show how many) | High for the count, medium for correctness |
| C3 thresholds (`new_track_thresh` 0.35 etc.) + A4 gates | False tracks, snow | **~50–80% fewer false-positive tracks**; small recall loss on very faint vehicles | Medium |
| C2 TrackTrack (re-scaled) | Duplicate / spurious new tracks, long occlusions | **0 to +5 IDF1 points** vs BoT-SORT; mostly from fewer spurious new tracks | Low–medium |
| C1 ReID (off vs yolo26-reid vs vehicle ReID) | Recovery after occlusion | **0 to +3 IDF1 points**; at 15–30 px per vehicle, appearance carries little signal. The main value may be removing noise from the current generic classifier | Low |
| B2 + D1 scene map + map-based linking | New IDs after camera moves; camera returning to parked cars | **Parked vehicles: ~50–80% fewer ID switches** when the camera pans away and back. Moving vehicles: a small gain (short gaps only). Also the prerequisite for speed / wrong-way / dwell-time violations | Medium for parked, low for moving |
| A1–A3 detector retrain (3 classes, 960–1280 px, trained to completion, fine-tuned on our non-snow footage, motion-blur augmentation) | Missed vehicles, drop-outs during camera moves | In-scope mAP50 **0.585 → ~0.65–0.72** (0.75 PRD target possible but not likely without SAHI); recall on our footage **+5–15 points**; fewer drop-outs during sudden moves. No specific improvement on snow false positives (not trained) | Medium |
| Detector at imgsz 1920 on 1080p input (vs 1280) | Missed small vehicles | A few more small vehicles per frame (on 720p, 640 → 1280 gave +16%, *measured*); ~2× detection time | Medium |
| D3 gap filling | Blinking boxes, speed jumps | **+1–3 MOTA points** (fills short misses); smoother speed estimates | Medium |
| A6 SAHI (optional) | Tiny vehicles | +5–7 AP on VisDrone (SAHI paper); several× slower | Medium (published) |

**Combined, on the 1-minute ground truth (after Phase 4):**

| Metric | Expected change vs. the Phase 2 baseline |
|---|---|
| ID switches | **−30% to −50%** (most of the gain on parked and continuously-visible vehicles) |
| IDF1 | **+10 to +20 points** |
| MOTA | **+5 to +15 points** (mostly fewer false positives and better recall) |
| False-positive tracks (incl. snow) | **−60% to −85%**; the remainder are mostly snow/roof blobs that score as confidently as real cars — the part that snow training would have fixed |
| Unique IDs vs true vehicle count | From several × too many today to roughly **1.2–1.5×** the true count |
| Class changes per track | **0** |

What will **not** be solved by this plan: a moving vehicle that leaves the view and returns much later keeps getting a new ID. Confident snow false positives (score above the start threshold, lasting over ~1 s) will also remain; how often these occur is measured separately on the ground truth in Phase 2.

## 6. Work order (v4 — detector retrain last)

**Team decision (2026-09-27):** every plan item that doesn't need a new detector is finished first; the **detector retrain is the last step**, followed only by re-tuning and integration. Old phase names are kept in brackets so earlier notes still make sense.

Status: ✅ done · 🔄 in progress · ⏳ to do · ⏸ waiting on data from the team.

### Stage 1 — Setup and quick wins ✅ (was Phase 0–1)

| Item | Status | Result |
|---|---|---|
| Shared rules, GT format, owners | ✅ | Work done by Afif; W1–W4 owners still _tbd_ |
| A4 filter flags (`--class-gates`, `--size-filter`) | ✅ built | Scoring → Stage 3 |
| Tracker configs (BoT-SORT no-ReID / yolo26s-ReID, TrackTrack, Deep OC-SORT, FastTrack) | ✅ | All run; smoke-tested |
| Stabilo drift test | ✅ | Decision: Stabilo SIFT + own keyframe chaining |
| D2 class voting + D4 track filter, `run_config.json`, `import_gt.py` | ✅ | Class flicker → 0 |

### Stage 2 — Baselines and measurement ✅ (was Phase 2, without the W1 dataset)

| Item | Status | Result |
|---|---|---|
| GT delivered + imported (CARLA `20260926_215405`, 40 vehicles) | ✅ | `ml/data/eval/gt_1080p/` |
| `run_experiment.py`, detection P/R, FP-track count | ✅ | Every run logged in `experiments.csv` |
| Baseline scored (raw / stitched / final) | ✅ | IDF1 0.852, MOTA 0.806, 3 ID switches |
| D4 thresholds checked on the GT | ✅ | Kept at 1 s / conf 0.3 |
| C1 ReID + C2 tracker comparison | ✅ | Provisional: TrackTrack ≈ BoT-SORT; ReID adds nothing here |
| Detector input size (640 / 1280 / 1920) | ✅ | 640 best for this footage |
| B2 `scene_map.py` | ✅ | Stopped cars within 0.05–0.16 vehicle lengths; scale drift late in the clip |
| D3 gap filling, HOTA, `regression.py` (moved up from Phase 3) | ✅ | Gap fill on by default (MOTA +1.6–2.3); HOTA 0.630 / 0.631 |

### Stage 3 — Improve the pipeline with the current detector ⏳ (current stage)

| # | Item | WS | Status | Needs | Done when |
|---|---|---|---|---|---|
| 3.1 | Score the A4 filters on the GT (class gates, size filter; one run each) | W1 | 🔄 running | — | Kept or dropped by the numbers |
| 3.2 | Score the Geo-trax detector on the GT (imgsz 640 / 1280 / 1920) | W1 | 🔄 queued | — | Decides the pre-labelling model and whether it beats ours |
| 3.3 | D1 map-based linking in `stitch_tracklets.py` (stationary + moving rules, Section 7-D) | W3 | 🔄 code written, **not yet tested** | — | IDF1 up / ID switches down, no wrong merges |
| 3.4 | Import + check the **second GT clip** (1 min, 30 fps, sudden camera moves); `regression.py` on both clips | W4 | ⏸ | Team delivers the clip | Both clips in the regression table |
| 3.5 | Final tracker + ReID choice (TrackTrack vs BoT-SORT) | W2 | ⏸ | 3.4 | Choice backed by both clips |
| 3.6 | Test the scene map + D1 on camera moves and on pan-away-and-return | W3 | ⏸ | 3.4 | Drift < 1 vehicle length; D1 links correct |
| 3.7 | C3 threshold tuning on the chosen tracker, one parameter per run (`new_track_thresh` first) | W2 | ⏳ | 3.5 | Tuned config committed |
| 3.8 | Vehicle ReID model (`clip_vehicleid` / OSNet on VRAI) — **only if 3.4 shows ReID helps**, otherwise closed with that evidence | W2 | ⏳ | 3.4 | ReID decision backed by numbers |
| 3.9 | Replace the Section 5 estimates with measured numbers | W4 | ⏳ | 3.4 | Section 5 updated |

**Stage 3 exit:** the full pipeline with the current detector is chosen, tuned and scored on both GT clips.

### Stage 4 — Prepare the retrain dataset ⏳ (was Phase 2 W1; no training yet)

Runs in parallel with Stage 3; it needs no GPU except the timing test. **Full guide: `docs/Detector_Retraining_Plan.md`** (dataset survey, frame counts, CARLA recording guide, splits, annotation format, training settings, acceptance criteria).

| # | Item | Details |
|---|---|---|
| 4.1 | Locate the datasets | VisDrone-DET, VisDrone-VID, UAV benchmark (UAVDT), CARLA |
| 4.2 | Convert everything to the 3 classes (`car` incl. van, `bus`, `truck`; others dropped) | VisDrone-DET all (~6,500) · VisDrone-VID every ~8th frame (~3,000) · UAVDT every ~10th frame (~3,000, ignore regions masked) · CARLA auto-labelled (~2,000) · empty road/roof frames as hard negatives (~800). **UAV123 not used** — single-object labels would teach the model to ignore unlabelled cars |
| 4.3 | CARLA auto-labels | Add a segmentation camera to `record_flight.py`; record varied maps, altitudes and fast camera moves; **never** use the GT flights |
| 4.4 | Frame mix | Low and high altitude, straight-down and angled, highways, junctions, roundabouts, car parks (parked cars), dense and sparse traffic, frame-edge vehicles, extra bus/truck frames, day plus some dusk/night/fog, **no snow** |
| 4.5 | Train/val split by **whole sequence** | Val = VisDrone-DET val + ~300 UAVDT + ~300 CARLA frames |
| 4.6 | Motion-blur augmentation | So vehicles survive sudden camera moves |
| 4.7 | Label check + one-epoch timing test | Boxes drawn on samples; confirms the epoch estimate |

This replaces the old plan of hand-correcting 300–500 frames in CVAT: CARLA auto-labels cover most of it.

### Stage 5 — Detector retrain ⏳ (LAST; was Phase 3 W1)

- Fine-tune from the current `best.pt`, **imgsz 960**, ~15,000 images, ~45 epochs, within the **24 h GPU budget** (Ultralytics `time=22`; best weights saved every epoch).
- Report VisDrone val mAP50 vs the 0.585 baseline, and detection P/R on both GT clips (`regression.py`).
- **Done when:** the new weights beat the current ones on GT detection P/R.

### Stage 6 — Re-tune and integrate ⏳ (was Phase 4)

Combine in this order, re-scoring after each step:

1. New detector weights (Stage 5); re-check the input size (640 vs 960).
2. **Re-tune the tracker thresholds** (repeat 3.7) — confidence scores shift after retraining.
3. Scene map + D1 map-based linking.
4. Post-processing D2–D4 + D3 gap filling.
5. Run the full roundabout video and the CARLA flights; final numbers in Section 5 and the tracker; hand **`trajectories_final.csv`** (with `map_x/map_y`) to Hussain for the violation logic.

**Exit:** final pipeline scored on both GT clips, full-video outputs regenerated, docs updated.

### Stage 7 — Optional, only if gaps remain (was Phase 5)

- **W1:** A6 SAHI sliced inference if small vehicles are still missed.
- **W1:** snow training back to the team only if confident snow false positives remain (currently decided against).
- **W2:** UAV trackers (AMOT, DroneMOT) only if the tracker is still clearly the bottleneck.
- **W3:** georeference the scene map to an orthophoto → metres for speed/distance (also Objective 7); reduce the scene map's late-clip scale drift if speed logic needs it.
- **W4:** a deblurring pre-stage for sudden-move frames, if motion-blur augmentation isn't enough.

### Dependencies at a glance

```
Stage 1-2 ✅  setup, baselines, scoring tools, scene map, gap fill, HOTA, regression

Stage 3       3.1 A4 score   3.2 Geo-trax score   3.3 D1 build
                     │               │
              ◄──── team delivers 2nd GT clip (camera moves) ────►
              3.4 import ──► 3.5 tracker/ReID choice ──► 3.7 C3 tune ──► 3.8 ReID model (if useful)
                        └──► 3.6 scene map + D1 on camera moves
Stage 4       dataset prep (parallel to Stage 3; 3.2 picks the pre-label model)
                     │
Stage 5       RETRAIN (24 h)  ◄── starts only when Stages 3 and 4 are done
                     │
Stage 6       new detector ──► re-tune tracker ──► map linking ──► post-processing ──► final score + handoff
```

The only item blocked by the team is the second GT clip (3.4); everything else in Stages 3–4 can start now.

## 7. Technical reference

Detail for each plan item; phases above say *when* and *who*.

### A. Detection (W1)

**A1. Retrain on 3 classes: `car` (car + van), `bus`, `truck`** (decided, v3). This removes six irrelevant VisDrone classes that currently compete for model capacity, and merging van into car removes the car ↔ van flicker at the source. When building the filtered VisDrone set, remap VisDrone `van` → `car`, matching the GT labels.

**A2. Train and infer at higher resolution.** Train at `imgsz=960` or `1280` (VisDrone images are ~1,400–2,000 px wide, so 640 throws away most of the pixels on small vehicles). On the 8 GB RTX 4060 this needs `batch` ≈ 4 at 1280. Inference: 1280 for 720p video; for **1080p** (the GT clip, CARLA) compare 1280 vs 1920 in Phase 1 — 1280 downscales 1080p by 1.5×.

**A3. Fine-tune on our own (non-snow) footage — the main fix for missed and dropped-out vehicles.**
1. Sample ~300–500 frames from CARLA flights and other clear-weather target footage (spread out, not consecutive; never from the GT clip, or the evaluation is contaminated).
2. Pre-label them with the best available model, then correct by hand.
3. Add **100–200 background-only frames of roofs, roads and road markings** (empty label files) as hard negatives — **no snow scenes** (team decision).
4. Add **motion-blur augmentation** so vehicles survive sudden camera moves.
5. Fine-tune on VisDrone + all of the above (mix, don't replace, to avoid forgetting).

**A4. Per-class confidence gates and size sanity.** Keep the detector threshold low (0.1) so weak boxes can still *continue* tracks, but require a higher score to *start* one (C3) and a stricter gate for rare classes (`bus`/`truck` ≥ 0.4). Reject boxes far outside the scene's median vehicle size (< 0.3× or > 4×). Without snow training, this is the main detector-level defence against snow blobs.

**A5. Keep class-agnostic NMS** (`dedupe_boxes()`, IoU 0.5). Done.

**A6. Optional: SAHI sliced inference** (+5–7 AP on VisDrone in the SAHI paper; official YOLO26 + SAHI guide). Several× slower and doesn't plug into `model.track()`.

Not recommended now: switching detector families (RF-DETR, D-FINE, DEIM) — see the YOLO26l assessment in `main_project_tracker.md`, Section 2.2.

#### Snow without snow training

Snow false positives are handled in layers, each catching what the previous one missed:

1. **Detection:** per-class gates and the size sanity filter (A4) drop implausible boxes.
2. **Track start:** `new_track_thresh` ~0.35 (C3) — a weak snow box can't start a track. TrackTrack's track-aware initialisation also refuses new tracks overlapping existing ones.
3. **Track level:** tracks under ~1 s or with mean confidence < 0.3 are removed (D4). Snow blobs typically flicker and score low.
4. **Measurement:** false-positive tracks are counted separately on the GT clip, so the team can see what remains and decide on Phase 5.

What gets through: snow patches the detector scores as confidently and as consistently as a real car. Only training (or a crop classifier) fixes those.

### B. Camera motion (W3)

**B1. Keep `gmc_method: sparseOptFlow`** for the tracker — measured best.

**B2. Stabilised scene-map coordinates.** Per-frame GMC only relates each frame to the previous one, so errors accumulate and there's no notion of "the same place" once the camera pans away and back. Instead register each frame to a keyframe (new keyframe when overlap falls below ~50%) and keyframes to each other, with RANSAC homography and vehicle boxes masked out; convert each box centre to `map_x, map_y`. Start from **Stabilo** (Section 3) rather than writing this from scratch. Stage 4 violation logic needs this anyway: speed, wrong-way and dwell time are meaningless in pixel coordinates when the camera moves.

### C. Tracker (W2)

**C1. Fix ReID.** Measure on the ground truth: (a) ReID off; (b) Ultralytics `yolo26{n,s}-reid.onnx`; (c) a vehicle ReID model — BoxMOT `clip_vehicleid` or an OSNet fine-tuned on **VRAI** (aerial, 137k images, 13k vehicles, 15–80 m altitude) plus positive pairs from our own tracks. Custom models are exported to ONNX **with normalisation inside the graph** (Ultralytics only scales crops to 0–1).

**C2. Compare trackers.** TrackTrack (CVPR 2025): *track-aware initialisation* refuses to start a new track on a detection overlapping an existing one (our duplicate-box and snow-blob pattern); `lost_match_thr` ~0.9 adds a looser rebind pass for long occlusions. Its stock thresholds (0.6 / 0.7) must be scaled down for our detector. Deep OC-SORT needs `gmc_method: sparseOptFlow`. FastTrack is also built in. BoxMOT (StrongSORT, BoostTrack) only if a wider comparison is needed — it requires a manual detect → track loop.

**C3. BoT-SORT parameters (starting point, then tune on the ground truth).**

| Parameter | Now | Recommended | Why |
|---|---|---|---|
| `track_high_thresh` | 0.15 | 0.25 | First-stage matches from confident boxes; weak boxes still used in stage 2 |
| `track_low_thresh` | 0.05 | 0.1 | Stage-2 pool for continuing tracks through weak detections |
| `new_track_thresh` | 0.15 | **0.35** | Start tracks only from confident boxes — main lever against snow false-positive tracks |
| `track_buffer` | 90 | 90–150 | In **frames**: 90 = 3 s at 30 fps but 6 s at 15 fps. Aim for ~3–5 s; longer gaps are the offline pass's job |
| `match_thresh` | 0.6 | 0.8 | A maximum *cost*: 0.6 is **stricter** than stock 0.8 (the sim config's comment has this backwards) |
| `fuse_score` | True | True | No measured difference once duplicates were removed |
| `proximity_thresh` | 0.2 | 0.2 | Lets ReID reach further after sudden moves (measured gain on CARLA) |
| `appearance_thresh` | 0.25 | retune | Only meaningful once C1 gives real vehicle embeddings |
| `gmc_method` | sparseOptFlow | sparseOptFlow | Measured best |
| detector `conf` | 0.1 | 0.1 | Keep low; gating happens in the tracker thresholds |

CARLA needed looser thresholds because the VisDrone model is weak on CARLA renders. Re-measure after the detector fine-tune; keep two configs if needed.

### D. Offline post-processing (W3: D1; W4: D2–D4)

**D1. Map-based tracklet linking** (upgrade `stitch_tracklets.py`): compare positions in map coordinates; **stationary** tracks ending and starting at the same map position with compatible size/class are one parked car regardless of gap; **moving** tracks keep the motion-predicted gate with a longer gap plus ReID similarity when available. Same idea as StrongSORT++'s AFLink and GTA's tracklet connector.

**D2. Per-track class voting.** Each final track gets the confidence-weighted majority class over its lifetime, written to every row (`raw_class` keeps the per-frame value).

**D3. Gap filling.** Interpolate boxes across short gaps (≤ ~1 s) inside a track; linear first, StrongSORT's GSI if worthwhile.

**D4. Track-level false-positive removal.** Drop final tracks shorter than ~1 s or with mean confidence < 0.3. Never filter by "stationary" — parked cars matter for no-parking.

### E. Evaluation (W4)

Twice in this project a plausible count-based conclusion turned out wrong (the beach-flight "domain gap", and the multi-run-per-process tracker sweep). Counting unique IDs cannot tell fewer switches from wrongly merged vehicles.

**Ground truth — provided by the team: 1 minute, 1080p.** Checklist for the delivery (agreed in Phase 0):

- [ ] The **exact source video file** the labels were drawn on (not a re-encode or a resized copy), plus its fps. At 30 fps, one minute is ~1,800 frames.
- [ ] One box per visible vehicle in **every frame**, including parked vehicles and partly visible vehicles at the frame edge (agree a rule, e.g. label if ≥ 50% visible).
- [ ] **One fixed ID per physical vehicle for the whole minute** — including when it leaves and re-enters the view, if that can be told.
- [ ] Class per vehicle: **`car`, `bus`, `truck`** (decided). Vans, minivans and SUVs are `car`. Agree up front where pickups and minibuses go (suggested: pickup → `car`, minibus → `bus`) and label them consistently. Motorcycles, bicycles and pedestrians are not labelled (the pipeline doesn't track them).
- [ ] Format: CVAT export (MOT 1.1 or CVAT XML) or our CSV schema; frame numbering stated (0- or 1-based).
- [ ] Ideally the clip contains **snow, parked cars and at least one sudden camera move**, so all reported problems are measured. If it doesn't, say so — the results then don't cover the missing case.
- [ ] Optional: "ignore" regions (e.g. a car park too dense to label), so the metrics don't punish them.

**Metrics:** IDF1, ID switches, MOTA (`eval_tracking.py`, already written); detection precision/recall on the same frames; false-positive track count; HOTA via TrackEval for the paper.

**Targets:** see Section 5 — confirmed or adjusted once the Phase 2 baseline is measured.

## 8. Decisions needed from the team

1. **Workstream owners** (Phase 0).
2. **GT delivery date and format** (Phase 0, checklist in Section 7-E).
3. **Filtered tracks**: removed from `trajectories_stitched.csv` but listed in `track_summary.csv` (recommended), or kept with a `keep` flag.

Decided (v3):
- No snow training.
- Ground truth is a team-provided 1-minute 1080p clip.
- **3 classes: `car` (includes vans), `bus`, `truck`** — for both the GT labels and the retrain (A1). Until the retrained model exists, the current model's `van` output is mapped to `car` before scoring.

## 9. Is a combination better than BoT-SORT alone?

Yes. Once duplicate boxes were removed, tracker settings changed the result by only a few IDs, while detection fixes changed it by thousands. Each stage fixes a failure the others can't: the detector decides what exists (missed vehicles); the gates, start threshold and track filtering keep snow out of the tracks; the scene map decides where things are when the camera moves or returns; the tracker links frame to frame; the offline pass fixes what the online tracker can't know yet (a vehicle reappearing 20 s later, the class across the whole track). Research-grade UAV trackers (AMOT, DroneMOT, VORTEX-MOT) are revisited only in Phase 5.

## 10. Target architecture (upcoming)

**Note (team decision, 2026-09-27):** this is the architecture the project will follow. It is built in the upcoming sections of the work, after the current Phase 2–4 detection/tracking improvements. It extends today's pipeline (detector → GMC → BoT-SORT → stitching → post-processing) with adaptive tiling, conditional enhancement, a pothole/crack segmentation branch, per-frame calibration to real-world units, and the zone-based violation engine.

```
                                  UAV VIDEO INPUT
                                           │
                                           ▼
                                   Frame Extraction
                                           │
                                           ▼
                          ┌───────────────────────────┐
                          │  Scene / Quality Analysis  │
                          │  (checks light, blur,      │
                          │   object density)          │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                                Adaptive Tiling
                        (more tiles where road is busy,
                         fewer tiles where it's empty)
                                        │
                        ┌───────────────┼───────────────┐
                        ▼               ▼               ▼
                     Tile 1          Tile 2           Tile N
                        │               │               │
                        ▼               ▼               ▼
                Conditional       Conditional      Conditional
                Enhancement       Enhancement      Enhancement
              (only if dark/       (skip if           (skip if
               blurry tile)         already fine)      already fine)
                        │               │               │
                        └───────────────┼───────────────┘
                                        ▼
                        ┌───────────────────────────────┐
                        │      Enhanced Tile (shared)     │
                        └───────────────┬───────────────┘
                                        │
                ┌───────────────────────┴───────────────────────┐
                ▼                                               ▼
       EVERY FRAME                                    EVERY Nth FRAME
                │                                               │
                ▼                                               ▼
        YOLO26L (vehicles)                          YOLO26-seg (potholes/cracks)
                │                                               │
                ▼                                               ▼
        Tile Detections                              Pixel-Level Mask Output
                │                                     (pothole / crack outline)
                ▼                                               │
     Coordinate Restoration                                     ▼
     (map boxes back to full frame)                  Mask Coordinate Restoration
                │                                     (map mask back to full frame)
                ▼                                               │
      Cross-Tile Fusion                                         ▼
      (NMS / Weighted Box Fusion)                    Mask → Real-World Size
                │                                     (reuses calibration below to
                ▼                                      convert pixel area → m²)
   ┌─────────────────────────────┐                              │
   │  Camera Motion Compensation  │                              ▼
   │  (GMC — corrects for drone   │                   Severity Classification
   │   movement between frames)   │                   (small / medium / large)
   └─────────────┬───────────────┘                              │
                 │                                               ▼
                 ▼                                   Duplicate-Pothole Check
         BoT-SORT Tracking                          (same GPS location already
   (+ Edge-Guided Feature Enhancement                 logged? skip if yes)
    for scale changes as drone                                   │
    moves closer/farther)                                        ▼
                 │                                     Road Condition Report
                 ▼                                    (location, size, severity,
      Motion-Corrected Trajectories                    timestamp, GPS)
                 │
                 ▼
   ┌─────────────────────────────┐
   │  Per-Frame Dynamic           │
   │  Calibration (pixel →        │◄──── (shared with pothole branch)
   │  real-world, updates every   │
   │  frame for drone height/     │
   │  angle changes)              │
   └─────────────┬───────────────┘
                 │
                 ▼
         Vehicle Trajectories
     (real-world position, speed,
      direction, over time)
                 │
   ┌─────────────┼─────────────┐
   ▼             ▼             ▼
Parking       Lane          Speed
Logic         Logic         Logic
(distance    (lane region  (compare
 to nearby    from seg.,    real speed
 structures)  crossing      vs limit)
   │             │             │
   └─────────────┼─────────────┘
                 ▼
      Zone-Sequence Violation Engine
   (violation = pattern of zones a
    vehicle passes through, matched
    against traffic rules)
                 │
                 ▼
        Frame-Counter Debounce
   (waits a few frames before
    confirming, avoids false alarms)
                 │
                 ▼
          Evidence + Timestamp
   (violation type, track ID,
    location, time, snapshot)
```

## 11. Sources

- Ultralytics tracking docs (trackers, ReID models, tuning tips): https://docs.ultralytics.com/modes/track
- Ultralytics YOLO26 + SAHI guide: https://docs.ultralytics.com/guides/sahi-tiled-inference
- SAHI paper: https://arxiv.org/abs/2202.06934
- TrackTrack, CVPR 2025: https://openaccess.thecvf.com/content/CVPR2025/html/Shim_Focusing_on_Tracks_for_Online_Multi-Object_Tracking_CVPR_2025_paper.html · code https://github.com/kamkyu94/TrackTrack
- StrongSORT (AFLink, GSI): https://arxiv.org/abs/2202.13514
- GTA global tracklet association: https://arxiv.org/abs/2411.08216
- BoxMOT (trackers, ReID weights incl. `clip_vehicleid`): https://github.com/mikel-brostrom/boxmot
- BoostTrack++: https://arxiv.org/pdf/2408.13003
- VRAI aerial vehicle ReID dataset: https://github.com/JiaoBL1234/VRAI-Dataset · paper https://arxiv.org/pdf/1904.01400
- Geo-trax (framework, Stabilo, pretrained detector): https://github.com/rfonod/geo-trax · paper https://arxiv.org/abs/2411.02136 · journal https://www.sciencedirect.com/science/article/pii/S0968090X25002098
- Nordic Vehicle Dataset (UAV, snow — reference only, not used): https://arxiv.org/pdf/2304.14466
- SWUAV severe-weather UAV vehicle dataset (reference only, not used): https://doi.org/10.3390/s26092793
- UAV MOT with video deblurring: https://link.springer.com/chapter/10.1007/978-981-95-4294-9_59
- Multi small-object tracking with dual motion modelling (Kalman + optical flow): https://pmc.ncbi.nlm.nih.gov/articles/PMC12473289/
- AMOT (AAAI 2026, UAV MOT): https://ojs.aaai.org/index.php/AAAI/article/view/37720
- UAV MOT benchmarks overview (VisDrone/UAVDT/MMOT): https://arxiv.org/html/2510.12565v1
