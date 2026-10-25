"""R0 containment (audit 08 H3): book text is not copied into traces by default.

OTel GenAI semantic conventions recommend not capturing prompts/completions by default
(https://opentelemetry.io/docs/specs/semconv/gen-ai/, read 2026-10-24). Capture is an explicit
setting (B2C_LLM_CAPTURE_CONTENT=true) and remote endpoints never capture.
"""
from __future__ import annotations

import httpx
from b2c_core import Settings
from b2c_llm_gateway import LLMGateway
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

BOOK_TEXT = "BOOK-TEXT-SENTINEL: a fair coin has two outcomes."


def _server(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "COMPLETION-SENTINEL"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def _spans(**kw):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    gateway = LLMGateway("http://llm:8080/v1", "m", transport=httpx.MockTransport(_server),
                         tracer_provider=provider, **kw)
    gateway.chat([{"role": "user", "content": BOOK_TEXT}], prompt_version="p1")
    gateway.complete_text([{"role": "user", "content": BOOK_TEXT}], prompt_version="p1", max_tokens=8)
    return exporter.get_finished_spans()


def _all_attribute_text(spans) -> str:
    return "\n".join(str(value) for span in spans for value in dict(span.attributes).values())


def test_gateway_does_not_capture_content_by_default():
    spans = _spans()
    assert spans
    for span in spans:
        assert "langfuse.observation.input" not in span.attributes
        assert "langfuse.observation.output" not in span.attributes
    text = _all_attribute_text(spans)
    assert "BOOK-TEXT-SENTINEL" not in text and "COMPLETION-SENTINEL" not in text


def test_gateway_captures_content_only_when_explicitly_enabled():
    text = _all_attribute_text(_spans(capture_content=True))
    assert "BOOK-TEXT-SENTINEL" in text and "COMPLETION-SENTINEL" in text


def test_capture_setting_defaults_off_and_is_explicit(monkeypatch):
    monkeypatch.delenv("B2C_LLM_CAPTURE_CONTENT", raising=False)
    assert Settings().llm_capture_content is False
    monkeypatch.setenv("B2C_LLM_CAPTURE_CONTENT", "true")
    assert Settings().llm_capture_content is True


def test_translation_gateways_follow_the_setting():
    from b2c_orchestration import worker as worker_mod
    from b2c_orchestration.translation_profiles import EngineProfile

    profile = EngineProfile(id="x", kind="chat", provider="p", base_url="http://llm:8080/v1", model_id="m")
    assert worker_mod.translation_gateway_factory(cache=None)(profile).capture_content is False
    assert worker_mod.translation_gateway_factory(cache=None, capture_content=True)(profile).capture_content is True
