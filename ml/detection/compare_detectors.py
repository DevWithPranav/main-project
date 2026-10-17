"""Detection-only comparison of two detectors on the same 3-class sets (Detector_Retraining_Plan.md
Section 7, criterion 3; also criterion 4 via the test split).

The old model has 10 VisDrone classes; its car + van are merged into car, bus -> bus, truck ->
truck, everything else dropped, then a per-class NMS (IoU 0.5) removes the car/van double boxes
the merge creates. The same NMS runs on every model, so both are scored identically.

Sets: retrain_v1 val, retrain_v1 test (also split by source), and the GT clip (gt_1080p, all
1,987 frames; frames without vehicles count as negatives).

Metrics: mAP50, mAP50-95, AP50 per class (Ultralytics ap_per_class), plus precision / recall at
the pipeline's operating point (conf 0.1, IoU 0.5).

    python ml/detection/compare_detectors.py                    # old vs new, each at 640
    python ml/detection/compare_detectors.py --imgsz 640 960    # several input sizes
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from torchvision.ops import batched_nms, box_iou
from ultralytics import YOLO
from ultralytics.utils.metrics import ap_per_class

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "ml" / "data" / "datasets" / "retrain_v1"
GT_CLIP = REPO_ROOT / "ml" / "data" / "eval" / "gt_1080p"
OUT_DIR = REPO_ROOT / "ml" / "data" / "results" / "retrain_v1" / "compare"
MODELS = {
    "old": REPO_ROOT / "ml" / "data" / "results" / "full_train" / "train" / "weights" / "best.pt",
    "new": REPO_ROOT / "ml" / "data" / "results" / "retrain_v1" / "train" / "weights" / "best.pt",
}
NAMES = {0: "car", 1: "bus", 2: "truck"}
BY_NAME = {"car": 0, "van": 0, "bus": 1, "truck": 2}  # model class name -> 3-class id (others dropped)
IOUV = np.linspace(0.5, 0.95, 10)
OP_CONF = 0.1  # pipeline detection confidence


def yolo_set(split: str) -> list[tuple[Path, np.ndarray, str]]:
    """(image, gt boxes [cls, x1, y1, x2, y2] in px, source) for a retrain_v1 split."""
    from PIL import Image
    items = []
    for img in sorted((DATASET / "images" / split).glob("*.jpg")):
        with Image.open(img) as im:
            w, h = im.size
        rows = []
        lbl = DATASET / "labels" / split / f"{img.stem}.txt"
        for line in lbl.read_text().split("\n") if lbl.exists() else []:
            p = line.split()
            if len(p) >= 5:
                c, cx, cy, bw, bh = int(p[0]), *(float(v) for v in p[1:5])
                rows.append([c, (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h])
        items.append((img, np.array(rows, dtype=float).reshape(-1, 5), img.stem.split("_")[0]))
    return items


def gt_clip_set() -> list[tuple[Path, np.ndarray, str]]:
    info = json.loads((GT_CLIP / "gt_info.json").read_text())
    frames_dir = REPO_ROOT / info["frames_dir"]
    by_frame = defaultdict(list)
    with (GT_CLIP / "gt.csv").open() as f:
        for r in csv.DictReader(f):
            cx, cy, w, h = (float(r[k]) for k in ("cx", "cy", "w", "h"))
            by_frame[int(r["frame"])].append([BY_NAME[r["class"]], cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
    return [(p, np.array(by_frame.get(int(p.stem), []), dtype=float).reshape(-1, 5), "gtclip")
            for p in sorted(frames_dir.glob("*.jpg"))]


def predict(model: YOLO, items: list, imgsz: int) -> list[np.ndarray]:
    """[cls, x1, y1, x2, y2, conf] per image, mapped to the 3 classes, per-class NMS at IoU 0.5."""
    cmap = {i: BY_NAME[n.lower()] for i, n in model.names.items() if n.lower() in BY_NAME}
    out = []
    paths = [str(i[0]) for i in items]
    # A list source is one batch to Ultralytics, so feed it in chunks
    results = (r for k in range(0, len(paths), 8)
               for r in model.predict(paths[k:k + 8], imgsz=imgsz, conf=0.001, max_det=300, verbose=False))
    for res in results:
        b = res.boxes
        cls = b.cls.int().tolist()
        keep = [k for k, c in enumerate(cls) if c in cmap]
        if not keep:
            out.append(np.zeros((0, 6)))
            continue
        xyxy, conf = b.xyxy[keep].float().cpu(), b.conf[keep].float().cpu()
        c3 = torch.tensor([cmap[cls[k]] for k in keep])
        idx = batched_nms(xyxy, conf, c3, 0.5)
        out.append(torch.cat([c3[idx, None].float(), xyxy[idx], conf[idx, None]], 1).numpy())
    return out


def match(gt: np.ndarray, pred: np.ndarray) -> np.ndarray:
    """tp matrix (n_pred x 10 IoU thresholds): greedy by confidence, same class, each GT used once."""
    tp = np.zeros((len(pred), len(IOUV)), bool)
    if len(gt) == 0 or len(pred) == 0:
        return tp
    iou = box_iou(torch.tensor(pred[:, 1:5]), torch.tensor(gt[:, 1:5])).numpy()
    iou[pred[:, None, 0] != gt[None, :, 0]] = 0
    order = np.argsort(-pred[:, 5])
    for t, thr in enumerate(IOUV):
        used = np.zeros(len(gt), bool)
        for i in order:
            cand = np.where((iou[i] >= thr) & ~used)[0]
            if len(cand):
                j = cand[np.argmax(iou[i, cand])]
                used[j] = tp[i, t] = True
    return tp


def score(items: list, preds: list) -> dict:
    tps, confs, pcls, tcls = [], [], [], []
    op_tp = op_np = 0
    for (_, gt, _), pr in zip(items, preds):
        tp = match(gt, pr)
        tps.append(tp); confs.append(pr[:, 5]); pcls.append(pr[:, 0]); tcls.append(gt[:, 0])
        sel = pr[:, 5] >= OP_CONF
        op_tp += int(match(gt, pr[sel])[:, 0].sum()); op_np += int(sel.sum())
    tp, conf, pc, tc = (np.concatenate(x) for x in (tps, confs, pcls, tcls))
    n_gt = len(tc)
    if n_gt == 0:
        return {}
    res = ap_per_class(tp, conf, pc, tc)
    ap, classes = res[5], res[6]
    return {"images": len(items), "gt_boxes": n_gt, "map50": round(float(ap[:, 0].mean()), 4),
            "map50_95": round(float(ap.mean()), 4),
            "ap50": {NAMES[int(c)]: round(float(ap[k, 0]), 4) for k, c in enumerate(classes)},
            f"P@{OP_CONF}": round(op_tp / max(op_np, 1), 4), f"R@{OP_CONF}": round(op_tp / n_gt, 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--imgsz", type=int, nargs="+", default=[640])
    ap.add_argument("--models", nargs="+", default=list(MODELS), choices=list(MODELS))
    args = ap.parse_args()

    sets = {"val": yolo_set("val"), "test": yolo_set("test"), "gt_clip": gt_clip_set()}
    results = {}
    for name in args.models:
        model = YOLO(str(MODELS[name]))
        for imgsz in args.imgsz:
            key = f"{name}@{imgsz}"
            results[key] = {}
            for set_name, items in sets.items():
                preds = predict(model, items, imgsz)
                results[key][set_name] = score(items, preds)
                for src in sorted({i[2] for i in items}) if set_name != "gt_clip" else []:
                    sel = [k for k, i in enumerate(items) if i[2] == src]
                    results[key][f"{set_name}/{src}"] = score([items[k] for k in sel], [preds[k] for k in sel])
                r = results[key][set_name]
                print(f"[compare] {key:9s} {set_name:7s} mAP50 {r['map50']:.4f}  mAP50-95 {r['map50_95']:.4f}  "
                      f"P {r[f'P@{OP_CONF}']:.3f} R {r[f'R@{OP_CONF}']:.3f}  {r['ap50']}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "compare.json").write_text(json.dumps(results, indent=2))
    print(f"\n{'model':9s} {'set':16s} {'mAP50':>6s} {'mAP50-95':>8s} {'car':>6s} {'bus':>6s} {'truck':>6s} "
          f"{'P':>6s} {'R':>6s}")
    for key, by_set in results.items():
        for set_name, r in by_set.items():
            if r:
                print(f"{key:9s} {set_name:16s} {r['map50']:6.3f} {r['map50_95']:8.3f} "
                      + " ".join(f"{r['ap50'].get(n, float('nan')):6.3f}" for n in NAMES.values())
                      + f" {r[f'P@{OP_CONF}']:6.3f} {r[f'R@{OP_CONF}']:6.3f}")
    print(f"-> {OUT_DIR / 'compare.json'}")


if __name__ == "__main__":
    main()
