"""Build the detector retrain dataset (Detector_Retraining_Plan.md, checklist step 5).

Converts VisDrone-DET, VisDrone-VID, UAVDT and the exported CARLA flights to one 3-class
YOLO dataset (0 car incl. van, 1 bus, 2 truck), samples per plan Section 3 and splits by
whole sequence per Section 6:

    train  VisDrone-DET train (all) + VisDrone-VID train (~3,000) + UAVDT train (~3,000, not M0207)
           + carla_det/train + carla_det/negatives
    val    VisDrone-DET val + VisDrone-VID val (every ~10th frame) + 5 UAVDT test sequences
           (~60 frames each, day/night/fog) + carla_det/val
    test   VisDrone-DET test-dev + carla_det/test

Ignore regions (VisDrone category 0 / score 0, UAVDT gt_ignore.txt) are filled with grey
(114,114,114) so unlabelled vehicles inside them aren't learned as background; frames more than
half covered by ignore regions are skipped. Images that need no change are hard-linked (no extra
disk on the same drive); changed or > 1920 px images are re-encoded (JPEG q95).

Output: ml/data/datasets/retrain_v1/{images,labels}/{train,val,test}/, data.yaml, manifest.csv.

    python ml/detection/build_retrain_dataset.py            # build (refuses to overwrite)
    python ml/detection/build_retrain_dataset.py --force    # rebuild from scratch
    python ml/detection/build_retrain_dataset.py --dry-run  # sampling counts only, nothing written
"""

import argparse
import csv
import os
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT.parent  # D:\Main-Project
RAW = DATA_ROOT / "project data"
DET_TRAIN = RAW / "VisDrone2019-DET-train (1)" / "VisDrone2019-DET-train"
VID_TRAIN = RAW / "VisDrone2019-VID-train" / "VisDrone2019-VID-train"
VID_VAL = RAW / "VisDrone2019-VID-val" / "VisDrone2019-VID-val"
UAVDT_FRAMES = RAW / "UAV-benchmark-M" / "UAV-benchmark-M"
UAVDT_GT = RAW / "UAV-benchmark-M" / "UAV-benchmark-MOTD_v1.0" / "GT"
UAVDT_ATTR = RAW / "UAV-benchmark-M" / "M_attr" / "M_attr"
VISDRONE_YOLO = DATA_ROOT / "datasets" / "VisDrone"  # Ultralytics copy: DET val / test-dev, 10-class YOLO labels
CARLA_DET = REPO_ROOT / "ml" / "data" / "datasets" / "carla_det"
OUT_DIR = REPO_ROOT / "ml" / "data" / "datasets" / "retrain_v1"
UAVDT_OVERRIDES = Path(__file__).resolve().parent / "uavdt_class_overrides.csv"

NAMES = {0: "car", 1: "bus", 2: "truck"}
VISDRONE_RAW = {4: 0, 5: 0, 9: 1, 6: 2}       # raw annotations: car, van -> car; bus; truck
VISDRONE_YOLO_MAP = {3: 0, 4: 0, 8: 1, 5: 2}  # Ultralytics VisDrone.yaml ids: car, van, bus, truck
UAVDT_MAP = {1: 0, 3: 1, 2: 2}                # UAVDT 1 car, 2 truck, 3 bus
UAVDT_EXCLUDE = {"M0207"}                     # labels missing on most vehicles (plan Section 2)
GREY = (114, 114, 114)
MAX_SIDE = 1920
MAX_IGNORE_FRAC = 0.5

VID_TRAIN_TARGET, UAVDT_TRAIN_TARGET, PER_SEQ_CAP = 3000, 3000, 120
VID_VAL_STRIDE, UAVDT_VAL_SEQS, UAVDT_VAL_PER_SEQ = 10, 5, 60


@dataclass
class Item:
    split: str
    source: str
    sequence: str
    frame: str
    image: Path
    boxes: list[tuple[int, float, float, float, float]] = field(default_factory=list)  # cls, x, y, w, h (px)
    ignore: list[tuple[float, float, float, float]] = field(default_factory=list)      # x, y, w, h (px)
    yolo_labels: Path | None = None  # already-normalised YOLO label file (CARLA, Ultralytics VisDrone)
    yolo_map: dict[int, int] | None = None

    @property
    def name(self) -> str:
        return f"{self.source}_{self.sequence}_{self.frame}".replace(" ", "")


# ------------------------------------------------------------------------------ sampling
def pick_frames(frames: list[int], bus_truck: dict[int, int], k: int) -> list[int]:
    """k evenly spaced frames; inside each window prefer the frame with the most bus/truck boxes."""
    if k >= len(frames):
        return frames
    picked = []
    for i in range(k):
        window = frames[round(i * len(frames) / k):round((i + 1) * len(frames) / k)]
        mid = window[len(window) // 2]
        picked.append(max(window, key=lambda f: (bus_truck.get(f, 0), -abs(f - mid))))
    return picked


def per_sequence_counts(lengths: dict[str, int], target: int, cap: int) -> dict[str, int]:
    """Uniform stride across sequences (capped per sequence) so the total is close to target."""
    lo, hi = 1.0, float(max(lengths.values()))
    for _ in range(50):
        stride = (lo + hi) / 2
        total = sum(min(cap, int(n / stride)) for n in lengths.values())
        lo, hi = (stride, hi) if total > target else (lo, stride)
    return {s: max(1, min(cap, int(n / hi))) for s, n in lengths.items()}


# ------------------------------------------------------------------------------ VisDrone
def read_visdrone(path: Path, video: bool) -> dict[int, tuple[list, list]]:
    """{frame: (boxes, ignore)} from a VisDrone DET (one image) or VID (one sequence) annotation file."""
    out: dict[int, tuple[list, list]] = {}
    for line in path.read_text().splitlines():
        v = [int(x) for x in line.strip().strip(",").split(",")[:10] if x != ""]
        if not v:
            continue
        if video:
            frame, (x, y, w, h, score, cat) = v[0], v[2:8]
        else:
            frame, (x, y, w, h, score, cat) = 0, v[0:6]
        boxes, ignore = out.setdefault(frame, ([], []))
        if cat == 0 or (score == 0 and cat in VISDRONE_RAW):
            ignore.append((x, y, w, h))
        elif cat in VISDRONE_RAW:
            boxes.append((VISDRONE_RAW[cat], x, y, w, h))
    return out


def visdrone_det_train() -> list[Item]:
    items = []
    for ann in sorted((DET_TRAIN / "annotations").glob("*.txt")):
        boxes, ignore = read_visdrone(ann, video=False).get(0, ([], []))
        items.append(Item("train", "vddet", "det", ann.stem, DET_TRAIN / "images" / f"{ann.stem}.jpg", boxes, ignore))
    return items


def visdrone_vid(root: Path, split: str, target: int | None, stride: int | None) -> list[Item]:
    seqs = {}
    for ann in sorted((root / "annotations").glob("*.txt")):
        if (root / "sequences" / ann.stem).is_dir():
            seqs[ann.stem] = read_visdrone(ann, video=True)
    if target:
        counts = per_sequence_counts({s: len(f) for s, f in seqs.items()}, target, PER_SEQ_CAP)
    items = []
    for seq, frames in seqs.items():
        order = sorted(frames)
        if target:
            bt = {f: sum(1 for b in frames[f][0] if b[0] != 0) for f in order}
            order = pick_frames(order, bt, counts[seq])
        else:
            order = order[::stride]
        for f in order:
            boxes, ignore = frames[f]
            items.append(Item(split, "vdvid", seq, f"{f:07d}", root / "sequences" / seq / f"{f:07d}.jpg", boxes, ignore))
    return items


def visdrone_yolo(split_dir: str, split: str) -> list[Item]:
    img_dir, lbl_dir = VISDRONE_YOLO / "images" / split_dir, VISDRONE_YOLO / "labels" / split_dir
    return [Item(split, "vddet", split_dir, p.stem, p, yolo_labels=lbl_dir / f"{p.stem}.txt", yolo_map=VISDRONE_YOLO_MAP)
            for p in sorted(img_dir.glob("*.jpg"))]


# ------------------------------------------------------------------------------ UAVDT
def uavdt_seq_lists() -> tuple[list[str], list[str]]:
    def names(d: str) -> list[str]:
        return sorted(p.name.replace(" ", "").split("_")[0] for p in (UAVDT_ATTR / d).glob("*_attr.txt"))
    return names("train"), names("test")


def uavdt_attrs(seq: str) -> list[int]:
    path = next(p for d in ("train", "test") for p in (UAVDT_ATTR / d).glob("*_attr.txt")
                if p.name.replace(" ", "").startswith(seq + "_"))
    return [int(x) for x in path.read_text().strip().split(",")]  # day, night, fog; low, med, high alt; ...


def uavdt_overrides() -> dict[tuple[str, int], int]:
    """Per-track class fixes (pickups / vans labelled truck, minibus-like vans labelled bus -> car)."""
    if not UAVDT_OVERRIDES.exists():
        return {}
    with UAVDT_OVERRIDES.open() as f:
        return {(r["sequence"], int(r["track_id"])): {v: k for k, v in NAMES.items()}[r["class"]]
                for r in csv.DictReader(f)}


def read_uavdt(seq: str, overrides: dict) -> dict[int, tuple[list, list]]:
    out: dict[int, tuple[list, list]] = {}
    for line in (UAVDT_GT / f"{seq}_gt_whole.txt").read_text().splitlines():
        frame, tid, x, y, w, h, _oov, _occ, cat = (int(v) for v in line.split(",")[:9])
        if cat in UAVDT_MAP:
            out.setdefault(frame, ([], []))[0].append((overrides.get((seq, tid), UAVDT_MAP[cat]), x, y, w, h))
    ign = UAVDT_GT / f"{seq}_gt_ignore.txt"
    if ign.exists():
        for line in ign.read_text().splitlines():
            frame, _tid, x, y, w, h = (int(v) for v in line.split(",")[:6])
            out.setdefault(frame, ([], []))[1].append((x, y, w, h))
    return out


def uavdt(seqs: list[str], split: str, target: int | None, per_seq: int | None) -> list[Item]:
    overrides = uavdt_overrides()
    data = {s: read_uavdt(s, overrides) for s in seqs}
    counts = (per_sequence_counts({s: len(f) for s, f in data.items()}, target, PER_SEQ_CAP) if target
              else {s: per_seq for s in seqs})
    items = []
    for seq, frames in data.items():
        order = sorted(frames)
        bt = {f: sum(1 for b in frames[f][0] if b[0] != 0) for f in order}
        for f in pick_frames(order, bt, counts[seq]):
            boxes, ignore = frames[f]
            items.append(Item(split, "uavdt", seq, f"{f:06d}", UAVDT_FRAMES / seq / f"img{f:06d}.jpg", boxes, ignore))
    return items


def uavdt_val_sequences(test_seqs: list[str]) -> list[str]:
    """5 test sequences covering daylight, night and fog, spread over altitudes."""
    attrs = {s: uavdt_attrs(s) for s in test_seqs}
    plan = [(0, 3), (0, 4), (1, 3), (1, 5), (2, 4)]  # (light index, altitude index): day low/med, night low/high, fog med
    chosen = []
    for light, alt in plan:
        cands = [s for s in test_seqs if s not in chosen and attrs[s][light]]
        cands.sort(key=lambda s: (not attrs[s][alt], s))
        if cands:
            chosen.append(cands[0])
    return chosen[:UAVDT_VAL_SEQS]


# ------------------------------------------------------------------------------ CARLA
def carla(split_dir: str, split: str) -> list[Item]:
    items = []
    for flight in sorted(p for p in (CARLA_DET / split_dir).iterdir() if p.is_dir()):
        for img in sorted((flight / "images").glob("*.jpg")):
            items.append(Item(split, "carla", flight.name, img.stem, img,
                              yolo_labels=flight / "labels" / f"{img.stem}.txt"))
    return items


# ------------------------------------------------------------------------------ writing
def ignore_fraction(ignore: list, w: int, h: int) -> float:
    if not ignore:
        return 0.0
    mask = np.zeros((h, w), np.uint8)
    for x, y, bw, bh in ignore:
        mask[max(0, int(y)):int(y + bh), max(0, int(x)):int(x + bw)] = 1
    return float(mask.mean())


def yolo_lines(item: Item, w: int, h: int) -> list[str]:
    if item.yolo_labels is not None:
        lines = []
        if item.yolo_labels.exists():
            for line in item.yolo_labels.read_text().split("\n"):
                parts = line.split()
                if len(parts) < 5:
                    continue
                cls = int(parts[0])
                if item.yolo_map is not None:
                    if cls not in item.yolo_map:
                        continue
                    cls = item.yolo_map[cls]
                lines.append(f"{cls} {' '.join(parts[1:5])}")
        return lines
    lines = []
    for cls, x, y, bw, bh in item.boxes:
        x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + bw), min(h, y + bh)
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        lines.append(f"{cls} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}")
    return lines


def write_item(item: Item) -> dict | None:
    if not item.image.exists():
        return None
    with Image.open(item.image) as im:
        w, h = im.size
    if ignore_fraction(item.ignore, w, h) > MAX_IGNORE_FRAC:
        return None
    lines = yolo_lines(item, w, h)
    img_out = OUT_DIR / "images" / item.split / f"{item.name}.jpg"
    lbl_out = OUT_DIR / "labels" / item.split / f"{item.name}.txt"
    if item.ignore or max(w, h) > MAX_SIDE:
        img = cv2.imread(str(item.image))
        for x, y, bw, bh in item.ignore:
            cv2.rectangle(img, (int(x), int(y)), (int(x + bw), int(y + bh)), GREY, -1)
        if max(w, h) > MAX_SIDE:
            s = MAX_SIDE / max(w, h)
            img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(img_out), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        mode = "rewritten"
    else:
        try:
            os.link(item.image, img_out)
            mode = "hardlink"
        except OSError:
            shutil.copy2(item.image, img_out)
            mode = "copy"
    lbl_out.write_text("\n".join(lines) + ("\n" if lines else ""))
    c = Counter(int(l.split()[0]) for l in lines)
    return {"split": item.split, "source": item.source, "sequence": item.sequence, "frame": item.frame,
            "image": img_out.relative_to(OUT_DIR).as_posix(), "n_car": c[0], "n_bus": c[1], "n_truck": c[2],
            "n_ignore": len(item.ignore), "mode": mode, "src_image": str(item.image)}


def collect() -> list[Item]:
    uavdt_train, uavdt_test = uavdt_seq_lists()
    val_seqs = uavdt_val_sequences(uavdt_test)
    print(f"[build] UAVDT train sequences: {len(uavdt_train)} (minus {sorted(UAVDT_EXCLUDE)}), val: {val_seqs}")
    return [
        *visdrone_det_train(),
        *visdrone_vid(VID_TRAIN, "train", VID_TRAIN_TARGET, None),
        *uavdt([s for s in uavdt_train if s not in UAVDT_EXCLUDE], "train", UAVDT_TRAIN_TARGET, None),
        *carla("train", "train"),
        *carla("negatives", "train"),
        *visdrone_yolo("val", "val"),
        *visdrone_vid(VID_VAL, "val", None, VID_VAL_STRIDE),
        *uavdt(val_seqs, "val", None, UAVDT_VAL_PER_SEQ),
        *carla("val", "val"),
        *visdrone_yolo("test", "test"),
        *carla("test", "test"),
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Delete an existing retrain_v1 and rebuild")
    ap.add_argument("--dry-run", action="store_true", help="Print the sampled counts only")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    items = collect()
    plan = Counter((i.split, i.source) for i in items)
    for (split, source), n in sorted(plan.items()):
        print(f"[build] {split:5s} {source:6s} {n:6d} candidate frames")
    if args.dry_run:
        return

    if OUT_DIR.exists():
        if not args.force:
            raise SystemExit(f"{OUT_DIR} exists; pass --force to rebuild")
        shutil.rmtree(OUT_DIR)
    for sub in ("images", "labels"):
        for split in ("train", "val", "test"):
            (OUT_DIR / sub / split).mkdir(parents=True, exist_ok=True)

    rows = []
    with ThreadPoolExecutor(args.workers) as pool:
        for i, row in enumerate(pool.map(write_item, items), 1):
            if row:
                rows.append(row)
            if i % 2000 == 0:
                print(f"[build] {i}/{len(items)} frames", flush=True)

    with (OUT_DIR / "manifest.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (OUT_DIR / "data.yaml").write_text(
        "# Detector retrain v1 (Detector_Retraining_Plan.md); built by ml/detection/build_retrain_dataset.py\n"
        f"path: {OUT_DIR.as_posix()}\ntrain: images/train\nval: images/val\ntest: images/test\n"
        "names:\n" + "".join(f"  {k}: {v}\n" for k, v in NAMES.items()))

    print(f"\n[build] skipped {len(items) - len(rows)} frames (missing image or > {MAX_IGNORE_FRAC:.0%} ignore region)")
    print(f"{'split':5s} {'source':6s} {'images':>7s} {'car':>8s} {'bus':>6s} {'truck':>7s} {'empty':>6s}")
    for key in sorted({(r['split'], r['source']) for r in rows}):
        sel = [r for r in rows if (r["split"], r["source"]) == key]
        print(f"{key[0]:5s} {key[1]:6s} {len(sel):7d} {sum(r['n_car'] for r in sel):8d} "
              f"{sum(r['n_bus'] for r in sel):6d} {sum(r['n_truck'] for r in sel):7d} "
              f"{sum(1 for r in sel if r['n_car'] + r['n_bus'] + r['n_truck'] == 0):6d}")
    print(f"[build] -> {OUT_DIR}")


if __name__ == "__main__":
    main()
