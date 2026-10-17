"""Pedestrian detection recall / precision on CARLA walkers, by altitude (Build Plan M3, Measure).

Truth: the instance-segmentation frames of a flight recorded with --labels (record_flight.py): every
instance with CARLA's semantic tag 12 (Pedestrian) is one walker, its box the box of its pixels.
The seg camera sees through tree leaves, so where the flight has depth frames a walker pixel only
counts if nothing is above it (depth >= camera height - VISIBLE_TOP_M); walkers with fewer than
--min-px visible pixels are left out (reported). The RGB frame of the same simulator tick (frame_times
carla_frame, within 1 tick) is run through the people detector (pedestrians.py: full_train,
pedestrian + people, class-agnostic NMS).

A detection matches a walker when their centres are within MATCH_M on the ground (pixels =
MATCH_M / ground sampling distance at that frame's height, so the test is the same at any height);
one-to-one, greedy by distance. Height above the road = camera z (camera_poses.csv) - the flight's
road height (median vehicle z). Results per height bin, input size and confidence threshold.

Usage:
    python ml/violation_engine/eval_pedestrian_detection.py <flight> [<flight> ...] --imgsz 640 1280 --every 2
    python ml/violation_engine/eval_pedestrian_detection.py <flight> --imgsz 1280 --tile 640 --heights 55 80
Writes ml/data/results/pedestrian_detection/<time>/results.json (+ run_config).
"""

import argparse
import csv
import datetime
import json
import math
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from pedestrians import PEOPLE_WEIGHTS, detect

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "pedestrian_detection"
PED_TAG = 12  # CARLA 0.9.15 semantic tag "Pedestrian"
VISIBLE_TOP_M = 2.5  # a walker's head is ~1.8 m up; anything closer to the camera than height - 2.5 m covers it
MATCH_M = 0.75  # centre distance on the ground (a person is ~0.5 m across from above; ~0.15 m position noise)
HFOV = 90.0
BINS = [(0, 50), (50, 60), (60, 75), (75, 200)]
CONFS = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]


def depth_m(path: Path) -> np.ndarray | None:
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    b, g, r = (im[:, :, k].astype(np.float64) for k in range(3))
    return (r + 256 * g + 65536 * b) / (2 ** 24 - 1) * 1000.0


def walkers(seg_path: Path, depth: np.ndarray | None, height: float, min_px: int) -> tuple[list, int]:
    """-> ([(cx, cy, w, h, n_visible)], n_hidden) for the tag-12 instances of a seg frame."""
    seg = cv2.imread(str(seg_path), cv2.IMREAD_UNCHANGED)
    tag = seg[:, :, 2]
    ys, xs = np.nonzero(tag == PED_TAG)
    if not len(xs):
        return [], 0
    iid = seg[ys, xs, 1].astype(int) + 256 * seg[ys, xs, 0].astype(int)
    if depth is not None:
        vis = depth[ys, xs] >= height - VISIBLE_TOP_M
    else:
        vis = np.ones(len(xs), bool)
    out, hidden = [], 0
    for i in np.unique(iid):
        sel = (iid == i) & vis
        if sel.sum() < min_px:
            hidden += int(((iid == i)).sum() >= min_px)
            continue
        x0, x1, y0, y1 = xs[sel].min(), xs[sel].max() + 1, ys[sel].min(), ys[sel].max() + 1
        out.append(((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, int(sel.sum())))
    return out, hidden


def match(gt: list, dets: list, radius_px: float) -> tuple[int, int, int]:
    pairs = sorted((math.hypot(g[0] - d["cx"], g[1] - d["cy"]), i, j) for i, g in enumerate(gt) for j, d in enumerate(dets))
    used_g, used_d = set(), set()
    for dist, i, j in pairs:
        if dist > radius_px:
            break
        if i in used_g or j in used_d:
            continue
        used_g.add(i)
        used_d.add(j)
    tp = len(used_g)
    return tp, len(gt) - tp, len(dets) - tp


def frames_of(flight: Path, every: int) -> list[dict]:
    """Seg frames with their RGB frame, camera height and depth path."""
    with open(flight / "frame_times.csv", newline="") as f:
        ft = {int(r["carla_frame"]): int(r["frame"]) for r in csv.DictReader(f)}
    cf_sorted = np.array(sorted(ft))
    with open(flight / "camera_poses.csv", newline="") as f:
        cam = {int(r["carla_frame"]): float(r["z"]) for r in csv.DictReader(f)}
    with open(flight / "vehicle_poses.csv", newline="") as f:
        zs = [float(r["z"]) for r in csv.DictReader(f)]
    road_z = float(np.median(zs)) if zs else 0.0
    rgb = sorted((flight / "frames").glob("*.jpg"))
    out = []
    for k, seg in enumerate(sorted((flight / "seg").glob("*.png"), key=lambda p: int(p.stem))):
        if k % every:
            continue
        cf = int(seg.stem)
        j = int(np.abs(cf_sorted - cf).argmin())
        if abs(int(cf_sorted[j]) - cf) > 1 or cf not in cam:
            continue
        fr = ft[int(cf_sorted[j])]
        if fr >= len(rgb):
            continue
        d = flight / "depth" / f"{cf}.png"
        out.append({"seg": seg, "rgb": rgb[fr], "height": cam[cf] - road_z, "depth": d if d.exists() else None})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flights", type=Path, nargs="+")
    ap.add_argument("--imgsz", type=int, nargs="+", default=[640, 1280])
    ap.add_argument("--every", type=int, default=2, help="Use every n-th seg frame")
    ap.add_argument("--min-px", type=int, default=6, help="Walkers with fewer visible seg pixels are left out")
    ap.add_argument("--max-frames", type=int, default=None, help="Per flight (smoke test)")
    ap.add_argument("--tile", type=int, default=None, help="Sliced inference: tile size in px (pedestrians.detect)")
    ap.add_argument("--heights", type=float, nargs=2, default=None, metavar=("MIN", "MAX"),
                    help="Only frames with the camera this high above the road (m)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    from ultralytics import YOLO
    model = YOLO(str(PEOPLE_WEIGHTS))

    out = args.out or RESULTS_DIR / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    # counts[imgsz][conf][bin] = [tp, fn, fp]; frames with no walker still count their false positives
    counts = {s: {c: defaultdict(lambda: [0, 0, 0]) for c in CONFS} for s in args.imgsz}
    gt_info = defaultdict(lambda: {"walkers": 0, "hidden": 0, "frames": 0, "px": []})
    per_flight = {}
    for flight in args.flights:
        fr = frames_of(flight, args.every)
        if args.heights:
            fr = [f for f in fr if args.heights[0] <= f["height"] < args.heights[1]]
        fr = fr[:args.max_frames]
        per_flight[flight.name] = len(fr)
        print(f"[{flight.name}] {len(fr)} frames, height {min(f['height'] for f in fr):.1f}-{max(f['height'] for f in fr):.1f} m"
              if fr else f"[{flight.name}] no usable frames")
        for f in fr:
            b = next(f"{lo}-{hi}" for lo, hi in BINS if lo <= f["height"] < hi)
            dep = depth_m(f["depth"]) if f["depth"] is not None else None
            gt, hidden = walkers(f["seg"], dep, f["height"], args.min_px)
            gi = gt_info[b]
            gi["walkers"] += len(gt)
            gi["hidden"] += hidden
            gi["frames"] += 1
            gi["px"] += [max(g[2], g[3]) for g in gt]
            gsd = 2 * f["height"] * math.tan(math.radians(HFOV / 2)) / 1920
            img = cv2.imread(str(f["rgb"]))
            for s in args.imgsz:
                dets = detect(model, img, imgsz=s, conf=min(CONFS), tile=args.tile)
                for c in CONFS:
                    tp, fn, fp = match(gt, [d for d in dets if d["conf"] >= c], MATCH_M / gsd)
                    acc = counts[s][c][b]
                    acc[0] += tp
                    acc[1] += fn
                    acc[2] += fp
    res = {"imgsz": {}, "truth": {}}
    for b, gi in gt_info.items():
        px = np.array(gi["px"]) if gi["px"] else np.zeros(1)
        res["truth"][b] = {"frames": gi["frames"], "walkers": gi["walkers"], "hidden_left_out": gi["hidden"],
                           "box_px_median": float(np.median(px)), "box_px_p10_p90": [float(np.percentile(px, 10)), float(np.percentile(px, 90))]}
    for s in args.imgsz:
        res["imgsz"][s] = {}
        for c in CONFS:
            row = {}
            for b in sorted(counts[s][c]):
                tp, fn, fp = counts[s][c][b]
                r = tp / (tp + fn) if tp + fn else None
                p = tp / (tp + fp) if tp + fp else None
                f1 = 2 * p * r / (p + r) if p and r else 0.0
                row[b] = {"tp": tp, "fn": fn, "fp": fp, "recall": None if r is None else round(r, 3),
                          "precision": None if p is None else round(p, 3), "f1": round(f1, 3)}
            res["imgsz"][s][c] = row
    cfg = {"date": datetime.datetime.now().isoformat(timespec="seconds"), "weights": str(PEOPLE_WEIGHTS),
           "flights": per_flight, "every": args.every, "min_px": args.min_px, "match_m": MATCH_M,
           "visible_top_m": VISIBLE_TOP_M, "tile": args.tile, "heights": args.heights}
    (out / "results.json").write_text(json.dumps({"config": cfg, **res}, indent=1))
    print(json.dumps(res["truth"], indent=1))
    for s in args.imgsz:
        for c in CONFS:
            print(f"imgsz {s} conf {c:.2f}: " + "  ".join(
                f"{b}: R {v['recall']} P {v['precision']}" for b, v in res["imgsz"][s][c].items()))
    print(f"-> {out / 'results.json'}")


if __name__ == "__main__":
    main()
