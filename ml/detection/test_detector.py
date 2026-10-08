"""Sanity check: run the trained detector on (1) VisDrone's test-dev split — images never
touched during training or validation, a true holdout — and (2) whatever's in
ml/data/raw_footage/, for a real-world (non-VisDrone) comparison point.
"""

from pathlib import Path

from ultralytics import YOLO
from ultralytics.utils import SETTINGS

BEST_PT = Path(__file__).resolve().parents[1] / "data" / "results" / "full_train" / "train" / "weights" / "best.pt"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "holdout_test"
RAW_FOOTAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "raw_footage"
N_SAMPLE_IMAGES = 10


def run(model: YOLO, images: list[Path], name: str) -> None:
    if not images:
        return
    results = model.predict(source=images, save=True, project=str(RESULTS_DIR), name=name, exist_ok=True)
    for image_path, result in zip(images, results):
        names = result.names
        counts: dict[str, int] = {}
        for box in result.boxes:
            cls_name = names[int(box.cls)]
            counts[cls_name] = counts.get(cls_name, 0) + 1
        print(f"{image_path.name}: {counts or 'none'}")
    print(f"Annotated output saved under: {RESULTS_DIR / name}")


def main() -> None:
    if not BEST_PT.exists():
        raise SystemExit(f"{BEST_PT} not found — run train_full.py first.")

    model = YOLO(str(BEST_PT))

    dataset_dir = Path(SETTINGS["datasets_dir"]) / "VisDrone"
    test_images = sorted((dataset_dir / "images" / "test").glob("*"))[:N_SAMPLE_IMAGES]
    print("=== VisDrone test-dev holdout ===")
    run(model, test_images, "visdrone_holdout")

    raw_images = sorted(
        p for p in RAW_FOOTAGE_DIR.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if raw_images:
        print("\n=== ml/data/raw_footage/ ===")
        run(model, raw_images, "raw_footage")


if __name__ == "__main__":
    main()
