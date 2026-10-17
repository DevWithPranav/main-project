"""Login, who-am-I, health and evidence files."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth, media, storage
from ..auth import Principal, authenticate, bearer, current_user
from ..db import get_db
from ..live import hub

router = APIRouter()


class Login(BaseModel):
    username: str
    password: str


class Refresh(BaseModel):
    refresh_token: str


class Logout(BaseModel):
    refresh_token: str | None = None


@router.post("/auth/login")
async def login(body: Login, db: AsyncSession = Depends(get_db)):
    u = await authenticate(db, body.username, body.password)
    if u is None:
        raise HTTPException(401, "wrong username or password")
    if u.disabled:
        raise HTTPException(403, "account disabled")
    return await auth.issue(u)


@router.post("/auth/refresh")
async def refresh(body: Refresh, db: AsyncSession = Depends(get_db)):
    return await auth.refresh(db, body.refresh_token)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, body: Logout | None = None):
    """Revokes the bearer access token and the refresh token in the body (either may be missing or expired)."""
    await auth.logout(bearer(request), body.refresh_token if body else None)
    return Response(status_code=204)


@router.get("/me")
async def me(user: Principal = Depends(current_user)):
    return {"username": user.username, "role": user.role}


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    out = {}
    try:
        await db.execute(text("SELECT 1"))
        out["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["db"] = f"error: {e}"
    try:
        await hub.redis.ping()
        out["redis"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["redis"] = f"error: {e}"
    out["s3"] = await asyncio.to_thread(storage.health)
    out["clips"] = media.clip_status()
    return out


@router.get("/files/{key:path}")
async def get_file(key: str, request: Request, exp: int | None = None, sig: str | None = None, token: str | None = None):
    """Evidence from the S3 store, with Range support so <video> can seek. Needs a signed link from
    the API (storage.file_url: ?exp=&sig=, PRD 28.5) or a token (bearer header or ?token=). An mp4 clip
    is served as its WebM once the background transcode has made it (media.queue_clip)."""
    if not storage.check_sig(key, exp, sig) and await auth.principal(bearer(request) or token) is None:
        if sig:
            raise HTTPException(403, "evidence link expired or invalid")
        raise HTTPException(401, "evidence needs a signed link or a token", headers={"WWW-Authenticate": "Bearer"})
    return await asyncio.to_thread(_serve_file, key, request.headers.get("range"))


def _serve_file(key: str, rng: str | None) -> StreamingResponse:
    if key.lower().endswith(".mp4") and storage.exists(media.webm_key(key)):
        key = media.webm_key(key)
    try:
        obj = storage.get(key, rng)
    except Exception:
        raise HTTPException(404, f"no file {key}")
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(obj["ContentLength"])}
    if obj.get("ContentRange"):
        headers["Content-Range"] = obj["ContentRange"]
    return StreamingResponse(obj["Body"].iter_chunks(1 << 16), status_code=206 if obj.get("ContentRange") else 200,
                             media_type=obj.get("ContentType") or "application/octet-stream", headers=headers)
