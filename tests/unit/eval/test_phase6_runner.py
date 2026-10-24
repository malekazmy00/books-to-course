from argparse import Namespace
import json
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "eval" / "suites" / "phase6"))
from run_hipporag2 import (PersistentCostLedger, PersistentTokenLedger, aggregate_usage, apply_llm_request_policy,
                           corpus_fingerprint, instrument_openai_usage, run, selected_queries)


def test_selected_queries_only_returns_questions_for_requested_split():
    rows = [
        {"query_id": "dev-1", "query": "What is X?", "split": "development", "qrels": {"c1": 1}},
        {"query_id": "test-1", "query": "What is Y?", "split": "test", "qrels": {"c2": 1}},
    ]
    assert selected_queries(rows, "development") == [
        {"query_id": "dev-1", "query": "What is X?"}
    ]


def test_selected_queries_rejects_duplicate_ids_and_empty_splits():
    duplicate = [
        {"query_id": "q1", "query": "First?", "split": "dev"},
        {"query_id": "q1", "query": "Second?", "split": "dev"},
    ]
    with pytest.raises(ValueError, match="unique"):
        selected_queries(duplicate, "dev")
    with pytest.raises(ValueError, match="no queries"):
        selected_queries([], "dev")


def test_corpus_hash_is_independent_of_argument_order(tmp_path):
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.write_text('{"chunk_id":"a"}\n', encoding="utf-8")
    second.write_text('{"chunk_id":"b"}\n', encoding="utf-8")
    assert corpus_fingerprint([first, second]) == corpus_fingerprint([second, first])


def test_runner_rejects_nonpositive_top_k_before_any_runtime_access(tmp_path):
    args = Namespace(
        chunks=[], dataset=tmp_path / "missing.jsonl", output=tmp_path / "out.json",
        output_root=tmp_path / "missing-root", top_k=0,
    )
    with pytest.raises(ValueError, match="top_k must be positive"):
        run(args)


def test_openrouter_request_policy_is_pinned_and_applied_to_all_unique_llm_clients():
    params = {"max_completion_tokens": 2048}
    model = SimpleNamespace(llm_config=SimpleNamespace(generate_params=params))
    client = SimpleNamespace(llm_model=model, extraction_llm=model, qa_llm=model)
    policy_path = ROOT / "eval" / "suites" / "phase6" / "openrouter-deepseek-v4-flash-0731.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    recorded = apply_llm_request_policy(client, policy)
    assert params["max_completion_tokens"] == 4096
    assert params["reasoning_effort"] == "none"
    assert params["extra_body"] == {"provider": policy["provider"]}
    assert recorded["model_id"] == "deepseek/deepseek-v4-flash-0731"
    assert recorded["provider"]["allow_fallbacks"] is False


def test_openrouter_request_policy_rejects_unbounded_or_fallback_provider_settings():
    params = {"max_completion_tokens": 2048}
    model = SimpleNamespace(llm_config=SimpleNamespace(generate_params=params))
    client = SimpleNamespace(llm_model=model)
    policy_path = ROOT / "eval" / "suites" / "phase6" / "openrouter-deepseek-v4-flash-0731.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["provider"]["allow_fallbacks"] = True
    with pytest.raises(ValueError, match="without fallback"):
        apply_llm_request_policy(client, policy)
    assert params == {"max_completion_tokens": 2048}




def test_usage_capture_records_only_provider_cost_and_token_metadata():
    response = SimpleNamespace(
        model="deepseek/deepseek-v4-flash-0731", _request_id="req-test",
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20,
                              cost=0.00042, model_extra={"cost_details": {"upstream_inference_cost": 0.0004}}),
    )
    class Completions:
        def create(self, *args, **kwargs):
            return response
    openai_client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    model = SimpleNamespace(openai_client=openai_client)
    client = SimpleNamespace(llm_model=model, extraction_llm=model, qa_llm=model)
    records = instrument_openai_usage(client)
    returned = openai_client.chat.completions.create(messages=[{"content": "private"}])
    assert returned is response
    total = aggregate_usage(records)
    assert total["request_count"] == 1
    assert total["prompt_tokens"] == 12 and total["completion_tokens"] == 8
    assert total["observed_cost_usd"] == 0.00042 and total["cost_complete"] is True
    assert "private" not in str(total)


def test_top_tools_policy_is_exact_and_omits_openrouter_only_fields():
    params = {"max_completion_tokens": 512, "reasoning_effort": "none", "extra_body": {"provider": "x"}}
    model = SimpleNamespace(llm_config=SimpleNamespace(generate_params=params))
    client = SimpleNamespace(llm_model=model)
    policy_path = ROOT / "eval" / "suites" / "phase6" / "top-tools-ai-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    recorded = apply_llm_request_policy(client, policy)
    assert params == {"max_tokens": 4096, "temperature": 0}
    assert recorded["provider_type"] == "top_tools_ai"
    assert recorded["quota_limit_tokens"] == 8_000_000
    assert recorded["prior_tokens_used"] == 543
    assert "reasoning_effort" not in policy and "provider" not in policy


def test_top_tools_policy_rejects_fallback_and_nonzero_temperature():
    params = {"max_tokens": 1024}
    client = SimpleNamespace(llm_model=SimpleNamespace(llm_config=SimpleNamespace(generate_params=params)))
    policy = json.loads((ROOT / "eval/suites/phase6/top-tools-ai-policy.json").read_text())
    policy["fallback_policy"] = "automatic"
    with pytest.raises(ValueError, match="no fallback"):
        apply_llm_request_policy(client, policy)
    policy["fallback_policy"] = "none"
    policy["temperature"] = 0.1
    with pytest.raises(ValueError, match="zero"):
        apply_llm_request_policy(client, policy)


def test_usage_capture_stops_before_provider_call_when_quota_reservation_exceeds_limit():
    from run_hipporag2 import ProviderQuotaExceeded
    called = False
    class Completions:
        def create(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("must not call provider")
    client = SimpleNamespace(llm_model=SimpleNamespace(openai_client=SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()))))
    instrument_openai_usage(client, daily_token_limit=100)
    with pytest.raises(ProviderQuotaExceeded, match="before provider call"):
        client.llm_model.openai_client.chat.completions.create(
            messages=[{"role": "user", "content": "x"}], max_tokens=4096)
    assert not called


def test_usage_capture_marks_unreported_cost_unknown():
    response = SimpleNamespace(model="Top-Tools-Ai", _request_id="req-toptools",
                               usage=SimpleNamespace(prompt_tokens=244, completion_tokens=52, total_tokens=296))
    class Completions:
        def create(self, *args, **kwargs):
            return response
    client = SimpleNamespace(llm_model=SimpleNamespace(openai_client=SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()))))
    records = instrument_openai_usage(client, daily_token_limit=8_000_000)
    client.llm_model.openai_client.chat.completions.create(messages=[{"content": "x"}], max_tokens=4096)
    usage = aggregate_usage(records, daily_token_limit=8_000_000)
    assert usage["observed_cost_usd"] is None
    assert usage["cost_complete"] is False
    assert usage["total_tokens"] == 296


def test_usage_capture_rejects_provider_response_from_different_model():
    from run_hipporag2 import ProviderRouteMismatch
    response = SimpleNamespace(model="some-paid-fallback", _request_id="req-route",
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    class Completions:
        def create(self, *args, **kwargs):
            return response
    client = SimpleNamespace(llm_model=SimpleNamespace(openai_client=SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()))))
    records = instrument_openai_usage(client, expected_model="Top-Tools-Ai")
    with pytest.raises(ProviderRouteMismatch, match="pinned model"):
        client.llm_model.openai_client.chat.completions.create(messages=[])
    assert records[0]["model"] == "some-paid-fallback"


def test_inception_mercury_policy_pins_free_model_and_endpoint_without_fallback():
    params = {"max_completion_tokens": 1024, "reasoning_effort": "none"}
    model = SimpleNamespace(llm_config=SimpleNamespace(generate_params=params))
    client = SimpleNamespace(llm_model=model)
    path = ROOT / "eval/suites/phase6/inception-mercury-2-policy.json"
    policy = json.loads(path.read_text())
    recorded = apply_llm_request_policy(client, policy)
    assert recorded["model_id"] == "mercury-2"
    assert recorded["base_url"] == "https://api.inceptionlabs.ai/v1"
    assert recorded["fallback_policy"] == "none"
    assert recorded["quota_limit_tokens"] == 10_000_000
    assert recorded["quota_scope"] == "cumulative"
    assert params == {"max_tokens": 4096, "temperature": 0}
    policy["base_url"] = "https://paid-route.example/v1"
    with pytest.raises(ValueError, match="base URL"):
        apply_llm_request_policy(client, policy)



def test_persistent_quota_ledger_accumulates_usage_across_instances(tmp_path):
    path = tmp_path / "provider-ledger.json"
    first = PersistentTokenLedger(path, provider="mercury", scope="cumulative", limit=1000,
                                  prior_tokens_used=100)
    reservation = first.reserve(300)
    first.complete(reservation, prompt=60, completion=40, total=100)
    second = PersistentTokenLedger(path, provider="mercury", scope="cumulative", limit=1000,
                                   prior_tokens_used=100)
    snapshot = second.snapshot()
    assert snapshot["prior_tokens_used"] == 100
    assert snapshot["actual_prompt_tokens"] == 60
    assert snapshot["actual_completion_tokens"] == 40
    assert snapshot["actual_total_tokens"] == 100
    assert snapshot["accounted_tokens"] == 200
    assert snapshot["remaining_tokens"] == 800
    from run_hipporag2 import ProviderQuotaExceeded
    with pytest.raises(ProviderQuotaExceeded, match="before provider call"):
        second.reserve(801)


def test_persistent_quota_ledger_charges_orphaned_reservations_fail_closed(tmp_path):
    path = tmp_path / "provider-ledger.json"
    first = PersistentTokenLedger(path, provider="top-tools", scope="daily", limit=1000,
                                  prior_tokens_used=0, window_id="2026-10-04")
    first.reserve(400)
    restarted = PersistentTokenLedger(path, provider="top-tools", scope="daily", limit=1000,
                                     prior_tokens_used=0, window_id="2026-10-04")
    snapshot = restarted.snapshot()
    assert snapshot["pending_reserved_tokens"] == 0
    assert snapshot["conservative_reserved_tokens"] == 400
    assert snapshot["accounted_tokens"] == 400
    from run_hipporag2 import ProviderQuotaExceeded
    with pytest.raises(ProviderQuotaExceeded, match="before provider call"):
        restarted.reserve(601)



def test_usage_instrumentation_persists_actual_mercury_tokens_in_cumulative_ledger(tmp_path):
    response = SimpleNamespace(model="mercury-2", _request_id="req-ledger",
                               usage=SimpleNamespace(prompt_tokens=31, completion_tokens=17,
                                                     total_tokens=48, model_extra={}))
    class Completions:
        def create(self, *args, **kwargs):
            return response
    client = SimpleNamespace(llm_model=SimpleNamespace(openai_client=SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()))))
    ledger = PersistentTokenLedger(tmp_path / "mercury.json", provider="inception_mercury",
                                  scope="cumulative", limit=10000, prior_tokens_used=200)
    records = instrument_openai_usage(client, expected_model="mercury-2", quota_ledger=ledger)
    client.llm_model.openai_client.chat.completions.create(
        messages=[{"role": "user", "content": "test"}], max_tokens=4096)
    usage = aggregate_usage(records, quota_ledger=ledger)
    assert usage["total_tokens"] == 48
    assert usage["quota_ledger"]["prior_tokens_used"] == 200
    assert usage["quota_ledger"]["actual_total_tokens"] == 48
    assert usage["quota_ledger"]["accounted_tokens"] == 248
    assert usage["observed_cost_usd"] is None


def test_persistent_quota_ledger_keeps_conservative_reservation_after_provider_400(tmp_path):
    from run_hipporag2 import PersistentTokenLedger
    class Completions:
        def create(self, *args, **kwargs):
            raise RuntimeError("provider returned HTTP 400")
    client = SimpleNamespace(llm_model=SimpleNamespace(openai_client=SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()))))
    ledger = PersistentTokenLedger(tmp_path / "mercury.json", provider="inception_mercury",
                                  scope="cumulative", limit=1000000, prior_tokens_used=367074)
    instrument_openai_usage(client, expected_model="mercury-2", quota_ledger=ledger)
    with pytest.raises(RuntimeError, match="HTTP 400"):
        client.llm_model.openai_client.chat.completions.create(
            messages=[{"role": "user", "content": "request"}], max_tokens=4096)
    snapshot = ledger.snapshot()
    assert snapshot["actual_total_tokens"] == 0
    assert snapshot["conservative_reserved_tokens"] > 4096
    assert snapshot["accounted_tokens"] > 367074


def test_persistent_quota_ledger_rejects_lower_prior_usage_on_retry(tmp_path):
    from run_hipporag2 import PersistentTokenLedger
    path = tmp_path / "mercury.json"
    first = PersistentTokenLedger(path, provider="inception_mercury", scope="cumulative",
                                  limit=10000, prior_tokens_used=500)
    first.reserve(100)
    with pytest.raises(ValueError, match="cannot decrease"):
        PersistentTokenLedger(path, provider="inception_mercury", scope="cumulative",
                              limit=10000, prior_tokens_used=100)


def test_deepseek_policy_enforces_no_fallback_and_aggregate_usd_cap():
    params = {"max_completion_tokens": 512}
    model = SimpleNamespace(llm_config=SimpleNamespace(generate_params=params))
    client = SimpleNamespace(llm_model=model)
    path = ROOT / "eval/suites/phase6/openrouter-deepseek-v4-flash-0731-capped.json"
    policy = json.loads(path.read_text())
    recorded = apply_llm_request_policy(client, policy)
    assert params["max_tokens"] == 1024
    assert "max_completion_tokens" not in params
    assert params["reasoning_effort"] == "none"
    assert params["extra_body"]["provider"]["allow_fallbacks"] is False
    assert recorded["cost_cap_usd"] == pytest.approx(0.25)
    assert recorded["prior_prompt_tokens"] == 163037
    assert recorded["prior_completion_tokens"] == 14794
    assert recorded["prompt_usd_per_million"] == pytest.approx(0.0152)
    assert recorded["completion_usd_per_million"] == pytest.approx(1.28)
    policy["cost_cap_usd"] = 0.251
    with pytest.raises(ValueError, match="authorized Phase 6 cap"):
        apply_llm_request_policy(client, policy)



def test_persistent_cost_ledger_prices_prompt_and_completion_separately(tmp_path):
    ledger = PersistentCostLedger(
        tmp_path / "openrouter-cost.json", provider="openrouter_deepseek",
        cost_cap_usd=0.01, prompt_usd_per_million=0.0152,
        completion_usd_per_million=1.28, prior_prompt_tokens=1000,
        prior_completion_tokens=1000,
    )
    reservation = ledger.reserve(prompt_bound=10000, completion_bound=4096)
    ledger.complete(reservation, prompt=1000, completion=100, total=1100)
    snapshot = ledger.snapshot()
    assert snapshot["actual_prompt_tokens"] == 1000
    assert snapshot["actual_completion_tokens"] == 100
    assert snapshot["actual_cost_usd"] == pytest.approx(0.000144)
    assert snapshot["accounted_cost_usd"] == pytest.approx(0.001439)
    assert snapshot["remaining_cost_usd"] == pytest.approx(0.008561)


def test_persistent_cost_ledger_fails_closed_on_orphan_and_cost_overrun(tmp_path):
    path = tmp_path / "openrouter-cost.json"
    ledger = PersistentCostLedger(
        path, provider="openrouter_deepseek", cost_cap_usd=0.01,
        prompt_usd_per_million=0.0152, completion_usd_per_million=1.28,
        prior_prompt_tokens=0, prior_completion_tokens=0,
    )
    ledger.reserve(prompt_bound=1000, completion_bound=5000)
    restarted = PersistentCostLedger(
        path, provider="openrouter_deepseek", cost_cap_usd=0.01,
        prompt_usd_per_million=0.0152, completion_usd_per_million=1.28,
        prior_prompt_tokens=0, prior_completion_tokens=0,
    )
    snapshot = restarted.snapshot()
    assert snapshot["pending"] == {}
    assert snapshot["conservative_reserved_microusd"] >= 6400
    from run_hipporag2 import ProviderQuotaExceeded
    with pytest.raises(ProviderQuotaExceeded, match="before provider call"):
        restarted.reserve(prompt_bound=0, completion_bound=3000)


def test_aggregate_usage_reports_cost_ledger_without_token_schema(tmp_path):
    ledger = PersistentCostLedger(
        tmp_path / "openrouter-cost.json", provider="openrouter_deepseek",
        cost_cap_usd=0.01, prompt_usd_per_million=0.0152,
        completion_usd_per_million=1.28, prior_prompt_tokens=0,
        prior_completion_tokens=0,
    )
    result = aggregate_usage([], quota_ledger=ledger)
    assert result["quota_status"] == "within_limit"
    assert result["quota_ledger"]["remaining_cost_usd"] == pytest.approx(0.01)
