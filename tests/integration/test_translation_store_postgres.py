"""TranslationResultStore against the real b2c Postgres (D-028 part D)."""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from b2c_contracts import EngineIdentity, QEResult, UnitTranslation
from b2c_core import Settings
from b2c_orchestration.translation_store import TranslationResultStore

pytestmark = pytest.mark.integration
NOW = datetime.now(timezone.utc)
ENGINE = EngineIdentity(provider="test", model_id="m", revision=None, prompt_version="p@v1")


def test_roundtrip_human_guard_counts_and_runs():
    store = TranslationResultStore(Settings().pg_dsn)
    run = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    book = f"itest-{uuid.uuid4().hex[:8]}"
    uid = f"{book}:u00001"
    assert store.register_run(run, course_id="itest", workflow_id=f"wf-{run}", input_json={"x": 1})
    assert not store.register_run(run, course_id="itest", workflow_id=f"wf-{run}", input_json={"x": 2})
    store.upsert(run, book, UnitTranslation(unit_id=uid, target_text="آلي.", origin="engine", engine=ENGINE,
                                            decision="needs_review", decided_by="policy", decided_at=NOW),
                 drafts=[{"lever": "initial"}])
    assert store.final_unit_ids(run, [uid]) == set()
    human = UnitTranslation(unit_id=uid, target_text="بشري.", origin="human", decision="accepted",
                            decided_by="human", decided_at=NOW)
    assert store.upsert(run, book, human)
    policy = UnitTranslation(unit_id=uid, target_text="آلي ثان.", origin="engine", engine=ENGINE,
                             qe=QEResult(deterministic_pass=True, route="auto_accept"),
                             decision="accepted", decided_by="policy", decided_at=NOW)
    assert not store.upsert(run, book, policy)
    assert store.fetch(run, uid).translation.target_text == "بشري."
    assert store.counts(run) == {"accepted": 1}
    assert store.final_unit_ids(run, [uid]) == {uid}
    assert store.list_by_decision(run, ["needs_review"]) == []
    store.set_units(run, units_ref={"k": "v"}, unit_count=1, book_id=book)
    assert store.set_assembly(run, {"a": 2}, snapshot_at=NOW + timedelta(seconds=1))
    assert not store.set_assembly(run, {"a": 1}, snapshot_at=NOW)
    got = store.get_run(run)
    assert got.units_ref == {"k": "v"} and got.assembly == {"a": 2} and got.input_json == {"x": 1}
