"""Run the full detection -> tracking -> stitching pipeline on any drone video file
(real footage, downloaded clips) — the video-file counterpart of
process_recorded_flight.py, sharing the same tracker configs and stitching step.

Outputs go to ml/data/results/video_validation/<video name>/<tracker or --out-name>/:
run_config.json, trajectories.csv, annotated.mp4 (tracker's raw IDs), and, unless
--no-stitch, trajectories_stitched.csv, stitch_links.json, then (unless --no-postprocess)
trajectories_final.csv + track_summary.csv and annotated_final.mp4 (final IDs + HUD).

--imgsz: the detector was trained at 640; for 720p footage with small vehicles
1280 finds noticeably more of them (checked on the roundabout clip: ~35.5 vs ~30.5
vehicles/frame, the extra boxes being real vehicles) at ~3x the detection cost.

Usage:
    python ml/violation_engine/process_video.py "path/to/video.mp4"
    python ml/violation_engine/process_video.py "path/to/video.mp4" --imgsz 640 --tracker bytetrack
"""

import argparse
from pathlib import Path

import cv2

from extract_trajectories import BEST_PT
from process_recorded_flight import TRACKER_CONFIGS, run_tracking
from stitch_tracklets import print_stats, stitch

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "video_validation"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path, help="Input video file")
    ap.add_argument("--tracker", choices=sorted(TRACKER_CONFIGS), default="tracktrack_ours")
    ap.add_argument("--imgsz", type=int, default=1280, help="Detector input size (default 1280, see module docstring)")
    ap.add_argument("--no-stitch", action="store_true", help="Skip the tracklet-stitching pass")
    ap.add_argument("--no-postprocess", action="store_true", help="Skip class voting + track filtering after stitching")
    ap.add_argument("--class-gates", action="store_true", help="A4: require bus/truck conf >= 0.4")
    ap.add_argument("--size-filter", action="store_true", help="A4: drop boxes far outside the median vehicle size")
    ap.add_argument("--max-frames", type=int, default=None, help="Stop after this many frames (smoke tests)")
    ap.add_argument("--no-video", action="store_true", help="Skip writing annotated videos")
    ap.add_argument("--weights", type=Path, default=None, help="Detector weights (default: our full_train best.pt)")
    ap.add_argument("--out-name", default=None,
                    help="Output subfolder name (default: the tracker name) — use one per experiment")
    args = ap.parse_args()

    weights = args.weights or BEST_PT
    if not weights.exists():
        raise SystemExit(f"{weights} not found.")
    if not args.video.exists():
        raise SystemExit(f"{args.video} not found.")

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    print(f"[video] {args.video.name}: {n_total} frames, {fps:.1f} fps, {size[0]}x{size[1]}")

    out_dir = RESULTS_DIR / args.video.stem / (args.out_name or args.tracker)
    csv_path, n_rows, n_frames, n_ids = run_tracking(args.video, out_dir, args.tracker, fps, imgsz=args.imgsz,
                                                     class_gates=args.class_gates, size_filter=args.size_filter,
                                                     max_frames=args.max_frames, video=not args.no_video, weights=weights)
    print(f"[tracker] {args.tracker}, imgsz {args.imgsz}")
    print(f"[trajectories] {n_rows} rows across {n_frames} frames -> {csv_path}")
    print(f"[tracking] unique vehicle track IDs: {n_ids}")

    if not args.no_stitch and n_rows:
        print_stats(stitch(args.video, csv_path, fps, video=not args.no_video, postprocess=not args.no_postprocess))


if __name__ == "__main__":
    main()
