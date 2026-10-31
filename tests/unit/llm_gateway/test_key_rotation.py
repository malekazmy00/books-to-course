"""Backup API keys for a remote profile: an exhausted balance (HTTP 402) moves to the next key.

Free plans (e.g. Oxion AI: 0.5M tokens/month per key) answer 402 when a key's balance is empty;
the owner supplies more keys so the line keeps running (D-033: production does not stop).
"""
import json

import httpx
import pytest
from b2c_llm_gateway import LLMGateway, LLMUnavailable

BASE = "https://oxionai.com/v1"
MSGS = [{"role": "user", "content": "Hello."}]


class KeyedServer:
    def __init__(self, exhausted):
        self.exhausted, self.keys = set(exhausted), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        self.keys.append(key)
        if key in self.exhausted:
            return httpx.Response(402, json={"error": {"code": "insufficient_balance", "message": "empty"}})
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def gateway(server, **kw):
    return LLMGateway(BASE, "m", transport=httpx.MockTransport(server), allow_remote=True, api_key="k1", **kw)


def test_exhausted_key_moves_to_the_backup_and_stays_there():
    srv = KeyedServer(exhausted={"k1"})
    gw = gateway(srv, fallback_api_keys=["k2"])
    assert gw.complete_text(MSGS, prompt_version="t@v1", max_tokens=8).text == "ok"
    assert gw.complete_text([{"role": "user", "content": "Again."}], prompt_version="t@v1", max_tokens=8).text == "ok"
    assert srv.keys == ["k1", "k2", "k2"]


def test_all_keys_exhausted_fails_closed_without_looping():
    srv = KeyedServer(exhausted={"k1", "k2"})
    gw = gateway(srv, fallback_api_keys=["k2"])
    with pytest.raises(LLMUnavailable):
        gw.complete_text(MSGS, prompt_version="t@v1", max_tokens=8)
    assert srv.keys == ["k1", "k2"]


def test_without_backup_keys_402_is_not_retried():
    srv = KeyedServer(exhausted={"k1"})
    with pytest.raises(LLMUnavailable):
        gateway(srv).complete_text(MSGS, prompt_version="t@v1", max_tokens=8)
    assert srv.keys == ["k1"]


def test_backup_keys_need_a_primary_key_and_are_never_exposed():
    with pytest.raises(ValueError):
        LLMGateway("http://llm:8080/v1", "m", fallback_api_keys=["k2"])
    gw = gateway(KeyedServer(exhausted=()), fallback_api_keys=["k2"])
    assert "k2" not in repr(vars(gw).get("usage_totals")) and "k1" not in json.dumps(gw.usage_totals)
