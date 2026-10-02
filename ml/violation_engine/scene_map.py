"""Stabilised scene-map coordinates (Improvement Plan B2, W3 / Phase 2).

Per-frame GMC only relates each frame to the previous one, so errors accumulate and
there is no notion of "the same place" once the camera pans away and back. This
registers every frame to a *keyframe* with Stabilo (SIFT, vehicle boxes masked out,
RANSAC homography) and chains the keyframes, giving one homography per frame from
image pixels to a fixed "map" (the first frame's pixel coordinates):

    frame f --H_kf<-f (Stabilo)--> current keyframe --K (chained)--> map

A new keyframe starts when the inlier count to the current one falls below
KF_MIN_INLIERS (the view has moved too far). If a frame can't be registered at all
(blank / blurred / featureless, e.g. sand or open water), the previous frame's
homography is reused and that frame becomes the new keyframe — i.e. the camera is
assumed not to have moved; any real motion there becomes drift.

Phase 1 decision (docs/main_project_tracker.md, Section 2.3): SIFT + keyframe chaining
kept 17 parked roundabout vehicles within 2-9 px over the largest 30 s pan.

Outputs (next to the trajectory CSV):
    scene_map.npz         H (N,3,3) frame->map, inliers (N,), keyframe (N,), failed (N,)
    map_x, map_y columns  added to the trajectory CSV (schema v2; readers must tolerate them missing)

Stabilo 1.4.3 is installed with --no-deps (its opencv-python<5 pin would downgrade the
project's OpenCV 5.0; SIFT works on 5.0; kornia is only needed for its DL detectors).

Usage:
    python ml/violation_engine/scene_map.py <source frames dir or video> <trajectories.csv>
    python ml/violation_engine/scene_map.py <source> <trajectories.csv> --drift ml/data/eval/gt_1080p/gt.csv
"""

import argparse
import csv
import logging
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from stitch_tracklets import iter_frames

KF_MIN_INLIERS = 150  # Phase 1: new keyframe below this many inliers
MIN_INLIERS = 30  # below this the registration is treated as failed
TRANSFORMATION = "projective"  # or "affine" (similarity: rotation + scale + shift; steadier when chained)
DOWNSAMPLE = 0.5  # SIFT on half-size frames; full-size 1080p took ~0.3 s/frame


def load_boxes(csv_path: Path) -> dict[int, np.ndarray]:
    """frame -> (n, 4) top-left xywh boxes, used as the vehicle mask."""
    boxes = defaultdict(list)
    for r in csv.DictReader(open(csv_path, newline="")):
        cx, cy, w, h = (float(r[k]) for k in ("cx", "cy", "w", "h"))
        boxes[int(r["frame"])].append([cx - w / 2, cy - h / 2, w, h])
    return {f: np.array(b, np.float32) for f, b in boxes.items()}


def quiet_logger() -> logging.Logger:
    """Stabilo warns on every frame without boxes or with few matches; failures are counted here instead."""
    log = logging.getLogger("scene_map.stabilo")
    log.setLevel(logging.ERROR)
    log.propagate = False
    return log


def build_scene_map(source: Path, boxes: dict[int, np.ndarray], detector: str = "sift",
                    transformation: str = TRANSFORMATION, downsample: float = DOWNSAMPLE,
                    kf_min_inliers: int = KF_MIN_INLIERS, min_inliers: int = MIN_INLIERS,
                    anchor: int | None = None) -> dict[str, np.ndarray]:
    """anchor: frame whose pixel coordinates define the map (default: the first frame with a box), so map
    units are image pixels at the flying altitude — not at e.g. a take-off frame close to the ground."""
    from stabilo import Stabilizer

    extra = {"ransac_method": cv2.RANSAC} if transformation == "affine" else {}  # default MAGSAC++ is projective-only
    st = Stabilizer(detector_name=detector, transformation_type=transformation, downsample_ratio=downsample,
                    logger=quiet_logger(), **extra)
    empty = np.zeros((0, 4), np.float32)
    Hs, inliers, keyframe, failed = [], [], [], []
    K = H_prev = np.eye(3)
    for f, img in enumerate(iter_frames(source)):
        b = boxes.get(f, empty)
        if f == 0:
            st.set_ref_frame(img, b)
            H, n, kf, bad = np.eye(3), 0, True, False
        else:
            st.stabilize(img, b)
            M, n = st.get_cur_trans_matrix(), st.get_cur_inliers_count() or 0
            if M is None or n < min_inliers or not np.all(np.isfinite(M)):
                H, kf, bad = H_prev, True, True
            else:
                H = K @ (np.vstack([M, [0, 0, 1]]) if M.shape == (2, 3) else M)
                H = H / H[2, 2]
                kf, bad = n < kf_min_inliers, False
            if kf:
                st.set_ref_frame(img, b)
                K = H
        Hs.append(H)
        inliers.append(n)
        keyframe.append(kf)
        failed.append(bad)
        H_prev = H
        if (f + 1) % 500 == 0:
            print(f"[scene_map] {f + 1} frames, {sum(keyframe)} keyframes, {sum(failed)} failed", flush=True)
    Hs = np.array(Hs)
    if anchor is None:
        anchor = min(boxes) if boxes else 0
    Hs = np.linalg.inv(Hs[anchor]) @ Hs
    Hs /= Hs[:, 2:3, 2:3]
    return {"H": Hs, "inliers": np.array(inliers), "keyframe": np.array(keyframe), "failed": np.array(failed),
            "anchor": np.array(anchor)}


def to_map(H: np.ndarray, frame: int, cx: float, cy: float) -> tuple[float, float]:
    p = H[frame] @ [cx, cy, 1.0]
    return p[0] / p[2], p[1] / p[2]


def add_map_columns(csv_path: Path, H: np.ndarray) -> int:
    """Adds (or overwrites) map_x, map_y in a trajectory CSV, in place. Returns rows written."""
    rows = list(csv.DictReader(open(csv_path, newline="")))
    if not rows:
        return 0
    for r in rows:
        mx, my = to_map(H, int(r["frame"]), float(r["cx"]), float(r["cy"]))
        r["map_x"], r["map_y"] = round(mx, 1), round(my, 1)
    columns = [k for k in rows[0] if k not in ("map_x", "map_y")] + ["map_x", "map_y"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def run(source: Path, traj_csv: Path, detector: str = "sift", transformation: str = TRANSFORMATION,
        downsample: float = DOWNSAMPLE) -> dict:
    """Builds the scene map from traj_csv's boxes, saves scene_map.npz and adds map columns to traj_csv."""
    sm = build_scene_map(source, load_boxes(traj_csv), detector, transformation, downsample)
    np.savez_compressed(traj_csv.with_name("scene_map.npz"), **sm)
    add_map_columns(traj_csv, sm["H"])
    stats = {"frames": len(sm["H"]), "keyframes": int(sm["keyframe"].sum()), "failed": int(sm["failed"].sum()),
             "median_inliers": int(np.median(sm["inliers"][~sm["failed"]])) if (~sm["failed"]).any() else 0}
    print(f"[scene_map] {stats['frames']} frames, {stats['keyframes']} keyframes, {stats['failed']} failed "
          f"registrations, median inliers {stats['median_inliers']} -> {traj_csv.with_name('scene_map.npz')}")
    return stats


def map_length(H: np.ndarray, frame: int, cx: float, cy: float, w: float, h: float) -> float:
    """The box's longer side measured in map units (the map scale changes with altitude/zoom)."""
    dx = np.subtract(to_map(H, frame, cx + w / 2, cy), to_map(H, frame, cx - w / 2, cy))
    dy = np.subtract(to_map(H, frame, cx, cy + h / 2), to_map(H, frame, cx, cy - h / 2))
    return float(max(np.hypot(*dx), np.hypot(*dy)))


def drift_report(H: np.ndarray, gt_csv: Path, fps: float, frame_wh: tuple[int, int], edge_margin: float = 5,
                 min_frames: int = 30) -> list[dict]:
    """Per GT vehicle, in vehicle lengths (map units): spread = 95th-percentile distance from its median
    map position; speed = median frame-to-frame map displacement per second; len_change = 95th/5th
    percentile of its map length (1.0 = no scale drift). Parked vehicles should have spread and speed near
    0; moving ones a plausible speed (a car at 30 km/h covers ~2 lengths/s). Boxes within edge_margin px
    of the frame border are skipped: a vehicle cut off by the edge has a shrunken, shifted box."""
    W, Hh = frame_wh
    per_id = defaultdict(list)
    for r in csv.DictReader(open(gt_csv, newline="")):
        f = int(r["frame"])
        cx, cy, w, h = (float(r[k]) for k in ("cx", "cy", "w", "h"))
        inside = (cx - w / 2 > edge_margin and cy - h / 2 > edge_margin and cx + w / 2 < W - edge_margin
                  and cy + h / 2 < Hh - edge_margin)
        if f < len(H) and inside:
            per_id[int(r["track_id"])].append((f, *to_map(H, f, cx, cy), map_length(H, f, cx, cy, w, h)))
    out = []
    for tid, pts in sorted(per_id.items()):
        if len(pts) < min_frames:
            continue
        pts.sort()
        fr, xy, lens = np.array([p[0] for p in pts]), np.array([p[1:3] for p in pts]), np.array([p[3] for p in pts])
        length = float(np.median(lens))
        spread = float(np.percentile(np.linalg.norm(xy - np.median(xy, 0), axis=1), 95))
        consec = np.diff(fr) == 1
        steps = np.linalg.norm(np.diff(xy, axis=0), axis=1)[consec]
        speed = float(np.median(steps)) * fps if len(steps) else float("nan")
        out.append({"track_id": tid, "frames": len(pts), "len_map": round(length, 1),
                    "spread_len": round(spread / length, 2), "speed_len_s": round(speed / length, 2),
                    "len_change": round(float(np.percentile(lens, 95) / np.percentile(lens, 5)), 2)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path, help="Frames directory or video the trajectories were computed on")
    ap.add_argument("traj_csv", type=Path, help="trajectories.csv (its boxes are masked during registration)")
    ap.add_argument("--detector", default="sift", help="Stabilo feature detector (default: sift)")
    ap.add_argument("--transformation", default=TRANSFORMATION, choices=["projective", "affine"])
    ap.add_argument("--downsample", type=float, default=DOWNSAMPLE, help="Frame scale for feature detection")
    ap.add_argument("--drift", type=Path, default=None, help="GT CSV: print each GT vehicle's map-position spread")
    ap.add_argument("--fps", type=float, default=30.0, help="For --drift speeds")
    ap.add_argument("--reuse", action="store_true", help="With --drift: use the existing scene_map.npz")
    args = ap.parse_args()

    if not args.reuse:
        run(args.source, args.traj_csv, args.detector, args.transformation, args.downsample)
    if args.drift:
        H = np.load(args.traj_csv.with_name("scene_map.npz"))["H"]
        first = next(iter_frames(args.source))
        rows = drift_report(H, args.drift, args.fps, (first.shape[1], first.shape[0]))
        print("track_id  frames  len(map)  spread/len  speed len/s  len 95/5 pct")
        for r in sorted(rows, key=lambda r: r["spread_len"]):
            print(f"{r['track_id']:8d}  {r['frames']:6d}  {r['len_map']:8.1f}  {r['spread_len']:10.2f}  "
                  f"{r['speed_len_s']:11.2f}  {r['len_change']:12.2f}")


if __name__ == "__main__":
    main()
