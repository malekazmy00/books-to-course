"""Durable conservative quota guard for OpenRouter free-model calls."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import psycopg


class OpenRouterQuotaExceeded(RuntimeError):
    """The B2C safety budget for free-tier requests is exhausted for this UTC day."""


class PostgresOpenRouterBudget:
    """Account-wide B2C request counter with a distributed minimum request interval."""

    DDL = """
    CREATE TABLE IF NOT EXISTS openrouter_free_request_budget (
        utc_day date PRIMARY KEY,
        requests_reserved integer NOT NULL DEFAULT 0,
        next_slot_at timestamptz NOT NULL
    );
    """

    def __init__(self, dsn: str, *, daily_limit: int = 40, min_interval_s: float = 6.0) -> None:
        if daily_limit < 1 or min_interval_s < 0:
            raise ValueError("OpenRouter budget must have a positive daily limit and nonnegative interval")
        self.dsn = dsn
        self.daily_limit = daily_limit
        self.min_interval_s = min_interval_s

    def migrate(self) -> None:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(self.DDL)

    def reserve(self) -> None:
        """Reserve one quota slot before sending; failed upstream attempts count too."""
        while True:
            now = datetime.now(timezone.utc)
            day = now.date()
            with psycopg.connect(self.dsn) as conn:
                conn.execute(
                    "INSERT INTO openrouter_free_request_budget (utc_day, requests_reserved, next_slot_at) "
                    "VALUES (%s, 0, %s) ON CONFLICT (utc_day) DO NOTHING", (day, now),
                )
                row = conn.execute(
                    "SELECT requests_reserved, next_slot_at FROM openrouter_free_request_budget "
                    "WHERE utc_day=%s FOR UPDATE", (day,),
                ).fetchone()
                used, next_slot = row
                if used >= self.daily_limit:
                    raise OpenRouterQuotaExceeded(
                        f"B2C OpenRouter free-model safety limit reached ({self.daily_limit} requests UTC day); "
                        "completed page batches remain cached and can resume after the daily reset"
                    )
                slot = max(now, next_slot)
                conn.execute(
                    "UPDATE openrouter_free_request_budget SET requests_reserved=requests_reserved+1, "
                    "next_slot_at=%s WHERE utc_day=%s",
                    (slot + timedelta(seconds=self.min_interval_s), day),
                )
            wait_s = (slot - datetime.now(timezone.utc)).total_seconds()
            if wait_s > 0:
                time.sleep(wait_s)
            return


class InMemoryOpenRouterBudget:
    """Single-process fallback; the production worker uses PostgreSQL for durable limits."""

    def __init__(self, *, daily_limit: int = 40, min_interval_s: float = 6.0) -> None:
        import threading
        self.daily_limit = daily_limit
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._day = datetime.now(timezone.utc).date()
        self._used = 0
        self._next = 0.0

    def reserve(self) -> None:
        while True:
            with self._lock:
                today = datetime.now(timezone.utc).date()
                if today != self._day:
                    self._day, self._used, self._next = today, 0, 0.0
                if self._used >= self.daily_limit:
                    raise OpenRouterQuotaExceeded(
                        f"B2C OpenRouter free-model safety limit reached ({self.daily_limit} requests UTC day)")
                slot = max(time.monotonic(), self._next)
                self._used += 1
                self._next = slot + self.min_interval_s
            time.sleep(max(0.0, slot - time.monotonic()))
            return


class FreeTokenQuotaExceeded(RuntimeError):
    """The local safety cap for a free model's daily token allowance was reached."""


class PostgresFreeTokenBudget:
    """Durably reserve a conservative lifetime token cap for a free API-key allowance."""

    DDL = """
    CREATE TABLE IF NOT EXISTS free_provider_token_budget (
        provider text PRIMARY KEY,
        tokens_reserved bigint NOT NULL DEFAULT 0
    );
    """

    def __init__(self, dsn: str, *, provider: str, token_limit: int = 10_000_000) -> None:
        if not provider or token_limit < 1:
            raise ValueError("free token budget needs a provider and positive token limit")
        self.dsn = dsn
        self.provider = provider
        self.token_limit = token_limit

    def migrate(self) -> None:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(self.DDL)

    def reserve(self, tokens: int) -> None:
        """Atomically reserve prompt-byte upper bound plus max output; reservations never reset."""
        if tokens < 1:
            raise ValueError("token reservation must be positive")
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO free_provider_token_budget (provider, tokens_reserved) "
                "VALUES (%s, 0) ON CONFLICT (provider) DO NOTHING",
                (self.provider,),
            )
            row = conn.execute(
                "SELECT tokens_reserved FROM free_provider_token_budget "
                "WHERE provider=%s FOR UPDATE",
                (self.provider,),
            ).fetchone()
            used = int(row[0])
            if used + tokens > self.token_limit:
                raise FreeTokenQuotaExceeded(
                    f"{self.provider} cumulative free-token safety cap reached "
                    f"({used} reserved + {tokens} requested > {self.token_limit}); stopped without fallback"
                )
            conn.execute(
                "UPDATE free_provider_token_budget SET tokens_reserved=%s WHERE provider=%s",
                (used + tokens, self.provider),
            )
