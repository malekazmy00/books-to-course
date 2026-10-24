"""LLM response cache: key = sha256(model_id + prompt + params) (docs/02 §1.4)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb


def cache_key(model_id: str, messages: list[dict[str, Any]], params: dict[str, Any]) -> str:
    raw = json.dumps({"model_id": model_id, "prompt": messages, "params": params},
                     sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Key format of the plain-text path (complete_text). Bump when the key's inputs change so
# old entries can never be served under a new meaning. The structured key above is kept
# unchanged for compatibility with existing cache entries.
TEXT_CACHE_KEY_FORMAT = "b2c-text-cache-key/1"


def text_cache_key(*, provider: str, base_url: str, model_id: str,
                   messages: list[dict[str, Any]], params: dict[str, Any],
                   prompt_version: str) -> str:
    """Key for complete_text: includes the serving identity (provider + base URL), so the
    same model id on two different servers or providers never shares an entry."""
    raw = json.dumps({"format": TEXT_CACHE_KEY_FORMAT, "provider": provider,
                      "base_url": base_url.rstrip("/"), "model_id": model_id,
                      "messages": messages, "params": params,
                      "prompt_version": prompt_version},
                     sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LLMCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, key: str, model_id: str, value: dict[str, Any], *, replace: bool = False) -> None: ...


class InMemoryLLMCache:
    def __init__(self) -> None:
        self._d: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._d.get(key)

    def put(self, key: str, model_id: str, value: dict[str, Any], *, replace: bool = False) -> None:
        if replace:
            self._d[key] = value
        else:
            self._d.setdefault(key, value)

    def __len__(self) -> int:
        return len(self._d)


class PostgresLLMCache:
    DDL = """
    CREATE TABLE IF NOT EXISTS llm_cache (
        key        text PRIMARY KEY,
        model_id   text NOT NULL,
        response   jsonb NOT NULL,
        created_at timestamptz NOT NULL
    );
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def migrate(self) -> None:
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute(self.DDL)

    def get(self, key: str) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn, autocommit=True) as c:
            row = c.execute("SELECT response FROM llm_cache WHERE key=%s", (key,)).fetchone()
        return None if row is None else row[0]

    def put(self, key: str, model_id: str, value: dict[str, Any], *, replace: bool = False) -> None:
        """Insert once; ``replace`` overwrites an entry proven stale by the caller."""
        conflict = ("ON CONFLICT (key) DO UPDATE SET model_id=EXCLUDED.model_id, "
                    "response=EXCLUDED.response, created_at=EXCLUDED.created_at"
                    if replace else "ON CONFLICT (key) DO NOTHING")
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute(
                "INSERT INTO llm_cache (key, model_id, response, created_at) VALUES (%s,%s,%s,%s) "
                + conflict,
                (key, model_id, Jsonb(value), datetime.now(timezone.utc)),
            )
