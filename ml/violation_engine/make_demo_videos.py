"""Stage 5 — presentation demo videos for a processed recorded flight.

Builds two videos from outputs already produced by process_recorded_flight.py
(run it with both --tracker bytetrack and the default botsort first):

  demo.mp4        — the final pipeline (detector + BoT-SORT-ReID + tracklet
                    stitching) with a HUD: vehicles in view, unique vehicles so far
  comparison.mp4  — ByteTrack baseline vs final pipeline side by side on the same
                    frames; the "unique vehicles so far" counters make the ID-switch
                    difference visible without explanation

Blank frames (nothing in view — e.g. before takeoff or after the drone clips
through the road) are skipped automatically.

Violation flags (Stage 4) are not drawn yet — the rules aren't implemented. Once
they write per-frame flags, overlay them in draw_tracks().

Usage:
    python ml/violation_engine/make_demo_videos.py 20260920_194932
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

RECORDED_FLIGHTS_DIR = Path(__file__).resolve().parents[2] / "simulation" / "data_export" / "recorded_flights"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "recorded_flight_validation"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "presentation"
BLANK_TEXTURE = 20.0  # Laplacian variance; blank grey frames measure ~3, real footage ~100-300


def load_rows(csv_path: Path) -> dict[int, list[dict]]:
    by_frame = defaultdict(list)
    for r in csv.DictReader(open(csv_path, newline="")):
        by_frame[int(r["frame"])].append(r)
    return by_frame


def id_color(tid: int) -> tuple[int, int, int]:
    return tuple(int(c) for c in np.random.default_rng(tid).integers(60, 255, 3))


def is_blank(img: np.ndarray) -> bool:
    gray = cv2.cvtColor(cv2.resize(img, (960, 540)), cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var() < BLANK_TEXTURE


def draw_tracks(img: np.ndarray, rows: list[dict], scale: float) -> None:
    for r in rows:
        tid = int(r["track_id"])
        cx, cy, w, h = (float(r[k]) * scale for k in ("cx", "cy", "w", "h"))
        p0, p1 = (int(cx - w / 2), int(cy - h / 2)), (int(cx + w / 2), int(cy + h / 2))
        color = id_color(tid)
        cv2.rectangle(img, p0, p1, color, 2)
        label = f"{tid} {r['class']}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        ty = max(th + 6, p0[1])
        cv2.rectangle(img, (p0[0], ty - th - 6), (p0[0] + tw + 4, ty), color, -1)
        cv2.putText(img, label, (p0[0] + 2, ty - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)


def draw_hud(img: np.ndarray, lines: list[str]) -> None:
    pad, lh = 10, 26
    w = max(cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)[0][0] for t in lines) + 2 * pad
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (w, pad + lh * len(lines)), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    for i, t in enumerate(lines):
        cv2.putText(img, t, (pad, pad + lh * (i + 1) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", help="Recorded flight run_id (processed with both bytetrack and botsort)")
    ap.add_argument("--width", type=int, default=1280, help="Output width of demo.mp4 (comparison uses 2x960)")
    args = ap.parse_args()

    run_dir = RECORDED_FLIGHTS_DIR / args.run_id
    res = RESULTS_DIR / args.run_id
    final_csv = res / "botsort" / "trajectories_stitched.csv"
    base_csv = res / "bytetrack" / "trajectories.csv"
    for p in (final_csv, base_csv):
        if not p.exists():
            raise SystemExit(f"{p} not found — run process_recorded_flight.py with --tracker bytetrack and botsort first.")
    meta_path = run_dir / "metadata.json"
    fps = json.loads(meta_path.read_text()).get("avg_fps", 15.0) if meta_path.exists() else 15.0

    final, base = load_rows(final_csv), load_rows(base_csv)
    frame_paths = sorted((run_dir / "frames").glob("*.jpg"))
    out = OUT_DIR / args.run_id
    out.mkdir(parents=True, exist_ok=True)

    demo_w, cmp_w = None, None
    seen_final, seen_base = set(), set()
    n_written = n_blank = 0
    for f, p in enumerate(frame_paths):
        img = cv2.imread(str(p))
        if is_blank(img):
            n_blank += 1
            continue
        seen_final.update(int(r["track_id"]) for r in final.get(f, []))
        seen_base.update(int(r["track_id"]) for r in base.get(f, []))
        t = f / fps

        s = args.width / img.shape[1]
        demo = cv2.resize(img, (args.width, int(img.shape[0] * s)))
        draw_tracks(demo, final.get(f, []), s)
        draw_hud(demo, [f"Detector: YOLO26l (VisDrone fine-tuned)  |  Tracker: BoT-SORT-ReID + stitching",
                        f"t = {t:5.1f}s   vehicles in view: {len(final.get(f, []))}   unique vehicles so far: {len(seen_final)}"])
        if demo_w is None:
            demo_w = cv2.VideoWriter(str(out / "demo.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, demo.shape[1::-1])
        demo_w.write(demo)

        halves = []
        for rows, seen, name in ((base.get(f, []), seen_base, "ByteTrack (baseline)"),
                                 (final.get(f, []), seen_final, "BoT-SORT-ReID + stitching")):
            s2 = 960 / img.shape[1]
            half = cv2.resize(img, (960, int(img.shape[0] * s2)))
            draw_tracks(half, rows, s2)
            draw_hud(half, [name, f"unique vehicle IDs so far: {len(seen)}"])
            halves.append(half)
        side = np.hstack([halves[0], np.full((halves[0].shape[0], 6, 3), 255, np.uint8), halves[1]])
        if cmp_w is None:
            cmp_w = cv2.VideoWriter(str(out / "comparison.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, side.shape[1::-1])
        cmp_w.write(side)
        n_written += 1

    for w in (demo_w, cmp_w):
        if w is not None:
            w.release()
    print(f"[demo] {n_written} frames written, {n_blank} blank frames skipped")
    print(f"[demo] unique vehicle IDs — ByteTrack: {len(seen_base)}, BoT-SORT + stitching: {len(seen_final)}")
    print(f"[demo] -> {out / 'demo.mp4'}\n[demo] -> {out / 'comparison.mp4'}")


if __name__ == "__main__":
    main()
