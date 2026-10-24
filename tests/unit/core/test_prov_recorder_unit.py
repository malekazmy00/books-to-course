"""ProvRecorder statement batching (no database: psycopg.connect is faked)."""
from __future__ import annotations

import b2c_core.prov as prov_mod
from b2c_core import ProvRecorder


class _FakeConn:
    def __init__(self, log, autocommit):
        self.log = log
        self.autocommit = autocommit
        self.statements = []
        self.committed = False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        self.committed = exc_type is None
        return False


def _fake_connect(monkeypatch):
    log = []

    def connect(dsn, autocommit=False):
        conn = _FakeConn(log, autocommit)
        log.append(conn)
        return conn
    monkeypatch.setattr(prov_mod.psycopg, "connect", connect)
    return log


def test_record_activity_uses_one_transaction(monkeypatch):
    log = _fake_connect(monkeypatch)
    act = ProvRecorder("postgresql://x").record_activity(
        stage="ingest-parse-pdf", stage_version="6", agent="docling-rapidocr", prompt_version=None,
        activity_id="act:ingest-parse-pdf:abc", used=["s3://a/src/"],
        derived=[("s3://a/out/", "s3://a/src/")], generated=["s3://a/out/"])
    assert act == "act:ingest-parse-pdf:abc"
    assert len(log) == 1
    conn = log[0]
    assert conn.autocommit is False and conn.committed
    sql = [statement for statement, _ in conn.statements]
    assert len(sql) == 5
    assert all("ON CONFLICT DO NOTHING" in s for s in sql if s.startswith("INSERT"))
    # Re-recording a cache hit must not move the original end time.
    assert any("ended_at IS NULL" in s for s in sql if s.startswith("UPDATE"))


def test_end_activity_is_idempotent(monkeypatch):
    log = _fake_connect(monkeypatch)
    ProvRecorder("postgresql://x").end_activity("act:x")
    assert "ended_at IS NULL" in log[0].statements[0][0]
