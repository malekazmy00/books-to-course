"""llm_gateway unit tests against a fake llama-server (httpx.MockTransport).

Covers docs/05 Phase 0 task 4: OpenAI-compatible client, cache, JSON-schema structured output,
2 retries then InvalidLLMOutput, OTel GenAI spans, no cloud provider (D-004).
"""
import json

import httpx
import pytest
from b2c_llm_gateway import (
    CloudProviderNotAllowed,
    InMemoryLLMCache,
    InvalidLLMOutput,
    LLMGateway,
    cache_key,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

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
        assert request.url.path.endswith("/chat/completions")
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


def test_remote_endpoint_can_be_explicitly_enabled_and_sends_bearer_key():
    seen = {}

    def server(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "model": "gpt-5.6-terra",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    gw = LLMGateway("https://codecraftapi.com/v1", "gpt-5.6-terra",
                    allow_remote=True, api_key="test-only-secret",
                    transport=httpx.MockTransport(server))
    assert gw.chat(MSGS, prompt_version="test").text == "ok"
    assert seen["authorization"] == "Bearer test-only-secret"


def test_openrouter_free_nemotron_uses_schema_constrained_json():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "nvidia/nemotron-3-super-120b-a12b:free",
                    allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="p1") == Greeting(name="Ada", greeting="Hi Ada")
    assert srv.requests[0]["response_format"]["type"] == "json_schema"


def test_openrouter_free_dots_uses_schema_constrained_json():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "dots-studio/dots-3-note-preview:free",
                    allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="p1") == Greeting(name="Ada", greeting="Hi Ada")
    assert srv.requests[0]["response_format"]["type"] == "json_schema"


def test_openrouter_free_apodex_uses_json_object_and_embedded_schema():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "apodex/apodex-1.1-mini:free",
                    allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="p1") == Greeting(name="Ada", greeting="Hi Ada")
    assert srv.requests[0]["response_format"] == {"type": "json_object"}
    assert any("JSON Schema" in message["content"] for message in srv.requests[0]["messages"])


def test_openrouter_free_inclusionai_uses_prompt_schema_without_unsupported_response_format():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "inclusionai/ling-3.0-flash-sante:free",
                    allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="p1") == Greeting(name="Ada", greeting="Hi Ada")
    assert "response_format" not in srv.requests[0]
    assert any("JSON Schema" in message["content"] for message in srv.requests[0]["messages"])


def test_openrouter_free_inkling_uses_prompt_schema_without_unsupported_response_format():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "thinkingmachines/inkling:free",
                    allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="p1") == Greeting(name="Ada", greeting="Hi Ada")
    assert "response_format" not in srv.requests[0]


def test_openrouter_free_laguna_uses_prompt_schema_without_unsupported_response_format():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "poolside/laguna-s-2.1:free",
                    allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="p1") == Greeting(name="Ada", greeting="Hi Ada")
    assert "response_format" not in srv.requests[0]


def test_openrouter_free_liquid_uses_json_object_schema_mode():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "liquid/lfm-2.5-2.6b:free",
                    allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="p1") == Greeting(name="Ada", greeting="Hi Ada")
    assert srv.requests[0]["response_format"] == {"type": "json_object"}


def test_empty_invalid_response_retry_does_not_send_empty_assistant_message():
    srv = FakeServer(["", '{"name": "Ada", "greeting": "Hi Ada"}'])
    gw, _ = make(srv)
    assert gw.structured(MSGS, Greeting, prompt_version="p1") == Greeting(name="Ada", greeting="Hi Ada")
    assert all(m.get("content", "").strip() for m in srv.requests[1]["messages"])


def test_remote_endpoint_requires_explicit_opt_in_and_key():
    with pytest.raises(CloudProviderNotAllowed):
        LLMGateway("https://codecraftapi.com/v1", "gpt-5.6-terra")
    with pytest.raises(CloudProviderNotAllowed, match="API key"):
        LLMGateway("https://codecraftapi.com/v1", "gpt-5.6-terra", allow_remote=True)
    with pytest.raises(CloudProviderNotAllowed, match="HTTPS"):
        LLMGateway("http://codecraftapi.com/v1", "gpt-5.6-terra",
                   allow_remote=True, api_key="test-only-secret")


def test_http_error_raises_llm_unavailable_after_bounded_transient_retries():
    from b2c_llm_gateway import LLMUnavailable
    calls = []

    def down(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "Loading model"}})

    gw = LLMGateway(BASE, "m", transport=httpx.MockTransport(down))
    with pytest.raises(LLMUnavailable):
        gw.chat(MSGS, prompt_version="p1")
    assert len(calls) == 3


def test_read_timeout_is_marked_ambiguous_and_not_retried():
    from b2c_llm_gateway import LLMUnavailable
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("response stalled", request=request)

    gw = LLMGateway(BASE, "m", transport=httpx.MockTransport(timeout))
    with pytest.raises(LLMUnavailable) as raised:
        gw.chat(MSGS, prompt_version="p1")
    assert raised.value.outcome_unknown is True
    assert len(calls) == 1


def test_transient_http_failure_recovers_within_bounded_retry_budget():
    calls = []
    def recover(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": {"message": "Loading model"}})
        return httpx.Response(200, json={"model":"m","choices":[{"message":{"content":"hello"}}],"usage":{}})
    gw = LLMGateway(BASE, "m", transport=httpx.MockTransport(recover))
    assert gw.chat(MSGS, prompt_version="p1").text == "hello"
    assert len(calls) == 3


def test_structured_with_result_preserves_reported_usage_cost():
    def server(request):
        return httpx.Response(200, json={
            "model":"judge-v1",
            "choices":[{"message":{"content":"{\"name\": \"Ada\", \"greeting\": \"Hi Ada\"}"},"finish_reason":"stop"}],
            "usage":{"prompt_tokens":13,"completion_tokens":5,"cost":0.0123},
        })
    gw=LLMGateway(BASE,"judge-v1",transport=httpx.MockTransport(server))
    result, value=gw.structured_with_result(MSGS,Greeting,prompt_version="judge-v1")
    assert value==Greeting(name="Ada",greeting="Hi Ada")
    assert result.model=="judge-v1"
    assert result.usage=={"input_tokens":13,"output_tokens":5,"cost_usd":0.0123}


def test_reasoning_effort_is_forwarded_when_explicitly_configured():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi"}'])
    gw, _ = make(srv, reasoning_effort="none")
    gw.structured(MSGS, Greeting, prompt_version="reasoning-none", max_tokens=4096)
    assert srv.requests[0]["reasoning_effort"] == "none"
    assert srv.requests[0]["max_tokens"] == 4096


def test_top_tools_provider_is_classified_and_unknown_cost_stays_null():
    server = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://top-tools-ai.com/api/v1", "Top-Tools-Ai",
        allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(server))
    assert gw.provider_name == "top_tools_ai"
    assert gw.usage_totals["cost_usd"] is None
    result = gw.structured_with_result(MSGS, Greeting, prompt_version="top-tools")[0]
    assert result.usage == {"input_tokens": 11, "output_tokens": 7}
    assert gw.usage_totals == {
        "api_calls": 1, "input_tokens": 11, "output_tokens": 7,
        "cost_usd": None, "cost_reported_calls": 0,
    }


def test_inception_free_route_reserves_a_conservative_token_cap_before_call():
    class Budget:
        def __init__(self):
            self.reservations = []
        def reserve(self, tokens):
            self.reservations.append(tokens)

    server = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    budget = Budget()
    gw = LLMGateway("https://api.inceptionlabs.ai/v1", "mercury-2",
        allow_remote=True, api_key="test-only-secret", request_budget=budget,
        transport=httpx.MockTransport(server))
    result = gw.structured_with_result(MSGS, Greeting, prompt_version="mercury-canary",
        max_tokens=4096)[0]
    assert result.model == "gemma4-test.gguf"
    assert gw.provider_name == "inceptionlabs"
    assert len(budget.reservations) == 1
    assert budget.reservations[0] >= 4096 + len(MSGS[0]["content"].encode("utf-8"))


def test_inception_rate_limit_fails_closed_without_retry_or_fallback():
    calls = []
    def limited(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "quota limit"}})
    gw = LLMGateway("https://api.inceptionlabs.ai/v1", "mercury-2",
        allow_remote=True, api_key="test-only-secret",
        transport=httpx.MockTransport(limited))
    from b2c_llm_gateway import LLMUnavailable
    with pytest.raises(LLMUnavailable, match="429"):
        gw.chat(MSGS, prompt_version="mercury-quota-stop")
    assert len(calls) == 1


def test_persistent_free_budget_stops_before_exceeding_lifetime_token_cap(monkeypatch):
    from b2c_llm_gateway.budget import PostgresFreeTokenBudget, FreeTokenQuotaExceeded
    import b2c_llm_gateway.budget as budget_module

    state = {"reserved": 0}
    class FakeCursor:
        def fetchone(self):
            return (state["reserved"],)
    class FakeConnection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params=()):
            if query.lstrip().startswith("UPDATE"):
                state["reserved"] = params[0]
            return FakeCursor()
    monkeypatch.setattr(budget_module.psycopg, "connect", lambda *a, **k: FakeConnection())
    budget = PostgresFreeTokenBudget("postgresql://not-used", provider="inception-mercury-2",
                                     token_limit=1000)
    budget.reserve(700)
    assert state["reserved"] == 700
    with pytest.raises(FreeTokenQuotaExceeded, match="without fallback"):
        budget.reserve(301)
    assert state["reserved"] == 700



def test_inception_structured_uses_json_object_and_validates_locally():
    srv = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gw = LLMGateway("https://api.inceptionlabs.ai/v1", "mercury-2",
        allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(srv))
    out = gw.structured(MSGS, Greeting, prompt_version="inception-json")
    assert out == Greeting(name="Ada", greeting="Hi Ada")
    assert "response_format" not in srv.requests[0]
    assert "temperature" not in srv.requests[0]

def test_http_400_exposes_only_sanitized_provider_fields():
    from b2c_llm_gateway import LLMRequestRejected
    def bad_request(request):
        return httpx.Response(400, json={"error": {
            "code": "invalid_schema", "param": "response_format.json_schema",
            "type": "invalid_request_error", "message": "secret prompt should not appear",
        }})
    gw = LLMGateway("https://api.inceptionlabs.ai/v1", "mercury-2",
        allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(bad_request))
    with pytest.raises(LLMRequestRejected) as exc:
        gw.chat(MSGS, prompt_version="bad-schema")
    assert "invalid_schema" in str(exc.value)
    assert "response_format.json_schema" in str(exc.value)
    assert "secret prompt" not in str(exc.value)


def test_http_400_extracts_only_allowlisted_parameter_name_from_message():
    from b2c_llm_gateway import LLMRequestRejected
    def bad_request(request):
        return httpx.Response(400, json={"error": {
            "code": "invalid_request_error", "type": "invalid_request_error",
            "message": "invalid value for max_tokens; echoed document text must not appear",
        }})
    gw = LLMGateway("https://api.inceptionlabs.ai/v1", "mercury-2",
        allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(bad_request))
    with pytest.raises(LLMRequestRejected) as exc:
        gw.chat(MSGS, prompt_version="safe-parameter")
    assert exc.value.param == "max_tokens"
    assert "echoed document" not in str(exc.value)


def test_http_400_diagnostics_expose_only_error_shape_and_category():
    from b2c_llm_gateway import LLMRequestRejected
    def bad_request(request):
        return httpx.Response(400, json={"error": {
            "code": "invalid_request_error", "type": "invalid_request_error",
            "message": "The request payload exceeded the context length; private prompt text.",
        }})
    gw = LLMGateway("https://api.inceptionlabs.ai/v1", "mercury-2",
        allow_remote=True, api_key="test-only-secret", transport=httpx.MockTransport(bad_request))
    with pytest.raises(LLMRequestRejected) as exc:
        gw.chat(MSGS, prompt_version="safe-error-shape")
    assert "context_limit" in str(exc.value)
    assert "private prompt" not in str(exc.value)
    assert "keys=code,message,type" in str(exc.value)



def test_openrouter_free_json_object_mode_includes_local_validation_schema_in_prompt():
    server = FakeServer(['{"name": "Ada", "greeting": "Hi Ada"}'])
    gateway = LLMGateway(
        "https://openrouter.ai/api/v1", "qwen/example:free",
        allow_remote=True, api_key="test-only-secret",
        transport=httpx.MockTransport(server), free_daily_limit=1, free_min_interval_s=0,
    )

    out = gateway.structured(MSGS, Greeting, prompt_version="free-schema-hint")

    assert out == Greeting(name="Ada", greeting="Hi Ada")
    request = server.requests[0]
    assert request["response_format"] == {"type": "json_object"}
    prompt = request["messages"][0]["content"]
    assert request["messages"][0]["role"] == "system"
    assert "JSON Schema" in prompt
    assert json.dumps(Greeting.model_json_schema(), ensure_ascii=False, separators=(",", ":")) in prompt
    assert request["messages"][1] == MSGS[0]


def test_truncated_output_preserves_partial_text_and_usage():
    class TruncatedServer(FakeServer):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.requests.append(body)
            return httpx.Response(200, json={
                "id": "truncated", "object": "chat.completion", "model": "gemma4-test.gguf",
                "choices": [{"index": 0, "finish_reason": "length",
                             "message": {"role": "assistant", "content": "{\"partial\":"}}],
                "usage": {"prompt_tokens": 19, "completion_tokens": 31, "total_tokens": 50},
            })

    from b2c_llm_gateway import LLMOutputTruncated
    gw, _ = make(TruncatedServer([]))
    with pytest.raises(LLMOutputTruncated) as exc:
        gw.structured(MSGS, Greeting, prompt_version="truncation-audit")
    assert exc.value.last_output == "{\"partial\":"
    assert exc.value.usage == {"input_tokens": 19, "output_tokens": 31}
    assert exc.value.model_id == "gemma4-test.gguf"
    assert exc.value.finish_reason == "length"
    assert gw.usage_totals["output_tokens"] == 31


def test_openrouter_nemotron_lightning_free_uses_prompt_schema_only():
    srv = FakeServer(['{"name":"Ada","greeting":"Hi"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "nvidia/nemotron-3.5-lightning:free",
                    api_key="test-key", allow_remote=True, transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="nemotron-lightning-schema").name == "Ada"
    assert "response_format" not in srv.requests[0]
    assert any("JSON Schema" in item["content"] for item in srv.requests[0]["messages"])


def test_openrouter_cohere_north_free_uses_prompt_schema_only():
    srv = FakeServer(['{"name":"Ada","greeting":"Hi"}'])
    gw = LLMGateway("https://openrouter.ai/api/v1", "cohere/north-mini-code:free",
                    api_key="test-key", allow_remote=True, transport=httpx.MockTransport(srv))
    assert gw.structured(MSGS, Greeting, prompt_version="cohere-north-schema").name == "Ada"
    assert "response_format" not in srv.requests[0]
    assert any("JSON Schema" in item["content"] for item in srv.requests[0]["messages"])


def test_cached_response_preserves_real_finish_reason_and_schema_repair_attempts():
    cache = InMemoryLLMCache()
    srv = FakeServer(["invalid json", '{"name":"Ada","greeting":"Hi"}'])
    gateway, exporter = make(srv, cache=cache)
    first, parsed = gateway.structured_with_result(MSGS, Greeting, prompt_version="telemetry-v1")
    second, repeated = gateway.structured_with_result(MSGS, Greeting, prompt_version="telemetry-v1")
    assert parsed == repeated
    assert first.attempts == 2 and first.finish_reason == "stop"
    assert second.cached and second.attempts == 0
    assert second.generation_attempts == 2 and second.finish_reason == "stop"
    assert len(srv.requests) == 2
    span = exporter.get_finished_spans()[-1]
    assert span.attributes["b2c.generation_attempts"] == 2
    assert span.attributes["gen_ai.response.finish_reasons"] == ("stop",)


def test_legacy_cache_unknown_finish_reason_is_not_fabricated():
    cache = InMemoryLLMCache()
    key = cache_key("gemma4-test", MSGS, {"temperature":0.0, "prompt_version":"legacy"})
    cache.put(key,"gemma4-test",{"text":"cached text","model":"gemma4-test","usage":{}})
    gateway, exporter = make(FakeServer([]), cache=cache)
    result = gateway.chat(MSGS,prompt_version="legacy")
    assert result.cached and result.attempts == 0
    assert result.finish_reason is None and result.generation_attempts is None
    assert "gen_ai.response.finish_reasons" not in exporter.get_finished_spans()[-1].attributes
    assert "b2c.generation_attempts" not in exporter.get_finished_spans()[-1].attributes


def test_structured_gateway_sends_full_input_and_unmodified_output_budget():
    long_text = "Body content including full source. " * 200
    messages = [{"role":"system","content":"Translate faithfully without omissions."},
                {"role":"user","content":long_text}]
    srv = FakeServer(['{"name":"Ada","greeting":"Heading"}'])
    gateway, _ = make(srv)
    output = gateway.structured(messages,Greeting,prompt_version="full-source",max_tokens=8192)
    assert srv.requests[0]["messages"] == messages
    assert srv.requests[0]["max_tokens"] == 8192
    assert output.greeting == "Heading"  # Schema acceptance alone is not semantic completeness.
