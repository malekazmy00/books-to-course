#!/usr/bin/env python3
"""Single-request, zero-priced OpenRouter extraction canary for Phase 4."""
from __future__ import annotations
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL = "qwen/qwen3.8-27b:free"
MODEL_URL = "https://openrouter.ai/api/v1/models"
KEY_URL = "https://openrouter.ai/api/v1/key"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
KEY_PATH = Path("/root/.config/book2course/openrouter-api-key")
CHUNK_PATH = Path("/mnt/wsl/b2cdata/phase4-kg-eval-20261004/openintro-statistics-ch03-8-chunks.jsonl")
OUT_DIR = Path("/mnt/wsl/b2cdata/phase4-lightrag-openrouter-20261005")
OUT_PATH = OUT_DIR / "qwen-free-ch03-chunk01-canary-attempt3.json"
MAX_TOKENS = 4096

def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def content_to_text(content: Any) -> str:
    """Normalize OpenAI/OpenRouter string or content-block-array shapes."""
    if isinstance(content, str):
        return content
    if content is None:
        raise ValueError("assistant message content is null")
    if not isinstance(content, list):
        raise TypeError(f"message.content must be str or list, got {type(content).__name__}")
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") == "text" and isinstance(block.get("content"), str):
                parts.append(block["content"])
    if not parts:
        raise ValueError("message.content array contained no text blocks")
    return "\n".join(parts)

def parse_json_object(text: str) -> dict[str, Any]:
    tick = chr(96)
    cleaned = text.strip()
    cleaned = re.sub("^" + tick * 3 + r"(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*" + tick * 3 + "$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("extraction response JSON root must be an object")
    return value

def validate_extraction(value: dict[str, Any], source_text: str) -> dict[str, Any]:
    entities, relations = value.get("entities"), value.get("relations")
    if not isinstance(entities, list) or not isinstance(relations, list):
        raise ValueError("JSON must contain entities and relations arrays")
    echecks, rchecks = [], []
    for item in entities:
        if not isinstance(item, dict):
            raise ValueError("each entity must be an object")
        quote = item.get("source_quote")
        echecks.append(isinstance(quote, str) and bool(quote.strip()) and quote in source_text)
    for item in relations:
        if not isinstance(item, dict):
            raise ValueError("each relation must be an object")
        quote = item.get("source_quote")
        rchecks.append(isinstance(quote, str) and bool(quote.strip()) and quote in source_text)
    return {"entities": len(entities), "relations": len(relations),
            "entity_quotes_present_and_exact": sum(echecks), "entity_quote_count": len(echecks),
            "relation_quotes_present_and_exact": sum(rchecks), "relation_quote_count": len(rchecks),
            "all_quotes_exact": all(echecks + rchecks)}

def fetch_json(url: str, key: str) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, None

def zero_price_model_present(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    for model in data.get("data", []):
        if model.get("id") == MODEL:
            pricing = model.get("pricing") or {}
            try:
                return float(pricing.get("prompt", -1)) == 0 and float(pricing.get("completion", -1)) == 0
            except (TypeError, ValueError):
                return False
    return False

def key_has_remaining_limit(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    details = data.get("data", data)
    try:
        return float(details.get("limit_remaining", 0)) > 0
    except (TypeError, ValueError):
        return False

def scrub_secrets(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, list):
        return [scrub_secrets(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: scrub_secrets(item, secret) for key, item in value.items()}
    return value

def save_artifact(artifact: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main() -> int:
    if not KEY_PATH.is_file():
        print("stop: authorized key file missing")
        return 3
    key = KEY_PATH.read_text(encoding="utf-8").strip()
    status, models = fetch_json(MODEL_URL, key)
    if status != 200 or not zero_price_model_present(models):
        print(f"stop: model-list status={status}; exact $0/$0 model unavailable")
        return 4
    key_status, key_data = fetch_json(KEY_URL, key)
    if key_status != 200 or not key_has_remaining_limit(key_data):
        print(f"stop: key-status HTTP={key_status}; no remaining request limit confirmed")
        return 5
    with CHUNK_PATH.open(encoding="utf-8") as stream:
        row = json.loads(stream.readline())
    source_text = row["text"]
    prompt = ("Extract only explicit educational concepts and relationships from this textbook passage. "
        "Return one JSON object with keys entities (array of {name,type,source_quote}) and "
        "relations (array of {source,relation,target,source_quote}). Every source_quote must be an "
        "exact, contiguous quote copied from the passage. Do not infer unsupported facts. Keep lists concise.\n\n"
        "PASSAGE:\n" + source_text)
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": MAX_TOKENS, "temperature": 0, "reasoning_effort": "none",
               "response_format": {"type": "json_object"},
               "provider": {"allow_fallbacks": False}}
    req = urllib.request.Request(CHAT_URL, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"})
    artifact = {"schema_version": 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "provider": "OpenRouter", "model_requested": MODEL, "source_chunk_id": row.get("chunk_id"),
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "source_char_count": len(source_text),
        "request": {"max_tokens": MAX_TOKENS, "temperature": 0, "reasoning_effort": "none",
                    "response_format": "json_object", "allow_fallbacks": False}}
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
            artifact["http_status"] = response.status
    except urllib.error.HTTPError as exc:
        artifact["http_status"] = exc.code
        artifact["error_type"] = "HTTPError"
        artifact["error_message_sanitized"] = str(exc.reason).replace(key, "[REDACTED]")
        artifact["response_body"] = exc.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
        save_artifact(artifact)
        print(f"canary HTTP {exc.code}; saved {OUT_PATH}")
        return 10 if exc.code in (401, 403, 429) else 11
    except Exception as exc:
        artifact["error_type"] = type(exc).__name__
        artifact["error_message_sanitized"] = str(exc).replace(key, "[REDACTED]")
        save_artifact(artifact)
        print(f"canary transport error {type(exc).__name__}; saved {OUT_PATH}")
        return 12
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    raw_content = message.get("content")
    safe_message = scrub_secrets(message, key)
    safe_content = scrub_secrets(raw_content, key)
    artifact["actual_model"] = result.get("model")
    artifact["response_shape"] = {"choice_keys": sorted(choice.keys()),
                                  "message_keys": sorted(message.keys()),
                                  "content_type": type(raw_content).__name__}
    artifact["raw_message"] = safe_message
    artifact["raw_message_sha256"] = hashlib.sha256(canonical_json(safe_message).encode("utf-8")).hexdigest()
    artifact["finish_reason"] = choice.get("finish_reason")
    usage = result.get("usage") or {}
    artifact["usage"] = {field: usage.get(field) for field in
        ("prompt_tokens", "completion_tokens", "total_tokens", "cost") if field in usage}
    artifact["raw_content"] = safe_content
    artifact["raw_content_sha256"] = hashlib.sha256(canonical_json(safe_content).encode("utf-8")).hexdigest()
    try:
        parsed = parse_json_object(content_to_text(safe_content))
        artifact["parse"] = {"ok": True, **validate_extraction(parsed, source_text)}
        artifact["parsed_extraction"] = parsed
    except Exception as exc:
        artifact["parse"] = {"ok": False, "error_type": type(exc).__name__,
            "error_message_sanitized": str(exc).replace(key, "[REDACTED]"),
            "raw_content_type": type(raw_content).__name__,
            "raw_content_item_types": [type(x).__name__ for x in raw_content] if isinstance(raw_content, list) else None}
    save_artifact(artifact)
    print("canary " + json.dumps({"http_status": artifact["http_status"], "model": artifact["actual_model"],
        "finish_reason": artifact["finish_reason"], "usage": artifact["usage"], "parse": artifact["parse"],
        "raw_content_sha256": artifact["raw_content_sha256"], "artifact": str(OUT_PATH)}, ensure_ascii=False))
    return 0 if artifact["parse"].get("ok") and artifact["parse"].get("all_quotes_exact") else 20

if __name__ == "__main__":
    raise SystemExit(main())

