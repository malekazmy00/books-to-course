"""Rebuilt book translation path (D-028 part D): start, status, review queue, human review, re-assembly.

The API starts ``TranslateBookWorkflow`` / ``AssembleBookTranslationWorkflow`` through Temporal
and reads unit results from the Postgres result store and the units artifact. Engine and QE
are chosen by profile *name*; the profiles themselves are worker configuration.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from b2c_contracts import ArtifactRef, TranslationUnit
from b2c_orchestration.queues import TRANSLATE_BOOK
from b2c_orchestration.translate_book import (PARSED_STAGE, TERMBASE_QUERY, UNITS_STAGE, AssembleBookTranslationWorkflow,
                                              AssembleTranslationInput, TranslateBookInput, TranslateBookWorkflow,
                                              UnitsCache, assembly_workflow_id, translate_book_workflow_id,
                                              translation_run_id)
from b2c_orchestration.translation_profiles import PROFILE_ID_PATTERN, UnknownProfile, load_translation_profiles
from b2c_orchestration.translation_review import ReviewConflict, ReviewRejected, apply_human_review
from b2c_orchestration.translation_store import RUN_ID, StoredUnitResult, TranslationResultStore
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from temporalio.exceptions import WorkflowAlreadyStartedError

from b2c_api.deps import REUSE_ONLY_AFTER_FAILURE, ApiContext, log, require_course_id, verified_ref

UNIT_ID = re.compile(r"[A-Za-z0-9._-]{1,120}:u\d{5,9}")
QUEUE_DECISIONS = ("needs_review", "rejected")
DECISIONS = ("accepted", "needs_review", "rejected", "dropped")
_UNITS = UnitsCache(size=4)


class BookTranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_artifact_ref: ArtifactRef
    source_language: Literal["en", "ar"]
    target_language: Literal["en", "ar"]
    engine_profile: str = Field(pattern=PROFILE_ID_PATTERN)
    qe_profile: str | None = Field(default=None, pattern=PROFILE_ID_PATTERN)
    max_unit_chars: int = Field(default=400, ge=40, le=4000)
    batch_size: int = Field(default=25, ge=1, le=200)
    # D-028 principle 5: translation waits until the book termbase is reviewed (see book_termbase.py)
    require_approved_termbase: bool = True
    termbase_min_frequency: int = Field(default=1, ge=1, le=1000)


class HumanReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "edit"]
    text: str | None = Field(default=None, max_length=20000)
    reviewer_id: str = Field(pattern=r"^[A-Za-z0-9._@-]{1,120}$")


def _result_json(result: StoredUnitResult) -> dict[str, Any]:
    t = result.translation
    return {"unit_id": result.unit_id, "seq": result.seq, "decision": t.decision, "origin": t.origin,
            "decided_by": t.decided_by, "target_text": t.target_text, "attempts": t.attempts,
            "engine": None if t.engine is None else t.engine.model_dump(mode="json"),
            "issues": [i.model_dump(mode="json") for i in t.issues],
            "qe": None if t.qe is None else t.qe.model_dump(mode="json"),
            "decided_at": t.decided_at.isoformat(), "updated_at": result.updated_at.isoformat()}


def book_translations_router(ctx: ApiContext, router: Any = None) -> Any:
    router = APIRouter() if router is None else router
    app, settings = ctx.app, ctx.settings

    def result_store() -> TranslationResultStore:
        if getattr(app.state, "translation_result_store", None) is None:
            app.state.translation_result_store = TranslationResultStore(settings.pg_dsn)
        return app.state.translation_result_store

    def memory() -> Any:
        if getattr(app.state, "translation_memory", None) is None:
            from b2c_translation.memory import TranslationMemory
            app.state.translation_memory = TranslationMemory(settings.pg_dsn)
        return app.state.translation_memory

    def profiles() -> Any:
        if getattr(app.state, "translation_profiles", None) is None:
            try:
                app.state.translation_profiles = load_translation_profiles(settings)
            except Exception as exc:
                log.exception("translation profiles unavailable")
                raise HTTPException(status_code=503, detail="translation profiles are unavailable") from exc
        return app.state.translation_profiles

    async def db(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(fn, *args, **kwargs)
        except (HTTPException, ValueError, ReviewConflict):
            raise
        except Exception as exc:
            log.exception("translation result store failed")
            raise HTTPException(status_code=503, detail="translation result store is unavailable") from exc

    def require_run_id(run_id: str) -> str:
        if RUN_ID.fullmatch(run_id or "") is None:
            raise HTTPException(status_code=404, detail="translation run not found")
        return run_id

    async def load_run(run_id: str) -> Any:
        run = await db(result_store().get_run, require_run_id(run_id))
        if run is None:
            raise HTTPException(status_code=404, detail="translation run not found")
        return run

    async def run_units(run: Any) -> list[TranslationUnit]:
        if not run.units_ref:
            raise HTTPException(status_code=409, detail="translation units are not prepared yet")
        ref = ArtifactRef.model_validate(run.units_ref)
        if ref.stage != UNITS_STAGE or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{run.course_id}/"):
            raise HTTPException(status_code=409, detail="translation units reference is out of scope")
        try:
            return await run_in_threadpool(_UNITS.get, ctx.get_store(), ref)
        except Exception as exc:
            log.exception("could not read translation units")
            raise HTTPException(status_code=503, detail="translation units could not be read") from exc

    async def start(workflow: Any, inp: Any, workflow_id: str) -> bool:
        client = await ctx.connected_temporal()
        try:
            await client.start_workflow(workflow, inp, id=workflow_id, task_queue=TRANSLATE_BOOK,
                                        id_reuse_policy=REUSE_ONLY_AFTER_FAILURE)
        except WorkflowAlreadyStartedError:
            return True
        except Exception as exc:
            log.exception("could not start %s", workflow_id)
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
        return False

    @router.post("/v2/courses/{course_id}/book-translations", status_code=202)
    async def start_book_translation(course_id: str, request: BookTranslationRequest) -> dict[str, Any]:
        require_course_id(course_id)
        source = request.source_artifact_ref
        if source.stage != PARSED_STAGE or not source.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
            raise HTTPException(status_code=422, detail="source artifact is outside this course or has the wrong stage")
        await verified_ref(ctx.get_store(), course_id, source, PARSED_STAGE, "source artifact")
        try:
            fingerprint = profiles().fingerprint(request.engine_profile, request.qe_profile)
        except UnknownProfile as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            inp = TranslateBookInput(course_id=course_id, source_artifact_ref=source.model_dump(mode="json"),
                                     source_language=request.source_language,
                                     target_language=request.target_language,
                                     engine_profile=request.engine_profile, qe_profile=request.qe_profile,
                                     max_unit_chars=request.max_unit_chars, batch_size=request.batch_size,
                                     profile_fingerprint=fingerprint,
                                     require_approved_termbase=request.require_approved_termbase,
                                     termbase_min_frequency=request.termbase_min_frequency)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid translation request") from exc
        run_id = translation_run_id(inp)
        workflow_id = translate_book_workflow_id(run_id)
        await db(result_store().register_run, run_id, course_id=course_id, workflow_id=workflow_id,
                 input_json=inp.model_dump(mode="json"))
        existing = await start(TranslateBookWorkflow.run, inp, workflow_id)
        return {"run_id": run_id, "workflow_id": workflow_id, "existing": existing,
                "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile,
                "profile_fingerprint": fingerprint}

    @router.get("/v2/book-translations/{run_id}")
    async def book_translation_status(run_id: str) -> dict[str, Any]:
        run = await load_run(run_id)
        counts = {d: 0 for d in DECISIONS}
        counts.update(await db(result_store().counts, run_id))
        decided = sum(counts.values())
        counts["pending"] = None if run.unit_count is None else max(0, run.unit_count - decided)
        status = termbase = None
        try:
            client = await ctx.get_temporal_client()
            handle = client.get_workflow_handle(run.workflow_id)
            status = (await handle.describe()).status.name
            if status == "RUNNING":
                termbase = await handle.query(TERMBASE_QUERY)
        except Exception:
            log.warning("could not describe %s", run.workflow_id)
        return {"run_id": run.run_id, "course_id": run.course_id, "workflow_id": run.workflow_id,
                "workflow_status": status, "book_id": run.book_id, "input": run.input_json,
                "unit_count": run.unit_count, "counts": counts, "units_ref": run.units_ref,
                "assembly": run.assembly, "termbase": termbase, "updated_at": run.updated_at.isoformat()}

    @router.get("/v2/book-translations/{run_id}/review-queue")
    async def book_translation_review_queue(
            run_id: str, decision: list[Literal["needs_review", "rejected"]] = Query(default=list(QUEUE_DECISIONS)),
            limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
        run = await load_run(run_id)
        units = {u.unit_id: u for u in await run_units(run)}
        rows = await db(result_store().list_by_decision, run_id, decision, limit=limit, offset=offset)
        total = await db(result_store().count_by_decision, run_id, decision)
        items = []
        for row in rows:
            unit = units.get(row.unit_id)
            if unit is None:
                continue
            items.append({**_result_json(row), "kind": unit.kind, "block_ids": unit.block_ids, "pages": unit.pages,
                          "section_path": unit.section_path, "source_text": unit.source_text,
                          "footnote_markers": unit.footnote_markers,
                          "latest_draft": row.drafts[-1] if row.drafts else None, "drafts": row.drafts})
        return {"run_id": run_id, "decisions": list(decision), "total": total, "limit": limit, "offset": offset,
                "items": items}

    @router.post("/v2/book-translations/{run_id}/units/{unit_id}/review", status_code=201)
    async def review_book_translation_unit(run_id: str, unit_id: str, request: HumanReviewRequest) -> dict[str, Any]:
        require_run_id(run_id)
        if UNIT_ID.fullmatch(unit_id) is None:
            raise HTTPException(status_code=404, detail="translation unit not found")
        run = await load_run(run_id)
        unit = next((u for u in await run_units(run) if u.unit_id == unit_id), None)
        if unit is None:
            raise HTTPException(status_code=404, detail="translation unit not found")
        try:
            stored = await db(apply_human_review, store=result_store(), memory=memory(), run_id=run_id, unit=unit,
                              action=request.action, text=request.text, reviewer_id=request.reviewer_id,
                              source_language=run.input_json["source_language"],
                              target_language=run.input_json["target_language"])
        except ReviewRejected as exc:
            raise HTTPException(status_code=422, detail={
                "message": "the text fails the leakage/footnote checks",
                "issues": [i.model_dump(mode="json") for i in exc.issues]}) from exc
        except ReviewConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**_result_json(stored), "drafts": stored.drafts}

    @router.post("/v2/book-translations/{run_id}/assemblies", status_code=202)
    async def reassemble_book_translation(run_id: str) -> dict[str, Any]:
        run = await load_run(run_id)
        if not run.units_ref:
            raise HTTPException(status_code=409, detail="translation units are not prepared yet")
        revision = await db(result_store().revision, run_id)
        workflow_id = assembly_workflow_id(run_id, revision)
        inp = AssembleTranslationInput(run_id=run_id, course_id=run.course_id, units_ref=run.units_ref)
        existing = await start(AssembleBookTranslationWorkflow.run, inp, workflow_id)
        return {"run_id": run_id, "workflow_id": workflow_id, "results_revision": revision, "existing": existing}

    return router
