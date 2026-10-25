"""Ingest v2 (R3): PDF -> S1 intermediates (text layer + Docling page batches) -> S2 ParsedBook v2
with a quality gate. New workflow and activity names; ``IngestPDFWorkflow`` and its command
sequence are untouched, so running histories replay unchanged.

Artifacts (all keyed on the file SHA, never on book_id/title, except the normalized book whose
content carries the book_id):

* ``ingest-text-layer``   text-layer.json            the PDF text layer, labels, bookmarks
* ``ingest-layout-batch`` layout/pages-AAAA-BBBB.json DoclingDocument export, all layers (one
                                                      activity and artifact per batch = checkpoint)
* ``ingest-parse-v2``     parsed-book-v2.json, parsed-book.json (v1 projection for the outline
                          and translation stages that have not migrated), ingest-quality.json,
                          ingest-source.json

Gate: when ``IngestQualityReport.accepted`` is False the workflow stops with
``status="needs_review"`` and does not build an outline; nothing is passed on silently.

Production switch (not enabled by this change): start ``IngestPDFV2Workflow`` with an
``IngestV2Input`` instead of ``IngestPDFWorkflow``; see docs/04-OPERATIONS.md §5.2.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import ArtifactRef, BookUpload, parsed_book_v1_from_v2, stage_key
    from b2c_ingest.layout import (
        DEFAULT_PAGE_BATCH, batch_name, batch_ranges, docling_layout_batch, docling_options,
        docling_versions, pdf_text_layer,
    )
    from b2c_ingest.docling_pdf import DoclingUnavailable
    from b2c_ingest.normalize import NORMALIZER_NAME, NORMALIZER_VERSION, BookMeta, normalize_docling
    from b2c_ingest.quality import REPORT_VERSION, quality_report

    from .controls import execute_checkpointed, wait_for_activity_checkpoint
    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .ingest import BuildOutlineInput
    from .queues import HEAVY
    from .retry import DEFAULT_RETRY

TEXT_STAGE, TEXT_STAGE_VERSION = "ingest-text-layer", "1"
LAYOUT_STAGE, LAYOUT_STAGE_VERSION = "ingest-layout-batch", "1"
PARSE_STAGE, PARSE_STAGE_VERSION = "ingest-parse-v2", "1"


@dataclass
class IngestV2Input:
    course_id: str
    upload: dict[str, Any]
    source_ref: dict[str, Any]
    supplementary_book_ids: list[str] = field(default_factory=list)
    cache_bust: str | None = None
    page_batch: int = DEFAULT_PAGE_BATCH


@dataclass
class LayoutBatchInput:
    course_id: str
    upload: dict[str, Any]
    source_ref: dict[str, Any]
    first_page: int
    last_page: int
    cache_bust: str | None = None


@dataclass
class NormalizeV2Input:
    course_id: str
    upload: dict[str, Any]
    text_layer_ref: dict[str, Any]
    batch_refs: list[dict[str, Any]]
    cache_bust: str | None = None


def _threads() -> int:
    try:
        return max(1, int(os.getenv("B2C_DOCLING_THREADS", "8")))
    except ValueError:
        return 8


@dataclass
class IngestV2Activities:
    store: Any
    prov: Any | None = None

    def _source(self, upload: BookUpload, source_ref: dict[str, Any], temp_dir: str) -> Path:
        path = Path(temp_dir) / "source.pdf"
        self.store.download_to_path(ArtifactRef.model_validate(source_ref), "source.pdf", str(path))
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != upload.file_sha256:
            raise ApplicationError("source PDF digest does not match upload metadata",
                                   type="SourceDigestMismatch", non_retryable=True)
        return path

    def _record(self, stage: str, version: str, agent: str, ref: ArtifactRef, used: list[str]) -> None:
        if self.prov is not None:
            self.prov.record_activity(stage=stage, stage_version=version, agent=agent, prompt_version=None,
                                      activity_id=f"act:{stage}:{ref.key[:16]}", used=used,
                                      derived=[(ref.uri, u) for u in used], generated=[ref.uri])

    @activity.defn(name="ingest_v2_text_layer")
    def text_layer(self, inp: IngestV2Input) -> dict[str, Any]:
        upload = BookUpload.model_validate(inp.upload)
        if upload.mime != "application/pdf":
            raise ApplicationError(f"ingest v2 reads PDFs only, got {upload.mime}", type="UnsupportedFormat",
                                   non_retryable=True)
        import pypdf

        key = stage_key(TEXT_STAGE, TEXT_STAGE_VERSION, [upload.file_sha256],
                        {"pypdf": pypdf.__version__, "cache_bust": inp.cache_bust})

        def compute() -> dict[str, bytes]:
            with tempfile.TemporaryDirectory(prefix="b2c-ingest-v2-") as temp_dir:
                try:
                    doc = pdf_text_layer(self._source(upload, inp.source_ref, temp_dir))
                except ValueError as exc:
                    raise ApplicationError(str(exc), type="UnsupportedFormat", non_retryable=True) from exc
            return {"text-layer.json": json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")}

        with activity_heartbeats("ingest_v2_text_layer"):
            ref, hit = self.store.get_or_compute(inp.course_id, TEXT_STAGE, key, compute)
        doc = json.loads(self.store.read(ref, "text-layer.json"))
        self._record(TEXT_STAGE, TEXT_STAGE_VERSION, f"pypdf-{pypdf.__version__}", ref, [inp.source_ref["uri"]])
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit, "page_count": int(doc["page_count"])}

    @activity.defn(name="ingest_v2_layout_batch")
    def layout_batch(self, inp: LayoutBatchInput) -> dict[str, Any]:
        upload = BookUpload.model_validate(inp.upload)
        if upload.ocr_language != "en":
            raise ApplicationError("ingest v2 layout supports English books only; Arabic stays on IngestPDFWorkflow",
                                   type="UnsupportedFormat", non_retryable=True)
        threads = _threads()
        options = docling_options(ocr_language=upload.ocr_language, num_threads=threads)
        options.pop("num_threads")  # does not change the output
        config = {"first_page": inp.first_page, "last_page": inp.last_page, "options": options,
                  "versions": docling_versions(), "cache_bust": inp.cache_bust}
        key = stage_key(LAYOUT_STAGE, LAYOUT_STAGE_VERSION, [upload.file_sha256], config)
        name = batch_name(inp.first_page, inp.last_page)

        def compute() -> dict[str, bytes]:
            with tempfile.TemporaryDirectory(prefix="b2c-ingest-v2-") as temp_dir:
                path = self._source(upload, inp.source_ref, temp_dir)
                try:
                    data = docling_layout_batch(path, inp.first_page, inp.last_page,
                                                ocr_language=upload.ocr_language, num_threads=threads)
                except DoclingUnavailable as exc:
                    raise ApplicationError(str(exc), type="DoclingUnavailable", non_retryable=True) from exc
            return {name: data}

        with activity_heartbeats("ingest_v2_layout_batch"):
            ref, hit = self.store.get_or_compute(inp.course_id, LAYOUT_STAGE, key, compute)
        self._record(LAYOUT_STAGE, LAYOUT_STAGE_VERSION, "docling-" + docling_versions()["docling"], ref,
                     [inp.source_ref["uri"]])
        wait_for_activity_checkpoint(self.store, f"layout:{upload.book_id}:{inp.first_page}")
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit, "name": name}

    @activity.defn(name="ingest_v2_normalize")
    def normalize(self, inp: NormalizeV2Input) -> dict[str, Any]:
        upload = BookUpload.model_validate(inp.upload)
        text_ref = ArtifactRef.model_validate(inp.text_layer_ref)
        batch_refs = [ArtifactRef.model_validate(r) for r in inp.batch_refs]
        config = {"normalizer": NORMALIZER_NAME, "normalizer_version": NORMALIZER_VERSION,
                  "report_version": REPORT_VERSION, "book_id": upload.book_id, "title": upload.title,
                  "role": upload.role, "cache_bust": inp.cache_bust}
        key = stage_key(PARSE_STAGE, PARSE_STAGE_VERSION, [text_ref.sha256, *[r.sha256 for r in batch_refs]], config)

        def compute() -> dict[str, bytes]:
            text_layer = json.loads(self.store.read(text_ref, "text-layer.json"))
            batches = []
            for ref in batch_refs:
                (name,) = [n for n in self.store.list_files(ref) if n.startswith("layout/")]
                batches.append(json.loads(self.store.read(ref, name)))
            versions = docling_versions()
            book = normalize_docling(BookMeta(upload.book_id, upload.title, upload.role), batches, text_layer,
                                     parser={"name": "docling-layout", "version": versions["docling"],
                                             "docling_core": versions["docling-core"],
                                             "source_file_sha256": upload.file_sha256})
            v2_bytes = book.model_dump_json().encode("utf-8")
            report = quality_report(book, text_layer, parsed_sha256=hashlib.sha256(v2_bytes).hexdigest())
            v1 = parsed_book_v1_from_v2(book)
            return {"parsed-book-v2.json": v2_bytes,
                    "parsed-book.json": v1.model_dump_json().encode("utf-8"),
                    "ingest-quality.json": report.model_dump_json(indent=1).encode("utf-8"),
                    "ingest-source.json": json.dumps({"source_file_sha256": upload.file_sha256,
                                                      "book_id": upload.book_id,
                                                      "text_layer_ref": inp.text_layer_ref,
                                                      "layout_batch_refs": inp.batch_refs},
                                                     sort_keys=True).encode("utf-8")}

        with activity_heartbeats("ingest_v2_normalize"):
            ref, hit = self.store.get_or_compute(inp.course_id, PARSE_STAGE, key, compute)
        report = json.loads(self.store.read(ref, "ingest-quality.json"))
        failed = [c["name"] for c in report["checks"] if c["passed"] is False]
        self._record(PARSE_STAGE, PARSE_STAGE_VERSION, f"{NORMALIZER_NAME}-{NORMALIZER_VERSION}", ref,
                     [text_ref.uri, *[r.uri for r in batch_refs]])
        log = activity.logger.info if report["accepted"] else activity.logger.warning
        log("ingest v2 quality gate", extra={"book_id": upload.book_id, "key": ref.key,
                                             "accepted": report["accepted"], "failed": failed})
        wait_for_activity_checkpoint(self.store, "parsed-book-v2:" + upload.book_id)
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit, "accepted": bool(report["accepted"]),
                "failed_checks": failed}


def ingest_v2_activities(acts: IngestV2Activities) -> list[Any]:
    """The activities IngestPDFV2Workflow schedules on HEAVY (outline_from_parsed_book is shared)."""
    return [acts.text_layer, acts.layout_batch, acts.normalize]


@workflow.defn(name="IngestPDFV2Workflow")
class IngestPDFV2Workflow:
    @workflow.signal(name="resume_stage")
    def resume_stage(self) -> None:
        self._stage_resume_requested = True

    @workflow.run
    async def run(self, inp: IngestV2Input) -> dict[str, Any]:
        heavy = {"task_queue": HEAVY, "retry_policy": DEFAULT_RETRY,
                 "heartbeat_timeout": LONG_ACTIVITY_HEARTBEAT_TIMEOUT}
        text = await execute_checkpointed(self, "ingest_v2_text_layer", inp,
                                          start_to_close_timeout=timedelta(hours=1), **heavy)
        batch_refs = []
        for first, last in batch_ranges(int(text["page_count"]), inp.page_batch):
            batch = await execute_checkpointed(
                self, "ingest_v2_layout_batch",
                LayoutBatchInput(inp.course_id, inp.upload, inp.source_ref, first, last, inp.cache_bust),
                start_to_close_timeout=timedelta(hours=4), **heavy)
            batch_refs.append(batch["ref"])
        parsed = await execute_checkpointed(
            self, "ingest_v2_normalize",
            NormalizeV2Input(inp.course_id, inp.upload, text["ref"], batch_refs, inp.cache_bust),
            start_to_close_timeout=timedelta(hours=2), **heavy)
        if not parsed["accepted"]:
            return {"parsed_book": parsed, "outline": None, "status": "needs_review"}
        if inp.upload.get("role") != "spine":
            return {"parsed_book": parsed, "outline": None, "status": "accepted"}
        outline = await execute_checkpointed(
            self, "outline_from_parsed_book",
            BuildOutlineInput(inp.course_id, parsed["ref"], inp.supplementary_book_ids, inp.cache_bust),
            start_to_close_timeout=timedelta(hours=12), **heavy)
        return {"parsed_book": parsed, "outline": outline, "status": "accepted"}
