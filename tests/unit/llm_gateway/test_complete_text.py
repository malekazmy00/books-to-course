"""llm_gateway.complete_text: the plain-text path used by the D-028 translation engines.

Same safety as the structured path (D-004 allow-list, budgets, OTel GenAI spans, cache at
temperature 0), plus a cache key that carries provider identity and a key-format version.
"""
import json

import httpx
import pytest
from b2c_llm_gateway import (
    CloudProviderNotAllowed,
    InMemoryLLMCache,
    LLMGateway,
    LLMOutputTruncated,
    TextCompletion,
    cache_key,
    text_cache_key,
)
from b2c_llm_gateway.cache import TEXT_CACHE_KEY_FORMAT
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

BASE = "http://hy-translation:3007/v1"
MSGS = [{"role": "system", "content": "Translate into Arabic."}, {"role": "user", "content": "Hello."}]


class FakeServer:
    def __init__(self, replies):
        self.replies = list(replies)  # (content, finish_reason)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        self.requests.append(json.loads(request.content))
        content, finish = self.replies.pop(0)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": "hy-test",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
        })


def make(server, base=BASE, model="hy-test", **kw):
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    gw = LLMGateway(base, model, transport=httpx.MockTransport(server), tracer_provider=tp, **kw)
    return gw, exporter


def test_complete_text_sends_messages_as_is_without_response_format():
    srv = FakeServer([("مرحبا.", "stop")])
    gw, _ = make(srv)
    out = gw.complete_text(MSGS, prompt_version="t@v1", max_tokens=64)
    assert isinstance(out, TextCompletion)
    assert out.text == "مرحبا." and out.finish_reason == "stop" and out.model == "hy-test"
    assert out.usage == {"input_tokens": 9, "output_tokens": 4}
    assert out.cached is False
    body = srv.requests[0]
    assert body["messages"] == MSGS
    assert body["max_tokens"] == 64 and body["temperature"] == 0.0
    assert "response_format" not in body


def test_complete_text_satisfies_translation_gateway_protocol_shape():
    srv = FakeServer([("x", "stop")])
    gw, _ = make(srv)
    out = gw.complete_text([{"role": "user", "content": "a"}], prompt_version="p", max_tokens=8)
    for attr in ("text", "model", "finish_reason", "usage"):
        assert hasattr(out, attr)


def test_complete_text_caches_at_temperature_zero_only():
    cache = InMemoryLLMCache()
    srv = FakeServer([("one", "stop"), ("two", "stop"), ("three", "stop")])
    gw, exporter = make(srv, cache=cache)
    first = gw.complete_text(MSGS, prompt_version="p", max_tokens=32)
    second = gw.complete_text(MSGS, prompt_version="p", max_tokens=32)
    assert first.text == second.text == "one"
    assert second.cached is True and second.finish_reason == "stop"
    assert len(srv.requests) == 1
    gw.complete_text(MSGS, prompt_version="p", max_tokens=32, temperature=0.7)
    gw.complete_text(MSGS, prompt_version="p", max_tokens=32, temperature=0.7)
    assert len(srv.requests) == 3
    hits = [s.attributes["b2c.cache_hit"] for s in exporter.get_finished_spans()]
    assert hits == [False, True, False, False]


def test_truncated_output_raises_and_is_never_cached():
    cache = InMemoryLLMCache()
    srv = FakeServer([("partial", "length"), ("full", "stop")])
    gw, exporter = make(srv, cache=cache)
    with pytest.raises(LLMOutputTruncated) as exc:
        gw.complete_text(MSGS, prompt_version="p", max_tokens=4)
    assert exc.value.last_output == "partial" and exc.value.finish_reason == "length"
    assert len(cache) == 0
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert gw.complete_text(MSGS, prompt_version="p", max_tokens=4).text == "full"


def test_text_cache_key_covers_provider_identity_model_messages_params_version():
    base = dict(provider="llama.cpp", base_url="http://a:1/v1", model_id="m",
                messages=MSGS, params={"temperature": 0.0, "max_tokens": 8}, prompt_version="p")
    k = text_cache_key(**base)
    assert k == text_cache_key(**base)
    for change in ({"provider": "openrouter"}, {"base_url": "http://b:1/v1"}, {"model_id": "n"},
                   {"messages": [{"role": "user", "content": "x"}]},
                   {"params": {"temperature": 0.0, "max_tokens": 9}}, {"prompt_version": "q"}):
        assert text_cache_key(**{**base, **change}) != k, change
    assert TEXT_CACHE_KEY_FORMAT  # versioned key format
    assert k != cache_key("m", MSGS, {"temperature": 0.0, "max_tokens": 8, "prompt_version": "p"})


def test_same_model_on_two_servers_does_not_share_cache_entries():
    cache = InMemoryLLMCache()
    a, _ = make(FakeServer([("from-a", "stop")]), base="http://hy-a:3007/v1", cache=cache)
    b_srv = FakeServer([("from-b", "stop")])
    b, _ = make(b_srv, base="http://hy-b:3007/v1", cache=cache)
    assert a.complete_text(MSGS, prompt_version="p", max_tokens=8).text == "from-a"
    assert b.complete_text(MSGS, prompt_version="p", max_tokens=8).text == "from-b"
    assert len(b_srv.requests) == 1


def test_structured_cache_key_is_unchanged():
    # The structured path keeps its historical key so existing cache entries stay valid.
    cache = InMemoryLLMCache()
    srv = FakeServer([("hello", "stop")])
    gw, _ = make(srv, cache=cache)
    gw.chat(MSGS, prompt_version="p1")
    assert cache.get(cache_key("hy-test", MSGS, {"temperature": 0.0, "prompt_version": "p1"}))


def test_span_follows_genai_semconv_and_records_finish_reason():
    srv = FakeServer([("ok", "stop")])
    gw, exporter = make(srv)
    gw.complete_text(MSGS, prompt_version="t@v1", max_tokens=16)
    (span,) = exporter.get_finished_spans()
    a = span.attributes
    assert span.name == "chat hy-test"
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.provider.name"] == "llama.cpp"
    assert a["gen_ai.request.model"] == "hy-test"
    assert a["gen_ai.output.type"] == "text"
    assert a["gen_ai.request.max_tokens"] == 16
    assert tuple(a["gen_ai.response.finish_reasons"]) == ("stop",)
    assert a["gen_ai.usage.input_tokens"] == 9 and a["gen_ai.usage.output_tokens"] == 4
    assert a["b2c.prompt_version"] == "t@v1"


def test_remote_endpoint_still_refused_without_opt_in():
    with pytest.raises(CloudProviderNotAllowed):
        LLMGateway("https://api.example.com/v1", "m")


def test_remote_content_is_not_captured_in_spans():
    srv = FakeServer([("ok", "stop")])
    gw, exporter = make(srv, base="https://codecraft.example.com/v1", allow_remote=True, api_key="k")
    gw.complete_text(MSGS, prompt_version="p", max_tokens=8)
    attrs = exporter.get_finished_spans()[0].attributes
    assert "langfuse.observation.input" not in attrs and "langfuse.observation.output" not in attrs


@pytest.mark.parametrize("messages", [[], [{"role": "user"}], [{"role": "tool", "content": "x"}],
                                      [{"role": "user", "content": 3}]])
def test_malformed_messages_are_rejected_before_any_request(messages):
    srv = FakeServer([])
    gw, _ = make(srv)
    with pytest.raises(ValueError):
        gw.complete_text(messages, prompt_version="p", max_tokens=8)
    assert srv.requests == []


def test_max_tokens_must_be_positive():
    gw, _ = make(FakeServer([]))
    with pytest.raises(ValueError):
        gw.complete_text(MSGS, prompt_version="p", max_tokens=0)


def test_inception_budget_reservation_applies_to_text_path():
    class Budget:
        def __init__(self):
            self.reserved = []

        def reserve(self, n):
            self.reserved.append(n)

    budget = Budget()
    srv = FakeServer([("ok", "stop")])
    gw, _ = make(srv, base="https://api.inceptionlabs.ai/v1", allow_remote=True, api_key="k",
                 request_budget=budget)
    gw.complete_text(MSGS, prompt_version="p", max_tokens=8)
    assert len(budget.reserved) == 1 and budget.reserved[0] > 8


def test_usage_totals_count_text_calls():
    srv = FakeServer([("ok", "stop")])
    gw, _ = make(srv)
    gw.complete_text(MSGS, prompt_version="p", max_tokens=8)
    assert gw.usage_totals["api_calls"] == 1 and gw.usage_totals["output_tokens"] == 4
