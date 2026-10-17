# Road-Surface Anomaly Model: Training and Testing Guide

**For:** Afif, running training and testing on a separate machine (Build Plan **M9**).
**Written:** 2026-10-10. **Deadline for results:** Sunday 2026-10-11, 20:00, so they can go into the
final build before 22:00.

This guide takes you from a fresh machine to trained, tested models and a results table. The code
is already in the repo (`ml/pothole/`). You only need the data, the environment and the commands
below.

---

## 1. Goal and the target to beat

| | |
|---|---|
| Task | Instance segmentation of road-surface anomalies: **pothole, crack, waterlogging, debris** (the 4 anomaly types in `schemas/event.schema.json`) |
| Base model | YOLO26l-seg (Ultralytics 8.4.174) |
| Our change (decision #2) | **DSConv + SimAM + GELU** spliced into the backbone and neck, after arXiv 2505.04207 |
| Pass mark (PRD §27.4) | **F1 ≥ 0.65 per category**, measured by `ml/pothole/evaluate.py` at the deployed confidence |
| Main data | PothRGBD: 1,000 RGB-D images, potholes only, YOLO-seg format |

**What we already measured on our machine** (RTX 4050 6 GB, so these are the numbers to compare against):

| Run | Split | Result |
|---|---|---|
| 2025 stock baseline (`runs/baseline`, 71 epochs, imgsz 640, batch 4) | random 800/100/100 | box P 0.936 / R 0.838 / mAP50 0.923; mask P 0.956 / R 0.856 / mAP50 0.927 (val, from the training log) |
| Same weights through `evaluate.py` (conf 0.25, mask IoU 0.5) | random split, test (100 images) | pothole **F1 0.887** (TP 102, FP 16, FN 10) |
| Modified model smoke run (1 epoch, 5% of data, imgsz 320) | grouped | trains, saves and reloads with its DSConv/SimAM layers; 91 s |

**Important:** the random split leaks. The images were shot in bursts, and 327 of 999 neighbouring
pairs are ≤ 10 s apart, which puts near-copies of test images into train. All new results use the
**session-grouped split** (§4.2). The 0.887 above is therefore an optimistic reference, not the
number to beat. Experiment E0 re-measures it fairly.

**Paper reference only** (claims, not our measurement): on PothRGBD with YOLOv8n-seg, the paper reports
P 91.9→93.7, R 85.2→90.4 and mAP50 91.9→93.8 from DSConv+SimAM+GELU ([arXiv 2505.04207](https://arxiv.org/abs/2505.04207)).

---

## 2. Machine setup (once)

**Needs:** an NVIDIA GPU with ≥ 6 GB (8–24 GB is better: bigger batch and image size), recent
NVIDIA driver, Python **3.12**, Git, about 30 GB free disk (more if you add the extra datasets in §5).

```powershell
git clone <repo url> main-project
cd main-project
git checkout pranav            # the branch with ml/pothole/ as of 2026-10-10
py -3.12 -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install ultralytics==8.4.174 opencv-python numpy pyyaml
python -c "import torch, ultralytics; print(torch.__version__, torch.cuda.is_available(), ultralytics.__version__)"
```
The last line must print `True` and `8.4.174`. Our machine runs torch 2.11.0+cu128.

**Data that is not in Git** (gitignored, so copy it over by USB or Drive):
- `PUBLIC POTHOLE DATASET\`, put in the repo root, with `images\` (1,000), `labels\` and `depths\` (`*_depth.npy`).
- The pretrained weights download themselves. Run this once in the repo root:
  ```powershell
  python -c "from ultralytics import YOLO; YOLO('yolo26l-seg.pt')"
  ```
  The file `yolo26l-seg.pt` must end up in the repo root (that's where `train_m9.py` looks).

**Rules** (same as the rest of the project):
- Don't commit `*.pt`, `runs/`, `ml/pothole/data/` or the dataset folder.
- Report only numbers you measured.
- Never overwrite a run. Every run gets a new `--name`, and the script refuses a name that exists.

---

## 3. Smoke test (5 minutes; do this before any long run)

```powershell
python ml/pothole/prepare_grouped.py
python ml/pothole/modify_model.py --check --imgsz 640 --batch 2
python ml/pothole/train_m9.py --variant modified --epochs 1 --fraction 0.05 --name smoke_afif --exist-ok
```

**Expected:**
- `prepare_grouped.py` prints these splits:

  | Split | Sessions | Images | Instances |
  |---|---|---|---|
  | train | 173 | 790 | 874 |
  | val | 38 | 105 | 118 |
  | test | 17 | 105 | 105 |

  If you get different numbers, stop and tell us: the split must be identical on both machines.
- `--check` shows these values:
  - `n_dsconv 6`
  - `n_simam` > 0
  - `offset_grad_nonzero True`
  - `rel_out_diff_vs_stock_no_gelu` close to 0 (the surgery keeps the pretrained weights)
  - `peak_mem_gb`: use it to pick the batch size
- The smoke run ends without errors, and `ml\pothole\runs\smoke_afif\m9_train_summary.json` exists.

**Pick batch / imgsz for your GPU:**

| VRAM | imgsz | batch |
|---|---|---|
| 6 GB | 640 | 4 |
| 8 GB | 640 | 6–8 |
| 12 GB | 640 / 800 | 8 / 6 |
| ≥ 16 GB | 800 / 1024 | 8–16 / 6–8 |

If you get an out-of-memory error, halve the batch.

---

## 4. Core experiments (pothole only, PothRGBD)

All runs use the same recipe and the same grouped split. Use `--time <hours>` so a run stops cleanly
and keeps `best.pt` even if it is slower than expected.

### 4.1 The runs, in order of priority

| ID | What it answers | Command |
|---|---|---|
| **E0** | Fair score of the 2025 baseline weights on the grouped test split (copy `ml\pothole\runs\baseline\weights\best.pt` from us) | `python ml/pothole/evaluate.py --weights ml/pothole/runs/baseline/weights/best.pt --out ml/pothole/runs/eval/E0_baseline2025.json` |
| **E1** | Stock YOLO26l-seg on the grouped split (the real baseline) | `python ml/pothole/train_m9.py --variant baseline --time 3 --name E1_baseline` |
| **E2** | **Our modified model** (DSConv+SimAM+GELU) | `python ml/pothole/train_m9.py --variant modified --time 4 --name E2_modified` |
| **E3** | Does GELU help or hurt? (ablation) | `python ml/pothole/train_m9.py --variant modified --no-gelu --time 4 --name E3_mod_nogelu` |
| **E4** | Is the difference real or noise? Repeat the better of E1/E2 with 2 more seeds | `... --seed 1 --name E4_<variant>_s1` and `--seed 2 --name E4_<variant>_s2` |

`--time` is in hours. Add `--batch` and `--imgsz` for your GPU (§3). With 105 test images, a
difference under about 0.02 F1 is within seed noise. E4 tells us that spread, so don't call a winner
without it.

### 4.2 Test every run the same way

```powershell
python ml/pothole/evaluate.py --weights ml/pothole/runs/E2_modified/weights/best.pt --out ml/pothole/runs/eval/E2_modified.json
```

`evaluate.py` reports two things:
- **`fixed_conf`:** precision, recall and F1 at the deployed confidence (0.25) with one-to-one mask
  matching (IoU ≥ 0.5). **This F1 is the one checked against 0.65.**
- **`ultralytics`:** box and mask P/R/mAP50/mAP50-95 from `model.val()`. That F1 is chosen at the
  best confidence for the test set itself, so it's an upper bound.

Categories with no test data come out as `no_data`, never as a pass. Always evaluate on `--split test`
(the default). Use `val` only for choosing settings, never for the final number.

---

## 5. Making it more advanced (research, 2026-10-10)

Each idea below is something to **test against E1/E2, not to assume**: adopt it only if it beats them
on the grouped test split (and across seeds).

### 5.1 Training upgrades (pothole data, cheap; try after E2)

| ID | Idea | Why | Command |
|---|---|---|---|
| A1 | **Copy-paste augmentation** | Segmentation-specific augmentation; pastes pothole instances onto other roads. Strong on small datasets | `--set copy_paste=0.3` |
| A2 | **Higher resolution** | Potholes from the air are small; more pixels per pothole | `--imgsz 800` or `--imgsz 1024` (lower the batch) |
| A3 | **Cosine LR + longer schedule** | Smoother convergence on 790 training images | `--set cos_lr=True --time 6` |
| A4 | **Test-time augmentation** | Flip/scale at inference; costs speed | evaluate with `model.val(augment=True)`; check that 8.4.174 supports it for segmentation and note it if not |

Example: `python ml/pothole/train_m9.py --variant modified --time 4 --name A1_mod_copypaste --set copy_paste=0.3`

### 5.2 The big one: the aerial domain gap

PothRGBD is shot **close to the ground with a handheld RealSense**. Our system flies at **about 67 m**,
where a pothole is a few dozen pixels. A model that scores 0.9 on PothRGBD can fail from the drone.
This matters more than any architecture change.

1. **Measure it:** build a small aerial test set (50–100 images) from a UAV dataset below, and
   report F1 on it separately from PothRGBD.
2. **Fix it:** fine-tune on aerial data (merged with PothRGBD, §5.4), and use **SAHI tiled
   inference** at test time. SAHI slices the 1920×1080 frame into overlapping tiles so small potholes
   get enough pixels ([Ultralytics SAHI guide](https://docs.ultralytics.com/guides/sahi-tiled-inference)):
   ```powershell
   pip install sahi
   ```
   Compare whole-frame vs tiled (slice 640, overlap 0.2) on the aerial test set.

### 5.3 Data for the other three categories

Without these, crack, waterlogging and debris stay `no_data` and can't pass. What exists publicly (checked 2026-10-10):

| Category | Dataset | View | Labels | Notes |
|---|---|---|---|---|
| pothole + crack | **HighRPD** (2025), 11,696 images | **high-altitude drone** | line/block/pit annotations | best match to our altitude ([paper](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11872502/)) |
| pothole + crack | **UAV-PDD2023**, 2,440 images, 11,158 instances | drone | boxes, 6 classes incl. potholes | ([paper](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10630617/)) |
| crack | **PaveCrack1300**, 1,300 image–mask pairs | drone (DJI Mini 4 Pro) | **pixel masks** | ([Mendeley](https://data.mendeley.com/datasets/8b27pdcxv7/1)) |
| crack | **UAV-Crack500**, 500 images | drone | pixel masks | from an IEEE T-ITS 2024 paper |
| waterlogging | **FloodNet**, 2,343 images | drone (DJI Mavic Pro), post-hurricane | **pixel masks incl. "road flooded"** | ~12 GB; [GitHub](https://github.com/BinaLab/FloodNet-Supervised_v1.0); check the license file |
| debris | **no good public aerial set found** | n/a | n/a | WildRoadBench has 48 debris images (boxes); otherwise CARLA props or our own annotation |

**Suggested scope for this weekend:**
- pothole: PothRGBD + HighRPD or UAV-PDD2023 pothole class;
- crack: PaveCrack1300;
- waterlogging: FloodNet "road flooded" only;
- debris: report it as `no_data` with the reason, unless time is left.

### 5.4 Converting and merging the data

Class ids must match `evaluate.py` and the event schema: **0 pothole, 1 crack, 2 waterlogging, 3 debris.**

- **Mask datasets (PaveCrack1300, FloodNet):** turn each class mask into polygons with
  `cv2.findContours`, drop tiny contours (< 20 px²), and write YOLO-seg lines
  `class x1 y1 x2 y2 ...`, normalised to 0–1. For FloodNet, keep only the *road flooded* class as
  class 2. Its images are large, so tile them to 1024 px first.
- **Box datasets (UAV-PDD2023, HighRPD):** make masks with **SAM**, using each ground-truth box as a
  prompt. Ultralytics' SAM accepts `bboxes=`:
  ```python
  from ultralytics import SAM
  sam = SAM("sam_b.pt")                       # or "sam2_b.pt" if your version supports it; check
  r = sam(image_path, bboxes=[[x1, y1, x2, y2], ...])[0]
  polys = r.masks.xyn                          # normalised polygons, one per box, same order
  ```
  Spot-check 30 masks by eye before training on them. Bad masks teach bad boundaries.
- **Cracks:** cracks are thin and long. Thin masks score low IoU, so look at both box and mask
  metrics. The DSConv "snake" convolution was designed for thin tubular shapes, so cracks are where
  it should help most.
- **Layout:** `ml\pothole\data\multi\{train,val,test}\{images,labels}` plus a `data.yaml` with
  `nc: 4` and `names: [pothole, crack, waterlogging, debris]`. Keep each source's own test split
  as test, and never put frames from one capture session in two splits (same reason as §1).
- **Train:**
  ```powershell
  python ml/pothole/train_m9.py --variant modified --data ml/pothole/data/multi/data.yaml --time 8 --name M1_multi_modified
  ```
  Then run `evaluate.py --data ml/pothole/data/multi/data.yaml`. It reports each category's F1 against 0.65.

### 5.5 Ideas looked at and not recommended now

- **RGB-D as a 4th input channel.** We found no study showing depth as an input channel improves
  YOLO segmentation. The PothRGBD paper uses depth only to *measure* potholes after segmentation.
  The drone has no depth sensor either, so skip it. Use depth only for severity (§7).
- **A bigger model (YOLO26x-seg).** Our deployment GPU is an RTX 4050 shared with the simulator, so
  stay with `l` (or try `m` if inference speed becomes a problem).

---

## 6. Overnight plan (suggested order)

| Slot | Run |
|---|---|
| Sat evening | §3 smoke test, then E0 (minutes) |
| Sat night | E1 (3 h), then E2 (4 h). Queue them in one PowerShell line: `python ...E1... ; python ...E2...` |
| Sun morning | E3 and A1 (copy-paste), evaluate everything |
| Sun day | §5.3/§5.4 data (in parallel while the GPU trains), then M1_multi_modified (≤ 8 h, use `--time` so it ends by about 19:00) |
| Sun 19:00–20:00 | evaluate, fill in the results table (§8), send back |

If short on time, the minimum useful result is **E1 + E2 + their evaluations** (and E0).

---

## 7. How the model is used in the system (for context)

The trained `best.pt` goes into the anomaly step of the pipeline. Per frame it produces masks, which
become ground positions through the drone camera pose, then anomaly events (`kind: anomaly`).
Each event carries:
- `severity_score`, from mask area in m² (plus depth where available);
- a de-duplication by location, so the same pothole seen in 200 frames is one event.

The dashboard and 3D twin show these events next to violations, with a maintenance status.
Nothing needed from you here beyond the weights and the numbers.

---

## 8. What to send back

For each run you want reported:
- `ml\pothole\runs\<name>\weights\best.pt`, by Drive or USB (**not Git**);
- `ml\pothole\runs\<name>\m9_train_summary.json`, `results.csv` and `args.yaml`;
- `ml\pothole\runs\eval\<name>.json` (from `evaluate.py`);
- your GPU model, and any changes you made to the code (as a Git branch or a patch).

Fill in this table and paste it to us (use only your measured numbers):

| Run | Data | Epochs / time | Pothole F1 (fixed conf) | Crack F1 | Waterlogging F1 | Debris F1 | Mask mAP50 | Mask mAP50-95 | Notes |
|---|---|---|---|---|---|---|---|---|---|
| E0 | grouped | n/a | | n/a | n/a | n/a | | | 2025 weights |
| E1 | grouped | | | n/a | n/a | n/a | | | stock |
| E2 | grouped | | | n/a | n/a | n/a | | | modified |
| … | | | | | | | | | |

We add the rows to `docs/main_project_tracker.md` §3.

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `CUDA out of memory` | Halve `--batch`; then lower `--imgsz` |
| `data.yaml missing` | Run `python ml/pothole/prepare_grouped.py` first |
| `runs\<name> exists` | Pick a new `--name`; results are never overwritten |
| Loading a modified `best.pt` fails (`modules.DSConv` not found) | Load it through `evaluate.py`, or `import modify_model` first (it registers the custom layers) |
| Training very slow, GPU idle | `cache="ram"` needs RAM. If you're short of RAM, use `--set cache=False`. Leave `--workers 0` on Windows unless you've tested more |
| Split counts differ from §3 | Different dataset copy; re-copy `PUBLIC POTHOLE DATASET\` |
| Metrics jump between seeds | Expected with 105 test images; report the mean and spread of E4 |

**Code map:**
- `prepare_grouped.py`: the leak-free split.
- `modules.py`: the DSConv and SimAM layers.
- `modify_model.py`: the surgery and the trainer.
- `train_m9.py`: training, with `--variant`, `--time`, `--seed` and `--set`.
- `evaluate.py`: per-category F1 against 0.65.
- `train_baseline.py`: the 2025 recipe; kept for reference, don't use for new runs.
