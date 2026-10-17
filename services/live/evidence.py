"""Evidence for live events: a snapshot at the flag time and a short clip around it, from the frames the
pipeline processed (services/live/pipeline.py).

The pipeline hands every processed frame to `add_frame` as it sends it to the dashboard (the small raw JPEG
and the boxes with their track ids), and calls `flag` when an event is flagged. The recorder keeps the last
BUFFER_S seconds; at the flag it makes the snapshot (the frame nearest the flag time), and once the stream
is POST_S past the flag, the clip (PRE_S before the flag to POST_S after). Both are drawn again with the
event's vehicles marked as flagged (when the frame was first drawn the car had not been flagged yet), and
written by a worker thread (decoding, drawing and VP8 encoding stay off the detector's loop) to
<out_dir>/<event_id>_snap.jpg and <event_id>.webm; `on_ready(event_id, kind, path)` is then called from that
thread (the pipeline uploads them: backend_client.post_evidence).

Times are the stream's sim time, the clock the events' flag_s and start_s carry. The clip is VP8 WebM, the
codec the dashboard plays (media.py transcodes imported mp4 clips to the same).
"""

import queue
import threading
from collections import deque
from pathlib import Path

import cv2
import numpy as np

PRE_S = 6.0     # clip: seconds before the flag
POST_S = 4.0    # and after it
BUFFER_S = PRE_S + POST_S + 4.0  # frames kept (a few s over the clip: the engine flags ~1 s after the frame)
JPEG_QUALITY = 85


class EvidenceRecorder:
    def __init__(self, out_dir: Path, draw, on_ready=None, pre_s: float = PRE_S, post_s: float = POST_S):
        """draw(img, boxes, k) -> annotated image: the pipeline's box drawing; boxes are (cx, cy, w, h, label,
        state) in source pixels, k the source -> frame scale."""
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.draw, self.on_ready = draw, on_ready
        self.pre_s, self.post_s = pre_s, post_s
        self.buffer_s = max(BUFFER_S, pre_s + post_s + 4.0)
        self.frames: deque = deque()   # (t, raw jpg, boxes, track ids, k)
        self.pending: dict[str, tuple[float, float, set]] = {}  # event_id -> (clip start, clip end, track ids)
        self.seen: set[str] = set()
        self.made = {"snapshot": 0, "clip": 0}
        self.failed = 0
        self.q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._work, daemon=True, name="evidence")
        self._thread.start()

    def add_frame(self, t: float, raw_jpg: bytes, boxes: list, track_ids: list[int], k: float) -> None:
        self.frames.append((t, raw_jpg, boxes, track_ids, k))
        while self.frames and self.frames[0][0] < t - self.buffer_s:
            self.frames.popleft()
        for eid in [e for e, (_, end, _) in self.pending.items() if t >= end]:
            self._clip(eid)

    def flag(self, event_id: str, t_flag: float, track_ids) -> bool:
        """An event was flagged at sim time t_flag: snapshot now, clip once the stream is post_s past it.
        Once per event (False if it was already flagged or there is no frame yet)."""
        if event_id in self.seen or not self.frames:
            return False
        self.seen.add(event_id)
        ids = {int(i) for i in track_ids}
        near = min(self.frames, key=lambda f: abs(f[0] - t_flag))
        self.q.put(("snapshot", event_id, [near], ids))
        self.pending[event_id] = (t_flag - self.pre_s, t_flag + self.post_s, ids)
        if self.frames[-1][0] >= t_flag + self.post_s:  # flagged late (at its close): the frames are already here
            self._clip(event_id)
        return True

    def _clip(self, event_id: str) -> None:
        t0, t1, ids = self.pending.pop(event_id)
        frames = [f for f in self.frames if t0 <= f[0] <= t1]
        if len(frames) >= 2:
            self.q.put(("clip", event_id, frames, ids))

    def finish(self, timeout: float = 30.0) -> None:
        """Clips of the events still waiting, from the frames there are; then wait for the writer."""
        for eid in list(self.pending):
            self._clip(eid)
        self.q.put(None)
        self._thread.join(timeout)

    def _render(self, frame, ids: set):
        t, jpg, boxes, track_ids, k = frame
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        marked = [(*b[:5], "flagged" if tid in ids else b[5]) for b, tid in zip(boxes, track_ids)]
        return self.draw(img, marked, k)

    def _work(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                return
            kind, eid, frames, ids = item
            try:
                path = self._write(kind, eid, frames, ids)
            except Exception as e:  # noqa: BLE001 - evidence must never stop the pipeline
                self.failed += 1
                print(f"[evidence] {kind} of {eid} failed: {e}")
                continue
            if path is None:
                self.failed += 1
                continue
            self.made[kind] += 1
            if self.on_ready is not None:
                self.on_ready(eid, kind, path)

    def _write(self, kind: str, eid: str, frames: list, ids: set) -> Path | None:
        if kind == "snapshot":
            path = self.dir / f"{eid}_snap.jpg"
            ok, buf = cv2.imencode(".jpg", self._render(frames[0], ids), [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
            if not ok:
                return None
            path.write_bytes(buf.tobytes())
            return path
        path = self.dir / f"{eid}.webm"
        span = frames[-1][0] - frames[0][0]
        fps = min(30.0, max(1.0, (len(frames) - 1) / span)) if span > 0 else 10.0  # the rate frames were processed at
        out, size = None, None
        try:
            for f in frames:
                img = self._render(f, ids)
                if out is None:
                    size = (img.shape[1], img.shape[0])
                    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"VP80"), fps, size)
                    if not out.isOpened():
                        return None
                elif (img.shape[1], img.shape[0]) != size:  # the source changed resolution mid-clip
                    img = cv2.resize(img, size)
                out.write(img)
        finally:
            if out is not None:
                out.release()
        return path if path.exists() and path.stat().st_size > 0 else None
