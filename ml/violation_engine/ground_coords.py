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

Road surface (Build Plan M1/M2): with a lane map that has road heights (export_lane_map.py
"z"), RoadSurface rasterises them and to_ground_on_roads() intersects each pixel's ray with
the road itself: of the heights where the ray lands on a road of that height, the highest
(the surface the camera sees first, e.g. a flyover over a street). Off the roads it falls back
to the flat plane. A road 10 m up seen from 67.6 m lands ~17% further from the nadir on the
flat plane, about one lane at 25 m off-centre (Town05's elevated ring, flight 20261009_201727).

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
    python ml/violation_engine/ground_coords.py <flight dir> --check --scene ml/violation_engine/configs/scenes/Town05.json
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
# taller classes: measured on the 2026-10-10 staging flights (4 flights, pipeline vs true positions,
# outward offset from the nadir point): with 0.75 m, cars came out -0.03 m (median) but bus +1.07 m
# and truck +1.09 m too high, so their positions were pushed outward (a bus 35 m off-centre ~1.1 m
# sideways, enough to read as straddling a line)
CLASS_CENTRE_Z = {"bus": 1.85, "truck": 1.85}


def centre_dz(classes) -> np.ndarray:
    """Per box: its class's box-centre height minus BOX_CENTRE_Z (0 for cars and unknown classes)."""
    return np.array([CLASS_CENTRE_Z.get(str(c), BOX_CENTRE_Z) - BOX_CENTRE_Z for c in classes], float)
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

    def to_ground(self, frame: int, u: np.ndarray, v: np.ndarray, ground_z: float | None = None,
                  dz=0.0) -> np.ndarray | None:
        """Pixels (u, v arrays) of one frame -> (N, 2) world x, y on the plane z = ground_z (+ dz per box:
        centre_dz of its class)."""
        p = self.pose(frame)
        if p is None:
            return None
        pos, R = p
        gz = (self.ground_z if ground_z is None else ground_z) + np.asarray(dz, float)
        # UE camera frame: x forward, y right, z up (u = W/2 + f*y/x, v = H/2 - f*z/x)
        d_cam = np.stack([np.ones_like(u, dtype=float), (np.asarray(u, float) - self.W / 2) / self.f,
                          -(np.asarray(v, float) - self.H / 2) / self.f])
        d = R @ d_cam
        t = (gz - pos[2]) / d[2]
        return (pos[:2, None] + t * d[:2]).T

    def to_ground_on_roads(self, frame: int, u: np.ndarray, v: np.ndarray, surface: "RoadSurface",
                           dz=0.0) -> np.ndarray | None:
        """Like to_ground, but onto the road surface (box centres BOX_CENTRE_Z + dz above it); points that
        land on no road at any height keep the flat-plane position."""
        p = self.pose(frame)
        if p is None:
            return None
        pos, R = p
        d = R @ np.stack([np.ones_like(u, dtype=float), (np.asarray(u, float) - self.W / 2) / self.f,
                          -(np.asarray(v, float) - self.H / 2) / self.f])
        levels = surface.levels  # candidate road heights, ascending
        dz = np.broadcast_to(np.asarray(dz, float), u.shape)
        t = (levels[:, None] + BOX_CENTRE_Z + dz[None, :] - pos[2]) / d[2][None, :]  # (L, N)
        X, Y = pos[0] + t * d[0][None, :], pos[1] + t * d[1][None, :]
        top, low = surface.heights(X, Y)
        tol = surface.LEVEL_STEP_M / 2 + 0.05
        ok = (np.abs(top - levels[:, None]) <= tol) | (np.abs(low - levels[:, None]) <= tol)
        hit = ok.any(axis=0)
        k = len(levels) - 1 - np.argmax(ok[::-1], axis=0)  # the highest level where the ray meets a road
        cols = np.arange(len(k))
        out = self.to_ground(frame, u, v, dz=dz)
        out[hit] = np.stack([X[k, cols], Y[k, cols]], axis=1)[hit]
        return out

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


class RoadSurface:
    """Road height grid from a lane map with per-point heights (lane "z"). Each CELL_M cell keeps the
    highest and the lowest road over it (a flyover above a street has two); NaN off the roads."""
    CELL_M = 0.5
    LEVEL_STEP_M = 0.2

    def __init__(self, scene: dict):
        pts, zs = [], []
        for l in scene.get("lanes", []):
            z = l.get("z")
            c = np.asarray(l["centreline"], float)
            if z is None or len(z) != len(c) or len(c) < 2:
                continue
            z = np.asarray(z, float)
            seg = np.hypot(*np.diff(c, axis=0).T)
            cum = np.r_[0.0, np.cumsum(seg)]
            s = np.arange(0.0, cum[-1] + 1e-9, self.CELL_M / 2)
            p = np.stack([np.interp(s, cum, c[:, 0]), np.interp(s, cum, c[:, 1])], axis=1)
            tz = np.interp(s, cum, z)
            tang = np.gradient(p, axis=0)
            n = np.stack([-tang[:, 1], tang[:, 0]], 1) / np.maximum(np.hypot(*tang.T), 1e-9)[:, None]
            half = float(l.get("width_m", 3.5)) / 2
            offs = np.arange(-half, half + 1e-9, self.CELL_M / 2)
            pts.append((p[:, None, :] + offs[None, :, None] * n[:, None, :]).reshape(-1, 2))
            zs.append(np.repeat(tz, len(offs)))
        if not pts:
            raise ValueError("lane map has no road heights (re-export it with export_lane_map.py)")
        P, Z = np.concatenate(pts), np.concatenate(zs)
        self.origin = P.min(axis=0) - self.CELL_M
        ij = np.floor((P - self.origin) / self.CELL_M).astype(int)
        self.shape = tuple(ij.max(axis=0) + 2)
        self.top = np.full(self.shape, -np.inf)
        self.low = np.full(self.shape, np.inf)
        np.maximum.at(self.top, (ij[:, 0], ij[:, 1]), Z)
        np.minimum.at(self.low, (ij[:, 0], ij[:, 1]), Z)
        self.top[np.isinf(self.top)] = np.nan
        self.low[np.isinf(self.low)] = np.nan
        self.levels = np.arange(np.floor(np.nanmin(Z)), np.nanmax(Z) + self.LEVEL_STEP_M, self.LEVEL_STEP_M)

    @classmethod
    def from_scene(cls, scene: dict) -> "RoadSurface | None":
        """None when the lane map carries no heights or is flat (nothing to correct)."""
        zs = [v for l in scene.get("lanes", []) for v in (l.get("z") or [])]
        if not zs or max(zs) - min(zs) < 0.5:
            return None
        return cls(scene)

    def heights(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(top, low) road height under each point (NaN off the roads); any array shape."""
        i = np.floor((np.asarray(x) - self.origin[0]) / self.CELL_M).astype(int)
        j = np.floor((np.asarray(y) - self.origin[1]) / self.CELL_M).astype(int)
        inside = (i >= 0) & (j >= 0) & (i < self.shape[0]) & (j < self.shape[1])
        top, low = np.full(i.shape, np.nan), np.full(i.shape, np.nan)
        top[inside] = self.top[i[inside], j[inside]]
        low[inside] = self.low[i[inside], j[inside]]
        return top, low


def flight_ground_z(flight: Path) -> float:
    """Median z of the vehicle actors over the flight (the road height, for a flat area)."""
    with open(flight / "vehicle_poses.csv", newline="") as fh:
        zs = [float(r["z"]) for r in csv.DictReader(fh)]
    return float(np.median(zs)) if zs else 0.0


def add_world_columns(csv_path: Path, cam: FlightCamera, surface: RoadSurface | None = None,
                      out_path: Path | None = None) -> tuple[int, int]:
    """Adds (or overwrites) wx, wy in a trajectory CSV (blank where the frame has no camera pose),
    in place or into out_path. With a RoadSurface the boxes go onto the road itself, else onto the
    flat plane. Returns (rows, rows without a position)."""
    rows = list(csv.DictReader(open(csv_path, newline="")))
    if not rows:
        return 0, 0
    by_frame = defaultdict(list)
    for r in rows:
        by_frame[int(r["frame"])].append(r)
    missing = 0
    for frame, rs in by_frame.items():
        u, v = np.array([float(r["cx"]) for r in rs]), np.array([float(r["cy"]) for r in rs])
        dz = centre_dz(r.get("class", "car") for r in rs)
        g = cam.to_ground(frame, u, v, dz=dz) if surface is None else cam.to_ground_on_roads(frame, u, v, surface, dz)
        exact = int(cam.pose_exact(frame))
        for i, r in enumerate(rs):
            r["pose_exact"] = exact
            if g is None:
                r["wx"] = r["wy"] = ""
                missing += 1
            else:
                r["wx"], r["wy"] = round(float(g[i, 0]), 3), round(float(g[i, 1]), 3)
    columns = [k for k in rows[0] if k not in ("wx", "wy", "pose_exact")] + ["wx", "wy", "pose_exact"]
    with open(out_path or csv_path, "w", newline="") as f:
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


def check(flight: Path, cam: FlightCamera, surface: RoadSurface | None = None) -> dict:
    """Projection error of the auto-label box centres against the true vehicle centres: with the
    true height (pose error only), on the flat plane, and on the road surface if given."""
    truth = true_centres(flight)
    errs, err_flat, err_road, rel_z = [], [], [], []
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
            if surface is not None:
                gr = cam.to_ground_on_roads(frame, u, v, surface)
                err_road.append(float(np.hypot(*(gr[0] - true[:2]))))
            rel_z.append(float(true[2] - cam.ground_z))
    if not errs:
        raise SystemExit("No auto-label box matched a vehicle pose — run carla_autolabel.py first.")
    q = lambda a, p: round(float(np.percentile(a, p)), 2)  # noqa: E731
    out = {"boxes": len(errs), "ground_z": round(cam.ground_z, 2),
           "err_true_height_m": {"median": q(errs, 50), "p95": q(errs, 95)},
           "err_flat_plane_m": {"median": q(err_flat, 50), "p95": q(err_flat, 95), "max": q(err_flat, 100)},
           "true_centre_minus_plane_z_m": {"p5": q(rel_z, 5), "median": q(rel_z, 50), "p95": q(rel_z, 95)}}
    if err_road:
        out["err_road_surface_m"] = {"median": q(err_road, 50), "p95": q(err_road, 95), "max": q(err_road, 100)}
        raised = np.array(rel_z) > 2.0  # cars on raised roads (flyover, ramps up)
        if raised.any():
            out["raised_cars_err_m"] = {"boxes": int(raised.sum()),
                                        "flat_median": q(np.array(err_flat)[raised], 50), "flat_p95": q(np.array(err_flat)[raised], 95),
                                        "road_median": q(np.array(err_road)[raised], 50), "road_p95": q(np.array(err_road)[raised], 95)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path, help="Recorded flight folder (simulation/data_export/recorded_flights/<id>)")
    ap.add_argument("trajectories", type=Path, nargs="?", help="Trajectory CSV to add wx, wy to")
    ap.add_argument("--check", action="store_true", help="Score the projection against the auto-labels")
    ap.add_argument("--scene", type=Path, default=None,
                    help="Lane map with road heights: project onto the road surface instead of a flat plane")
    args = ap.parse_args()

    cam = FlightCamera(args.flight)
    surface = RoadSurface.from_scene(json.loads(args.scene.read_text())) if args.scene else None
    if args.check:
        print(json.dumps(check(args.flight, cam, surface), indent=1))
    if args.trajectories:
        n, missing = add_world_columns(args.trajectories, cam, surface)
        print(f"[ground] wx, wy added to {n} rows of {args.trajectories} ({missing} without a camera pose)")
    if not args.check and not args.trajectories:
        ap.error("give a trajectories CSV and/or --check")


if __name__ == "__main__":
    main()
