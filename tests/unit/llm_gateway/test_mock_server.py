"""The stdlib mock llama-server used by integration tests and as the `llm-mock` fallback."""
import threading

import pytest
from pydantic import BaseModel

from b2c_llm_gateway import InvalidLLMOutput, LLMGateway
from b2c_llm_gateway.mock_server import make_server


class Item(BaseModel):
    title: str
    count: int
    tags: list[str]
    ok: bool


@pytest.fixture
def server():
    srv = make_server("127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_mock_returns_schema_valid_json(server):
    gw = LLMGateway(server, "mock")
    out = gw.structured([{"role": "user", "content": "x"}], Item, prompt_version="p1")
    assert isinstance(out, Item)


def test_mock_invalid_model_forces_invalid_output(server):
    gw = LLMGateway(server, "mock-invalid")
    with pytest.raises(InvalidLLMOutput):
        gw.structured([{"role": "user", "content": "x"}], Item, prompt_version="p1")
