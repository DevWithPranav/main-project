"""Detection metrics report (PRD 27.1): mAP@0.5, mAP@0.5:0.95, AP per class (at both), precision / recall, inference time.

Runs the detector's own evaluation on the retrain_v1 val and test splits and writes one JSON and one Markdown file with
each number next to its PRD target. Numbers are measured here (this PC, this GPU); nothing is copied from papers.

Classes are the three the project trains (car incl. van, bus, truck): the PRD's "motorcycle" is not trained or scored.

Usage:
    venv/Scripts/python.exe ml/detection/detection_report.py [--weights <best.pt>] [--imgsz 960] [--splits val test]
"""

import argparse
import json
import platform
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_WEIGHTS = REPO / "ml" / "data" / "results" / "retrain_v1" / "train" / "weights" / "best.pt"
DATA_YAML = REPO / "ml" / "data" / "datasets" / "retrain_v1" / "data.yaml"
OUT = REPO / "ml" / "data" / "results" / "retrain_v1" / "detection_report"

TARGETS = {"map50": 0.75, "map50_95": 0.55, "ap_per_class": 0.70, "inference_ms": 100.0}


def local_yaml() -> Path:
    """data.yaml with `path:` pointing at this PC's copy (the stored one names the training PC's D: drive); the dataset file is not touched."""
    lines = [f"path: {DATA_YAML.parent.as_posix()}" if ln.startswith("path:") else ln for ln in DATA_YAML.read_text().splitlines()]
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "data_local.yaml"
    out.write_text(chr(10).join(lines) + chr(10))
    return out


def evaluate(weights: Path, split: str, imgsz: int, batch: int) -> dict:
    from ultralytics import YOLO
    m = YOLO(str(weights)).val(data=str(local_yaml()), split=split, imgsz=imgsz, batch=batch, plots=False, verbose=False,
                               project=str(OUT / "ultralytics"), name=split, exist_ok=True)
    per = {}
    for i, c in enumerate(m.box.ap_class_index):
        per[m.names[int(c)]] = {"ap50": round(float(m.box.ap50[i]), 4), "ap50_95": round(float(m.box.ap[i]), 4)}
    sp = m.speed  # ms per image: preprocess / inference / postprocess
    return {"split": split, "images": sum(1 for _ in (DATA_YAML.parent / "images" / split).iterdir()), "map50": round(float(m.box.map50), 4), "map50_95": round(float(m.box.map), 4),
            "precision": round(float(m.box.mp), 4), "recall": round(float(m.box.mr), 4), "per_class": per,
            "ms_per_image": {k: round(float(v), 2) for k, v in sp.items()},
            "ms_total": round(float(sum(sp.values())), 2)}


def verdict(ok: bool) -> str:
    return "met" if ok else "NOT met"


def markdown(rep: dict) -> str:
    L = [f"# Detection metrics report (PRD 27.1)", "",
         f"Measured {rep['date']} on {rep['machine']}; weights `{rep['weights']}`, imgsz {rep['imgsz']}, dataset `retrain_v1` "
         f"(3 classes: car incl. van, bus, truck; PRD's motorcycle class is not trained).", "",
         "| Metric | Target | " + " | ".join(f"{r['split']} ({r['images']} images)" for r in rep["results"]) + " |",
         "|---|---|" + "---|" * len(rep["results"])]
    L.append(f"| mAP@0.5 | >= {TARGETS['map50']} | " + " | ".join(f"{r['map50']} ({verdict(r['map50'] >= TARGETS['map50'])})" for r in rep["results"]) + " |")
    L.append(f"| mAP@0.5:0.95 | >= {TARGETS['map50_95']} | " + " | ".join(f"{r['map50_95']} ({verdict(r['map50_95'] >= TARGETS['map50_95'])})" for r in rep["results"]) + " |")
    L.append("| Precision / recall | - | " + " | ".join(f"{r['precision']} / {r['recall']}" for r in rep["results"]) + " |")
    for cls in rep["results"][0]["per_class"]:
        L.append(f"| AP@0.5 {cls} | >= {TARGETS['ap_per_class']} | " + " | ".join(
            f"{r['per_class'][cls]['ap50']} ({verdict(r['per_class'][cls]['ap50'] >= TARGETS['ap_per_class'])})" for r in rep["results"]) + " |")
    for cls in rep["results"][0]["per_class"]:
        L.append(f"| AP@0.5:0.95 {cls} | (no PRD target) | " + " | ".join(f"{r['per_class'][cls]['ap50_95']}" for r in rep["results"]) + " |")
    L.append(f"| Inference time per image (ms) | <= {TARGETS['inference_ms']:.0f} | " + " | ".join(
        f"{r['ms_total']} ({verdict(r['ms_total'] <= TARGETS['inference_ms'])})" for r in rep["results"]) + " |")
    L += ["", "Inference time is the validation loop's preprocess + inference + postprocess per image at the batch size used; the live "
          "pipeline's end-to-end ms per frame (with tracking and rules) is in `docs/Build_Plan.md` M4 / the tracker, not here.", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--splits", nargs="+", default=["val", "test"])
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    import torch
    rep = {"date": time.strftime("%Y-%m-%d %H:%M"), "machine": f"{platform.node()}, {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}",
           "weights": str(a.weights.relative_to(REPO)) if a.weights.is_relative_to(REPO) else str(a.weights), "imgsz": a.imgsz,
           "targets": TARGETS, "results": [evaluate(a.weights, s, a.imgsz, a.batch) for s in a.splits]}
    (OUT / "detection_report.json").write_text(json.dumps(rep, indent=1))
    (OUT / "detection_report.md").write_text(markdown(rep), encoding="utf-8")
    print(markdown(rep))


if __name__ == "__main__":
    main()
