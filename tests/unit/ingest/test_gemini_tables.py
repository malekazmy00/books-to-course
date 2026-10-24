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
    assert urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query) == {"key": ["test-secret"]}
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