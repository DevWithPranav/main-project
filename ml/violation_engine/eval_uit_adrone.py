"""Score violation events against UIT-ADrone's anomaly labels (Violation Engine, Phase B step B5).

docs/Violation_Engine_Architecture.md, Section 3.6 / 6 ("L2 pipeline (real)"). UIT-ADrone
(IEEE JSTARS 2023, https://uit-together.github.io/datasets/UIT-ADrone/): 51 drone videos of 3
roundabouts in Ho Chi Minh City, 30 fps, 50-70 m altitude. Its anomaly labels are FRAME-LEVEL
only: one .npy per test video, one 0 (normal) / 1 (abnormal) per frame - no type, no vehicle.

The labels cover 10 anomaly types, of which ours can see the car ones: driving in the
opposite direction (wrong_way), illegal left/right turn and wrong roundabout direction
(illegal_u_turn / wrong_way), illegal parking in the street (no_parking). Pedestrian,
sidewalk, bulky-goods and motorbike-fall anomalies are out of scope, so recall has a ceiling
well below 1; precision (are our flags in abnormal frames?) is the more telling number.

Per video, from run_violations.py --site output (events on the video's own frame indices):
  frame score  max confidence of the counted events whose start..end covers the frame (0 if none)
  frame_auc    ROC AUC of that score against the labels (the dataset's usual protocol)
  frame P/R/F1 at "any counted event covers the frame"
  event_precision    share of our counted events that overlap an abnormal stretch
  segment_recall     share of abnormal stretches (runs of 1s) touched by at least one event

Layout: <gt dir>/<video>.npy and <results root>/<video>/**/violations/violations.json (the first
match). Videos without results are listed and skipped.

Usage:
    python ml/violation_engine/eval_uit_adrone.py <gt dir> <results root>
    python ml/violation_engine/eval_uit_adrone.py <gt dir> <results root> --types wrong_way no_parking illegal_u_turn
"""

import argparse
import json
from pathlib import Path

import numpy as np

from events import COUNTED_STATUS

IN_SCOPE = ("wrong_way", "illegal_u_turn", "no_parking", "highway_stop", "lane_violation")


def frame_scores(events: list[dict], n: int, types) -> np.ndarray:
    s = np.zeros(n)
    for e in events:
        if e["status"] not in COUNTED_STATUS or e["type"] not in types:
            continue
        a, b = e["start_frame"], e["end_frame"] if e["end_frame"] is not None else e["flag_frame"]
        a, b = max(0, int(a)), min(n - 1, int(b))
        if a <= b:
            s[a:b + 1] = np.maximum(s[a:b + 1], max(float(e["confidence"]), 1e-3))
    return s


def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    """ROC AUC by the rank-sum (Mann-Whitney) formula, ties counted half."""
    pos, neg = labels == 1, labels == 0
    if not pos.any() or not neg.any():
        return None
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores))
    s_sorted = scores[order]
    i = 0
    while i < len(s_sorted):  # average ranks over ties
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    n_pos, n_neg = pos.sum(), neg.sum()
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def segments(labels: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.r_[0, labels.astype(int), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def score_video(labels: np.ndarray, events: list[dict], types) -> dict:
    labels = (np.asarray(labels).ravel() > 0).astype(int)
    n = len(labels)
    s = frame_scores(events, n, types)
    pred = s > 0
    tp, fp, fn = int((pred & (labels == 1)).sum()), int((pred & (labels == 0)).sum()), int((~pred & (labels == 1)).sum())
    ours = [e for e in events if e["status"] in COUNTED_STATUS and e["type"] in types]
    segs = segments(labels)
    hit_ev = sum(1 for e in ours if labels[max(0, e["start_frame"]):min(n, (e["end_frame"] or e["flag_frame"]) + 1)].any())
    hit_seg = sum(1 for a, b in segs if s[a:b + 1].any())
    return {"frames": n, "abnormal_frames": int(labels.sum()), "abnormal_segments": len(segs), "events": len(ours),
            "frame_auc": None if (a := auc(s, labels)) is None else round(a, 3),
            "frame_precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "frame_recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "frame_f1": round(2 * tp / (2 * tp + fp + fn), 3) if tp + fp + fn else None,
            "event_precision": round(hit_ev / len(ours), 3) if ours else None,
            "segment_recall": round(hit_seg / len(segs), 3) if segs else None,
            "_counts": (tp, fp, fn, hit_ev, len(ours), hit_seg, len(segs))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("gt", type=Path, help="Folder of <video>.npy frame labels (UIT-ADrone test set)")
    ap.add_argument("results", type=Path, help="Folder with one sub-folder per video holding run_violations.py output")
    ap.add_argument("--types", nargs="*", default=list(IN_SCOPE), help="Event types that count as anomaly flags")
    ap.add_argument("--out", type=Path, default=None, help="Result JSON (default: <results>/uit_adrone_eval.json)")
    args = ap.parse_args()

    per, missing = {}, []
    tot = np.zeros(7, int)
    for npy in sorted(args.gt.glob("*.npy")):
        found = sorted((args.results / npy.stem).glob("**/violations/violations.json"))
        if not found:
            missing.append(npy.stem)
            continue
        r = score_video(np.load(npy), json.loads(found[0].read_text()), set(args.types))
        tot += np.array(r.pop("_counts"))
        per[npy.stem] = {**r, "source": str(found[0])}
    tp, fp, fn, hit_ev, n_ev, hit_seg, n_seg = (int(v) for v in tot)
    overall = {"videos": len(per), "frame_precision": round(tp / (tp + fp), 3) if tp + fp else None,
               "frame_recall": round(tp / (tp + fn), 3) if tp + fn else None,
               "frame_f1": round(2 * tp / (2 * tp + fp + fn), 3) if tp + fp + fn else None,
               "frame_auc_mean": round(float(np.mean([v["frame_auc"] for v in per.values() if v["frame_auc"] is not None])), 3)
               if any(v["frame_auc"] is not None for v in per.values()) else None,
               "event_precision": round(hit_ev / n_ev, 3) if n_ev else None,
               "segment_recall": round(hit_seg / n_seg, 3) if n_seg else None, "types": args.types}
    result = {"overall": overall, "per_video": per, "videos_without_results": missing}
    out = args.out or args.results / "uit_adrone_eval.json"
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(overall, indent=1))
    if missing:
        print(f"[uit-adrone] no results for {len(missing)} videos: {missing[:10]}{' ...' if len(missing) > 10 else ''}")
    print(f"[uit-adrone] -> {out}")


if __name__ == "__main__":
    main()
