"""Stage 1.3/1.4 — fine-tune yolo26l-obb.pt on the small DOTA vehicle subset and compare
zero-shot vs. fine-tuned mAP on the held-out val split.

The val split here is DOTAv1's official val set (disjoint from the train split the
released yolo26l-obb.pt weights were trained on), so the zero-shot number is a valid
baseline — unlike `dota8`, which is drawn from the train split.
"""

from pathlib import Path

from ultralytics import YOLO

DATA_YAML = (
    Path(__file__).resolve().parents[2] / "data" / "datasets" / "dota_vehicle_subset" / "dota_vehicle_subset.yaml"
)
RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "results" / "smoke_test"

EPOCHS = 50


def main() -> None:
    if not DATA_YAML.exists():
        raise SystemExit(f"{DATA_YAML} not found — run prepare_dota_vehicle_subset.py first.")

    print("=== Zero-shot baseline (yolo26l-obb.pt, held-out DOTAv1 val split) ===")
    zero_shot = YOLO("yolo26l-obb.pt")
    zero_shot_metrics = zero_shot.val(data=str(DATA_YAML), split="val")

    print("\n=== Fine-tuning on 132-image vehicle subset ===")
    finetuned = YOLO("yolo26l-obb.pt")
    finetuned.train(
        data=str(DATA_YAML),
        epochs=EPOCHS,
        imgsz=1024,
        batch=2,
        workers=0,
        device=0,
        project=str(RESULTS_DIR),
        name="train",
        exist_ok=True,
    )

    print("\n=== Fine-tuned model, same held-out val split ===")
    finetuned_metrics = finetuned.val(data=str(DATA_YAML), split="val")

    print("\n=== Comparison ===")
    print(f"{'':12s} {'mAP50':>8s} {'mAP50-95':>10s}")
    print(f"{'zero-shot':12s} {zero_shot_metrics.box.map50:8.4f} {zero_shot_metrics.box.map:10.4f}")
    print(f"{'fine-tuned':12s} {finetuned_metrics.box.map50:8.4f} {finetuned_metrics.box.map:10.4f}")


if __name__ == "__main__":
    main()
