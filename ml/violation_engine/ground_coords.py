"""Ground-plane (metric) coordinates for a CARLA recorded flight (Stage 4 foundation).

Violation rules need positions in metres on the ground: speed in km/h, "stationary" and
heading all mean nothing in pixels when the drone itself moves. For a CARLA flight the
camera's world pose is known, so every pixel can be projected exactly: a ray from the
camera through the pixel, intersected with the horizontal plane z = ground_z.

Camera poses: flights recorded from 2026-10-03 on have the RGB camera's pose on every frame
in frame_times.csv, which is used as is. Older flights only have camera_poses.csv, logged on
seg-camera ticks (~every 7-8 sim ticks, `record_flight.py --labels`); RGB frames in between
get the pose interpolated by carla_frame: position linearly, rotation by slerp (the camera
looks straight down, pitch -90, where yaw and roll are degenerate, so Euler angles must not
be interpolated). Interpolation is a fallback only: hiding every other logged pose on flight
20261002_003959 (a manually yawed flight) gave 0.3 m median / 3.1 m p95 error at the frame
corners, too noisy for speeds.

ground_z: a flat plane at the median vehicle-actor z of the flight plus BOX_CENTRE_Z (a
nadir box centre is about mid-way up the vehicle, not on the road). Hills and bridges
break the flat assumption; `--check` reports the error this causes.

Outputs:
    wx, wy columns added to the trajectory CSV in place (CARLA world metres; readers must
    tolerate them missing, same as map_x/map_y), and pose_exact (1 = the frame's camera pose
    was logged, 0 = interpolated; kinematics.py uses only exact frames as measurements when
    a flight has both)

--check: projects the auto-label boxes (autolabel/gt.csv, track_id = CARLA actor id) and
compares them with the true vehicle centres from vehicle_poses.csv, on the frames where
both exist. That measures the projection alone, with no detector or tracker involved.

Usage:
    python ml/violation_engine/ground_coords.py <flight dir> <trajectories.csv>
    python ml/violation_engine/ground_coords.py <flight dir> --check
"""

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "detection"))
from carla_autolabel import ue_matrix  # noqa: E402

BOX_CENTRE_Z = 0.75  # m above the road: a car's box centre seen from above (~half its height)
MAX_POSE_GAP = 40  # sim ticks; a frame further than this from any logged pose has no position


class FlightCamera:
    """Per-frame camera pose of one recorded flight, and pixel <-> ground projection."""

    def __init__(self, flight: Path):
        meta = json.loads((flight / "metadata.json").read_text())
        cam = meta.get("camera", {})
        self.W, self.H = cam.get("width", 1920), cam.get("height", 1080)
        self.f = self.W / (2 * math.tan(math.radians(cam.get("fov", 90.0)) / 2))

        with open(flight / "frame_times.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.frame_cf = {int(r["frame"]): int(r["carla_frame"]) for r in rows}
        self.frame_time = {int(r["frame"]): float(r["time_s"]) for r in rows}
        # flights recorded from 2026-10-03 on log the RGB camera's own pose on every frame
        self.frame_pose = {}
        if rows and "yaw" in rows[0]:
            for r in rows:
                m = ue_matrix((float(r["x"]), float(r["y"]), float(r["z"])),
                              (float(r["pitch"]), float(r["yaw"]), float(r["roll"])))
                self.frame_pose[int(r["frame"])] = (m[:3, 3], m[:3, :3])

        cfs, pos, mats = [], [], []
        with open(flight / "camera_poses.csv", newline="") as fh:
            for r in csv.DictReader(fh):
                m = ue_matrix((float(r["x"]), float(r["y"]), float(r["z"])),
                              (float(r["pitch"]), float(r["yaw"]), float(r["roll"])))
                cfs.append(int(r["carla_frame"]))
                pos.append(m[:3, 3])
                mats.append(m[:3, :3])
        if len(cfs) < 2:
            raise SystemExit(f"{flight / 'camera_poses.csv'}: need at least 2 poses (recorded with --labels?)")
        self.pose_cf = np.array(cfs)
        self.pose_pos = np.array(pos)
        self._slerp = Slerp(self.pose_cf, Rotation.from_matrix(np.array(mats)))
        self.ground_z = flight_ground_z(flight) + BOX_CENTRE_Z

    def pose_exact(self, frame: int) -> bool:
        """True if this frame's camera pose was logged, not interpolated."""
        if frame in self.frame_pose:
            return True
        cf = self.frame_cf.get(frame)
        if cf is None:
            return False
        k = int(np.searchsorted(self.pose_cf, cf))
        return k < len(self.pose_cf) and int(self.pose_cf[k]) == cf

    def pose(self, frame: int) -> tuple[np.ndarray, np.ndarray] | None:
        """-> (camera position (3,), camera-to-world rotation (3,3)) or None if no pose is close enough."""
        if frame in self.frame_pose:
            return self.frame_pose[frame]
        cf = self.frame_cf.get(frame)
        if cf is None:
            return None
        k = int(np.searchsorted(self.pose_cf, cf))
        nearest = min(abs(cf - self.pose_cf[j]) for j in (k - 1, k) if 0 <= j < len(self.pose_cf))
        if nearest > MAX_POSE_GAP:
            return None
        cf_c = float(np.clip(cf, self.pose_cf[0], self.pose_cf[-1]))
        pos = np.array([np.interp(cf_c, self.pose_cf, self.pose_pos[:, i]) for i in range(3)])
        return pos, self._slerp([cf_c]).as_matrix()[0]

    def to_ground(self, frame: int, u: np.ndarray, v: np.ndarray, ground_z: float | None = None) -> np.ndarray | None:
        """Pixels (u, v arrays) of one frame -> (N, 2) world x, y on the plane z = ground_z."""
        p = self.pose(frame)
        if p is None:
            return None
        pos, R = p
        gz = self.ground_z if ground_z is None else ground_z
        # UE camera frame: x forward, y right, z up (u = W/2 + f*y/x, v = H/2 - f*z/x)
        d_cam = np.stack([np.ones_like(u, dtype=float), (np.asarray(u, float) - self.W / 2) / self.f,
                          -(np.asarray(v, float) - self.H / 2) / self.f])
        d = R @ d_cam
        t = (gz - pos[2]) / d[2]
        return (pos[:2, None] + t * d[:2]).T

    def to_pixels(self, frame: int, xyz: np.ndarray) -> np.ndarray | None:
        """World points (N, 3) -> (N, 2) pixels of one frame (NaN for points behind the camera)."""
        p = self.pose(frame)
        if p is None:
            return None
        pos, R = p
        c = R.T @ (np.asarray(xyz, float) - pos).T
        with np.errstate(divide="ignore", invalid="ignore"):
            uv = np.stack([self.W / 2 + self.f * c[1] / c[0], self.H / 2 - self.f * c[2] / c[0]], axis=1)
        uv[c[0] < 0.5] = np.nan
        return uv


def flight_ground_z(flight: Path) -> float:
    """Median z of the vehicle actors over the flight (the road height, for a flat area)."""
    with open(flight / "vehicle_poses.csv", newline="") as fh:
        zs = [float(r["z"]) for r in csv.DictReader(fh)]
    return float(np.median(zs)) if zs else 0.0


def add_world_columns(csv_path: Path, cam: FlightCamera) -> tuple[int, int]:
    """Adds (or overwrites) wx, wy in a trajectory CSV, in place (blank where the frame has
    no camera pose). Returns (rows, rows without a position)."""
    rows = list(csv.DictReader(open(csv_path, newline="")))
    if not rows:
        return 0, 0
    by_frame = defaultdict(list)
    for r in rows:
        by_frame[int(r["frame"])].append(r)
    missing = 0
    for frame, rs in by_frame.items():
        g = cam.to_ground(frame, np.array([float(r["cx"]) for r in rs]), np.array([float(r["cy"]) for r in rs]))
        exact = int(cam.pose_exact(frame))
        for i, r in enumerate(rs):
            r["pose_exact"] = exact
            if g is None:
                r["wx"] = r["wy"] = ""
                missing += 1
            else:
                r["wx"], r["wy"] = round(float(g[i, 0]), 3), round(float(g[i, 1]), 3)
    columns = [k for k in rows[0] if k not in ("wx", "wy", "pose_exact")] + ["wx", "wy", "pose_exact"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return len(rows), missing


def true_centres(flight: Path) -> dict[int, dict[str, np.ndarray]]:
    """carla_frame -> {actor id: world centre of its 3D box (3,)} from vehicle_poses.csv + actors.json."""
    actors = json.loads((flight / "actors.json").read_text())
    out = defaultdict(dict)
    with open(flight / "vehicle_poses.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            a = actors.get(r["id"])
            if a is None:
                continue
            m = ue_matrix((float(r["x"]), float(r["y"]), float(r["z"])),
                          (float(r["pitch"]), float(r["yaw"]), float(r["roll"])))
            out[int(r["carla_frame"])][r["id"]] = (m @ [*a["bbox"]["loc"], 1.0])[:3]
    return out


def check(flight: Path, cam: FlightCamera) -> dict:
    """Projection error of the auto-label box centres against the true vehicle centres."""
    truth = true_centres(flight)
    errs, err_flat, rel_z = [], [], []
    with open(flight / "autolabel" / "gt.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            frame, cf = int(r["frame"]), cam.frame_cf.get(int(r["frame"]))
            true = truth.get(cf, {}).get(r["track_id"])
            if true is None:
                continue
            u, v = np.array([float(r["cx"])]), np.array([float(r["cy"])])
            g = cam.to_ground(frame, u, v, ground_z=true[2])  # exact height: projection/pose error only
            gf = cam.to_ground(frame, u, v)  # flat plane, as used by add_world_columns
            if g is None:
                continue
            errs.append(float(np.hypot(*(g[0] - true[:2]))))
            err_flat.append(float(np.hypot(*(gf[0] - true[:2]))))
            rel_z.append(float(true[2] - cam.ground_z))
    if not errs:
        raise SystemExit("No auto-label box matched a vehicle pose — run carla_autolabel.py first.")
    q = lambda a, p: round(float(np.percentile(a, p)), 2)  # noqa: E731
    return {"boxes": len(errs), "ground_z": round(cam.ground_z, 2),
            "err_true_height_m": {"median": q(errs, 50), "p95": q(errs, 95)},
            "err_flat_plane_m": {"median": q(err_flat, 50), "p95": q(err_flat, 95), "max": q(err_flat, 100)},
            "true_centre_minus_plane_z_m": {"p5": q(rel_z, 5), "median": q(rel_z, 50), "p95": q(rel_z, 95)}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path, help="Recorded flight folder (simulation/data_export/recorded_flights/<id>)")
    ap.add_argument("trajectories", type=Path, nargs="?", help="Trajectory CSV to add wx, wy to")
    ap.add_argument("--check", action="store_true", help="Score the projection against the auto-labels")
    args = ap.parse_args()

    cam = FlightCamera(args.flight)
    if args.check:
        print(json.dumps(check(args.flight, cam), indent=1))
    if args.trajectories:
        n, missing = add_world_columns(args.trajectories, cam)
        print(f"[ground] wx, wy added to {n} rows of {args.trajectories} ({missing} without a camera pose)")
    if not args.check and not args.trajectories:
        ap.error("give a trajectories CSV and/or --check")


if __name__ == "__main__":
    main()
