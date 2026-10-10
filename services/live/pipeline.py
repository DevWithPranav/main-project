"""Live detector service (Build Plan M4): frames in, vehicle states and violation events out.

    frames (transport.py: stream_flight.py or replay_source.py)
      -> detection + tracking (same weights, conf, dedupe and TrackTrack config as the offline
         process_recorded_flight.py run, frame by frame with persist=True)
      -> ground coordinates (the streamed camera pose; road surface from the lane map)
      -> online kinematics (ml/violation_engine/online.py: forward Kalman, fixed-lag smoothing,
         online re-linking, track confirmation)
      -> rules.Engine stepped frame by frame (online.IncrementalEngine)
      -> POST /api/live/state (<= --state-hz) and POST /api/events (backend_client.py), and
         ml/data/results/live/<session>/events.jsonl

Frames that arrive while one is being processed are dropped (latest-frame-wins), so latency stays
bounded when the GPU is slower than the camera; --all-frames processes every frame instead
(latency then grows, for comparisons with the offline run). --skip N processes every Nth frame.

Session outputs (ml/data/results/live/<session>/): events.jsonl (open + close records with wall
times and latency), kinematics.csv (rows as fed to the engine), tracks.csv (raw boxes with
ground positions, kinematics.load_tracks format), violations.json (final events), summary.json
(fps, per-stage ms, latency, drop counts, GPU utilisation samples), run_config.json.

Usage (one machine; start the backend or mock_backend.py first, then the pipeline, then a source):
    venv\\Scripts\\python.exe services/live/pipeline.py --backend http://localhost:8000
    venv\\Scripts\\python.exe services/live/replay_source.py 20261009_201727
  options: --imgsz 640 --skip 2 --port 5555 --scene ml/violation_engine/configs/scenes/Town05.json
           --zones <scenario_log.json> --profile town05 --kin fixedlag|filter --lag 1.0 --all-frames
"""

import argparse
import csv
import datetime
import json
import subprocess
import sys
import threading
import time
from argparse import Namespace
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2  # noqa: E402

from backend_client import BackendClient  # noqa: E402
from transport import DEFAULT_PORT, FrameReceiver  # noqa: E402

LIVE_RESULTS = REPO / "ml" / "data" / "results" / "live"
DEFAULT_WEIGHTS = REPO / "ml" / "data" / "results" / "retrain_v1" / "train" / "weights" / "best.pt"  # current detector (tracker, 2026-10-03)
DEFAULT_IMGSZ = 960  # what the offline flight runs use with retrain_v1 (run_config.json of 20261009_201727)
SCENES = REPO / "ml" / "violation_engine" / "configs" / "scenes"
RECENT_FLAG_S = 5.0  # a vehicle shows as "flagged" on the live map this long after its event closed


def stats(a) -> dict:
    a = np.asarray(a, float)
    if not len(a):
        return {"n": 0}
    return {"n": int(len(a)), "mean": round(float(a.mean()), 2), "p50": round(float(np.percentile(a, 50)), 2),
            "p95": round(float(np.percentile(a, 95)), 2), "max": round(float(a.max()), 2)}


class GpuSampler(threading.Thread):
    """nvidia-smi utilisation / memory every `every` s (GPU share with CarlaAir, Build Plan M4)."""

    def __init__(self, every: float = 2.0):
        super().__init__(daemon=True)
        self.every, self.samples, self.stop = every, [], False

    def run(self):
        while not self.stop:
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[0]
                u, m = (float(v) for v in out.split(","))
                self.samples.append((u, m))
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
            time.sleep(self.every)


def build_engine(args, map_name: str, session: str):
    """Scene, profile and rule settings exactly as run_violations.py builds them for a flight."""
    from ground_coords import RoadSurface
    from lane_map import SceneMap
    from rules import Engine
    from run_violations import engine_config, extra_params, merge_zones, road_scene
    ns = Namespace(profile=args.profile, params=args.params, red_light=False, learn_flow=False,
                   scene=args.scene, zones=args.zones)
    params, disabled = engine_config(ns)
    scene_path = ns.scene or SCENES / f"{Path(map_name).name or 'Town05'}.json"
    if not Path(scene_path).exists():
        raise SystemExit(f"no lane map {scene_path}; give --scene")
    scene_data = road_scene(merge_zones(json.loads(Path(scene_path).read_text()), ns.zones), ns)
    surface = None if args.flat_ground else RoadSurface.from_scene(scene_data)
    engine = Engine(SceneMap(scene_data), extra_params(params, ns.zones), prefix=session, disabled_conditions=disabled)
    return engine, surface, str(scene_path)


def load_detector(weights: Path):
    from process_recorded_flight import dedupe_boxes, vehicle_class_ids
    from ultralytics import YOLO
    model = YOLO(str(weights))
    model.add_callback("on_predict_postprocess_end", dedupe_boxes)
    return model, vehicle_class_ids(model)


def event_dict(e, session: str, provisional: bool) -> dict:
    d = asdict(e)
    d["session_id"] = session
    d["track_ids"] = [int(t) for t in d["track_ids"]]
    if provisional:
        d["tags"] = d["tags"] + ["provisional"]
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--backend", default=None, help="Backend base URL, e.g. http://localhost:8000 (default: none)")
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--imgsz", type=int, default=DEFAULT_IMGSZ)
    ap.add_argument("--tracker", default="tracktrack_ours", help="Tracker yaml name in ml/violation_engine/")
    ap.add_argument("--skip", type=int, default=1, help="Process every Nth received frame")
    ap.add_argument("--all-frames", action="store_true", help="Queue every frame instead of latest-frame-wins")
    ap.add_argument("--kin", choices=["fixedlag", "filter"], default="fixedlag")
    ap.add_argument("--lag", type=float, default=None, help="Engine delay in s (default online.LAG_S)")
    ap.add_argument("--no-relink", action="store_true")
    ap.add_argument("--scene", type=Path, default=None, help="Lane map (default: configs/scenes/<map>.json)")
    ap.add_argument("--zones", type=Path, default=None, help="Extra zones / scenario log (as run_violations.py)")
    ap.add_argument("--profile", type=Path, default=None)
    ap.add_argument("--params", type=Path, default=None)
    ap.add_argument("--flat-ground", action="store_true")
    ap.add_argument("--state-hz", type=float, default=10.0)
    ap.add_argument("--session", default=None, help="Session id (default live_<date>_<time>)")
    ap.add_argument("--out", type=Path, default=LIVE_RESULTS)
    ap.add_argument("--once", action="store_true", help="Exit after the first source ends (default: wait for the next)")
    args = ap.parse_args()

    from kinematics import edge_mask
    from online import LAG_S, IncrementalEngine, LiveCamera, OnlineKinematics
    from process_recorded_flight import DETECT_CONF, TRACKER_CONFIGS

    model, classes = load_detector(args.weights)
    tracker_cfg = TRACKER_CONFIGS[args.tracker]
    rx = FrameReceiver(args.host, args.port, mode="all" if args.all_frames else "latest")
    print(f"[live] detector {args.weights.name} imgsz {args.imgsz}, tracker {args.tracker}; listening on :{rx.port}")
    # warm the GPU up before the first frame arrives (first call builds the model)
    model.predict(np.zeros((1080, 1920, 3), np.uint8), imgsz=args.imgsz, verbose=False)
    hello = rx.wait_hello()
    session = args.session or f"live_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    out = args.out / session
    out.mkdir(parents=True, exist_ok=True)
    print(f"[live] source: {hello}; session {session} -> {out}")

    engine, surface, scene_path = build_engine(args, hello.get("map", ""), session)
    cam = LiveCamera(hello["width"], hello["height"], hello.get("fov", 90.0), float(hello.get("ground_z", 0.0)))
    lag = LAG_S if args.lag is None else args.lag
    kin = OnlineKinematics(lag_s=lag, mode=args.kin, relink=not args.no_relink)
    ie = IncrementalEngine(engine)
    backend = BackendClient(args.backend)
    gpu = GpuSampler()
    gpu.start()
    (out / "run_config.json").write_text(json.dumps({
        "date": datetime.datetime.now().isoformat(timespec="seconds"), "command": " ".join(sys.argv),
        "hello": hello, "weights": str(args.weights), "imgsz": args.imgsz, "conf": DETECT_CONF,
        "tracker": tracker_cfg, "skip": args.skip, "all_frames": args.all_frames, "kin": args.kin, "lag_s": lag,
        "relink": not args.no_relink, "scene": scene_path, "zones": str(args.zones), "profile": str(args.profile),
        "road_surface": surface is not None, "backend": args.backend}, indent=1))

    ev_file = open(out / "events.jsonl", "w")
    kin_file = open(out / "kinematics.csv", "w", newline="")
    kin_w = None
    trk_file = open(out / "tracks.csv", "w", newline="")
    trk_w = csv.writer(trk_file)
    trk_w.writerow(["frame", "time_s", "track_id", "tracker_id", "class", "cx", "cy", "w", "h", "conf", "wx", "wy", "pose_exact"])

    capture_wall: dict[int, float] = {}
    cw_order: deque = deque()
    ms = defaultdict(list)
    latency = defaultdict(list)
    recent_flag: dict[int, float] = {}
    n_seen = n_proc = 0
    last_state = 0.0
    first_wall = None
    final_events: dict[str, dict] = {}

    def handle(opened, closed, now_wall):
        for phase, evs in (("open", opened), ("close", closed)):
            for e in evs:
                d = event_dict(e, session, provisional=phase == "open")
                cw = capture_wall.get(e.flag_frame)
                lat = round(now_wall - cw, 3) if cw is not None else None
                if phase == "open" and lat is not None:
                    latency[e.status].append(lat)
                    latency["all"].append(lat)
                rec = {"phase": phase, "emit_wall": now_wall, "flag_capture_wall": cw, "latency_s": lat, "event": d}
                ev_file.write(json.dumps(rec) + "\n")
                ev_file.flush()
                if phase == "close":
                    final_events[e.event_id] = d
                    for tid in e.track_ids:
                        recent_flag[tid] = now_wall
                if phase == "close" or e.status != "suppressed":
                    backend.post_event(d, phase)
                print(f"[event] {phase:5s} {e.event_id} {e.type} {e.condition} {e.status} tracks={e.track_ids} "
                      f"flag_s={e.flag_s} latency={lat}s")

    def run_engine(batches):
        nonlocal kin_w
        for t, fr, rows in batches:
            if rows:
                if kin_w is None:
                    kin_w = csv.DictWriter(kin_file, fieldnames=list(rows[0]))
                    kin_w.writeheader()
                kin_w.writerows(rows)
            t0 = time.perf_counter()
            opened, closed = ie.step(t, fr, rows)
            ms["engine"].append((time.perf_counter() - t0) * 1000)
            handle(opened, closed, time.time())

    try:
        while True:
            item = rx.get(1.0)
            if item is None:
                if rx.ended:
                    if args.once:
                        break
                    rx.ended = False
                    print("[live] source ended; waiting for the next (Ctrl+C to stop)")
                continue
            h, jpg = item
            n_seen += 1
            if args.skip > 1 and (n_seen - 1) % args.skip:
                continue
            t_start = time.perf_counter()
            first_wall = first_wall or time.time()
            frame, t = int(h["frame"]), float(h["sim_time"])
            capture_wall[frame] = h["capture_wall"]
            cw_order.append(frame)
            while len(cw_order) > 5000:
                capture_wall.pop(cw_order.popleft(), None)
            ms["queue_wait"].append((time.time() - h["recv_wall"]) * 1000)
            ms["transport"].append((h["recv_wall"] - h["capture_wall"]) * 1000)

            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            t1 = time.perf_counter()
            ms["decode"].append((t1 - t_start) * 1000)
            res = model.track(img, persist=True, tracker=tracker_cfg, classes=classes, conf=DETECT_CONF,
                              imgsz=args.imgsz, verbose=False)[0]
            t2 = time.perf_counter()
            ms["detect_track"].append((t2 - t1) * 1000)
            sp = res.speed or {}
            ms["detector_inference"].append(sp.get("inference", 0.0))

            dets = []
            if res.boxes is not None and res.boxes.id is not None and len(res.boxes):
                xywh = res.boxes.xywh.cpu().numpy()
                ids = res.boxes.id.cpu().numpy().astype(int)
                confs = res.boxes.conf.cpu().numpy()
                clss = res.boxes.cls.cpu().numpy().astype(int)
                cam.set_pose(frame, h["pose"])
                u, v = xywh[:, 0], xywh[:, 1]
                g = cam.to_ground_on_roads(frame, u, v, surface) if surface is not None else cam.to_ground(frame, u, v)
                ok = edge_mask(xywh[:, 0], xywh[:, 1], xywh[:, 2], xywh[:, 3], cam.W, cam.H)
                for i in range(len(ids)):
                    name = res.names[int(clss[i])].lower()
                    dets.append({"tracker_id": int(ids[i]), "cls": name, "conf": float(confs[i]),
                                 "x": float(g[i, 0]), "y": float(g[i, 1]), "edge_ok": bool(ok[i])})
            t3 = time.perf_counter()
            ms["ground"].append((t3 - t2) * 1000)
            live = kin.update(frame, t, dets)
            src_of = kin.id_map
            for i, d in enumerate(dets):
                trk_w.writerow([frame, t, src_of.get(d["tracker_id"], -1), d["tracker_id"], d["cls"],
                                *(round(float(x), 1) for x in xywh[i]), round(d["conf"], 3),
                                round(d["x"], 3), round(d["y"], 3), 1])
            batches = kin.emit(t)
            t4 = time.perf_counter()
            ms["kinematics"].append((t4 - t3) * 1000)
            run_engine(batches)
            t5 = time.perf_counter()
            n_proc += 1

            now = time.time()
            if live and now - last_state >= 1.0 / args.state_hz:
                last_state = now
                flagged = ie.open_track_ids() | {k for k, w in recent_flag.items() if now - w < RECENT_FLAG_S}
                states = []
                for s in live:
                    q = engine.hist.obs.get(s["track_id"])
                    lane = q[-1].lane.lane.id if q and q[-1].lane is not None else None
                    states.append({"session_id": session, "track_id": s["track_id"], "cls": s["cls"], "x": s["x"],
                                   "y": s["y"], "speed_kmh": s["speed_kmh"], "heading_deg": s["heading_deg"],
                                   "lane_id": lane, "t_s": s["t_s"],
                                   "state": "flagged" if s["track_id"] in flagged else ("ok" if s["confirmed"] else "checking")})
                backend.post_states(states)
            ms["total"].append((time.perf_counter() - t_start) * 1000)
            ms["post_engine"].append((time.perf_counter() - t5) * 1000)
            if n_proc % 200 == 0:
                el = time.time() - first_wall
                print(f"[live] {n_proc} processed / {rx.received} received ({rx.dropped} dropped), "
                      f"{n_proc / el:.1f} fps, total {np.mean(ms['total'][-200:]):.0f} ms/frame, "
                      f"events {len(ie.engine.log.events)}", flush=True)
    except KeyboardInterrupt:
        print("[live] stopping")
    finally:
        run_engine(kin.flush())
        opened, closed = ie.finish()
        handle(opened, closed, time.time())
        elapsed = time.time() - (first_wall or time.time())
        backend.close()
        gpu.stop = True
        ev_file.close()
        kin_file.close()
        trk_file.close()
        rx.close()
        (out / "violations.json").write_text(json.dumps(list(final_events.values()), indent=1))
        post_lat = []
        opens = {}
        with open(out / "events.jsonl") as f:
            for line in f:
                r = json.loads(line)
                if r["phase"] == "open":
                    opens[r["event"]["event_id"]] = r["flag_capture_wall"]
        for p in backend.posted:
            if p["phase"] == "open" and p["ok"] and opens.get(p["event_id"]):
                post_lat.append(p["post_wall"] - opens[p["event_id"]])
        summary = {
            "session": session, "source": hello, "elapsed_s": round(elapsed, 1),
            "frames_received": rx.received, "frames_dropped_latest_wins": rx.dropped, "frames_seen": n_seen,
            "frames_processed": n_proc, "fps_processed": round(n_proc / elapsed, 2) if elapsed else None,
            "ms_per_stage": {k: stats(v) for k, v in ms.items()},
            "event_latency_s_capture_to_emit": {k: stats(v) for k, v in latency.items()},
            "event_latency_s_capture_to_post_ok": stats(post_lat),
            "backend_posts_ok": backend.ok, "backend_posts_failed": backend.failed,
            "events_final": len(final_events), "kinematics_stats": dict(kin.stats),
            "gpu_util_pct": stats([s[0] for s in gpu.samples]), "gpu_mem_mib": stats([s[1] for s in gpu.samples]),
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps({k: summary[k] for k in ("frames_received", "frames_processed", "fps_processed",
                                                   "events_final", "event_latency_s_capture_to_emit")}, indent=1))
        print(f"[live] session -> {out}")


if __name__ == "__main__":
    main()
