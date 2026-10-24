import json
import hashlib
from types import SimpleNamespace
import boto3
import httpx
import pytest
from moto import mock_aws
from temporalio.service import RPCError, RPCStatusCode
from b2c_api.app import create_app
from b2c_core import ArtifactStore, Settings


def capture_set(function, name, value):
    cells = dict(zip(function.__code__.co_freevars, function.__closure__ or ()))
    cells[name].cell_contents = value


@pytest.mark.asyncio
async def test_qa_result_recovery_requires_matching_book_workflow_and_retains_progress():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        wf = "b2c-qa-" + "a"*64
        qa = {"status": "failed", "scopes": [
            {"status": "final", "items": [{"question": "Q", "answer": "A"}]},
            {"status": "failed", "items": []}]}
        ref = store.put("course", "generate-course-qa", "b"*64,
                        {"course-qa.json": json.dumps(qa).encode()})
        app = create_app(settings=Settings(), artifact_store=store)
        endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/v1/course-qa/{workflow_id}")
        class Handle:
            async def describe(self):
                raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
        async def temporal():
            return SimpleNamespace(get_workflow_handle=lambda _: Handle())
        async def registry(cid, bid):
            return {"stages": {"qa": {"workflow_id": wf if bid == "right-book" else "another-workflow",
                                     "artifact_ref": ref.model_dump(mode="json")}}}
        capture_set(endpoint, "get_temporal_client", temporal)
        capture_set(endpoint, "book_progress", registry)
        owner_record = {"course_id": "course", "book_id": "right-book", "stages": {"qa": {"workflow_id": wf, "artifact_ref": ref.model_dump(mode="json")}}}
        store.s3.put_object(Bucket=store.bucket, Key="course/book-progress/" + hashlib.sha256(b"right-book").hexdigest() + ".json", Body=json.dumps(owner_record).encode())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/v1/course-qa/{wf}?recovery_course_id=course&recovery_book_id=right-book")
            assert response.status_code == 200
            result = response.json()
            assert result["status"] == "PARTIAL"
            assert result["recovered_from_artifact"]
            assert result["progress"]["completed_scopes"] == 1
            assert result["progress"]["failed_scopes"] == 1
            assert result["progress"]["question_count"] == 1
            legacy = await client.get(f"/v1/course-qa/{wf}")
            assert legacy.status_code == 200
            assert legacy.json()["course_qa"] == qa
            wrong = await client.get(f"/v1/course-qa/{wf}?recovery_course_id=course&recovery_book_id=wrong-book")
            assert wrong.status_code == 503


@pytest.mark.asyncio
async def test_failed_qa_reads_qa_checkpoint_namespace():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        wf = "b2c-qa-" + "a"*64
        checkpoint = {"completed_scopes": 4, "failed_scopes": 1, "total_scopes": 8, "question_count": 20}
        store.s3.put_object(Bucket=store.bucket, Key=f"_system/qa-progress/{wf}.json",
                            Body=json.dumps(checkpoint).encode())
        app = create_app(settings=Settings(), artifact_store=store)
        endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/v1/course-qa/{workflow_id}")
        class Handle:
            async def describe(self): return SimpleNamespace(status=SimpleNamespace(name="FAILED"))
            async def result(self): raise RuntimeError("model failed")
        async def temporal(): return SimpleNamespace(get_workflow_handle=lambda _: Handle())
        capture_set(endpoint, "get_temporal_client", temporal)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/v1/course-qa/{wf}")
        assert response.status_code == 200
        assert response.json()["progress"] == checkpoint


def test_legacy_qa_owner_lookup_refuses_foreign_or_ambiguous_book_records():
    from b2c_api.qa_recovery import saved_qa_owner
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        wf = "b2c-qa-" + "a"*64
        def put(cid, bid, owner_course=None):
            record = {"course_id": owner_course or cid, "book_id": bid,
                      "stages": {"qa": {"workflow_id": wf, "artifact_ref": {"key": "saved"}}}}
            store.s3.put_object(Bucket=store.bucket, Key=f"{cid}/book-progress/{bid}.json", Body=json.dumps(record).encode())
        put("course", "one")
        assert saved_qa_owner(store, wf) == ("course", "one")
        put("foreign", "bad", "wrong-prefix")
        assert saved_qa_owner(store, wf) == ("course", "one")
        put("course", "two")
        assert saved_qa_owner(store, wf) is None
