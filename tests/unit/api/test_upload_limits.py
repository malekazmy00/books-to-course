"""Oversized uploads are refused before the multipart body is spooled."""
from __future__ import annotations

import json

import httpx
import pytest

from b2c_api.app import UPLOAD_MULTIPART_ALLOWANCE, create_app
from b2c_core import Settings


class Store:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError("an oversized upload must not reach storage")
        return record


async def _connect(_):
    raise AssertionError("an oversized upload must not reach Temporal")


@pytest.mark.asyncio
async def test_declared_content_length_over_limit_is_413_without_reading_body():
    app = create_app(settings=Settings(max_upload_bytes=1024), artifact_store=Store(), temporal_connect=_connect)
    declared = 1024 + UPLOAD_MULTIPART_ALLOWANCE + 1
    received = []
    sent = []

    async def receive():
        received.append(1)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/v1/books", "raw_path": b"/v1/books", "query_string": b"",
             "root_path": "", "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"host", b"test"), (b"content-length", str(declared).encode()),
                         (b"content-type", b"multipart/form-data; boundary=x")]}
    await app(scope, receive, send)
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 413
    assert received == []


@pytest.mark.asyncio
async def test_chunked_upload_is_cut_off_once_the_cap_is_exceeded():
    store = Store()
    app = create_app(settings=Settings(max_upload_bytes=1024), artifact_store=store, temporal_connect=_connect)
    chunk = b"%PDF-" + b"0" * (256 * 1024)
    produced = []

    async def body():
        boundary = b"--x\r\n"
        yield boundary + b'Content-Disposition: form-data; name="metadata_json"\r\n\r\n'
        yield json.dumps({"course_id": "c", "title": "t", "role": "spine",
                          "rights_status": "cleared"}).encode() + b"\r\n"
        yield boundary + b'Content-Disposition: form-data; name="file"; filename="b.pdf"\r\n'
        yield b"Content-Type: application/pdf\r\n\r\n"
        for _ in range(64):  # 16 MiB if fully consumed
            produced.append(1)
            yield chunk
        yield b"\r\n--x--\r\n"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/books", content=body(),
                                     headers={"content-type": "multipart/form-data; boundary=x"})
    assert response.status_code == 413, response.text
    assert store.calls == []
    assert len(produced) < 64
