"""Ingest v2 activities and workflow (R3). Docling is faked; S3 is moto; Temporal is the
time-skipping test server (skipped when it cannot be downloaded)."""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from temporalio import activity
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

from b2c_contracts import BookUpload, IngestQualityReport, ParsedBook, ParsedBookV2, stage_key
from b2c_core import ArtifactStore
from b2c_orchestration import ingest_v2 as iv2
from b2c_orchestration.outline_v2 import BuildOutlineV2Input
from b2c_orchestration.ingest_v2 import (
    IngestPDFV2Workflow, IngestV2Activities, IngestV2Input, LayoutBatchInput, NormalizeV2Input,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))
from test_normalize_v2 import FILLER, Doc  # noqa: E402

PAGES = [FILLER + "The analyst must weigh the evidence about the",
         "movement and its demands. " + FILLER]


def make_pdf(pages: list[str]) -> bytes:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=400, height=600)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica"),
                                 NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 8 Tf 10 500 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    import io
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def fake_layout(drop_page: int | None = None):
    def run(path, first, last, *, ocr_language="en", num_threads=8):
        doc = Doc(first, last)
        for p in range(first, last + 1):
            if p != drop_page:
                doc.add("text", p, PAGES[p - 1], top=60, bottom=500)
        payload = doc.json()
        return json.dumps(payload, sort_keys=True).encode()
    return run


@pytest.fixture
def setup(monkeypatch):
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        store = ArtifactStore(s3, "artifacts")
        store.ensure_bucket()
        raw = make_pdf(PAGES)
        digest = hashlib.sha256(raw).hexdigest()
        upload = BookUpload(book_id="bk", role="spine", file_sha256=digest, mime="application/pdf",
                            title="T", rights_status="internal_test_only")
        source_ref = store.put("c1", "book-upload", stage_key("book-upload", "1", [digest], {}), {"source.pdf": raw})
        monkeypatch.setattr(iv2, "docling_versions", lambda: {"docling": "x", "docling-core": "y",
                                                               "docling-ibm-models": "z"})
        yield store, upload, source_ref


def run_activities(store, upload, source_ref, layout):
    acts = IngestV2Activities(store=store)
    env = ActivityEnvironment()
    inp = IngestV2Input("c1", upload.model_dump(mode="json"), source_ref.model_dump(mode="json"), [], page_batch=1)
    text = env.run(acts.text_layer, inp)
    refs = []
    for first in range(1, text["page_count"] + 1):
        out = env.run(acts.layout_batch, LayoutBatchInput("c1", inp.upload, inp.source_ref, first, first))
        refs.append(out["ref"])
    parsed = env.run(acts.normalize, NormalizeV2Input("c1", inp.upload, text["ref"], refs))
    return text, refs, parsed


def test_activities_store_intermediates_v2_v1_projection_and_quality(setup, monkeypatch):
    store, upload, source_ref = setup
    monkeypatch.setattr(iv2, "docling_layout_batch", fake_layout())
    text, refs, parsed = run_activities(store, upload, source_ref, None)
    assert text["page_count"] == 2 and len(refs) == 2
    from b2c_contracts import ArtifactRef
    ref = ArtifactRef.model_validate(parsed["ref"])
    assert sorted(store.list_files(ref)) == ["ingest-quality.json", "ingest-source.json", "parsed-book-v2.json",
                                             "parsed-book.json"]
    v2 = ParsedBookV2.model_validate_json(store.read(ref, "parsed-book-v2.json"))
    v1 = ParsedBook.model_validate_json(store.read(ref, "parsed-book.json"))
    report = IngestQualityReport.model_validate_json(store.read(ref, "ingest-quality.json"))
    joined = [b for b in v2.blocks if "about the movement" in b.text]
    assert len(joined) == 1 and [r.page_no for r in joined[0].refs] == [1, 2]
    assert v1.parser["format"] == "parsed-book/2->1"
    assert parsed["accepted"] is report.accepted
    assert report.parsed_sha256 == hashlib.sha256(store.read(ref, "parsed-book-v2.json")).hexdigest()
    # re-running is a cache hit with the same artifact
    _, _, again = run_activities(store, upload, source_ref, None)
    assert again["cache_hit"] is True and again["ref"]["key"] == parsed["ref"]["key"]


def test_intermediates_are_keyed_on_file_content_not_book_id(setup, monkeypatch):
    store, upload, source_ref = setup
    monkeypatch.setattr(iv2, "docling_layout_batch", fake_layout())
    text_a, refs_a, _ = run_activities(store, upload, source_ref, None)
    other = upload.model_copy(update={"book_id": "bk-other", "title": "Other"})
    text_b, refs_b, parsed_b = run_activities(store, other, source_ref, None)
    assert text_a["ref"]["key"] == text_b["ref"]["key"]
    assert [r["key"] for r in refs_a] == [r["key"] for r in refs_b]
    assert parsed_b["cache_hit"] is False  # the normalized book carries its book_id


def test_gate_failure_is_reported_not_passed(setup, monkeypatch):
    store, upload, source_ref = setup
    monkeypatch.setattr(iv2, "docling_layout_batch", fake_layout(drop_page=2))
    _, _, parsed = run_activities(store, upload, source_ref, None)
    assert parsed["accepted"] is False
    assert "uncovered_text_pages" in parsed["failed_checks"]


def test_arabic_books_are_refused_by_the_v2_layout(setup):
    store, upload, source_ref = setup
    from temporalio.exceptions import ApplicationError
    ar = upload.model_copy(update={"ocr_language": "ar"})
    with pytest.raises(ApplicationError):
        ActivityEnvironment().run(IngestV2Activities(store=store).layout_batch,
                                  LayoutBatchInput("c1", ar.model_dump(mode="json"),
                                                   source_ref.model_dump(mode="json"), 1, 1))


# ----------------------------------------------------------------------------- workflow
REF = {"stage": "s", "key": "k" * 64, "uri": "s3://artifacts/c1/s/k/", "sha256": "a" * 64,
       "created_at": "2026-10-25T00:00:00Z"}


@pytest.fixture
async def env():
    from temporalio.testing import WorkflowEnvironment
    try:
        environment = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # pragma: no cover - depends on the test-server download
        pytest.skip(f"Temporal test server unavailable: {exc}")
    try:
        yield environment
    finally:
        await environment.shutdown()


def fakes(accepted: bool, calls: list):
    @activity.defn(name="ingest_v2_text_layer")
    def text_layer(inp: IngestV2Input) -> dict:
        calls.append("text")
        return {"ref": REF, "cache_hit": False, "page_count": 5}

    @activity.defn(name="ingest_v2_layout_batch")
    def layout_batch(inp: LayoutBatchInput) -> dict:
        calls.append(("layout", inp.first_page, inp.last_page))
        return {"ref": REF, "cache_hit": False, "name": "n"}

    @activity.defn(name="ingest_v2_normalize")
    def normalize(inp: NormalizeV2Input) -> dict:
        calls.append(("normalize", len(inp.batch_refs)))
        return {"ref": REF, "cache_hit": False, "accepted": accepted, "failed_checks": [] if accepted else ["x"]}

    @activity.defn(name="outline_from_parsed_book")
    def outline(inp) -> dict:
        calls.append("outline-v1")
        return {"ref": REF, "module_count": 1, "lesson_count": 1}

    @activity.defn(name="outline_v2_from_parsed_book")
    def outline_v2(inp: BuildOutlineV2Input) -> dict:
        calls.append("outline")
        assert inp.parsed_ref == REF and inp.course_id == "c1"
        return {"ref": REF, "format": "course-outline/2", "module_count": 1, "lesson_count": 1,
                "stage_output_status": "candidate"}

    return [text_layer, layout_batch, normalize, outline, outline_v2]


@pytest.mark.parametrize("accepted", [True, False])
async def test_workflow_batches_pages_and_stops_on_a_failed_gate(env, accepted):
    from concurrent.futures import ThreadPoolExecutor
    from b2c_orchestration.queues import HEAVY
    calls: list = []
    upload = {"book_id": "bk", "role": "spine", "file_sha256": "0" * 64, "mime": "application/pdf",
              "title": "T", "rights_status": "internal_test_only", "ocr_language": "en"}
    async with Worker(env.client, task_queue=HEAVY, workflows=[IngestPDFV2Workflow],
                      activities=fakes(accepted, calls), activity_executor=ThreadPoolExecutor(4)):
        result = await env.client.execute_workflow(
            IngestPDFV2Workflow.run, IngestV2Input("c1", upload, REF, [], page_batch=2),
            id=f"wf-{uuid.uuid4()}", task_queue=HEAVY)
    assert calls[:4] == ["text", ("layout", 1, 2), ("layout", 3, 4), ("layout", 5, 5)]
    assert calls[4] == ("normalize", 3)
    if accepted:
        # R4: new histories build the outline v2 candidate, never the v1 outline
        assert result["status"] == "accepted" and calls[-1] == "outline" and "outline-v1" not in calls
        assert result["outline"]["format"] == "course-outline/2"
    else:
        assert result["status"] == "needs_review" and result["outline"] is None and "outline" not in calls
