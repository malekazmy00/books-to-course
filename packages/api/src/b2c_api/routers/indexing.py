"""Index workflow status (with committed-index recovery) and the run's chunk view."""
from __future__ import annotations

from typing import Any

import psycopg

from b2c_indexing.postgres_store import PostgresChunkStore
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from temporalio.service import RPCError, RPCStatusCode
from b2c_api.ingestion_recovery import recover_index
from b2c_api.deps import (
    TERMINAL_FAILURE_STATUSES,
    workflow_failure,
    require_course_id,
    require_workflow_id,
)

from fastapi import APIRouter
from b2c_api.deps import ApiContext, log


def indexing_router(ctx: ApiContext, router: Any = None) -> Any:
    """Register the routes on ``router`` (the app itself in ``create_app``,
    keeping the route table flat and in its original order)."""
    router = APIRouter() if router is None else router
    settings = ctx.settings
    get_store = ctx.get_store
    get_temporal_client = ctx.get_temporal_client

    @router.get("/v1/indexings/{workflow_id}")
    async def indexing_status(workflow_id: str, recovery_course_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("index", workflow_id, "indexing workflow not found")
        if recovery_course_id is not None:
            require_course_id(recovery_course_id)
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
            store = get_store()
            try:
                recovered = await recover_index(store, PostgresChunkStore(lambda: psycopg.connect(settings.pg_dsn)),
                                                workflow_id, course_id=recovery_course_id)
            except Exception as recovery_exc:
                log.exception("failed to recover committed index")
                raise HTTPException(status_code=503, detail="stored index is unavailable") from recovery_exc
            if recovered is None:
                raise HTTPException(status_code=404, detail="index history expired and no matching committed index is available") from exc
            return {"workflow_id": workflow_id, "status": "COMPLETED", "result": recovered,
                    "recovered_from_artifact": True, "workflow_history_available": False,
                    "completion_scope": "persisted_index"}
        except Exception as exc:
            log.exception("failed to read indexing status")
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
        status_name = getattr(getattr(description, "status", None), "name", "")
        response: dict[str, Any] = {"workflow_id": workflow_id, "status": status_name}
        if status_name == "COMPLETED":
            try:
                response["result"] = await handle.result()
            except Exception as exc:
                log.exception("failed to read completed indexing result")
                raise HTTPException(status_code=503, detail="indexing result is unavailable") from exc
        elif status_name in TERMINAL_FAILURE_STATUSES:
            if failure := await workflow_failure(handle):
                response["failure"] = failure
        return response

    @router.get("/v1/indexings/{workflow_id}/chunks")
    async def indexing_chunks(workflow_id: str, course_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("index", workflow_id, "indexing workflow not found")
        status = await indexing_status(workflow_id, recovery_course_id=course_id)
        if status["status"] != "COMPLETED":
            raise HTTPException(status_code=409, detail="indexing is not complete")
        try:
            result = status["result"]
            course_id = result.get("course_id", "")
            if not course_id:
                raise HTTPException(status_code=503, detail="index course is missing")
            # The workflow's result is versioned and may not include an artifact
            # reference (older workers returned only chunk IDs/counts). Read the
            # already-persisted chunks, scoped to exactly this run's books and
            # chunk IDs: the course table also holds other runs' chunks.
            run_books = result.get("books") or {}
            run_chunk_ids = {chunk_id for item in run_books.values() if isinstance(item, dict)
                             for chunk_id in item.get("chunk_ids") or []}
            if not run_books or not run_chunk_ids:
                raise HTTPException(status_code=503, detail="index run does not record its chunk identities")
            store = PostgresChunkStore(lambda: psycopg.connect(settings.pg_dsn))
            chunks = await run_in_threadpool(lambda: store.list_chunks(course_id, book_ids=set(run_books)))
            chunks = [chunk for chunk in chunks if chunk.chunk_id in run_chunk_ids]
            if not chunks:
                raise ValueError("no persisted index chunks found")
            return {"workflow_id": workflow_id, "course_id": course_id,
                    "chunk_count": len(chunks),
                    "chunks": [chunk.model_dump(mode="json") for chunk in chunks]}
        except HTTPException: raise
        except Exception as exc:
            log.exception("failed to read completed index chunks")
            raise HTTPException(status_code=503,detail="index chunks are unavailable") from exc

    ctx.provide(indexing_status=indexing_status)
    return router
