"""MP3 generation, status/manifest, track and archive downloads."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from b2c_contracts import (
    ArtifactRef,
)
from b2c_orchestration.audio import GenerateMP3Input, GenerateMP3Workflow, validate_mp3_input_lineage
from b2c_orchestration.queues import AUDIO

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from b2c_api.audio_exports import audio_progress, ordered_audio_tracks, validate_audio_manifest, course_audio_checkpoints, audio_zip
from starlette.concurrency import run_in_threadpool
from temporalio.exceptions import WorkflowAlreadyStartedError
from b2c_api.deps import (
    require_course_id,
    require_workflow_id,
    PROGRESS_WARNING,
    with_progress_warning,
    s3_bytes,
    s3_json,
    stream_artifact_file,
    verified_ref,
)

from fastapi import APIRouter
from b2c_api.deps import ApiContext, CHUNK_BYTES, REUSE_ONLY_AFTER_FAILURE, log
from b2c_api.schemas import MP3GenerationRequest


def audio_router(ctx: ApiContext, router: Any = None) -> Any:
    """Register the routes on ``router`` (the app itself in ``create_app``,
    keeping the route table flat and in its original order)."""
    router = APIRouter() if router is None else router
    settings = ctx.settings
    get_store = ctx.get_store
    get_temporal_client = ctx.get_temporal_client
    remember_book_progress = ctx.late("remember_book_progress")

    @router.post("/v1/courses/{course_id}/audio", status_code=202)
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
            identity = (course_id + source.sha256 + request.source_kind + request.language +
                        request.book_audio_layout + json.dumps([request.lesson_ids or [], request.allow_partial_qa, request.include_summaries, request.include_extra_scopes], separators=(",", ":")) +
                        (outline_ref.sha256 if outline_ref else "") +
                        (request.source_book_ref.sha256 if request.source_book_ref else "") +
                        (request.source_book_translation_ref.sha256 if request.source_book_translation_ref else ""))
            legacy_workflow_id = "b2c-mp3-" + hashlib.sha256(identity.encode()).hexdigest()
            workflow_id = legacy_workflow_id
            if request.source_qa_artifact_ref is not None:
                # The QA source selects different audio content, so it is part
                # of the identity. Requests without it keep their old ID.
                qa_identity = "\0source-qa:" + request.source_qa_artifact_ref.sha256
                workflow_id = "b2c-mp3-" + hashlib.sha256((identity + qa_identity).encode()).hexdigest()
            inp = GenerateMP3Input(course_id=course_id, source_artifact_ref=source.model_dump(mode="json"),
                source_kind=request.source_kind, language=request.language,
                approved_outline_ref=outline_ref.model_dump(mode="json") if outline_ref else None,
                source_qa_artifact_ref=request.source_qa_artifact_ref.model_dump(mode="json") if request.source_qa_artifact_ref else None,
                source_book_ref=request.source_book_ref.model_dump(mode="json") if request.source_book_ref else None,
                source_book_translation_ref=request.source_book_translation_ref.model_dump(mode="json") if request.source_book_translation_ref else None,
                book_audio_layout=request.book_audio_layout, lesson_ids=request.lesson_ids,
                allow_partial_qa=request.allow_partial_qa, include_summaries=request.include_summaries, include_extra_scopes=request.include_extra_scopes,
                activity_task_queue=os.getenv("B2C_AUDIO_TASK_QUEUE", AUDIO))
            if workflow_id != legacy_workflow_id:
                # A run started before the QA ref was hashed keeps serving the
                # same request: reuse its ID only if its saved input names the
                # same QA artifact.
                previous = await load_audio_input(legacy_workflow_id)
                if (previous is not None and previous.course_id == course_id
                        and previous.source_qa_artifact_ref == inp.source_qa_artifact_ref):
                    workflow_id = legacy_workflow_id
            store = get_store()
            try:
                await run_in_threadpool(validate_mp3_input_lineage, store, inp)
            except (ValueError, KeyError, TypeError) as exc:
                log.warning("audio request failed source lineage validation: %s", exc)
                raise HTTPException(status_code=422,
                                    detail="audio sources do not share one validated book lineage") from exc
            except Exception as exc:
                log.exception("could not verify audio source lineage")
                raise HTTPException(status_code=503, detail="audio source lineage could not be verified") from exc
            try:
                await client.start_workflow(GenerateMP3Workflow.run, inp, id=workflow_id, task_queue=inp.activity_task_queue,
                                            id_reuse_policy=REUSE_ONLY_AFTER_FAILURE)
            except WorkflowAlreadyStartedError:
                pass
        except HTTPException: raise
        except Exception as exc:
            log.exception("failed to queue MP3 generation")
            raise HTTPException(status_code=503, detail="MP3 generation could not be queued") from exc
        progress_warning = None
        try:
            if outline_ref is not None:
                store = get_store()
                manifest_bytes = await s3_bytes(store.s3, settings.s3_bucket,
                    outline_ref.uri.removeprefix(f"s3://{settings.s3_bucket}/") + "_manifest.json")
                if manifest_bytes is None:
                    raise ValueError("approved outline manifest is missing")
                outline_manifest = ArtifactRef.model_validate_json(manifest_bytes)
                approved_payload = json.loads(await run_in_threadpool(store.read, outline_manifest, "approved-outline.json"))
                book_id = approved_payload["outline"]["spine_book_id"]
                spine_ref = ArtifactRef.model_validate(approved_payload["spine_parsed_ref"])
                progress_warning = await remember_book_progress(course_id, book_id, "audio", workflow_id, {
                    "source_parsed_sha256": spine_ref.sha256,
                    "outline_sha256": outline_ref.sha256,
                    "source_artifact_sha256": source.sha256,
                    "language": request.language, "lesson_ids": request.lesson_ids,
                })
        except Exception:
            log.exception("could not associate audio workflow with its book")
            progress_warning = PROGRESS_WARNING
        return with_progress_warning({"course_id": course_id, "workflow_id": workflow_id, "status_url": f"/v1/audio/{workflow_id}", "file_url": f"/v1/audio/{workflow_id}/file", "manifest_url": f"/v1/audio/{workflow_id}/manifest"}, progress_warning)

    async def partial_audio_manifest(workflow_id: str) -> dict[str, Any]:
        store = get_store()
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

    @router.get("/v1/audio/{workflow_id}")
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

    @router.get("/v1/audio/{workflow_id}/manifest")
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
        store = get_store()
        try:
            value = json.loads(await run_in_threadpool(store.read, ref, "audio-manifest.json"))
            validate_audio_manifest(value, settings.s3_bucket, result.get("course_id"))
            return await add_audio_lesson_order(value, workflow_id)
        except Exception as exc: raise HTTPException(status_code=503, detail="audio manifest is unavailable") from exc

    @router.get("/v1/audio/{workflow_id}/tracks/{track_id}")
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
        store = get_store()
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
        store = get_store()
        try:
            course_settings = await s3_json(store.s3, settings.s3_bucket, course_id + "/course-settings.json")
        except Exception as exc:
            raise HTTPException(503, "course name is unavailable") from exc
        return str((course_settings or {}).get("name") or course_id)

    async def list_course_audio_runs(course_id: str) -> dict[str, Any]:
        await audio_course_name(course_id)
        store = get_store()
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

    @router.get("/v1/courses/{course_id}/audio/zip")
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
        store = get_store()
        try:
            archive = await run_in_threadpool(audio_zip, store, manifest, name)
        except Exception as exc:
            raise HTTPException(503, "course audio archive is unavailable") from exc
        def chunks():
            try:
                while data := archive.read(CHUNK_BYTES):
                    yield data
            finally:
                archive.close()
        return StreamingResponse(chunks(), media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="course-audio.zip"'},
            background=BackgroundTask(archive.close))

    async def load_audio_input(workflow_id: str) -> GenerateMP3Input | None:
        """The immutable start input of an MP3 run: saved copy, else Temporal history.

        ``None`` when neither source has it; storage failures are 503.
        """
        try:
            saved_input = await s3_bytes(get_store().s3, settings.s3_bucket, f"_system/audio-inputs/{workflow_id}.json")
            if saved_input is not None:
                return GenerateMP3Input.model_validate_json(saved_input)
        except Exception as exc:
            raise HTTPException(503, "audio input is unavailable") from exc
        try:
            client = await get_temporal_client()
            history = await client.get_workflow_handle(workflow_id).fetch_history()
            started = next(e for e in history.events if e.HasField("workflow_execution_started_event_attributes"))
            values = await client.data_converter.decode(started.workflow_execution_started_event_attributes.input.payloads, [GenerateMP3Input])
            return GenerateMP3Input.model_validate(values[0])
        except Exception:
            return None

    @router.get("/v1/audio/{workflow_id}/request")
    async def original_audio_request(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("mp3", workflow_id, "audio workflow not found")
        store = get_store()
        inp = await load_audio_input(workflow_id)
        if inp is None:
            raise HTTPException(409, "original audio input is unavailable")
        try:
            await run_in_threadpool(validate_mp3_input_lineage, store, inp)
        except Exception as exc:
            raise HTTPException(409, "original audio input no longer passes source validation") from exc
        request = MP3GenerationRequest.model_validate(inp.model_dump(exclude={"course_id", "activity_task_queue"}))
        return {"workflow_id": workflow_id, "course_id": inp.course_id,
                "request": request.model_dump(mode="json"), "source_version": "original_validated"}

    @router.post("/v1/courses/{course_id}/audio/regenerate", status_code=202)
    async def regenerate_lesson_audio(course_id: str, request: MP3GenerationRequest) -> dict[str, Any]:
        if not request.lesson_ids or len(request.lesson_ids) != 1 or request.include_extra_scopes:
            raise HTTPException(422, "select exactly one lesson and set include_extra_scopes=false")
        # Updated accepted translation/source ArtifactRefs change the track cache identity.
        # Unchanged valid tracks retain their existing durable cache.
        return await generate_course_audio(course_id, request)

    @router.get("/v1/audio/{workflow_id}/file")
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

    ctx.provide(list_course_audio_runs=list_course_audio_runs)
    return router
