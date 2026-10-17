"""Which road points the drone camera can't see because a structure is in the way (Build Plan M2).

Staging spot 3 (Town03, under the elevated railway) scored 0/12 on the pipeline: the stager only
knew the lane map, so it put acts on lanes the railway deck hides from the camera. This module reads
the town's static objects (export_town_objects.py: oriented boxes from
world.get_environment_objects()) and tests the line of sight from a car's roof to the camera, so
parallax is included (a 20 m building 40 m off-centre hides the street behind it, not only what
is under it).

  - Boxes: buildings, walls, bridges, rail, fences, static/dynamic props.
  - Vegetation off by default (vegetation=True: the ellipsoid inside its box). Measured on the
    2026-10-10 session: at spot 4 the wrong-way acts C1 / C2 had 35 % / 32 % of their points
    behind tree crowns by this model and the pipeline still caught both (crowns are partly
    see-through, and the box runs from the ground up, trunk included), while at spot 3 the
    railway hid 100 % of 14 of 15 acts and the pipeline caught 0 / 12.
  - Left out: poles, traffic lights and signs (thin: a box round a pole with a mast arm over
    the lanes is mostly air), and boxes > MAX_SPAN_M in both horizontal axes (curved linear
    meshes whose box spans what the curve encloses: the Town03 rail-loop corners, Town05 3;
    the same rule as the twin, M7 2026-10-10).
  - A point inside a Bridge box is the car on the bridge deck, not under it: not hidden.

No CARLA import: the stager (carlaAir env) and the unit tests (main venv) both use it.

Usage (from the stager):
    occ = Occluder.for_town("Town03")          # None if <Town>_objects.json is missing
    hidden = occ.hidden(points_xyz, camera_xyz)  # bool per point
"""

import json
import math
from pathlib import Path

import numpy as np

SCENES_DIR = Path(__file__).resolve().parents[2] / "ml" / "violation_engine" / "configs" / "scenes"
SKIP_LABELS = {"Poles", "TrafficLight", "TrafficSigns"}
ELLIPSOID_LABELS = {"Vegetation"}
DRIVE_ON_LABELS = {"Bridge"}  # a car inside one of these is on it
MAX_SPAN_M = 60.0  # half-extent 30 m both ways: twin's rule for curved linear meshes
CAR_TOP_M = 1.5  # line of sight from the roof of a car (Tesla Model 3 1.44 m)


def rotation_matrix(pitch: float, yaw: float, roll: float) -> np.ndarray:
    """CARLA's Rotation.RotateVector (LibCarla geom/Rotation.h): columns are the box's local x, y, z
    axes in world coordinates."""
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    return np.array([[cp * cy, cy * sp * sr - sy * cr, -cy * sp * cr - sy * sr],
                     [cp * sy, sy * sp * sr + cy * cr, -sy * sp * cr + cy * sr],
                     [sp, -cp * sr, cp * cr]])


class Occluder:
    def __init__(self, objects: list[dict], labels: list[str], vegetation: bool = False):
        keep = []
        for o in objects:
            lab = labels[o["l"]]
            e = [abs(v) for v in o["e"]]
            if lab in SKIP_LABELS or (lab in ELLIPSOID_LABELS and not vegetation) or (2 * e[0] > MAX_SPAN_M and 2 * e[1] > MAX_SPAN_M):
                continue
            keep.append((o, lab, e))
        self.labels = [lab for _, lab, _ in keep]
        self.c = np.array([o["c"] for o, _, _ in keep], float).reshape(-1, 3)
        self.e = np.array([e for _, _, e in keep], float).reshape(-1, 3)
        # axes[m, k] = the box's k-th local axis in world coordinates
        self.axes = np.array([rotation_matrix(*o["r"]).T for o, _, _ in keep], float).reshape(-1, 3, 3)
        self.ellipsoid = np.array([lab in ELLIPSOID_LABELS for lab in self.labels], bool)
        self.drive_on = np.array([lab in DRIVE_ON_LABELS for lab in self.labels], bool)
        self.radius = np.linalg.norm(self.e[:, :2], axis=1)  # horizontal bounding circle

    @classmethod
    def for_town(cls, town: str, scenes_dir: Path = SCENES_DIR, vegetation: bool = False):
        path = scenes_dir / f"{town}_objects.json"
        if not path.exists():
            return None
        doc = json.loads(path.read_text())
        return cls(doc["objects"], doc["labels"], vegetation)

    def near(self, xy_min, xy_max, pad: float = 0.0) -> "Occluder":
        """A copy with only the objects whose bounding circle reaches the rectangle (+ pad m)."""
        lo, hi = np.asarray(xy_min, float) - pad, np.asarray(xy_max, float) + pad
        sel = np.all((self.c[:, :2] + self.radius[:, None] >= lo) & (self.c[:, :2] - self.radius[:, None] <= hi), axis=1)
        sub = Occluder.__new__(Occluder)
        sub.labels = [lab for lab, s in zip(self.labels, sel) if s]
        for k in ("c", "e", "axes", "ellipsoid", "drive_on", "radius"):
            setattr(sub, k, getattr(self, k)[sel])
        return sub

    def hidden(self, pts, camera, car_top: float = CAR_TOP_M, chunk: int = 256) -> np.ndarray:
        """True for each road point (x, y, z) whose car roof (z + car_top) the camera can't see."""
        p = np.atleast_2d(np.asarray(pts, float))[:, :3].copy()
        p[:, 2] += car_top
        cam = np.asarray(camera, float)
        out = np.zeros(len(p), bool)
        if not len(self.c) or not len(p):
            return out
        for i in range(0, len(p), chunk):
            out[i:i + chunk] = self._blocked(p[i:i + chunk], cam)
        return out

    def _blocked(self, p: np.ndarray, cam: np.ndarray) -> np.ndarray:
        d = cam[None, :] - p  # segment p -> camera, t in [0, 1]
        # horizontal prefilter: distance from each object's centre to each segment's xy
        seg = d[:, None, :2]
        rel = self.c[None, :, :2] - p[:, None, :2]
        t = np.clip(np.sum(rel * seg, -1) / np.maximum(np.sum(seg * seg, -1), 1e-9), 0, 1)
        dist = np.linalg.norm(rel - t[..., None] * seg, axis=-1)
        cand = dist <= self.radius[None, :] + 0.5
        if not cand.any():
            return np.zeros(len(p), bool)
        n_idx, m_idx = np.nonzero(cand)
        ax = self.axes[m_idx]  # (K, 3, 3)
        o = np.einsum("kj,kij->ki", p[n_idx] - self.c[m_idx], ax)  # segment start in box frame
        v = np.einsum("kj,kij->ki", d[n_idx], ax)
        e = self.e[m_idx]
        hit = np.zeros(len(n_idx), bool)
        inside = np.zeros(len(n_idx), bool)

        box = ~self.ellipsoid[m_idx]
        if box.any():  # slab test
            ob, vb, eb = o[box], v[box], e[box]
            with np.errstate(divide="ignore", invalid="ignore"):
                t1, t2 = (-eb - ob) / vb, (eb - ob) / vb
            par = np.abs(vb) < 1e-12  # parallel to a slab: inside it or never
            lo = np.where(par, np.where(np.abs(ob) <= eb, -np.inf, np.inf), np.minimum(t1, t2))
            hi = np.where(par, np.where(np.abs(ob) <= eb, np.inf, -np.inf), np.maximum(t1, t2))
            tmin, tmax = lo.max(1), hi.min(1)
            hit[box] = (tmax >= np.maximum(tmin, 0.0)) & (tmin <= 1.0)
            inside[box] = np.all(np.abs(ob) <= eb, axis=1)

        ell = ~box
        if ell.any():  # |o/e + t v/e|^2 <= 1 for some t in [0, 1]
            oe, ve = o[ell] / e[ell], v[ell] / e[ell]
            a, b = np.sum(ve * ve, 1), 2 * np.sum(oe * ve, 1)
            c = np.sum(oe * oe, 1) - 1.0
            ts = np.clip(-b / np.maximum(2 * a, 1e-12), 0, 1)
            hit[ell] = a * ts * ts + b * ts + c <= 0
            inside[ell] = c <= 0

        hit &= ~(inside & self.drive_on[m_idx])
        out = np.zeros(len(p), bool)
        np.logical_or.at(out, n_idx, hit)
        return out
