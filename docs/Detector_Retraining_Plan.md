# Detector Retraining Plan

Status: **v1.1** (2026-09-27; team answers to the open questions folded in, Section 9). Owner: Afif. Part of `docs/Vehicle_Detection_Tracking_Improvement_Plan.md` (v4), **Stage 4** (dataset) and **Stage 5** (retrain). Team decision: the retrain is the **last** step. Stage 3 of the improvement plan (tracker, ReID, map linking, thresholds) is finished first; this dataset is prepared in parallel.

**In short:** fine-tune our current YOLO26l (`ml/data/results/full_train/train/weights/best.pt`) on about **15,300 images** from four sources, at **imgsz 960**, for about **45 epochs** within a **24-hour GPU budget**. Classes: **`car` (incl. van), `bus`, `truck`**. Judge the result on our own ground-truth clips, not only on VisDrone.

---

## 1. Why retrain

On our CARLA ground truth (tracker Section 2.3), the tracker is no longer the main problem. The top trackers differ by 1–2 ID switches, while about **10% of vehicles are still missed** and most remaining errors are detection errors. The current model:

- was trained only on VisDrone-DET, at 640 px, with 10 classes, and stopped early at epoch 80 (in-scope mAP50 0.585);
- only works at VisDrone-like vehicle sizes: at 1280 / 1920 px input, results on our 1080p CARLA clip got *worse* (HOTA falls, false tracks go 7 → 45 → 62);
- flickers between car and van, and loses vehicles during fast camera moves (motion blur).

The retrain fixes these with our own kind of footage (CARLA), more varied real drone video (VisDrone-VID, UAVDT), 3 classes, higher input resolution and motion-blur augmentation.

---

## 2. Datasets available

Surveyed on 2026-09-27 in `D:\Main-Project\project data\` (and the Ultralytics VisDrone copy in `D:\Main-Project\datasets\VisDrone\`).

| Dataset | Location | Size | Vehicle labels (car / van / truck / bus) | Notes |
|---|---|---|---|---|
| **VisDrone2019-DET train** | `project data\VisDrone2019-DET-train (1)\` | 6,471 images, mostly ~1,400 px wide | 144,867 / 24,956 / 12,875 / 5,926; 4,238 images contain a bus or truck | Current training set. Also 8,813 "ignored region" boxes |
| **VisDrone2019-DET val / test-dev** | `datasets\VisDrone\images\val`, `test` | 548 / 1,610 images | — | The val set is the one the 0.585 baseline was measured on |
| **VisDrone2019-VID train** | `project data\VisDrone2019-VID-train\` | 56 sequences, **24,201 frames**, 1344–2720 px wide, 58–1,424 frames/sequence (median 398) | 505,301 / 46,940 / 30,498 / 9,653; 13,683 frames contain a bus or truck | Consecutive frames are near-identical, so we sample |
| **VisDrone2019-VID val** | `project data\VisDrone2019-VID-val\` | 7 sequences, 2,846 frames | 31,821 / 6,842 / 1,359 / 264 | The same videos as the MOT-val copy in `ml/data/datasets/`. The `sequences` folder also holds a stray `uav0000305_00000_v.zip` with no annotations; ignore it |
| **UAV benchmark (UAVDT, `UAV-benchmark-M`)** | `project data\UAV-benchmark-M\` (frames), `...\UAV-benchmark-MOTD_v1.0\GT\` (boxes), `...\M_attr\` (attributes) | 50 sequences, **40,735 frames**, 1024×540, 265–2,035 frames/sequence. Official split: train 30 seqs / 24,143 frames, test 20 / 16,592 | Train: car 394,633 / truck 17,491 / bus 10,787 (1,283 / 70 / 31 tracks); no van class | Labels verified 2026-09-28, see **UAVDT notes** below |
| **CARLA** | being recorded by the team | — | — | Section 5 is the guide |
| VisDrone2019-MOT-train | `project data\VisDrone2019-MOT-train.zip` | same byte size as the VID-train zip | — | **Duplicate** of VID-train (the same videos, with tracking labels). Don't use both |
| UAV123 | not in the folder | — | — | **Not used.** Single-object tracking: only one target is labelled per frame, so every other car in the frame would be trained as background |

**UAVDT notes** (label check, 2026-09-28):
- Box format per line of `<seq>_gt_whole.txt`: `frame, id, left, top, width, height, out_of_view, occlusion, category` (1 car, 2 truck, 3 bus). `<seq>_gt_ignore.txt` has the ignore regions in the same box format. Every box lies inside the 1024×540 image.
- Checked on a middle frame of every sequence: boxes are aligned and nearly all vehicles are labelled, including at night and in fog.
- **Exclude `M0207` (train)**: only 0–3 boxes per frame when ~15 vehicles are visible, and frames 572–885 have no boxes at all. Training on it would teach the model that cars are background.
- **UAVDT `truck` includes some pickups and vans** (seen in `M0606`, `M1001`, `M1301`), which our scheme labels `car`; a few `bus` tracks are van-like minibuses. Labels are per track, so fix them per track: the train split has only 70 truck and 31 bus tracks, so one crop per track can be reviewed in step 6 and relabelled `car` where needed.
- Faraway vehicles near the horizon in angled views (e.g. `M0201`, `M0301`) are sometimes unlabelled. Minor; watch for it in the step 6 label check.
- The attribute file `test\M0701 _attr.txt` has a space in its name.
- Compared with our GT labelling rules (`Ground_Truth_Creation_Guide.md` Section 6): same tight, shadow-free boxes and 3 classes; differences are the truck/pickup rule above, unlabelled parked cars at the edges in some sequences, and far vehicles left unlabelled without an ignore region. UAVDT's own flags (`README.md` in the zip): out-of-view 1 no-out / 2 medium-out / 3 small-out (725k / 37k / 37k boxes); occlusion 1 none / 2 large / 3 medium / 4 small (715k / 9k / 10k / 64k). Candidate filter to match our "≥ 50% visible" edge rule: drop `out-of-view = 2` boxes. Check this by eye in step 6 before relying on it. VisDrone has a truncation flag (VID train vehicles: 0 = 557k, 1 = 36k), where 1 means partly cut off (up to 50%), so all its vehicle boxes already meet the rule.

Existing CARLA recordings (`simulation/data_export/recorded_flights/`): `20260926_215405` is the **ground-truth clip** and is never used for training. `20260926_214226` and `20260926_214717` were recorded in the same session over the same streets, so they are excluded too, to avoid leaking the test scene. `20260920_194932` (Town10HD, 899 frames) has no labels; new CARLA recordings with automatic labels replace it.

---

## 3. Recommended training mix (24-hour budget)

**How the size was chosen.** The last run (YOLO26l, 640 px, batch 8, RTX 4060 8 GB) did 80 epochs over 6,471 images in 7.3 h, about **20 images/s**. At 960 px each image costs about 2.25× as much, so about **9 images/s**, or about 700,000 image passes in 22 h of training (2 h are left for validation and overhead). A fine-tune from `best.pt` needs about 40–50 epochs, which gives **about 15,000 training images**. Run a one-epoch timing test first (Section 8, step 8) and adjust.

| Source | Train images | How to select | Share |
|---|---|---|---|
| VisDrone-DET train | **6,471** (all) | Everything; it anchors the comparison with the 0.585 baseline | 42% |
| VisDrone-VID train | **3,000** | Uniform stride (~every 8th frame), **at most 120 frames per sequence** so long videos don't dominate; when two candidate frames are close, prefer the one with a bus or truck | 20% |
| UAVDT | **3,000** | Uniform stride (~every 8th frame), at most 120 per sequence, across the official train sequences **except `M0207`** (29 sequences); mask the ignore regions (Section 4) | 20% |
| CARLA | **2,000** | Section 5 | 13% |
| Hard negatives (no vehicles) | **800** | **All from CARLA** (team decision): empty roads, roofs, road markings, car-park lines, bus stops, containers, beach, water. Empty label files | 5% |
| **Total** | **~15,300** | | |

**Fallback, only if the UAVDT labels turn out unusable:** replace its 3,000 frames with +1,500 VisDrone-VID (stride ~5, cap 180 per sequence) and +1,500 CARLA. UAVDT is still worth getting: it is the only source with real **night and fog** footage and labelled altitude/view attributes.

**Sampling rules for every source:**
- Take whole sequences into either train or val/test, **never both** (Section 6).
- Space the frames kept from one video evenly; never take consecutive frames.
- Drop frames where more than half the image is an ignore region.
- Bus and truck are rare (together about 10% of VisDrone-DET vehicle boxes). Prefer frames that contain them, but don't duplicate frames.

---

## 4. Class scheme and label mapping

Training classes, in this order (matching the GT labels): **`0 car`, `1 bus`, `2 truck`**. Vans, minivans, SUVs and pickups count as `car`; minibuses as `bus`. Motorcycles, bicycles, tricycles and people are **not labelled**: our pipeline doesn't track them and the GT doesn't contain them.

| Source label | → Class |
|---|---|
| VisDrone `4 car`, `5 van` | `car` |
| VisDrone `9 bus` | `bus` |
| VisDrone `6 truck` | `truck` |
| VisDrone `0 ignored region` | no box; the region is **filled with grey (114,114,114)** in the image, so unlabelled vehicles inside it aren't learned as background |
| VisDrone `1 pedestrian`, `2 people`, `3 bicycle`, `7 tricycle`, `8 awning-tricycle`, `10 motor`, `11 others` | dropped |
| UAVDT `1 car` / `2 truck` / `3 bus` | `car` / `truck` / `bus`, except truck/bus **tracks** reviewed as pickups or vans → `car` (per-track override list, UAVDT notes in Section 2) |
| UAVDT `gt_ignore.txt` regions | filled with grey, as above |
| CARLA semantic tag `14 Car` | `car` (CARLA vans such as the Sprinter and T2 carry this tag) |
| CARLA `16 Bus` | `bus` |
| CARLA `15 Truck` | `truck`, **except pickups** (e.g. Cybertruck) → `car` via the blueprint override table |
| CARLA `18 Motorcycle`, `19 Bicycle`, `12 Pedestrian`, `17 Train` | not labelled |

The pipeline selects vehicle classes **by name** (`run_tracking()`, `VEHICLE_NAMES`), and post-processing and the class gates also work by name. So the new 3-class weights plug in with no code change.

---

## 5. CARLA dataset guide

### 5.1 Target size

| Split | Labelled frames | Flights | Maps |
|---|---|---|---|
| Train | **2,000** + **800** negatives | ~16–20 flights of 2–3 min | Town01, Town02, Town03, Town04, Town10HD* (the additional maps are **not** used — team decision) |
| Val | **300** | 3 separate flights | Same maps as train, different routes |
| Test | **300** | 3 flights | **Town05 only**, a map never used for training, to measure generalisation |

\* Town10HD is where our GT clip was recorded. Keep training flights away from the GT route (the beach-front road and the downtown avenue filmed in `20260926_215405`).

**Per flight:** record at about 15–25 fps, then keep **at most ~120 frames**, at least **1 s apart**, so frames differ. Close-together frames add training time, not information.

### 5.2 Camera and altitude

| Variable | Values | Share |
|---|---|---|
| Altitude (vehicle size at 1080p, 90° FOV) | **Low 20–40 m** (vehicles ~150–300 px, like our GT clip) | 30% |
| | **Medium 50–80 m** (~60–150 px) | 40% |
| | **High 100–150 m** (~20–60 px, like VisDrone and the roundabout video) | 30% |
| Camera pitch | Straight down (−90°) | 60% |
| | Angled (−45° to −70°) | 40% |
| FOV | 90° (current recorder) | 80% |
| | 60–70° | 20% |
| Resolution | **1920×1080**, the same as the GT clips | 100% |

### 5.3 Camera motion

| Motion | Share | Why |
|---|---|---|
| Hovering / nearly still | 40% | Parked and queued vehicles, dense scenes |
| Smooth forward flight / slow pan | 40% | Normal survey footage |
| **Sudden moves**: fast yaw, fast pan, quick climb/descent | **20%** | Motion blur and scale change are what make vehicles disappear today |

Turn on CARLA's own motion blur on the RGB camera: `motion_blur_intensity` 0.3–0.8, `motion_blur_max_distortion` ~0.35. The labels come from the segmentation camera, which has no blur, so each box stays at the vehicle's true position while the image is blurred. That is exactly what the model needs to learn.

### 5.4 Scenes and traffic

| Scenario | Where | Target share |
|---|---|---|
| Urban streets and junctions (traffic lights, turning vehicles) | Town10HD, Town03, Town05 | 30% |
| Roundabout | Town03 (centre about x = −83.4, y = 6.4) | 10% |
| Highway / multi-lane, fast traffic | Town04 | 15% |
| **Car parks and kerbside parking** (rows of parked cars) | Town05, Town10HD, plus vehicles spawned as parked | 15% |
| Queues at lights, dense stop-and-go | any junction | 15% |
| Narrow residential / small-town roads, sparse traffic | Town01, Town02 | 15% |

- **Traffic density:** mix dense scenes (80–150 vehicles spawned near the filmed area) with sparse ones (under 10 in view).
- **Parked vehicles:** CARLA's traffic manager only drives. Spawn extra vehicles in parking spots or at the kerb with autopilot off. The no-parking violation depends on parked cars being detected.
- **Every frame is automatically labelled**, including stopped, parked, partly hidden and edge vehicles (Section 5.7).

### 5.5 Vehicle types (classes)

Use every 4-wheel CARLA vehicle blueprint, with **random paint colours** (the `color` attribute). Spawn by class so the rare classes are well represented:

| Class | CARLA blueprints (0.9.15) | Spawn share |
|---|---|---|
| `car` | all passenger cars; vans (Mercedes Sprinter, VW T2), police cars, **pickups (e.g. Tesla Cybertruck) — always `car`** (team decision, matches the GT rule) | ~70% |
| `truck` | CarlaCola, European HGV, fire truck, **Ford ambulance** (CARLA calls it a van, but from above it is a box body like a small delivery truck; team decision 2026-09-29), and any other blueprint tagged Truck | ~18% |
| `bus` | Mitsubishi Fuso Rosa, and any other blueprint tagged Bus | ~12% |
| (not labelled) | motorcycles, bicycles | a few, so the model learns they are *not* cars |

Before recording, check the class of each blueprint once. Spawn every blueprint, take one frame, and read the semantic tag (instance-segmentation R channel) or `bp.get_attribute('base_type')`. Record the result in the dataset README. **If CARLA tags a pickup as `Truck` (15), relabel it `car`**: keep a small `BLUEPRINT_CLASS_OVERRIDE` table (blueprint id → class) in the labelling code, applied to the actor behind each instance ID.

### 5.6 Weather and lighting

CARLA has no snow, which matches the team's no-snow decision.

| Condition | CARLA setting | Share |
|---|---|---|
| Clear / cloudy day | `ClearNoon`, `CloudyNoon`, sun altitude 30–70° | 45% |
| Low sun, long shadows | `ClearSunset`, `CloudySunset`, sun altitude 5–20° | 15% |
| Wet road / rain | `WetNoon`, `WetCloudyNoon`, `MidRainyNoon`, `SoftRainNoon` | 15% |
| Fog / haze | `fog_density` 20–50, `fog_distance` 10–40 | 10% |
| Dusk / night with street lights | sun altitude −10° to 0° (dusk) and below −10° (night), vehicle lights on | 15% |

Vary the **sun direction** as well. Vehicle shadows at low sun are a known source of double boxes.

### 5.7 Edge cases the dataset must contain

- **Vehicles cut off at the frame edge.** Box the visible part; drop it if less than **50%** of the vehicle is inside the frame (the GT rule, `Ground_Truth_Creation_Guide.md` Section 6; was 25% until 2026-09-28).
- **Partial occlusion** by trees, bridges, overpasses or other vehicles. Box the visible part if at least 25% is visible.
- **Densely packed vehicles:** queues and car-park rows, where boxes touch.
- **Very small vehicles** at high altitude. Keep boxes of at least 8×8 px; drop smaller ones.
- **Turning / diagonal vehicles.** Axis-aligned boxes get larger and overlap neighbours.
- **Vehicles in deep shadow**, and bright or white vehicles on bright concrete.
- **Motion-blurred frames** from sudden moves.
- **Scale change within a flight** (climb/descent).
- **Look-alikes with no vehicle:** roofs, rooftop AC units, road markings and arrows, car-park lines, bus shelters, containers and dumpsters, beach and water. These go into the 800 hard-negative frames.
- Motorcycles and bicycles present but **not labelled**.

### 5.8 Annotation requirements and format

**How labels are made: automatically, from CARLA's instance-segmentation camera.**
1. Attach a `sensor.camera.instance_segmentation` to the drone with **exactly the same transform, resolution and FOV** as the RGB camera.
2. Pair RGB and segmentation images by **`image.frame`** (the simulator tick), or record in synchronous mode (`synchronous_mode=True`, `fixed_delta_seconds=0.05`). Drop any frame without an exact pair. The current `record_flight.py` streams asynchronously, so a dataset mode is needed.
3. In the segmentation image, the R channel is the semantic tag and G+B form the instance ID. For each vehicle instance (tags 14/15/16), the box is the **tight rectangle around its visible pixels**. This matches how the GT is hand-labelled: the visible extent, not a projected 3D box.
4. Apply the rules in 5.7: at least 8×8 px; at the frame edge at least 50% inside the frame, when occluded at least 25% visible (compare the visible pixel count with the actor's projected 3D box, or with a frame where it isn't occluded).
5. **Map-baked parked cars** (part of the map, not actors) still show up as tag 14 pixels, but have no actor behind them, so the blueprint override and the visibility check can't be applied. Either unload them (`world.unload_map_layer(carla.MapLayer.ParkedVehicles)` on `_Opt` maps) and spawn parked cars as actors, or box them from pixels as `car` and skip the checks. Never leave them unlabelled: that teaches parked cars as background.
6. **Only frames with a label file are training images.** The seg camera captures every 0.2 s, so only about 1 RGB frame in 10 has a matching seg image and gets `autolabel/labels/<frame>.txt` (an *empty* file = checked, no vehicles). The other RGB frames have no label file and must **not** be copied into the dataset: YOLO would read them as "no vehicles" and learn real cars as background. `build_retrain_dataset.py` takes frames from `autolabel/labels/`, never from `frames/`.
7. **Check against the GT style** before recording at scale: draw the auto-labels on one short test flight (a video, like the VisDrone/UAVDT check videos) and compare with the hand-labelled boxes in `new_validation_annotation.xml`. The auto boxes should be about as tight and exclude shadows.

**Format (YOLO)** — written by `ml/detection/export_carla_flight.py <flight> --split train|val|test|negatives` after the flight's `check.mp4` has been checked (the split is the folder; a flight can only be in one):
```
ml/data/datasets/carla_det/
  <split>/<flight_id>/
    images/00123.jpg           # RGB frame, 1920×1080, copied unchanged
    labels/00123.txt           # one line per vehicle: "<class> <cx> <cy> <w> <h>", normalised 0-1
    meta.csv                   # frame, time, map, weather, sun altitude, fog, camera height/pitch/speed, n_vehicles
    gt.csv                     # the flight's full track labels (all labelled frames), for tracking checks
    source.json                # source flight, export settings, counts, recording metadata
```
Export rules: only labelled frames; >= 1 s apart; <= 120 per flight; camera >= 10 m (take-off/landing frames are grey road surface).
- Classes: `0 car`, `1 bus`, `2 truck`. Hard-negative frames get an **empty** `.txt` file.
- `meta.csv` lets us balance the mix (Sections 5.2–5.6) and report results per condition (for example, recall at night).

**Quality check** (before a flight is accepted):
- Draw the boxes on 50 random frames per flight.
- Check that every visible vehicle is boxed, there are no boxes on shadows or on motorcycles, bus and truck classes are right, and boxes are tight.
- Target: under 2% wrong or missing boxes. Record the result in the README.

---

## 6. Train / validation / test split

**Always split by whole sequence or flight, never by frame.** Frames from the same video are near-copies; spreading one video over train and val would make validation meaningless.

| Split | Contents | Size | Used for |
|---|---|---|---|
| **Train** | Section 3 mix | ~15,300 | Training |
| **Val** | VisDrone-DET val (548) + VisDrone-VID val, every ~10th frame (~285) + 5 held-out UAVDT sequences, ~60 frames each (~300; use the official train/test lists shipped with the annotations if present, covering day, night and fog) + CARLA val flights (300) | ~1,430 | Choosing `best.pt` during training (early stopping) |
| **Test** | VisDrone-DET test-dev (1,610) + CARLA Town05 flights (300) | ~1,900 | Final report only, never for decisions |
| **Our GT clips** | `ml/data/eval/gt_1080p` + the coming camera-motion clip | 2 clips | **The acceptance decision** (Section 7), with `regression.py` |

---

## 7. Training configuration and acceptance

**Configuration** (Ultralytics 8.4.116; new script `ml/detection/train_retrain.py`):

| Setting | Value | Why |
|---|---|---|
| Start weights | current `best.pt` | Already knows aerial vehicles; the 10-class head is replaced by a 3-class one |
| `imgsz` | 960 | More detail on small vehicles; fits the 24 h budget (1280 would allow only ~25 epochs) |
| `batch` | 4–6 (largest that fits in 8 GB) | |
| `epochs` / `time` | `epochs=60`, **`time=22`** | `time` ends training within 22 h with a finished learning-rate schedule |
| `patience` | 15 | Stop early if validation stops improving |
| Augmentation | mosaic 1.0 (`close_mosaic=10`), `scale=0.5`, `fliplr=0.5`, `flipud=0.5` (fine for top-down views), default HSV | |
| **Motion blur** | Custom `MotionBlur` (Albumentations, kernel 7–25 px, random direction) on ~20% of images | Ultralytics' built-in blur is too weak (p = 0.01) |
| `cache` / `workers` | `False` / 4 | 15k images at 960 don't fit in RAM |
| Logging | `run_config.json` + a tracker entry | Project rule |

**Acceptance: the new weights replace the current ones only if all of these hold:**
1. **Detection recall on both GT clips goes up** and precision does not go down (`regression.py --weights <new best.pt>`).
2. **HOTA on both GT clips** is at least as good as the current pipeline (0.630 on `gt_1080p`).
3. **VisDrone-DET val mAP50 (3 classes)** at least matches the current model **re-scored on the same 3 classes**. First re-score the current model with car+van merged; the old 0.585 used a different class set and isn't directly comparable.
4. No large drop on the CARLA Town05 test set, or on night/fog (per-condition results from `meta.csv`).

After acceptance, go to **Stage 6** of the improvement plan: re-check the input size (640 vs 960), then re-tune the tracker thresholds (confidence scores shift after retraining).

---

## 8. Step-by-step checklist

| # | Step | Who | Blocks | Status |
|---|---|---|---|---|
| 1 | Download the **UAVDT annotations** (`UAV-benchmark-MOTD_v1.0`) into `project data\UAV-benchmark-M\` | Afif | UAVDT part | ✅ 2026-09-28: attributes (`M_attr`) + box labels (`UAV-benchmark-MOTD_v1.0\GT`) downloaded and verified; see the UAVDT notes below |
| 2 | ~~Extract `AdditionalMaps_Latest.zip`~~: **not doing** (team decision); CARLA uses Town01–05 + Town10HD | — | — | ✖ dropped |
| 3 | `record_flight.py` dataset mode: instance-segmentation camera, frame pairing / synchronous mode, motion blur, weather/sun/altitude/pitch settings, `meta.csv`, parked-vehicle spawning, class-weighted spawning, pickup → `car` override | CARLA team | CARLA part | ✅ |
| 4 | Record the CARLA flights (Section 5.1), including the 800 hard-negative frames; QA each flight | CARLA team | CARLA part | ✅ declared done 2026-10-02: 1,248 train (incl. 62 negatives), 102 val, 177 test |
| 5 | `ml/detection/build_retrain_dataset.py`: convert VisDrone-DET/VID, UAVDT and CARLA to 3-class YOLO, grey-fill ignore regions, sample per Section 3, split per Section 6 → `ml/data/datasets/retrain_v1/` + `data.yaml` + `manifest.csv` (source, sequence, frame, split) | Afif | Training | ✅ 2026-10-02: train 13,719 / val 1,211 / test 1,787 (see tracker) |
| 6 | Label check: boxes drawn on ~200 random training images across all sources | Afif | Training | 🔄 spot check done (one image per source, all correct); full 200-image check not done |
| 7 | Re-score the **current** model on the new val/test sets and GT clips (3 classes): the baseline to beat | Afif | Acceptance | ⏳ |
| 8 | One-epoch timing test at imgsz 960; fix the batch size and epoch count | Afif | Full run | ⏳ |
| 9 | **Full retrain (24 h)**, only after Stage 3 of the improvement plan is done | Afif | — | ⏳ last |
| 10 | Evaluate (Section 7); log everything in `main_project_tracker.md`; continue with Stage 6 | Afif | — | ⏳ |

---

## 9. Team decisions (2026-09-27)

1. **UAVDT labels:** Afif downloads `UAV-benchmark-MOTD_v1.0`; UAVDT stays in the mix (3,000 frames). The fallback in Section 3 applies only if the labels turn out unusable.
2. **Hard negatives:** all 800 come from CARLA (empty roads, roofs, markings, car parks, beach, water), recorded with the other CARLA flights.
3. **Additional CARLA maps:** not used. CARLA data comes from Town01–05 and Town10HD; Town05 stays reserved for the CARLA test set.
4. **Pickups** (e.g. Tesla Cybertruck) are labelled **`car`**, the same rule as the GT, even where CARLA tags them `Truck`.
5. (2026-09-29) The **Ford ambulance** is labelled **`truck`**: CARLA's base type is van, but from the drone it is a box body the same shape as a small delivery truck (the GT's "large delivery truck → truck"), and VisDrone/UAVDT label such vehicles truck. Implemented in `carla_autolabel.py`'s `BLUEPRINT_CLASS_OVERRIDE`.
