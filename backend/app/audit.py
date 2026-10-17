"""PRD 28.4 data-access audit log: an ASGI middleware that appends one audit_log row per API action.

Logged: every /api request that reads or changes data (events, evidence files, exports, sessions,
profiles, scenarios, recommendations, reviews, logins), with user, role, action, resource id, client
IP and the HTTP status. Not logged: /api/health, the live pipeline's 10 Hz /api/live/state and
/api/live/frame posts, /api/live/sources polls, the WebSocket, CORS preflights, reads of the audit log itself, and evidence-file byte ranges past the
first (a video player fetches a clip in many Range requests; the first one is the access). A failed
audit write never fails the request.
"""

import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote

from .auth import principal_from_token
from .db import SessionLocal
from .models import AuditLog

log = logging.getLogger("backend.audit")
SKIP = re.compile(r"^/api/(health|live/(state|frame|sources)$|ws/|audit$)")
# (method, path regex, action); the regex's first group (if any) is the resource id
_ACTIONS = [
    ("POST", r"^/api/auth/login$", "auth.login"),
    ("POST", r"^/api/auth/refresh$", "auth.refresh"),
    ("POST", r"^/api/auth/logout$", "auth.logout"),
    ("GET", r"^/api/users$", "users.list"),
    ("POST", r"^/api/users$", "user.create"),
    ("PATCH", r"^/api/users/([^/]+)$", "user.update"),
    ("POST", r"^/api/users/([^/]+)/password$", "user.password"),
    ("GET", r"^/api/events/([^/]+)/reviews$", "event.reviews"),
    ("POST", r"^/api/events/([^/]+)/review$", "event.review"),
    ("GET", r"^/api/events/([^/]+)$", "event.read"),
    ("GET", r"^/api/events$", "events.list"),
    ("POST", r"^/api/events$", "event.create"),
    ("PUT", r"^/api/live/events/([^/]+)$", "event.update"),
    ("GET", r"^/api/export$", "export"),
    ("GET", r"^/api/stats$", "stats.read"),
    ("GET", r"^/api/files/(.+)$", "files.get"),
    ("POST", r"^/api/sessions/import$", "session.import"),
    ("GET", r"^/api/sessions/([^/]+)/trajectories$", "session.trajectories"),
    ("POST", r"^/api/sessions/([^/]+)/video$", "session.video"),
    ("PUT", r"^/api/profiles/([^/]+)$", "profile.save"),
    ("POST", r"^/api/scenarios$", "scenario.create"),
    ("POST", r"^/api/recommendations/([^/]+)/decision$", "recommendation.decide"),
    ("GET", r"^/api/recommendations$", "recommendations.read"),
    ("PATCH", r"^/api/anomalies/([^/]+)$", "anomaly.update"),
    ("GET", r"^/api/zones$", "zones.list"),
    ("POST", r"^/api/zones$", "zone.create"),
    ("GET", r"^/api/zones/([^/]+)/history$", "zone.history"),
    ("GET", r"^/api/zones/([^/]+)$", "zone.read"),
    ("PUT", r"^/api/zones/([^/]+)$", "zone.update"),
    ("DELETE", r"^/api/zones/([^/]+)$", "zone.deactivate"),
    ("POST", r"^/api/reports/generate$", "report"),
    ("GET", r"^/api/reports$", "reports.list"),
    ("GET", r"^/api/reports/([^/]+)$", "report.download"),
    ("GET", r"^/api/live/video$", "live.video"),
]
ACTIONS = [(m, re.compile(p), a) for m, p, a in _ACTIONS]


def action_of(method: str, path: str, query: dict) -> tuple[str, str | None]:
    """(action name, resource id) for a request."""
    for m, rx, a in ACTIONS:
        hit = rx.match(path) if m == method else None
        if hit:
            if a == "export":
                return f"export.{query.get('format', ['?'])[0]}", query.get("session_id", [None])[0]
            if a == "report":  # POST /reports/generate: report.generate.<format>
                return f"report.generate.{query.get('format', ['?'])[0]}", query.get("session_id", [None])[0]
            if a == "zones.list":
                return a, query.get("scene", [None])[0]
            if a in ("events.list", "stats.read", "recommendations.read", "reports.list", "live.video"):
                return a, query.get("session_id", [None])[0]
            return a, hit.group(1) if rx.groups else None
    return f"{method.lower()} {path[5:60]}", None


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or scope["method"] == "OPTIONS" or SKIP.match(path):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        rng = headers.get("range", "")
        if path.startswith("/api/files/") and rng and not rng.startswith("bytes=0-"):
            return await self.app(scope, receive, send)
        written = False

        async def send_wrap(msg):
            nonlocal written
            if msg["type"] == "http.response.start" and not written:  # row lands before the client sees the response
                written = True
                await self._write(scope, headers, msg["status"])
            await send(msg)

        try:
            await self.app(scope, receive, send_wrap)
        finally:
            if not written:  # failed before any response
                await self._write(scope, headers, 500)

    async def _write(self, scope, headers: dict, code: int) -> None:
        method, path = scope["method"], unquote(scope["path"])
        query = parse_qs(scope.get("query_string", b"").decode())
        action, rid = action_of(method, path, query)
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else query.get("token", [None])[0]
        p = principal_from_token(token)
        client = scope.get("client") or (None, None)
        try:
            async with SessionLocal() as db:
                db.add(AuditLog(at=datetime.now(timezone.utc), user_id=p.username if p else None, role=p.role if p else None,
                                action=action[:64], method=method, path=path[:2000], resource_id=rid[:256] if rid else None,
                                ip_address=headers.get("x-forwarded-for", client[0]), result=code))
                await db.commit()
        except Exception as e:  # noqa: BLE001 - auditing must not break the API
            log.warning("audit write failed: %s", e)
