"""Copy a checked, auto-labelled CARLA flight into the detector dataset
(Detector_Retraining_Plan.md, Sections 5.8 and 6).

Takes only frames that have a label file in <flight>/autolabel/labels/ (the others were
never labelled and must not be used), keeps frames at least --min-gap seconds apart
(consecutive frames are near-duplicates), and writes:

  ml/data/datasets/carla_det/<split>/<flight_id>/
    images/<frame>.jpg     RGB frame, copied unchanged (1920x1080)
    labels/<frame>.txt     YOLO labels: "<cls> <cx> <cy> <w> <h>", 0 car / 1 bus / 2 truck;
                           an empty file = checked, no vehicles
    meta.csv               per frame: time, map, weather, sun altitude, camera height/pitch/speed, vehicles
    gt.csv                 the flight's full track labels (all labelled frames), for tracking checks
    source.json            where it came from, settings, counts

Run after watching autolabel/check.mp4 (and adding label_excludes.csv if needed):
    python ml/detection/export_carla_flight.py simulation/data_export/recorded_flights/20260928_231831 --split val
"""

import argparse
import csv
import json
import math
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "ml" / "data" / "datasets" / "carla_det"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path)
    ap.add_argument("--split", required=True, choices=["train", "val", "test", "negatives"])
    ap.add_argument("--min-gap", type=float, default=1.0, help="Seconds between kept frames (plan: >= 1 s)")
    ap.add_argument("--max-frames", type=int, default=120, help="Cap per flight (plan: ~120)")
    ap.add_argument("--min-height", type=float, default=10.0,
                    help="Skip frames with the camera lower than this (m, world z): take-off/landing frames are "
                         "the grey road surface, not aerial views")
    ap.add_argument("--only", choices=["all", "vehicles", "empty"], default=None,
                    help="Keep only frames with vehicle labels, or only empty ones (default: 'empty' for "
                         "--split negatives, else 'all'). Lets one flight feed both train and negatives.")
    ap.add_argument("--t-min", type=float, default=0.0, help="Ignore frames before this time (s), e.g. a stuck start")
    ap.add_argument("--early-gap", type=float, default=None,
                    help="Keep frames before --t-min too, but only this many seconds apart (e.g. 5): a thin "
                         "sample of a static or repetitive start. Default: frames before --t-min are skipped")
    ap.add_argument("--force", action="store_true", help="Replace an existing export of this flight")
    args = ap.parse_args()

    flight = args.flight.resolve()
    labels_dir = flight / "autolabel" / "labels"
    if not labels_dir.exists():
        raise SystemExit(f"No {labels_dir} — run carla_autolabel.py first")
    for other in OUT_ROOT.glob(f"*/{flight.name}"):
        if other.parent.name != args.split and {other.parent.name, args.split} != {"train", "negatives"}:
            raise SystemExit(f"{flight.name} is already exported to '{other.parent.name}'. A flight goes into one "
                             f"split only (train + negatives may share a flight); delete {other} first if you really mean to move it.")
    out = OUT_ROOT / args.split / flight.name
    if out.exists():
        if not args.force:
            raise SystemExit(f"{out} exists; use --force to replace it")
        shutil.rmtree(out)
    (out / "images").mkdir(parents=True)
    (out / "labels").mkdir()

    meta = json.loads((flight / "metadata.json").read_text())
    with open(flight / "frame_times.csv", newline="") as f:
        times = {int(r["frame"]): (float(r["time_s"]), int(r["carla_frame"])) for r in csv.DictReader(f)}
    poses = {}
    if (flight / "camera_poses.csv").exists():
        with open(flight / "camera_poses.csv", newline="") as f:
            poses = {int(r["carla_frame"]): (float(r["x"]), float(r["y"]), float(r["z"]), float(r["pitch"]))
                     for r in csv.DictReader(f)}

    only = args.only or ("empty" if args.split == "negatives" else "all")
    labelled = sorted(int(p.stem) for p in labels_dir.glob("*.txt"))
    if args.early_gap is None:
        labelled = [fr for fr in labelled if times[fr][0] >= args.t_min]
    if only != "all":
        has_boxes = {fr: any(line.strip() for line in open(labels_dir / f"{fr:05d}.txt")) for fr in labelled}
        labelled = [fr for fr in labelled if has_boxes[fr] == (only == "vehicles")]
    low = [fr for fr in labelled if times[fr][1] in poses and poses[times[fr][1]][2] < args.min_height]
    kept, last_t = [], -1e9
    for fr in labelled:
        if fr in low:
            continue
        t = times[fr][0]
        early = args.early_gap is not None and t < args.t_min
        if t - last_t >= (args.early_gap if early else args.min_gap):
            kept.append(fr)
            last_t = t
    if len(kept) > args.max_frames:  # thin evenly rather than cutting the end of the flight
        step = len(kept) / args.max_frames
        kept = [kept[int(i * step)] for i in range(args.max_frames)]

    weather = meta.get("weather", {})
    rows, n_boxes, n_empty, prev = [], 0, 0, None
    for fr in kept:
        t, cf = times[fr]
        shutil.copy2(flight / "frames" / f"{fr:05d}.jpg", out / "images" / f"{fr:05d}.jpg")
        shutil.copy2(labels_dir / f"{fr:05d}.txt", out / "labels" / f"{fr:05d}.txt")
        n = sum(1 for line in open(labels_dir / f"{fr:05d}.txt") if line.strip())
        n_boxes += n
        n_empty += n == 0
        p = poses.get(cf)
        speed = None
        if p and prev and t > prev[0]:
            speed = round(math.dist(p[:3], prev[1][:3]) / (t - prev[0]), 1)
        if p:
            prev = (t, p)
        rows.append({"frame": fr, "time_s": t, "map": meta.get("map", "").rsplit("/", 1)[-1],
                     "weather": meta.get("weather_preset") or "", "sun_altitude": weather.get("sun_altitude_angle", ""),
                     "fog_density": weather.get("fog_density", ""), "camera_z_m": round(p[2], 1) if p else "",
                     "camera_pitch": round(p[3], 1) if p else meta.get("camera", {}).get("pitch", ""),
                     "camera_speed_mps": speed if speed is not None else "", "n_vehicles": n})
    with open(out / "meta.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    shutil.copy2(flight / "autolabel" / "gt.csv", out / "gt.csv")

    info = {
        "flight": str(flight.relative_to(REPO)) if flight.is_relative_to(REPO) else str(flight),
        "split": args.split, "only": only, "t_min_s": args.t_min, "early_gap_s": args.early_gap, "min_gap_s": args.min_gap,
        "labelled_frames_in_flight": len(labelled), "skipped_below_min_height": len(low),
        "min_height_m": args.min_height, "exported_frames": len(kept),
        "boxes": n_boxes, "empty_frames": n_empty,
        "label_excludes": (flight / "label_excludes.csv").exists(),
        "classes": {"0": "car", "1": "bus", "2": "truck"},
        "metadata": meta,
    }
    (out / "source.json").write_text(json.dumps(info, indent=2))
    print(f"{flight.name} -> {out}")
    print(f"  {len(kept)} of {len(labelled)} labelled frames (>= {args.min_gap} s apart; {len(low)} skipped as "
          f"below {args.min_height} m), {n_boxes} boxes, {n_empty} frames with no vehicles")


if __name__ == "__main__":
    main()
