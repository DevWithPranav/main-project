r"""Score returned M9 weights in one command: per-category F1 vs the PRD target (Build Plan M9).

For every weights file (each `<runs>/<name>/weights/best.pt`, or --weights) and every test set
found, runs evaluate.py's two views (fixed-confidence F1 with one-to-one mask matching, and
Ultralytics val) and checks each anomaly category against F1 >= 0.65 (PRD Section 27.4):

    pass / fail     the category has test data and the model has the class
    not_trained     the test set has the category but the model has no such class (counts as fail)
    no_data         no test set here has the category (never a pass)

Test sets (each used when its data.yaml exists):
    grouped     ml/pothole/data/grouped        PothRGBD session-grouped split (prepare_grouped.py)
    multi       ml/pothole/data/multi          4-class merged set (Training Guide 5.4)
    aerial      ml/pothole/data/aerial_test    drone-view test set (Training Guide 5.2)
    random_clean ml/pothole/data/random_clean  the random split's val+test images whose capture session
                has no image in its train (--make-random-clean): the only leak-free test of the
                2025 baseline weights (20 images)
    + any --data <name>=<data.yaml>

Leak check: when a run's args.yaml names its training data, the training image file names are
compared with each test set's; an overlap is reported (the 2025 baseline was trained on the random
split, which holds 81 of the 105 grouped-test images).

--synthetic <flight>: also runs synthetic_check.py (model mode) on that CARLA flight for each
weights file: pasted potholes at aerial scale -> per-frame mask recall and event recall.

Output: ml/pothole/runs/eval/score_<stamp>.json and .md (the Training Guide section 8 table).

Usage:
    python ml/pothole/score_m9.py --runs ml/pothole/runs
    python ml/pothole/score_m9.py --weights ml/pothole/runs/E2_modified/weights/best.pt --data aerial=D:/aerial/data.yaml
    python ml/pothole/score_m9.py --runs ml/pothole/runs --synthetic simulation/data_export/recorded_flights/20261002_001635
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate as ev  # noqa: E402  (imports modify_model: custom layers load)

DATA = HERE / "data"
KNOWN_SETS = {"grouped": DATA / "grouped" / "data.yaml", "multi": DATA / "multi" / "data.yaml",
              "aerial": DATA / "aerial_test" / "data.yaml", "random_clean": DATA / "random_clean" / "data.yaml"}
EVAL_DIR = HERE / "runs" / "eval"


def make_random_clean() -> Path:
    """Random-split val+test images whose 60 s capture session has no train image -> random_clean/test."""
    import shutil
    import prepare_grouped as pg
    split = {p.name: s for s in ("train", "val", "test") for p in (DATA / s / "images").iterdir()}
    imgs = [DATA / s / "images" / n for n, s in split.items()]
    out = DATA / "random_clean"
    if out.exists():
        raise SystemExit(f"{out} exists")
    (out / "test" / "images").mkdir(parents=True)
    (out / "test" / "labels").mkdir(parents=True)
    n = 0
    for g in pg.sessions(imgs, pg.SESSION_GAP_S):
        if any(split[q.name] == "train" for q in g):
            continue
        for q in g:
            shutil.copy2(q, out / "test" / "images" / q.name)
            lab = q.parent.parent / "labels" / f"{q.stem}.txt"
            if lab.is_file():
                shutil.copy2(lab, out / "test" / "labels" / lab.name)
            n += 1
    (out / "data.yaml").write_text(f"path: {out.as_posix()}\ntrain: test/images\nval: test/images\n"
                                   "test: test/images\n\nnc: 1\nnames: ['pothole']\n")
    print(f"random_clean: {n} images -> {out}")
    return out / "data.yaml"


def find_weights(runs: Path) -> list[Path]:
    return sorted(p for p in runs.glob("*/weights/best.pt") if p.parent.parent.name not in ("eval", "smoke_test"))


def data_names(data_yaml: Path) -> list[str]:
    n = yaml.safe_load(data_yaml.read_text())["names"]
    return list(n.values()) if isinstance(n, dict) else list(n)


def image_names(data_yaml: Path, split: str) -> set[str]:
    try:
        return {p.name for p in ev.split_images(data_yaml, split)}
    except (KeyError, FileNotFoundError, OSError):
        return set()


def train_yaml(weights: Path) -> Path | None:
    args = weights.parent.parent / "args.yaml"
    if not args.is_file():
        return None
    d = yaml.safe_load(args.read_text()).get("data")
    if not d:
        return None
    p = Path(d)
    if p.is_file():
        return p
    local = DATA / p.name if p.parent.name == "data" else DATA / p.parent.name / p.name  # another machine's path
    return local if local.is_file() else None


def leak(weights: Path, test_yaml: Path) -> dict | None:
    ty = train_yaml(weights)
    if ty is None:
        return None
    if ty.parent == DATA and (DATA / "data_local.yaml").is_file():  # random split's yaml points elsewhere
        ty = DATA / "data_local.yaml"
    train = image_names(ty, "train")
    test = image_names(test_yaml, "test")
    if not train or not test:
        return None
    n = len(train & test)
    return {"train_data": str(ty), "test_images_in_train": n, "test_images": len(test)}


def score_one(weights: Path, set_name: str, data_yaml: Path, a) -> dict:
    from ultralytics import YOLO
    model = YOLO(str(weights))
    names = model.names
    mcats = set(names.values())
    dcats = set(data_names(data_yaml))
    images = ev.split_images(data_yaml, a.split)
    t0 = time.time()
    try:
        ul = ev.ultralytics_eval(model, data_yaml, a.split, a.imgsz, a.device, EVAL_DIR / "m9_eval_tmp", 0)
        ul_err = None
    except Exception as e:  # noqa: BLE001 - e.g. a 1-class model on a 4-class set; fixed_conf still runs
        ul, ul_err = {}, f"{type(e).__name__}: {e}"
    fc = ev.fixed_conf_eval(model, images, names, a.conf, a.iou, a.imgsz, a.device)
    cats = {}
    for c in ev.CATEGORIES:
        if c in dcats and c in mcats and c in fc and fc[c]["tp"] + fc[c]["fn"] > 0:
            cats[c] = {"status": "pass" if fc[c]["f1"] >= ev.F1_TARGET else "fail", "f1": fc[c]["f1"]}
        elif c in dcats and c not in mcats:
            cats[c] = {"status": "not_trained", "f1": 0.0}
        else:
            cats[c] = {"status": "no_data", "f1": None}
    return {"weights": str(weights), "run": weights.parent.parent.name, "set": set_name, "data": str(data_yaml),
            "split": a.split, "n_images": len(images), "conf": a.conf, "iou": a.iou, "imgsz": a.imgsz,
            "model_classes": sorted(mcats), "m9_mods": (model.model.yaml or {}).get("m9_mods"),
            "f1_target": ev.F1_TARGET, "categories": cats, "fixed_conf": fc, "ultralytics": ul,
            "ultralytics_error": ul_err, "leak": leak(weights, data_yaml), "eval_s": round(time.time() - t0, 1)}


def synthetic(weights: Path, flight: str, a) -> dict | None:
    out = EVAL_DIR / f"synthetic_{weights.parent.parent.name}_{datetime.now():%Y%m%d_%H%M%S}"
    cmd = [sys.executable, str(HERE / "synthetic_check.py"), "--flight", flight, "--weights", str(weights),
           "--modes", "model", "--stride", "20", "--max-frames", "30", "--out", str(out), "--device", a.device,
           "--batch", "4"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    f = out / "check.json"
    if r.returncode or not f.is_file():
        return {"error": (r.stderr or r.stdout)[-500:]}
    m = json.loads(f.read_text())["modes"]["model"]
    return {"out": str(out), "event_recall": m["event_recall"], "false_events": m["false_events"],
            "per_frame_mask": m["per_frame_mask"]}


def table(rows: list[dict]) -> str:
    def f(r, c):
        x = r["categories"][c]
        return "n/a" if x["status"] == "no_data" else f"{x['f1']:.3f} {'PASS' if x['status'] == 'pass' else 'FAIL'}" + \
            (" (no class)" if x["status"] == "not_trained" else "")
    head = ("| Run | Test set | Images | Pothole F1 | Crack F1 | Waterlogging F1 | Debris F1 | Mask mAP50 | Mask mAP50-95 "
            "| Notes |\n|---|---|---|---|---|---|---|---|---|---|\n")
    lines = []
    for r in rows:
        ul = r["ultralytics"]
        m50 = sum(v["mask"]["map50"] for v in ul.values()) / len(ul) if ul else None
        m95 = sum(v["mask"]["map50_95"] for v in ul.values()) / len(ul) if ul else None
        notes = []
        if r["leak"] and r["leak"]["test_images_in_train"]:
            notes.append(f"LEAK {r['leak']['test_images_in_train']}/{r['leak']['test_images']} test images in train")
        if r.get("synthetic"):
            s = r["synthetic"]
            notes.append(f"aerial synthetic: event recall {s.get('event_recall')}, mask recall "
                         f"{(s.get('per_frame_mask') or {}).get('recall')}" if "error" not in s else "synthetic failed")
        if r["ultralytics_error"]:
            notes.append("val failed")
        lines.append(f"| {r['run']} | {r['set']} | {r['n_images']} | " + " | ".join(f(r, c) for c in ev.CATEGORIES) +
                     f" | {'' if m50 is None else f'{m50:.3f}'} | {'' if m95 is None else f'{m95:.3f}'} | {'; '.join(notes)} |")
    return head + "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default=None, help="folder of runs: every <name>/weights/best.pt is scored")
    ap.add_argument("--weights", nargs="*", default=[])
    ap.add_argument("--data", nargs="*", default=[], help="extra test sets as name=path/to/data.yaml")
    ap.add_argument("--sets", default=None, help="comma list of known sets to use (default: all that exist)")
    ap.add_argument("--split", default="test")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="0")
    ap.add_argument("--synthetic", default=None, help="CARLA flight folder for the aerial synthetic check")
    ap.add_argument("--tag", default=None, help="name for the output files")
    ap.add_argument("--make-random-clean", action="store_true", help="build data/random_clean first (once)")
    a = ap.parse_args()
    if a.make_random_clean:
        make_random_clean()

    weights = [Path(w) for w in a.weights] + (find_weights(Path(a.runs)) if a.runs else [])
    if not weights:
        raise SystemExit("no weights: give --runs <folder> or --weights <best.pt ...>")
    sets = {k: v for k, v in KNOWN_SETS.items() if v.is_file() and (not a.sets or k in a.sets.split(","))}
    for d in a.data:
        k, _, v = d.partition("=")
        sets[k] = Path(v)
    if not sets:
        raise SystemExit("no test set found; run python ml/pothole/prepare_grouped.py first")
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for w in weights:
        syn = synthetic(w, a.synthetic, a) if a.synthetic else None
        for name, y in sets.items():
            r = score_one(w, name, y, a)
            if syn and name == next(iter(sets)):
                r["synthetic"] = syn
            rows.append(r)
            print(f"{r['run']:<24} {name:<8} " + "  ".join(f"{c}={r['categories'][c]['status']}:{r['categories'][c]['f1']}"
                                                         for c in ev.CATEGORIES))
    stamp = a.tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    md = table(rows)
    (EVAL_DIR / f"score_{stamp}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    (EVAL_DIR / f"score_{stamp}.md").write_text(
        f"# M9 scores {stamp}\n\nF1 at conf {a.conf}, mask IoU >= {a.iou}, target {ev.F1_TARGET} per category "
        f"(PRD 27.4). Split: {a.split}.\n\n" + md, encoding="utf-8")
    print("\n" + md)
    print(f"-> {EVAL_DIR / f'score_{stamp}.md'}")


if __name__ == "__main__":
    main()
