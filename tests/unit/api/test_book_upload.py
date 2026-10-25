from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from b2c_api.app import create_app, _deduplicate_library_books
from b2c_contracts import (
    SCHEMA_VERSION,
    ArtifactRef,
    Block,
    CourseOutline,
    Lesson,
    LessonSpan,
    Module,
    PageRef,
    ParsedBook,
)
from b2c_core import Settings


class FakeStore:
    bucket = "artifacts"

    def __init__(self, committed=()):
        self.ensure_calls = 0
        self.put_calls = []
        self.s3 = FakeProgressS3()
        # Committed manifests, keyed like ArtifactStore.exists().
        self.committed = {(ref.uri.split("/")[3], ref.stage, ref.key): ref for ref in committed}

    def exists(self, course_id, stage, key):
        return self.committed.get((course_id, stage, key))

    def ensure_bucket(self):
        self.ensure_calls += 1

    def put(self, course_id, stage, key, files):
        content = {name: data for name, data in files.items()}
        self.put_calls.append((course_id, stage, key, content))
        return SimpleNamespace(
            schema_version="1.0.0", stage=stage, key=key,
            uri=f"s3://artifacts/{course_id}/{stage}/{key}/", sha256="f" * 64,
            created_at=datetime.now(UTC).isoformat(),
            model_dump=lambda **kwargs: {
                "schema_version": "1.0.0", "stage": stage, "key": key,
                "uri": f"s3://artifacts/{course_id}/{stage}/{key}/",
                "sha256": "f" * 64, "created_at": datetime.now(UTC).isoformat(),
            }
        )

    def put_stream(self, course_id, stage, key, files, file_hashes):
        content = {name: stream.read() for name, stream in files.items()}
        self.put_calls.append((course_id, stage, key, content))
        return SimpleNamespace(
            schema_version="1.0.0", stage=stage, key=key,
            uri=f"s3://artifacts/{course_id}/{stage}/{key}/", sha256="a" * 64,
            created_at=datetime.now(UTC).isoformat(),
            model_dump=lambda **kwargs: {
                "schema_version": "1.0.0", "stage": stage, "key": key,
                "uri": f"s3://artifacts/{course_id}/{stage}/{key}/",
                "sha256": "a" * 64, "created_at": datetime.now(UTC).isoformat(),
            }
        )


class FakeTemporal:
    def __init__(self):
        self.starts = []
        self.described = None
        self.status_name = "RUNNING"
        self.result_value = None

    async def start_workflow(self, workflow, inp, **kwargs):
        self.starts.append((workflow, inp, kwargs))

    def get_workflow_handle(self, workflow_id):
        async def describe():
            return SimpleNamespace(status=SimpleNamespace(name=self.status_name), start_time=None, close_time=None)
        async def result():
            return self.result_value
        return SimpleNamespace(describe=describe, result=result)


class FakeNotFoundError(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeProgressS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise FakeNotFoundError(Key)
        return {"Body": SimpleNamespace(read=lambda: self.objects[Key])}

    def list_objects_v2(self, *, Bucket, Prefix, Delimiter=None):
        return {"CommonPrefixes": []}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body


async def post_upload(client, data=b"%PDF-1.7\ncontent", ocr_language="en", force_reprocess=False,
                      content_type="application/pdf"):
    metadata = {
        "course_id": "course-1", "book_id": "book-main", "title": "Probability",
        "role": "spine", "rights_status": "internal_test_only",
        "supplementary_book_ids": ["book-extra"], "ocr_language": ocr_language,
        "force_reprocess": force_reprocess,
    }
    return await client.post("/v1/books", data={"metadata_json": json.dumps(metadata)},
                             files={"file": ("book.pdf", data, content_type)})


async def test_upload_persists_source_and_starts_temporal_workflow():
    store = FakeStore()
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client)

    assert response.status_code == 202
    body = response.json()
    assert body["course_id"] == "course-1"
    import hashlib
    assert body["book_id"] == "book-" + hashlib.sha256(b"%PDF-1.7\ncontent").hexdigest()[:24]
    assert temporal.starts[0][1].upload["book_id"] == body["book_id"]
    assert body["status_url"] == f"/v1/ingestions/{body['workflow_id']}"
    assert store.ensure_calls == 1
    assert store.put_calls[0][3]["source.pdf"].startswith(b"%PDF-")
    assert len(temporal.starts) == 1
    assert temporal.starts[0][2]["task_queue"] == "light"
    assert temporal.starts[0][1].supplementary_book_ids == ["book-extra"]
    assert temporal.starts[0][1].upload["ocr_language"] == "en"


async def test_upload_rejects_non_pdf():
    store = FakeStore()
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client, b"not a pdf")
    assert response.status_code == 415
    assert store.put_calls == []
    assert temporal.starts == []


async def test_upload_rejects_non_pdf_declared_mime_before_persisting():
    store = FakeStore()
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client, content_type="image/png")
    assert response.status_code == 415
    assert store.put_calls == []
    assert temporal.starts == []


async def test_upload_rejects_files_over_configured_limit():
    store = FakeStore()
    temporal = FakeTemporal()
    app = create_app(settings=Settings(max_upload_bytes=8), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client, b"%PDF-1.7\nthis is too big")
    assert response.status_code == 413
    assert store.put_calls == []
    assert temporal.starts == []


async def test_ingestion_status_exposes_temporal_state():
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=FakeStore(),
                     temporal_connect=lambda settings: _resolved(temporal))
    workflow_id = "b2c-ingest-" + "1" * 64
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/ingestions/{workflow_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"


async def _resolved(value):
    return value


async def test_repeated_upload_uses_same_workflow_and_artifact_keys():
    store = FakeStore()
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await post_upload(client)
        second = await post_upload(client)
    assert first.status_code == second.status_code == 202
    assert first.json()["workflow_id"] == second.json()["workflow_id"]
    uploads = [call for call in store.put_calls if call[1] == "book-upload"]
    library = [call for call in store.put_calls if call[1] == "book-library"]
    assert len(uploads) == len(library) == 2
    assert uploads[0][2] == uploads[1][2]
    first_record = json.loads(library[0][3]["book-library.json"])
    second_record = json.loads(library[1][3]["book-library.json"])
    assert first_record["workflow_id"] == second_record["workflow_id"]
    assert json.loads(library[0][3]["book-library.json"])["workflow_id"] == first.json()["workflow_id"]


async def test_temporal_failure_keeps_uploaded_source_for_retry():
    async def unavailable(settings):
        raise RuntimeError("Temporal down")

    store = FakeStore()
    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=unavailable)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client)
    assert response.status_code == 503
    assert "source artifact was retained" in response.json()["detail"]["message"]
    assert len(store.put_calls) == 1


async def test_outline_approval_is_blocked_until_ingestion_completes():
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=FakeStore(),
                     temporal_connect=lambda settings: _resolved(temporal))
    workflow_id = "b2c-ingest-" + "1" * 64
    review = {
        "reviewer_id": "local-owner",
        "outline": {
            "course_id": "course-1", "spine_book_id": "book-main",
            "supplementary_book_ids": [], "requires_review": True,
            "modules": [{
                "module_id": "m1", "title": "Chapter 1", "order": 1,
                "lessons": [{
                    "lesson_id": "m1.l1", "title": "Lesson 1", "order": 1,
                    "spine_span": {"start_block": "book-main:1:0",
                                   "end_block": "book-main:1:0", "pages": [1, 1]},
                    "provenance": "headings", "prior_lessons": [],
                }],
            }],
        },
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{workflow_id}/outline/approve", json=review)
    assert response.status_code == 409


async def test_upload_preserves_arabic_ocr_language():
    store = FakeStore()
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client, ocr_language="ar")
    assert response.status_code == 202
    assert temporal.starts[0][1].upload["ocr_language"] == "ar"


async def test_upload_rejects_unsupported_ocr_language():
    app = create_app(settings=Settings(), artifact_store=FakeStore(),
                     temporal_connect=lambda settings: _resolved(FakeTemporal()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client, ocr_language="zh")
    assert response.status_code == 422


async def test_completed_outline_is_approved_and_persisted():
    store = FakeStore()
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    now = datetime.now(UTC)
    draft_ref = ArtifactRef(stage="build-outline", key="a" * 64,
                            uri="s3://artifacts/course-1/build-outline/draft/",
                            sha256="b" * 64, created_at=now)
    parsed_ref = ArtifactRef(stage="ingest-parse-pdf", key="c" * 64,
                             uri="s3://artifacts/course-1/ingest-parse-pdf/parsed/",
                             sha256="d" * 64, created_at=now)
    parsed = ParsedBook(
        book_id="book-main", title="Probability", role="spine", page_count=1,
        toc_source="pdf_outline",
        blocks=[Block(block_id="book-main:1:0", type="heading", level=1,
                      text="Chapter 1", refs=[PageRef(book_id="book-main", page_no=1)],
                      section_path=["Chapter 1"], ocr=False)],
        parser={"name": "fixture", "version": "1", "options_hash": "e" * 64},
    )
    outline = CourseOutline(
        course_id="course-1", spine_book_id="book-main",
        supplementary_book_ids=[], requires_review=True,
        modules=[Module(
            module_id="m1", title="Chapter 1", order=1,
            lessons=[Lesson(
                lesson_id="m1.l1", title="Lesson 1", order=1,
                spine_span=LessonSpan(start_block="book-main:1:0",
                                      end_block="book-main:1:0", pages=[1, 1]),
                provenance="headings", prior_lessons=[],
            )],
        )],
    )
    store.read = lambda ref, name: outline.model_dump_json().encode() if name == "course-outline.json" else parsed.model_dump_json().encode()
    temporal.result_value = {
        "outline": {"ref": draft_ref.model_dump(mode="json")},
        "parsed_book": {"ref": parsed_ref.model_dump(mode="json")},
    }
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    review = {"reviewer_id": "reviewer-1", "outline": outline.model_dump(mode="json")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{'b2c-ingest-' + '1' * 64}/outline/approve", json=review)
    assert response.status_code == 201
    assert response.json()["reviewer_id"] == "reviewer-1"
    assert response.json()["index_workflow_id"].startswith("b2c-index-")
    assert len(temporal.starts) == 1
    from b2c_orchestration.indexing import (
        IndexApprovedBookInput,
        IndexApprovedBookWorkflow,
    )
    workflow, index_input, options = temporal.starts[0]
    assert workflow == IndexApprovedBookWorkflow.run
    assert isinstance(index_input, IndexApprovedBookInput)
    assert index_input.course_id == "course-1" and index_input.supplementary_parsed_refs == {}
    assert options["task_queue"] == "light"
    assert store.put_calls[-1][1] == "approve-outline"
    assert "approved-outline.json" in store.put_calls[-1][3]
    approved_payload = json.loads(store.put_calls[-1][3]["approved-outline.json"])
    assert approved_payload["spine_parsed_ref"]["sha256"] == parsed_ref.sha256
    assert approved_payload["supplementary_parsed_refs"] == {}


async def test_force_reprocess_creates_new_workflow_and_bypasses_cached_generation():
    store = FakeStore()
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await post_upload(client)
        forced = await post_upload(client, force_reprocess=True)
    assert first.status_code == forced.status_code == 202
    assert first.json()["workflow_id"] != forced.json()["workflow_id"]
    assert forced.json()["force_reprocess"] is True
    assert temporal.starts[0][1].cache_bust is None
    assert temporal.starts[1][1].cache_bust



async def test_course_qa_generation_requires_matching_completed_index_and_queues_heavy_work():
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    approved_ref = ArtifactRef(stage="approve-outline", key="a" * 64,
        uri="s3://artifacts/course-1/approve-outline/" + "a" * 64 + "/",
        sha256="b" * 64, created_at=datetime.now(UTC))
    temporal.result_value = {"course_id": "course-1", "outline_sha256": approved_ref.sha256}
    app = create_app(settings=Settings(), artifact_store=FakeStore([approved_ref]),
        temporal_connect=lambda settings: _resolved(temporal))
    payload = {"approved_outline_ref": approved_ref.model_dump(mode="json"),
        "index_workflow_id": "b2c-index-" + "1" * 64,
        "lesson_question_count": 2, "group_question_count": 1, "course_question_count": 3,
        "max_tokens": 2048}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/course-1/qa/generate", json=payload)
    assert response.status_code == 202
    assert response.json()["status_url"].startswith("/v1/course-qa/b2c-qa-")
    assert temporal.starts[0][2]["task_queue"] == "heavy"
    assert temporal.starts[0][1].approved_outline_ref["sha256"] == approved_ref.sha256
    assert temporal.starts[0][1].max_tokens == 2048
    assert temporal.starts[0][1].retrieval_mode == "lexical"
    assert temporal.starts[0][1].retrieval_top_k == 20


async def test_course_qa_generation_rejects_index_for_another_approved_outline():
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    approved_ref = ArtifactRef(stage="approve-outline", key="a" * 64,
        uri="s3://artifacts/course-1/approve-outline/" + "a" * 64 + "/",
        sha256="b" * 64, created_at=datetime.now(UTC))
    temporal.result_value = {"course_id": "course-1", "outline_sha256": "c" * 64}
    app = create_app(settings=Settings(), artifact_store=FakeStore([approved_ref]),
        temporal_connect=lambda settings: _resolved(temporal))
    payload = {"approved_outline_ref": approved_ref.model_dump(mode="json"),
        "index_workflow_id": "b2c-index-" + "1" * 64}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/course-1/qa/generate", json=payload)
    assert response.status_code == 422
    assert temporal.starts == []



async def test_course_qa_status_returns_persisted_result_only_after_completion():
    store = FakeStore()
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    result_ref = ArtifactRef(stage="generate-course-qa", key="c" * 64,
        uri="s3://artifacts/course-1/generate-course-qa/" + "c" * 64 + "/",
        sha256="d" * 64, created_at=datetime.now(UTC))
    temporal.result_value = {"course_id": "course-1", "status": "final",
        "artifact": result_ref.model_dump(mode="json")}
    store.read = lambda ref, name: json.dumps({"status": "final", "scopes": []}).encode()
    app = create_app(settings=Settings(), artifact_store=store,
        temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/course-qa/" + "b2c-qa-" + "1" * 64)
    assert response.status_code == 200
    assert response.json()["course_qa"] == {"status": "final", "scopes": []}


async def test_course_qa_generation_waits_for_completed_index():
    temporal = FakeTemporal()
    temporal.status_name = "RUNNING"
    approved_ref = ArtifactRef(stage="approve-outline", key="a" * 64,
        uri="s3://artifacts/course-1/approve-outline/" + "a" * 64 + "/",
        sha256="b" * 64, created_at=datetime.now(UTC))
    app = create_app(settings=Settings(), artifact_store=FakeStore([approved_ref]),
        temporal_connect=lambda settings: _resolved(temporal))
    payload = {"approved_outline_ref": approved_ref.model_dump(mode="json"),
        "index_workflow_id": "b2c-index-" + "1" * 64}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/course-1/qa/generate", json=payload)
    assert response.status_code == 409
    assert temporal.starts == []

async def test_system_structural_review_approves_with_machine_provenance_only(monkeypatch):
    # System approval is an explicit opt-in since R0 (audit 02 P3).
    monkeypatch.setenv("B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL", "true")
    store = FakeStore()
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    now = datetime.now(UTC)
    draft_ref = ArtifactRef(stage="build-outline", key="a" * 64,
        uri="s3://artifacts/course-1/build-outline/draft/", sha256="b" * 64, created_at=now)
    parsed_ref = ArtifactRef(stage="ingest-parse-pdf", key="c" * 64,
        uri="s3://artifacts/course-1/ingest-parse-pdf/parsed/", sha256="d" * 64, created_at=now)
    parsed = ParsedBook(book_id="book-main", title="Probability", role="spine", page_count=1,
        toc_source="pdf_outline", blocks=[
            Block(block_id="book-main:1:0", type="heading", level=1, text="Lesson 1",
                  refs=[PageRef(book_id="book-main", page_no=1)], section_path=["Lesson 1"], ocr=False),
            Block(block_id="book-main:1:1", type="paragraph", text="A fair coin has two outcomes.",
                  refs=[PageRef(book_id="book-main", page_no=1)], section_path=["Lesson 1"], ocr=False),
        ], parser={"name":"fixture", "version":"1", "options_hash":"e"*64})
    outline = CourseOutline(course_id="course-1", spine_book_id="book-main", supplementary_book_ids=[],
        requires_review=True, modules=[Module(module_id="m1", title="Lesson 1", order=1, lessons=[
            Lesson(lesson_id="m1.l1", title="Lesson 1", order=1,
                spine_span=LessonSpan(start_block="book-main:1:0", end_block="book-main:1:1", pages=(1,1)),
                provenance="pdf_outline", prior_lessons=[])
        ])])
    store.read = lambda ref, name: outline.model_dump_json().encode() if name == "course-outline.json" else parsed.model_dump_json().encode()
    temporal.result_value = {"outline":{"ref":draft_ref.model_dump(mode="json")},
                             "parsed_book":{"ref":parsed_ref.model_dump(mode="json")}}
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    review = {"review_type":"system", "outline":outline.model_dump(mode="json")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{'b2c-ingest-' + '1'*64}/outline/approve", json=review)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["review_type"] == "system" and body["reviewer_id"] is None
    provenance = body["approval_provenance"]
    assert provenance["review_type"] == "system" and provenance["reviewer_id"] is None
    assert provenance["system_id"] == "b2c-outline-structural-review"
    assert len(provenance["evidence_sha256"]) == 64
    approved = json.loads(store.put_calls[-1][3]["approved-outline.json"])
    assert approved["reviewer_id"] is None
    assert approved["outline"]["requires_review"] is False
    assert approved["outline"]["schema_version"] == SCHEMA_VERSION


async def test_system_review_rejects_forged_human_reviewer_id():
    app = create_app(settings=Settings(), artifact_store=FakeStore(),
                     temporal_connect=lambda settings: _resolved(FakeTemporal()))
    payload = {"review_type":"system", "reviewer_id":"pretend-human", "outline":{
        "course_id":"course-1", "spine_book_id":"book-main", "supplementary_book_ids":[],
        "requires_review":True, "modules":[{"module_id":"m1", "title":"Lesson 1", "order":1,
        "lessons":[{"lesson_id":"m1.l1", "title":"Lesson 1", "order":1,
        "spine_span":{"start_block":"book-main:1:0", "end_block":"book-main:1:0", "pages":[1,1]},
        "provenance":"pdf_outline", "prior_lessons":[]}]}]}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{'b2c-ingest-' + '1'*64}/outline/approve", json=payload)
    assert response.status_code == 422

async def test_course_qa_hybrid_requires_embedding_endpoint_before_queueing():
    temporal = FakeTemporal()
    app = create_app(settings=Settings(retrieval_mode="hybrid"), artifact_store=FakeStore(),
        temporal_connect=lambda settings: _resolved(temporal))
    approved_ref = ArtifactRef(stage="approve-outline", key="a" * 64,
        uri="s3://artifacts/course-1/approve-outline/" + "a" * 64 + "/",
        sha256="b" * 64, created_at=datetime.now(UTC))
    payload = {"approved_outline_ref": approved_ref.model_dump(mode="json"),
        "index_workflow_id": "b2c-index-" + "1" * 64}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/course-1/qa/generate", json=payload)
    assert response.status_code == 503
    assert temporal.starts == []


async def test_course_qa_hybrid_configuration_flows_to_temporal_input():
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    approved_ref = ArtifactRef(stage="approve-outline", key="a" * 64,
        uri="s3://artifacts/course-1/approve-outline/" + "a" * 64 + "/",
        sha256="b" * 64, created_at=datetime.now(UTC))
    temporal.result_value = {"course_id": "course-1", "outline_sha256": approved_ref.sha256}
    settings = Settings(retrieval_mode="hybrid", embedding_base_url="http://bge-embed:8000/v1",
                        retrieval_top_k=12)
    app = create_app(settings=settings, artifact_store=FakeStore([approved_ref]),
        temporal_connect=lambda settings: _resolved(temporal))
    payload = {"approved_outline_ref": approved_ref.model_dump(mode="json"),
        "index_workflow_id": "b2c-index-" + "1" * 64}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/course-1/qa/generate", json=payload)
    assert response.status_code == 202
    inp = temporal.starts[0][1]
    assert inp.retrieval_mode == "hybrid" and inp.retrieval_top_k == 12


async def test_course_qa_completed_workflow_reports_failed_content():
    store = FakeStore()
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    result_ref = ArtifactRef(stage="generate-course-qa", key="c" * 64,
        uri="s3://artifacts/course-1/generate-course-qa/" + "c" * 64 + "/",
        sha256="d" * 64, created_at=datetime.now(UTC))
    temporal.result_value = {"course_id": "course-1", "status": "failed",
        "artifact": result_ref.model_dump(mode="json")}
    store.read = lambda ref, name: json.dumps({"status": "failed", "scopes": []}).encode()
    app = create_app(settings=Settings(), artifact_store=store,
        temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/course-qa/" + "b2c-qa-" + "1" * 64)
    assert response.status_code == 200
    assert response.json()["course_qa"] == {"status": "failed", "scopes": []}

    assert response.json()["status"] == "FAILED"
    assert response.json()["progress"]["status"] == "FAILED"



class FakeControlS3:
    def __init__(self):
        self.objects = {}
    def list_objects_v2(self, *, Bucket, Prefix, Delimiter=None):
        return {"CommonPrefixes": []}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body


async def test_course_qa_pause_persists_safe_boundary_request():
    store = FakeStore()
    store.bucket = "artifacts"
    store.s3 = FakeControlS3()
    temporal = FakeTemporal()
    temporal.status_name = "RUNNING"
    app = create_app(settings=Settings(), artifact_store=store,
        temporal_connect=lambda settings: _resolved(temporal))
    workflow_id = "b2c-qa-" + "a" * 64
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/course-qa/{workflow_id}/pause")
    assert response.status_code == 200
    assert response.json()["status"] == "PAUSE_REQUESTED"
    assert json.loads(store.s3.objects[f"_system/qa-controls/{workflow_id}.json"])["action"] == "pause_after_checkpoint"


async def test_course_qa_resume_starts_new_workflow_with_checkpoint_lineage():
    store = FakeStore()
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    temporal.result_value = {"status": "paused", "model_id": "local-model",
        "resume_context": {
            "course_id": "course-1",
            "approved_outline_ref": {"stage": "approve-outline", "sha256": "b" * 64},
            "index_result": {"course_id": "course-1"},
            "index_workflow_id": "b2c-index-" + "1" * 64,
            "checkpoint_source_workflow_ids": [],
        }}
    app = create_app(settings=Settings(llm_model_id="local-model"), artifact_store=store,
        temporal_connect=lambda settings: _resolved(temporal))
    workflow_id = "b2c-qa-" + "a" * 64
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/course-qa/{workflow_id}/resume")
    assert response.status_code == 200, response.text
    assert temporal.starts[0][1].checkpoint_source_workflow_ids == [workflow_id]
    assert temporal.starts[0][1].index_workflow_id == "b2c-index-" + "1" * 64



async def test_course_qa_status_preserves_paused_state_and_partial_lesson_assets():
    store = FakeStore()
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    ref = ArtifactRef(stage="generate-course-qa", key="c" * 64,
        uri="s3://artifacts/course-1/generate-course-qa/" + "c" * 64 + "/",
        sha256="d" * 64, created_at=datetime.now(UTC))
    temporal.result_value = {"course_id": "course-1", "status": "paused",
        "artifact": ref.model_dump(mode="json")}
    saved = {"status": "paused", "scopes": [],
        "lesson_assets": [{"lesson_id": "m1.l1", "draft": {"sentences": []}}]}
    store.read = lambda ref, name: json.dumps(saved).encode()
    app = create_app(settings=Settings(), artifact_store=store,
        temporal_connect=lambda settings: _resolved(temporal))
    workflow_id = "b2c-qa-" + "c" * 64
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/course-qa/{workflow_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "PAUSED"
    assert response.json()["course_qa"]["lesson_assets"][0]["lesson_id"] == "m1.l1"


async def test_upload_identity_is_stable_for_retries_and_changes_with_ingest_config():
    store = FakeStore()
    temporal = FakeTemporal()
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await post_upload(client)
        retry = await post_upload(client)
        changed = await post_upload(client, ocr_language="ar")
    assert first.status_code == retry.status_code == changed.status_code == 202
    assert first.json()["book_id"] == retry.json()["book_id"] == changed.json()["book_id"]
    assert first.json()["workflow_id"] == retry.json()["workflow_id"]
    assert changed.json()["workflow_id"] != first.json()["workflow_id"]


def test_book_library_prefers_canonical_record_over_legacy_alias():
    digest = "70c88ae9b2ba9ed6f61e813ca80e8685773ce18245477ed38e14d46eb7632292"
    legacy = {"book_id": "book-main", "title": "كتاب التجربة", "role": "spine",
              "workflow_id": "old-workflow", "file_sha256": digest,
              "source_filename": "book.pdf", "uploaded_at": "2026-10-09T19:10:28Z"}
    canonical = {"book_id": "book-" + digest[:24], "title": "كتاب التجربة", "role": "spine",
                 "workflow_id": "new-workflow", "file_sha256": digest,
                 "source_filename": "book.pdf", "uploaded_at": "2026-10-09T20:33:37Z"}
    books = _deduplicate_library_books([legacy, canonical])
    assert len(books) == 1
    assert books[0]["book_id"] == "book-" + digest[:24]
    assert books[0]["display_name"] == "كتاب التجربة · 70c88ae9"


def test_book_library_display_names_distinguish_same_title_different_files():
    first = {"book_id": "book-main", "title": "كتاب التجربة", "file_sha256": "a" * 64}
    second = {"book_id": "book-main", "title": "كتاب التجربة", "file_sha256": "b" * 64}
    books = _deduplicate_library_books([first, second])
    assert len(books) == 2
    assert books[0]["display_name"] != books[1]["display_name"]


def test_course_composition_can_assign_uploaded_spine_as_supplementary():
    from b2c_api.app import _validate_course_supplementary_identity

    parsed = ParsedBook(
        book_id="book-main", title="Shared source", role="spine", page_count=1,
        toc_source="pdf_outline", blocks=[],
        parser={"name": "fixture", "version": "1", "options_hash": "e" * 64},
    )

    # Upload role is source metadata; the course composition assigns its current role.
    _validate_course_supplementary_identity(parsed, "book-main")
    try:
        _validate_course_supplementary_identity(parsed, "another-book")
    except ValueError as exc:
        assert "identity mismatch" in str(exc)
    else:
        raise AssertionError("a mismatched source book identity must be rejected")


def test_library_prefers_canonical_workflow_and_distinguishes_matching_titles():
    from b2c_api.app import _deduplicate_library_books

    digest = "a" * 64
    books = _deduplicate_library_books([
        {"book_id": "book-main", "title": "كتاب التجربة", "file_sha256": digest,
         "workflow_id": ""},
        {"book_id": "book-" + digest[:24], "title": "كتاب التجربة",
         "source_filename": "كتاب-ثان.pdf", "file_sha256": digest,
         "workflow_id": "b2c-ingest-" + "1" * 64},
        {"book_id": "book-" + "b" * 24, "title": "كتاب التجربة",
         "source_filename": "كتاب-أول.pdf", "file_sha256": "b" * 64,
         "workflow_id": "b2c-ingest-" + "2" * 64},
    ])

    assert len(books) == 2
    assert {item["book_id"] for item in books} == {"book-" + digest[:24], "book-" + "b" * 24}
    assert all(item["display_name"] != "كتاب التجربة" for item in books)
    assert len({item["display_name"] for item in books}) == 2
