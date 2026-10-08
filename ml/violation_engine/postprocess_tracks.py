"""Offline track post-processing (Improvement Plan items D2 + D4), run after stitching.

D2 — per-track class voting. The detector classifies every frame independently, so the
same vehicle flickers between classes (car <-> van, car <-> truck). Each final track
gets the confidence-weighted majority class over its whole lifetime, written to every
row; the per-frame detector class is kept in `raw_class`. Classes are first mapped to
the project's 3-class scheme (`car` incl. van, `bus`, `truck` — plan A1), so the
current 4-class model can be scored against the 3-class ground truth.

D4 — track-level false-positive removal. Tracks shorter than MIN_DURATION_S or with
mean confidence below MIN_MEAN_CONF are dropped (flickering snow/roof blobs, one-off
misdetections). Never filtered by "stationary" — parked cars matter for no-parking.

D3 — gap filling (--gap-fill SECONDS, default 1 s, 0 = off): inside each kept track, gaps of up to
that many seconds are filled with linearly interpolated boxes (conf 0, interp=1), so a
vehicle the detector briefly lost doesn't blink out (and its speed doesn't jump).

Outputs next to the input CSV:
  trajectories_final.csv  kept tracks only, voted `class` + `raw_class` (schema v2)
  track_summary.csv       one row per track: class, votes, duration, mean conf,
                          kept / dropped + reason

Usage (standalone, on an existing stitched output):
    python ml/violation_engine/postprocess_tracks.py path/to/trajectories_stitched.csv --fps 30
    python ml/violation_engine/postprocess_tracks.py path/to/trajectories_stitched.csv --fps 30 --min-duration 0.5
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

CLASS_MAP = {"van": "car"}  # 3-class scheme (plan A1); anything not listed keeps its name
MIN_DURATION_S = 1.0
MIN_MEAN_CONF = 0.3
GAP_FILL_S = 1.0  # D3; on the CARLA GT: MOTA +1.6-2.3 points, precision unchanged (tracker Section 2.3)
OUTPUT_COLUMNS = ["frame", "time_s", "track_id", "class", "raw_class", "cx", "cy", "w", "h", "conf"]


def count_class_changes(rows: list[dict], key: str) -> int:
    """Number of times a track's class differs from its previous row, summed over tracks."""
    last: dict[str, str] = {}
    changes = 0
    for r in sorted(rows, key=lambda r: int(r["frame"])):
        tid, cls = r["track_id"], r[key]
        if tid in last and last[tid] != cls:
            changes += 1
        last[tid] = cls
    return changes


INTERP_COLUMNS = ["cx", "cy", "w", "h", "map_x", "map_y", "time_s"]


def fill_gaps(rows: list[dict], max_gap_frames: int) -> tuple[list[dict], int]:
    """D3: inside each track, add linearly interpolated rows across gaps of up to max_gap_frames missing
    frames (a vehicle the detector briefly lost). Interpolated rows get conf 0 and interp=1; real rows
    interp=0. Returns (rows sorted by frame, number of rows added)."""
    by_track: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_track[str(r["track_id"])].append({**r, "interp": 0})
    out, added = [], 0
    for trs in by_track.values():
        trs.sort(key=lambda r: int(r["frame"]))
        for a, b in zip(trs, trs[1:]):
            out.append(a)
            fa, fb = int(a["frame"]), int(b["frame"])
            if 1 < fb - fa <= max_gap_frames + 1:
                for f in range(fa + 1, fb):
                    t = (f - fa) / (fb - fa)
                    new = {**a, "frame": f, "conf": 0, "interp": 1}
                    for k in INTERP_COLUMNS:
                        if a.get(k) not in (None, "") and b.get(k) not in (None, ""):
                            new[k] = round(float(a[k]) + t * (float(b[k]) - float(a[k])), 4 if k == "time_s" else 1)
                    out.append(new)
                    added += 1
        out.append(trs[-1])
    out.sort(key=lambda r: (int(r["frame"]), int(r["track_id"])))
    return out, added


def postprocess(rows: list[dict], fps: float, min_duration_s: float = MIN_DURATION_S,
                min_mean_conf: float = MIN_MEAN_CONF, gap_fill_s: float = GAP_FILL_S) -> tuple[list[dict], list[dict], dict]:
    """rows: trajectory rows (csv.DictReader dicts, final track_ids). Returns
    (kept rows with voted class, per-track summary rows, stats). gap_fill_s > 0 adds D3 gap filling
    (gaps up to that many seconds) after the filtering."""
    by_track: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_track[str(r["track_id"])].append(r)

    voted, summary, dropped = {}, [], set()
    for tid, trs in by_track.items():
        votes: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for r in trs:
            cls = CLASS_MAP.get(r["class"], r["class"])
            votes[cls] += float(r["conf"])
            counts[cls] += 1
        cls = max(votes, key=lambda c: (votes[c], counts[c]))
        voted[tid] = cls

        frames = [int(r["frame"]) for r in trs]
        times = [float(r["time_s"]) for r in trs]
        duration = max(times) - min(times) + 1.0 / fps
        mean_conf = sum(float(r["conf"]) for r in trs) / len(trs)
        reasons = []
        if duration < min_duration_s:
            reasons.append(f"duration<{min_duration_s:g}s")
        if mean_conf < min_mean_conf:
            reasons.append(f"mean_conf<{min_mean_conf:g}")
        if reasons:
            dropped.add(tid)
        summary.append({
            "track_id": tid, "class": cls,
            "votes": ";".join(f"{c}:{v:.2f}" for c, v in sorted(votes.items(), key=lambda kv: -kv[1])),
            "n_rows": len(trs), "first_frame": min(frames), "last_frame": max(frames),
            "duration_s": round(duration, 3), "mean_conf": round(mean_conf, 3),
            "kept": int(not reasons), "drop_reason": "+".join(reasons),
        })

    extra = [k for k in rows[0].keys() if k not in OUTPUT_COLUMNS] if rows else []
    kept = []
    for r in rows:
        tid = str(r["track_id"])
        if tid in dropped:
            continue
        kept.append({**r, "raw_class": r.get("raw_class") or r["class"], "class": voted[tid]})

    summary.sort(key=lambda s: int(s["track_id"]))
    interpolated = 0
    if gap_fill_s > 0:
        kept, interpolated = fill_gaps(kept, round(gap_fill_s * fps))
        extra = extra + ["interp"]
    stats = {
        "rows_interpolated": interpolated,
        "ids_before": len(by_track), "ids_after": len(by_track) - len(dropped),
        "dropped_short": sum(1 for s in summary if "duration" in s["drop_reason"]),
        "dropped_low_conf": sum(1 for s in summary if "mean_conf" in s["drop_reason"]),
        "rows_before": len(rows), "rows_after": len(kept),
        "class_changes_before": count_class_changes(rows, "class"),
        "class_changes_after": count_class_changes(kept, "class"),
        "columns": OUTPUT_COLUMNS + extra,
    }
    return kept, summary, stats


def write_outputs(traj_csv: Path, kept: list[dict], summary: list[dict], columns: list[str]) -> tuple[Path, Path]:
    out_csv = traj_csv.with_name("trajectories_final.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)
    summary_csv = traj_csv.with_name("track_summary.csv")
    with open(summary_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()) if summary else ["track_id"])
        w.writeheader()
        w.writerows(summary)
    return out_csv, summary_csv


def run(traj_csv: Path, fps: float, min_duration_s: float = MIN_DURATION_S,
        min_mean_conf: float = MIN_MEAN_CONF, gap_fill_s: float = GAP_FILL_S) -> dict:
    rows = list(csv.DictReader(open(traj_csv, newline="")))
    kept, summary, stats = postprocess(rows, fps, min_duration_s, min_mean_conf, gap_fill_s)
    stats["out_csv"], stats["summary_csv"] = write_outputs(traj_csv, kept, summary, stats["columns"])
    return stats


def format_stats(stats: dict) -> str:
    return (f"unique IDs {stats['ids_before']} -> {stats['ids_after']} "
            f"(dropped {stats['dropped_short']} short, {stats['dropped_low_conf']} low-conf; a track can be both); "
            f"class changes within tracks {stats['class_changes_before']} -> {stats['class_changes_after']}"
            + (f"; {stats['rows_interpolated']} rows gap-filled" if stats.get("rows_interpolated") else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("traj_csv", type=Path, help="trajectories_stitched.csv (or trajectories.csv)")
    ap.add_argument("--fps", type=float, required=True, help="Video fps (used for the duration of one frame)")
    ap.add_argument("--min-duration", type=float, default=MIN_DURATION_S, help="Drop tracks shorter than this (s)")
    ap.add_argument("--min-mean-conf", type=float, default=MIN_MEAN_CONF, help="Drop tracks with lower mean conf")
    ap.add_argument("--gap-fill", type=float, default=GAP_FILL_S, help="D3: interpolate gaps up to this many seconds (0 = off)")
    args = ap.parse_args()

    if not args.traj_csv.exists():
        raise SystemExit(f"{args.traj_csv} not found.")
    stats = run(args.traj_csv, args.fps, args.min_duration, args.min_mean_conf, args.gap_fill)
    print(f"[postprocess] {format_stats(stats)}")
    print(f"[postprocess] wrote {stats['out_csv']} + {stats['summary_csv'].name}")


if __name__ == "__main__":
    main()
