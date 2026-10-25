import json
import boto3
import httpx
import pytest
from fastapi import FastAPI
from moto import mock_aws
from b2c_core import ArtifactStore
from b2c_api.saved_audio import saved_audio_router
from b2c_orchestration.audio import VERIFICATION_FILE, verification_record


# R0: exports only serve audio with a verification record (here: spoken from untranslated text).
VERIFIED = {VERIFICATION_FILE: json.dumps(verification_record(None)).encode()}


def track(store, key, identity):
    files = {"track.mp3": b"ID3" + bytes(300), **VERIFIED,
             "track.json": json.dumps({"track_id": "m1.l1-source-text",
                                      "kind": "lesson_source_text", "language": "ar"}).encode()}
    if identity is not None:
        files["source-identity.json"] = json.dumps(identity).encode()
    return store.put("course", "generate-mp3-track", key*64, files)


@pytest.mark.asyncio
async def test_library_and_download_require_both_pdf_and_parsed_fingerprints():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        identity = {"source_file_sha256": "a"*64, "source_parsed_sha256": "b"*64}
        matching = track(store, "1", identity)
        unknown = track(store, "2", None)
        foreign_pdf = track(store, "3", {**identity, "source_file_sha256": "c"*64})
        older_parse = track(store, "4", {**identity, "source_parsed_sha256": "d"*64})
        async def progress(cid, bid):
            return {"source_file_sha256": "a"*64,
                    "stages": {"ingest": {"source_parsed_sha256": "b"*64}}}
        app = FastAPI()
        app.include_router(saved_audio_router(lambda: store, progress))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            root = "/v1/courses/course/books/book/saved-audio"
            response = await client.get(root)
            assert response.status_code == 200
            result = response.json()
            assert result["kind"] == "historical_saved_audio"
            assert result["track_count"] == 1
            row = result["tracks"][0]
            assert row["artifact_key"] == matching.key
            assert row["lesson_id"] == "m1.l1"
            assert row["historical"] is True
            assert row["render_policy"] == "legacy"
            audio = await client.get(row["download_path"])
            assert audio.status_code == 200
            assert audio.content == b"ID3" + bytes(300)
            for ref in (unknown, foreign_pdf, older_parse):
                denied = await client.get(root+"/"+ref.key)
                assert denied.status_code == 404
        # Discovery is read-only and leaves every artifact's audio unchanged.
        assert store.read(matching, "track.mp3") == b"ID3" + bytes(300)


@pytest.mark.asyncio
async def test_library_refuses_a_book_without_exact_parsed_identity():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        async def progress(cid, bid): return {"source_file_sha256": "a"*64, "stages": {}}
        app = FastAPI()
        app.include_router(saved_audio_router(lambda: store, progress))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/v1/courses/course/books/book/saved-audio")
        assert result.status_code == 409


@pytest.mark.asyncio
async def test_legacy_course_audio_get_preserves_book_identity_and_download_route():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        track(store, "1", {"source_file_sha256": "a"*64, "source_parsed_sha256": "b"*64})
        track(store, "2", {"source_file_sha256": "c"*64, "source_parsed_sha256": "d"*64})
        async def progress(cid, bid):
            return {"source_file_sha256": ("a" if bid == "one" else "c")*64,
                    "stages": {"ingest": {"source_parsed_sha256": ("b" if bid == "one" else "d")*64}}}
        async def books(cid):
            return {"books": [{"book_id": "one", "title": "One"}, {"book_id": "two", "title": "Two"}]}
        app = FastAPI()
        app.include_router(saved_audio_router(lambda: store, progress, books))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/courses/course/audio")
            assert response.status_code == 200
            rows = response.json()["tracks"]
            assert len(rows) == 2
            assert {row["book_id"] for row in rows} == {"one", "two"}
            for row in rows:
                assert f'/books/{row["book_id"]}/saved-audio/' in row["download_path"]
                assert (await client.get(row["download_path"])).status_code == 200
            filtered = await client.get("/v1/courses/course/audio?book_id=one")
            assert filtered.status_code == 200
            assert filtered.json()["track_count"] == 1
