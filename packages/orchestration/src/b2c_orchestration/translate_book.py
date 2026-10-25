"""Rebuilt book translation workflow (D-028 part D; docs/proposals/2026-10-24-translation-rebuild.md).

A new workflow type with new activity names, registered beside the old
``TranslateContentWorkflow`` (whose paused executions must keep replaying unchanged).

    prepare_translation_units   ParsedBook -> TranslationUnits artifact (stage "translation-units")
    build_book_termbase         candidates + proposals -> termbase artifact (stage "book-termbase") and
                                review state in Postgres (``termbase_store``); human decisions survive
    check_book_termbase         approval gate; when open, freezes the APPROVED entries as an artifact
                                (stage "book-termbase-approved") whose digest every batch carries
    (signal termbase_approved)  with ``require_approved_termbase`` the workflow waits here until no
                                pending entry at or above ``termbase_min_frequency`` is left
    translate_unit_batch  x N   one bounded batch of units; each unit result is upserted into
                                Postgres as soon as it is decided (a crash loses at most one unit)
    assemble_translation        accepted/dropped text per block -> artifact (stage "translate-book")
    build_translator_notes      every unit not accepted -> a translator note in place (stage
                                "translator-notes", D-040); gate ``translator-notes.coverage`` proves
                                each unit is accepted, dropped or annotated

Durable state is the ``translation_unit_results`` table (``translation_store``), not JSON under
``_system/`` (audit 08 §2.2). Artifacts stay immutable: an assembly is keyed by the digest of
the results it was built from, so a later human edit produces a new assembly artifact.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import (ArtifactRef, ParsedBook, ProducerIdentity, QAIssue, TranslationUnit,
                               TranslatorNotesDocument, UnitTranslation, stage_key)
    from b2c_llm_gateway import InvalidLLMOutput, LLMOutputTruncated, LLMRequestRejected
    from b2c_translation.book_pipeline import Draft, PipelineConfig, UnitResult, UnitTranslator, assemble_blocks
    from b2c_translation.glossary import ContextualGlossary
    from b2c_translation.notes import NOTES_BUILDER_VERSION, build_translator_notes
    from b2c_translation.qa.estimators import NotConfigured
    from b2c_translation.termbase import (BookTermbase, GlossarySource, MemoryTermSource, PriorTermbaseSource,
                                          TermbaseError, TransliterationSource)
    from b2c_translation.termbase import build_book_termbase as build_termbase_from_units
    from b2c_translation.units import build_units_with_provenance

    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .promotion import PromotionService
    from .retry import DEFAULT_RETRY
    from .termbase_review import approved_digest, approved_snapshot, gate_counts
    from .termbase_store import TermbaseScope, TermbaseStore
    from .translation_profiles import PROFILE_ID_PATTERN, UnknownProfile
    from .translation_store import DECISIONS, TranslationResultStore

PARSED_STAGE = "ingest-parse-pdf"
UNITS_STAGE = "translation-units"
ASSEMBLY_STAGE = "translate-book"
NOTES_STAGE = "translator-notes"
NOTES_PATCH = "translator-notes-v1"
TERMBASE_STAGE = "book-termbase"
TERMBASE_APPROVED_STAGE = "book-termbase-approved"
TERMBASE_STAGE_VERSION = "book-termbase-2026-10-24.1"
TERMBASE_PATCH = "book-termbase-gate-v1"
TERMBASE_SIGNAL = "termbase_approved"
TERMBASE_QUERY = "termbase_state"
DIGEST_PATTERN = r"^(|[0-9a-f]{64})$"
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
                        "QualityEstimatorNotConfigured", "CloudProviderNotAllowed",
                        "TermbaseChanged", "TermbaseNotConfigured", "InvalidTermbase"})
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
    # Termbase gate (D-028 principle 5). Not part of the run id: each result is stored with the
    # digest of the approved termbase it was produced with, and only reused under that digest.
    require_approved_termbase: bool = True
    termbase_min_frequency: int = Field(default=1, ge=1, le=1000)
    contextual_glossary: ContextualGlossary | None = None

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
    # frozen APPROVED termbase (stage "book-termbase-approved") and its digest; None/"" = no terms
    termbase_ref: dict[str, Any] | None = None
    termbase_digest: str = Field(default="", pattern=DIGEST_PATTERN)


class BuildTermbaseInput(BaseModel):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    course_id: str = Field(pattern=COURSE_ID_PATTERN)
    units_ref: dict[str, Any]
    engine_profile: str = Field(pattern=PROFILE_ID_PATTERN)
    source_language: Literal["en", "ar"]
    target_language: Literal["en", "ar"]
    contextual_glossary: ContextualGlossary | None = None


class TermbaseBuild(BaseModel):
    book_id: str
    artifact: dict[str, Any]
    termbase_sha256: str
    entry_count: int
    extracted: int
    absent: int
    proposer: bool


class CheckTermbaseInput(BaseModel):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    course_id: str = Field(pattern=COURSE_ID_PATTERN)
    book_id: str = Field(min_length=1, max_length=120)
    source_language: Literal["en", "ar"]
    target_language: Literal["en", "ar"]
    min_frequency: int = Field(default=1, ge=1, le=1000)
    require_approved: bool = True


class TermbaseGate(BaseModel):
    open: bool
    counts: dict[str, int]
    snapshot: dict[str, Any] | None = None
    digest: str = ""
    locked_terms: int = 0
    entities: int = 0


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
    # Registry status of the assembly (R1): accepted only when every unit has a publishable
    # decision. None when the worker has no registry (results of older histories also lack it).
    status: str | None = None


class TranslatorNotesResult(BaseModel):
    artifact: dict[str, Any]
    counts: dict[str, int]  # the coverage gate metrics (recomputed from the stored results)
    results_digest: str
    cache_hit: bool
    status: str | None = None  # registry status; None when the worker has no registry


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
def translation_results_digest(translations: Mapping[str, UnitTranslation]) -> str:
    """Digest of the unit results an assembly was built from (consumers re-check it, R7)."""
    return hashlib.sha256(json.dumps(
        [[uid, translations[uid].model_dump(mode="json")] for uid in sorted(translations)],
        sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


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
                 units_cache: UnitsCache | None = None, termbase_store: TermbaseStore | None = None,
                 concept_sources: Callable[[str], list[Any]] | None = None,
                 termbase_builder: Callable[..., BookTermbase] = build_termbase_from_units,
                 promotion: PromotionService | None = None) -> None:
        self.artifact_store = artifact_store
        self.result_store = result_store
        self.runtime = runtime
        self.memory = memory
        self.clock = clock
        self.units_cache = units_cache or UnitsCache()
        self.termbase_store = termbase_store
        self.concept_sources = concept_sources  # book_id -> extra established sources (Wikidata/AGROVOC)
        self.termbase_builder = termbase_builder
        self.promotion = promotion
        self._approved: OrderedDict[str, BookTermbase] = OrderedDict()
        self._approved_lock = threading.Lock()

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

    # .............................................................. termbase
    def _termbase_store(self) -> TermbaseStore:
        if self.termbase_store is None:
            raise _fail("no termbase store is configured on this worker", "TermbaseNotConfigured")
        return self.termbase_store

    def _term_sources(self, inp: BuildTermbaseInput, book_id: str) -> tuple[list[Any], bool]:
        """Proposal sources: established, memory (prior approvals, human TM), model (if the
        profile enables it), rules. ``build_book_termbase`` orders proposals by provenance."""
        sources: list[Any] = []
        if inp.contextual_glossary is not None:
            sources.append(GlossarySource(inp.contextual_glossary))
        if self.concept_sources is not None:
            sources += self.concept_sources(book_id)
        prior: dict[str, list[Any]] = {}
        for entry in self._termbase_store().approved_for_book(book_id, inp.source_language, inp.target_language):
            prior.setdefault(entry.course_id, []).append(entry.record())
        if prior:
            sources.append(PriorTermbaseSource([
                BookTermbase(book_id=book_id, source_language=inp.source_language,
                             target_language=inp.target_language, entries=records)
                for _, records in sorted(prior.items())]))
        if self.memory is not None and hasattr(self.memory, "lookup_entry"):
            sources.append(MemoryTermSource(self.memory, book_id=book_id, source_language=inp.source_language,
                                            target_language=inp.target_language))
        try:
            proposer = self.runtime.term_proposer(inp.engine_profile)
        except UnknownProfile as exc:
            raise _fail(str(exc), "UnknownTranslationProfile") from exc
        if proposer is not None:
            sources.append(proposer)
        sources.append(TransliterationSource())
        return sources, proposer is not None

    @activity.defn(name="build_book_termbase")
    def build_termbase(self, inp: BuildTermbaseInput) -> TermbaseBuild:
        """Extract candidates from the stored units, collect proposals, merge them into the review
        state (human decisions are kept) and store the merged termbase as a versioned artifact.
        Nothing is approved here."""
        ref = _scoped_ref(inp.units_ref, bucket=self.artifact_store.bucket, course_id=inp.course_id,
                          stage=UNITS_STAGE)
        store = self._termbase_store()
        units = self.units_cache.get(self.artifact_store, ref)
        book_ids = {u.book_id for u in units}
        if len(book_ids) != 1:
            raise _fail("the units artifact must hold exactly one book", "InvalidTranslationSource")
        book_id = book_ids.pop()
        scope = TermbaseScope(inp.course_id, book_id, inp.source_language, inp.target_language)
        with activity_heartbeats("build_book_termbase"):
            sources, proposer = self._term_sources(inp, book_id)
            # termhood suggestions (D-039) only when the engine profile enables them
            classify = getattr(self.runtime, "termhood_classifier", None)
            try:
                classifier = classify(inp.engine_profile) if classify is not None else None
            except UnknownProfile as exc:
                raise _fail(str(exc), "UnknownTranslationProfile") from exc
            extra = {"classifier": classifier} if classifier is not None else {}
            extracted = self.termbase_builder(units, sources=sources, source_language=inp.source_language,
                                              target_language=inp.target_language, **extra)
            outcome = store.merge_extraction(scope, extracted)
            merged = store.load(scope, extractor_version=extracted.extractor_version)
            sha = merged.sha256()
            document = merged.to_json().encode("utf-8")
            key = stage_key(TERMBASE_STAGE, TERMBASE_STAGE_VERSION, [ref.sha256, sha],
                            {"book_id": book_id, "source_language": inp.source_language,
                             "target_language": inp.target_language})
            out_ref, _ = self.artifact_store.get_or_compute(inp.course_id, TERMBASE_STAGE, key,
                                                            lambda: {"termbase.json": document})
            ref_json = out_ref.model_dump(mode="json")
            store.set_build(scope, artifact_ref=ref_json, termbase_sha256=sha, entry_count=len(merged.entries))
        return TermbaseBuild(book_id=book_id, artifact=ref_json, termbase_sha256=sha,
                             entry_count=len(merged.entries), extracted=outcome.extracted, absent=outcome.absent,
                             proposer=proposer)

    @activity.defn(name="check_book_termbase")
    def check_termbase(self, inp: CheckTermbaseInput) -> TermbaseGate:
        """Gate state; when open, freeze the approved entries (never pending ones) as an artifact."""
        store = self._termbase_store()
        scope = TermbaseScope(inp.course_id, inp.book_id, inp.source_language, inp.target_language)
        counts = gate_counts(store.entries(scope), inp.min_frequency)
        if inp.require_approved and counts.pending_blocking:
            return TermbaseGate(open=False, counts=counts.as_json())
        snapshot = approved_snapshot(store.load(scope))
        digest = approved_digest(snapshot)
        document = snapshot.to_json().encode("utf-8")
        key = stage_key(TERMBASE_APPROVED_STAGE, TERMBASE_STAGE_VERSION, [digest],
                        {"book_id": inp.book_id, "source_language": inp.source_language,
                         "target_language": inp.target_language})
        ref, _ = self.artifact_store.get_or_compute(inp.course_id, TERMBASE_APPROVED_STAGE, key,
                                                    lambda: {"termbase.json": document})
        return TermbaseGate(open=True, counts=counts.as_json(), snapshot=ref.model_dump(mode="json"), digest=digest,
                            locked_terms=len(snapshot.locked_terms()), entities=len(snapshot.entity_table()))

    def _approved_termbase(self, inp: TranslateUnitBatchInput, book_id: str) -> BookTermbase | None:
        if inp.termbase_ref is None:
            if inp.termbase_digest:
                raise _fail("a termbase digest without a termbase reference", "InvalidTermbase")
            return None
        ref = _scoped_ref(inp.termbase_ref, bucket=self.artifact_store.bucket, course_id=inp.course_id,
                          stage=TERMBASE_APPROVED_STAGE)
        with self._approved_lock:
            cached = self._approved.get(ref.key)
        if cached is None:
            try:
                cached = approved_snapshot(BookTermbase.from_json(self.artifact_store.read(ref, "termbase.json")))
            except TermbaseError as exc:
                raise _fail(f"approved termbase artifact is invalid: {exc}", "InvalidTermbase") from exc
            with self._approved_lock:
                self._approved[ref.key] = cached
                while len(self._approved) > 8:
                    self._approved.popitem(last=False)
        if cached.book_id != book_id:
            raise _fail("the approved termbase belongs to another book", "InvalidTermbase")
        if approved_digest(cached) != inp.termbase_digest:
            raise _fail("the approved termbase does not match this run's digest", "TermbaseChanged")
        return cached

    # .............................................................. translate
    def _translator(self, inp: TranslateUnitBatchInput, termbase: BookTermbase | None = None) -> UnitTranslator:
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
        # Only APPROVED entries: locked terms -> terms, approved names -> entities.
        terms = termbase.locked_terms() if termbase is not None else None
        entities = termbase.entity_table() if termbase is not None else None
        forbidden = termbase.forbidden_variants() if termbase is not None else None
        hints = termbase.hint_terms() if termbase is not None else None  # D-039: preferred, minor check
        variants = termbase.entity_variants() if termbase is not None else None  # D-047: check only
        return UnitTranslator(engine, estimator=estimator, memory=self.memory, clock=self.clock,
                              terms=terms, entities=entities, forbidden=forbidden, hints=hints,
                              entity_variants=variants, config=PipelineConfig(source_language=inp.source_language,
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
        termbase = self._approved_termbase(inp, units[index[inp.unit_ids[0]]].book_id)
        translator = self._translator(inp, termbase)
        limit = translator.config.context_chars
        done = self.result_store.final_unit_ids(inp.run_id, inp.unit_ids, termbase_digest=inp.termbase_digest)
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
                                         drafts=[draft_record(d) for d in result.drafts],
                                         termbase_digest=inp.termbase_digest)
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
            digest = translation_results_digest(translations)
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
            status = None
            if self.promotion is not None:
                identities = [ProducerIdentity(role="engine", **json.loads(e)) for e in engines]
                status = self.promotion.gate_translation_assembly(
                    inp.course_id, out_ref, units_ref=ref, counts=counts, total_units=len(units),
                    identities=identities, config={"run_id": inp.run_id, "assembly_version": ASSEMBLY_VERSION,
                                                   "results_digest": digest}).status
            result = AssemblyResult(artifact=out_ref.model_dump(mode="json"), counts=counts,
                                    total_blocks=len(blocks), complete_blocks=complete, results_digest=digest,
                                    cache_hit=cache_hit, status=status)
            snapshot = max((r.updated_at for r in stored), default=datetime(1970, 1, 1, tzinfo=timezone.utc))
            self.result_store.set_assembly(inp.run_id, result.model_dump(mode="json"), snapshot_at=snapshot)
        return result


    # .............................................................. translator notes (D-040)
    @activity.defn(name="build_translator_notes")
    def translator_notes(self, inp: AssembleTranslationInput) -> TranslatorNotesResult:
        """Every unit that is not accepted/dropped becomes a translator note in place. The coverage
        gate is recomputed from the stored decisions, so a unit cannot be lost silently."""
        from .promotion import evaluate_translator_notes

        ref = _scoped_ref(inp.units_ref, bucket=self.artifact_store.bucket, course_id=inp.course_id,
                          stage=UNITS_STAGE)
        with activity_heartbeats("build_translator_notes"):
            units = self.units_cache.get(self.artifact_store, ref)
            known = {u.unit_id for u in units}
            stored = [r for r in self.result_store.fetch_run(inp.run_id) if r.unit_id in known]
            translations = {r.unit_id: r.translation for r in stored}
            drafts = {r.unit_id: r.drafts for r in stored}
            digest = translation_results_digest(translations)
            key = stage_key(NOTES_STAGE, NOTES_BUILDER_VERSION, [ref.sha256, digest], {"run_id": inp.run_id})

            def compute() -> dict[str, bytes]:
                doc = build_translator_notes(units, translations, drafts, run_id=inp.run_id, units_ref=ref,
                                             results_digest=digest)
                return {"translator-notes.json": doc.model_dump_json(indent=1).encode("utf-8")}

            out_ref, cache_hit = self.artifact_store.get_or_compute(inp.course_id, NOTES_STAGE, key, compute)
            noted = [n.unit_id for n in TranslatorNotesDocument.model_validate_json(
                self.artifact_store.read(out_ref, "translator-notes.json")).notes]
            decisions = {u.unit_id: (translations[u.unit_id].decision if u.unit_id in translations else None)
                         for u in units}
            _, metrics = evaluate_translator_notes(decisions, noted)
            status = None
            if self.promotion is not None:
                status = self.promotion.gate_translator_notes(
                    inp.course_id, out_ref, units_ref=ref, decisions=decisions, noted=noted,
                    config={"run_id": inp.run_id, "builder_version": NOTES_BUILDER_VERSION,
                            "results_digest": digest}).status
        return TranslatorNotesResult(artifact=out_ref.model_dump(mode="json"), counts=metrics,
                                     results_digest=digest, cache_hit=cache_hit, status=status)


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

NOTES_OPTIONS: dict[str, Any] = ASSEMBLE_OPTIONS


# Proposing an Arabic equivalent is one model call per candidate (≈1100 for a 400-page book at
# ≈6 s each on CPU, shared with other runs): a 1 h attempt failed the Gramsci run on 2026-10-25.
# Retries reuse the gateway's cached proposals, so a retry continues rather than restarts.
TERMBASE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(hours=8),
                                        schedule_to_close_timeout=timedelta(hours=24),
                                        heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(3))
CHECK_TERMBASE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(minutes=10),
                                              schedule_to_close_timeout=timedelta(minutes=30),
                                              retry_policy=_retry(3))


class TermbaseApproval(BaseModel):
    """Payload of the ``termbase_approved`` signal: the reviewer who finished the review."""
    reviewer_id: str = Field(pattern=r"^[A-Za-z0-9._@-]{1,120}$")


async def _translator_notes(inp: AssembleTranslationInput) -> TranslatorNotesResult | None:
    """The notes step (D-040). Patched: histories recorded before it replay without it."""
    if not workflow.patched(NOTES_PATCH):
        return None
    return await workflow.execute_activity("build_translator_notes", inp, result_type=TranslatorNotesResult,
                                           **NOTES_OPTIONS)


@workflow.defn(name="TranslateBookWorkflow")
class TranslateBookWorkflow:
    def __init__(self) -> None:
        self._approvals: list[str] = []
        self._termbase: dict[str, Any] = {"phase": "not_started"}

    @workflow.signal(name=TERMBASE_SIGNAL)
    def termbase_approved(self, approval: TermbaseApproval) -> None:
        self._approvals.append(approval.reviewer_id)

    @workflow.query(name=TERMBASE_QUERY)
    def termbase_state(self) -> dict[str, Any]:
        return {**self._termbase, "signals": len(self._approvals),
                "last_reviewer": self._approvals[-1] if self._approvals else None}

    async def _termbase_gate(self, run_id: str, inp: TranslateBookInput, prepared: PreparedUnits) -> TermbaseGate:
        """Build the termbase, then (if required) wait for the reviewer until nothing at or
        above the frequency threshold is pending. Returns the open gate with the frozen
        approved termbase; pending entries are never used and never auto-approved."""
        self._termbase = {"phase": "building", "require_approved": inp.require_approved_termbase,
                          "min_frequency": inp.termbase_min_frequency}
        built: TermbaseBuild = await workflow.execute_activity(
            "build_book_termbase",
            BuildTermbaseInput(run_id=run_id, course_id=inp.course_id, units_ref=prepared.units_ref,
                               engine_profile=inp.engine_profile, source_language=inp.source_language,
                               target_language=inp.target_language, contextual_glossary=inp.contextual_glossary),
            result_type=TermbaseBuild, **TERMBASE_OPTIONS)
        self._termbase.update(termbase_ref=built.artifact, entry_count=built.entry_count)
        check = CheckTermbaseInput(run_id=run_id, course_id=inp.course_id, book_id=built.book_id,
                                   source_language=inp.source_language, target_language=inp.target_language,
                                   min_frequency=inp.termbase_min_frequency,
                                   require_approved=inp.require_approved_termbase)
        while True:
            seen = len(self._approvals)
            gate: TermbaseGate = await workflow.execute_activity(
                "check_book_termbase", check, result_type=TermbaseGate, **CHECK_TERMBASE_OPTIONS)
            self._termbase.update(counts=gate.counts, checks=self._termbase.get("checks", 0) + 1)
            if gate.open:
                self._termbase.update(phase="approved", approved_ref=gate.snapshot, digest=gate.digest,
                                      locked_terms=gate.locked_terms, entities=gate.entities)
                return gate
            self._termbase["phase"] = "awaiting_approval"
            await workflow.wait_condition(lambda: len(self._approvals) > seen)

    @workflow.run
    async def run(self, inp: TranslateBookInput) -> dict[str, Any]:
        run_id = translation_run_id(inp)
        prepared: PreparedUnits = await workflow.execute_activity(
            "prepare_translation_units", PrepareUnitsInput(run_id=run_id, inp=inp), result_type=PreparedUnits,
            **PREPARE_OPTIONS)
        gate: TermbaseGate | None = None
        if workflow.patched(TERMBASE_PATCH):
            gate = await self._termbase_gate(run_id, inp, prepared)
        termbase_ref = gate.snapshot if gate is not None else None
        termbase_digest = gate.digest if gate is not None else ""
        ids = prepared.unit_ids
        totals: Counter[str] = Counter()
        for start in range(0, len(ids), inp.batch_size):
            outcome: BatchOutcome = await workflow.execute_activity(
                "translate_unit_batch",
                TranslateUnitBatchInput(run_id=run_id, course_id=inp.course_id, units_ref=prepared.units_ref,
                                        unit_ids=ids[start:start + inp.batch_size],
                                        engine_profile=inp.engine_profile, qe_profile=inp.qe_profile,
                                        profile_fingerprint=inp.profile_fingerprint,
                                        source_language=inp.source_language, target_language=inp.target_language,
                                        termbase_ref=termbase_ref, termbase_digest=termbase_digest),
                result_type=BatchOutcome, **BATCH_OPTIONS)
            totals.update({"translated": outcome.translated, "skipped": outcome.skipped})
        assembled: AssemblyResult = await workflow.execute_activity(
            "assemble_translation",
            AssembleTranslationInput(run_id=run_id, course_id=inp.course_id, units_ref=prepared.units_ref),
            result_type=AssemblyResult, **ASSEMBLE_OPTIONS)
        notes = await _translator_notes(AssembleTranslationInput(run_id=run_id, course_id=inp.course_id,
                                                                 units_ref=prepared.units_ref))
        return {"run_id": run_id, "units_ref": prepared.units_ref, "unit_count": len(ids),
                "translated": totals["translated"], "skipped": totals["skipped"],
                "assembly": assembled.model_dump(mode="json"),
                "termbase": None if gate is None else {"digest": gate.digest, "approved_ref": gate.snapshot,
                                                       "locked_terms": gate.locked_terms,
                                                       "entities": gate.entities},
                **({} if notes is None else {"translator_notes": notes.model_dump(mode="json")})}


@workflow.defn(name="AssembleBookTranslationWorkflow")
class AssembleBookTranslationWorkflow:
    """Re-assembly after human review: same activity, no translation."""

    @workflow.run
    async def run(self, inp: AssembleTranslationInput) -> dict[str, Any]:
        assembled: AssemblyResult = await workflow.execute_activity(
            "assemble_translation", inp, result_type=AssemblyResult, **ASSEMBLE_OPTIONS)
        notes = await _translator_notes(inp)
        return {"run_id": inp.run_id, "assembly": assembled.model_dump(mode="json"),
                **({} if notes is None else {"translator_notes": notes.model_dump(mode="json")})}


__all__ = ["ASSEMBLY_STAGE", "NOTES_PATCH", "NOTES_STAGE", "TranslatorNotesResult", "AssembleBookTranslationWorkflow", "AssembleTranslationInput", "AssemblyResult",
           "BatchOutcome", "BuildTermbaseInput", "CheckTermbaseInput", "PrepareUnitsInput", "PreparedUnits", "TranslateBookActivities", "TranslateBookInput",
           "TERMBASE_APPROVED_STAGE", "TERMBASE_PATCH", "TERMBASE_QUERY", "TERMBASE_SIGNAL", "TERMBASE_STAGE",
           "TermbaseApproval", "TermbaseBuild", "TermbaseGate",
           "TranslateBookWorkflow", "TranslateUnitBatchInput", "translation_results_digest", "UNITS_BUILDER_VERSION", "UNITS_STAGE", "UnitsCache",
           "assembly_workflow_id", "draft_record", "translate_book_workflow_id", "translation_run_id",
           "units_stage_key"]
