"""JWT login (HS256, 12 h), bcrypt passwords, role checks and the live pipeline's service token."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import config
from .models import User

SERVICE = "SERVICE"


@dataclass
class Principal:
    username: str
    role: str


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def check_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def make_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + timedelta(hours=config.JWT_HOURS)},
                      config.JWT_SECRET, algorithm="HS256")


def principal_from_token(token: str | None) -> Principal | None:
    if not token:
        return None
    if token == config.SERVICE_TOKEN:
        return Principal("service", SERVICE)
    try:
        p = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return Principal(p["sub"], p["role"])


def bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


async def current_user(request: Request) -> Principal:
    p = principal_from_token(bearer(request))
    if p is None:
        raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})
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


async def seed_users(db: AsyncSession) -> None:
    have = set((await db.execute(select(User.username))).scalars())
    for name, role in config.DEV_USERS.items():
        if name not in have:
            db.add(User(username=name, password_hash=hash_password(f"{name}123"), role=role))
    await db.commit()
