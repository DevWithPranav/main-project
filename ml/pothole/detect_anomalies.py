r"""Road-surface anomaly events from a recorded flight or a video (Build Plan M9).

Runs the anomaly segmenter (YOLO26l-seg, pothole / crack / waterlogging / debris) as a separate
pass over the frames, then per detection (ml/pothole/anomalies.py):

  1. tiled (SAHI-style) inference: overlapping --tile windows + a whole-frame pass, masks merged
     across tile borders. From 67 m a 0.5 m pothole is ~7 px wide in a 1920 px frame; a 640 tile
     at imgsz 640 keeps full resolution where the whole frame at 640 shrinks it 3x.
  2. edge cases: shadow test (darker, same colour + relative texture as the road around it ->
     tag possible_shadow, confidence x0.5, or dropped with --drop-shadows); pothole inside a
     waterlogging mask -> tag waterlogged, confidence x0.7.
  3. ground position: the mask outline through the camera pose onto the road (CARLA flight:
     ground_coords.FlightCamera, at road level, on the lane map's road heights where it has
     them), or a fixed --gsd for a video without pose (static nadir camera assumed; x = u*gsd,
     y = -v*gsd in image metres). Off-road detections are dropped when a lane map is known.
     Masks whose ground area is implausible for one defect (anomalies.SIZE_M2) are dropped.
  4. severity from area (m^2) x class weight (+ depth proxy / crack pattern), PRD 9.2.
  5. de-duplication by type within 3 m over all frames (PRD FAQ), >= --min-frames sightings.
  6. one schema-valid anomaly event per defect (checked with ml/violation_engine/schemas.py),
     with a snapshot (crop + outline) as evidence.

Output folder (never overwritten; refuses an existing one):
    anomalies.json      {"events": [...], "meta": {...}} - POST /api/sessions/import loads it
    detections.csv      every per-frame detection (ground x, y, area, score, tags, cluster)
    snapshots/<id>.jpg  evidence crops
    summary.json        counts, timings, settings
Default folder for a flight: <RESULTS_DIR>/<flight>/tracktrack_ours/anomalies_<YYYYmmdd_HHMMSS>/
(the backend import picks the newest anomalies*/ folder).

Usage:
    python ml/pothole/detect_anomalies.py --flight simulation/data_export/recorded_flights/20261002_001635
    python ml/pothole/detect_anomalies.py --flight <dir> --weights ml/pothole/runs/E2_modified/weights/best.pt --stride 5
    python ml/pothole/detect_anomalies.py --video clip.mp4 --gsd 0.02 --out ml/data/results/anomalies/clip
    python ml/pothole/detect_anomalies.py --images <folder of jpg> --gsd 0.005 --min-frames 1 --out <dir>
"""

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENGINE = REPO / "ml" / "violation_engine"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ENGINE))
import anomalies as A  # noqa: E402

RESULTS_DIR = REPO / "ml" / "data" / "results" / "recorded_flight_validation"
TRACKER_RUN = "tracktrack_ours"
DEFAULT_WEIGHTS = HERE / "runs" / "baseline" / "weights" / "best.pt"  # 2025 PothRGBD baseline (pothole only)
ON_ROAD_MARGIN_M = 1.0  # a detection this far past a lane edge still counts as on the road (shoulder)
SNAP_MIN_PX = 160


# --- frame sources -------------------------------------------------------------------------------

def flight_frames(flight: Path, stride: int, max_frames: int | None):
    """(frame index, time_s, BGR image) of a recorded flight; time from frame_times.csv (sim time)."""
    times = {}
    with open(flight / "frame_times.csv", newline="") as f:
        for r in csv.DictReader(f):
            times[int(r["frame"])] = float(r["time_s"])
    files = sorted((flight / "frames").glob("*.jpg"))
    n = 0
    for p in files[::stride]:
        i = int(p.stem)
        if i not in times:
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        yield i, times[i], img
        n += 1
        if max_frames and n >= max_frames:
            return


def video_frames(video: Path, stride: int, max_frames: int | None):
    """(frame index, PTS seconds, image); time from the container PTS, never frame / fps."""
    cap = cv2.VideoCapture(str(video))
    i = n = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % stride == 0:
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            ok, img = cap.retrieve()
            if ok:
                yield i, max(t, 0.0), img
                n += 1
                if max_frames and n >= max_frames:
                    break
        i += 1
    cap.release()


def image_frames(folder: Path, stride: int, max_frames: int | None):
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})[::stride]
    for k, p in enumerate(files[:max_frames] if max_frames else files):
        img = cv2.imread(str(p))
        if img is not None:
            yield k, float(k), img


# --- projection ------------------------------------------------------------------------------------

class FixedGSD:
    """Static nadir camera: image metres (x right, y up). No pose, so positions are per-video."""

    def __init__(self, gsd: float):
        self.gsd = gsd

    def __call__(self, frame: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        return np.stack([np.asarray(u, float) * self.gsd, -np.asarray(v, float) * self.gsd], axis=1)


class RoadLevel:
    """CARLA flight: rays from the logged camera pose onto the road itself (not the vehicle-box
    height ground_coords uses for cars): on the lane map's road heights where they exist (the highest
    road the ray meets, e.g. a flyover), else the flat plane at the flight's median road height."""

    def __init__(self, flight: Path, scene: dict | None):
        from ground_coords import BOX_CENTRE_Z, FlightCamera, RoadSurface
        self.cam = FlightCamera(flight)
        self.plane_z = self.cam.ground_z - BOX_CENTRE_Z
        self.surface = RoadSurface.from_scene(scene) if scene else None

    def __call__(self, frame: int, u: np.ndarray, v: np.ndarray) -> np.ndarray | None:
        p = self.cam.pose(frame)
        if p is None:
            return None
        pos, R = p
        cam = self.cam
        d = R @ np.stack([np.ones_like(u, dtype=float), (np.asarray(u, float) - cam.W / 2) / cam.f,
                          -(np.asarray(v, float) - cam.H / 2) / cam.f])
        t = (self.plane_z - pos[2]) / d[2]
        out = (pos[:2, None] + t * d[:2]).T
        s = self.surface
        if s is None:
            return out
        levels = s.levels
        tt = (levels[:, None] - pos[2]) / d[2][None, :]
        X, Y = pos[0] + tt * d[0][None, :], pos[1] + tt * d[1][None, :]
        top, low = s.heights(X, Y)
        tol = s.LEVEL_STEP_M / 2 + 0.05
        ok = (np.abs(top - levels[:, None]) <= tol) | (np.abs(low - levels[:, None]) <= tol)
        hit = ok.any(axis=0)
        k = len(levels) - 1 - np.argmax(ok[::-1], axis=0)
        cols = np.arange(len(k))
        out[hit] = np.stack([X[k, cols], Y[k, cols]], axis=1)[hit]
        return out


class Lanes:
    """Nearest lane of a ground point (lane_id when inside the lane, on_road with a margin)."""

    def __init__(self, scene: dict):
        from scipy.spatial import cKDTree
        pts, ids, half = [], [], []
        for l in scene.get("lanes", []):
            c = np.asarray(l["centreline"], float)
            if len(c) < 2:
                continue
            seg = np.hypot(*np.diff(c, axis=0).T)
            cum = np.r_[0.0, np.cumsum(seg)]
            s = np.arange(0.0, cum[-1] + 1e-9, 0.5)
            pts.append(np.stack([np.interp(s, cum, c[:, 0]), np.interp(s, cum, c[:, 1])], axis=1))
            ids += [l["id"]] * len(s)
            half += [float(l.get("width_m", 3.5)) / 2] * len(s)
        self.tree = cKDTree(np.concatenate(pts))
        self.ids, self.half = ids, np.asarray(half)

    def lookup(self, x: float, y: float) -> tuple[str | None, bool]:
        d, i = self.tree.query([x, y])
        return (self.ids[i] if d <= self.half[i] else None), bool(d <= self.half[i] + ON_ROAD_MARGIN_M)


# --- inference -------------------------------------------------------------------------------------

def category_of(names: dict) -> dict[int, str]:
    """Model class id -> anomaly category (names outside the 4 categories are ignored)."""
    return {int(k): v for k, v in names.items() if v in A.CATEGORIES}


def predict(model, img: np.ndarray, a, cats: dict[int, str]) -> list[dict]:
    """Detections of one frame: {"cls", "conf", "mask" (full-frame bool), "tile"}."""
    h, w = img.shape[:2]
    windows = A.tiles(w, h, a.tile, a.overlap) if a.tile else []
    jobs = ([(0, 0, w, h)] if (a.full or not windows) else []) + windows
    dets = []
    for k in range(0, len(jobs), a.batch):
        chunk = jobs[k:k + a.batch]
        crops = [img[y0:y1, x0:x1] for x0, y0, x1, y1 in chunk]
        res = model.predict(crops, imgsz=a.imgsz, conf=a.conf, device=a.device, retina_masks=True, verbose=False)
        for (x0, y0, x1, y1), r in zip(chunk, res):
            if r.masks is None or not len(r.boxes):
                continue
            md = r.masks.data.cpu().numpy() > 0.5
            for m, c, f in zip(md, r.boxes.cls.cpu().numpy().astype(int), r.boxes.conf.cpu().numpy()):
                if int(c) not in cats or not m.any():
                    continue
                full = np.zeros((h, w), bool)
                full[y0:y1, x0:x1] = m[: y1 - y0, : x1 - x0]
                dets.append({"cls": cats[int(c)], "conf": float(f), "mask": full, "tile": (x0, y0)})
    return A.merge_tile_masks(dets) if len(jobs) > 1 else dets


def snapshot(img: np.ndarray, mask: np.ndarray, label: str) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    cx, cy = (xs.min() + xs.max()) // 2, (ys.min() + ys.max()) // 2
    half = max(SNAP_MIN_PX // 2, int(1.5 * max(xs.max() - xs.min(), ys.max() - ys.min())))
    h, w = img.shape[:2]
    x0, y0, x1, y1 = max(0, cx - half), max(0, cy - half), min(w, cx + half), min(h, cy + half)
    out = img.copy()
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, cs, -1, (0, 0, 255), 1)
    crop = out[y0:y1, x0:x1]
    if crop.shape[0] < SNAP_MIN_PX:
        f = SNAP_MIN_PX / crop.shape[0]
        crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_NEAREST)
    cv2.putText(crop, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return crop


# --- the run ---------------------------------------------------------------------------------------

def sightings_of_frame(frame: int, t_s: float, img: np.ndarray, dets: list[dict], project, lanes, a, stats) -> list[tuple]:
    """Detections of one frame -> [(Sighting, mask)] after edge cases, projection and road filter."""
    water = [d["mask"] for d in dets if d["cls"] == "waterlogging"]
    out = []
    for d in dets:
        cls, conf, mask = d["cls"], d["conf"], d["mask"]
        tags = []
        feat = A.shadow_features(img, mask)
        extra = {}
        if feat:
            extra["shadow_features"] = {k: round(v, 3) for k, v in feat.items()}
        if cls in ("pothole", "debris") and A.looks_like_shadow(feat):
            stats["shadow_flagged"] += 1
            if a.drop_shadows:
                continue
            tags.append("possible_shadow")
            conf *= A.SHADOW_CONF_FACTOR
        if cls == "pothole" and A.waterlogged(mask, water):
            stats["waterlogged"] += 1
            tags.append("waterlogged")
            conf *= A.WATERLOG_CONF_FACTOR
        outline = A.mask_outline(mask)
        if len(outline) < 3:
            stats["no_outline"] += 1
            continue
        g = project(frame, outline[:, 0], outline[:, 1])
        if g is None or not np.isfinite(g).all():
            stats["no_pose"] += 1
            continue
        xy = A.polygon_centroid(g)
        lane_id = None
        if lanes is not None:
            lane_id, on_road = lanes.lookup(float(xy[0]), float(xy[1]))
            if not on_road:
                stats["off_road"] += 1
                continue
        area = A.mask_area_m2(mask, g, outline)
        if not A.plausible_size(cls, area):
            stats["implausible_size"] += 1
            continue
        darkness = (1 - feat["brightness"]) if (feat and cls == "pothole") else None
        pattern = A.crack_pattern(mask) if cls == "crack" else None
        score, _ = A.severity(cls, area, darkness, pattern)
        if pattern:
            tags.append(pattern)
        extra.update({"lane_id": lane_id, "mask_px": int(mask.sum())})
        out.append((A.Sighting(frame, t_s, cls, float(conf), float(xy[0]), float(xy[1]), area, score, tuple(tags), g, extra), mask))
    return out


def run(a) -> dict:
    from ultralytics import YOLO
    import modify_model  # noqa: F401  (registers DSConv/SimAM so modified checkpoints load)
    from schemas import event_errors

    scene = None
    if a.flight:
        flight = Path(a.flight)
        meta = json.loads((flight / "metadata.json").read_text())
        town = (meta.get("map") or "").rsplit("/", 1)[-1]
        scene_path = Path(a.scene) if a.scene else ENGINE / "configs" / "scenes" / f"{town}.json"
        if scene_path.is_file() and not a.no_scene:
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
        project = RoadLevel(flight, scene)
        frames = flight_frames(flight, a.stride, a.max_frames)
        name = flight.name
        out = Path(a.out) if a.out else RESULTS_DIR / name / TRACKER_RUN / f"anomalies_{datetime.now():%Y%m%d_%H%M%S}"
    else:
        if not a.gsd:
            raise SystemExit("--video / --images need --gsd (metres per pixel) since they have no camera pose")
        if not a.out:
            raise SystemExit("--video / --images need --out")
        project = FixedGSD(a.gsd)
        src = Path(a.video or a.images)
        frames = video_frames(src, a.stride, a.max_frames) if a.video else image_frames(src, a.stride, a.max_frames)
        name = src.stem
        out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} exists and is not empty; pick a new --out (results are never overwritten)")
    (out / "snapshots").mkdir(parents=True, exist_ok=True)
    lanes = Lanes(scene) if scene else None

    model = YOLO(str(a.weights))
    cats = category_of(model.names)
    if not cats:
        raise SystemExit(f"model classes {model.names} contain none of {A.CATEGORIES}")
    dedup = A.Deduper(a.radius, a.min_frames)
    best_crop: dict[int, tuple[float, np.ndarray]] = {}
    stats = {k: 0 for k in ("frames", "raw_detections", "shadow_flagged", "waterlogged", "no_outline", "no_pose", "off_road", "implausible_size", "sightings")}
    t_inf = []
    t0 = time.perf_counter()
    rows = []
    for frame, t_s, img in frames:
        ti = time.perf_counter()
        dets = predict(model, img, a, cats)
        t_inf.append(time.perf_counter() - ti)
        stats["frames"] += 1
        stats["raw_detections"] += len(dets)
        for s, mask in sightings_of_frame(frame, t_s, img, dets, project, lanes, a, stats):
            c = dedup.add(s)
            cid = id(c)
            stats["sightings"] += 1
            if cid not in best_crop or s.conf > best_crop[cid][0]:
                best_crop[cid] = (s.conf, snapshot(img, mask, f"{s.cls} {s.conf:.2f} {s.area_m2:.2f} m2"))
            rows.append([frame, round(t_s, 3), s.cls, round(s.conf, 4), round(s.x, 2), round(s.y, 2), round(s.area_m2, 3),
                         s.score, "|".join(s.tags), dedup.clusters.index(c)])
    elapsed = time.perf_counter() - t0

    events = []
    for k, c in enumerate(dedup.events()):
        eid = f"{name}-anom-{k:04d}"
        snap = f"snapshots/{eid}.jpg"
        cv2.imwrite(str(out / snap), best_crop[id(c)][1])
        lane = c.best().extra.get("lane_id")
        events.append(A.to_event(c, eid, lane_id=lane, snapshot=snap))
    errors = event_errors(events)
    with open(out / "detections.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame", "t_s", "type", "conf", "x", "y", "area_m2", "severity", "tags", "cluster"])
        wr.writerows(rows)
    settings = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items()}
    summary = {"source": name, "weights": str(a.weights), "classes": cats, "settings": settings, "stats": stats,
               "clusters": len(dedup.clusters), "events": len(events), "by_type": {t: sum(e["type"] == t for e in events) for t in A.CATEGORIES},
               "schema_errors": errors[:20], "elapsed_s": round(elapsed, 2),
               "infer_ms_per_frame": round(1000 * float(np.mean(t_inf)), 1) if t_inf else None,
               "lane_map": bool(scene), "projection": type(project).__name__}
    (out / "anomalies.json").write_text(json.dumps({"events": events, "meta": summary}, indent=1), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if errors:
        raise SystemExit(f"{len(errors)} schema errors, first: {errors[0]}")
    print(json.dumps({k: summary[k] for k in ("stats", "clusters", "events", "by_type", "elapsed_s", "infer_ms_per_frame")}, indent=1))
    print(f"-> {out}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--flight", help="recorded flight folder (frames/, frame_times.csv, camera poses)")
    src.add_argument("--video")
    src.add_argument("--images", help="folder of frames")
    ap.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    ap.add_argument("--gsd", type=float, default=None, help="metres per pixel (video / images only)")
    ap.add_argument("--scene", default=None, help="lane map (default: configs/scenes/<town>.json of the flight)")
    ap.add_argument("--no-scene", action="store_true", help="ignore the lane map (no road filter, flat ground)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--stride", type=int, default=10, help="every n-th frame (defects are static)")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--conf", type=float, default=0.25, help="deployed confidence (evaluate.py uses the same)")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--tile", type=int, default=640, help="tile size in px; 0 = whole frame only")
    ap.add_argument("--overlap", type=float, default=0.2)
    ap.add_argument("--no-full", dest="full", action="store_false", help="skip the whole-frame pass when tiling")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="0")
    ap.add_argument("--radius", type=float, default=A.DEDUP_RADIUS_M, help="dedup radius, m (PRD: 3 m)")
    ap.add_argument("--min-frames", type=int, default=A.MIN_FRAMES)
    ap.add_argument("--drop-shadows", action="store_true", help="drop shadow-like detections instead of tagging")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
