"""Stage 3 — track vehicles across a real VisDrone2019-MOT video sequence using our
Stage 1 detector (ml/detection/train_full.py's best.pt) + Ultralytics' built-in ByteTrack.

ByteTrack is a classical (non-learned) tracker, so no training step is needed here — we
just need real video to confirm it correctly follows vehicles frame-to-frame. VisDrone-MOT
sequences are provided as loose JPG frames, so we first assemble the chosen sequence into
an actual video file (tracking needs frame continuity, not a one-off image batch).

Only tracks car/van/truck/bus — pedestrian/people/bicycle/motor tracking isn't relevant to
this project's violation types. `motor` was tried initially but dropped: on a busy
intersection sequence it produced severe ID churn (444+ IDs by frame 100 for ~10 visible
motorcycles) due to low, flickering detection confidence on small/occluded objects, while
car/van tracking on the same sequence stayed rock-solid (same IDs held from frame 0 to 100).
"""

from pathlib import Path

import cv2
from ultralytics import YOLO

BEST_PT = Path(__file__).resolve().parents[1] / "data" / "results" / "full_train" / "train" / "weights" / "best.pt"
MOT_DIR = Path(__file__).resolve().parents[1] / "data" / "datasets" / "VisDrone2019-MOT-val"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "tracking_demo"

SEQUENCE = "uav0000137_00458_v"
FPS = 20
VEHICLE_CLASS_IDS = [3, 4, 5, 8]  # car, van, truck, bus


def build_video(sequence_dir: Path, out_path: Path) -> None:
    frames = sorted(sequence_dir.glob("*.jpg"))
    if not frames:
        raise SystemExit(f"No frames found in {sequence_dir}")

    first = cv2.imread(str(frames[0]))
    h, w = first.shape[:2]
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
    for frame_path in frames:
        writer.write(cv2.imread(str(frame_path)))
    writer.release()
    print(f"Built {len(frames)}-frame video at {out_path}")


def main() -> None:
    if not BEST_PT.exists():
        raise SystemExit(f"{BEST_PT} not found — run train_full.py first.")

    sequence_dir = MOT_DIR / "sequences" / SEQUENCE
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    video_path = RESULTS_DIR / f"{SEQUENCE}.mp4"
    build_video(sequence_dir, video_path)

    model = YOLO(str(BEST_PT))
    results = model.track(
        source=str(video_path),
        tracker="bytetrack.yaml",
        classes=VEHICLE_CLASS_IDS,
        save=True,
        project=str(RESULTS_DIR),
        name="tracked",
        exist_ok=True,
        persist=True,
    )

    track_ids = set()
    for result in results:
        if result.boxes.id is not None:
            track_ids.update(int(i) for i in result.boxes.id)

    print(f"\nUnique vehicle track IDs across sequence: {len(track_ids)}")
    print(f"Annotated tracking video saved under: {RESULTS_DIR / 'tracked'}")


if __name__ == "__main__":
    main()
