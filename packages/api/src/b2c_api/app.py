"""HTTP interface for source-book ingestion."""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import logging
import os
import secrets
import uuid
import threading
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any, Literal

import uvicorn
import psycopg
from b2c_contracts import (
    AudioAsset,
    SCHEMA_VERSION,
    ArtifactRef,
    BookUpload,
    CourseOutline,
    OutlineApprovalProvenance,
    ParsedBook,
    Question,
    stage_key,
)
from b2c_core import ArtifactStore, Settings
from b2c_core.source_identity import artifact_source_identity
from b2c_translation.generation import translation_completeness_issue
from b2c_translation.glossary import ContextualGlossary, glossary_identity
from b2c_core.processing_plan import plan_parsed_book
from b2c_orchestration.indexing import IndexApprovedBookInput, IndexApprovedBookWorkflow
from b2c_orchestration.ingest import IngestPDFInput, IngestPDFWorkflow
from b2c_orchestration.qa import GenerateCourseQAInput, GenerateCourseQAWorkflow
from b2c_orchestration.translation import TranslateContentInput, TranslateContentWorkflow, RetranslateSegmentInput, RetranslateSegmentWorkflow, translation_source_segments, translation_manual_overrides
from b2c_orchestration.audio import GenerateMP3Input, GenerateMP3Workflow, validate_mp3_input_lineage
from b2c_orchestration.queues import AUDIO, HEAVY, LIGHT
from b2c_pedagogy.qa_generation import ScopeResult
from b2c_pedagogy.srs import build_review_quiz

from b2c_api.serving import S3CourseServingCatalog, learner_question, parse_single_byte_range
from b2c_api.srs import PostgresSRSStore
from b2c_indexing.postgres_store import PostgresChunkStore
from b2c_api.model_lifecycle import ModelLifecycle, configured_providers, lifecycle_router
from b2c_outline.review import (
    POLICY_ID,
    POLICY_VERSION,
    OutlineReviewRejected,
    review_outline,
    merge_bodyless_lessons,
    include_leading_blocks_in_first_lesson,
)
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.background import BackgroundTask
from b2c_api.audio_exports import audio_progress, ordered_audio_tracks, validate_audio_manifest, course_audio_checkpoints, audio_zip
from starlette.concurrency import run_in_threadpool
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from b2c_translation.generation import validate_manual_translation
from b2c_api.ingestion_recovery import recover_extraction, recover_index
from b2c_api.upload_limits import UPLOAD_MULTIPART_ALLOWANCE, UploadSizeLimitMiddleware
from b2c_api.deps import (
    STAGE_CONTROL_ID,
    is_course_id,
    is_workflow_id,
    require_book_id,
    require_course_id,
    require_workflow_id,
    describe_or_404,
    s3_bytes,
    s3_json,
    stream_artifact_file,
    verified_ref,
)
from b2c_api.workflow_resume import (
    FAILED as RETRY_STATUSES,
    ResumeStageRequest,
    retry_failed_stage,
    retryable_kind,
    workflow_resume_router,
)

log = logging.getLogger("b2c.api")
_CHUNK_BYTES = 1024 * 1024


def _serialize_progress_writes(function):
    lock = threading.RLock()

    def synchronized(*args, **kwargs):
        with lock:
            return function(*args, **kwargs)

    return synchronized


class UploadTooLarge(ValueError):
    pass


class _MissingObject(LookupError):
    """A checkpoint object that has not been written yet."""


def _hash_upload(stream: Any, max_bytes: int) -> tuple[str, bool]:
    digest = sha256()
    stream.seek(0)
    signature = stream.read(5) == b"%PDF-"
    stream.seek(0)
    total = 0
    while chunk := stream.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest(), signature


class OutlineReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_type: Literal["human", "system"] = "human"
    reviewer_id: str | None = Field(default=None, min_length=1, max_length=120)
    outline: CourseOutline
    supplementary_workflow_ids: dict[str, str] = Field(default_factory=dict)
    target_tokens: int = Field(default=500, ge=300, le=800)
    overlap_ratio: float = Field(default=0.12, ge=0, lt=0.5)
    index_after_approval: bool = True

    @model_validator(mode="after")
    def validate_reviewer(self) -> OutlineReview:
        if self.review_type == "human" and not self.reviewer_id:
            raise ValueError("human review requires reviewer_id")
        if self.review_type == "system" and self.reviewer_id is not None:
            raise ValueError("system review must not provide reviewer_id")
        return self


class SystemOutlineApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplementary_workflow_ids: dict[str, str] = Field(default_factory=dict)
    target_tokens: int = Field(default=500, ge=300, le=800)
    overlap_ratio: float = Field(default=0.12, ge=0, lt=0.5)
    index_after_approval: bool = True


class CourseQAGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_outline_ref: ArtifactRef
    index_workflow_id: str = Field(pattern=r"^b2c-index-[0-9a-f]{64}$")
    lesson_question_count: int = Field(default=5, ge=1, le=20)
    group_question_count: int = Field(default=5, ge=1, le=20)
    course_question_count: int = Field(default=10, ge=1, le=20)
    lesson_mcq_count: int = Field(default=5, ge=3, le=20)
    include_lesson_enrichment: bool = False
    include_review_assessments: bool = False
    assessment_question_count: int = Field(default=10, ge=5, le=20)
    max_tokens: int = Field(default=4096, ge=512, le=16384)
    lesson_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    force_regenerate: bool = False
    retrieval_mode: Literal["lexical", "hybrid"] | None = None
    retrieval_provider_id: Literal["lexical", "bge-m3", "embeddinggemma2-lite", "lightrag-157", "hipporag-2"] | None = None
    retrieval_top_k: int | None = Field(default=None, ge=1, le=100)


class TranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contextual_glossary: ContextualGlossary | None = None
    provider_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    source_artifact_ref: ArtifactRef
    source_kind: Literal["book", "course"] = "book"
    source_language: Literal["en", "ar"]
    target_language: Literal["en", "ar"]
    max_tokens: int = Field(default=8192, ge=512, le=16384)
    resume_from_workflow_id: str | None = Field(default=None, pattern=r"^b2c-translate-[0-9a-f]{64}$")
    force_regenerate: bool = False


class TranslationRetranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1, max_length=160)


class TranslationRevisionSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1,max_length=160)
    revision_id: str | None = Field(default=None,pattern=r"^[0-9a-f]{32,64}$")


class TranslationCandidateAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class TranslationCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1, max_length=160)
    translated_text: str = Field(min_length=1, max_length=24000)


class MP3GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_artifact_ref: ArtifactRef
    source_kind: Literal["book", "course"]
    language: Literal["en", "ar"]
    approved_outline_ref: ArtifactRef | None = None
    source_qa_artifact_ref: ArtifactRef | None = None
    source_book_ref: ArtifactRef | None = None
    source_book_translation_ref: ArtifactRef | None = None
    book_audio_layout: Literal["full_book", "lessons"] = "full_book"
    lesson_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)
    allow_partial_qa: bool = False
    include_summaries: bool = True
    include_extra_scopes: bool = True

    @model_validator(mode="after")
    def validate_lesson_selection(self):
        if self.lesson_ids is not None and (self.source_kind == "book" and self.book_audio_layout != "lessons"):
            raise ValueError("lesson_ids require book audio with lessons layout")
        if self.lesson_ids is not None and len(set(self.lesson_ids)) != len(self.lesson_ids):
            raise ValueError("lesson_ids must be unique")
        return self

class ReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    question_id: str = Field(min_length=1, max_length=200)
    rating: Literal[1, 2, 3, 4]
    reviewed_at: datetime

    @field_validator("reviewed_at")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("reviewed_at must be timezone-aware UTC")
        return value


class UploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    book_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    title: str = Field(min_length=1, max_length=300)
    role: Literal["spine", "supplementary"]
    rights_status: Literal["cleared", "pending", "internal_test_only"]
    ocr_language: Literal["en", "ar"] = "en"
    ocr_engine: Literal["rapidocr", "surya", "openrouter"] | None = None
    supplementary_book_ids: list[str] = Field(default_factory=list, max_length=100)
    force_reprocess: bool = False


def _validate_course_supplementary_identity(parsed: ParsedBook, expected_book_id: str) -> None:
    """A source uploaded as a spine may be assigned as a course supplement later."""
    if parsed.book_id != expected_book_id:
        raise ValueError(f"supplementary book identity mismatch: {expected_book_id}")


def _deduplicate_library_books(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer content-addressed, runnable records over legacy book-main aliases."""
    def priority(item: dict[str, Any]) -> tuple[bool, bool, bool, str, str]:
        digest = str(item.get("file_sha256", ""))
        canonical = bool(digest and item.get("book_id") == "book-" + digest[:24])
        return (canonical, bool(item.get("workflow_id")), bool(item.get("title_verified")),
                str(item.get("uploaded_at", "")),
                str((item.get("source_artifact_ref") or {}).get("created_at", "")))

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in sorted(records, key=priority, reverse=True):
        digest = str(item.get("file_sha256", ""))
        upload_key = str((item.get("source_artifact_ref") or {}).get("key", ""))
        identities = ([ ("sha256", digest) ] if digest else []) + ([ ("upload", upload_key) ] if upload_key else [])
        if identities and any(identity in seen for identity in identities):
            continue
        seen.update(identities)
        title = str(item.get("title") or "كتاب محفوظ").strip()
        filename = str(item.get("source_filename") or "").strip()
        suffix = digest[:8] if digest else str(item.get("book_id", ""))[-8:]
        label_parts = [title]
        if filename and filename.lower() not in {"book.pdf", "document.pdf"}:
            label_parts.append(filename)
        if suffix:
            label_parts.append(suffix)
        item["display_name"] = " · ".join(label_parts)
        if digest:
            item["artifact_book_id"] = item.get("artifact_book_id") or item.get("book_id")
            item["book_id"] = "book-" + digest[:24]
        selected.append(item)
    return sorted(selected, key=lambda item: str(item.get("display_name", "")).casefold())


async def _connect_temporal(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                data_converter=pydantic_data_converter)


def create_app(
    *,
    settings: Settings | None = None,
    artifact_store: Any | None = None,
    temporal_connect: Callable[[Settings], Awaitable[Any]] = _connect_temporal,
    serving_catalog: Any | None = None,
    srs_store: Any | None = None,
    learner_authenticator: Callable[[Request], Any] | None = None,
    model_lifecycle: ModelLifecycle | None = None,
) -> FastAPI:
    settings = settings or Settings()
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.model_lifecycle.ensure_warm_loop()
        try:
            yield
        finally:
            await application.state.model_lifecycle.close()

    app = FastAPI(title="Book2Course API", version="0.1.0", lifespan=lifespan)
    # Inner to the bearer-token middleware below: unauthenticated requests are
    # refused first, then oversized PDF uploads are cut off before spooling.
    app.add_middleware(UploadSizeLimitMiddleware, path="/v1/books",
                       max_body_bytes=settings.max_upload_bytes + UPLOAD_MULTIPART_ALLOWANCE)
    api_auth_required = os.getenv("B2C_API_AUTH_REQUIRED", "false").strip().lower() in {"1", "true", "yes"}
    api_token = os.getenv("B2C_APP_API_TOKEN", "").strip()
    token_file = os.getenv("B2C_APP_API_TOKEN_FILE", "").strip()
    if not api_token and token_file:
        try:
            api_token = open(token_file, encoding="utf-8").read().strip()
        except OSError:
            api_token = ""
    if api_auth_required and not api_token:
        raise RuntimeError("B2C API auth is required but its token is unavailable")
    app.state.model_lifecycle = model_lifecycle or ModelLifecycle(
        configured_providers(settings), preferences_file=os.getenv("B2C_MODEL_PREFERENCES_FILE", ""),
        catalog_file=os.getenv("B2C_MODEL_CATALOG_FILE", ""),
        secrets_dir=os.getenv("B2C_MODEL_SECRETS_DIR", ""))
    app.include_router(lifecycle_router(app.state.model_lifecycle, api_token))

    def generation_profile(stage: str) -> tuple[str, str, str]:
        manager = app.state.model_lifecycle
        provider_id = manager.defaults.get(stage, "")
        provider = manager.providers.get(provider_id)
        if provider_id:
            if provider is None or not provider.task_queue:
                raise HTTPException(status_code=503, detail="the saved model route is currently disabled")
            return provider.model, provider.task_queue, provider.id
        return (settings.llm_model_id, os.getenv("B2C_GENERATION_TASK_QUEUE", HEAVY),
                "configured-llm")

    @app.middleware("http")
    async def require_mobile_api_token(request: Request, call_next: Callable[..., Any]) -> Any:
        if not api_auth_required or (request.method == "GET" and request.url.path == "/health"):
            return await call_next(request)
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {api_token}"
        if not secrets.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "valid bearer token required"})
        return await call_next(request)
    app.state.artifact_store = artifact_store
    app.state.temporal_client = None
    app.state.serving_catalog = serving_catalog
    app.state.srs_store = srs_store
    app.state.learner_authenticator = learner_authenticator

    def get_serving_catalog() -> Any:
        if app.state.serving_catalog is None:
            store = app.state.artifact_store
            if store is None:
                store = ArtifactStore.from_settings(settings)
                app.state.artifact_store = store
            app.state.serving_catalog = S3CourseServingCatalog(store, settings.s3_bucket)
        return app.state.serving_catalog

    def get_srs_store() -> Any:
        if app.state.srs_store is None:
            app.state.srs_store = PostgresSRSStore(settings.pg_dsn)
        return app.state.srs_store

    async def require_learner_id(request: Request) -> str:
        authenticator = app.state.learner_authenticator
        if authenticator is None:
            raise HTTPException(status_code=503, detail="learner authentication is not configured")
        principal = authenticator(request)
        if inspect.isawaitable(principal):
            principal = await principal
        if not isinstance(principal, str) or not principal or len(principal) > 120:
            raise HTTPException(status_code=401, detail="authenticated learner identity is missing")
        return principal

    def get_store() -> Any:
        if app.state.artifact_store is None:
            app.state.artifact_store = ArtifactStore.from_settings(settings)
        return app.state.artifact_store

    async def get_temporal_client() -> Any:
        if app.state.temporal_client is None:
            app.state.temporal_client = await temporal_connect(settings)
        return app.state.temporal_client

    async def connected_temporal() -> Any:
        try:
            return await get_temporal_client()
        except Exception as exc:
            log.exception("could not connect to Temporal")
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc

    async def model_worker_queue_ready(provider) -> bool:
        # A workflow poller is enough to accept and persist a translation request.
        # Activity pollers can temporarily disappear while a long translation activity
        # occupies the worker's configured concurrency slot; that is queue saturation,
        # not a missing worker or an unavailable model. The workflow will wait for the
        # activity slot and remain resumable through Temporal.
        if provider.task_queue != "hy-translation" and provider.translation_engine != "booktrans-pro":
            return True
        from temporalio.api.enums.v1 import TaskQueueType
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
        client = await get_temporal_client()
        queue = await client.workflow_service.describe_task_queue(DescribeTaskQueueRequest(
            namespace=client.namespace, task_queue=TaskQueue(name=provider.task_queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW))
        return bool(queue.pollers)

    app.state.model_lifecycle.route_ready = model_worker_queue_ready

    @_serialize_progress_writes
    def write_book_progress(course_id: str, book_id: str, stage: str, workflow_id: str,
                            metadata: dict[str, Any] | None = None) -> None:
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        key = f"{course_id}/book-progress/{hashlib.sha256(book_id.encode()).hexdigest()}.json"
        record: dict[str, Any] = {"schema_version": "1.0", "course_id": course_id,
                                  "book_id": book_id, "stages": {}}
        try:
            obj = store.s3.get_object(Bucket=settings.s3_bucket, Key=key)
            record.update(json.loads(obj["Body"].read()))
        except Exception as exc:
            code = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code != 404:
                raise
        stages = record.setdefault("stages", {})
        if stage != "ingest":
            ingestion = stages.get("ingest", {})
            incoming = metadata or {}
            current_parsed = ingestion.get("source_parsed_sha256")
            incoming_parsed = incoming.get("source_parsed_sha256")
            current_pdf = ingestion.get("source_file_sha256") or record.get("source_file_sha256")
            identity = incoming.get("source_identity") or {}
            incoming_pdf = incoming.get("source_file_sha256") or identity.get("source_file_sha256")
            foreign_ingestion = (incoming.get("source_ingest_workflow_id") and
                ingestion.get("workflow_id") and
                incoming["source_ingest_workflow_id"] != ingestion["workflow_id"])
            mismatched = (foreign_ingestion or
                (current_parsed and incoming_parsed and current_parsed != incoming_parsed) or
                (current_pdf and incoming_pdf and current_pdf != incoming_pdf))
            if mismatched:
                # Keep the completed work recoverable without replacing the
                # current PDF's stage pointers under a reused legacy book ID.
                record["stage_history"] = [*record.get("stage_history", []), {
                    "reason": "source_identity_mismatch", "stage": stage,
                    "workflow_id": workflow_id, **incoming,
                    "recorded_at": datetime.now(UTC).isoformat()}]
                record["updated_at"] = datetime.now(UTC).isoformat()
                store.s3.put_object(Bucket=settings.s3_bucket, Key=key,
                    Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                    ContentType="application/json")
                return
            if (incoming.get("source_ingest_workflow_id") == ingestion.get("workflow_id")
                    and incoming_parsed):
                ingestion["source_parsed_sha256"] = incoming_parsed
        previous = stages.get(stage, {})
        if previous.get("workflow_id") != workflow_id:
            previous = {}
        stages[stage] = {**previous, "workflow_id": workflow_id,
                         "status_url": f"/v1/{stage_route(stage)}/{workflow_id}",
                         "requested_at": previous.get("requested_at") or datetime.now(UTC).isoformat()}
        if metadata:
            stages[stage].update(metadata)
        # The upload fingerprint is stable across OCR/reprocessing outputs. Retain
        # it at book and stage level, but never bind a foreign parsed source to it.
        ingest = stages.get("ingest", {})
        upload_digest = ingest.get("source_file_sha256")
        if stage == "ingest" and upload_digest:
            previous_digest = record.get("source_file_sha256")
            if previous_digest and previous_digest != upload_digest:
                record["stage_history"] = [*record.get("stage_history", []),
                    {"source_file_sha256": previous_digest,
                     "stages": {name: value for name, value in stages.items() if name != "ingest"}}]
                record["stages"] = stages = {"ingest": stages["ingest"]}
            record["source_file_sha256"] = upload_digest
            record["source_upload_ref"] = ingest.get("source_upload_ref")
        elif stage != "ingest" and upload_digest:
            expected_parsed = ingest.get("source_parsed_sha256")
            actual_parsed = stages[stage].get("source_parsed_sha256")
            if expected_parsed and actual_parsed == expected_parsed:
                stages[stage]["source_file_sha256"] = upload_digest
        record["updated_at"] = datetime.now(UTC).isoformat()
        store.s3.put_object(Bucket=settings.s3_bucket, Key=key,
                            Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                            ContentType="application/json")

    def stage_route(stage: str) -> str:
        return {"ingest": "ingestions", "outline": "indexings", "index": "indexings", "qa": "course-qa",
                "translation": "translations", "book_translation": "translations", "audio": "audio"}[stage]

    async def remember_book_progress(course_id: str, book_id: str, stage: str, workflow_id: str,
                                     metadata: dict[str, Any] | None = None) -> None:
        try:
            incoming = dict(metadata or {})
            parsed_sha = incoming.get("source_parsed_sha256")
            if stage != "ingest" and parsed_sha:
                # Legacy artifacts retain their original IDs. Route their progress
                # to the PDF-derived external identity only after exact source proof.
                identity = incoming.get("source_identity") or {}
                catalog = await list_course_books(course_id)
                for candidate in catalog["books"]:
                    if candidate.get("artifact_book_id", candidate.get("book_id")) != book_id:
                        continue
                    digest = candidate.get("file_sha256")
                    candidate_workflow = candidate.get("workflow_id")
                    if not digest or not candidate_workflow:
                        continue
                    state = await ingestion_status(candidate_workflow, recovery_course_id=course_id)
                    parsed_data = state.get("result", {}).get("parsed_book", {}).get("ref")
                    if not parsed_data or parsed_data.get("sha256") != parsed_sha:
                        continue
                    if identity.get("source_file_sha256") not in (None, digest):
                        continue
                    book_id = candidate["book_id"]
                    incoming.update({"source_file_sha256": digest,
                                     "artifact_book_id": candidate.get("artifact_book_id", book_id)})
                    await run_in_threadpool(write_book_progress, course_id, book_id, "ingest",
                        candidate_workflow, {"source_file_sha256": digest,
                        "source_upload_ref": candidate.get("source_artifact_ref"),
                        "source_parsed_sha256": parsed_sha,
                        "artifact_book_id": candidate.get("artifact_book_id", book_id),
                        "book_title": candidate.get("title")})
                    break
            await run_in_threadpool(write_book_progress, course_id, book_id, stage, workflow_id, incoming)
        except Exception:
            log.exception("could not persist book workflow progress")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/books", status_code=202)
    async def upload_book(
        file: Annotated[UploadFile, File(description="A digital-text PDF book")],
        metadata_json: Annotated[str, Form(description="JSON fields for UploadMetadata")],
    ) -> dict[str, Any]:
        try:
            metadata = UploadMetadata.model_validate_json(metadata_json)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="metadata_json is invalid") from exc

        # Reject mismatched declared media types before reading or persisting the upload.
        # The PDF signature below remains the authoritative content check.
        if (file.content_type or "").split(";", 1)[0].strip().lower() != "application/pdf":
            await file.close()
            raise HTTPException(status_code=415, detail="only application/pdf uploads are accepted")

        try:
            digest, is_pdf = await run_in_threadpool(
                _hash_upload, file.file, settings.max_upload_bytes,
            )
        except UploadTooLarge as exc:
            await file.close()
            raise HTTPException(status_code=413, detail="PDF exceeds the configured upload limit") from exc
        if not is_pdf:
            await file.close()
            raise HTTPException(status_code=415, detail="only PDF files are accepted")
        # A content-derived identifier is generated by the server. Re-uploading
        # the same PDF is idempotent; a different PDF can never inherit another
        # book's workflow/progress key even if an older APK reuses "book-main".
        canonical_book_id = "book-" + digest[:24]
        upload = BookUpload(
            book_id=canonical_book_id, role=metadata.role, file_sha256=digest,
            mime="application/pdf", title=metadata.title, rights_status=metadata.rights_status, ocr_language=metadata.ocr_language,
        )
        config = {"upload": upload.model_dump(mode="json")}
        key = stage_key("book-upload", "1", [digest], config)
        store = app.state.artifact_store
        if store is None:
            store = ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
        try:
            await run_in_threadpool(store.ensure_bucket)
            metadata_bytes = upload.model_dump_json().encode("utf-8")
            source_ref = await run_in_threadpool(
                store.put_stream, metadata.course_id, "book-upload", key,
                {"source.pdf": file.file, "book-upload.json": io.BytesIO(metadata_bytes)},
                {"source.pdf": digest, "book-upload.json": sha256(metadata_bytes).hexdigest()},
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await file.close()

        cache_bust = uuid.uuid4().hex if metadata.force_reprocess else None
        selected_ocr_engine = metadata.ocr_engine or os.getenv("B2C_OCR_ENGINE", "rapidocr").strip().lower()
        ingest_identity = {
            "course_id": metadata.course_id, "book_id": canonical_book_id, "file_sha256": digest,
            "title": metadata.title, "role": metadata.role, "rights_status": metadata.rights_status,
            "ocr_language": metadata.ocr_language, "ocr_engine": selected_ocr_engine,
            "supplementary_book_ids": sorted(metadata.supplementary_book_ids),
            "cache_bust": cache_bust or "",
        }
        workflow_id = "b2c-ingest-" + hashlib.sha256(
            json.dumps(ingest_identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        # Preserve idempotency for uploads created before configuration became
        # part of the workflow identity. Reuse only when every ingest input matches.
        if not cache_bust:
            try:
                catalog = await list_course_books(metadata.course_id)
                matching = next((item for item in catalog.get("books", [])
                    if item.get("book_id") == canonical_book_id
                    and item.get("file_sha256") == digest
                    and item.get("title") == metadata.title
                    and item.get("role") == metadata.role
                    and item.get("rights_status") == metadata.rights_status
                    and item.get("ocr_language") == metadata.ocr_language
                    and item.get("ocr_engine") == selected_ocr_engine
                    and sorted(item.get("supplementary_book_ids", [])) == sorted(metadata.supplementary_book_ids)
                    and item.get("workflow_id")), None)
                if matching:
                    workflow_id = matching["workflow_id"]
            except Exception:
                log.exception("could not inspect existing book configuration before upload")
        inp = IngestPDFInput(
            course_id=metadata.course_id, upload=upload.model_dump(mode="json"),
            source_ref=source_ref.model_dump(mode="json"),
            supplementary_book_ids=metadata.supplementary_book_ids,
            cache_bust=cache_bust, ocr_engine=selected_ocr_engine,
        )
        try:
            client = await get_temporal_client()
            try:
                await client.start_workflow(
                    IngestPDFWorkflow.run, inp, id=workflow_id, task_queue=LIGHT,
                )
            except WorkflowAlreadyStartedError:
                pass  # stable workflow id makes a retried upload request idempotent
        except Exception as exc:
            log.exception("failed to start book ingestion workflow")
            raise HTTPException(
                status_code=503,
                detail={"message": "Temporal is unavailable; the source artifact was retained", "workflow_id": workflow_id},
            ) from exc

        library_key = stage_key("book-library", "1", [digest], {"book_id": canonical_book_id, "workflow_id": workflow_id})
        library_record = {"course_id": metadata.course_id, "book_id": canonical_book_id, "title": metadata.title,
            "role": metadata.role, "rights_status": metadata.rights_status, "ocr_language": metadata.ocr_language,
            "ocr_engine": selected_ocr_engine, "workflow_id": workflow_id, "file_sha256": digest,
            "supplementary_book_ids": metadata.supplementary_book_ids,
            "source_filename": file.filename, "uploaded_at": datetime.now(UTC).isoformat(),
            "source_artifact_ref": source_ref.model_dump(mode="json")}
        await run_in_threadpool(store.put, metadata.course_id, "book-library", library_key,
            {"book-library.json": json.dumps(library_record, ensure_ascii=False).encode("utf-8")})
        await remember_book_progress(metadata.course_id, canonical_book_id, "ingest", workflow_id,
                                     {"book_title": metadata.title, "source_file_sha256": digest,
                                      "source_upload_ref": source_ref.model_dump(mode="json")})
        return {
            "course_id": metadata.course_id, "book_id": canonical_book_id,
            "workflow_id": workflow_id, "force_reprocess": metadata.force_reprocess,
            "source_artifact": source_ref.model_dump(mode="json"),
            "status_url": f"/v1/ingestions/{workflow_id}",
        }

    @app.get("/v1/courses/{course_id}/books")
    async def list_course_books(course_id: str) -> dict[str, Any]:
        require_course_id(course_id)
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        try:
            response = await run_in_threadpool(store.s3.list_objects_v2, Bucket=settings.s3_bucket,
                                                Prefix=course_id + "/book-library/", Delimiter="/")
            books: list[dict[str, Any]] = []
            for entry in response.get("CommonPrefixes", []):
                manifest = await s3_bytes(store.s3, settings.s3_bucket, entry["Prefix"] + "_manifest.json")
                if manifest is None:
                    continue  # uncommitted library entry
                ref = ArtifactRef.model_validate_json(manifest)
                item = json.loads(await run_in_threadpool(store.read, ref, "book-library.json"))
                source_ref = item.get("source_artifact_ref") or {}
                digest = str(item.get("file_sha256", ""))
                if not digest and source_ref.get("uri"):
                    try:
                        source = ArtifactRef.model_validate(source_ref)
                        upload_meta = json.loads(await run_in_threadpool(store.read, source, "book-upload.json"))
                        digest = str(upload_meta.get("file_sha256", ""))
                    except Exception:
                        pass
                if digest:
                    item["file_sha256"] = digest
                books.append(item)
            uploaded = await run_in_threadpool(store.s3.list_objects_v2, Bucket=settings.s3_bucket,
                                               Prefix=course_id + "/book-upload/", Delimiter="/")
            temporal = None
            for entry in uploaded.get("CommonPrefixes", []):
                manifest = await s3_bytes(store.s3, settings.s3_bucket, entry["Prefix"] + "_manifest.json")
                if manifest is None:
                    continue  # uncommitted upload
                upload_ref = ArtifactRef.model_validate_json(manifest)
                meta = json.loads(await run_in_threadpool(store.read, upload_ref, "book-upload.json"))
                digest = str(meta.get("file_sha256", ""))
                engine = os.getenv("B2C_OCR_ENGINE", "rapidocr").strip().lower()
                workflow_id = ""
                try:
                    temporal = temporal or await get_temporal_client()
                    for candidate in dict.fromkeys([engine, "rapidocr", "surya", "openrouter"]):
                        candidate_id = "b2c-ingest-" + hashlib.sha256(
                            f"{course_id}\0{meta['book_id']}\0{meta['file_sha256']}\0{candidate}\0".encode()
                        ).hexdigest()
                        try:
                            await temporal.get_workflow_handle(candidate_id).describe()
                            workflow_id = candidate_id; engine = candidate; break
                        except Exception: continue
                except Exception:
                    pass
                books.append({"course_id": course_id, **meta, "ocr_engine": engine,
                              "workflow_id": workflow_id, "file_sha256": digest,
                              "source_artifact_ref": upload_ref.model_dump(mode="json")})
            books = _deduplicate_library_books(books)
            from b2c_api.course_library import apply_book_names
            books = await run_in_threadpool(apply_book_names, store, settings.s3_bucket, course_id, books)
            return {"course_id": course_id, "books": books}
        except Exception as exc:
            log.exception("failed to list uploaded books")
            raise HTTPException(status_code=503, detail="book library is unavailable") from exc

    from b2c_api.course_library import course_library_router
    def library_store():
        if app.state.artifact_store is None:
            app.state.artifact_store = ArtifactStore.from_settings(settings)
        return app.state.artifact_store
    app.include_router(course_library_router(library_store, settings.s3_bucket, list_course_books))

    @app.get("/v1/courses/{course_id}/books/{book_id}/progress")
    async def book_progress(course_id: str, book_id: str) -> dict[str, Any]:
        require_course_id(course_id)
        require_book_id(book_id)
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        key = f"{course_id}/book-progress/{hashlib.sha256(book_id.encode()).hexdigest()}.json"
        try:
            record = await s3_json(store.s3, settings.s3_bucket, key)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="book progress is unavailable") from exc
        if record is None:
            record = {"schema_version": "1.0", "course_id": course_id, "book_id": book_id, "stages": {}}
        catalog = await list_course_books(course_id)
        book = next((item for item in catalog["books"] if item.get("book_id") == book_id), None)
        artifact_book_id = book.get("artifact_book_id", book_id) if book else book_id
        legacy_record = None
        if book and book.get("file_sha256"):
            record["source_file_sha256"] = book["file_sha256"]
            record["artifact_book_id"] = artifact_book_id
            record["source_upload_ref"] = book.get("source_artifact_ref")
        if "ingest" not in record["stages"] and book and book.get("workflow_id"):
            record["stages"]["ingest"] = {"workflow_id": book["workflow_id"],
                "source_file_sha256": book.get("file_sha256"),
                "artifact_book_id": artifact_book_id}
        if artifact_book_id != book_id:
            legacy_key = f"{course_id}/book-progress/{hashlib.sha256(artifact_book_id.encode()).hexdigest()}.json"
            try:
                legacy_record = await s3_json(store.s3, settings.s3_bucket, legacy_key)
            except Exception as exc:
                raise HTTPException(status_code=503, detail="legacy book progress is unavailable") from exc
        # A registry entry is not reusable just because it says COMPLETED. Bind
        # every downstream stage to the current immutable parsed-book artifact.
        ingest_item = record.get("stages", {}).get("ingest", {})
        ingest_id = ingest_item.get("workflow_id", "")
        current_parsed_sha = None
        if ingest_id:
            try:
                ingest_state = await ingestion_status(ingest_id, recovery_course_id=course_id)
                parsed_ref_data = (ingest_state.get("result", {}).get("parsed_book", {}).get("ref")
                                   if ingest_state.get("status") == "COMPLETED" else None)
                if parsed_ref_data:
                    parsed_ref = ArtifactRef.model_validate(parsed_ref_data)
                    if (parsed_ref.stage == "ingest-parse-pdf" and
                            parsed_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
                        current_parsed_sha = parsed_ref.sha256
                        ingest_item["parsed_artifact_ref"] = parsed_ref.model_dump(mode="json")
                        ingest_item["source_parsed_sha256"] = parsed_ref.sha256
                        parsed_book = ParsedBook.model_validate_json(await run_in_threadpool(
                            store.read, parsed_ref, "parsed-book.json"))
                        if parsed_book.book_id != artifact_book_id:
                            raise HTTPException(status_code=409, detail="ingestion book identity mismatch")
                        fingerprint = parsed_book.parser.get("source_file_sha256")
                        if fingerprint:
                            if ingest_item.get("source_file_sha256") not in (None, fingerprint):
                                raise HTTPException(status_code=409, detail="ingestion source PDF fingerprint mismatch")
                            ingest_item["source_file_sha256"] = fingerprint
                            record["source_file_sha256"] = fingerprint
            except HTTPException:
                current_parsed_sha = None
        if current_parsed_sha and legacy_record:
            candidates = list(legacy_record.get("stages", {}).items())
            for history in legacy_record.get("stage_history", []):
                if history.get("stage"):
                    candidates.append((history["stage"], history))
                candidates.extend(history.get("stages", {}).items())
            for legacy_stage, item in candidates:
                if (legacy_stage != "ingest" and item.get("source_parsed_sha256") == current_parsed_sha
                        and item.get("source_file_sha256") in (None, record.get("source_file_sha256"))):
                    existing = record["stages"].get(legacy_stage)
                    if not existing or str(item.get("requested_at", item.get("recorded_at", ""))) > str(existing.get("requested_at", "")):
                        record["stages"][legacy_stage] = {**item,
                            "source_file_sha256": record.get("source_file_sha256"),
                            "artifact_book_id": artifact_book_id,
                            "projected_from_legacy": True}
        # Older approvals stored the indexing workflow under the outline key.
        # Split that legacy record into the two real stages so the app does not
        # show outline as failed or hide the failed RAG/index stage.
        legacy_outline = record.get("stages", {}).get("outline")
        if (legacy_outline and not record.get("stages", {}).get("index")
                and str(legacy_outline.get("workflow_id", "")).startswith("b2c-index-")
                and legacy_outline.get("approved_outline_ref")
                and legacy_outline.get("source_parsed_sha256") == current_parsed_sha):
            index_id = legacy_outline["workflow_id"]
            record["stages"]["index"] = {
                **legacy_outline,
                "workflow_id": index_id,
                "status_url": f"/v1/indexings/{index_id}",
            }
            ingest_id = legacy_outline.get("source_ingest_workflow_id") or ingest_id
            record["stages"]["outline"] = {
                **legacy_outline,
                "workflow_id": ingest_id,
                "status_url": f"/v1/ingestions/{ingest_id}" if ingest_id else "",
                "status": "COMPLETED",
            }
        for stage in list(record.get("stages", {})):
            if stage == "ingest":
                continue
            item = record["stages"][stage]
            if not current_parsed_sha or item.get("source_parsed_sha256") != current_parsed_sha:
                record["stages"].pop(stage, None)
        client = await get_temporal_client()
        for stage, item in record["stages"].items():
            # Outline approval is an immutable stored result, independent of
            # Temporal retention of its originating extraction workflow.
            if stage == "outline" and item.get("approved_outline_ref"):
                try:
                    approval_ref = ArtifactRef.model_validate(item["approved_outline_ref"])
                    committed = await run_in_threadpool(store.exists, course_id, "approve-outline", approval_ref.key)
                    if committed == approval_ref:
                        envelope = json.loads(await run_in_threadpool(store.read, approval_ref, "approved-outline.json"))
                        if ArtifactRef.model_validate(envelope["spine_parsed_ref"]).sha256 == current_parsed_sha:
                            item["status"] = "COMPLETED"
                            item["recovered_from_artifact"] = True
                            continue
                except (ValueError, KeyError):
                    pass
            workflow_id = item.get("workflow_id", "")
            if not workflow_id:
                item["status"] = "UNKNOWN"
                continue
            try:
                desc = await client.get_workflow_handle(workflow_id).describe()
                item["status"] = getattr(getattr(desc, "status", None), "name", "UNKNOWN")
                if stage == "qa" and item["status"] == "COMPLETED" and item.get("result_status") == "failed":
                    item["workflow_status"] = "COMPLETED"
                    item["status"] = "PARTIAL"
            except RPCError as exc:
                item["status"] = "UNKNOWN"
                if exc.status == RPCStatusCode.NOT_FOUND and stage == "qa" and item.get("artifact_ref"):
                    try:
                        qa_ref = ArtifactRef.model_validate(item["artifact_ref"])
                        if qa_ref.stage != "generate-course-qa" or not qa_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
                            raise ValueError("QA artifact is outside course")
                        qa_payload = json.loads(await run_in_threadpool(store.read, qa_ref, "course-qa.json"))
                        item["status"] = "COMPLETED" if qa_payload.get("status") == "final" else "PAUSED" if qa_payload.get("status") == "paused" else "PARTIAL"
                        item["recovered_from_artifact"] = True
                        item["workflow_status"] = "NOT_FOUND"
                    except Exception:
                        item["recovery_state"] = "unavailable"
                if exc.status == RPCStatusCode.NOT_FOUND and stage in {"ingest", "index", "outline"}:
                    try:
                        recovered = (await ingestion_status(workflow_id, recovery_course_id=course_id)
                                     if stage == "ingest" else
                                     await indexing_status(workflow_id, recovery_course_id=course_id))
                        item["status"] = recovered["status"]
                        item["recovered_from_artifact"] = recovered.get("recovered_from_artifact", False)
                    except HTTPException:
                        item["recovery_state"] = "unavailable"
            except Exception:
                item["status"] = "UNKNOWN"
        record["updated_at"] = datetime.now(UTC).isoformat()
        return record

    from b2c_api.saved_audio import saved_audio_router
    app.include_router(saved_audio_router(library_store, book_progress, list_course_books, lambda cid: list_course_audio_runs(cid)))
    from b2c_api.result_exports import result_exports_router
    app.include_router(result_exports_router(library_store, settings.s3_bucket))

    @app.get("/v1/courses/{course_id}/books/{book_id}/source")
    async def download_uploaded_book(course_id: str, book_id: str) -> StreamingResponse:
        catalog = await list_course_books(course_id)
        item = next((x for x in catalog["books"] if x.get("book_id") == book_id), None)
        if item is None: raise HTTPException(status_code=404, detail="uploaded book not found")
        ref = ArtifactRef.model_validate(item["source_artifact_ref"])
        if ref.stage != "book-upload" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
            raise HTTPException(status_code=503, detail="uploaded book reference is invalid")
        try:
            return await stream_artifact_file(get_store(), ref, "source.pdf", media_type="application/pdf",
                headers={"Content-Disposition": f'inline; filename="{book_id}.pdf"'})
        except Exception as exc:
            raise HTTPException(status_code=503, detail="uploaded PDF is unavailable") from exc

    @app.get("/v1/ingestions/{workflow_id}/text")
    async def ingestion_text_preview(workflow_id: str, course_id: str | None = None, offset: int = Query(0, ge=0),
                                     limit: int = Query(40, ge=1, le=100)) -> dict[str, Any]:
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        status = await ingestion_status(workflow_id, recovery_course_id=course_id)
        if status["status"] != "COMPLETED":
            raise HTTPException(status_code=409, detail="OCR extraction is not complete")
        result = status["result"]
        parsed = result.get("parsed_book", {}).get("ref")
        if not parsed: raise HTTPException(status_code=404, detail="parsed text artifact not found")
        ref = ArtifactRef.model_validate(parsed)
        if ref.stage != "ingest-parse-pdf" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/") or (course_id and not ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
            raise HTTPException(status_code=503, detail="parsed artifact reference is invalid")
        store = app.state.artifact_store or ArtifactStore.from_settings(settings); app.state.artifact_store = store
        book = ParsedBook.model_validate_json(await run_in_threadpool(store.read, ref, "parsed-book.json"))
        blocks = book.blocks[offset:offset + limit]
        return {"workflow_id": workflow_id, "book_id": book.book_id, "title": book.title,
            "offset": offset, "limit": limit, "total_blocks": len(book.blocks),
            "blocks": [{"block_id": b.block_id, "type": b.type, "text": b.text,
                        "pages": sorted({r.page_no for r in b.refs})} for b in blocks]}
    @app.get("/v1/ingestions/{workflow_id}")
    async def ingestion_status(workflow_id: str, recovery_course_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        if recovery_course_id is not None:
            require_course_id(recovery_course_id)
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            try:
                recovered = await recover_extraction(store, client, workflow_id, course_id=recovery_course_id)
            except Exception as recovery_exc:
                log.exception("failed to recover committed extraction")
                raise HTTPException(status_code=503, detail="stored extraction is unavailable") from recovery_exc
            if recovered is None:
                raise HTTPException(status_code=404, detail="ingestion not found and no committed extraction is associated") from exc
            return {"workflow_id": workflow_id, "status": "COMPLETED", "start_time": None,
                    "close_time": None, "result": recovered, "recovered_from_artifact": True,
                    "completion_scope": "extraction", "workflow_history_available": False}
        except Exception as exc:
            log.exception("failed to read ingestion status")
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
        status = getattr(description, "status", None)
        status_name = getattr(status, "name", str(status))
        response = {
            "workflow_id": workflow_id,
            "status": status_name,
            "start_time": getattr(description, "start_time", None),
            "close_time": getattr(description, "close_time", None),
        }
        if status_name == "COMPLETED":
            try:
                response["result"] = await handle.result()
            except Exception as exc:
                log.exception("failed to read completed ingestion result")
                raise HTTPException(status_code=503, detail="ingestion result is unavailable") from exc
        elif status_name in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            try:
                await handle.result()
            except Exception as exc:
                cause = getattr(exc, "cause", None)
                response["failure"] = {"type": getattr(cause, "type", type(cause or exc).__name__)}
        return response

    @app.get("/v1/ingestions/{workflow_id}/processing-plan")
    async def ingestion_processing_plan(
        workflow_id: str,
        course_id: str = Query(..., min_length=1, max_length=80),
        target_output: Literal["ocr", "outline", "rag", "course", "questions", "translation", "audio"] = "ocr",
        target_language: Literal["en", "ar"] | None = None,
        source_language: Literal["en", "ar"] | None = None,
        content_kind: Literal["book", "course", "questions"] = "course",
        include_questions: bool = True,
    ) -> dict[str, Any]:
        """Plan dependencies from server-verified extraction and book progress."""
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        try:
            status_response = await ingestion_status(workflow_id, recovery_course_id=course_id)
            if status_response["status"] != "COMPLETED":
                raise HTTPException(status_code=409, detail="ingestion must complete before downstream planning")
            result = status_response["result"]
            parsed_ref = ArtifactRef.model_validate(result["parsed_book"]["ref"])
            if parsed_ref.stage != "ingest-parse-pdf" or not parsed_ref.uri.startswith(
                    f"s3://{settings.s3_bucket}/{course_id}/"):
                raise HTTPException(status_code=422, detail="parsed book is outside the requested course")
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            parsed = ParsedBook.model_validate_json(await run_in_threadpool(
                store.read, parsed_ref, "parsed-book.json"))
            progress = await book_progress(course_id, parsed.book_id)
            available: list[str] = ["ocr"]
            stages = progress.get("stages", {})
            parsed_sha = parsed_ref.sha256

            # Old progress records have no content lineage; never reuse them.
            outline_item = stages.get("outline", {})
            approved_ref_data = outline_item.get("approved_outline_ref")
            approved_ref = None
            if (outline_item.get("source_parsed_sha256") == parsed_sha and approved_ref_data):
                try:
                    candidate = ArtifactRef.model_validate(approved_ref_data)
                    if candidate.stage == "approve-outline" and candidate.uri.startswith(
                            f"s3://{settings.s3_bucket}/{course_id}/"):
                        approved_payload = json.loads(await run_in_threadpool(
                            store.read, candidate, "approved-outline.json"))
                        spine_ref = ArtifactRef.model_validate(approved_payload["spine_parsed_ref"])
                        outline = CourseOutline.model_validate(approved_payload["outline"])
                        if spine_ref.sha256 == parsed_sha and outline.spine_book_id == parsed.book_id:
                            approved_ref = candidate
                            available.append("outline")
                except Exception:
                    approved_ref = None

            index_item = stages.get("index", {})
            index_valid = False
            if (approved_ref and index_item.get("status") == "COMPLETED" and
                    index_item.get("source_parsed_sha256") == parsed_sha and
                    index_item.get("outline_sha256") == approved_ref.sha256):
                try:
                    index_state = await indexing_status(index_item["workflow_id"], recovery_course_id=course_id)
                    index_valid = (index_state.get("status") == "COMPLETED" and
                                   index_state.get("result", {}).get("outline_sha256") == approved_ref.sha256 and
                                   index_state.get("result", {}).get("course_id") == course_id)
                except Exception:
                    index_valid = False
            if index_valid:
                available.append("rag")

            qa_item = stages.get("qa", {})
            if (approved_ref and index_valid and qa_item.get("status") == "COMPLETED" and
                    qa_item.get("source_parsed_sha256") == parsed_sha and
                    qa_item.get("outline_sha256") == approved_ref.sha256):
                try:
                    qa_state = await course_qa_status(qa_item["workflow_id"])
                    qa = qa_state.get("course_qa", {})
                    if (qa_state.get("status") == "COMPLETED" and qa.get("status") == "final" and
                            qa.get("approved_outline_sha256") == approved_ref.sha256 and
                            qa.get("spine_parsed_sha256") == parsed_sha):
                        available.extend(["course", "questions"])
                except Exception:
                    pass
            # Translation reuse is deliberately not advertised here: the mobile
            # client must resolve the exact matching translation workflow/result,
            # not infer it from a generic COMPLETED stage flag. Deterministic
            # translation workflow IDs still make a repeat request idempotent.
            plan = plan_parsed_book(
                parsed, target_output, target_language,
                source_language=source_language,
                available_stages=available,
                content_kind=content_kind,
                include_questions=include_questions,
            )
            return {
                "course_id": course_id, "book_id": parsed.book_id,
                "source_artifact": parsed_ref.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
            }
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to build processing plan")
            raise HTTPException(status_code=503, detail="processing plan is unavailable") from exc

    @app.get("/v1/ingestions/{workflow_id}/outline")
    async def ingestion_outline(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        try:
            status_response = await ingestion_status(workflow_id)
            if status_response["status"] != "COMPLETED":
                raise HTTPException(status_code=409, detail="ingestion must complete before outline review")
            result = status_response["result"]
            outline_result = result.get("outline") if isinstance(result, dict) else None
            if not outline_result:
                raise HTTPException(status_code=409, detail="this ingestion has no spine outline")
            ref = ArtifactRef.model_validate(outline_result["ref"])
            store = app.state.artifact_store
            if store is None:
                store = ArtifactStore.from_settings(settings)
                app.state.artifact_store = store
            outline = CourseOutline.model_validate_json(
                await run_in_threadpool(store.read, ref, "course-outline.json"))
            return {"outline": outline.model_dump(mode="json"), "artifact": ref.model_dump(mode="json")}
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to read draft outline")
            raise HTTPException(status_code=503, detail="outline artifact is unavailable") from exc

    @app.post("/v1/ingestions/{workflow_id}/outline/approve-system", status_code=201)
    async def approve_system_outline(
        workflow_id: str, request: SystemOutlineApprovalRequest,
    ) -> dict[str, Any]:
        draft = await ingestion_outline(workflow_id)
        options = request
        draft_outline = CourseOutline.model_validate(draft["outline"])
        selected_supplementary_ids = list(options.supplementary_workflow_ids)
        draft_outline = CourseOutline.model_validate({
            **draft_outline.model_dump(mode="python"),
            "supplementary_book_ids": selected_supplementary_ids,
        })
        review = OutlineReview(
            review_type="system", outline=draft_outline,
            supplementary_workflow_ids=options.supplementary_workflow_ids,
            target_tokens=options.target_tokens, overlap_ratio=options.overlap_ratio,
            index_after_approval=options.index_after_approval)
        return await approve_outline(workflow_id, review)

    @app.post("/v1/ingestions/{workflow_id}/outline/approve", status_code=201)
    async def approve_outline(workflow_id: str, review: OutlineReview) -> dict[str, Any]:
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        try:
            status_response = await ingestion_status(workflow_id)
            if status_response["status"] != "COMPLETED":
                raise HTTPException(status_code=409, detail="ingestion must complete before outline review")
            result = status_response["result"]
            client = await get_temporal_client()
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to retrieve outline for review")
            raise HTTPException(status_code=503, detail="ingestion result is unavailable") from exc

        outline_result = result.get("outline") if isinstance(result, dict) else None
        parsed_result = result.get("parsed_book") if isinstance(result, dict) else None
        if not outline_result or not parsed_result:
            raise HTTPException(status_code=409, detail="this ingestion has no spine outline to review")
        draft_ref = ArtifactRef.model_validate(outline_result["ref"])
        parsed_ref = ArtifactRef.model_validate(parsed_result["ref"])
        expected_prefix = f"s3://{settings.s3_bucket}/{review.outline.course_id}/"
        if not parsed_ref.uri.startswith(expected_prefix):
            raise HTTPException(status_code=422, detail="spine ParsedBook artifact is outside the approved course")
        store = app.state.artifact_store
        if store is None:
            store = ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
        try:
            draft = CourseOutline.model_validate_json(
                await run_in_threadpool(store.read, draft_ref, "course-outline.json")
            )
            parsed = ParsedBook.model_validate_json(
                await run_in_threadpool(store.read, parsed_ref, "parsed-book.json")
            )
        except Exception as exc:
            log.exception("failed to load outline review artifacts")
            raise HTTPException(status_code=503, detail="outline artifacts are unavailable") from exc

        outline = review.outline
        if (
            outline.course_id != draft.course_id
            or outline.spine_book_id != draft.spine_book_id
            or outline.supplementary_book_ids != draft.supplementary_book_ids
        ):
            raise HTTPException(status_code=422, detail="reviewed outline identity does not match the draft")
        system_review = None
        if review.review_type == "system":
            try:
                outline = merge_bodyless_lessons(parsed, outline)
                outline = include_leading_blocks_in_first_lesson(parsed, outline)
                system_review = review_outline(parsed, outline)
                outline = CourseOutline.model_validate({**outline.model_dump(mode="python"), "requires_review": False, "schema_version": SCHEMA_VERSION})
            except (OutlineReviewRejected, ValidationError) as exc:
                raise HTTPException(status_code=422, detail=f"system structural review rejected: {exc}") from exc
        block_positions = {block.block_id: index for index, block in enumerate(parsed.blocks)}
        known_lessons: set[str] = set()
        for module in outline.modules:
            for lesson in module.lessons:
                span = lesson.spine_span
                if span.start_block not in block_positions or span.end_block not in block_positions:
                    raise HTTPException(status_code=422, detail="lesson span references an unknown spine block")
                if block_positions[span.start_block] > block_positions[span.end_block]:
                    raise HTTPException(status_code=422, detail="lesson span ends before it starts")
                if span.pages[0] < 1 or span.pages[1] < span.pages[0] or span.pages[1] > parsed.page_count:
                    raise HTTPException(status_code=422, detail="lesson page span is outside the parsed book")
                if any(prior not in known_lessons for prior in lesson.prior_lessons):
                    raise HTTPException(status_code=422, detail="lesson prerequisite must refer to an earlier lesson")
                known_lessons.add(lesson.lesson_id)

        if set(review.supplementary_workflow_ids) != set(outline.supplementary_book_ids):
            raise HTTPException(status_code=422, detail="provide a completed ingestion workflow for every supplementary book")
        supplementary_parsed_refs: dict[str, dict[str, Any]] = {}
        for book_id, supplementary_workflow_id in review.supplementary_workflow_ids.items():
            if not is_workflow_id("ingest", supplementary_workflow_id):
                raise HTTPException(status_code=422, detail=f"invalid ingestion workflow for supplementary book {book_id}")
            try:
                supplement_handle = client.get_workflow_handle(supplementary_workflow_id)
                supplement_description = await supplement_handle.describe()
                supplement_status = getattr(getattr(supplement_description, "status", None), "name", "")
                if supplement_status != "COMPLETED":
                    raise HTTPException(status_code=409, detail=f"supplementary book {book_id} is not parsed yet")
                supplement_result = await supplement_handle.result()
                supplement_ref = ArtifactRef.model_validate(supplement_result["parsed_book"]["ref"])
                expected_prefix = f"s3://{settings.s3_bucket}/{outline.course_id}/"
                if not supplement_ref.uri.startswith(expected_prefix):
                    raise HTTPException(status_code=422, detail=f"supplementary artifact is outside course scope: {book_id}")
                supplement_parsed = ParsedBook.model_validate_json(
                    await run_in_threadpool(store.read, supplement_ref, "parsed-book.json")
                )
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("failed to resolve supplementary ParsedBook artifact")
                raise HTTPException(status_code=503, detail=f"supplementary book {book_id} result is unavailable") from exc
            try:
                _validate_course_supplementary_identity(supplement_parsed, book_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            supplementary_parsed_refs[book_id] = supplement_ref.model_dump(mode="json")

        provenance = (OutlineApprovalProvenance(
            review_type="system", system_id=POLICY_ID, policy_version=POLICY_VERSION,
            evidence_sha256=system_review.evidence_sha256,
        ) if system_review else OutlineApprovalProvenance(
            review_type="human", reviewer_id=review.reviewer_id,
        ))
        payload = {
            "outline": outline.model_dump(mode="json"),
            "approval_provenance": provenance.model_dump(mode="json"),
            "system_review_checks": list(system_review.checks) if system_review else None,
            "reviewer_id": review.reviewer_id,
            "approved_at": datetime.now(UTC).isoformat(),
            "draft_ref": draft_ref.model_dump(mode="json"),
            "spine_parsed_ref": parsed_ref.model_dump(mode="json"),
            "source_identity": await run_in_threadpool(artifact_source_identity, store, parsed_ref),
            "supplementary_parsed_refs": supplementary_parsed_refs,
        }
        payload_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        key = stage_key("approve-outline", "2", [draft_ref.sha256, sha256(payload_bytes).hexdigest()],
                        {"approval_provenance": provenance.model_dump(mode="json")})
        try:
            approved_ref = await run_in_threadpool(
                store.put, outline.course_id, "approve-outline", key,
                {"approved-outline.json": payload_bytes},
            )
        except Exception as exc:
            log.exception("failed to store approved outline")
            raise HTTPException(status_code=503, detail="could not store approved outline") from exc
        index_workflow_id = None
        if review.index_after_approval:
            index_input = IndexApprovedBookInput(
                course_id=outline.course_id, parsed_ref=parsed_ref.model_dump(mode="json"),
                approved_outline_ref=approved_ref.model_dump(mode="json"),
                supplementary_parsed_refs=supplementary_parsed_refs,
                activity_task_queue=settings.indexing_task_queue,
                target_tokens=review.target_tokens,
                overlap_ratio=review.overlap_ratio,
            )
            index_workflow_id = "b2c-index-" + hashlib.sha256(
                json.dumps(index_input.model_dump(mode="json"), sort_keys=True).encode("utf-8")
            ).hexdigest()
            try:
                await client.start_workflow(
                    IndexApprovedBookWorkflow.run, index_input,
                    id=index_workflow_id, task_queue=settings.indexing_task_queue,
                )
            except WorkflowAlreadyStartedError:
                pass
            except Exception as exc:
                log.exception("approved outline persisted, but indexing did not start")
                raise HTTPException(status_code=503, detail={
                    "message": "outline was approved but indexing could not be queued",
                    "approved_outline": approved_ref.model_dump(mode="json"),
                    "index_workflow_id": index_workflow_id,
                }) from exc
        await remember_book_progress(outline.course_id, outline.spine_book_id, "outline",
                                     index_workflow_id or workflow_id,
                                     {"approved_outline_ref": approved_ref.model_dump(mode="json"),
                                      "source_parsed_sha256": parsed_ref.sha256,
                                      "source_identity": payload["source_identity"],
                                      "source_file_sha256": payload["source_identity"].get("source_file_sha256"),
                                      "source_ingest_workflow_id": workflow_id,
                                      "approval_provenance": provenance.model_dump(mode="json")})
        if index_workflow_id:
            await remember_book_progress(outline.course_id, outline.spine_book_id, "index",
                                         index_workflow_id, {"source_parsed_sha256": parsed_ref.sha256,
                                         "source_identity": payload["source_identity"],
                                         "source_file_sha256": payload["source_identity"].get("source_file_sha256"),
                                         "source_ingest_workflow_id": workflow_id,
                                         "outline_sha256": approved_ref.sha256,
                                         "index_parameters": {"target_tokens": review.target_tokens,
                                         "overlap_ratio": review.overlap_ratio, "embedding_model": index_input.embedding_model}})
        return {"approved_outline": approved_ref.model_dump(mode="json"),
                "reviewer_id": review.reviewer_id, "review_type": review.review_type,
                "approval_provenance": provenance.model_dump(mode="json"),
                "approved_at": payload["approved_at"], "index_workflow_id": index_workflow_id}

    @app.get("/v1/indexings/{workflow_id}")
    async def indexing_status(workflow_id: str, recovery_course_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("index", workflow_id, "indexing workflow not found")
        if recovery_course_id is not None:
            require_course_id(recovery_course_id)
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            try:
                recovered = await recover_index(store, PostgresChunkStore(lambda: psycopg.connect(settings.pg_dsn)),
                                                workflow_id, course_id=recovery_course_id)
            except Exception as recovery_exc:
                log.exception("failed to recover committed index")
                raise HTTPException(status_code=503, detail="stored index is unavailable") from recovery_exc
            if recovered is None:
                raise HTTPException(status_code=404, detail="index history expired and no matching committed index is available") from exc
            return {"workflow_id": workflow_id, "status": "COMPLETED", "result": recovered,
                    "recovered_from_artifact": True, "workflow_history_available": False,
                    "completion_scope": "persisted_index"}
        except Exception as exc:
            log.exception("failed to read indexing status")
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
        status_name = getattr(getattr(description, "status", None), "name", "")
        response: dict[str, Any] = {"workflow_id": workflow_id, "status": status_name}
        if status_name == "COMPLETED":
            try:
                response["result"] = await handle.result()
            except Exception as exc:
                log.exception("failed to read completed indexing result")
                raise HTTPException(status_code=503, detail="indexing result is unavailable") from exc
        elif status_name in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            try:
                await handle.result()
            except Exception as exc:
                cause = getattr(exc, "cause", None)
                response["failure"] = {"type": getattr(cause, "type", type(cause or exc).__name__)}
        return response

    @app.get("/v1/indexings/{workflow_id}/chunks")
    async def indexing_chunks(workflow_id: str, course_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("index", workflow_id, "indexing workflow not found")
        status = await indexing_status(workflow_id, recovery_course_id=course_id)
        if status["status"] != "COMPLETED":
            raise HTTPException(status_code=409, detail="indexing is not complete")
        try:
            result = status["result"]
            course_id = result.get("course_id", "")
            if not course_id:
                raise HTTPException(status_code=503, detail="index course is missing")
            # The workflow's result is versioned and may not include an artifact
            # reference (older workers returned only chunk IDs/counts). Read the
            # already-persisted, course-scoped chunks from the indexing store.
            store = PostgresChunkStore(lambda: psycopg.connect(settings.pg_dsn))
            chunks = await run_in_threadpool(store.list_chunks, course_id)
            if not chunks:
                raise ValueError("no persisted index chunks found")
            return {"workflow_id": workflow_id, "course_id": course_id,
                    "chunk_count": len(chunks),
                    "chunks": [chunk.model_dump(mode="json") for chunk in chunks]}
        except HTTPException: raise
        except Exception as exc:
            log.exception("failed to read completed index chunks")
            raise HTTPException(status_code=503,detail="index chunks are unavailable") from exc

    async def original_translation_input(workflow_id: str) -> TranslateContentInput:
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        key = f"_system/translation-requests/{workflow_id}.json"
        saved_request = await s3_json(store.s3, settings.s3_bucket, key)
        if saved_request is not None:
            inp = TranslateContentInput.model_validate(saved_request)
        else:
            client = await get_temporal_client()
            history = await client.get_workflow_handle(workflow_id).fetch_history()
            started = next((e for e in history.events if e.HasField("workflow_execution_started_event_attributes")), None)
            if started is None:
                raise ValueError("original translation input is unavailable")
            decoded = await client.data_converter.decode(started.workflow_execution_started_event_attributes.input.payloads, [TranslateContentInput])
            if len(decoded) != 1:
                raise ValueError("original translation input is ambiguous")
            inp = TranslateContentInput.model_validate(decoded[0])
        ref = ArtifactRef.model_validate(inp.source_artifact_ref)
        expected = "ingest-parse-pdf" if inp.source_kind == "book" else "generate-course-qa"
        if ref.stage != expected or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{inp.course_id}/"):
            raise ValueError("original translation source failed scope validation")
        return inp

    @app.post("/v1/courses/{course_id}/translations", status_code=202)
    async def translate_course_content(course_id: str, request: TranslationRequest) -> dict[str, Any]:
        source = request.source_artifact_ref
        expected_stage = "ingest-parse-pdf" if request.source_kind == "book" else "generate-course-qa"
        if source.stage != expected_stage or not source.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
            raise HTTPException(status_code=422, detail="source artifact is outside this course or has the wrong stage")
        if request.source_language == request.target_language:
            raise HTTPException(status_code=422, detail="source and target language must differ")
        await verified_ref(get_store(), course_id, source, expected_stage, "source artifact")
        try:
            if request.resume_from_workflow_id:
                previous = await original_translation_input(request.resume_from_workflow_id)
                if previous.course_id != course_id or previous.source_artifact_ref != source.model_dump(mode="json") or any(getattr(previous,name) != getattr(request,name) for name in
                    ("source_kind", "source_language", "target_language", "max_tokens", "contextual_glossary")):
                    raise HTTPException(status_code=422, detail="resume request must match the validated original translation input")
            correction_ids = []
            if request.resume_from_workflow_id:
                correction_store = app.state.artifact_store
                corrections = await run_in_threadpool(translation_manual_overrides, correction_store, previous, request.resume_from_workflow_id)
                correction_ids = sorted(x["revision_id"] for x in corrections)
            correction_identity = hashlib.sha256(json.dumps(correction_ids).encode()).hexdigest() if correction_ids else ""
            client = await get_temporal_client()
            if request.provider_id:
                manager = app.state.model_lifecycle
                provider = manager.provider(request.provider_id)
                if "translation" not in provider.stages or not provider.task_queue:
                    raise HTTPException(409, "provider has no enabled translation worker route")
                if (await manager.status(provider.id))["state"] != "ready":
                    raise HTTPException(503, "selected translation model service is not ready")
                selected_model, selected_queue, selected_provider = provider.model, provider.task_queue, provider.id
                selected_translation_engine = provider.translation_engine
            elif request.resume_from_workflow_id:
                selected_model = previous.selected_model_id or settings.llm_model_id
                selected_queue = previous.activity_task_queue
                selected_provider = previous.selected_provider_id or "configured-llm"
                selected_translation_engine = previous.selected_translation_engine
            else:
                selected_model, selected_queue, selected_provider = generation_profile("translation")
                configured_provider = app.state.model_lifecycle.providers.get(selected_provider)
                selected_translation_engine = (
                    configured_provider.translation_engine if configured_provider else "b2c"
                )
            nonce = uuid.uuid4().hex if request.force_regenerate else ""
            reasoning_identity = f"\0reasoning:{settings.llm_reasoning_effort}" if settings.llm_reasoning_effort else ""
            suffix = hashlib.sha256((course_id + source.sha256 + request.source_kind +
                request.source_language + request.target_language + selected_model +
                str(request.max_tokens) + "\0provider:" + selected_provider + "\0queue:" + selected_queue + (request.resume_from_workflow_id or "") +
                reasoning_identity + correction_identity + "\0glossary:" + glossary_identity(request.contextual_glossary) + nonce).encode()).hexdigest()
            workflow_id = "b2c-translate-" + suffix
            inp = TranslateContentInput(course_id=course_id, source_artifact_ref=source.model_dump(mode="json"),
                source_kind=request.source_kind, source_language=request.source_language,
                target_language=request.target_language, max_tokens=request.max_tokens,
                resume_from_workflow_id=request.resume_from_workflow_id, manual_override_revision_ids=correction_ids,
                activity_task_queue=selected_queue, selected_model_id=selected_model,
                selected_provider_id=selected_provider,
                selected_translation_engine=selected_translation_engine,
                contextual_glossary=request.contextual_glossary)
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
                Key=f"_system/translation-requests/{workflow_id}.json",
                Body=inp.model_dump_json().encode("utf-8"), ContentType="application/json")
            try:
                await client.start_workflow(TranslateContentWorkflow.run, inp, id=workflow_id, task_queue=inp.activity_task_queue)
            except WorkflowAlreadyStartedError:
                pass
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to queue translation workflow")
            raise HTTPException(status_code=503, detail="translation could not be queued") from exc
        try:
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            book_id = None
            source_parsed_sha256 = None
            outline_sha256 = None
            if request.source_kind == "book":
                parsed_book = ParsedBook.model_validate_json(await run_in_threadpool(
                    store.read, source, "parsed-book.json"))
                book_id = parsed_book.book_id
                source_parsed_sha256 = source.sha256
            else:
                qa_source = json.loads(await run_in_threadpool(store.read, source, "course-qa.json"))
                book_id = qa_source.get("spine_book_id")
                source_parsed_sha256 = qa_source.get("spine_parsed_sha256")
                outline_sha256 = qa_source.get("approved_outline_sha256")
            if book_id and source_parsed_sha256:
                await remember_book_progress(course_id, book_id,
                    "book_translation" if request.source_kind == "book" else "translation", workflow_id, {
                        "source_parsed_sha256": source_parsed_sha256,
                        "source_artifact_sha256": source.sha256,
                        "source_identity": await run_in_threadpool(artifact_source_identity, store, source),
                        "source_kind": request.source_kind,
                        "target_language": request.target_language,
                        "outline_sha256": outline_sha256,
                    })
        except Exception:
            log.exception("could not associate translation workflow with its exact book source")
        return {"course_id": course_id, "workflow_id": workflow_id, "status_url": f"/v1/translations/{workflow_id}"}

    @app.get("/v1/translations/{workflow_id}")
    async def translation_status(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        client = await connected_temporal()
        handle, description = await describe_or_404(client, workflow_id, "translation workflow not found")
        status_name = getattr(getattr(description, "status", None), "name", "")
        response: dict[str, Any] = {"workflow_id": workflow_id, "status": status_name}
        if status_name == "RUNNING":
            # Durable progress is written by the activity itself. Temporal event enum
            # encodings vary across SDK versions, so do not infer queue state from a
            # magic event number; an absent progress object is handled below as queued.
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            progress_key = f"_system/translation-progress/{workflow_id}.json"
            try:
                progress = await s3_json(store.s3, settings.s3_bucket, progress_key)
                if progress is None:
                    raise _MissingObject(progress_key)
                now = datetime.now(UTC)
                checkpoint = progress.get("last_progress_at") or progress.get("started_at") or progress.get("updated_at")
                age_seconds = None
                if checkpoint:
                    age_seconds = max(0, int((now - datetime.fromisoformat(checkpoint.replace("Z", "+00:00"))).total_seconds()))
                state = "active" if age_seconds is None or age_seconds < 600 else "no_recent_checkpoint"
                progress["progress_state"] = state
                progress["checkpoint_age_seconds"] = age_seconds
                done = int(progress.get("completed_segments", 0))
                total = int(progress.get("total_segments", 0))
                progress["remaining_segments"] = max(0, total - done) if total else None
                progress["successful_segments"] = done
                current_segment_ids = set(progress.get("current_segment_ids", []))
                failed_segment_ids = set(progress.get("failed_segment_ids", []))
                progress["active_segments"] = len(current_segment_ids)
                # deferred_batches is transient and excludes the batch currently
                # being retried; unresolved per-segment failures are durable.
                progress["deferred_units_count"] = len(failed_segment_ids - current_segment_ids)
                progress["failed_segments"] = len(failed_segment_ids)
                progress["completion_percent"] = round(100 * done / total, 1) if total else None
                if progress.get("target_language") == "ar" and not isinstance(progress.get("arabic_correction"), dict):
                    progress["arabic_correction"] = {
                        "status": "waiting_for_translation",
                        "processed_segments": 0,
                        "total_segments": total,
                        "remaining_segments": total,
                        "accepted_edits": 0,
                        "rejected_edits": 0,
                        "message": "يبدأ التصحيح العربي بعد انتهاء الترجمة؛ هذا عداد مستقل عن عداد الترجمة.",
                    }
                citations = progress.get("completed_citations", [])
                current = progress.get("current_citations", [])
                if state == "no_recent_checkpoint":
                    progress["message"] = f"\u0644\u0645 \u064a\u0633\u062c\u0644 \u062a\u0642\u062f\u0645 \u0645\u0646\u0630 {age_seconds // 60} \u062f\u0642\u064a\u0642\u0629\u061b \u0642\u062f \u064a\u0643\u0648\u0646 \u0627\u0644\u0637\u0644\u0628 \u0628\u0637\u064a\u0626\u064b\u0627 \u0623\u0648 \u0645\u062a\u0648\u0642\u0641\u064b\u0627."
                else:
                    progress["message"] = (
                        f"\u062a\u0631\u062c\u0645\u0629 {done}/{total} \u0645\u0642\u0637\u0639\u064b\u0627\u061b "
                        f"\u0627\u0644\u062f\u0641\u0639\u0629 {progress.get('batch_index', 0)}/"
                        f"{progress.get('total_batches', 0)}. "
                        f"\u0627\u0644\u0635\u0641\u062d\u0627\u062a \u0627\u0644\u0645\u0643\u062a\u0645\u0644\u0629: {', '.join(citations[-12:]) or '\u062c\u0627\u0631\u064d \u0627\u0644\u062a\u062d\u0636\u064a\u0631'}; "
                        f"\u0627\u0644\u062f\u0641\u0639\u0629 \u0627\u0644\u062d\u0627\u0644\u064a\u0629: {', '.join(current) or '\u0644\u0627 \u062a\u0648\u062c\u062f \u062f\u0641\u0639\u0629 \u0646\u0634\u0637\u0629'}."
                    )
                response["progress"] = progress
            except Exception as exc:
                if not isinstance(exc, _MissingObject):
                    log.exception("failed to read translation progress checkpoint")
                    raise HTTPException(status_code=503, detail="translation progress is unavailable") from exc
                response["progress"] = {
                    "progress_state": "not_reported",
                    "checkpointing": False,
                    "message": "\u0627\u0644\u0631\u062d\u0644\u0629 \u0642\u064a\u062f \u0627\u0644\u062a\u0646\u0641\u064a\u0630 \u0644\u0643\u0646 \u0644\u0627 \u064a\u0648\u062c\u062f \u0633\u062c\u0644 \u062a\u0642\u062f\u0645 \u0645\u062d\u0641\u0648\u0638; \u0644\u0627 \u064a\u0645\u0643\u0646 \u062a\u062d\u062f\u064a\u062f \u0627\u0644\u0645\u0642\u0627\u0637\u0639 \u0627\u0644\u0645\u0646\u062c\u0632\u0629 \u062d\u062a\u0649 \u0627\u0644\u0622\u0646.",
                }
        if status_name == "COMPLETED":
            try:
                result = await handle.result()
            except Exception as exc:
                log.exception("failed to read completed translation result")
                raise HTTPException(status_code=503, detail="translation result is unavailable") from exc
            response["result"] = result
            count = int(result.get("segment_count", 0))
            total = int(result.get("total_segments", count))
            partial = result.get("status") == "partial"
            response["result_status"] = "partial" if partial else "completed"
            response["progress"] = {"completed_segments": count, "successful_segments": count,
                                     "total_segments": total, "active_segments": 0,
                                     "deferred_units_count": 0,
                                     "failed_segments": len(result.get("failed_segment_ids", [])),
                                     "failed_segment_ids": result.get("failed_segment_ids", []),
                                     "missing_segment_ids": result.get("missing_segment_ids", []),
                                     "retry_segment_ids": result.get("retry_segment_ids", []),
                                     "retry_segments": max(0, total - count),
                                     "completion_percent": round(100 * count / total, 1) if total else 0,
                                     "progress_state": "partial" if partial else "completed",
                                     "message": f"حُفظت ترجمة {count}/{total} مقطعًا؛ يمكن إعادة الأجزاء الناقصة." if partial else f"اكتملت ترجمة {count} مقطعًا."}
            course_id = result.get("course_id", "")
            ref = ArtifactRef.model_validate(result["artifact"])
            if ref.stage != "translate-content" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
                raise HTTPException(status_code=503, detail="translation artifact failed scope validation")
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            try:
                response["translation"] = json.loads(await run_in_threadpool(store.read, ref, "translation.json"))
            except Exception as exc:
                log.exception("failed to read completed translation artifact")
                raise HTTPException(status_code=503, detail="translation artifact is unavailable") from exc
        elif status_name in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            try:
                progress_key = f"_system/translation-progress/{workflow_id}.json"
                progress = await s3_json(store.s3, settings.s3_bucket, progress_key)
                if progress is None:
                    raise _MissingObject(progress_key)
                progress["successful_segments"] = int(progress.get("completed_segments", 0))
                progress["active_segments"] = len(progress.get("current_segment_ids", []))
                progress["deferred_units_count"] = len(progress.get("deferred_batches", []))
                progress["failed_segments"] = len(progress.get("failed_segment_ids", []))
                total = int(progress.get("total_segments", 0))
                done = int(progress.get("completed_segments", 0))
                progress["completion_percent"] = round(100 * done / total, 1) if total else None
                response["progress"] = progress
            except Exception as progress_error:
                if not isinstance(progress_error, _MissingObject):
                    log.exception("failed to read terminal translation progress checkpoint")
                    raise HTTPException(status_code=503, detail="translation progress is unavailable") from progress_error
                response["progress"] = {"progress_state": "not_reported", "completed_segments": 0,
                                         "total_segments": 0, "message": "لا توجد نقاط تقدم محفوظة لهذا التشغيل."}
            try:
                await handle.result()
            except Exception as exc:
                cause = getattr(exc, "cause", None)
                response["failure"] = {"type": getattr(cause, "type", type(cause or exc).__name__)}
        try:
            original = await original_translation_input(workflow_id)
            from temporalio.api.enums.v1 import PendingActivityState
            pending_activities = getattr(getattr(description, "raw_description", None), "pending_activities", ())
            has_started_activity = any(a.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
                                       for a in pending_activities)
            progress_state = response.get("progress", {}).get("progress_state")
            if status_name == "RUNNING" and has_started_activity and progress_state == "no_recent_checkpoint":
                heartbeats = [getattr(a, "last_heartbeat_time", None) for a in pending_activities
                              if a.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED]
                heartbeats = [value.ToDatetime() if hasattr(value, "ToDatetime") else value
                              for value in heartbeats if value is not None]
                if heartbeats:
                    latest_heartbeat = max(heartbeats)
                    if latest_heartbeat.tzinfo is None:
                        latest_heartbeat = latest_heartbeat.replace(tzinfo=UTC)
                    heartbeat_age = max(0, int((datetime.now(UTC) - latest_heartbeat).total_seconds()))
                    if heartbeat_age <= 180:
                        progress = response["progress"]
                        progress["progress_state"] = "active"
                        progress["activity_heartbeat_age_seconds"] = heartbeat_age
                        progress["segment_checkpoint_state"] = "waiting_during_active_request"
                        progress["message"] = (
                            f"العامل نشط ويرسل نبضات حياة؛ لم يُحفظ مقطع جديد منذ "
                            f"{int(progress.get('checkpoint_age_seconds', 0)) // 60} دقيقة، "
                            "وقد يكون النموذج ما زال يعالج الطلب الحالي."
                        )
            if (status_name == "RUNNING" and original.resume_from_workflow_id
                    and (progress_state == "not_reported"
                         or (progress_state == "no_recent_checkpoint" and not has_started_activity))):
                # A continuation can wait in the queue before its activity writes
                # its first heartbeat. Show validated immutable parent checkpoints.
                units = await translation_units(workflow_id)
                total, done = units["total_segments"], units["saved_segments"]
                parent_key = f"_system/translation-progress/{original.resume_from_workflow_id}.json"
                try:
                    parent_progress = await s3_json(store.s3, settings.s3_bucket, parent_key) or {}
                    missing = {u["segment_id"] for u in units["units"] if u["checkpoint_status"] != "saved"}
                    failed_ids = sorted(set(parent_progress.get("failed_segment_ids", [])) & missing)
                except Exception:
                    failed_ids = []
                response["progress"] = {
                    "progress_state": "queued", "checkpointing": True,
                    "inherited_checkpoint_workflow_id": original.resume_from_workflow_id,
                    "total_segments": total, "completed_segments": done,
                    "successful_segments": done, "checkpointed_segments": units["checkpointed_segments"],
                    "remaining_segments": max(0, total - done), "retry_segments": units["retry_segments"],
                    "suspect_segments": units["suspect_segments"], "active_segments": 0,
                    "failed_segments": len(failed_ids), "failed_segment_ids": failed_ids, "deferred_units_count": 0,
                    "completion_percent": round(100 * done / total, 1) if total else 0,
                    "message": f"محفوظ {done}/{total} مقطعًا من نقطة الاستئناف؛ ينتظر بدء المعالجة."
                }
            if status_name in {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
                units = await translation_units(workflow_id)
                response["manual_correction_count"] = units["manual_correction_count"]
                progress = response.setdefault("progress", {})
                missing_ids = {u["segment_id"] for u in units["units"] if u["checkpoint_status"] == "not_saved"}
                confirmed_failed_ids = set(progress.get("failed_segment_ids", [])) & missing_ids
                progress["failed_segments"] = len(confirmed_failed_ids)
                progress["failed_segment_ids"] = sorted(confirmed_failed_ids)
                progress["not_saved_segments"] = len(missing_ids)
                progress["missing_segment_ids"] = sorted(missing_ids)
                progress["retry_segment_ids"] = [u["segment_id"] for u in units["units"] if u["checkpoint_status"] != "saved"]
                progress["suspect_segments"] = units["suspect_segments"]
                progress["suspect_segment_ids"] = units["suspect_segment_ids"]
                progress["suspect_details"] = units["suspect_details"]
                progress["checkpointed_segments"] = units["checkpointed_segments"]
                progress["completeness_policy"] = units["completeness_policy"]
                progress["remaining_unconfirmed_segments"] = len(missing_ids - confirmed_failed_ids)
                progress.update({"total_segments":units["total_segments"],"completed_segments":units["saved_segments"],
                    "successful_segments":units["saved_segments"],"retry_segments":units["retry_segments"],
                    "active_segments":0,"completion_percent":round(100*units["saved_segments"]/units["total_segments"],1) if units["total_segments"] else 0})
            response["course_id"] = original.course_id
            response["resume_request"] = {name: getattr(original, name) for name in
                ("source_artifact_ref", "source_kind", "source_language", "target_language", "max_tokens")}
            if original.contextual_glossary is not None:
                response["resume_request"]["contextual_glossary"] = original.contextual_glossary.model_dump(mode="json")
        except Exception:
            response["resume_unavailable_reason"] = "original translation input could not be validated"
        return response

    @app.get("/v1/translations/{workflow_id}/checkpoints")
    async def translation_checkpoints(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        store=app.state.artifact_store or ArtifactStore.from_settings(settings); app.state.artifact_store=store
        prefix=f"_system/translation-checkpoints/{workflow_id}/"
        def read_batches() -> list[dict[str,Any]]:
            output=[]; token=None
            while True:
                args={"Bucket":settings.s3_bucket,"Prefix":prefix}
                if token: args["ContinuationToken"]=token
                page=store.s3.list_objects_v2(**args)
                for entry in page.get("Contents",[]):
                    key=entry["Key"]; relative=key[len(prefix):]; parts=relative.split("/")
                    if len(parts)!=2 or not parts[0] or not parts[1].startswith("batch-") or not parts[1].endswith(".json"): continue
                    try:
                        payload=json.loads(store.s3.get_object(Bucket=settings.s3_bucket,Key=key)["Body"].read())
                        if payload.get("run_fingerprint")!=parts[0] or int(payload.get("batch_index",0))<1: continue
                        segments=payload.get("segments")
                        if not isinstance(segments,list) or not segments: continue
                        if any(not isinstance(x,dict) or not x.get("segment_id") or not x.get("text") for x in segments): continue
                    except Exception: continue
                    output.append({"run_fingerprint":parts[0],"batch_index":payload["batch_index"],
                        "source_sha256":payload.get("source_sha256"),"model_id":payload.get("model_id"),"source_language":payload.get("source_language"),
                        "target_language":payload.get("target_language"),"completed_at":payload.get("completed_at"),
                        "segments":segments})
                if not page.get("IsTruncated"): break
                token=page.get("NextContinuationToken")
                if not token: raise ValueError("truncated translation checkpoint listing has no continuation token")
            return output
        try:
            batches=await run_in_threadpool(read_batches)
            return {"workflow_id":workflow_id,"batch_count":len(batches),"batches":batches}
        except Exception as exc:
            log.exception("failed to read translation batch checkpoints")
            raise HTTPException(status_code=503,detail="translation checkpoints are unavailable") from exc

    @app.get("/v1/translations/{workflow_id}/units")
    async def translation_units(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        try:
            original = await original_translation_input(workflow_id)
            store = app.state.artifact_store
            source = await run_in_threadpool(translation_source_segments, store, original)
            # A resumed workflow gets a new ID but must keep exposing its
            # parent's durable work while it processes the remaining units.
            # Timestamp ordering lets newer child checkpoints supersede older ones.
            checkpoint_workflow_ids = list(dict.fromkeys(
                item for item in (original.resume_from_workflow_id, workflow_id) if item
            ))
            checkpoint_batches: list[dict[str, Any]] = []
            for checkpoint_workflow_id in checkpoint_workflow_ids:
                checkpoint_batches.extend(
                    (await translation_checkpoints(checkpoint_workflow_id))["batches"]
                )
            source_ids = {x.segment_id for x in source}
            saved: dict[str, dict[str, Any]] = {}
            for batch in sorted(checkpoint_batches, key=lambda b: b.get("completed_at") or ""):
                if batch.get("source_sha256") != original.source_artifact_ref["sha256"] or batch.get("source_language") != original.source_language or batch.get("target_language") != original.target_language:
                    continue
                for segment in batch["segments"]:
                    if segment["segment_id"] in source_ids:
                        saved[segment["segment_id"]] = segment
            overrides: list[dict[str, Any]] = []
            for override_workflow_id in checkpoint_workflow_ids:
                overrides.extend(await run_in_threadpool(
                    translation_manual_overrides, store, original, override_workflow_id
                ))
            overrides.sort(key=lambda item: (item["created_at"], item["revision_id"]))
            latest = {x["segment_id"]:x for x in overrides}
            units = [{"segment_id":x.segment_id,"source_text":x.text,"citations":x.citations,
                "source_artifact_ref":original.source_artifact_ref,
                "translated_text":latest.get(x.segment_id, saved.get(x.segment_id, {})).get("text"),
                "original_translated_text":saved.get(x.segment_id,{}).get("text"),
                "revisions":[r for r in overrides if r["segment_id"] == x.segment_id],
                "manual_override_revision":latest.get(x.segment_id, {}).get("revision_id"),
                "checkpoint_status":"saved" if x.segment_id in saved else "not_saved"} for x in source]
            for unit in units:
                text = unit["translated_text"]
                issue = translation_completeness_issue(unit["source_text"], text, target_language=original.target_language) if text is not None else None
                unit["completeness_issue"] = issue
                unit["suspect"] = issue is not None
                if issue:
                    unit["checkpoint_status"] = "suspect"
            suspect = [u for u in units if u["suspect"]]
            valid_saved = sum(u["checkpoint_status"] == "saved" for u in units)
            return {"workflow_id":workflow_id,"course_id":original.course_id,"source_kind":original.source_kind,
                "total_segments":len(source),"saved_segments":valid_saved,"checkpointed_segments":len(saved),
                "retry_segments":len(source)-valid_saved,"suspect_segments":len(suspect),
                "suspect_segment_ids":[u["segment_id"] for u in suspect],
                "suspect_details":[{"segment_id":u["segment_id"], **u["completeness_issue"]} for u in suspect],
                "completeness_policy":"exact ordered numeric sequence; recognized rating-table cells/literals preserved; balanced-source brackets; no introduced markup/provenance; long English prose blocks in Arabic output are rejected; conservative length triage (source>=200 chars and target/source<0.45)",
                "manual_correction_count":len(latest),"units":units}
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("translation units unavailable")
            raise HTTPException(status_code=503, detail="original source or checkpoints could not be verified") from exc

    async def terminal_translation(workflow_id: str) -> TranslateContentInput:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        client = await connected_temporal()
        _, description = await describe_or_404(client, workflow_id, "translation workflow not found")
        if getattr(getattr(description, "status", None), "name", "") not in {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            raise HTTPException(status_code=409, detail="selected-unit changes require a terminal parent translation")
        return await original_translation_input(workflow_id)

    @app.post("/v1/translations/{workflow_id}/retranslations", status_code=202)
    async def retranslate_unit(workflow_id: str, request: TranslationRetranslateRequest) -> dict[str, Any]:
        original = await terminal_translation(workflow_id)
        store = app.state.artifact_store
        units = await translation_units(workflow_id)
        matches = [x for x in units["units"] if x["segment_id"] == request.segment_id]
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="selected segment missing or ambiguous")
        unit = matches[0]
        # Persist exact recovered history input, so worker checks the same original request.
        await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
            Key=f"_system/translation-requests/{workflow_id}.json", Body=original.model_dump_json().encode(), ContentType="application/json")
        inp = RetranslateSegmentInput(original=original, parent_workflow_id=workflow_id,
            segment_id=request.segment_id, source_text_sha256=hashlib.sha256(unit["source_text"].encode()).hexdigest())
        candidate_id = "b2c-retranslate-" + uuid.uuid4().hex
        client = await get_temporal_client()
        await client.start_workflow(RetranslateSegmentWorkflow.run, inp, id=candidate_id, task_queue=original.activity_task_queue)
        return {"workflow_id":workflow_id,"candidate_workflow_id":candidate_id,"segment_id":request.segment_id,
            "status":"queued","other_segments_unchanged":True}

    async def selected_candidate(workflow_id: str, candidate_id: str, revision_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("retranslate", candidate_id, "candidate not found")
        original = await original_translation_input(workflow_id)
        store = app.state.artifact_store
        record = await s3_json(store.s3, settings.s3_bucket, (f"_system/translation-candidate-revisions/{candidate_id}/{revision_id}.json" if revision_id else f"_system/translation-candidates/{candidate_id}.json"))
        if record is None:
            raise HTTPException(status_code=409, detail="candidate is not ready for review")
        sources = await run_in_threadpool(translation_source_segments, store, original)
        matches = [x for x in sources if x.segment_id == record.get("segment_id")]
        if (record.get("parent_workflow_id") != workflow_id or record.get("candidate_workflow_id") != candidate_id
            or record.get("source_artifact_ref") != original.source_artifact_ref
            or record.get("source_language") != original.source_language or record.get("target_language") != original.target_language
            or len(matches) != 1 or record.get("source_text_sha256") != hashlib.sha256(matches[0].text.encode()).hexdigest()):
            raise HTTPException(status_code=422, detail="candidate source identity mismatch")
        if revision_id and record.get("revision_id") != revision_id:
            raise HTTPException(status_code=422, detail="candidate revision identity mismatch")
        try:
            record["text"] = validate_manual_translation(matches[0].text, record["text"], target_language=original.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return record

    @app.get("/v1/translations/{workflow_id}/retranslations/{candidate_id}")
    async def retranslation_status(workflow_id: str, candidate_id: str) -> dict[str, Any]:
        ready = None
        try:
            ready = await selected_candidate(workflow_id,candidate_id)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
        if ready is not None:
            # Immutable saved candidates remain reviewable after Temporal retention expires.
            status = "COMPLETED"
        else:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(candidate_id)
            history = await handle.fetch_history()
            started = next((e for e in history.events if e.HasField("workflow_execution_started_event_attributes")), None)
            if started is None:
                raise HTTPException(status_code=404, detail="candidate request missing")
            decoded = await client.data_converter.decode(started.workflow_execution_started_event_attributes.input.payloads, [RetranslateSegmentInput])
            if len(decoded) != 1 or RetranslateSegmentInput.model_validate(decoded[0]).parent_workflow_id != workflow_id:
                raise HTTPException(status_code=404, detail="candidate does not belong to this translation")
            description = await handle.describe()
            status = getattr(getattr(description,"status",None),"name","")
        response = {"candidate_workflow_id":candidate_id,"workflow_id":workflow_id,"status":status}
        if status == "COMPLETED":
            response["candidate"] = ready or await selected_candidate(workflow_id, candidate_id)
            original = await original_translation_input(workflow_id)
            sources = await run_in_threadpool(translation_source_segments, app.state.artifact_store, original)
            response["source_text"] = next(x.text for x in sources if x.segment_id == response["candidate"]["segment_id"])
            units = await translation_units(workflow_id)
            response["current_text"] = next(x["translated_text"] for x in units["units"] if x["segment_id"] == response["candidate"]["segment_id"])
            def candidate_revisions() -> list[dict[str, Any]]:
                result = []
                store = app.state.artifact_store
                for page in store.s3.get_paginator("list_objects_v2").paginate(Bucket=settings.s3_bucket, Prefix=f"_system/translation-candidate-revisions/{candidate_id}/"):
                    for item in page.get("Contents", []):
                        revision = json.loads(store.s3.get_object(Bucket=settings.s3_bucket, Key=item["Key"])["Body"].read())
                        result.append(revision)
                return sorted(result,key=lambda x:(x["created_at"],x["revision_id"]))
            response["revisions"] = await run_in_threadpool(candidate_revisions)
        elif status in {"FAILED","TIMED_OUT","CANCELED","TERMINATED"}:
            response["detail"] = "Selected-unit retranslation failed; original saved translation remains available."
        return response

    @app.post("/v1/translations/{workflow_id}/retranslations/{candidate_id}/revisions", status_code=201)
    async def edit_retranslation_candidate(workflow_id: str, candidate_id: str, request: TranslationCorrectionRequest) -> dict[str, Any]:
        original = await terminal_translation(workflow_id)
        candidate = await selected_candidate(workflow_id,candidate_id)
        if request.segment_id != candidate["segment_id"]:
            raise HTTPException(status_code=422,detail="revision must edit the selected candidate segment")
        sources = await run_in_threadpool(translation_source_segments,app.state.artifact_store,original)
        source = next(x for x in sources if x.segment_id == request.segment_id)
        try:
            text = validate_manual_translation(source.text,request.translated_text, target_language=original.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        revision = {**candidate,"text":text,"revision_id":uuid.uuid4().hex,
            "created_at":datetime.now(UTC).isoformat(),"origin":"operator_candidate_edit","status":"awaiting_review"}
        await run_in_threadpool(app.state.artifact_store.s3.put_object,Bucket=settings.s3_bucket,
            Key=f"_system/translation-candidate-revisions/{candidate_id}/{revision['revision_id']}.json",
            Body=json.dumps(revision,ensure_ascii=False).encode(),ContentType="application/json",IfNoneMatch="*")
        return revision

    @app.post("/v1/translations/{workflow_id}/retranslations/{candidate_id}/accept", status_code=201)
    async def accept_retranslation(workflow_id: str, candidate_id: str, request: TranslationCandidateAcceptRequest) -> dict[str, Any]:
        original = await terminal_translation(workflow_id)
        record = await selected_candidate(workflow_id, candidate_id, request.revision_id)
        # Candidate-derived revision ID makes repeated acceptance idempotent.
        revision_id = hashlib.sha256((candidate_id + (request.revision_id or "")).encode()).hexdigest()
        override = {**record,"workflow_id":workflow_id,"revision_id":revision_id,
            "created_at":datetime.now(UTC).isoformat(),"origin":"operator_accepted_retranslation"}
        store = app.state.artifact_store
        try:
            await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
                Key=f"_system/translation-overrides/{workflow_id}/{revision_id}.json",
                Body=json.dumps(override,ensure_ascii=False).encode(),ContentType="application/json",IfNoneMatch="*")
        except Exception as exc:
            if getattr(exc,"response",{}).get("ResponseMetadata",{}).get("HTTPStatusCode") != 412:
                raise
        return {"workflow_id":workflow_id,"segment_id":record["segment_id"],"revision_id":revision_id,
            "status":"saved_for_resume","original_checkpoints_unchanged":True}

    @app.post("/v1/translations/{workflow_id}/revision-selection", status_code=201)
    async def select_translation_revision(workflow_id: str, request: TranslationRevisionSelectionRequest) -> dict[str, Any]:
        original = await terminal_translation(workflow_id)
        units = await translation_units(workflow_id)
        matches = [x for x in units["units"] if x["segment_id"] == request.segment_id]
        if len(matches) != 1:
            raise HTTPException(status_code=404,detail="source segment missing or ambiguous")
        unit = matches[0]
        if request.revision_id is None:
            text = unit.get("original_translated_text")
            if not text:
                raise HTTPException(status_code=409,detail="original machine translation is not checkpointed")
        else:
            versions = [x for x in unit["revisions"] if x["revision_id"] == request.revision_id]
            if len(versions) != 1:
                raise HTTPException(status_code=404,detail="selected saved revision not found")
            text = versions[0]["text"]
        try:
            text = validate_manual_translation(unit["source_text"],text, target_language=original.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        revision_id = uuid.uuid4().hex
        selection = {"workflow_id":workflow_id,"revision_id":revision_id,"segment_id":request.segment_id,
            "created_at":datetime.now(UTC).isoformat(),"text":text,"source_sha256":original.source_artifact_ref["sha256"],
            "source_artifact_ref":original.source_artifact_ref,"source_language":original.source_language,
            "target_language":original.target_language,"citations":unit["citations"],
            "origin":"operator_revision_selection","selected_revision_id":request.revision_id,
            "selected_original_machine_translation":request.revision_id is None}
        await run_in_threadpool(app.state.artifact_store.s3.put_object,Bucket=settings.s3_bucket,
            Key=f"_system/translation-overrides/{workflow_id}/{revision_id}.json",
            Body=json.dumps(selection,ensure_ascii=False).encode(),ContentType="application/json",IfNoneMatch="*")
        return {"workflow_id":workflow_id,"segment_id":request.segment_id,"revision_id":revision_id,
            "status":"saved_for_resume","original_checkpoints_unchanged":True}

    @app.post("/v1/translations/{workflow_id}/corrections", status_code=201)
    async def correct_translation_unit(workflow_id: str, request: TranslationCorrectionRequest) -> dict[str, Any]:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        client = await connected_temporal()
        _, description = await describe_or_404(client, workflow_id, "translation workflow not found")
        status = getattr(getattr(description, "status", None), "name", "")
        if status not in {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            raise HTTPException(status_code=409, detail="corrections require a terminal translation; active work cannot be edited")
        original = await original_translation_input(workflow_id)
        store = app.state.artifact_store
        sources = await run_in_threadpool(translation_source_segments, store, original)
        source = next((x for x in sources if x.segment_id == request.segment_id), None)
        if source is None:
            raise HTTPException(status_code=404, detail="segment not found in original source")
        try:
            text = validate_manual_translation(source.text, request.translated_text, target_language=original.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = {"workflow_id":workflow_id,"revision_id":uuid.uuid4().hex,"created_at":datetime.now(UTC).isoformat(),
            "segment_id":source.segment_id,"text":text,"source_sha256":original.source_artifact_ref["sha256"],
            "source_language":original.source_language,"target_language":original.target_language,
            "source_artifact_ref":original.source_artifact_ref,"citations":source.citations,"origin":"operator_manual_edit"}
        await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
            Key=f"_system/translation-overrides/{workflow_id}/{record['revision_id']}.json",
            Body=json.dumps(record, ensure_ascii=False).encode("utf-8"), ContentType="application/json", IfNoneMatch="*")
        return {"workflow_id":workflow_id,"revision_id":record["revision_id"],"segment_id":source.segment_id,
            "status":"saved_for_resume","original_checkpoints_unchanged":True}

    @app.post("/v1/courses/{course_id}/audio", status_code=202)
    async def generate_course_audio(course_id: str, request: MP3GenerationRequest) -> dict[str, Any]:
        source = request.source_artifact_ref
        if source.stage not in {"ingest-parse-pdf", "generate-course-qa", "translate-content"} or not source.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
            raise HTTPException(status_code=422, detail="source artifact is outside this course or not an audio-ready artifact")
        outline_ref = request.approved_outline_ref
        if request.source_kind == "course" and (outline_ref is None or outline_ref.stage != "approve-outline" or not outline_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
            raise HTTPException(status_code=422, detail="course audio requires the approved outline for this course")
        if request.source_kind == "course":
            if (request.source_book_ref is None or request.source_book_ref.stage != "ingest-parse-pdf" or
                    not request.source_book_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
                raise HTTPException(status_code=422, detail="course lesson audio requires the original parsed book")
            if request.source_book_translation_ref is not None and (
                    request.source_book_translation_ref.stage != "translate-content" or
                    not request.source_book_translation_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
                raise HTTPException(status_code=422, detail="lesson translation must belong to this course")
        if request.source_kind == "book" and request.book_audio_layout == "lessons":
            if (outline_ref is None or outline_ref.stage != "approve-outline" or
                    not outline_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
                raise HTTPException(status_code=422, detail="lesson-split book audio requires an approved outline")
            if (request.source_book_ref is None or request.source_book_ref.stage != "ingest-parse-pdf" or
                    not request.source_book_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
                raise HTTPException(status_code=422, detail="lesson-split book audio requires the original parsed book")
        if request.source_qa_artifact_ref is not None and (
                request.source_qa_artifact_ref.stage != "generate-course-qa" or
                not request.source_qa_artifact_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
            raise HTTPException(status_code=422, detail="source QA artifact must belong to this course")
        store = get_store()
        for label, supplied in (("source artifact", source), ("approved outline", outline_ref),
                                ("source book", request.source_book_ref),
                                ("source book translation", request.source_book_translation_ref),
                                ("source QA artifact", request.source_qa_artifact_ref)):
            if supplied is not None:
                await verified_ref(store, course_id, supplied, supplied.stage, label)
        try:
            client = await get_temporal_client()
            digest = hashlib.sha256((course_id + source.sha256 + request.source_kind + request.language +
                                     request.book_audio_layout + json.dumps([request.lesson_ids or [], request.allow_partial_qa, request.include_summaries, request.include_extra_scopes], separators=(",", ":")) +
                                     (outline_ref.sha256 if outline_ref else "") +
                                     (request.source_book_ref.sha256 if request.source_book_ref else "") +
                                     (request.source_book_translation_ref.sha256 if request.source_book_translation_ref else "")).encode()).hexdigest()
            workflow_id = "b2c-mp3-" + digest
            inp = GenerateMP3Input(course_id=course_id, source_artifact_ref=source.model_dump(mode="json"),
                source_kind=request.source_kind, language=request.language,
                approved_outline_ref=outline_ref.model_dump(mode="json") if outline_ref else None,
                source_qa_artifact_ref=request.source_qa_artifact_ref.model_dump(mode="json") if request.source_qa_artifact_ref else None,
                source_book_ref=request.source_book_ref.model_dump(mode="json") if request.source_book_ref else None,
                source_book_translation_ref=request.source_book_translation_ref.model_dump(mode="json") if request.source_book_translation_ref else None,
                book_audio_layout=request.book_audio_layout, lesson_ids=request.lesson_ids,
                allow_partial_qa=request.allow_partial_qa, include_summaries=request.include_summaries, include_extra_scopes=request.include_extra_scopes,
                activity_task_queue=os.getenv("B2C_AUDIO_TASK_QUEUE", AUDIO))
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            try:
                await run_in_threadpool(validate_mp3_input_lineage, store, inp)
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except Exception as exc:
                log.exception("could not verify audio source lineage")
                raise HTTPException(status_code=503, detail="audio source lineage could not be verified") from exc
            try: await client.start_workflow(GenerateMP3Workflow.run, inp, id=workflow_id, task_queue=inp.activity_task_queue)
            except WorkflowAlreadyStartedError: pass
        except HTTPException: raise
        except Exception as exc:
            log.exception("failed to queue MP3 generation")
            raise HTTPException(status_code=503, detail="MP3 generation could not be queued") from exc
        try:
            if outline_ref is not None:
                store = app.state.artifact_store or ArtifactStore.from_settings(settings)
                app.state.artifact_store = store
                manifest_bytes = await s3_bytes(store.s3, settings.s3_bucket,
                    outline_ref.uri.removeprefix(f"s3://{settings.s3_bucket}/") + "_manifest.json")
                if manifest_bytes is None:
                    raise ValueError("approved outline manifest is missing")
                outline_manifest = ArtifactRef.model_validate_json(manifest_bytes)
                approved_payload = json.loads(await run_in_threadpool(store.read, outline_manifest, "approved-outline.json"))
                book_id = approved_payload["outline"]["spine_book_id"]
                spine_ref = ArtifactRef.model_validate(approved_payload["spine_parsed_ref"])
                await remember_book_progress(course_id, book_id, "audio", workflow_id, {
                    "source_parsed_sha256": spine_ref.sha256,
                    "outline_sha256": outline_ref.sha256,
                    "source_artifact_sha256": source.sha256,
                    "language": request.language, "lesson_ids": request.lesson_ids,
                })
        except Exception:
            log.exception("could not associate audio workflow with its book")
        return {"course_id": course_id, "workflow_id": workflow_id, "status_url": f"/v1/audio/{workflow_id}", "file_url": f"/v1/audio/{workflow_id}/file", "manifest_url": f"/v1/audio/{workflow_id}/manifest"}

    async def partial_audio_manifest(workflow_id: str) -> dict[str, Any]:
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        try:
            value = await s3_json(store.s3, settings.s3_bucket, f"_system/audio-progress/{workflow_id}.json")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="audio checkpoint is unavailable") from exc
        if value is None:
            raise HTTPException(status_code=409, detail="no completed audio tracks are available yet")
        if value.get("workflow_id") != workflow_id or not value.get("course_id") or not isinstance(value.get("tracks"), list):
            raise HTTPException(status_code=503, detail="audio checkpoint identity is invalid")
        for track in value["tracks"]:
            try:
                ref = ArtifactRef.model_validate(track.get("artifact_ref"))
                if ref.stage != "generate-mp3-track" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{value['course_id']}/"):
                    raise ValueError("track is outside course")
            except Exception as exc:
                raise HTTPException(status_code=503, detail="audio track checkpoint failed scope validation") from exc
        return await add_audio_lesson_order(value, workflow_id)

    @app.get("/v1/audio/{workflow_id}")
    async def audio_status(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("mp3", workflow_id, "audio workflow not found")
        try:
            client = await get_temporal_client(); handle = client.get_workflow_handle(workflow_id); desc = await handle.describe()
        except Exception as exc:
            try:
                partial = await partial_audio_manifest(workflow_id)
            except HTTPException:
                raise HTTPException(status_code=503, detail="Temporal and saved audio status are unavailable") from exc
            return {"workflow_id": workflow_id, "status": "PARTIAL", "workflow_status": "UNKNOWN", "recovered_from_artifact": True, "manifest": partial, "progress": audio_progress(partial, "PARTIAL")}
        status = getattr(getattr(desc, "status", None), "name", "")
        out: dict[str, Any] = {"workflow_id": workflow_id, "status": status}
        if status == "RUNNING":
            try:
                partial = await partial_audio_manifest(workflow_id)
                out["manifest"] = partial
                out["progress"] = audio_progress(partial, status)
            except HTTPException as exc:
                if exc.status_code != 409: raise
                out["progress"] = {"completed_tracks": 0, "status": "RUNNING", "checkpointing": False}
        if status == "COMPLETED":
            result = await handle.result(); out["result"] = result; ref = ArtifactRef.model_validate(result["artifact"])
            if ref.stage != "generate-mp3" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{result.get('course_id')}/"): raise HTTPException(status_code=503, detail="MP3 artifact failed scope validation")
            out["manifest_url"] = f"/v1/audio/{workflow_id}/manifest"
            if result.get("kind") in {"course", "book_lessons"}:
                out["manifest"] = result.get("manifest")
                if out["manifest"]: out["progress"] = audio_progress(out["manifest"], status)
            else: out["file_url"] = f"/v1/audio/{workflow_id}/file"
        elif status in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            try:
                partial = await partial_audio_manifest(workflow_id)
                out["manifest"] = partial
                out["progress"] = audio_progress(partial, status)
            except HTTPException as exc:
                if exc.status_code != 409: raise
            try: await handle.result()
            except Exception as exc:
                cause = getattr(exc, "cause", None); out["failure"] = {"type": getattr(cause, "type", type(cause or exc).__name__)}
        return out

    @app.get("/v1/audio/{workflow_id}/manifest")
    async def audio_manifest(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("mp3", workflow_id, "audio workflow not found")
        try:
            client = await get_temporal_client(); handle = client.get_workflow_handle(workflow_id)
            status = getattr(getattr(await handle.describe(), "status", None), "name", "")
        except Exception:
            return await partial_audio_manifest(workflow_id)
        if status != "COMPLETED": return await partial_audio_manifest(workflow_id)
        result = await handle.result(); ref = ArtifactRef.model_validate(result["artifact"])
        if ref.stage != "generate-mp3" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{result.get('course_id')}/"): raise HTTPException(status_code=503, detail="audio manifest reference is invalid")
        store = app.state.artifact_store or ArtifactStore.from_settings(settings); app.state.artifact_store = store
        try:
            value = json.loads(await run_in_threadpool(store.read, ref, "audio-manifest.json"))
            validate_audio_manifest(value, settings.s3_bucket, result.get("course_id"))
            return await add_audio_lesson_order(value, workflow_id)
        except Exception as exc: raise HTTPException(status_code=503, detail="audio manifest is unavailable") from exc

    @app.get("/v1/audio/{workflow_id}/tracks/{track_id}")
    async def download_audio_track(workflow_id: str, track_id: str) -> StreamingResponse:
        manifest = await audio_manifest(workflow_id)
        entry = next((x for x in manifest.get("tracks", []) if x.get("track_id") == track_id), None)
        if entry is None: raise HTTPException(status_code=404, detail="audio track not found")
        ref = ArtifactRef.model_validate(entry.get("artifact_ref"))
        if ref.stage != "generate-mp3-track" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{manifest.get('course_id')}/"): raise HTTPException(status_code=503, detail="audio track reference is invalid")
        name = await audio_course_name(str(manifest["course_id"]))
        filename = next(t["filename"] for t in ordered_audio_tracks(manifest, name) if t["track_id"] == track_id)
        try:
            return await stream_artifact_file(get_store(), ref, "track.mp3", media_type="audio/mpeg",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        except Exception as exc:
            raise HTTPException(status_code=503, detail="audio track is unavailable") from exc

    async def add_audio_lesson_order(value: dict, workflow_id: str) -> dict:
        store = app.state.artifact_store
        try:
            display = await s3_json(store.s3, settings.s3_bucket, f"_system/audio-display/{workflow_id}.json")
            if display is None:
                return value
            if display.get("course_id") != value.get("course_id") or display.get("workflow_id") != workflow_id:
                raise HTTPException(503, "audio display identity is invalid")
            return {**value, "lesson_order": display.get("lesson_order", [])}
        except HTTPException: raise
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if isinstance(exc, KeyError) or code in {"NoSuchKey", "404", "NotFound"}:
                return value
            raise HTTPException(503, "audio lesson order is unavailable") from exc


    async def audio_course_name(course_id: str) -> str:
        require_course_id(course_id)
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        try:
            course_settings = await s3_json(store.s3, settings.s3_bucket, course_id + "/course-settings.json")
        except Exception as exc:
            raise HTTPException(503, "course name is unavailable") from exc
        return str((course_settings or {}).get("name") or course_id)


    async def list_course_audio_runs(course_id: str) -> dict[str, Any]:
        await audio_course_name(course_id)
        store = app.state.artifact_store
        try:
            records = await run_in_threadpool(course_audio_checkpoints, store, settings.s3_bucket, course_id)
        except Exception as exc:
            raise HTTPException(503, "course audio list is unavailable") from exc
        name = await audio_course_name(course_id)
        workflows = []
        for record in records:
            wid = record["workflow_id"]
            record = await add_audio_lesson_order(record, wid)
            # Durable tracks remain listable even after Temporal history expires.
            try:
                client = await get_temporal_client()
                desc = await client.get_workflow_handle(wid).describe()
                state = desc.status.name
            except Exception:
                state = "UNKNOWN"
            workflows.append({**record, "status": state,
                "progress": audio_progress(record, state),
                "tracks": [{**track, "download_url": f"/v1/audio/{wid}/tracks/{track['track_id']}"}
                           for track in ordered_audio_tracks(record, name)],
                "zip_url": f"/v1/courses/{course_id}/audio/zip?workflow_id={wid}"})
        return {"course_id": course_id, "course_name": name, "workflows": workflows,
                "total_saved_tracks": sum(len(record["tracks"]) for record in records)}


    @app.get("/v1/courses/{course_id}/audio/zip")
    async def download_course_audio_zip(course_id: str, workflow_id: str) -> StreamingResponse:
        manifest = await audio_manifest(workflow_id)
        try:
            validate_audio_manifest(manifest, settings.s3_bucket, course_id)
        except Exception as exc:
            raise HTTPException(404, "audio workflow is outside this course") from exc
        if not manifest["tracks"]:
            raise HTTPException(409, "no saved audio tracks are available")
        # A full-course archive must never silently contain only a partial lesson set.
        if len(manifest["tracks"]) < int(manifest.get("track_count", len(manifest["tracks"]))):
            raise HTTPException(409, "course audio is incomplete; download saved tracks individually")
        name = await audio_course_name(course_id)
        store = app.state.artifact_store
        try:
            archive = await run_in_threadpool(audio_zip, store, manifest, name)
        except Exception as exc:
            raise HTTPException(503, "course audio archive is unavailable") from exc
        def chunks():
            try:
                while data := archive.read(_CHUNK_BYTES):
                    yield data
            finally:
                archive.close()
        return StreamingResponse(chunks(), media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="course-audio.zip"'},
            background=BackgroundTask(archive.close))


    @app.get("/v1/audio/{workflow_id}/request")
    async def original_audio_request(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("mp3", workflow_id, "audio workflow not found")
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        try:
            saved_input = await s3_bytes(store.s3, settings.s3_bucket, f"_system/audio-inputs/{workflow_id}.json")
            inp = GenerateMP3Input.model_validate_json(saved_input) if saved_input is not None else None
        except Exception as exc:
            raise HTTPException(503, "audio input is unavailable") from exc
        if inp is None:
            try:
                client = await get_temporal_client()
                history = await client.get_workflow_handle(workflow_id).fetch_history()
                started = next(e for e in history.events if e.HasField("workflow_execution_started_event_attributes"))
                values = await client.data_converter.decode(started.workflow_execution_started_event_attributes.input.payloads, [GenerateMP3Input])
                inp = GenerateMP3Input.model_validate(values[0])
            except Exception as history_error:
                raise HTTPException(409, "original audio input is unavailable") from history_error
        try:
            await run_in_threadpool(validate_mp3_input_lineage, store, inp)
        except Exception as exc:
            raise HTTPException(409, "original audio input no longer passes source validation") from exc
        request = MP3GenerationRequest.model_validate(inp.model_dump(exclude={"course_id", "activity_task_queue"}))
        return {"workflow_id": workflow_id, "course_id": inp.course_id,
                "request": request.model_dump(mode="json"), "source_version": "original_validated"}


    @app.post("/v1/courses/{course_id}/audio/regenerate", status_code=202)
    async def regenerate_lesson_audio(course_id: str, request: MP3GenerationRequest) -> dict[str, Any]:
        if not request.lesson_ids or len(request.lesson_ids) != 1 or request.include_extra_scopes:
            raise HTTPException(422, "select exactly one lesson and set include_extra_scopes=false")
        # Updated accepted translation/source ArtifactRefs change the track cache identity.
        # Unchanged valid tracks retain their existing durable cache.
        return await generate_course_audio(course_id, request)


    @app.get("/v1/audio/{workflow_id}/file")
    async def download_audio(workflow_id: str) -> StreamingResponse:
        require_workflow_id("mp3", workflow_id, "audio workflow not found")
        try:
            client = await get_temporal_client(); handle = client.get_workflow_handle(workflow_id)
            if getattr(getattr(await handle.describe(), "status", None), "name", "") != "COMPLETED": raise HTTPException(status_code=409, detail="MP3 is not ready")
            result = await handle.result()
            if result.get("kind") != "book": raise HTTPException(status_code=409, detail="this audio is split into separate tracks; use the manifest")
            ref = ArtifactRef.model_validate(result["artifact"])
            if ref.stage != "generate-mp3" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{result.get('course_id')}/"): raise HTTPException(status_code=503, detail="MP3 artifact failed scope validation")
            return await stream_artifact_file(get_store(), ref, "course.mp3", media_type="audio/mpeg",
                headers={"Content-Disposition": 'attachment; filename="course.mp3"'})
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="MP3 is unavailable") from exc
    @app.post("/v1/courses/{course_id}/qa/generate", status_code=202)
    async def generate_course_questions(course_id: str, request: CourseQAGenerationRequest) -> dict[str, Any]:
        manager = app.state.model_lifecycle
        saved_retrieval_default = manager.defaults.get("rag")
        requested_retrieval_provider = request.retrieval_provider_id is not None or saved_retrieval_default is not None
        retrieval_provider_id = request.retrieval_provider_id or saved_retrieval_default
        if retrieval_provider_id is None:
            configured_mode = request.retrieval_mode or settings.retrieval_mode
            retrieval_provider_id = "bge-m3" if configured_mode == "hybrid" else "lexical"
        expected_modes = {
            "lexical": "lexical", "bge-m3": "hybrid", "embeddinggemma2-lite": "hybrid",
            "lightrag-157": "lightrag", "hipporag-2": "hipporag",
        }
        retrieval_mode = expected_modes[retrieval_provider_id]
        if retrieval_provider_id != "lexical":
            provider = manager.providers.get(retrieval_provider_id)
            if provider is None or provider.adapter == "unsupported":
                raise HTTPException(status_code=503, detail="the selected retrieval route is not configured on this server")
            if requested_retrieval_provider and (state := await manager.status(retrieval_provider_id))["state"] != "ready":
                raise HTTPException(status_code=503, detail="the selected retrieval route is not ready")
            if not requested_retrieval_provider and retrieval_mode == "hybrid" and not settings.embedding_base_url:
                raise HTTPException(status_code=503, detail="hybrid retrieval endpoint is not configured")
            retrieval_model_id = provider.model
        else:
            retrieval_model_id = "lexical-v1"
        retrieval_top_k = request.retrieval_top_k or settings.retrieval_top_k
        approved_ref = request.approved_outline_ref
        if approved_ref.stage != "approve-outline" or not approved_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
            raise HTTPException(status_code=422, detail="approved outline is outside the requested course")
        await verified_ref(get_store(), course_id, approved_ref, "approve-outline", "approved outline")
        try:
            client = await get_temporal_client()
            saved_index = await indexing_status(request.index_workflow_id, recovery_course_id=course_id)
            if saved_index["status"] != "COMPLETED":
                raise HTTPException(status_code=409, detail="course indexing must complete before question generation")
            index_result = saved_index["result"]
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to verify completed course indexing")
            raise HTTPException(status_code=503, detail="indexing status is unavailable") from exc
        if (not isinstance(index_result, dict) or index_result.get("course_id") != course_id
                or index_result.get("outline_sha256") != approved_ref.sha256):
            raise HTTPException(status_code=422, detail="index workflow result does not match the approved course outline")
        cache_bust = uuid.uuid4().hex if request.force_regenerate else ""
        if request.lesson_ids is not None and len(set(request.lesson_ids)) != len(request.lesson_ids):
            raise HTTPException(status_code=422, detail="lesson_ids must not contain duplicates")
        selected_model, selected_queue, selected_provider = generation_profile("questions")
        generation_config = (
            f"{request.lesson_question_count}:{request.group_question_count}:"
            f"{request.course_question_count}:{request.lesson_mcq_count}:{request.max_tokens}:"
            f"enrichment={int(request.include_lesson_enrichment)}:assessments={int(request.include_review_assessments)}:{request.assessment_question_count}:"
            f"{retrieval_provider_id}:{retrieval_model_id}:{retrieval_mode}:{retrieval_top_k}:"
            f"{','.join(request.lesson_ids or [])}"
        )
        reasoning_identity = (
            f"\0reasoning:{settings.llm_reasoning_effort}"
            if settings.llm_reasoning_effort else ""
        )
        workflow_id = "b2c-qa-" + hashlib.sha256(
            (course_id + "\0" + approved_ref.sha256 + "\0" + generation_config
             + "\0" + selected_model + "\0" + selected_provider + "\0" + selected_queue + reasoning_identity
             + "\0phase5-grounded-qa-v4\0" + cache_bust).encode("utf-8")
        ).hexdigest()
        inp = GenerateCourseQAInput(
            course_id=course_id, approved_outline_ref=approved_ref.model_dump(mode="json"),
            index_result=index_result, lesson_question_count=request.lesson_question_count,
            group_question_count=request.group_question_count, course_question_count=request.course_question_count,
            lesson_mcq_count=request.lesson_mcq_count,
            include_lesson_enrichment=request.include_lesson_enrichment,
            include_review_assessments=request.include_review_assessments,
            assessment_question_count=request.assessment_question_count,
            max_tokens=request.max_tokens, lesson_ids=request.lesson_ids,
            index_workflow_id=request.index_workflow_id, cache_bust=cache_bust or None,
            retrieval_mode=retrieval_mode,
            retrieval_provider_id=retrieval_provider_id,
            retrieval_model_id=retrieval_model_id,
            retrieval_top_k=retrieval_top_k,
            activity_task_queue=selected_queue,
            selected_model_id=selected_model, selected_provider_id=selected_provider,
        )
        try:
            try:
                await client.start_workflow(GenerateCourseQAWorkflow.run, inp, id=workflow_id, task_queue=inp.activity_task_queue)
            except WorkflowAlreadyStartedError:
                pass
        except Exception as exc:
            log.exception("failed to start course question generation")
            raise HTTPException(status_code=503, detail="question generation could not be queued") from exc
        try:
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            approved_payload = json.loads(await run_in_threadpool(store.read, approved_ref, "approved-outline.json"))
            book_id = approved_payload["outline"]["spine_book_id"]
            spine_ref = ArtifactRef.model_validate(approved_payload["spine_parsed_ref"])
            await remember_book_progress(course_id, book_id, "qa", workflow_id, {
                "source_parsed_sha256": spine_ref.sha256,
                "outline_sha256": approved_ref.sha256,
                "index_workflow_id": request.index_workflow_id,
            })
        except Exception:
            log.exception("could not associate QA workflow with its book")
        return {"course_id": course_id, "workflow_id": workflow_id, "status_url": f"/v1/course-qa/{workflow_id}", "force_regenerate": request.force_regenerate}

    @app.get("/v1/course-qa/{workflow_id}")
    async def course_qa_status(workflow_id: str, recovery_course_id: str | None = None, recovery_book_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("qa", workflow_id, "question generation workflow not found")
        if recovery_course_id is not None:
            require_course_id(recovery_course_id)
        if recovery_book_id is not None:
            require_book_id(recovery_book_id)
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
        except Exception as exc:
            if isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND and not (recovery_course_id and recovery_book_id):
                # Older APKs know only the workflow ID. Discover its exact saved
                # owner (within the given course when one is supplied), then use
                # the same source-lineage validation as new APKs.
                from b2c_api.qa_recovery import saved_qa_owner
                owner = await run_in_threadpool(saved_qa_owner, library_store(), workflow_id, recovery_course_id)
                if owner:
                    recovery_course_id, recovery_book_id = owner
            if isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND and recovery_course_id and recovery_book_id:
                registry = await book_progress(recovery_course_id, recovery_book_id)
                saved = registry.get("stages", {}).get("qa", {})
                if saved.get("workflow_id") == workflow_id and saved.get("artifact_ref"):
                    recovered_ref = ArtifactRef.model_validate(saved["artifact_ref"])
                    if recovered_ref.stage != "generate-course-qa" or not recovered_ref.uri.startswith(f"s3://{settings.s3_bucket}/{recovery_course_id}/"):
                        raise HTTPException(status_code=503, detail="question generation artifact failed scope validation")
                    recovered_store = app.state.artifact_store or ArtifactStore.from_settings(settings)
                    qa = json.loads(await run_in_threadpool(recovered_store.read, recovered_ref, "course-qa.json"))
                    scopes = qa.get("scopes", [])
                    recovered_status = "COMPLETED" if qa.get("status") == "final" else "PAUSED" if qa.get("status") == "paused" else "PARTIAL"
                    return {"workflow_id": workflow_id, "status": recovered_status, "recovered_from_artifact": True,
                        "result": {"course_id": recovery_course_id, "artifact": recovered_ref.model_dump(mode="json")},
                        "course_qa": qa, "progress": {"phase": "questions", "status": recovered_status,
                        "completed_scopes": sum(scope.get("status") == "final" for scope in scopes),
                        "failed_scopes": sum(scope.get("status") != "final" for scope in scopes),
                        "total_scopes": len(scopes),
                        "question_count": sum(len(scope.get("items", [])) for scope in scopes),
                        "covered_lessons": len(qa.get("covered_lesson_ids", [])),
                        "expected_lessons": len(qa.get("expected_lesson_ids", []))}}
            log.exception("failed to read question generation status")
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
        status_name = getattr(getattr(description, "status", None), "name", "")
        response: dict[str, Any] = {"workflow_id": workflow_id, "status": status_name}
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        if status_name == "RUNNING":
            progress_key = f"_system/qa-progress/{workflow_id}.json"
            progress_value = None
            if getattr(store, "s3", None) is not None:
                try:
                    progress_value = await s3_json(store.s3, settings.s3_bucket, progress_key)
                except Exception as exc:
                    log.exception("failed to read QA progress checkpoint")
                    raise HTTPException(status_code=503, detail="question generation progress is unavailable") from exc
            if progress_value is not None:
                response["progress"] = progress_value
            else:
                started = getattr(description, "start_time", None)
                elapsed = max(0, int((datetime.now(UTC) - started).total_seconds())) if started else None
                response["progress"] = {"phase": "questions", "status": "RUNNING",
                    "message": "الرحلة تعمل؛ لا تملك هذه الرحلة القديمة نقاط تقدم داخلية محفوظة.",
                    "elapsed_seconds": elapsed, "checkpointing": False}
        if status_name == "COMPLETED":
            try:
                result = await handle.result()
                response["result"] = result
                if isinstance(result, dict) and result.get("artifact"):
                    result_ref = ArtifactRef.model_validate(result["artifact"])
                    if (result_ref.stage != "generate-course-qa" or
                            not result_ref.uri.startswith(f"s3://{settings.s3_bucket}/{result.get('course_id')}/")):
                        raise HTTPException(status_code=503, detail="question generation artifact failed scope validation")
                    store = app.state.artifact_store
                    if store is None:
                        store = ArtifactStore.from_settings(settings)
                        app.state.artifact_store = store
                    response["course_qa"] = json.loads(await run_in_threadpool(
                        store.read, result_ref, "course-qa.json"))
                    qa_result = response["course_qa"]
                    scopes = qa_result.get("scopes", [])
                    content_ok = qa_result.get("status") == "final"
                    paused = qa_result.get("status") == "paused"
                    response["status"] = "COMPLETED" if content_ok else "PAUSED" if paused else "FAILED"
                    response["progress"] = {"phase": "complete" if content_ok else "questions",
                        "status": response["status"],
                        "completed_scopes": sum(scope.get("status") == "final" for scope in scopes),
                        "failed_scopes": sum(scope.get("status") != "final" for scope in scopes),
                        "total_scopes": len(scopes),
                        "question_count": sum(len(scope.get("items", [])) for scope in scopes),
                        "covered_lessons": len(qa_result.get("covered_lesson_ids", [])),
                        "expected_lessons": len(qa_result.get("expected_lesson_ids", []))}
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("failed to read completed question generation")
                raise HTTPException(status_code=503, detail="question generation result is unavailable") from exc
        elif status_name in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            try:
                progress_key = f"_system/qa-progress/{workflow_id}.json"
                saved_progress = await s3_json(store.s3, settings.s3_bucket, progress_key)
            except Exception as progress_error:
                log.exception("failed to read terminal question generation progress checkpoint")
                raise HTTPException(status_code=503, detail="question generation progress is unavailable") from progress_error
            if saved_progress is not None:
                response["progress"] = saved_progress
            else:
                response["progress"] = {"progress_state": "not_reported", "completed_scopes": 0,
                                         "total_scopes": 0, "message": "لا توجد نقاط تقدم محفوظة لهذا التشغيل."}
            try:
                await handle.result()
            except Exception as exc:
                cause = getattr(exc, "cause", None)
                response["failure"] = {"type": getattr(cause, "type", type(cause or exc).__name__)}
        return response

    def stage_control_kind(workflow_id: str) -> str:
        match = STAGE_CONTROL_ID.fullmatch(workflow_id)
        if match is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return match.group(1)

    async def read_stage_control(workflow_id: str) -> dict[str, Any]:
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        try:
            return await s3_json(store.s3, store.bucket, f"_system/workflow-controls/{workflow_id}.json") or {}
        except Exception as exc:
            raise HTTPException(status_code=503, detail="could not read pause control") from exc

    @app.get("/v1/workflows/{workflow_id}/control")
    async def stage_control_status(workflow_id: str) -> dict[str, Any]:
        kind = stage_control_kind(workflow_id)
        if kind == "qa":
            return {"workflow_id": workflow_id, "control_kind": "qa_checkpoint",
                    "pause_url": f"/v1/course-qa/{workflow_id}/pause",
                    "resume_url": f"/v1/course-qa/{workflow_id}/resume"}
        client = await connected_temporal()
        _, description = await describe_or_404(client, workflow_id)
        record = await read_stage_control(workflow_id)
        boundary = {"ingest": "after_complete_parsed_book", "index": "after_indexed_book",
                    "translate": "after_saved_translation_batch", "mp3": "after_saved_audio_track"}[kind]
        return {"workflow_id": workflow_id, "temporal_status": description.status.name,
                "status": record.get("state", "ACTIVE"), "checkpoint_boundary": boundary,
                "checkpoint": record.get("checkpoint"), "updated_at": record.get("acknowledged_at")}

    @app.post("/v1/workflows/{workflow_id}/pause")
    async def pause_stage_workflow(workflow_id: str) -> dict[str, Any]:
        kind = stage_control_kind(workflow_id)
        if kind == "qa":
            return await pause_course_qa(workflow_id)
        client = await connected_temporal()
        handle, description = await describe_or_404(client, workflow_id)
        if description.status.name != "RUNNING":
            raise HTTPException(status_code=409, detail="only a running workflow can be paused")
        if kind == "mp3":
            history = await handle.fetch_history()
            for event in history.events:
                if event.HasField("workflow_execution_started_event_attributes"):
                    values = await client.data_converter.decode(event.workflow_execution_started_event_attributes.input.payloads)
                    if values:
                        inp = values[0]
                        inp = inp.model_dump(mode="json") if hasattr(inp, "model_dump") else dict(inp)
                        if inp.get("source_kind") == "book" and inp.get("book_audio_layout", "full_book") != "lessons":
                            raise HTTPException(status_code=409, detail="whole-book audio has no per-track checkpoint; use lesson audio")
                    break
        current = await read_stage_control(workflow_id)
        if current.get("action") == "pause_after_checkpoint":
            return {"workflow_id": workflow_id, "status": current.get("state", "PAUSE_REQUESTED")}
        store = app.state.artifact_store
        record = {"workflow_id": workflow_id, "action": "pause_after_checkpoint",
                  "state": "PAUSE_REQUESTED", "requested_at": datetime.now(UTC).isoformat()}
        try:
            await run_in_threadpool(store.s3.put_object, Bucket=store.bucket,
                Key=f"_system/workflow-controls/{workflow_id}.json",
                Body=json.dumps(record).encode(), ContentType="application/json")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="pause request could not be saved") from exc
        return {"workflow_id": workflow_id, "status": "PAUSE_REQUESTED",
                "message": "Pauses after the current durable checkpoint; it does not interrupt unfinished output."}

    @app.post("/v1/workflows/{workflow_id}/resume")
    async def resume_stage_workflow(workflow_id: str, response: Response,
                                    request: ResumeStageRequest | None = None) -> dict[str, Any]:
        """One path, dispatched by status: QA checkpoint resume, failed-stage
        retry (FAILED/TIMED_OUT/CANCELED/TERMINATED), or live paused resume."""
        kind = stage_control_kind(workflow_id)
        if kind == "qa":
            return await resume_course_qa(workflow_id)
        client = await connected_temporal()
        _, description = await describe_or_404(client, workflow_id)
        status_name = description.status.name
        if status_name in RETRY_STATUSES and retryable_kind(workflow_id):
            if request is None:
                raise HTTPException(status_code=422, detail="book_id is required to retry a failed stage")
            try:
                result = await retry_failed_stage(client, workflow_id, request.book_id,
                                                  verify_resume_owner, remember_book_progress, status_name)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=503, detail="stage could not resume safely") from exc
            response.status_code = 202
            return result
        if status_name != "RUNNING":
            raise HTTPException(status_code=409, detail="this endpoint resumes a paused live workflow; use the stage retry endpoint for a finished workflow")
        record = await read_stage_control(workflow_id)
        if record.get("action") not in {"pause_after_checkpoint", "resume"}:
            raise HTTPException(status_code=409, detail="workflow has no pending pause")
        record.update({"action": "resume", "state": "ACTIVE", "resumed_at": datetime.now(UTC).isoformat()})
        store = app.state.artifact_store
        try:
            await run_in_threadpool(store.s3.put_object, Bucket=store.bucket,
                Key=f"_system/workflow-controls/{workflow_id}.json",
                Body=json.dumps(record).encode(), ContentType="application/json")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="resume request could not be saved") from exc
        try:
            await client.get_workflow_handle(workflow_id).signal("resume_stage")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="resume was saved but signaling failed; retry resume") from exc
        return {"workflow_id": workflow_id, "status": "RESUME_REQUESTED"}

    @app.post("/v1/course-qa/{workflow_id}/pause")
    async def pause_course_qa(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("qa", workflow_id, "question generation workflow not found")
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            status_name = getattr(getattr(description, "status", None), "name", "")
            if status_name != "RUNNING":
                raise HTTPException(status_code=409, detail="only a running question generation can be paused")
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
                Key=f"_system/qa-controls/{workflow_id}.json",
                Body=json.dumps({"action":"pause_after_checkpoint","requested_at":datetime.now(UTC).isoformat()}).encode(),
                ContentType="application/json")
            return {"workflow_id": workflow_id, "status": "PAUSE_REQUESTED",
                    "message": "The current lesson will finish its checkpoint before pausing."}
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to request a safe QA pause")
            raise HTTPException(status_code=503, detail="pause request could not be saved") from exc

    @app.post("/v1/course-qa/{workflow_id}/resume")
    async def resume_course_qa(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("qa", workflow_id, "question generation workflow not found")
        try:
            client = await get_temporal_client()
            old_handle = client.get_workflow_handle(workflow_id)
            description = await old_handle.describe()
            temporal_status = getattr(getattr(description, "status", None), "name", "")
            if temporal_status not in {"COMPLETED", "FAILED"}:
                raise HTTPException(status_code=409, detail="the workflow must stop before it can be resumed")
            prior = {}
            if temporal_status == "COMPLETED":
                prior = await old_handle.result()
            context = dict(prior.get("resume_context") or {})
            if not context:
                # Older failed runs did not return resume_context. Rebuild it from the
                # immutable Temporal start event, then read only compatible saved checkpoints.
                history = await old_handle.fetch_history()
                for event in history.events:
                    if event.HasField("workflow_execution_started_event_attributes"):
                        payloads = event.workflow_execution_started_event_attributes.input.payloads
                        values = await client.data_converter.decode(payloads)
                        if values:
                            raw = values[0]
                            context = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict(raw)
                            break
            if not context or (temporal_status == "COMPLETED" and
                    prior.get("status") not in {"paused", "failed"}):
                raise HTTPException(status_code=409, detail="this workflow has no safe checkpoint to resume")
            expected_model = context.get("selected_model_id") or settings.llm_model_id
            if prior.get("model_id") and prior.get("model_id") != expected_model:
                raise HTTPException(status_code=409,
                    detail="the saved workflow model differs from its checkpoints")
            context["checkpoint_source_workflow_ids"] = list(dict.fromkeys(
                [*context.get("checkpoint_source_workflow_ids", []), workflow_id]))
            inp = GenerateCourseQAInput.model_validate(context)
            resumed_id = "b2c-qa-" + hashlib.sha256((workflow_id + "\0resume").encode()).hexdigest()
            try:
                await client.start_workflow(GenerateCourseQAWorkflow.run, inp,
                    id=resumed_id, task_queue=inp.activity_task_queue)
            except WorkflowAlreadyStartedError:
                pass
            try:
                store = app.state.artifact_store or ArtifactStore.from_settings(settings)
                app.state.artifact_store = store
                approved_ref = ArtifactRef.model_validate(inp.approved_outline_ref)
                approved_payload = json.loads(await run_in_threadpool(store.read, approved_ref, "approved-outline.json"))
                book_id = approved_payload["outline"]["spine_book_id"]
                await remember_book_progress(inp.course_id, book_id, "qa", resumed_id)
            except Exception:
                log.exception("could not associate resumed QA workflow with its book")
            return {"course_id": inp.course_id, "workflow_id": resumed_id,
                    "status_url": f"/v1/course-qa/{resumed_id}", "resumed_from": workflow_id}
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to resume QA from saved checkpoints")
            raise HTTPException(status_code=503, detail="QA could not resume from its saved checkpoint") from exc

    @app.get("/v1/course-qa/{workflow_id}/checkpoints")
    async def course_qa_checkpoints(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("qa", workflow_id, "question generation workflow not found")
        store=app.state.artifact_store or ArtifactStore.from_settings(settings); app.state.artifact_store=store
        prefix=f"_system/qa-checkpoints/{workflow_id}/"
        def read_scopes() -> list[dict[str,Any]]:
            output=[]; token=None
            while True:
                args={"Bucket":settings.s3_bucket,"Prefix":prefix}
                if token: args["ContinuationToken"]=token
                page=store.s3.list_objects_v2(**args)
                for entry in page.get("Contents",[]):
                    key=entry["Key"]; relative=key[len(prefix):]; parts=relative.split("/")
                    if len(parts)!=2 or not parts[0] or not parts[1].endswith(".json"): continue
                    try:
                        payload=json.loads(store.s3.get_object(Bucket=settings.s3_bucket,Key=key)["Body"].read())
                        scope=ScopeResult.model_validate(payload.get("scope_result"))
                    except Exception: continue
                    if not scope.items: continue
                    output.append({"checkpoint_fingerprint":parts[0],"identity":payload.get("identity",{}),
                        "saved_at":payload.get("saved_at"),"scope_result":scope.model_dump(mode="json")})
                if not page.get("IsTruncated"): break
                token=page.get("NextContinuationToken")
                if not token: raise ValueError("truncated scope checkpoint listing has no continuation token")
            return output
        def read_lesson_assets() -> list[dict[str, Any]]:
            output: dict[str, tuple[str, dict[str, Any]]] = {}
            token = None
            while True:
                args = {"Bucket": settings.s3_bucket, "Prefix": prefix}
                if token:
                    args["ContinuationToken"] = token
                page = store.s3.list_objects_v2(**args)
                for entry in page.get("Contents", []):
                    key = entry["Key"]
                    relative = key[len(prefix):]
                    parts = relative.split("/")
                    if len(parts) != 3 or parts[1] != "enrichment" or not parts[2].endswith(".json"):
                        continue
                    try:
                        payload = json.loads(store.s3.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read())
                        enrichment = payload.get("enrichment")
                        lesson_id = str(enrichment.get("lesson_id", "")) if isinstance(enrichment, dict) else ""
                        saved_at = str(payload.get("saved_at", ""))
                        if lesson_id:
                            previous = output.get(lesson_id)
                            if previous is None or saved_at >= previous[0]:
                                output[lesson_id] = (saved_at, enrichment)
                    except Exception:
                        continue
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
                if not token:
                    raise ValueError("truncated lesson asset listing has no continuation token")
            return [item[1] for item in output.values()]

        try:
            scopes, lesson_assets = await run_in_threadpool(lambda: (read_scopes(), read_lesson_assets()))
            return {"workflow_id":workflow_id,"scope_count":len(scopes),"scopes":scopes,
                    "lesson_assets": lesson_assets}
        except Exception as exc:
            log.exception("failed to read QA scope checkpoints")
            raise HTTPException(status_code=503,detail="question checkpoints are unavailable") from exc

    def load_released_course(course_id: str):
        try:
            catalog = get_serving_catalog()
            return catalog.load(course_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="released course not found") from exc
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to load released course")
            raise HTTPException(status_code=503, detail="released course artifacts are unavailable") from exc

    def lesson_in_course(course: Any, lesson_id: str):
        for module in course.outline.modules:
            for lesson in module.lessons:
                if lesson.lesson_id == lesson_id:
                    return lesson
        raise HTTPException(status_code=404, detail="released lesson not found")

    def lesson_questions(course: Any) -> dict[str, Question]:
        questions: dict[str, Question] = {}
        for asset in course.qa.lesson_assets:
            for item in asset.get("questions", []):
                question = Question.model_validate(item)
                if question.qid in questions:
                    raise HTTPException(status_code=503, detail="released lesson has duplicate question IDs")
                questions[question.qid] = question
        return questions

    # GET /v1/courses belongs to the operator course library (Android client);
    # the learner release catalog has its own path so neither shadows the other.
    @app.get("/v1/released-courses")
    async def list_released_courses() -> dict[str, Any]:
        try:
            catalog = get_serving_catalog()
            course_ids = await run_in_threadpool(catalog.released_course_ids)
            courses = []
            for course_id in course_ids:
                course = await run_in_threadpool(catalog.load, course_id)
                courses.append({
                    "course_id": course_id,
                    "release_id": course.release.release_id,
                    "lesson_count": len(course.release.lesson_versions),
                    "created_at": course.release.created_at.isoformat(),
                })
            return {"courses": courses}
        except Exception as exc:
            log.exception("failed to list released courses")
            raise HTTPException(status_code=503, detail="released course catalog is unavailable") from exc

    @app.get("/v1/courses/{course_id}")
    async def read_released_course(course_id: str) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        return {
            "course_id": course.release.course_id,
            "release": course.release.model_dump(mode="json"),
            "outline": course.outline.model_dump(mode="json"),
            "course_qa": course.qa.model_dump(mode="json"),
            "audio_lesson_ids": sorted(course.audio_assets),
        }

    @app.get("/v1/lessons/{lesson_id}")
    async def read_released_lesson(lesson_id: str, course_id: str = Query(min_length=1, max_length=80)) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        lesson = lesson_in_course(course, lesson_id)
        asset = next((item for item in course.qa.lesson_assets if item.get("lesson_id") == lesson_id), None)
        scope = next((item for item in course.qa.scopes if item.scope_id == lesson_id), None)
        return {
            "course_id": course_id,
            "lesson": lesson.model_dump(mode="json"),
            "lesson_assets": asset,
            "grounded_questions": [item.model_dump(mode="json") for item in scope.items] if scope else [],
        }

    @app.get("/v1/lessons/{lesson_id}/review-quiz")
    async def read_review_quiz(
        lesson_id: str,
        course_id: str = Query(min_length=1, max_length=80),
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        lesson_in_course(course, lesson_id)
        bank = list(lesson_questions(course).values())
        try:
            questions = build_review_quiz(course.outline, lesson_id, bank, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="released lesson not found") from exc
        return {"course_id": course_id, "lesson_id": lesson_id,
                "questions": [learner_question(question) for question in questions]}

    @app.get("/v1/lessons/{lesson_id}/segments")
    async def read_lesson_segments(lesson_id: str, course_id: str = Query(min_length=1, max_length=80)) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        lesson_in_course(course, lesson_id)
        asset = course.audio_assets.get(lesson_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="released lesson audio is unavailable")
        return {"course_id": course_id, "lesson_id": lesson_id, "segments": asset.segments}

    @app.get("/v1/lessons/{lesson_id}/audio")
    async def stream_lesson_audio(
        lesson_id: str,
        request: Request,
        course_id: str = Query(min_length=1, max_length=80),
    ):
        course = await run_in_threadpool(load_released_course, course_id)
        lesson_in_course(course, lesson_id)
        try:
            bucket, key = get_serving_catalog().mp3_location(course, lesson_id)
            head = await run_in_threadpool(
                lambda: get_serving_catalog().s3.head_object(Bucket=bucket, Key=key)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="released lesson audio is unavailable") from exc
        except Exception as exc:
            log.exception("failed to inspect released audio")
            raise HTTPException(status_code=503, detail="released lesson audio is unavailable") from exc
        size = int(head["ContentLength"])
        start, end = 0, size - 1
        status_code = 200
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(size)}
        if head.get("ETag"):
            headers["ETag"] = head["ETag"]
        range_header = request.headers.get("range")
        request_args: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if range_header:
            try:
                start, end = parse_single_byte_range(range_header, size)
            except ValueError as exc:
                raise HTTPException(status_code=416, detail="requested byte range is not satisfiable",
                                    headers={"Content-Range": f"bytes */{size}"}) from exc
            request_args["Range"] = f"bytes={start}-{end}"
            status_code = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1)
        try:
            result = await run_in_threadpool(
                lambda: get_serving_catalog().s3.get_object(**request_args)
            )
        except Exception as exc:
            log.exception("failed to read released audio")
            raise HTTPException(status_code=503, detail="released lesson audio is unavailable") from exc
        body = result["Body"]
        def chunks():
            try:
                while data := body.read(64 * 1024):
                    yield data
            finally:
                body.close()
        return StreamingResponse(chunks(), status_code=status_code, media_type="audio/mpeg",
                                 headers=headers, background=BackgroundTask(body.close))

    @app.get("/v1/srs/due")
    async def due_srs_cards(
        course_id: str = Query(min_length=1, max_length=80),
        limit: int = Query(default=50, ge=1, le=200),
        user_id: str = Depends(require_learner_id),
    ) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        questions = lesson_questions(course)
        now = datetime.now(UTC)
        try:
            due = await run_in_threadpool(
                get_srs_store().due, user_id=user_id, course_id=course_id, now=now, limit=limit
            )
        except Exception as exc:
            log.exception("failed to read due SRS cards")
            raise HTTPException(status_code=503, detail="SRS storage is unavailable") from exc
        items = [{"question_id": row["question_id"], "due_at": row["due_at"].isoformat(),
                  "question": learner_question(questions[row["question_id"]])}
                 for row in due if row["question_id"] in questions]
        return {"course_id": course_id, "due_at": now.isoformat(), "items": items}

    @app.get("/v1/reviews")
    async def read_reviews(
        course_id: str = Query(min_length=1, max_length=80),
        limit: int = Query(default=100, ge=1, le=500),
        user_id: str = Depends(require_learner_id),
    ) -> dict[str, Any]:
        await run_in_threadpool(load_released_course, course_id)
        try:
            rows = await run_in_threadpool(
                get_srs_store().history, user_id=user_id, course_id=course_id, limit=limit
            )
        except Exception as exc:
            log.exception("failed to read learner review history")
            raise HTTPException(status_code=503, detail="review history is unavailable") from exc
        return {"course_id": course_id, "reviews": [row.model_dump(mode="json") for row in rows]}

    @app.post("/v1/reviews", status_code=201)
    async def submit_review(
        submission: ReviewSubmission,
        user_id: str = Depends(require_learner_id),
    ) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, submission.course_id)
        if submission.question_id not in lesson_questions(course):
            raise HTTPException(status_code=404, detail="released review question not found")
        try:
            entry, due_at = await run_in_threadpool(
                get_srs_store().review,
                user_id=user_id,
                course_id=submission.course_id,
                question_id=submission.question_id,
                rating=submission.rating,
                reviewed_at=submission.reviewed_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            log.exception("failed to persist learner review")
            raise HTTPException(status_code=503, detail="review could not be saved") from exc
        return {"review": entry.model_dump(mode="json"), "due_at": due_at.isoformat()}

    async def verify_resume_owner(course_id, book_id, stage, old_workflow):
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        key = f"{course_id}/book-progress/{hashlib.sha256(book_id.encode()).hexdigest()}.json"
        try:
            record = await s3_json(store.s3, settings.s3_bucket, key)
        except Exception as exc:
            raise HTTPException(503, detail="book recovery record is unavailable") from exc
        if record is None:
            raise HTTPException(409, detail="book recovery record is unavailable")
        stages = record.get("stages", {})
        recorded_ids = {stages.get(stage, {}).get("workflow_id"),
                        old_workflow.rsplit("-", 1)[0] + "-" + hashlib.sha256((old_workflow + "\0safe-resume-v1").encode()).hexdigest()}
        # Legacy outline records may contain the indexing workflow id; accept it
        # only when the approved outline and parsed-book hash bind it to this book.
        legacy_index_binding = (stage == "index"
            and stages.get("outline", {}).get("workflow_id") == old_workflow
            and bool(stages.get("outline", {}).get("approved_outline_ref"))
            and bool(stages.get("outline", {}).get("source_parsed_sha256")))
        if (record.get("course_id") != course_id or record.get("book_id") != book_id
                or (old_workflow not in recorded_ids and not legacy_index_binding)):
            raise HTTPException(409, detail="stage does not match the selected book")

    # The resume POST is served above (status dispatch); only the capability
    # probe comes from this router so the two can never shadow each other.
    app.include_router(workflow_resume_router(get_temporal_client, verify_resume_owner,
                                              remember_book_progress, include_resume=False))

    return app


app = create_app()


def main() -> None:
    uvicorn.run("b2c_api.app:app", host="0.0.0.0", port=8000)
