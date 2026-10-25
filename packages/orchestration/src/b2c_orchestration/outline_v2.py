"""Outline v2 activity (R4): ParsedBook v2 -> CourseOutline v2 as a promotion candidate.

``outline_v2_from_parsed_book`` reads ``parsed-book-v2.json`` of an ``ingest-parse-v2`` artifact,
builds the outline deterministically (b2c_outline.v2), runs the machine gate and stores:

* ``course-outline-v2.json``  the CourseOutlineV2 draft (source of truth)
* ``course-outline.json``     its v1 projection (block ids of the stored parsed-book.json), so the
                              existing review/approve route and indexing keep working
* ``outline-quality.json``    OutlineQualityReport (the deterministic structure gate)
* ``outline-crosscheck.json`` the independent cross-check (printed TOC, layout headings vs the chosen
                              structure; flags + reason when the sources disagree)
* ``source-identity.json``    the immutable source chain, as for the v1 outline

and registers the artifact through the promotion service with two required system gates, the
structure gate and the cross-check, then settles it: accepted when both pass (and the build revision
is clean), rejected otherwise. No human acts on outlines (owner decision 2026-10-25).

``IngestPDFV2Workflow`` schedules it behind ``workflow.patched("outline-v2-activity-v1")``; the v1
``outline_from_parsed_book`` activity and ``IngestPDFWorkflow`` are unchanged.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import (
        ArtifactRef, CourseOutlineV2, OutlineQualityReport, ParsedBook, ParsedBookV2, course_outline_v1_from_v2,
        parsed_book_v1_from_v2, stage_key,
    )
    from b2c_core.source_identity import parsed_source_identity, source_identity_file
    from b2c_outline.v2 import BUILDER_NAME, BUILDER_VERSION, OutlineConfig, OutlineUnavailable, build_outline_v2
    from b2c_outline.v2.gate import REPORT_VERSION, CrossCheckResult, cross_check, outline_quality

    from .heartbeat import activity_heartbeats
    from .promotion import PromotionService

OUTLINE_V2_STAGE = "build-outline-v2"
OUTLINE_V2_STAGE_VERSION = "1"
OUTLINE_V2_ACTIVITY = "outline_v2_from_parsed_book"
OUTLINE_V2_PATCH = "outline-v2-activity-v1"


@dataclass
class BuildOutlineV2Input:
    course_id: str
    parsed_ref: dict[str, Any]  # an ingest-parse-v2 artifact (parsed-book-v2.json + parsed-book.json)
    supplementary_book_ids: list[str] = field(default_factory=list)
    cache_bust: str | None = None


@dataclass
class OutlineV2Activities:
    store: Any
    prov: Any | None = None
    promotion: PromotionService | None = None
    config: OutlineConfig = field(default_factory=OutlineConfig)

    @activity.defn(name=OUTLINE_V2_ACTIVITY)
    def build(self, inp: BuildOutlineV2Input) -> dict[str, Any]:
        with activity_heartbeats("outline_v2"):
            return self._build(inp)

    def _build(self, inp: BuildOutlineV2Input) -> dict[str, Any]:
        parsed_ref = ArtifactRef.model_validate(inp.parsed_ref)
        raw = self.store.read(parsed_ref, "parsed-book-v2.json")
        parsed_sha = hashlib.sha256(raw).hexdigest()
        config = {"course_id": inp.course_id, "supplementary_book_ids": list(inp.supplementary_book_ids),
                  "builder": BUILDER_NAME, "builder_version": BUILDER_VERSION, "gate_report_version": REPORT_VERSION,
                  "outline_config": self.config.as_dict(), "cache_bust": inp.cache_bust}
        key = stage_key(OUTLINE_V2_STAGE, OUTLINE_V2_STAGE_VERSION, [parsed_sha], config)

        def compute() -> dict[str, bytes]:
            book = ParsedBookV2.model_validate_json(raw)
            try:
                outline = build_outline_v2(book, course_id=inp.course_id,
                                           supplementary_book_ids=list(inp.supplementary_book_ids),
                                           config=self.config, parsed_sha256=parsed_sha)
            except OutlineUnavailable as exc:
                raise ApplicationError(str(exc), type="OutlineNeedsReview", non_retryable=True) from exc
            outline_bytes = outline.model_dump_json().encode("utf-8")
            report = outline_quality(book, outline, outline_sha256=hashlib.sha256(outline_bytes).hexdigest())
            v1 = course_outline_v1_from_v2(outline, book)
            parsed_v1 = ParsedBook.model_validate_json(self.store.read(parsed_ref, "parsed-book.json"))
            if [b.block_id for b in parsed_v1.blocks] != [b.block_id for b in parsed_book_v1_from_v2(book).blocks]:
                raise ApplicationError("stored parsed-book.json is not the v1 projection of parsed-book-v2.json",
                                       type="ValidationError", non_retryable=True)
            check = cross_check(book, outline, report)
            return {"course-outline-v2.json": outline_bytes,
                    "outline-crosscheck.json": json.dumps(
                        {"passed": check.passed, "flags": check.flags, "reason": check.reason,
                         "metrics": check.metrics}, indent=1, sort_keys=True).encode("utf-8"),
                    "course-outline.json": v1.model_dump_json().encode("utf-8"),
                    "outline-quality.json": report.model_dump_json(indent=1).encode("utf-8"),
                    "source-identity.json": source_identity_file(parsed_source_identity(parsed_v1, parsed_ref))}

        ref, hit = self.store.get_or_compute(inp.course_id, OUTLINE_V2_STAGE, key, compute)
        outline = CourseOutlineV2.model_validate_json(self.store.read(ref, "course-outline-v2.json"))
        report = OutlineQualityReport.model_validate_json(self.store.read(ref, "outline-quality.json"))
        stored = json.loads(self.store.read(ref, "outline-crosscheck.json"))
        check = CrossCheckResult(stored["passed"], stored["flags"], stored["reason"], stored["metrics"])
        status = None
        if self.promotion is not None:
            record = self.promotion.gate_outline(inp.course_id, ref, parsed_ref=parsed_ref, report=report,
                                                 cross_check=check,
                                                 config={k: v for k, v in config.items() if k != "course_id"})
            status = record.status
        if self.prov is not None:
            self.prov.record_activity(stage=OUTLINE_V2_STAGE, stage_version=OUTLINE_V2_STAGE_VERSION,
                                      agent=f"{BUILDER_NAME}-{BUILDER_VERSION}", prompt_version=None,
                                      activity_id=f"act:{OUTLINE_V2_STAGE}:{ref.key[:16]}", used=[parsed_ref.uri],
                                      derived=[(ref.uri, parsed_ref.uri)], generated=[ref.uri])
        log = activity.logger.info if report.accepted else activity.logger.warning
        log("outline v2 gate", extra={"course_id": inp.course_id, "key": ref.key, "accepted": report.accepted,
                                      "failed": report.failed, "stage_output_status": status})
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit, "format": outline.format,
                "module_count": len(outline.modules), "lesson_count": outline.lesson_count,
                "requires_review": status != "accepted",
                "gate": {"accepted": report.accepted, "failed": report.failed},
                "cross_check": {"passed": check.passed, "flags": check.flags, "reason": check.reason},
                "stage_output_status": status}


def outline_v2_summary(store: Any, ref: ArtifactRef) -> dict[str, Any]:
    """The v2 draft and its gate report, for review screens."""
    return {"outline_v2": json.loads(store.read(ref, "course-outline-v2.json")),
            "quality": json.loads(store.read(ref, "outline-quality.json"))}


__all__ = ["BuildOutlineV2Input", "OUTLINE_V2_ACTIVITY", "OUTLINE_V2_PATCH", "OUTLINE_V2_STAGE",
           "OUTLINE_V2_STAGE_VERSION", "OutlineV2Activities", "outline_v2_summary"]
