"""Temporal PDF ingestion workflow: source artifact -> ParsedBook artifact."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .controls import wait_for_activity_checkpoint, execute_checkpointed
    from b2c_core.source_identity import parsed_source_identity, source_identity_file
    from b2c_contracts import ArtifactRef, BookUpload, CourseOutline, ParsedBook, stage_key
    from b2c_ingest import (
        OCRRequired, UnsupportedPDF, SuryaUnavailable, parse_pdf_book,
        parse_pdf_with_docling, parse_pdf_with_surya, parse_pdf_with_openrouter,
    )
    from b2c_outline import InferredSection, OutlineUnavailable, outline_from_book

    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .queues import HEAVY, LIGHT
    from .retry import DEFAULT_RETRY

STAGE = "ingest-parse-pdf"
STAGE_VERSION = "6"


@dataclass
class IngestPDFInput:
    course_id: str
    upload: dict[str, Any]
    source_ref: dict[str, Any]
    supplementary_book_ids: list[str]
    cache_bust: str | None = None
    ocr_engine: str | None = None


@dataclass
class IngestPDFActivities:
    store: Any
    prov: Any | None = None
    gemini_api_key_file: str | None = None
    gateway: Any | None = None

    def _record(self, ref: ArtifactRef, source_uri: str) -> None:
        if self.prov is None:
            return
        activity_record = self.prov.start_activity(
            stage=STAGE, stage_version=STAGE_VERSION,
            agent=(self.gateway.model_id if os.getenv("B2C_OCR_ENGINE", "rapidocr").strip().lower() == "openrouter" and self.gateway else
                   "surya-2-llama.cpp" if os.getenv("B2C_OCR_ENGINE", "rapidocr").strip().lower() == "surya" else
                   "docling-rapidocr"),
            prompt_version=None, activity_id=f"act:{STAGE}:{ref.key[:16]}",
        )
        self.prov.used(activity_record, source_uri)
        self.prov.derived(ref.uri, source_uri)
        self.prov.generated(ref.uri, activity_record)
        self.prov.end_activity(activity_record)

    @activity.defn(name="ingest_parse_pdf")
    def parse_pdf(self, inp: IngestPDFInput) -> dict[str, Any]:
        # OCR/Docling calls block for minutes; keep the attempt visibly alive.
        with activity_heartbeats("ingest_parse_liveness"):
            return self._parse_pdf(inp)

    def _parse_pdf(self, inp: IngestPDFInput) -> dict[str, Any]:
        upload = BookUpload.model_validate(inp.upload)
        source = ArtifactRef.model_validate(inp.source_ref)
        input_sha = upload.file_sha256
        ocr_engine = (inp.ocr_engine or os.getenv("B2C_OCR_ENGINE", "rapidocr")).strip().lower()
        if ocr_engine not in {"rapidocr", "surya", "openrouter"}:
            raise ApplicationError(
                "B2C_OCR_ENGINE must be rapidocr, surya, or openrouter",
                type="InvalidOCREngine", non_retryable=True,
            )
        surya_url = os.getenv("B2C_SURYA_INFERENCE_URL", "").strip()
        surya_model = os.getenv(
            "B2C_SURYA_MODEL",
            "/data/models/phase2-ocr-candidates/surya-ocr-2-gguf/surya-2.gguf",
        ).strip()
        try:
            surya_max_tokens = int(os.getenv("B2C_SURYA_MAX_TOKENS", "16384"))
            surya_timeout_seconds = int(os.getenv("B2C_SURYA_TIMEOUT_SECONDS", "3600"))
        except ValueError as exc:
            raise ApplicationError(
                "B2C_SURYA_MAX_TOKENS must be an integer",
                type="InvalidSuryaConfig", non_retryable=True,
            ) from exc
        if surya_max_tokens < 8192 or surya_timeout_seconds < 1800:
            raise ApplicationError(
                "Surya token ceiling must be at least 8192 and timeout at least 1800 seconds",
                type="InvalidSuryaConfig", non_retryable=True,
            )
        config = {
            "pipeline": f"pypdf-text-v1+{ocr_engine}-ocr-v4",
            "ocr_engine": ocr_engine,
            "surya_model": surya_model if ocr_engine == "surya" else None,
            "surya_max_tokens": surya_max_tokens if ocr_engine == "surya" else None,
            "surya_timeout_seconds": surya_timeout_seconds if ocr_engine == "surya" else None,
            "table_backend": (
                "surya-layout" if ocr_engine == "surya"
                else ("gemini-3.5-flash-lite+openrouter-vision" if self.gemini_api_key_file else "openrouter-markdown") if ocr_engine == "openrouter"
                else "gemini-3.5-flash-lite" if self.gemini_api_key_file
                else "docling-tableformer"
            ),
            "upload": upload.model_dump(mode="json"),
            "cache_bust": inp.cache_bust,
        }
        key = stage_key(STAGE, STAGE_VERSION, [input_sha], config)

        def compute() -> dict[str, bytes]:
            try:
                with tempfile.TemporaryDirectory(prefix="b2c-ingest-") as temp_dir:
                    path = Path(temp_dir) / "source.pdf"
                    self.store.download_to_path(source, "source.pdf", str(path))
                    digest = hashlib.sha256()
                    with path.open("rb") as source_file:
                        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != input_sha:
                        raise ApplicationError(
                            "source PDF digest does not match upload metadata",
                            type="SourceDigestMismatch", non_retryable=True,
                        )
                    if ocr_engine == "openrouter":
                        if self.gateway is None:
                            raise ApplicationError("OpenRouter OCR gateway is not configured",
                                type="OpenRouterOCRUnavailable", non_retryable=True)
                        gemini_key = None
                        if self.gemini_api_key_file:
                            try:
                                gemini_key = Path(self.gemini_api_key_file).read_text(encoding="utf-8").strip()
                            except OSError:
                                raise ApplicationError("configured Gemini table secret is unavailable",
                                    type="GeminiKeyUnavailable", non_retryable=True) from None
                            if not gemini_key:
                                raise ApplicationError("configured Gemini table secret is empty",
                                    type="GeminiKeyUnavailable", non_retryable=True)
                        parsed = parse_pdf_with_openrouter(upload, path, gateway=self.gateway,
                            gemini_api_key=gemini_key,
                            max_tokens=int(os.getenv("B2C_OPENROUTER_OCR_MAX_TOKENS", "12000")))
                    elif ocr_engine == "surya":
                        try:
                            parsed = parse_pdf_book(upload, path)
                        except OCRRequired:
                            if not surya_url:
                                raise ApplicationError(
                                    "Surya was selected but B2C_SURYA_INFERENCE_URL is not configured",
                                    type="SuryaEndpointUnavailable", non_retryable=True,
                                )
                            parsed = parse_pdf_with_surya(
                                upload, path, base_url=surya_url, model=surya_model,
                                timeout_seconds=surya_timeout_seconds, max_tokens=surya_max_tokens,
                            )
                    elif self.gemini_api_key_file:
                        try:
                            api_key = Path(self.gemini_api_key_file).read_text(encoding="utf-8").strip()
                        except OSError:
                            raise ApplicationError(
                                "configured Gemini key secret is unavailable",
                                type="GeminiKeyUnavailable", non_retryable=True,
                            ) from None
                        if not api_key:
                            raise ApplicationError(
                                "configured Gemini key secret is empty",
                                type="GeminiKeyUnavailable", non_retryable=True,
                            )
                        parsed = parse_pdf_with_docling(upload, path, gemini_api_key=api_key)
                    else:
                        try:
                            parsed = parse_pdf_book(upload, path)
                        except OCRRequired:
                            parsed = parse_pdf_with_docling(upload, path)
            except UnsupportedPDF as exc:
                raise ApplicationError(str(exc), type="UnsupportedFormat", non_retryable=True) from exc
            parsed = parsed.model_copy(update={"parser": {**parsed.parser,
                "source_file_sha256": input_sha}})
            return {"parsed-book.json": parsed.model_dump_json().encode("utf-8"),
                    "ingest-source.json": json.dumps({"source_file_sha256": input_sha,
                        "book_id": upload.book_id, "source_upload_ref": inp.source_ref},
                        sort_keys=True).encode("utf-8")}

        ref, cache_hit = self.store.get_or_compute(
            inp.course_id, STAGE, key, compute
        )
        parsed = ParsedBook.model_validate_json(self.store.read(ref, "parsed-book.json"))
        if not cache_hit:
            self._record(ref, source.uri)
        activity.logger.info("PDF parsed", extra={
            "stage": STAGE, "book_id": upload.book_id, "key": ref.key,
            "page_count": parsed.page_count, "blocks": len(parsed.blocks), "cache_hit": cache_hit,
            "ocr_engine": ocr_engine,
        })
        wait_for_activity_checkpoint(self.store, "parsed-book:" + upload.book_id)
        return {"ref": ref.model_dump(mode="json"), "cache_hit": cache_hit,
                 "page_count": parsed.page_count, "block_count": len(parsed.blocks)}


@dataclass
class BuildOutlineInput:
    course_id: str
    parsed_ref: dict[str, Any]
    supplementary_book_ids: list[str]
    cache_bust: str | None = None


class _SectionProposal(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    start_block: str = Field(min_length=1)
    end_block: str = Field(min_length=1)


class _SectionProposals(BaseModel):
    sections: list[_SectionProposal] = Field(min_length=1, max_length=32)


OUTLINE_PROMPT_VERSION = "outline-no-toc-v1"


@dataclass
class OutlineActivities:
    store: Any
    prov: Any | None = None
    gateway: Any | None = None

    def _inference_enabled(self) -> bool:
        if self.gateway is None:
            return False
        is_remote = getattr(self.gateway, "provider_name", "llama.cpp") in {"openrouter", "kiosapi", "codecraft"}
        return not is_remote or os.getenv("B2C_OUTLINE_REMOTE_EVAL_DATA", "0") == "1"

    def _infer_sections(self, parsed: ParsedBook) -> list[InferredSection]:
        if not self._inference_enabled():
            raise OutlineUnavailable(
                "no-TOC outline inference is unavailable; configure a local model or explicitly enable the remote evaluation-data route"
            )
        assert self.gateway is not None
        model = self.gateway.model_id
        max_chars = min(18000, int(getattr(self.gateway, "page_batch_max_chars", 24000)))
        max_pages = min(10, int(getattr(self.gateway, "page_batch_max_pages", 10)))
        groups: list[list[Any]] = []
        current: list[Any] = []
        current_chars = 0
        current_pages: set[int] = set()
        for block in parsed.blocks:
            serialized = json.dumps({"block_id": block.block_id, "type": block.type,
                                     "page": min(r.page_no for r in block.refs),
                                     "text": block.text}, ensure_ascii=False)
            block_pages = {r.page_no for r in block.refs}
            if len(serialized) > max_chars:
                raise OutlineUnavailable(f"block {block.block_id} exceeds the safe outline-inference input size")
            if current and (current_chars + len(serialized) > max_chars or
                            len(current_pages | block_pages) > max_pages):
                groups.append(current)
                current, current_chars, current_pages = [], 0, set()
            current.append(block)
            current_chars += len(serialized)
            current_pages.update(block_pages)
        if current:
            groups.append(current)

        all_sections: list[InferredSection] = []
        for group_no, blocks in enumerate(groups, start=1):
            payload = [{"block_id": b.block_id, "type": b.type,
                        "page": min(r.page_no for r in b.refs), "text": b.text} for b in blocks]
            response = self.gateway.structured(
                [{"role": "system", "content": (
                    "Organize the supplied ordered source blocks into concise course sections. "
                    "Return only JSON matching the schema. Use exact block_id values from the input. "
                    "Sections must partition the input in order, without gaps or overlap. "
                    "Do not add facts or rely on outside knowledge; titles may summarize only the included text."
                )}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                _SectionProposals, prompt_version=f"{OUTLINE_PROMPT_VERSION}:group-{group_no}",
                temperature=0.0, max_tokens=2048,
            )
            positions = {b.block_id: i for i, b in enumerate(blocks)}
            cursor = 0
            for section in response.sections:
                start = positions.get(section.start_block)
                end = positions.get(section.end_block)
                if start is None or end is None or start != cursor or end < start:
                    raise OutlineUnavailable("model sections did not partition their source blocks exactly")
                all_sections.append(InferredSection(
                    title=section.title, start_block=section.start_block, end_block=section.end_block,
                ))
                cursor = end + 1
            if cursor != len(blocks):
                raise OutlineUnavailable("model sections omitted one or more source blocks")
        return all_sections

    def _record(self, ref: ArtifactRef, source_uri: str, outline: CourseOutline) -> None:
        if self.prov is None:
            return
        inferred = any(lesson.provenance == "llm_inferred"
                       for module in outline.modules for lesson in module.lessons)
        activity_record = self.prov.start_activity(
            stage="build-outline", stage_version="3",
            agent=(getattr(self.gateway, "model_id", "outline-inference") if inferred else "deterministic-outline-v1"),
            prompt_version=OUTLINE_PROMPT_VERSION if inferred else None,
            activity_id=f"act:build-outline:{ref.key[:16]}",
        )
        self.prov.used(activity_record, source_uri)
        self.prov.derived(ref.uri, source_uri)
        self.prov.generated(ref.uri, activity_record)
        self.prov.end_activity(activity_record)

    @activity.defn(name="outline_from_parsed_book")
    def build(self, inp: BuildOutlineInput) -> dict[str, Any]:
        with activity_heartbeats("outline_liveness"):
            return self._build(inp)

    def _build(self, inp: BuildOutlineInput) -> dict[str, Any]:
        parsed_ref = ArtifactRef.model_validate(inp.parsed_ref)
        parsed = ParsedBook.model_validate_json(self.store.read(parsed_ref, "parsed-book.json"))
        has_headings = any(block.type == "heading" for block in parsed.blocks)
        inference_model = getattr(self.gateway, "model_id", None) if not has_headings and self._inference_enabled() else None
        config = {"course_id": inp.course_id,
                  "supplementary_book_ids": inp.supplementary_book_ids,
                  "cache_bust": inp.cache_bust,
                  "inference_model": inference_model,
                  "prompt_version": OUTLINE_PROMPT_VERSION if inference_model else None}
        key = stage_key("build-outline", "4", [parsed_ref.sha256], config)

        def compute() -> dict[str, bytes]:
            try:
                outline = outline_from_book(
                    parsed, course_id=inp.course_id,
                    supplementary_book_ids=inp.supplementary_book_ids,
                    infer_sections=self._infer_sections if not has_headings else None,
                )
            except OutlineUnavailable as exc:
                raise ApplicationError(str(exc), type="OutlineNeedsReview", non_retryable=True) from exc
            return {"course-outline.json": outline.model_dump_json().encode("utf-8"),
                    "source-identity.json": source_identity_file(parsed_source_identity(parsed, parsed_ref))}

        ref, cache_hit = self.store.get_or_compute(
            inp.course_id, "build-outline", key, compute
        )
        outline = CourseOutline.model_validate_json(self.store.read(ref, "course-outline.json"))
        if not cache_hit:
            self._record(ref, parsed_ref.uri, outline)
        return {
            "ref": ref.model_dump(mode="json"), "cache_hit": cache_hit,
            "module_count": len(outline.modules),
            "lesson_count": sum(len(module.lessons) for module in outline.modules),
            "requires_review": outline.requires_review,
        }


@workflow.defn
class IngestPDFWorkflow:
    @workflow.signal(name="resume_stage")
    def resume_stage(self) -> None:
        self._stage_resume_requested = True

    @workflow.run
    async def run(self, inp: IngestPDFInput) -> dict[str, Any]:
        activity_timeout = timedelta(hours=24)
        parse_options: dict[str, Any] = {}
        # Pre-existing histories scheduled the parse without a heartbeat timeout.
        if workflow.patched("ingest-parse-heartbeat-v1"):
            parse_options["heartbeat_timeout"] = LONG_ACTIVITY_HEARTBEAT_TIMEOUT
        parsed = await execute_checkpointed(
            self, "ingest_parse_pdf", inp, task_queue=HEAVY, retry_policy=DEFAULT_RETRY,
            start_to_close_timeout=activity_timeout, **parse_options,
        )
        if inp.upload.get("role") != "spine":
            return {"parsed_book": parsed, "outline": None}
        # No-TOC outlines call the LLM once per page group: that is heavy work
        # (docs/04 §2) and can exceed minutes. Old histories keep the LIGHT
        # queue and 5 minute timeout they were scheduled with.
        if workflow.patched("outline-heavy-queue-v1"):
            outline_options: dict[str, Any] = {
                "task_queue": HEAVY, "start_to_close_timeout": timedelta(hours=12),
                "heartbeat_timeout": LONG_ACTIVITY_HEARTBEAT_TIMEOUT,
            }
        else:
            outline_options = {"task_queue": LIGHT, "start_to_close_timeout": timedelta(minutes=5)}
        outline = await execute_checkpointed(
            self, "outline_from_parsed_book",
            BuildOutlineInput(inp.course_id, parsed["ref"], inp.supplementary_book_ids, inp.cache_bust),
            retry_policy=DEFAULT_RETRY, **outline_options,
        )
        return {"parsed_book": parsed, "outline": outline}
