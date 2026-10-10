"""Build Plan M9: train the pothole segmenter, stock YOLO26l-seg (baseline) or DSConv+SimAM+GELU (modified).

Both variants use the same data, recipe and seed so the comparison isolates the architecture. The
default data is the session-grouped split (prepare_grouped.py); the 2025 baseline in runs/baseline
used the random split and is not comparable on test (near-duplicate frames leak across it).

`--time` is ultralytics' wall-clock cap in hours: it stops cleanly and keeps best.pt, so each run
fits its slot even if the it/s estimate is off. Runs go to ml/pothole/runs/<name>/ (never reused:
an existing name is refused unless --exist-ok).

Usage:
    python ml/pothole/prepare_grouped.py                                   # once
    python ml/pothole/train_m9.py --variant modified --time 4.5 --name m9_modified
    python ml/pothole/train_m9.py --variant baseline --time 3 --name m9_baseline
    python ml/pothole/train_m9.py --variant modified --epochs 1 --fraction 0.05 --name m9_smoke --exist-ok   # smoke
    python ml/pothole/train_m9.py --variant modified --time 4 --name m9_mod_cp --set copy_paste=0.3 --seed 1
"""

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from modify_model import make_trainer  # noqa: E402  (also puts ml/pothole on sys.path for unpickling)

from ultralytics import YOLO  # noqa: E402

RUNS = HERE / "runs"
GROUPED_YAML = HERE / "data" / "grouped" / "data.yaml"

# Recipe: same as train_baseline.py (2025 baseline, tracker section 3.1) where it worked; batch 4 at
# 640 was what the 8 GB card took, the RTX 4050 has 6 GB (checked by the smoke run, see M9_surface.md).
RECIPE = dict(imgsz=640, batch=4, patience=25, workers=0, cache="ram", seed=0, mosaic=1.0, fliplr=0.5,
              hsv_h=0.015, hsv_s=0.7, hsv_v=0.4, scale=0.5, translate=0.1, close_mosaic=10, plots=True,
              save_period=-1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=["modified", "baseline"], required=True)
    ap.add_argument("--data", default=str(GROUPED_YAML))
    ap.add_argument("--weights", default=str(REPO / "yolo26l-seg.pt"), help="stock pretrained start point")
    ap.add_argument("--name", required=True)
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--time", type=float, default=None, help="hours cap (overrides epochs)")
    ap.add_argument("--imgsz", type=int, default=RECIPE["imgsz"])
    ap.add_argument("--batch", type=int, default=RECIPE["batch"])
    ap.add_argument("--fraction", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=RECIPE["workers"])
    ap.add_argument("--no-gelu", action="store_true", help="modified variant without GELU (ablation)")
    ap.add_argument("--device", default="0")
    ap.add_argument("--exist-ok", action="store_true")
    ap.add_argument("--seed", type=int, default=RECIPE["seed"], help="repeat a run with seeds 0/1/2 to see the spread")
    ap.add_argument("--set", nargs="*", default=[], metavar="KEY=VALUE",
                    help="extra ultralytics train args, e.g. copy_paste=0.3 cos_lr=True imgsz=1024")
    a = ap.parse_args()

    if not Path(a.data).exists():
        raise SystemExit(f"{a.data} missing: run python ml/pothole/prepare_grouped.py first")
    if (RUNS / a.name).exists() and not a.exist_ok:
        raise SystemExit(f"{RUNS / a.name} exists; pick a new --name (results are never overwritten)")

    args = {**RECIPE, "data": a.data, "epochs": a.epochs, "imgsz": a.imgsz, "batch": a.batch,
            "fraction": a.fraction, "workers": a.workers, "device": a.device, "project": str(RUNS),
            "name": a.name, "exist_ok": a.exist_ok, "seed": a.seed}
    for kv in a.set:
        k, _, v = kv.partition("=")
        try:
            args[k] = json.loads(v.lower() if v.lower() in ("true", "false") else v)
        except json.JSONDecodeError:
            args[k] = v
    if a.time:
        args["time"] = a.time
    model = YOLO(a.weights)
    t0 = time.time()
    if a.variant == "modified":
        mods = {"dsconv": True, "simam": True, "gelu": not a.no_gelu}
        res = model.train(trainer=make_trainer(mods), **args)
    else:
        res = model.train(**args)
    wall = time.time() - t0
    out = {"variant": a.variant, "args": {k: v for k, v in args.items()}, "wall_s": round(wall, 1),
           "final_val": {k: float(v) for k, v in (getattr(res, "results_dict", None) or {}).items()}}
    run_dir = Path(getattr(model.trainer, "save_dir", RUNS / a.name))
    (run_dir / "m9_train_summary.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
