"""Run the full detection -> tracking -> stitching pipeline on any drone video file
(real footage, downloaded clips) — the video-file counterpart of
process_recorded_flight.py, sharing the same tracker configs and stitching step.

Outputs go to ml/data/results/video_validation/<video name>/<tracker>/:
trajectories.csv, annotated.mp4 (tracker's raw IDs), and, unless --no-stitch,
trajectories_stitched.csv, stitch_links.json, annotated_stitched.mp4 (final IDs + HUD).

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
from stitch_tracklets import stitch

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "video_validation"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path, help="Input video file")
    ap.add_argument("--tracker", choices=sorted(TRACKER_CONFIGS), default="botsort")
    ap.add_argument("--imgsz", type=int, default=1280, help="Detector input size (default 1280, see module docstring)")
    ap.add_argument("--no-stitch", action="store_true", help="Skip the tracklet-stitching pass")
    args = ap.parse_args()

    if not BEST_PT.exists():
        raise SystemExit(f"{BEST_PT} not found — run ml/detection/train_full.py first.")
    if not args.video.exists():
        raise SystemExit(f"{args.video} not found.")

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    print(f"[video] {args.video.name}: {n_total} frames, {fps:.1f} fps, {size[0]}x{size[1]}")

    out_dir = RESULTS_DIR / args.video.stem / args.tracker
    csv_path, n_rows, n_frames, n_ids = run_tracking(args.video, out_dir, args.tracker, fps, imgsz=args.imgsz)
    print(f"[tracker] {args.tracker}, imgsz {args.imgsz}")
    print(f"[trajectories] {n_rows} rows across {n_frames} frames -> {csv_path}")
    print(f"[tracking] unique vehicle track IDs: {n_ids}")

    if not args.no_stitch and n_rows:
        stats = stitch(args.video, csv_path, fps)
        print(f"[stitch] {stats['links']} links: unique IDs {stats['ids_before']} -> {stats['ids_after']}")
        print(f"[output] final annotated video -> {out_dir / 'annotated_stitched.mp4'}")


if __name__ == "__main__":
    main()
