"""Live fan-out: Redis pub/sub -> WebSockets (API.md WS /api/ws/live).

POST /api/live/state and POST /api/events publish to Redis (channels live:{session} and events);
one background listener per process (started in the app lifespan) hands each message to the
sockets of this process. Each socket gets every event at once and vehicle batches at <= 10 Hz
(the newest batch wins when they arrive faster).
"""

import asyncio
import contextlib
import json
import logging
import time

import redis.asyncio as aioredis
from fastapi import WebSocket

from . import config

log = logging.getLogger("backend.live")


class Conn:
    def __init__(self, ws: WebSocket, session_id: str | None):
        self.ws, self.session_id = ws, session_id
        self.events: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.vehicles: dict | None = None
        self.wake = asyncio.Event()

    def wants(self, session_id: str | None) -> bool:
        return self.session_id is None or session_id is None or session_id == self.session_id

    async def sender(self) -> None:
        last = 0.0
        while True:
            await self.wake.wait()
            self.wake.clear()
            while not self.events.empty():
                await self.ws.send_text(self.events.get_nowait())
            if self.vehicles is not None:
                wait = config.WS_MIN_PERIOD_S - (time.monotonic() - last)
                if wait > 0:
                    await asyncio.sleep(wait)
                msg, self.vehicles = self.vehicles, None
                if msg is not None:
                    await self.ws.send_json(msg)
                    last = time.monotonic()
                while not self.events.empty():
                    await self.ws.send_text(self.events.get_nowait())


class Hub:
    def __init__(self):
        self.conns: set[Conn] = set()
        self.redis: aioredis.Redis | None = None
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        self.redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
        self.task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        if self.redis:
            await self.redis.aclose()

    async def _listen(self) -> None:
        while True:
            try:
                ps = self.redis.pubsub()
                await ps.psubscribe("live:*")
                await ps.subscribe("events")
                async for m in ps.listen():
                    if m["type"] in ("message", "pmessage"):
                        self.dispatch(m["channel"], m["data"])
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - Redis restarts: reconnect
                log.warning("redis listener: %s; reconnecting in 1 s", e)
                await asyncio.sleep(1)

    def dispatch(self, channel: str, data: str) -> None:
        msg = json.loads(data)
        if channel == "events":
            sid = msg.get("event", {}).get("session_id")
            for c in self.conns:
                if c.wants(sid):
                    with contextlib.suppress(asyncio.QueueFull):
                        c.events.put_nowait(data)
                    c.wake.set()
        else:
            sid = channel.split(":", 1)[1]
            for c in self.conns:
                if c.wants(sid):
                    c.vehicles = msg
                    c.wake.set()

    async def serve(self, ws: WebSocket, session_id: str | None) -> None:
        conn = Conn(ws, session_id)
        self.conns.add(conn)
        sender = asyncio.create_task(conn.sender())
        try:
            snap = await self.snapshot(session_id)
            if snap:
                conn.vehicles = {"type": "vehicles", "items": snap}
                conn.wake.set()
            while True:  # clients only need to keep the socket open; ignore what they send
                await ws.receive_text()
        finally:
            self.conns.discard(conn)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender

    async def snapshot(self, session_id: str | None) -> list[dict]:
        keys = [k async for k in self.redis.scan_iter(f"live:{session_id or '*'}:*", count=500)]
        vals = await self.redis.mget(keys) if keys else []
        return [json.loads(v) for v in vals if v]

    async def publish_states(self, states: list[dict]) -> int:
        by_session: dict[str, list[dict]] = {}
        pipe = self.redis.pipeline()
        for s in states:
            pipe.set(f"live:{s['session_id']}:{s['track_id']}", json.dumps(s), px=config.LIVE_TTL_MS)
            by_session.setdefault(s["session_id"], []).append(s)
        for sid, items in by_session.items():
            pipe.publish(f"live:{sid}", json.dumps({"type": "vehicles", "items": items}))
        await pipe.execute()
        return len(states)

    async def publish_event(self, event: dict) -> None:
        await self.redis.publish("events", json.dumps({"type": "event", "event": event}))


hub = Hub()
