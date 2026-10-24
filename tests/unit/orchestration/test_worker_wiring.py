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
