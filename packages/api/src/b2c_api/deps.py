"""Shared request-path helpers for the API routers."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

log = logging.getLogger("b2c.api")
NOT_FOUND_CODES = frozenset({"NoSuchKey", "404", "NotFound"})

# Every server-issued workflow ID is a fixed prefix plus a lowercase SHA-256
# digest; selected-unit retranslation candidates use a 128-bit hex suffix.
WORKFLOW_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    kind: re.compile(rf"b2c-{kind}-[0-9a-f]{{{width}}}")
    for kind, width in (("ingest", 64), ("index", 64), ("translate", 64), ("mp3", 64),
                        ("qa", 64), ("retranslate", 32))
}
STAGE_CONTROL_ID = re.compile(r"b2c-(ingest|index|translate|mp3|qa)-[0-9a-f]{64}")
COURSE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
BOOK_ID = re.compile(r"[A-Za-z0-9._-]{1,120}")


async def describe_or_404(client: Any, workflow_id: str,
                          detail: str = "workflow not found") -> tuple[Any, Any]:
    """Describe a workflow: Temporal NOT_FOUND -> 404, any other failure -> 503."""
    from temporalio.service import RPCError, RPCStatusCode

    handle = client.get_workflow_handle(workflow_id)
    try:
        return handle, await handle.describe()
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc


async def verified_ref(store: Any, course_id: str, ref: Any, stage: str, label: str) -> Any:
    """Accept a client-supplied ArtifactRef only if it is the committed manifest.

    The ref must name ``stage``, live at the canonical
    ``s3://{bucket}/{course_id}/{stage}/{key}/`` prefix, and match the stored
    manifest's sha256 and URI (same rule as ingestion recovery).
    """
    from b2c_api.ingestion_recovery import checked_ref

    if ref.stage != stage:
        raise HTTPException(status_code=422, detail=f"{label} has the wrong artifact stage")
    try:
        await checked_ref(store, course_id, ref.model_dump(mode="json"), stage)
    except ValueError as exc:
        raise HTTPException(status_code=422,
                            detail=f"{label} is not a committed artifact of this course") from exc
    except Exception as exc:
        log.exception("could not verify %s", label)
        raise HTTPException(status_code=503, detail=f"{label} could not be verified") from exc
    return ref


def is_workflow_id(kind: str, workflow_id: str) -> bool:
    return WORKFLOW_ID_PATTERNS[kind].fullmatch(workflow_id) is not None


def require_workflow_id(kind: str, workflow_id: str, detail: str = "workflow not found") -> str:
    """404 unless ``workflow_id`` is exactly a server-issued ID of ``kind``."""
    if not is_workflow_id(kind, workflow_id):
        raise HTTPException(status_code=404, detail=detail)
    return workflow_id


def is_course_id(course_id: str | None) -> bool:
    return bool(course_id) and COURSE_ID.fullmatch(course_id) is not None and ".." not in course_id


def require_course_id(course_id: str) -> str:
    if not is_course_id(course_id):
        raise HTTPException(status_code=422, detail="invalid course_id")
    return course_id


def require_book_id(book_id: str) -> str:
    if not BOOK_ID.fullmatch(book_id or ""):
        raise HTTPException(status_code=422, detail="invalid book_id")
    return book_id


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
