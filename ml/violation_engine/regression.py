"""One-command regression table (Improvement Plan W4 / Phase 3).

Runs the full pipeline (detect + track -> stitch -> post-process) for each config on each
ground-truth clip under ml/data/eval/ (every folder with gt.csv + gt_info.json), each via
run_experiment.py (fresh process, logged to experiments.csv), then prints one table.

    python ml/violation_engine/regression.py                          # default configs, all clips
    python ml/violation_engine/regression.py --configs botsort tracktrack_ours --clips gt_1080p
    python ml/violation_engine/regression.py --table                  # no re-run: rescore the latest run
                                                                      # of each config on each clip

--table rescores from the saved trajectory CSVs, so older runs also get the newer metrics (HOTA).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from eval_tracking import score  # noqa: E402
from run_experiment import EVAL_DIR, EXPERIMENTS_DIR, REPO_ROOT, STAGE_FILES  # noqa: E402

DEFAULT_CONFIGS = ["tracktrack_ours", "botsort"]  # pipeline tracker (plan 3.5) + previous one for reference
COLUMNS = [("hota", "HOTA", ".3f"), ("idf1", "IDF1", ".3f"), ("mota", "MOTA", ".3f"), ("num_switches", "IDsw", "d"),
           ("det_precision", "P", ".3f"), ("det_recall", "R", ".3f"), ("fp_tracks", "FPtr", "d"),
           ("pred_ids", "IDs", "d"), ("gt_ids", "GT", "d")]


def clips() -> list[str]:
    return sorted(p.name for p in EVAL_DIR.iterdir() if (p / "gt.csv").exists() and (p / "gt_info.json").exists())


def run_settings(passthrough: list[str]) -> dict:
    """The detector settings a run must have to count as 'this config' in --table mode."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--conf", type=float, default=0.1)
    ap.add_argument("--class-gates", action="store_true")
    ap.add_argument("--size-filter", action="store_true")
    a, _ = ap.parse_known_args(passthrough)
    return {"imgsz": a.imgsz or "model default", "conf": a.conf, "class_gates": a.class_gates,
            "size_filter": a.size_filter}


def latest_run(clip: str, config: str, settings: dict) -> Path | None:
    """Newest full run of `config` on `clip` with the given detector settings (not a --reuse variant)."""
    for run_dir in sorted((p for p in (EXPERIMENTS_DIR / clip).glob(f"*_{config}") if p.is_dir()), reverse=True):
        cfg_path = run_dir / "run_config.json"
        if not cfg_path.exists():
            continue
        cfg = json.loads(cfg_path.read_text())
        if (cfg.get("imgsz") == settings["imgsz"] and cfg.get("conf") == settings["conf"]
                and bool(cfg.get("class_gates")) == settings["class_gates"]
                and bool(cfg.get("size_filter")) == settings["size_filter"]):
            return run_dir
    return None


def print_table(results: list[tuple[str, str, str, dict, Path]]) -> None:
    head = f"{'clip':12s} {'config':22s} {'stage':8s} " + " ".join(f"{h:>6s}" for _, h, _ in COLUMNS)
    print(head)
    print("-" * len(head))
    for clip, config, stage, s, _ in results:
        print(f"{clip:12s} {config:22s} {stage:8s} " + " ".join(f"{s[k]:>6{fmt}}" for k, _, fmt in COLUMNS))
    print("\nHOTA/IDF1/MOTA: higher is better. IDsw: ID switches. P/R: box precision/recall at IoU 0.5. "
          "FPtr: predicted tracks matching a GT vehicle in < 50% of their boxes.")
    for (clip, config), run_dir in {(c, cfg): d for c, cfg, _, _, d in results}.items():
        print(f"  {clip}/{config}: {run_dir.relative_to(REPO_ROOT).as_posix()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="+", default=DEFAULT_CONFIGS, help="Tracker config names")
    ap.add_argument("--clips", nargs="+", default=None, help="Clip folders under ml/data/eval/ (default: all)")
    ap.add_argument("--table", action="store_true", help="Don't run; rescore the latest existing run of each")
    ap.add_argument("--member", default="")
    ap.add_argument("--stages", nargs="+", default=list(STAGE_FILES), choices=list(STAGE_FILES))
    args, passthrough = ap.parse_known_args()  # anything else (--imgsz, --scene-map, ...) goes to run_experiment

    todo = args.clips or clips()
    if not todo:
        raise SystemExit(f"No clips with gt.csv + gt_info.json under {EVAL_DIR}")

    settings = run_settings(passthrough)
    results = []
    for clip in todo:
        for config in args.configs:
            if not args.table:
                subprocess.run([sys.executable, str(HERE / "run_experiment.py"), "--clip", clip, "--tracker", config,
                                "--change", f"regression: {config}", "--member", args.member, *passthrough],
                               check=True)
            run_dir = latest_run(clip, config, settings)
            if run_dir is None:
                print(f"[regression] no run of {config} on {clip} — skipped")
                continue
            for stage in args.stages:
                pred = run_dir / STAGE_FILES[stage]
                if pred.exists():
                    results.append((clip, config, stage, score(EVAL_DIR / clip / "gt.csv", pred), run_dir))
    print()
    print_table(results)


if __name__ == "__main__":
    main()
