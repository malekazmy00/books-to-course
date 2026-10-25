"""Read-only discovery of independently committed audio with exact book lineage."""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool
from b2c_api.audio_exports import LEGACY_REFUSAL, artifact_unverified
from b2c_api.deps import stream_artifact_file
from b2c_contracts import ArtifactRef


def saved_book_tracks(store: Any, course_id: str, pdf_sha: str, parsed_sha: str) -> list[dict[str, Any]]:
    """Never associate a legacy unbound track or reuse another parsed edition."""
    tracks = []
    args = {"Bucket": store.bucket, "Prefix": f"{course_id}/generate-mp3-track/", "Delimiter": "/"}
    while True:
        page = store.s3.list_objects_v2(**args)
        for item in page.get("CommonPrefixes", []):
            prefix = item["Prefix"]
            try:
                manifest = store.s3.get_object(Bucket=store.bucket, Key=prefix+"_manifest.json")
                ref = ArtifactRef.model_validate_json(manifest["Body"].read())
                if ref.stage != "generate-mp3-track" or ref.uri != f"s3://{store.bucket}/{prefix}":
                    continue
                identity = json.loads(store.read(ref, "source-identity.json"))
                if identity.get("source_file_sha256") != pdf_sha or identity.get("source_parsed_sha256") != parsed_sha:
                    continue
                config = json.loads(store.read(ref, "track.json"))
                mp3 = store.s3.head_object(Bucket=store.bucket, Key=prefix+"track.mp3")
                tracks.append({"artifact_key": ref.key, "artifact_ref": ref.model_dump(mode="json"),
                    "track_id": config["track_id"], "lesson_id": config["track_id"].removesuffix("-source-text").removesuffix("-attachment"),
                    "kind": config.get("kind"), "language": config.get("language"),
                    "title": config["track_id"], "filename": config["track_id"]+".mp3",
                    "bytes": mp3["ContentLength"], "saved_at": ref.created_at.isoformat(),
                    "render_policy": config.get("render_policy", "legacy"),
                    "source_identity": identity, "historical": True,
                    # R0: no verification record (pre-R0) or a legacy mark => unverified.
                    "unverified_legacy": artifact_unverified(store, ref)})
            except Exception:
                # Uncommitted, corrupt, or pre-lineage objects are not selectable.
                continue
        token = page.get("NextContinuationToken")
        if not token:
            break
        args["ContinuationToken"] = token
    return sorted(tracks, key=lambda t: (t["lesson_id"], t["kind"] == "lesson_attachment", t["saved_at"], t["artifact_key"]))


def saved_audio_router(store_provider: Callable, book_progress: Callable, list_course_books: Callable | None = None, list_course_runs: Callable | None = None) -> APIRouter:
    router = APIRouter()

    async def bound_tracks(course_id: str, book_id: str, include_unverified_legacy: bool = False):
        registry = await book_progress(course_id, book_id)
        ingest = registry.get("stages", {}).get("ingest", {})
        pdf_sha = registry.get("source_file_sha256")
        parsed_sha = ingest.get("source_parsed_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", str(pdf_sha or "")) or not re.fullmatch(r"[0-9a-f]{64}", str(parsed_sha or "")):
            raise HTTPException(409, "book source identity is not available yet")
        store = store_provider()
        rows = await run_in_threadpool(saved_book_tracks, store, course_id, pdf_sha, parsed_sha)
        if not include_unverified_legacy:
            rows = [row for row in rows if not row["unverified_legacy"]]
        root = f"/v1/courses/{course_id}/books/{book_id}/saved-audio"
        for row in rows:
            row["download_path"] = root+"/"+row["artifact_key"]
        return store, registry, rows

    @router.get("/v1/courses/{course_id}/audio")
    async def course_audio(course_id: str, book_id: str | None = None,
                           include_unverified_legacy: bool = Query(default=False)):
        # Compatibility for released Android clients. Keep exact source binding
        # and identify each book instead of inheriting whichever run was latest.
        if book_id:
            _, _, rows = await bound_tracks(course_id, book_id, include_unverified_legacy)
            return {"course_id": course_id, "book_id": book_id, "tracks": rows, "track_count": len(rows)}
        if list_course_books is None:
            raise HTTPException(422, "book_id is required")
        catalog = await list_course_books(course_id)
        tracks = []
        for book in catalog.get("books", []):
            try:
                _, _, rows = await bound_tracks(course_id, book["book_id"], include_unverified_legacy)
            except HTTPException as exc:
                if exc.status_code == 409:
                    continue  # Uploaded books may not have parsed text yet.
                raise
            for row in rows:
                tracks.append({**row, "book_id": book["book_id"], "book_title": book.get("title", "")})
        runs = await list_course_runs(course_id, include_unverified_legacy) if list_course_runs is not None else {}
        return {"course_id": course_id, "tracks": tracks, "track_count": len(tracks), **runs}

    @router.get("/v1/courses/{course_id}/books/{book_id}/saved-audio")
    async def saved_audio(course_id: str, book_id: str, include_unverified_legacy: bool = Query(default=False)):
        _, registry, rows = await bound_tracks(course_id, book_id, include_unverified_legacy)
        return {"course_id": course_id, "book_id": book_id,
                "source_file_sha256": registry["source_file_sha256"],
                "source_parsed_sha256": registry["stages"]["ingest"]["source_parsed_sha256"],
                "kind": "historical_saved_audio", "track_count": len(rows), "tracks": rows}

    @router.get("/v1/courses/{course_id}/books/{book_id}/saved-audio/{artifact_key}")
    async def download_saved_audio(course_id: str, book_id: str, artifact_key: str,
                                   allow_unverified_legacy: bool = Query(default=False)):
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_key):
            raise HTTPException(404, "saved audio track not found")
        store, _, rows = await bound_tracks(course_id, book_id, include_unverified_legacy=True)
        entry = next((r for r in rows if r["artifact_key"] == artifact_key), None)
        if entry is None:
            raise HTTPException(404, "saved audio track not found for this book")
        if entry["unverified_legacy"] and not allow_unverified_legacy:
            raise HTTPException(409, LEGACY_REFUSAL)
        try:
            return await stream_artifact_file(store, ArtifactRef.model_validate(entry["artifact_ref"]), "track.mp3",
                media_type="audio/mpeg",
                headers={"Content-Disposition": f'attachment; filename="{entry["filename"]}"',
                         "X-B2C-Verification": "unverified_legacy" if entry["unverified_legacy"] else "accepted"})
        except Exception as exc:
            raise HTTPException(503, "saved audio track is unavailable") from exc

    return router
