"""D-040: ingest v2 is reachable from the upload API only behind B2C_INGEST_V2_ENABLED (default
off). With the setting off, the upload starts exactly the workflow it started before, under the
same workflow id, so books already ingested (and translations running on their ParsedBook) see
no new key."""
from __future__ import annotations

import hashlib
import json

import httpx
from b2c_api.app import create_app
from b2c_core import Settings
from b2c_orchestration.ingest import IngestPDFInput, IngestPDFWorkflow
from b2c_orchestration.ingest_v2 import IngestPDFV2Workflow, IngestV2Input

from tests.unit.api.test_book_upload import FakeStore, FakeTemporal, _resolved, post_upload

DATA = b"%PDF-1.7\ncontent"


async def upload(settings: Settings, **kw):
    store, temporal = FakeStore(), FakeTemporal()
    app = create_app(settings=settings, artifact_store=store, temporal_connect=lambda s: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client, data=DATA, **kw)
    assert response.status_code == 202, response.text
    library = [json.loads(c[3]["book-library.json"]) for c in store.put_calls if c[1] == "book-library"]
    return response.json(), temporal.starts[0], library[0]


def v1_workflow_id(book_id: str) -> str:
    """The identity formula of the upload route before D-040 (must not change while v2 is off)."""
    identity = {"course_id": "course-1", "book_id": book_id, "file_sha256": hashlib.sha256(DATA).hexdigest(),
                "title": "Probability", "role": "spine", "rights_status": "internal_test_only",
                "ocr_language": "en", "ocr_engine": "rapidocr", "supplementary_book_ids": ["book-extra"],
                "cache_bust": ""}
    return "b2c-ingest-" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False)
                                          .encode("utf-8")).hexdigest()


def test_the_setting_is_off_by_default(monkeypatch):
    monkeypatch.delenv("B2C_INGEST_V2_ENABLED", raising=False)
    assert Settings().ingest_v2_enabled is False


async def test_default_upload_starts_the_v1_workflow_with_its_old_id(monkeypatch):
    monkeypatch.delenv("B2C_OCR_ENGINE", raising=False)
    body, (workflow, inp, _), record = await upload(Settings())
    assert workflow == IngestPDFWorkflow.run and isinstance(inp, IngestPDFInput)
    assert body["workflow_id"] == v1_workflow_id(body["book_id"])
    assert "ingest_pipeline" not in record


async def test_enabled_upload_starts_the_v2_workflow_under_a_distinct_id(monkeypatch):
    monkeypatch.delenv("B2C_OCR_ENGINE", raising=False)
    body, (workflow, inp, kwargs), record = await upload(Settings(ingest_v2_enabled=True))
    assert workflow == IngestPDFV2Workflow.run and isinstance(inp, IngestV2Input)
    assert inp.upload["book_id"] == body["book_id"] and inp.supplementary_book_ids == ["book-extra"]
    assert kwargs["task_queue"] == "light"
    assert body["workflow_id"] != v1_workflow_id(body["book_id"])
    assert record["ingest_pipeline"] == "v2" and record["workflow_id"] == body["workflow_id"]


async def test_arabic_books_stay_on_v1_even_when_enabled(monkeypatch):
    monkeypatch.delenv("B2C_OCR_ENGINE", raising=False)
    _, (workflow, inp, _), record = await upload(Settings(ingest_v2_enabled=True), ocr_language="ar")
    assert workflow == IngestPDFWorkflow.run and isinstance(inp, IngestPDFInput)
    assert "ingest_pipeline" not in record
