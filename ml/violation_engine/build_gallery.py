"""One folder with every detected violation's evidence clip, CARLA and real footage, by type.

    <out>/carla/<type>/<condition>_<event id>.mp4
    <out>/real/<type>/<condition>_<event id>.mp4
    <out>/index.csv     one row per counted event (flagged / needs_review / possible_breakdown):
                        source, flight or clip, event id, type, condition + name, status, tags,
                        time, value, clip path (empty if the event has no clip)
    <out>/README.md     counts per source and type

Clips are hard links to the evidence clips render_violations.py --clips wrote (no extra disk
space; a copy if the link fails). Suppressed events (queue, edge cases kept for audit) have no
clip and are left out. The folder is rebuilt from scratch on every run.

Sources: the newest violations* folder with a violations.json of each processed CARLA flight
(ml/data/results/recorded_flight_validation/<flight>/<tracker>/) and each real clip
(ml/data/results/video_validation/<clip>/<tracker>/), or --carla / --real <violations dir> ...

Usage:
    venv\\Scripts\\python.exe ml/violation_engine/build_gallery.py
    venv\\Scripts\\python.exe ml/violation_engine/build_gallery.py --out ml/data/results/violation_gallery
"""

import argparse
import csv
import datetime
import json
import os
import shutil
from collections import Counter
from pathlib import Path

from events import COUNTED_STATUS
from schemas import condition_of

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "ml" / "data" / "results"
CARLA_ROOT = RESULTS / "recorded_flight_validation"
REAL_ROOT = RESULTS / "video_validation"
CONDITIONS = REPO / "schemas" / "conditions.json"
REVIEW = Path(__file__).resolve().parent / "configs" / "real_event_review.json"  # by-eye verdicts on real footage


def newest_violations(run: Path) -> Path | None:
    """The newest violations* folder with a violations.json, preferring one with rendered clips
    (events/*.mp4): experiment folders (e.g. violations_m3_pre) are usually left without clips."""
    dirs = [d for d in run.glob("violations*") if (d / "violations.json").is_file()]
    return max(dirs, key=lambda d: (any((d / "events").glob("*.mp4")), (d / "violations.json").stat().st_mtime), default=None)


def discover(root: Path) -> list[Path]:
    """One violations folder per flight / clip: across its run folders (tracker or detector variants,
    e.g. tracktrack_ours, tracktrack_retrain_v1), the newest with rendered clips."""
    out = []
    for item in sorted(p for p in root.iterdir() if p.is_dir()):
        cands = [v for run in item.iterdir() if run.is_dir() and (v := newest_violations(run)) is not None]
        if cands:
            out.append(max(cands, key=lambda d: (any((d / "events").glob("*.mp4")), (d / "violations.json").stat().st_mtime)))
    return out


def link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=RESULTS / "violation_gallery")
    ap.add_argument("--carla", type=Path, nargs="*", default=None, help="CARLA violations folders (default: discover)")
    ap.add_argument("--real", type=Path, nargs="*", default=None, help="Real-footage violations folders (default: discover)")
    a = ap.parse_args()

    names = {c["id"]: c["name"] for c in json.loads(CONDITIONS.read_text(encoding="utf-8"))["conditions"]}
    review = json.loads(REVIEW.read_text(encoding="utf-8"))["events"] if REVIEW.is_file() else {}
    sources = [("carla", d) for d in (a.carla if a.carla is not None else discover(CARLA_ROOT))]
    sources += [("real", d) for d in (a.real if a.real is not None else discover(REAL_ROOT))]
    if a.out.exists():
        shutil.rmtree(a.out)
    a.out.mkdir(parents=True)

    rows, counts, no_clip = [], Counter(), 0
    for kind, vdir in sources:
        data = json.loads((vdir / "violations.json").read_text(encoding="utf-8"))
        events = data["events"] if isinstance(data, dict) else data
        clip_name = vdir.parents[1].name if kind == "real" else vdir.parents[1].name  # <flight> or <clip>
        for e in events:
            if e.get("status") not in COUNTED_STATUS or e.get("kind", "violation") != "violation":
                continue
            cond = e.get("condition") or condition_of(e) or "-"  # runs before 2026-10-09 have no condition field
            rel = (e.get("evidence") or {}).get("clip")
            dst = ""
            if rel and (vdir / rel).is_file():
                d = a.out / kind / e["type"] / f"{cond}_{e['event_id']}.mp4"
                link(vdir / rel, d)
                dst = d.relative_to(a.out).as_posix()
            else:
                no_clip += 1
            counts[(kind, e["type"], cond)] += 1
            rows.append({"source": kind, "flight_or_clip": clip_name, "violations_dir": vdir.relative_to(REPO).as_posix(),
                         "event_id": e["event_id"], "type": e["type"], "condition": cond, "condition_name": names.get(cond, ""),
                         "status": e["status"], "tags": " ".join(e.get("tags") or []), "flag_s": e.get("flag_s"),
                         "track_ids": " ".join(map(str, e.get("track_ids") or [])),
                         "value": json.dumps(e.get("value") or {}, separators=(",", ":")), "clip": dst,
                         "by_eye": review.get(e["event_id"], {}).get("verdict", ""),
                         "by_eye_note": review.get(e["event_id"], {}).get("note", "")})

    with open(a.out / "index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["source"])
        w.writeheader()
        w.writerows(rows)

    lines = [f"# Violation gallery", "",
             f"Built {datetime.datetime.now():%Y-%m-%d %H:%M} by `ml/violation_engine/build_gallery.py`. "
             f"{len(rows)} counted events ({len(rows) - no_clip} with a clip). Suppressed events are not included.", "",
             "Clips: `carla/<type>/` and `real/<type>/`, named `<condition>_<event id>.mp4`. Every event is listed in `index.csv`.", "",
             "Sources:", ""]
    lines += [f"- {k}: `{d.relative_to(REPO).as_posix()}`" for k, d in sources]
    rv = Counter((r["type"], r["by_eye"]) for r in rows if r["source"] == "real" and r["by_eye"])
    if rv:
        lines += ["", "Real footage, checked by eye (`configs/real_event_review.json`):", "",
                  "| Type | true | plausible | false |", "|---|---|---|---|"]
        lines += [f"| {t} | {rv[(t, 'true')]} | {rv[(t, 'plausible')]} | {rv[(t, 'false')]} |" for t in sorted({t for t, _ in rv})]
    lines += ["", "| Source | Type | Condition | Events |", "|---|---|---|---|"]
    lines += [f"| {k} | {t} | {c} {names.get(c, '')} | {n} |" for (k, t, c), n in sorted(counts.items())]
    (a.out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[gallery] {len(rows)} events, {len(rows) - no_clip} clips, {len(sources)} sources -> {a.out}")
    for (k, t), n in sorted(Counter({(k, t): n for (k, t, _), n in counts.items()}).items()):
        print(f"   {k:5s} {t:15s} {sum(v for (kk, tt, _), v in counts.items() if kk == k and tt == t)}")


if __name__ == "__main__":
    main()
