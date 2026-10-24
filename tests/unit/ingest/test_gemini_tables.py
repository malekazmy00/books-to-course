from __future__ import annotations

import json
import urllib.parse

import pytest

from b2c_ingest import gemini_tables
from b2c_ingest.gemini_tables import GeminiTableError, transcribe_table_png


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_transcribe_table_png_returns_markdown_and_usage(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        seen["payload"] = json.loads(request.data)
        seen["timeout"] = timeout
        return _Response({
            "candidates": [{"content": {"parts": [{"text": "| A | B |\\n|---|---|\\n| 1 | 2 |"}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8},
        })

    monkeypatch.setattr(gemini_tables.urllib.request, "urlopen", fake_urlopen)
    text, usage = transcribe_table_png(b"png-bytes", "test-secret")

    assert "| A | B |" in text
    assert usage == {"prompt_tokens": 10, "output_tokens": 8}
    # The key goes in the x-goog-api-key header, never in the URL.
    assert urllib.parse.urlparse(seen["url"]).query == ""
    assert "test-secret" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "test-secret"
    assert seen["payload"]["generationConfig"] == {"temperature": 0, "maxOutputTokens": 4096}
    assert seen["payload"]["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"
    assert seen["timeout"] == 45.0


def test_http_error_does_not_echo_key_or_upstream_body(monkeypatch):
    def fake_urlopen(_request, timeout):
        raise gemini_tables.urllib.error.HTTPError(
            "https://example.test/?key=test-secret", 429, "limited", {}, None
        )

    monkeypatch.setattr(gemini_tables.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(GeminiTableError) as exc:
        transcribe_table_png(b"png-bytes", "test-secret")
    assert "429" in str(exc.value)
    assert "test-secret" not in str(exc.value)


def test_empty_key_is_rejected_before_network(monkeypatch):
    monkeypatch.setattr(
        gemini_tables.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("network must not be called"),
    )
    with pytest.raises(GeminiTableError, match="API key is empty"):
        transcribe_table_png(b"png-bytes", "  ")

def test_table_backend_identity_reports_per_table_outcome():
    from b2c_ingest.docling_pdf import _table_backend_identity

    assert _table_backend_identity([(1, 0, "docling-tableformer")], False) == (
        "docling-tableformer", {"table_backend": "docling-tableformer"})
    summary, config = _table_backend_identity(
        [(1, 2, "gemini-3.5-flash-lite"), (3, 0, "docling-tableformer")], True)
    assert summary == "mixed:docling-tableformer=1,gemini-3.5-flash-lite=1"
    assert config["table_backend_outcomes"] == ["1:2:gemini-3.5-flash-lite", "3:0:docling-tableformer"]
    # Every Gemini request fell back: never claim Gemini produced the tables.
    summary, config = _table_backend_identity([(2, 1, "docling-tableformer")], True)
    assert summary == config["table_backend"] == "docling-tableformer"
    assert config["table_backend_requested"] == "gemini-3.5-flash-lite"
    assert _table_backend_identity([], True)[0] == "none"
