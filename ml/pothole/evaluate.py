"""Build Plan M9: per-category precision / recall / F1 and mask mAP of a road-anomaly segmenter.

PRD Section 27.4 target: detection F1 >= 0.65 per anomaly category (pothole, crack, waterlogging,
debris). Two views of the same held-out split:

  ultralytics   model.val(): box and mask P/R/mAP50/mAP50-95 per class. Its P/R sit at the
                confidence that maximises mean F1 over the split (chosen on the test set itself),
                so its F1 is an upper bound.
  fixed_conf    the deployed operating point: predict at --conf, match predicted to ground-truth
                instance masks one-to-one (greedy by mask IoU >= --iou, same class), count TP/FP/FN.
                This F1 is the one checked against the target.

Categories the model was not trained on are reported as "no_data", never as a pass.
Load modified checkpoints (DSConv/SimAM) through this script: it imports modify_model first.

Usage:
    python ml/pothole/evaluate.py --weights ml/pothole/runs/m9_modified/weights/best.pt
    python ml/pothole/evaluate.py --weights ml/pothole/runs/baseline/weights/best.pt \\
        --data ml/pothole/data/grouped/data.yaml --split test --out ml/pothole/runs/m9_eval/baseline_2025.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import modify_model  # noqa: E402,F401  (registers DSConv/SimAM for unpickling)

CATEGORIES = ["pothole", "crack", "waterlogging", "debris"]  # schemas/event.schema.json anomaly.type
F1_TARGET = 0.65  # PRD Section 27.4
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r > 0 else 0.0


def gt_masks(label: Path, h: int, w: int) -> tuple[list[int], list[np.ndarray]]:
    """YOLO-seg label file -> (class ids, boolean masks at image size)."""
    cls, masks = [], []
    if not label.exists():
        return cls, masks
    for ln in label.read_text().splitlines():
        v = ln.split()
        if len(v) < 7:
            continue
        pts = (np.asarray(v[1:], float).reshape(-1, 2) * [w, h]).round().astype(np.int32)
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [pts], 1)
        cls.append(int(v[0]))
        masks.append(m.astype(bool))
    return cls, masks


def match(pred_cls, pred_masks, pred_conf, gt_cls, gt_m, iou_thr: float, nc: int) -> np.ndarray:
    """Greedy one-to-one matching per class -> (nc, 3) array of TP, FP, FN."""
    out = np.zeros((nc, 3), int)
    for c in range(nc):
        P = [i for i, k in enumerate(pred_cls) if k == c]
        G = [j for j, k in enumerate(gt_cls) if k == c]
        P.sort(key=lambda i: -pred_conf[i])
        used = set()
        for i in P:
            best, bj = 0.0, None
            for j in G:
                if j in used:
                    continue
                inter = np.logical_and(pred_masks[i], gt_m[j]).sum()
                if inter == 0:
                    continue
                iou = inter / np.logical_or(pred_masks[i], gt_m[j]).sum()
                if iou > best:
                    best, bj = iou, j
            if bj is not None and best >= iou_thr:
                used.add(bj)
                out[c, 0] += 1
            else:
                out[c, 1] += 1
        out[c, 2] += len(G) - len(used)
    return out


def split_images(data_yaml: Path, split: str) -> list[Path]:
    import yaml
    d = yaml.safe_load(data_yaml.read_text())
    root = Path(d.get("path", data_yaml.parent))
    src = Path(d[split])
    src = src if src.is_absolute() else root / src
    return sorted(p for p in src.iterdir() if p.suffix.lower() in IMG_EXT)


def fixed_conf_eval(model, images: list[Path], names: dict, conf: float, iou: float, imgsz: int, device) -> dict:
    nc = len(names)
    tally = np.zeros((nc, 3), int)
    for p in images:
        img = cv2.imread(str(p))
        h, w = img.shape[:2]
        r = model.predict(img, conf=conf, imgsz=imgsz, device=device, retina_masks=True, verbose=False)[0]
        if r.masks is not None and len(r.boxes):
            pm = (r.masks.data.cpu().numpy() > 0.5)
            pc = r.boxes.cls.cpu().numpy().astype(int).tolist()
            pf = r.boxes.conf.cpu().numpy().tolist()
        else:
            pm, pc, pf = [], [], []
        gc, gm = gt_masks(p.parent.parent / "labels" / (p.stem + ".txt"), h, w)
        tally += match(pc, pm, pf, gc, gm, iou, nc)
    out = {}
    for c in range(nc):
        tp, fp, fn = map(int, tally[c])
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        out[names[c]] = {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4),
                         "f1": round(f1(prec, rec), 4)}
    return out


def ultralytics_eval(model, data: Path, split: str, imgsz: int, device, project: Path, workers: int = 0) -> dict:
    # workers=0: DataLoader worker processes die on Windows (measured 2026-10-10, RTX 4060 box)
    m = model.val(data=str(data), split=split, imgsz=imgsz, device=device, batch=4, plots=False, workers=workers,
                  project=str(project), name="val", exist_ok=True, verbose=False)
    out = {}
    for k, idx in enumerate(m.ap_class_index):
        name = m.names[int(idx)]
        bp, br, b50, b5095 = m.box.class_result(k)
        mp, mr, m50, m5095 = m.seg.class_result(k)
        out[name] = {"box": {"precision": round(float(bp), 4), "recall": round(float(br), 4), "f1": round(f1(bp, br), 4),
                             "map50": round(float(b50), 4), "map50_95": round(float(b5095), 4)},
                     "mask": {"precision": round(float(mp), 4), "recall": round(float(mr), 4), "f1": round(f1(mp, mr), 4),
                              "map50": round(float(m50), 4), "map50_95": round(float(m5095), 4)}}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--data", default=str(HERE / "data" / "grouped" / "data.yaml"))
    ap.add_argument("--split", default="test")
    ap.add_argument("--conf", type=float, default=0.25, help="deployed confidence threshold")
    ap.add_argument("--iou", type=float, default=0.5, help="mask IoU for a true positive")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="0")
    ap.add_argument("--workers", type=int, default=0, help="val DataLoader workers (0 on Windows)")
    ap.add_argument("--out", default=None, help="JSON path (default: <run>/m9_eval_<split>.json)")
    a = ap.parse_args()

    from ultralytics import YOLO
    w = Path(a.weights)
    out_path = Path(a.out) if a.out else w.parent.parent / f"m9_eval_{a.split}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(w))
    names = model.names
    images = split_images(Path(a.data), a.split)
    t0 = time.time()
    ul = ultralytics_eval(model, Path(a.data), a.split, a.imgsz, a.device, out_path.parent / "m9_eval_tmp", a.workers)
    fc = fixed_conf_eval(model, images, names, a.conf, a.iou, a.imgsz, a.device)
    cats = {}
    for cat in CATEGORIES:
        if cat in fc:
            cats[cat] = {"status": "pass" if fc[cat]["f1"] >= F1_TARGET else "fail", "f1": fc[cat]["f1"]}
        else:
            cats[cat] = {"status": "no_data", "f1": None}
    res = {"weights": str(w), "data": a.data, "split": a.split, "n_images": len(images), "conf": a.conf,
           "iou": a.iou, "imgsz": a.imgsz, "m9_mods": (model.model.yaml or {}).get("m9_mods"),
           "f1_target": F1_TARGET, "categories": cats, "fixed_conf": fc, "ultralytics": ul,
           "eval_s": round(time.time() - t0, 1)}
    out_path.write_text(json.dumps(res, indent=1))
    print(json.dumps({"categories": cats, "fixed_conf": fc,
                      "mask_map50": {k: v["mask"]["map50"] for k, v in ul.items()}}, indent=1))
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
