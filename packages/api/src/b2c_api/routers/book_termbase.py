"""Per-book termbase review for the rebuilt translation path (D-028 principle 5, Gate G3).

A translation run builds the book's termbase before any unit is translated. A human reviewer
lists the entries, approves them (optionally with an edited Arabic target), rejects them, or
bulk-approves the proposals they selected; then the "done" action signals the waiting
``TranslateBookWorkflow``. Only approved entries are ever used by translation.
"""
from __future__ import annotations

from typing import Any, Literal

from b2c_orchestration.termbase_review import (MAX_TARGET_CHARS, TermbaseDecisionRejected, TermbaseNotFound,
                                               TermSelection, approve_terms, gate_counts, reject_term)
from b2c_orchestration.termbase_store import STATUSES, TermbaseScope, TermbaseStore
from b2c_orchestration.translate_book import TERMBASE_QUERY, TERMBASE_SIGNAL, TermbaseApproval
from b2c_orchestration.translation_store import RUN_ID, TranslationResultStore
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from b2c_api.deps import ApiContext, log

REVIEWER_ID = r"^[A-Za-z0-9._@-]{1,120}$"
MAX_SOURCE_CHARS = 200
MAX_BULK = 500


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApproveTermRequest(_Body):
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    target: str | None = Field(default=None, max_length=MAX_TARGET_CHARS * 2)  # None = top proposal
    reviewer_id: str = Field(pattern=REVIEWER_ID)
    replace: bool = False
    allow_shared_target: bool = False


class RejectTermRequest(_Body):
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    reviewer_id: str = Field(pattern=REVIEWER_ID)
    reason: str = Field(default="", max_length=500)


class ProposalSelection(_Body):
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    target: str | None = Field(default=None, max_length=MAX_TARGET_CHARS * 2)  # must be one of its proposals


class BulkApproveRequest(_Body):
    reviewer_id: str = Field(pattern=REVIEWER_ID)
    selections: list[ProposalSelection] = Field(min_length=1, max_length=MAX_BULK)


class TermbaseDoneRequest(_Body):
    reviewer_id: str = Field(pattern=REVIEWER_ID)


def book_termbase_router(ctx: ApiContext, router: Any = None) -> Any:
    router = APIRouter() if router is None else router
    app, settings = ctx.app, ctx.settings

    def result_store() -> TranslationResultStore:
        if getattr(app.state, "translation_result_store", None) is None:
            app.state.translation_result_store = TranslationResultStore(settings.pg_dsn)
        return app.state.translation_result_store

    def termbase_store() -> TermbaseStore:
        if getattr(app.state, "termbase_store", None) is None:
            app.state.termbase_store = TermbaseStore(settings.pg_dsn)
        return app.state.termbase_store

    async def db(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(fn, *args, **kwargs)
        except (HTTPException, TermbaseDecisionRejected, TermbaseNotFound):
            raise
        except Exception as exc:
            log.exception("termbase store failed")
            raise HTTPException(status_code=503, detail="termbase store is unavailable") from exc

    async def run_scope(run_id: str) -> tuple[Any, TermbaseScope]:
        if RUN_ID.fullmatch(run_id or "") is None:
            raise HTTPException(status_code=404, detail="translation run not found")
        run = await db(result_store().get_run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="translation run not found")
        if not run.book_id:
            raise HTTPException(status_code=409, detail="translation units are not prepared yet")
        inp = run.input_json
        return run, TermbaseScope(run.course_id, run.book_id, inp["source_language"], inp["target_language"])

    async def built_scope(run_id: str) -> tuple[Any, TermbaseScope]:
        run, scope = await run_scope(run_id)
        if await db(termbase_store().get_build, scope) is None:
            raise HTTPException(status_code=409, detail="the termbase of this book is not built yet")
        return run, scope

    def min_frequency(run: Any) -> int:
        return int(run.input_json.get("termbase_min_frequency", 1))

    async def decide(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await db(fn, *args, **kwargs)
        except TermbaseNotFound as exc:
            raise HTTPException(status_code=404, detail="termbase entry not found") from exc
        except TermbaseDecisionRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/v2/book-translations/{run_id}/termbase")
    async def list_termbase(run_id: str, status: list[Literal["pending", "approved", "rejected"]] = Query(default=[]),
                            kind: list[Literal["term", "acronym", "person", "organization", "place", "work"]] = Query(
                                default=[]),
                            min_frequency_filter: int | None = Query(default=None, ge=1, le=100000,
                                                                     alias="min_frequency"),
                            q: str | None = Query(default=None, min_length=1, max_length=100),
                            include_absent: bool = False,
                            limit: int = Query(default=100, ge=1, le=500),
                            offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
        run, scope = await built_scope(run_id)
        entries = await db(termbase_store().entries, scope)
        build = await db(termbase_store().get_build, scope)
        needle = (q or "").casefold()
        selected = [e for e in entries
                    if (not status or e.status in status) and (not kind or e.kind in kind)
                    and (min_frequency_filter is None or e.frequency >= min_frequency_filter)
                    and (include_absent or e.present)
                    and (not needle or needle in e.source.casefold() or needle in (e.target or "")
                         or any(needle in p.target for p in e.proposals))]
        selected.sort(key=lambda e: (-e.frequency, e.source_key))
        return {"run_id": run_id, "book_id": scope.book_id, "course_id": scope.course_id,
                "require_approved_termbase": bool(run.input_json.get("require_approved_termbase", True)),
                "gate": gate_counts(entries, min_frequency(run)).as_json(),
                "build": {"artifact": build.artifact_ref, "termbase_sha256": build.termbase_sha256,
                          "entry_count": build.entry_count, "built_at": build.built_at.isoformat()},
                "statuses": list(STATUSES), "total": len(selected), "limit": limit, "offset": offset,
                "items": [e.as_json() for e in selected[offset:offset + limit]]}

    @router.post("/v2/book-translations/{run_id}/termbase/approve")
    async def approve_termbase_entry(run_id: str, request: ApproveTermRequest) -> dict[str, Any]:
        _, scope = await built_scope(run_id)
        [entry] = await decide(approve_terms, termbase_store(), scope,
                               [TermSelection(request.source, request.target)], request.reviewer_id,
                               replace=request.replace, allow_shared_target=request.allow_shared_target)
        return entry.as_json()

    @router.post("/v2/book-translations/{run_id}/termbase/reject")
    async def reject_termbase_entry(run_id: str, request: RejectTermRequest) -> dict[str, Any]:
        _, scope = await built_scope(run_id)
        entry = await decide(reject_term, termbase_store(), scope, request.source, request.reviewer_id,
                             request.reason)
        return entry.as_json()

    @router.post("/v2/book-translations/{run_id}/termbase/bulk-approve")
    async def bulk_approve_termbase(run_id: str, request: BulkApproveRequest) -> dict[str, Any]:
        _, scope = await built_scope(run_id)
        entries = await decide(approve_terms, termbase_store(), scope,
                               [TermSelection(s.source, s.target) for s in request.selections],
                               request.reviewer_id, proposals_only=True)
        return {"approved": len(entries), "items": [e.as_json() for e in entries]}

    @router.post("/v2/book-translations/{run_id}/termbase/done", status_code=202)
    async def termbase_review_done(run_id: str, request: TermbaseDoneRequest) -> dict[str, Any]:
        run, scope = await built_scope(run_id)
        counts = gate_counts(await db(termbase_store().entries, scope), min_frequency(run))
        if counts.pending_blocking:
            raise HTTPException(status_code=409, detail={
                "message": "pending termbase entries at or above the frequency threshold remain",
                "gate": counts.as_json()})
        client = await ctx.connected_temporal()
        handle = client.get_workflow_handle(run.workflow_id)
        try:
            await handle.signal(TERMBASE_SIGNAL, TermbaseApproval(reviewer_id=request.reviewer_id))
        except Exception as exc:
            log.warning("could not signal %s", run.workflow_id)
            raise HTTPException(status_code=409, detail="the translation workflow is not waiting") from exc
        state = None
        try:
            state = await handle.query(TERMBASE_QUERY)
        except Exception:
            log.warning("could not query %s", run.workflow_id)
        return {"run_id": run_id, "workflow_id": run.workflow_id, "signalled": True, "gate": counts.as_json(),
                "termbase_state": state}

    return router
