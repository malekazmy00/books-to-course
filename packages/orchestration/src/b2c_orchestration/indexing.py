"""Indexing activity, gated on an approved course outline and on its own content gate (R5).

Every book (spine and supplementary) is built with ``b2c_indexing.index_book``: content gate,
span-based chunks, lesson parity. The snapshot (chunks.json, index-report.json,
quarantine.json) is registered as a promotion candidate with its gate result. Only when every
book's gate passed are the chunks written to the retrieval table; otherwise nothing is written
and the activity fails non-retryably with the failed checks (the report stays stored).
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any
from pydantic import BaseModel, Field
from temporalio import activity, workflow
from datetime import timedelta
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .controls import wait_for_activity_checkpoint, execute_checkpointed
    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .retry import DEFAULT_RETRY
    from b2c_contracts import (ArtifactRef, CourseOutline, IndexQualityReport, ParsedBook, ParsedBookV2,
                               stage_key)
    from b2c_contracts.models import StructuredChunk
    from b2c_indexing import CHUNKER_VERSION, PostgresChunkStore, index_book
    from b2c_core.source_identity import parsed_source_identity, source_identity_file

class IndexApprovedBookInput(BaseModel):
    course_id: str = Field(min_length=1)
    parsed_ref: dict[str, Any]
    approved_outline_ref: dict[str, Any]
    supplementary_parsed_refs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    target_tokens: int = Field(default=400, ge=64, le=800)
    max_tokens: int = Field(default=800, ge=64, le=2048)
    overlap_ratio: float = Field(default=0.12, ge=0, lt=0.5)  # ignored since chunker v2 (no overlap)
    embedding_model: str = "unassigned"
    activity_task_queue: str = "light"

PARSE_STAGES = {"ingest-parse-pdf": ("parsed-book.json", ParsedBook),
                "ingest-parse-v2": ("parsed-book-v2.json", ParsedBookV2)}


def _read_parsed(store: Any, ref: ArtifactRef):
    filename, model = PARSE_STAGES[ref.stage]
    return model.model_validate_json(store.read(ref, filename))


@dataclass
class IndexActivities:
    """Build gated chunks and persist them after outline approval."""
    artifact_store: Any
    chunk_store: PostgresChunkStore
    promotion: Any | None = None  # PromotionService; None = gate enforced, not registered

    @activity.defn(name="index_approved_book")
    def index_approved_book(self, inp: IndexApprovedBookInput) -> dict[str, Any]:
        with activity_heartbeats("index_liveness"):
            return self._index_approved_book(inp)

    def _index_approved_book(self, inp: IndexApprovedBookInput) -> dict[str, Any]:
        parsed_ref = ArtifactRef.model_validate(inp.parsed_ref)
        approved_ref = ArtifactRef.model_validate(inp.approved_outline_ref)
        if parsed_ref.stage not in PARSE_STAGES or approved_ref.stage != "approve-outline":
            raise ApplicationError("indexing requires parse and approved-outline artifacts",
                                   type="InvalidIndexArtifacts", non_retryable=True)
        bucket = approved_ref.uri.split("/")[2] if approved_ref.uri.startswith("s3://") else ""
        course_prefix = f"s3://{bucket}/{inp.course_id}/" if bucket else ""
        if not course_prefix or not parsed_ref.uri.startswith(course_prefix):
            raise ApplicationError("parsed artifact is outside the approved course scope",
                                   type="IndexScopeMismatch", non_retryable=True)
        try:
            parsed = _read_parsed(self.artifact_store, parsed_ref)
            envelope = json.loads(self.artifact_store.read(approved_ref, "approved-outline.json"))
            outline = CourseOutline.model_validate(envelope["outline"])
        except Exception as exc:
            raise ApplicationError("indexing artifacts are invalid or unavailable",
                                   type="InvalidIndexArtifacts", non_retryable=True) from exc
        if parsed.book_id != outline.spine_book_id or parsed.role != "spine":
            raise ApplicationError("primary parsed book does not match the approved spine",
                                   type="IndexScopeMismatch", non_retryable=True)
        if inp.course_id != outline.course_id:
            raise ApplicationError("course_id does not match the approved outline",
                                   type="IndexScopeMismatch", non_retryable=True)
        if set(inp.supplementary_parsed_refs) != set(outline.supplementary_book_ids):
            raise ApplicationError("supplementary ParsedBook refs must match every approved supplementary book",
                                   type="IndexScopeMismatch", non_retryable=True)

        books = [(parsed, "spine", outline)]
        for book_id in outline.supplementary_book_ids:
            try:
                ref = ArtifactRef.model_validate(inp.supplementary_parsed_refs[book_id])
                if ref.stage not in PARSE_STAGES or not ref.uri.startswith(course_prefix):
                    raise ValueError("not a parsed-book artifact in this course")
                supplement = _read_parsed(self.artifact_store, ref)
            except Exception as exc:
                raise ApplicationError(f"supplementary book artifact is invalid: {book_id}",
                                       type="InvalidIndexArtifacts", non_retryable=True) from exc
            if supplement.book_id != book_id:
                raise ApplicationError(f"supplementary book identity mismatch: {book_id}",
                                       type="IndexScopeMismatch", non_retryable=True)
            # Role belongs to the course composition, not immutable upload metadata.
            supplement = supplement.model_copy(update={"role": "supplementary"})
            books.append((supplement, "supplementary", None))

        per_book = {}
        built = []
        for source_book, role, accepted_outline in books:
            source_ref = parsed_ref if role == "spine" else ArtifactRef.model_validate(
                inp.supplementary_parsed_refs[source_book.book_id])
            snapshot_key = stage_key(
                "index-approved-book", "snapshot-3", [source_ref.sha256, approved_ref.sha256],
                {"book_id": source_book.book_id, "role": role, "chunker": CHUNKER_VERSION,
                 "target_tokens": inp.target_tokens, "max_tokens": inp.max_tokens,
                 "embedding_model": inp.embedding_model},
            )
            def compute_snapshot() -> dict[str, bytes]:
                build = index_book(
                    source_book, outline=accepted_outline, target_tokens=inp.target_tokens,
                    max_tokens=max(inp.max_tokens, inp.target_tokens), embedding_model=inp.embedding_model,
                    outline_sha256=approved_ref.sha256 if accepted_outline is not None else None,
                )
                return {"chunks.json": json.dumps([c.model_dump(mode="json") for c in build.chunks],
                                                    ensure_ascii=False).encode("utf-8"),
                        "index-report.json": build.report.model_dump_json(indent=2).encode("utf-8"),
                        "quarantine.json": json.dumps(build.quarantined, ensure_ascii=False).encode("utf-8"),
                        "source-identity.json": source_identity_file(parsed_source_identity(source_book, source_ref))}
            snapshot, cache_hit = self.artifact_store.get_or_compute(
                inp.course_id, "index-approved-book", snapshot_key, compute_snapshot)
            chunks = [StructuredChunk.model_validate(item) for item in json.loads(
                self.artifact_store.read(snapshot, "chunks.json"))]
            report = IndexQualityReport.model_validate_json(self.artifact_store.read(snapshot, "index-report.json"))
            if any(chunk.book_id != source_book.book_id or chunk.role != role for chunk in chunks):
                raise ApplicationError("chunk builder changed book identity or role",
                                       type="InvalidIndexChunks", non_retryable=True)
            status = None
            if self.promotion is not None:
                status = self.promotion.gate_index_build(
                    inp.course_id, snapshot, report=report, inputs=[source_ref, approved_ref],
                    config={"book_id": source_book.book_id, "role": role, "chunker": CHUNKER_VERSION,
                            "target_tokens": inp.target_tokens, "max_tokens": inp.max_tokens}).status
            per_book[source_book.book_id] = {**parsed_source_identity(source_book, source_ref), "role": role,
                                             "chunk_count": len(chunks),
                                             "chunk_ids": [chunk.chunk_id for chunk in chunks],
                                             "artifact_ref": snapshot.model_dump(mode="json"),
                                             "cache_hit": cache_hit,
                                             "gate": {"accepted": report.accepted, "failed": report.failed,
                                                      "status": status,
                                                      "quarantined_blocks": report.metrics.get("quarantined_blocks")}}
            built.append((source_book, chunks, report))
        failed = {book.book_id: report.failed for book, _, report in built if not report.accepted}
        if failed:
            raise ApplicationError(f"index content gate failed: {json.dumps(failed, sort_keys=True)}",
                                   {"books": per_book}, type="IndexGateFailed", non_retryable=True)
        self.chunk_store.ensure_schema()
        all_chunks = []
        for source_book, chunks, _ in built:
            count = self.chunk_store.replace_book(inp.course_id, source_book.book_id, chunks)
            per_book[source_book.book_id]["chunk_count"] = count
            all_chunks.extend(chunks)
            wait_for_activity_checkpoint(self.artifact_store, "indexed-book:" + source_book.book_id)
        return {
            "course_id": inp.course_id, "outline_sha256": approved_ref.sha256,
            "book_count": len(books), "chunk_count": len(all_chunks),
            "books": per_book,
            "pages_by_book": {book.book_id: sorted({page for c in per_book_chunks
                for page in c.pages}) for book, _, _ in books
                for per_book_chunks in [[c for c in all_chunks if c.book_id == book.book_id]]},
        }

@workflow.defn
class IndexApprovedBookWorkflow:
    """Persist one book's chunks after the course outline is approved."""

    @workflow.signal(name="resume_stage")
    def resume_stage(self) -> None:
        self._stage_resume_requested = True

    @workflow.run
    async def run(self, inp: IndexApprovedBookInput) -> dict[str, Any]:
        options: dict[str, Any] = {}
        # Pre-existing histories scheduled indexing without a heartbeat timeout.
        if workflow.patched("index-heartbeat-timeout-v1"):
            options["heartbeat_timeout"] = LONG_ACTIVITY_HEARTBEAT_TIMEOUT
        # Pre-existing histories used the server default (unlimited attempts).
        if workflow.patched("index-bounded-retry-v1"):
            options["retry_policy"] = DEFAULT_RETRY
        return await execute_checkpointed(
            self, "index_approved_book", inp, task_queue=inp.activity_task_queue,
            start_to_close_timeout=timedelta(minutes=30), **options,
        )
