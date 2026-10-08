"""Stage 6 — run the Stage 1/3 detector+tracker against a manually-recorded
CARLA flight (simulation/carla_scripts/record_flight.py), processing every
saved frame in a single streaming pass and writing both a trajectory CSV and
a real annotated video (not just annotated images — Ultralytics only saves
images for a directory-of-frames source, so the video is assembled here).

Run in the project's main venv (has ultralytics/torch) — not the CarlaAir
conda env used to record the footage.

Usage:
    python ml/violation_engine/process_recorded_flight.py
    python ml/violation_engine/process_recorded_flight.py 20260920_190022
    python ml/violation_engine/process_recorded_flight.py 20260920_190022 --tracker botsort
    python ml/violation_engine/process_recorded_flight.py 20260920_190022 --no-stitch

After tracking, stitch_tracklets.py re-links tracks the tracker split into several
IDs and writes trajectories_stitched.csv + annotated_stitched.mp4 alongside the raw
outputs (skip with --no-stitch).
"""

import argparse
import csv
import datetime
import json
import subprocess
import sys
from pathlib import Path

import cv2
import torchvision
import ultralytics
import yaml
from ultralytics import YOLO

from detection_filters import DetectionFilter
from extract_trajectories import BEST_PT
from stitch_tracklets import print_stats, stitch

RECORDED_FLIGHTS_DIR = Path(__file__).resolve().parents[2] / "simulation" / "data_export" / "recorded_flights"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "recorded_flight_validation"
REPO_ROOT = Path(__file__).resolve().parents[2]
# Every tracker yaml in this folder, keyed by file name without the "_sim" suffix:
# bytetrack_sim.yaml -> "bytetrack", botsort_sim.yaml -> "botsort", tracktrack_ours.yaml -> "tracktrack_ours"
TRACKER_CONFIGS = {p.stem.removesuffix("_sim"): str(p) for p in sorted(Path(__file__).resolve().parent.glob("*.yaml"))}
VEHICLE_NAMES = {"car", "van", "truck", "bus"}  # tracked classes, by name so any detector's weights work
DETECT_CONF = 0.1  # default ~0.25 gate drops the sparse/weak detections that break tracking on CARLA footage
DEDUPE_IOU = 0.5


def dedupe_boxes(predictor) -> None:
    """Class-agnostic NMS before tracking. YOLO26's NMS-free head often returns the same
    vehicle twice with different classes (car + van); the tracker then matches one box and
    starts a short-lived new ID from the other. Ultralytics' agnostic_nms / end2end=False
    options don't change this model's output, so it's done here."""
    for i, r in enumerate(predictor.results):
        if r.boxes is not None and len(r.boxes) > 1:
            keep = torchvision.ops.nms(r.boxes.xyxy.float(), r.boxes.conf.float(), DEDUPE_IOU)
            predictor.results[i] = r[keep.sort().values]


def load_frame_times(path: Path) -> dict[int, float]:
    times = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            times[int(row["frame"])] = float(row["time_s"])
    return times


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT,
                               capture_output=True, text=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def vehicle_class_ids(model) -> list[int]:
    return sorted(i for i, n in model.names.items() if n.lower() in VEHICLE_NAMES)


def write_run_config(out_dir: Path, **fields) -> Path:
    """run_config.json next to a run's outputs, so every result can be traced back to
    exactly what produced it (shared rule in the Improvement Plan, Section 4)."""
    tracker_path = Path(fields["tracker_config"])
    cfg = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "command": " ".join(sys.argv),
        "ultralytics": ultralytics.__version__,
        **fields,
        "tracker_yaml": yaml.safe_load(tracker_path.read_text()),
    }
    path = out_dir / "run_config.json"
    path.write_text(json.dumps(cfg, indent=1, default=str))
    return path


def run_tracking(source: Path, out_dir: Path, tracker: str, fps: float, frame_times: dict[int, float] | None = None,
                 imgsz: int | None = None, conf: float = DETECT_CONF, class_gates: bool = False,
                 size_filter: bool = False, max_frames: int | None = None,
                 video: bool = True, weights: Path | None = None) -> tuple[Path, int, int, int]:
    """Detect + track every frame of `source` (frame directory or video file), writing
    trajectories.csv, run_config.json and the tracker's own annotated.mp4 into out_dir.
    class_gates / size_filter: optional A4 detection filters (detection_filters.py).
    max_frames: stop early (smoke tests). weights: detector weights (default: our BEST_PT); vehicle
    classes are picked by name, so another model's class numbering works too.
    Returns (csv_path, n_rows, n_frames, n_unique_ids)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "trajectories.csv"
    video_path = out_dir / "annotated.mp4"
    frame_times = frame_times or {}

    weights = Path(weights or BEST_PT)
    model = YOLO(str(weights))
    classes = vehicle_class_ids(model)
    # callbacks must be added before track() registers the tracker; filters run before dedupe (see detection_filters.py)
    det_filter = DetectionFilter(class_gates, size_filter)
    if class_gates or size_filter:
        model.add_callback("on_predict_postprocess_end", det_filter)
    model.add_callback("on_predict_postprocess_end", dedupe_boxes)
    run_config = write_run_config(
        out_dir, detector_weights=str(weights), source=str(source), tracker=tracker, tracker_config=TRACKER_CONFIGS[tracker], fps=fps,
        imgsz=imgsz or "model default", conf=conf, dedupe_iou=DEDUPE_IOU, classes=classes,
        max_frames=max_frames, **det_filter.config(),
    )
    started = datetime.datetime.now()
    results = model.track(
        source=str(source),
        tracker=TRACKER_CONFIGS[tracker],
        classes=classes,
        conf=conf,
        stream=True,
        persist=True,
        verbose=False,
        **({"imgsz": imgsz} if imgsz else {}),
    )

    writer = None
    track_ids = set()
    n_rows = n_frames = 0
    with open(csv_path, "w", newline="") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(["frame", "time_s", "track_id", "class", "cx", "cy", "w", "h", "conf"])
        for frame_idx, result in enumerate(results):
            n_frames += 1
            time_s = frame_times.get(frame_idx, round(frame_idx / fps, 3))
            if result.boxes.id is not None:
                names = result.names
                for box, track_id in zip(result.boxes, result.boxes.id):
                    cx, cy, w, h = (float(v) for v in box.xywh[0])
                    csv_writer.writerow([
                        frame_idx, time_s, int(track_id), names[int(box.cls)].lower(),
                        round(cx, 1), round(cy, 1), round(w, 1), round(h, 1), round(float(box.conf), 3),
                    ])
                    track_ids.add(int(track_id))
                    n_rows += 1
            if video:
                annotated = result.plot()
                if writer is None:
                    h, w = annotated.shape[:2]
                    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                writer.write(annotated)
            if n_frames % 500 == 0:
                print(f"[tracking] {n_frames} frames processed", flush=True)
            if max_frames and n_frames >= max_frames:
                break
    if writer is not None:
        writer.release()

    elapsed = (datetime.datetime.now() - started).total_seconds()
    cfg = json.loads(run_config.read_text())
    cfg["result"] = {"frames": n_frames, "rows": n_rows, "unique_ids": len(track_ids),
                     "seconds": round(elapsed, 1), "ms_per_frame": round(1000 * elapsed / max(n_frames, 1), 1),
                     "filter_stats": det_filter.stats}
    run_config.write_text(json.dumps(cfg, indent=1, default=str))
    return csv_path, n_rows, n_frames, len(track_ids)


def latest_run_id() -> str:
    runs = sorted((p.name for p in RECORDED_FLIGHTS_DIR.iterdir() if p.is_dir()))
    if not runs:
        raise SystemExit(f"No recorded flights found under {RECORDED_FLIGHTS_DIR} — run record_flight.py first.")
    return runs[-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", nargs="?", default=None,
                     help="Recorded flight run_id under simulation/data_export/recorded_flights/ (default: latest)")
    ap.add_argument("--output-fps", type=float, default=None,
                     help="Playback fps for annotated.mp4 (default: the flight's own measured avg_fps)")
    ap.add_argument("--tracker", choices=sorted(TRACKER_CONFIGS), default="tracktrack_ours",
                     help="Which tracker config to use (default: tracktrack_ours, Improvement Plan 3.5)")
    ap.add_argument("--no-stitch", action="store_true", help="Skip the tracklet-stitching pass")
    ap.add_argument("--no-postprocess", action="store_true", help="Skip class voting + track filtering after stitching")
    ap.add_argument("--imgsz", type=int, default=None, help="Detector input size (default: the model's own)")
    ap.add_argument("--class-gates", action="store_true", help="A4: require bus/truck conf >= 0.4")
    ap.add_argument("--size-filter", action="store_true", help="A4: drop boxes far outside the median vehicle size")
    ap.add_argument("--weights", type=Path, default=None, help="Detector weights (default: our full_train best.pt)")
    args = ap.parse_args()

    weights = args.weights or BEST_PT
    if not weights.exists():
        raise SystemExit(f"{weights} not found.")

    run_id = args.run_id or latest_run_id()
    run_dir = RECORDED_FLIGHTS_DIR / run_id
    frames_dir = run_dir / "frames"
    frame_times_path = run_dir / "frame_times.csv"
    metadata_path = run_dir / "metadata.json"

    frame_paths = sorted(frames_dir.glob("*.jpg"))
    if not frame_paths:
        raise SystemExit(f"{frames_dir} not found or empty.")

    frame_times = load_frame_times(frame_times_path) if frame_times_path.exists() else {}
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    output_fps = args.output_fps or metadata.get("avg_fps") or 15.0

    out_dir = RESULTS_DIR / run_id / args.tracker
    csv_path, n_rows, n_frames, n_ids = run_tracking(frames_dir, out_dir, args.tracker, output_fps, frame_times,
                                                     imgsz=args.imgsz, class_gates=args.class_gates,
                                                     size_filter=args.size_filter, weights=weights)

    print(f"[tracker] {args.tracker} ({TRACKER_CONFIGS[args.tracker]})")
    print(f"[trajectories] wrote {n_rows} rows across {n_frames} frames -> {csv_path}")
    print(f"[tracking] unique vehicle track IDs across flight: {n_ids}")
    print(f"[video] annotated video ({n_frames} frames, every frame processed) -> {out_dir / 'annotated.mp4'}")

    if not args.no_stitch and n_rows:
        print_stats(stitch(frames_dir, csv_path, output_fps, postprocess=not args.no_postprocess))


if __name__ == "__main__":
    main()
