"""Temporal NOT_FOUND is a 404 and validation failures are 422, never 500."""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from temporalio.service import RPCError, RPCStatusCode

from b2c_api.app import create_app
from b2c_contracts import ArtifactRef
from b2c_core import Settings
from b2c_translation import SourceSegment

WF = "b2c-translate-" + "1" * 64
SOURCE = ArtifactRef(stage="ingest-parse-pdf", key="source", uri="s3://artifacts/c/source",
                     sha256="a" * 64, created_at=datetime.now(UTC))


class Store:
    bucket = "artifacts"

    def __init__(self):
        self.s3 = self
        self.writes = {}

    def get_object(self, **args):
        if "/translation-requests/" in args["Key"]:
            data = {"course_id": "c", "source_artifact_ref": SOURCE.model_dump(mode="json"),
                    "source_kind": "book", "source_language": "en", "target_language": "ar"}
        elif "/translation-checkpoints/" in args["Key"]:
            data = {"run_fingerprint": "fingerprint", "batch_index": 1, "source_sha256": SOURCE.sha256,
                    "source_language": "en", "target_language": "ar",
                    "completed_at": "2026-10-09T01:00:00Z",
                    "segments": [{"segment_id": "s1", "text": "correct 5"}]}
        else:
            raise KeyError(args["Key"])
        return {"Body": io.BytesIO(json.dumps(data).encode())}

    def list_objects_v2(self, **args):
        return {"Contents": [{"Key": args["Prefix"] + "fingerprint/batch-000001.json"}], "IsTruncated": False}

    def put_object(self, **kw):
        self.writes[kw["Key"]] = kw["Body"]


class Handle:
    def __init__(self, status, error=None, result_error=None):
        self.status, self.error, self.result_error = status, error, result_error

    async def describe(self):
        if self.error:
            raise self.error
        return SimpleNamespace(status=SimpleNamespace(name=self.status), raw_description=None)

    async def result(self):
        if self.result_error:
            raise self.result_error
        return {}

    async def signal(self, name):
        return None


class Temporal:
    def __init__(self, handle):
        self.handle = handle

    def get_workflow_handle(self, _):
        return self.handle


def app_for(handle, store=None):
    async def connect(_):
        return Temporal(handle)
    return create_app(settings=Settings(), artifact_store=store or Store(), temporal_connect=connect)


NOT_FOUND = RPCError("gone", RPCStatusCode.NOT_FOUND, b"")


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("POST", f"/v1/translations/{WF}/corrections", {"segment_id": "s1", "translated_text": "x 5"}),
    ("POST", f"/v1/translations/{WF}/retranslations", {"segment_id": "s1"}),
    ("POST", f"/v1/translations/{WF}/revision-selection", {"segment_id": "s1"}),
    ("GET", f"/v1/translations/{WF}", None),
    ("GET", f"/v1/workflows/{WF}/control", None),
    ("POST", f"/v1/workflows/{WF}/pause", None),
    ("POST", f"/v1/workflows/{WF}/resume", None),
])
async def test_missing_workflow_is_404(method, path, body):
    app = app_for(Handle("RUNNING", error=NOT_FOUND))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json=body)
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_temporal_outage_is_503_not_500():
    app = app_for(Handle("RUNNING", error=RPCError("down", RPCStatusCode.UNAVAILABLE, b"")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/translations/{WF}/corrections",
                                     json={"segment_id": "s1", "translated_text": "x 5"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_completed_translation_result_failure_is_503():
    app = app_for(Handle("COMPLETED", result_error=RuntimeError("history gone")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/translations/{WF}")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_rejected_saved_revision_is_422(monkeypatch):
    import b2c_api.routers.translations as router_module
    import b2c_api.services.translation_units as units_module
    monkeypatch.setattr(units_module, "translation_source_segments",
                        lambda *_: [SourceSegment(segment_id="s1", text="value 5")])
    monkeypatch.setattr(units_module, "translation_manual_overrides", lambda *_: [])

    def reject(*args, **kwargs):
        raise ValueError("numeric_token_mismatch")
    monkeypatch.setattr(router_module, "validate_manual_translation", reject)
    app = app_for(Handle("COMPLETED"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/translations/{WF}/revision-selection", json={"segment_id": "s1"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "numeric_token_mismatch"
