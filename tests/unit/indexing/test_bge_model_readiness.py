"""Readiness must enumerate models without calling embedding inference."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from fastapi.testclient import TestClient


def test_bge_authenticated_model_list_without_inference(monkeypatch):
    torch = ModuleType("torch")
    torch.set_num_threads = lambda _: None
    torch.nn = ModuleType("torch.nn")
    torch.nn.functional = ModuleType("torch.nn.functional")
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch.nn", torch.nn)
    monkeypatch.setitem(sys.modules, "torch.nn.functional", torch.nn.functional)
    transformers = ModuleType("transformers")

    def no_inference(*args, **kwargs):
        raise AssertionError("model listing must not invoke inference")

    class StubFactory:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return SimpleNamespace(eval=lambda: None, __call__=no_inference)

    transformers.AutoModel = transformers.AutoTokenizer = StubFactory
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setenv("B2C_EMBEDDING_API_KEY_FILE", "")
    monkeypatch.setenv("B2C_LIGHTRAG_EMBED_KEY", "unit-test-key")
    path = Path(__file__).parents[3] / "packages/indexing/src/b2c_indexing/embedding_api.py"
    spec = importlib.util.spec_from_file_location("b2c_bge_readiness_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get("/v1/models", headers={"Authorization": "Bearer unit-test-key"})
    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": [
        {"id": "BAAI/bge-m3", "object": "model", "owned_by": "local"}]}
