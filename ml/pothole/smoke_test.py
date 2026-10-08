"""
smoke_test.py
-------------
Quick sanity-check: fine-tune stock yolo26l-seg on a tiny 25-image subset
to prove the architecture can learn the pothole domain before committing
to a full training run.

Run from repo root:
    py ml/pothole/smoke_test.py

Decision checkpoint after run:
    PASS  — val/mask_mAP50 improves over epochs, masks look plausible
    FAIL  — no learning signal → check label format / class mapping
"""

import random
import shutil
from pathlib import Path

from ultralytics import YOLO

# ── Config ─────────────────────────────────────────────────────────────────────
REPO_ROOT    = Path(__file__).resolve().parents[2]
DATA_DIR     = REPO_ROOT / "ml" / "pothole" / "data"
RUNS_DIR     = REPO_ROOT / "ml" / "pothole" / "runs"
SMOKE_DIR    = RUNS_DIR / "smoke_test"
SUBSET_SIZE  = 25
SEED         = 42

TRAIN_ARGS = dict(
    epochs   = 20,
    patience = 5,
    batch    = 4,
    imgsz    = 640,
    workers  = 0,       # 0 = main process only; avoids pagefile/DLL crash on 16GB RAM (same fix as Phase 1)
    device   = 0,       # RTX 4060 Laptop GPU
    exist_ok = True,
    project  = str(RUNS_DIR),
    name     = "smoke_test",
    plots    = True,
)

# ── Build 25-image subset ──────────────────────────────────────────────────────
train_imgs = sorted((DATA_DIR / "train" / "images").glob("*.jpg"))
train_imgs += sorted((DATA_DIR / "train" / "images").glob("*.png"))

random.seed(SEED)
subset = random.sample(train_imgs, min(SUBSET_SIZE, len(train_imgs)))

subset_img_dir = SMOKE_DIR / "subset" / "images"
subset_lbl_dir = SMOKE_DIR / "subset" / "labels"
subset_img_dir.mkdir(parents=True, exist_ok=True)
subset_lbl_dir.mkdir(parents=True, exist_ok=True)

for img_path in subset:
    lbl_path = DATA_DIR / "train" / "labels" / (img_path.stem + ".txt")
    shutil.copy2(img_path, subset_img_dir / img_path.name)
    if lbl_path.exists():
        shutil.copy2(lbl_path, subset_lbl_dir / lbl_path.name)

print(f"Smoke subset: {len(subset)} images → {subset_img_dir}")

# ── Write temporary data.yaml ──────────────────────────────────────────────────
# Use full val split for evaluation (gives a real, unseen signal)
smoke_yaml = SMOKE_DIR / "smoke_data.yaml"
smoke_yaml.write_text(
    f"path: {SMOKE_DIR.as_posix()}\n"
    f"train: subset/images\n"
    f"val:   {(DATA_DIR / 'val' / 'images').as_posix()}\n"
    f"\n"
    f"nc: 1\n"
    f"names: ['pothole']\n"
)

print(f"Smoke data.yaml: {smoke_yaml}")

# ── Train ──────────────────────────────────────────────────────────────────────
print("\n--- Starting smoke test fine-tune ---")
model = YOLO("yolo26l-seg.pt")

results = model.train(data=str(smoke_yaml), **TRAIN_ARGS)

# ── Decision checkpoint ────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SMOKE TEST — DECISION CHECKPOINT")
print("=" * 60)

metrics = results.results_dict if hasattr(results, "results_dict") else {}

mask_map50 = metrics.get("metrics/mAP50(M)", None)
box_map50  = metrics.get("metrics/mAP50(B)", None)

if mask_map50 is not None:
    print(f"  mask mAP@50 : {mask_map50:.4f}")
    print(f"  box  mAP@50 : {box_map50:.4f}")
    if mask_map50 > 0.05:
        print("\n  ✅ PASS — Learning signal detected. Proceed to full training.")
    else:
        print("\n  ⚠️  WEAK — mAP@50 very low. Check label alignment or reduce LR.")
else:
    print("  (Metrics not captured inline — check the run folder for results.csv)")
    print(f"  Results saved to: {RUNS_DIR / 'smoke_test'}")

print(f"\nFull results: {RUNS_DIR / 'smoke_test'}")
print("Review results.png and val_batch0_pred.jpg before proceeding.")
