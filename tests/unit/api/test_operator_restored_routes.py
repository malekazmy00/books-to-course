import io
import json
import zipfile
import boto3
import httpx
import pytest
from moto import mock_aws
from temporalio.service import RPCError, RPCStatusCode
from b2c_api.app import create_app
from b2c_core import ArtifactStore, Settings
from b2c_api.course_library import course_library_router
from fastapi import FastAPI
from b2c_orchestration.audio import VERIFICATION_FILE, verification_record

# R0: exports only serve audio with a verification record (here: spoken from untranslated text).
VERIFIED = {VERIFICATION_FILE: json.dumps(verification_record(None)).encode()}


class GoneTemporal:
    def get_workflow_handle(self, _):
        return self
    async def describe(self):
        raise RPCError("history expired", RPCStatusCode.NOT_FOUND, b"")

@pytest.mark.asyncio
async def test_expired_audio_remains_listable_downloadable_and_course_scoped(monkeypatch):
    monkeypatch.setenv("B2C_APP_API_AUTH_REQUIRED", "false")
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        audio = b"ID3"+bytes(250)
        ref = store.put("course", "generate-mp3-track", "a"*64, {"track.mp3":audio, **VERIFIED})
        wid = "b2c-mp3-"+"b"*64
        manifest = {"course_id":"course", "workflow_id":wid, "track_count":1,
                    "tracks":[{"track_id":"l1", "lesson_id":"l1", "title":"Lesson",
                               "kind":"lesson_source_text", "artifact_ref":ref.model_dump(mode="json")}],
                    "language":"ar", "kind":"book_lessons"}
        store.s3.put_object(Bucket=store.bucket,Key="_system/audio-progress/"+wid+".json",Body=json.dumps(manifest).encode())
        store.s3.put_object(Bucket=store.bucket,Key="course/course-settings.json",Body=json.dumps({"name":"Revolution"}).encode())
        async def connect(*_, **__): return GoneTemporal()
        app = create_app(settings=Settings(),artifact_store=store,temporal_connect=connect)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
            status = await c.get("/v1/audio/"+wid)
            assert status.status_code == 200
            assert status.json()["status"] == "PARTIAL"
            assert status.json()["recovered_from_artifact"] is True
            folder = await c.get("/v1/courses/course/audio")
            assert folder.status_code == 200
            assert "tracks" in folder.json()  # Legacy response preserved.
            run = folder.json()["workflows"][0]
            assert run["workflow_id"] == wid
            assert run["tracks"][0]["display_filename"] == "Revolution1.mp3"
            download = await c.get(run["tracks"][0]["download_url"])
            assert download.status_code == 200 and download.content == audio
            archive = await c.get("/v1/courses/course/audio/zip",params={"workflow_id":wid})
            assert archive.status_code == 200
            with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
                assert z.read("Revolution1.mp3") == audio
            denied = await c.get("/v1/courses/foreign/audio/zip",params={"workflow_id":wid})
            assert denied.status_code == 404
            schema = (await c.get("/openapi.json")).json()["paths"]
            assert "post" in schema["/v1/courses/{course_id}/audio/regenerate"]
            assert "post" in schema["/v1/courses/{course_id}/results/export"]
            assert "patch" in schema["/v1/courses/{course_id}/memberships/{book_id}"]
        # No worker invocation; existing audio remains byte-for-byte.
        assert store.read(ref,"track.mp3") == audio

@pytest.mark.asyncio
async def test_membership_display_name_preserves_roles_and_source_identity():
    from tests.unit.api.test_course_library import Store
    store = Store()
    original = {"book_id":"book", "title":"Original", "role":"spine", "file_sha256":"a"*64}
    async def books(cid):return {"books":[{**original,"course_id":cid}]}
    app = FastAPI()
    app.include_router(course_library_router(lambda:store,"artifacts",books))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
        renamed = await c.patch("/v1/courses/course/memberships/book",json={"display_name":"My course book"})
        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "My course book"
        membership = (await c.get("/v1/courses/course/memberships")).json()["books"][0]
        assert membership["display_name"] == "My course book"
        assert membership["file_sha256"] == original["file_sha256"]
        assert membership["role"] == "spine"
        assert original["title"] == "Original"
