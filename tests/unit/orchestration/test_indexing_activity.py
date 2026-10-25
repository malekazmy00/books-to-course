from datetime import datetime, timezone
import json
import pytest
from temporalio.testing import ActivityEnvironment
from temporalio.exceptions import ApplicationError
from b2c_contracts import ArtifactRef
from b2c_contracts.models import Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook
from b2c_orchestration.indexing import IndexActivities, IndexApprovedBookInput


def fixtures(course="course", extra_role="supplementary"):
    parsed = ParsedBook(book_id="spine", title="No TOC", role="spine", page_count=2,
        toc_source="llm_inferred", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id=f"b{i}", type="paragraph", text=f"Text for concept {i}.",
                  refs=[PageRef(book_id="spine", page_no=1 if i < 3 else 2)], section_path=[], ocr=False)
            for i in range(1, 5)
        ])
    outline = CourseOutline(course_id=course, spine_book_id="spine", supplementary_book_ids=["extra"],
        requires_review=True, modules=[
            Module(module_id="m1", title="Module A", order=1, lessons=[
                Lesson(lesson_id="m1.l1", title="Concept A", order=1,
                       spine_span=LessonSpan(start_block="b1", end_block="b2", pages=(1, 1)),
                       provenance="llm_inferred", prior_lessons=[])]),
            Module(module_id="m2", title="Module B", order=2, lessons=[
                Lesson(lesson_id="m2.l1", title="Concept B", order=1,
                       spine_span=LessonSpan(start_block="b3", end_block="b4", pages=(2, 2)),
                       provenance="llm_inferred", prior_lessons=[])]),
        ])
    parsed_ref = ArtifactRef(stage="ingest-parse-pdf", key="a" * 64,
        uri="s3://artifacts/course/ingest-parse-pdf/" + "a" * 64 + "/",
        sha256="b" * 64, created_at=datetime.now(timezone.utc))
    approved_ref = ArtifactRef(stage="approve-outline", key="c" * 64,
        uri="s3://artifacts/course/approve-outline/" + "c" * 64 + "/",
        sha256="d" * 64, created_at=datetime.now(timezone.utc))
    extra = ParsedBook(book_id="extra", title="Supplement", role=extra_role, page_count=1,
        toc_source="headings", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id="extra:b1", type="paragraph", text="Additional context.",
                  refs=[PageRef(book_id="extra", page_no=3)], section_path=["Notes"], ocr=False)
        ])
    extra_ref = ArtifactRef(stage="ingest-parse-pdf", key="e" * 64,
        uri="s3://artifacts/course/ingest-parse-pdf/" + "e" * 64 + "/",
        sha256="f" * 64, created_at=datetime.now(timezone.utc))
    refs = {
        (parsed_ref.uri, "parsed-book.json"): parsed.model_dump_json().encode(),
        (approved_ref.uri, "approved-outline.json"): json.dumps({
            "outline": outline.model_dump(mode="json"), "reviewer_id": "reviewer",
            "approved_at": "2026-10-04T00:00:00Z",
        }).encode(),
        (extra_ref.uri, "parsed-book.json"): extra.model_dump_json().encode(),
    }
    return parsed, outline, parsed_ref, approved_ref, extra_ref, refs


class FakeArtifacts:
    def __init__(self, refs): self.refs = refs
    def read(self, ref, filename): return self.refs[(ref.uri, filename)]
    def get_or_compute(self, course, stage, key, compute):
        uri = f"s3://artifacts/{course}/{stage}/{key}/"
        ref = ArtifactRef(stage=stage, key=key, uri=uri, sha256=key,
                          created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        if (uri, "chunks.json") in self.refs:
            return ref, True
        for name, data in compute().items():
            self.refs[(uri, name)] = data
        return ref, False


class FakeChunks:
    def __init__(self): self.schema_calls = 0; self.replacements = []
    def ensure_schema(self): self.schema_calls += 1
    def replace_book(self, course, book, chunks):
        self.replacements.append((course, book, list(chunks)))
        return len(chunks)


def test_retry_restores_saved_chunks_after_database_failure(monkeypatch):
    _, _, parsed_ref, approved_ref, extra_ref, refs = fixtures()
    artifacts = FakeArtifacts(refs)
    chunks = FakeChunks()
    activity = IndexActivities(artifacts, chunks)
    inp = IndexApprovedBookInput(course_id="course", parsed_ref=parsed_ref.model_dump(mode="json"),
        approved_outline_ref=approved_ref.model_dump(mode="json"),
        supplementary_parsed_refs={"extra": extra_ref.model_dump(mode="json")})
    original_replace = chunks.replace_book
    def failed_replace(*args):
        raise ConnectionError("database temporarily unavailable")
    chunks.replace_book = failed_replace
    with pytest.raises(ConnectionError):
        ActivityEnvironment().run(activity.index_approved_book, inp)
    chunks.replace_book = original_replace
    from b2c_orchestration import indexing
    original_chunk = indexing.index_book
    def check_cached(book, **kwargs):
        if book.book_id == "spine":
            raise AssertionError("completed spine chunking must not repeat")
        return original_chunk(book, **kwargs)
    monkeypatch.setattr(indexing, "index_book", check_cached)
    result = ActivityEnvironment().run(activity.index_approved_book, inp)
    assert result["books"]["spine"]["cache_hit"] is True
    assert result["chunk_count"] == 3


def test_activity_chunks_approved_no_toc_outline_and_persists_provenance():
    parsed, _, parsed_ref, approved_ref, extra_ref, refs = fixtures()
    chunk_store = FakeChunks()
    activity = IndexActivities(FakeArtifacts(refs), chunk_store)
    result = ActivityEnvironment().run(activity.index_approved_book,
        IndexApprovedBookInput(course_id="course", parsed_ref=parsed_ref.model_dump(mode="json"),
                               approved_outline_ref=approved_ref.model_dump(mode="json"),
                               supplementary_parsed_refs={"extra": extra_ref.model_dump(mode="json")}))
    assert result["book_count"] == 2
    assert result["chunk_count"] == 3
    assert result["outline_sha256"] == approved_ref.sha256
    primary_chunks = chunk_store.replacements[0][2]
    supplement_chunks = chunk_store.replacements[1][2]
    assert [c.section_path for c in primary_chunks] == [["Module A", "Concept A"], ["Module B", "Concept B"]]
    assert [c.block_ids for c in primary_chunks] == [["b1", "b2"], ["b3", "b4"]]
    assert all(c.pages and c.book_id == parsed.book_id and c.role == "spine" for c in primary_chunks)
    assert len(supplement_chunks) == 1
    assert supplement_chunks[0].book_id == "extra" and supplement_chunks[0].role == "supplementary"
    assert supplement_chunks[0].pages == [3] and supplement_chunks[0].block_ids == ["extra:b1"]
    assert result["books"]["extra"]["role"] == "supplementary"
    assert chunk_store.schema_calls == 1


def test_activity_rejects_wrong_course_before_database_write():
    _, _, parsed_ref, approved_ref, _, refs = fixtures()
    chunks = FakeChunks()
    activity = IndexActivities(FakeArtifacts(refs), chunks)
    with pytest.raises(ApplicationError, match="course"):
        ActivityEnvironment().run(activity.index_approved_book,
            IndexApprovedBookInput(course_id="other-course", parsed_ref=parsed_ref.model_dump(mode="json"),
                                   approved_outline_ref=approved_ref.model_dump(mode="json"),
                                   supplementary_parsed_refs={"extra": {"stage":"ingest-parse-pdf"}}))
    assert chunks.replacements == []
    assert chunks.schema_calls == 0


def test_course_role_override_chunks_uploaded_spine_as_supplementary():
    parsed, _, parsed_ref, approved_ref, extra_ref, refs = fixtures(extra_role="spine")
    chunk_store = FakeChunks()
    activity = IndexActivities(FakeArtifacts(refs), chunk_store)
    result = ActivityEnvironment().run(activity.index_approved_book,
        IndexApprovedBookInput(course_id="course", parsed_ref=parsed_ref.model_dump(mode="json"),
                               approved_outline_ref=approved_ref.model_dump(mode="json"),
                               supplementary_parsed_refs={"extra": extra_ref.model_dump(mode="json")}))
    assert result["books"]["extra"]["role"] == "supplementary"
    assert all(chunk.role == "supplementary" for _, book_id, chunks in chunk_store.replacements
               if book_id == "extra" for chunk in chunks)
    # The durable ParsedBook keeps its original upload role.
    assert ParsedBook.model_validate_json(refs[(extra_ref.uri, "parsed-book.json")]).role == "spine"
