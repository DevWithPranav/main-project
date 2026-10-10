"""Pedestrian detection and tracking next to the vehicle detector (Build Plan M3).

Expected_Output Section 11, decision #1: the retrained 3-class model stays for vehicles; the earlier
10-class VisDrone model (full_train, pedestrian mAP50 0.540 on VisDrone val) runs alongside it, and
only its "pedestrian" and "people" boxes are kept. People get their own tracker instance, ground
positions (same camera projection as vehicles) and kinematics, then join the vehicle rows going
into the engine with track ids offset by PEOPLE_ID_OFFSET, so they never collide with vehicle ids
(rules.Engine keeps them away from the vehicle rules by class, predicates.PEOPLE_CLASSES).

Offline: process_recorded_flight.py --people writes people_trajectories.csv next to the vehicles'
trajectories; run_violations.py picks it up (people_rows).
Live (services/live/pipeline.py): PeopleTracker.update(frame) per frame -> detections in the same
dict form the vehicle path uses; project them with the same LiveCamera, feed them to a second
online.OnlineKinematics(min_mean_conf=PEOPLE_MIN_MEAN_CONF) and pass its rows through offset_rows()
before IncrementalEngine.step.

The thresholds below are starting values, NOT yet measured: eval_pedestrian_detection.py on a
flight with CARLA walkers (recorded with --labels) sets them (Build Plan M3, Measure).

Usage (a quick look at one frame):
    python ml/violation_engine/pedestrians.py <image> [--imgsz 1280] [--conf 0.2]
"""

import argparse
from pathlib import Path

PEOPLE_WEIGHTS = Path(__file__).resolve().parents[1] / "data" / "results" / "full_train" / "train" / "weights" / "best.pt"
PEOPLE_NAMES = {"pedestrian", "people"}  # VisDrone: walking / standing ("pedestrian") and sitting or in groups ("people")
ENGINE_CLASS = "pedestrian"  # both become one class for the rules
# starting values (not measured yet; eval_pedestrian_detection.py --imgsz 640 1280 decides): 1280 px
# because people are a few pixels tall from ~67 m (vehicles: 1280 found ~16 % more on the roundabout clip)
PEOPLE_IMGSZ = 1280
PEOPLE_CONF = 0.2
PEOPLE_TRACK_CONF = 0.1  # the tracker sees boxes down to this (its own second-stage association), rows keep their conf
PEOPLE_MIN_MEAN_CONF = 0.2  # postprocess_tracks.MIN_MEAN_CONF (0.3) is tuned for vehicles
PEOPLE_ID_OFFSET = 1_000_000_000
DEDUPE_IOU = 0.5


def people_class_ids(model) -> list[int]:
    return sorted(i for i, n in model.names.items() if n.lower() in PEOPLE_NAMES)


def offset_rows(rows: list[dict], key: str = "track_id") -> list[dict]:
    """People's kinematics rows -> engine rows: ids offset, class unified."""
    for r in rows:
        r[key] = int(r[key]) + PEOPLE_ID_OFFSET
        if "src_track_id" in r and r["src_track_id"] not in ("", None):
            r["src_track_id"] = int(r["src_track_id"]) + PEOPLE_ID_OFFSET
        r["class"] = ENGINE_CLASS
    return rows


def _dedupe(r):
    """Class-agnostic NMS: the model often returns one person as both pedestrian and people."""
    import torchvision  # here: run_violations.py imports this module without needing torch
    if r.boxes is not None and len(r.boxes) > 1:
        keep = torchvision.ops.nms(r.boxes.xyxy.float(), r.boxes.conf.float(), DEDUPE_IOU)
        r = r[keep.sort().values]
    return r


class PeopleTracker:
    """A second detector + tracker instance for people, one frame at a time (live mode)."""

    def __init__(self, weights: Path = PEOPLE_WEIGHTS, tracker_yaml: str | None = None, conf: float = PEOPLE_TRACK_CONF,
                 imgsz: int = PEOPLE_IMGSZ, device=None):
        from ultralytics import YOLO
        if tracker_yaml is None:
            tracker_yaml = str(Path(__file__).resolve().parent / "tracktrack_ours.yaml")
        self.model = YOLO(str(weights))
        self.classes = people_class_ids(self.model)
        self.tracker_yaml, self.conf, self.imgsz, self.device = tracker_yaml, conf, imgsz, device

    def update(self, frame) -> list[dict]:
        """BGR image -> [{tracker_id, cls, raw_cls, conf, cx, cy, w, h}] (pixels; tracker ids not offset:
        offset_rows() does that after kinematics)."""
        kw = {"device": self.device} if self.device is not None else {}
        r = self.model.track(frame, persist=True, tracker=self.tracker_yaml, classes=self.classes, conf=self.conf,
                             imgsz=self.imgsz, verbose=False, **kw)[0]
        if r.boxes is None or r.boxes.id is None:
            return []
        out = []
        for box, tid in zip(r.boxes, r.boxes.id):
            cx, cy, w, h = (float(v) for v in box.xywh[0])
            out.append({"tracker_id": int(tid), "cls": ENGINE_CLASS, "raw_cls": r.names[int(box.cls)].lower(),
                        "conf": round(float(box.conf), 3), "cx": cx, "cy": cy, "w": w, "h": h})
        return out


def detect(model, image, imgsz: int = PEOPLE_IMGSZ, conf: float = PEOPLE_CONF, tile: int | None = None,
           overlap: int = 64) -> list[dict]:
    """Detection only (no tracking): [{cls, conf, cx, cy, w, h}] after class-agnostic NMS. image: a BGR
    array. tile: sliced inference (SAHI-style): tile x tile crops with `overlap` px overlap, each
    resized to imgsz (so tile 640 at imgsz 1280 doubles the people's size), merged by NMS."""
    classes = people_class_ids(model)
    if not tile:
        r = _dedupe(model.predict(image, imgsz=imgsz, conf=conf, classes=classes, verbose=False)[0])
        return [{"cls": r.names[int(b.cls)].lower(), "conf": float(b.conf), **dict(zip(("cx", "cy", "w", "h"),
                 (float(v) for v in b.xywh[0])))} for b in r.boxes]
    import torch
    import torchvision
    H, W = image.shape[:2]
    step = tile - overlap
    xs = sorted({min(x, max(W - tile, 0)) for x in range(0, max(W - overlap, 1), step)})
    ys = sorted({min(y, max(H - tile, 0)) for y in range(0, max(H - overlap, 1), step)})
    crops = [(x, y, image[y:y + tile, x:x + tile]) for y in ys for x in xs]
    res = [model.predict(c, imgsz=imgsz, conf=conf, classes=classes, verbose=False)[0] for _, _, c in crops]
    boxes, scores, names = [], [], []
    for (x, y, _), r in zip(crops, res):
        for b in r.boxes:
            x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
            boxes.append([x0 + x, y0 + y, x1 + x, y1 + y])
            scores.append(float(b.conf))
            names.append(r.names[int(b.cls)].lower())
    if not boxes:
        return []
    keep = torchvision.ops.nms(torch.tensor(boxes), torch.tensor(scores), DEDUPE_IOU).tolist()
    return [{"cls": names[k], "conf": scores[k], "cx": (boxes[k][0] + boxes[k][2]) / 2, "cy": (boxes[k][1] + boxes[k][3]) / 2,
             "w": boxes[k][2] - boxes[k][0], "h": boxes[k][3] - boxes[k][1]} for k in sorted(keep)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--imgsz", type=int, default=PEOPLE_IMGSZ)
    ap.add_argument("--conf", type=float, default=PEOPLE_CONF)
    args = ap.parse_args()
    from ultralytics import YOLO
    for d in detect(YOLO(str(PEOPLE_WEIGHTS)), str(args.image), args.imgsz, args.conf):
        print(d)


if __name__ == "__main__":
    main()
