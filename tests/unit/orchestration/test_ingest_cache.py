import hashlib

import boto3
from moto import mock_aws
from temporalio.testing import ActivityEnvironment

from b2c_contracts import BookUpload, ParsedBook, stage_key
from b2c_core import ArtifactStore
from b2c_orchestration.ingest import IngestPDFActivities, IngestPDFInput


def _parsed(upload):
    return ParsedBook(
        book_id=upload.book_id, title=upload.title, role=upload.role, page_count=1,
        toc_source="headings", blocks=[],
        parser={"name": "fake-parser", "version": "1", "options_hash": "a" * 64},
    )


def test_parse_cache_is_checked_before_reading_source_or_running_parser(monkeypatch):
    raw = b"%PDF-1.7 test source"
    digest = hashlib.sha256(raw).hexdigest()
    upload = BookUpload(
        book_id="book-1", role="supplementary", file_sha256=digest,
        mime="application/pdf", title="Test book", rights_status="internal_test_only",
    )
    calls = []
    monkeypatch.setattr("b2c_orchestration.ingest.parse_pdf_book",
                        lambda upload, path: (calls.append(path) or _parsed(upload)))
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        store = ArtifactStore(s3, "artifacts")
        store.ensure_bucket()
        source_key = stage_key("book-upload", "1", [digest], {})
        source_ref = store.put("course-1", "book-upload", source_key, {"source.pdf": raw})
        downloaded_stages = []
        original_download = store.download_to_path

        def track_download(ref, name, path):
            downloaded_stages.append(ref.stage)
            return original_download(ref, name, path)

        store.download_to_path = track_download
        acts = IngestPDFActivities(store=store)
        inp = IngestPDFInput("course-1", upload.model_dump(mode="json"),
                             source_ref.model_dump(mode="json"), [])
        first = ActivityEnvironment().run(acts.parse_pdf, inp)
        second = ActivityEnvironment().run(acts.parse_pdf, inp)
        assert first["cache_hit"] is False
        assert second["cache_hit"] is True
        assert first["ref"]["key"] == second["ref"]["key"]
        assert len(calls) == 1
        assert downloaded_stages.count("book-upload") == 1

        forced = ActivityEnvironment().run(
            acts.parse_pdf, IngestPDFInput("course-1", upload.model_dump(mode="json"),
                                           source_ref.model_dump(mode="json"), [],
                                           cache_bust="explicit-rerun-1")
        )
        assert forced["cache_hit"] is False
        assert forced["ref"]["key"] != first["ref"]["key"]
        assert len(calls) == 2
        assert downloaded_stages.count("book-upload") == 2

def test_selected_surya_engine_dispatches_scans_to_local_client(monkeypatch):
    from b2c_ingest import OCRRequired

    raw = b"%PDF-1.7 test scanned source"
    digest = hashlib.sha256(raw).hexdigest()
    upload = BookUpload(
        book_id="book-surya", role="spine", file_sha256=digest,
        mime="application/pdf", title="Scanned test book", rights_status="internal_test_only",
    )
    surya_calls = []
    monkeypatch.setenv("B2C_OCR_ENGINE", "surya")
    monkeypatch.setenv("B2C_SURYA_INFERENCE_URL", "http://surya-ocr:8080/v1")
    monkeypatch.setenv("B2C_SURYA_MAX_TOKENS", "16384")
    monkeypatch.setattr(
        "b2c_orchestration.ingest.parse_pdf_book",
        lambda *args, **kwargs: (_ for _ in ()).throw(OCRRequired("scan")),
    )
    monkeypatch.setattr(
        "b2c_orchestration.ingest.parse_pdf_with_surya",
        lambda upload, path, **kwargs: (surya_calls.append(kwargs) or _parsed(upload)),
    )

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        store = ArtifactStore(s3, "artifacts")
        store.ensure_bucket()
        source_key = stage_key("book-upload", "1", [digest], {})
        source_ref = store.put("course-surya", "book-upload", source_key, {"source.pdf": raw})
        inp = IngestPDFInput("course-surya", upload.model_dump(mode="json"),
                             source_ref.model_dump(mode="json"), [])
        result = ActivityEnvironment().run(IngestPDFActivities(store=store).parse_pdf, inp)

    assert result["cache_hit"] is False
    assert surya_calls == [{
        "base_url": "http://surya-ocr:8080/v1",
        "model": "/data/models/phase2-ocr-candidates/surya-ocr-2-gguf/surya-2.gguf",
        "timeout_seconds": 3600,
        "max_tokens": 16384,
    }]


class _FakeProv:
    def __init__(self):
        self.activities = []

    def record_activity(self, **kwargs):
        self.activities.append(kwargs)
        return kwargs["activity_id"]


class _OCRGateway:
    provider_name = "openrouter"

    def __init__(self, model_id):
        self.model_id = model_id


def _run_parse(monkeypatch, *, ocr_engine=None, gateway=None, prov=None, gemini_file=None, runs=1):
    raw = b"%PDF-1.7 identity source"
    digest = hashlib.sha256(raw).hexdigest()
    upload = BookUpload(book_id="book-id", role="supplementary", file_sha256=digest,
                        mime="application/pdf", title="T", rights_status="internal_test_only")
    for name in ("parse_pdf_book", "parse_pdf_with_openrouter", "parse_pdf_with_docling"):
        monkeypatch.setattr(f"b2c_orchestration.ingest.{name}",
                            lambda upload, path, **kwargs: _parsed(upload))
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        store = ArtifactStore(s3, "artifacts")
        store.ensure_bucket()
        source_ref = store.put("course-id", "book-upload", stage_key("book-upload", "1", [digest], {}),
                               {"source.pdf": raw})
        acts = IngestPDFActivities(store=store, prov=prov, gateway=gateway, gemini_api_key_file=gemini_file)
        inp = IngestPDFInput("course-id", upload.model_dump(mode="json"),
                             source_ref.model_dump(mode="json"), [], ocr_engine=ocr_engine)
        results = [ActivityEnvironment().run(acts.parse_pdf, inp) for _ in range(runs)]
    return results, upload, digest


def test_openrouter_parse_key_tracks_ocr_model_and_token_ceiling(monkeypatch):
    monkeypatch.setenv("B2C_OPENROUTER_OCR_MAX_TOKENS", "12000")
    (a,), _, _ = _run_parse(monkeypatch, ocr_engine="openrouter",
                            gateway=_OCRGateway("qwen/qwen3.8-27b:free"))
    (b,), _, _ = _run_parse(monkeypatch, ocr_engine="openrouter",
                            gateway=_OCRGateway("deepseek/deepseek-v4-flash-vision-exp"))
    monkeypatch.setenv("B2C_OPENROUTER_OCR_MAX_TOKENS", "8000")
    (c,), _, _ = _run_parse(monkeypatch, ocr_engine="openrouter",
                            gateway=_OCRGateway("qwen/qwen3.8-27b:free"))
    assert len({a["ref"]["key"], b["ref"]["key"], c["ref"]["key"]}) == 3


def test_gemini_table_model_is_part_of_parse_key(monkeypatch, tmp_path):
    import inspect
    from b2c_ingest import gemini_tables
    secret = tmp_path / "gemini"
    secret.write_text("k")
    (with_gemini,), _, _ = _run_parse(monkeypatch, gemini_file=str(secret))
    monkeypatch.setattr(gemini_tables, "transcribe_table_png",
                        lambda image_png, api_key, *, model="gemini-next", timeout_s=45.0: None)
    (other_model,), _, _ = _run_parse(monkeypatch, gemini_file=str(secret))
    assert inspect.signature(gemini_tables.transcribe_table_png).parameters["model"].default == "gemini-next"
    assert with_gemini["ref"]["key"] != other_model["ref"]["key"]


def test_local_parse_key_is_unchanged_for_existing_rapidocr_cache(monkeypatch):
    monkeypatch.delenv("B2C_OCR_ENGINE", raising=False)
    (result,), upload, digest = _run_parse(monkeypatch)
    legacy_config = {"pipeline": "pypdf-text-v1+rapidocr-ocr-v4", "ocr_engine": "rapidocr",
                     "surya_model": None, "surya_max_tokens": None, "surya_timeout_seconds": None,
                     "table_backend": "docling-tableformer", "upload": upload.model_dump(mode="json"),
                     "cache_bust": None}
    assert result["ref"]["key"] == stage_key("ingest-parse-pdf", "6", [digest], legacy_config)


def test_parse_provenance_agent_follows_requested_engine_not_env(monkeypatch):
    monkeypatch.setenv("B2C_OCR_ENGINE", "rapidocr")
    prov = _FakeProv()
    _run_parse(monkeypatch, ocr_engine="openrouter", gateway=_OCRGateway("qwen/qwen3.8-27b:free"), prov=prov)
    assert prov.activities[0]["agent"] == "qwen/qwen3.8-27b:free"


def test_outline_provenance_stage_version_matches_cache_key_version():
    from datetime import UTC, datetime
    from b2c_contracts import ArtifactRef, CourseOutline
    from b2c_orchestration.ingest import OUTLINE_STAGE_VERSION, OutlineActivities
    outline = CourseOutline.model_validate({
        "course_id": "c", "spine_book_id": "b", "supplementary_book_ids": [], "requires_review": True,
        "modules": [{"module_id": "m1", "title": "M", "order": 1, "lessons": [{
            "lesson_id": "m1.l1", "title": "L", "order": 1, "provenance": "headings",
            "spine_span": {"start_block": "b:1", "end_block": "b:1", "pages": [1, 1]}}]}]})
    ref = ArtifactRef(stage="build-outline", key="a" * 64, uri="s3://artifacts/c/build-outline/x/",
                      sha256="b" * 64, created_at=datetime.now(UTC))
    prov = _FakeProv()
    OutlineActivities(store=None, prov=prov)._record(ref, "s3://artifacts/c/parsed/", outline)
    assert OUTLINE_STAGE_VERSION == "4"
    assert prov.activities[0]["stage_version"] == OUTLINE_STAGE_VERSION


def test_parse_provenance_is_recorded_on_cache_hit(monkeypatch):
    prov = _FakeProv()
    results, _, _ = _run_parse(monkeypatch, prov=prov, runs=2)
    assert [r["cache_hit"] for r in results] == [False, True]
    assert len(prov.activities) == 2 and prov.activities[0] == prov.activities[1]
    record = prov.activities[0]
    out = record["generated"][0]
    assert record["used"] and record["derived"] == [(out, record["used"][0])]
