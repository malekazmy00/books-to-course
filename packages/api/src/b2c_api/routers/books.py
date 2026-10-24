"""Book upload, per-course library listing, progress registry view and source download."""
from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any

from b2c_contracts import (
    ArtifactRef,
    BookUpload,
    ParsedBook,
    stage_key,
)
from b2c_core import ArtifactStore
from b2c_orchestration.ingest import IngestPDFInput, IngestPDFWorkflow
from b2c_orchestration.queues import LIGHT

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from b2c_api.deps import (
    require_book_id,
    require_course_id,
    PROGRESS_WARNING,
    STORAGE_ERRORS,
    with_progress_warning,
    s3_bytes,
    s3_json,
    stream_artifact_file,
)

from fastapi import APIRouter
from b2c_api.deps import ApiContext, CHUNK_BYTES, REUSE_ONLY_AFTER_FAILURE, log
from b2c_api.schemas import UploadMetadata


class UploadTooLarge(ValueError):
    pass


def _hash_upload(stream: Any, max_bytes: int) -> tuple[str, bool]:
    digest = sha256()
    stream.seek(0)
    signature = stream.read(5) == b"%PDF-"
    stream.seek(0)
    total = 0
    while chunk := stream.read(CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest(), signature


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


def books_router(ctx: ApiContext, router: Any = None) -> Any:
    """Register the routes on ``router`` (the app itself in ``create_app``,
    keeping the route table flat and in its original order)."""
    router = APIRouter() if router is None else router
    app = ctx.app
    settings = ctx.settings
    get_store = ctx.get_store
    get_temporal_client = ctx.get_temporal_client
    ingestion_status = ctx.late("ingestion_status")
    indexing_status = ctx.late("indexing_status")
    write_book_progress = ctx.late("write_book_progress")

    async def remember_book_progress(course_id: str, book_id: str, stage: str, workflow_id: str,
                                     metadata: dict[str, Any] | None = None) -> str | None:
        """Record a stage in the book's progress registry.

        The workflow has already been started, so a storage failure is not
        fatal: it is logged and returned as a client-visible warning. Any other
        exception is a bug and propagates.
        """
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
        except STORAGE_ERRORS:
            log.exception("could not persist book workflow progress")
            return PROGRESS_WARNING
        return None

    @router.post("/v1/books", status_code=202)
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
            log.warning("rejected book upload storage request: %s", exc)
            raise HTTPException(status_code=422, detail="uploaded book could not be stored under this course") from exc
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
                    id_reuse_policy=REUSE_ONLY_AFTER_FAILURE,
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
        progress_warning = await remember_book_progress(
            metadata.course_id, canonical_book_id, "ingest", workflow_id,
            {"book_title": metadata.title, "source_file_sha256": digest,
             "source_upload_ref": source_ref.model_dump(mode="json")})
        return with_progress_warning({
            "course_id": metadata.course_id, "book_id": canonical_book_id,
            "workflow_id": workflow_id, "force_reprocess": metadata.force_reprocess,
            "source_artifact": source_ref.model_dump(mode="json"),
            "status_url": f"/v1/ingestions/{workflow_id}",
        }, progress_warning)

    @router.get("/v1/courses/{course_id}/books")
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

    @router.get("/v1/courses/{course_id}/books/{book_id}/progress")
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

    @router.get("/v1/courses/{course_id}/books/{book_id}/source")
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

    ctx.provide(list_course_books=list_course_books, book_progress=book_progress, remember_book_progress=remember_book_progress)
    return router
