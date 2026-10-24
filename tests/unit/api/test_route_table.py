"""Route-table integrity: no handler may silently shadow another."""
from __future__ import annotations

import hashlib
import io
import json
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.routing import APIRoute

from b2c_api.app import create_app
from b2c_core import Settings
from b2c_orchestration.audio import GenerateMP3Input


def _flatten(routes, prefix=""):
    # FastAPI >= 0.14x keeps included routers as lazy branches; walk them so a
    # router-provided route cannot hide behind (or be hidden by) an app route.
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            yield from _flatten(route.original_router.routes,
                                prefix + getattr(route.include_context, "prefix", ""))


def _routes(app):
    pairs = []
    for path, route in _flatten(app.router.routes):
        pairs.extend((method, path) for method in route.methods)
    return pairs


def test_create_app_has_no_duplicate_method_and_path():
    app = create_app(settings=Settings(), artifact_store=object())
    duplicates = [pair for pair, count in Counter(_routes(app)).items() if count > 1]
    assert duplicates == []


def test_course_library_and_release_catalog_are_both_reachable():
    app = create_app(settings=Settings(), artifact_store=object())
    pairs = set(_routes(app))
    assert ("GET", "/v1/courses") in pairs
    assert ("GET", "/v1/released-courses") in pairs
    endpoints = {path: route.endpoint.__name__ for path, route in _flatten(app.router.routes)
                 if "GET" in route.methods}
    assert endpoints["/v1/released-courses"] == "list_released_courses"
    assert endpoints["/v1/courses"] == "courses"


class Store:
    bucket = "artifacts"

    def __init__(self):
        self.s3 = self
        self.objects = {}

    def get_object(self, **kw):
        if kw["Key"] not in self.objects:
            raise KeyError(kw["Key"])
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}

    def put_object(self, **kw):
        self.objects[kw["Key"]] = kw["Body"]

    def list_objects_v2(self, **kw):
        return {"CommonPrefixes": []}


@pytest.mark.asyncio
async def test_failed_stage_resume_reaches_stage_retry_through_shared_path():
    wid = "b2c-mp3-" + "a" * 64
    inp = GenerateMP3Input(course_id="c", source_kind="book", language="ar", source_artifact_ref={
        "stage": "translate-content", "uri": "s3://artifacts/c/t", "sha256": "a" * 64, "key": "k",
        "created_at": datetime.now(UTC).isoformat()})
    attrs = SimpleNamespace(workflow_type=SimpleNamespace(name="GenerateMP3Workflow"),
                            task_queue=SimpleNamespace(name="audio"),
                            input=SimpleNamespace(payloads=["saved"]))
    event = SimpleNamespace(workflow_execution_started_event_attributes=attrs, HasField=lambda _: True)
    started = []

    class Converter:
        async def decode(self, payloads, types=None):
            return [inp]

    class Handle:
        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="FAILED"))

        async def fetch_history(self):
            return SimpleNamespace(events=[event])

    class Temporal:
        data_converter = Converter()

        def get_workflow_handle(self, _):
            return Handle()

        async def start_workflow(self, run, arg, **kwargs):
            started.append((arg, kwargs))

    store = Store()
    key = "c/book-progress/" + hashlib.sha256(b"b").hexdigest() + ".json"
    store.objects[key] = json.dumps({"course_id": "c", "book_id": "b",
                                     "stages": {"audio": {"workflow_id": wid}}}).encode()

    async def connect(_):
        return Temporal()

    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/workflows/{wid}/resume", json={"book_id": "b"})
        missing_book = await client.post(f"/v1/workflows/{wid}/resume")
    assert response.status_code == 202, response.text
    assert response.json()["resumed_from"] == wid
    assert started and started[0][0] is inp
    assert missing_book.status_code == 422
