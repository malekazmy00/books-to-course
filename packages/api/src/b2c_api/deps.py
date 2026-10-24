"""Shared request-path helpers for the API routers."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from temporalio.common import WorkflowIDReusePolicy

log = logging.getLogger("b2c.api")
NOT_FOUND_CODES = frozenset({"NoSuchKey", "404", "NotFound"})
CHUNK_BYTES = 1024 * 1024
# Deterministic workflow IDs make a repeated POST idempotent only if Temporal
# refuses to start a second run after a successful one. A failed, cancelled,
# terminated or timed-out run may still be retried under the same ID. The QA
# checkpoint resume and failed-stage retry flows mint their own derived IDs.
REUSE_ONLY_AFTER_FAILURE = WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
TERMINAL_FAILURE_STATUSES = frozenset({"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"})


class MissingObject(LookupError):
    """A checkpoint object that has not been written yet."""


class ApiContext:
    """Per-app dependencies shared by the routers built in ``create_app``.

    Routers register the functions other routers call (``provide``) and look
    them up at request time (``late``), so router modules never import each
    other and build order does not matter.
    """

    def __init__(self, app: Any, settings: Any, temporal_connect: Callable[[Any], Awaitable[Any]]) -> None:
        self.app = app
        self.settings = settings
        self._temporal_connect = temporal_connect
        self._temporal_lock = asyncio.Lock()
        self._provided: dict[str, Callable[..., Any]] = {}

    def get_store(self) -> Any:
        """The app's artifact store, created from settings on first use."""
        if self.app.state.artifact_store is None:
            from b2c_core import ArtifactStore
            self.app.state.artifact_store = ArtifactStore.from_settings(self.settings)
        return self.app.state.artifact_store

    async def get_temporal_client(self) -> Any:
        # Concurrent first requests must share one connection, not race to
        # open several and leak all but the last.
        if self.app.state.temporal_client is None:
            async with self._temporal_lock:
                if self.app.state.temporal_client is None:
                    self.app.state.temporal_client = await self._temporal_connect(self.settings)
        return self.app.state.temporal_client

    async def connected_temporal(self) -> Any:
        try:
            return await self.get_temporal_client()
        except Exception as exc:
            log.exception("could not connect to Temporal")
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc

    def generation_profile(self, stage: str) -> tuple[str, str, str]:
        from b2c_orchestration.queues import HEAVY

        manager = self.app.state.model_lifecycle
        provider_id = manager.defaults.get(stage, "")
        provider = manager.providers.get(provider_id)
        if provider_id:
            if provider is None or not provider.task_queue:
                raise HTTPException(status_code=503, detail="the saved model route is currently disabled")
            return provider.model, provider.task_queue, provider.id
        return (self.settings.llm_model_id, os.getenv("B2C_GENERATION_TASK_QUEUE", HEAVY),
                "configured-llm")

    def provide(self, **functions: Callable[..., Any]) -> None:
        self._provided.update(functions)

    def late(self, name: str) -> Callable[..., Any]:
        """A function another router or service provides.

        Already-provided functions are returned as-is; otherwise an async proxy
        resolves the name at call time (routers may reference each other).
        """
        if name in self._provided:
            return self._provided[name]

        async def call(*args: Any, **kwargs: Any) -> Any:
            return await self._provided[name](*args, **kwargs)
        call.__name__ = call.__qualname__ = name
        return call


async def workflow_failure(handle: Any) -> dict[str, str] | None:
    """Failure type of a terminal workflow, read from its result (None if it returned)."""
    try:
        await handle.result()
    except Exception as exc:
        cause = getattr(exc, "cause", None)
        return {"type": getattr(cause, "type", type(cause or exc).__name__)}
    return None

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


STREAM_CHUNK_BYTES = 64 * 1024


async def stream_artifact_file(store: Any, ref: Any, name: str, *, media_type: str, bucket: str | None = None,
                               headers: dict[str, str] | None = None) -> Any:
    """Stream one committed artifact file from S3 in bounded chunks.

    Mirrors ``ArtifactStore.read``'s addressing (``<ref prefix>/<name>``) but
    never materializes the object; the body is closed when the response ends.
    """
    from starlette.background import BackgroundTask
    from starlette.responses import StreamingResponse

    if not name or name.startswith("/") or "\\" in name or any(
            part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError(f"unsafe artifact file name: {name!r}")
    bucket = bucket or store.bucket
    prefix = ref.uri.removeprefix(f"s3://{bucket}/")
    if prefix == ref.uri:
        raise ValueError("artifact is outside the configured bucket")
    obj = await run_in_threadpool(store.s3.get_object, Bucket=bucket, Key=prefix + name)
    body = obj["Body"]

    def chunks():
        try:
            while data := body.read(STREAM_CHUNK_BYTES):
                yield data
        finally:
            body.close()

    response_headers = dict(headers or {})
    if obj.get("ContentLength") is not None:
        response_headers["Content-Length"] = str(obj["ContentLength"])
    return StreamingResponse(chunks(), media_type=media_type, headers=response_headers,
                             background=BackgroundTask(body.close))


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


try:  # boto is always present with the S3 store, but keep deps importable without it
    from botocore.exceptions import BotoCoreError, ClientError
    _BOTO_ERRORS: tuple[type[BaseException], ...] = (BotoCoreError, ClientError)
except ImportError:  # pragma: no cover
    _BOTO_ERRORS = ()

# Failures of the object store (or of API lookups that wrap it as 503/404),
# plus corrupt stored JSON. Anything else in a best-effort write is a bug.
STORAGE_ERRORS: tuple[type[BaseException], ...] = (
    *_BOTO_ERRORS, OSError, json.JSONDecodeError, HTTPException)
PROGRESS_WARNING = "book progress could not be saved; the workflow was started"


def with_progress_warning(response: dict[str, Any], warning: str | None) -> dict[str, Any]:
    if warning:
        response["progress_warning"] = warning
    return response


def validation_locations(exc: Any) -> list[dict[str, Any]]:
    """Pydantic error locations and types only; never the offending input values."""
    return [{"loc": list(error.get("loc", ())), "type": error.get("type", "value_error")}
            for error in exc.errors(include_input=False, include_context=False, include_url=False)]


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
