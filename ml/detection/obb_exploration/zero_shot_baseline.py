"""Stage 1.2 — zero-shot check: run pretrained YOLO26l-OBB (DOTA weights) on real aerial imagery.

Uses Ultralytics' built-in `dota8` sample set (first 8 images of DOTAv1, real nadir
drone/satellite imagery with oriented vehicle annotations) so we get both a quantitative
zero-shot mAP and visual detections, without needing to source our own top-down footage yet.

OBB (oriented bounding box) is used instead of axis-aligned detection because top-down
aerial footage has vehicles at arbitrary rotation angles.
"""

from pathlib import Path

from ultralytics import YOLO
from ultralytics.utils import SETTINGS

RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "results" / "zero_shot_dota8"


def main() -> None:
    model = YOLO("yolo26l-obb.pt")

    metrics = model.val(data="dota8.yaml")
    print("\n=== Zero-shot yolo26l-obb.pt on dota8 (val split) ===")
    print(f"mAP50:    {metrics.box.map50:.4f}")
    print(f"mAP50-95: {metrics.box.map:.4f}")

    dataset_dir = Path(SETTINGS["datasets_dir"]) / "dota8"
    val_images = sorted((dataset_dir / "images" / "val").glob("*"))
    if not val_images:
        raise SystemExit(f"No val images found under {dataset_dir / 'images' / 'val'}")

    results = model.predict(
        source=val_images, save=True, project=str(RESULTS_DIR), name="run", exist_ok=True
    )
    for image_path, result in zip(val_images, results):
        names = result.names
        counts: dict[str, int] = {}
        for obb in result.obb:
            cls_name = names[int(obb.cls)]
            counts[cls_name] = counts.get(cls_name, 0) + 1
        print(f"{image_path.name}: {counts or 'none'}")

    print(f"\nAnnotated output saved under: {RESULTS_DIR / 'run'}")


if __name__ == "__main__":
    main()
