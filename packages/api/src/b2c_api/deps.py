"""Shared request-path helpers for the API routers."""
from __future__ import annotations

import json
from typing import Any

from starlette.concurrency import run_in_threadpool

NOT_FOUND_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


def is_not_found(exc: BaseException) -> bool:
    """True for a missing S3 object (boto/moto ClientError, or a dict-backed fake)."""
    if isinstance(exc, KeyError):
        return True
    response = getattr(exc, "response", None) or {}
    if not isinstance(response, dict):
        return False
    return (response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
            or response.get("Error", {}).get("Code") in NOT_FOUND_CODES)


def _get_bytes(s3: Any, bucket: str, key: str) -> bytes | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if is_not_found(exc):
            return None
        raise
    body = obj["Body"]
    try:
        return body.read()
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()


async def s3_bytes(s3: Any, bucket: str, key: str) -> bytes | None:
    """GET and read an object off the event loop; ``None`` when it does not exist."""
    return await run_in_threadpool(_get_bytes, s3, bucket, key)


async def s3_json(s3: Any, bucket: str, key: str) -> Any | None:
    """Like :func:`s3_bytes`, decoded as JSON."""
    data = await s3_bytes(s3, bucket, key)
    return None if data is None else json.loads(data)
