"""Stage 4 foundation — run detector + ByteTrack on a video and save per-frame,
per-vehicle centroid positions to CSV. All three violation rules (no-parking,
wrong-way, speeding) are built on top of this trajectory data.

Schema: frame, time_s, track_id, class, cx, cy, w, h, conf
(cx, cy, w, h are in pixel coordinates, frame-relative)
"""

import csv
from pathlib import Path

from ultralytics import YOLO

BEST_PT = Path(__file__).resolve().parents[1] / "data" / "results" / "full_train" / "train" / "weights" / "best.pt"
VEHICLE_CLASS_IDS = [3, 4, 5, 8]  # car, van, truck, bus — matches ml/tracking/track_demo.py


def extract_trajectories(
    video_path: Path, fps: float, out_csv: Path, tracker: str = "bytetrack.yaml", conf: float | None = None
) -> int:
    model = YOLO(str(BEST_PT))
    track_kwargs = {} if conf is None else {"conf": conf}
    results = model.track(
        source=str(video_path),
        tracker=tracker,
        classes=VEHICLE_CLASS_IDS,
        stream=True,
        persist=True,
        **track_kwargs,
    )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    n_rows = 0
    with open(out_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "time_s", "track_id", "class", "cx", "cy", "w", "h", "conf"])
        for frame_idx, result in enumerate(results):
            if result.boxes.id is None:
                continue
            names = result.names
            for box, track_id in zip(result.boxes, result.boxes.id):
                cx, cy, w, h = (float(v) for v in box.xywh[0])
                writer.writerow(
                    [
                        frame_idx,
                        round(frame_idx / fps, 3),
                        int(track_id),
                        names[int(box.cls)],
                        round(cx, 1),
                        round(cy, 1),
                        round(w, 1),
                        round(h, 1),
                        round(float(box.conf), 3),
                    ]
                )
                n_rows += 1
    return n_rows


def main() -> None:
    video_path = Path(__file__).resolve().parents[1] / "data" / "results" / "tracking_demo" / "uav0000137_00458_v.mp4"
    out_csv = Path(__file__).resolve().parents[1] / "data" / "results" / "trajectories" / "uav0000137_00458_v.csv"
    fps = 20  # matches ml/tracking/track_demo.py's FPS used to build this video

    if not video_path.exists():
        raise SystemExit(f"{video_path} not found — run ml/tracking/track_demo.py first.")

    n_rows = extract_trajectories(video_path, fps, out_csv)
    print(f"Wrote {n_rows} rows to {out_csv}")


if __name__ == "__main__":
    main()
