import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from b2c_api.model_lifecycle import ModelLifecycle, Provider, lifecycle_router


def local(provider_id="gemma"):
    return Provider(id=provider_id, label="Test model", stages=["questions", "translation"],
                    kind="local", adapter="ollama", base_url="http://test-ollama:11434", model=provider_id, task_queue="heavy")


class FakeAdapter:
    def __init__(self):
        self.loaded = set()
        self.calls = []
        self.release = asyncio.Event()
        self.fail = False

    async def ready(self, p):
        return p.id in self.loaded

    async def load(self, p, *, pinned=False):
        self.calls.append((p.id, pinned))
        await self.release.wait()
        if self.fail:
            raise RuntimeError("secret-must-not-be-returned")
        self.loaded.add(p.id)


@pytest.mark.asyncio
async def test_loading_is_deduplicated_and_ready_requires_real_probe():
    adapter = FakeAdapter()
    manager = ModelLifecycle([local()], adapter)
    assert (await manager.activate("gemma"))["state"] == "starting"
    await asyncio.sleep(0)
    assert (await manager.status("gemma"))["state"] == "loading"
    await manager.activate("gemma")
    assert len(adapter.calls) == 1
    adapter.release.set()
    await manager.tasks["gemma"]
    assert (await manager.status("gemma"))["state"] == "ready"
    adapter.loaded.clear()
    assert (await manager.status("gemma"))["state"] == "inactive"
    await manager.close()


@pytest.mark.asyncio
async def test_failure_and_retry_preserve_sanitized_state():
    adapter = FakeAdapter()
    adapter.release.set()
    adapter.fail = True
    manager = ModelLifecycle([local()], adapter)
    await manager.activate("gemma")
    await manager.tasks["gemma"]
    assert manager.snapshot("gemma")["state"] == "failed"
    assert (await manager.status("gemma"))["state"] == "failed"
    assert "secret" not in json.dumps(manager.snapshot("gemma"))
    adapter.fail = False
    await manager.activate("gemma")
    await manager.tasks["gemma"]
    assert manager.snapshot("gemma")["state"] == "ready"
    await manager.close()


@pytest.mark.asyncio
async def test_default_persists_and_alternate_does_not_unpin_it(tmp_path):
    adapter = FakeAdapter()
    adapter.release.set()
    prefs = str(tmp_path / "preferences.json")
    manager = ModelLifecycle([local(), local("hy")], adapter, prefs)
    await manager.set_default("questions", "gemma")
    await manager.activate("hy")
    await manager.tasks["hy"]
    assert manager.snapshot("gemma")["default_for_stages"] == ["questions"]
    assert ("hy", False) in adapter.calls
    assert json.loads((tmp_path / "preferences.json").read_text())["defaults"]["questions"] == "gemma"
    await manager.set_default("questions", "hy")
    assert ("gemma", False) in adapter.calls  # idle expiration, no forced stop
    restored = ModelLifecycle([local(), local("hy")], adapter, prefs)
    assert restored.defaults["questions"] == "hy"
    await manager.close()


def test_authenticated_routes_and_catalog_do_not_expose_configuration():
    provider = local()
    provider.key_file = "/secret/key-file"
    manager = ModelLifecycle([provider], FakeAdapter())
    app = FastAPI()
    app.include_router(lifecycle_router(manager, "test-token"))
    with TestClient(app) as client:
        assert client.get("/v1/model-providers").status_code == 401
        response = client.get("/v1/model-providers", headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 200
        body = response.json()
        assert body["job_switching_supported"] is False
        assert "test-ollama" not in response.text
        assert "key-file" not in response.text
        assert client.post("/v1/model-providers/unknown/activate", headers={"Authorization": "Bearer test-token"}).status_code == 404


@pytest.mark.asyncio
async def test_unsupported_services_never_report_success_or_start_commands():
    p = Provider(id="fasih", label="Fasih", stages=["audio"], kind="local")
    adapter = FakeAdapter()
    manager = ModelLifecycle([p], adapter)
    assert (await manager.activate("fasih"))["state"] == "unsupported"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_stage_defaults_are_independent_and_invalid_stage_is_rejected(tmp_path):
    from fastapi import HTTPException
    adapter = FakeAdapter()
    adapter.release.set()
    manager = ModelLifecycle([local(), local("hy")], adapter, str(tmp_path / "prefs.json"))
    await manager.set_default("questions", "gemma")
    await manager.set_default("translation", "hy")
    assert manager.defaults == {"questions": "gemma", "translation": "hy"}
    assert ("gemma", False) not in adapter.calls
    with pytest.raises(HTTPException) as exc:
        await manager.set_default("audio", "gemma")
    assert exc.value.status_code == 409
    await manager.close()


@pytest.mark.asyncio
async def test_default_failure_preserves_previous_preference(tmp_path):
    from fastapi import HTTPException
    adapter = FakeAdapter()
    adapter.release.set()
    manager = ModelLifecycle([local(), local("hy")], adapter, str(tmp_path / "prefs.json"))
    await manager.set_default("translation", "gemma")
    adapter.fail = True
    with pytest.raises(HTTPException):
        await manager.set_default("translation", "hy")
    assert manager.defaults == {"translation": "gemma"}
    assert json.loads((tmp_path / "prefs.json").read_text())["defaults"] == manager.defaults
    await manager.close()


@pytest.mark.asyncio
async def test_retrieval_engine_can_be_selected_as_durable_rag_default(tmp_path):
    engine = Provider(id="lightrag-157", label="LightRAG", stages=["rag"], kind="local",
                      adapter="health", base_url="http://graph:9630", model="lightrag-1.5.7",
                      option_type="retrieval_engine", evaluation_status="tested")
    adapter = FakeAdapter()
    adapter.loaded.add(engine.id)
    manager = ModelLifecycle([engine], adapter, str(tmp_path / "prefs.json"))
    await manager.set_default("rag", engine.id)
    assert manager.defaults == {"rag": engine.id}
    assert manager.snapshot(engine.id)["default_for_stages"] == ["rag"]
    assert json.loads((tmp_path / "prefs.json").read_text())["defaults"] == {"rag": engine.id}
    await manager.close()


@pytest.mark.asyncio
async def test_external_credentials_are_verified_persisted_and_not_returned(tmp_path):
    from pydantic import SecretStr
    external = Provider(id="external", label="External", stages=["questions"], kind="external",
                        adapter="openai", base_url="https://approved.example/v1", model="old-model")
    adapter = FakeAdapter()
    adapter.loaded.add("external")
    catalog = tmp_path / "catalog.json"
    manager = ModelLifecycle([external], adapter, catalog_file=str(catalog), secrets_dir=str(tmp_path))
    result = await manager.register_credentials("external", "new-model", SecretStr("test-secret-value"))
    assert result["state"] == "ready"
    assert "test-secret-value" not in json.dumps(result)
    stored = json.loads(catalog.read_text())
    assert stored[0]["model"] == "new-model"
    path = __import__("pathlib").Path(stored[0]["key_file"])
    assert path.read_text() == "test-secret-value"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "test-secret-value" not in catalog.read_text()


@pytest.mark.asyncio
async def test_failed_external_probe_leaves_no_secret_or_changed_catalog(tmp_path):
    from fastapi import HTTPException
    from pydantic import SecretStr
    external = Provider(id="external", label="External", stages=["questions"], kind="external",
                        adapter="openai", base_url="https://approved.example/v1", model="old-model")
    catalog = tmp_path / "catalog.json"
    catalog.write_text("[]")
    manager = ModelLifecycle([external], FakeAdapter(), catalog_file=str(catalog), secrets_dir=str(tmp_path))
    with pytest.raises(HTTPException):
        await manager.register_credentials("external", "new-model", SecretStr("test-secret-value"))
    assert catalog.read_text() == "[]"
    assert list(tmp_path.glob("*.key")) == []
    assert manager.provider("external").model == "old-model"


@pytest.mark.asyncio
async def test_embedding_loader_and_real_adapter_model_identity_checks():
    from b2c_api.model_lifecycle import HTTPAdapter
    p = local()
    p.load_mode = "embedding"
    adapter = HTTPAdapter()
    calls = []
    async def request(provider, path, body=None):
        calls.append((path, body))
        return {"models": [{"name": "other"}]} if path == "/api/ps" else {}
    adapter.request = request
    await adapter.load(p, pinned=True)
    assert calls == [("/api/embed", {"model": "gemma", "keep_alive": -1, "input": ""})]
    assert await adapter.ready(p) is False


def test_gemma_route_is_selectable_only_when_its_runtime_overlay_is_enabled(monkeypatch):
    from types import SimpleNamespace
    from b2c_api.model_lifecycle import configured_providers

    settings = SimpleNamespace(
        llm_allow_remote=False,
        llm_base_url="http://llm:8080/v1",
        llm_model_id="local",
        llm_api_key_file="",
    )
    monkeypatch.delenv("B2C_GEMMA4_ENABLED", raising=False)
    disabled = next(p for p in configured_providers(settings) if p.id == "gemma4-e4b-local")
    assert disabled.task_queue is None
    assert disabled.public()["job_switching_supported"] is False
    assert disabled.evaluation_status == "limited_test"

    monkeypatch.setenv("B2C_GEMMA4_ENABLED", "true")
    enabled = next(p for p in configured_providers(settings) if p.id == "gemma4-e4b-local")
    assert enabled.task_queue == "gemma4-generation"
    assert enabled.public()["job_switching_supported"] is True
    assert "Ollama" in enabled.evaluation_note


def test_booktrans_pro_profiles_are_separate_opt_in_translation_routes(monkeypatch):
    from types import SimpleNamespace
    from b2c_api.model_lifecycle import configured_providers

    settings = SimpleNamespace(
        llm_allow_remote=False, llm_base_url="http://llm:8080/v1",
        llm_model_id="local", llm_api_key_file="",
    )
    monkeypatch.setenv("B2C_GENERATION_PROVIDER_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("B2C_GENERATION_MODEL_ID", "deepseek/model-small")
    monkeypatch.setenv("B2C_GENERATION_PROVIDER_KEY_FILE", "/run/secrets/external.key")
    monkeypatch.setenv("B2C_GENERATION_TASK_QUEUE", "external-generation")
    monkeypatch.delenv("B2C_BOOKTRANS_PRO_ENABLED", raising=False)
    providers = {p.id: p for p in configured_providers(settings)}
    assert "booktrans-pro-external" not in providers

    monkeypatch.setenv("B2C_BOOKTRANS_PRO_ENABLED", "true")
    monkeypatch.setenv("B2C_GEMMA4_ENABLED", "true")
    providers = {p.id: p for p in configured_providers(settings)}
    external = providers["booktrans-pro-external"]
    local = providers["booktrans-pro-gemma4-local"]
    assert external.task_queue == "external-generation"
    assert external.model == "deepseek/model-small"
    assert external.translation_engine == local.translation_engine == "booktrans-pro"
    assert external.public()["job_switching_supported"] is True
    assert local.task_queue == "gemma4-generation"
    assert local.base_url.endswith("/v1")
@pytest.mark.asyncio
async def test_configured_external_generation_route_is_selectable_and_durable(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from b2c_api.model_lifecycle import configured_providers

    monkeypatch.setenv("B2C_GENERATION_TASK_QUEUE", "external-generation")
    monkeypatch.setenv("B2C_GENERATION_PROVIDER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("B2C_GENERATION_MODEL_ID", "deepseek/deepseek-v4-flash-0731")
    monkeypatch.setenv("B2C_GENERATION_PROVIDER_KEY_FILE", "/run/secrets/openrouter_api_key")
    settings = SimpleNamespace(llm_allow_remote=False, llm_base_url="http://llm:8080/v1",
                               llm_model_id="local", llm_api_key_file="")
    provider = next(p for p in configured_providers(settings) if p.id == "configured-llm")
    assert provider.kind == "external"
    assert provider.model == "deepseek/deepseek-v4-flash-0731"
    assert provider.task_queue == "external-generation"
    assert "enrichment" not in provider.stages
    assert provider.public()["job_switching_supported"] is True

    adapter = FakeAdapter()
    adapter.release.set()
    adapter.loaded.add(provider.id)
    manager = ModelLifecycle([provider], adapter, str(tmp_path / "preferences.json"))
    await manager.set_default("questions", provider.id)
    assert manager.defaults["questions"] == provider.id
    assert json.loads((tmp_path / "preferences.json").read_text())["defaults"]["questions"] == provider.id
    await manager.close()
