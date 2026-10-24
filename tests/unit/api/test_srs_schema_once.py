"""SRS DDL runs once per store, not on every learner request."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from b2c_api.srs import _SRS_DDL, PostgresSRSStore


def test_schema_is_created_once(monkeypatch):
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, args=None):
            statements.append(sql)
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    store = PostgresSRSStore("unused")
    monkeypatch.setattr(store, "_connect", lambda: Connection())
    now = datetime.now(UTC)
    store.review(user_id="a", course_id="demo", question_id="q1", rating=3, reviewed_at=now)
    store.due(user_id="a", course_id="demo", now=now)
    store.history(user_id="a", course_id="demo")
    store.due(user_id="a", course_id="demo", now=now)
    ddl = [sql for sql in statements if sql in _SRS_DDL]
    assert ddl == list(_SRS_DDL)
