"""Stage 1.3 prep — build a small VisDrone2019-DET train subset for the fine-tuning smoke test.

Triggers VisDrone's built-in Ultralytics auto-download (no leakage/dataset-mismatch concerns,
unlike the earlier DOTA/OBB detour — see ml/detection/obb_exploration/), then copies a small
random sample of train images+labels into ml/data/datasets/visdrone_subset/images/train. The
val split is referenced directly from the full downloaded VisDrone val set (548 images) rather
than copied, since it doesn't need subsetting for evaluation.
"""

import random
from pathlib import Path

from ultralytics.data.utils import check_det_dataset
from ultralytics.utils import SETTINGS

N_TRAIN = 25
SEED = 0

SUBSET_DIR = Path(__file__).resolve().parents[1] / "data" / "datasets" / "visdrone_subset"


def main() -> None:
    print("Ensuring VisDrone2019-DET is downloaded (auto-downloads on first use)...")
    check_det_dataset("VisDrone.yaml")

    visdrone_dir = Path(SETTINGS["datasets_dir"]) / "VisDrone"
    train_images_src = visdrone_dir / "images" / "train"
    train_labels_src = visdrone_dir / "labels" / "train"
    val_images_dir = visdrone_dir / "images" / "val"

    all_train_images = sorted(train_images_src.glob("*"))
    random.seed(SEED)
    sample = random.sample(all_train_images, min(N_TRAIN, len(all_train_images)))

    images_dst = SUBSET_DIR / "images" / "train"
    labels_dst = SUBSET_DIR / "labels" / "train"
    images_dst.mkdir(parents=True, exist_ok=True)
    labels_dst.mkdir(parents=True, exist_ok=True)

    for image_path in sample:
        (images_dst / image_path.name).write_bytes(image_path.read_bytes())
        label_path = train_labels_src / f"{image_path.stem}.txt"
        if label_path.exists():
            (labels_dst / label_path.name).write_bytes(label_path.read_bytes())

    yaml_path = SUBSET_DIR / "visdrone_subset.yaml"
    yaml_path.write_text(
        "train: {}\nval: {}\n\nnames:\n"
        "  0: pedestrian\n  1: people\n  2: bicycle\n  3: car\n  4: van\n"
        "  5: truck\n  6: tricycle\n  7: awning-tricycle\n  8: bus\n  9: motor\n".format(
            images_dst.as_posix(), val_images_dir.as_posix()
        )
    )

    print(f"Copied {len(sample)} train images to {images_dst}")
    print(f"Val split referenced directly from {val_images_dir} ({len(list(val_images_dir.glob('*')))} images)")
    print(f"Dataset yaml written to {yaml_path}")


if __name__ == "__main__":
    main()
