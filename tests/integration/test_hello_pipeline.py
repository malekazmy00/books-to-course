"""HelloPipeline end-to-end on the real Temporal + worker + postgres + minio + llm stack."""
import uuid

import pytest

from b2c_orchestration.run_hello import run_pipeline

pytestmark = pytest.mark.integration


async def test_hello_pipeline_completes_and_second_run_is_cached():
    name = f"Ada{uuid.uuid4().hex[:6]}"
    r1 = await run_pipeline(name=name, course_id="crs_itest")
    assert r1["result"]["name"] == name
    assert r1["result"]["greeting"]
    assert r1["cache_hits"] == [False, False, False]

    r2 = await run_pipeline(name=name, course_id="crs_itest")
    assert r2["result"] == r1["result"]
    assert r2["cache_hits"] == [True, True, True]
