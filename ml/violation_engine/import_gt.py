"""Import a ground-truth labelling export into this project's trajectory CSV schema
and sanity-check it (Improvement Plan, W4 / Section 7-E).

Accepted inputs (format auto-detected):
  - CVAT "MOT 1.1" export: the .zip, the extracted folder, or gt/gt.txt directly
    (gt.txt rows: frame, id, x, y, w, h, not_ignored, class_id, visibility;
    class_id indexes gt/labels.txt, 1-based; frames are 1-based by default)
  - CVAT "CVAT for video 1.1" XML export (annotations.xml, or the .zip); frames 0-based
  - a CSV already in our schema (frame, track_id, class, cx, cy, w, h, ...)

Output (default: next to the input, or --out):
  gt.csv         frame, time_s, track_id, class, cx, cy, w, h, conf(=1) — frames 0-based,
                 matching the pipeline's frame numbering; classes mapped to car/bus/truck
  gt_ignore.csv  "ignore" boxes/regions (MOT not_ignored=0, or an `ignore` label), if any

Classes: the 3-class scheme agreed in v3 of the plan. Vans, minivans, SUVs and
pickups are `car`; minibuses are `bus`. Motorcycles, bicycles and pedestrians are
not tracked by the pipeline and are dropped (counted in the report). Any other
label stops the import so it can be mapped explicitly.

Usage:
    python ml/violation_engine/import_gt.py ml/data/eval/gt_1080p/export.zip --video ml/data/eval/gt_1080p/clip.mp4
    python ml/violation_engine/import_gt.py gt.txt --labels labels.txt --fps 30 --frame-base 1
"""

import argparse
import csv
import io
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

GT_CLASS_MAP = {
    "car": "car", "van": "car", "minivan": "car", "suv": "car", "pickup": "car", "pickup truck": "car",
    "bus": "bus", "minibus": "bus",
    "truck": "truck", "lorry": "truck",
}
NOT_TRACKED = {"motorcycle", "motor", "motorbike", "bicycle", "bike", "pedestrian", "person", "people", "tricycle",
               "awning-tricycle"}
IGNORE_LABELS = {"ignore", "ignored", "ignore region", "dontcare"}
OUT_COLUMNS = ["frame", "time_s", "track_id", "class", "cx", "cy", "w", "h", "conf"]


class Box(dict):
    """frame (0-based), track_id, label (raw), x, y (top-left), w, h, ignore"""


def normalise_label(label: str) -> str:
    return label.strip().lower().replace("_", " ")


# ---------------------------------------------------------------- readers

def read_text(path: Path, member_suffix: str) -> tuple[str, dict[str, str]]:
    """Main file text + sibling files (e.g. labels.txt) from a path, folder or zip."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            files = {n: z.read(n).decode("utf-8") for n in z.namelist() if not n.endswith("/")}
        main = next((n for n in files if n.endswith(member_suffix)), None)
        if main is None:
            raise SystemExit(f"No *{member_suffix} inside {path}")
        return files[main], files
    if path.is_dir():
        main = next(iter(sorted(path.rglob(f"*{member_suffix}"))), None)
        if main is None:
            raise SystemExit(f"No *{member_suffix} under {path}")
        return main.read_text(encoding="utf-8"), {str(p.relative_to(path)).replace("\\", "/"): p.read_text(encoding="utf-8")
                                                  for p in path.rglob("*.txt")}
    return path.read_text(encoding="utf-8"), {}


def detect_format(path: Path) -> str:
    if path.suffix == ".zip":
        names = zipfile.ZipFile(path).namelist()
        if any(n.endswith("gt.txt") for n in names):
            return "mot"
        if any(n.endswith(".xml") for n in names):
            return "cvat_xml"
    if path.is_dir():
        if any(path.rglob("gt.txt")):
            return "mot"
        if any(path.rglob("*.xml")):
            return "cvat_xml"
    if path.suffix == ".xml":
        return "cvat_xml"
    if path.suffix == ".csv":
        return "csv"
    if path.suffix == ".txt":
        return "mot"
    raise SystemExit(f"Can't tell the format of {path}; expected a MOT 1.1 / CVAT XML export or our CSV")


def read_mot(path: Path, labels_path: Path | None, frame_base: int) -> list[Box]:
    text, siblings = read_text(path, "gt.txt")
    if labels_path:
        labels = labels_path.read_text(encoding="utf-8").splitlines()
    else:
        lab = next((v for k, v in siblings.items() if k.endswith("labels.txt")), None)
        labels = lab.splitlines() if lab else []
    labels = [l.strip() for l in labels if l.strip()]
    boxes = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip():
            continue
        frame, tid, x, y, w, h = int(float(row[0])), int(float(row[1])), *map(float, row[2:6])
        not_ignored = int(float(row[6])) if len(row) > 6 else 1
        class_id = int(float(row[7])) if len(row) > 7 else -1
        if labels and 1 <= class_id <= len(labels):
            label = labels[class_id - 1]
        elif not labels and class_id == -1:
            label = "car"  # plain MOT without classes
        else:
            raise SystemExit(f"class_id {class_id} has no entry in labels.txt ({labels}) — pass --labels")
        boxes.append(Box(frame=frame - frame_base, track_id=tid, label=label, x=x, y=y, w=w, h=h,
                         ignore=not_ignored == 0))
    return boxes


def read_cvat_xml(path: Path, frame_base: int) -> list[Box]:
    text, _ = read_text(path, ".xml")
    root = ET.fromstring(text)
    tracks = root.findall("track")
    if not tracks and root.findall("image"):
        raise SystemExit("This is a 'CVAT for images' export (no track IDs). Export as 'CVAT for video 1.1' or MOT 1.1.")
    boxes = []
    for t in tracks:
        tid, label = int(t.get("id")), t.get("label")
        for b in t.findall("box"):
            if b.get("outside") == "1":
                continue
            x0, y0, x1, y1 = (float(b.get(k)) for k in ("xtl", "ytl", "xbr", "ybr"))
            boxes.append(Box(frame=int(b.get("frame")) - frame_base, track_id=tid, label=label,
                             x=x0, y=y0, w=x1 - x0, h=y1 - y0, ignore=False))
    return boxes


def read_csv(path: Path, frame_base: int) -> list[Box]:
    boxes = []
    for r in csv.DictReader(open(path, newline="")):
        cx, cy, w, h = (float(r[k]) for k in ("cx", "cy", "w", "h"))
        boxes.append(Box(frame=int(r["frame"]) - frame_base, track_id=int(r["track_id"]), label=r.get("class", "car"),
                         x=cx - w / 2, y=cy - h / 2, w=w, h=h, ignore=False))
    return boxes


# ---------------------------------------------------------------- mapping + checks

def map_classes(boxes: list[Box]) -> tuple[list[Box], list[Box], Counter]:
    kept, ignore, dropped, unknown = [], [], Counter(), set()
    for b in boxes:
        label = normalise_label(b["label"])
        if b["ignore"] or label in IGNORE_LABELS:
            ignore.append(b)
        elif label in GT_CLASS_MAP:
            kept.append(Box(b, cls=GT_CLASS_MAP[label]))
        elif label in NOT_TRACKED:
            dropped[label] += 1
        else:
            unknown.add(b["label"])
    if unknown:
        raise SystemExit(f"Unknown labels {sorted(unknown)} — add them to GT_CLASS_MAP or NOT_TRACKED in import_gt.py")
    return kept, ignore, dropped


def video_info(video: Path) -> tuple[int, float, int, int]:
    import cv2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"Can't open {video}")
    info = (int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS),
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return info


def sanity_check(boxes: list[Box], n_frames: int | None, width: int | None, height: int | None) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings)."""
    errors, warnings = [], []
    if not boxes:
        return ["no vehicle boxes after class mapping"], warnings

    frames = sorted({b["frame"] for b in boxes})
    if frames[0] < 0:
        errors.append(f"negative frame index {frames[0]} — wrong --frame-base?")
    dup = Counter((b["frame"], b["track_id"]) for b in boxes)
    dups = [k for k, n in dup.items() if n > 1]
    if dups:
        errors.append(f"{len(dups)} (frame, track_id) pairs appear more than once, e.g. {dups[:3]}")

    last = n_frames - 1 if n_frames else frames[-1]
    empty = sorted(set(range(0, last + 1)) - set(frames))
    if n_frames and frames[-1] >= n_frames:
        errors.append(f"labels go up to frame {frames[-1]} but the video has {n_frames} frames (0..{n_frames - 1}) "
                      "— wrong --frame-base, or labels drawn on a different file?")
    if empty:
        warnings.append(f"{len(empty)} frames without any box (first few: {empty[:10]}) — "
                        "fine if no vehicle is visible, otherwise frames were skipped")

    by_track = defaultdict(list)
    for b in boxes:
        by_track[b["track_id"]].append(b)
    multi_class = {tid: sorted({b["cls"] for b in bs}) for tid, bs in by_track.items() if len({b["cls"] for b in bs}) > 1}
    if multi_class:
        warnings.append(f"{len(multi_class)} IDs change class over time, e.g. {list(multi_class.items())[:3]} — "
                        "one physical vehicle should keep one class")
    gappy = {}
    for tid, bs in by_track.items():
        fs = sorted(b["frame"] for b in bs)
        gaps = [(a, b) for a, b in zip(fs, fs[1:]) if b - a > 1]
        if gaps:
            gappy[tid] = gaps
    if gappy:
        warnings.append(f"{len(gappy)} IDs have gaps (vehicle left and re-entered, or missed frames), "
                        f"e.g. ID {next(iter(gappy))}: {gappy[next(iter(gappy))][:3]} — check these are real exits")
    if width and height:
        outside = [b for b in boxes if b["x"] + b["w"] < 0 or b["y"] + b["h"] < 0 or b["x"] > width or b["y"] > height]
        if outside:
            errors.append(f"{len(outside)} boxes lie completely outside the {width}x{height} frame — labels drawn on a "
                          "resized copy?")
        if max(b["x"] + b["w"] for b in boxes) < 0.6 * width and max(b["y"] + b["h"] for b in boxes) < 0.6 * height:
            warnings.append(f"all boxes fall in the top-left part of the {width}x{height} frame — labels drawn on a "
                            "downscaled copy?")
    tiny = [b for b in boxes if b["w"] < 2 or b["h"] < 2]
    if tiny:
        warnings.append(f"{len(tiny)} boxes smaller than 2 px")
    return errors, warnings


def write_csv(path: Path, boxes: list[Box], fps: float, cls_key: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(OUT_COLUMNS)
        for b in sorted(boxes, key=lambda b: (b["frame"], b["track_id"])):
            w.writerow([b["frame"], round(b["frame"] / fps, 3), b["track_id"], b.get(cls_key, b["label"]),
                        round(b["x"] + b["w"] / 2, 1), round(b["y"] + b["h"] / 2, 1),
                        round(b["w"], 1), round(b["h"], 1), 1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export", type=Path, help="MOT 1.1 zip/folder/gt.txt, CVAT video XML (or zip), or our CSV schema")
    ap.add_argument("--video", type=Path, default=None,
                    help="The exact video the labels were drawn on (gives fps, frame count and size for the checks)")
    ap.add_argument("--fps", type=float, default=None, help="Video fps (default: from --video)")
    ap.add_argument("--labels", type=Path, default=None, help="MOT labels.txt, if not inside the export")
    ap.add_argument("--frame-base", type=int, default=None,
                    help="First frame number in the export (default: 1 for MOT, 0 for CVAT XML and CSV)")
    ap.add_argument("--out", type=Path, default=None, help="Output folder (default: next to the export)")
    args = ap.parse_args()

    if not args.export.exists():
        raise SystemExit(f"{args.export} not found.")
    fmt = detect_format(args.export)
    frame_base = args.frame_base if args.frame_base is not None else (1 if fmt == "mot" else 0)

    n_frames = width = height = None
    fps = args.fps
    if args.video:
        n_frames, video_fps, width, height = video_info(args.video)
        fps = fps or video_fps
        print(f"[video] {args.video.name}: {n_frames} frames, {video_fps:.2f} fps, {width}x{height}")
    if not fps:
        raise SystemExit("Pass --video or --fps.")

    if fmt == "mot":
        raw = read_mot(args.export, args.labels, frame_base)
    elif fmt == "cvat_xml":
        raw = read_cvat_xml(args.export, frame_base)
    else:
        raw = read_csv(args.export, frame_base)
    boxes, ignore, dropped = map_classes(raw)

    errors, warnings = sanity_check(boxes, n_frames, width, height)
    frames = sorted({b["frame"] for b in boxes})
    ids = {b["track_id"] for b in boxes}
    per_class = Counter()
    for tid in ids:
        per_class[Counter(b["cls"] for b in boxes if b["track_id"] == tid).most_common(1)[0][0]] += 1
    print(f"[gt] format {fmt}, frame base {frame_base}: {len(boxes)} vehicle boxes, {len(ids)} vehicle IDs "
          f"({dict(per_class)}), frames {frames[0] if frames else '-'}..{frames[-1] if frames else '-'}, "
          f"{len(boxes) / max(len(frames), 1):.1f} boxes/labelled frame")
    if dropped:
        print(f"[gt] dropped (not tracked by the pipeline): {dict(dropped)}")
    if ignore:
        print(f"[gt] {len(ignore)} ignore boxes/regions")
    for w in warnings:
        print(f"[warning] {w}")
    for e in errors:
        print(f"[ERROR] {e}")
    if errors:
        raise SystemExit("Not written — fix the errors above (or the options) first.")

    out_dir = args.out or (args.export if args.export.is_dir() else args.export.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "gt.csv", boxes, fps, "cls")
    print(f"[gt] wrote {out_dir / 'gt.csv'}")
    if ignore:
        write_csv(out_dir / "gt_ignore.csv", ignore, fps, "label")
        print(f"[gt] wrote {out_dir / 'gt_ignore.csv'}")


if __name__ == "__main__":
    main()
