"""Phase 5 durable generation workflow for source-grounded lesson questions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import (
        ArtifactRef,
        CourseOutline,
        OutlineApprovalProvenance,
        ParsedBook,
        stage_key,
    )
    from b2c_core.source_identity import parsed_source_identity, source_identity_file
    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from b2c_llm_gateway import LLMRequestRejected, OpenRouterQuotaExceeded, FreeTokenQuotaExceeded, OpenRouterRateLimited
    from b2c_indexing import PostgresChunkStore, QueryRetrievalService
    from b2c_pedagogy.lesson_enrichment import (LESSON_ENRICHMENT_PROMPT_VERSION, LessonEnrichment,
                                                generate_lesson_enrichments)
    from b2c_pedagogy.qa_generation import CourseQAResult, ScopeCheckpointError, ScopeResult, _derive_scopes, generate_course_qa


QA_PROMPT_VERSION = "phase5-grounded-qa-v4"


def _compatible_checkpoint_identity(stored: dict, current: dict, *, allow_model_switch: bool) -> bool:
    """Only model execution settings may change during explicit recovery."""
    if not isinstance(stored, dict):
        return False
    old, new = dict(stored), dict(current)
    old_config, new_config = dict(old.pop("config", {})), dict(new.pop("config", {}))
    if allow_model_switch:
        old.pop("model_id", None)
        new.pop("model_id", None)
        for config in (old_config, new_config):
            config.pop("model_id", None)
            config.pop("max_tokens", None)
            config.pop("reasoning_effort", None)
    return old == new and old_config == new_config


def _load_qa_checkpoints(store: Any, workflow_id: str | Sequence[str], identity: dict,
                         *, allow_model_switch: bool = False) -> list[ScopeResult]:
    """Read all fingerprints and pages, excluding enrichment artifacts."""
    source_ids = [workflow_id] if isinstance(workflow_id, str) else list(workflow_id)
    restored: dict[str, ScopeResult] = {}
    for source_id in dict.fromkeys(source_ids):
        prefix = f"_system/qa-checkpoints/{source_id}/"
        token = None
        while True:
            kwargs = {"Bucket": store.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = store.s3.list_objects_v2(**kwargs)
            for entry in page.get("Contents", []):
                relative = entry["Key"][len(prefix):]
                if len(relative.split("/")) != 2 or not relative.endswith(".json"):
                    continue
                payload = json.loads(store.s3.get_object(Bucket=store.bucket, Key=entry["Key"])["Body"].read())
                if not _compatible_checkpoint_identity(payload.get("identity"), identity,
                                                         allow_model_switch=allow_model_switch):
                    continue
                if "scope_result" not in payload:
                    continue
                result = ScopeResult.model_validate(payload["scope_result"])
                previous = restored.get(result.scope_id)
                if previous is None or (len(result.items), result.status == "final") >= (len(previous.items), previous.status == "final"):
                    restored[result.scope_id] = result
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
            if not token:
                raise ScopeCheckpointError("truncated checkpoint listing has no continuation token")
    return list(restored.values())


class _PartialQAResult(Exception):
    def __init__(self, files: dict[str, bytes]):
        self.files = files


class _PauseRequested(Exception):
    """Pause only after the latest scope checkpoint has been persisted."""


class GenerateCourseQAInput(BaseModel):
    course_id: str = Field(min_length=1)
    approved_outline_ref: dict[str, Any]
    index_result: dict[str, Any]
    lesson_question_count: int = Field(default=5, ge=1, le=20)
    group_question_count: int = Field(default=5, ge=1, le=20)
    course_question_count: int = Field(default=10, ge=1, le=20)
    lesson_mcq_count: int = Field(default=5, ge=3, le=20)
    include_lesson_enrichment: bool = False
    include_review_assessments: bool = False
    assessment_question_count: int = Field(default=10, ge=5, le=20)
    max_tokens: int = Field(default=4096, ge=512, le=16384)
    lesson_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    cache_bust: str | None = None
    activity_task_queue: str = "heavy"
    selected_model_id: str | None = None
    selected_provider_id: str | None = None
    allow_checkpoint_model_switch: bool = False
    index_workflow_id: str | None = None
    checkpoint_source_workflow_ids: list[str] = Field(default_factory=list)
    retrieval_mode: Literal["lexical", "hybrid", "lightrag", "hipporag"] = "lexical"
    retrieval_provider_id: Literal["lexical", "bge-m3", "embeddinggemma2-lite", "lightrag-157", "hipporag-2"] = "lexical"
    retrieval_model_id: str = "lexical-v1"
    retrieval_top_k: int = Field(default=20, ge=1, le=100)


@dataclass
class CourseQAActivities:
    artifact_store: Any
    chunk_store: PostgresChunkStore
    gateway: Any
    retrieval_service: Any | None = None
    retrieval_services: dict[str, Any] = field(default_factory=dict)
    graph_retrieval_router: Any | None = None
    graph_provider_configs: dict[str, Any] = field(default_factory=dict)

    @activity.defn(name="generate_course_qa")
    def generate(self, inp: GenerateCourseQAInput) -> dict[str, Any]:
        # Progress heartbeats arrive only at scope boundaries; one scope can
        # block on a slow model call for longer than the heartbeat timeout.
        with activity_heartbeats("qa_liveness"):
            return self._generate(inp)

    def _generate(self, inp: GenerateCourseQAInput) -> dict[str, Any]:
        approved_ref = ArtifactRef.model_validate(inp.approved_outline_ref)
        if approved_ref.stage != "approve-outline":
            raise ApplicationError("question generation requires approved-outline artifact",
                                   type="InvalidQAArtifacts", non_retryable=True)
        bucket = approved_ref.uri.split("/")[2] if approved_ref.uri.startswith("s3://") else ""
        course_prefix = f"s3://{bucket}/{inp.course_id}/" if bucket else ""
        if not course_prefix or not approved_ref.uri.startswith(course_prefix):
            raise ApplicationError("approved outline is outside the requested course scope",
                                   type="QAScopeMismatch", non_retryable=True)
        try:
            envelope = json.loads(self.artifact_store.read(approved_ref, "approved-outline.json"))
            outline = CourseOutline.model_validate(envelope["outline"])
            parsed_ref = ArtifactRef.model_validate(envelope["spine_parsed_ref"])
            supplementary_refs = {
                book_id: ArtifactRef.model_validate(ref)
                for book_id, ref in envelope.get("supplementary_parsed_refs", {}).items()
            }
            if parsed_ref.stage != "ingest-parse-pdf" or not parsed_ref.uri.startswith(course_prefix):
                raise ValueError("spine parsed-book ref has the wrong stage or course")
            if set(supplementary_refs) != set(outline.supplementary_book_ids):
                raise ValueError("supplementary parsed-book refs do not match accepted outline")
            if any(ref.stage != "ingest-parse-pdf" or not ref.uri.startswith(course_prefix)
                   for ref in supplementary_refs.values()):
                raise ValueError("supplementary parsed-book ref has the wrong stage or course")
            spine = ParsedBook.model_validate_json(
                self.artifact_store.read(parsed_ref, "parsed-book.json"))
            approval = OutlineApprovalProvenance.model_validate(envelope.get("approval_provenance"))
            reviewer_id = envelope.get("reviewer_id")
            approved_by_policy = approval.review_type == "system" and bool(
                approval.system_id and approval.policy_version and approval.evidence_sha256
            )
            approved_by_human = approval.review_type == "human" and reviewer_id == approval.reviewer_id
            if outline.course_id != inp.course_id or not (approved_by_human or approved_by_policy) or not envelope.get("approved_at"):
                raise ValueError("outline is not explicitly approved for the requested course")
            if spine.book_id != outline.spine_book_id or spine.role != "spine":
                raise ValueError("parsed spine does not match approved outline")
            index_result = inp.index_result
            if (index_result.get("course_id") != inp.course_id or
                    index_result.get("outline_sha256") != approved_ref.sha256):
                raise ValueError("completed indexing result does not match the approved outline")
            expected_books = {outline.spine_book_id, *outline.supplementary_book_ids}
            indexed_books = index_result.get("books", {})
            if set(indexed_books) != expected_books:
                raise ValueError("completed indexing result does not cover approved books")
            chunks = self.chunk_store.list_chunks(
                inp.course_id,
                book_ids=expected_books,
            )
            chunk_books = {(chunk.book_id, chunk.role) for chunk in chunks}
            for book_id in expected_books:
                entry = indexed_books[book_id]
                actual_ids = sorted(chunk.chunk_id for chunk in chunks if chunk.book_id == book_id)
                if (entry.get("role") != ("spine" if book_id == outline.spine_book_id else "supplementary")
                        or sorted(entry.get("chunk_ids", [])) != actual_ids
                        or entry.get("chunk_count") != len(actual_ids)):
                    raise ValueError(f"indexed chunks do not match completed indexing result for {book_id}")
            if not chunks or (outline.spine_book_id, "spine") not in chunk_books:
                raise ValueError("approved spine has no indexed chunks")
            if any((book_id, "supplementary") not in chunk_books
                   for book_id in outline.supplementary_book_ids):
                raise ValueError("one or more approved supplementary books have no indexed chunks")
            if any(chunk.book_id == outline.spine_book_id and chunk.role != "spine" or
                   chunk.book_id in outline.supplementary_book_ids and chunk.role != "supplementary"
                   for chunk in chunks):
                raise ValueError("indexed chunk role conflicts with approved book role")
        except Exception as exc:
            raise ApplicationError("approved outline or indexed source artifacts are invalid or incomplete",
                                   type="InvalidQAArtifacts", non_retryable=True) from exc

        config = {
            "lesson_question_count": inp.lesson_question_count,
            "group_question_count": inp.group_question_count,
            "course_question_count": inp.course_question_count,
            "lesson_mcq_count": inp.lesson_mcq_count,
            "max_tokens": inp.max_tokens,
            "lesson_ids": inp.lesson_ids,
            "model_id": self.gateway.model_id,
            **({"reasoning_effort": reasoning_effort}
               if (reasoning_effort := getattr(self.gateway, "reasoning_effort", None)) is not None else {}),
            "cache_bust": inp.cache_bust,
            "retrieval_mode": inp.retrieval_mode,
            "retrieval_provider_id": inp.retrieval_provider_id,
            "retrieval_model_id": inp.retrieval_model_id,
            "retrieval_top_k": inp.retrieval_top_k,
        }
        cache_config = {**config,
            "include_lesson_enrichment": inp.include_lesson_enrichment,
            "include_review_assessments": inp.include_review_assessments,
            "assessment_question_count": inp.assessment_question_count,
        }
        source_index_identity = [
            {"chunk_id": chunk.chunk_id, "book_id": chunk.book_id, "role": chunk.role,
             "pages": chunk.pages, "text_sha256": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()}
            for chunk in chunks
        ]
        source_identity = parsed_source_identity(spine, parsed_ref)
        checkpoint_identity = {
            "approved_outline_sha256": approved_ref.sha256,
            "index_result_sha256": hashlib.sha256(
                json.dumps(index_result, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "indexed_sources_sha256": hashlib.sha256(
                json.dumps(source_index_identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "model_id": self.gateway.model_id,
            "config": config,
            "qa_prompt_version": QA_PROMPT_VERSION,
            # Frozen on purpose: changing it would orphan resumable scope checkpoints.
            # Resumed enrichments are re-validated, and the final key below tracks the live prompt.
            "enrichment_prompt_version": "phase5-lesson-enrichment-v2",
        }
        checkpoint_fingerprint = hashlib.sha256(
            json.dumps(checkpoint_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        # The final-result cache must change whenever the indexed evidence or
        # the QA prompt changes; same chunk ids with re-indexed text used to
        # reuse a stale artifact. Per-book cache_hit flags describe how the
        # index run executed, not what it produced, so they are excluded.
        stable_index_result = {**index_result, "books": {
            book_id: {name: value for name, value in entry.items() if name != "cache_hit"}
            for book_id, entry in index_result.get("books", {}).items()}}
        cache_config["index_result_sha256"] = hashlib.sha256(
            json.dumps(stable_index_result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cache_config["indexed_sources_sha256"] = checkpoint_identity["indexed_sources_sha256"]
        cache_config["qa_prompt_version"] = QA_PROMPT_VERSION
        key = stage_key("generate-course-qa", "3", [approved_ref.sha256], cache_config,
                        model_id=self.gateway.model_id, prompt_version=LESSON_ENRICHMENT_PROMPT_VERSION)
        try:
            workflow_id = activity.info().workflow_id
        except RuntimeError:
            workflow_id = ""

        last_processed_by_phase: dict[str, int] = {}
        def report_progress(details: dict[str, Any]) -> None:
            if not workflow_id:
                return
            phase = str(details.get("phase", ""))
            completed = int(details.get("completed_scopes", 0) or 0)
            processed = int(details.get("processed_scopes", completed) or 0)
            prior_processed = last_processed_by_phase.get(phase, 0)
            progress = {"workflow_id": workflow_id, **details,
                        "updated_at": datetime.now().astimezone().isoformat()}
            try:
                activity.heartbeat(progress)
            except RuntimeError:
                pass
            try:
                self.artifact_store.s3.put_object(
                    Bucket=self.artifact_store.bucket,
                    Key=f"_system/qa-progress/{workflow_id}.json",
                    Body=json.dumps(progress, ensure_ascii=False).encode("utf-8"),
                    ContentType="application/json",
                )
            except Exception:
                activity.logger.warning("could not persist QA progress checkpoint")
            last_processed_by_phase[phase] = max(prior_processed, processed)
            # Pause after every durable scope boundary, including a failed scope.
            # Checking only completed_scopes left long runs unable to honor pause
            # when consecutive scopes failed before the next successful one.
            if phase in {"questions", "lesson_assets"} and processed > prior_processed:
                try:
                    self.artifact_store.s3.get_object(
                        Bucket=self.artifact_store.bucket,
                        Key=f"_system/qa-controls/{workflow_id}.json",
                    )
                except Exception as exc:
                    code = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
                    if not isinstance(exc, KeyError) and code != 404:
                        raise ScopeCheckpointError("could not read QA pause control") from exc
                else:
                    raise _PauseRequested()

        def save_scope_checkpoint(scope_result: ScopeResult) -> None:
            if not workflow_id:
                return
            scope_key = hashlib.sha256(scope_result.scope_id.encode()).hexdigest()
            try:
                self.artifact_store.s3.put_object(
                    Bucket=self.artifact_store.bucket,
                    Key=f"_system/qa-checkpoints/{workflow_id}/{checkpoint_fingerprint}/{scope_key}.json",
                    Body=json.dumps({
                        "identity": checkpoint_identity, "source_identity": source_identity,
                        "scope_result": scope_result.model_dump(mode="json"),
                        "saved_at": datetime.now().astimezone().isoformat(),
                    }, ensure_ascii=False).encode("utf-8"),
                    ContentType="application/json",
                )
            except Exception as exc:
                raise ScopeCheckpointError("could not persist validated QA scope") from exc

        def save_enrichment_checkpoint(enrichment: LessonEnrichment) -> None:
            if not workflow_id:
                return
            lesson_key = hashlib.sha256(enrichment.lesson_id.encode()).hexdigest()
            self.artifact_store.s3.put_object(
                Bucket=self.artifact_store.bucket,
                Key=f"_system/qa-checkpoints/{workflow_id}/{checkpoint_fingerprint}/enrichment/{lesson_key}.json",
                Body=json.dumps({
                    "identity": checkpoint_identity, "source_identity": source_identity,
                    "enrichment": enrichment.model_dump(mode="json"),
                    "saved_at": datetime.now().astimezone().isoformat(),
                }, ensure_ascii=False).encode("utf-8"),
                ContentType="application/json",
            )

        def load_enrichment_checkpoints() -> list[LessonEnrichment]:
            if not workflow_id:
                return []
            restored: dict[str, LessonEnrichment] = {}
            sources = inp.checkpoint_source_workflow_ids or [workflow_id]
            for source_id in dict.fromkeys(sources):
                prefix = f"_system/qa-checkpoints/{source_id}/{checkpoint_fingerprint}/enrichment/"
                token = None
                while True:
                    kwargs = {"Bucket": self.artifact_store.bucket, "Prefix": prefix}
                    if token:
                        kwargs["ContinuationToken"] = token
                    response = self.artifact_store.s3.list_objects_v2(**kwargs)
                    for item in response.get("Contents", []):
                        obj = self.artifact_store.s3.get_object(Bucket=self.artifact_store.bucket, Key=item["Key"])
                        payload = json.loads(obj["Body"].read())
                        if payload.get("identity") != checkpoint_identity:
                            raise ValueError("lesson enrichment checkpoint identity does not match this run")
                        enrichment = LessonEnrichment.model_validate(payload["enrichment"])
                        restored[enrichment.lesson_id] = enrichment
                    if not response.get("IsTruncated"):
                        break
                    token = response.get("NextContinuationToken")
                    if not token:
                        raise ScopeCheckpointError("truncated enrichment listing has no continuation token")
            return list(restored.values())

        def load_scope_checkpoints() -> list[ScopeResult]:
            if not workflow_id:
                return []
            sources = inp.checkpoint_source_workflow_ids or [workflow_id]
            return _load_qa_checkpoints(self.artifact_store, sources, checkpoint_identity,
                allow_model_switch=inp.allow_checkpoint_model_switch)

        def compute() -> dict[str, bytes]:
            before_usage = dict(getattr(self.gateway, "usage_totals", {}))
            retrieval = self.retrieval_service or QueryRetrievalService(self.chunk_store)
            def retrieve_scope(query: str, candidates: Sequence[Any], top_k: int, *, allow_no_hits: bool = False):
                if inp.retrieval_mode in {"lightrag", "hipporag"}:
                    graph = getattr(self, "graph_retrieval_router", None)
                    if graph is None and self.graph_provider_configs:
                        from b2c_indexing.graph_router import GraphRetrievalRouter
                        graph_generation = hashlib.sha256((
                            (inp.index_workflow_id or inp.course_id) + "\0" + inp.retrieval_provider_id
                            + "\0" + inp.retrieval_model_id
                        ).encode("utf-8")).hexdigest()
                        graph = GraphRetrievalRouter(inp.course_id, graph_generation,
                                                     self.graph_provider_configs)
                    if graph is None:
                        raise ApplicationError("selected graph retrieval route is not configured",
                                               type="RetrievalRouteUnavailable", non_retryable=True)
                    hits = graph.search_candidates(query, candidates, top_k=top_k,
                        mode=inp.retrieval_mode, allowed_book_ids={chunk.book_id for chunk in candidates},
                        allowed_roles={chunk.role for chunk in candidates})
                else:
                    services = getattr(self, "retrieval_services", {})
                    active_retrieval = services.get(inp.retrieval_provider_id, retrieval)
                    if inp.retrieval_provider_id == "lexical" and inp.retrieval_mode == "hybrid":
                        active_retrieval = self.retrieval_service or active_retrieval
                    if inp.retrieval_mode == "hybrid" and getattr(active_retrieval, "_embed_query", None) is None:
                        raise ApplicationError("selected embedding route is not configured",
                                               type="RetrievalRouteUnavailable", non_retryable=True)
                    hits = active_retrieval.search_candidates(query, candidates, top_k=top_k,
                                                              mode=inp.retrieval_mode)
                # Exact lesson/group/course scope is already enforced by _derive_scopes.
                # Only the lexical default may fall back to its already-bounded scope;
                # selected dense/graph routes must never silently become lexical RAG.
                if hits:
                    return [hit.chunk for hit in hits]
                if allow_no_hits:
                    return []
                if inp.retrieval_provider_id == "lexical" and inp.retrieval_mode == "lexical":
                    return list(candidates)
                raise ApplicationError(
                    "selected retrieval route returned no evidence",
                    type="RetrievalNoEvidence",
                    non_retryable=True,
                )
            restored_scopes = load_scope_checkpoints()
            result = generate_course_qa(
                outline, spine, chunks, self.gateway,
                prompt_version=QA_PROMPT_VERSION,
                lesson_question_count=inp.lesson_question_count,
                group_question_count=inp.group_question_count,
                course_question_count=inp.course_question_count,
                max_tokens=inp.max_tokens,
                lesson_ids=inp.lesson_ids,
                include_review_assessments=inp.include_review_assessments,
                assessment_question_count=inp.assessment_question_count,
                outline_approved=True,
                precompleted_scopes=restored_scopes,
                accepted_precompleted_model_ids={item.model_id for scope in restored_scopes for item in scope.items},
                scope_retriever=retrieve_scope,
                retrieval_top_k=inp.retrieval_top_k,
                progress_callback=report_progress,
                scope_checkpoint_callback=save_scope_checkpoint,
            )
            if result.status != "final":
                result = result.model_copy(update={"retrieval_mode": inp.retrieval_mode,
                    "retrieval_top_k": inp.retrieval_top_k})
                raise _PartialQAResult({"course-qa.json": result.model_dump_json(indent=2).encode("utf-8")})
            enrichments = []
            if inp.include_lesson_enrichment:
                enrichment_scopes, _ = _derive_scopes(
                    outline, spine, chunks, lesson_question_count=inp.lesson_question_count,
                    group_question_count=inp.group_question_count,
                    course_question_count=inp.course_question_count, outline_approved=True,
                )
                supplementary = [chunk for chunk in chunks if chunk.role == "supplementary"]
                conflict_candidates = {}
                if supplementary:
                    for scope in enrichment_scopes:
                        if scope.kind != "lesson" or (
                            inp.lesson_ids is not None and scope.scope_id not in inp.lesson_ids
                        ):
                            continue
                        query = scope.scope_title + " " + " ".join(
                            chunk.text[:600] for chunk in scope.chunks[:4]
                        )
                        conflict_candidates[scope.scope_id] = retrieve_scope(
                            query, supplementary, top_k=5, allow_no_hits=True
                        )
                enrichments = generate_lesson_enrichments(
                    outline, spine, chunks, self.gateway,
                    question_count=inp.lesson_mcq_count,
                    max_tokens=inp.max_tokens,
                    lesson_ids=inp.lesson_ids,
                    supplementary_chunks_by_lesson=conflict_candidates,
                    progress_callback=report_progress,
                    precompleted=load_enrichment_checkpoints(),
                    checkpoint_callback=save_enrichment_checkpoint,
                )
            result = result.model_copy(update={
                "lesson_assets": [item.model_dump(mode="json") for item in enrichments],
            })
            after_usage = dict(getattr(self.gateway, "usage_totals", {}))
            usage_delta = {}
            for name, value in after_usage.items():
                before = before_usage.get(name)
                usage_delta[name] = (
                    None if value is None else
                    value - before if isinstance(value, (int, float)) and isinstance(before, (int, float)) else value
                )
            result = result.model_copy(update={"provider_usage": usage_delta,
                "approved_outline_sha256": approved_ref.sha256,
                "spine_parsed_sha256": parsed_ref.sha256,
                "spine_book_id": outline.spine_book_id,
                "retrieval_mode": inp.retrieval_mode, "retrieval_provider_id": inp.retrieval_provider_id,
                "retrieval_model_id": inp.retrieval_model_id,
                "retrieval_top_k": inp.retrieval_top_k})
            return {"course-qa.json": result.model_dump_json(indent=2).encode("utf-8"),
                    "source-identity.json": source_identity_file(source_identity)}

        try:
            try:
                result_ref, cache_hit = self.artifact_store.get_or_compute(
                    inp.course_id, "generate-course-qa", key, compute,
                )
            except _PartialQAResult as partial:
                # Keep diagnostics and accepted items without occupying the final cache key.
                partial_key = hashlib.sha256((key + ":partial:" + hashlib.sha256(
                    partial.files["course-qa.json"]).hexdigest()).encode()).hexdigest()
                result_ref = self.artifact_store.put(inp.course_id, "generate-course-qa", partial_key, {**partial.files,
                    "source-identity.json": source_identity_file(source_identity)})
                cache_hit = False
            except _PauseRequested:
                saved_scopes = load_scope_checkpoints()
                saved_assets = load_enrichment_checkpoints()
                expected_ids = [lesson.lesson_id for module in outline.modules for lesson in module.lessons]
                if inp.lesson_ids is not None:
                    selected = set(inp.lesson_ids)
                    expected_ids = [lesson_id for lesson_id in expected_ids if lesson_id in selected]
                partial = CourseQAResult(
                    status="paused", expected_lesson_ids=expected_ids,
                    covered_lesson_ids=sorted({lesson_id for scope in saved_scopes for lesson_id in scope.lesson_ids}),
                    scopes=saved_scopes, errors=[], model_id=self.gateway.model_id,
                    prompt_version=QA_PROMPT_VERSION,
                    approved_outline_sha256=approved_ref.sha256, spine_parsed_sha256=parsed_ref.sha256,
                    spine_book_id=outline.spine_book_id,
                    retrieval_mode=inp.retrieval_mode, retrieval_top_k=inp.retrieval_top_k,
                    lesson_assets=[item.model_dump(mode="json") for item in saved_assets],
                )
                partial_bytes = partial.model_dump_json(indent=2).encode("utf-8")
                partial_key = hashlib.sha256((key + ":paused:" + hashlib.sha256(partial_bytes).hexdigest()).encode()).hexdigest()
                result_ref = self.artifact_store.put(inp.course_id, "generate-course-qa", partial_key,
                    {"course-qa.json": partial_bytes,
                     "source-identity.json": source_identity_file(source_identity)})
                cache_hit = False
            result = json.loads(self.artifact_store.read(result_ref, "course-qa.json"))
            scopes = result.get("scopes", [])
            succeeded = sum(scope.get("status") == "final" for scope in scopes)
            failed = len(scopes) - succeeded
            report_progress({"phase": "complete", "status": "COMPLETED" if result.get("status") == "final" else "PAUSED" if result.get("status") == "paused" else "FAILED",
                             "completed_scopes": succeeded, "succeeded_scopes": succeeded,
                             "failed_scopes": failed, "processed_scopes": len(scopes),
                             "total_scopes": len(scopes),
                             "question_count": len({item["qa_id"] for scope in scopes for item in scope.get("items", [])}),
                             "accepted_question_count": sum(len(scope.get("items", [])) for scope in scopes),
                             "cache_hit": cache_hit})
        except ApplicationError:
            raise
        except Exception as exc:
            raise ApplicationError("course question generation failed",
                                   type="CourseQAGenerationFailed", non_retryable=isinstance(exc, (ValueError, LLMRequestRejected, OpenRouterQuotaExceeded, FreeTokenQuotaExceeded, OpenRouterRateLimited))) from exc
        return {"course_id": inp.course_id, "status": result.get("status", "failed"),
                "artifact": result_ref.model_dump(mode="json"), "cache_hit": cache_hit,
                "expected_lesson_count": len(result.get("expected_lesson_ids", [])),
                "covered_lesson_count": len(result.get("covered_lesson_ids", [])),
                "scope_count": len(result.get("scopes", [])), "model_id": result.get("model_id"),
                "errors": result.get("errors", []),
                "provider_usage": result.get("provider_usage", {}),
                "retrieval_mode": result.get("retrieval_mode", "lexical"),
                "retrieval_top_k": result.get("retrieval_top_k", inp.retrieval_top_k),
                "resume_context": {
                    "course_id": inp.course_id, "approved_outline_ref": inp.approved_outline_ref,
                    "index_result": inp.index_result, "index_workflow_id": inp.index_workflow_id,
                    "lesson_question_count": inp.lesson_question_count,
                    "group_question_count": inp.group_question_count,
                    "course_question_count": inp.course_question_count,
                    "lesson_mcq_count": inp.lesson_mcq_count, "max_tokens": inp.max_tokens,
                    "lesson_ids": inp.lesson_ids, "cache_bust": inp.cache_bust,
                    "activity_task_queue": inp.activity_task_queue,
                    "retrieval_mode": inp.retrieval_mode, "retrieval_provider_id": inp.retrieval_provider_id,
                    "retrieval_model_id": inp.retrieval_model_id,
                    "retrieval_top_k": inp.retrieval_top_k,
                    "checkpoint_source_workflow_ids": [workflow_id, *inp.checkpoint_source_workflow_ids],
                } if result.get("status") in {"paused", "failed"} else None}


@workflow.defn
class GenerateCourseQAWorkflow:
    @workflow.run
    async def run(self, inp: GenerateCourseQAInput) -> dict[str, Any]:
        options: dict[str, Any] = {}
        # Pre-existing histories scheduled QA without a heartbeat timeout.
        if workflow.patched("qa-heartbeat-timeout-v1"):
            options["heartbeat_timeout"] = LONG_ACTIVITY_HEARTBEAT_TIMEOUT
        return await workflow.execute_activity(
            "generate_course_qa", inp, task_queue=inp.activity_task_queue,
            start_to_close_timeout=timedelta(hours=24),
            schedule_to_close_timeout=timedelta(hours=48),
            retry_policy=RetryPolicy(maximum_attempts=3), **options,
        )
