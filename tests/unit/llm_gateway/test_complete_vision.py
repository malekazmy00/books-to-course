"""llm_gateway.complete_vision: text + image(s) to an OpenAI-compatible vision model (D-044).

Images go as ``image_url`` data-URL parts BEFORE the text of the last user message (Gemma 4 guidance
on ollama.com/library/gemma4: image content first). The cache key carries the images' SHA-256, never
their bytes; the same allow-list (D-004) applies."""
import base64
import hashlib
import json

import httpx
import pytest
from b2c_llm_gateway import CloudProviderNotAllowed, InMemoryLLMCache, LLMGateway, TextCompletion

BASE = "http://gemma4-ollama:11434/v1"
MSGS = [{"role": "system", "content": "Describe."}, {"role": "user", "content": "Figure 2.1"}]
PNG = b"\x89PNG\r\n\x1a\nfake"


def _gateway(cache=None):
    seen: list[dict] = []

    def server(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"model": body["model"], "choices": [
            {"message": {"content": "A figure."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 5}})
    gw = LLMGateway(BASE, "gemma4-b2c:26b-a4b-it-qat-t18", cache=cache, transport=httpx.MockTransport(server))
    return gw, seen


def test_images_are_sent_first_as_data_urls():
    gw, seen = _gateway()
    out = gw.complete_vision(MSGS, images=[PNG], prompt_version="fig@1", max_tokens=64)
    assert isinstance(out, TextCompletion) and out.text == "A figure."
    user = seen[0]["messages"][-1]
    assert user["role"] == "user" and isinstance(user["content"], list)
    image, text = user["content"]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert text == {"type": "text", "text": "Figure 2.1"}
    assert seen[0]["messages"][0] == MSGS[0]


def test_cache_key_uses_image_digest_and_hits():
    cache = InMemoryLLMCache()
    gw, seen = _gateway(cache)
    gw.complete_vision(MSGS, images=[PNG], prompt_version="fig@1", max_tokens=64)
    again = gw.complete_vision(MSGS, images=[PNG], prompt_version="fig@1", max_tokens=64)
    assert again.cached and len(seen) == 1
    stored = json.dumps(list(cache._d.values()), default=str)
    assert base64.b64encode(PNG).decode() not in stored
    other = gw.complete_vision(MSGS, images=[PNG + b"x"], prompt_version="fig@1", max_tokens=64)
    assert not other.cached and len(seen) == 2
    assert hashlib.sha256(PNG).hexdigest()  # digest is what distinguishes the two calls


def test_needs_at_least_one_image_and_a_known_type():
    gw, _ = _gateway()
    with pytest.raises(ValueError):
        gw.complete_vision(MSGS, images=[], prompt_version="p", max_tokens=8)
    with pytest.raises(ValueError):
        gw.complete_vision(MSGS, images=[b"GIF89a"], prompt_version="p", max_tokens=8)


def test_remote_endpoint_still_refused():
    with pytest.raises(CloudProviderNotAllowed):
        LLMGateway("https://example.com/v1", "m")
