"""Smoothed position, speed and heading per track (Violation Engine, Layer 3).

docs/Violation_Engine_Architecture.md, Section 5.3. Input: a trajectory CSV with ground
positions in metres (wx, wy from ground_coords.py). Every track is run through a Kalman
filter and then an RTS smoother (forward pass, then a backward pass that also uses later
frames), one constant-acceleration model per axis:

    state  [p, v, a]       position (m), velocity (m/s), acceleration (m/s^2)
    model  white-noise jerk with spectral density JERK_Q
    meas.  p only, sigma MEAS_SIGMA_M (the projection error measured by ground_coords.py --check)

Frame-to-frame differences are useless for speed: 0.1 m of position noise at ~27 fps is
about +-10 km/h. The smoother uses the whole track instead.

Boxes touching the frame edge are cut, so their centre is wrong: they are not used as
measurements (the track is predicted through them) and are marked visible = 0. A
measurement more than GATE_SIGMA standard deviations from the prediction is skipped
(a one-frame box glitch); after MAX_REJECTS rejections in a row the next one is taken, so
the filter cannot run away on extrapolation.

A track is cut where two consecutive measurements are more than SPLIT_GAP_S apart, or would
need more than SPLIT_SPEED_KMH or an impossible acceleration (an ID switch between two
vehicles); later pieces get new ids.

Output (kinematics.csv): frame, time_s, track_id, src_track_id, class, conf, visible, x, y,
vx, vy, speed_kmh, speed_sigma_kmh, heading_deg (CARLA convention: atan2(vy, vx); blank
below HEADING_MIN_KMH, where the direction of a near-stationary car is noise). visible = 1
only for a full box with a real measurement within SUPPORT_S on both sides; the moving-
vehicle rules (speeding, wrong-way, lane, U-turn) only trust those rows.

--check <flight>: speed and heading error against CARLA's true vehicle motion
(vehicle_poses.csv), for the smoother and for plain frame differences.

Usage:
    python ml/violation_engine/kinematics.py <trajectories with wx, wy>.csv
    python ml/violation_engine/kinematics.py <trajectories>.csv --check simulation/data_export/recorded_flights/<id>
"""

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

MEAS_SIGMA_M = 0.15  # ground_coords.py --check: median 0.11-0.12 m, p95 0.74-0.88 m over 3 flights
JERK_Q = 2.0  # white-noise jerk spectral density (m^2/s^5); larger = follows braking/turning faster, noisier
GATE_SIGMA = 5.0
MAX_REJECTS = 3
WARMUP_S = 0.5  # no outlier gate during a track's first half second
SPLIT_GAP_S = 2.0  # no measurement for this long: a new piece of track
SPLIT_SPEED_KMH = 150.0  # two consecutive measurements further apart than this speed allows: ID switch
SPLIT_ACCEL_MPS2 = 15.0  # ... or a velocity change no car can make (hard braking is ~10 m/s^2)
SPLIT_SLACK_MPS = 5.0  # allowance for position noise (p95 ~0.8 m over ~0.2 s between measurements)
SPLIT_MIN_DT_S = 0.2  # velocities for the jump test are taken over at least this long
SUPPORT_S = 0.4  # a row is "supported" with a measurement this close on both sides
EDGE_PX = 2.0  # a box this close to the frame border is cut by it
HEADING_MIN_KMH = 3.0


def _ca_matrices(dt: float, q: float) -> tuple[np.ndarray, np.ndarray]:
    """Constant-acceleration transition F and process noise Q for one axis, time step dt."""
    F = np.array([[1, dt, dt * dt / 2], [0, 1, dt], [0, 0, 1]])
    Q = q * np.array([[dt ** 5 / 20, dt ** 4 / 8, dt ** 3 / 6],
                      [dt ** 4 / 8, dt ** 3 / 3, dt ** 2 / 2],
                      [dt ** 3 / 6, dt ** 2 / 2, dt]])
    return F, Q


def smooth_axis(t: np.ndarray, z: np.ndarray, use: np.ndarray, meas_sigma: float = MEAS_SIGMA_M,
                q: float = JERK_Q, gate: float = GATE_SIGMA) -> tuple[np.ndarray, np.ndarray]:
    """Kalman + RTS smoother for one axis. t (s, increasing), z (m), use (bool: measurement usable).
    Returns smoothed states (N, 3) and covariances (N, 3, 3)."""
    n = len(t)
    R = meas_sigma ** 2
    H = np.array([1.0, 0.0, 0.0])
    xf, Pf = np.zeros((n, 3)), np.zeros((n, 3, 3))  # filtered
    xp, Pp = np.zeros((n, 3)), np.zeros((n, 3, 3))  # predicted
    Fs = np.zeros((n, 3, 3))
    first = int(np.flatnonzero(use)[0]) if use.any() else 0
    x = np.array([z[first], 0.0, 0.0])
    P = np.diag([R, 25.0 ** 2, 5.0 ** 2])  # up to ~90 km/h and 5 m/s^2 before the first update
    rejected = 0
    for k in range(n):
        if k > 0:
            F, Q = _ca_matrices(max(t[k] - t[k - 1], 1e-3), q)
            x, P = F @ x, F @ P @ F.T + Q
            Fs[k] = F
        xp[k], Pp[k] = x, P
        if use[k]:
            s = H @ P @ H + R
            innov = z[k] - H @ x
            # the gate drops one-off glitches; after MAX_REJECTS in a row the filter, not the data,
            # is wrong (it would otherwise run away on pure extrapolation), so take the measurement
            # no gating while the track is young: its velocity starts at 0, so the first real
            # measurements of a moving car look like outliers (seen on the highway clip: a car at
            # ~75 km/h read 19 km/h for its first half second)
            warm = t[k] - t[first] < WARMUP_S
            if k == first or warm or innov * innov <= gate * gate * s or rejected >= MAX_REJECTS:
                K = P @ H / s
                x = x + K * innov
                P = P - np.outer(K, H @ P)
                rejected = 0
            else:
                rejected += 1
        xf[k], Pf[k] = x, P
    xs, Ps = xf.copy(), Pf.copy()
    for k in range(n - 2, -1, -1):
        C = Pf[k] @ Fs[k + 1].T @ np.linalg.inv(Pp[k + 1])
        xs[k] = xf[k] + C @ (xs[k + 1] - xp[k + 1])
        Ps[k] = Pf[k] + C @ (Ps[k + 1] - Pp[k + 1]) @ C.T
    return xs, Ps


def smooth_track(t: np.ndarray, x: np.ndarray, y: np.ndarray, use: np.ndarray, **kw) -> dict[str, np.ndarray]:
    """Smoothed kinematics of one track -> dict of per-frame arrays."""
    sx, Px = smooth_axis(t, x, use, **kw)
    sy, Py = smooth_axis(t, y, use, **kw)
    vx, vy = sx[:, 1], sy[:, 1]
    speed = np.hypot(vx, vy)
    with np.errstate(divide="ignore", invalid="ignore"):
        var = (vx * vx * Px[:, 1, 1] + vy * vy * Py[:, 1, 1]) / (speed * speed)
    var = np.where(speed > 1e-6, var, (Px[:, 1, 1] + Py[:, 1, 1]) / 2)
    heading = np.degrees(np.arctan2(vy, vx))
    heading[speed * 3.6 < HEADING_MIN_KMH] = np.nan
    return {"x": sx[:, 0], "y": sy[:, 0], "vx": vx, "vy": vy, "speed_kmh": speed * 3.6,
            "speed_sigma_kmh": np.sqrt(var) * 3.6, "heading_deg": heading}


def edge_mask(cx, cy, w, h, frame_w: int | None, frame_h: int | None) -> np.ndarray:
    """True where the box is clear of the frame border (its centre can be trusted)."""
    if not frame_w or not frame_h:
        return np.ones(len(cx), dtype=bool)
    return ((cx - w / 2 > EDGE_PX) & (cy - h / 2 > EDGE_PX) &
            (cx + w / 2 < frame_w - EDGE_PX) & (cy + h / 2 < frame_h - EDGE_PX))


def measurement_mask(tr: dict, visible: np.ndarray) -> np.ndarray:
    """Which rows the filter may use as measurements: full boxes, not gap-filled (interp = 1:
    postprocess_tracks.py drew them as straight lines in pixels while the camera moved), and,
    on flights where some frames' camera poses are interpolated, only the exactly-posed frames.
    Measured on Town05 flight 20261002_001635 (old flight, pose every ~8th frame), speed /
    heading / parked-car error p95: all rows 23 km/h / 70 deg / 32 km/h; this mask
    8 km/h / 7 deg / 2 km/h."""
    use = visible & (tr["interp"] < 0.5)
    exact = tr["pose_exact"] > 0.5
    if exact.any() and not exact.all():
        use &= exact
    return use


def load_tracks(csv_path: Path, x_col: str = "wx", y_col: str = "wy") -> dict[int, dict[str, np.ndarray]]:
    """track_id -> column arrays sorted by time; rows without a ground position are dropped."""
    cols = defaultdict(lambda: defaultdict(list))
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get(x_col, "") in ("", None):
                continue
            c = cols[int(r["track_id"])]
            for k in ("frame", "time_s", "cx", "cy", "w", "h", "conf"):
                c[k].append(float(r[k]))
            c["x"].append(float(r[x_col]))
            c["y"].append(float(r[y_col]))
            c["class"].append(r["class"])
            c["interp"].append(float(r.get("interp") or 0))
            c["pose_exact"].append(float(r.get("pose_exact") or 1))
    out = {}
    for tid, c in cols.items():
        order = np.argsort(c["time_s"], kind="stable")
        tr = {k: (np.array(v)[order] if k != "class" else [v[i] for i in order]) for k, v in c.items()}
        keep = np.r_[True, np.diff(tr["time_s"]) > 1e-6]  # drop duplicate timestamps
        out[tid] = {k: (v[keep] if k != "class" else [v[i] for i in np.flatnonzero(keep)]) for k, v in tr.items()}
    return out


def split_points(t: np.ndarray, x: np.ndarray, y: np.ndarray, use: np.ndarray) -> list[int]:
    """Row indices where a track must be cut: consecutive measurements further apart in time than
    SPLIT_GAP_S, implying more than SPLIT_SPEED_KMH, or a velocity change faster than
    SPLIT_ACCEL_MPS2 (the tracker / stitcher put two different vehicles under one ID - seen on
    Town05 flight 20261002_001635: track 11617 jumped from a car at 7 km/h to a parked car 11 m
    away in 0.4 s, which read as 68 km/h speeding)."""
    idx = np.flatnonzero(use)
    cuts = []
    v_prev = None
    a = idx[0] if len(idx) else 0
    prev = a
    for b in idx[1:]:
        if t[b] - t[prev] > SPLIT_GAP_S:  # a long gap: always a new piece
            cuts.append(int(b))
            v_prev, a, prev = None, b, b
            continue
        prev = b
        dt = t[b] - t[a]
        if dt < SPLIT_MIN_DT_S:  # judge jumps over >= SPLIT_MIN_DT_S: at 60 fps, 0.1 m of noise
            continue             # between neighbouring frames already looks like 6 m/s
        v = np.array([x[b] - x[a], y[b] - y[a]]) / dt
        jump = v_prev is not None and np.hypot(*(v - v_prev)) > SPLIT_ACCEL_MPS2 * dt + SPLIT_SLACK_MPS
        if np.hypot(*v) * 3.6 > SPLIT_SPEED_KMH or jump:
            cuts.append(int(b))
            v_prev = None
        else:
            v_prev = v
        a = b
    return cuts


def supported_mask(t: np.ndarray, use: np.ndarray, window: float = SUPPORT_S) -> np.ndarray:
    """True where a real measurement lies within `window` s on both sides (or the row is one):
    elsewhere the smoother is extrapolating or bridging a long gap."""
    mt = t[use]
    if not len(mt):
        return np.zeros(len(t), bool)
    k = np.searchsorted(mt, t)
    before = np.where(k > 0, t - mt[np.maximum(k - 1, 0)], np.inf)
    k2 = np.searchsorted(mt, t, side="left")
    after = np.where(k2 < len(mt), mt[np.minimum(k2, len(mt) - 1)] - t, np.inf)
    return use | ((before <= window) & (after <= window))


def compute(csv_path: Path, frame_w: int | None = None, frame_h: int | None = None, **kw) -> list[dict]:
    """Kinematics rows for every track of a trajectory CSV. A track cut by split_points() gets
    new ids for its later pieces: track_id * 1000 + piece (src_track_id keeps the original)."""
    rows = []
    for src_tid, tr in load_tracks(csv_path).items():
        if len(tr["time_s"]) < 2:
            continue
        vis_all = edge_mask(tr["cx"], tr["cy"], tr["w"], tr["h"], frame_w, frame_h)
        use_all = measurement_mask(tr, vis_all)
        bounds = [0] + split_points(tr["time_s"], tr["x"], tr["y"], use_all) + [len(tr["time_s"])]
        for piece, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
            rows += _piece_rows(tr, a, b, vis_all, use_all, src_tid, src_tid if piece == 0 else src_tid * 1000 + piece, **kw)
    rows.sort(key=lambda r: (r["frame"], r["track_id"]))
    return rows


def _piece_rows(tr, a, b, vis_all, use_all, src_tid, tid, **kw) -> list[dict]:
    t, vis, use = tr["time_s"][a:b], vis_all[a:b], use_all[a:b]
    if use.sum() < 2:
        return []
    k = smooth_track(t, tr["x"][a:b], tr["y"][a:b], use, **kw)
    sup = supported_mask(t, use)
    rows = []
    for j in range(len(t)):
        i = a + j
        rows.append({"frame": int(tr["frame"][i]), "time_s": round(float(t[j]), 4), "track_id": tid,
                     "src_track_id": src_tid, "class": tr["class"][i], "conf": tr["conf"][i],
                     "visible": int(vis[j] and sup[j]),  # full box and backed by real measurements
                     "x": round(float(k["x"][j]), 3), "y": round(float(k["y"][j]), 3),
                     "vx": round(float(k["vx"][j]), 3), "vy": round(float(k["vy"][j]), 3),
                     "speed_kmh": round(float(k["speed_kmh"][j]), 2),
                     "speed_sigma_kmh": round(float(k["speed_sigma_kmh"][j]), 2),
                     "heading_deg": "" if math.isnan(k["heading_deg"][j]) else round(float(k["heading_deg"][j]), 1)})
    return rows


def write_rows(rows: list[dict], out: Path) -> None:
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["frame"])
        w.writeheader()
        w.writerows(rows)


def true_motion(flight: Path) -> dict[str, dict[str, np.ndarray]]:
    """actor id -> {t, x, y, speed_kmh, heading_deg} from vehicle_poses.csv (box centres), on the
    flight's own time base (frame_times.csv time_s, interpolated over carla_frame for seg ticks
    without an RGB frame), central differences over +-2 logged ticks."""
    from ground_coords import true_centres  # noqa: PLC0415  (only needed for --check)
    with open(flight / "frame_times.csv", newline="") as f:
        ft = list(csv.DictReader(f))
    cf = np.array([int(r["carla_frame"]) for r in ft])
    tcol = "sim_time" if ft and "sim_time" in ft[0] else "time_s"
    tt = np.array([float(r[tcol]) for r in ft])
    per_actor = defaultdict(list)
    for c, actors in true_centres(flight).items():
        for aid, p in actors.items():
            per_actor[aid].append((float(np.interp(c, cf, tt)), p[0], p[1]))
    out = {}
    for aid, pts in per_actor.items():
        pts.sort()
        a = np.array(pts)
        if len(a) < 5:
            continue
        t, x, y = a[:, 0], a[:, 1], a[:, 2]
        vx, vy = np.full(len(t), np.nan), np.full(len(t), np.nan)
        dt = t[4:] - t[:-4]
        vx[2:-2], vy[2:-2] = (x[4:] - x[:-4]) / dt, (y[4:] - y[:-4]) / dt
        out[aid] = {"t": t, "x": x, "y": y, "speed_kmh": np.hypot(vx, vy) * 3.6,
                    "heading_deg": np.degrees(np.arctan2(vy, vx))}
    return out


def check(rows: list[dict], raw_csv: Path, flight: Path, match_m: float = 2.0) -> dict:
    """Speed / heading error of the smoothed rows (and of raw frame differences) vs CARLA truth."""
    truth = true_motion(flight)
    ids = list(truth)
    # truth positions resampled at any time t: nearest logged sample within 0.15 s
    def truth_at(t: float):
        best = []
        for aid in ids:
            tr = truth[aid]
            j = int(np.searchsorted(tr["t"], t))
            for k in (j - 1, j):
                if 0 <= k < len(tr["t"]) and abs(tr["t"][k] - t) <= 0.15 and not np.isnan(tr["speed_kmh"][k]):
                    best.append((aid, k))
                    break
        return best

    # raw frame-difference speed, same tracks, for comparison
    raw_speed = {}
    for tid, tr in load_tracks(raw_csv).items():
        for i in range(1, len(tr["time_s"])):
            dt = tr["time_s"][i] - tr["time_s"][i - 1]
            if dt > 0:
                raw_speed[(tid, int(tr["frame"][i]))] = math.hypot(tr["x"][i] - tr["x"][i - 1],
                                                                   tr["y"][i] - tr["y"][i - 1]) / dt * 3.6
    by_time = defaultdict(list)
    for r in rows:
        by_time[r["time_s"]].append(r)
    err_s, err_raw, err_h, still = [], [], [], []
    cache = {}
    for t, rs in by_time.items():
        cands = cache.setdefault(t, truth_at(t))
        if not cands:
            continue
        for r in rs:
            if not r["visible"]:
                continue
            d = [(math.hypot(truth[a]["x"][k] - r["x"], truth[a]["y"][k] - r["y"]), a, k) for a, k in cands]
            dist, a, k = min(d)
            if dist > match_m:
                continue
            ts, th = truth[a]["speed_kmh"][k], truth[a]["heading_deg"][k]
            if ts < 1.0:
                still.append(r["speed_kmh"])
                continue
            if ts < 5.0:
                continue
            err_s.append(r["speed_kmh"] - ts)
            rs_ = raw_speed.get((r["track_id"], r["frame"]))
            if rs_ is not None:
                err_raw.append(rs_ - ts)
            if ts > 10 and r["heading_deg"] != "":
                err_h.append(abs((r["heading_deg"] - th + 180) % 360 - 180))
    q = lambda a, p: round(float(np.percentile(np.abs(a), p)), 2) if len(a) else None  # noqa: E731
    return {"moving_rows_matched": len(err_s),
            "speed_abs_err_kmh": {"median": q(err_s, 50), "p95": q(err_s, 95),
                                  "bias": round(float(np.median(err_s)), 2) if err_s else None},
            "raw_frame_diff_abs_err_kmh": {"median": q(err_raw, 50), "p95": q(err_raw, 95)},
            "heading_abs_err_deg_above_10kmh": {"median": q(err_h, 50), "p95": q(err_h, 95)},
            "stationary_rows": len(still),
            "stationary_measured_speed_kmh": {"median": q(still, 50), "p95": q(still, 95)}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trajectories", type=Path, help="Trajectory CSV with wx, wy (ground_coords.py)")
    ap.add_argument("--out", type=Path, default=None, help="Output CSV (default: kinematics.csv next to the input)")
    ap.add_argument("--frame-size", type=int, nargs=2, default=None, metavar=("W", "H"),
                    help="Frame size for the edge test (default: from --check flight metadata, else 1920 1080)")
    ap.add_argument("--meas-sigma", type=float, default=MEAS_SIGMA_M)
    ap.add_argument("--jerk-q", type=float, default=JERK_Q)
    ap.add_argument("--check", type=Path, default=None, metavar="FLIGHT", help="Score against CARLA truth")
    args = ap.parse_args()

    W, H = args.frame_size or (1920, 1080)
    if args.check and not args.frame_size:
        cam = json.loads((args.check / "metadata.json").read_text()).get("camera", {})
        W, H = cam.get("width", W), cam.get("height", H)
    rows = compute(args.trajectories, W, H, meas_sigma=args.meas_sigma, q=args.jerk_q)
    out = args.out or args.trajectories.with_name("kinematics.csv")
    write_rows(rows, out)
    print(f"[kinematics] {len(rows)} rows, {len({r['track_id'] for r in rows})} tracks -> {out}")
    if args.check:
        print(json.dumps(check(rows, args.trajectories, args.check), indent=1))


if __name__ == "__main__":
    main()
