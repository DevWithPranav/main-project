"""Stage 1.2 — zero-shot check: run pretrained yolo26l.pt (COCO weights, axis-aligned) on
VisDrone2019-DET, the dataset chosen for the full pipeline (Stage 2 onward).

No data-leakage concern here: yolo26l.pt is COCO-pretrained only, never trained on VisDrone,
so its val split gives a legitimate zero-shot number straight away.
"""

from pathlib import Path

from ultralytics import YOLO
from ultralytics.utils import SETTINGS

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "zero_shot_visdrone"
N_SAMPLE_IMAGES = 5


def main() -> None:
    model = YOLO("yolo26l.pt")

    metrics = model.val(data="VisDrone.yaml", split="val")
    print("\n=== Zero-shot yolo26l.pt on VisDrone2019-DET (val split) ===")
    print(f"mAP50:    {metrics.box.map50:.4f}")
    print(f"mAP50-95: {metrics.box.map:.4f}")

    dataset_dir = Path(SETTINGS["datasets_dir"]) / "VisDrone"
    val_images = sorted((dataset_dir / "images" / "val").glob("*"))[:N_SAMPLE_IMAGES]
    if not val_images:
        raise SystemExit(f"No val images found under {dataset_dir / 'images' / 'val'}")

    results = model.predict(
        source=val_images, save=True, project=str(RESULTS_DIR), name="run", exist_ok=True
    )
    for image_path, result in zip(val_images, results):
        names = result.names
        counts: dict[str, int] = {}
        for box in result.boxes:
            cls_name = names[int(box.cls)]
            counts[cls_name] = counts.get(cls_name, 0) + 1
        print(f"{image_path.name}: {counts or 'none'}")

    print(f"\nAnnotated output saved under: {RESULTS_DIR / 'run'}")


if __name__ == "__main__":
    main()
