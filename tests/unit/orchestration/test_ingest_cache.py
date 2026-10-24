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
