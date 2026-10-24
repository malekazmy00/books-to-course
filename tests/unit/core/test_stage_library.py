import boto3
import pytest
from moto import mock_aws
from b2c_contracts import BookUpload, ParsedBook, stage_key
from b2c_core import ArtifactStore
from b2c_core.stage_library import StageBundle, StageLibrary, payload_digest

@pytest.fixture
def library():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        yield StageLibrary(store)

def values():
    upload = BookUpload(book_id="book", role="spine", file_sha256="a"*64,
        mime="application/pdf", title="Test", rights_status="internal_test_only")
    parsed = ParsedBook(book_id="book", role="spine", title="Test", page_count=1,
        toc_source="headings", parser={"name":"external", "version":"1"}, blocks=[{
        "block_id":"book:1:1", "type":"paragraph", "text":"Source text.",
        "refs":[{"book_id":"book", "page_no":1}], "section_path":[], "ocr":True}])
    payload = parsed.model_dump(mode="json")
    bundle = StageBundle(stage="ocr", book_id="book", source_sha256=upload.file_sha256,
        config={"engine":"external-v1"}, payload=payload, payload_sha256=payload_digest(payload))
    return upload, parsed, bundle

def test_import_roundtrip_and_idempotency(library):
    upload, parsed, bundle = values()
    ref = library.import_ocr("course", upload, bundle)
    assert library.import_ocr("course", upload, bundle) == ref
    exported = ParsedBook.model_validate_json(library.export_artifact("course", ref)["parsed-book.json"])
    assert exported == parsed.model_copy(update={"parser": {**parsed.parser, "source_file_sha256": upload.file_sha256}})
    assert exported.parser["source_file_sha256"] == upload.file_sha256
    with pytest.raises(ValueError, match="outside"):
        library.export_artifact("foreign", ref)

@pytest.mark.parametrize("fault", ["source", "digest", "pages", "duplicate"])
def test_reject_untrusted_ocr_before_persistence(library, fault):
    upload, _, bundle = values()
    if fault == "source": bundle.source_sha256 = "b"*64
    if fault == "digest": bundle.payload_sha256 = "c"*64
    if fault == "pages": bundle.payload["blocks"][0]["refs"][0]["page_no"] = 2
    if fault == "duplicate": bundle.payload["blocks"].append(bundle.payload["blocks"][0])
    if fault in {"pages","duplicate"}: bundle.payload_sha256 = payload_digest(bundle.payload)
    with pytest.raises(ValueError):
        library.import_ocr("course", upload, bundle)

class ChunkStore:
    def __init__(self): self.calls = []
    def ensure_schema(self): pass
    def replace_book(self, *args): self.calls.append(args)
    def list_chunks(self, course_id, *, book_ids=None, roles=None):
        chunks = self.calls[-1][2] if self.calls and self.calls[-1][0] == course_id else []
        return [c for c in chunks if (book_ids is None or c.book_id in book_ids)
                and (roles is None or c.role in roles)]

def test_rag_import_checks_provenance_and_outline_and_restores_index(library):
    upload, parsed, _ = values()
    payload={"chunks":[{"chunk_id":"chunk", "book_id":"book", "role":"spine",
        "section_path":[], "block_ids":["book:1:1"], "pages":[1], "text":"Source text.",
        "token_count":2, "embedding_model":"test@1"}]}
    bundle=StageBundle(stage="rag", book_id="book", source_sha256=upload.file_sha256,
        config={"parsed_sha256":"b"*64,"outline_sha256":"c"*64,"embedding_model":"test@1"},
        payload=payload,payload_sha256=payload_digest(payload))
    chunks=ChunkStore()
    with pytest.raises(ValueError, match="fingerprint"):
        library.import_rag("course",upload,bundle,parsed,parsed_sha256="b"*64,
                           outline_sha256="d"*64,chunk_store=chunks)
    assert chunks.calls == []
    ref=library.import_rag("course",upload,bundle,parsed,parsed_sha256="b"*64,
                          outline_sha256="c"*64,chunk_store=chunks)
    assert ref.stage == "index-approved-book" and len(chunks.calls) == 1
    from b2c_indexing import QueryRetrievalService
    service = QueryRetrievalService(chunks)
    hits = service.search("course", "Source", allowed_book_ids={"book"}, mode="lexical")
    assert len(hits) == 1 and hits[0].chunk.chunk_id == "chunk"
    assert service.search("foreign-course", "Source", mode="lexical") == []
    with pytest.raises(ValueError, match="embedders"):
        service.search("course", "Source", mode="hybrid")
    embedding_calls = []
    def embed(text):
        embedding_calls.append(text)
        return [1.0, 0.0]
    hybrid = QueryRetrievalService(chunks, embed_query=embed, embed_passage=embed)
    assert hybrid.search("course", "Source", mode="hybrid")[0].chunk.chunk_id == "chunk"
    assert embedding_calls == ["Source", "Source text."]
    bundle.payload["chunks"][0]["text"]="Invented content"
    bundle.payload_sha256=payload_digest(bundle.payload)
    with pytest.raises(ValueError, match="grounded"):
        library.import_rag("course",upload,bundle,parsed,parsed_sha256="b"*64,
                          outline_sha256="c"*64,chunk_store=chunks)

def test_index_workflow_reuses_imported_rag_snapshot(library, monkeypatch):
    import json
    from temporalio.testing import ActivityEnvironment
    from b2c_contracts.models import CourseOutline, Module, Lesson, LessonSpan
    from b2c_orchestration.indexing import IndexActivities, IndexApprovedBookInput
    from b2c_orchestration import indexing
    upload, parsed, ocr_bundle = values()
    parsed_ref = library.import_ocr("course", upload, ocr_bundle)
    outline = CourseOutline(course_id="course", spine_book_id="book", supplementary_book_ids=[], requires_review=False,
        modules=[Module(module_id="m1", title="M", order=1,
            lessons=[Lesson(lesson_id="m1.l1", title="L", order=1,
                spine_span=LessonSpan(start_block="book:1:1", end_block="book:1:1", pages=(1,1)),
                provenance="headings")])])
    approved_ref = library.store.put("course","approve-outline","d"*64,
        {"approved-outline.json": json.dumps({"outline":outline.model_dump(mode="json")}).encode()})
    payload={"chunks":[{"chunk_id":"imported-chunk","book_id":"book","role":"spine",
        "section_path":["M","L"],"block_ids":["book:1:1"],"pages":[1],
        "text":"Source text.","token_count":2,"embedding_model":"test@1"}]}
    bundle=StageBundle(stage="rag",book_id="book",source_sha256=upload.file_sha256,
        config={"parsed_sha256":parsed_ref.sha256,"outline_sha256":approved_ref.sha256,
                "embedding_model":"test@1","target_tokens":500,"overlap_ratio":0.12},
        payload=payload,payload_sha256=payload_digest(payload))
    chunks=ChunkStore()
    ref=library.import_rag("course",upload,bundle,parsed,parsed_sha256=parsed_ref.sha256,
                          outline_sha256=approved_ref.sha256,chunk_store=chunks)
    def forbidden(*args,**kwargs):
        raise AssertionError("imported RAG must bypass chunking")
    monkeypatch.setattr(indexing,"chunk_book",forbidden)
    result=ActivityEnvironment().run(IndexActivities(library.store,chunks).index_approved_book,
        IndexApprovedBookInput(course_id="course",parsed_ref=parsed_ref.model_dump(mode="json"),
            approved_outline_ref=approved_ref.model_dump(mode="json"),embedding_model="test@1"))
    assert result["books"]["book"]["cache_hit"] is True
    assert result["books"]["book"]["artifact_ref"]["key"] == ref.key
    assert result["books"]["book"]["chunk_ids"] == ["imported-chunk"]
    bundle.payload["chunks"][0]["chunk_id"]="different"
    bundle.payload_sha256=payload_digest(bundle.payload)
    before=len(chunks.calls)
    with pytest.raises(ValueError,match="incompatible"):
        library.import_rag("course",upload,bundle,parsed,parsed_sha256=parsed_ref.sha256,
                          outline_sha256=approved_ref.sha256,chunk_store=chunks)
    assert len(chunks.calls)==before


