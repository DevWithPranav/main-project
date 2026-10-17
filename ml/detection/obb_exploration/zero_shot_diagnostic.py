"""One-off diagnostic: is the OBB zero-detection result a domain mismatch or a threshold issue?

Runs the same image through:
  1. yolo26l.pt (COCO, axis-aligned) at default confidence
  2. yolo26l-obb.pt (DOTA, oriented) at a much lower confidence threshold
"""

from pathlib import Path

from ultralytics import YOLO

RAW_FOOTAGE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw_footage"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "results" / "diagnostic"


def run(model_name: str, run_name: str, conf: float) -> None:
    images = sorted(
        p for p in RAW_FOOTAGE_DIR.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    model = YOLO(model_name)
    results = model.predict(
        source=images, conf=conf, save=True, project=str(RESULTS_DIR), name=run_name, exist_ok=True
    )
    print(f"\n=== {model_name} (conf={conf}) ===")
    for image_path, result in zip(images, results):
        names = result.names
        dets = result.obb if result.obb is not None else result.boxes
        counts: dict[str, int] = {}
        for d in dets:
            cls_name = names[int(d.cls)]
            counts[cls_name] = counts.get(cls_name, 0) + 1
        print(f"{image_path.name}: {counts or 'none'}")


def main() -> None:
    run("yolo26l.pt", "coco_default_conf", conf=0.25)
    run("yolo26l-obb.pt", "obb_low_conf", conf=0.05)


if __name__ == "__main__":
    main()
