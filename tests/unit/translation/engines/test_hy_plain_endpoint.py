"""Hy server D-028 endpoint: OpenAI-compatible chat completions with the messages passed to
the tokenizer chat template as-is. The real model is never loaded (fakes for torch/model)."""
import contextlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER_PATH = Path(__file__).resolve().parents[4] / "docker" / "hy-translation" / "server.py"


class FakeIds:
    def __init__(self, n):
        self.shape = (1, n)


class FakeTokenizer:
    def __init__(self):
        self.templated = []

    def apply_chat_template(self, messages, add_generation_prompt, tokenize, return_dict, return_tensors):
        assert add_generation_prompt and tokenize and return_dict and return_tensors == "pt"
        self.templated.append([dict(m) for m in messages])
        return {"input_ids": FakeIds(sum(len(m["content"].split()) for m in messages) + 3)}

    def encode(self, text, add_special_tokens=False):
        return text.split()

    def decode(self, ids, skip_special_tokens=True):
        return " " + " ".join(f"t{i}" for i in ids) + " "


class FakeModel:
    def __init__(self, n_new=3):
        self.n_new = n_new
        self.calls = []

    def generate(self, input_ids, max_new_tokens, do_sample, stopping_criteria):
        self.calls.append({"max_new_tokens": max_new_tokens, "do_sample": do_sample})
        count = input_ids.shape[-1]
        return [list(range(count)) + list(range(min(self.n_new, max_new_tokens)))]


@pytest.fixture
def server(monkeypatch):
    torch = types.ModuleType("torch")
    torch.inference_mode = contextlib.nullcontext
    transformers = types.ModuleType("transformers")
    transformers.StoppingCriteriaList = list
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    spec = importlib.util.spec_from_file_location("hy_server_plain_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.tokenizer, module.model = FakeTokenizer(), FakeModel()
    return module


def body(messages, **kw):
    return {"model": "tencent/Hy-MT2-1.8B", "messages": messages, "max_tokens": 64, "temperature": 0.0, **kw}


HY_PROMPT = ("Translate the following text into Arabic. Note that you should only output the translated "
             "result without any additional explanation:\n\nHello world.")


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/plain/v1/chat/completions"])
def test_plain_request_is_templated_as_is_and_returns_text(server, path):
    client = TestClient(server.app)  # no lifespan: the real model is not loaded
    messages = [{"role": "user", "content": HY_PROMPT}]
    r = client.post(path, json=body(messages))
    assert r.status_code == 200, r.text
    data = r.json()
    assert server.tokenizer.templated == [messages]
    assert data["choices"][0]["message"] == {"role": "assistant", "content": "t0 t1 t2"}
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["model"] == "tencent/Hy-MT2-1.8B" and data["usage"]["completion_tokens"] == 3
    assert server.model.calls[0]["do_sample"] is False


def test_plain_path_uses_the_same_token_bound_and_reports_truncation(server):
    server.model.n_new = 10_000
    client = TestClient(server.app)
    r = client.post("/plain/v1/chat/completions", json=body([{"role": "user", "content": "a b c d"}], max_tokens=8192))
    limit = server._bounded_output_tokens(8192, 4)
    assert server.model.calls[0]["max_new_tokens"] == limit
    assert r.json()["choices"][0]["finish_reason"] == "length"


def test_system_and_assistant_turns_pass_through_unchanged(server):
    client = TestClient(server.app)
    messages = [{"role": "system", "content": "Translate into Arabic."}, {"role": "user", "content": "Hi."},
                {"role": "assistant", "content": "مرحبا"}, {"role": "user", "content": "Revise."}]
    assert client.post("/plain/v1/chat/completions", json=body(messages)).status_code == 200
    assert server.tokenizer.templated == [messages]


@pytest.mark.parametrize("messages,extra", [
    ([], {}),
    ([{"role": "tool", "content": "x"}], {}),
    ([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], {}),
    ([{"role": "user", "content": "x"}], {"temperature": 0.7}),
    ([{"role": "user", "content": "x"}], {"stream": True}),
    ([{"role": "user", "content": "x"}], {"model": "other"}),
])
def test_plain_path_rejects_unsupported_requests(server, messages, extra):
    r = TestClient(server.app).post("/plain/v1/chat/completions", json=body(messages, **extra))
    assert r.status_code == 422
    assert server.model.calls == []


def test_plain_path_rejects_response_format(server):
    r = TestClient(server.app).post("/plain/v1/chat/completions", json=body(
        [{"role": "user", "content": "x"}], response_format={"type": "json_object"}))
    assert r.status_code == 422


def test_plain_path_reports_not_ready_while_model_loads(server):
    server.model = None
    r = TestClient(server.app).post("/v1/chat/completions", json=body([{"role": "user", "content": "x"}]))
    assert r.status_code == 503


def test_legacy_structured_requests_keep_the_old_segment_protocol(server):
    client = TestClient(server.app)
    segments = {"segments": [{"segment_id": "book:p1", "text": "Value one"}], "glossary": {}}
    r = client.post("/v1/chat/completions", json=body(
        [{"role": "system", "content": "Translate faithfully from en to ar."},
         {"role": "user", "content": json.dumps(segments)}],
        response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}))
    assert r.status_code == 200, r.text
    content = json.loads(r.json()["choices"][0]["message"]["content"])
    assert content["segments"][0]["segment_id"] == "book:p1"
    (templated,) = server.tokenizer.templated
    assert templated[0]["role"] == "user"
    assert templated[0]["content"] == server.build_segment_prompt("ar", segments["segments"][0], {})


def test_legacy_detection(server):
    req = server.CompletionRequest
    assert server.is_legacy_request(req(model="m", messages=[{"role": "user", "content": "x"}],
                                        response_format={"type": "json_object"}))
    assert server.is_legacy_request(req(model="m", messages=[
        {"role": "system", "content": "Translate from en to ar"}, {"role": "user", "content": "{}"}]))
    assert not server.is_legacy_request(req(model="m", messages=[{"role": "user", "content": HY_PROMPT}]))


def test_legacy_rejections_are_unchanged(server):
    r = TestClient(server.app).post("/v1/chat/completions", json=body(
        [{"role": "system", "content": "Translate from en to ar"}, {"role": "user", "content": "not json"}]))
    assert r.status_code == 422 and "segments" in r.json()["detail"]
