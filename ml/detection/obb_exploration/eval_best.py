"""Evaluate the smoke test's best.pt checkpoint (best-fitness epoch, not last epoch) on the
held-out val split, with the same per-class breakdown as the zero-shot baseline for comparison.
"""

from pathlib import Path

from ultralytics import YOLO

DATA_YAML = (
    Path(__file__).resolve().parents[2] / "data" / "datasets" / "dota_vehicle_subset" / "dota_vehicle_subset.yaml"
)
BEST_PT = Path(__file__).resolve().parents[2] / "data" / "results" / "smoke_test" / "train" / "weights" / "best.pt"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "results" / "smoke_test"


def main() -> None:
    model = YOLO(str(BEST_PT))
    model.val(data=str(DATA_YAML), split="val", project=str(RESULTS_DIR), name="best_eval", exist_ok=True)


if __name__ == "__main__":
    main()
