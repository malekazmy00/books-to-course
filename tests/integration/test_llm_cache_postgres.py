"""PostgresLLMCache against the real b2c Postgres."""
import uuid

import pytest

from b2c_core import Settings
from b2c_llm_gateway import PostgresLLMCache

pytestmark = pytest.mark.integration


def test_put_get_roundtrip_and_idempotent():
    cache = PostgresLLMCache(Settings().pg_dsn)
    cache.migrate()
    cache.migrate()
    key = uuid.uuid4().hex
    assert cache.get(key) is None
    cache.put(key, "m", {"text": "hi", "usage": {"input_tokens": 1, "output_tokens": 1}})
    cache.put(key, "m", {"text": "other"})  # first write wins
    assert cache.get(key)["text"] == "hi"
