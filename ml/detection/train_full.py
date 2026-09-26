"""Stage 1 — full overnight training run: fine-tune yolo26l.pt on the complete
VisDrone2019-DET train split (6,471 images), validated on the full val split (548 images).

Follows the smoke test (ml/detection/finetune_smoke_test.py), which confirmed the
architecture learns this domain (mAP50 0.034 -> 0.121 on a 25-image subset).

Capped at 10 hours wall-clock (time=10) with a high epoch ceiling (unlikely to be reached)
and early-stopping patience in case it converges sooner. workers=2 balances dataloader
parallelism against this machine's 16GB RAM, after workers=8 (default) crashed with a
Windows pagefile commit-limit error during the smoke test.
"""

from pathlib import Path

from ultralytics import YOLO

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "full_train"

EPOCHS = 300
TIME_HOURS = 10
PATIENCE = 20


def main() -> None:
    model = YOLO("yolo26l.pt")
    model.train(
        data="VisDrone.yaml",
        epochs=EPOCHS,
        time=TIME_HOURS,
        patience=PATIENCE,
        imgsz=640,
        batch=8,
        workers=2,
        device=0,
        project=str(RESULTS_DIR),
        name="train",
        exist_ok=True,
    )

    best_pt = RESULTS_DIR / "train" / "weights" / "best.pt"
    print(f"\n=== Final eval of best.pt on full VisDrone val split ===")
    metrics = YOLO(str(best_pt)).val(
        data="VisDrone.yaml", split="val", project=str(RESULTS_DIR), name="final_eval", exist_ok=True
    )
    print(f"mAP50:    {metrics.box.map50:.4f}")
    print(f"mAP50-95: {metrics.box.map:.4f}")
    print(f"\nWeights saved at: {best_pt}")


if __name__ == "__main__":
    main()
