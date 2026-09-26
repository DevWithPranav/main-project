"""
train_baseline.py
-----------------
Full baseline training: stock yolo26l-seg on all 800 PothRGBD training images.
No architecture modifications — this establishes the reference mAP to compare
DSConv / SimAM / GELU against in Phase 3.

Run from repo root:
    py ml/pothole/train_baseline.py

Time cap: 10 hours (Ultralytics will stop cleanly and save best.pt).
Results:  ml/pothole/runs/baseline/
"""

from pathlib import Path
from ultralytics import YOLO

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_YAML = REPO_ROOT / "ml" / "pothole" / "data" / "data.yaml"
RUNS_DIR  = REPO_ROOT / "ml" / "pothole" / "runs"

# ── Sanity-check paths ─────────────────────────────────────────────────────────
assert DATA_YAML.exists(), f"data.yaml not found: {DATA_YAML}\nRun prepare_dataset.py first."
train_count = len(list((REPO_ROOT / "ml" / "pothole" / "data" / "train" / "images").glob("*")))
val_count   = len(list((REPO_ROOT / "ml" / "pothole" / "data" / "val"   / "images").glob("*")))
print(f"Dataset: {train_count} train / {val_count} val images")
print(f"Data YAML: {DATA_YAML}")
print(f"Results → {RUNS_DIR / 'baseline'}\n")

# ── Training args ──────────────────────────────────────────────────────────────
TRAIN_ARGS = dict(
    data        = str(DATA_YAML),

    # Time & stopping
    epochs      = 300,          # High ceiling — time cap will stop it first
    time        = 10,           # ← 10-hour hard cap (Ultralytics saves best.pt cleanly on timeout)
    patience    = 20,           # Early stopping: halt if no improvement for 20 epochs

    # Hardware — RTX 4060 Laptop (8GB VRAM)
    batch       = 4,            # Seg head needs more VRAM than detection; safe at 4
    imgsz       = 640,
    device      = 0,
    workers     = 0,            # Disable multiprocessing — prevents pagefile/DLL crash on 16GB RAM

    # Augmentation (standard)
    mosaic      = 1.0,
    fliplr      = 0.5,
    hsv_h       = 0.015,
    hsv_s       = 0.7,
    hsv_v       = 0.4,
    scale       = 0.5,
    translate   = 0.1,

    # Saving & logging
    project     = str(RUNS_DIR),
    name        = "baseline",
    exist_ok    = True,
    save        = True,
    save_period = 10,           # Checkpoint every 10 epochs as insurance
    plots       = True,
    verbose     = True,
)

# ── Train ──────────────────────────────────────────────────────────────────────
print("Loading yolo26l-seg.pt (stock weights)...")
model = YOLO("yolo26l-seg.pt")

print("Starting baseline training — 10-hour cap, patience=20\n")
results = model.train(**TRAIN_ARGS)

# ── Final report ───────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("BASELINE TRAINING COMPLETE")
print("=" * 60)

metrics = results.results_dict if hasattr(results, "results_dict") else {}

box_p      = metrics.get("metrics/precision(B)",  "N/A")
box_r      = metrics.get("metrics/recall(B)",     "N/A")
box_map50  = metrics.get("metrics/mAP50(B)",      "N/A")
box_map    = metrics.get("metrics/mAP50-95(B)",   "N/A")
mask_p     = metrics.get("metrics/precision(M)",  "N/A")
mask_r     = metrics.get("metrics/recall(M)",     "N/A")
mask_map50 = metrics.get("metrics/mAP50(M)",      "N/A")
mask_map   = metrics.get("metrics/mAP50-95(M)",   "N/A")

print(f"\n  {'Metric':<22} {'Box':>10}  {'Mask':>10}")
print(f"  {'-'*44}")
print(f"  {'Precision':<22} {str(round(box_p,  4)) if isinstance(box_p,  float) else box_p:>10}  {str(round(mask_p,  4)) if isinstance(mask_p,  float) else mask_p:>10}")
print(f"  {'Recall':<22} {str(round(box_r,  4)) if isinstance(box_r,  float) else box_r:>10}  {str(round(mask_r,  4)) if isinstance(mask_r,  float) else mask_r:>10}")
print(f"  {'mAP@50':<22} {str(round(box_map50,4)) if isinstance(box_map50,float) else box_map50:>10}  {str(round(mask_map50,4)) if isinstance(mask_map50,float) else mask_map50:>10}")
print(f"  {'mAP@50-95':<22} {str(round(box_map,  4)) if isinstance(box_map,  float) else box_map:>10}  {str(round(mask_map,  4)) if isinstance(mask_map,  float) else mask_map:>10}")

best_pt = RUNS_DIR / "baseline" / "weights" / "best.pt"
print(f"\n  Best checkpoint: {best_pt}")
print(f"  Full results:    {RUNS_DIR / 'baseline'}")
print("\n  ← Record these numbers. They are the baseline to beat in Phase 3 (DSConv+SimAM+GELU).")
