"""Repeated POSTs return the existing run instead of starting duplicates."""
from __future__ import annotations

import pytest
import httpx
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from b2c_api.app import create_app
from b2c_core import Settings
from b2c_translation import SourceSegment

from tests.unit.api.test_book_upload import FakeStore, FakeTemporal, post_upload, _resolved
from tests.unit.api.test_temporal_error_mapping import WF, Handle, Store

FAILED_ONLY = WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY


@pytest.mark.asyncio
async def test_upload_start_rejects_reuse_after_a_successful_run():
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=FakeStore(),
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await post_upload(client)).status_code == 202
    assert temporal.starts[0][2]["id_reuse_policy"] == FAILED_ONLY


class StartingTemporal:
    def __init__(self):
        self.started = {}
        self.handle = Handle("COMPLETED")

    def get_workflow_handle(self, _):
        return self.handle

    async def start_workflow(self, run, inp, *, id, task_queue, **kwargs):
        if id in self.started:
            raise WorkflowAlreadyStartedError(id, "RetranslateSegmentWorkflow")
        self.started[id] = (inp, kwargs)


@pytest.mark.asyncio
async def test_retranslation_candidate_id_is_deterministic_and_restart_safe(monkeypatch):
    import b2c_api.app as module
    monkeypatch.setattr(module, "translation_source_segments",
                        lambda *_: [SourceSegment(segment_id="s1", text="value 5")])
    monkeypatch.setattr(module, "translation_manual_overrides", lambda *_: [])
    temporal = StartingTemporal()

    async def connect(_):
        return temporal
    app = create_app(settings=Settings(), artifact_store=Store(), temporal_connect=connect)
    path = f"/v1/translations/{WF}/retranslations"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(path, json={"segment_id": "s1"})
        again = await client.post(path, json={"segment_id": "s1"})
        other = await client.post(path, json={"segment_id": "s1", "request_nonce": "second-try"})
    assert first.status_code == again.status_code == other.status_code == 202, first.text
    assert first.json()["candidate_workflow_id"] == again.json()["candidate_workflow_id"]
    assert other.json()["candidate_workflow_id"] != first.json()["candidate_workflow_id"]
    assert len(temporal.started) == 2
    assert all(kwargs["id_reuse_policy"] == FAILED_ONLY for _, kwargs in temporal.started.values())
