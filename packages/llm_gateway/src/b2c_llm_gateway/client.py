"""OpenAI-compatible chat client for local llama-server.

- temperature defaults to 0 (deterministic tasks); only temperature==0 calls are cached.
- structured(): response_format json_schema generated from Pydantic; an invalid output is retried
  twice with the validation error fed back, then InvalidLLMOutput is raised.
- one OTel span per logical call, named `chat {model}` with gen_ai.* attributes (semconv GenAI,
  status Development — see eval/reports/phase-0-verify-notes.md V-10).
"""
from __future__ import annotations

import ipaddress
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel, ValidationError

from .budget import FreeTokenQuotaExceeded, InMemoryOpenRouterBudget, OpenRouterQuotaExceeded
from .cache import LLMCache, cache_key, text_cache_key
from .errors import (
    CloudProviderNotAllowed,
    InvalidLLMOutput,
    LLMOutputTruncated,
    LLMRequestRejected,
    LLMUnavailable,
    OpenRouterRateLimited,
)

log = logging.getLogger("b2c.llm_gateway")
T = TypeVar("T", bound=BaseModel)
PROVIDER = "llama.cpp"
MAX_RETRIES = 2
OPENROUTER_JSON_SCHEMA_MODELS = {
    "deepseek/deepseek-v4-flash-0731",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "dots-studio/dots-3-note-preview:free",
}
OPENROUTER_PROMPT_SCHEMA_ONLY_MODELS = {
    "inclusionai/ling-3.0-flash-sante:free",
    "thinkingmachines/inkling:free",
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3.5-lightning:free",
    "cohere/north-mini-code:free",
}
OPENROUTER_RETRYABLE_POOL_MARKERS = ("upstream_provider_shared_pool", "temporarily rate-limited upstream")


def _is_local(host: str) -> bool:
    if host == "localhost" or "." not in host:  # docker service names: llm, llm-mock, ...
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith((".internal", ".local", ".localhost"))
    return ip.is_private or ip.is_loopback


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    usage: dict[str, int | float] = field(default_factory=dict)
    cached: bool = False
    attempts: int = 1
    finish_reason: str | None = None
    generation_attempts: int | None = None


@dataclass(frozen=True)
class TextCompletion:
    """Plain-text completion (D-028 engines). Field-compatible with
    ``b2c_translation.engines.base.TextCompletion``; ``cached`` is extra metadata."""
    text: str
    model: str
    finish_reason: str | None = None
    usage: dict[str, int | float] = field(default_factory=dict)
    cached: bool = False


_TEXT_ROLES = {"system", "user", "assistant"}


def _validate_text_messages(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, (list, tuple)) or not messages:
        raise ValueError("messages must be a non-empty sequence")
    clean: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role") if hasattr(message, "get") else None
        content = message.get("content") if hasattr(message, "get") else None
        if role not in _TEXT_ROLES or not isinstance(content, str):
            raise ValueError("each message needs role system/user/assistant and string content")
        clean.append({"role": role, "content": content})
    return clean


class LLMGateway:
    def __init__(self, base_url: str, model_id: str, *, cache: LLMCache | None = None,
                 timeout_s: float = 600.0, transport: httpx.BaseTransport | None = None,
                 tracer_provider: trace.TracerProvider | None = None,
                 capture_content: bool = True, allow_remote: bool = False,
                 api_key: str | None = None, request_budget: Any | None = None,
                 free_daily_limit: int = 40, free_min_interval_s: float = 6.0,
                 page_batch_max_chars: int = 24000, page_batch_max_pages: int = 10,
                 reasoning_effort: str | None = None) -> None:
        host = urlparse(base_url).hostname or ""
        is_local = _is_local(host)
        if not is_local and not allow_remote:
            raise CloudProviderNotAllowed(
                f"{base_url!r} is not a local endpoint; only local models are allowed (D-004)")
        if not is_local and urlparse(base_url).scheme != "https":
            raise CloudProviderNotAllowed("remote model endpoints require HTTPS")
        if not is_local and not api_key:
            raise CloudProviderNotAllowed("remote model endpoint requires an API key")
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.cache = cache
        # Remote document text must never be copied into Langfuse span attributes.
        self.capture_content = capture_content if is_local else False
        self.provider_name = (
            "openrouter" if host == "openrouter.ai"
            else ("kiosapi" if host == "api.kiosapi.id"
                  else ("top_tools_ai" if host == "top-tools-ai.com"
                        else ("inceptionlabs" if host == "api.inceptionlabs.ai"
                              else ("codecraft" if not is_local else PROVIDER))))
        )
        self.request_budget = request_budget
        if self.provider_name == "openrouter" and model_id.endswith(":free") and self.request_budget is None:
            self.request_budget = InMemoryOpenRouterBudget(
                daily_limit=free_daily_limit, min_interval_s=free_min_interval_s)
        self.page_batch_max_chars = page_batch_max_chars
        self.page_batch_max_pages = page_batch_max_pages
        self.reasoning_effort = reasoning_effort
        self.usage_totals: dict[str, int | float | None] = {
            "api_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": None,
            "cost_reported_calls": 0,
        }
        headers = {"Authorization": f"Bearer {api_key}"} if not is_local else {}
        self._http = httpx.Client(timeout=timeout_s, transport=transport, headers=headers)
        self._tracer = trace.get_tracer("b2c_llm_gateway", tracer_provider=tracer_provider)

    # ---------------------------------------------------------------- http
    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        is_free_openrouter = self.provider_name == "openrouter" and self.model_id.endswith(":free")
        if self.provider_name == "inceptionlabs" and self.request_budget is not None:
            prompt_payload = json.dumps(
                {"messages": body.get("messages", []), "response_format": body.get("response_format")},
                ensure_ascii=False, separators=(",", ":"),
            )
            # UTF-8 bytes overestimate normal subword tokenization; max_tokens reserves all output.
            reservation = len(prompt_payload.encode("utf-8")) + int(body.get("max_tokens", 4096)) + 64
            self.request_budget.reserve(reservation)
        for attempt in range(3):
            if is_free_openrouter:
                self.request_budget.reserve()
            try:
                r = self._http.post(f"{self.base_url}/chat/completions", json=body)
            except httpx.HTTPError as e:
                # A read/write timeout is ambiguous: the model may still have
                # accepted and processed the request. Never replay that payload.
                if isinstance(e, (httpx.ReadTimeout, httpx.WriteTimeout)):
                    raise LLMUnavailable(
                        f"LLM request timed out: {type(e).__name__}",
                        outcome_unknown=True,
                    ) from e
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise LLMUnavailable(f"LLM request failed: {type(e).__name__}") from e
            if r.status_code == 429 and is_free_openrouter:
                # Only retry transient shared-provider-pool limits. Account/day limits stop now.
                try:
                    error = r.json().get("error", {})
                    detail = json.dumps(error, ensure_ascii=False).lower()
                except (ValueError, AttributeError):
                    detail = ""
                if any(marker in detail for marker in OPENROUTER_RETRYABLE_POOL_MARKERS) and attempt < 2:
                    try:
                        delay = min(60.0, max(1.0, float(r.headers.get("retry-after", 5 * (2 ** attempt)))))
                    except ValueError:
                        delay = 5.0 * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise OpenRouterRateLimited(
                    "OpenRouter free-model request was rate-limited; request stopped without fallback")
            if r.status_code in {408, 500, 502, 504}:
                # These responses may arrive after upstream inference has begun.
                # Defer to translation-level recovery, which can split the source.
                raise LLMUnavailable(
                    f"LLM server returned HTTP {r.status_code}",
                    status_code=r.status_code,
                    outcome_unknown=True,
                )
            if r.status_code in {425, 503} and attempt < 2:
                try:
                    delay = min(30.0, max(0.0, float(r.headers.get("retry-after", 0))))
                except ValueError:
                    delay = 0.0
                time.sleep(delay if delay > 0 else 0.5 * (2 ** attempt))
                continue
            if r.status_code == 400:
                # Expose only schema-safe provider metadata; never log a body that can echo prompts.
                try:
                    err = r.json().get("error", {})
                    safe = lambda value: value if isinstance(value, str) and __import__("re").fullmatch(
                        r"[A-Za-z0-9_.\[\]-]{1,80}", value) else None
                    code = safe(err.get("code"))
                    param = safe(err.get("param"))
                    message = err.get("message", "")
                    if param is None and isinstance(message, str):
                        lowered = message.lower()
                        if any(cue in lowered for cue in ("invalid", "unsupported", "unknown", "required", "not allowed", "must")):
                            param = next((field for field in (
                                "model", "messages", "temperature", "max_tokens",
                                "max_completion_tokens", "response_format", "reasoning_effort",
                            ) if field in lowered), None)
                    message = err.get("message", "")
                    lowered = message.lower() if isinstance(message, str) else ""
                    error_keys = sorted(key for key in err if isinstance(key, str) and
                        __import__("re").fullmatch(r"[A-Za-z0-9_.-]{1,40}", key))
                    hints = []
                    if "context" in lowered and any(x in lowered for x in ("length", "limit", "exceed")):
                        hints.append("context_limit")
                    if "payload" in lowered and any(x in lowered for x in ("large", "limit", "size")):
                        hints.append("request_size")
                    if "model" in lowered and any(x in lowered for x in ("unknown", "missing", "access", "available")):
                        hints.append("model_access")
                    if any(x in lowered for x in ("unauthorized", "api key", "permission")):
                        hints.append("authorization")
                    if "quota" in lowered or "rate limit" in lowered:
                        hints.append("quota")
                    if any(x in lowered for x in ("json_schema", "response_format", "structured output")):
                        hints.append("structured_output")
                    if param is None and isinstance(message, str):
                        if any(cue in lowered for cue in ("invalid", "unsupported", "unknown", "required", "not allowed", "must")):
                            param = next((field for field in (
                                "model", "messages", "temperature", "max_tokens",
                                "max_completion_tokens", "response_format", "reasoning_effort",
                            ) if field in lowered), None)
                    if not hints:
                        hints.append("provider_message_present" if isinstance(message, str) and message else "provider_message_missing")
                    diagnostic = "keys=" + ",".join(error_keys) + ";hint=" + ",".join(hints)
                    if isinstance(message, str):
                        diagnostic += f";message_length={len(message)}"
                    raise LLMRequestRejected(400, code=code, param=param,
                        error_type=safe(err.get("type")), diagnostic=diagnostic)
                except LLMRequestRejected:
                    raise
                except Exception:
                    raise LLMRequestRejected(400) from None
            if r.status_code >= 400:
                # Keep provider bodies out of logs; they may echo request details.
                raise LLMUnavailable(f"LLM server returned HTTP {r.status_code}")
            return r.json()
        raise OpenRouterRateLimited("OpenRouter free-model provider remained rate-limited after retries")

    def _call(self, messages: list[dict[str, Any]], params: dict[str, Any]) -> ChatResult:
        data = self._post({"model": self.model_id, "messages": messages, **params})
        u = data.get("usage") or {}
        usage: dict[str, int | float] = {"input_tokens": int(u.get("prompt_tokens", 0)),
            "output_tokens": int(u.get("completion_tokens", 0))}
        reported_cost = u.get("cost", data.get("cost"))
        if isinstance(reported_cost, (int, float)) and not isinstance(reported_cost, bool):
            usage["cost_usd"] = float(reported_cost)
        result = ChatResult(
            text=data["choices"][0]["message"].get("content") or "",
            model=data.get("model", self.model_id), usage=usage,
            finish_reason=data["choices"][0].get("finish_reason"),
        )
        self.usage_totals["api_calls"] += 1
        self.usage_totals["input_tokens"] += int(usage.get("input_tokens", 0))
        self.usage_totals["output_tokens"] += int(usage.get("output_tokens", 0))
        if "cost_usd" in usage:
            self.usage_totals["cost_usd"] = float(self.usage_totals["cost_usd"] or 0.0) + float(usage["cost_usd"])
            self.usage_totals["cost_reported_calls"] += 1
        return result

    # ---------------------------------------------------------------- api
    def chat(self, messages: list[dict[str, Any]], *, prompt_version: str,
             temperature: float = 0.0, max_tokens: int | None = None) -> ChatResult:
        params = self._params(temperature, max_tokens, self.reasoning_effort)
        return self._run(messages, params, prompt_version, schema=None)[0]

    def chat_book_pages(self, pages: list[tuple[int, str]], *, instruction: str,
                        prompt_version: str, max_tokens: int | None = None,
                        max_input_chars: int | None = None,
                        max_pages_per_request: int | None = None) -> list[ChatResult]:
        """Process page-tagged book text in cached bounded batches; split truncated batches automatically."""
        max_input_chars = max_input_chars or self.page_batch_max_chars
        max_pages_per_request = max_pages_per_request or self.page_batch_max_pages
        if max_input_chars < 256 or max_pages_per_request < 1:
            raise ValueError("page batch limits are too small")
        segments: list[tuple[int, int, str]] = []
        for page_no, text in pages:
            text = str(text)
            while text:
                if len(text) <= max_input_chars:
                    part, text = text, ""
                else:
                    cut = text.rfind("\n", 0, max_input_chars)
                    cut = cut if cut >= max_input_chars // 2 else max_input_chars
                    part, text = text[:cut], text[cut:]
                segments.append((int(page_no), len(segments) + 1, part))
        batches: list[list[tuple[int, int, str]]] = []
        current: list[tuple[int, int, str]] = []
        size = 0
        for segment in segments:
            cost = len(segment[2]) + 32
            if current and (len(current) >= max_pages_per_request or size + cost > max_input_chars):
                batches.append(current)
                current, size = [], 0
            current.append(segment)
            size += cost
        if current:
            batches.append(current)
        results: list[ChatResult] = []

        def run(batch: list[tuple[int, int, str]]) -> None:
            user_text = "\n\n".join(f"[PAGE {page_no} PART {part_no}]\n{text}"
                                       for page_no, part_no, text in batch)
            try:
                result = self.chat(
                    [{"role": "system", "content": instruction +
                      " Preserve every page marker exactly and never merge content across pages."},
                     {"role": "user", "content": user_text}],
                    prompt_version=f"{prompt_version}:pages:" + ",".join(str(x[0]) for x in batch),
                    max_tokens=max_tokens)
                results.append(result)
            except LLMOutputTruncated:
                if len(batch) == 1:
                    raise
                middle = len(batch) // 2
                run(batch[:middle])
                run(batch[middle:])

        for batch in batches:
            run(batch)
        return results

    def structured(self, messages: list[dict[str, Any]], schema: type[T], *, prompt_version: str,
                   temperature: float = 0.0, max_tokens: int | None = None) -> T:
        """Return validated structured output, preserving the established API."""
        return self.structured_with_result(messages, schema, prompt_version=prompt_version,
            temperature=temperature, max_tokens=max_tokens)[1]

    def structured_with_result(self, messages: list[dict[str, Any]], schema: type[T], *,
                              prompt_version: str, temperature: float = 0.0,
                              max_tokens: int | None = None) -> tuple[ChatResult, T]:
        """Return validated output plus model/usage metadata for auditable evaluations."""
        params = self._params(temperature, max_tokens, self.reasoning_effort)
        if self.provider_name == "inceptionlabs":
            # Mercury 2 free endpoint may reject deterministic temperature=0; rely on
            # provider default temperature while schema validation remains local.
            params.pop("temperature", None)
        if self.provider_name == "openrouter" and self.model_id in OPENROUTER_JSON_SCHEMA_MODELS:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True},
            }
        elif self.provider_name == "openrouter" and self.model_id.endswith(":free"):
            # JSON-object mode guarantees syntax only; it does not transmit the Pydantic
            # schema as a constrained-decoding schema. Include the contract in the prompt
            # so free routes can produce locally valid data without silently weakening checks.
            schema_instruction = {
                "role": "system",
                "content": (
                    "Return exactly one JSON object, with no Markdown or extra keys, that "
                    "satisfies this required JSON Schema. All required properties must be present. "
                    "Do not add commentary.\nJSON Schema:\n"
                    + json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
                ),
            }
            effective_messages = list(messages)
            insertion = 1 if effective_messages and effective_messages[0].get("role") == "system" else 0
            effective_messages.insert(insertion, schema_instruction)
            messages = effective_messages
            if self.model_id not in OPENROUTER_PROMPT_SCHEMA_ONLY_MODELS:
                params["response_format"] = {"type": "json_object"}
        elif self.provider_name == "inceptionlabs":
            # Mercury 2's public endpoint rejects both structured response_format modes.
            # Ask for JSON in the prompt and validate the response locally against this schema.
            pass
        else:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True},
            }
        result, parsed = self._run(messages, params, prompt_version, schema=schema)
        assert parsed is not None
        return result, parsed

    def complete_text(self, messages, *, prompt_version: str, max_tokens: int,
                      temperature: float = 0.0) -> TextCompletion:
        """Plain-text chat completion: messages are sent as given, no response_format.

        Satisfies ``TextCompletionGateway`` (b2c_translation.engines.base). Cached only at
        temperature 0, under ``text_cache_key`` (provider + base URL + model + messages +
        params + prompt_version + key-format version). ``finish_reason == "length"`` raises
        LLMOutputTruncated and is never cached.
        """
        clean = _validate_text_messages(messages)
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        params = self._params(temperature, max_tokens, self.reasoning_effort)
        cacheable = self.cache is not None and params["temperature"] == 0.0
        key = text_cache_key(provider=self.provider_name, base_url=self.base_url,
                             model_id=self.model_id, messages=clean, params=params,
                             prompt_version=prompt_version)
        with self._tracer.start_as_current_span(f"chat {self.model_id}", kind=SpanKind.CLIENT) as span:
            span.set_attributes({
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": self.provider_name,
                "gen_ai.request.model": self.model_id,
                "gen_ai.request.temperature": params["temperature"],
                "gen_ai.request.max_tokens": max_tokens,
                "gen_ai.output.type": "text",
                "b2c.prompt_version": prompt_version,
                "b2c.cache_key": key,
            })
            if self.capture_content:
                span.set_attribute("langfuse.observation.input", json.dumps(clean, ensure_ascii=False))
            hit = self.cache.get(key) if cacheable else None
            if hit is not None and isinstance(hit.get("text"), str):
                res = ChatResult(text=hit["text"], model=hit.get("model", self.model_id),
                                 usage=hit.get("usage", {}), cached=True, attempts=0,
                                 finish_reason=hit.get("finish_reason"), generation_attempts=1)
                self._finish(span, res, cache_hit=True)
                return TextCompletion(res.text, res.model, res.finish_reason, dict(res.usage), True)
            try:
                res = self._call(clean, params)
                if res.finish_reason == "length":
                    raise LLMOutputTruncated(
                        "LLM output reached its token limit", last_output=res.text,
                        usage=res.usage, model_id=res.model, finish_reason=res.finish_reason)
            except (LLMOutputTruncated, OpenRouterQuotaExceeded, OpenRouterRateLimited,
                    FreeTokenQuotaExceeded, LLMRequestRejected, LLMUnavailable) as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            if cacheable:
                self.cache.put(key, self.model_id, {
                    "text": res.text, "model": res.model, "usage": res.usage,
                    "finish_reason": res.finish_reason, "key_inputs": {
                        "provider": self.provider_name, "base_url": self.base_url,
                        "prompt_version": prompt_version}})
            self._finish(span, res, cache_hit=False)
            return TextCompletion(res.text, res.model, res.finish_reason, dict(res.usage), False)

    # ---------------------------------------------------------------- internals
    @staticmethod
    def _params(temperature: float, max_tokens: int | None,
                reasoning_effort: str | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {"temperature": float(temperature)}
        if max_tokens is not None:
            p["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            p["reasoning_effort"] = reasoning_effort
        return p

    def _run(self, messages, params, prompt_version, schema):
        cacheable = self.cache is not None and params.get("temperature") == 0.0
        key = cache_key(self.model_id, messages, {**params, "prompt_version": prompt_version})
        with self._tracer.start_as_current_span(f"chat {self.model_id}", kind=SpanKind.CLIENT) as span:
            span.set_attributes({
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": self.provider_name,
                "gen_ai.request.model": self.model_id,
                "gen_ai.request.temperature": params.get("temperature"),
                "gen_ai.output.type": "json" if schema else "text",
                "b2c.prompt_version": prompt_version,
                "b2c.cache_key": key,
            })
            if "max_tokens" in params:
                span.set_attribute("gen_ai.request.max_tokens", params["max_tokens"])
            if self.capture_content:
                span.set_attribute("langfuse.observation.input", json.dumps(messages, ensure_ascii=False))

            hit = self.cache.get(key) if cacheable else None
            stale_hit = False
            if hit is not None:
                res = ChatResult(text=hit["text"], model=hit["model"], usage=hit.get("usage", {}),
                                 cached=True, attempts=0, finish_reason=hit.get("finish_reason"),
                                 generation_attempts=hit.get("generation_attempts"))
                try:
                    parsed = schema.model_validate_json(res.text) if schema else None
                except ValidationError as exc:
                    # The schema changed without a prompt_version bump (the key does
                    # not cover the schema). Treat the entry as a miss: regenerate and
                    # replace it, rather than failing deterministically forever.
                    stale_hit = True
                    span.add_event("b2c.cache_stale", {"b2c.schema": schema.__name__})
                    log.warning("stale LLM cache entry failed %s validation; regenerating "
                                "(key=%s, prompt_version=%s): %s", schema.__name__, key,
                                prompt_version, str(exc).splitlines()[0] if str(exc) else "invalid")
                else:
                    self._finish(span, res, cache_hit=True)
                    return res, parsed

            try:
                res, parsed = self._generate(messages, params, schema, span)
            except (LLMOutputTruncated, OpenRouterQuotaExceeded, OpenRouterRateLimited, FreeTokenQuotaExceeded, LLMRequestRejected) as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            if cacheable:
                value = {"text": res.text, "model": res.model, "usage": res.usage,
                         "finish_reason": res.finish_reason, "generation_attempts": res.attempts}
                if stale_hit:
                    self.cache.put(key, self.model_id, value, replace=True)
                else:
                    self.cache.put(key, self.model_id, value)
            self._finish(span, res, cache_hit=False)
            return res, parsed

    def _generate(self, messages, params, schema, span):
        convo = list(messages)
        errors: list[str] = []
        res: ChatResult | None = None
        for attempt in range(1, MAX_RETRIES + 2):
            res = self._call(convo, params)
            if res.finish_reason == "length":
                raise LLMOutputTruncated(
                    "LLM output reached its token limit",
                    last_output=res.text, usage=res.usage, model_id=res.model,
                    finish_reason=res.finish_reason,
                )
            if schema is None:
                return ChatResult(res.text, res.model, res.usage, attempts=attempt,
                                  finish_reason=res.finish_reason), None
            try:
                parsed = schema.model_validate_json(res.text)
                return ChatResult(res.text, res.model, res.usage, attempts=attempt,
                                  finish_reason=res.finish_reason), parsed
            except ValidationError as e:
                err = str(e).splitlines()[0] if str(e) else "validation failed"
                errors.append(err)
                convo = [*messages]
                if res.text.strip():
                    convo.append({"role": "assistant", "content": res.text})
                convo.append({"role": "user", "content": (
                    f"Your previous output was invalid for the required JSON schema: {e}. "
                    "Return only a JSON object that satisfies the schema.")})
        span.set_attribute("b2c.attempts", MAX_RETRIES + 1)
        span.set_attribute("b2c.cache_hit", False)
        span.set_status(Status(StatusCode.ERROR, "InvalidLLMOutput"))
        raise InvalidLLMOutput(
            f"{schema.__name__}: output invalid after {MAX_RETRIES + 1} attempts",
            attempts=MAX_RETRIES + 1, last_output=res.text if res else "", errors=errors)

    def _finish(self, span, res: ChatResult, *, cache_hit: bool) -> None:
        # Old cache entries lack completion metadata. Preserve that uncertainty
        # instead of inventing a provider "stop" response for historical results.
        if res.finish_reason is not None:
            span.set_attribute("gen_ai.response.finish_reasons", [res.finish_reason])
        original_attempts = res.generation_attempts if cache_hit else res.attempts
        if original_attempts is not None:
            span.set_attribute("b2c.generation_attempts", original_attempts)
        span.set_attributes({
            "gen_ai.response.model": res.model,
            "gen_ai.usage.input_tokens": res.usage.get("input_tokens", 0),
            "gen_ai.usage.output_tokens": res.usage.get("output_tokens", 0),
            "b2c.cache_hit": cache_hit,
            "b2c.attempts": res.attempts,
        })
        if self.capture_content:
            span.set_attribute("langfuse.observation.output", res.text)
