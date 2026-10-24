"""Course-scoped audio projections and bounded-memory archive export."""
from __future__ import annotations
import json
import re
import tempfile
import zipfile
from typing import Any
from b2c_contracts import ArtifactRef


def audio_progress(manifest: dict, status: str) -> dict:
    completed = len(manifest.get("tracks", []))
    total = manifest.get("track_count")
    return {"completed_tracks": completed, "track_count": total,
            "remaining_tracks": max(0, total-completed) if isinstance(total, int) else None,
            "failed_tracks": manifest.get("failed_tracks", 0),
            "failed_track_ids": manifest.get("failed_track_ids", []),
            "current_track": manifest.get("current_track"),
            "renderer_progress": manifest.get("renderer_progress"), "status": status,
            "updated_at": manifest.get("updated_at"), "concurrency": manifest.get("concurrency", 1),
            "checkpointing": True}


def ordered_audio_tracks(manifest: dict, course_name: str) -> list[dict[str, Any]]:
    """Retain outline encounter order, place each attachment immediately after its lesson."""
    prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", course_name).strip("-_")[:80] or "Course"
    tracks = manifest.get("tracks", [])
    lessons = list(manifest.get("lesson_order") or dict.fromkeys(str(t["lesson_id"]) for t in tracks if t.get("lesson_id")))
    lesson_numbers = {lesson: n for n, lesson in enumerate(lessons, 1)}
    indexed = list(enumerate(tracks))
    indexed.sort(key=lambda pair: (lesson_numbers.get(str(pair[1].get("lesson_id")), len(lessons)+1),
                                  pair[1].get("kind") == "lesson_attachment", pair[0]))
    output = []
    extras = 0
    for _, track in indexed:
        number = lesson_numbers.get(str(track.get("lesson_id")))
        if number is None:
            extras += 1
            filename = f"Review-{prefix}{extras}.mp3"
        else:
            filename = f"{'Att-' if track.get('kind') == 'lesson_attachment' else ''}{prefix}{number}.mp3"
        output.append({**track, "lesson_number": number, "display_filename": filename,
                       "filename": filename, "order": len(output)+1})
    return output


def validate_audio_manifest(manifest: dict, bucket: str, course_id: str | None = None) -> None:
    cid = manifest.get("course_id")
    if not cid or (course_id is not None and cid != course_id) or not isinstance(manifest.get("tracks"), list):
        raise ValueError("audio manifest is outside the requested course")
    seen = set()
    for track in manifest["tracks"]:
        tid = track.get("track_id")
        if not tid or tid in seen:
            raise ValueError("duplicate or missing audio track identity")
        seen.add(tid)
        ref = ArtifactRef.model_validate(track.get("artifact_ref"))
        if ref.stage != "generate-mp3-track" or not ref.uri.startswith(f"s3://{bucket}/{cid}/"):
            raise ValueError("audio track is outside the requested course")


def course_audio_checkpoints(store, bucket: str, course_id: str) -> list[dict]:
    manifests = []
    args = {"Bucket": bucket, "Prefix": "_system/audio-progress/"}
    while True:
        page = store.s3.list_objects_v2(**args)
        for item in page.get("Contents", []):
            if not item["Key"].endswith(".json"):
                continue
            record = json.loads(store.s3.get_object(Bucket=bucket, Key=item["Key"])["Body"].read())
            if record.get("course_id") != course_id:
                continue
            if not re.fullmatch(r"b2c-mp3-[0-9a-f]{64}", str(record.get("workflow_id", ""))):
                raise ValueError("invalid audio workflow identity")
            validate_audio_manifest(record, bucket, course_id)
            manifests.append(record)
        token = page.get("NextContinuationToken")
        if not token:
            break
        args["ContinuationToken"] = token
    return sorted(manifests, key=lambda m: (m.get("updated_at", ""), m["workflow_id"]), reverse=True)


def audio_zip(store, manifest: dict, course_name: str):
    """Spool archives to disk after 8 MiB; no whole-course in-memory buffer."""
    validate_audio_manifest(manifest, store.bucket)
    target = tempfile.SpooledTemporaryFile(max_size=8*1024*1024, mode="w+b")
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
            tracks = ordered_audio_tracks(manifest, course_name)
            if len({t["filename"] for t in tracks}) != len(tracks):
                raise ValueError("ambiguous lesson tracks cannot be exported together")
            for track in tracks:
                archive.writestr(track["filename"], store.read(ArtifactRef.model_validate(track["artifact_ref"]), "track.mp3"))
            archive.writestr("manifest.json", json.dumps({**manifest, "tracks": tracks}, ensure_ascii=False, indent=2))
        target.seek(0)
        return target
    except Exception:
        target.close()
        raise
