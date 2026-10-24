"""S3 object bodies must be read off the event loop."""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from b2c_api.app import create_app
from b2c_api.deps import s3_json
from b2c_core import Settings


class LoopCheckingBody:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = False

    def read(self, *args):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self.data
        raise AssertionError("S3 Body.read() ran on the event loop")

    def close(self):
        self.closed = True


class Store:
    bucket = "artifacts"

    def __init__(self, objects):
        self.s3 = self
        self.objects = objects

    def get_object(self, **kw):
        if kw["Key"] not in self.objects:
            raise KeyError(kw["Key"])
        return {"Body": LoopCheckingBody(self.objects[kw["Key"]])}

    def put_object(self, **kw):
        self.objects[kw["Key"]] = kw["Body"]

    def list_objects_v2(self, **kw):
        return {"CommonPrefixes": []}


class Temporal:
    def __init__(self, status="RUNNING"):
        self.status = status

    def get_workflow_handle(self, _):
        return self

    async def describe(self):
        return SimpleNamespace(status=SimpleNamespace(name=self.status), start_time=None)


async def test_s3_json_helper_returns_none_for_missing_objects():
    store = Store({"present.json": b'{"a": 1}'})
    assert await s3_json(store.s3, "artifacts", "present.json") == {"a": 1}
    assert await s3_json(store.s3, "artifacts", "missing.json") is None


@pytest.mark.asyncio
async def test_status_routes_read_bodies_in_threadpool():
    tid = "b2c-translate-" + "a" * 64
    qid = "b2c-qa-" + "b" * 64
    progress_key = f"c/book-progress/{hashlib.sha256(b'book').hexdigest()}.json"
    store = Store({
        f"_system/workflow-controls/{tid}.json": json.dumps({"state": "PAUSED"}).encode(),
        f"_system/translation-progress/{tid}.json": json.dumps({"completed_segments": 1,
                                                                "total_segments": 2}).encode(),
        f"_system/qa-progress/{qid}.json": json.dumps({"phase": "questions"}).encode(),
        progress_key: json.dumps({"course_id": "c", "book_id": "book", "stages": {}}).encode(),
    })

    async def connect(_):
        return Temporal()

    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        control = await client.get(f"/v1/workflows/{tid}/control")
        assert control.status_code == 200, control.text
        assert control.json()["status"] == "PAUSED"
        translation = await client.get(f"/v1/translations/{tid}")
        assert translation.status_code == 200, translation.text
        assert translation.json()["progress"]["completed_segments"] == 1
        qa = await client.get(f"/v1/course-qa/{qid}")
        assert qa.status_code == 200, qa.text
        assert qa.json()["progress"] == {"phase": "questions"}
        progress = await client.get("/v1/courses/c/books/book/progress")
        assert progress.status_code == 200, progress.text
