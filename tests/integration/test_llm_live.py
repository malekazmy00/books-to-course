"""Smoke test against the real llama-server (`llm` service). Skipped if B2C_LLM_BASE_URL is the mock."""
import os

import pytest
from pydantic import BaseModel

from b2c_core import Settings
from b2c_llm_gateway import LLMGateway

pytestmark = pytest.mark.integration


class Capital(BaseModel):
    country: str
    capital: str


@pytest.mark.skipif("llm-mock" in os.environ.get("B2C_LLM_BASE_URL", ""), reason="mock endpoint")
def test_live_json_schema_output():
    s = Settings()
    gw = LLMGateway(s.llm_base_url, s.llm_model_id, timeout_s=s.llm_timeout_s,
                    allow_remote=s.llm_allow_remote, api_key=s.llm_api_key)
    out = gw.structured(
        [{"role": "user", "content": "What is the capital of France? Answer as JSON."}],
        Capital, prompt_version="smoke-v1", max_tokens=64,
    )
    assert out.capital.strip().lower() == "paris"
