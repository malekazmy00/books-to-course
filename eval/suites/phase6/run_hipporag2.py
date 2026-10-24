"""Generate same-corpus Phase 6 rankings in a dedicated isolated runtime.

This runner never passes qrels or answer text to HippoRAG; it performs retrieval
only and writes rankings consumable by eval/suites/phase4/compare_rankings.py.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Mapping

from hipporag2_bridge import (
    HIPPORAG_COMMIT,
    HIPPORAG_VERSION,
    HippoRAG2Backend,
    RuntimeConfig,
    create_isolated_runtime,
    to_hipporag_chunks,
)


def reuse_llm_cache(client: Any, source: Path, allowed_root: Path) -> str:
    """Copy only the response cache from a prior isolated run after integrity checks."""
    if source.is_symlink() or not source.is_file():
        raise ValueError("reused LLM cache must be a regular non-symlink file")
    root = allowed_root.resolve()
    source_resolved = source.resolve()
    try:
        source_resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("reused LLM cache must remain inside the isolated scratch root") from exc
    if source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("reused LLM cache exceeds the 64 MiB evaluation limit")
    with sqlite3.connect(f"file:{source_resolved}?mode=ro", uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("reused LLM cache failed SQLite integrity check")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {"cache"}:
            raise ValueError("reused LLM cache has unexpected tables")
        columns = [row[1] for row in db.execute("PRAGMA table_info(cache)")]
        if columns != ["key", "message", "metadata"]:
            raise ValueError("reused LLM cache schema mismatch")
    target = Path(client.llm_model.cache_file_name)
    if target.exists() or target.is_symlink():
        raise ValueError("new runtime already contains an LLM cache")
    shutil.copyfile(source_resolved, target)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def install_bounded_ner_recovery(client: Any) -> list[dict[str, Any]]:
    """Retry malformed NER once with strict JSON instructions; never synthesize entities."""
    from hipporag.information_extraction.openie_openai import _extract_ner_from_response
    from hipporag.utils.misc_utils import NerRawOutput

    recovered: list[dict[str, Any]] = []
    original_ner = client.openie.ner

    def recover(chunk_key: str, passage: str):
        result = original_ner(chunk_key, passage)
        if not result.metadata.get("error"):
            return result
        if len(recovered) >= 2:
            raise RuntimeError("bounded NER recovery limit of two malformed responses exceeded")
        messages = [
            {"role": "system", "content": (
                "Extract only explicit named entities from the supplied passage. The passage may be "
                "fragmented, noisy, a formula, or a partial sentence. Do not judge or refuse the passage. "
                "Do not explain. Return exactly one valid JSON object with the key named_entities and a "
                "list of strings. If no explicit named entities are present, return "
                "{\"named_entities\": []}. Do not invent entities."
            )},
            {"role": "user", "content": "Passage:\n" + passage + "\n\nReturn only the required JSON object."},
        ]
        raw, metadata, cache_hit = client.openie.llm_model.infer(messages=messages, max_new_tokens=512)
        entities = _extract_ner_from_response(raw)
        if any(not isinstance(item, str) or not item.strip() for item in entities):
            raise ValueError("strict NER recovery returned invalid entity items")
        metadata = dict(metadata)
        metadata.update({"cache_hit": cache_hit, "phase6_recovery": "bounded strict-JSON retry"})
        recovered.append({"status": "valid_empty" if not entities else "valid_entities",
                          "response_char_count": len(raw), "cache_hit": bool(cache_hit)})
        return NerRawOutput(chunk_id=chunk_key, response=raw,
                            unique_entities=list(dict.fromkeys(entities)), metadata=metadata)

    client.openie.ner = recover
    return recovered


def install_bounded_triple_recovery(client: Any) -> list[dict[str, Any]]:
    """Retry truncated/invalid triples once with a compact, strict JSON request."""
    from hipporag.information_extraction.openie_openai import _extract_json_list_field
    from hipporag.utils.llm_utils import filter_invalid_triples
    from hipporag.utils.misc_utils import TripleRawOutput

    recovered: list[dict[str, Any]] = []
    original = client.openie.triple_extraction

    def recover(chunk_key: str, passage: str, named_entities: list[str]):
        result = original(chunk_key, passage, named_entities)
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        triples = result.triples if isinstance(result.triples, list) else []
        truncated = metadata.get("finish_reason") == "length"
        unique = {tuple(row) for row in triples if isinstance(row, list)}
        repetitive = len(triples) >= 20 and len(unique) <= 3
        if not metadata.get("error") and not truncated and not repetitive:
            return result
        if len(recovered) >= 2:
            raise RuntimeError("bounded triple recovery limit of two malformed responses exceeded")
        messages = [
            {"role": "system", "content": (
                "Extract only explicit factual relations stated in the passage. Return exactly one JSON object "
                'with key "triples" and a list of [subject, relation, object] string triples. Keep the list '
                "short: at most 12 distinct triples. Do not repeat triples, infer unstated facts, or explain. "
                'If no reliable relation is stated, return {"triples": []}. Do not invent data.'
            )},
            {"role": "user", "content": (
                "Passage:\n" + passage + "\n\nExplicit entities (use only when supported):\n" +
                json.dumps(named_entities, ensure_ascii=False) +
                '\n\nReturn only {"triples": [...]}.'
            )},
        ]
        raw, retry_metadata, cache_hit = client.openie.llm_model.infer(
            messages=messages, max_new_tokens=1024
        )
        extracted = _extract_json_list_field(raw, "triples")
        valid = filter_invalid_triples(triples=extracted)
        if len(valid) > 12:
            raise ValueError("strict triple recovery exceeded the 12-triple response limit")
        normalized = [list(row) for row in valid]
        if any(len(row) != 3 or any(not isinstance(part, str) or not part.strip() for part in row)
               for row in normalized):
            raise ValueError("strict triple recovery returned malformed triples")
        recovery_meta = dict(retry_metadata)
        recovery_meta.update({"cache_hit": cache_hit, "phase6_recovery": "bounded strict-JSON retry"})
        recovered.append({"status": "valid_empty" if not normalized else "valid_triples",
                          "triple_count": len(normalized), "response_char_count": len(raw),
                          "cache_hit": bool(cache_hit), "trigger": "truncated_or_malformed"})
        return TripleRawOutput(chunk_id=chunk_key, response=raw, triples=normalized, metadata=recovery_meta)

    client.openie.triple_extraction = recover
    return recovered


def read_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_no}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"expected an object at {path}:{line_no}")
            rows.append(row)
    return rows


def corpus_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def selected_queries(rows: list[dict[str, Any]], split: str) -> list[dict[str, str]]:
    selected = [row for row in rows if row.get("split") == split]
    if not selected:
        raise ValueError(f"no queries in split {split!r}")
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for row in selected:
        query_id, query = row.get("query_id"), row.get("query")
        if not isinstance(query_id, str) or not query_id.strip() or query_id in seen:
            raise ValueError(f"query_id must be non-empty and unique: {query_id!r}")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"query {query_id} is empty")
        seen.add(query_id)
        output.append({"query_id": query_id, "query": query})
    return output


def apply_llm_request_policy(client: Any, policy: Mapping[str, Any]) -> dict[str, Any]:
    """Apply and validate provider routing options to HippoRAG's isolated clients."""
    if not isinstance(policy, Mapping):
        raise ValueError("LLM request policy must be a JSON object")
    provider_type = policy.get("provider_type")
    if provider_type == "openrouter_deepseek":
        allowed = {"provider_type", "model_id", "base_url", "max_tokens", "reasoning_effort",
                   "provider", "prior_prompt_tokens", "prior_completion_tokens", "cost_cap_usd",
                   "policy_source_url"}
        if set(policy) != allowed:
            raise ValueError(f"DeepSeek request policy keys must be exactly {sorted(allowed)}")
        if policy["base_url"] != "https://openrouter.ai/api/v1":
            raise ValueError("DeepSeek provider base URL does not match the pinned OpenRouter route")
        if policy["model_id"] != "deepseek/deepseek-v4-flash-0731":
            raise ValueError("DeepSeek model ID does not match the pinned route")
        if policy["reasoning_effort"] != "none":
            raise ValueError("Phase 6 DeepSeek policy requires reasoning_effort=none")
        max_tokens = policy["max_tokens"]
        prior_prompt = policy["prior_prompt_tokens"]
        prior_completion = policy["prior_completion_tokens"]
        cost_cap = policy["cost_cap_usd"]
        if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
            raise ValueError("DeepSeek max_tokens must be an integer from 1 to 4096")
        if (type(prior_prompt) is not int or prior_prompt < 0 or
                type(prior_completion) is not int or prior_completion < 0):
            raise ValueError("DeepSeek prior prompt/completion usage must be non-negative integers")
        if type(cost_cap) not in (int, float) or cost_cap <= 0:
            raise ValueError("DeepSeek cost cap must be positive")
        provider = policy["provider"]
        if not isinstance(provider, Mapping) or set(provider) != {"sort", "allow_fallbacks", "max_price"}:
            raise ValueError("DeepSeek provider policy must pin sort, fallback, and price ceilings")
        if provider["sort"] != "price" or provider["allow_fallbacks"] is not False:
            raise ValueError("DeepSeek policy must sort by price without fallback")
        prices = provider["max_price"]
        if not isinstance(prices, Mapping) or set(prices) != {"prompt", "completion"}:
            raise ValueError("DeepSeek max_price must pin prompt and completion ceilings")
        if any(type(value) not in (int, float) or value <= 0 for value in prices.values()):
            raise ValueError("DeepSeek max_price ceilings must be positive numbers")
        if cost_cap > 0.25:
            raise ValueError("DeepSeek USD cap cannot exceed the authorized Phase 6 cap")
        prior_cost = (prior_prompt * float(prices["prompt"]) +
                      prior_completion * float(prices["completion"])) / 1_000_000
        if prior_cost >= cost_cap:
            raise ValueError("DeepSeek prior usage already consumes the configured USD cap")
        seen: set[int] = set()
        for attr in ("llm_model", "extraction_llm", "qa_llm"):
            model = getattr(client, attr, None)
            if model is None or id(model) in seen:
                continue
            seen.add(id(model))
            params = getattr(getattr(model, "llm_config", None), "generate_params", None)
            if not isinstance(params, dict):
                raise RuntimeError(f"HippoRAG {attr} does not expose generation parameters")
            params["max_tokens"] = max_tokens
            params.pop("max_completion_tokens", None)
            params["reasoning_effort"] = "none"
            params["extra_body"] = {"provider": dict(provider)}
        if not seen:
            raise RuntimeError("HippoRAG did not expose any configured LLM client")
        return {
            "provider_type": provider_type, "model_id": policy["model_id"],
            "base_url": policy["base_url"], "max_tokens": max_tokens,
            "reasoning_effort": "none", "provider": {
                "sort": "price", "allow_fallbacks": False,
                "max_price": {k: float(v) for k, v in prices.items()}},
            "quota_scope": "aggregate_cost_cap",
            "prior_prompt_tokens": prior_prompt, "prior_completion_tokens": prior_completion,
            "cost_cap_usd": float(cost_cap),
            "prompt_usd_per_million": float(prices["prompt"]),
            "completion_usd_per_million": float(prices["completion"]),
            "policy_source_url": policy["policy_source_url"],
        }
    if provider_type in {"top_tools_ai", "inception_mercury"}:
        common = {"provider_type", "model_id", "base_url", "max_tokens", "temperature",
                  "fallback_policy", "prior_tokens_used", "policy_source_url"}
        quota_key = "daily_token_limit" if provider_type == "top_tools_ai" else "cumulative_token_limit"
        allowed = common | {quota_key}
        if provider_type == "top_tools_ai":
            allowed.add("quota_window_id")
        if set(policy) != allowed:
            raise ValueError(f"free-provider request policy keys must be exactly {sorted(allowed)}")
        expected_base_urls = {
            "top_tools_ai": "https://top-tools-ai.com/api/v1",
            "inception_mercury": "https://api.inceptionlabs.ai/v1",
        }
        if policy["base_url"] != expected_base_urls[provider_type]:
            raise ValueError("provider base URL does not match the pinned free-provider route")
        expected_models = {"top_tools_ai": "Top-Tools-Ai", "inception_mercury": "mercury-2"}
        if policy["model_id"] != expected_models[provider_type]:
            raise ValueError("model ID does not match the pinned free-provider route")
        if policy["fallback_policy"] != "none":
            raise ValueError("free-provider policy must use one exact route with no fallback")
        if policy["temperature"] != 0:
            raise ValueError("free-provider temperature must be pinned to zero")
        quota_limit = policy[quota_key]
        if type(quota_limit) is not int or quota_limit <= 0:
            raise ValueError("free-provider token limit must be a positive integer")
        if type(policy["prior_tokens_used"]) is not int or policy["prior_tokens_used"] < 0:
            raise ValueError("prior_tokens_used must be a non-negative integer")
        if not isinstance(policy["policy_source_url"], str) or not policy["policy_source_url"].startswith("https://"):
            raise ValueError("free-provider policy source URL must be HTTPS")
        if provider_type == "top_tools_ai" and (not isinstance(policy["quota_window_id"], str) or not policy["quota_window_id"].strip()):
            raise ValueError("Top-Tools quota_window_id is required for its daily quota")
        max_tokens = policy["max_tokens"]
        if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
            raise ValueError("free-provider max_tokens must be an integer from 1 to 4096")
        seen: set[int] = set()
        for attr in ("llm_model", "extraction_llm", "qa_llm"):
            model = getattr(client, attr, None)
            if model is None or id(model) in seen:
                continue
            seen.add(id(model))
            params = getattr(getattr(model, "llm_config", None), "generate_params", None)
            if not isinstance(params, dict):
                raise RuntimeError(f"HippoRAG {attr} does not expose generation parameters")
            params["max_tokens"] = max_tokens
            params.pop("max_completion_tokens", None)
            params["temperature"] = 0
            params.pop("reasoning_effort", None)
            params.pop("extra_body", None)
        if not seen:
            raise RuntimeError("HippoRAG did not expose any configured LLM client")
        return {
            "provider_type": provider_type, "model_id": policy["model_id"],
            "base_url": policy["base_url"], "max_tokens": max_tokens,
            "temperature": 0, "fallback_policy": "none",
            "quota_limit_tokens": quota_limit,
            "quota_scope": "daily" if provider_type == "top_tools_ai" else "cumulative",
            "prior_tokens_used": policy["prior_tokens_used"],
            "quota_window_id": policy.get("quota_window_id"),
            "policy_source_url": policy["policy_source_url"], "reasoning_effort": None,
        }
    allowed = {"model_id", "base_url", "max_tokens", "reasoning_effort", "provider"}
    if set(policy) != allowed:
        raise ValueError(f"LLM request policy keys must be exactly {sorted(allowed)}")
    if not isinstance(policy["model_id"], str) or not policy["model_id"].strip():
        raise ValueError("LLM request policy model_id is required")
    if not isinstance(policy["base_url"], str) or not policy["base_url"].strip():
        raise ValueError("LLM request policy base_url is required")
    max_tokens = policy["max_tokens"]
    if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
        raise ValueError("LLM request policy max_tokens must be an integer from 1 to 4096")
    if policy["reasoning_effort"] != "none":
        raise ValueError("Phase 6 request policy requires reasoning_effort=none")
    provider = policy["provider"]
    if not isinstance(provider, Mapping) or set(provider) != {"sort", "allow_fallbacks", "max_price"}:
        raise ValueError("provider policy must pin sort, allow_fallbacks, and max_price")
    if provider["sort"] != "price" or provider["allow_fallbacks"] is not False:
        raise ValueError("Phase 6 provider policy must sort by price without fallback")
    max_price = provider["max_price"]
    if not isinstance(max_price, Mapping) or set(max_price) != {"prompt", "completion"}:
        raise ValueError("provider max_price must pin prompt and completion ceilings")
    if any(type(value) not in (int, float) or value <= 0 for value in max_price.values()):
        raise ValueError("provider price ceilings must be positive numbers")

    seen: set[int] = set()
    for attr in ("llm_model", "extraction_llm", "qa_llm"):
        model = getattr(client, attr, None)
        if model is None or id(model) in seen:
            continue
        seen.add(id(model))
        params = getattr(getattr(model, "llm_config", None), "generate_params", None)
        if not isinstance(params, dict):
            raise RuntimeError(f"HippoRAG {attr} does not expose generation parameters")
        # The OpenAI SDK merges extra_body fields into the top-level request.
        # Preserve HippoRAG's per-call token overrides while raising its default
        # output cap to match the successfully canaried endpoint.
        params["max_completion_tokens"] = max_tokens
        params["reasoning_effort"] = "none"
        params["extra_body"] = {"provider": dict(provider)}
    if not seen:
        raise RuntimeError("HippoRAG did not expose any configured LLM client")
    return {
        "model_id": policy["model_id"],
        "base_url": policy["base_url"],
        "max_tokens": max_tokens,
        "reasoning_effort": "none",
        "provider": {"sort": "price", "allow_fallbacks": False,
                     "max_price": {k: float(v) for k, v in max_price.items()}},
    }


class ProviderQuotaExceeded(RuntimeError):
    """Raised before a request when the configured provider token budget is exhausted."""


class ProviderRouteMismatch(RuntimeError):
    """Raised when the provider returns a model other than the exact pinned model."""


class PersistentTokenLedger:
    """Cross-process quota ledger; pending calls fail closed after a crash."""

    def __init__(self, path: Path, *, provider: str, scope: str, limit: int,
                 prior_tokens_used: int, window_id: str | None = None):
        if path.is_symlink() or not path.parent.is_dir():
            raise ValueError("quota ledger must be a regular file under an existing isolated directory")
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        if self.lock_path.is_symlink():
            raise ValueError("quota ledger lock path must not be a symlink")
        self.provider = provider
        self.scope = scope
        self.limit = limit
        self.prior_tokens_used = prior_tokens_used
        self.window_id = window_id
        if not self._locked_update(self._initialize):
            raise RuntimeError("quota ledger initialization failed")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": "b2c-token-quota-ledger/1", "provider": self.provider,
                "scope": self.scope, "limit_tokens": self.limit,
                "window_id": self.window_id, "prior_tokens_used": self.prior_tokens_used,
                "actual_prompt_tokens": 0, "actual_completion_tokens": 0,
                "actual_total_tokens": 0, "conservative_reserved_tokens": 0,
                "pending": {},
            }
        if not self.path.is_file():
            raise ValueError("quota ledger path is not a regular file")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        identity = (data.get("provider"), data.get("scope"), data.get("limit_tokens"), data.get("window_id"))
        expected = (self.provider, self.scope, self.limit, self.window_id)
        if identity != expected:
            raise ValueError("existing quota ledger does not match this provider policy/window")
        if self.prior_tokens_used < data["prior_tokens_used"]:
            raise ValueError("configured prior token usage cannot decrease an existing ledger")
        if self.prior_tokens_used > data["prior_tokens_used"]:
            data["prior_tokens_used"] += self.prior_tokens_used - data["prior_tokens_used"]
        return data

    def _write(self, data: dict[str, Any]) -> None:
        descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _locked_update(self, operation):
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            data = self._read()
            result = operation(data)
            self._write(data)
            return result

    def _initialize(self, data: dict[str, Any]) -> bool:
        # Any request left pending by a prior process is charged at its full
        # reservation. We cannot prove whether the provider processed it.
        pending = data.get("pending", {})
        data["conservative_reserved_tokens"] += sum(int(v) for v in pending.values())
        data["pending"] = {}
        return True

    def reserve(self, amount: int) -> str:
        reservation_id = uuid.uuid4().hex
        def add(data: dict[str, Any]) -> str:
            accounted = (data["prior_tokens_used"] + data["actual_total_tokens"]
                         + data["conservative_reserved_tokens"] + sum(data["pending"].values()))
            if accounted + amount > self.limit:
                raise ProviderQuotaExceeded("persistent token ledger stopped request before provider call")
            data["pending"][reservation_id] = amount
            return reservation_id
        return self._locked_update(add)

    def complete(self, reservation_id: str, *, prompt: int, completion: int, total: int) -> None:
        def apply(data: dict[str, Any]) -> None:
            if reservation_id not in data["pending"]:
                raise RuntimeError("quota reservation is missing from persistent ledger")
            del data["pending"][reservation_id]
            data["actual_prompt_tokens"] += prompt
            data["actual_completion_tokens"] += completion
            data["actual_total_tokens"] += total
        self._locked_update(apply)

    def abandon(self, reservation_id: str) -> None:
        def apply(data: dict[str, Any]) -> None:
            amount = data["pending"].pop(reservation_id, None)
            if amount is not None:
                data["conservative_reserved_tokens"] += amount
        self._locked_update(apply)

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        def read(data: dict[str, Any]) -> None:
            result.update(data)
        self._locked_update(read)
        accounted = (result["prior_tokens_used"] + result["actual_total_tokens"]
                     + result["conservative_reserved_tokens"] + sum(result["pending"].values()))
        return {
            "provider": self.provider, "scope": self.scope,
            "window_id": self.window_id, "limit_tokens": self.limit,
            "prior_tokens_used": result["prior_tokens_used"],
            "actual_prompt_tokens": result["actual_prompt_tokens"],
            "actual_completion_tokens": result["actual_completion_tokens"],
            "actual_total_tokens": result["actual_total_tokens"],
            "conservative_reserved_tokens": result["conservative_reserved_tokens"],
            "pending_reserved_tokens": sum(result["pending"].values()),
            "accounted_tokens": accounted,
            "remaining_tokens": max(0, self.limit - accounted),
        }



class PersistentCostLedger:
    """USD cap with separate prompt/completion ceilings and crash-safe reservations."""
    def __init__(self, path: Path, *, provider: str, cost_cap_usd: float,
                 prompt_usd_per_million: float, completion_usd_per_million: float,
                 prior_prompt_tokens: int, prior_completion_tokens: int):
        if path.is_symlink() or not path.parent.is_dir():
            raise ValueError("cost ledger must be regular and under an existing isolated directory")
        if (cost_cap_usd <= 0 or prompt_usd_per_million <= 0 or completion_usd_per_million <= 0
                or min(prior_prompt_tokens, prior_completion_tokens) < 0):
            raise ValueError("cost cap, prices, and prior usage must be valid")
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        if self.lock_path.is_symlink():
            raise ValueError("cost ledger lock path must not be a symlink")
        self.provider = provider
        self.limit_microusd = math.ceil(cost_cap_usd * 1_000_000)
        self.prompt_rate = float(prompt_usd_per_million)
        self.completion_rate = float(completion_usd_per_million)
        self.prior_prompt_tokens = prior_prompt_tokens
        self.prior_completion_tokens = prior_completion_tokens
        self._locked_update(self._initialize)

    def _new(self):
        return {
            "schema_version": "b2c-cost-quota-ledger/1", "provider": self.provider,
            "limit_microusd": self.limit_microusd, "prompt_usd_per_million": self.prompt_rate,
            "completion_usd_per_million": self.completion_rate,
            "prior_prompt_tokens": self.prior_prompt_tokens,
            "prior_completion_tokens": self.prior_completion_tokens,
            "actual_prompt_tokens": 0, "actual_completion_tokens": 0, "actual_microusd": 0,
            "conservative_reserved_microusd": 0, "pending": {},
        }

    def _read(self):
        if not self.path.exists():
            return self._new()
        if not self.path.is_file():
            raise ValueError("cost ledger path is not a regular file")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        identity = (data.get("provider"), data.get("limit_microusd"),
                    data.get("prompt_usd_per_million"), data.get("completion_usd_per_million"))
        expected = (self.provider, self.limit_microusd, self.prompt_rate, self.completion_rate)
        if identity != expected:
            raise ValueError("existing cost ledger does not match this provider policy")
        if (self.prior_prompt_tokens < data["prior_prompt_tokens"] or
                self.prior_completion_tokens < data["prior_completion_tokens"]):
            raise ValueError("configured prior provider usage cannot decrease an existing ledger")
        data["prior_prompt_tokens"] = self.prior_prompt_tokens
        data["prior_completion_tokens"] = self.prior_completion_tokens
        return data

    def _write(self, data):
        descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _locked_update(self, operation):
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            data = self._read()
            result = operation(data)
            self._write(data)
            return result

    def _cost(self, prompt, completion):
        usd = (prompt * self.prompt_rate + completion * self.completion_rate) / 1_000_000
        return math.ceil(usd * 1_000_000)

    def _initialize(self, data):
        data["conservative_reserved_microusd"] += sum(int(v) for v in data.get("pending", {}).values())
        data["pending"] = {}

    def reserve(self, prompt_bound, completion_bound):
        amount = self._cost(prompt_bound, completion_bound)
        reservation_id = uuid.uuid4().hex
        def add(data):
            used = self._cost(
                data["prior_prompt_tokens"] + data["actual_prompt_tokens"],
                data["prior_completion_tokens"] + data["actual_completion_tokens"],
            ) + data["conservative_reserved_microusd"] + sum(data["pending"].values())
            if used + amount > self.limit_microusd:
                raise ProviderQuotaExceeded("persistent USD ledger stopped request before provider call")
            data["pending"][reservation_id] = amount
            return reservation_id
        return self._locked_update(add)

    def complete(self, reservation_id, *, prompt, completion, total):
        def apply(data):
            if reservation_id not in data["pending"]:
                raise RuntimeError("cost reservation is missing from persistent ledger")
            del data["pending"][reservation_id]
            data["actual_prompt_tokens"] += prompt
            data["actual_completion_tokens"] += completion
            data["actual_microusd"] += self._cost(prompt, completion)
        self._locked_update(apply)

    def abandon(self, reservation_id):
        def apply(data):
            amount = data["pending"].pop(reservation_id, None)
            if amount is not None:
                data["conservative_reserved_microusd"] += amount
        self._locked_update(apply)

    def snapshot(self):
        result = {}
        def read(data):
            result.update(data)
        self._locked_update(read)
        accounted = self._cost(
            result["prior_prompt_tokens"] + result["actual_prompt_tokens"],
            result["prior_completion_tokens"] + result["actual_completion_tokens"],
        ) + result["conservative_reserved_microusd"] + sum(result["pending"].values())
        return {
            **result,
            "actual_cost_usd": result["actual_microusd"] / 1_000_000,
            "accounted_cost_usd": accounted / 1_000_000,
            "remaining_cost_usd": max(0, self.limit_microusd - accounted) / 1_000_000,
            "pending_reserved_microusd": sum(result["pending"].values()),
        }


def instrument_openai_usage(client: Any, *, daily_token_limit: int | None = None,
                            expected_model: str | None = None,
                            quota_ledger: PersistentTokenLedger | None = None) -> list[dict[str, Any]]:
    """Capture token/cost metadata only; never persist prompts or responses."""
    records: list[dict[str, Any]] = []
    accounting_lock = threading.Lock()
    reserved_tokens = 0
    used_tokens = 0
    seen: set[int] = set()
    for attr in ("llm_model", "extraction_llm", "qa_llm"):
        model = getattr(client, attr, None)
        openai_client = getattr(model, "openai_client", None)
        if openai_client is None or id(openai_client) in seen:
            continue
        seen.add(id(openai_client))
        resource = openai_client.chat.completions
        original = resource.create

        def tracked_create(*args: Any, _original=original, **kwargs: Any) -> Any:
            nonlocal reserved_tokens, used_tokens
            reservation = 0
            reservation_id: str | None = None
            if daily_token_limit is not None or quota_ledger is not None:
                messages = kwargs.get("messages", [])
                prompt_reserve = len(json.dumps(messages, ensure_ascii=False, default=str).encode("utf-8"))
                output_reserve = int(kwargs.get("max_completion_tokens", kwargs.get("max_tokens", 4096)))
                reservation = prompt_reserve + output_reserve
                if isinstance(quota_ledger, PersistentCostLedger):
                    reservation_id = quota_ledger.reserve(prompt_reserve, output_reserve)
                elif quota_ledger is not None:
                    reservation_id = quota_ledger.reserve(reservation)
                else:
                    with accounting_lock:
                        if used_tokens + reserved_tokens + reservation > daily_token_limit:
                            raise ProviderQuotaExceeded("token quota guard stopped request before provider call")
                        reserved_tokens += reservation
            try:
                response = _original(*args, **kwargs)
            except BaseException:
                if reservation_id is not None:
                    quota_ledger.abandon(reservation_id)
                elif reservation:
                    with accounting_lock:
                        reserved_tokens -= reservation
                raise
            usage = getattr(response, "usage", None)
            if usage is None:
                if reservation_id is not None:
                    quota_ledger.abandon(reservation_id)
                elif reservation:
                    with accounting_lock:
                        reserved_tokens -= reservation
                raise RuntimeError("provider response omitted usage accounting")
            prompt = getattr(usage, "prompt_tokens", None)
            completion = getattr(usage, "completion_tokens", None)
            total = getattr(usage, "total_tokens", None)
            if not all(type(value) is int and value >= 0 for value in (prompt, completion, total)):
                if reservation_id is not None:
                    quota_ledger.abandon(reservation_id)
                elif reservation:
                    with accounting_lock:
                        reserved_tokens -= reservation
                raise RuntimeError("provider response omitted valid token usage fields")
            extras = getattr(usage, "model_extra", None) or {}
            cost = getattr(usage, "cost", None)
            record = {
                "model": getattr(response, "model", None),
                "request_id": getattr(response, "_request_id", None),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
            }
            cost_details = extras.get("cost_details")
            if isinstance(cost_details, Mapping):
                record["cost_details"] = dict(cost_details)
            with accounting_lock:
                if quota_ledger is not None and reservation_id is not None:
                    quota_ledger.complete(reservation_id, prompt=prompt, completion=completion, total=total)
                elif reservation:
                    reserved_tokens -= reservation
                    used_tokens += total
                records.append(record)
            if expected_model is not None and record["model"] != expected_model:
                raise ProviderRouteMismatch("provider response model did not match the pinned model")
            return response

        resource.create = tracked_create
    if not seen:
        raise RuntimeError("HippoRAG did not expose an OpenAI-compatible client for usage accounting")
    return records


def aggregate_usage(records: list[dict[str, Any]], *, daily_token_limit: int | None = None,
                    quota_stopped: bool = False,
                    quota_ledger: PersistentTokenLedger | PersistentCostLedger | None = None) -> dict[str, Any]:
    costs = [row["cost_usd"] for row in records]
    ledger_snapshot = quota_ledger.snapshot() if quota_ledger is not None else None
    if quota_stopped:
        quota_status = "stopped_by_guard_or_provider"
    elif (ledger_snapshot and "remaining_cost_usd" in ledger_snapshot
          and ledger_snapshot["remaining_cost_usd"] <= 0):
        quota_status = "limit_reached"
    elif (ledger_snapshot and "remaining_tokens" in ledger_snapshot
          and ledger_snapshot["remaining_tokens"] <= 0):
        quota_status = "limit_reached"
    elif daily_token_limit is not None and sum(row["total_tokens"] or 0 for row in records) >= daily_token_limit:
        quota_status = "limit_reached"
    else:
        quota_status = "within_limit"
    return {
        "request_count": len(records),
        "prompt_tokens": sum(row["prompt_tokens"] or 0 for row in records),
        "completion_tokens": sum(row["completion_tokens"] or 0 for row in records),
        "total_tokens": sum(row["total_tokens"] or 0 for row in records),
        "observed_cost_usd": sum(costs) if costs and all(cost is not None for cost in costs) else None,
        "cost_complete": bool(costs) and all(cost is not None for cost in costs),
        "daily_token_limit": daily_token_limit,
        "quota_status": quota_status,
        "requests": records,
        "quota_ledger": ledger_snapshot,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    chunk_paths = list(args.chunks)
    dataset_path: Path = args.dataset
    output: Path = args.output
    output_root = args.output_root.resolve()
    if args.top_k < 1:
        raise ValueError("top_k must be positive")
    if output_root.is_symlink() or not output_root.is_dir():
        raise ValueError("output root must be an existing, non-symlink isolated mount")
    if output.is_symlink() or output.exists():
        raise ValueError("output file must be new and must not be a symlink")
    if not output.parent.is_dir():
        raise ValueError("output parent must be an existing directory")
    try:
        output.parent.resolve().relative_to(output_root)
    except ValueError as exc:
        raise ValueError("output parent must be inside the dedicated isolated output root") from exc
    if output.resolve().parent != output.parent.resolve():
        raise ValueError("output path must not traverse symlinked directories")
    for path in [*chunk_paths, dataset_path]:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"input must be an existing regular non-symlink file: {path}")

    chunks = read_jsonl(chunk_paths)
    queries = selected_queries(read_jsonl([dataset_path]), args.split)
    if len({row.get("chunk_id") for row in chunks}) != len(chunks):
        raise ValueError("duplicate chunk IDs in frozen corpus")

    config = RuntimeConfig(
        save_dir=args.save_dir,
        allowed_root=args.save_root,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        embedding_model=args.embedding_model,
        embedding_base_url=args.embedding_base_url,
        embedding_provider=args.embedding_provider,
        llm_api_key_env=args.llm_api_key_env,
        embedding_api_key_env=args.embedding_api_key_env,
    )
    client, provider_chunk_type = create_isolated_runtime(config)
    llm_cache_reuse_sha256 = None
    if args.reuse_llm_cache is not None:
        llm_cache_reuse_sha256 = reuse_llm_cache(client, args.reuse_llm_cache, args.save_root)
    request_policy = None
    if args.request_policy is not None:
        policy = json.loads(args.request_policy.read_text(encoding="utf-8"))
        if policy.get("model_id") != args.llm_model or policy.get("base_url") != args.llm_base_url:
            raise ValueError("LLM model/base URL do not match the pinned request policy")
        request_policy = apply_llm_request_policy(client, policy)
    quota_ledger = None
    if request_policy is not None:
        provider_type = request_policy["provider_type"]
        quota_path = output_root / f"phase6-{provider_type}-cost-ledger.json"
        if provider_type == "openrouter_deepseek":
            quota_ledger = PersistentCostLedger(
                quota_path, provider=provider_type,
                cost_cap_usd=request_policy["cost_cap_usd"],
                prompt_usd_per_million=request_policy["prompt_usd_per_million"],
                completion_usd_per_million=request_policy["completion_usd_per_million"],
                prior_prompt_tokens=request_policy["prior_prompt_tokens"],
                prior_completion_tokens=request_policy["prior_completion_tokens"],
            )
        else:
            quota_ledger = PersistentTokenLedger(
                quota_path, provider=provider_type,
                scope=request_policy["quota_scope"],
                limit=request_policy["quota_limit_tokens"],
                prior_tokens_used=request_policy["prior_tokens_used"],
                window_id=request_policy.get("quota_window_id"),
            )
    expected_model = request_policy.get("model_id") if request_policy else None
    llm_usage_records = instrument_openai_usage(
        client, expected_model=expected_model, quota_ledger=quota_ledger
    )
    ner_recoveries = install_bounded_ner_recovery(client) if (
        request_policy and request_policy.get("provider_type") == "openrouter_deepseek"
    ) else []
    triple_recoveries = install_bounded_triple_recovery(client) if (
        request_policy and request_policy.get("provider_type") == "openrouter_deepseek"
    ) else []
    documents, sources = to_hipporag_chunks(
        chunks, scope_id=args.scope_id, chunk_type=provider_chunk_type
    )
    backend = HippoRAG2Backend(client, sources=sources)
    print(json.dumps({"event": "index_started", "chunk_count": len(documents)}), file=sys.stderr, flush=True)
    index_start = time.perf_counter()
    index_seconds: float | None = None
    rankings: dict[str, list[str]] = {}
    query_seconds: dict[str, float] = {}
    quota_stopped = False
    provider_failure: dict[str, Any] | None = None
    try:
        backend.index(documents)
        index_seconds = time.perf_counter() - index_start
        print(json.dumps({"event": "index_finished", "index_seconds": index_seconds}), file=sys.stderr, flush=True)
        for item in queries:
            query_start = time.perf_counter()
            hits = backend.retrieve(item["query"], top_k=args.top_k)
            query_seconds[item["query_id"]] = time.perf_counter() - query_start
            rankings[item["query_id"]] = [hit.chunk_id for hit in hits]
            if len(query_seconds) % 5 == 0 or len(query_seconds) == len(queries):
                print(json.dumps({"event": "retrieval_progress", "completed": len(query_seconds),
                                  "total": len(queries)}), file=sys.stderr, flush=True)
    except ProviderQuotaExceeded:
        quota_stopped = True
        print(json.dumps({"event": "quota_stopped", "completed_queries": len(query_seconds)}), file=sys.stderr, flush=True)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        text = str(exc).lower()
        quota_stopped = status == 429 or "quota" in text or "rate limit" in text
        provider_failure = {
            "error_type": type(exc).__name__,
            "http_status": status,
            "category": "quota_or_rate_limit" if quota_stopped else "provider_error",
        }
        print(json.dumps({"event": "provider_stopped", **provider_failure,
                          "completed_queries": len(query_seconds)}), file=sys.stderr, flush=True)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    auto_labeled = any(row.get("query_adjudication") != "human_adjudicated" for row in read_jsonl([dataset_path]) if row.get("split") == args.split)
    result = {
        "schema_version": "b2c-phase6-ranking/1",
        "retriever": f"hipporag-{HIPPORAG_VERSION}@{HIPPORAG_COMMIT[:12]}",
        "source_commit": HIPPORAG_COMMIT,
        "source_package_version": HIPPORAG_VERSION,
        "corpus_sha256": corpus_fingerprint(chunk_paths),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "split": args.split,
        "query_count": len(queries),
        "run_status": "quota_stopped" if quota_stopped else ("provider_error" if provider_failure else "complete"),
        "stop_reason": provider_failure,
        "top_k": args.top_k,
        "rankings": rankings,
        "timing": {
            "index_seconds": index_seconds,
            "query_seconds_by_id": query_seconds,
            "mean_query_seconds": sum(query_seconds.values()) / len(query_seconds) if query_seconds else None,
            "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        },
        "label_provenance": "model_auto_validated_non_gold" if auto_labeled else "human_adjudicated",
        "gold_eligible": not auto_labeled,
        "llm_usage": aggregate_usage(llm_usage_records, quota_stopped=quota_stopped,
                                     quota_ledger=quota_ledger),
        "openie_recovery": {
            "bounded_strict_json_retries": ner_recoveries,
            "bounded_triple_retries": triple_recoveries,
        },
        "llm_cache_reuse_sha256": llm_cache_reuse_sha256,
        "model_ids": {
            "llm": args.llm_model,
            "embedding": args.embedding_model,
            "embedding_provider": args.embedding_provider,
            "request_policy": request_policy,
        },
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        # link() is atomic and refuses to overwrite a result created by a
        # concurrent run; output artifacts are immutable once published.
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, nargs="+", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--split", default="development")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--save-dir", type=Path, required=True)
    parser.add_argument("--save-root", type=Path, required=True)
    parser.add_argument("--llm-model", required=True)
    parser.add_argument("--llm-base-url", required=True)
    parser.add_argument("--llm-api-key-env", default="B2C_PHASE6_LLM_API_KEY")
    parser.add_argument("--request-policy", type=Path, help="pinned, non-secret provider request policy JSON")
    parser.add_argument("--reuse-llm-cache", type=Path,
                        help="reuse a verified response cache from the isolated scratch root")
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--embedding-provider", choices=("openai", "transformers"), default="openai")
    parser.add_argument("--embedding-base-url")
    parser.add_argument("--embedding-api-key-env", default="B2C_PHASE6_EMBEDDING_API_KEY")
    return parser.parse_args()


if __name__ == "__main__":
    result = run(parse_args())
    print(json.dumps({key: result[key] for key in ("retriever", "query_count", "corpus_sha256", "dataset_sha256", "gold_eligible", "timing", "llm_usage")}, indent=2))
