"""Binary downloads stream the S3 body instead of reading whole files into memory."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import boto3
import httpx
import pytest
from fastapi import FastAPI
from moto import mock_aws

from b2c_api.app import create_app
from b2c_api.result_exports import result_exports_router
from b2c_api.saved_audio import saved_audio_router
from b2c_core import ArtifactStore, Settings
from b2c_orchestration.audio import VERIFICATION_FILE, verification_record

STREAMED = {"source.pdf", "track.mp3", "course.mp3", "notes.txt"}
# R0: exports only serve audio with a verification record (here: spoken from untranslated text).
VERIFIED = {VERIFICATION_FILE: json.dumps(verification_record(None)).encode()}



class NoWholeReadStore(ArtifactStore):
    """store.read() of a streamed payload means the response buffered the file."""

    def read(self, ref, name):
        if name in STREAMED:
            raise AssertionError(f"{name} was read whole into memory")
        return super().read(ref, name)


def new_store():
    store = NoWholeReadStore(boto3.client("s3", region_name="us-east-1"))
    store.ensure_bucket()
    return store


class Temporal:
    def __init__(self, result=None):
        self.value = result

    def get_workflow_handle(self, _):
        return self

    async def describe(self):
        return SimpleNamespace(status=SimpleNamespace(name="COMPLETED"))

    async def result(self):
        return self.value


async def get(app, path, **params):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, params=params)


@pytest.mark.asyncio
async def test_whole_book_mp3_and_track_downloads_stream():
    with mock_aws():
        store = new_store()
        audio = b"ID3" + bytes(5000)
        book_ref = store.put("course", "generate-mp3", "a" * 64, {"course.mp3": audio, **VERIFIED})
        track_ref = store.put("course", "generate-mp3-track", "b" * 64, {"track.mp3": audio, **VERIFIED})
        wid = "b2c-mp3-" + "c" * 64

        async def connect(_):
            return Temporal({"kind": "book", "course_id": "course", "artifact": book_ref.model_dump(mode="json")})
        app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
        response = await get(app, f"/v1/audio/{wid}/file")
        assert response.status_code == 200, response.text
        assert response.content == audio
        assert response.headers["content-length"] == str(len(audio))

        manifest = {"course_id": "course", "workflow_id": wid, "track_count": 1,
                    "tracks": [{"track_id": "l1", "lesson_id": "l1", "kind": "lesson_source_text",
                                "artifact_ref": track_ref.model_dump(mode="json")}]}
        store.s3.put_object(Bucket=store.bucket, Key=f"_system/audio-progress/{wid}.json",
                            Body=json.dumps(manifest).encode())

        async def running(_):
            temporal = Temporal()

            async def describe():
                return SimpleNamespace(status=SimpleNamespace(name="RUNNING"))
            temporal.describe = describe
            return temporal
        app = create_app(settings=Settings(), artifact_store=store, temporal_connect=running)
        response = await get(app, f"/v1/audio/{wid}/tracks/l1")
        assert response.status_code == 200, response.text
        assert response.content == audio


@pytest.mark.asyncio
async def test_uploaded_pdf_download_streams():
    with mock_aws():
        store = new_store()
        pdf = b"%PDF-1.7\n" + bytes(4000)
        digest = hashlib.sha256(pdf).hexdigest()
        upload = store.put("course", "book-upload", "d" * 64, {
            "source.pdf": pdf, "book-upload.json": json.dumps({"book_id": "book-" + digest[:24],
                                                               "file_sha256": digest}).encode()})
        record = {"course_id": "course", "book_id": "book-" + digest[:24], "title": "T",
                  "file_sha256": digest, "workflow_id": "", "source_artifact_ref": upload.model_dump(mode="json")}
        store.put("course", "book-library", "e" * 64, {"book-library.json": json.dumps(record).encode()})

        async def connect(_):
            return Temporal()
        app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
        response = await get(app, f"/v1/courses/course/books/book-{digest[:24]}/source")
        assert response.status_code == 200, response.text
        assert response.content == pdf


@pytest.mark.asyncio
async def test_saved_audio_and_result_file_downloads_stream():
    with mock_aws():
        store = new_store()
        audio = b"ID3" + bytes(3000)
        identity = {"source_file_sha256": "a" * 64, "source_parsed_sha256": "b" * 64}
        ref = store.put("course", "generate-mp3-track", "1" * 64, {
            "track.mp3": audio, "source-identity.json": json.dumps(identity).encode(),
            "track.json": json.dumps({"track_id": "m1.l1-source-text", "kind": "lesson_source_text"}).encode(),
            **VERIFIED})

        async def progress(cid, bid):
            return {"source_file_sha256": "a" * 64, "stages": {"ingest": {"source_parsed_sha256": "b" * 64}}}
        notes = b"saved result " * 100
        # translate-content (legacy) results are refused since R0; translate-book is served.
        result = store.put("course", "translate-book", "2" * 64, {"notes.txt": notes})
        app = FastAPI()
        app.include_router(saved_audio_router(lambda: store, progress))
        app.include_router(result_exports_router(lambda: store, store.bucket))
        response = await get(app, f"/v1/courses/course/books/book/saved-audio/{ref.key}")
        assert response.status_code == 200, response.text
        assert response.content == audio
        response = await get(app, f"/v1/courses/course/results/translate-book:{result.key}/file", name="notes.txt")
        assert response.status_code == 200, response.text
        assert response.content == notes
        assert response.headers["x-artifact-sha256"] == result.sha256
