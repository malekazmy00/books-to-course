"""Manual translation overrides are validated against the requested target language."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO

import pytest

from b2c_contracts import ArtifactRef, Block, PageRef, ParsedBook
from b2c_orchestration.translation import TranslateContentInput, translation_manual_overrides

SOURCE = ("Probability measures how likely an event is to happen in a repeated trial, and the "
          "measure always lies between zero and one. Independent events do not change each "
          "other's probability, which lets us multiply their individual chances together safely.")


class _S3:
    def __init__(self, objects):
        self.objects = objects

    def get_paginator(self, name):
        objects = self.objects

        class Paginator:
            def paginate(self, *, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(objects) if k.startswith(Prefix)]}
        return Paginator()

    def get_object(self, *, Bucket, Key):
        return {"Body": BytesIO(self.objects[Key])}


class _Store:
    bucket = "artifacts"

    def __init__(self, parsed, objects):
        self.parsed = parsed
        self.s3 = _S3(objects)

    def read(self, ref, name):
        assert name == "parsed-book.json"
        return self.parsed.model_dump_json().encode()


def test_override_left_in_english_is_rejected_for_arabic_target():
    parsed = ParsedBook(book_id="b", title="T", role="spine", page_count=1, toc_source="headings",
        parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id="b:1", type="paragraph", text=SOURCE,
                  refs=[PageRef(book_id="b", page_no=1)], section_path=[], ocr=False)])
    ref = ArtifactRef(stage="ingest-parse-pdf", key="a" * 64, uri="s3://artifacts/c/ingest-parse-pdf/x/",
                      sha256="b" * 64, created_at=datetime.now(UTC))
    workflow_id = "b2c-translate-" + "0" * 64
    override = {"workflow_id": workflow_id, "source_sha256": ref.sha256, "source_language": "en",
                "target_language": "ar", "segment_id": "b:1", "text": SOURCE,
                "created_at": "2026-10-01T00:00:00Z", "revision_id": "r1"}
    store = _Store(parsed, {f"_system/translation-overrides/{workflow_id}/r1.json": json.dumps(override).encode()})
    inp = TranslateContentInput(course_id="c", source_artifact_ref=ref.model_dump(mode="json"),
                                source_kind="book", source_language="en", target_language="ar")
    with pytest.raises(ValueError):
        translation_manual_overrides(store, inp, workflow_id)
