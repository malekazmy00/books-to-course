import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from b2c_api.app import create_app
from b2c_contracts import ArtifactRef
from b2c_core import Settings
from b2c_translation import SourceSegment

WF = "b2c-translate-" + "1"*64
SOURCE = ArtifactRef(stage="ingest-parse-pdf", key="source", uri="s3://artifacts/c/source",
                     sha256="a"*64, created_at=datetime.now(UTC))
RESULT = ArtifactRef(stage="translate-content", key="result", uri="s3://artifacts/c/result",
                     sha256="b"*64, created_at=datetime.now(UTC))

class Store:
    bucket = "artifacts"
    def __init__(self):
        self.s3 = self
    def get_object(self, **args):
        if "/translation-requests/" in args["Key"]:
            data = {"course_id":"c", "source_artifact_ref": SOURCE.model_dump(mode="json"),
                    "source_kind":"book", "source_language":"en", "target_language":"ar"}
        else:
            data = {"run_fingerprint":"fingerprint", "batch_index":1, "source_sha256":SOURCE.sha256,
                    "source_language":"en", "target_language":"ar", "completed_at":"2026-10-09T01:00:00Z",
                    "segments":[{"segment_id":"s1", "text":"correct 5"}]}
        return {"Body":io.BytesIO(json.dumps(data).encode())}
    def list_objects_v2(self, **args):
        return {"Contents":[{"Key":args["Prefix"]+"fingerprint/batch-000001.json"}], "IsTruncated":False}
    def read(self, ref, filename):
        assert ref == RESULT and filename == "translation.json"
        return json.dumps({"status":"partial", "segments":[{"segment_id":"s1", "text":"correct 5"}],
                           "missing_segment_ids":["s0"]}).encode()

class Handle:
    async def describe(self):
        return SimpleNamespace(status=SimpleNamespace(name="COMPLETED"))
    async def result(self):
        return {"course_id":"c", "status":"partial", "artifact":RESULT.model_dump(mode="json"),
                "segment_count":1, "total_segments":2, "failed_segment_ids":["s0"],
                "missing_segment_ids":["s0"], "retry_segment_ids":["s0"]}
class Temporal:
    def get_workflow_handle(self, workflow):
        assert workflow == WF
        return Handle()

@pytest.mark.asyncio
async def test_completed_temporal_partial_result_exposes_gap_artifact_and_resume(monkeypatch):
    import importlib
    mod = importlib.import_module("b2c_api.services.translation_units")
    monkeypatch.setattr(mod, "translation_source_segments", lambda *_:[
        SourceSegment(segment_id="s0", text="value 5"), SourceSegment(segment_id="s1", text="value 5")])
    monkeypatch.setattr(mod, "translation_manual_overrides", lambda *_:[])
    async def connect(_): return Temporal()
    app = create_app(settings=Settings(), artifact_store=Store(), temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/translations/{WF}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "COMPLETED" and body["result_status"] == "partial"
    assert body["progress"]["progress_state"] == "partial"
    assert body["progress"]["completion_percent"] == 50
    assert body["progress"]["successful_segments"] == 1
    assert body["progress"]["failed_segment_ids"] == ["s0"]
    assert body["progress"]["missing_segment_ids"] == body["progress"]["retry_segment_ids"] == ["s0"]
    assert body["translation"]["status"] == "partial"
    assert body["resume_request"]["source_artifact_ref"] == SOURCE.model_dump(mode="json")
    assert "resume_unavailable_reason" not in body

@pytest.mark.asyncio
@pytest.mark.parametrize("activity_state,expected_count,expected_state,heartbeat", [
    (1, 1, "queued", None),
    (2, 0, "no_recent_checkpoint", None),
    (2, 0, "active", datetime.now(UTC)),
])
async def test_stale_resumed_progress_reconciles_checkpoint_only_when_activity_not_started(monkeypatch, activity_state, expected_count, expected_state, heartbeat):
    import importlib
    mod = importlib.import_module("b2c_api.services.translation_units")
    parent = "b2c-translate-" + "2"*64
    class ResumedStore(Store):
        def get_object(self, **args):
            if "/translation-requests/" in args["Key"]:
                response = super().get_object(**args)
                data = json.loads(response["Body"].read())
                data["resume_from_workflow_id"] = parent
            elif "/translation-progress/" in args["Key"]:
                data = {"completed_segments":0, "total_segments":2,
                        "last_progress_at":"2020-01-01T00:00:00Z",
                        "failed_segment_ids":["s0"], "current_segment_ids":[]}
            else:
                return super().get_object(**args)
            return {"Body":io.BytesIO(json.dumps(data).encode())}
    class RunningHandle(Handle):
        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="RUNNING"),
                raw_description=SimpleNamespace(pending_activities=[SimpleNamespace(
                    state=activity_state, last_heartbeat_time=heartbeat)]))
    class RunningTemporal(Temporal):
        def get_workflow_handle(self, workflow):return RunningHandle()
    monkeypatch.setattr(mod,"translation_source_segments",lambda *_:[
        SourceSegment(segment_id="s0",text="value 5"),SourceSegment(segment_id="s1",text="value 5")])
    monkeypatch.setattr(mod,"translation_manual_overrides",lambda *_:[])
    async def connect(_):return RunningTemporal()
    app=create_app(settings=Settings(),artifact_store=ResumedStore(),temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get(f"/v1/translations/{WF}")
    assert response.status_code==200,response.text
    body=response.json()
    assert body["progress"]["completed_segments"]==expected_count
    assert body["progress"]["progress_state"]==expected_state


@pytest.mark.asyncio
async def test_running_translation_counts_failed_units_waiting_for_retry_not_only_deferred_batches(monkeypatch):

    class ActiveStore(Store):
        def get_object(self, **args):
            if "/translation-progress/" in args["Key"]:
                data = {
                    "completed_segments": 10,
                    "total_segments": 20,
                    "failed_segment_ids": ["s1", "s2", "s3"],
                    "current_segment_ids": ["s3"],
                    "target_language": "ar",
                    # The active retry loop removes its current batch from this
                    # transient list; the unresolved per-segment failures remain.
                    "deferred_batches": [],
                    "last_progress_at": datetime.now(UTC).isoformat(),
                }
                return {"Body": io.BytesIO(json.dumps(data).encode())}
            return super().get_object(**args)

    class RunningHandle(Handle):
        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="RUNNING"),
                raw_description=SimpleNamespace(pending_activities=[]))

    class RunningTemporal(Temporal):
        def get_workflow_handle(self, workflow):
            assert workflow == WF
            return RunningHandle()

    async def connect(_):
        return RunningTemporal()

    app = create_app(settings=Settings(), artifact_store=ActiveStore(), temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/translations/{WF}")
    assert response.status_code == 200, response.text
    progress = response.json()["progress"]
    assert progress["active_segments"] == 1
    assert progress["failed_segments"] == 3
    assert progress["deferred_units_count"] == 2
    assert progress["arabic_correction"]["status"] == "waiting_for_translation"
    assert progress["arabic_correction"]["total_segments"] == 20
