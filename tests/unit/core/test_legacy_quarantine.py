"""Legacy quarantine (R0 item 1 / R1): every pre-gate outline/QA/translation/MP3 artifact is
registered as ``quarantined`` with reason "legacy pre-gate"; nothing is deleted; dry run writes nothing."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from b2c_core import ArtifactStore
from b2c_core.legacy_quarantine import (LEGACY_REASON, LEGACY_STAGES, ReadOnlyS3, quarantine_legacy,
                                        scan_bucket)
from b2c_core.stage_registry import StageOutputRegistry
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


@pytest.fixture
def bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        store = ArtifactStore(s3, bucket="artifacts")
        store.ensure_bucket()
        store.put("b2c-demo", "translate-content", "1" * 64, {"translation.json": b"{}"})
        store.put("b2c-demo", "generate-course-qa", "2" * 64, {"course-qa.json": b"{}"})
        store.put("b2c-demo", "generate-mp3-track", "3" * 64, {"track.mp3": b"ID3"})
        store.put("b2c-demo", "approve-outline", "4" * 64, {"outline.json": b"{}"})
        store.put("b2c-demo", "ingest-parse-pdf", "5" * 64, {"parsed-book.json": b"{}"})  # not a legacy content stage
        store.put("crs2", "translate-book", "6" * 64, {"translation.json": b"{}"})
        s3.put_object(Bucket="artifacts", Key="_system/translation-checkpoints/x.json", Body=b"{}")
        s3.put_object(Bucket="artifacts", Key="b2c-demo/book-progress/y.json", Body=b"{}")
        # a manifest whose content disagrees with its path is reported, never registered
        bad = json.loads(s3.get_object(Bucket="artifacts",
                                       Key=f"b2c-demo/approve-outline/{'4' * 64}/_manifest.json")["Body"].read())
        s3.put_object(Bucket="artifacts", Key=f"b2c-demo/approve-outline/{'7' * 64}/_manifest.json",
                      Body=json.dumps(bad).encode())
        yield s3


def registry(db):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return StageOutputRegistry("postgresql://unused", connect=db.connect, clock=lambda: next(clock))


def test_read_only_client_refuses_writes(bucket):
    ro = ReadOnlyS3(bucket)
    with pytest.raises(PermissionError):
        ro.put_object(Bucket="artifacts", Key="x", Body=b"")
    with pytest.raises(PermissionError):
        ro.delete_object(Bucket="artifacts", Key="x")
    assert ro.get_object(Bucket="artifacts", Key="_system/translation-checkpoints/x.json")["Body"].read() == b"{}"


def test_scan_finds_legacy_manifests_and_counts_everything_else(bucket):
    scan = scan_bucket(ReadOnlyS3(bucket), "artifacts")
    stages = sorted(ref.stage for _, ref in scan.legacy)
    assert stages == ["approve-outline", "generate-course-qa", "generate-mp3-track", "translate-book",
                      "translate-content"]
    assert scan.manifests_by_stage["ingest-parse-pdf"] == 1
    assert scan.invalid == [f"b2c-demo/approve-outline/{'7' * 64}/_manifest.json"]
    assert scan.non_manifest_prefixes["_system"] == 1
    assert scan.non_manifest_prefixes["book-progress"] == 1
    assert scan.courses == {"b2c-demo", "crs2"}
    assert {"translate-content", "generate-course-qa", "generate-mp3-track", "approve-outline",
            "build-outline", "translate-book"} <= LEGACY_STAGES


def test_dry_run_writes_nothing(bucket):
    db = FakeRegistryDB()
    scan = scan_bucket(ReadOnlyS3(bucket), "artifacts")
    report = quarantine_legacy(scan, None, apply=False)
    assert report["would_register"] == 5 and report["registered"] == 0
    assert db.statements == []


def test_apply_registers_quarantined_once_and_never_deletes(bucket):
    db = FakeRegistryDB()
    reg = registry(db)
    scan = scan_bucket(ReadOnlyS3(bucket), "artifacts")
    first = quarantine_legacy(scan, reg, apply=True)
    assert first["registered"] == 5 and first["already_registered"] == 0
    rec = reg.get("b2c-demo", "translate-content", "1" * 64)
    assert rec.status == "quarantined" and rec.status_reason == LEGACY_REASON == "legacy pre-gate"
    assert rec.code_revision == "unknown" and rec.required_gates == []
    second = quarantine_legacy(scan_bucket(ReadOnlyS3(bucket), "artifacts"), reg, apply=True)
    assert second["registered"] == 0 and second["already_registered"] == 5
    assert len(db.events) == 5
    # objects are untouched
    assert len(bucket.list_objects_v2(Bucket="artifacts")["Contents"]) == 15


def test_apply_without_registry_is_refused(bucket):
    with pytest.raises(ValueError):
        quarantine_legacy(scan_bucket(ReadOnlyS3(bucket), "artifacts"), None, apply=True)
