"""Stage 6 — run the Stage 1/3 detector+tracker against a CARLA/AirSim sim
flight captured by simulation/carla_scripts/fly_and_capture.py, to check the
model still detects/tracks vehicles correctly on synthetic aerial footage
before Stage 4 (violation logic) is built on top.

Runs directly on the captured frames/ JPGs, not the review-only flight.mp4 —
the mp4 is a second, lossier re-encode that would degrade what the detector
sees and could drop/duplicate frames at the muxer level; the JPG sequence is
exactly what was captured, frame for frame.

Run in the project's main venv (has ultralytics/torch) — not the CarlaAir
conda env used to capture the footage.

Usage:
    python ml/violation_engine/run_sim_validation.py <run_id>
    python ml/violation_engine/run_sim_validation.py 20260808_153000
    python ml/violation_engine/run_sim_validation.py 20260808_153000 --tracker botsort
"""

import argparse
import json
from pathlib import Path

from ultralytics import YOLO

from extract_trajectories import BEST_PT, VEHICLE_CLASS_IDS, extract_trajectories

SIM_FLIGHTS_DIR = Path(__file__).resolve().parents[2] / "simulation" / "data_export" / "sim_flights"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "sim_validation"
TRACKER_CONFIGS = {
    "bytetrack": str(Path(__file__).resolve().parent / "bytetrack_sim.yaml"),
    "botsort": str(Path(__file__).resolve().parent / "botsort_sim.yaml"),
}
SIM_DETECT_CONF = 0.1  # default ~0.25 gate drops the sparse/weak detections that break tracking on CARLA footage


def latest_run_id() -> str:
    runs = sorted((p.name for p in SIM_FLIGHTS_DIR.iterdir() if p.is_dir()))
    if not runs:
        raise SystemExit(f"No sim flights found under {SIM_FLIGHTS_DIR} — run fly_and_capture.py first.")
    return runs[-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", nargs="?", default=None,
                     help="Sim flight run_id under simulation/data_export/sim_flights/ (default: latest)")
    ap.add_argument("--tracker", choices=sorted(TRACKER_CONFIGS), default="botsort",
                     help="Which tracker config to use (default: botsort)")
    args = ap.parse_args()
    sim_tracker = TRACKER_CONFIGS[args.tracker]

    if not BEST_PT.exists():
        raise SystemExit(f"{BEST_PT} not found — run ml/detection/train_full.py first.")

    run_id = args.run_id or latest_run_id()
    run_dir = SIM_FLIGHTS_DIR / run_id
    frames_dir = run_dir / "frames"
    metadata_path = run_dir / "metadata.json"
    if not frames_dir.exists() or not any(frames_dir.glob("*.jpg")):
        raise SystemExit(f"{frames_dir} not found or empty.")

    metadata = json.loads(metadata_path.read_text())
    fps = metadata["capture_fps"]

    out_dir = RESULTS_DIR / run_id / args.tracker
    csv_path = out_dir / "trajectories.csv"
    n_rows = extract_trajectories(frames_dir, fps, csv_path, tracker=sim_tracker, conf=SIM_DETECT_CONF)
    print(f"[tracker] {args.tracker} ({sim_tracker})")
    print(f"[trajectories] wrote {n_rows} rows to {csv_path}")

    model = YOLO(str(BEST_PT))
    results = model.track(
        source=str(frames_dir),
        tracker=sim_tracker,
        classes=VEHICLE_CLASS_IDS,
        conf=SIM_DETECT_CONF,
        save=True,
        project=str(out_dir),
        name="annotated",
        exist_ok=True,
        persist=True,
    )
    track_ids = set()
    for result in results:
        if result.boxes.id is not None:
            track_ids.update(int(i) for i in result.boxes.id)

    print(f"[tracking] unique vehicle track IDs across flight: {len(track_ids)}")
    print(f"[tracking] annotated video saved under: {out_dir / 'annotated'}")


if __name__ == "__main__":
    main()
