"""Stage 1.3 prep — build a small DOTAv1 vehicle-only subset for the fine-tuning smoke test.

Downloads the full DOTAv1 dataset (~2GB, official Ultralytics mirror), finds images whose
labels include the `large-vehicle` (9) or `small-vehicle` (10) classes, copies a small
sample of those into a raw subset folder, then tiles them into 1024x1024 crops (via
Ultralytics' own split_dota utility, required because raw DOTA images are far larger than
1024px and naive resizing would destroy small-vehicle detail).

Output: ml/data/datasets/dota_vehicle_subset/{images,labels}/{train,val} + a yaml
pointing at it, ready to pass as `data=` to model.train().
"""

import random
from pathlib import Path

from ultralytics.data.split_dota import split_trainval
from ultralytics.utils.downloads import safe_download

DOTA_ZIP_URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/DOTAv1.zip"
DATASETS_DIR = Path(__file__).resolve().parents[2] / "data" / "datasets"
DOTA_ROOT = DATASETS_DIR / "DOTAv1"
RAW_SUBSET_DIR = DATASETS_DIR / "dota_vehicle_subset_raw"
TILED_SUBSET_DIR = DATASETS_DIR / "dota_vehicle_subset"

VEHICLE_CLASS_IDS = {"9", "10"}  # large-vehicle, small-vehicle (DOTAv1.yaml class order)
N_TRAIN = 15
N_VAL = 5
SEED = 0

NAMES = [
    "plane", "ship", "storage tank", "baseball diamond", "tennis court",
    "basketball court", "ground track field", "harbor", "bridge", "large vehicle",
    "small vehicle", "helicopter", "roundabout", "soccer ball field", "swimming pool",
]


def download_dota() -> None:
    if DOTA_ROOT.exists():
        print(f"DOTAv1 already present at {DOTA_ROOT}, skipping download.")
        return
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    print("Downloading DOTAv1 (~2GB)... this will take a while.")
    safe_download(url=DOTA_ZIP_URL, dir=DATASETS_DIR, unzip=True, delete=True)


def find_vehicle_images(split: str) -> list[str]:
    labels_dir = DOTA_ROOT / "labels" / split
    ids = []
    for label_file in labels_dir.glob("*.txt"):
        with open(label_file) as f:
            if any(line.split()[0] in VEHICLE_CLASS_IDS for line in f if line.strip()):
                ids.append(label_file.stem)
    return sorted(ids)


def copy_subset(split: str, image_ids: list[str]) -> None:
    images_src = DOTA_ROOT / "images" / split
    labels_src = DOTA_ROOT / "labels" / split
    images_dst = RAW_SUBSET_DIR / "images" / split
    labels_dst = RAW_SUBSET_DIR / "labels" / split
    images_dst.mkdir(parents=True, exist_ok=True)
    labels_dst.mkdir(parents=True, exist_ok=True)

    for image_id in image_ids:
        image_src = next(images_src.glob(f"{image_id}.*"))
        (images_dst / image_src.name).write_bytes(image_src.read_bytes())
        label_src = labels_src / f"{image_id}.txt"
        (labels_dst / label_src.name).write_bytes(label_src.read_bytes())


def write_yaml() -> Path:
    yaml_path = TILED_SUBSET_DIR / "dota_vehicle_subset.yaml"
    names_block = "\n".join(f"  {i}: {name}" for i, name in enumerate(NAMES))
    yaml_path.write_text(
        f"path: {TILED_SUBSET_DIR.as_posix()}\ntrain: images/train\nval: images/val\n\nnames:\n{names_block}\n"
    )
    return yaml_path


def main() -> None:
    download_dota()

    random.seed(SEED)
    train_ids = find_vehicle_images("train")
    val_ids = find_vehicle_images("val")
    print(f"Found {len(train_ids)} train / {len(val_ids)} val images with vehicle annotations.")

    train_sample = random.sample(train_ids, min(N_TRAIN, len(train_ids)))
    val_sample = random.sample(val_ids, min(N_VAL, len(val_ids)))

    copy_subset("train", train_sample)
    copy_subset("val", val_sample)
    print(f"Copied {len(train_sample)} train / {len(val_sample)} val raw images to {RAW_SUBSET_DIR}")

    print("Tiling into 1024x1024 crops...")
    split_trainval(data_root=str(RAW_SUBSET_DIR), save_dir=str(TILED_SUBSET_DIR), crop_size=1024, gap=200)

    yaml_path = write_yaml()
    n_tiles_train = len(list((TILED_SUBSET_DIR / "images" / "train").glob("*")))
    n_tiles_val = len(list((TILED_SUBSET_DIR / "images" / "val").glob("*")))
    print(f"Tiled subset: {n_tiles_train} train / {n_tiles_val} val crops at {TILED_SUBSET_DIR}")
    print(f"Dataset yaml written to {yaml_path}")


if __name__ == "__main__":
    main()
