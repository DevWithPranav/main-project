"""Run one pipeline configuration on a ground-truth clip and log the score
(Improvement Plan, W4 / Phase 2).

One run = one change, one fresh Python process (shared rule, plan Section 4): the
tracking + stitching step runs in a subprocess of this script, so no tracker or model
state can leak between runs. Each run is scored at three stages, one row each in
ml/data/results/experiments.csv:
    raw       trajectories.csv           (detector + tracker)
    stitched  trajectories_stitched.csv  (+ tracklet stitching)
    final     trajectories_final.csv     (+ class voting, short / low-confidence track removal)

Outputs go to ml/data/results/experiments/<clip>/<timestamp>_<config>/ with run_config.json,
score.json (all metrics per stage) and the trajectory CSVs.

A clip is a folder under ml/data/eval/ with gt.csv and gt_info.json (import_gt.py);
gt_info.json names the frames folder or video the labels were drawn on.

Usage:
    python ml/violation_engine/run_experiment.py --tracker botsort --change "baseline" --member Afif
    python ml/violation_engine/run_experiment.py --tracker tracktrack_ours --change "C2: TrackTrack"
    python ml/violation_engine/run_experiment.py --tracker botsort --imgsz 1280 --change "imgsz 1280"
    # re-run only the post-processing thresholds on an existing run (no re-tracking):
    python ml/violation_engine/run_experiment.py --reuse <run dir> --min-mean-conf 0.2 --change "D4 conf 0.2"
"""

import argparse
import csv
import datetime
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
EVAL_DIR = REPO_ROOT / "ml" / "data" / "eval"
EXPERIMENTS_DIR = REPO_ROOT / "ml" / "data" / "results" / "experiments"
EXPERIMENTS_CSV = REPO_ROOT / "ml" / "data" / "results" / "experiments.csv"
LOG_COLUMNS = ["date", "member", "clip", "change", "stage", "config", "hota", "idf1", "id_switches", "mota",
               "det_precision", "det_recall", "fp_tracks", "pred_ids", "gt_ids", "mostly_tracked", "mostly_lost",
               "fn", "fp", "deta", "assa", "loca", "ms_per_frame", "run_dir", "git_commit", "notes"]
STAGE_FILES = {"raw": "trajectories.csv", "stitched": "trajectories_stitched.csv", "final": "trajectories_final.csv"}


def load_clip(name: str) -> tuple[Path, dict]:
    clip_dir = EVAL_DIR / name
    gt, info = clip_dir / "gt.csv", clip_dir / "gt_info.json"
    if not gt.exists() or not info.exists():
        raise SystemExit(f"{clip_dir} needs gt.csv and gt_info.json (import_gt.py)")
    return gt, json.loads(info.read_text())


def clip_source(info: dict) -> Path:
    """The exact frames/video the GT was drawn on; a recorded CARLA flight is run from its frames/."""
    source = REPO_ROOT / (info.get("frames_dir") or info["video"])
    if not source.exists():
        raise SystemExit(f"{source} not found")
    return source


def worker(args: argparse.Namespace) -> None:
    """Runs inside the fresh subprocess: detect + track, stitch, post-process."""
    sys.path.insert(0, str(HERE))
    from process_recorded_flight import load_frame_times, run_tracking
    from stitch_tracklets import stitch, write_video

    _, info = load_clip(args.clip)
    source = clip_source(info)
    fps = info["fps_used_for_time_s"]
    times_csv = source.parent / "frame_times.csv"
    frame_times = load_frame_times(times_csv) if source.is_dir() and times_csv.exists() else None

    csv_path, n_rows, _, _ = run_tracking(source, args.out_dir, args.tracker, fps, frame_times, imgsz=args.imgsz,
                                          conf=args.conf, class_gates=args.class_gates,
                                          size_filter=args.size_filter, video=args.video, weights=args.weights)
    if not n_rows:
        print("[experiment] tracker produced no rows")
        return
    if args.scene_map:  # map_x/map_y added before stitching, so they carry through to every stage's CSV
        import scene_map
        scene_map.run(source, csv_path)
    stitch(source, csv_path, fps, video=False, postprocess=False)
    final_rows = postprocess_step(args.out_dir, fps, args.min_duration, args.min_mean_conf, args.gap_fill)
    if args.video:
        write_video(source, final_rows, args.out_dir / "annotated_final.mp4", fps)


def postprocess_step(run_dir: Path, fps: float, min_duration: float, min_mean_conf: float,
                     gap_fill: float = 0) -> list[dict]:
    sys.path.insert(0, str(HERE))
    import postprocess_tracks
    stitched = run_dir / STAGE_FILES["stitched"]
    rows = list(csv.DictReader(open(stitched, newline="")))
    kept, summary, stats = postprocess_tracks.postprocess(rows, fps, min_duration, min_mean_conf, gap_fill)
    postprocess_tracks.write_outputs(stitched, kept, summary, stats["columns"])
    print(f"[postprocess] {postprocess_tracks.format_stats(stats)}")
    return kept


def migrate_log() -> None:
    """Rewrites experiments.csv with the current LOG_COLUMNS if an older header is found (new columns blank)."""
    if not EXPERIMENTS_CSV.exists():
        return
    with open(EXPERIMENTS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == LOG_COLUMNS:
            return
        rows = list(reader)
    with open(EXPERIMENTS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def log_rows(args: argparse.Namespace, run_dir: Path, scores: dict, run_cfg: dict) -> None:
    migrate_log()
    new_file = not EXPERIMENTS_CSV.exists()
    EXPERIMENTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(EXPERIMENTS_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if new_file:
            w.writeheader()
        for stage, s in scores.items():
            w.writerow({
                "date": datetime.datetime.now().isoformat(timespec="seconds"), "member": args.member,
                "clip": args.clip, "change": args.change, "stage": stage,
                "config": Path(run_cfg["tracker_config"]).relative_to(REPO_ROOT).as_posix()
                          if run_cfg.get("tracker_config") else "",
                "hota": round(s["hota"], 4), "deta": round(s["deta"], 4), "assa": round(s["assa"], 4),
                "loca": round(s["loca"], 4), "idf1": round(s["idf1"], 4), "id_switches": s["num_switches"], "mota": round(s["mota"], 4),
                "det_precision": round(s["det_precision"], 4), "det_recall": round(s["det_recall"], 4),
                "fp_tracks": s["fp_tracks"], "pred_ids": s["pred_ids"], "gt_ids": s["gt_ids"],
                "mostly_tracked": s["mostly_tracked"], "mostly_lost": s["mostly_lost"],
                "fn": s["num_misses"], "fp": s["num_false_positives"],
                "ms_per_frame": run_cfg.get("result", {}).get("ms_per_frame", ""),
                "run_dir": run_dir.relative_to(REPO_ROOT).as_posix(), "git_commit": run_cfg.get("git_commit", ""),
                "notes": args.notes,
            })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default="gt_1080p", help="Folder under ml/data/eval/ (default: gt_1080p)")
    ap.add_argument("--tracker", default="tracktrack_ours", help="Tracker config name, as process_recorded_flight.py --tracker")
    ap.add_argument("--change", required=True, help="One line: the single change this run tests")
    ap.add_argument("--member", default="", help="Who ran it")
    ap.add_argument("--notes", default="")
    ap.add_argument("--imgsz", type=int, default=None, help="Detector input size (default: the model's own, 640)")
    ap.add_argument("--conf", type=float, default=0.1, help="Detector confidence threshold")
    ap.add_argument("--weights", type=Path, default=None, help="Detector weights (default: our full_train best.pt)")
    ap.add_argument("--class-gates", action="store_true", help="A4: require bus/truck conf >= 0.4")
    ap.add_argument("--size-filter", action="store_true", help="A4: drop boxes far outside the median vehicle size")
    ap.add_argument("--min-duration", type=float, default=1.0, help="D4: drop final tracks shorter than this (s)")
    ap.add_argument("--min-mean-conf", type=float, default=0.3, help="D4: drop final tracks with lower mean conf")
    ap.add_argument("--gap-fill", type=float, default=1.0,
                    help="D3: interpolate gaps up to this many seconds (0 = off; default 1 since 2026-09-27)")
    ap.add_argument("--video", action="store_true", help="Also write annotated.mp4 + annotated_final.mp4")
    ap.add_argument("--scene-map", action="store_true",
                    help="B2: add map_x/map_y (scene_map.py; ~0.3 s/frame on 1080p) to the trajectory CSVs")
    ap.add_argument("--reuse", type=Path, default=None,
                    help="Existing run dir: skip tracking/stitching, redo post-processing, score 'final' only")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out-dir", type=Path, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.worker:
        worker(args)
        return

    gt_csv, info = load_clip(args.clip)
    sys.path.insert(0, str(HERE))
    from eval_tracking import format_score, score

    if args.reuse:
        run_dir = args.reuse.resolve()
        if not (run_dir / STAGE_FILES["stitched"]).exists():
            raise SystemExit(f"{run_dir} has no {STAGE_FILES['stitched']}")
        # write into a sibling dir so the original run's final CSV stays as it was
        src_dir, run_dir = run_dir, run_dir.with_name(
            f"{run_dir.name}__pp_dur{args.min_duration:g}_conf{args.min_mean_conf:g}_gap{args.gap_fill:g}")
        run_dir.mkdir(exist_ok=True)
        for name in ("run_config.json", STAGE_FILES["stitched"]):
            (run_dir / name).write_bytes((src_dir / name).read_bytes())
        postprocess_step(run_dir, info["fps_used_for_time_s"], args.min_duration, args.min_mean_conf, args.gap_fill)
        stages = ["final"]
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = EXPERIMENTS_DIR / args.clip / f"{stamp}_{args.tracker}"
        cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--out-dir", str(run_dir)] + sys.argv[1:]
        print(f"[experiment] {args.change!r}: {args.tracker} on {args.clip} -> {run_dir}", flush=True)
        subprocess.run(cmd, check=True)
        stages = list(STAGE_FILES)

    run_cfg = json.loads((run_dir / "run_config.json").read_text())
    run_cfg["experiment"] = {"clip": args.clip, "change": args.change, "member": args.member,
                             "min_duration": args.min_duration, "min_mean_conf": args.min_mean_conf,
                             "gap_fill_s": args.gap_fill,
                             "reused_from": str(args.reuse) if args.reuse else None}
    (run_dir / "run_config.json").write_text(json.dumps(run_cfg, indent=1, default=str))

    scores = {}
    for stage in stages:
        pred = run_dir / STAGE_FILES[stage]
        if pred.exists():
            scores[stage] = score(gt_csv, pred)
            print(f"[{stage:8s}] {format_score(scores[stage])}")
    (run_dir / "score.json").write_text(json.dumps(scores, indent=1))
    log_rows(args, run_dir, scores, run_cfg)
    print(f"[experiment] logged {len(scores)} rows -> {EXPERIMENTS_CSV}")


if __name__ == "__main__":
    main()
