"""Detector retrain (Improvement Plan Stage 5; Detector_Retraining_Plan.md Section 7).

Fine-tunes our current YOLO26l (full_train best.pt, 10 VisDrone classes) on the 3-class
retrain_v1 dataset (0 car incl. van, 1 bus, 2 truck); Ultralytics replaces the 10-class head
with a 3-class one and keeps every other weight.

Settings (plan Section 7): imgsz 960, epochs 60 capped at time=22 h (the LR schedule is fitted
to the time cap), patience 15, mosaic with close_mosaic 10, scale 0.5, flips both ways (top-down
views), and a motion-blur augmentation on ~20% of images (Albumentations MotionBlur, kernel
7-25 px, random direction) so vehicles survive sudden camera moves. Ultralytics' own blur is
p=0.01, too weak. best.pt / last.pt are rewritten every epoch, so a crash loses at most one epoch.

    # 1. timing test: one epoch on 10% of train (plus full val) -> extrapolated hours per epoch
    python ml/detection/train_retrain.py --timing-test
    # 2. the full run (~24 h)
    python ml/detection/train_retrain.py
    # if it stops (crash, reboot): continue from last.pt with the same settings
    python ml/detection/train_retrain.py --resume

Outputs: ml/data/results/retrain_v1/<name>/ (weights/best.pt, results.csv, run_config.json),
then a final 3-class evaluation of best.pt on the val and test splits.
"""

import argparse
import datetime
import json
import subprocess
import time
from pathlib import Path

from ultralytics import YOLO

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_YAML = REPO_ROOT / "ml" / "data" / "datasets" / "retrain_v1" / "data.yaml"
START_WEIGHTS = REPO_ROOT / "ml" / "data" / "results" / "full_train" / "train" / "weights" / "best.pt"
RESULTS_DIR = REPO_ROOT / "ml" / "data" / "results" / "retrain_v1"


def motion_blur() -> list:
    import albumentations as A
    return [A.MotionBlur(blur_limit=(7, 25), p=0.2)]


def evaluate(best_pt: Path, run_dir: Path, imgsz: int, batch: int) -> dict:
    out = {}
    for split in ("val", "test"):
        m = YOLO(str(best_pt)).val(data=str(DATA_YAML), split=split, imgsz=imgsz, batch=batch,
                                   project=str(run_dir), name=f"final_{split}", exist_ok=True, plots=True)
        per_class = {m.names[c]: round(float(m.box.ap50[i]), 4) for i, c in enumerate(m.box.ap_class_index)}
        out[split] = {"map50": round(float(m.box.map50), 4), "map50_95": round(float(m.box.map), 4),
                      "precision": round(float(m.box.mp), 4), "recall": round(float(m.box.mr), 4),
                      "ap50_per_class": per_class}
        print(f"[retrain] {split}: mAP50 {out[split]['map50']:.4f}  mAP50-95 {out[split]['map50_95']:.4f}  "
              f"P {out[split]['precision']:.3f}  R {out[split]['recall']:.3f}  per class {per_class}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timing-test", action="store_true", help="1 epoch on --fraction of train, then report time")
    ap.add_argument("--fraction", type=float, default=0.1, help="Share of train used by --timing-test")
    ap.add_argument("--resume", action="store_true", help="Continue the run in --name from its last.pt")
    ap.add_argument("--name", default=None, help="Run folder name (default: train, or timing_test)")
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--batch", type=int, default=4,
                    help="Batch 6 needed 9.7 GB at 960 on the 8 GB RTX 4060 (spills to shared memory, ~3 s/it)")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--hours", type=float, default=22.0, help="Ultralytics time= cap; overrides epochs when hit")
    ap.add_argument("--patience", type=int, default=15)
    ap.add_argument("--workers", type=int, default=4, help="Drop to 2 if Windows reports a pagefile/commit error")
    args = ap.parse_args()

    if not DATA_YAML.exists():
        raise SystemExit(f"{DATA_YAML} not found; run ml/detection/build_retrain_dataset.py first")
    name = args.name or ("timing_test" if args.timing_test else "train")
    run_dir = RESULTS_DIR / name

    if args.resume:
        last = run_dir / "weights" / "last.pt"
        if not last.exists():
            raise SystemExit(f"{last} not found")
        YOLO(str(last)).train(resume=True, augmentations=motion_blur())
    else:
        train_args = dict(
            data=str(DATA_YAML), imgsz=args.imgsz, batch=args.batch, workers=args.workers, device=0,
            epochs=1 if args.timing_test else args.epochs, time=None if args.timing_test else args.hours,
            patience=args.patience, fraction=args.fraction if args.timing_test else 1.0,
            mosaic=1.0, close_mosaic=0 if args.timing_test else 10, scale=0.5, fliplr=0.5, flipud=0.5,
            cache=False, project=str(RESULTS_DIR), name=name, exist_ok=True,
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                cwd=REPO_ROOT).stdout.strip()
        (run_dir / "run_config.json").write_text(json.dumps({
            "date": datetime.datetime.now().isoformat(timespec="seconds"), "git_commit": commit,
            "start_weights": str(START_WEIGHTS.relative_to(REPO_ROOT)), "timing_test": args.timing_test,
            "augmentations": "albumentations MotionBlur(blur_limit=(7, 25), p=0.2)", **train_args,
        }, indent=2))
        t0 = time.time()
        YOLO(str(START_WEIGHTS)).train(**train_args, augmentations=motion_blur())
        if args.timing_test:
            hours = (time.time() - t0) / 3600
            per_epoch = hours / args.fraction
            print(f"\n[timing] {hours:.2f} h for 1 epoch on {args.fraction:.0%} of train (incl. setup + full val)")
            print(f"[timing] rough full epoch: ~{per_epoch:.2f} h -> ~{args.hours / per_epoch:.0f} epochs "
                  f"in {args.hours:g} h (over-estimate: setup and val are not scaled by the fraction)")
            return

    best = run_dir / "weights" / "best.pt"
    if best.exists():
        metrics = evaluate(best, run_dir, args.imgsz, args.batch)
        (run_dir / "final_metrics.json").write_text(json.dumps(metrics, indent=2))
        print(f"[retrain] best weights: {best}")


if __name__ == "__main__":
    main()
