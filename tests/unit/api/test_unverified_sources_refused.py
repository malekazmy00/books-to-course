"""R0 containment (audits 07 C3, 09 C2): unverified content does not reach audio or exports.

* The MP3 request accepts a translate-book v2 assembly; a legacy translate-content translation
  is refused (409) unless the request says ``allow_unverified_legacy=true``, which then becomes
  part of the run identity and of the run input.
* Audio exports (ZIP, single track, full-book file, saved-audio library, course audio list)
  refuse or hide any audio artifact that has no verification record or is marked
  ``unverified_legacy``; an explicit ``allow_unverified_legacy=true`` download carries the mark.
* Result exports hide/refuse legacy translate-content results the same way and accept
  translate-book results.
"""
from __future__ import annotations

import io
import json
import re
import zipfile

import boto3
import httpx
import pytest
from b2c_api.app import create_app
from b2c_api.result_exports import result_exports_router
from b2c_api.saved_audio import saved_audio_router
from b2c_core import ArtifactStore, Settings
from b2c_orchestration.audio import VERIFICATION_FILE, verification_record
from fastapi import FastAPI
from moto import mock_aws

from tests.unit.api.test_audio_exports import WID, Temporal
from tests.unit.api.test_audio_workflow_identity import InputStore
from tests.unit.api.test_client_ref_verification import OUTLINE, PARSED, ref
from tests.unit.api.test_result_exports import Store as ResultStore

ARABIC = re.compile(r"[؀-ۿ]")
LEGACY = ref("translate-content", "4" * 64, sha="e" * 64)
ACCEPTED = ref("translate-book", "5" * 64, sha="9" * 64)


def _assert_legacy_refusal(response):
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert ARABIC.search(detail["message_ar"])
    assert "unverified" in detail["message_en"]
    assert detail["parameter"] == "allow_unverified_legacy"


async def _post_audio(body, monkeypatch, committed=(PARSED, OUTLINE, LEGACY, ACCEPTED)):
    import b2c_api.routers.audio as module
    seen = []
    monkeypatch.setattr(module, "validate_mp3_input_lineage", lambda store, inp: seen.append(inp))

    class Temporal:
        def __init__(self):
            self.started = []

        async def start_workflow(self, workflow, inp, **kwargs):
            self.started.append((inp, kwargs))

    temporal = Temporal()

    async def connect(_):
        return temporal
    app = create_app(settings=Settings(), artifact_store=InputStore(committed), temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/c/audio", json=body)
    return response, temporal


def _book_body(source, **extra):
    return {"source_artifact_ref": source.model_dump(mode="json"), "source_kind": "book", "language": "ar",
            "book_audio_layout": "lessons", "approved_outline_ref": OUTLINE.model_dump(mode="json"),
            "source_book_ref": PARSED.model_dump(mode="json"), **extra}


# ------------------------------------------------------------------ MP3 request
@pytest.mark.asyncio
async def test_audio_request_refuses_legacy_translation_by_default(monkeypatch):
    response, temporal = await _post_audio(_book_body(LEGACY), monkeypatch)
    _assert_legacy_refusal(response)
    assert temporal.started == []


@pytest.mark.asyncio
async def test_audio_request_refuses_legacy_book_translation_for_course_audio(monkeypatch):
    qa = ref("generate-course-qa", "3" * 64)
    body = {"source_artifact_ref": qa.model_dump(mode="json"), "source_kind": "course", "language": "ar",
            "approved_outline_ref": OUTLINE.model_dump(mode="json"), "source_book_ref": PARSED.model_dump(mode="json"),
            "source_qa_artifact_ref": qa.model_dump(mode="json"),
            "source_book_translation_ref": LEGACY.model_dump(mode="json")}
    response, temporal = await _post_audio(body, monkeypatch, committed=(PARSED, OUTLINE, LEGACY, qa))
    _assert_legacy_refusal(response)
    assert temporal.started == []


@pytest.mark.asyncio
async def test_audio_request_accepts_translate_book_assembly(monkeypatch):
    response, temporal = await _post_audio(_book_body(ACCEPTED), monkeypatch)
    assert response.status_code == 202, response.text
    inp, _ = temporal.started[0]
    assert inp.source_artifact_ref["stage"] == "translate-book"
    assert inp.allow_unverified_legacy is False


@pytest.mark.asyncio
async def test_explicit_legacy_opt_in_is_carried_into_a_distinct_run(monkeypatch):
    response, temporal = await _post_audio(_book_body(LEGACY, allow_unverified_legacy=True), monkeypatch)
    assert response.status_code == 202, response.text
    inp, kwargs = temporal.started[0]
    assert inp.allow_unverified_legacy is True
    # The opt-in is part of the run identity: it can never reuse/alias an unmarked earlier run.
    response2, temporal2 = await _post_audio(_book_body(ACCEPTED), monkeypatch)
    assert kwargs["id"] != temporal2.started[0][1]["id"]


# ------------------------------------------------------------------ audio exports
def _put_track(store, number, verification):
    files = {"track.mp3": b"ID3-audio-" + str(number).encode()}
    if verification is not None:
        files[VERIFICATION_FILE] = json.dumps(verification).encode()
    return store.put("revolution", "generate-mp3-track", str(number) * 64, files)


@pytest.fixture
def audio_env():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()

        def write(verifications):
            tracks = []
            for number, verification in enumerate(verifications, 1):
                track = _put_track(store, number, verification)
                tracks.append({"track_id": f"lesson{number}-lesson_source_text", "lesson_id": f"lesson{number}",
                               "kind": "lesson_source_text", "artifact_ref": track.model_dump(mode="json")})
            record = {"workflow_id": WID, "course_id": "revolution", "tracks": tracks, "track_count": len(tracks),
                      "lesson_order": [t["lesson_id"] for t in tracks], "updated_at": "2026-10-25T00:00:00Z"}
            store.s3.put_object(Bucket=store.bucket, Key=f"_system/audio-progress/{WID}.json",
                                Body=json.dumps(record).encode())
        store.s3.put_object(Bucket=store.bucket, Key="revolution/course-settings.json",
                            Body=json.dumps({"name": "Rev"}).encode())

        async def connect(_):
            return Temporal()
        app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
        yield store, write, app


ACCEPTED_RECORD = verification_record(ACCEPTED)
LEGACY_RECORD = verification_record(LEGACY)


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [None, LEGACY_RECORD], ids=["pre-R0-without-record", "marked-legacy"])
async def test_audio_zip_and_track_refuse_unverified_by_default(audio_env, second):
    store, write, app = audio_env
    write([ACCEPTED_RECORD, second])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        _assert_legacy_refusal(await client.get(f"/v1/courses/revolution/audio/zip?workflow_id={WID}"))
        _assert_legacy_refusal(await client.get(f"/v1/audio/{WID}/tracks/lesson2-lesson_source_text"))
        accepted = await client.get(f"/v1/audio/{WID}/tracks/lesson1-lesson_source_text")
        assert accepted.status_code == 200 and accepted.content == b"ID3-audio-1"
        listing = (await client.get("/v1/courses/revolution/audio")).json()
        listed = [t["track_id"] for w in listing["workflows"] for t in w["tracks"]]
        assert listed == ["lesson1-lesson_source_text"]
        everything = (await client.get("/v1/courses/revolution/audio?include_unverified_legacy=true")).json()
        flags = {t["track_id"]: t["unverified_legacy"] for w in everything["workflows"] for t in w["tracks"]}
        assert flags == {"lesson1-lesson_source_text": False, "lesson2-lesson_source_text": True}


@pytest.mark.asyncio
async def test_explicit_unverified_zip_is_marked(audio_env):
    store, write, app = audio_env
    write([ACCEPTED_RECORD, LEGACY_RECORD])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/courses/revolution/audio/zip?workflow_id={WID}&allow_unverified_legacy=true")
        assert response.status_code == 200, response.text
        assert response.headers["x-b2c-verification"] == "unverified_legacy"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "UNVERIFIED-LEGACY.txt" in archive.namelist()
            assert ARABIC.search(archive.read("UNVERIFIED-LEGACY.txt").decode())
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["unverified_legacy"] is True
            assert [t["unverified_legacy"] for t in manifest["tracks"]] == [False, True]
        track = await client.get(f"/v1/audio/{WID}/tracks/lesson2-lesson_source_text?allow_unverified_legacy=true")
        assert track.status_code == 200
        assert track.headers["x-b2c-verification"] == "unverified_legacy"


@pytest.mark.asyncio
async def test_verified_zip_is_marked_accepted(audio_env):
    store, write, app = audio_env
    write([ACCEPTED_RECORD, verification_record(None)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/courses/revolution/audio/zip?workflow_id={WID}")
        assert response.status_code == 200, response.text
        assert response.headers["x-b2c-verification"] == "accepted"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "UNVERIFIED-LEGACY.txt" not in archive.namelist()


@pytest.mark.asyncio
async def test_saved_audio_library_hides_and_refuses_unverified_tracks():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        identity = {"source_file_sha256": "a" * 64, "source_parsed_sha256": "b" * 64}

        def put(key, verification):
            files = {"track.mp3": b"ID3" + bytes(300), "source-identity.json": json.dumps(identity).encode(),
                     "track.json": json.dumps({"track_id": f"m1.l{key}-source-text", "kind": "lesson_source_text",
                                               "language": "ar"}).encode()}
            if verification is not None:
                files[VERIFICATION_FILE] = json.dumps(verification).encode()
            return store.put("course", "generate-mp3-track", key * 64, files)
        accepted = put("1", ACCEPTED_RECORD)
        pre_r0 = put("2", None)
        marked = put("3", LEGACY_RECORD)

        async def progress(cid, bid):
            return {"source_file_sha256": "a" * 64, "stages": {"ingest": {"source_parsed_sha256": "b" * 64}}}

        async def books(cid):
            return {"books": [{"book_id": "book", "title": "Book"}]}
        app = FastAPI()
        app.include_router(saved_audio_router(lambda: store, progress, books))
        root = "/v1/courses/course/books/book/saved-audio"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            listing = (await client.get(root)).json()
            assert [row["artifact_key"] for row in listing["tracks"]] == [accepted.key]
            assert listing["tracks"][0]["unverified_legacy"] is False
            compat = (await client.get("/v1/courses/course/audio")).json()
            assert [row["artifact_key"] for row in compat["tracks"]] == [accepted.key]
            full = (await client.get(root + "?include_unverified_legacy=true")).json()
            assert {row["artifact_key"]: row["unverified_legacy"] for row in full["tracks"]} == {
                accepted.key: False, pre_r0.key: True, marked.key: True}
            assert (await client.get(f"{root}/{accepted.key}")).status_code == 200
            for ref_ in (pre_r0, marked):
                _assert_legacy_refusal(await client.get(f"{root}/{ref_.key}"))
                allowed = await client.get(f"{root}/{ref_.key}?allow_unverified_legacy=true")
                assert allowed.status_code == 200
                assert allowed.headers["x-b2c-verification"] == "unverified_legacy"


# ------------------------------------------------------------------ result exports
@pytest.mark.asyncio
async def test_result_exports_hide_and_refuse_legacy_translations():
    store = ResultStore()
    store.add("alpha", "translate-content", "c" * 64, {"translation.json": b'{"segments":[{"text":"old"}]}'})
    store.add("alpha", "translate-book", "d" * 64,
              {"translation.json": json.dumps({"blocks": {"b:1:0": {"text": "accepted", "complete": True}}}).encode()})
    app = FastAPI()
    app.include_router(result_exports_router(lambda: store, "artifacts"))
    legacy_id, accepted_id = "translate-content:" + "c" * 64, "translate-book:" + "d" * 64
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = (await client.get("/v1/courses/alpha/results")).json()
        assert [item["result_id"] for item in listing["results"]] == [accepted_id]
        full = (await client.get("/v1/courses/alpha/results?include_unverified_legacy=true")).json()
        assert {item["result_id"]: item["unverified_legacy"] for item in full["results"]} == {
            accepted_id: False, legacy_id: True}
        _assert_legacy_refusal(await client.get(f"/v1/courses/alpha/results/{legacy_id}/file",
                                                params={"name": "translation.json"}))
        _assert_legacy_refusal(await client.get(f"/v1/courses/alpha/results/{legacy_id}/units",
                                                params={"name": "translation.json"}))
        _assert_legacy_refusal(await client.post("/v1/courses/alpha/results/export",
                                                 json={"result_ids": [legacy_id]}))
        assert (await client.get(f"/v1/courses/alpha/results/{accepted_id}/file",
                                 params={"name": "translation.json"})).status_code == 200
        default_export = await client.post("/v1/courses/alpha/results/export", json={"result_ids": []})
        assert default_export.status_code == 200
        with zipfile.ZipFile(io.BytesIO(default_export.content)) as archive:
            assert not any(name.startswith("translate-content/") for name in archive.namelist())
            assert any(name.startswith("translate-book/") for name in archive.namelist())
        allowed = await client.post("/v1/courses/alpha/results/export",
                                    json={"result_ids": [legacy_id], "allow_unverified_legacy": True})
        assert allowed.status_code == 200
        with zipfile.ZipFile(io.BytesIO(allowed.content)) as archive:
            provenance = json.loads(archive.read("translate-content/" + "c" * 64 + "/provenance.json"))
            assert provenance["unverified_legacy"] is True
            assert ARABIC.search(archive.read("translate-content/" + "c" * 64 + "/UNVERIFIED-LEGACY.txt").decode())
        file = await client.get(f"/v1/courses/alpha/results/{legacy_id}/file",
                                params={"name": "translation.json", "allow_unverified_legacy": True})
        assert file.status_code == 200 and file.headers["x-b2c-verification"] == "unverified_legacy"
