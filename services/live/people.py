"""Pedestrians in the live pipeline (zebra conditions F2 blocking, F3 failure to yield, F5 obstructing).

The vehicle path finds no people: the retrained 3-class model has no person class. As offline
(process_recorded_flight.py --people, pedestrians.py), the 10-class full_train model runs as a second
detector with its own tracker instance, keeping only pedestrian / people boxes; they get ground
positions from the same camera pose (on the flat road plane, as run_violations.people_rows: people
stand on pavements and kerbs, where the road surface model has no height), their own online
kinematics, and join the vehicle rows going into the engine with ids offset by PEOPLE_ID_OFFSET
(rules.Engine keeps them away from the vehicle rules by class).

The people detector runs at 1280 px on every processed frame it is used on, which roughly doubles a
frame's GPU time, and the people rules only look at crosswalks. So it runs only while a crosswalk is
near the camera's view (CrosswalkGate: the frame's ground footprint grown by GATE_MARGIN_M touches a
crosswalk zone of the scene), and for HOLD_S after: over the Town04 highway it costs nothing.

PeoplePath.step(frame, t, img, cam) -> (people dets for drawing, live states), PeoplePath.emit(t) /
flush() -> engine batches with ids offset; merge_batches() joins them with the vehicles' batches.
"""

import sys
from pathlib import Path

import numpy as np
import shapely

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))

from pedestrians import ENGINE_CLASS, PEOPLE_ID_OFFSET, PEOPLE_IMGSZ, PEOPLE_MIN_MEAN_CONF, offset_rows  # noqa: E402

GATE_MARGIN_M = 15.0  # a walker 15 m from a crossing is tracked before it steps on (and a car approaching it)
HOLD_S = 3.0  # keep detecting this long after the last crosswalk left the view (no flicker at the frame edge)


class CrosswalkGate:
    """Is a crosswalk zone of the scene near the camera's ground footprint this frame?"""

    def __init__(self, zones, margin_m: float = GATE_MARGIN_M, hold_s: float = HOLD_S):
        self.crossings = [z.polygon for z in zones if z.type == "crosswalk"]
        self.tree = shapely.STRtree(self.crossings) if self.crossings else None
        self.margin, self.hold = margin_m, hold_s
        self.last_on = -np.inf

    def footprint(self, cam, frame: int):
        """The frame's corners on the ground (cam's pose for `frame` must be set), or None."""
        u = np.array([0.0, cam.W, cam.W, 0.0])
        v = np.array([0.0, 0.0, cam.H, cam.H])
        g = cam.to_ground(frame, u, v)
        if g is None or not np.all(np.isfinite(g)):
            return None
        return shapely.Polygon(g)

    def near(self, footprint) -> bool:
        if self.tree is None or footprint is None:
            return False
        return len(self.tree.query(footprint.buffer(self.margin), predicate="intersects")) > 0

    def __call__(self, cam, frame: int, t: float) -> bool:
        if self.near(self.footprint(cam, frame)):
            self.last_on = t
        return t - self.last_on <= self.hold


def merge_batches(*batch_lists) -> list[tuple[float, int, list[dict]]]:
    """Kinematics batches [(t, frame, rows)] from several OnlineKinematics -> one list in time order, one
    batch per frame (the engine steps once per frame)."""
    by = {}
    for batches in batch_lists:
        for t, fr, rows in batches:
            by.setdefault((t, fr), []).extend(rows)
    return [(t, fr, rows) for (t, fr), rows in sorted(by.items())]


class PeoplePath:
    """People detector + tracker + kinematics for the live pipeline; see the module docstring."""

    def __init__(self, zones, lag_s: float, mode: str, relink: bool, imgsz: int = PEOPLE_IMGSZ,
                 always: bool = False, tracker=None):
        from online import OnlineKinematics
        if tracker is None:
            from pedestrians import PeopleTracker
            tracker = PeopleTracker(imgsz=imgsz)
        self.tracker, self.always = tracker, always
        self.gate = CrosswalkGate(zones)
        self.kin = OnlineKinematics(lag_s=lag_s, mode=mode, relink=relink, min_mean_conf=PEOPLE_MIN_MEAN_CONF)
        self.frames_on = 0

    def warm_up(self):
        if hasattr(self.tracker, "model"):
            self.tracker.model.predict(np.zeros((1080, 1920, 3), np.uint8), imgsz=self.tracker.imgsz,
                                       classes=self.tracker.classes, verbose=False)

    @property
    def available(self) -> bool:
        """False when the scene has no crosswalk (and the detector is not forced on): nothing to do."""
        return self.always or self.gate.tree is not None

    def step(self, frame: int, t: float, img, cam, edge_mask=None) -> tuple[list[dict], list[dict]]:
        """One processed frame -> (dets with pixel boxes and offset ids for drawing, live states with offset
        ids). cam's pose for `frame` must be set. Off the crosswalks the kinematics still get an empty
        update, so lost people are forgotten and their rows still come out."""
        dets = []
        if self.always or self.gate(cam, frame, t):
            self.frames_on += 1
            boxes = self.tracker.update(img)
            if boxes:
                u = np.array([b["cx"] for b in boxes])
                v = np.array([b["cy"] for b in boxes])
                g = cam.to_ground(frame, u, v)
                ok = (edge_mask(u, v, np.array([b["w"] for b in boxes]), np.array([b["h"] for b in boxes]), cam.W, cam.H)
                      if edge_mask is not None else np.ones(len(boxes), bool))
                if g is not None:
                    for i, b in enumerate(boxes):
                        dets.append({"tracker_id": b["tracker_id"], "cls": ENGINE_CLASS, "conf": b["conf"],
                                     "x": float(g[i, 0]), "y": float(g[i, 1]), "edge_ok": bool(ok[i]),
                                     "xywh": (b["cx"], b["cy"], b["w"], b["h"])})
        live = self.kin.update(frame, t, [{k: v for k, v in d.items() if k != "xywh"} for d in dets])
        for d in dets:  # the engine's id (the track's current piece), as events and live states carry it
            tr = self.kin.tracks.get(self.kin.id_map.get(d["tracker_id"]))
            d["track_id"] = tr.pieces[-1].tid + PEOPLE_ID_OFFSET if tr is not None and tr.pieces else -1
        for s in live:
            s["track_id"] = int(s["track_id"]) + PEOPLE_ID_OFFSET
            s["cls"] = ENGINE_CLASS
        return dets, live

    def emit(self, t: float):
        return [(bt, fr, offset_rows(rows)) for bt, fr, rows in self.kin.emit(t)]

    def flush(self):
        return [(bt, fr, offset_rows(rows)) for bt, fr, rows in self.kin.flush()]
