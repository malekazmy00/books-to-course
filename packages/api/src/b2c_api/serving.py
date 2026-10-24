"""Course release serving helpers for Phase 9.

A course is visible only through an immutable course-release artifact. Draft and
internal-test artifacts are never discovered by these public read paths.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from b2c_contracts import ArtifactRef, AudioAsset, CourseOutline, CourseRelease, OutlineApprovalProvenance
from b2c_pedagogy.qa_generation import CourseQAResult
from pydantic import BaseModel, ConfigDict, Field


COURSE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
RELEASE_STAGE = "course-release"
RELEASE_FILE = "serving-manifest.json"
OUTLINE_STAGE = "approve-outline"
QA_STAGE = "generate-course-qa"


class CourseServingManifest(BaseModel):
    """Release envelope linking existing immutable artifacts; no writer is provided here."""
    model_config = ConfigDict(extra="forbid")
    release: CourseRelease
    approved_outline_ref: ArtifactRef
    course_qa_ref: ArtifactRef
    audio_asset_refs: dict[str, ArtifactRef] = Field(default_factory=dict)


@dataclass(frozen=True)
class ServedCourse:
    manifest_ref: ArtifactRef
    release: CourseRelease
    outline: CourseOutline
    outline_envelope: dict[str, Any]
    qa: CourseQAResult
    audio_assets: dict[str, AudioAsset]


class S3CourseServingCatalog:
    def __init__(self, artifact_store: Any, bucket: str) -> None:
        self.store = artifact_store
        self.s3 = artifact_store.s3
        self.bucket = bucket

    def _course_prefix(self, course_id: str) -> str:
        if not COURSE_ID.fullmatch(course_id) or ".." in course_id:
            raise ValueError("invalid course_id")
        return f"{course_id}/"

    def course_ids(self) -> list[str]:
        ids: set[str] = set()
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Delimiter="/"):
            for item in page.get("CommonPrefixes", []):
                prefix = item.get("Prefix", "")
                course_id = prefix[:-1] if prefix.endswith("/") else ""
                if COURSE_ID.fullmatch(course_id) and ".." not in course_id:
                    ids.add(course_id)
        return sorted(ids)

    def released_course_ids(self) -> list[str]:
        result = []
        for course_id in self.course_ids():
            if self._release_refs(course_id):
                result.append(course_id)
        return result

    def _release_refs(self, course_id: str) -> list[ArtifactRef]:
        prefix = self._course_prefix(course_id) + RELEASE_STAGE + "/"
        refs = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item.get("Key", "")
                if not key.endswith("/_manifest.json"):
                    continue
                manifest = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
                ref = ArtifactRef.model_validate_json(manifest)
                if ref.stage != RELEASE_STAGE or not ref.uri.startswith(f"s3://{self.bucket}/{prefix}"):
                    raise ValueError("release artifact is outside its course scope")
                refs.append(ref)
        return sorted(refs, key=lambda ref: ref.created_at, reverse=True)

    def load(self, course_id: str) -> ServedCourse:
        refs = self._release_refs(course_id)
        if not refs:
            raise KeyError(course_id)
        manifest_ref = refs[0]
        manifest = CourseServingManifest.model_validate(
            self.store.read_json(manifest_ref, RELEASE_FILE)
        )
        release = manifest.release
        if release.course_id != course_id:
            raise ValueError("release course_id mismatch")
        outline_ref = manifest.approved_outline_ref
        qa_ref = manifest.course_qa_ref
        for ref, stage in ((outline_ref, OUTLINE_STAGE), (qa_ref, QA_STAGE)):
            if ref.stage != stage or not ref.uri.startswith(f"s3://{self.bucket}/{course_id}/"):
                raise ValueError(f"release references an invalid {stage} artifact")
        envelope = self.store.read_json(outline_ref, "approved-outline.json")
        outline = CourseOutline.model_validate(envelope.get("outline"))
        OutlineApprovalProvenance.model_validate(envelope.get("approval_provenance"))
        if not envelope.get("approved_at"):
            raise ValueError("approved outline has no approval timestamp")
        if outline.course_id != course_id:
            raise ValueError("approved outline course_id mismatch")
        book_ids = {outline.spine_book_id, *outline.supplementary_book_ids}
        if set(release.book_rights) != book_ids or any(value != "cleared" for value in release.book_rights.values()):
            raise ValueError("release rights do not match the approved outline")
        lesson_ids = {
            lesson.lesson_id
            for module in outline.modules
            for lesson in module.lessons
        }
        if set(release.lesson_versions) != lesson_ids:
            raise ValueError("release lesson versions do not match the approved outline")
        qa = CourseQAResult.model_validate(self.store.read_json(qa_ref, "course-qa.json"))
        if qa.status != "final" or set(qa.expected_lesson_ids) != lesson_ids or set(qa.covered_lesson_ids) != lesson_ids:
            raise ValueError("course QA artifact is not final for every released lesson")
        audio_assets: dict[str, AudioAsset] = {}
        if set(manifest.audio_asset_refs) - lesson_ids:
            raise ValueError("release contains an audio asset for an unknown lesson")
        for lesson_id, ref in manifest.audio_asset_refs.items():
            if ref.stage == RELEASE_STAGE or not ref.uri.startswith(f"s3://{self.bucket}/{course_id}/"):
                raise ValueError("audio asset reference is outside the released course")
            asset = AudioAsset.model_validate(self.store.read_json(ref, "audio-asset.json"))
            if asset.lesson_id != lesson_id or not asset.acx_pass or asset.voice != release.voice:
                raise ValueError("audio asset is invalid, failed ACX validation, or uses a different released voice")
            parsed = urlparse(asset.mp3_uri)
            if parsed.scheme != "s3" or parsed.netloc != self.bucket:
                raise ValueError("audio URI is outside the artifact bucket")
            object_key = parsed.path.lstrip("/")
            if not object_key.startswith(f"{course_id}/"):
                raise ValueError("audio object is outside the released course")
            audio_assets[lesson_id] = asset
        return ServedCourse(manifest_ref, release, outline, envelope, qa, audio_assets)

    def mp3_location(self, course: ServedCourse, lesson_id: str) -> tuple[str, str]:
        asset = course.audio_assets.get(lesson_id)
        if asset is None:
            raise KeyError(lesson_id)
        parsed = urlparse(asset.mp3_uri)
        return parsed.netloc, parsed.path.lstrip("/")


def parse_single_byte_range(value: str, size: int) -> tuple[int, int]:
    """Parse one RFC 9110 byte range; reject malformed/multiple/unsatisfiable ranges."""
    if size <= 0 or not value.startswith("bytes=") or "," in value:
        raise ValueError("unsupported byte range")
    spec = value[6:].strip()
    if not re.fullmatch(r"(?:[0-9]+-[0-9]*|-[0-9]+)", spec):
        raise ValueError("unsupported byte range")
    first, last = spec.split("-", 1)
    try:
        if first == "":
            suffix = int(last)
            if suffix <= 0:
                raise ValueError
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(first)
            end = size - 1 if last == "" else int(last)
            if start < 0 or end < start or start >= size:
                raise ValueError
            end = min(end, size - 1)
    except ValueError as exc:
        raise ValueError("unsatisfiable byte range") from exc
    return start, end


def learner_question(question: Any) -> dict[str, Any]:
    """Return question fields safe to show before an answer is submitted."""
    value = question.model_dump(mode="json")
    for key in ("answer_index", "answer_text", "rationale", "validation"):
        value.pop(key, None)
    return value

