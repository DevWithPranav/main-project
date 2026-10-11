"""Posts live vehicle states and events to the backend (backend/API.md), never blocking the pipeline.

A background thread drains a queue and POSTs with the service token. If the backend or its
database is down, the pipeline continues and the failed event posts are buffered (PRD 29.2, Mode 5):
kept in order, retried with exponential backoff (1, 2, 4, 8, 16, 30 s) and flushed once the backend
answers again; with `spool_path` the buffer is also on disk (a JSON line per event, removed when
drained) so a pipeline restart does not lose it. A post the backend refuses as bad data (4xx other than
408 / 429) is dropped with a log line, not retried for ever. Events are also written to events.jsonl by
the pipeline (`replay_events.py` re-sends that file). Vehicle states are only the newest batch (older
unsent batches are replaced: a stale position is useless). Camera frames (raw + annotated JPEG for the
dashboard's Live page) work the same way and go last.

Event updates: an event is POSTed to /api/events when it opens (flag time, "provisional" tag,
confidence 0) and PUT to /api/live/events/{event_id} when it closes (final status, confidence,
end); POST /api/events answers 409 for an existing event_id, the PUT replaces it (or creates it).

Usage: imported by services/live/pipeline.py.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path


BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)  # PRD 29.2: 1, 2, 4, 8 s ... max 30 s
BUFFER_MAX = 10000                              # events held while the backend is down (oldest dropped beyond this)


class BackendClient:
    def __init__(self, base_url: str | None, token: str | None = None, timeout: float = 2.0,
                 spool_path: str | os.PathLike | None = None):
        # "localhost" tries ::1 first; uvicorn listens on IPv4 only, and on Windows the refused IPv6
        # attempt costs ~2 s per request (2.11 s vs 0.015 s measured 2026-10-10), so states came at 0.3 Hz
        self.base = base_url.rstrip("/").replace("//localhost", "//127.0.0.1", 1) if base_url else None
        self.token = token or os.environ.get("SERVICE_TOKEN", "dev-service-token")
        self.timeout = timeout
        self.events: deque = deque()
        self.states = None
        self.frames = None  # (session, t_s, {kind: jpeg bytes}); newest only, sent after events and states
        self.cv = threading.Condition()
        self.ok = self.failed = 0
        self.posted: list[dict] = []  # {event_id, phase, post_wall, ok}
        self.buffer: deque = deque()  # (event, phase) whose post failed, oldest first
        self.degraded = False          # the last event post failed: the backend / database is not taking events
        self.dropped = 0               # events lost to BUFFER_MAX or refused as bad data
        self._fail_streak = 0
        self._retry_at = 0.0
        self._status = 0               # HTTP status of the last request (0: no answer)
        self.spool = Path(spool_path) if spool_path else None
        if self.spool and self.spool.exists():  # left by an earlier run that was stopped while buffering
            for line in self.spool.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.buffer.append((r["event"], r["phase"]))
        self._last_err = 0.0
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        if self.base:
            self._thread.start()

    def post_event(self, event: dict, phase: str) -> None:
        if not self.base:
            return
        with self.cv:
            self.events.append((event, phase))
            self.cv.notify()

    def post_states(self, states: list[dict]) -> None:
        if not self.base:
            return
        with self.cv:
            self.states = states
            self.cv.notify()

    def post_frames(self, session: str, t_s: float, jpgs: dict[str, bytes]) -> None:
        """Camera frames for the dashboard (raw / annotated JPEG); an unsent older set is replaced."""
        if not self.base:
            return
        with self.cv:
            self.frames = (session, t_s, jpgs)
            self.cv.notify()

    def close(self, timeout: float = 10.0) -> None:
        if not self.base:
            return
        end = time.time() + timeout
        while time.time() < end:
            with self.cv:
                if not self.events and not (self.buffer and not self.degraded):
                    break
            time.sleep(0.05)
        self._stop = True
        with self.cv:
            self.cv.notify()
        self._thread.join(2.0)
        self._write_spool()  # whatever is still undelivered stays on disk for the next run

    @property
    def buffered(self) -> int:
        return len(self.buffer)

    def _post(self, path: str, body, method: str = "POST", content_type: str = "application/json") -> bool:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": content_type,
                                              "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                r.read()
            self._status = 200
            self.ok += 1
            return True
        except urllib.error.HTTPError as e:
            self._status = e.code
            err = e
        except (urllib.error.URLError, OSError, ValueError) as e:
            self._status = 0
            err = e
        self.failed += 1
        if time.time() - self._last_err > 10:
            print(f"[backend] {method} {path} failed ({err}); continuing, events are buffered and in events.jsonl")
            self._last_err = time.time()
        return False

    def _write_spool(self) -> None:
        if not self.spool:
            return
        if not self.buffer:
            self.spool.unlink(missing_ok=True)
            return
        tmp = self.spool.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps({"event": e, "phase": ph}) + chr(10) for e, ph in self.buffer))
        tmp.replace(self.spool)

    def _send_event(self, ev: tuple) -> bool:
        """One event post; True = delivered (or nothing more to do), False = keep it and retry later."""
        event, phase = ev
        if phase == "open":
            ok = self._post("/api/events", event)
            if not ok and self._status == 409:  # already stored (a replay, or a retry that did arrive)
                ok = True
        else:
            ok = self._post(f"/api/live/events/{urllib.parse.quote(event['event_id'])}", event, "PUT")
        if not ok and 400 <= self._status < 500 and self._status not in (408, 429):
            self.dropped += 1  # refused as bad data: retrying cannot help
            print(f"[backend] event {event['event_id']} ({phase}) refused with {self._status}; dropped")
            return True
        return ok

    def _deliver_events(self) -> None:
        """Buffered events first (oldest first), then the new ones; stops at the first failure."""
        while not self._stop:
            if self.buffer:
                if time.time() < self._retry_at:
                    return
                ev, from_buffer = self.buffer[0], True
            else:
                with self.cv:
                    ev = self.events.popleft() if self.events else None
                if ev is None:
                    return
                from_buffer = False
            ok = self._send_event(ev)
            self.posted.append({"event_id": ev[0]["event_id"], "phase": ev[1], "post_wall": time.time(), "ok": ok})
            if ok:
                if from_buffer:
                    self.buffer.popleft()
                    self._write_spool()
                self._fail_streak, self.degraded = 0, False
                continue
            with self.cv:  # everything still queued goes behind it, in order: the backend is not taking events
                pending = ([] if from_buffer else [ev]) + list(self.events)
                self.events.clear()
            for e in pending:
                if len(self.buffer) >= BUFFER_MAX:
                    self.buffer.popleft()
                    self.dropped += 1
                self.buffer.append(e)
            self._write_spool()
            self.degraded = True
            self._retry_at = time.time() + BACKOFF_S[min(self._fail_streak, len(BACKOFF_S) - 1)]
            self._fail_streak += 1
            return

    def _run(self) -> None:
        while not self._stop:
            with self.cv:
                while (not self.events and self.states is None and self.frames is None and not self._stop
                       and not (self.buffer and time.time() >= self._retry_at)):
                    self.cv.wait(0.5)
                has_events = bool(self.events) or bool(self.buffer and time.time() >= self._retry_at)
                st, fr = None, None
                if not has_events:
                    st, self.states = self.states, None
                    if st is None:
                        fr, self.frames = self.frames, None
            if has_events:  # events first: they carry the latency target
                self._deliver_events()
                if self.degraded:  # events are stuck behind a down backend: do not hammer it with states / frames
                    with self.cv:
                        self.states = self.frames = None
                    time.sleep(0.05)
            elif st is not None:
                self._post("/api/live/state", st)
            elif fr is not None:
                session, t_s, jpgs = fr
                for kind, jpg in jpgs.items():
                    q = urllib.parse.urlencode({"session_id": session, "kind": kind, "t_s": round(t_s, 3)})
                    self._post(f"/api/live/frame?{q}", jpg, content_type="image/jpeg")
