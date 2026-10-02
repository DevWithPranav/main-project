"""Automatic labels for a CARLA flight recorded with `record_flight.py --labels`
(Detector_Retraining_Plan.md, Section 5.8).

Inputs, in the flight folder:
  frames/<idx>.jpg        RGB frames
  seg/<carla_frame>.png   instance segmentation: R = semantic tag, G + 256*B = instance id
  frame_times.csv         frame, time_s, carla_frame  (pairs frames/ with seg/)
  actors.json             vehicle actor id -> type_id, base_type, 3D box (relative to the actor)
  camera_poses.csv        seg camera world pose per carla_frame           } projection mode;
  vehicle_poses.csv       every vehicle actor's world pose per carla_frame } flights recorded
  map_vehicles.json       parked vehicles baked into the map, world 3D boxes } before 2026-09-29 lack these
  depth/<carla_frame>.png depth camera, same mount and interval as seg (flights from 2026-09-29 on)

The seg camera sees *through* alpha-masked surfaces (tree leaves, gaps in the rail bridge deck),
so a car under a tree shows up in seg as fully visible. With depth/, every seg vehicle pixel whose
depth is clearly closer than the vehicle's projected 3D box (something in front of it) is removed
before the box, pixel count and visibility are computed. Without depth/ that check is off.

The seg camera renders less often than the RGB camera, so only RGB frames with a
seg image of the same simulator tick get labels. Every other frame is left out of
the outputs (not written as "no vehicles").

Rules (same as the hand-labelled GT, Ground_Truth_Creation_Guide.md Section 6):
  - box = tight rectangle around the vehicle's visible pixels (so no shadow, and
    only the visible part when occluded)
  - classes car / bus / truck; vans and pickups -> car; motorcycles, bicycles and
    pedestrians are not labelled
  - dropped: boxes under 8 px on a side; vehicles touching the frame edge with less
    than 50% visible; vehicles less than 25% visible elsewhere (e.g. seen through the
    gaps of a bridge deck)
  - track id = CARLA's instance id, stable for the whole flight

"Visible" = the vehicle's seg pixels / the area its full 3D box covers in the image
(projection mode), divided by SILHOUETTE (a car covers about that share of its box's
outline when nothing hides it; measured per flight, reported in summary.json).
Without pose files it falls back to the most pixels the same vehicle shows, clear
of the frame edge, within +-2 s, which fails for a vehicle that is always occluded.

Outputs (in <flight>/autolabel/):
  gt.csv         frame, time_s, track_id, class, cx, cy, w, h, conf  (our GT schema)
  labels/*.txt   YOLO: "<cls> <cx> <cy> <w> <h>" normalised, 0 car / 1 bus / 2 truck
  check.mp4      every RGB frame at real speed, for eyeballing only (never used for training): labelled
                 frames with their boxes, frames in between with thin boxes interpolated per vehicle
  dropped.csv    every box left out by the visibility rules, with the reason and visible share
  summary.json   counts, drop reasons, and the seg-vs-projection alignment check

Usage (main venv):
    python ml/detection/carla_autolabel.py simulation/data_export/recorded_flights/<run_id>
"""

import argparse
import colorsys
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

TAG_CLASS = {14: "car", 15: "truck", 16: "bus"}  # CARLA 0.9.15 semantic tags
BASE_TYPE_CLASS = {"car": "car", "van": "car", "truck": "truck", "bus": "bus"}
# blueprints whose class is decided by how they look from above, whatever CARLA calls them
# (team decisions): pickups are cars; the ambulance (CARLA: van) is a box body, same as a
# small delivery truck, which VisDrone/UAVDT label truck
BLUEPRINT_CLASS_OVERRIDE = {"vehicle.tesla.cybertruck": "car", "vehicle.ford.ambulance": "truck"}
YOLO_ID = {"car": 0, "bus": 1, "truck": 2}
MIN_SIDE = 8
MIN_PIXELS = 30
EDGE_MIN_VISIBLE = 0.5
OCCLUDED_MIN_VISIBLE = 0.25
REF_WINDOW_S = 2.0
DEFAULT_SILHOUETTE = 0.8
DEPTH_MARGIN_M, DEPTH_MARGIN_REL = 0.75, 0.02  # "in front of the vehicle" = closer than its box by more than this
DEPTH_MAX_TICK_GAP = 10  # accept a depth frame this many sim ticks from the seg frame — the two
# cameras are spawned a moment apart so their sensor_tick phases differ; seen as a steady ~3-tick
# offset (2026-09-29), not jitter, so this needs to comfortably clear that, not just round to 0
CORNERS = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=float)


def instance_class(iid: int, tag: int, actors: dict) -> str | None:
    a = actors.get(str(iid))
    if a is not None:
        if a["type_id"] in BLUEPRINT_CLASS_OVERRIDE:
            return BLUEPRINT_CLASS_OVERRIDE[a["type_id"]]
        cls = BASE_TYPE_CLASS.get(a["base_type"].lower())
        if cls:
            return cls
        if a["base_type"].lower() in ("motorcycle", "bicycle"):
            return None
    return TAG_CLASS.get(tag)  # map-baked vehicles have no actor: fall back to the pixel tag


def extract_pixels(seg: np.ndarray) -> list[tuple]:
    """-> [(instance_id, tag, xs, ys)] for the vehicle pixels of each seg instance."""
    tag = seg[:, :, 2]
    mask = np.isin(tag, list(TAG_CLASS))
    if not mask.any():
        return []
    ys, xs = np.nonzero(mask)
    ids = seg[ys, xs, 1].astype(np.int64) + 256 * seg[ys, xs, 0].astype(np.int64)
    tags = tag[ys, xs]
    out = []
    order = np.argsort(ids, kind="stable")
    ids, xs, ys, tags = ids[order], xs[order], ys[order], tags[order]
    starts = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])
    ends = np.r_[starts[1:], len(ids)]
    for s, e in zip(starts, ends):
        t = Counter(tags[s:e].tolist()).most_common(1)[0][0]
        out.append((int(ids[s]), int(t), xs[s:e], ys[s:e]))
    return out


def summarise(iid: int, tag: int, xs: np.ndarray, ys: np.ndarray) -> tuple:
    """-> (instance_id, tag, x0, y0, x1, y1, n_pixels, mean_x, mean_y)."""
    return (iid, tag, int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, int(len(xs)),
            float(xs.mean()), float(ys.mean()))


def decode_depth(img: np.ndarray) -> np.ndarray:
    """CARLA depth PNG (BGR) -> metres from the camera plane."""
    b, g, r = (img[:, :, i].astype(np.float64) for i in range(3))
    return (r + g * 256 + b * 65536) / (256 ** 3 - 1) * 1000.0


def depth_visible(depth: np.ndarray, xs: np.ndarray, ys: np.ndarray, near_m: float | None) -> np.ndarray:
    """Mask of the seg pixels not hidden behind something closer (leaves, bridge deck...).

    near_m = the vehicle's nearest distance from its projected 3D box; without one, the
    vehicle is taken to be at the far end of its own pixels' depths (anything in front of
    it is an occluder, since the seg camera only sees *through* things, never past the car)."""
    d = depth[ys, xs]
    if near_m is None:
        near_m = float(np.percentile(d, 90)) - 1.0
    return d >= near_m - (DEPTH_MARGIN_M + DEPTH_MARGIN_REL * near_m)


def ue_matrix(loc, rot) -> np.ndarray:
    """Unreal/CARLA transform -> 4x4 matrix (same formula as carla.Transform.get_matrix)."""
    pitch, yaw, roll = (math.radians(a) for a in rot)
    cp, sp, cy, sy, cr, sr = math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw), math.cos(roll), math.sin(roll)
    return np.array([
        [cp * cy, cy * sp * sr - sy * cr, -cy * sp * cr - sy * sr, loc[0]],
        [cp * sy, sy * sp * sr + cy * cr, -sy * sp * cr + cy * sr, loc[1]],
        [sp, -cp * sr, cp * cr, loc[2]],
        [0, 0, 0, 1]])


def box_corners_local(bbox: dict) -> np.ndarray:
    """8 corners (4x8 homogeneous) of a CARLA BoundingBox, in its parent's frame."""
    pts = CORNERS * np.array(bbox["ext"])
    return ue_matrix(bbox["loc"], bbox["rot"]) @ np.c_[pts, np.ones(8)].T


class Projector:
    def __init__(self, W: int, H: int, fov: float):
        self.W, self.H = W, H
        self.f = W / (2 * math.tan(math.radians(fov) / 2))

    def hull(self, world_to_cam: np.ndarray, corners_world: np.ndarray):
        """-> (hull polygon Nx2 float32, full area px, nearest corner distance m) or None if any
        corner is behind the camera."""
        c = world_to_cam @ corners_world  # UE camera frame: x forward, y right, z up
        if (c[0] < 0.5).any():
            return None
        u = self.W / 2 + self.f * c[1] / c[0]
        v = self.H / 2 - self.f * c[2] / c[0]
        h = cv2.convexHull(np.c_[u, v].astype(np.float32))
        return h, float(cv2.contourArea(h)), float(c[0].min())


def load_projection(d: Path):
    need = ["camera_poses.csv", "vehicle_poses.csv", "map_vehicles.json"]
    if not all((d / n).exists() for n in need):
        return None
    cams = {}
    with open(d / "camera_poses.csv", newline="") as f:
        for r in csv.DictReader(f):
            cams[int(r["carla_frame"])] = np.linalg.inv(ue_matrix(
                (float(r["x"]), float(r["y"]), float(r["z"])), (float(r["pitch"]), float(r["yaw"]), float(r["roll"]))))
    veh = defaultdict(dict)
    with open(d / "vehicle_poses.csv", newline="") as f:
        for r in csv.DictReader(f):
            veh[int(r["carla_frame"])][r["id"]] = ue_matrix(
                (float(r["x"]), float(r["y"]), float(r["z"])), (float(r["pitch"]), float(r["yaw"]), float(r["roll"])))
    baked = json.loads((d / "map_vehicles.json").read_text())
    baked_corners = [box_corners_local(b["bbox"]) for b in baked]  # map boxes are already in world space
    baked_centres = np.array([b["bbox"]["loc"] for b in baked]) if baked else np.zeros((0, 3))
    return cams, veh, baked, baked_corners, baked_centres


def color(tid: int) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb((tid * 0.61803) % 1.0, 0.85, 1.0)
    return int(b * 255), int(g * 255), int(r * 255)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--video-labelled-only", action="store_true",
                    help="check.mp4 with only the labelled frames (default: every frame, boxes interpolated between)")
    args = ap.parse_args()
    d = args.flight
    out = d / "autolabel"
    (out / "labels").mkdir(parents=True, exist_ok=True)

    actors = json.loads((d / "actors.json").read_text())
    meta = json.loads((d / "metadata.json").read_text()) if (d / "metadata.json").exists() else {}
    seg_frames = {int(p.stem) for p in (d / "seg").glob("*.png")}
    with open(d / "frame_times.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if int(r["carla_frame"]) in seg_frames]
    if not rows:
        raise SystemExit("No RGB frame has a seg image of the same tick — was it recorded with --labels?")
    H, W = cv2.imread(str(d / "frames" / f"{int(rows[0]['frame']):05d}.jpg")).shape[:2]
    proj_data = load_projection(d)
    projector = Projector(W, H, meta.get("camera", {}).get("fov", 90.0))
    depth_frames = np.array(sorted(int(p.stem) for p in (d / "depth").glob("*.png"))) if (d / "depth").exists() \
        else np.zeros(0, dtype=int)
    print("mode:", "projected 3D boxes" if proj_data else "fallback (no pose files: +-2 s size estimate)",
          "| depth occlusion check:", "on" if len(depth_frames) else "OFF (no depth/ — trees and bridge gaps not caught)")

    # pass 1: raw instances per paired frame, plus the full projected area where we can get it
    raw = []  # (frame, time, carla_frame, [inst + (full_area, proj_bbox or None)])
    baked_match = defaultdict(Counter)  # seg instance id -> votes for a map_vehicles.json index
    depth_stats = Counter()
    for i, r in enumerate(rows):
        cf = int(r["carla_frame"])
        seg = cv2.imread(str(d / "seg" / f"{cf}.png"), cv2.IMREAD_UNCHANGED)
        depth = None
        if len(depth_frames):
            k = int(np.abs(depth_frames - cf).argmin())
            if abs(int(depth_frames[k]) - cf) <= DEPTH_MAX_TICK_GAP:
                depth = decode_depth(cv2.imread(str(d / "depth" / f"{depth_frames[k]}.png"), cv2.IMREAD_UNCHANGED))
                depth_stats["frames with depth"] += 1
            else:
                depth_stats["frames without a depth frame within 2 ticks"] += 1
        insts = []
        if proj_data:
            cams, veh, baked, baked_corners, baked_centres = proj_data
            w2c = cams.get(cf)
            baked_hulls = None
        for iid, tag, xs, ys in extract_pixels(seg):
            inst = summarise(iid, tag, xs, ys)
            full = None
            if proj_data and w2c is not None:
                a = actors.get(str(iid))
                if a is not None and str(iid) in veh.get(cf, {}):
                    res = projector.hull(w2c, veh[cf][str(iid)] @ box_corners_local(a["bbox"]))
                    full = res
                elif a is None and len(baked):
                    if baked_hulls is None:  # project the map vehicles near the camera, once per frame
                        cam_pos = np.linalg.inv(w2c)[:3, 3]
                        near = np.flatnonzero(np.linalg.norm(baked_centres - cam_pos, axis=1) < 400)
                        baked_hulls = [(k, projector.hull(w2c, baked_corners[k])) for k in near]
                        baked_hulls = [(k, h) for k, h in baked_hulls if h is not None]
                    hits = [(h[1], k, h) for k, h in baked_hulls
                            if cv2.pointPolygonTest(h[0], (inst[7], inst[8]), False) >= 0]
                    if hits:
                        _, k, h = min(hits, key=lambda x: x[0])  # innermost box containing the pixels
                        baked_match[iid][k] += 1
                        full = h
            if depth is not None:
                vis = depth_visible(depth, xs, ys, full[2] if full is not None else None)
                depth_stats["pixels checked"] += len(xs)
                depth_stats["pixels hidden (something in front)"] += int((~vis).sum())
                if not vis.any():
                    depth_stats["vehicles fully hidden"] += 1
                    continue
                if not vis.all():
                    inst = summarise(iid, tag, xs[vis], ys[vis])  # box around what is really visible
            insts.append(inst + (full,))
        raw.append((int(r["frame"]), float(r["time_s"]), cf, insts))
        if i % 100 == 0:
            print(f"\r  reading seg {i}/{len(rows)}", end="", flush=True)
    print()

    # silhouette share: how much of its projected box outline an unhidden vehicle fills (median, clear of edges)
    shares = [inst[6] / inst[9][1] for _, _, _, insts in raw for inst in insts
              if inst[9] is not None and inst[9][1] > 0 and str(inst[0]) in actors
              and inst[2] > 0 and inst[3] > 0 and inst[4] < W and inst[5] < H]
    silhouette = float(np.clip(np.median(shares), 0.5, 1.0)) if len(shares) >= 20 else DEFAULT_SILHOUETTE

    # fallback reference size per instance: most pixels shown clear of the frame edge, within +-REF_WINDOW_S
    clear = defaultdict(list)
    for _, t, _, insts in raw:
        for inst in insts:
            if inst[2] > 0 and inst[3] > 0 and inst[4] < W and inst[5] < H:
                clear[inst[0]].append((t, inst[6]))

    def ref_pixels(iid: int, t: float) -> int | None:
        near = [n for tt, n in clear.get(iid, ()) if abs(tt - t) <= REF_WINDOW_S]
        return max(near) if near else None

    # hand-checked exclusions for flights recorded before the depth check (e.g. cars under trees):
    # <flight>/label_excludes.csv with track_id, frame_from, frame_to, reason
    excludes = []
    if (d / "label_excludes.csv").exists():
        with open(d / "label_excludes.csv", newline="") as f:
            excludes = [(int(r["track_id"]), int(r["frame_from"]), int(r["frame_to"]), r["reason"])
                        for r in csv.DictReader(f)]
        print(f"label_excludes.csv: {len(excludes)} rules")

    def excluded(iid: int, fi: int) -> str | None:
        for tid, a, b, why in excludes:
            if tid == iid and a <= fi <= b:
                return why
        return None

    dropped = Counter()
    dropped_rows = []  # (frame, iid, reason, x0, y0, x1, y1, visible) — drawn grey in check.mp4 for QA
    visibility_source = Counter()
    offsets = defaultdict(list)  # alignment check: seg box centre vs projected box centre, fully visible vehicles
    kept_rows = []
    classes_by_id = {}
    for fi, t, cf, insts in raw:
        lines = []
        for iid, tag, x0, y0, x1, y1, n, mx, my, full in insts:
            cls = instance_class(iid, tag, actors)
            if cls is None:
                dropped["not a car/bus/truck"] += 1
                continue
            w, h = x1 - x0, y1 - y0
            if w < MIN_SIDE or h < MIN_SIDE or n < MIN_PIXELS:
                dropped["too small"] += 1
                continue
            why = excluded(iid, fi)
            if why:
                dropped[f"excluded by hand: {why}"] += 1
                dropped_rows.append((fi, iid, f"excluded: {why}", x0, y0, x1, y1, float("nan")))
                continue
            at_edge = x0 == 0 or y0 == 0 or x1 == W or y1 == H
            if full is not None and full[1] > 0:
                frac = n / (full[1] * silhouette)
                visibility_source["projected 3D box"] += 1
                if not at_edge and frac > 0.8 and str(iid) in actors:
                    bx, by, bw, bh = cv2.boundingRect(full[0].astype(np.int32))
                    offsets[iid].append(math.hypot((x0 + x1) / 2 - (bx + bw / 2), (y0 + y1) / 2 - (by + bh / 2)))
            else:
                ref = ref_pixels(iid, t)
                frac = n / ref if ref else None
                visibility_source["fallback +-2 s" if ref else "none (kept)"] += 1
            if frac is not None:
                reason = ("edge < 50% visible" if at_edge and frac < EDGE_MIN_VISIBLE else
                          "occluded < 25% visible" if not at_edge and frac < OCCLUDED_MIN_VISIBLE else None)
                if reason:
                    dropped[reason] += 1
                    dropped_rows.append((fi, iid, reason, x0, y0, x1, y1, frac))
                    continue
            classes_by_id.setdefault(iid, Counter())[cls] += 1
            kept_rows.append((fi, t, iid, cls, (x0 + x1) / 2, (y0 + y1) / 2, w, h, at_edge, str(iid) in actors))
            lines.append(f"{YOLO_ID[cls]} {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} {w / W:.6f} {h / H:.6f}")
        (out / "labels" / f"{fi:05d}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))

    with open(out / "gt.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame", "time_s", "track_id", "class", "cx", "cy", "w", "h", "conf"])
        for fi, t, iid, cls, cx, cy, w, h, _, _ in kept_rows:
            wr.writerow([fi, t, iid, cls, round(cx, 1), round(cy, 1), w, h, 1])

    with open(out / "dropped.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame", "track_id", "reason", "x0", "y0", "x1", "y1", "visible"])
        for fi, iid, reason, x0, y0, x1, y1, frac in dropped_rows:
            wr.writerow([fi, iid, reason, x0, y0, x1, y1, round(frac, 3)])

    per_class = Counter()
    for iid, c in classes_by_id.items():
        per_class[c.most_common(1)[0][0]] += 1
    all_off = [o for v in offsets.values() for o in v]
    summary = {
        "flight": str(d),
        "mode": "projected 3D boxes" if proj_data else "fallback",
        "rgb_frames": sum(1 for _ in open(d / "frame_times.csv")) - 1,
        "labelled_frames": len(raw),
        "boxes": len(kept_rows),
        "track_ids": len(classes_by_id),
        "tracks_per_class": dict(per_class),
        "tracks_without_actor (map-baked)": sum(1 for i in classes_by_id if str(i) not in actors),
        "map_baked_matched_to_3d_box": len(baked_match),
        "silhouette_share": round(silhouette, 3),
        "depth_occlusion_check": dict(depth_stats) or "off (no depth/ folder)",
        "visibility_from": dict(visibility_source),
        "dropped_boxes": dict(dropped),
        "alignment_px (seg box vs projected box centre, fully visible spawned vehicles)": {
            "n": len(all_off),
            "median": round(float(np.median(all_off)), 1) if all_off else None,
            "p95": round(float(np.percentile(all_off, 95)), 1) if all_off else None,
            "worst_tracks": sorted(((k, round(float(np.median(v)), 1)) for k, v in offsets.items()),
                                   key=lambda x: -x[1])[:5],
        },
        "note": "only frames with a seg image of the same tick are labelled; the others are absent, not empty",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

    if args.no_video:
        return
    by_frame = defaultdict(list)
    for r in kept_rows:
        by_frame[r[0]].append(r)
    dropped_by_frame = defaultdict(list)
    for r in dropped_rows:
        dropped_by_frame[r[0]].append(r)
    map_names = {iid: f"P{k + 1}" for k, iid in enumerate(sorted(i for i in classes_by_id if str(i) not in actors))}

    def draw_box(img, iid, cls, x0, y0, w, h, has_actor, between):
        c = color(iid)
        cv2.rectangle(img, (x0, y0), (x0 + w, y0 + h), c, 1 if between else (3 if has_actor else 2))
        label = f"{iid} {cls}" if has_actor else f"{map_names[iid]} {cls} (parked, map)"
        if between:
            cv2.putText(img, label, (x0, max(14, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
            return
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        ly = max(y0, th + 6)
        cv2.rectangle(img, (x0, ly - th - 6), (x0 + tw + 6, ly), c, -1)
        cv2.putText(img, label, (x0 + 3, ly - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)

    # every RGB frame, not only the labelled ones: between two labelled frames each vehicle's box is
    # moved linearly from one to the other (thin, "between labels") so the video plays smoothly.
    # Those in-between boxes are for viewing only; the training labels are the labelled frames.
    with open(d / "frame_times.csv", newline="") as f:
        all_frames = [(int(r["frame"]), float(r["time_s"])) for r in csv.DictReader(f)]
    if args.video_labelled_only:
        all_frames = [(fi, t) for fi, t, _, _ in raw]
    labelled = [(fi, t) for fi, t, _, _ in raw]
    lab_idx = 0
    span = all_frames[-1][1] - all_frames[0][1]
    fps = max(1.0, round(len(all_frames) / span, 1)) if span > 0 else 10.0  # real-time playback
    vw = cv2.VideoWriter(str(out / "check.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    for n_done, (fi, t) in enumerate(all_frames):
        img = cv2.imread(str(d / "frames" / f"{fi:05d}.jpg"))
        while lab_idx + 1 < len(labelled) and labelled[lab_idx + 1][0] <= fi:
            lab_idx += 1
        is_labelled = labelled[lab_idx][0] == fi
        if is_labelled:
            for _, _, reason, x0, y0, x1, y1, frac in dropped_by_frame.get(fi, []):  # grey = not labelled, and why
                cv2.rectangle(img, (x0, y0), (x1, y1), (150, 150, 150), 1)
                txt = reason if reason.startswith("excluded") else \
                    f"dropped: {'edge' if reason.startswith('edge') else 'hidden'} {frac:.0%} visible"
                cv2.putText(img, txt, (x0, min(H - 6, y1 + 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1,
                            cv2.LINE_AA)
            boxes = [(r[2], r[3], int(r[4] - r[6] / 2), int(r[5] - r[7] / 2), r[6], r[7], r[9])
                     for r in by_frame.get(fi, [])]
        elif labelled[lab_idx][0] < fi and lab_idx + 1 < len(labelled):
            (f0, t0), (f1, t1) = labelled[lab_idx], labelled[lab_idx + 1]
            a = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            nxt = {r[2]: r for r in by_frame.get(f1, [])}
            boxes = []
            for r in by_frame.get(f0, []):
                s = nxt.get(r[2])
                if s is None:
                    continue
                cx, cy, w, h = (r[k] + a * (s[k] - r[k]) for k in (4, 5, 6, 7))
                boxes.append((r[2], r[3], int(cx - w / 2), int(cy - h / 2), int(w), int(h), r[9]))
        else:
            boxes = []
        for iid, cls, x0, y0, w, h, has_actor in boxes:
            draw_box(img, iid, cls, x0, y0, w, h, has_actor, between=not is_labelled)
        cv2.rectangle(img, (0, 0), (W, 44), (0, 0, 0), -1)
        kind = "LABELLED" if is_labelled else "between labels (thin boxes, view only)"
        cv2.putText(img, f"CARLA auto-labels  frame {fi}  t={t:.1f}s  {kind}  vehicles {len(boxes)}"
                         f"  (P# = parked in map; grey = dropped)", (12, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
                    (255, 255, 255) if is_labelled else (170, 170, 170), 2, cv2.LINE_AA)
        vw.write(img)
        if n_done % 200 == 0:
            print(f"\r  rendering check video {n_done}/{len(all_frames)}", end="", flush=True)
    vw.release()
    print(f"\ncheck video: {out / 'check.mp4'} ({len(all_frames)} frames at {fps} fps, {len(labelled)} labelled)")


if __name__ == "__main__":
    main()
