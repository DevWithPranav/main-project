"""Login, who-am-I, health and evidence files."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import media, storage
from ..auth import Principal, authenticate, current_user, make_token
from ..db import get_db
from ..live import hub

router = APIRouter()


class Login(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
async def login(body: Login, db: AsyncSession = Depends(get_db)):
    u = await authenticate(db, body.username, body.password)
    if u is None:
        raise HTTPException(401, "wrong username or password")
    return {"access_token": make_token(u.username, u.role), "token_type": "bearer", "role": u.role}


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
def get_file(key: str, request: Request):
    """Evidence from the S3 store, with Range support so <video> can seek. Open without a token in
    dev so <video src> works (see API.md). An mp4 clip is served as its WebM once the background
    transcode has made it (media.queue_clip)."""
    if key.lower().endswith(".mp4") and storage.exists(media.webm_key(key)):
        key = media.webm_key(key)
    try:
        obj = storage.get(key, request.headers.get("range"))
    except Exception:
        raise HTTPException(404, f"no file {key}")
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(obj["ContentLength"])}
    if obj.get("ContentRange"):
        headers["Content-Range"] = obj["ContentRange"]
    return StreamingResponse(obj["Body"].iter_chunks(1 << 16), status_code=206 if obj.get("ContentRange") else 200,
                             media_type=obj.get("ContentType") or "application/octet-stream", headers=headers)
