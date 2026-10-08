"""Offline tracklet stitching — re-link a vehicle's track that ends to a new track
that starts shortly after, when the tracker (BoT-SORT) issued a new ID for the same
vehicle.

Main cause on recorded drone flights: during a sudden camera move the detector
briefly loses vehicles, and on re-detection BoT-SORT only consults its ReID
appearance match if the new box already overlaps the predicted one by IoU >=
proximity_thresh — after a large jump it usually doesn't, so a new ID is issued
(see docs/main_project_tracker.md, Section 2.3). Recorded flights are processed
offline, so a second pass can re-link those fragments with no IoU requirement:

  1. position — the old track's last position is carried forward through the
     per-frame camera motion (Ultralytics' own sparseOptFlow GMC) plus the
     vehicle's own recent velocity, and must land near the new track's first box
  2. size and class — similar box size, same (or commonly-confused) class
  3. appearance — HSV colour histogram of the box crops must match

Pairs are linked greedily by lowest combined cost; each track gets at most one
predecessor and one successor, so chains (A -> B -> C) are allowed.

Writes trajectories_stitched.csv (same schema; track_id rewritten to the first ID
of each chain) next to the input. Then, unless --no-postprocess, postprocess_tracks.py
writes trajectories_final.csv (class voting, short/low-confidence tracks removed) and
track_summary.csv. The video (unless --no-video) is annotated_final.mp4, or
annotated_stitched.mp4 with --no-postprocess. The raw trajectories.csv is left untouched.

Usage:
    python ml/violation_engine/stitch_tracklets.py 20260920_194932
    python ml/violation_engine/stitch_tracklets.py 20260920_194932 --tracker botsort --max-gap 90
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from ultralytics.trackers.utils import gmc as gmc_module
from ultralytics.trackers.utils.gmc import GMC

import postprocess_tracks

RECORDED_FLIGHTS_DIR = Path(__file__).resolve().parents[2] / "simulation" / "data_export" / "recorded_flights"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "recorded_flight_validation"

COMPATIBLE_CLASSES = [{"car", "van"}]  # the detector flickers between these on the same vehicle
EDGE_FRAMES = 5  # how many frames at a track's end/start are used for velocity and appearance


@dataclass
class Tracklet:
    track_id: int
    frames: list[int] = field(default_factory=list)
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)  # cx, cy, w, h
    classes: list[str] = field(default_factory=list)
    map_pts: list[tuple[float, float]] = field(default_factory=list)  # map_x, map_y per row (D1), if present
    end_hist: np.ndarray | None = None
    start_hist: np.ndarray | None = None

    @property
    def start(self) -> int:
        return self.frames[0]

    @property
    def end(self) -> int:
        return self.frames[-1]

    @property
    def label(self) -> str:
        return Counter(self.classes).most_common(1)[0][0]


def load_tracklets(csv_path: Path) -> tuple[list[dict], dict[int, Tracklet]]:
    rows = list(csv.DictReader(open(csv_path, newline="")))
    tracks: dict[int, Tracklet] = {}
    for r in sorted(rows, key=lambda r: int(r["frame"])):
        t = tracks.setdefault(int(r["track_id"]), Tracklet(int(r["track_id"])))
        t.frames.append(int(r["frame"]))
        t.boxes.append((float(r["cx"]), float(r["cy"]), float(r["w"]), float(r["h"])))
        t.classes.append(r["class"])
        if r.get("map_x") not in (None, ""):
            t.map_pts.append((float(r["map_x"]), float(r["map_y"])))
    return rows, tracks


def crop_hist(img: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
    cx, cy, w, h = box
    x0, y0 = max(0, int(cx - w / 2)), max(0, int(cy - h / 2))
    x1, y1 = min(img.shape[1], int(cx + w / 2)), min(img.shape[0], int(cy + h / 2))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [12, 6, 6], [0, 180, 0, 256, 0, 256])
    return cv2.normalize(hist, hist).flatten()


def iter_frames(source: Path):
    """Frames from a directory of JPGs (recorded CARLA flights) or a video file."""
    if source.is_dir():
        for p in sorted(source.glob("*.jpg")):
            yield cv2.imread(str(p))
        return
    cap = cv2.VideoCapture(str(source))
    try:
        while True:
            ok, img = cap.read()
            if not ok:
                return
            yield img
    finally:
        cap.release()


def camera_motion_and_appearance(source: Path, tracks: dict[int, Tracklet]) -> list[np.ndarray]:
    """One pass over the frames: per-frame camera affine (frame f-1 -> f) and the
    mean colour histogram of each track's first/last EDGE_FRAMES crops."""
    wanted: dict[int, list[tuple[int, str, tuple]]] = defaultdict(list)
    for t in tracks.values():
        for f, b in zip(t.frames[-EDGE_FRAMES:], t.boxes[-EDGE_FRAMES:]):
            wanted[f].append((t.track_id, "end", b))
        for f, b in zip(t.frames[:EDGE_FRAMES], t.boxes[:EDGE_FRAMES]):
            wanted[f].append((t.track_id, "start", b))

    gmc = GMC(method="sparseOptFlow")
    warn = gmc_module.LOGGER.warning
    gmc_module.LOGGER.warning = lambda *a, **k: None  # blank frames spam "not enough matching points"
    hists: dict[tuple[int, str], list[np.ndarray]] = defaultdict(list)
    warps = []
    try:
        for f, img in enumerate(iter_frames(source)):
            try:
                H = gmc.apply(img)
                if H is None or not np.all(np.isfinite(H)):
                    H = np.eye(2, 3)
            except Exception:
                H = np.eye(2, 3)
            warps.append(H.astype(np.float64))
            for tid, which, box in wanted.get(f, []):
                hist = crop_hist(img, box)
                if hist is not None:
                    hists[(tid, which)].append(hist)
    finally:
        gmc_module.LOGGER.warning = warn

    for t in tracks.values():
        for which in ("end", "start"):
            hs = hists.get((t.track_id, which))
            if hs:
                setattr(t, f"{which}_hist", np.mean(hs, axis=0).astype(np.float32))
    return warps


def warp(pt: np.ndarray, H: np.ndarray) -> np.ndarray:
    return H[:, :2] @ pt + H[:, 2]


def own_velocity(t: Tracklet, warps: list[np.ndarray]) -> np.ndarray:
    """Vehicle's own per-frame motion at the end of the track, with camera motion removed."""
    steps = []
    fr, bx = t.frames[-EDGE_FRAMES - 1 :], t.boxes[-EDGE_FRAMES - 1 :]
    for (f0, b0), (f1, b1) in zip(zip(fr, bx), zip(fr[1:], bx[1:])):
        if f1 == f0 + 1:
            steps.append(np.array(b1[:2]) - warp(np.array(b0[:2]), warps[f1]))
    return np.median(steps, axis=0) if steps else np.zeros(2)


def classes_compatible(a: str, b: str) -> bool:
    return a == b or any({a, b} <= group for group in COMPATIBLE_CLASSES)


def find_links(tracks: dict[int, Tracklet], warps: list[np.ndarray], max_gap: int,
               max_app_dist: float, max_cost: float) -> list[dict]:
    velocity = {tid: own_velocity(t, warps) for tid, t in tracks.items()}
    candidates = []
    for a in tracks.values():
        pos = np.array(a.boxes[-1][:2])
        size_a = float(np.sqrt(a.boxes[-1][2] * a.boxes[-1][3]))
        predicted = {}
        for f in range(a.end + 1, min(a.end + max_gap, len(warps) - 1) + 1):
            pos = warp(pos, warps[f]) + velocity[a.track_id]
            predicted[f] = pos.copy()
        for b in tracks.values():
            if b.track_id == a.track_id or b.start not in predicted:
                continue
            gap = b.start - a.end
            size_b = float(np.sqrt(b.boxes[0][2] * b.boxes[0][3]))
            if not 0.6 <= size_b / size_a <= 1.67:
                continue
            if not classes_compatible(a.label, b.label):
                continue
            dist = float(np.linalg.norm(predicted[b.start] - np.array(b.boxes[0][:2])))
            max_dist = min(1.5 * size_a + 5.0 * gap, 400.0)
            if dist > max_dist:
                continue
            if a.end_hist is None or b.start_hist is None:
                continue
            app = float(cv2.compareHist(a.end_hist, b.start_hist, cv2.HISTCMP_BHATTACHARYYA))
            if app > max_app_dist:
                continue
            # each term is in [0, 1]; a wrong merge is worse than a missed link, so borderline pairs are dropped
            cost = dist / max_dist + app / max_app_dist
            if cost > max_cost:
                continue
            candidates.append({"from": a.track_id, "to": b.track_id, "gap": gap, "dist": round(dist, 1),
                               "max_dist": round(max_dist, 1), "app": round(app, 3), "cost": round(cost, 3)})
    return select_links(candidates)


def select_links(candidates: list[dict]) -> list[dict]:
    """Greedy by lowest cost; each track gets at most one predecessor and one successor."""
    links, has_next, has_prev = [], set(), set()
    for c in sorted(candidates, key=lambda c: c["cost"]):
        if c["from"] in has_next or c["to"] in has_prev:
            continue
        links.append(c)
        has_next.add(c["from"])
        has_prev.add(c["to"])
    return links


# ---------------------------------------------------------------- D1: map-based linking

STATIONARY_MAX_MOVE = 0.5  # vehicle lengths moved over the track's first/last second to count as stationary
MOVING_MAX_GAP_S = 3.0


def map_edge(t: Tracklet, H: np.ndarray, fps: float, end: bool) -> dict:
    """Map position, length (map units) and velocity (map units/frame) over the track's first/last second."""
    n = max(2, round(fps))
    fr, bx, mp = (t.frames[-n:], t.boxes[-n:], t.map_pts[-n:]) if end else (t.frames[:n], t.boxes[:n], t.map_pts[:n])
    f, (cx, cy, w, h) = (fr[-1], bx[-1]) if end else (fr[0], bx[0])
    from scene_map import map_length  # scene_map imports this module (iter_frames)
    length = map_length(H, f, cx, cy, w, h)
    pts = np.array(mp)
    span = max(fr[-1] - fr[0], 1)
    velocity = (pts[-1] - pts[0]) / span
    moved = float(np.linalg.norm(pts[-1] - pts[0])) / max(length, 1e-6)
    return {"pos": pts[-1] if end else pts[0], "len": length, "vel": velocity,
            "stationary": moved < STATIONARY_MAX_MOVE and len(pts) >= n // 2}


def find_links_map(tracks: dict[int, Tracklet], H: np.ndarray, fps: float, max_app_dist: float, max_cost: float,
                   moving_max_gap_s: float = MOVING_MAX_GAP_S) -> list[dict]:
    """D1 (Improvement Plan): link tracklets by stabilised map position.
    stationary: a ends and b starts standing still within 1 vehicle length of each other on the map, similar
                size and class -> same (parked / queued) vehicle, whatever the gap (camera panned away and back).
    moving:     a's map velocity carried forward over the gap (<= moving_max_gap_s) lands near b's start.
    Appearance (colour histogram) must still match when both crops exist; a wrong merge is worse than a miss."""
    usable = {tid: t for tid, t in tracks.items() if len(t.map_pts) == len(t.frames) and t.map_pts}
    ends = {tid: map_edge(t, H, fps, end=True) for tid, t in usable.items()}
    starts = {tid: map_edge(t, H, fps, end=False) for tid, t in usable.items()}
    max_gap_moving = round(moving_max_gap_s * fps)
    candidates = []
    for a in usable.values():
        ea = ends[a.track_id]
        for b in usable.values():
            if b.track_id == a.track_id or b.start <= a.end:
                continue
            sb, gap = starts[b.track_id], b.start - a.end
            if not 0.6 <= sb["len"] / max(ea["len"], 1e-6) <= 1.67 or not classes_compatible(a.label, b.label):
                continue
            if ea["stationary"] and sb["stationary"]:
                kind, predicted, max_dist = "stationary", ea["pos"], 1.0 * ea["len"]
            elif gap <= max_gap_moving and not (ea["stationary"] and sb["stationary"]):
                kind, predicted = "moving", ea["pos"] + ea["vel"] * gap
                max_dist = ea["len"] * (1.0 + 0.5 * gap / fps)  # uncertainty grows with the gap
            else:
                continue
            dist = float(np.linalg.norm(predicted - sb["pos"]))
            if dist > max_dist:
                continue
            app = 0.0
            if a.end_hist is not None and b.start_hist is not None:
                app = float(cv2.compareHist(a.end_hist, b.start_hist, cv2.HISTCMP_BHATTACHARYYA))
                if app > max_app_dist:
                    continue
            cost = dist / max_dist + app / max_app_dist
            if cost > max_cost:
                continue
            candidates.append({"from": a.track_id, "to": b.track_id, "kind": kind, "gap": gap,
                               "dist_len": round(dist / ea["len"], 2), "app": round(app, 3), "cost": round(cost, 3)})
    return select_links(candidates)


def chain_ids(tracks: dict[int, Tracklet], links: list[dict]) -> dict[int, int]:
    prev = {l["to"]: l["from"] for l in links}
    root = {}
    for tid in tracks:
        r = tid
        while r in prev:
            r = prev[r]
        root[tid] = r
    return root


def id_color(tid: int) -> tuple[int, int, int]:
    rng = np.random.default_rng(tid)
    return tuple(int(c) for c in rng.integers(60, 255, 3))


def write_video(source: Path, rows: list[dict], out_path: Path, fps: float) -> None:
    """rows carry final track IDs (after stitching / post-processing)."""
    by_frame = defaultdict(list)
    for r in rows:
        by_frame[int(r["frame"])].append(r)
    writer = None
    seen = set()
    for f, img in enumerate(iter_frames(source)):
        font = max(0.4, img.shape[1] / 2560)  # 0.75 at 1920 px wide, 0.5 at 1280
        thick = 1 if font < 0.6 else 2
        for r in by_frame.get(f, []):
            sid = int(r["track_id"])
            seen.add(sid)
            cx, cy, w, h = (float(r[k]) for k in ("cx", "cy", "w", "h"))
            p0, p1 = (int(cx - w / 2), int(cy - h / 2)), (int(cx + w / 2), int(cy + h / 2))
            color = id_color(sid)
            cv2.rectangle(img, p0, p1, color, 2)
            label = f"{sid} {r['class']}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font, thick)
            ty = max(th + 6, p0[1])
            cv2.rectangle(img, (p0[0], ty - th - 6), (p0[0] + tw + 4, ty), color, -1)
            cv2.putText(img, label, (p0[0] + 2, ty - 4), cv2.FONT_HERSHEY_SIMPLEX, font, (0, 0, 0), thick)
        hud = f"t {f / fps:6.1f}s | vehicles in view: {len(by_frame.get(f, []))} | unique vehicles so far: {len(seen)}"
        (hw, hh), _ = cv2.getTextSize(hud, cv2.FONT_HERSHEY_SIMPLEX, font, thick)
        cv2.rectangle(img, (0, 0), (hw + 16, hh + 16), (0, 0, 0), -1)
        cv2.putText(img, hud, (8, hh + 8), cv2.FONT_HERSHEY_SIMPLEX, font, (255, 255, 255), thick)
        if writer is None:
            writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                     (img.shape[1], img.shape[0]))
        writer.write(img)
    if writer is not None:
        writer.release()


def stitch(source: Path, traj_csv: Path, fps: float, max_gap: int = 90, max_app_dist: float = 0.4,
           max_cost: float = 1.0, video: bool = True, postprocess: bool = True, map_linking: bool = True) -> dict:
    """source: a directory of frame JPGs or a video file, matching traj_csv's frame numbering.

    Writes trajectories_stitched.csv (stitching only). With postprocess, also runs
    postprocess_tracks.py (class voting + short/low-confidence track removal) and writes
    trajectories_final.csv + track_summary.csv; the video then shows the final tracks.
    If traj_csv has map_x/map_y and scene_map.npz sits next to it (scene_map.py), links are found in map
    coordinates (D1, find_links_map) instead of by chained per-frame GMC; map_linking=False forces the latter."""
    rows, tracks = load_tracklets(traj_csv)
    warps = camera_motion_and_appearance(source, tracks)
    sm_path = traj_csv.with_name("scene_map.npz")
    if map_linking and sm_path.exists() and all(len(t.map_pts) == len(t.frames) for t in tracks.values()):
        links = find_links_map(tracks, np.load(sm_path)["H"], fps, max_app_dist, max_cost)
    else:
        links = find_links(tracks, warps, max_gap, max_app_dist, max_cost)
    root = chain_ids(tracks, links)

    stitched = [{**r, "track_id": str(root[int(r["track_id"])])} for r in rows]
    out_csv = traj_csv.with_name("trajectories_stitched.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(stitched)
    traj_csv.with_name("stitch_links.json").write_text(json.dumps(links, indent=1))
    stats = {"ids_before": len(tracks), "ids_after": len(set(root.values())), "links": len(links),
             "link_kinds": dict(Counter(l.get("kind", "gmc") for l in links)),
             "out_csv": out_csv}

    video_rows, video_name = stitched, "annotated_stitched.mp4"
    if postprocess:
        kept, summary, pp = postprocess_tracks.postprocess(stitched, fps)
        pp["out_csv"], pp["summary_csv"] = postprocess_tracks.write_outputs(traj_csv, kept, summary, pp["columns"])
        stats["postprocess"] = pp
        video_rows, video_name = kept, "annotated_final.mp4"

    if video:
        write_video(source, video_rows, traj_csv.with_name(video_name), fps)
        stats["video"] = traj_csv.with_name(video_name)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", help="Recorded flight run_id under simulation/data_export/recorded_flights/")
    ap.add_argument("--tracker", default="tracktrack_ours",
                    help="Which tracker's output folder to stitch (default: tracktrack_ours)")
    ap.add_argument("--max-gap", type=int, default=90, help="Max frames between a track ending and its continuation")
    ap.add_argument("--max-app-dist", type=float, default=0.4,
                    help="Max colour-histogram (Bhattacharyya) distance to accept a link, 0 = identical")
    ap.add_argument("--max-cost", type=float, default=1.0,
                    help="Max combined position+appearance cost (0-2) to accept a link; lower = stricter")
    ap.add_argument("--no-video", action="store_true", help="Skip writing the annotated video")
    ap.add_argument("--no-postprocess", action="store_true", help="Skip class voting + track filtering")
    args = ap.parse_args()

    run_dir = RECORDED_FLIGHTS_DIR / args.run_id
    traj_csv = RESULTS_DIR / args.run_id / args.tracker / "trajectories.csv"
    if not traj_csv.exists():
        raise SystemExit(f"{traj_csv} not found — run process_recorded_flight.py first.")
    metadata_path = run_dir / "metadata.json"
    fps = json.loads(metadata_path.read_text()).get("avg_fps", 15.0) if metadata_path.exists() else 15.0

    stats = stitch(run_dir / "frames", traj_csv, fps, args.max_gap, args.max_app_dist, args.max_cost,
                   not args.no_video, not args.no_postprocess)
    print_stats(stats)


def print_stats(stats: dict) -> None:
    print(f"[stitch] {stats['links']} links: unique IDs {stats['ids_before']} -> {stats['ids_after']} "
          f"-> {stats['out_csv']}")
    if "postprocess" in stats:
        print(f"[postprocess] {postprocess_tracks.format_stats(stats['postprocess'])} "
              f"-> {stats['postprocess']['out_csv']}")
    if "video" in stats:
        print(f"[video] {stats['video']}")


if __name__ == "__main__":
    main()
