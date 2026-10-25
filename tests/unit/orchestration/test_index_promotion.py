"""T-IDX-07: index snapshots are promotion candidates, accepted only when the content gate passes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from b2c_contracts import (ArtifactRef, BlockV2, IndexQualityReport, PageInfo, ParsedBookV2, QualityCheck,
                           StructuredChunk)
from b2c_contracts.models import CourseOutline, Lesson, LessonSpan, Module, PageRef
from b2c_core.stage_registry import StageOutputRegistry
from b2c_orchestration.indexing import IndexActivities, IndexApprovedBookInput
from b2c_orchestration.promotion import INDEX_CONTENT_POLICY, PromotionService
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from tests.unit.orchestration.test_indexing_activity import FakeArtifacts, FakeChunks, fixtures

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40


def service():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    registry = StageOutputRegistry("postgresql://unused", connect=FakeRegistryDB().connect,
                                   clock=lambda: next(clock))
    return PromotionService(registry, code_revision=SHA, clock=lambda: NOW)


def snapshot_ref(key="b" * 64):
    return ArtifactRef(stage="index-approved-book", key=key, uri=f"s3://artifacts/course/index-approved-book/{key}/",
                       sha256="c" * 64, created_at=NOW)


def report(passed: bool) -> IndexQualityReport:
    value = 0.0 if passed else 2.0
    return IndexQualityReport(
        book_id="spine", role="spine", report_version="r", chunker_version="c", source_format="parsed-book/2",
        metrics={"orphan_body_blocks": value},
        checks=[QualityCheck(name="orphan_body_blocks", value=value, op="==", threshold=0, passed=passed)],
        accepted=passed)


def test_passing_index_report_promotes_the_snapshot():
    promo = service()
    record = promo.gate_index_build("course", snapshot_ref(), report=report(True), inputs=[])
    assert record.status == "accepted"
    assert record.required_gates == [INDEX_CONTENT_POLICY.gate_id]
    assert promo.require_accepted("course", snapshot_ref()).status == "accepted"


def test_failing_index_report_rejects_the_snapshot():
    record = service().gate_index_build("course", snapshot_ref(), report=report(False), inputs=[])
    assert record.status == "rejected"
    assert INDEX_CONTENT_POLICY.gate_id in record.status_reason
    gate = record.latest_gate_results()[INDEX_CONTENT_POLICY.gate_id]
    assert gate.passed is False and gate.metrics["failed_checks"] == ["orphan_body_blocks"]
    assert gate.evidence_ref.endswith("index-report.json")


def _inp(parsed_ref, approved_ref, extra_ref):
    return IndexApprovedBookInput(course_id="course", parsed_ref=parsed_ref.model_dump(mode="json"),
                                  approved_outline_ref=approved_ref.model_dump(mode="json"),
                                  supplementary_parsed_refs={"extra": extra_ref.model_dump(mode="json")})


def test_activity_registers_every_book_and_writes_only_accepted_indexes():
    _, _, parsed_ref, approved_ref, extra_ref, refs = fixtures()
    artifacts, store, promo = FakeArtifacts(refs), FakeChunks(), service()
    result = ActivityEnvironment().run(IndexActivities(artifacts, store, promotion=promo).index_approved_book,
                                       _inp(parsed_ref, approved_ref, extra_ref))
    assert {b: e["gate"]["status"] for b, e in result["books"].items()} == {"spine": "accepted", "extra": "accepted"}
    for entry in result["books"].values():
        snap = ArtifactRef.model_validate(entry["artifact_ref"])
        assert IndexQualityReport.model_validate_json(artifacts.read(snap, "index-report.json")).accepted
        assert promo.require_accepted("course", snap)
    assert [book for _, book, _ in store.replacements] == ["spine", "extra"]


def _failing_fixture():
    """Lesson 1 is a heading only: the gate fails (lessons_without_body), nothing is written."""
    _, _, parsed_ref, approved_ref, extra_ref, refs = fixtures()
    v2 = ParsedBookV2(book_id="spine", title="T", role="spine", page_count=1, toc_source="pdf_outline",
                      pages=[PageInfo(page_no=1, status="content")], parser={"name": "f", "version": "1"},
                      normalizer={"name": "n", "version": "1"}, blocks=[
                          BlockV2(block_id=f"spine:b0000{i}", type="heading" if i == 1 else "paragraph",
                                  level=1 if i == 1 else None, zone="body", text=f"Text for concept {i}.",
                                  raw_text="t", refs=[PageRef(book_id="spine", page_no=1)], section_path=[],
                                  ocr=False) for i in (1, 2)])
    outline = CourseOutline(course_id="course", spine_book_id="spine", supplementary_book_ids=["extra"],
                            requires_review=False, modules=[Module(module_id="m1", title="M", order=1, lessons=[
                                Lesson(lesson_id="m1.l1", title="Part", order=1, provenance="pdf_outline",
                                       spine_span=LessonSpan(start_block="spine:b00001", end_block="spine:b00001",
                                                             pages=(1, 1))),
                                Lesson(lesson_id="m1.l2", title="Body", order=2, provenance="pdf_outline",
                                       spine_span=LessonSpan(start_block="spine:b00002", end_block="spine:b00002",
                                                             pages=(1, 1)))])])
    v2_ref = parsed_ref.model_copy(update={"stage": "ingest-parse-v2",
                                           "uri": parsed_ref.uri.replace("ingest-parse-pdf", "ingest-parse-v2")})
    refs[(v2_ref.uri, "parsed-book-v2.json")] = v2.model_dump_json().encode()
    refs[(approved_ref.uri, "approved-outline.json")] = json.dumps(
        {"outline": outline.model_dump(mode="json"), "reviewer_id": "r", "approved_at": "2026-10-25T00:00:00Z"}
    ).encode()
    return v2_ref, approved_ref, extra_ref, refs


def test_failed_gate_rejects_and_writes_nothing():
    v2_ref, approved_ref, extra_ref, refs = _failing_fixture()
    artifacts, store, promo = FakeArtifacts(refs), FakeChunks(), service()
    with pytest.raises(ApplicationError) as err:
        ActivityEnvironment().run(IndexActivities(artifacts, store, promotion=promo).index_approved_book,
                                  _inp(v2_ref, approved_ref, extra_ref))
    assert err.value.type == "IndexGateFailed" and err.value.non_retryable
    assert "lessons_without_body" in str(err.value)
    assert store.replacements == []
    rejected = promo.registry.list(course_id="course", stage="index-approved-book", status="rejected")
    assert [r.status for r in rejected] == ["rejected"]


def test_v2_parse_artifact_is_read_and_its_chunks_carry_parts():
    v2_ref, approved_ref, extra_ref, refs = _failing_fixture()
    outline = CourseOutline.model_validate(json.loads(refs[(approved_ref.uri, "approved-outline.json")])["outline"])
    lessons = outline.modules[0].lessons
    merged = lessons[0].model_copy(update={"spine_span": LessonSpan(start_block="spine:b00001",
                                                                      end_block="spine:b00002", pages=(1, 1))})
    good = outline.model_copy(update={"modules": [outline.modules[0].model_copy(update={"lessons": [merged]})]})
    refs[(approved_ref.uri, "approved-outline.json")] = json.dumps(
        {"outline": good.model_dump(mode="json"), "reviewer_id": "r", "approved_at": "2026-10-25T00:00:00Z"}).encode()
    store = FakeChunks()
    result = ActivityEnvironment().run(IndexActivities(FakeArtifacts(refs), store).index_approved_book,
                                       _inp(v2_ref, approved_ref, extra_ref))
    spine_chunks: list[StructuredChunk] = store.replacements[0][2]
    assert result["books"]["spine"]["gate"]["accepted"] is True
    assert result["books"]["spine"]["gate"]["status"] is None  # no promotion service configured
    assert [c.block_ids for c in spine_chunks] == [["spine:b00001", "spine:b00002"]]
    assert all(c.parts and c.lesson_id == "m1.l1" for c in spine_chunks)
