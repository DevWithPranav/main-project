"""Login, bcrypt passwords, role checks and the live pipeline's service token.

Tokens (PRD 14.1): a short-lived access JWT (HS256, config.ACCESS_MINUTES, with a jti) plus an opaque
refresh token kept in Redis (config.REFRESH_HOURS), rotated on every /auth/refresh. Revocation, all in
Redis with a TTL of the longest remaining lifetime: auth:deny:{jti} (logout), auth:cutoff:{user}
(disable / role change / password reset: tokens issued before it are refused), auth:refresh:{sha256}.
If Redis is down the revocation check is skipped (logged) rather than locking everyone out.
"""

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import config
from .live import hub
from .models import User

log = logging.getLogger("backend.auth")
SERVICE = "SERVICE"


@dataclass
class Principal:
    username: str
    role: str
    jti: str | None = None
    iat: float = 0.0
    exp: float = 0.0


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def check_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def make_token(username: str, role: str, minutes: float | None = None) -> str:
    now = time.time()
    return jwt.encode({"sub": username, "role": role, "jti": secrets.token_hex(12), "iat": now,
                       "exp": int(now + 60 * (config.ACCESS_MINUTES if minutes is None else minutes))},
                      config.JWT_SECRET, algorithm="HS256")


def principal_from_token(token: str | None) -> Principal | None:
    """Signature and expiry only (no revocation check: the audit log uses it to name the caller)."""
    if not token:
        return None
    if token == config.SERVICE_TOKEN:
        return Principal("service", SERVICE)
    try:
        p = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "jti", "sub"]})
    except jwt.PyJWTError:
        return None
    return Principal(p["sub"], p["role"], p["jti"], float(p.get("iat", 0)), float(p["exp"]))


async def principal(token: str | None) -> Principal | None:
    """A valid token that was not revoked (logout, user disabled / role changed / password reset)."""
    p = principal_from_token(token)
    if p is None or p.role == SERVICE:
        return p
    try:
        deny, cut = await hub.redis.mget(f"auth:deny:{p.jti}", f"auth:cutoff:{p.username}")
    except Exception as e:  # noqa: BLE001 - Redis down: accept (access tokens are short-lived)
        log.warning("token revocation check skipped: %s", e)
        return p
    return None if deny or (cut and p.iat < float(cut)) else p


def bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


async def current_user(request: Request) -> Principal:
    p = await principal(bearer(request))
    if p is None:
        raise HTTPException(401, "missing, expired or revoked token", headers={"WWW-Authenticate": "Bearer"})
    return p


def require(*roles: str, service: bool = False):
    """Dependency: the caller has one of `roles` (ADMIN always passes; the service token only when
    service=True)."""
    allowed = set(roles) | {"ADMIN"} | ({SERVICE} if service else set())

    async def dep(user: Principal = Depends(current_user)) -> Principal:
        if user.role not in allowed:
            raise HTTPException(403, f"role {user.role} may not do this (needs {', '.join(sorted(allowed))})")
        return user
    return dep


async def authenticate(db: AsyncSession, username: str, password: str) -> User | None:
    u = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    return u if u and check_password(password, u.password_hash) else None


def _rkey(refresh_token: str) -> str:
    return f"auth:refresh:{hashlib.sha256(refresh_token.encode()).hexdigest()}"


async def issue(u: User) -> dict:
    """Login / refresh answer: a new access token and a new refresh token."""
    rt = secrets.token_urlsafe(32)
    try:
        await hub.redis.set(_rkey(rt), json.dumps({"u": u.username, "iat": time.time()}), ex=config.REFRESH_HOURS * 3600)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"token store (Redis) unavailable: {e}")
    return {"access_token": make_token(u.username, u.role), "refresh_token": rt, "token_type": "bearer",
            "role": u.role, "expires_in": config.ACCESS_MINUTES * 60}


async def refresh(db: AsyncSession, refresh_token: str) -> dict:
    """Swap a refresh token (single use) for a new pair; refused once its user is disabled or revoked."""
    try:
        raw = await hub.redis.getdel(_rkey(refresh_token))
        rec = json.loads(raw) if raw else None
        cut = await hub.redis.get(f"auth:cutoff:{rec['u']}") if rec else None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"token store (Redis) unavailable: {e}")
    u = (await db.execute(select(User).where(User.username == rec["u"]))).scalar_one_or_none() if rec else None
    if u is None or u.disabled or (cut and rec["iat"] < float(cut)):
        raise HTTPException(401, "invalid, expired or revoked refresh token")
    return await issue(u)


async def logout(token: str | None, refresh_token: str | None) -> None:
    """Revoke this access token (until it would have expired) and the refresh token."""
    p = principal_from_token(token)
    try:
        if p and p.jti:
            await hub.redis.set(f"auth:deny:{p.jti}", 1, ex=max(1, int(p.exp - time.time()) + 1))
        if refresh_token:
            await hub.redis.delete(_rkey(refresh_token))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"token store (Redis) unavailable: {e}")


async def revoke_user(username: str) -> None:
    """Refuse every token of `username` issued before now (disable, role change, password reset)."""
    await hub.redis.set(f"auth:cutoff:{username}", repr(time.time()), ex=config.REFRESH_HOURS * 3600)


async def seed_users(db: AsyncSession) -> None:
    have = set((await db.execute(select(User.username))).scalars())
    for name, role in config.DEV_USERS.items():
        if name not in have:
            db.add(User(username=name, password_hash=hash_password(f"{name}123"), role=role))
    await db.commit()
