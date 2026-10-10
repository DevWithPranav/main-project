"""Evidence store: S3 API on SeaweedFS (path-style), bucket `evidence`. boto3 is blocking, so
callers run these in a thread (FastAPI sync routes / asyncio.to_thread)."""

import mimetypes
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


def get(key: str, range_header: str | None = None) -> dict:
    kw = {"Bucket": config.S3_BUCKET, "Key": key}
    if range_header:
        kw["Range"] = range_header
    return client().get_object(**kw)


def file_url(key: str) -> str:
    return f"/api/files/{key}"
