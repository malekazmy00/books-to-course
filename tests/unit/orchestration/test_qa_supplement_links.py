"""T-IDX-06 at the consumer: lesson enrichment receives only supplementary passages whose
relevance judgement passed the threshold; an unrelated supplement yields no link."""
from __future__ import annotations

from datetime import UTC, datetime

from temporalio.testing import ActivityEnvironment

from b2c_contracts import ArtifactRef, Block, CourseOutline, PageRef, ParsedBook, StructuredChunk
from b2c_orchestration.qa import CourseQAActivities, GenerateCourseQAInput
from tests.unit.orchestration.test_course_qa_activity import FakeChunkStore, FakeGateway, FakeStore, _fixtures


def _run(monkeypatch, supplement_text: str):
    import b2c_orchestration.qa as qa
    approved_ref, parsed_ref, envelope, parsed, chunks = _fixtures()
    supplement = ParsedBook(book_id="extra", title="Supplement", role="supplementary", page_count=1,
        toc_source="headings", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id="s1", type="paragraph", text=supplement_text,
                  refs=[PageRef(book_id="extra", page_no=7)], section_path=["X"], ocr=False)])
    supplement_ref = ArtifactRef(stage="ingest-parse-pdf", key="f" * 64,
        uri="s3://artifacts/course/ingest-parse-pdf/" + "f" * 64 + "/", sha256="a" * 64,
        created_at=datetime.now(UTC))
    outline = CourseOutline.model_validate(envelope["outline"]).model_copy(update={"supplementary_book_ids": ["extra"]})
    envelope["outline"] = outline.model_dump(mode="json")
    envelope["supplementary_parsed_refs"] = {"extra": supplement_ref.model_dump(mode="json")}
    chunks = [*chunks, StructuredChunk(chunk_id="extra:s1", book_id="extra", role="supplementary",
        section_path=["X"], block_ids=["s1"], pages=[7], text=supplement_text, token_count=12,
        embedding_model="fixture")]
    store = FakeStore(approved_ref, parsed_ref, envelope, parsed)
    store.files[(supplement_ref.uri, "parsed-book.json")] = supplement.model_dump_json().encode()
    seen = {}

    def capture(*args, supplementary_chunks_by_lesson=None, **kwargs):
        seen.update({k: [c.chunk_id for c in v] for k, v in (supplementary_chunks_by_lesson or {}).items()})
        return []

    monkeypatch.setattr(qa, "generate_lesson_enrichments", capture)
    written = {}
    original_put = store.put

    def put(course_id, stage, key, files):
        written.update(files)
        return original_put(course_id, stage, key, files)

    store.put = put
    ActivityEnvironment().run(CourseQAActivities(store, FakeChunkStore(chunks), FakeGateway()).generate,
        GenerateCourseQAInput(course_id="course", approved_outline_ref=approved_ref.model_dump(mode="json"),
            index_result={"course_id": "course", "outline_sha256": approved_ref.sha256, "books": {
                "spine": {"role": "spine", "chunk_count": 2, "chunk_ids": ["c1", "c2"]},
                "extra": {"role": "supplementary", "chunk_count": 1, "chunk_ids": ["extra:s1"]}}},
            lesson_question_count=1, group_question_count=1, course_question_count=1,
            include_lesson_enrichment=True))
    return seen, written


def test_unrelated_supplement_is_not_linked_and_the_judgement_is_stored(monkeypatch):
    seen, written = _run(monkeypatch, "Gramsci describes hegemony as consent organised by civil society, "
                                      "the war of position fought in schools, churches and newspapers.")
    assert seen == {"unit.l1": [], "unit.l2": []}
    assert b'"below_threshold"' in written["supplement-links.json"]


def test_related_supplement_is_linked(monkeypatch):
    seen, _ = _run(monkeypatch, "Independent events do not change each other's probability; the probability "
                                "of independent events multiplies.")
    assert seen["unit.l2"] == ["extra:s1"]
