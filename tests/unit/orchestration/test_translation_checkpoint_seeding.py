"""Seeding from earlier translation checkpoints: skip bad content, never storage errors."""
from __future__ import annotations

import json
from io import BytesIO

import pytest

from b2c_orchestration.translation import seed_translation_checkpoints
from b2c_translation import SourceSegment

COMPATIBLE = {"source_sha256": "s" * 64, "target_language": "ar"}
SOURCES = {"u1": SourceSegment(segment_id="u1", text="Short source."),
           "u2": SourceSegment(segment_id="u2", text="Another source.")}


def _checkpoint(segment_id, text):
    return json.dumps({**COMPATIBLE, "batch_index": 1, "completed_at": "2026-10-01T00:00:00+00:00",
                       "segments": [{"segment_id": segment_id, "text": text}]}).encode()


class _S3:
    def __init__(self, objects, *, broken_pagination=False, failing_key=None):
        self.objects = objects
        self.broken_pagination = broken_pagination
        self.failing_key = failing_key
        self.list_calls = 0

    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        self.list_calls += 1
        assert self.list_calls < 10, "listing loops forever"
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if self.broken_pagination:
            return {"Contents": [{"Key": k} for k in keys], "IsTruncated": True}
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def get_object(self, *, Bucket, Key):
        if Key == self.failing_key:
            raise ConnectionError("S3 unavailable")
        return {"Body": BytesIO(self.objects[Key])}


class _Store:
    bucket = "artifacts"

    def __init__(self, s3):
        self.s3 = s3


PREFIX = "_system/translation-checkpoints/wf/fp/"


def test_malformed_checkpoints_are_skipped_but_valid_ones_seed():
    store = _Store(_S3({PREFIX + "batch-000001.json": _checkpoint("u1", "نص عربي قصير."),
                        PREFIX + "batch-000002.json": b"{not json",
                        PREFIX + "batch-000003.json": b"[1, 2]"}))
    seeded = seed_translation_checkpoints(store, ["wf"], COMPATIBLE, SOURCES)
    assert set(seeded) == {"u1"}


def test_storage_errors_propagate_instead_of_dropping_checkpoints():
    store = _Store(_S3({PREFIX + "batch-000001.json": _checkpoint("u1", "نص عربي قصير.")},
                       failing_key=PREFIX + "batch-000001.json"))
    with pytest.raises(ConnectionError):
        seed_translation_checkpoints(store, ["wf"], COMPATIBLE, SOURCES)


def test_truncated_listing_without_token_fails_instead_of_looping():
    store = _Store(_S3({PREFIX + "batch-000001.json": _checkpoint("u1", "نص عربي قصير.")},
                       broken_pagination=True))
    with pytest.raises(ValueError, match="pagination"):
        seed_translation_checkpoints(store, ["wf"], COMPATIBLE, SOURCES)
