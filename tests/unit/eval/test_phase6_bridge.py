from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "eval" / "suites" / "phase6"))
from hipporag2_bridge import (
    HippoRAG2Backend,
    RuntimeConfig,
    create_isolated_runtime,
    require_pinned_version,
    to_hipporag_chunks,
)
from b2c_contracts import StructuredChunk
from b2c_kg.graph_retriever import GraphAdapterConfig, GraphRetrieverAdapter


@dataclass
class ProviderChunk:
    content: str
    source_id: str
    metadata: dict


@dataclass
class RetrievalResult:
    docs: list[str]
    doc_scores: list[float]
    doc_metadata: list[dict]


class FakeHippo:
    def __init__(self, results=None):
        self.documents = []
        self.results = results or []
        self.index_calls = 0
        self.queries = []

    def index(self, docs):
        self.index_calls += 1
        self.documents = docs

    def retrieve(self, queries, num_to_retrieve):
        self.queries.extend(queries)
        return self.results[:len(queries)]


def source(chunk_id="lane:1", book_id="lane", role="spine", text="source evidence"):
    return {
        "chunk_id": chunk_id,
        "book_id": book_id,
        "role": role,
        "pages": [3, 4],
        "block_ids": [f"{book_id}:3:1"],
        "section_path": ["Chapter 1", "Lesson 1"],
        "text": text,
    }


def indexed_bridge(rows=None, result_override=None):
    rows = rows or [source()]
    docs, source_map = to_hipporag_chunks(rows, scope_id="course-a", chunk_type=ProviderChunk)
    metadata = [dict(doc.metadata, source_id=doc.source_id) for doc in docs]
    results = [RetrievalResult([doc.content for doc in docs], [0.75] * len(docs), metadata)]
    if result_override is not None:
        results = [result_override]
    fake = FakeHippo(results)
    backend = HippoRAG2Backend(fake, sources=source_map)
    backend.index(docs)
    return backend, fake, docs


def test_bridge_indexes_exact_text_with_scope_and_provenance_metadata():
    backend, fake, docs = indexed_bridge()
    assert fake.index_calls == 1
    assert len(docs) == 1
    assert docs[0].content == "source evidence"
    assert docs[0].metadata == {
        "b2c_scope_id": "course-a",
        "b2c_book_id": "lane",
        "b2c_role": "spine",
        "b2c_chunk_id": "lane:1",
        "b2c_pages": [3, 4],
        "b2c_block_ids": ["lane:3:1"],
        "b2c_section_path": ["Chapter 1", "Lesson 1"],
    }
    assert backend.retrieve("question", top_k=2)[0].chunk_id == "lane:1"


def test_bridge_rejects_reindex_and_missing_source_metadata_without_fabrication():
    backend, _, docs = indexed_bridge()
    with pytest.raises(RuntimeError, match="one-shot"):
        backend.index(docs)

    result = RetrievalResult(["source evidence"], [0.7], [{"book_id": "lane"}])
    backend, _, _ = indexed_bridge(result_override=result)
    assert backend.retrieve("question", top_k=1) == []


def test_bridge_fails_closed_on_wrong_scope_role_or_page_provenance():
    bad_cases = [
        {"b2c_scope_id": "course-b"},
        {"b2c_role": "supplementary"},
        {"b2c_pages": [99]},
        {"b2c_block_ids": ["wrong:block"]},
    ]
    for changed in bad_cases:
        row = source()
        provider_id = to_hipporag_chunks([row], scope_id="course-a", chunk_type=ProviderChunk)[0][0].source_id
        info = {
            "source_id": provider_id,
            "b2c_scope_id": "course-a",
            "b2c_book_id": "lane",
            "b2c_role": "spine",
            "b2c_chunk_id": "lane:1",
            "b2c_pages": [3, 4],
            "b2c_block_ids": ["lane:3:1"],
        }
        info.update(changed)
        result = RetrievalResult(["source evidence"], [0.7], [info])
        backend, _, _ = indexed_bridge(result_override=result)
        with pytest.raises(RuntimeError, match="mismatch"):
            backend.retrieve("question", top_k=1)


def test_bridge_rejects_duplicate_text_and_invalid_top_k():
    rows = [source(), source(chunk_id="other", book_id="supp", role="supplementary")]
    with pytest.raises(ValueError, match="content hash collision"):
        to_hipporag_chunks(rows, scope_id="course-a", chunk_type=ProviderChunk)
    backend, _, _ = indexed_bridge()
    with pytest.raises(ValueError, match="top_k"):
        backend.retrieve("question", top_k=0)


def test_legacy_frozen_chunk_without_block_ids_keeps_provenance_absent():
    row = source()
    row.pop("block_ids")
    docs, sources = to_hipporag_chunks([row], scope_id="course-a", chunk_type=ProviderChunk)
    assert docs[0].metadata["b2c_block_ids"] == []
    assert sources[docs[0].source_id].block_ids == ()


def test_bridge_composes_with_b2c_graph_retriever_and_keeps_source_roles():
    rows = [
        source(chunk_id="lane:1", book_id="lane", role="spine", text="primary evidence"),
        source(chunk_id="supp:1", book_id="supp", role="supplementary", text="extra evidence"),
    ]
    docs, source_map = to_hipporag_chunks(rows, scope_id="course-a", chunk_type=ProviderChunk)
    metadata = [dict(doc.metadata, source_id=doc.source_id) for doc in reversed(docs)]
    results = [RetrievalResult([doc.content for doc in reversed(docs)], [0.9, 0.8], metadata)]
    fake = FakeHippo(results)
    backend = HippoRAG2Backend(fake, sources=source_map)
    backend.index(docs)
    retriever = GraphRetrieverAdapter(
        backend, config=GraphAdapterConfig(enabled=True, scope_id="course-a")
    )
    authorized = [
        StructuredChunk(chunk_id=row["chunk_id"], book_id=row["book_id"], role=row["role"],
                        section_path=row["section_path"], block_ids=row["block_ids"],
                        pages=row["pages"], text=row["text"], token_count=2,
                        embedding_model="fixture")
        for row in rows
    ]
    hits = retriever.search("question", authorized, top_k=2, allowed_roles={"spine"})
    assert [(chunk.chunk_id, chunk.book_id, chunk.role, chunk.pages) for chunk, _ in hits] == [
        ("lane:1", "lane", "spine", [3, 4])
    ]

    wrong_scope = GraphRetrieverAdapter(
        backend, config=GraphAdapterConfig(enabled=True, scope_id="course-b")
    )
    assert wrong_scope.search("question", authorized, top_k=2) == []
    unbound = GraphRetrieverAdapter(backend, config=GraphAdapterConfig(enabled=True))
    with pytest.raises(ValueError, match="requires a matching adapter scope_id"):
        unbound.search("question", authorized, top_k=2)


def test_runtime_requires_exact_pinned_version():
    require_pinned_version("2.0.0a5")
    with pytest.raises(RuntimeError, match="requires HippoRAG"):
        require_pinned_version("2.0.0a4")


def test_isolated_runtime_requires_explicit_provider_routes_and_fresh_private_storage(tmp_path):
    root = tmp_path / "phase6"
    root.mkdir()
    config = RuntimeConfig(
        save_dir=root / "run-1",
        allowed_root=root,
        llm_model="",
        llm_base_url="http://local/v1",
        embedding_model="bge-m3",
        embedding_base_url="http://local/v1",
    )
    with pytest.raises(ValueError, match="llm_model"):
        create_isolated_runtime(config)

    config = RuntimeConfig(
        save_dir=root / "run-1",
        allowed_root=root,
        llm_model="local-free-model",
        llm_base_url="http://local/v1",
        embedding_model="bge-m3",
        embedding_base_url="http://local/v1",
    )
    with pytest.raises(ValueError, match="required secret environment"):
        create_isolated_runtime(config)

    (root / "reused").mkdir()
    config = RuntimeConfig(
        save_dir=root / "reused",
        allowed_root=root,
        llm_model="local-free-model",
        llm_base_url="http://local/v1",
        embedding_model="bge-m3",
        embedding_base_url="http://local/v1",
    )
    with pytest.raises(ValueError, match="new, non-symlink"):
        create_isolated_runtime(config)


def test_local_transformers_embeddings_need_no_provider_url_or_embedding_secret(tmp_path, monkeypatch):
    root = tmp_path / "phase6"
    root.mkdir()
    config = RuntimeConfig(
        save_dir=root / "run-1",
        allowed_root=root,
        llm_model="local-free-model",
        llm_base_url="http://llm/v1",
        embedding_model="Transformers//models/bge-m3",
        embedding_provider="transformers",
    )
    monkeypatch.setenv("B2C_PHASE6_LLM_API_KEY", "local-placeholder")
    with pytest.raises(PackageNotFoundError, match="hipporag"):
        create_isolated_runtime(config)
