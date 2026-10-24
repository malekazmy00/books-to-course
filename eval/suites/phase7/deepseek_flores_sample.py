#!/usr/bin/env python3
"""One explicitly capped DeepSeek comparison after the free Qwen route was rate-limited."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

MODEL_ID = "deepseek/deepseek-v4-flash-0731"
BASE_URL = "https://openrouter.ai/api/v1"
COST_CAP_USD = 0.01
MAX_TOKENS = 4096


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, obj: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--max-input-bytes", type=int, default=32_000)
    args = parser.parse_args()
    api_key = sys.stdin.read().strip()
    if not api_key:
        raise SystemExit("OpenRouter API key must be provided on stdin")

    sources_all = args.source.read_text(encoding="utf-8").splitlines()
    refs_all = args.reference.read_text(encoding="utf-8").splitlines()
    if len(sources_all) != len(refs_all) or not 1 <= args.sample_size <= len(sources_all):
        raise SystemExit("FLORES sample configuration is invalid")
    if args.sample_size == 1:
        indices = [len(sources_all) // 2]
    else:
        indices = sorted({round(i * (len(sources_all) - 1) / (args.sample_size - 1))
                          for i in range(args.sample_size)})
    sources = [sources_all[i] for i in indices]
    refs = [refs_all[i] for i in indices]
    prompt = (
        "Translate each English sentence into Modern Standard Arabic. Preserve meaning and names. "
        "Preserve every numeric digit sequence exactly; do not spell digits out. "
        f"Return exactly {len(sources)} numbered lines (1 through {len(sources)}), no explanations.\n\n"
        + "\n".join(f"{n}. {text}" for n, text in enumerate(sources, 1))
    )
    prompt_bytes = len(prompt.encode("utf-8"))
    if prompt_bytes > args.max_input_bytes:
        raise SystemExit("prompt exceeds the bounded input allowance")

    headers = {"Authorization": f"Bearer {api_key}"}
    model_response = httpx.get(f"{BASE_URL}/models", headers=headers, timeout=30)
    if model_response.status_code != 200:
        raise SystemExit(f"model preflight HTTP {model_response.status_code}")
    models = model_response.json().get("data", [])
    model = next((m for m in models if m.get("id") == MODEL_ID), None)
    if model is None:
        raise SystemExit("authorized DeepSeek model is unavailable")
    pricing = model.get("pricing", {})
    prompt_rate = float(pricing.get("prompt", -1))
    completion_rate = float(pricing.get("completion", -1))
    if prompt_rate < 0 or completion_rate < 0:
        raise SystemExit("DeepSeek model pricing is unavailable; no request sent")
    reserve_bound = prompt_bytes * prompt_rate + MAX_TOKENS * completion_rate
    if reserve_bound > COST_CAP_USD:
        raise SystemExit(f"request cost bound ${reserve_bound:.8f} exceeds $0.01 cap")

    key_response = httpx.get(f"{BASE_URL}/auth/key", headers=headers, timeout=30)
    if key_response.status_code != 200:
        raise SystemExit(f"OpenRouter key balance check HTTP {key_response.status_code}")
    key_info = key_response.json().get("data", {})
    remaining = key_info.get("limit_remaining")
    if not isinstance(remaining, (int, float)) or remaining < reserve_bound:
        raise SystemExit("OpenRouter remaining balance is unavailable or below request bound")

    body = {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": "none",
        "stream": False,
    }
    started = time.monotonic()
    try:
        response = httpx.post(f"{BASE_URL}/chat/completions", json=body,
            headers={**headers, "Content-Type": "application/json"}, timeout=180)
    except httpx.HTTPError as exc:
        payload = {"status": "transport_error", "model_id": MODEL_ID,
                   "error_type": type(exc).__name__, "reserved_max_cost_usd": reserve_bound,
                   "phase7_cost_cap_usd": COST_CAP_USD}
        save(args.raw_output, payload)
        print(json.dumps({k: payload[k] for k in payload if k not in {"raw_response"}}))
        raise SystemExit(2) from None
    elapsed = time.monotonic() - started
    if response.status_code >= 400:
        try:
            error = response.json().get("error", {})
            safe_error = {k: error[k] for k in ("code", "type", "param")
                          if isinstance(error.get(k), (str, int, float))}
        except (ValueError, AttributeError):
            safe_error = {}
        payload = {"status": "http_error", "http_status": response.status_code,
                   "error_metadata": safe_error, "model_id": MODEL_ID,
                   "reserved_max_cost_usd": reserve_bound,
                   "phase7_cost_cap_usd": COST_CAP_USD, "elapsed_s": elapsed}
        save(args.raw_output, payload)
        print(json.dumps(payload, sort_keys=True))
        raise SystemExit(2)

    provider = response.json()
    choice = (provider.get("choices") or [{}])[0]
    usage = provider.get("usage") or {}
    raw_text = (choice.get("message") or {}).get("content") or ""
    actual_cost = usage.get("cost", provider.get("cost"))
    if actual_cost is None and isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
        actual_cost = (usage["prompt_tokens"] * prompt_rate
                       + usage["completion_tokens"] * completion_rate)
    payload = {
        "status": "response_saved", "model_id": MODEL_ID,
        "response_model": provider.get("model"),
        "request": {"schema": None, "reasoning_effort": "none", "temperature": 0.0,
                    "max_tokens": MAX_TOKENS, "indices": indices, "elapsed_s": elapsed},
        "sources": sources, "references": refs, "raw_response": raw_text,
        "raw_provider_response": response.text, "finish_reason": choice.get("finish_reason"),
        "usage": usage, "provider_reported_cost": usage.get("cost", provider.get("cost")),
        "estimated_cost_usd": actual_cost, "prompt_price_per_token": prompt_rate,
        "completion_price_per_token": completion_rate,
        "reserved_max_cost_usd": reserve_bound, "phase7_cost_cap_usd": COST_CAP_USD,
        "balance_remaining_before_usd": remaining, "balance_limit_usd": key_info.get("limit"),
        "source_sha256": digest(args.source), "reference_sha256": digest(args.reference),
    }
    save(args.raw_output, payload)
    print(json.dumps({"status": payload["status"], "model_id": MODEL_ID,
        "response_model": payload["response_model"], "sample_size": len(sources),
        "max_tokens": MAX_TOKENS, "reserved_max_cost_usd": reserve_bound,
        "reported_cost_usd": payload["provider_reported_cost"],
        "estimated_cost_usd": actual_cost,
        "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
        "finish_reason": payload["finish_reason"], "elapsed_s": round(elapsed, 3),
        "raw_output_path": str(args.raw_output), "raw_output_mode": "0600"}, sort_keys=True))


if __name__ == "__main__":
    main()
