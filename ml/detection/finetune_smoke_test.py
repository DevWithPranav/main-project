"""Stage 1.3/1.4 — fine-tune yolo26l.pt on the small VisDrone train subset and compare
zero-shot vs. fine-tuned mAP on the full VisDrone val split.

Uses early stopping (patience) this time — the earlier OBB/DOTA smoke test
(ml/detection/obb_exploration/) showed a tiny fine-tune set overfits fast past
its best epoch, so we stop automatically instead of relying on a fixed last-epoch read.
"""

from pathlib import Path

from ultralytics import YOLO

DATA_YAML = Path(__file__).resolve().parents[1] / "data" / "datasets" / "visdrone_subset" / "visdrone_subset.yaml"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "smoke_test_visdrone"

EPOCHS = 30
PATIENCE = 5


def main() -> None:
    if not DATA_YAML.exists():
        raise SystemExit(f"{DATA_YAML} not found — run prepare_visdrone_subset.py first.")

    print("=== Zero-shot baseline (yolo26l.pt, full VisDrone val split) ===")
    zero_shot = YOLO("yolo26l.pt")
    zero_shot_metrics = zero_shot.val(data=str(DATA_YAML), split="val")

    print("\n=== Fine-tuning on 25-image VisDrone train subset ===")
    finetuned = YOLO("yolo26l.pt")
    finetuned.train(
        data=str(DATA_YAML),
        epochs=EPOCHS,
        patience=PATIENCE,
        imgsz=640,
        batch=8,
        workers=0,
        device=0,
        project=str(RESULTS_DIR),
        name="train",
        exist_ok=True,
    )

    print("\n=== Fine-tuned model (best.pt), same val split ===")
    best_pt = RESULTS_DIR / "train" / "weights" / "best.pt"
    finetuned_metrics = YOLO(str(best_pt)).val(data=str(DATA_YAML), split="val")

    print("\n=== Comparison ===")
    print(f"{'':12s} {'mAP50':>8s} {'mAP50-95':>10s}")
    print(f"{'zero-shot':12s} {zero_shot_metrics.box.map50:8.4f} {zero_shot_metrics.box.map:10.4f}")
    print(f"{'fine-tuned':12s} {finetuned_metrics.box.map50:8.4f} {finetuned_metrics.box.map:10.4f}")


if __name__ == "__main__":
    main()
