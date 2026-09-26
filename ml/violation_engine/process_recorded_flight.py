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
import json
from pathlib import Path

import cv2
import torchvision
from ultralytics import YOLO

from extract_trajectories import BEST_PT, VEHICLE_CLASS_IDS
from stitch_tracklets import stitch

RECORDED_FLIGHTS_DIR = Path(__file__).resolve().parents[2] / "simulation" / "data_export" / "recorded_flights"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "recorded_flight_validation"
TRACKER_CONFIGS = {
    "bytetrack": str(Path(__file__).resolve().parent / "bytetrack_sim.yaml"),
    "botsort": str(Path(__file__).resolve().parent / "botsort_sim.yaml"),
}
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


def run_tracking(source: Path, out_dir: Path, tracker: str, fps: float, frame_times: dict[int, float] | None = None,
                 imgsz: int | None = None, conf: float = DETECT_CONF) -> tuple[Path, int, int, int]:
    """Detect + track every frame of `source` (frame directory or video file), writing
    trajectories.csv and the tracker's own annotated.mp4 into out_dir.
    Returns (csv_path, n_rows, n_frames, n_unique_ids)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "trajectories.csv"
    video_path = out_dir / "annotated.mp4"
    frame_times = frame_times or {}

    model = YOLO(str(BEST_PT))
    model.add_callback("on_predict_postprocess_end", dedupe_boxes)  # must be added before track() registers the tracker
    results = model.track(
        source=str(source),
        tracker=TRACKER_CONFIGS[tracker],
        classes=VEHICLE_CLASS_IDS,
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
                        frame_idx, time_s, int(track_id), names[int(box.cls)],
                        round(cx, 1), round(cy, 1), round(w, 1), round(h, 1), round(float(box.conf), 3),
                    ])
                    track_ids.add(int(track_id))
                    n_rows += 1
            annotated = result.plot()
            if writer is None:
                h, w = annotated.shape[:2]
                writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            writer.write(annotated)
            if n_frames % 500 == 0:
                print(f"[tracking] {n_frames} frames processed", flush=True)
    if writer is not None:
        writer.release()
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
    ap.add_argument("--tracker", choices=sorted(TRACKER_CONFIGS), default="botsort",
                     help="Which tracker config to use (default: botsort)")
    ap.add_argument("--no-stitch", action="store_true", help="Skip the tracklet-stitching pass")
    args = ap.parse_args()

    if not BEST_PT.exists():
        raise SystemExit(f"{BEST_PT} not found — run ml/detection/train_full.py first.")

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
    csv_path, n_rows, n_frames, n_ids = run_tracking(frames_dir, out_dir, args.tracker, output_fps, frame_times)

    print(f"[tracker] {args.tracker} ({TRACKER_CONFIGS[args.tracker]})")
    print(f"[trajectories] wrote {n_rows} rows across {n_frames} frames -> {csv_path}")
    print(f"[tracking] unique vehicle track IDs across flight: {n_ids}")
    print(f"[video] annotated video ({n_frames} frames, every frame processed) -> {out_dir / 'annotated.mp4'}")

    if not args.no_stitch and n_rows:
        stats = stitch(frames_dir, csv_path, output_fps)
        print(f"[stitch] {stats['links']} links: unique IDs {stats['ids_before']} -> {stats['ids_after']} "
              f"-> {stats['out_csv']}")


if __name__ == "__main__":
    main()
