"""Temporal translation workflow over immutable B2C source artifacts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .controls import wait_for_activity_checkpoint, execute_checkpointed
    from .heartbeat import start_activity_heartbeat_pump
    from b2c_contracts import ArtifactRef, ParsedBook, stage_key
    from b2c_core.source_identity import artifact_source_identity
    from b2c_translation.generation import validate_manual_translation, translation_completeness_issue
    from b2c_translation import SourceSegment, TranslatedSegment, TranslationPack, plan_translation_batches, translate_segments
from b2c_translation.arabic_correction import correct_pack, correction_identity
from b2c_translation.glossary import ContextualGlossary, glossary_identity


class TranslateContentInput(BaseModel):
    course_id: str = Field(min_length=1, max_length=80)
    source_artifact_ref: dict[str, Any]
    source_kind: Literal["book", "course"]
    source_language: Literal["en", "ar"]
    target_language: Literal["en", "ar"]
    max_tokens: int = Field(default=8192, ge=512, le=16384)
    resume_from_workflow_id: str | None = Field(default=None, pattern=r"^b2c-translate-[0-9a-f]{64}$")
    activity_task_queue: str = "heavy"
    selected_model_id: str | None = None
    selected_provider_id: str | None = None
    selected_translation_engine: Literal["b2c", "booktrans-pro"] = "b2c"
    manual_override_revision_ids: list[str] | None = None
    contextual_glossary: ContextualGlossary | None = None


def _start_activity_heartbeat_pump(workflow_id: str, *, interval_seconds: float = 30.0) -> tuple[threading.Event, threading.Thread]:
    """Keep long synchronous provider calls alive in Temporal (see heartbeat.py)."""
    return start_activity_heartbeat_pump(workflow_id, interval_seconds=interval_seconds,
                                         kind="translation_liveness")


def translation_source_segments(artifact_store: Any, inp: TranslateContentInput) -> list[SourceSegment]:
    source_ref = ArtifactRef.model_validate(inp.source_artifact_ref)
    if inp.source_kind == "book":
        parsed = ParsedBook.model_validate_json(artifact_store.read(source_ref, "parsed-book.json"))
        source_segments = [SourceSegment(segment_id=block.block_id, text=block.text,
            citations=[f"{ref.book_id}:p{ref.page_no}" for ref in block.refs])
            for block in parsed.blocks if block.text.strip()]
    else:
        source = json.loads(artifact_store.read(source_ref, "course-qa.json"))
        source_segments = []
        for lesson in source.get("lesson_assets", []):
            lesson_id = str(lesson.get("lesson_id", "lesson"))
            for sentence in lesson.get("draft", {}).get("sentences", []):
                source_segments.append(SourceSegment(segment_id=str(sentence["sid"]),
                    text=str(sentence["text_en"]), citations=list(sentence.get("citations", []))))
            for i, point in enumerate(lesson.get("summary", {}).get("points", []), 1):
                source_segments.append(SourceSegment(segment_id=f"{lesson_id}:summary:{i}", text=str(point)))
            for i, term in enumerate(lesson.get("glossary", []), 1):
                source_segments.append(SourceSegment(segment_id=f"{lesson_id}:glossary:{i}",
                    text=f"{term.get('term_en', '')}: {term.get('definition_en', '')}"))
            for i, question in enumerate(lesson.get("questions", []), 1):
                citations = list(question.get("evidence_sids", []))
                source_segments.append(SourceSegment(segment_id=f"{lesson_id}:question:{i}:stem", text=str(question.get("stem", "")), citations=citations))
                for n, option in enumerate(question.get("options", []), 1):
                    source_segments.append(SourceSegment(segment_id=f"{lesson_id}:question:{i}:option:{n}", text=str(option), citations=citations))
                options = question.get("options", [])
                answer_index = int(question.get("answer_index", 0))
                if options and 0 <= answer_index < len(options):
                    source_segments.append(SourceSegment(segment_id=f"{lesson_id}:question:{i}:answer", text=str(options[answer_index]), citations=citations))
                if question.get("rationale"):
                    source_segments.append(SourceSegment(segment_id=f"{lesson_id}:question:{i}:rationale", text=str(question["rationale"]), citations=citations))
        for scope in source.get("scopes", []):
            scope_id = str(scope.get("scope_id", ""))
            kind = str(scope.get("kind", ""))
            for i, question in enumerate(scope.get("items", []), 1):
                prefix = (f"{scope_id}:lesson-qa" if kind == "lesson" else f"scope:{scope_id}:qa") + f":{i}"
                citations = list(question.get("evidence_chunk_ids", []))
                source_segments.append(SourceSegment(segment_id=prefix + ":question", text=str(question.get("question", "")), citations=citations))
                source_segments.append(SourceSegment(segment_id=prefix + ":answer", text=str(question.get("answer", "")), citations=citations))
    return source_segments



def merge_translation_checkpoint(
    saved: dict[str, Any], compatible: dict[str, Any],
    sources: dict[str, SourceSegment],
    selected: dict[str, tuple[datetime, TranslatedSegment]],
) -> None:
    """Retain the newest complete, compatible result for each source unit.

    A newer truncated result must never displace a previously verified unit.
    Malformed records cannot affect another unit's valid saved result.
    """
    if any(saved.get(name) != value for name, value in compatible.items()):
        return
    if saved.get("glossary_sha256") != compatible.get("glossary_sha256"):
        return
    try:
        if int(saved.get("batch_index", 0)) < 1:
            return
        completed_at = datetime.fromisoformat(str(saved["completed_at"]).replace("Z", "+00:00"))
        if completed_at.tzinfo is None:
            return
        completed_at = completed_at.astimezone(UTC)
    except (KeyError, TypeError, ValueError):
        return
    for item in saved.get("segments", []):
        try:
            translated = TranslatedSegment.model_validate(item)
            source = sources.get(translated.segment_id)
            if source is None or translation_completeness_issue(source.text, translated.text, target_language=compatible.get("target_language")):
                continue
            translated = translated.model_copy(update={"text": validate_manual_translation(source.text, translated.text, target_language=compatible.get("target_language"))})
        except (TypeError, ValueError):
            continue
        previous = selected.get(translated.segment_id)
        if previous is None or completed_at > previous[0]:
            selected[translated.segment_id] = (completed_at, translated)



def translation_checkpoint_records(artifact_store: Any, prefix: str):
    """Read every committed checkpoint page without predicting batch count."""
    continuation = None
    while True:
        args = {"Bucket": artifact_store.bucket, "Prefix": prefix}
        if continuation:
            args["ContinuationToken"] = continuation
        page = artifact_store.s3.list_objects_v2(**args)
        for entry in page.get("Contents", []):
            key = entry["Key"]
            if not key.startswith(prefix) or not key.endswith(".json"):
                continue
            saved = json.loads(artifact_store.s3.get_object(
                Bucket=artifact_store.bucket, Key=key)["Body"].read())
            yield key, saved
        if not page.get("IsTruncated"):
            return
        next_token = page.get("NextContinuationToken")
        if not next_token or next_token == continuation:
            raise ValueError("translation checkpoint listing has invalid pagination")
        continuation = next_token


def translation_manual_overrides(artifact_store: Any, inp: TranslateContentInput, workflow_id: str) -> list[dict[str, Any]]:
    originals = {x.segment_id:x for x in translation_source_segments(artifact_store, inp)}
    prefix = f"_system/translation-overrides/{workflow_id}/"
    output = []
    for page in artifact_store.s3.get_paginator("list_objects_v2").paginate(Bucket=artifact_store.bucket, Prefix=prefix):
        for entry in page.get("Contents", []):
            data = json.loads(artifact_store.s3.get_object(Bucket=artifact_store.bucket, Key=entry["Key"])["Body"].read())
            if (data.get("workflow_id") != workflow_id or data.get("source_sha256") != inp.source_artifact_ref["sha256"]
                or data.get("source_language") != inp.source_language or data.get("target_language") != inp.target_language
                or data.get("segment_id") not in originals):
                raise ValueError("translation override identity mismatch")
            data["text"] = validate_manual_translation(originals[data["segment_id"]].text, data["text"])
            output.append(data)
    return sorted(output, key=lambda x:(x["created_at"], x["revision_id"]))


class RetranslateSegmentInput(BaseModel):
    original: TranslateContentInput
    parent_workflow_id: str = Field(pattern=r"^b2c-translate-[0-9a-f]{64}$")
    segment_id: str = Field(min_length=1, max_length=160)
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TranslationActivities:
    def __init__(self, artifact_store: Any, gateway: Any, booktrans_api_key: str | None = None) -> None:
        self.artifact_store = artifact_store
        self.gateway = gateway
        self.booktrans_api_key = booktrans_api_key

    @activity.defn(name="retranslate_segment")
    async def retranslate_segment(self, inp: RetranslateSegmentInput) -> dict[str, Any]:
        description = await self.temporal_client.get_workflow_handle(inp.parent_workflow_id).describe()
        if getattr(getattr(description, "status", None), "name", "") not in {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            raise ApplicationError("parent translation is active", non_retryable=True)
        candidate_workflow_id = activity.info().workflow_id
        return await asyncio.to_thread(self._retranslate_segment, inp, candidate_workflow_id)

    def _retranslate_segment(self, inp: RetranslateSegmentInput, candidate_workflow_id: str) -> dict[str, Any]:
        original = inp.original
        saved_input = TranslateContentInput.model_validate(json.loads(self.artifact_store.s3.get_object(
            Bucket=self.artifact_store.bucket, Key=f"_system/translation-requests/{inp.parent_workflow_id}.json")["Body"].read()))
        if saved_input.model_dump(exclude={"activity_task_queue"}) != original.model_dump(exclude={"activity_task_queue"}):
            raise ApplicationError("original translation request mismatch", non_retryable=True)
        ref = ArtifactRef.model_validate(original.source_artifact_ref)
        expected = "ingest-parse-pdf" if original.source_kind == "book" else "generate-course-qa"
        if ref.stage != expected or not ref.uri.startswith(f"s3://{self.artifact_store.bucket}/{original.course_id}/"):
            raise ApplicationError("invalid original translation source", non_retryable=True)
        sources = translation_source_segments(self.artifact_store, original)
        matches = [x for x in sources if x.segment_id == inp.segment_id]
        if len(matches) != 1 or hashlib.sha256(matches[0].text.encode()).hexdigest() != inp.source_text_sha256:
            raise ApplicationError("selected source segment identity mismatch", non_retryable=True)
        segment = matches[0]
        candidate_key = f"_system/translation-candidates/{candidate_workflow_id}.json"
        try:
            saved = json.loads(self.artifact_store.s3.get_object(Bucket=self.artifact_store.bucket, Key=candidate_key)["Body"].read())
        except Exception as exc:
            if getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                raise
        else:
            if saved.get("parent_workflow_id") != inp.parent_workflow_id or saved.get("segment_id") != inp.segment_id or saved.get("source_text_sha256") != inp.source_text_sha256:
                raise ApplicationError("candidate identity mismatch", non_retryable=True)
            saved["text"] = validate_manual_translation(segment.text, saved["text"])
            return saved
        if original.selected_translation_engine == "booktrans-pro":
            from .booktrans_route import run_booktrans_pro
            result = run_booktrans_pro(
                [segment],
                work_root=os.getenv("B2C_BOOKTRANS_WORK_DIR", "/var/lib/booktrans"),
                source_identity=inp.source_text_sha256,
                model_id=original.selected_model_id or self.gateway.model_id,
                base_url=self.gateway.base_url,
                api_key=self.booktrans_api_key,
                source_language=original.source_language,
                target_language=original.target_language,
                glossary=original.contextual_glossary.mappings if original.contextual_glossary else None,
                domain_id=original.contextual_glossary.domain_id if original.contextual_glossary else None,
            )
            if len(result) != 1 or result[0].segment_id != segment.segment_id:
                raise ApplicationError("BookTrans isolated retry returned an invalid unit", non_retryable=True)
            pack = translate_segments([segment], self.gateway, source_language=original.source_language,
                target_language=original.target_language, max_tokens=original.max_tokens, batch_size=1,
                glossary=original.contextual_glossary.mappings if original.contextual_glossary is not None else None,
                resume_segments=result)
        else:
            pack = translate_segments([segment], self.gateway, source_language=original.source_language,
                target_language=original.target_language, max_tokens=original.max_tokens, batch_size=1,
                glossary=original.contextual_glossary.mappings if original.contextual_glossary is not None else None)
        raw_pack = pack
        pack, edit_audit = correct_pack(pack, [segment], self.artifact_store,
            glossary=original.contextual_glossary.mappings if original.contextual_glossary is not None else None)
        if len(pack.segments) != 1 or pack.segments[0].segment_id != segment.segment_id:
            raise ApplicationError("isolated translation must return exactly the selected segment", non_retryable=True)
        record = {"candidate_workflow_id":candidate_workflow_id, "parent_workflow_id":inp.parent_workflow_id,
            "segment_id":segment.segment_id, "source_text_sha256":inp.source_text_sha256,
            "source_sha256":ref.sha256, "source_artifact_ref":original.source_artifact_ref,
            "source_language":original.source_language, "target_language":original.target_language,
            "text":validate_manual_translation(segment.text, pack.segments[0].text), "citations":segment.citations,
            "raw_text": raw_pack.segments[0].text, "arabic_editing": edit_audit,
            "glossary_sha256": glossary_identity(original.contextual_glossary),
            "model_id":self.gateway.model_id, "created_at":datetime.now(UTC).isoformat(),
            "status":"awaiting_review", "prompt_version":"phase7-translation-v4"}
        self.artifact_store.s3.put_object(Bucket=self.artifact_store.bucket, Key=candidate_key,
            Body=json.dumps(record, ensure_ascii=False).encode(), ContentType="application/json", IfNoneMatch="*")
        return record

    @activity.defn(name="translate_content")
    def translate(self, inp: TranslateContentInput) -> dict[str, Any]:
        try:
            workflow_id = activity.info().workflow_id
        except RuntimeError:
            # Supports direct unit invocation outside a Temporal activity.
            return self._translate_content(inp)
        try:
            configured_interval = float(os.getenv("B2C_TRANSLATION_HEARTBEAT_INTERVAL_SECONDS", "30"))
        except ValueError:
            configured_interval = 30.0
        interval = min(300.0, max(5.0, configured_interval))
        stop, heartbeat_thread = _start_activity_heartbeat_pump(workflow_id, interval_seconds=interval)
        try:
            return self._translate_content(inp)
        finally:
            stop.set()
            heartbeat_thread.join(timeout=2.0)

    def _translate_content(self, inp: TranslateContentInput) -> dict[str, Any]:
        source_ref = ArtifactRef.model_validate(inp.source_artifact_ref)
        if source_ref.stage != ("ingest-parse-pdf" if inp.source_kind == "book" else "generate-course-qa"):
            raise ApplicationError("source artifact stage does not match source_kind",
                                   type="InvalidTranslationSource", non_retryable=True)
        if not source_ref.uri.startswith(f"s3://{self.artifact_store.bucket}/{inp.course_id}/"):
            raise ApplicationError("source artifact is outside the requested course",
                                   type="TranslationScopeMismatch", non_retryable=True)
        workflow_id = activity.info().workflow_id
        source_hash = source_ref.sha256
        source_identity = artifact_source_identity(self.artifact_store, source_ref)
        run_identity = {
            "source_sha256": source_hash, "source_kind": inp.source_kind,
            "source_language": inp.source_language, "target_language": inp.target_language,
            "max_tokens": inp.max_tokens, "model_id": inp.selected_model_id or self.gateway.model_id,
            "translation_engine": inp.selected_translation_engine,
            "provider_id": inp.selected_provider_id or "configured-llm",
            "prompt_version": "phase7-translation-v7-source-boundary",
        }
        # Raw translation checkpoints depend on terminology, but not on a later editor.
        if inp.contextual_glossary is not None:
            run_identity["glossary_sha256"] = glossary_identity(inp.contextual_glossary)
        editing_identity = correction_identity() if inp.target_language == "ar" else {"policy": "not_applicable"}
        glossary = inp.contextual_glossary.mappings if inp.contextual_glossary is not None else {}
        run_fingerprint = hashlib.sha256(
            json.dumps(run_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        progress_key = f"_system/translation-progress/{workflow_id}.json"
        batch_prefix = f"_system/translation-checkpoints/{workflow_id}/{run_fingerprint}"

        record: dict[str, Any] = {
            "workflow_id": workflow_id, "course_id": inp.course_id,
            "source_kind": inp.source_kind, "source_language": inp.source_language,
            "target_language": inp.target_language, "model_id": self.gateway.model_id,
            "source_sha256": source_hash, "source_identity": source_identity, "run_fingerprint": run_fingerprint,
            "status": "RUNNING", "event": "started",
            "started_at": datetime.now(UTC).isoformat(),
            "completed_segments": 0, "total_segments": 0,
            "completed_segment_ids": [], "current_segment_ids": [],
            "completed_citations": [], "current_citations": [],
            "completed_batches": [],
        }

        def save_progress(progress: dict[str, Any]) -> None:
            batch_translations = progress.pop("batch_translations", None)
            failure_map = {item.get("segment_id"): item for item in record.get("failed_units", [])
                           if item.get("segment_id")}
            incoming_failures = progress.get("failed_units", [])
            for item in incoming_failures:
                segment_id = item.get("segment_id")
                if not segment_id:
                    continue
                previous = failure_map.get(segment_id, {})
                # Final summaries repeat the failure manifest; they do not perform
                # another model attempt and must not inflate the retry counter.
                summary = progress.get("event") in {"translation_partial", "translation_completed", "result_committed"}
                attempts = (int(previous.get("attempts", 0)) if summary and previous else
                            int(previous.get("attempts", 0)) + int(item.get("attempts", 1)))
                failure_map[segment_id] = {**previous, **item, "attempts": attempts}
            resolved_ids = set(progress.get("completed_segment_ids", []))
            if batch_translations is not None:
                resolved_ids.update(item.get("segment_id") for item in batch_translations)
            for segment_id in resolved_ids:
                failure_map.pop(segment_id, None)
            if progress.get("event") == "translation_partial":
                for segment_id in progress.get("failed_segment_ids", []):
                    if segment_id not in failure_map:
                        failure_map[segment_id] = {
                            "segment_id": segment_id, "error_category": "unclassified",
                            "error_type": "MissingCheckpoint", "error_reason": "unresolved_without_saved_attempt_detail",
                            "attempts": 0,
                        }
            progress["failed_units"] = list(failure_map.values())
            progress["failed_segment_ids"] = sorted(failure_map)
            progress["failed_segments"] = len(failure_map)
            if batch_translations is not None:
                batch_index = int(progress["batch_index"])
                checkpoint = {
                    **run_identity, "source_identity": source_identity, "run_fingerprint": run_fingerprint,
                    "arabic_editing": editing_identity,
                    "batch_index": batch_index, "segments": batch_translations,
                    "completed_at": datetime.now(UTC).isoformat(),
                }
                self.artifact_store.s3.put_object(
                    Bucket=self.artifact_store.bucket,
                    Key=f"{batch_prefix}/batch-{batch_index:06d}.json",
                    Body=json.dumps(checkpoint, ensure_ascii=False).encode("utf-8"),
                    ContentType="application/json",
                )
            if progress.get("event") == "checkpoint_audited" and record.get("suspect_segment_ids"):
                previous = {x["segment_id"]: x for x in record.get("suspect_details", [])}
                previous.update({x["segment_id"]: x for x in progress.get("suspect_details", [])})
                progress.update({"suspect_details": list(previous.values()),
                                 "suspect_segment_ids": list(previous),
                                 "suspect_segments": len(previous)})
            record.update(progress)
            if progress.get("event") in {"batch_completed", "batch_resumed", "batch_partial_completed"}:
                completed_ids = set(record.get("completed_segment_ids", []))
                checkpointed_ids = set(record.get("checkpointed_segment_ids", [])) | completed_ids
                record["checkpointed_segment_ids"] = sorted(checkpointed_ids)
                record["checkpointed_segments"] = len(checkpointed_ids)
                record["successful_segments"] = len(completed_ids)
                remaining_issues = [x for x in record.get("suspect_details", [])
                                    if x["segment_id"] not in completed_ids]
                record.update({"suspect_details": remaining_issues,
                               "suspect_segment_ids": [x["segment_id"] for x in remaining_issues],
                               "suspect_segments": len(remaining_issues)})
            now = datetime.now(UTC).isoformat()
            record["updated_at"] = now
            if progress.get("event") in {"batch_completed", "batch_resumed", "batch_partial_completed", "batch_retry", "batch_deferred", "batch_failed"}:
                record["last_progress_at"] = now
            if progress.get("event") in {"batch_completed", "batch_resumed"}:
                completed_batches = set(record.get("completed_batches", []))
                completed_batches.add(int(progress["batch_index"]))
                record["completed_batches"] = sorted(completed_batches)
            try:
                activity.heartbeat(record)
            except RuntimeError:
                pass
            self.artifact_store.s3.put_object(
                Bucket=self.artifact_store.bucket, Key=progress_key,
                Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                ContentType="application/json",
            )
            if batch_translations is not None:
                wait_for_activity_checkpoint(self.artifact_store, f"translation-batch:{progress['batch_index']}")

        save_progress({"event": "started"})
        try:
            source_segments = translation_source_segments(self.artifact_store, inp)
            if not source_segments:
                raise ValueError("source artifact contains no translatable text")
            total_batches = len(plan_translation_batches(source_segments, 4))
            resume_segments = []
            # Actual page/character-bounded batches can exceed the nominal
            # four-segment plan. Discover all committed objects, including tails.
            for key, saved in translation_checkpoint_records(self.artifact_store, batch_prefix + "/"):
                if any(saved.get(name) != value for name, value in run_identity.items()):
                    raise ValueError("translation checkpoint identity does not match this run")
                batch_index = int(saved.get("batch_index", 0))
                if (saved.get("run_fingerprint") != run_fingerprint or batch_index < 1
                        or key != f"{batch_prefix}/batch-{batch_index:06d}.json"):
                    raise ValueError("translation checkpoint fingerprint or batch number is invalid")
                resume_segments.extend(TranslatedSegment.model_validate(item) for item in saved.get("segments", []))
            # Keep only validated, source-compatible checkpoints from older run
            # fingerprints. A prompt-version improvement should target unresolved
            # units, not cause a paid replay of every already-saved segment.
            checkpoint_workflow_ids = list(dict.fromkeys(
                item for item in (inp.resume_from_workflow_id, workflow_id) if item
            ))
            seeded: dict[str, tuple[datetime, TranslatedSegment]] = {}
            source_by_id = {item.segment_id: item for item in source_segments}
            compatible = {name: value for name, value in run_identity.items()
                          if name not in {"max_tokens", "prompt_version", "model_id",
                                          "translation_engine", "provider_id"}}
            for checkpoint_workflow_id in checkpoint_workflow_ids:
                prefix = f"_system/translation-checkpoints/{checkpoint_workflow_id}/"
                token = None
                while True:
                    args = {"Bucket": self.artifact_store.bucket, "Prefix": prefix}
                    if token:
                        args["ContinuationToken"] = token
                    page = self.artifact_store.s3.list_objects_v2(**args)
                    for entry in page.get("Contents", []):
                        try:
                            saved = json.loads(self.artifact_store.s3.get_object(
                                Bucket=self.artifact_store.bucket, Key=entry["Key"])["Body"].read())
                            merge_translation_checkpoint(saved, compatible, source_by_id, seeded)
                        except Exception:
                            continue
                    if not page.get("IsTruncated"):
                        break
                    token = page.get("NextContinuationToken")
            resumed_by_id = {item.segment_id: item for item in resume_segments}
            resumed_by_id.update({key: value[1] for key, value in seeded.items()})
            resume_segments = list(resumed_by_id.values())
            overrides = []
            for override_workflow in dict.fromkeys([inp.resume_from_workflow_id, workflow_id]):
                if override_workflow:
                    overrides.extend(translation_manual_overrides(self.artifact_store, inp, override_workflow))
            if inp.manual_override_revision_ids is not None:
                allowed_revisions = set(inp.manual_override_revision_ids)
                overrides = [x for x in overrides if x["revision_id"] in allowed_revisions]
                if {x["revision_id"] for x in overrides} != allowed_revisions:
                    raise ValueError("translation override snapshot is incomplete")
            if overrides:
                resumed_by_id = {x.segment_id:x for x in resume_segments}
                for correction in sorted(overrides, key=lambda x:(x["created_at"], x["revision_id"])):
                    resumed_by_id[correction["segment_id"]] = TranslatedSegment(segment_id=correction["segment_id"], text=correction["text"])
                resume_segments = list(resumed_by_id.values())
            if inp.selected_translation_engine == "booktrans-pro":
                from .booktrans_route import booktrans_usage_summary, run_booktrans_pro
                selected_model_id = inp.selected_model_id or self.gateway.model_id
                booktrans_work_root = os.getenv("B2C_BOOKTRANS_WORK_DIR", "/var/lib/booktrans")
                booktrans_domain = inp.contextual_glossary.domain_id if inp.contextual_glossary else None
                route_segments = run_booktrans_pro(
                    source_segments,
                    work_root=booktrans_work_root,
                    source_identity=source_identity,
                    model_id=selected_model_id,
                    base_url=self.gateway.base_url,
                    api_key=self.booktrans_api_key,
                    source_language=inp.source_language,
                    target_language=inp.target_language,
                    glossary=glossary,
                    domain_id=booktrans_domain,
                    seed_translations=resume_segments,
                    prompt_version="phase7-booktrans-v1",
                )
                record["booktrans_usage"] = booktrans_usage_summary(
                    booktrans_work_root, source_identity=source_identity,
                    model_id=selected_model_id, base_url=self.gateway.base_url,
                    source_language=inp.source_language,
                    target_language=inp.target_language, glossary=glossary,
                    domain_id=booktrans_domain, prompt_version="phase7-booktrans-v1")
                save_progress({"event": "booktrans_passes_completed",
                               "booktrans_usage": record["booktrans_usage"]})
                routed_by_id = {item.segment_id: item for item in resume_segments}
                routed_by_id.update({item.segment_id: item for item in route_segments})
                resume_segments = list(routed_by_id.values())
            source_by_id = {x.segment_id: x for x in source_segments}
            record["checkpointed_segment_ids"] = sorted({x.segment_id for x in resume_segments})
            record["checkpointed_segments"] = len(record["checkpointed_segment_ids"])
            suspect_details = []
            accepted_resume_segments = []
            for translated in resume_segments:
                source = source_by_id.get(translated.segment_id)
                if source is None:
                    raise ValueError("translation checkpoint contains unknown source segment")
                issue = translation_completeness_issue(source.text, translated.text, target_language=inp.target_language)
                if issue:
                    suspect_details.append({"segment_id": translated.segment_id, **issue})
                else:
                    accepted_resume_segments.append(translated)
            resume_segments = accepted_resume_segments
            record.update({"suspect_segments": len(suspect_details),
                           "suspect_segment_ids": [x["segment_id"] for x in suspect_details],
                           "suspect_details": suspect_details,
                           "completeness_policy": "long English prose blocks in Arabic output are rejected; source_chars>=200 and target/source<0.45"})
            record["total_segments"] = len(source_segments)
            record["total_batches"] = total_batches
            save_progress({"event": "prepared", "total_segments": len(source_segments),
                           "total_batches": total_batches, "completed_segments": len(resume_segments),
                           "completed_segment_ids": [item.segment_id for item in resume_segments]})
            pack = translate_segments(source_segments, self.gateway,
                source_language=inp.source_language, target_language=inp.target_language,
                max_tokens=inp.max_tokens, progress_callback=save_progress,
                prompt_version=f"phase7-translation-v7-source-boundary:workflow-{workflow_id}",
                resume_segments=resume_segments, batch_size=4, glossary=glossary)
            unresolved_suspect_ids = [detail["segment_id"] for detail in suspect_details
                                      if detail["segment_id"] in pack.missing_segment_ids]
            pack = pack.model_copy(update={"suspect_segment_ids": sorted(
                set(pack.suspect_segment_ids) | set(unresolved_suspect_ids))})
            raw_pack = pack
            arabic_edit_identity = editing_identity

            def save_arabic_correction_progress(correction: dict[str, Any]) -> None:
                now = datetime.now(UTC).isoformat()
                record["arabic_correction"] = correction
                record["updated_at"] = now
                # Arabic correction is a separate durable sub-stage; its updates
                # must not be confused with completed translation units.
                record["last_progress_at"] = now
                try:
                    activity.heartbeat(record)
                except RuntimeError:
                    pass
                self.artifact_store.s3.put_object(
                    Bucket=self.artifact_store.bucket, Key=progress_key,
                    Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                    ContentType="application/json",
                )

            pack, arabic_edit_audit = correct_pack(
                pack, source_segments, self.artifact_store, glossary=glossary,
                progress_callback=save_arabic_correction_progress,
            )
            config = {"source_kind": inp.source_kind, "source_language": inp.source_language,
                      "target_language": inp.target_language, "arabic_editing": arabic_edit_identity,
                      "glossary_sha256": glossary_identity(inp.contextual_glossary),
                      "translation_engine": inp.selected_translation_engine,
                      "provider_id": inp.selected_provider_id or "configured-llm",
                      "selected_model_id": inp.selected_model_id or self.gateway.model_id}
            if overrides:
                config["manual_overrides_sha256"] = hashlib.sha256(json.dumps(overrides, sort_keys=True).encode()).hexdigest()
            # An incomplete result must never poison the complete-result cache.
            # Hash the accepted payload and gap manifest; retries create a new
            # immutable artifact even when source/model identities are unchanged.
            config["raw_result_sha256"] = hashlib.sha256(raw_pack.model_dump_json().encode()).hexdigest()
            config["result_sha256"] = hashlib.sha256(pack.model_dump_json().encode()).hexdigest()
            key = stage_key("translate-content", "3", [source_ref.sha256], config,
                            model_id=inp.selected_model_id or self.gateway.model_id,
                            prompt_version="phase7-translation-v7-source-boundary")
            result_ref, cache_hit = self.artifact_store.get_or_compute(inp.course_id,
                "translate-content", key, lambda: {"translation.json": pack.model_dump_json(indent=2).encode("utf-8"),
                                                   "translation-raw.json": raw_pack.model_dump_json(indent=2).encode("utf-8"),
                                                   "translation-glossary.json": json.dumps(inp.contextual_glossary.model_dump(mode="json") if inp.contextual_glossary is not None else None, ensure_ascii=False).encode("utf-8"),
                                                   "translation.txt": "\n\n".join(x.text for x in pack.segments).encode("utf-8"), "translation-overrides.json": json.dumps(overrides, ensure_ascii=False).encode("utf-8"), "translation-arabic-editing.json": json.dumps(arabic_edit_audit, ensure_ascii=False, indent=2).encode("utf-8"), "translation-source.json": json.dumps({"source_artifact_ref": source_ref.model_dump(mode="json"), "source_identity": source_identity}).encode("utf-8"),
                                                   **({"translation-booktrans-usage.json": json.dumps(record.get("booktrans_usage", {}), ensure_ascii=False, indent=2).encode("utf-8")} if inp.selected_translation_engine == "booktrans-pro" else {})})
            save_progress({"event": "result_committed", "status": "PARTIAL" if pack.status == "partial" else "COMPLETED",
                           "result_status": pack.status, "artifact": result_ref.model_dump(mode="json"),
                           "missing_segment_ids": pack.missing_segment_ids, "retry_segment_ids": pack.missing_segment_ids,
                           "retry_segments": len(pack.missing_segment_ids),
                           "failed_segment_ids": pack.failed_segment_ids, "current_segment_ids": []})
        except ApplicationError:
            raise
        except Exception as exc:
            cause = exc
            while cause is not None:
                if isinstance(cause, ApplicationError) and cause.type == "StageCheckpointPaused":
                    raise ApplicationError(str(cause), type="StageCheckpointPaused", non_retryable=True) from None
                cause = cause.__cause__
            raise ApplicationError("content translation failed", type="ContentTranslationFailed") from exc
        return {"course_id": inp.course_id, "source_kind": inp.source_kind,
                "source_language": inp.source_language, "target_language": inp.target_language,
                "status": pack.status, "artifact": result_ref.model_dump(mode="json"),
                "cache_hit": cache_hit, "segment_count": len(pack.segments),
                "total_segments": len(source_segments), "missing_segment_ids": pack.missing_segment_ids,
                "retry_segment_ids": pack.missing_segment_ids, "retry_segments": len(pack.missing_segment_ids),
                "failed_segment_ids": pack.failed_segment_ids, "suspect_segment_ids": pack.suspect_segment_ids,
                "model_id": inp.selected_model_id or self.gateway.model_id,
                "translation_engine": inp.selected_translation_engine,
                "booktrans_usage": record.get("booktrans_usage")}


@workflow.defn
class TranslateContentWorkflow:
    @workflow.signal(name="resume_stage")
    def resume_stage(self) -> None:
        self._stage_resume_requested = True

    @workflow.run
    async def run(self, inp: TranslateContentInput) -> dict[str, Any]:
        options = {
            "task_queue": inp.activity_task_queue,
            "start_to_close_timeout": timedelta(hours=24),
            "schedule_to_close_timeout": timedelta(hours=48),
            "retry_policy": RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30),
                                         maximum_interval=timedelta(minutes=5)),
        }
        # Keep pre-existing executions replay-compatible; new runs can detect a
        # genuinely wedged worker after exceeding the model server's 30m ceiling.
        heartbeat_timeout_v1 = workflow.patched("translation-heartbeat-timeout-v1")
        heartbeat_timeout_v2 = workflow.patched("translation-heartbeat-timeout-v2")
        long_run_timeouts = workflow.patched("translation-long-run-timeouts-v1")
        if heartbeat_timeout_v2:
            options["heartbeat_timeout"] = timedelta(minutes=2)
        elif heartbeat_timeout_v1:
            options["heartbeat_timeout"] = timedelta(minutes=40)
        if long_run_timeouts:
            # Large books can take longer than one day even when each unit is
            # checkpointed. Keep bounded attempts, but allow the workflow to
            # survive several daily checkpoint/retry cycles.
            options["start_to_close_timeout"] = timedelta(hours=72)
            options["schedule_to_close_timeout"] = timedelta(days=7)
        return await execute_checkpointed(self, "translate_content", inp, **options)


@workflow.defn
class RetranslateSegmentWorkflow:
    @workflow.run
    async def run(self, inp: RetranslateSegmentInput) -> dict[str, Any]:
        return await workflow.execute_activity("retranslate_segment", inp,
            task_queue=inp.original.activity_task_queue, start_to_close_timeout=timedelta(minutes=30),
            schedule_to_close_timeout=timedelta(hours=24), retry_policy=RetryPolicy(maximum_attempts=2))
