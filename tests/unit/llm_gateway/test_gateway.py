"""llm_gateway unit tests against a fake llama-server (httpx.MockTransport).

Covers docs/05 Phase 0 task 4: OpenAI-compatible client, cache, JSON-schema structured output,
2 retries then InvalidLLMOutput, OTel GenAI spans, no cloud provider (D-004).
"""
import json

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from b2c_llm_gateway import (
    CloudProviderNotAllowed,
    InMemoryLLMCache,
    InvalidLLMOutput,
    LLMGateway,
    cache_key,
)

BASE = "http://llm:8080/v1"
MSGS = [{"role": "user", "content": "Say hi to Ada as JSON."}]


class Greeting(BaseModel):
    name: str
    greeting: str


class FakeServer:
    """Replays a queue of assistant contents; records every request body."""

    def __init__(self, contents):
        self.contents = list(contents)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        self.requests.append(body)
        content = self.contents.pop(0)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": "gemma4-test.gguf",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })


def make(server, **kw):
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    gw = LLMGateway(BASE, "gemma4-test", transport=httpx.MockTransport(server),
                    tracer_provider=tp, **kw)
    return gw, exporter


def test_chat_sends_openai_request_with_temperature_zero_default():
    srv = FakeServer(["hello"])
    gw, _ = make(srv)
    res = gw.chat(MSGS, prompt_version="p1")
    assert res.text == "hello"
    assert res.usage == {"input_tokens": 11, "output_tokens": 7}
    body = srv.requests[0]
    assert body["model"] == "gemma4-test"
    assert body["messages"] == MSGS
    assert body["temperature"] == 0.0


def test_structured_uses_json_schema_response_format_and_validates():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw, _ = make(srv)
    out = gw.structured(MSGS, Greeting, prompt_version="p1")
    assert out == Greeting(name="Ada", greeting="Hi Ada")
    rf = srv.requests[0]["response_format"]
    # OpenAI nested form, which llama-server reads (VERIFY V-03)
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == Greeting.model_json_schema()
    assert rf["json_schema"]["name"] == "Greeting"


def test_invalid_output_is_retried_twice_then_raises():
    srv = FakeServer(["not json", '{"name": 1}', '{"greeting": "x"}'])
    gw, _ = make(srv)
    with pytest.raises(InvalidLLMOutput) as ei:
        gw.structured(MSGS, Greeting, prompt_version="p1")
    assert len(srv.requests) == 3  # 1 attempt + 2 retries, never silently accepted
    assert ei.value.attempts == 3
    assert ei.value.last_output == '{"greeting": "x"}'
    # retries carry the validation error back to the model
    assert srv.requests[1]["messages"][-1]["role"] == "user"
    assert "invalid" in srv.requests[1]["messages"][-1]["content"].lower()


def test_retry_recovers_when_second_attempt_is_valid():
    srv = FakeServer(["{", '{"name": "Ada", "greeting": "Hi"}'])
    gw, _ = make(srv)
    assert gw.structured(MSGS, Greeting, prompt_version="p1").name == "Ada"
    assert len(srv.requests) == 2


def test_cache_hit_skips_server_and_invalid_outputs_are_not_cached():
    cache = InMemoryLLMCache()
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi"}'])
    gw, exp = make(srv, cache=cache)
    a = gw.structured(MSGS, Greeting, prompt_version="p1")
    b = gw.structured(MSGS, Greeting, prompt_version="p1")
    assert a == b and len(srv.requests) == 1
    hits = [s.attributes["b2c.cache_hit"] for s in exp.get_finished_spans()]
    assert hits == [False, True]

    bad = FakeServer(["x", "x", "x"])
    gw2, _ = make(bad, cache=cache)
    with pytest.raises(InvalidLLMOutput):
        gw2.structured([{"role": "user", "content": "other"}], Greeting, prompt_version="p1")
    assert len(cache) == 1


def test_cache_key_depends_on_model_prompt_and_params():
    k = cache_key("m", MSGS, {"temperature": 0.0})
    assert k == cache_key("m", MSGS, {"temperature": 0.0})
    assert k != cache_key("m2", MSGS, {"temperature": 0.0})
    assert k != cache_key("m", MSGS + [{"role": "user", "content": "x"}], {"temperature": 0.0})
    assert k != cache_key("m", MSGS, {"temperature": 0.7})


def test_nondeterministic_calls_bypass_cache():
    cache = InMemoryLLMCache()
    srv = FakeServer(["a", "b"])
    gw, _ = make(srv, cache=cache)
    gw.chat(MSGS, prompt_version="p1", temperature=0.8)
    gw.chat(MSGS, prompt_version="p1", temperature=0.8)
    assert len(srv.requests) == 2 and len(cache) == 0


def test_span_has_genai_attributes():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi"}'])
    gw, exp = make(srv)
    gw.structured(MSGS, Greeting, prompt_version="p1")
    (span,) = exp.get_finished_spans()
    a = span.attributes
    assert span.name == "chat gemma4-test"
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.provider.name"] == "llama.cpp"
    assert a["gen_ai.request.model"] == "gemma4-test"
    assert a["gen_ai.response.model"] == "gemma4-test.gguf"
    assert a["gen_ai.request.temperature"] == 0.0
    assert a["gen_ai.usage.input_tokens"] == 11
    assert a["gen_ai.usage.output_tokens"] == 7
    assert a["gen_ai.output.type"] == "json"
    assert a["b2c.prompt_version"] == "p1"
    assert a["b2c.attempts"] == 1


def test_failed_structured_call_marks_span_error():
    srv = FakeServer(["x", "x", "x"])
    gw, exp = make(srv)
    with pytest.raises(InvalidLLMOutput):
        gw.structured(MSGS, Greeting, prompt_version="p1")
    (span,) = exp.get_finished_spans()
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["b2c.attempts"] == 3


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://api.anthropic.com/v1",
    "https://generativelanguage.googleapis.com/v1beta/openai",
    "http://8.8.8.8:8080/v1",
])
def test_cloud_endpoints_rejected(url):  # D-004
    with pytest.raises(CloudProviderNotAllowed):
        LLMGateway(url, "m")


@pytest.mark.parametrize("url", [
    "http://llm:8080/v1", "http://localhost:8080/v1", "http://127.0.0.1:8080/v1",
    "http://10.0.0.5:8080/v1", "http://172.18.0.3:8080/v1", "http://llm-mock:8080/v1",
])
def test_local_endpoints_allowed(url):
    LLMGateway(url, "m")


def test_http_error_raises_llm_unavailable():
    from b2c_llm_gateway import LLMUnavailable

    def down(request):
        return httpx.Response(503, json={"error": {"message": "Loading model"}})

    gw = LLMGateway(BASE, "m", transport=httpx.MockTransport(down))
    with pytest.raises(LLMUnavailable):
        gw.chat(MSGS, prompt_version="p1")
