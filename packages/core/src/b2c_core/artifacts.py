"""S3-compatible artifact store (docs/02-CONTRACTS.md §1.1-1.2).

Layout: s3://{bucket}/{course_id}/{stage}/{key}/<files>, plus a `_manifest.json` holding the
ArtifactRef. An artifact is immutable once its manifest exists: `put` with an existing key returns
the stored ref and does not overwrite, which is what makes activities safe to re-run.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import BinaryIO
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from b2c_contracts import ArtifactRef

if TYPE_CHECKING:
    from .prov import ProvRecorder

MANIFEST = "_manifest.json"
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_KEY = re.compile(r"^[0-9a-f]{64}$")


def _check_segment(value: str, what: str) -> None:
    if not _SEGMENT.fullmatch(value) or ".." in value:
        raise ValueError(f"unsafe {what}: {value!r}")


def _check_name(name: str) -> None:
    parts = name.split("/")
    if name == MANIFEST or any(not _SEGMENT.fullmatch(p) or ".." in p for p in parts):
        raise ValueError(f"unsafe file name: {name!r}")


def _content_sha256_from_hashes(file_hashes: Mapping[str, str]) -> str:
    """Order-independent digest over (name, sha256(content)) pairs."""
    h = hashlib.sha256()
    for name in sorted(file_hashes):
        h.update(name.encode("utf-8") + b"\0" + bytes.fromhex(file_hashes[name]))
    return h.hexdigest()


def content_sha256(files: Mapping[str, bytes]) -> str:
    return _content_sha256_from_hashes({name: hashlib.sha256(data).hexdigest()
                                        for name, data in files.items()})


class ArtifactStore:
    def __init__(self, s3_client: Any, bucket: str = "artifacts",
                 recorder: ProvRecorder | None = None) -> None:
        self.s3 = s3_client
        self.bucket = bucket
        self.recorder = recorder

    @classmethod
    def from_settings(cls, settings: Any, recorder: ProvRecorder | None = None) -> ArtifactStore:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(s3={"addressing_style": "path"}),
        )
        return cls(client, settings.s3_bucket, recorder)

    # ---------------------------------------------------------------- paths
    def _prefix(self, course_id: str, stage: str, key: str) -> str:
        _check_segment(course_id, "course_id")
        _check_segment(stage, "stage")
        if not _KEY.fullmatch(key):
            raise ValueError(f"key must be a stage_key sha256 hex digest: {key!r}")
        return f"{course_id}/{stage}/{key}/"

    def _prefix_of(self, ref: ArtifactRef) -> str:
        return ref.uri.removeprefix(f"s3://{self.bucket}/")

    def _record(self, course_id: str, ref: ArtifactRef) -> None:
        # Idempotent (ON CONFLICT DO NOTHING): also registers artifacts written
        # earlier by a store that had no recorder.
        if self.recorder is not None:
            self.recorder.record_artifact(course_id, ref)

    # ---------------------------------------------------------------- api
    def ensure_bucket(self) -> None:
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except Exception:
            self.s3.create_bucket(Bucket=self.bucket)

    def exists(self, course_id: str, stage: str, key: str) -> ArtifactRef | None:
        prefix = self._prefix(course_id, stage, key)
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=prefix + MANIFEST)
        except self.s3.exceptions.NoSuchKey:
            return None
        return ArtifactRef.model_validate_json(obj["Body"].read())

    def put(self, course_id: str, stage: str, key: str, files: Mapping[str, bytes]) -> ArtifactRef:
        if not files:
            raise ValueError("artifact must contain at least one file")
        prefix = self._prefix(course_id, stage, key)
        for name in files:
            _check_name(name)
        existing = self.exists(course_id, stage, key)
        if existing is not None:
            self._record(course_id, existing)
            return existing
        for name, data in files.items():
            self.s3.put_object(Bucket=self.bucket, Key=prefix + name, Body=data)
        ref = ArtifactRef(
            stage=stage,
            key=key,
            uri=f"s3://{self.bucket}/{prefix}",
            sha256=content_sha256(files),
            created_at=datetime.now(timezone.utc),
        )
        # manifest last: its presence marks the artifact complete
        self.s3.put_object(Bucket=self.bucket, Key=prefix + MANIFEST,
                           Body=ref.model_dump_json().encode("utf-8"))
        if self.recorder is not None:
            self.recorder.record_artifact(course_id, ref)
        return ref

    def put_stream(self, course_id: str, stage: str, key: str,
                   files: Mapping[str, BinaryIO], file_hashes: Mapping[str, str]) -> ArtifactRef:
        """Upload file-like objects without buffering their contents in process memory.

        The caller supplies SHA-256 digests computed while streaming each file. The
        manifest is still written last, preserving the same immutable/idempotent contract
        as `put`.
        """
        if not files or set(files) != set(file_hashes):
            raise ValueError("stream files and file_hashes must have the same non-empty names")
        prefix = self._prefix(course_id, stage, key)
        for name in files:
            _check_name(name)
        for digest in file_hashes.values():
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("file hashes must be lowercase SHA-256 hex digests")
        existing = self.exists(course_id, stage, key)
        if existing is not None:
            return existing
        for name, stream in files.items():
            stream.seek(0)
            self.s3.upload_fileobj(stream, self.bucket, prefix + name)
        ref = ArtifactRef(
            stage=stage, key=key, uri=f"s3://{self.bucket}/{prefix}",
            sha256=_content_sha256_from_hashes(file_hashes),
            created_at=datetime.now(timezone.utc),
        )
        self.s3.put_object(Bucket=self.bucket, Key=prefix + MANIFEST,
                           Body=ref.model_dump_json().encode("utf-8"))
        if self.recorder is not None:
            self.recorder.record_artifact(course_id, ref)
        return ref

    def get_or_compute(self, course_id: str, stage: str, key: str,
                       compute: Callable[[], Mapping[str, bytes]]) -> tuple[ArtifactRef, bool]:
        """Return (ref, cache_hit). `compute` runs only when the artifact does not exist yet."""
        existing = self.exists(course_id, stage, key)
        if existing is not None:
            self._record(course_id, existing)
            return existing, True
        return self.put(course_id, stage, key, compute()), False

    def read(self, ref: ArtifactRef, name: str) -> bytes:
        _check_name(name)
        obj = self.s3.get_object(Bucket=self.bucket, Key=self._prefix_of(ref) + name)
        return obj["Body"].read()

    def download_to_path(self, ref: ArtifactRef, name: str, path: str) -> None:
        """Download an artifact without buffering the complete object in memory."""
        _check_name(name)
        self.s3.download_file(self.bucket, self._prefix_of(ref) + name, path)

    def read_json(self, ref: ArtifactRef, name: str) -> Any:
        return json.loads(self.read(ref, name))

    def list_files(self, ref: ArtifactRef) -> list[str]:
        prefix = self._prefix_of(ref)
        names: list[str] = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            names += [o["Key"][len(prefix):] for o in page.get("Contents", [])]
        return [n for n in names if n != MANIFEST]
