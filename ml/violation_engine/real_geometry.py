"""Ground coordinates for real drone video (Violation Engine, Layer 2 - Phase B1).

docs/Violation_Engine_Architecture.md, Section 5.2 (real footage). The CARLA path
(ground_coords.py) knows the camera pose; real footage does not, so positions go
    pixel in frame f --scene_map.py--> pixel in the reference frame --calibration--> metres
scene_map.py (Stabilo, SIFT, keyframes) already cancels camera motion; the calibration
turns reference-frame pixels into metres on the road plane. It lives in a **site file**
(one per clip, drawn with zone_tool.py), together with the lanes and zones:

    {"video": "<path>", "reference_frame": 0, "frame_size": [W, H],
     "calibration": {"method": "scale", "image_points": [[u, v], [u, v]], "length_m": 3.5}
                  | {"method": "homography", "image_points": [[u, v] x >= 4], "world_points": [[x, y] x >= 4]}
                  | {"method": "srt", "srt": "<file.SRT>", "hfov_deg": 82.0},
     "ref_point": 0.5,                     # where on the box the vehicle stands: 0.5 = centre (camera
                                           # looking down), ~0.75 for an oblique camera (ground is low in the box)
     "lanes": [{"id", "centreline_px": [[u, v], ...], "width_m", "speed_limit_kmh", "left_line", "right_line",
                "lane_type", "junction"}],
     "zones": [{"id", "type", "polygon_px": [[u, v], ...], ...}]}

Pixels are those of the reference frame. "scale" assumes a camera looking straight down;
"homography" handles a tilted camera (4+ ground points with known positions, e.g. a lane
rectangle); "srt" reads a DJI .SRT telemetry file (rel_alt = height above the take-off
point, so it is only right if the road is at take-off height).

Time comes from each frame's own timestamp (ffprobe if installed, else OpenCV's
CAP_PROP_POS_MSEC), never frame / fps: phone and screen recordings can have a variable
frame rate.

Outputs: wx, wy (metres), pose_exact (1 = the frame registered to the scene map; 0 =
registration failed, so kinematics.py does not use it as a measurement) and a corrected
time_s, written into the trajectory CSV; plus the site's lanes/zones in metres as a scene
JSON for lane_map.py.

Usage:
    python ml/violation_engine/real_geometry.py <site.json> <trajectories_final.csv>           # adds wx, wy, writes scene
    python ml/violation_engine/real_geometry.py <site.json> <trajectories_final.csv> --check   # + car-length sanity check
"""

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
CAR_LENGTH_M = (4.0, 5.2)  # typical car length; the --check median should fall about here


# --- time -------------------------------------------------------------------------------------

def frame_times(video: Path) -> np.ndarray:
    """Presentation time (s) of every frame."""
    if shutil.which("ffprobe"):
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(video)],
                             capture_output=True, text=True, check=True).stdout
        ts = np.array([float(x) for x in out.split() if x.strip() not in ("", "N/A")])
        if len(ts):
            return ts - ts[0]
    cap = cv2.VideoCapture(str(video))
    ts = []
    while cap.grab():
        ts.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
    cap.release()
    ts = np.array(ts)
    return ts - ts[0] if len(ts) else ts


# --- DJI telemetry ----------------------------------------------------------------------------

SRT_FIELD = re.compile(r"\[?\s*(rel_alt|abs_alt|focal_len|latitude|longitude|gb_pitch|gb_yaw)\s*[:=]\s*(-?[\d.]+)")


def parse_srt(text: str) -> list[dict]:
    """DJI .SRT telemetry -> [{t, rel_alt, ...}] (one entry per subtitle block; t = its start, s)."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->", block)
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0
        rec = {"t": t}
        for k, v in SRT_FIELD.findall(block):
            rec[k] = float(v)
        out.append(rec)
    return out


def srt_metres_per_px(srt: list[dict], hfov_deg: float, width_px: int, t: float = 0.0) -> float:
    """Ground size of one pixel for a camera looking straight down, from the altitude at time t."""
    recs = [r for r in srt if "rel_alt" in r]
    if not recs:
        raise SystemExit("SRT has no rel_alt field")
    r = min(recs, key=lambda r: abs(r["t"] - t))
    return 2 * r["rel_alt"] * math.tan(math.radians(hfov_deg) / 2) / width_px


# --- site -------------------------------------------------------------------------------------

class Site:
    """Reference-frame pixels <-> metres for one clip, plus its lanes and zones."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text())
        self.video = (REPO / self.data["video"]) if not Path(self.data["video"]).is_absolute() else Path(self.data["video"])
        self.ref_frame = int(self.data.get("reference_frame", 0))
        self.ref_point = float(self.data.get("ref_point", 0.5))
        self.W, self.H = self.data.get("frame_size", [None, None])
        self.px2m = self._calibrate(self.data["calibration"])
        self.m2px = np.linalg.inv(self.px2m)
        # handedness of the metric frame: image axes (x right, y down) are left-handed seen from above;
        # a calibration that keeps the orientation keeps that
        J = self._jacobian(self.W / 2 if self.W else 0.0, self.H / 2 if self.H else 0.0)
        self.left_handed = bool(np.linalg.det(J) > 0)

    def _calibrate(self, c: dict) -> np.ndarray:
        m = c["method"]
        if m == "scale":
            (u1, v1), (u2, v2) = c["image_points"][:2]
            mpp = float(c["length_m"]) / math.hypot(u2 - u1, v2 - v1)
            return np.diag([mpp, mpp, 1.0])
        if m == "homography":
            src = np.array(c["image_points"], np.float64)
            dst = np.array(c["world_points"], np.float64)
            if len(src) < 4 or len(src) != len(dst):
                raise SystemExit("homography calibration needs >= 4 image_points with matching world_points")
            H, _ = cv2.findHomography(src, dst, 0)
            return H / H[2, 2]
        if m == "srt":
            srt = parse_srt((self.path.parent / c["srt"]).read_text(errors="ignore"))
            mpp = srt_metres_per_px(srt, float(c["hfov_deg"]), int(self.W), float(c.get("t", 0.0)))
            return np.diag([mpp, mpp, 1.0])
        raise SystemExit(f"unknown calibration method {m!r}")

    def _jacobian(self, u: float, v: float, eps: float = 1.0) -> np.ndarray:
        p0, pu, pv = self.to_metres(np.array([[u, v], [u + eps, v], [u, v + eps]]))
        return np.stack([pu - p0, pv - p0], axis=1) / eps

    def to_metres(self, px: np.ndarray) -> np.ndarray:
        p = np.c_[np.asarray(px, float), np.ones(len(px))] @ self.px2m.T
        return p[:, :2] / p[:, 2:3]

    def to_px(self, m: np.ndarray) -> np.ndarray:
        p = np.c_[np.asarray(m, float), np.ones(len(m))] @ self.m2px.T
        return p[:, :2] / p[:, 2:3]

    def scene(self) -> dict:
        """Lanes and zones in metres, as a lane_map.py scene."""
        lanes = []
        for l in self.data.get("lanes", []):
            d = {k: v for k, v in l.items() if k != "centreline_px"}
            d["centreline"] = np.round(self.to_metres(np.array(l["centreline_px"])), 3).tolist()
            lanes.append(d)
        zones = []
        for z in self.data.get("zones", []):
            d = {k: v for k, v in z.items() if k != "polygon_px"}
            d["polygon"] = np.round(self.to_metres(np.array(z["polygon_px"])), 3).tolist()
            zones.append(d)
        return {"scene": self.path.stem, "coords": "site_m", "left_handed": self.left_handed,
                "lanes": lanes, "zones": zones, "stop_lines": []}


# --- applying it to trajectories --------------------------------------------------------------

class RealCamera:
    """Per-frame mapping between a clip's pixels and site metres (scene map + calibration), with the
    same to_pixels() interface as ground_coords.FlightCamera so render_violations.py can draw on it."""

    def __init__(self, site: Site, scene_map_npz: Path | None):
        self.site = site
        if scene_map_npz is not None and Path(scene_map_npz).exists():
            sm = np.load(scene_map_npz)
            self.Hs = sm["H"]  # frame px -> anchor px
            self.failed = sm["failed"].astype(bool)
            anchor_to_ref = np.linalg.inv(self.Hs[site.ref_frame])  # anchor px -> reference-frame px
            self.Hs = np.einsum("ij,fjk->fik", anchor_to_ref, self.Hs)
        else:  # camera assumed still: frame pixels are reference pixels
            self.Hs, self.failed = None, None
        self.W, self.H = site.W, site.H  # frame size, as FlightCamera
        self.ground_z = 0.0

    def frame_to_ref(self, frame: int, px: np.ndarray) -> np.ndarray:
        if self.Hs is None:
            return np.asarray(px, float)
        H = self.Hs[min(frame, len(self.Hs) - 1)]
        p = np.c_[np.asarray(px, float), np.ones(len(px))] @ H.T
        return p[:, :2] / p[:, 2:3]

    def to_ground(self, frame: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        return self.site.to_metres(self.frame_to_ref(frame, np.c_[u, v]))

    def registered(self, frame: int) -> bool:
        return self.failed is None or not bool(self.failed[min(frame, len(self.failed) - 1)])

    def to_pixels(self, frame: int, xyz: np.ndarray) -> np.ndarray:
        ref = self.site.to_px(np.asarray(xyz, float)[:, :2])
        if self.Hs is None:
            return ref
        Hinv = np.linalg.inv(self.Hs[min(frame, len(self.Hs) - 1)])
        p = np.c_[ref, np.ones(len(ref))] @ Hinv.T
        return p[:, :2] / p[:, 2:3]


def add_columns(traj_csv: Path, cam: RealCamera, times: np.ndarray | None) -> dict:
    """wx, wy (metres, at the site's ref_point on the box), pose_exact (frame registered) and, when
    frame timestamps are given, time_s from them. In place; returns counts."""
    rows = list(csv.DictReader(open(traj_csv, newline="")))
    by_frame = defaultdict(list)
    for r in rows:
        by_frame[int(r["frame"])].append(r)
    k = cam.site.ref_point - 0.5
    for f, rs in by_frame.items():
        u = np.array([float(r["cx"]) for r in rs])
        v = np.array([float(r["cy"]) + k * float(r["h"]) for r in rs])
        g = cam.to_ground(f, u, v)
        ok = int(cam.registered(f))
        for i, r in enumerate(rs):
            r["wx"], r["wy"] = round(float(g[i, 0]), 3), round(float(g[i, 1]), 3)
            r["pose_exact"] = ok
            if times is not None and f < len(times):
                r["time_s"] = round(float(times[f]), 4)
    cols = [c for c in rows[0] if c not in ("wx", "wy", "pose_exact")] + ["wx", "wy", "pose_exact"]
    with open(traj_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return {"rows": len(rows), "frames_unregistered": sum(1 for f in by_frame if not cam.registered(f))}


def car_length_check(traj_csv: Path, cam: RealCamera) -> dict:
    """Median ground length of car boxes (the longer side, measured in metres). For a camera looking
    down this should be ~4-5 m; far off means the calibration is wrong. A tilted camera adds the
    car's height to its box, so expect a little more there."""
    lengths = []
    for r in csv.DictReader(open(traj_csv, newline="")):
        if r["class"] != "car":
            continue
        f, cx, cy, w, h = int(r["frame"]), float(r["cx"]), float(r["cy"]), float(r["w"]), float(r["h"])
        c = cam.to_ground(f, np.array([cx - w / 2, cx + w / 2, cx, cx]), np.array([cy, cy, cy - h / 2, cy + h / 2]))
        lengths.append(max(np.hypot(*(c[1] - c[0])), np.hypot(*(c[3] - c[2]))))
    if not lengths:
        return {"cars": 0}
    q = np.percentile(lengths, [25, 50, 75])
    return {"cars": len(lengths), "car_length_m": {"p25": round(q[0], 2), "median": round(q[1], 2), "p75": round(q[2], 2)},
            "expected_m": list(CAR_LENGTH_M),
            "scale_factor_to_4p6m": round(4.6 / q[1], 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", type=Path, help="Site file (zone_tool.py)")
    ap.add_argument("trajectories", type=Path, help="trajectories_final.csv of that clip (scene_map.npz next to it)")
    ap.add_argument("--scene-out", type=Path, default=None, help="Scene JSON in metres (default: <site>.scene.json)")
    ap.add_argument("--no-pts", action="store_true", help="Keep the CSV's time_s instead of frame timestamps")
    ap.add_argument("--check", action="store_true", help="Car-length sanity check of the calibration")
    args = ap.parse_args()

    site = Site(args.site)
    cam = RealCamera(site, args.trajectories.with_name("scene_map.npz"))
    if cam.Hs is None:
        print("[real] no scene_map.npz next to the trajectories: camera assumed still (run scene_map.py for a moving one)")
    times = None if args.no_pts else frame_times(site.video)
    stats = add_columns(args.trajectories, cam, times)
    scene_out = args.scene_out or args.site.with_suffix(".scene.json")
    scene_out.write_text(json.dumps(site.scene(), indent=1))
    print(f"[real] {stats['rows']} rows -> wx, wy ({site.data['calibration']['method']} calibration, "
          f"{'left' if site.left_handed else 'right'}-handed metres), {stats['frames_unregistered']} frames unregistered; "
          f"time from {'frame timestamps' if times is not None else 'the CSV'}; scene -> {scene_out}")
    if args.check:
        print(json.dumps(car_length_check(args.trajectories, cam), indent=1))


if __name__ == "__main__":
    main()
