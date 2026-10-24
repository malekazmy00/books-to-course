"""Read-only, course-scoped exports of committed non-audio artifacts."""
from __future__ import annotations
import io
import json
import re
import tempfile
import zipfile
from typing import Callable
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from b2c_contracts import ArtifactRef

# Explicit allowlist excludes secrets, internal checkpoints and audio.
RESULT_STAGES = {"ingest-parse-pdf", "approve-outline", "generate-course-qa", "translate-content", "index-approved-book"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

class ResultSelection(BaseModel):
    result_ids: list[str] = Field(default_factory=list, max_length=500)

def scoped_ref(store, bucket: str, course_id: str, result_id: str) -> ArtifactRef:
    if not SAFE_ID.fullmatch(course_id) or ".." in course_id:
        raise HTTPException(422, "invalid course_id")
    parts = result_id.split(":")
    if len(parts) != 2 or parts[0] not in RESULT_STAGES or not re.fullmatch(r"[0-9a-f]{64}", parts[1]):
        raise HTTPException(422, "invalid result identity")
    stage, key = parts
    prefix = f"{course_id}/{stage}/{key}/"
    try:
        ref = ArtifactRef.model_validate_json(store.s3.get_object(Bucket=bucket, Key=prefix + "_manifest.json")["Body"].read())
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if isinstance(exc, KeyError) or code in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(404, "saved result not found") from exc
        raise HTTPException(503, "saved result unavailable") from exc
    if ref.stage != stage or ref.key != key or ref.uri != f"s3://{bucket}/{prefix}":
        raise HTTPException(503, "saved result failed course scope validation")
    return ref

def available_results(store, bucket: str, course_id: str) -> list[dict]:
    if not SAFE_ID.fullmatch(course_id) or ".." in course_id:
        raise HTTPException(422, "invalid course_id")
    found = []
    args = {"Bucket": bucket, "Prefix": course_id + "/"}
    while True:
        page = store.s3.list_objects_v2(**args)
        for entry in page.get("Contents", []):
            key = entry["Key"]
            parts = key.split("/")
            if len(parts) == 4 and parts[1] in RESULT_STAGES and parts[3] == "_manifest.json":
                identity = parts[1] + ":" + parts[2]
                ref = scoped_ref(store, bucket, course_id, identity)
                files = sorted(store.list_files(ref))
                found.append({"result_id": identity, "stage": ref.stage,
                    "artifact_ref": ref.model_dump(mode="json"), "files": files,
                    "created_at": ref.created_at.isoformat(), "course_id": course_id})
        token = page.get("NextContinuationToken")
        if not token:
            break
        args["ContinuationToken"] = token
    return sorted(found, key=lambda item: (item["created_at"], item["result_id"]), reverse=True)

def read_result_file(store, bucket: str, course_id: str, result_id: str, name: str) -> tuple[ArtifactRef, bytes]:
    ref = scoped_ref(store, bucket, course_id, result_id)
    if name not in store.list_files(ref) or "\\" in name or name.startswith("/") or any(p in {"", ".", ".."} for p in name.split("/")):
        raise HTTPException(404, "saved result file not found")
    return ref, store.read(ref, name)

def result_units(document: dict) -> list[dict]:
    """Keep exact saved content and citations; do not regenerate or combine books."""
    output = []
    outline = document.get("outline", document)
    lessons = list(document.get("lessons", []))
    if isinstance(outline, dict):
        for module in outline.get("modules", []):
            if isinstance(module, dict):
                lessons.extend(module.get("lessons", []))
    for kind, records in (("lesson", lessons), ("questions", document.get("scopes", [])), ("translation-segment", document.get("segments", [])), ("ocr-block", document.get("blocks", []))):
        if isinstance(records, list):
            for n, record in enumerate(records, 1):
                if isinstance(record, dict):
                    output.append({"unit_id": f"{kind}-{n:04d}", "kind": kind, "content": record})
    return output

def unit_markdown(unit: dict, course_id: str, ref: ArtifactRef) -> str:
    content = unit["content"]
    title = content.get("title") or content.get("scope_title") or content.get("lesson_id") or content.get("segment_id") or unit["unit_id"]
    lines = [f"# {title}", "", f"Course: {course_id}", f"Source artifact: {ref.uri}", f"Source SHA-256: {ref.sha256}", ""]
    for field in ("text", "summary", "body", "content"):
        if isinstance(content.get(field), str):
            lines += [content[field], ""]
    for n, item in enumerate(content.get("items", content.get("questions", [])), 1):
        if isinstance(item, dict):
            lines += [f"## {n}. {item.get('question', '')}", "", str(item.get("answer", "")), ""]
            if item.get("evidence_quote"):
                lines += ["Evidence: " + str(item["evidence_quote"]), ""]
            if item.get("pages"):
                lines += ["Pages: " + ", ".join(map(str, item["pages"])), ""]
    return "\n".join(lines)

def build_result_archive(store, bucket: str, course_id: str, result_ids: list[str]):
    # Write archive on disk; never collect a whole course in memory.
    selected = result_ids or [r["result_id"] for r in available_results(store, bucket, course_id)]
    if len(selected) > 500:
        raise HTTPException(413, "select at most 500 saved results")
    if not selected:
        raise HTTPException(404, "no committed results for this course")
    archive = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
            for identity in dict.fromkeys(selected):
                ref = scoped_ref(store, bucket, course_id, identity)
                directory = ref.stage + "/" + ref.key + "/"
                provenance = {"course_id": course_id, "artifact_ref": ref.model_dump(mode="json")}
                target.writestr(directory + "provenance.json", json.dumps(provenance, ensure_ascii=False))
                for name in sorted(store.list_files(ref)):
                    _, data = read_result_file(store, bucket, course_id, identity, name)
                    target.writestr(directory + name, data)
                    # Retain original document plus individually addressable lesson records.
                    if name.endswith(".json"):
                        try:
                            document = json.loads(data)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        units = result_units(document) if isinstance(document, dict) else []
                        for unit in units:
                            target.writestr(directory + f"separate-units/{unit['unit_id']}.json",
                                json.dumps({**provenance, **unit}, ensure_ascii=False))
                            target.writestr(directory + f"separate-units/{unit['unit_id']}.md",
                                unit_markdown(unit, course_id, ref))
        archive.seek(0)
        return archive
    except Exception:
        archive.close()
        raise

def result_exports_router(store_provider: Callable, bucket: str) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/courses/{course_id}/results")
    async def discover(course_id: str) -> dict:
        items = await run_in_threadpool(available_results, store_provider(), bucket, course_id)
        return {"course_id": course_id, "results": items, "total": len(items),
            "scope": "committed_artifacts_in_this_course", "cross_course_materialization": False}

    @router.get("/v1/courses/{course_id}/results/{result_id}/file")
    async def download(course_id: str, result_id: str, name: str = Query(min_length=1, max_length=300),
                       preview: bool = False):
        ref, data = await run_in_threadpool(read_result_file, store_provider(), bucket, course_id, result_id, name)
        media = "application/json" if name.endswith(".json") else "text/plain; charset=utf-8" if name.endswith((".txt", ".md")) else "application/octet-stream"
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", name.rsplit("/", 1)[-1])
        return Response(data, media_type=media, headers={"Content-Disposition": f'{"inline" if preview else "attachment"}; filename="{filename}"', "X-Artifact-SHA256": ref.sha256})

    @router.get("/v1/courses/{course_id}/results/{result_id}/units")
    async def units(course_id: str, result_id: str, name: str = Query(min_length=1, max_length=300),
                    offset: int = Query(0, ge=0), limit: int = Query(40, ge=1, le=100)):
        ref, data = await run_in_threadpool(read_result_file, store_provider(), bucket, course_id, result_id, name)
        try:
            document = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "select a JSON result document") from exc
        records = result_units(document) if isinstance(document, dict) else []
        return {"course_id": course_id, "artifact_ref": ref.model_dump(mode="json"),
            "units": records[offset:offset+limit], "offset": offset, "total": len(records)}

    @router.get("/v1/courses/{course_id}/results/{result_id}/units/{unit_id}/file")
    async def unit_download(course_id: str, result_id: str, unit_id: str, name: str = Query(min_length=1, max_length=300)):
        ref, data = await run_in_threadpool(read_result_file, store_provider(), bucket, course_id, result_id, name)
        try:
            document = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "select a JSON result document") from exc
        unit = next((u for u in result_units(document) if u["unit_id"] == unit_id), None) if isinstance(document, dict) else None
        if unit is None:
            raise HTTPException(404, "saved result unit not found")
        payload = {"course_id": course_id, "artifact_ref": ref.model_dump(mode="json"), **unit}
        return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{unit_id}.json"'})

    @router.post("/v1/courses/{course_id}/results/export")
    async def export(course_id: str, body: ResultSelection):
        archive = await run_in_threadpool(build_result_archive, store_provider(), bucket, course_id, body.result_ids)
        def chunks():
            while chunk := archive.read(1024 * 1024):
                yield chunk
        return StreamingResponse(chunks(), media_type="application/zip", background=BackgroundTask(archive.close),
            headers={"Content-Disposition": f'attachment; filename="{course_id}-results.zip"'})

    return router



