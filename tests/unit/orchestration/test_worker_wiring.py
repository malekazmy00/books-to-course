"""Worker process wiring (no Temporal/Postgres/S3: collaborators are faked)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import b2c_orchestration.worker as worker_mod


def test_audio_worker_store_records_artifact_provenance(monkeypatch):
    created = {}

    class FakeProv:
        def __init__(self, dsn):
            created["prov"] = self
            self.migrated = False

        def migrate(self):
            self.migrated = True

    class FakeStore:
        @classmethod
        def from_settings(cls, settings, recorder=None):
            store = cls()
            store.recorder = recorder
            created["store"] = store
            return store

        def ensure_bucket(self):
            pass

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            return None

    async def connect(settings):
        return object()

    settings = SimpleNamespace(log_level="INFO", service_name="b2c", otel_endpoint="", pg_dsn="postgresql://x")
    monkeypatch.setattr(worker_mod, "Settings", lambda: settings)
    monkeypatch.setattr(worker_mod, "configure_logging", lambda level: None)
    monkeypatch.setattr(worker_mod, "setup_tracing", lambda *a: SimpleNamespace(shutdown=lambda: None))
    monkeypatch.setattr(worker_mod, "ProvRecorder", FakeProv)
    monkeypatch.setattr(worker_mod, "ArtifactStore", FakeStore)
    monkeypatch.setattr(worker_mod, "Worker", FakeWorker)
    monkeypatch.setattr(worker_mod, "connect", connect)

    asyncio.run(worker_mod.run_audio_worker())

    assert created["store"].recorder is created["prov"]
    assert created["prov"].migrated


# ------------------------------------------------------------------ D-028 part D
def test_translate_book_worker_registers_new_names_on_its_own_queue(monkeypatch):
    from b2c_orchestration.queues import TRANSLATE_BOOK, max_concurrent_activities
    from b2c_orchestration.translate_book import AssembleBookTranslationWorkflow, TranslateBookWorkflow

    captured = {}

    class FakeWorker:
        def __init__(self, client, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(worker_mod, "Worker", FakeWorker)
    monkeypatch.delenv("B2C_TRANSLATION_PROFILES_FILE", raising=False)
    settings = SimpleNamespace(pg_dsn="postgresql://x", llm_base_url="http://llm:8080/v1", llm_model_id="m",
                               llm_allow_remote=False, llm_api_key_file="", llm_reasoning_effort=None)
    worker_mod.translate_book_worker(object(), settings, store=SimpleNamespace(bucket="artifacts"), cache=None,
                                     executor=object())
    assert captured["task_queue"] == TRANSLATE_BOOK
    assert captured["workflows"] == [TranslateBookWorkflow, AssembleBookTranslationWorkflow]
    names = sorted(fn.__temporal_activity_definition.name for fn in captured["activities"])
    assert names == ["assemble_translation", "build_book_termbase", "check_book_termbase",
                     "prepare_translation_units", "translate_unit_batch"]
    assert captured["activities"][0].__self__.termbase_store is not None
    assert captured["max_concurrent_activities"] == max_concurrent_activities(TRANSLATE_BOOK) == 1


def test_invalid_translation_profiles_do_not_stop_the_worker(monkeypatch, tmp_path):
    import pytest
    from b2c_orchestration.translation_profiles import UnknownProfile

    captured = {}

    class FakeWorker:
        def __init__(self, client, **kwargs):
            captured.update(kwargs)

    bad = tmp_path / "profiles.json"
    bad.write_text("{not json")
    monkeypatch.setattr(worker_mod, "Worker", FakeWorker)
    monkeypatch.setenv("B2C_TRANSLATION_PROFILES_FILE", str(bad))
    settings = SimpleNamespace(pg_dsn="postgresql://x", llm_base_url="http://llm:8080/v1", llm_model_id="m",
                               llm_allow_remote=False, llm_api_key_file="", llm_reasoning_effort=None)
    worker_mod.translate_book_worker(object(), settings, store=SimpleNamespace(bucket="artifacts"), cache=None,
                                     executor=object())
    runtime = captured["activities"][0].__self__.runtime
    with pytest.raises(UnknownProfile):
        runtime.engine("hy-mt2")
    with pytest.raises(UnknownProfile):
        runtime.term_proposer("hy-mt2")


def test_translation_gateway_factory_stays_local_by_default():
    import pytest
    from b2c_llm_gateway import CloudProviderNotAllowed
    from b2c_orchestration.translation_profiles import EngineProfile

    make = worker_mod.translation_gateway_factory(cache=None)
    local = make(EngineProfile(id="x", kind="chat", provider="p", base_url="http://llm:8080/v1", model_id="m"))
    assert local.model_id == "m"
    with pytest.raises(CloudProviderNotAllowed):
        make(EngineProfile(id="y", kind="chat", provider="p", base_url="https://api.example.com/v1", model_id="m"))
