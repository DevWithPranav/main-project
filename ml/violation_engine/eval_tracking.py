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


SUMMARY_METRICS = ["mota", "motp", "idf1", "idp", "idr", "num_switches", "num_false_positives", "num_misses",
                   "num_fragmentations", "num_matches", "num_objects", "num_predictions",
                   "num_unique_objects", "mostly_tracked", "partially_tracked", "mostly_lost"]


def hota(gt_frames: dict, pred_frames: dict, all_frames: list[int]) -> dict:
    """HOTA (Luiten et al., IJCV 2021) via TrackEval's own HOTA class, fed IoU matrices directly (no dataset
    loader). Averaged over its 19 IoU thresholds 0.05-0.95. DetA = detection accuracy, AssA = association
    (identity) accuracy, LocA = localisation; HOTA = sqrt(DetA * AssA) per threshold.
    TrackEval is installed with --no-deps (PyPI `trackeval` 1.3.0, a packaging of the MIT-licensed
    official code); it prints a BURST/pycocotools import warning, silenced here."""
    import contextlib
    import io as _io
    with contextlib.redirect_stdout(_io.StringIO()):
        from trackeval.metrics.hota import HOTA

    gt_map, pr_map = {}, {}
    data = {"gt_ids": [], "tracker_ids": [], "similarity_scores": []}
    for frame in all_frames:
        g, p = gt_frames.get(frame, []), pred_frames.get(frame, [])
        data["gt_ids"].append(np.array([gt_map.setdefault(b[0], len(gt_map)) for b in g], dtype=int))
        data["tracker_ids"].append(np.array([pr_map.setdefault(b[0], len(pr_map)) for b in p], dtype=int))
        dist = mm.distances.iou_matrix([b[1:] for b in g], [b[1:] for b in p], max_iou=1.0)
        data["similarity_scores"].append(np.nan_to_num(1 - np.asarray(dist, dtype=float), nan=0.0).reshape(len(g), len(p)))
    data.update(num_gt_ids=len(gt_map), num_tracker_ids=len(pr_map),
                num_gt_dets=sum(len(x) for x in data["gt_ids"]), num_tracker_dets=sum(len(x) for x in data["tracker_ids"]),
                num_timesteps=len(all_frames))
    res = HOTA().eval_sequence(data)
    return {k.lower(): float(np.mean(res[k])) for k in ("HOTA", "DetA", "AssA", "LocA")}


def score(gt_csv: Path, pred_csv: Path, start_frame: int | None = None, end_frame: int | None = None,
          iou_thresh: float = 0.5, fp_track_match_ratio: float = 0.5) -> dict:
    """Score pred_csv against gt_csv. Returns the motmetrics summary plus:
      det_precision / det_recall  box-level, from the same IoU matching (matches / predictions, matches / GT boxes)
      pred_ids                    unique predicted track IDs
      fp_tracks                   predicted IDs matched to a GT box in < fp_track_match_ratio of their boxes
                                  (false vehicles: snow/roof blobs, duplicates, boxes too loose to reach the IoU)
    Frames present in neither file contribute nothing, so frames with no vehicle in the GT must still be
    inside [start_frame, end_frame] for predictions there to count as false positives — they do, because
    any predicted box creates the frame."""
    gt_frames = load_frames(gt_csv, start_frame, end_frame)
    pred_frames = load_frames(pred_csv, start_frame, end_frame)
    all_frames = sorted(set(gt_frames) | set(pred_frames))
    if not all_frames:
        raise SystemExit("No overlapping frames between the GT and the prediction in the given range.")

    acc = mm.MOTAccumulator(auto_id=True)
    for frame in all_frames:
        gt_boxes = gt_frames.get(frame, [])
        pred_boxes = pred_frames.get(frame, [])
        # motmetrics' `max_iou` kwarg is actually a max *distance* (1 - IoU)
        # threshold, so a minimum-IoU-of-0.5 requirement is max_iou=1-0.5=0.5
        # (symmetric here, but not in general — see distances.py's docstring).
        dist_matrix = mm.distances.iou_matrix([b[1:] for b in gt_boxes], [b[1:] for b in pred_boxes],
                                              max_iou=1 - iou_thresh)
        acc.update([b[0] for b in gt_boxes], [b[0] for b in pred_boxes], dist_matrix)

    mh = mm.metrics.create()
    summary = mh.compute(acc, metrics=SUMMARY_METRICS, name=pred_csv.stem)
    ratios = {"mota", "motp", "idf1", "idp", "idr"}
    out = {k: (float(v) if k in ratios else int(v)) for k, v in summary.iloc[0].items()}

    events = acc.mot_events
    matched = events[events["Type"].isin(["MATCH", "SWITCH"])]["HId"].value_counts()
    boxes_per_pred = {}
    for boxes in pred_frames.values():
        for b in boxes:
            boxes_per_pred[b[0]] = boxes_per_pred.get(b[0], 0) + 1
    fp_tracks = [tid for tid, n in boxes_per_pred.items() if matched.get(tid, 0) < fp_track_match_ratio * n]

    out.update(hota(gt_frames, pred_frames, all_frames))
    out.update({
        "frames_scored": len(all_frames), "first_frame": all_frames[0], "last_frame": all_frames[-1],
        "det_precision": out["num_matches"] / max(out["num_predictions"], 1),
        "det_recall": out["num_matches"] / max(out["num_objects"], 1),
        "pred_ids": len(boxes_per_pred), "gt_ids": out["num_unique_objects"],
        "fp_tracks": len(fp_tracks), "fp_track_ids": sorted(fp_tracks),
    })
    return out


def format_score(s: dict) -> str:
    return (f"HOTA {s['hota']:.3f}  IDF1 {s['idf1']:.3f}  MOTA {s['mota']:.3f}  ID switches {s['num_switches']}  "
            f"det P {s['det_precision']:.3f} / R {s['det_recall']:.3f}  "
            f"pred IDs {s['pred_ids']} (GT {s['gt_ids']})  FP tracks {s['fp_tracks']}  "
            f"MT/PT/ML {s['mostly_tracked']}/{s['partially_tracked']}/{s['mostly_lost']}")


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

    s = score(args.gt, args.pred, args.start_frame, args.end_frame, args.iou_thresh)
    print(f"Scored {s['frames_scored']} frames ({s['first_frame']}-{s['last_frame']}) "
          f"from {args.gt.name} vs {args.pred.name}\n")
    print(format_score(s))
    print(f"FN {s['num_misses']}  FP {s['num_false_positives']}  fragmentations {s['num_fragmentations']}  "
          f"IDP {s['idp']:.3f}  IDR {s['idr']:.3f}")
    print(
        "\nid_switches = how many times the same ground-truth vehicle's predicted ID changed "
        "(the exact bug this evaluation exists to catch)."
    )


if __name__ == "__main__":
    main()
