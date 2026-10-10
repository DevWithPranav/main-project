r"""End-to-end check of the anomaly pipeline on a CARLA flight with pasted potholes (Build Plan M9).

CARLA towns have no potholes, so this pastes real ones: pothole crops from the PothRGBD grouped
TEST split are warped onto the road at fixed world points (through the frame's logged camera pose,
at a chosen real size), on every processed frame, so they move with the drone like a real defect.
Ground truth is then exact: world position, size and per-frame mask of each pasted pothole.

Two modes (both run by default):
  oracle   the pasted masks themselves are the detections: measures projection (position error),
           area (m^2 error) and de-duplication (one event per pothole) with no model involved.
  model    the segmenter runs on the composited frames (tiled, as detect_anomalies.py): measures
           per-frame recall at aerial scale and event-level recall / false events.

Output (new folder): check.json, and a few composited frames with GT outlines.

Usage:
    python ml/pothole/synthetic_check.py --flight simulation/data_export/recorded_flights/20261002_001635
    python ml/pothole/synthetic_check.py --flight <dir> --sizes 1,2,4 --n 6 --weights <best.pt> --modes model
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import anomalies as A  # noqa: E402
import detect_anomalies as D  # noqa: E402

GT_SPLIT = HERE / "data" / "grouped" / "test"
OUT_ROOT = HERE.parents[1] / "ml" / "data" / "results" / "anomaly_checks"
MATCH_M = 1.5  # an event within this of a pasted pothole's centre is that pothole
MIN_SPACING_M = 8.0  # pasted potholes further apart than 2 x the 3 m dedup radius


def f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def patches(n: int, seed: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """(BGR crop, bool mask) of n pothole instances from the grouped test split (bbox + 10%)."""
    rng = np.random.default_rng(seed)
    imgs = sorted((GT_SPLIT / "images").glob("*.jpg"))
    out = []
    for p in rng.permutation(imgs):
        lab = GT_SPLIT / "labels" / f"{p.stem}.txt"
        if not lab.is_file():
            continue
        img = cv2.imread(str(p))
        h, w = img.shape[:2]
        for ln in lab.read_text().splitlines()[:1]:
            pts = (np.asarray(ln.split()[1:], float).reshape(-1, 2) * [w, h]).astype(np.int32)
            x0, y0 = pts.min(axis=0)
            x1, y1 = pts.max(axis=0)
            mx, my = int(0.1 * (x1 - x0)), int(0.1 * (y1 - y0))
            x0, y0, x1, y1 = max(0, x0 - mx), max(0, y0 - my), min(w, x1 + mx), min(h, y1 + my)
            m = np.zeros((h, w), np.uint8)
            cv2.fillPoly(m, [pts], 1)
            out.append((img[y0:y1, x0:x1].copy(), m[y0:y1, x0:x1].astype(bool)))
        if len(out) >= n:
            break
    return out


def pick_points(project: "D.RoadLevel", lanes: "D.Lanes", frames: list, n: int) -> list[np.ndarray]:
    """n on-lane world points seen near the middle of the flight, >= MIN_SPACING_M apart."""
    pts = []
    for frame, _, img in frames[len(frames) // 3: 2 * len(frames) // 3 + 1]:
        h, w = img.shape[:2]
        uu, vv = np.meshgrid(np.linspace(0.3 * w, 0.7 * w, 9), np.linspace(0.3 * h, 0.7 * h, 7))
        g = project(frame, uu.ravel(), vv.ravel())
        if g is None:
            continue
        for x, y in g:
            lane, _ = lanes.lookup(x, y)
            if lane and all(np.hypot(x - p[0], y - p[1]) >= MIN_SPACING_M for p in pts):
                pts.append(np.array([x, y]))
                if len(pts) >= n:
                    return pts
    return pts


def road_z(project: "D.RoadLevel", x: float, y: float) -> float:
    if project.surface is not None:
        top, _ = project.surface.heights(np.array([x]), np.array([y]))
        if np.isfinite(top[0]):
            return float(top[0])
    return project.plane_z


def paste(img: np.ndarray, frame: int, project: "D.RoadLevel", centre: np.ndarray, size_m: float,
          patch: tuple[np.ndarray, np.ndarray]) -> np.ndarray | None:
    """Warp the patch onto the size_m x size_m ground square at centre; returns its frame mask."""
    z = road_z(project, *centre)
    hs = size_m / 2
    world = np.array([[centre[0] - hs, centre[1] - hs, z], [centre[0] + hs, centre[1] - hs, z],
                      [centre[0] + hs, centre[1] + hs, z], [centre[0] - hs, centre[1] + hs, z]])
    uv = project.cam.to_pixels(frame, world)
    if uv is None or not np.isfinite(uv).all():
        return None
    h, w = img.shape[:2]
    x0, y0 = np.floor(uv.min(axis=0)).astype(int)
    x1, y1 = np.ceil(uv.max(axis=0)).astype(int)
    if x1 < 0 or y1 < 0 or x0 >= w or y0 >= h:
        return None
    crop, m = patch
    ph, pw = crop.shape[:2]
    H = cv2.getPerspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]), uv.astype(np.float32))
    warped = cv2.warpPerspective(crop, H, (w, h), flags=cv2.INTER_AREA)
    wm = cv2.warpPerspective(m.astype(np.float32), H, (w, h), flags=cv2.INTER_LINEAR) > 0.5
    if wm.sum() < 4:
        return None
    img[wm] = warped[wm]
    return wm


def score(events: list[dict], gt: list[dict]) -> dict:
    used, errs, area_err, matched = set(), [], [], 0
    for g in gt:
        best, bd = None, MATCH_M
        for k, e in enumerate(events):
            d = float(np.hypot(e["x"] - g["x"], e["y"] - g["y"]))
            if k not in used and d <= bd:
                best, bd = k, d
        if best is not None:
            used.add(best)
            matched += 1
            errs.append(bd)
            area_err.append((events[best]["area_sq_m"] - g["area_m2"]) / g["area_m2"])
    return {"n_gt": len(gt), "n_events": len(events), "matched": matched, "false_events": len(events) - len(used),
            "event_recall": round(matched / len(gt), 3) if gt else None,
            "pos_err_m_median": round(float(np.median(errs)), 3) if errs else None,
            "pos_err_m_max": round(float(np.max(errs)), 3) if errs else None,
            "area_rel_err_median": round(float(np.median(area_err)), 3) if area_err else None,
            "area_rel_err_abs_max": round(float(np.max(np.abs(area_err))), 3) if area_err else None,
            "by_band": {b: sum(e["severity_band"] == b for e in events) for b in ("low", "medium", "high")}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flight", required=True)
    ap.add_argument("--weights", default=str(D.DEFAULT_WEIGHTS))
    ap.add_argument("--n", type=int, default=6, help="potholes to paste")
    ap.add_argument("--sizes", default="1,2,3", help="pothole sizes in metres, cycled over the n potholes")
    ap.add_argument("--stride", type=int, default=10)
    ap.add_argument("--max-frames", type=int, default=60)
    ap.add_argument("--modes", default="oracle,model")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--tile", type=int, default=640)
    ap.add_argument("--overlap", type=float, default=0.2)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="0")
    ap.add_argument("--min-frames", type=int, default=A.MIN_FRAMES)
    a = ap.parse_args()
    a.full, a.drop_shadows = True, False

    flight = Path(a.flight)
    meta = json.loads((flight / "metadata.json").read_text())
    town = (meta.get("map") or "").rsplit("/", 1)[-1]
    scene = json.loads((D.ENGINE / "configs" / "scenes" / f"{town}.json").read_text(encoding="utf-8"))
    project, lanes = D.RoadLevel(flight, scene), D.Lanes(scene)
    out = Path(a.out) if a.out else OUT_ROOT / f"{flight.name}_{datetime.now():%Y%m%d_%H%M%S}"
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} exists; pick a new --out")
    out.mkdir(parents=True, exist_ok=True)

    frames = [f for f in D.flight_frames(flight, a.stride, a.max_frames) if project.cam.pose(f[0]) is not None]
    pts = pick_points(project, lanes, frames, a.n)
    sizes = [float(s) for s in a.sizes.split(",")]
    pats = patches(len(pts), a.seed)
    gt = []
    for i, p in enumerate(pts):
        s = sizes[i % len(sizes)]
        frac = float(pats[i][1].mean())  # pothole share of the pasted square
        gt.append({"id": i, "x": round(float(p[0]), 2), "y": round(float(p[1]), 2), "size_m": s, "area_m2": round(frac * s * s, 3)})

    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    model = cats = None
    if "model" in modes:
        from ultralytics import YOLO
        import modify_model  # noqa: F401
        model = YOLO(a.weights)
        cats = D.category_of(model.names)
    dd = {m: A.Deduper(min_frames=a.min_frames) for m in modes}
    stats = {m: {k: 0 for k in ("frames", "raw_detections", "shadow_flagged", "waterlogged", "no_outline", "no_pose",
                                "off_road", "implausible_size", "sightings")} for m in modes}
    px = {"gt_instances": 0, "tp": 0, "fp": 0}
    t0 = time.perf_counter()
    for k, (frame, t_s, img) in enumerate(frames):
        comp = img.copy()
        gms = []
        for g, pat in zip(gt, pats):
            m = paste(comp, frame, project, np.array([g["x"], g["y"]]), g["size_m"], pat)
            if m is not None:
                gms.append(m)
        px["gt_instances"] += len(gms)
        if k % max(1, len(frames) // 4) == 0:
            vis = comp.copy()
            for m in gms:
                cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(vis, cs, -1, (0, 255, 0), 1)
            cv2.imwrite(str(out / f"frame_{frame:05d}.jpg"), vis)
        for mode in modes:
            if mode == "oracle":
                dets = [{"cls": "pothole", "conf": 1.0, "mask": m} for m in gms]
            else:
                dets = D.predict(model, comp, a, cats)
                used = set()
                for d in dets:
                    j = next((j for j, m in enumerate(gms) if j not in used and A.mask_iou(d["mask"], m)[0] >= 0.5), None)
                    if j is None:
                        px["fp"] += 1
                    else:
                        used.add(j)
                        px["tp"] += 1
            stats[mode]["frames"] += 1
            stats[mode]["raw_detections"] += len(dets)
            for s, _ in D.sightings_of_frame(frame, t_s, comp, dets, project, lanes, a, stats[mode]):
                dd[mode].add(s)
    res = {"flight": flight.name, "town": town, "weights": a.weights if model else None, "frames": len(frames),
           "stride": a.stride, "gt": gt, "elapsed_s": round(time.perf_counter() - t0, 1), "modes": {}}
    for mode in modes:
        events = [A.to_event(c, f"synthetic-{mode}-{i:03d}") for i, c in enumerate(dd[mode].events())]
        r = score(events, gt)
        r["stats"] = stats[mode]
        r["clusters"] = len(dd[mode].clusters)
        if mode == "model":
            fn = px["gt_instances"] - px["tp"]
            prec = px["tp"] / (px["tp"] + px["fp"]) if px["tp"] + px["fp"] else 0.0
            rec = px["tp"] / px["gt_instances"] if px["gt_instances"] else 0.0
            r["per_frame_mask"] = {**px, "fn": fn, "precision": round(prec, 3), "recall": round(rec, 3),
                                   "f1": round(f1(prec, rec), 3)}
        res["modes"][mode] = r
    (out / "check.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({m: {k: v for k, v in r.items() if k != "stats"} for m, r in res["modes"].items()}, indent=1))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
