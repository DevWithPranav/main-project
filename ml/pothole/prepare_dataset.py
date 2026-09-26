"""
prepare_dataset.py
------------------
Splits the PothRGBD dataset (PUBLIC POTHOLE DATASET/) into train / val / test
(80 / 10 / 10) and writes the Ultralytics-compatible directory layout under
ml/pothole/data/ along with a data.yaml.

Run from repo root:
    py ml/pothole/prepare_dataset.py
"""

import random
import shutil
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).resolve().parents[2]
SRC_IMAGES  = REPO_ROOT / "PUBLIC POTHOLE DATASET" / "images"
SRC_LABELS  = REPO_ROOT / "PUBLIC POTHOLE DATASET" / "labels"
OUT_DIR     = REPO_ROOT / "ml" / "pothole" / "data"

SPLITS = {"train": 0.80, "val": 0.10, "test": 0.10}
SEED   = 42

# ── Collect & shuffle ──────────────────────────────────────────────────────────
all_images = sorted(SRC_IMAGES.glob("*.jpg")) + sorted(SRC_IMAGES.glob("*.png"))
random.seed(SEED)
random.shuffle(all_images)

n = len(all_images)
n_train = int(n * SPLITS["train"])
n_val   = int(n * SPLITS["val"])

split_map = {
    "train": all_images[:n_train],
    "val":   all_images[n_train : n_train + n_val],
    "test":  all_images[n_train + n_val :],
}

# ── Copy files ─────────────────────────────────────────────────────────────────
for split, images in split_map.items():
    img_out = OUT_DIR / split / "images"
    lbl_out = OUT_DIR / split / "labels"
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)

    missing_labels = 0
    for img_path in images:
        lbl_path = SRC_LABELS / (img_path.stem + ".txt")
        shutil.copy2(img_path, img_out / img_path.name)
        if lbl_path.exists():
            shutil.copy2(lbl_path, lbl_out / lbl_path.name)
        else:
            missing_labels += 1
            print(f"  [WARN] No label for: {img_path.name}")

    print(f"  {split:5s}: {len(images)} images, {missing_labels} missing labels")

# ── Write data.yaml ────────────────────────────────────────────────────────────
yaml_path = OUT_DIR / "data.yaml"
yaml_path.write_text(
    f"path: {OUT_DIR.as_posix()}\n"
    f"train: train/images\n"
    f"val:   val/images\n"
    f"test:  test/images\n"
    f"\n"
    f"nc: 1\n"
    f"names: ['pothole']\n"
)

# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\nDataset split complete:")
for split, images in split_map.items():
    print(f"  {split:5s}: {len(images)}")
print(f"\ndata.yaml written to: {yaml_path}")
print(f"Verify in Ultralytics with:  YOLO('yolo26l-seg.pt').train(data='{yaml_path}')")
