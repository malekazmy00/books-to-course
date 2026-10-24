"""HelloPipeline activities: idempotent via stage_key + ArtifactStore; queue/retry configuration."""
import json
from datetime import timedelta

import boto3
import httpx
import pytest
from moto import mock_aws
from temporalio.testing import ActivityEnvironment

from b2c_core import ArtifactStore
from b2c_llm_gateway import InMemoryLLMCache, LLMGateway
from b2c_orchestration.hello import HelloActivities, HelloInput
from b2c_orchestration.queues import HEAVY, IO, LIGHT, max_concurrent_activities
from b2c_orchestration.retry import DEFAULT_RETRY


def fake_llm(calls):
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        name = body["messages"][-1]["content"].split("'")[1]
        content = json.dumps({"name": name, "greeting": f"Hello, {name}!"})
        return httpx.Response(200, json={
            "model": "fake", "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            "choices": [{"message": {"role": "assistant", "content": content}}]})
    return handler


@pytest.fixture
def acts():
    calls = []
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        store = ArtifactStore(s3, "artifacts")
        store.ensure_bucket()
        gw = LLMGateway("http://llm:8080/v1", "fake", cache=InMemoryLLMCache(),
                        transport=httpx.MockTransport(fake_llm(calls)))
        yield HelloActivities(store=store, gateway=gw, prov=None), calls, store


def run(fn, *args):
    return ActivityEnvironment().run(fn, *args)


def test_pipeline_activities_chain_and_produce_correct_result(acts):
    a, calls, store = acts
    inp = HelloInput(course_id="crs_hello", name="Ada")
    p = run(a.prepare, inp)
    g = run(a.greet, inp, p["ref"])
    f = run(a.finalize, inp, g["ref"])
    assert f["result"] == {"name": "Ada", "greeting": "Hello, Ada!"}
    assert [p["cache_hit"], g["cache_hit"], f["cache_hit"]] == [False, False, False]
    assert len(calls) == 1
    for r in (p, g, f):
        assert r["ref"]["uri"].startswith("s3://artifacts/crs_hello/hello-")


def test_rerun_is_all_cache_hits_and_no_llm_call(acts):
    a, calls, _ = acts
    inp = HelloInput(course_id="crs_hello", name="Ada")
    first = [run(a.prepare, inp)]
    first.append(run(a.greet, inp, first[0]["ref"]))
    first.append(run(a.finalize, inp, first[1]["ref"]))
    second = [run(a.prepare, inp)]
    second.append(run(a.greet, inp, second[0]["ref"]))
    second.append(run(a.finalize, inp, second[1]["ref"]))
    assert [r["cache_hit"] for r in second] == [True, True, True]
    assert [r["ref"] for r in second] == [r["ref"] for r in first]
    assert len(calls) == 1


def test_different_input_gets_different_keys(acts):
    a, _, _ = acts
    p1 = run(a.prepare, HelloInput(course_id="c", name="Ada"))
    p2 = run(a.prepare, HelloInput(course_id="c", name="Bob"))
    assert p1["ref"]["key"] != p2["ref"]["key"]


def test_heavy_queue_runs_one_activity_at_a_time():  # D-007
    assert max_concurrent_activities(HEAVY) == 1
    assert max_concurrent_activities(LIGHT) > 1
    assert max_concurrent_activities(IO) > 1
    assert (LIGHT, HEAVY, IO) == ("light", "heavy", "io")


def test_default_retry_policy_matches_contract():  # docs/02 §1.3
    assert DEFAULT_RETRY.initial_interval == timedelta(seconds=5)
    assert DEFAULT_RETRY.backoff_coefficient == 2.0
    assert DEFAULT_RETRY.maximum_interval == timedelta(minutes=10)
    assert DEFAULT_RETRY.maximum_attempts == 5
    assert set(DEFAULT_RETRY.non_retryable_error_types) == {
        "ValidationError", "UnsupportedFormat", "RightsNotCleared", "ConsentMissing",
        "OutlineNeedsReview", "OpenRouterRateLimited", "LLMOutputTruncated", "OpenRouterQuotaExceeded",
    }
