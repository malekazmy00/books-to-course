"""HTTP interface for source-book ingestion: app factory, wiring and lifespan.

Endpoints live in ``b2c_api.routers``; shared request-path helpers in
``b2c_api.deps``; request bodies in ``b2c_api.schemas``.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from b2c_core import Settings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from b2c_api.course_library import course_library_router
from b2c_api.deps import ApiContext
from b2c_api.model_lifecycle import ModelLifecycle, configured_providers, lifecycle_router
from b2c_api.result_exports import result_exports_router
from b2c_api.routers.audio import audio_router
from b2c_api.routers.book_termbase import book_termbase_router
from b2c_api.routers.book_translations import book_translations_router
from b2c_api.routers.books import _deduplicate_library_books, books_router
from b2c_api.routers.indexing import indexing_router
from b2c_api.routers.ingestion import _validate_course_supplementary_identity, ingestion_router
from b2c_api.routers.qa import qa_router
from b2c_api.routers.serving import serving_router
from b2c_api.routers.translations import translations_router
from b2c_api.routers.workflow_controls import workflow_controls_router
from b2c_api.saved_audio import saved_audio_router
from b2c_api.services.book_progress import bind_book_progress
from b2c_api.services.translation_units import bind_translation_units
from b2c_api.upload_limits import UPLOAD_MULTIPART_ALLOWANCE, UploadSizeLimitMiddleware

__all__ = ["UPLOAD_MULTIPART_ALLOWANCE", "_deduplicate_library_books",
           "_validate_course_supplementary_identity", "app", "create_app", "main"]


async def _connect_temporal(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                data_converter=pydantic_data_converter)


def create_app(
    *,
    settings: Settings | None = None,
    artifact_store: Any | None = None,
    temporal_connect: Callable[[Settings], Awaitable[Any]] = _connect_temporal,
    serving_catalog: Any | None = None,
    srs_store: Any | None = None,
    learner_authenticator: Callable[[Request], Any] | None = None,
    model_lifecycle: ModelLifecycle | None = None,
) -> FastAPI:
    settings = settings or Settings()
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.model_lifecycle.ensure_warm_loop()
        try:
            yield
        finally:
            await application.state.model_lifecycle.close()

    app = FastAPI(title="Book2Course API", version="0.1.0", lifespan=lifespan)
    # Inner to the bearer-token middleware below: unauthenticated requests are
    # refused first, then oversized PDF uploads are cut off before spooling.
    app.add_middleware(UploadSizeLimitMiddleware, path="/v1/books",
                       max_body_bytes=settings.max_upload_bytes + UPLOAD_MULTIPART_ALLOWANCE)
    api_auth_required = os.getenv("B2C_API_AUTH_REQUIRED", "true").strip().lower() in {"1", "true", "yes"}
    api_token = os.getenv("B2C_APP_API_TOKEN", "").strip()
    token_file = os.getenv("B2C_APP_API_TOKEN_FILE", "").strip()
    if not api_token and token_file:
        try:
            api_token = open(token_file, encoding="utf-8").read().strip()
        except OSError:
            api_token = ""
    if api_auth_required and not api_token:
        raise RuntimeError("B2C API auth is required but its token is unavailable")
    app.state.model_lifecycle = model_lifecycle or ModelLifecycle(
        configured_providers(settings), preferences_file=os.getenv("B2C_MODEL_PREFERENCES_FILE", ""),
        catalog_file=os.getenv("B2C_MODEL_CATALOG_FILE", ""),
        secrets_dir=os.getenv("B2C_MODEL_SECRETS_DIR", ""))
    app.include_router(lifecycle_router(app.state.model_lifecycle, api_token))

    @app.middleware("http")
    async def require_mobile_api_token(request: Request, call_next: Callable[..., Any]) -> Any:
        if not api_auth_required or (request.method == "GET" and request.url.path == "/health"):
            return await call_next(request)
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {api_token}"
        if not secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return JSONResponse(status_code=401, content={"detail": "valid bearer token required"})
        return await call_next(request)
    app.state.artifact_store = artifact_store
    app.state.temporal_client = None
    app.state.serving_catalog = serving_catalog
    app.state.srs_store = srs_store
    app.state.learner_authenticator = learner_authenticator
    ctx = ApiContext(app, settings, temporal_connect)

    async def model_worker_queue_ready(provider) -> bool:
        # A workflow poller is enough to accept and persist a translation request.
        # Activity pollers can temporarily disappear while a long translation activity
        # occupies the worker's configured concurrency slot; that is queue saturation,
        # not a missing worker or an unavailable model. The workflow will wait for the
        # activity slot and remain resumable through Temporal.
        if provider.task_queue != "hy-translation" and provider.translation_engine != "booktrans-pro":
            return True
        from temporalio.api.enums.v1 import TaskQueueType
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
        client = await ctx.get_temporal_client()
        queue = await client.workflow_service.describe_task_queue(DescribeTaskQueueRequest(
            namespace=client.namespace, task_queue=TaskQueue(name=provider.task_queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW))
        return bool(queue.pollers)

    app.state.model_lifecycle.route_ready = model_worker_queue_ready

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Services first; routers find them (and each other) through ctx. The
    # stage routers register directly on the app so the route table stays
    # flat and in its original order.
    bind_book_progress(ctx)
    bind_translation_units(ctx)
    books_router(ctx, app)
    list_course_books = ctx.late("list_course_books")
    app.include_router(course_library_router(ctx.get_store, settings.s3_bucket, list_course_books))
    app.include_router(saved_audio_router(ctx.get_store, ctx.late("book_progress"), list_course_books,
                                          ctx.late("list_course_audio_runs")))
    app.include_router(result_exports_router(ctx.get_store, settings.s3_bucket))
    ingestion_router(ctx, app)
    indexing_router(ctx, app)
    translations_router(ctx, app)
    book_translations_router(ctx, app)
    book_termbase_router(ctx, app)
    audio_router(ctx, app)
    qa_router(ctx, app)
    workflow_controls_router(ctx, app)
    serving_router(ctx, app)
    return app


app = create_app()


def main() -> None:
    uvicorn.run("b2c_api.app:app", host="0.0.0.0", port=8000)
