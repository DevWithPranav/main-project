"""Session-grouped train/val/test split of PothRGBD (Build Plan M9).

prepare_dataset.py splits the 1,000 images at random. They were shot in bursts: 327 of the 999
consecutive pairs are <= 10 s apart (filename timestamps), i.e. the same pothole from a slightly
moved camera, so a random split puts near-copies of test images into train and flatters test
metrics. Here images are grouped into capture sessions (a new session after a gap > SESSION_GAP_S)
and whole sessions are assigned to train/val/test (~80/10/10 of images, seeded).

The source folder is only read. Output (gitignored):
    ml/pothole/data/grouped/{train,val,test}/{images,labels}/   copies
    ml/pothole/data/grouped/data.yaml                          absolute path, nc 1
    ml/pothole/data/grouped/split.json                         session -> split, counts
It also writes ml/pothole/data/data_local.yaml for the old random split (its data.yaml points to
another machine's D:\\ path).

Usage:
    python ml/pothole/prepare_grouped.py
    python ml/pothole/prepare_grouped.py --gap 60 --seed 42
"""

import argparse
import json
import random
import shutil
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "PUBLIC POTHOLE DATASET"
DATA = REPO / "ml" / "pothole" / "data"
OUT = DATA / "grouped"
SESSION_GAP_S = 60  # 60 s gap -> 228 sessions (30 s: 372, 120 s: 126); measured on the filenames 2026-10-10
FRACTIONS = {"train": 0.8, "val": 0.1, "test": 0.1}
NAMES = ["pothole"]


def stamp(p: Path) -> datetime:
    return datetime.strptime(p.name[:15], "%Y%m%d_%H%M%S")


def sessions(images: list[Path], gap_s: float) -> list[list[Path]]:
    images = sorted(images, key=stamp)
    out = [[images[0]]]
    for a, b in zip(images, images[1:]):
        if (stamp(b) - stamp(a)).total_seconds() > gap_s:
            out.append([])
        out[-1].append(b)
    return out


def assign(groups: list[list[Path]], seed: int) -> dict[str, list[list[Path]]]:
    """Shuffle sessions, then fill val and test to ~10% of images each; the rest is train."""
    order = list(range(len(groups)))
    random.Random(seed).shuffle(order)
    total = sum(map(len, groups))
    split = {"train": [], "val": [], "test": []}
    filled = {"val": 0, "test": 0}
    for i in order:
        for s in ("test", "val"):
            if filled[s] + len(groups[i]) <= FRACTIONS[s] * total + 5:
                split[s].append(groups[i])
                filled[s] += len(groups[i])
                break
        else:
            split["train"].append(groups[i])
    return split


def write_yaml(path: Path, root: Path, names: list[str]) -> None:
    path.write_text(f"path: {root.as_posix()}\ntrain: train/images\nval: val/images\ntest: test/images\n\n"
                    f"nc: {len(names)}\nnames: {names}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gap", type=float, default=SESSION_GAP_S)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    if (DATA / "train" / "images").is_dir():
        write_yaml(DATA / "data_local.yaml", DATA, NAMES)
    images = sorted(p for p in (SRC / "images").iterdir() if p.suffix.lower() in (".jpg", ".png"))
    groups = sessions(images, a.gap)
    split = assign(groups, a.seed)
    report = {"gap_s": a.gap, "seed": a.seed, "n_sessions": len(groups), "splits": {}}
    for s, gs in split.items():
        img_dir, lbl_dir = OUT / s / "images", OUT / s / "labels"
        if img_dir.parent.exists():
            shutil.rmtree(img_dir.parent)
        img_dir.mkdir(parents=True)
        lbl_dir.mkdir(parents=True)
        n_inst = 0
        for g in gs:
            for p in g:
                shutil.copy2(p, img_dir / p.name)
                lbl = SRC / "labels" / (p.stem + ".txt")
                if lbl.exists():
                    shutil.copy2(lbl, lbl_dir / lbl.name)
                    n_inst += sum(1 for ln in lbl.read_text().splitlines() if ln.strip())
        report["splits"][s] = {"sessions": len(gs), "images": sum(map(len, gs)), "instances": n_inst,
                               "first_images": sorted(g[0].name[:15] for g in gs)}
        print(f"{s:5s}: {len(gs):3d} sessions, {sum(map(len, gs)):4d} images, {n_inst:4d} instances")
    write_yaml(OUT / "data.yaml", OUT, NAMES)
    (OUT / "split.json").write_text(json.dumps(report, indent=1))
    print(f"-> {OUT / 'data.yaml'}")


if __name__ == "__main__":
    main()
