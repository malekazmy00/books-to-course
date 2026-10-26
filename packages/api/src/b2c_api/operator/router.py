"""Operator API v3 routes: ``/v3/operator/...`` (read-only stages and progress of every book).

Every route is a GET behind the app-wide bearer token (D-030). Nothing here starts, stops,
approves or changes anything; actions are a later phase that needs the owner's approval
(docs/proposals/2026-10-26-operator-section.md §5).

Load: every piece read from Postgres, the artifact store or Temporal is cached for
``CACHE_TTL_S`` seconds per app (one in-flight read per key), so any number of phones polling
every 15 s cost at most one light read per key per TTL.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from b2c_api.deps import is_course_id, log
from b2c_api.operator.progress import (
    STAGE_BY_ID,
    STAGES,
    BookInputs,
    book_aliases,
    book_records,
    book_runs,
    compute_stages,
    current_stage,
    iso,
    output_info,
    overall_percent,
    translation_counts,
    translation_progress,
    workflow_info,
)
from b2c_api.operator.reader import OperatorReader, PostgresOperatorReader, UnitRow
from b2c_api.operator.schemas import (
    API_VERSION,
    MIN_CLIENT_VERSION,
    BookList,
    BookStages,
    BookSummary,
    OperatorMeta,
    Rejection,
    RunInfo,
    StageDetail,
    UnitSample,
)

PREFIX = "/v3/operator"
CACHE_TTL_S = 20
REFRESH_INTERVAL_S = 15
SAMPLE_UNITS = 10
MAX_NOTES = 50
MAX_REJECTIONS = 50
UNAVAILABLE = "operator data is unavailable"

__all__ = ["CACHE_TTL_S", "PREFIX", "STAGES", "operator_router"]


class TTLCache:
    def __init__(self, ttl_s: float, monotonic: Callable[[], float]) -> None:
        self.ttl_s = ttl_s
        self.monotonic = monotonic
        self._items: dict[Hashable, tuple[float, Any]] = {}
        self._locks: dict[Hashable, asyncio.Lock] = {}

    async def get(self, key: Hashable, factory: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._items.get(key)
        if hit is not None and hit[0] > self.monotonic():
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._items.get(key)
            if hit is not None and hit[0] > self.monotonic():
                return hit[1]
            value = await factory()
            now = self.monotonic()
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            self._items[key] = (now + self.ttl_s, value)
            return value


def _json_response(request: Request, model: Any) -> Response:
    body = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def _issue_text(issue: dict[str, Any]) -> str:
    """``category (severity): check — explanation``; the evidence text is the check's own words, cut short."""
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    detail = str(evidence.get("explanation") or evidence.get("message") or "").strip()
    text = f"{issue.get('category')} ({issue.get('severity')})"
    if issue.get("check_id"):
        text += f": {issue['check_id']}"
    if detail:
        text += f" — {detail[:200]}"
    return text


def _short(book: dict[str, Any]) -> str:
    digest = str(book.get("file_sha256") or "")
    return digest[:8] if digest else str(book.get("book_id", ""))[-8:]


def operator_router(ctx: Any) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["operator"])
    state = ctx.app.state

    def reader() -> OperatorReader:
        if getattr(state, "operator_reader", None) is None:
            state.operator_reader = PostgresOperatorReader(ctx)
        return state.operator_reader

    def cache() -> TTLCache:
        if getattr(state, "operator_cache", None) is None:
            state.operator_cache = TTLCache(CACHE_TTL_S, getattr(state, "operator_monotonic", None) or time.monotonic)
        return state.operator_cache

    async def read(key: Hashable, name: str, *args: Any) -> Any:
        async def call() -> Any:
            try:
                return await getattr(reader(), name)(*args)
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("operator read failed", extra={"read": name})
                raise HTTPException(status_code=503, detail=UNAVAILABLE) from exc
        return await cache().get(key, call)

    async def workflow(workflow_id: str) -> Any:
        if not workflow_id:
            return None

        async def call() -> Any:
            try:
                return await reader().workflow(workflow_id)
            except Exception:  # Temporal down: the stage still shows, its workflow state is unknown
                log.warning("could not describe a workflow for the operator view")
                from b2c_api.operator.reader import WorkflowState
                return WorkflowState(workflow_id=workflow_id, status="UNKNOWN")
        return await cache().get(("workflow", workflow_id), call)

    def now_iso() -> str:
        return reader().now().isoformat()

    async def find_book(course_id: str, book_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not is_course_id(course_id) or not book_id or len(book_id) > 160:
            raise HTTPException(status_code=422, detail="invalid course_id or book_id")
        books = await read(("books", course_id), "books", course_id)
        for book in books:
            if book_id in book_aliases(book):
                return book, books
        raise HTTPException(status_code=404, detail="book not found")

    async def inputs_for(course_id: str, book: dict[str, Any], books: list[dict[str, Any]]) -> BookInputs:
        records = await read(("outputs", course_id), "stage_outputs", course_id)
        runs = book_runs(book, await read(("runs", course_id), "translation_runs", course_id))
        mine = book_records(book, records, runs, single_book=len(books) == 1)
        aliases = tuple(book_aliases(book))
        termbase = await read(("termbase", course_id, aliases), "termbase", course_id, list(aliases))
        wanted = [str(book.get("workflow_id") or "")] + ([runs[0].workflow_id] if runs else [])
        workflows = {wid: await workflow(wid) for wid in wanted if wid}
        outlines = [r for r in mine if r.artifact.stage == "build-outline-v2"]
        total = None
        if outlines:
            total = await read(("episodes", course_id, outlines[0].artifact.key), "episode_total", course_id,
                               outlines[0])
        return BookInputs(book=book, records=mine, runs=runs, termbase=termbase, workflows=workflows,
                          episode_total=total)

    async def stages_for(course_id: str, book: dict[str, Any], books: list[dict[str, Any]]):
        return compute_stages(await inputs_for(course_id, book, books))

    @router.get("/meta", response_model=OperatorMeta)
    async def operator_meta(request: Request) -> Response:
        return _json_response(request, OperatorMeta(
            api_version=API_VERSION, min_client_version=MIN_CLIENT_VERSION, cache_ttl_s=CACHE_TTL_S,
            refresh_interval_s=REFRESH_INTERVAL_S, stages=[s.info(i) for i, s in enumerate(STAGES, 1)]))

    @router.get("/books", response_model=BookList)
    async def operator_books(request: Request) -> Response:
        summaries: list[BookSummary] = []
        for course_id in await read(("courses",), "course_ids"):
            books = await read(("books", course_id), "books", course_id)
            for book in books:
                stages = await stages_for(course_id, book, books)
                current = current_stage(stages)
                updated = [s.updated_at for s in stages if s.updated_at]
                summaries.append(BookSummary(
                    course_id=course_id, book_id=str(book["book_id"]),
                    title=str(book.get("title") or book.get("display_name") or book["book_id"]),
                    display_name=str(book.get("display_name") or book.get("title") or book["book_id"]),
                    short_id=_short(book), file_sha256=book.get("file_sha256") or None,
                    uploaded_at=book.get("uploaded_at") or None, role=book.get("role"),
                    current_stage=current.stage_id if current else None,
                    current_stage_title=current.title if current else None,
                    current_status=current.status if current else "completed",
                    overall_percent=overall_percent(stages),
                    completed_stages=sum(s.status == "completed" for s in stages), stage_count=len(stages),
                    updated_at=max(updated) if updated else None))
        # Most recently active first; books with no recorded activity last.
        summaries.sort(key=lambda b: b.updated_at or "", reverse=True)
        return _json_response(request, BookList(books=summaries, generated_at=now_iso()))

    @router.get("/courses/{course_id}/books/{book_id}/stages", response_model=BookStages)
    async def operator_book_stages(course_id: str, book_id: str, request: Request) -> Response:
        book, books = await find_book(course_id, book_id)
        stages = await stages_for(course_id, book, books)
        return _json_response(request, BookStages(
            course_id=course_id, book_id=str(book["book_id"]),
            title=str(book.get("title") or book.get("display_name") or book["book_id"]), stages=stages,
            generated_at=now_iso()))

    async def unit_samples(course_id: str, run: Any, rows: list[UnitRow]) -> list[UnitSample]:
        ids = [r.unit_id for r in rows]
        sources = await read(("sources", course_id, run.run_id, tuple(ids)), "unit_sources", course_id,
                             run.units_ref, ids) if ids else {}
        return [UnitSample(unit_id=r.unit_id, seq=r.seq, decision=r.decision, source_text=sources.get(r.unit_id),
                           target_text=r.target_text or None,
                           reasons=list(dict.fromkeys(str(i.get("category")) for i in r.issues if i.get("category"))),
                           updated_at=r.updated_at.isoformat()) for r in rows]

    @router.get("/courses/{course_id}/books/{book_id}/stages/{stage_id}", response_model=StageDetail)
    async def operator_stage_detail(course_id: str, book_id: str, stage_id: str, request: Request) -> Response:
        book, books = await find_book(course_id, book_id)
        if stage_id not in STAGE_BY_ID:
            raise HTTPException(status_code=404, detail="stage not found")
        inp = await inputs_for(course_id, book, books)
        stage = next(s for s in compute_stages(inp) if s.stage_id == stage_id)
        records = inp.stage_records(stage_id) if stage_id not in ("translation", "translator_notes") else [
            r for r in inp.records if r.artifact.stage in STAGE_BY_ID[stage_id].registry_stages]
        detail = StageDetail(course_id=course_id, book_id=str(book["book_id"]), stage=stage,
                             outputs=[output_info(r) for r in records[:20]], generated_at=now_iso())
        rejections = [Rejection(subject=f"{r.artifact.stage}/{r.artifact.key[:16]}",
                                reason=r.status_reason or "rejected", at=r.status_changed_at.isoformat())
                      for r in records if r.status == "rejected"]
        run = inp.primary_run
        if stage_id in ("translation", "translator_notes") and run is not None:
            notes = await read(("notes", run.run_id), "units_by_decision", run.run_id,
                               ["needs_review", "rejected"], MAX_NOTES)
            detail.translator_notes = await unit_samples(course_id, run, notes)
            if stage_id == "translation":
                recent = await read(("recent", run.run_id), "recent_units", run.run_id, SAMPLE_UNITS)
                detail.recent_units = await unit_samples(course_id, run, recent)
                rejected = await read(("rejected", run.run_id), "units_by_decision", run.run_id, ["rejected"],
                                      MAX_REJECTIONS)
                rejections += [Rejection(subject=r.unit_id, reason="; ".join(_issue_text(i) for i in r.issues
                                                                         if i.get("category")) or "rejected",
                                         at=r.updated_at.isoformat()) for r in rejected]
                detail.runs = [RunInfo(run_id=r.run_id, created_at=r.created_at.isoformat(),
                                       updated_at=r.updated_at.isoformat(), unit_count=r.unit_count,
                                       counts=translation_counts(r), progress=translation_progress(r),
                                       workflow=workflow_info(await workflow(r.workflow_id)))
                               for r in inp.runs]
        if stage_id == "termbase":
            aliases = tuple(book_aliases(book))
            terms = await read(("term-rejections", course_id, aliases), "termbase_rejections", course_id,
                               list(aliases), MAX_REJECTIONS)
            rejections += [Rejection(subject=t.source, reason=t.reason or "rejected", at=iso(t.decided_at))
                           for t in terms]
        detail.rejections = rejections
        return _json_response(request, detail)

    return router
