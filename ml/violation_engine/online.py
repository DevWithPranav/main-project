"""Online (live) counterparts of the offline track post-processing, kinematics and engine run
(Build Plan M4). Used by services/live/pipeline.py; kept here so it imports the engine modules
the same flat way they import each other. Nothing in the offline modules is changed.

Offline (run_violations.py) the whole flight is known: stitch_tracklets.py re-links IDs,
postprocess_tracks.py votes classes and drops short tracks, kinematics.py runs a Kalman filter
and then an RTS smoother that looks backward from the end of each track. Live, each frame must
be handled when it arrives, so:

    LiveCamera        ground projection from the pose streamed with each frame (ground_coords.py
                      FlightCamera math, unchanged: flat plane or the road surface)
    OnlineKinematics  per track the same constant-acceleration Kalman filter as kinematics.py
                      (same model, noise, outlier gate, warm-up, split tests), run forward only.
                      Rows go to the engine LAG_S seconds late (a fixed-lag smoother): in that
                      window the RTS pass of kinematics.py runs over the last LAG_S seconds only
                      (mode "fixedlag"), or the plain filtered state is used (mode "filter"). The
                      lag also gives what postprocess_tracks.py needs to decide on a track
                      (>= MIN_DURATION_S long, mean conf >= MIN_MEAN_CONF) and kinematics.py's
                      "supported on both sides" test. Online re-linking replaces stitching: a new
                      tracker ID that starts where a recently lost track is predicted to be (in
                      ground metres) continues that track.
    IncrementalEngine rules.Engine stepped frame by frame (Engine.step / observe, unchanged);
                      reports events as they open (flag time) and when they close.

Usage: imported by services/live/pipeline.py; unit tests in services/live/tests/.
"""

import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

import numpy as np

from ground_coords import BOX_CENTRE_Z, FlightCamera
from kinematics import (GATE_SIGMA, HEADING_MIN_KMH, JERK_Q, MAX_REJECTS, MEAS_SIGMA_M, SPLIT_ACCEL_MPS2,
                        SPLIT_GAP_S, SPLIT_MIN_DT_S, SPLIT_SLACK_MPS, SPLIT_SPEED_KMH, SUPPORT_S, WARMUP_S,
                        _ca_matrices)
from postprocess_tracks import CLASS_MAP, MIN_DURATION_S, MIN_MEAN_CONF
from schemas import condition_of
from stitch_tracklets import COMPATIBLE_CLASSES

LAG_S = 1.0  # engine delay: = MIN_DURATION_S, so a track can be judged on its first row; 2.5x SUPPORT_S
RELINK_MAX_GAP_S = 3.0  # stitch_tracklets.py MOVING_MAX_GAP_S
RELINK_RADIUS_M = 2.5  # predicted vs new position; under a lane width (3.5 m) so neighbours are not merged
RELINK_SPEED_FRAC = 0.3  # gate grows with the distance the lost track would have driven
FORGET_S = 10.0  # drop a lost track's state after this long


class LiveCamera(FlightCamera):
    """FlightCamera whose per-frame poses arrive with the frames instead of from files."""

    def __init__(self, width: int, height: int, fov: float, ground_z: float):
        self.W, self.H = int(width), int(height)
        self.f = self.W / (2 * math.tan(math.radians(fov) / 2))
        self.ground_z = ground_z + BOX_CENTRE_Z  # ground_z: road height (median vehicle z), as flight_ground_z
        self.frame_pose = {}

    def set_pose(self, frame: int, pose6) -> None:
        """pose6: x, y, z, pitch, yaw, roll of the camera (CARLA/UE, as frame_times.csv)."""
        from carla_autolabel import ue_matrix  # on sys.path via ground_coords
        m = ue_matrix(tuple(pose6[:3]), tuple(pose6[3:6]))
        self.frame_pose = {frame: (m[:3, 3], m[:3, :3])}  # only the current frame is ever projected

    def pose(self, frame: int):
        return self.frame_pose.get(frame)

    def pose_exact(self, frame: int) -> bool:
        return frame in self.frame_pose


# --- kinematics -----------------------------------------------------------------------------------

@dataclass
class _Step:
    """One row of a track piece: the forward filter's result plus what the backward pass needs."""
    frame: int
    t: float
    tracker_id: int
    cls: str
    conf: float
    edge_ok: bool
    meas: bool  # used as a measurement
    xf: np.ndarray  # (2, 3) filtered state, axes x / y
    Pf: np.ndarray  # (2, 3, 3)
    xp: np.ndarray  # predicted (before the update)
    Pp: np.ndarray
    C: np.ndarray | None = None  # RTS gain to the next step, set when the next step arrives


@dataclass
class _Piece:
    tid: int  # engine track id: src for piece 0, src * 1000 + n after a split (kinematics.compute)
    steps: deque = field(default_factory=deque)
    n_meas: int = 0
    first_meas_t: float | None = None
    x: np.ndarray | None = None
    P: np.ndarray | None = None
    rejected: np.ndarray = field(default_factory=lambda: np.zeros(2, int))
    last_t: float | None = None
    # split_points() state: anchor measurement, previous measurement time, last velocity
    anchor: tuple | None = None
    prev_meas_t: float | None = None
    v_prev: np.ndarray | None = None


@dataclass
class _Track:
    src: int
    birth_t: float
    last_t: float
    pieces: list = field(default_factory=list)
    votes: Counter = field(default_factory=Counter)
    counts: Counter = field(default_factory=Counter)
    conf_sum: float = 0.0
    n: int = 0
    raw_cls: str = "car"
    meas_t: deque = field(default_factory=deque)  # recent measurement times (support test)
    confirmed: bool = False

    @property
    def cls(self) -> str:
        return max(self.votes, key=lambda c: (self.votes[c], self.counts[c])) if self.votes else self.raw_cls


def _compatible(a: str, b: str) -> bool:
    a, b = CLASS_MAP.get(a, a), CLASS_MAP.get(b, b)
    return a == b or any(a in s and b in s for s in COMPATIBLE_CLASSES)


class OnlineKinematics:
    """Detections of one frame in, kinematics rows (kinematics.py columns) out LAG_S later.

    update(frame, t, dets): dets are dicts with tracker_id, cls, conf, x, y (ground m), edge_ok.
    Returns the current (lag-free, filtered) state of every track seen in this frame, for the live map.
    emit(t): rows for every frame with time <= t - lag, in time order, as [(t, frame, rows), ...].
    flush(): everything still buffered (end of stream)."""

    def __init__(self, lag_s: float = LAG_S, mode: str = "fixedlag", meas_sigma: float = MEAS_SIGMA_M,
                 q: float = JERK_Q, relink: bool = True, min_duration_s: float = MIN_DURATION_S,
                 min_mean_conf: float = MIN_MEAN_CONF):
        if mode not in ("fixedlag", "filter"):
            raise ValueError(mode)
        self.lag, self.mode, self.R, self.q, self.relink = lag_s, mode, meas_sigma ** 2, q, relink
        self.min_duration_s, self.min_mean_conf = min_duration_s, min_mean_conf
        self.tracks: dict[int, _Track] = {}  # src id -> track
        self.id_map: dict[int, int] = {}  # tracker id -> src id
        self._next_src = 1
        self.pending: dict[tuple[float, int], list[tuple[_Track, _Piece, _Step]]] = defaultdict(list)
        self.stats = Counter()

    # -- input -----------------------------------------------------------------------------------
    def update(self, frame: int, t: float, dets: list[dict]) -> list[dict]:
        live = []
        seen_now = set()
        for d in sorted(dets, key=lambda d: -d["conf"]):  # confident boxes claim lost tracks first
            tr = self._track_for(d, t, seen_now)
            seen_now.add(tr.src)
            st = self._add(tr, frame, t, d)
            if st is not None:
                live.append(self._live_state(tr, st))
        self._forget(t)
        return live

    def _track_for(self, d: dict, t: float, seen_now: set) -> _Track:
        tid = int(d["tracker_id"])
        src = self.id_map.get(tid)
        if src is not None and src in self.tracks and src not in seen_now:
            return self.tracks[src]
        if src is None and self.relink:
            best = self._relink_candidate(d, t, seen_now)
            if best is not None:
                self.id_map[tid] = best.src
                self.stats["relinked"] += 1
                return best
        src = self._next_src
        self._next_src += 1
        self.id_map[tid] = src
        self.tracks[src] = _Track(src=src, birth_t=t, last_t=t, raw_cls=d["cls"])
        self.stats["tracks"] += 1
        return self.tracks[src]

    def _relink_candidate(self, d: dict, t: float, seen_now: set) -> _Track | None:
        best, best_d = None, math.inf
        for tr in self.tracks.values():
            gap = t - tr.last_t
            if tr.src in seen_now or gap <= 1e-6 or gap > RELINK_MAX_GAP_S or not _compatible(tr.cls, d["cls"]):
                continue
            pc = tr.pieces[-1] if tr.pieces else None
            if pc is None or pc.x is None:
                continue
            px, py = pc.x[0, 0], pc.x[1, 0]
            vx, vy = pc.x[0, 1], pc.x[1, 1]
            if math.hypot(vx, vy) > 1.0:  # constant velocity through the gap (acceleration is noise here)
                px, py = px + vx * gap, py + vy * gap
            dist = math.hypot(d["x"] - px, d["y"] - py)
            gate = RELINK_RADIUS_M + RELINK_SPEED_FRAC * math.hypot(vx, vy) * gap
            if dist <= gate and dist < best_d:
                best, best_d = tr, dist
        return best

    def _add(self, tr: _Track, frame: int, t: float, d: dict) -> _Step | None:
        if tr.pieces and tr.pieces[-1].last_t is not None and t - tr.pieces[-1].last_t <= 1e-6:
            return None  # duplicate timestamp (kinematics.load_tracks drops them too)
        cls = CLASS_MAP.get(d["cls"], d["cls"])
        tr.votes[cls] += float(d["conf"])
        tr.counts[cls] += 1
        tr.conf_sum += float(d["conf"])
        tr.n += 1
        tr.last_t = t
        tr.raw_cls = d["cls"]
        use = bool(d.get("edge_ok", True)) and d.get("x") is not None
        z = np.array([d["x"], d["y"]], float)
        pc = tr.pieces[-1] if tr.pieces else None
        if pc is None or (use and self._is_split(pc, t, z)):
            pc = _Piece(tid=tr.src if not tr.pieces else tr.src * 1000 + len(tr.pieces))
            tr.pieces.append(pc)
            if len(tr.pieces) > 1:
                self.stats["splits"] += 1
            if use:
                self._is_split(pc, t, z)  # the piece's first measurement: sets its split state
        if use:
            tr.meas_t.append(t)
            while tr.meas_t and tr.meas_t[0] < t - max(self.lag, SUPPORT_S) - SUPPORT_S - 1.0:
                tr.meas_t.popleft()
        st = self._filter(pc, frame, t, d, z, use)
        pc.steps.append(st)
        self.pending[(t, frame)].append((tr, pc, st))
        return st

    def _is_split(self, pc: _Piece, t: float, z: np.ndarray) -> bool:
        """kinematics.split_points, one measurement at a time. Updates the piece's split state."""
        if pc.prev_meas_t is None:
            pc.anchor, pc.prev_meas_t = (t, z), t
            return False
        if t - pc.prev_meas_t > SPLIT_GAP_S:
            return True
        pc.prev_meas_t = t
        ta, za = pc.anchor
        dt = t - ta
        if dt < SPLIT_MIN_DT_S:
            return False
        v = (z - za) / dt
        jump = pc.v_prev is not None and np.hypot(*(v - pc.v_prev)) > SPLIT_ACCEL_MPS2 * dt + SPLIT_SLACK_MPS
        if np.hypot(*v) * 3.6 > SPLIT_SPEED_KMH or jump:
            return True
        pc.v_prev, pc.anchor = v, (t, z)
        return False

    def _filter(self, pc: _Piece, frame: int, t: float, d: dict, z: np.ndarray, use: bool) -> _Step:
        """kinematics.smooth_axis forward pass, both axes at once."""
        if pc.x is None or (use and pc.n_meas == 0):
            # start (again) at the first usable measurement, like smooth_axis; before it, at the raw box
            pc.x = np.array([[z[0], 0.0, 0.0], [z[1], 0.0, 0.0]])
            pc.P = np.repeat(np.diag([self.R, 25.0 ** 2, 5.0 ** 2])[None], 2, axis=0)
            F = np.eye(3)
            fresh = True
        else:
            F, Q = _ca_matrices(max(t - pc.last_t, 1e-3), self.q)
            pc.x = pc.x @ F.T
            pc.P = F @ pc.P @ F.T + Q
            fresh = False
        xp, Pp = pc.x.copy(), pc.P.copy()
        if pc.steps:
            prev = pc.steps[-1]
            # RTS gain of the previous step, now that its successor's prediction is known
            prev.C = prev.Pf @ F.T @ np.linalg.inv(Pp)
        if use:
            if pc.n_meas == 0:
                pc.first_meas_t = t
            warm = t - pc.first_meas_t < WARMUP_S
            for a in range(2):
                s = pc.P[a, 0, 0] + self.R
                innov = z[a] - pc.x[a, 0]
                if fresh or pc.n_meas == 0 or warm or innov * innov <= GATE_SIGMA ** 2 * s or pc.rejected[a] >= MAX_REJECTS:
                    K = pc.P[a, :, 0] / s
                    pc.x[a] = pc.x[a] + K * innov
                    pc.P[a] = pc.P[a] - np.outer(K, pc.P[a, 0, :])
                    pc.rejected[a] = 0
                else:
                    pc.rejected[a] += 1
            pc.n_meas += 1
        pc.last_t = t
        return _Step(frame=frame, t=t, tracker_id=int(d["tracker_id"]), cls=d["cls"], conf=float(d["conf"]),
                     edge_ok=bool(d.get("edge_ok", True)), meas=use, xf=pc.x.copy(), Pf=pc.P.copy(), xp=xp, Pp=Pp)

    def _forget(self, t: float) -> None:
        dead = [s for s, tr in self.tracks.items() if t - tr.last_t > FORGET_S]
        for s in dead:
            del self.tracks[s]
        if dead:
            gone = set(dead)
            self.id_map = {k: v for k, v in self.id_map.items() if v not in gone}

    # -- output ----------------------------------------------------------------------------------
    def emit(self, t_now: float) -> list[tuple[float, int, list[dict]]]:
        out = []
        for key in sorted(k for k in self.pending if k[0] <= t_now - self.lag + 1e-9):
            out.append((key[0], key[1], self._rows(self.pending.pop(key))))
        return out

    def flush(self) -> list[tuple[float, int, list[dict]]]:
        return self.emit(math.inf)

    def _rows(self, items) -> list[dict]:
        rows = []
        for tr, pc, st in items:
            while pc.steps and pc.steps[0] is not st:  # earlier steps are done with
                pc.steps.popleft()
            if not tr.confirmed:
                tr.confirmed = (tr.last_t - tr.birth_t >= self.min_duration_s - 1e-6 and
                                tr.conf_sum / tr.n >= self.min_mean_conf)
            if not tr.confirmed:
                self.stats["rows_unconfirmed"] += 1
                continue
            if pc.n_meas < 2:  # kinematics._piece_rows drops pieces with < 2 measurements
                continue
            x, P = self._state_at(pc, st)
            rows.append(self._row(tr, pc, st, x, P))
        return rows

    def _state_at(self, pc: _Piece, st: _Step) -> tuple[np.ndarray, np.ndarray]:
        if self.mode == "filter" or pc.steps[-1] is st:
            return st.xf, st.Pf
        steps = list(pc.steps)
        k = next(i for i in range(len(steps) - 1, -1, -1) if steps[i] is st)
        xs, Ps = steps[-1].xf, steps[-1].Pf
        for i in range(len(steps) - 2, k - 1, -1):
            s, nxt = steps[i], steps[i + 1]
            xs = s.xf + np.einsum("aij,aj->ai", s.C, xs - nxt.xp)
            Ps = s.Pf + s.C @ (Ps - nxt.Pp) @ np.transpose(s.C, (0, 2, 1))
        return xs, Ps

    def _supported(self, tr: _Track, st: _Step) -> bool:
        if st.meas:
            return True
        before = [m for m in tr.meas_t if m <= st.t]
        after = [m for m in tr.meas_t if m >= st.t]
        if not before:
            return False
        if self.lag < SUPPORT_S:  # cannot look far enough ahead: past side only
            return st.t - before[-1] <= SUPPORT_S
        return bool(after) and st.t - before[-1] <= SUPPORT_S and after[0] - st.t <= SUPPORT_S

    def _row(self, tr: _Track, pc: _Piece, st: _Step, x: np.ndarray, P: np.ndarray) -> dict:
        vx, vy = float(x[0, 1]), float(x[1, 1])
        speed = math.hypot(vx, vy)
        if speed > 1e-6:
            var = (vx * vx * P[0, 1, 1] + vy * vy * P[1, 1, 1]) / (speed * speed)
        else:
            var = (P[0, 1, 1] + P[1, 1, 1]) / 2
        heading = math.degrees(math.atan2(vy, vx)) if speed * 3.6 >= HEADING_MIN_KMH else ""
        return {"frame": st.frame, "time_s": round(st.t, 4), "track_id": pc.tid, "src_track_id": tr.src,
                "class": tr.cls, "conf": st.conf, "visible": int(st.edge_ok and self._supported(tr, st)),
                "x": round(float(x[0, 0]), 3), "y": round(float(x[1, 0]), 3), "vx": round(vx, 3), "vy": round(vy, 3),
                "speed_kmh": round(speed * 3.6, 2), "speed_sigma_kmh": round(math.sqrt(max(var, 0.0)) * 3.6, 2),
                "heading_deg": round(heading, 1) if heading != "" else ""}

    def _live_state(self, tr: _Track, st: _Step) -> dict:
        """Lag-free state for the live map (forward filter only)."""
        vx, vy = float(st.xf[0, 1]), float(st.xf[1, 1])
        sp = math.hypot(vx, vy) * 3.6
        pc = tr.pieces[-1]
        return {"track_id": pc.tid, "src_track_id": tr.src, "cls": tr.cls, "x": round(float(st.xf[0, 0]), 2),
                "y": round(float(st.xf[1, 0]), 2), "speed_kmh": round(sp, 1),
                "heading_deg": round(math.degrees(math.atan2(vy, vx)), 1) if sp >= HEADING_MIN_KMH else None,
                "t_s": round(st.t, 3), "confirmed": tr.confirmed}


# --- engine ---------------------------------------------------------------------------------------

class IncrementalEngine:
    """rules.Engine stepped one frame at a time; reports events as they open and as they close.

    step(t, frame, rows) -> (opened, closed): lists of events.Event (live objects; copy before
    changing). finish() closes what is still open, as Engine.run does at the end of a flight.
    Conditions are set on open (provisional: tags can still change) and again on close, as run()."""

    def __init__(self, engine):
        self.engine = engine
        self._n = 0
        self._closed: set[str] = set()
        self._open: dict[str, object] = {}
        self.t, self.frame = 0.0, 0

    def step(self, t: float, frame: int, rows: list[dict]):
        self.t, self.frame = t, frame
        self.engine.step(t, frame, [self.engine.observe(r) for r in rows])
        return self._collect()

    def finish(self):
        for m in self.engine.monitors:
            m.finish(self.t, self.frame)
        return self._collect()

    def _collect(self):
        events = self.engine.log.events
        opened = []
        for e in events[self._n:]:
            e.condition = condition_of({"type": e.type, "tags": e.tags, "value": e.value})
            self._open[e.event_id] = e
            opened.append(e)
        self._n = len(events)
        closed = []
        for eid, e in list(self._open.items()):
            if e.end_s is not None:
                e.condition = condition_of({"type": e.type, "tags": e.tags, "value": e.value})
                closed.append(e)
                self._closed.add(eid)
                del self._open[eid]
        dis = self.engine.disabled_conditions
        return [e for e in opened if e.condition not in dis], [e for e in closed if e.condition not in dis]

    def open_track_ids(self) -> set[int]:
        return {tid for e in self._open.values() for tid in e.track_ids}
