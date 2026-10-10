"""Evidence store: S3 API on SeaweedFS (path-style), bucket `evidence`. boto3 is blocking, so
callers run these in a thread (FastAPI sync routes / asyncio.to_thread)."""

import base64
import hashlib
import hmac
import mimetypes
import time
from urllib.parse import quote

mimetypes.add_type("video/webm", ".webm")  # not in every Windows registry
from functools import cache
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from . import config


@cache
def client():
    return boto3.client("s3", endpoint_url=config.S3_ENDPOINT, aws_access_key_id=config.S3_ACCESS_KEY,
                        aws_secret_access_key=config.S3_SECRET_KEY, region_name="us-east-1",
                        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4",
                                      connect_timeout=3, read_timeout=30, retries={"max_attempts": 2}))


def ensure_bucket() -> None:
    try:
        client().head_bucket(Bucket=config.S3_BUCKET)
    except ClientError:
        client().create_bucket(Bucket=config.S3_BUCKET)


def health() -> str:
    try:
        client().head_bucket(Bucket=config.S3_BUCKET)
        return "ok"
    except Exception as e:  # noqa: BLE001 - reported, not raised
        return f"error: {e}"


def exists(key: str) -> int | None:
    """Object size, or None if absent."""
    try:
        return client().head_object(Bucket=config.S3_BUCKET, Key=key)["ContentLength"]
    except ClientError:
        return None


def upload(path: Path, key: str) -> bool:
    """Upload unless an object of the same size is already there; True if uploaded."""
    if exists(key) == path.stat().st_size:
        return False
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    client().upload_file(str(path), config.S3_BUCKET, key, ExtraArgs={"ContentType": ctype})
    return True


def put_bytes(body: bytes, key: str, ctype: str) -> None:
    client().put_object(Bucket=config.S3_BUCKET, Key=key, Body=body, ContentType=ctype)


def get(key: str, range_header: str | None = None) -> dict:
    kw = {"Bucket": config.S3_BUCKET, "Key": key}
    if range_header:
        kw["Range"] = range_header
    return client().get_object(**kw)


_SIGN_KEY = (config.FILE_URL_SECRET.encode() if config.FILE_URL_SECRET
             else hmac.new(config.JWT_SECRET.encode(), b"evidence-links", hashlib.sha256).digest())


def sign(key: str, exp: int) -> str:
    return base64.urlsafe_b64encode(hmac.new(_SIGN_KEY, f"{key}\n{exp}".encode(), hashlib.sha256).digest()[:18]).decode()


def check_sig(key: str, exp: int | None, sig: str | None) -> bool:
    """A file_url signature that matches `key` and has not expired."""
    return bool(exp and sig) and exp >= time.time() and hmac.compare_digest(sign(key, exp), sig)


def file_url(key: str, minutes: float | None = None) -> str:
    """PRD 28.5: a signed link that works without a token until `exp` (config.FILE_URL_MINUTES, rounded up
    to 5 min so repeated reads give the same URL and <video> does not reload)."""
    exp = int(-(-(time.time() + 60 * (config.FILE_URL_MINUTES if minutes is None else minutes)) // 300) * 300)
    # real clips' keys carry their folder name (spaces, commas): percent-encode, keep the path's slashes
    return f"/api/files/{quote(key, safe='/')}?exp={exp}&sig={sign(key, exp)}"
