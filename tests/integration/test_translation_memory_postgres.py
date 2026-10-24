"""TranslationMemory against the real b2c Postgres (D-028)."""
import uuid
from datetime import datetime, timezone

import pytest

from b2c_contracts import EngineIdentity, QEResult, TranslationUnit, UnitTranslation
from b2c_core import Settings
from b2c_translation.memory import TranslationMemory

pytestmark = pytest.mark.integration
NOW = datetime.now(timezone.utc)
ENGINE = EngineIdentity(provider="test", model_id="m", revision=None, prompt_version="p@v1")


def _unit(text, book):
    return TranslationUnit(unit_id=f"{book}:u00001", book_id=book, block_ids=["b"], kind="prose",
                           source_text=text, pages=[1], section_path=[])


def test_roundtrip_human_precedence_and_scope():
    tm = TranslationMemory(Settings().pg_dsn)
    text = f"Integration source {uuid.uuid4().hex}."
    book = f"itest-{uuid.uuid4().hex[:8]}"
    u = _unit(text, book)
    assert tm.lookup(u, "en", "ar") is None
    tm.store(u, UnitTranslation(unit_id=u.unit_id, target_text="آلي.", origin="engine", engine=ENGINE,
                                qe=QEResult(deterministic_pass=True, route="auto_accept"),
                                decision="accepted", decided_by="policy", decided_at=NOW))
    hit = tm.lookup(_unit("  " + text.replace(" ", "  "), "other-book"), "en", "ar")
    assert hit is not None and hit.target_text == "آلي." and hit.engine == ENGINE
    tm.store(u, UnitTranslation(unit_id=u.unit_id, target_text="بشري.", origin="human",
                                decision="accepted", decided_by="human", decided_at=NOW))
    tm.store(u, UnitTranslation(unit_id=u.unit_id, target_text="آلي ثان.", origin="engine", engine=ENGINE,
                                qe=QEResult(deterministic_pass=True, route="auto_accept"),
                                decision="accepted", decided_by="policy", decided_at=NOW))
    hit = tm.lookup(u, "en", "ar")
    assert hit.target_text == "بشري." and hit.decided_by == "human"
    tm.store(u, UnitTranslation(unit_id=u.unit_id, target_text="خاص.", origin="human",
                                decision="accepted", decided_by="human", decided_at=NOW), book_scoped=True)
    assert tm.lookup(u, "en", "ar").target_text == "خاص."
    assert tm.lookup(_unit(text, "other-book"), "en", "ar").target_text == "بشري."
