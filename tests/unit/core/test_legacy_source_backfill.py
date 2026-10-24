import hashlib,json
import boto3,pytest
from moto import mock_aws
from b2c_core import ArtifactStore
from b2c_core.legacy_source_backfill import prepare_backfill,apply_backfill
from b2c_contracts import ParsedBook,CourseOutline,Module,Lesson,LessonSpan

@pytest.fixture
def prepared_source():
    with mock_aws():
        store=ArtifactStore(boto3.client("s3",region_name="us-east-1"));store.ensure_bucket()
        raw=b"%PDF-1.7 explicit old book";digest=hashlib.sha256(raw).hexdigest()
        upload=store.put("course","book-upload","a"*64,{"source.pdf":raw,
            "book-upload.json":json.dumps({"book_id":"book-main","file_sha256":digest,"title":"Old","role":"spine"}).encode()})
        parsed=ParsedBook(book_id="book-main",title="Old",role="spine",page_count=1,toc_source="headings",blocks=[],parser={"name":"legacy"})
        parsed_ref=store.put("course","ingest-parse-pdf","b"*64,{"parsed-book.json":parsed.model_dump_json().encode()})
        outline=CourseOutline(course_id="course",spine_book_id="book-main",supplementary_book_ids=[],requires_review=False,
            modules=[Module(module_id="m1",title="M",order=1,lessons=[Lesson(lesson_id="m1.l1",title="L",order=1,
            spine_span=LessonSpan(start_block="legacy",end_block="legacy",pages=(1,1)),provenance="headings")])])
        approved=store.put("course","approve-outline","c"*64,{"approved-outline.json":json.dumps({"outline":outline.model_dump(mode="json"),"spine_parsed_ref":parsed_ref.model_dump(mode="json")}).encode()})
        mapping={"course_id":"course","artifact_book_id":"book-main","file_sha256":digest,
            "upload_key":upload.key,"upload_sha256":upload.sha256,"parsed_key":parsed_ref.key,
            "parsed_sha256":parsed_ref.sha256,"approved_key":approved.key,"approved_sha256":approved.sha256,
            "page_count":1,"block_count":0,"ingest_workflow_id":"b2c-ingest-"+"d"*64,"evidence":"explicit-fixture"}
        yield store,mapping

def test_preparation_is_readonly_and_apply_is_idempotent_without_overwriting_later_progress(prepared_source):
    store,mapping=prepared_source
    before=store.s3.list_objects_v2(Bucket=store.bucket)["KeyCount"]
    plan=prepare_backfill(store,mapping)
    assert store.s3.list_objects_v2(Bucket=store.bucket)["KeyCount"]==before
    apply_backfill(store,plan)
    progress=plan["progress"].copy()
    progress["stages"]={**progress["stages"],"audio":{"workflow_id":"later-audio"}}
    store.s3.put_object(Bucket=store.bucket,Key=plan["progress_key"],Body=json.dumps(progress).encode())
    apply_backfill(store,plan)
    saved=json.loads(store.s3.get_object(Bucket=store.bucket,Key=plan["progress_key"])["Body"].read())
    assert saved["stages"]["audio"]["workflow_id"]=="later-audio"
    assert saved["artifact_book_id"]=="book-main"
    assert saved["book_id"]=="book-"+mapping["file_sha256"][:24]
    from b2c_core.source_identity import artifact_source_identity
    parsed_ref=store.exists("course","ingest-parse-pdf",mapping["parsed_key"])
    resolved=artifact_source_identity(store,parsed_ref)
    assert resolved["source_file_sha256"]==mapping["file_sha256"]
    assert resolved["external_book_id"]==saved["book_id"]
    assert resolved["source_fingerprint_verified"] is True

def test_corruption_or_explicit_mapping_mismatch_never_backfills(prepared_source):
    store,mapping=prepared_source
    with pytest.raises(ValueError,match="index workflow identity"):
        prepare_backfill(store,{**mapping,"index_workflow_id":"b2c-index-"+"f"*64})
    invalid={**mapping,"file_sha256":"f"*64}
    with pytest.raises(ValueError,match="PDF upload"):
        prepare_backfill(store,invalid)
    key=f"course/ingest-parse-pdf/{mapping['parsed_key']}/parsed-book.json"
    store.s3.put_object(Bucket=store.bucket,Key=key,Body=b"tampered")
    with pytest.raises(ValueError,match="corruption"):
        prepare_backfill(store,mapping)
