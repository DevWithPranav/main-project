"""Live fan-out: Redis pub/sub -> WebSockets (API.md WS /api/ws/live).

POST /api/live/state and POST /api/events publish to Redis (channels live:{session} and events);
one background listener per process (started in the app lifespan) hands each message to the
sockets of this process. Each socket gets every event at once and vehicle batches at <= 10 Hz
(the newest batch wins when they arrive faster).

Traffic lights (POST /api/live/signals, CARLA sources): the light list is kept in signals:{session}:lights
and each light's last SIGNAL_HISTORY changes [[t, state], ...] in the hash signals:{session}:state
(SIGNALS_TTL_S); every post is published on the "signals" channel and goes to the sockets like an
event (never dropped). A new socket gets the lights and those recent changes first ({"type": "signals",
"snapshot": true}): the twin shows a light as it was at the time of the vehicles on screen, which is
behind the newest change by the detector's delay, so the latest state alone is not enough. t is
CARLA's sim time, the vehicles' t_s clock.
"""

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from fastapi import WebSocket
from sqlalchemy import select

from . import config
from .db import SessionLocal
from .events import to_api
from .models import Event

log = logging.getLogger("backend.live")
SIGNAL_HISTORY = 8  # recent changes kept per light for a socket that connects mid-run (a cycle of red / green / yellow and some)


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
                await ps.subscribe("events", "signals")
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
        if channel in ("events", "signals"):
            sid = msg.get("event", {}).get("session_id") if channel == "events" else msg.get("session_id")
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

    async def catch_up(self, conn: Conn, session_id: str | None, window_s: float) -> None:
        """PRD 29.3: events stored in the last `window_s` s (<= 60), oldest first, then a `catch_up` marker.
        A socket that reconnects after a drop gets what it missed; one event can arrive twice (stored just
        as the socket registered), so clients de-duplicate by event_id."""
        window_s = max(0.0, min(float(window_s), config.WS_CATCH_UP_MAX_S))
        if window_s <= 0:
            return
        since = datetime.now(timezone.utc) - timedelta(seconds=window_s)
        q = select(Event).where(Event.created_at >= since).order_by(Event.created_at).limit(config.WS_CATCH_UP_MAX_EVENTS)
        if session_id:
            q = q.where(Event.session_id == session_id)
        async with SessionLocal() as db:
            rows = (await db.execute(q)).scalars().all()
        for r in rows:
            conn.events.put_nowait(json.dumps({"type": "event", "event": to_api(r), "catch_up": True}))
        conn.events.put_nowait(json.dumps({"type": "catch_up", "window_s": window_s, "n": len(rows), "session_id": session_id}))
        conn.wake.set()

    async def serve(self, ws: WebSocket, session_id: str | None, catch_up_s: float = 60.0) -> None:
        conn = Conn(ws, session_id)
        self.conns.add(conn)
        sender = asyncio.create_task(conn.sender())
        try:
            await self.catch_up(conn, session_id, catch_up_s)
            snap = await self.snapshot(session_id)
            if snap:
                conn.vehicles = {"type": "vehicles", "items": snap}
                conn.wake.set()
            for msg in await self.signals_snapshot(session_id):
                conn.events.put_nowait(json.dumps(msg))
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

    async def signals_snapshot(self, session_id: str | None) -> list[dict]:
        """{"type": "signals", "session_id", "lights", "changes": [[t, id, state]], "snapshot": true} per live session."""
        out = []
        async for key in self.redis.scan_iter(f"signals:{session_id or '*'}:lights", count=200):
            sid = key.split(":", 1)[1].rsplit(":", 1)[0]
            lights = await self.redis.get(key)
            states = await self.redis.hgetall(f"signals:{sid}:state")
            changes = []
            for lid, v in states.items():
                changes += [[t, lid, st] for t, st in json.loads(v)]
            out.append({"type": "signals", "session_id": sid, "lights": json.loads(lights) if lights else [],
                        "changes": sorted(changes), "snapshot": True})
        return out

    async def publish_signals(self, session_id: str, lights: list | None, changes: list[list]) -> int:
        """lights (optional) and changes [carla_frame, sim_time, id, state]: each light's last SIGNAL_HISTORY changes in
        Redis (merged in time order, so a late older change lands in its place), then published as [[t, id, state]]."""
        key_l, key_s = f"signals:{session_id}:lights", f"signals:{session_id}:state"
        ttl = config.SIGNALS_TTL_S
        pipe = self.redis.pipeline()
        if lights:
            pipe.set(key_l, json.dumps(lights), ex=ttl)
        out = sorted(([float(c[1]), str(c[2]), str(c[3])] for c in changes), key=lambda c: c[0])
        if out:
            ids = sorted({lid for _, lid, _ in out})
            cur = dict(zip(ids, await self.redis.hmget(key_s, ids)))
            hist: dict[str, list] = {lid: json.loads(v) if v else [] for lid, v in cur.items()}
            for t, lid, st in out:
                if [t, st] not in hist[lid]:
                    hist[lid].append([t, st])
            pipe.hset(key_s, mapping={k: json.dumps(sorted(v)[-SIGNAL_HISTORY:]) for k, v in hist.items()})
            pipe.expire(key_s, ttl)
        if not lights:  # keep the light list alive as long as its states
            pipe.expire(key_l, ttl)
        pipe.publish("signals", json.dumps({"type": "signals", "session_id": session_id, "lights": lights or None, "changes": out}))
        await pipe.execute()
        return len(out)

    async def publish_event(self, event: dict) -> None:
        await self.redis.publish("events", json.dumps({"type": "event", "event": event}))


hub = Hub()
