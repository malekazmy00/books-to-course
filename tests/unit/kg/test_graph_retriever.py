from dataclasses import dataclass

import pytest
from b2c_contracts import StructuredChunk
from b2c_kg.graph_retriever import GraphAdapterConfig, GraphHit, GraphRetrieverAdapter


def chunk(cid: str, book: str, role: str) -> StructuredChunk:
    return StructuredChunk(chunk_id=cid, book_id=book, role=role, section_path=["Lesson"],
                           block_ids=[f"{book}:1:1"], pages=[1], text=f"source {cid}",
                           token_count=2, embedding_model="none")


@dataclass
class FakeBackend:
    hits: list[GraphHit]
    calls: int = 0

    def retrieve(self, query: str, *, top_k: int):
        self.calls += 1
        return self.hits[:top_k]


def test_graph_adapter_is_disabled_by_default_and_does_not_call_backend():
    backend = FakeBackend([GraphHit("c1", 0.9)])
    adapter = GraphRetrieverAdapter(backend)
    with pytest.raises(RuntimeError, match="disabled"):
        adapter.search("query", [chunk("c1", "book1", "spine")])
    assert backend.calls == 0


def test_enabled_adapter_returns_only_authorized_chunks_with_original_provenance():
    backend = FakeBackend([GraphHit("supp", 0.99), GraphHit("unknown", 0.98),
                           GraphHit("spine", 0.97), GraphHit("spine", 0.96)])
    adapter = GraphRetrieverAdapter(backend, config=GraphAdapterConfig(enabled=True))
    result = adapter.search("query", [chunk("spine", "primary", "spine"),
                                       chunk("supp", "extra", "supplementary")],
                            allowed_roles={"spine"}, allowed_book_ids={"primary"})
    assert len(result) == 1
    got, score = result[0]
    assert (got.chunk_id, got.book_id, got.role, got.pages, got.block_ids, score) == (
        "spine", "primary", "spine", [1], ["primary:1:1"], 0.97)


def test_duplicate_authorized_chunk_ids_fail_closed():
    adapter = GraphRetrieverAdapter(FakeBackend([]), config=GraphAdapterConfig(enabled=True))
    with pytest.raises(ValueError, match="duplicate authorized"):
        adapter.search("query", [chunk("same", "book", "spine"), chunk("same", "book", "spine")])


def test_invalid_query_and_top_k_rejected_before_backend_call():
    backend = FakeBackend([])
    adapter = GraphRetrieverAdapter(backend, config=GraphAdapterConfig(enabled=True))
    with pytest.raises(ValueError, match="query"):
        adapter.search("  ", [])
    with pytest.raises(ValueError, match="top_k"):
        adapter.search("query", [], top_k=0)
    assert backend.calls == 0
