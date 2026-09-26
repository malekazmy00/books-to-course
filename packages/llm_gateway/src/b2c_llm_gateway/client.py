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
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel, ValidationError

from .cache import LLMCache, cache_key
from .errors import CloudProviderNotAllowed, InvalidLLMOutput, LLMUnavailable

T = TypeVar("T", bound=BaseModel)
PROVIDER = "llama.cpp"
MAX_RETRIES = 2


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
    usage: dict[str, int] = field(default_factory=dict)
    cached: bool = False
    attempts: int = 1


class LLMGateway:
    def __init__(self, base_url: str, model_id: str, *, cache: LLMCache | None = None,
                 timeout_s: float = 600.0, transport: httpx.BaseTransport | None = None,
                 tracer_provider: trace.TracerProvider | None = None,
                 capture_content: bool = True) -> None:
        host = urlparse(base_url).hostname or ""
        if not _is_local(host):
            raise CloudProviderNotAllowed(
                f"{base_url!r} is not a local endpoint; only local models are allowed (D-004)")
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.cache = cache
        self.capture_content = capture_content
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self._tracer = trace.get_tracer("b2c_llm_gateway", tracer_provider=tracer_provider)

    # ---------------------------------------------------------------- http
    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._http.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"LLM request failed: {e}") from e
        if r.status_code >= 400:
            raise LLMUnavailable(f"LLM server returned {r.status_code}: {r.text[:500]}")
        return r.json()

    def _call(self, messages: list[dict[str, Any]], params: dict[str, Any]) -> ChatResult:
        data = self._post({"model": self.model_id, "messages": messages, **params})
        u = data.get("usage") or {}
        return ChatResult(
            text=data["choices"][0]["message"].get("content") or "",
            model=data.get("model", self.model_id),
            usage={"input_tokens": int(u.get("prompt_tokens", 0)),
                   "output_tokens": int(u.get("completion_tokens", 0))},
        )

    # ---------------------------------------------------------------- api
    def chat(self, messages: list[dict[str, Any]], *, prompt_version: str,
             temperature: float = 0.0, max_tokens: int | None = None) -> ChatResult:
        params = self._params(temperature, max_tokens)
        return self._run(messages, params, prompt_version, schema=None)[0]

    def structured(self, messages: list[dict[str, Any]], schema: type[T], *, prompt_version: str,
                   temperature: float = 0.0, max_tokens: int | None = None) -> T:
        params = self._params(temperature, max_tokens)
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(),
                            "strict": True},
        }
        return self._run(messages, params, prompt_version, schema=schema)[1]

    # ---------------------------------------------------------------- internals
    @staticmethod
    def _params(temperature: float, max_tokens: int | None) -> dict[str, Any]:
        p: dict[str, Any] = {"temperature": float(temperature)}
        if max_tokens is not None:
            p["max_tokens"] = max_tokens
        return p

    def _run(self, messages, params, prompt_version, schema):
        cacheable = self.cache is not None and params["temperature"] == 0.0
        key = cache_key(self.model_id, messages, {**params, "prompt_version": prompt_version})
        with self._tracer.start_as_current_span(f"chat {self.model_id}", kind=SpanKind.CLIENT) as span:
            span.set_attributes({
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": PROVIDER,
                "gen_ai.request.model": self.model_id,
                "gen_ai.request.temperature": params["temperature"],
                "gen_ai.output.type": "json" if schema else "text",
                "b2c.prompt_version": prompt_version,
                "b2c.cache_key": key,
            })
            if "max_tokens" in params:
                span.set_attribute("gen_ai.request.max_tokens", params["max_tokens"])
            if self.capture_content:
                span.set_attribute("langfuse.observation.input", json.dumps(messages, ensure_ascii=False))

            hit = self.cache.get(key) if cacheable else None
            if hit is not None:
                res = ChatResult(text=hit["text"], model=hit["model"], usage=hit.get("usage", {}),
                                 cached=True, attempts=0)
                parsed = schema.model_validate_json(res.text) if schema else None
                self._finish(span, res, cache_hit=True)
                return res, parsed

            res, parsed = self._generate(messages, params, schema, span)
            if cacheable:
                self.cache.put(key, self.model_id,
                               {"text": res.text, "model": res.model, "usage": res.usage})
            self._finish(span, res, cache_hit=False)
            return res, parsed

    def _generate(self, messages, params, schema, span):
        convo = list(messages)
        errors: list[str] = []
        res: ChatResult | None = None
        for attempt in range(1, MAX_RETRIES + 2):
            res = self._call(convo, params)
            if schema is None:
                return ChatResult(res.text, res.model, res.usage, attempts=attempt), None
            try:
                parsed = schema.model_validate_json(res.text)
                return ChatResult(res.text, res.model, res.usage, attempts=attempt), parsed
            except ValidationError as e:
                err = str(e).splitlines()[0] if str(e) else "validation failed"
                errors.append(err)
                convo = [*messages,
                         {"role": "assistant", "content": res.text},
                         {"role": "user", "content": (
                             f"Your previous output was invalid for the required JSON schema: {e}. "
                             "Return only a JSON object that satisfies the schema.")}]
        span.set_attribute("b2c.attempts", MAX_RETRIES + 1)
        span.set_attribute("b2c.cache_hit", False)
        span.set_status(Status(StatusCode.ERROR, "InvalidLLMOutput"))
        raise InvalidLLMOutput(
            f"{schema.__name__}: output invalid after {MAX_RETRIES + 1} attempts",
            attempts=MAX_RETRIES + 1, last_output=res.text if res else "", errors=errors)

    def _finish(self, span, res: ChatResult, *, cache_hit: bool) -> None:
        span.set_attributes({
            "gen_ai.response.model": res.model,
            "gen_ai.usage.input_tokens": res.usage.get("input_tokens", 0),
            "gen_ai.usage.output_tokens": res.usage.get("output_tokens", 0),
            "b2c.cache_hit": cache_hit,
            "b2c.attempts": res.attempts,
        })
        if self.capture_content:
            span.set_attribute("langfuse.observation.output", res.text)
