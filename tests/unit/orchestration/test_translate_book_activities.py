"""Activities of the rebuilt book translation workflow (D-028 part D), with fakes.

The artifact store is the real ``ArtifactStore`` over moto S3; Postgres is the in-memory fake
of ``translation_store``; the engine and QE are fakes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from b2c_contracts import ArtifactRef, Block, EngineIdentity, PageRef, ParsedBook, QAIssue, QEResult, stage_key
from b2c_core import ArtifactStore
from b2c_translation.engines import EngineCapabilities, EngineResult
from b2c_translation.qa import QEOutcome
from moto import mock_aws
from temporalio.exceptions import ApplicationError

import b2c_orchestration.translate_book as tb
from b2c_orchestration.translate_book import (AssembleTranslationInput, PrepareUnitsInput, TranslateBookActivities,
                                              TranslateBookInput, TranslateUnitBatchInput, translation_run_id)
from b2c_orchestration.translation_store import TranslationResultStore
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
IDENTITY = EngineIdentity(provider="fake", model_id="fake-mt", revision="r1", prompt_version="fake@1")
TEXTS = [("heading", "Chapter One: Soil"),
         ("paragraph", "Farmers have improved the soil for many centuries. They added manure and rotated "
                       "their crops carefully."),
         ("paragraph", "The yield rose by 12 percent in the first decade.1"),
         ("paragraph", "Rice grows best in warm, wet fields. Wheat prefers a cooler climate."),
         ("page_footer", "17")]
ARABIC = {
    "Chapter One: Soil": "الفصل الأول: التربة",
    "Farmers have improved the soil for many centuries. They added manure and rotated their crops carefully.":
        "حسّن المزارعون التربة على مدى قرون عديدة. وأضافوا السماد وناوبوا بين محاصيلهم بعناية.",
    "The yield rose by 12 percent in the first decade.": "ارتفع المحصول بنسبة 12 في المئة في العقد الأول.",
    "Rice grows best in warm, wet fields. Wheat prefers a cooler climate.":
        "ينمو الأرز على أفضل وجه في الحقول الدافئة الرطبة. أما القمح فيفضّل مناخًا أبرد.",
    # sentence-level retranslation of the unit above (the pipeline's first lever)
    "Rice grows best in warm, wet fields.": "ينمو الأرز على أفضل وجه في الحقول الدافئة الرطبة.",
    "Wheat prefers a cooler climate.": "أما القمح فيفضّل مناخًا أبرد.",
}


def parsed_book() -> ParsedBook:
    blocks = [Block(block_id=f"bk:1:{i}", type=t, text=x, refs=[PageRef(book_id="bk", page_no=1)],
                    section_path=["Chapter One"], ocr=False) for i, (t, x) in enumerate(TEXTS)]
    return ParsedBook(book_id="bk", title="T", role="spine", page_count=1, toc_source="headings", blocks=blocks,
                      parser={"name": "test"})


class FakeEngine:
    def __init__(self, fail_on: str | None = None, error: Exception | None = None):
        self.calls: list = []
        self.fail_on, self.error = fail_on, error

    identity = property(lambda self: IDENTITY)
    capabilities = property(lambda self: EngineCapabilities(
        follows_instructions=True, supports_terms=False, supports_context=True, supports_repair=False,
        max_source_chars=2000))

    def translate(self, request):
        self.calls.append(request)
        if self.fail_on and request.unit.source_text.startswith(self.fail_on):
            raise self.error
        return EngineResult(text=ARABIC[request.unit.source_text], identity=IDENTITY)


class PassingQE:
    name = "fake-qe"

    def estimate(self, unit, target):
        return QEOutcome(passed=True, result=QEResult(deterministic_pass=True, route="auto_accept", cometkiwi=0.9))


class FakeRuntime:
    def __init__(self, engine, qe=None, fingerprint="f" * 64):
        self._engine, self._qe, self._fp = engine, qe, fingerprint

    def fingerprint(self, engine_profile, qe_profile):
        return self._fp

    def engine(self, profile_id):
        return self._engine

    def estimator(self, profile_id, *, source_language="en", target_language="ar"):
        return None if profile_id is None else self._qe


class FakeMemory:
    def __init__(self):
        self.stored = []

    def lookup(self, unit, source_language, target_language):
        return None

    def store(self, unit, translation, **kwargs):
        self.stored.append((unit.unit_id, translation.origin))


@pytest.fixture
def artifacts():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        store.ensure_bucket()
        yield store


@pytest.fixture
def db():
    return FakeTranslationDB()


@pytest.fixture
def results(db):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return TranslationResultStore("postgresql://unused", connect=db.connect, clock=lambda: next(clock))


def parsed_ref(artifacts, course="c1") -> ArtifactRef:
    return artifacts.put(course, "ingest-parse-pdf", "b" * 64,
                         {"parsed-book.json": parsed_book().model_dump_json().encode()})


def book_input(ref: ArtifactRef, **kw) -> TranslateBookInput:
    data = dict(course_id="c1", source_artifact_ref=ref.model_dump(mode="json"), source_language="en",
                target_language="ar", engine_profile="fake", qe_profile="qe", profile_fingerprint="f" * 64,
                batch_size=2)
    data.update(kw)
    return TranslateBookInput(**data)


def activities(artifacts, results, engine=None, qe=PassingQE(), memory=None):
    return TranslateBookActivities(artifact_store=artifacts, result_store=results,
                                   runtime=FakeRuntime(engine or FakeEngine(), qe), memory=memory)


def prepare(acts, inp):
    return acts.prepare(PrepareUnitsInput(run_id=translation_run_id(inp), inp=inp))


def batch(inp, prepared, ids):
    return TranslateUnitBatchInput(run_id=translation_run_id(inp), course_id=inp.course_id,
                                   units_ref=prepared.units_ref, unit_ids=ids, engine_profile=inp.engine_profile,
                                   qe_profile=inp.qe_profile, profile_fingerprint=inp.profile_fingerprint,
                                   source_language="en", target_language="ar")


# ------------------------------------------------------------------ input and run id
def test_input_validates_languages_profiles_and_bounds(artifacts):
    ref = parsed_ref(artifacts)
    with pytest.raises(ValueError):
        book_input(ref, target_language="en")
    with pytest.raises(ValueError):
        book_input(ref, engine_profile="Bad Profile")
    with pytest.raises(ValueError):
        book_input(ref, batch_size=0)
    with pytest.raises(ValueError):
        book_input(ref, max_unit_chars=10)
    assert book_input(ref).max_unit_chars == 400 and book_input(ref, batch_size=25).batch_size == 25


def test_run_id_is_deterministic_and_covers_what_changes_output(artifacts):
    ref = parsed_ref(artifacts)
    base = translation_run_id(book_input(ref))
    assert base == translation_run_id(book_input(ref)) and len(base) == 64
    # batching does not change any unit result, so it does not change the run
    assert translation_run_id(book_input(ref, batch_size=7)) == base
    for change in ({"course_id": "c2"}, {"engine_profile": "other"}, {"qe_profile": None},
                   {"max_unit_chars": 300}, {"profile_fingerprint": "e" * 64},
                   {"source_language": "ar", "target_language": "en"}):
        assert translation_run_id(book_input(ref, **change)) != base, change
    other_source = ref.model_copy(update={"sha256": "c" * 64})
    assert translation_run_id(book_input(other_source)) != base
    assert tb.translate_book_workflow_id(base) == f"b2c-translate-book-{base}"


# ------------------------------------------------------------------ prepare
def test_prepare_stores_units_artifact_once_and_registers_the_run(artifacts, results):
    inp = book_input(parsed_ref(artifacts))
    acts = activities(artifacts, results)
    first = prepare(acts, inp)
    again = prepare(acts, inp)
    assert first.unit_ids == ["bk:u00000", "bk:u00001", "bk:u00002", "bk:u00003", "bk:u00004"]
    assert first.units_ref == again.units_ref and again.cache_hit and not first.cache_hit
    ref = ArtifactRef.model_validate(first.units_ref)
    assert ref.stage == "translation-units"
    assert ref.key == stage_key("translation-units", tb.UNITS_BUILDER_VERSION,
                                [inp.source_artifact_ref["sha256"]], {"max_unit_chars": 400})
    units = json.loads(artifacts.read(ref, "units.json"))
    assert [u["unit_id"] for u in units] == first.unit_ids
    build = json.loads(artifacts.read(ref, "build.json"))
    assert build["units_builder_version"] == tb.UNITS_BUILDER_VERSION and build["book_id"] == "bk"
    run = results.get_run(translation_run_id(inp))
    assert run.units_ref == first.units_ref and run.unit_count == 5 and run.book_id == "bk"
    assert run.input_json["engine_profile"] == "fake"


def test_prepare_refuses_a_source_outside_the_course_or_stage(artifacts, results):
    acts = activities(artifacts, results)
    foreign = parsed_ref(artifacts, course="other")
    with pytest.raises(ApplicationError) as err:
        prepare(acts, book_input(foreign))
    assert err.value.non_retryable and err.value.type == "InvalidTranslationSource"
    wrong_stage = artifacts.put("c1", "generate-course-qa", "d" * 64, {"course-qa.json": b"{}"})
    with pytest.raises(ApplicationError):
        prepare(acts, book_input(wrong_stage))


# ------------------------------------------------------------------ batches
def test_batch_translates_with_neighbour_context_and_stores_every_unit(artifacts, results):
    inp = book_input(parsed_ref(artifacts))
    engine, memory = FakeEngine(), FakeMemory()
    acts = activities(artifacts, results, engine, memory=memory)
    prepared = prepare(acts, inp)
    out = acts.translate_batch(batch(inp, prepared, prepared.unit_ids[1:3]))
    assert out.translated == 2 and out.skipped == 0
    # context comes from the book's neighbouring units, also across the batch boundary
    first = engine.calls[0]
    assert first.context_before == "Chapter One: Soil" and first.context_after.startswith("The yield")
    run_id = translation_run_id(inp)
    stored = results.fetch(run_id, "bk:u00002")
    assert stored.decision == "accepted" and stored.translation.target_text.endswith(" 1")
    assert stored.drafts and stored.drafts[0]["lever"] == "initial" and stored.drafts[0]["qe"]["passed"]
    assert ("bk:u00001", "engine") in memory.stored


def test_without_qe_every_engine_result_needs_review(artifacts, results):
    inp = book_input(parsed_ref(artifacts), qe_profile=None)
    acts = activities(artifacts, results, qe=None)
    prepared = prepare(acts, inp)
    acts.translate_batch(batch(inp, prepared, prepared.unit_ids))
    counts = results.counts(translation_run_id(inp))
    assert counts == {"needs_review": 4, "dropped": 1}


def test_crash_then_resume_does_not_redo_finished_units(artifacts, results):
    inp = book_input(parsed_ref(artifacts))
    crashing = FakeEngine(fail_on="The yield", error=ConnectionError("worker lost the model server"))
    acts = activities(artifacts, results, crashing)
    prepared = prepare(acts, inp)
    with pytest.raises(ConnectionError):
        acts.translate_batch(batch(inp, prepared, prepared.unit_ids))
    run_id = translation_run_id(inp)
    assert set(results.counts(run_id)) == {"accepted"}  # u0 and u1 survived the crash
    assert results.fetch(run_id, "bk:u00002") is None

    resumed = FakeEngine()
    acts = activities(artifacts, results, resumed)
    out = acts.translate_batch(batch(inp, prepared, prepared.unit_ids))
    assert out.skipped == 2 and out.translated == 3
    assert [c.unit.unit_id for c in resumed.calls] == ["bk:u00002", "bk:u00003"]  # u4 is noise, no engine
    assert results.counts(run_id) == {"accepted": 4, "dropped": 1}


def test_human_results_are_never_retranslated(artifacts, results):
    from b2c_contracts import UnitTranslation
    inp = book_input(parsed_ref(artifacts), qe_profile=None)
    engine = FakeEngine()
    acts = activities(artifacts, results, engine, qe=None)
    prepared = prepare(acts, inp)
    run_id = translation_run_id(inp)
    results.upsert(run_id, "bk", UnitTranslation(unit_id="bk:u00001", target_text="بشري.", origin="human",
                                                 decision="accepted", decided_by="human", decided_at=NOW))
    acts.translate_batch(batch(inp, prepared, ["bk:u00001", "bk:u00002"]))
    assert [c.unit.unit_id for c in engine.calls] == ["bk:u00002"]
    assert results.fetch(run_id, "bk:u00001").translation.target_text == "بشري."


def test_deterministic_engine_errors_become_rejected_units(artifacts, results):
    inp = book_input(parsed_ref(artifacts))
    engine = FakeEngine(fail_on="The yield", error=ValueError("bk:u00002: 9999 chars exceeds engine limit"))
    acts = activities(artifacts, results, engine)
    prepared = prepare(acts, inp)
    acts.translate_batch(batch(inp, prepared, prepared.unit_ids))
    stored = results.fetch(translation_run_id(inp), "bk:u00002")
    assert stored.decision == "rejected" and stored.translation.engine == IDENTITY
    assert stored.translation.issues[0].check_id == "engine_error"
    assert stored.translation.issues[0].severity == "critical"


def test_batch_refuses_unknown_units_and_changed_profiles(artifacts, results):
    inp = book_input(parsed_ref(artifacts))
    acts = activities(artifacts, results)
    prepared = prepare(acts, inp)
    with pytest.raises(ApplicationError) as err:
        acts.translate_batch(batch(inp, prepared, ["bk:u09999"]))
    assert err.value.non_retryable and err.value.type == "UnknownTranslationUnit"
    changed = batch(inp, prepared, prepared.unit_ids[:1]).model_copy(update={"profile_fingerprint": "e" * 64})
    with pytest.raises(ApplicationError) as err:
        acts.translate_batch(changed)
    assert err.value.non_retryable and err.value.type == "TranslationProfileChanged"
    foreign = batch(inp, prepared, prepared.unit_ids[:1]).model_copy(update={"course_id": "c2"})
    with pytest.raises(ApplicationError) as err:
        acts.translate_batch(foreign)
    assert err.value.type == "InvalidTranslationSource"


def test_unknown_profile_is_not_retryable(artifacts, results):
    from b2c_orchestration.translation_profiles import UnknownProfile

    class Missing(FakeRuntime):
        def engine(self, profile_id):
            raise UnknownProfile(profile_id)
    inp = book_input(parsed_ref(artifacts))
    acts = TranslateBookActivities(artifact_store=artifacts, result_store=results, runtime=Missing(FakeEngine()))
    prepared = prepare(acts, inp)
    with pytest.raises(ApplicationError) as err:
        acts.translate_batch(batch(inp, prepared, prepared.unit_ids[:1]))
    assert err.value.non_retryable and err.value.type == "UnknownTranslationProfile"


# ------------------------------------------------------------------ assemble
def test_assemble_publishes_only_accepted_and_dropped_text(artifacts, results):
    inp = book_input(parsed_ref(artifacts))

    class SplitQE(PassingQE):
        def estimate(self, unit, target):
            if unit.unit_id == "bk:u00003":
                return QEOutcome(passed=False, result=QEResult(deterministic_pass=True, route="human_review"),
                                 issues=[QAIssue(check_id="qe", category="meaning_error", severity="major",
                                                 origin="qe")])
            return super().estimate(unit, target)
    acts = activities(artifacts, results, qe=SplitQE())
    prepared = prepare(acts, inp)
    acts.translate_batch(batch(inp, prepared, prepared.unit_ids))
    run_id = translation_run_id(inp)
    out = acts.assemble(AssembleTranslationInput(run_id=run_id, course_id="c1", units_ref=prepared.units_ref))
    ref = ArtifactRef.model_validate(out.artifact)
    assert ref.stage == "translate-book"
    doc = json.loads(artifacts.read(ref, "translation.json"))
    blocks = doc["blocks"]
    assert blocks["bk:1:0"] == {"text": "الفصل الأول: التربة", "complete": True, "pending_unit_ids": [],
                                "merged_into": None}
    assert blocks["bk:1:3"]["text"] is None and blocks["bk:1:3"]["pending_unit_ids"] == ["bk:u00003"]
    assert blocks["bk:1:4"] == {"text": "", "complete": True, "pending_unit_ids": [], "merged_into": None}
    assert "ينمو الأرز" not in json.dumps(doc, ensure_ascii=False)  # unverified engine text never leaks
    assert doc["counts"] == {"accepted": 3, "needs_review": 1, "rejected": 0, "dropped": 1, "pending": 0}
    assert doc["engines"] == [IDENTITY.model_dump(mode="json")]
    assert doc["units_ref"] == prepared.units_ref and doc["run_id"] == run_id
    assert out.complete_blocks == 4 and out.total_blocks == 5
    assert results.get_run(run_id).assembly["artifact"] == out.artifact
    # unchanged results -> the same immutable artifact
    again = acts.assemble(AssembleTranslationInput(run_id=run_id, course_id="c1", units_ref=prepared.units_ref))
    assert again.artifact == out.artifact


def test_assemble_counts_units_without_a_result_as_pending(artifacts, results):
    inp = book_input(parsed_ref(artifacts))
    acts = activities(artifacts, results)
    prepared = prepare(acts, inp)
    acts.translate_batch(batch(inp, prepared, prepared.unit_ids[:2]))
    out = acts.assemble(AssembleTranslationInput(run_id=translation_run_id(inp), course_id="c1",
                                                 units_ref=prepared.units_ref))
    assert out.counts["pending"] == 3 and out.counts["accepted"] == 2
