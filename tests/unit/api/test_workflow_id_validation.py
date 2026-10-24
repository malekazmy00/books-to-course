"""Strict workflow-ID validation and course-scoped recovery scans."""
from __future__ import annotations

import json

import boto3
import httpx
import pytest
from moto import mock_aws

from b2c_api.app import create_app
from b2c_api.deps import is_workflow_id
from b2c_core import ArtifactStore, Settings


@pytest.mark.parametrize("kind,value,ok", [
    ("ingest", "b2c-ingest-" + "a" * 64, True),
    ("ingest", "b2c-ingest-" + "A" * 64, False),
    ("ingest", "b2c-ingest-" + "a" * 63 + "/", False),
    ("index", "b2c-index-" + "0" * 64, True),
    ("index", "b2c-index-" + "0" * 65, False),
    ("translate", "b2c-translate-" + "f" * 64, True),
    ("translate", "b2c-translate-" + "g" * 64, False),
    ("mp3", "b2c-mp3-" + "1" * 64, True),
    ("qa", "b2c-qa-" + "1" * 64, True),
    ("qa", "b2c-qa-" + "1" * 64 + "\n", False),
    ("retranslate", "b2c-retranslate-" + "1" * 32, True),
    ("retranslate", "b2c-retranslate-" + "1" * 64, False),
])
def test_workflow_id_patterns_are_exact(kind, value, ok):
    assert is_workflow_id(kind, value) is ok


class ExplodingStore:
    bucket = "artifacts"

    def __init__(self):
        self.s3 = self

    def __getattr__(self, name):
        raise AssertionError(f"store must not be touched for a malformed id ({name})")


class Temporal:
    def get_workflow_handle(self, _):
        raise AssertionError("Temporal must not be queried for a malformed id")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/v1/ingestions/b2c-ingest-" + "Z" * 64,
    "/v1/indexings/b2c-index-" + "Z" * 64,
    "/v1/translations/b2c-translate-" + "Z" * 64,
    "/v1/audio/b2c-mp3-" + "Z" * 64,
    "/v1/course-qa/b2c-qa-" + "Z" * 64,
])
async def test_malformed_ids_are_rejected_before_any_lookup(path):
    async def connect(_):
        return Temporal()

    app = create_app(settings=Settings(), artifact_store=ExplodingStore(), temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == 404


def test_qa_owner_lookup_is_scoped_to_the_given_course():
    from b2c_api.qa_recovery import saved_qa_owner
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"))
        store.ensure_bucket()
        wf = "b2c-qa-" + "a" * 64
        listed = []
        original = store.s3.list_objects_v2

        def spy(**kwargs):
            listed.append(kwargs.get("Prefix", ""))
            return original(**kwargs)

        store.s3.list_objects_v2 = spy
        for cid, bid in (("course", "one"), ("other", "two")):
            record = {"course_id": cid, "book_id": bid,
                      "stages": {"qa": {"workflow_id": wf, "artifact_ref": {"key": "saved"}}}}
            store.s3.put_object(Bucket=store.bucket, Key=f"{cid}/book-progress/{bid}.json",
                                Body=json.dumps(record).encode())
        assert saved_qa_owner(store, wf, course_id="course") == ("course", "one")
        assert listed and all(prefix == "course/book-progress/" for prefix in listed)


@pytest.mark.asyncio
async def test_index_recovery_scan_is_scoped_to_the_given_course():
    from b2c_api.ingestion_recovery import associated_record

    prefixes = []

    class Paginator:
        def paginate(self, **kwargs):
            prefixes.append(kwargs["Prefix"])
            return []

    class Store:
        bucket = "artifacts"

        def __init__(self):
            self.s3 = self

        def get_paginator(self, _):
            return Paginator()

    assert await associated_record(Store(), "b2c-index-" + "a" * 64, "index", "course") is None
    assert prefixes == ["course/book-progress/"]
