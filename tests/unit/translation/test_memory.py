"""Translation memory (D-028): exact match on normalized source + language pair (+ book scope);
human entries take precedence and are never overwritten by engine output."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from b2c_contracts import EngineIdentity, QEResult, TranslationUnit, UnitTranslation
from b2c_translation.memory import TranslationMemory, normalize_source, source_hash

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
ENGINE = EngineIdentity(provider="hy-translation", model_id="tencent/Hy-MT2-1.8B",
                        revision="9a341cd1", prompt_version="hy-mt2.default@v1")


class FakeDB:
    """Emulates the translation_memory table for the statements memory.py issues."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}
        self.ddl = 0
        self.statements: list[str] = []
        self.clock = NOW


class FakeConn:
    def __init__(self, db: FakeDB):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        db = self.db
        db.statements.append(sql)
        s = " ".join(sql.split())
        if s.startswith("CREATE"):
            db.ddl += 1
            return FakeCursor([])
        if s.startswith("INSERT INTO translation_memory"):
            (h, src, tgt, scope, origin, source_text, target_text, engine, unit_id, book_id, at) = params
            key = (h, src, tgt, scope)
            old = db.rows.get(key)
            if old is not None and old["origin"] == "human" and origin != "human":
                return FakeCursor([])  # the ON CONFLICT ... WHERE guard
            db.rows[key] = dict(origin=origin, source_text=source_text, target_text=target_text,
                                engine=json.loads(engine) if engine else None, unit_id=unit_id,
                                book_id=book_id, updated_at=at, scope=scope)
            return FakeCursor([])
        if s.startswith("SELECT"):
            h, src, tgt, book = params
            out = [(r["scope"], r["origin"], r["target_text"], r["engine"], r["updated_at"])
                   for (kh, ks, kt, kscope), r in db.rows.items()
                   if kh == h and ks == src and kt == tgt and kscope in ("", book)]
            return FakeCursor(out)
        raise AssertionError(f"unexpected SQL: {s}")


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def tm(db):
    return TranslationMemory("postgresql://unused", connect=lambda: FakeConn(db),
                             clock=lambda: db.clock)


def unit(text="Intelligence  analysis began in 1947.", book="book1", n=1):
    return TranslationUnit(unit_id=f"{book}:u{n:05d}", book_id=book, block_ids=["b1"], kind="prose",
                           source_text=text, pages=[1], section_path=["Ch 1"])


def engine_accepted(u, text="بدأ تحليل الاستخبارات عام 1947."):
    return UnitTranslation(unit_id=u.unit_id, target_text=text, origin="engine", engine=ENGINE,
                           qe=QEResult(deterministic_pass=True, route="auto_accept"),
                           decision="accepted", decided_by="policy", decided_at=NOW)


def human_accepted(u, text="بدأ التحليل الاستخباري عام 1947."):
    return UnitTranslation(unit_id=u.unit_id, target_text=text, origin="human",
                           decision="accepted", decided_by="human", decided_at=NOW)


def test_normalization_is_exact_match_modulo_whitespace_and_unicode_form():
    assert normalize_source("  a  b\n c ") == "a b c"
    assert normalize_source("Café") == normalize_source("Café")
    assert source_hash("A b") != source_hash("a b")  # case is significant
    assert source_hash("x  y") == source_hash(" x y")


def test_miss_then_hit_on_normalized_source_and_language_pair(tm):
    u = unit()
    assert tm.lookup(u, "en", "ar") is None
    tm.store(u, engine_accepted(u), source_language="en", target_language="ar")
    other = unit(text="Intelligence analysis   began in 1947.", book="book2", n=7)
    hit = tm.lookup(other, "en", "ar")
    assert hit is not None
    assert hit.unit_id == other.unit_id and hit.origin == "memory"
    assert hit.target_text == "بدأ تحليل الاستخبارات عام 1947."
    assert hit.engine == ENGINE
    assert hit.decision == "needs_review" and hit.decided_by == "policy"  # engine output is re-checked
    assert tm.lookup(other, "en", "fr") is None
    assert tm.lookup(unit(text="Something else."), "en", "ar") is None


def test_human_entry_wins_and_is_never_overwritten_by_engine_output(tm, db):
    u = unit()
    tm.store(u, engine_accepted(u), source_language="en", target_language="ar")
    tm.store(u, human_accepted(u), source_language="en", target_language="ar")
    db.clock = NOW + timedelta(hours=1)
    tm.store(u, engine_accepted(u, "ترجمة آلية أحدث."), source_language="en", target_language="ar")
    hit = tm.lookup(u, "en", "ar")
    assert hit.target_text == "بدأ التحليل الاستخباري عام 1947."
    assert hit.decision == "accepted" and hit.decided_by == "human"
    insert = next(s for s in db.statements if "INSERT INTO translation_memory" in s)
    assert "WHERE translation_memory.origin <> 'human' OR EXCLUDED.origin = 'human'" in " ".join(insert.split())


def test_human_correction_replaces_an_earlier_human_entry(tm):
    u = unit()
    tm.store(u, human_accepted(u, "أولى."), source_language="en", target_language="ar")
    tm.store(u, human_accepted(u, "ثانية."), source_language="en", target_language="ar")
    assert tm.lookup(u, "en", "ar").target_text == "ثانية."


def test_book_scope_is_preferred_within_origin_and_isolated_from_other_books(tm):
    u = unit()
    tm.store(u, engine_accepted(u, "عام."), source_language="en", target_language="ar")
    tm.store(u, engine_accepted(u, "خاص بالكتاب."), source_language="en", target_language="ar",
             book_scoped=True)
    assert tm.lookup(u, "en", "ar").target_text == "خاص بالكتاب."
    assert tm.lookup(unit(book="book2"), "en", "ar").target_text == "عام."


def test_global_human_beats_book_scoped_engine(tm):
    u = unit()
    tm.store(u, engine_accepted(u, "آلي للكتاب."), source_language="en", target_language="ar",
             book_scoped=True)
    tm.store(u, human_accepted(u, "بشري عام."), source_language="en", target_language="ar")
    assert tm.lookup(u, "en", "ar").target_text == "بشري عام."


@pytest.mark.parametrize("bad", [
    lambda u: UnitTranslation(unit_id=u.unit_id, target_text="x", origin="engine", engine=ENGINE,
                              decision="needs_review", decided_by="policy", decided_at=NOW),
    lambda u: UnitTranslation(unit_id=u.unit_id, target_text="x", origin="rule",
                              decision="accepted", decided_by="policy", decided_at=NOW),
    lambda u: UnitTranslation(unit_id=u.unit_id, target_text="x", origin="memory",
                              decision="accepted", decided_by="policy", decided_at=NOW),
    lambda u: UnitTranslation(unit_id="other:u00001", target_text="x", origin="human",
                              decision="accepted", decided_by="human", decided_at=NOW),
])
def test_only_accepted_engine_or_human_translations_of_this_unit_are_stored(tm, db, bad):
    u = unit()
    with pytest.raises(ValueError):
        tm.store(u, bad(u), source_language="en", target_language="ar")
    assert db.rows == {}


def test_schema_is_created_once_per_instance(tm, db):
    u = unit()
    tm.lookup(u, "en", "ar")
    tm.store(u, engine_accepted(u), source_language="en", target_language="ar")
    tm.lookup(u, "en", "ar")
    ddl_statements = db.ddl
    assert ddl_statements >= 1
    tm.lookup(u, "en", "ar")
    assert db.ddl == ddl_statements
    second = TranslationMemory("postgresql://unused", connect=lambda: FakeConn(db))
    second.lookup(u, "en", "ar")
    assert db.ddl == 2 * ddl_statements


def test_store_defaults_to_project_language_pair(tm):
    u = unit()
    tm.store(u, engine_accepted(u))
    assert tm.lookup(u, "en", "ar") is not None


def test_hit_has_utc_decided_at(tm):
    u = unit()
    tm.store(u, human_accepted(u))
    assert tm.lookup(u, "en", "ar").decided_at == NOW
