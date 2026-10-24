#!/usr/bin/env python3
"""One-request OpenRouter free-model FLORES translation canary; no schema or retry."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
from b2c_core import Settings
from b2c_llm_gateway import PostgresOpenRouterBudget

MODEL_ID = "qwen/qwen3.8-27b:free"
BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 4096


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight(api_key: str) -> dict[str, object]:
    response = httpx.get(f"{BASE_URL}/models",
                         headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter model preflight HTTP {response.status_code}")
    models = response.json().get("data", [])
    model = next((m for m in models if m.get("id") == MODEL_ID), None)
    if not model:
        raise RuntimeError("configured free Qwen model is unavailable")
    pricing = model.get("pricing", {})
    if pricing.get("prompt") != "0" or pricing.get("completion") != "0":
        raise RuntimeError("configured Qwen model is not listed at zero input/output price")
    return {"status_code": response.status_code, "model_available": True,
            "model_id": MODEL_ID, "zero_pricing": True}


def usage_state(settings: Settings) -> tuple[int, int]:
    day = datetime.now(timezone.utc).date()
    with psycopg.connect(settings.pg_dsn) as conn:
        exists = conn.execute("SELECT to_regclass(%s)",
                              ("public.openrouter_free_request_budget",)).fetchone()[0]
        row = conn.execute(
            "SELECT requests_reserved FROM openrouter_free_request_budget WHERE utc_day=%s", (day,)
        ).fetchone() if exists else None
    return (int(row[0]) if row else 0, settings.openrouter_free_daily_limit)


def save(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=3)
    args = parser.parse_args()
    api_key = sys.stdin.read().strip()
    if not api_key:
        raise SystemExit("OpenRouter API key must be provided on stdin")

    source_lines = args.source.read_text(encoding="utf-8").splitlines()
    reference_lines = args.reference.read_text(encoding="utf-8").splitlines()
    if len(source_lines) != len(reference_lines):
        raise SystemExit("FLORES source/reference line counts do not match")
    if args.sample_size < 1 or args.sample_size > len(source_lines):
        raise SystemExit("sample-size out of range")
    if args.sample_size == 1:
        sample_indices = [len(source_lines) // 2]
    else:
        sample_indices = sorted({round(i * (len(source_lines) - 1) / (args.sample_size - 1))
                                for i in range(args.sample_size)})
    preflight_result = preflight(api_key)

    settings = Settings()
    if settings.openrouter_free_daily_limit > 40 or settings.openrouter_free_daily_limit < 1:
        raise SystemExit("B2C OpenRouter free request cap must be within 1..40 per UTC day")
    reserved_before, limit = usage_state(settings)
    if reserved_before >= limit:
        raise SystemExit(f"daily free request budget exhausted: {reserved_before}/{limit}")
    budget = PostgresOpenRouterBudget(
        settings.pg_dsn,
        daily_limit=limit,
        min_interval_s=settings.openrouter_free_min_interval_seconds,
    )
    source_rows = [source_lines[i] for i in sample_indices]
    reference_rows = [reference_lines[i] for i in sample_indices]
    prompt = (
        "Translate each English sentence into Modern Standard Arabic. Preserve meaning and names. Preserve every numeric digit sequence exactly; do not spell digits out. "
        f"Return exactly {len(source_rows)} numbered lines (1 through {len(source_rows)}), no explanations.\n\n"
        + "\n".join(f"{n}. {text}" for n, text in enumerate(source_rows, 1))
    )
    body = {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
        "reasoning": {"effort": "none", "exclude": True},
        "stream": False,
    }
    # Reserve atomically before the one and only inference request; failed calls count too.
    budget.reserve()
    started = time.monotonic()
    try:
        response = httpx.post(f"{BASE_URL}/chat/completions", json=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=180)
    except httpx.HTTPError as exc:
        payload = {
            "status": "transport_error", "model_id": MODEL_ID,
            "error_type": type(exc).__name__, "daily_reserved_before": reserved_before,
            "daily_limit": limit, "api_requests_reserved": 1,
            "elapsed_s": time.monotonic() - started,
        }
        save(args.raw_output, payload)
        print(json.dumps({k: payload[k] for k in (
            "status", "model_id", "error_type", "daily_reserved_before", "daily_limit",
            "api_requests_reserved", "elapsed_s")}, sort_keys=True))
        raise SystemExit(2) from None

    elapsed = time.monotonic() - started
    if response.status_code >= 400:
        try:
            error = response.json().get("error", {})
            safe_error = {k: error[k] for k in ("code", "type", "param")
                          if isinstance(error.get(k), (str, int, float))}
        except (ValueError, AttributeError):
            safe_error = {}
        payload = {
            "status": "http_error", "http_status": response.status_code,
            "error_metadata": safe_error, "model_id": MODEL_ID,
            "daily_reserved_before": reserved_before, "daily_limit": limit,
            "api_requests_reserved": 1, "elapsed_s": elapsed,
        }
        save(args.raw_output, payload)
        print(json.dumps({k: payload[k] for k in (
            "status", "http_status", "error_metadata", "model_id", "daily_reserved_before",
            "daily_limit", "api_requests_reserved", "elapsed_s")}, sort_keys=True))
        raise SystemExit(2)

    provider_body = response.json()
    choice = (provider_body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    raw_text = message.get("content") or ""
    usage = provider_body.get("usage") or {}
    payload = {
        "status": "response_saved",
        "model_id": MODEL_ID,
        "response_model": provider_body.get("model"),
        "preflight": preflight_result,
        "request": {"schema": None, "temperature": 0.0, "reasoning_effort": "none",
                    "max_tokens": MAX_TOKENS, "indices": list(sample_indices),
                    "elapsed_s": elapsed},
        "sources": source_rows,
        "references": reference_rows,
        "raw_response": raw_text,
        "raw_provider_response": response.text,
        "finish_reason": choice.get("finish_reason"),
        "usage": usage,
        "provider_reported_cost": usage.get("cost", provider_body.get("cost")),
        "api_requests_reserved": 1,
        "daily_reserved_before": reserved_before,
        "daily_limit": limit,
        "source_sha256": sha256(args.source),
        "reference_sha256": sha256(args.reference),
    }
    save(args.raw_output, payload)
    print(json.dumps({
        "status": payload["status"], "model_id": MODEL_ID,
        "response_model": payload["response_model"],
        "api_requests_reserved": 1, "daily_reserved_before": reserved_before,
        "daily_limit": limit, "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "provider_reported_cost": payload["provider_reported_cost"],
        "finish_reason": payload["finish_reason"], "elapsed_s": round(elapsed, 3),
        "raw_output_path": str(args.raw_output), "raw_output_mode": "0600",
    }, sort_keys=True))


if __name__ == "__main__":
    main()



