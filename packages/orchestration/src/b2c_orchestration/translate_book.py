"""Rebuilt book translation workflow (D-028 part D; docs/proposals/2026-10-24-translation-rebuild.md).

A new workflow type with new activity names, registered beside the old
``TranslateContentWorkflow`` (whose paused executions must keep replaying unchanged).

    prepare_translation_units   ParsedBook -> TranslationUnits artifact (stage "translation-units")
    translate_unit_batch  x N   one bounded batch of units; each unit result is upserted into
                                Postgres as soon as it is decided (a crash loses at most one unit)
    assemble_translation        accepted/dropped text per block -> artifact (stage "translate-book")

Durable state is the ``translation_unit_results`` table (``translation_store``), not JSON under
``_system/`` (audit 08 §2.2). Artifacts stay immutable: an assembly is keyed by the digest of
the results it was built from, so a later human edit produces a new assembly artifact.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import ArtifactRef, ParsedBook, QAIssue, TranslationUnit, UnitTranslation, stage_key
    from b2c_llm_gateway import InvalidLLMOutput, LLMOutputTruncated, LLMRequestRejected
    from b2c_translation.book_pipeline import Draft, PipelineConfig, UnitResult, UnitTranslator, assemble_blocks
    from b2c_translation.qa.estimators import NotConfigured
    from b2c_translation.units import build_units_with_provenance

    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .retry import DEFAULT_RETRY
    from .translation_profiles import PROFILE_ID_PATTERN, UnknownProfile
    from .translation_store import DECISIONS, TranslationResultStore

PARSED_STAGE = "ingest-parse-pdf"
UNITS_STAGE = "translation-units"
ASSEMBLY_STAGE = "translate-book"
# Bump when units.py changes its output for the same ParsedBook: it is part of the units
# artifact key and of the run id, so new units never mix with results of old ones.
UNITS_BUILDER_VERSION = "units-2026-10-24.e758aff"
ASSEMBLY_VERSION = "assemble-2026-10-24.1"
WORKFLOW_PREFIX = "b2c-translate-book-"
ASSEMBLY_WORKFLOW_PREFIX = "b2c-translate-book-assembly-"
COURSE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"
RUN_ID_PATTERN = r"^[0-9a-f]{64}$"

NON_RETRYABLE = sorted({*DEFAULT_RETRY.non_retryable_error_types, "InvalidTranslationSource",
                        "UnknownTranslationUnit", "UnknownTranslationProfile", "TranslationProfileChanged",
                        "QualityEstimatorNotConfigured", "CloudProviderNotAllowed"})
# Per-unit failures that a retry cannot change (temperature 0, cached gateway): the unit is
# recorded as rejected and goes to human review instead of failing the batch.
DETERMINISTIC_UNIT_ERRORS: tuple[type[BaseException], ...] = (
    ValueError, LLMOutputTruncated, LLMRequestRejected, InvalidLLMOutput)


# ------------------------------------------------------------------ inputs / outputs
class TranslateBookInput(BaseModel):
    course_id: str = Field(pattern=COURSE_ID_PATTERN)
    source_artifact_ref: dict[str, Any]
    source_language: Literal["en", "ar"]
    target_language: Literal["en", "ar"]
    engine_profile: str = Field(pattern=PROFILE_ID_PATTERN)
    qe_profile: str | None = Field(default=None, pattern=PROFILE_ID_PATTERN)
    max_unit_chars: int = Field(default=400, ge=40, le=4000)
    batch_size: int = Field(default=25, ge=1, le=200)
    # sha256 of the resolved engine/QE profiles (translation_profiles.fingerprint); "" = unchecked
    profile_fingerprint: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")

    @model_validator(mode="after")
    def _languages_differ(self) -> "TranslateBookInput":
        if self.source_language == self.target_language:
            raise ValueError("source and target language must differ")
        return self


class PrepareUnitsInput(BaseModel):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    inp: TranslateBookInput


class PreparedUnits(BaseModel):
    units_ref: dict[str, Any]
    unit_ids: list[str]
    book_id: str
    cache_hit: bool


class TranslateUnitBatchInput(BaseModel):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    course_id: str = Field(pattern=COURSE_ID_PATTERN)
    units_ref: dict[str, Any]
    unit_ids: list[str] = Field(min_length=1, max_length=200)
    engine_profile: str = Field(pattern=PROFILE_ID_PATTERN)
    qe_profile: str | None = Field(default=None, pattern=PROFILE_ID_PATTERN)
    profile_fingerprint: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")
    source_language: Literal["en", "ar"]
    target_language: Literal["en", "ar"]


class BatchOutcome(BaseModel):
    translated: int
    skipped: int
    decisions: dict[str, int]


class AssembleTranslationInput(BaseModel):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    course_id: str = Field(pattern=COURSE_ID_PATTERN)
    units_ref: dict[str, Any]


class AssemblyResult(BaseModel):
    artifact: dict[str, Any]
    counts: dict[str, int]
    total_blocks: int
    complete_blocks: int
    results_digest: str
    cache_hit: bool


# ------------------------------------------------------------------ identities
def translation_run_id(inp: TranslateBookInput) -> str:
    """Deterministic run id: same source, languages, profiles and unit config -> same run.

    ``batch_size`` is excluded: batching never changes a unit result."""
    source = inp.source_artifact_ref
    payload = {"course_id": inp.course_id, "source_sha256": source.get("sha256"), "source_uri": source.get("uri"),
               "source_language": inp.source_language, "target_language": inp.target_language,
               "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile,
               "profile_fingerprint": inp.profile_fingerprint, "max_unit_chars": inp.max_unit_chars,
               "units_builder_version": UNITS_BUILDER_VERSION}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def translate_book_workflow_id(run_id: str) -> str:
    return WORKFLOW_PREFIX + run_id


def assembly_workflow_id(run_id: str, results_revision: str) -> str:
    return ASSEMBLY_WORKFLOW_PREFIX + hashlib.sha256(f"{run_id}\0{results_revision}".encode()).hexdigest()


def units_stage_key(source_sha256: str, max_unit_chars: int) -> str:
    return stage_key(UNITS_STAGE, UNITS_BUILDER_VERSION, [source_sha256], {"max_unit_chars": max_unit_chars})


# ------------------------------------------------------------------ helpers
def _fail(message: str, error_type: str) -> ApplicationError:
    return ApplicationError(message, type=error_type, non_retryable=True)


def _scoped_ref(raw: dict[str, Any], *, bucket: str, course_id: str, stage: str) -> ArtifactRef:
    try:
        ref = ArtifactRef.model_validate(raw)
    except ValueError as exc:
        raise _fail("artifact reference is malformed", "InvalidTranslationSource") from exc
    if ref.stage != stage or not ref.uri.startswith(f"s3://{bucket}/{course_id}/{stage}/"):
        raise _fail(f"artifact is not a {stage} artifact of course {course_id}", "InvalidTranslationSource")
    return ref


class UnitsCache:
    """Small in-process cache of parsed units artifacts (immutable, keyed by artifact key)."""

    def __init__(self, size: int = 4) -> None:
        self._items: OrderedDict[str, list[TranslationUnit]] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def get(self, artifact_store: Any, ref: ArtifactRef) -> list[TranslationUnit]:
        with self._lock:
            if ref.key in self._items:
                self._items.move_to_end(ref.key)
                return self._items[ref.key]
        units = [TranslationUnit.model_validate(u) for u in json.loads(artifact_store.read(ref, "units.json"))]
        with self._lock:
            self._items[ref.key] = units
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return units


def draft_record(draft: Draft) -> dict[str, Any]:
    qe = None
    if draft.qe is not None:
        qe = {"passed": draft.qe.passed, "result": draft.qe.result.model_dump(mode="json"),
              "issues": [i.model_dump(mode="json") for i in draft.qe.issues]}
    return {"lever": draft.lever, "text": draft.text, "decision": draft.decision.decision,
            "next_action": draft.decision.next_action, "reasons": list(draft.decision.reasons),
            "issues": [i.model_dump(mode="json") for i in draft.issues], "qe": qe}


def _progress(details: dict[str, Any]) -> None:
    try:
        activity.heartbeat(details)
    except RuntimeError:  # direct invocation outside an activity (tests)
        pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ activities
class TranslateBookActivities:
    def __init__(self, *, artifact_store: Any, result_store: TranslationResultStore, runtime: Any,
                 memory: Any | None = None, clock: Callable[[], datetime] = _now,
                 units_cache: UnitsCache | None = None) -> None:
        self.artifact_store = artifact_store
        self.result_store = result_store
        self.runtime = runtime
        self.memory = memory
        self.clock = clock
        self.units_cache = units_cache or UnitsCache()

    # .............................................................. prepare
    @activity.defn(name="prepare_translation_units")
    def prepare(self, inp: PrepareUnitsInput) -> PreparedUnits:
        book = inp.inp
        source = _scoped_ref(book.source_artifact_ref, bucket=self.artifact_store.bucket,
                             course_id=book.course_id, stage=PARSED_STAGE)
        with activity_heartbeats("prepare_translation_units"):
            parsed = ParsedBook.model_validate_json(self.artifact_store.read(source, "parsed-book.json"))
            key = units_stage_key(source.sha256, book.max_unit_chars)

            def compute() -> dict[str, bytes]:
                build = build_units_with_provenance(parsed, max_unit_chars=book.max_unit_chars)
                units = [u.model_dump(mode="json") for u in build.units]
                meta = {"units_builder_version": UNITS_BUILDER_VERSION, "book_id": parsed.book_id,
                        "source_artifact_ref": source.model_dump(mode="json"),
                        "max_unit_chars": book.max_unit_chars, "unit_count": len(units),
                        "stats": dict(sorted(build.stats.items()))}
                return {"units.json": json.dumps(units, ensure_ascii=False).encode("utf-8"),
                        "build.json": json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")}

            ref, cache_hit = self.artifact_store.get_or_compute(book.course_id, UNITS_STAGE, key, compute)
            units = self.units_cache.get(self.artifact_store, ref)
            self.result_store.register_run(inp.run_id, course_id=book.course_id,
                                           workflow_id=translate_book_workflow_id(inp.run_id),
                                           input_json=book.model_dump(mode="json"))
            ref_json = ref.model_dump(mode="json")
            self.result_store.set_units(inp.run_id, units_ref=ref_json, unit_count=len(units),
                                        book_id=parsed.book_id)
        return PreparedUnits(units_ref=ref_json, unit_ids=[u.unit_id for u in units], book_id=parsed.book_id,
                             cache_hit=cache_hit)

    # .............................................................. translate
    def _translator(self, inp: TranslateUnitBatchInput) -> UnitTranslator:
        try:
            if inp.profile_fingerprint and self.runtime.fingerprint(
                    inp.engine_profile, inp.qe_profile) != inp.profile_fingerprint:
                raise _fail("the engine/QE profile changed since this run started; start a new run",
                            "TranslationProfileChanged")
            engine = self.runtime.engine(inp.engine_profile)
            estimator = self.runtime.estimator(inp.qe_profile, source_language=inp.source_language,
                                               target_language=inp.target_language)
        except UnknownProfile as exc:
            raise _fail(str(exc), "UnknownTranslationProfile") from exc
        except NotConfigured as exc:
            raise _fail(str(exc), "QualityEstimatorNotConfigured") from exc
        return UnitTranslator(engine, estimator=estimator, memory=self.memory, clock=self.clock,
                              config=PipelineConfig(source_language=inp.source_language,
                                                    target_language=inp.target_language))

    @activity.defn(name="translate_unit_batch")
    def translate_batch(self, inp: TranslateUnitBatchInput) -> BatchOutcome:
        ref = _scoped_ref(inp.units_ref, bucket=self.artifact_store.bucket, course_id=inp.course_id,
                          stage=UNITS_STAGE)
        units = self.units_cache.get(self.artifact_store, ref)
        index = {u.unit_id: i for i, u in enumerate(units)}
        unknown = [uid for uid in inp.unit_ids if uid not in index]
        if unknown:
            raise _fail(f"{len(unknown)} unit id(s) are not in the units artifact", "UnknownTranslationUnit")
        translator = self._translator(inp)
        limit = translator.config.context_chars
        done = self.result_store.final_unit_ids(inp.run_id, inp.unit_ids)
        decisions: Counter[str] = Counter()
        translated = skipped = 0
        with activity_heartbeats("translate_unit_batch"):
            for uid in inp.unit_ids:
                if uid in done:
                    skipped += 1
                    _progress({"unit_id": uid, "skipped": True})
                    continue
                i = index[uid]
                unit = units[i]
                before = units[i - 1].source_text[-limit:] if i > 0 else None
                after = units[i + 1].source_text[:limit] if i + 1 < len(units) else None
                try:
                    result = translator.translate(unit, context_before=before, context_after=after)
                except DETERMINISTIC_UNIT_ERRORS as exc:
                    result = self._engine_error(unit, translator, exc)
                self.result_store.upsert(inp.run_id, unit.book_id, result.translation,
                                         drafts=[draft_record(d) for d in result.drafts])
                decisions[result.translation.decision] += 1
                translated += 1
                _progress({"unit_id": uid, "decision": result.translation.decision})
        return BatchOutcome(translated=translated, skipped=skipped, decisions=dict(decisions))

    def _engine_error(self, unit: TranslationUnit, translator: UnitTranslator, exc: BaseException) -> UnitResult:
        issue = QAIssue(check_id="engine_error", category="other", severity="critical", origin="deterministic",
                        evidence={"error_type": type(exc).__name__, "message": str(exc)[:300]})
        return UnitResult(UnitTranslation(unit_id=unit.unit_id, target_text="", origin="engine",
                                          engine=translator.engine.identity, attempts=1, issues=[issue],
                                          decision="rejected", decided_by="policy", decided_at=self.clock()))

    # .............................................................. assemble
    @activity.defn(name="assemble_translation")
    def assemble(self, inp: AssembleTranslationInput) -> AssemblyResult:
        ref = _scoped_ref(inp.units_ref, bucket=self.artifact_store.bucket, course_id=inp.course_id,
                          stage=UNITS_STAGE)
        with activity_heartbeats("assemble_translation"):
            units = self.units_cache.get(self.artifact_store, ref)
            known = {u.unit_id for u in units}
            stored = [r for r in self.result_store.fetch_run(inp.run_id) if r.unit_id in known]
            translations = {r.unit_id: r.translation for r in stored}
            blocks = assemble_blocks(units, translations)
            counts = {d: 0 for d in DECISIONS}
            counts.update(Counter(t.decision for t in translations.values()))
            counts["pending"] = len(units) - len(translations)
            engines = sorted({json.dumps(t.engine.model_dump(mode="json"), sort_keys=True)
                              for t in translations.values() if t.engine is not None})
            digest = hashlib.sha256(json.dumps(
                [[uid, translations[uid].model_dump(mode="json")] for uid in sorted(translations)],
                sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
            document = {
                "run_id": inp.run_id, "units_ref": ref.model_dump(mode="json"), "results_digest": digest,
                "assembly_version": ASSEMBLY_VERSION, "counts": counts,
                "engines": [json.loads(e) for e in engines], "total_units": len(units),
                "blocks": {bid: {"text": b.text, "complete": b.complete, "pending_unit_ids": b.pending_unit_ids,
                                 "merged_into": b.merged_into} for bid, b in blocks.items()},
            }
            key = stage_key(ASSEMBLY_STAGE, ASSEMBLY_VERSION, [ref.sha256, digest], {"run_id": inp.run_id})
            out_ref, cache_hit = self.artifact_store.get_or_compute(
                inp.course_id, ASSEMBLY_STAGE, key,
                lambda: {"translation.json": json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")})
            complete = sum(1 for b in blocks.values() if b.complete and b.merged_into is None)
            result = AssemblyResult(artifact=out_ref.model_dump(mode="json"), counts=counts,
                                    total_blocks=len(blocks), complete_blocks=complete, results_digest=digest,
                                    cache_hit=cache_hit)
            snapshot = max((r.updated_at for r in stored), default=datetime(1970, 1, 1, tzinfo=timezone.utc))
            self.result_store.set_assembly(inp.run_id, result.model_dump(mode="json"), snapshot_at=snapshot)
        return result


# ------------------------------------------------------------------ workflows
def _retry(maximum_attempts: int) -> RetryPolicy:
    return RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0,
                       maximum_interval=timedelta(minutes=5), maximum_attempts=maximum_attempts,
                       non_retryable_error_types=NON_RETRYABLE)


PREPARE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(minutes=30),
                                       schedule_to_close_timeout=timedelta(hours=2),
                                       heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(3))
BATCH_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(hours=3),
                                     schedule_to_close_timeout=timedelta(hours=12),
                                     heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(5))
ASSEMBLE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(minutes=30),
                                        schedule_to_close_timeout=timedelta(hours=2),
                                        heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(3))


@workflow.defn(name="TranslateBookWorkflow")
class TranslateBookWorkflow:
    @workflow.run
    async def run(self, inp: TranslateBookInput) -> dict[str, Any]:
        run_id = translation_run_id(inp)
        prepared: PreparedUnits = await workflow.execute_activity(
            "prepare_translation_units", PrepareUnitsInput(run_id=run_id, inp=inp), result_type=PreparedUnits,
            **PREPARE_OPTIONS)
        ids = prepared.unit_ids
        totals: Counter[str] = Counter()
        for start in range(0, len(ids), inp.batch_size):
            outcome: BatchOutcome = await workflow.execute_activity(
                "translate_unit_batch",
                TranslateUnitBatchInput(run_id=run_id, course_id=inp.course_id, units_ref=prepared.units_ref,
                                        unit_ids=ids[start:start + inp.batch_size],
                                        engine_profile=inp.engine_profile, qe_profile=inp.qe_profile,
                                        profile_fingerprint=inp.profile_fingerprint,
                                        source_language=inp.source_language, target_language=inp.target_language),
                result_type=BatchOutcome, **BATCH_OPTIONS)
            totals.update({"translated": outcome.translated, "skipped": outcome.skipped})
        assembled: AssemblyResult = await workflow.execute_activity(
            "assemble_translation",
            AssembleTranslationInput(run_id=run_id, course_id=inp.course_id, units_ref=prepared.units_ref),
            result_type=AssemblyResult, **ASSEMBLE_OPTIONS)
        return {"run_id": run_id, "units_ref": prepared.units_ref, "unit_count": len(ids),
                "translated": totals["translated"], "skipped": totals["skipped"],
                "assembly": assembled.model_dump(mode="json")}


@workflow.defn(name="AssembleBookTranslationWorkflow")
class AssembleBookTranslationWorkflow:
    """Re-assembly after human review: same activity, no translation."""

    @workflow.run
    async def run(self, inp: AssembleTranslationInput) -> dict[str, Any]:
        assembled: AssemblyResult = await workflow.execute_activity(
            "assemble_translation", inp, result_type=AssemblyResult, **ASSEMBLE_OPTIONS)
        return {"run_id": inp.run_id, "assembly": assembled.model_dump(mode="json")}


__all__ = ["ASSEMBLY_STAGE", "AssembleBookTranslationWorkflow", "AssembleTranslationInput", "AssemblyResult",
           "BatchOutcome", "PrepareUnitsInput", "PreparedUnits", "TranslateBookActivities", "TranslateBookInput",
           "TranslateBookWorkflow", "TranslateUnitBatchInput", "UNITS_BUILDER_VERSION", "UNITS_STAGE", "UnitsCache",
           "assembly_workflow_id", "draft_record", "translate_book_workflow_id", "translation_run_id",
           "units_stage_key"]
