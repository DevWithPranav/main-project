"""Static-vehicle cross-check (Violation Engine, Layer 5; Phase B step B6).

docs/Violation_Engine_Architecture.md, R19 / Section 5.5 (`static_confirmed`). A second,
tracker-independent signal for the stop-type rules (no-parking, zebra crossing, highway stop):
look at the pixels of the event's ground spot itself. The AI City Challenge stalled-vehicle
winners used the same idea: on stabilised video a car that stops becomes a fixed patch that
differs from the empty road.

How, per spot (x, y in metres) and time window (the event's start..end frames):
  - every SAMPLE_S, cut a PATCH_M square of ground around the spot out of the frame, rectified
    to a top-down grid (camera pose for CARLA, scene map + calibration for real footage), grey
  - background = per-pixel median of the patches seen OUTSIDE the window (within BG_SPAN_S of
    it): the empty road, if the spot was free before the car arrived or after it left
  - d(t) = mean |patch(t) - background| over a car-sized disk in the middle (each patch's own
    mean removed first, so lighting changes cancel)
  - occupied(t) = d(t) > D_OCC
verdict:
  confirmed     occupied in >= OCC_CONFIRM of the window's samples
  contradicted  occupied in <= OCC_CONTRADICT: the spot looked like empty road
  unknown       spot not in view, or no empty-road reference (never seen without the car)

Effect on events (apply mode): value["static"] gets the numbers, tag static_confirmed /
static_contradicted; confirmed adds STATIC_BONUS to the confidence, contradicted sends a
flagged event to needs_review. Re-running starts again from the confidence before the check.

--check (CARLA): calibrates D_OCC against the truth. Positives: true stationary episodes of
vehicle actors (vehicle_poses.csv), arrived and left within the flight; negatives: driving-lane
spots with no actor within NEG_CLEAR_M during the window. Reports confirmed / contradicted /
unknown rates for a sweep of D_OCC.

Usage:
    python ml/violation_engine/static_vehicles.py <flight dir> --check --scene ml/violation_engine/configs/scenes/Town05.json
    python ml/violation_engine/static_vehicles.py <flight dir> [--violations <run_violations.py output>]
    python ml/violation_engine/static_vehicles.py --site <site.json> --trajectories <clip>/trajectories_final.csv
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from events import MIN_CONFIDENCE, rewrite_events
from ground_coords import FlightCamera
from run_violations import RESULTS_DIR

STOP_TYPES = {"no_parking", "zebra_crossing", "highway_stop"}
PATCH_M, RES_M = 7.0, 0.15  # patch side and ground resolution: 47 x 47 px, a car is ~30 px long
CORE_R_M = 2.5  # disk compared with the background: a car-sized area, little of the neighbours
SAMPLE_S = 0.5
BG_SPAN_S = 30.0  # background samples come from this far around the window at most
BG_MARGIN_S = 2.0  # ... but not from right next to it (the car arriving / leaving)
MIN_BG_SIDE = 4  # background samples needed on a side (2 s of empty road before or after)
BG_CLEAR_M = 3.0  # a background view with a tracked vehicle this close to the spot is not empty road
D_OCC = 12.0  # grey levels; calibrated with --check
OCC_CONFIRM, OCC_CONTRADICT = 0.7, 0.3
STATIC_BONUS = 0.1
# --check
POS_MIN_S, POS_MAX_MOVE_M = 10.0, 1.0
NEG_CLEAR_M, NEG_WINDOW_S, N_NEG = 7.0, 15.0, 400
SWEEP = (6, 8, 10, 12, 15, 20, 25)


class FrameReader:
    """Random access to the frames of a CARLA flight (JPG folder) or a video (sequential seeking)."""

    def __init__(self, source: Path):
        self.files = sorted(source.glob("*.jpg")) if source.is_dir() else None
        self.cap = None if self.files is not None else cv2.VideoCapture(str(source))
        self.pos = 0

    def __len__(self):
        return len(self.files) if self.files is not None else int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))

    def gray(self, frame: int) -> np.ndarray | None:
        """Frames must be asked for in increasing order for a video."""
        if self.files is not None:
            img = cv2.imread(str(self.files[frame]), cv2.IMREAD_GRAYSCALE)
            return img
        while self.pos < frame:
            self.cap.grab()
            self.pos += 1
        ok, img = self.cap.read()
        self.pos += 1
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if ok else None


def grid(x: float, y: float, z: float) -> tuple[np.ndarray, int]:
    n = int(round(PATCH_M / RES_M))
    off = (np.arange(n) - (n - 1) / 2) * RES_M
    gx, gy = np.meshgrid(x + off, y + off)
    return np.c_[gx.ravel(), gy.ravel(), np.full(n * n, z)], n


def sample_patches(cam, reader: FrameReader, probes: list[dict], fps: float) -> None:
    """probes: dicts with x, y, f0, f1 (frames). Adds probe["frames"], probe["patches"] (spot in view only)."""
    step = max(1, int(round(SAMPLE_S * fps)))
    span = int(BG_SPAN_S * fps)
    # CARLA flights before 2026-10-03 log the camera pose only every ~7-8 ticks; interpolated poses
    # are off by up to metres (ground_coords.py), which shifts the road markings inside the patch
    # and reads as a change. Sample the exact-pose frames only, about every SAMPLE_S.
    exact = getattr(cam, "pose_exact", None)
    usable = [f for f in range(len(reader)) if exact is None or exact(f)]
    grid_frames, last = [], -step
    for f in usable:
        if f - last >= step:
            grid_frames.append(f)
            last = f
    grid_frames = np.array(grid_frames)
    want = defaultdict(list)
    for i, p in enumerate(probes):
        p["frames"], p["patches"] = [], []
        p["_grid"], p["_n"] = grid(p["x"], p["y"], cam.ground_z)
        for f in grid_frames[(grid_frames >= p["f0"] - span) & (grid_frames <= p["f1"] + span)]:
            want[int(f)].append(i)
    registered = getattr(cam, "registered", lambda f: True)
    for f in sorted(want):
        if not registered(f):
            continue
        img = None
        for i in want[f]:
            p = probes[i]
            uv = cam.to_pixels(f, p["_grid"])
            if uv is None or np.isnan(uv).any() or uv[:, 0].min() < 0 or uv[:, 1].min() < 0 \
                    or uv[:, 0].max() > cam.W - 1 or uv[:, 1].max() > cam.H - 1:
                continue
            if img is None:
                img = reader.gray(f)
                if img is None:
                    break
            n = p["_n"]
            mx, my = (uv[:, k].reshape(n, n).astype(np.float32) for k in (0, 1))
            p["frames"].append(f)
            p["patches"].append(cv2.remap(img, mx, my, cv2.INTER_LINEAR))


def tracked_positions(kin_csv: Path) -> dict[int, np.ndarray]:
    """frame -> (N, 2) positions of every tracked vehicle (kinematics.csv of the pipeline)."""
    by_frame = defaultdict(list)
    with open(kin_csv, newline="") as f:
        for r in csv.DictReader(f):
            by_frame[int(r["frame"])].append((float(r["x"]), float(r["y"])))
    return {k: np.array(v) for k, v in by_frame.items()}


def mark_blocked(probes: list[dict], tracked: dict[int, np.ndarray]) -> None:
    """Background views where the tracker had a vehicle on the spot are not empty road: at crossings
    and stop lines other cars queue on the same spot, and the nearest background view would be one
    of them. Adds probe["blocked"] (bool per sampled frame)."""
    for p in probes:
        p["blocked"] = np.array([f in tracked and np.hypot(*(tracked[f] - (p["x"], p["y"])).T).min() < BG_CLEAR_M
                                 for f in p["frames"]], bool)


def verdict(p: dict, fps: float, d_occ: float = D_OCC) -> dict:
    frames = np.array(p["frames"])
    if len(frames) == 0:
        return {"static": "unknown", "reason": "spot never in view"}
    P = np.array(p["patches"], np.float32)
    P -= P.mean(axis=(1, 2), keepdims=True)
    n = P.shape[1]
    yy, xx = np.mgrid[:n, :n] - (n - 1) / 2
    core = np.hypot(xx, yy) * RES_M <= CORE_R_M
    margin = int(BG_MARGIN_S * fps)
    inside = (frames >= p["f0"]) & (frames <= p["f1"])
    free = ~p["blocked"] if "blocked" in p else np.ones(len(frames), bool)
    sides = {"before": (frames < p["f0"] - margin) & free, "after": (frames > p["f1"] + margin) & free}
    if inside.sum() < 2:
        return {"static": "unknown", "reason": "spot not in view during the window", "samples": int(inside.sum())}
    sides = {k: m for k, m in sides.items() if m.sum() >= MIN_BG_SIDE}
    if not sides:
        return {"static": "unknown", "reason": "no empty-road reference", "samples": int(inside.sum())}
    # Nearest background view, not a median: the drone moves, so buildings, poles and trees lean
    # into the patch differently from view to view. Before and after are judged apart: a stopped
    # car arrived (road empty before) or left (empty after), but the event may end while the car
    # is still there (the tracker lost it), so one side can show the same car. Occupied = unlike
    # every view of at least one side.
    Pi = P[inside][:, core]
    occ_side, d_side = {}, {}
    for k, m in sides.items():
        d = np.abs(Pi[:, None, :] - P[m][:, core][None, :, :]).mean(axis=2).min(axis=1)
        occ_side[k], d_side[k] = float((d > d_occ).mean()), float(np.median(d))
    best = max(occ_side, key=occ_side.get)
    occ = occ_side[best]
    v = "confirmed" if occ >= OCC_CONFIRM else "contradicted" if occ <= OCC_CONTRADICT else "unknown"
    return {"static": v, "occupied_frac": round(occ, 2), "d_median": round(d_side[best], 1), "reference": best,
            "samples": int(inside.sum()), "bg_samples": {k: int(m.sum()) for k, m in sides.items()}}


# --- applying it to events ---------------------------------------------------------------------

def apply(events: list[dict], verdicts: dict[str, dict]) -> None:
    for e in events:
        res = verdicts.get(e["event_id"])
        if res is None:
            continue
        base = e["value"].get("confidence_before_static", e["confidence"])
        e["value"]["confidence_before_static"] = base
        e["value"]["static"] = res
        e["tags"] = [t for t in e["tags"] if not t.startswith("static_")]
        conf = base + (STATIC_BONUS if res["static"] == "confirmed" else 0.0)
        e["confidence"] = round(min(1.0, conf), 3)
        if res["static"] != "unknown":
            e["tags"].append(f"static_{res['static']}")
        if e["status"] in ("flagged", "needs_review"):  # needs_review only ever comes from the confidence
            ok = e["confidence"] >= MIN_CONFIDENCE.get(e["type"], 0.0) and res["static"] != "contradicted"
            e["status"] = "flagged" if ok else "needs_review"


def check_events(cam, reader, fps: float, vdir: Path, kin_csv: Path) -> None:
    events = json.loads((vdir / "violations.json").read_text())
    stop = [e for e in events if e["type"] in STOP_TYPES]
    probes = [{"id": e["event_id"], "x": e["x"], "y": e["y"], "f0": e["start_frame"],
               "f1": e["end_frame"] if e["end_frame"] is not None else e["flag_frame"]} for e in stop]
    sample_patches(cam, reader, probes, fps)
    mark_blocked(probes, tracked_positions(kin_csv))
    verdicts = {p["id"]: verdict(p, fps) for p in probes}
    apply(events, verdicts)
    rewrite_events(vdir, events)
    for e in stop:
        print(f"{e['event_id']} {e['type']:15s} {e['status']:13s} conf {e['confidence']:.2f}  {verdicts[e['event_id']]}")
    print(f"[static] {len(stop)} stop-type events of {len(events)} checked -> {vdir / 'violations.json'}")


# --- calibration against CARLA truth -----------------------------------------------------------

def truth_probes(flight: Path, cam: FlightCamera, scene: dict, fps: float, n_frames: int) -> tuple[list, list]:
    """Positives: actors standing still >= POS_MIN_S that arrived and left inside the flight.
    Negatives: driving-lane points with no actor within NEG_CLEAR_M during a NEG_WINDOW_S window."""
    cf_frame = sorted((cf, fr) for fr, cf in cam.frame_cf.items())
    cfs, frs = np.array([c for c, _ in cf_frame]), np.array([f for _, f in cf_frame])
    to_frame = lambda c: int(frs[min(len(cfs) - 1, int(np.searchsorted(cfs, c)))])  # noqa: E731
    tracks = defaultdict(list)
    with open(flight / "vehicle_poses.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            tracks[r["id"]].append((int(r["carla_frame"]), float(r["x"]), float(r["y"])))
    pos = []
    by_frame = defaultdict(list)  # frame -> actor positions, for the negatives
    for aid, pts in tracks.items():
        a = np.array(sorted(pts))
        fr = np.array([to_frame(c) for c in a[:, 0]])
        for f, x, y in zip(fr, a[:, 1], a[:, 2]):
            by_frame[f].append((x, y))
        i = 0
        while i < len(a):  # maximal runs that stay within POS_MAX_MOVE_M of where they started
            j = i
            while j + 1 < len(a) and np.hypot(a[j + 1, 1] - a[i, 1], a[j + 1, 2] - a[i, 2]) <= POS_MAX_MOVE_M:
                j += 1
            f0, f1 = int(fr[i]), int(fr[j])
            if (f1 - f0) / fps >= POS_MIN_S and i > 0 and j < len(a) - 1:  # moved before and after
                pos.append({"id": f"actor{aid}@{f0}", "x": float(a[i:j + 1, 1].mean()), "y": float(a[i:j + 1, 2].mean()),
                            "f0": f0, "f1": f1})
            i = j + 1
    rng = np.random.default_rng(0)
    lanes = [np.array(l["centreline"], float) for l in scene["lanes"] if l.get("lane_type", "driving") == "driving"]
    pts = np.vstack(lanes)
    frames_sorted = np.array(sorted(by_frame))
    actors_at = {f: np.array(by_frame[f]) for f in frames_sorted}
    neg, tries = [], 0
    win = int(NEG_WINDOW_S * fps)
    while len(neg) < N_NEG and tries < 50000:
        tries += 1
        f0 = int(rng.integers(0, max(1, n_frames - win)))
        mid = f0 + win // 2
        uv = cam.to_pixels(mid, np.c_[pts, np.full(len(pts), cam.ground_z)])
        if uv is None:
            continue
        seen = np.flatnonzero((uv[:, 0] > 100) & (uv[:, 0] < cam.W - 100) & (uv[:, 1] > 100) & (uv[:, 1] < cam.H - 100))
        if len(seen) == 0:
            continue
        x, y = pts[seen[rng.integers(len(seen))]]
        in_win = frames_sorted[(frames_sorted >= f0) & (frames_sorted <= f0 + win)]
        near = np.vstack([actors_at[f] for f in in_win]) if len(in_win) else np.empty((0, 2))
        if len(near) and np.hypot(near[:, 0] - x, near[:, 1] - y).min() < NEG_CLEAR_M:
            continue
        neg.append({"id": f"road{len(neg)}", "x": float(x), "y": float(y), "f0": f0, "f1": f0 + win})
    return pos, neg


def check(flight: Path, scene_path: Path) -> None:
    cam = FlightCamera(flight)
    fps = json.loads((flight / "metadata.json").read_text()).get("avg_fps", 25.0)
    reader = FrameReader(flight / "frames")
    pos, neg = truth_probes(flight, cam, json.loads(scene_path.read_text()), fps, len(reader))
    print(f"[check] {len(pos)} true stationary episodes, {len(neg)} empty-road candidates; sampling frames...")
    sample_patches(cam, reader, pos + neg, fps)
    mark_blocked(pos + neg, tracked_positions(pipeline_kinematics(flight)))  # the pipeline's tracks, not the truth
    # only probes the check can judge at all (seen in the window, with an empty-road reference)
    usable = lambda ps: [p for p in ps if "occupied_frac" in verdict(p, fps)]  # noqa: E731
    pos_v, neg_v = usable(pos), usable(neg)
    for name, ps in (("parked", pos), ("empty_road", neg)):
        reasons = defaultdict(int)
        for p in ps:
            reasons[verdict(p, fps).get("reason", "usable")] += 1
        print(f"[check] {name}: {dict(reasons)}")
    rows = []
    for d in SWEEP:
        r = {"D_OCC": d}
        for name, ps in (("parked", pos_v), ("empty_road", neg_v)):
            vs = [verdict(p, fps, d)["static"] for p in ps]
            r[name] = {k: round(vs.count(k) / max(len(vs), 1), 3) for k in ("confirmed", "contradicted", "unknown")}
        rows.append(r)
        print(f"D_OCC {d:3d}  parked: {r['parked']}   empty road: {r['empty_road']}")
    stats = {"flight": flight.name, "parked_usable": len(pos_v), "empty_road_usable": len(neg_v), "sweep": rows,
             "d_median_parked": sorted(verdict(p, fps)["d_median"] for p in pos_v),
             "d_median_empty": sorted(verdict(p, fps)["d_median"] for p in neg_v)}
    out = RESULTS_DIR / flight.name / "static_check.json"
    out.write_text(json.dumps(stats, indent=1))
    print(f"[check] -> {out}")


def pipeline_kinematics(flight: Path) -> Path:
    kin = RESULTS_DIR / flight.name / "tracktrack_ours" / "violations" / "kinematics.csv"
    if not kin.exists():
        raise SystemExit(f"{kin} not found: run run_violations.py on this flight first (its tracks mark busy background views)")
    return kin


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path, nargs="?", default=None, help="Recorded CARLA flight folder")
    ap.add_argument("--check", action="store_true", help="Calibrate against CARLA truth (needs --scene)")
    ap.add_argument("--scene", type=Path, default=None, help="CARLA lane map (for --check's empty-road spots)")
    ap.add_argument("--violations", type=Path, default=None,
                    help="run_violations.py output folder (default: the pipeline's, under recorded_flight_validation)")
    ap.add_argument("--site", type=Path, default=None, help="Real footage: the clip's site file; needs --trajectories")
    ap.add_argument("--trajectories", type=Path, default=None)
    args = ap.parse_args()

    if args.site:
        from real_geometry import RealCamera, Site
        if args.trajectories is None:
            raise SystemExit("--site needs --trajectories")
        site = Site(args.site)
        cam = RealCamera(site, args.trajectories.with_name("scene_map.npz"))
        reader = FrameReader(site.video)
        fps = reader.cap.get(cv2.CAP_PROP_FPS) or 30.0
        vdir = args.violations or args.trajectories.parent / "violations"
        check_events(cam, reader, fps, vdir, vdir / "kinematics.csv")
        return
    if args.flight is None:
        ap.error("give a flight folder (or --site)")
    if args.check:
        if args.scene is None:
            ap.error("--check needs --scene")
        check(args.flight, args.scene)
        return
    cam = FlightCamera(args.flight)
    fps = json.loads((args.flight / "metadata.json").read_text()).get("avg_fps", 25.0)
    vdir = args.violations or RESULTS_DIR / args.flight.name / "tracktrack_ours" / "violations"
    check_events(cam, FrameReader(args.flight / "frames"), fps, vdir, pipeline_kinematics(args.flight))


if __name__ == "__main__":
    main()
