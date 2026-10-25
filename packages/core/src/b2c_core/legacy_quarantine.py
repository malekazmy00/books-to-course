"""Quarantine of legacy stage outputs (R0 item 1; audit 08 §6.4 step 0).

Every outline / QA / translation / MP3 artifact that exists in the bucket was produced before
any acceptance gate existed. This module finds them by their manifests (read-only S3: list and
get only) and registers each as ``quarantined`` with reason ``"legacy pre-gate"`` in the stage
output registry. It never writes or deletes objects. An artifact that already has a registry
row (e.g. a new candidate) is left as it is, so the operation is idempotent.

A quarantined output is not served and not consumed; it can only be re-gated
(``quarantined -> candidate``) by a stage that applies its current gates.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from b2c_contracts import ArtifactRef, StageOutputRecord

from .artifacts import MANIFEST
from .stage_registry import StageOutputRegistry

LEGACY_REASON = "legacy pre-gate"
QUARANTINE_ACTOR = "script:quarantine-legacy-artifacts"
# Learner-facing content stages (outline, QA, translation, audio, release) of the old and the
# rebuilt paths. Assemblies made by translate-book before the registry was deployed are legacy too.
LEGACY_STAGES = frozenset({
    "build-outline", "approve-outline", "generate-course-qa", "translate-content", "translate-book",
    "generate-mp3-track", "generate-mp3", "course-release",
})
_MANIFEST_KEY = re.compile(rf"^([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)/([0-9a-f]{{64}})/{MANIFEST}$")
_READ_METHODS = frozenset({"get_object", "list_objects_v2", "get_paginator", "head_object", "head_bucket"})


class ReadOnlyS3:
    """An S3 client proxy that only lets read calls through."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        if name in _READ_METHODS or name == "exceptions":
            return getattr(self._client, name)
        raise PermissionError(f"read-only S3 client: {name} is not allowed")


@dataclass
class LegacyScan:
    total_objects: int = 0
    manifests_by_stage: Counter = field(default_factory=Counter)
    legacy: list[tuple[str, ArtifactRef]] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    non_manifest_prefixes: Counter = field(default_factory=Counter)  # objects outside any manifest-backed artifact
    courses: set[str] = field(default_factory=set)

    def summary(self) -> dict[str, Any]:
        legacy_by_stage = Counter(ref.stage for _, ref in self.legacy)
        legacy_by_course = Counter(course for course, _ in self.legacy)
        return {"total_objects": self.total_objects, "courses": len(self.courses),
                "manifests_by_stage": dict(sorted(self.manifests_by_stage.items())),
                "legacy_by_stage": dict(sorted(legacy_by_stage.items())),
                "legacy_by_course": dict(sorted(legacy_by_course.items())),
                "legacy_total": len(self.legacy), "invalid_manifests": len(self.invalid),
                "non_manifest_objects_by_prefix": dict(sorted(self.non_manifest_prefixes.items()))}


def _objects(s3: Any, bucket: str) -> Iterable[str]:
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            yield obj["Key"]


def scan_bucket(s3: Any, bucket: str, *, stages: frozenset[str] = LEGACY_STAGES) -> LegacyScan:
    """List the bucket and read the manifests of legacy stages. Read-only."""
    scan = LegacyScan()
    keys = list(_objects(s3, bucket))
    scan.total_objects = len(keys)
    artifact_prefixes: set[str] = set()
    for key in keys:
        match = _MANIFEST_KEY.fullmatch(key)
        if match is None:
            continue
        course, stage, artifact_key = match.groups()
        artifact_prefixes.add(f"{course}/{stage}/{artifact_key}/")
        scan.manifests_by_stage[stage] += 1
        scan.courses.add(course)
        if stage not in stages:
            continue
        try:
            ref = ArtifactRef.model_validate_json(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        except ValueError:
            scan.invalid.append(key)
            continue
        if (ref.stage, ref.key, ref.uri) != (stage, artifact_key, f"s3://{bucket}/{course}/{stage}/{artifact_key}/"):
            scan.invalid.append(key)
            continue
        scan.legacy.append((course, ref))
    for key in keys:
        parts = key.split("/")
        if len(parts) >= 4 and "/".join(parts[:3]) + "/" in artifact_prefixes:
            continue
        # classify: "_system/..." -> "_system"; "{course}/{area}/..." -> area
        scan.non_manifest_prefixes[parts[0] if parts[0].startswith("_") or len(parts) < 2 else parts[1]] += 1
    return scan


def quarantine_legacy(scan: LegacyScan, registry: StageOutputRegistry | None, *, apply: bool,
                      actor: str = QUARANTINE_ACTOR) -> dict[str, Any]:
    """Register every legacy artifact as quarantined. With ``apply=False`` nothing is written."""
    report: dict[str, Any] = {**scan.summary(), "mode": "apply" if apply else "dry-run", "would_register": 0,
                              "registered": 0, "already_registered": 0}
    if not apply:
        report["would_register"] = len(scan.legacy)
        return report
    if registry is None:
        raise ValueError("apply needs a registry")
    now = datetime.now(timezone.utc)
    for course, ref in scan.legacy:
        record = StageOutputRecord(course_id=course, artifact=ref, code_revision="unknown", status="quarantined",
                                   status_reason=LEGACY_REASON, status_changed_at=now, status_changed_by=actor,
                                   config={"quarantined_by": actor})
        if registry.register(record):
            report["registered"] += 1
        else:
            report["already_registered"] += 1
    return report


__all__ = ["LEGACY_REASON", "LEGACY_STAGES", "LegacyScan", "QUARANTINE_ACTOR", "ReadOnlyS3", "quarantine_legacy",
           "scan_bucket"]
