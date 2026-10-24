"""Book progress registry: per-book stage pointers bound to exact source lineage."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any


from fastapi import HTTPException
from b2c_api.deps import (
    is_not_found,
    s3_json,
)

from b2c_api.deps import ApiContext


def _serialize_progress_writes(function):
    lock = threading.RLock()

    def synchronized(*args, **kwargs):
        with lock:
            return function(*args, **kwargs)

    return synchronized


def bind_book_progress(ctx: ApiContext) -> None:
    settings = ctx.settings
    get_store = ctx.get_store

    @_serialize_progress_writes
    def write_book_progress(course_id: str, book_id: str, stage: str, workflow_id: str,
                            metadata: dict[str, Any] | None = None) -> None:
        store = get_store()
        key = f"{course_id}/book-progress/{hashlib.sha256(book_id.encode()).hexdigest()}.json"
        record: dict[str, Any] = {"schema_version": "1.0", "course_id": course_id,
                                  "book_id": book_id, "stages": {}}
        try:
            obj = store.s3.get_object(Bucket=settings.s3_bucket, Key=key)
            record.update(json.loads(obj["Body"].read()))
        except Exception as exc:
            if not is_not_found(exc):
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

    async def verify_resume_owner(course_id, book_id, stage, old_workflow):
        store = get_store()
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

    ctx.provide(write_book_progress=write_book_progress, verify_resume_owner=verify_resume_owner)
