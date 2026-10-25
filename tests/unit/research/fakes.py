"""Test doubles for b2c_research: a routing fetcher and a scripted text LLM."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

from b2c_research.http import FetchRefused, FetchResult

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


class RoutingFetcher:
    """Serves the first route whose substring occurs in the full URL; unknown URLs raise FetchRefused."""

    def __init__(self, routes: dict[str, tuple[str, str | dict]]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> FetchResult:
        full = url + ("?" + urlencode(params, doseq=True) if params else "")
        self.calls.append(full)
        for needle, (ctype, body) in self.routes.items():
            if needle in full:
                raw = json.dumps(body) if isinstance(body, dict) else body
                return FetchResult(url=full, final_url=full, status=200, content_type=ctype,
                                   body=raw.encode("utf-8"), fetched_at=NOW, from_cache=False)
        raise FetchRefused("fetch", f"no route for {full}")


@dataclass
class Reply:
    text: str
    model: str = "fake"
    finish_reason: str = "stop"


class ScriptedLLM:
    """``complete_text`` answers through ``script(prompt_version, user_message) -> dict | str``."""

    def __init__(self, script: Callable[[str, str], dict | str], model_id: str = "fake/model"):
        self.script = script
        self.model_id = model_id
        self.calls: list[tuple[str, list[dict]]] = []

    def complete_text(self, messages, *, prompt_version: str, max_tokens: int, temperature: float = 0.0):
        self.calls.append((prompt_version, messages))
        out = self.script(prompt_version, messages[-1]["content"])
        return Reply(out if isinstance(out, str) else json.dumps(out))
