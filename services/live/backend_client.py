"""Posts live vehicle states and events to the backend (backend/API.md), never blocking the pipeline.

A background thread drains a queue and POSTs with the service token. If the backend is down,
the failure is logged (rate-limited) and the pipeline continues; events are also written to
events.jsonl by the pipeline, so nothing is lost. Vehicle states are only the newest batch
(older unsent batches are replaced: a stale position is useless).

Event updates: an event is POSTed when it opens (flag time, "provisional" tag, confidence 0) and
again when it closes (final status, confidence, end). Both carry the same event_id; the backend
should upsert on event_id (contract note in services/live/README.md).

Usage: imported by services/live/pipeline.py.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque


class BackendClient:
    def __init__(self, base_url: str | None, token: str | None = None, timeout: float = 2.0):
        self.base = base_url.rstrip("/") if base_url else None
        self.token = token or os.environ.get("SERVICE_TOKEN", "dev-service-token")
        self.timeout = timeout
        self.events: deque = deque()
        self.states = None
        self.cv = threading.Condition()
        self.ok = self.failed = 0
        self.posted: list[dict] = []  # {event_id, phase, post_wall, ok}
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

    def close(self, timeout: float = 10.0) -> None:
        if not self.base:
            return
        end = time.time() + timeout
        while time.time() < end:
            with self.cv:
                if not self.events:
                    break
            time.sleep(0.05)
        self._stop = True
        with self.cv:
            self.cv.notify()
        self._thread.join(2.0)

    def _post(self, path: str, body) -> bool:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                r.read()
            self.ok += 1
            return True
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.failed += 1
            if time.time() - self._last_err > 10:
                print(f"[backend] POST {path} failed ({e}); continuing, events still go to events.jsonl")
                self._last_err = time.time()
            return False

    def _run(self) -> None:
        while not self._stop:
            with self.cv:
                while not self.events and self.states is None and not self._stop:
                    self.cv.wait(0.5)
                ev = self.events.popleft() if self.events else None
                st, self.states = (self.states, None) if ev is None else (None, self.states)
            if ev is not None:  # events first: they carry the latency target
                ok = self._post("/api/events", ev[0])
                self.posted.append({"event_id": ev[0]["event_id"], "phase": ev[1], "post_wall": time.time(), "ok": ok})
            elif st is not None:
                self._post("/api/live/state", st)
