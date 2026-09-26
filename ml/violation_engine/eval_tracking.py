"""Stage 3/6 — score a tracker's output against a hand-labeled ground-truth
clip, so a tracker/threshold change can be judged by a real number (ID
switches, MOTA, IDF1) instead of eyeballing an annotated video.

See docs/Vehicle_Tracking_ID_Consistency_Plan.md, Phase 1, for why this
exists: without a fixed, human-verified "correct answer" to score against,
any before/after comparison between trackers (e.g. ByteTrack vs BoT-SORT)
is another guess — the same mistake already made once tuning ByteTrack
thresholds against unverified footage (docs/main_project_tracker.md,
Section 2.3).

Ground truth format: same CSV schema as this project's trajectories.csv
(frame, track_id, cx, cy, w, h — time_s/class/conf columns are ignored if
present), produced by hand-labeling a short clip frame-by-frame (e.g. in
CVAT, then exported/converted to this schema). track_id must be a fixed,
human-assigned ID that never changes for the same physical vehicle across
the clip — that's the "correct answer" everything else is measured against.

Needs `motmetrics` (pip install motmetrics) — not part of the project's
existing dependencies, installed separately for this evaluation step only.

Usage:
    python ml/violation_engine/eval_tracking.py --gt path/to/ground_truth.csv --pred path/to/trajectories.csv
    python ml/violation_engine/eval_tracking.py --gt gt.csv --pred pred.csv --start-frame 350 --end-frame 549
"""

import argparse
import csv
from pathlib import Path

import numpy as np

# motmetrics 1.4.0 calls np.asfarray, removed in NumPy 2.0 — shim it back in
# rather than pin an older numpy (which ultralytics/torch elsewhere need
# current NumPy 2.x for).
if not hasattr(np, "asfarray"):
    np.asfarray = lambda a, dtype=float: np.asarray(a, dtype=dtype)

import motmetrics as mm


def load_frames(csv_path: Path, start_frame: int | None, end_frame: int | None) -> dict[int, list[tuple[int, float, float, float, float]]]:
    """frame_idx -> list of (track_id, x, y, w, h) — x,y is the top-left
    corner, matching the (x,y,w,h) rectangle format motmetrics.distances
    expects (NOT xyxy, and NOT the cx/cy center format trajectories.csv uses)."""
    frames: dict[int, list[tuple[int, float, float, float, float]]] = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            frame = int(row["frame"])
            if start_frame is not None and frame < start_frame:
                continue
            if end_frame is not None and frame > end_frame:
                continue
            cx, cy, w, h = float(row["cx"]), float(row["cy"]), float(row["w"]), float(row["h"])
            x, y = cx - w / 2, cy - h / 2
            frames.setdefault(frame, []).append((int(row["track_id"]), x, y, w, h))
    return frames


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", type=Path, required=True, help="Hand-labeled ground-truth CSV")
    ap.add_argument("--pred", type=Path, required=True, help="Tracker output CSV (e.g. trajectories.csv)")
    ap.add_argument("--start-frame", type=int, default=None, help="Only score frames >= this (default: all)")
    ap.add_argument("--end-frame", type=int, default=None, help="Only score frames <= this (default: all)")
    ap.add_argument("--iou-thresh", type=float, default=0.5,
                     help="Minimum IoU (0-1) for a ground-truth box and a predicted box to be considered a possible match")
    args = ap.parse_args()

    if not args.gt.exists():
        raise SystemExit(f"{args.gt} not found.")
    if not args.pred.exists():
        raise SystemExit(f"{args.pred} not found.")

    gt_frames = load_frames(args.gt, args.start_frame, args.end_frame)
    pred_frames = load_frames(args.pred, args.start_frame, args.end_frame)

    all_frames = sorted(set(gt_frames) | set(pred_frames))
    if not all_frames:
        raise SystemExit("No overlapping frames between --gt and --pred in the given range.")

    acc = mm.MOTAccumulator(auto_id=True)

    for frame in all_frames:
        gt_boxes = gt_frames.get(frame, [])
        pred_boxes = pred_frames.get(frame, [])

        gt_ids = [b[0] for b in gt_boxes]
        pred_ids = [b[0] for b in pred_boxes]
        gt_xywh = [b[1:] for b in gt_boxes]
        pred_xywh = [b[1:] for b in pred_boxes]

        # motmetrics' `max_iou` kwarg is actually a max *distance* (1 - IoU)
        # threshold, so a minimum-IoU-of-0.5 requirement is max_iou=1-0.5=0.5
        # (symmetric here, but not in general — see distances.py's docstring).
        dist_matrix = mm.distances.iou_matrix(gt_xywh, pred_xywh, max_iou=1 - args.iou_thresh)
        acc.update(gt_ids, pred_ids, dist_matrix)

    mh = mm.metrics.create()
    summary = mh.compute(
        acc,
        metrics=["mota", "idf1", "num_switches", "num_false_positives", "num_misses",
                 "num_fragmentations", "num_matches", "num_objects", "num_predictions"],
        name=args.pred.stem,
    )

    print(f"Scored {len(all_frames)} frames ({all_frames[0]}-{all_frames[-1]}) "
          f"from {args.gt.name} vs {args.pred.name}\n")
    print(mm.io.render_summary(
        summary,
        formatters=mh.formatters,
        namemap=mm.io.motchallenge_metric_names,
    ))
    print(
        "\nid_switches = how many times the same ground-truth vehicle's predicted ID changed "
        "(the exact bug this evaluation exists to catch)."
    )


if __name__ == "__main__":
    main()
