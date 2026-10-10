"""Browser-playable video (dashboard M6 / twin M7 video sync).

The pipeline writes MPEG-4 Part 2 (`mp4v`) files: annotated overlay videos and evidence clips.
Chrome and Firefox do not decode that codec, so <video> stays black. OpenCV's bundled FFmpeg can
write VP8 / WebM here (no H.264 encoder in the pip wheel; checked 2026-10-10), so videos are
transcoded once:
  - evidence clips after import, one at a time in a background worker (queue_clip); the mp4 is
    uploaded first, and /api/files serves <key>.webm in its place once it exists
  - a session's overlay video on request, in a background thread (20261002_001635: 5 611 frames
    1080p -> 854x480 every 2nd frame, ~14 source fps on the dev laptop, so ~7 min)
The session video is stored in S3 as sessions/<id>/video.webm with sessions/<id>/video.json:
{fps, step, frames, frame_t: [t_s of each output frame], source}, so a player maps
video time <-> session time (sim seconds, the time base of events and trajectories).

Usage (also run by POST /api/sessions/{id}/video):
    venv\\Scripts\\python.exe -m backend.app.media <flight>
"""

import argparse
import csv
import json
import queue
import tempfile
import threading
import time
from pathlib import Path

from . import config, storage

WEB_W = 854          # output width; 480p keeps the 1080p overlay readable at ~70 kB/frame
SESSION_STEP = 2     # session videos keep every 2nd frame (13 fps from 26.7) to halve encode time and size
CLIP_W = 960
_jobs: dict[str, dict] = {}   # session id -> {status, progress, error}
_lock = threading.Lock()


def to_webm(src: Path, dst: Path, width: int = WEB_W, step: int = 1, progress=None) -> dict:
    """Transcode src to VP8 WebM. Returns {fps, step, frames, src_frames}."""
    import cv2
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w0, h0 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w = min(width, w0) // 2 * 2
    h = round(h0 * w / w0) // 2 * 2
    out = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"VP80"), fps / step, (w, h))
    if not out.isOpened():
        raise RuntimeError("OpenCV has no VP8 writer")
    i = written = 0
    try:
        while True:
            if i % step:
                if not cap.grab():
                    break
            else:
                ok, img = cap.read()
                if not ok:
                    break
                if img.shape[1] != w:
                    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
                out.write(img)
                written += 1
                if progress and n and written % 50 == 0:
                    progress(i / n)
            i += 1
    finally:
        cap.release()
        out.release()
    return {"fps": fps / step, "step": step, "frames": written, "src_frames": i}


def clip_to_webm(src: Path, tmp_dir: Path) -> Path | None:
    """Evidence clip -> a WebM in tmp_dir (results folders are left untouched). None if it fails."""
    dst = tmp_dir / f"{src.stem}.webm"
    try:
        to_webm(src, dst, CLIP_W)
        return dst
    except Exception:  # noqa: BLE001 - the original clip is uploaded instead
        dst.unlink(missing_ok=True)
        return None


# ------------------------------------------------------------------ evidence clips, in the background

_clip_q: "queue.Queue[tuple[Path, str]]" = queue.Queue()
_clip_stats = {"queued": 0, "done": 0, "skipped": 0, "failed": 0}
_clip_worker: threading.Thread | None = None


def webm_key(key: str) -> str:
    """S3 key of the WebM made from an uploaded mp4 clip."""
    return key[:-4] + ".webm" if key.lower().endswith(".mp4") else key


def queue_clip(src: Path, key: str) -> None:
    """Transcode src later and upload it as webm_key(key). The mp4 stays as the fallback."""
    global _clip_worker
    with _lock:
        _clip_stats["queued"] += 1
        if _clip_worker is None or not _clip_worker.is_alive():
            _clip_worker = threading.Thread(target=_run_clips, daemon=True, name="clip-webm")
            _clip_worker.start()
    _clip_q.put((src, key))


def _run_clips() -> None:
    while True:
        src, key = _clip_q.get()
        outcome = "failed"
        try:
            if storage.exists(webm_key(key)):
                outcome = "skipped"  # made by an earlier import
            else:
                # the worker is a daemon: a server stopped mid-encode leaves the file open
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    dst = clip_to_webm(src, Path(tmp))
                    if dst is not None:
                        storage.upload(dst, webm_key(key))
                        outcome = "done"
        except Exception:  # noqa: BLE001 - the mp4 is still served
            pass
        with _lock:
            _clip_stats[outcome] += 1
        _clip_q.task_done()


def clip_status() -> dict:
    """{queued, done, skipped, failed, pending} since the server started."""
    with _lock:
        out = dict(_clip_stats)
    out["pending"] = out["queued"] - out["done"] - out["skipped"] - out["failed"]
    return out


# ------------------------------------------------------------------ session overlay video

def session_source(flight: str, run: Path | None = None) -> Path | None:
    """The overlay video with boxes, IDs and speeds; else the raw flight video. run: a real clip's run
    folder (process_video.py output), whose annotated video is in clip frames = session time / fps."""
    run = run or config.RESULTS_DIR / flight / config.TRACKER_RUN
    for p in (run / "annotated_final.mp4", run / "annotated.mp4", config.RECORDINGS_DIR / flight / "flight.mp4"):
        if p.is_file():
            return p
    return None


def frame_times(flight: str) -> list[float] | None:
    """Session time of every source frame (frame_times.csv time_s: the engine's time base)."""
    p = config.RECORDINGS_DIR / flight / "frame_times.csv"
    if not p.is_file():
        return None
    with p.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    col = "sim_time" if rows and "sim_time" in rows[0] else "time_s"
    return [float(r[col]) for r in rows]


def video_key(sid: str) -> tuple[str, str]:
    return f"sessions/{sid}/video.webm", f"sessions/{sid}/video.json"


def video_info(sid: str) -> dict:
    """{status: none|encoding|ready|failed, ...}; ready adds url + the sidecar."""
    with _lock:
        job = dict(_jobs.get(sid) or {})
    if job.get("status") in ("encoding", "failed"):
        return job
    vk, jk = video_key(sid)
    try:
        side = json.loads(storage.get(jk)["Body"].read())
    except Exception:  # noqa: BLE001 - not made yet
        return {"status": "none"}
    return {"status": "ready", "url": storage.file_url(vk), **side}


def encode_session(sid: str, flight: str, run: Path | None = None) -> dict:
    """Blocking: transcode + upload. Updates _jobs for progress."""
    src = session_source(flight, run)
    if src is None:
        raise FileNotFoundError(f"no video for flight {flight}")
    ft = frame_times(flight)

    def prog(x: float) -> None:
        with _lock:
            _jobs[sid] = {"status": "encoding", "progress": round(x, 3), "source": src.name}

    prog(0.0)
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "video.webm"
        meta = to_webm(src, dst, WEB_W, SESSION_STEP, prog)
        if ft and len(ft) >= meta["src_frames"]:
            frame_t = [round(ft[i], 3) for i in range(0, meta["src_frames"], SESSION_STEP)][: meta["frames"]]
        else:  # no frame clock: assume the video starts at session time 0
            frame_t = [round(i / meta["fps"], 3) for i in range(meta["frames"])]
        side = {**meta, "frame_t": frame_t, "source": src.name, "encode_s": round(time.perf_counter() - t0, 1)}
        vk, jk = video_key(sid)
        storage.client().upload_file(str(dst), config.S3_BUCKET, vk, ExtraArgs={"ContentType": "video/webm"})
        storage.client().put_object(Bucket=config.S3_BUCKET, Key=jk, Body=json.dumps(side).encode(),
                                    ContentType="application/json")
    with _lock:
        _jobs.pop(sid, None)
    return side


def start_encode(sid: str, flight: str, run: Path | None = None) -> dict:
    """Start encode_session in a daemon thread unless one runs; returns the current info."""
    with _lock:
        if (_jobs.get(sid) or {}).get("status") == "encoding":
            return dict(_jobs[sid])
        _jobs[sid] = {"status": "encoding", "progress": 0.0}

    def run() -> None:
        try:
            encode_session(sid, flight, run)
        except Exception as e:  # noqa: BLE001 - reported by GET .../video
            with _lock:
                _jobs[sid] = {"status": "failed", "error": str(e)}

    threading.Thread(target=run, daemon=True, name=f"video-{sid}").start()
    return {"status": "encoding", "progress": 0.0}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("flight")
    a = ap.parse_args()
    side = encode_session(a.flight, a.flight)
    print({k: v for k, v in side.items() if k != "frame_t"})


if __name__ == "__main__":
    main()
