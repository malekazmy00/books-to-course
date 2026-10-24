"""Explicit Phase 3 indexing activity, gated on an approved course outline."""
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
    from b2c_contracts import ArtifactRef, CourseOutline, ParsedBook, stage_key
    from b2c_contracts.models import StructuredChunk
    from b2c_indexing import PostgresChunkStore, chunk_book
    from b2c_core.source_identity import parsed_source_identity, source_identity_file

class IndexApprovedBookInput(BaseModel):
    course_id: str = Field(min_length=1)
    parsed_ref: dict[str, Any]
    approved_outline_ref: dict[str, Any]
    supplementary_parsed_refs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    target_tokens: int = Field(default=500, ge=1, le=800)
    overlap_ratio: float = Field(default=0.12, ge=0, lt=0.5)
    embedding_model: str = "unassigned"
    activity_task_queue: str = "light"

@dataclass
class IndexActivities:
    """Build deterministic chunks and persist them after outline approval."""
    artifact_store: Any
    chunk_store: PostgresChunkStore

    @activity.defn(name="index_approved_book")
    def index_approved_book(self, inp: IndexApprovedBookInput) -> dict[str, Any]:
        parsed_ref = ArtifactRef.model_validate(inp.parsed_ref)
        approved_ref = ArtifactRef.model_validate(inp.approved_outline_ref)
        if parsed_ref.stage != "ingest-parse-pdf" or approved_ref.stage != "approve-outline":
            raise ApplicationError("indexing requires parse and approved-outline artifacts",
                                   type="InvalidIndexArtifacts", non_retryable=True)
        bucket = approved_ref.uri.split("/")[2] if approved_ref.uri.startswith("s3://") else ""
        course_prefix = f"s3://{bucket}/{inp.course_id}/" if bucket else ""
        if not course_prefix or not parsed_ref.uri.startswith(course_prefix):
            raise ApplicationError("parsed artifact is outside the approved course scope",
                                   type="IndexScopeMismatch", non_retryable=True)
        try:
            parsed = ParsedBook.model_validate_json(
                self.artifact_store.read(parsed_ref, "parsed-book.json"))
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
                if ref.stage != "ingest-parse-pdf" or not ref.uri.startswith(course_prefix):
                    raise ValueError("not a parsed-book artifact in this course")
                supplement = ParsedBook.model_validate_json(
                    self.artifact_store.read(ref, "parsed-book.json"))
            except Exception as exc:
                raise ApplicationError(f"supplementary book artifact is invalid: {book_id}",
                                       type="InvalidIndexArtifacts", non_retryable=True) from exc
            if supplement.book_id != book_id:
                raise ApplicationError(f"supplementary book identity mismatch: {book_id}",
                                       type="IndexScopeMismatch", non_retryable=True)
            # Role belongs to the course composition, not immutable upload metadata.
            supplement = supplement.model_copy(update={"role": "supplementary"})
            books.append((supplement, "supplementary", None))

        self.chunk_store.ensure_schema()
        per_book = {}
        all_chunks = []
        for source_book, role, accepted_outline in books:
            source_ref = parsed_ref if role == "spine" else ArtifactRef.model_validate(
                inp.supplementary_parsed_refs[source_book.book_id])
            snapshot_key = stage_key(
                "index-approved-book", "snapshot-2", [source_ref.sha256, approved_ref.sha256],
                {"book_id": source_book.book_id, "role": role,
                 "target_tokens": inp.target_tokens, "overlap_ratio": inp.overlap_ratio,
                 "embedding_model": inp.embedding_model},
            )
            def compute_snapshot() -> dict[str, bytes]:
                computed = chunk_book(
                    source_book, target_tokens=inp.target_tokens,
                    overlap_ratio=inp.overlap_ratio, embedding_model=inp.embedding_model,
                    outline=accepted_outline,
                )
                return {"chunks.json": json.dumps([c.model_dump(mode="json") for c in computed],
                                                    ensure_ascii=False).encode("utf-8"),
                        "source-identity.json": source_identity_file(parsed_source_identity(source_book, source_ref))}
            snapshot, cache_hit = self.artifact_store.get_or_compute(
                inp.course_id, "index-approved-book", snapshot_key, compute_snapshot)
            chunks = [StructuredChunk.model_validate(item) for item in json.loads(
                self.artifact_store.read(snapshot, "chunks.json"))]
            if any(chunk.book_id != source_book.book_id or chunk.role != role for chunk in chunks):
                raise ApplicationError("chunk builder changed book identity or role",
                                       type="InvalidIndexChunks", non_retryable=True)
            count = self.chunk_store.replace_book(inp.course_id, source_book.book_id, chunks)
            per_book[source_book.book_id] = {**parsed_source_identity(source_book, source_ref), "role": role, "chunk_count": count,
                                             "chunk_ids": [chunk.chunk_id for chunk in chunks],
                                             "artifact_ref": snapshot.model_dump(mode="json"),
                                             "cache_hit": cache_hit}
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
        return await execute_checkpointed(self, 
            "index_approved_book", inp, task_queue=inp.activity_task_queue,
            start_to_close_timeout=timedelta(minutes=30),
        )
