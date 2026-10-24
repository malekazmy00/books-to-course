from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from fastapi.testclient import TestClient
from b2c_contracts.models import StructuredChunk
from b2c_indexing.graph_adapters import RetrievalScope, GraphRetrievalError
from b2c_indexing.graph_runtime import GraphIndexRegistry
from b2c_indexing.graph_api import create_graph_app
from b2c_indexing.graph_router import GraphRetrievalRouter, GraphProviderConfig
from b2c_indexing.graph_native import NativeHippoRAG, NativeRuntimeFactory, NativeRuntimeConfig, _tag


def chunk(cid="a", book="main", role="spine"):
    return StructuredChunk(chunk_id=cid, book_id=book, role=role, section_path=["s"],
        block_ids=[cid], pages=[1], text="Canonical text", token_count=2, embedding_model="none")

class FakeNative:
    def index(self, scope): self.scope = scope
    def retrieve(self, query, top_k):
        return [{"source_id": self.scope.source_id(c), "score": .8} for c in self.scope.chunks][:top_k]

class Factory:
    def __init__(self): self.calls = []
    def __call__(self, engine, directory):
        assert not list(directory.iterdir())
        self.calls.append((engine, directory))
        return FakeNative()


def test_provisioning_is_idempotent_persists_exact_ready_manifest_and_sources(tmp_path):
    factory = Factory()
    registry = GraphIndexRegistry(tmp_path, factory)
    scope = RetrievalScope("c", "g", (chunk(),))
    first = registry.ensure("lightrag", scope)
    assert registry.ensure("lightrag", scope) == first
    assert len(factory.calls) == 1
    path = tmp_path / registry.key("lightrag", scope)
    assert json.loads((path / "manifest.json").read_text()) == first
    assert json.loads((path / "sources.json").read_text())["chunks"][0]["text"] == "Canonical text"


def test_restart_does_not_load_previous_pickle_or_trust_ready_without_live_backend(tmp_path):
    factory = Factory()
    scope = RetrievalScope("c", "g", (chunk(),))
    registry = GraphIndexRegistry(tmp_path, factory)
    registry.ensure("hipporag", scope)
    previous = factory.calls[0][1]
    (previous / "graph.pickle").write_bytes(b"untrusted payload must never be deserialized")
    restarted = GraphIndexRegistry(tmp_path, factory)
    with pytest.raises(GraphRetrievalError, match="not loaded"): restarted.retrieve("hipporag", scope, "q", 5)
    restarted.ensure("hipporag", scope)
    assert factory.calls[1][1] != previous
    assert not list(factory.calls[1][1].iterdir())


def test_concurrent_ensure_creates_only_one_live_index(tmp_path):
    factory = Factory(); registry = GraphIndexRegistry(tmp_path, factory)
    scope = RetrievalScope("c", "g", (chunk(),))
    with ThreadPoolExecutor(4) as executor:
        results = list(executor.map(lambda _: registry.ensure("lightrag", scope), range(8)))
    assert len(factory.calls) == 1 and all(r == results[0] for r in results)


def test_failures_are_persisted_and_retry_uses_new_empty_attempt(tmp_path):
    attempts = []
    class Bad(FakeNative):
        def index(self, scope): raise RuntimeError("secret provider diagnostic")
    def factory(engine, directory):
        attempts.append(directory)
        return Bad() if len(attempts) == 1 else FakeNative()
    registry = GraphIndexRegistry(tmp_path, factory); scope = RetrievalScope("c", "g", (chunk(),))
    with pytest.raises(GraphRetrievalError) as err: registry.ensure("lightrag", scope)
    assert "secret" not in str(err.value)
    saved = json.loads((tmp_path / registry.key("lightrag", scope) / "manifest.json").read_text())
    assert saved["state"] == "failed" and "secret" not in json.dumps(saved)
    assert registry.ensure("lightrag", scope)["state"] == "ready"
    assert attempts[0] != attempts[1]


@pytest.mark.parametrize("engine", ["lightrag", "hipporag"])
def test_router_real_facade_http_preserves_canonical_text_and_book_allowlist(tmp_path, engine):
    factory = Factory()
    client = TestClient(create_graph_app(tmp_path, "test-only", factory))
    sent = []
    def transport(url, body, headers, timeout):
        sent.append(body)
        response = client.post("/" + url.split("/", 3)[3], json=body, headers=headers)
        response.raise_for_status()
        return response.json()
    router = GraphRetrievalRouter("course", "generation", {engine: GraphProviderConfig("http://graph", "test-only")}, transport=transport)
    main, supp = chunk(), chunk("b", "other", "supplementary")
    hits = router.search_candidates("query", [main, supp], mode=engine, allowed_book_ids={"main"})
    assert [h.chunk for h in hits] == [main]
    assert all(body["chunks"] == [main.model_dump(mode="json")] for body in sent)
    assert len(factory.calls) == 1
    router.search_candidates("query", [supp], mode=engine)
    assert len(factory.calls) == 2


def test_authentication_precedes_any_graph_provisioning(tmp_path):
    factory = Factory(); client = TestClient(create_graph_app(tmp_path, "test-only", factory))
    body = {"engine": "lightrag", "course_id": "c", "generation_id": "g", "chunks": [chunk().model_dump(mode="json")]}
    assert client.post("/v1/index/ensure", json=body).status_code == 401
    assert factory.calls == []


def test_course_generation_changes_never_reuse_graph(tmp_path):
    factory = Factory(); registry = GraphIndexRegistry(tmp_path, factory)
    for course, generation in [("c", "g"), ("other", "g"), ("c", "new")]:
        registry.ensure("hipporag", RetrievalScope(course, generation, (chunk(),)))
    assert len(factory.calls) == 3 and len({directory for _, directory in factory.calls}) == 3


def test_router_rejects_non_configured_engine_without_network():
    router = GraphRetrievalRouter("c", "g", {})
    with pytest.raises(GraphRetrievalError, match="not configured"): router.search_candidates("q", [chunk()], mode="hipporag")


def test_symlink_index_root_is_rejected(tmp_path):
    real = tmp_path / "real"; real.mkdir(); link = tmp_path / "link"; link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"): GraphIndexRegistry(link, Factory())


def test_native_factory_refuses_any_preexisting_upstream_serialization(tmp_path):
    (tmp_path / "graph.pickle").write_bytes(b"not loaded")
    factory = NativeRuntimeFactory(NativeRuntimeConfig("http://llm", "m", "key", "http://embed", "e", "key"))
    with pytest.raises(GraphRetrievalError, match="empty"): factory("hipporag", tmp_path)


def test_tagging_identical_text_under_two_books_preserves_upstream_content_hash_identity():
    a, b = chunk(book="a"), chunk(book="b")
    scope = RetrievalScope("c", "g", (a, b))
    assert _tag(scope, a) != _tag(scope, b)
    assert a.text == b.text == "Canonical text"


def test_native_hippo_explicitly_rejects_dense_fallback():
    from types import SimpleNamespace
    native = NativeHippoRAG.__new__(NativeHippoRAG)
    native.client = SimpleNamespace(retrieve=lambda *a, **k: [SimpleNamespace(graph_seeds=[])])
    with pytest.raises(GraphRetrievalError, match="dense-only"): native.retrieve("q", 5)


def test_runtime_cache_is_bounded(tmp_path):
    factory = Factory(); registry = GraphIndexRegistry(tmp_path, factory, max_loaded=1)
    a, b = RetrievalScope("c", "a", (chunk(),)), RetrievalScope("c", "b", (chunk(),))
    registry.ensure("lightrag", a); registry.ensure("lightrag", b)
    assert len(registry._active) == 1
    with pytest.raises(GraphRetrievalError): registry.retrieve("lightrag", a, "q", 5)


def test_status_distinguishes_saved_index_from_loaded_ready_after_restart(tmp_path):
    factory = Factory(); scope = RetrievalScope("c", "g", (chunk(),))
    registry = GraphIndexRegistry(tmp_path, factory)
    assert registry.status("lightrag", scope)["state"] == "not_started"
    registry.ensure("lightrag", scope)
    assert registry.status("lightrag", scope)["state"] == "ready"
    assert GraphIndexRegistry(tmp_path, factory).status("lightrag", scope)["state"] == "rebuild_required"


def test_model_profile_changes_create_new_index_storage_generation(tmp_path):
    a, b = Factory(), Factory()
    a.identity, b.identity = "profile-a", "profile-b"
    scope = RetrievalScope("c", "g", (chunk(),))
    old, new = GraphIndexRegistry(tmp_path, a), GraphIndexRegistry(tmp_path, b)
    assert old.key("lightrag", scope) != new.key("lightrag", scope)
    assert old.ensure("lightrag", scope)["runtime_profile_sha256"] == "profile-a"


def test_native_health_checks_pinned_package_without_loading_model_or_secret(monkeypatch):
    import b2c_indexing.graph_native as module
    monkeypatch.setattr(module, "version", lambda package: "wrong-version")
    factory = NativeRuntimeFactory(NativeRuntimeConfig("http://llm", "model", "/nonexistent", "http://embedding", "embed", "/nonexistent"), enabled_engine="lightrag")
    assert factory.health()["status"] == "unavailable"
    assert factory.health()["engines"]["lightrag"]["expected_version"] == "1.5.7"


def test_http_service_health_failure_is_503_and_does_not_index(tmp_path):
    factory = Factory()
    factory.health = lambda: {"status": "unavailable"}
    client = TestClient(create_graph_app(tmp_path, "test", factory))
    assert client.get("/health").status_code == 503
    assert factory.calls == []
