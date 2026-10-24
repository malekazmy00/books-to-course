import hashlib
import json
import boto3
from moto import mock_aws
from b2c_api.app import create_app
from b2c_core import ArtifactStore, Settings

def capture(function, name):
    cells = dict(zip(function.__code__.co_freevars, function.__closure__ or ()))
    return cells[name].cell_contents

def writer(app):
    upload = next(route.endpoint for route in app.routes if getattr(route,"path",None)=="/v1/books")
    remember = capture(upload,"remember_book_progress")
    return capture(remember,"write_book_progress")

def record(store):
    key = "course/book-progress/"+hashlib.sha256(b"book-main").hexdigest()+".json"
    return json.loads(store.s3.get_object(Bucket=store.bucket, Key=key)["Body"].read())

def test_foreign_audio_is_archived_without_overwriting_current_book_stages():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3",region_name="us-east-1")); store.ensure_bucket()
        save = writer(create_app(settings=Settings(),artifact_store=store))
        save("course","book-main","ingest","new-ingest",{"source_file_sha256":"a"*64,"source_parsed_sha256":"b"*64})
        save("course","book-main","index","new-index",{"source_parsed_sha256":"b"*64})
        save("course","book-main","audio","old-audio",{"source_parsed_sha256":"c"*64})
        stored = record(store)
        assert "audio" not in stored["stages"]
        assert stored["stages"]["index"]["workflow_id"] == "new-index"
        assert stored["stage_history"][-1]["workflow_id"] == "old-audio"
        assert stored["stage_history"][-1]["reason"] == "source_identity_mismatch"

def test_reusing_legacy_book_id_for_another_pdf_preserves_old_stages_in_history():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3",region_name="us-east-1")); store.ensure_bucket()
        save = writer(create_app(settings=Settings(),artifact_store=store))
        save("course","book-main","ingest","old-ingest",{"source_file_sha256":"a"*64})
        save("course","book-main","index","old-index",{})
        save("course","book-main","ingest","new-ingest",{"source_file_sha256":"b"*64})
        stored = record(store)
        assert set(stored["stages"]) == {"ingest"}
        assert stored["source_file_sha256"] == "b"*64
        assert stored["stage_history"][-1]["stages"]["index"]["workflow_id"] == "old-index"

import httpx
import pytest
from types import SimpleNamespace
from b2c_contracts import ParsedBook


def replace_capture(function, name, value):
    cells = dict(zip(function.__code__.co_freevars, function.__closure__ or ()))
    cells[name].cell_contents = value


@pytest.mark.asyncio
async def test_alias_progress_projects_exact_old_stages_and_audio_write_stays_isolated():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3",region_name="us-east-1")); store.ensure_bucket()
        old_digest = "a"*64; new_digest = "7"*64
        old_id = "book-"+old_digest[:24]; new_id = "book-"+new_digest[:24]
        old_parsed = ParsedBook(book_id="book-main", title="Old", role="spine",page_count=1,
            toc_source="headings",blocks=[],parser={"name":"old"})
        new_parsed = old_parsed.model_copy(update={"book_id":new_id,"title":"New"})
        old_ref = store.put("course","ingest-parse-pdf","b"*64,{"parsed-book.json":old_parsed.model_dump_json().encode()})
        new_ref = store.put("course","ingest-parse-pdf","c"*64,{"parsed-book.json":new_parsed.model_dump_json().encode()})
        app = create_app(settings=Settings(),artifact_store=store)
        endpoint = next(route.endpoint for route in app.routes if getattr(route,"path",None)=="/v1/courses/{course_id}/books/{book_id}/progress")
        async def catalog(course_id):
            return {"books":[{"book_id":old_id,"artifact_book_id":"book-main","file_sha256":old_digest,"workflow_id":"old-ingest"},
                {"book_id":new_id,"artifact_book_id":new_id,"file_sha256":new_digest,"workflow_id":"new-ingest"}]}
        async def ingestion(workflow_id, **kwargs):
            ref = old_ref if workflow_id == "old-ingest" else new_ref
            return {"status":"COMPLETED","result":{"parsed_book":{"ref":ref.model_dump(mode="json")}}}
        class Temporal:
            def get_workflow_handle(self, workflow_id):
                async def describe():
                    if workflow_id == "old-qa":
                        from temporalio.service import RPCError, RPCStatusCode
                        raise RPCError("retention expired", RPCStatusCode.NOT_FOUND, b"")
                    return SimpleNamespace(status=SimpleNamespace(name="COMPLETED"))
                return SimpleNamespace(describe=describe)
        async def temporal(): return Temporal()
        replace_capture(endpoint,"list_course_books",catalog)
        replace_capture(endpoint,"ingestion_status",ingestion)
        replace_capture(endpoint,"get_temporal_client",temporal)
        save=writer(app)
        save("course","book-main","ingest","new-ingest",{"source_parsed_sha256":new_ref.sha256})
        # This old legacy result survived before fingerprinting existed.
        save("course","book-main","book_translation","old-translation",{"source_parsed_sha256":old_ref.sha256})
        approved=store.put("course","approve-outline","d"*64,{"approved-outline.json":json.dumps(
            {"spine_parsed_ref":old_ref.model_dump(mode="json")}).encode()})
        save("course","book-main","outline","old-ingest",{"source_parsed_sha256":old_ref.sha256,
            "approved_outline_ref":approved.model_dump(mode="json")})
        qa_ref = store.put("course", "generate-course-qa", "e"*64,
            {"course-qa.json": json.dumps({"status": "failed", "scopes": []}).encode()})
        save("course","book-main","qa","old-qa",{"source_parsed_sha256":old_ref.sha256,"result_status":"failed",
            "artifact_ref": qa_ref.model_dump(mode="json")})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            old=(await client.get(f"/v1/courses/course/books/{old_id}/progress")).json()
            new=(await client.get(f"/v1/courses/course/books/{new_id}/progress")).json()
        assert old["stages"]["book_translation"]["workflow_id"] == "old-translation"
        assert old["book_id"] == old_id
        assert old["artifact_book_id"] == "book-main"
        assert old["stages"]["outline"]["status"] == "COMPLETED"
        assert old["stages"]["outline"]["recovered_from_artifact"] is True
        assert old["stages"]["qa"]["status"] == "PARTIAL"
        assert old["stages"]["qa"]["recovered_from_artifact"] is True
        assert old["stages"]["qa"]["status"] == "PARTIAL"
        assert old["stages"]["qa"]["workflow_status"] == "NOT_FOUND"
        assert "book_translation" not in new["stages"]
        upload = next(route.endpoint for route in app.routes if getattr(route,"path",None)=="/v1/books")
        remember=capture(upload,"remember_book_progress")
        await remember("course","book-main","audio","old-audio",{"source_parsed_sha256":old_ref.sha256})
        old_key="course/book-progress/"+hashlib.sha256(old_id.encode()).hexdigest()+".json"
        saved_old=json.loads(store.s3.get_object(Bucket=store.bucket,Key=old_key)["Body"].read())
        assert saved_old["stages"]["audio"]["workflow_id"] == "old-audio"
        assert "audio" not in record(store)["stages"]


def test_same_ingestion_registry_refresh_preserves_original_request_time():
    # Resolving legacy identity while starting a later stage is a registry
    # refresh, not a new PDF upload. Old stages must not become falsely stale.
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        save = writer(create_app(settings=Settings(), artifact_store=store))
        save("course", "book-main", "ingest", "same-ingest",
             {"source_file_sha256": "a"*64})
        first = record(store)["stages"]["ingest"]["requested_at"]
        save("course", "book-main", "ingest", "same-ingest",
             {"source_parsed_sha256": "b"*64})
        refreshed = record(store)["stages"]["ingest"]
        assert refreshed["requested_at"] == first
        assert refreshed["source_parsed_sha256"] == "b"*64
        save("course", "book-main", "ingest", "different-ingest",
             {"source_file_sha256": "a"*64})
        assert record(store)["stages"]["ingest"]["requested_at"] != first
