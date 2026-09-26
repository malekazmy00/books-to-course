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


class LLMCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, key: str, model_id: str, value: dict[str, Any]) -> None: ...


class InMemoryLLMCache:
    def __init__(self) -> None:
        self._d: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._d.get(key)

    def put(self, key: str, model_id: str, value: dict[str, Any]) -> None:
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

    def put(self, key: str, model_id: str, value: dict[str, Any]) -> None:
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute(
                "INSERT INTO llm_cache (key, model_id, response, created_at) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (key) DO NOTHING",
                (key, model_id, Jsonb(value), datetime.now(timezone.utc)),
            )
