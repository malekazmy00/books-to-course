from collections import defaultdict
import pytest
from b2c_contracts.models import StructuredChunk
from b2c_indexing import QueryRetrievalService

def chunk(chunk_id, book, role, text):
    return StructuredChunk(chunk_id=chunk_id, book_id=book, role=role, section_path=["unit"],
        block_ids=[chunk_id], pages=[1], text=text, token_count=len(text.split()), embedding_model="unassigned")

class ScopedMemoryStore:
    def __init__(self, rows_by_course): self.rows_by_course, self.calls = rows_by_course, []
    def list_chunks(self, course_id, *, book_ids=None, roles=None):
        self.calls.append((course_id, book_ids, roles))
        if not course_id.strip(): raise ValueError("course required")
        return [c for c in self.rows_by_course.get(course_id, [])
                if (book_ids is None or c.book_id in book_ids) and (roles is None or c.role in roles)]

def test_query_retrieval_is_course_scoped_and_defaults_to_lexical_only():
    store = ScopedMemoryStore({
        "course-a": [chunk("a:1", "main", "spine", "Bayes theorem probability")],
        "course-b": [chunk("b:1", "main", "spine", "Bayes theorem probability")]})
    result = QueryRetrievalService(store).search("course-a", "Bayes theorem")
    assert [hit.chunk.chunk_id for hit in result] == ["a:1"]
    assert result[0].method == "lexical" and store.calls == [("course-a", None, None)]
    assert QueryRetrievalService(store).search("course-b", "Bayes theorem")[0].chunk.chunk_id == "b:1"
    with pytest.raises(ValueError, match="injected"):
        QueryRetrievalService(store).search("course-a", "Bayes", mode="dense")

def test_book_and_role_allow_lists_reach_store_and_constrain_results():
    spine = chunk("main:1", "main", "spine", "Bayes probability")
    supplement = chunk("extra:1", "extra", "supplementary", "Bayes probability")
    store = ScopedMemoryStore({"c": [spine, supplement]})
    hits = QueryRetrievalService(store).search("c", "Bayes", allowed_book_ids={"main"}, allowed_roles={"spine"})
    assert [hit.chunk.chunk_id for hit in hits] == ["main:1"]
    assert store.calls == [("c", {"main"}, {"spine"})]

def test_query_service_defensively_filters_faulty_store_before_embedding():
    allowed = chunk("a:1", "main", "spine", "Bayes conditional probability")
    leaked = chunk("x:1", "other", "supplementary", "Bayes other probability")
    class LeakyStore:
        def list_chunks(self, course_id, *, book_ids=None, roles=None): return [allowed, leaked]
    seen = []
    service = QueryRetrievalService(LeakyStore(), embed_query=lambda _: [1.0, 0.0],
        embed_passage=lambda text: (seen.append(text) or [1.0, 0.0]))
    hits = service.search("c", "Bayes", allowed_book_ids={"main"}, allowed_roles={"spine"}, mode="dense")
    assert [hit.chunk.chunk_id for hit in hits] == ["a:1"] and seen == [allowed.text]

def test_hybrid_uses_injected_dense_adapter_and_preserves_scope_with_duplicate_ids():
    a = chunk("shared:1", "main", "spine", "Bayes conditional probability")
    b = chunk("shared:1", "extra", "supplementary", "Bayes prior probability")
    store = ScopedMemoryStore({"c": [a, b]})
    seen = []
    def embed(text):
        seen.append(text)
        return [1.0, 0.0] if "conditional" in text else [0.0, 1.0]
    service = QueryRetrievalService(store, embed_query=lambda _: [1.0, 0.0], embed_passage=embed)
    hits = service.search("c", "Bayes probability", top_k=5, allowed_book_ids={"main", "extra"},
                          allowed_roles={"spine", "supplementary"}, mode="hybrid")
    assert len(hits) == 2
    assert {(h.chunk.chunk_id, h.chunk.book_id) for h in hits} == {("shared:1", "main"), ("shared:1", "extra")}
    assert all(h.method == "rrf" for h in hits) and set(seen) == {a.text, b.text}

@pytest.mark.parametrize("kwargs", [
    {"course_id": ""}, {"query": ""}, {"top_k": 0}, {"mode": "unknown"}, {"allowed_roles": {"admin"}}])
def test_invalid_query_scope_or_options_are_rejected(kwargs):
    store = ScopedMemoryStore(defaultdict(list))
    course, query = kwargs.pop("course_id", "c"), kwargs.pop("query", "query")
    with pytest.raises(ValueError): QueryRetrievalService(store).search(course, query, **kwargs)


def test_lexical_abstention_threshold_is_optional_and_never_assumed_for_other_modes():
    store = ScopedMemoryStore({"c": [chunk("a:1", "main", "spine", "Bayes theorem probability")]})
    service = QueryRetrievalService(store)
    default = service.search("c", "Bayes theorem")
    assert len(default) == 1
    assert service.search("c", "Bayes theorem", min_score=default[0].score + 0.01) == []
    assert len(service.search("c", "Bayes theorem", min_score=default[0].score)) == 1
    hybrid = QueryRetrievalService(store, embed_query=lambda _: [1.0], embed_passage=lambda _: [1.0])
    with pytest.raises(ValueError, match="separate calibration"):
        hybrid.search("c", "Bayes theorem", mode="hybrid", min_score=1.0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "11"])
def test_lexical_threshold_rejects_non_finite_or_non_numeric_values(value):
    service=QueryRetrievalService(ScopedMemoryStore({"c":[chunk("c1","b","spine","Probability theorem example")]}))
    with pytest.raises(ValueError,match="finite number"):
        service.search("c","probability",min_score=value)


def test_search_candidates_uses_bounded_set_and_batch_embeddings_for_hybrid():
    spine = chunk("shared", "main", "spine", "chance and probability")
    supplement = chunk("shared", "extra", "supplementary", "chance and independent events")
    outside = chunk("other", "other", "supplementary", "chance probability")
    class BatchEmbedder:
        def __init__(self):
            self.batches = []
        def __call__(self, text):
            return [1.0, 0.0]
        def embed_many(self, texts):
            self.batches.append(list(texts))
            return [[1.0, 0.0] for _ in texts]
    embedder = BatchEmbedder()
    service = QueryRetrievalService(ScopedMemoryStore({}),
        embed_query=lambda _: [1.0, 0.0], embed_passage=embedder)
    hits = service.search_candidates("chance", [spine, supplement, outside], top_k=2,
        mode="hybrid", allowed_book_ids={"main", "extra"},
        allowed_roles={"spine", "supplementary"})
    assert {(hit.chunk.book_id, hit.chunk.role) for hit in hits} == {
        ("main", "spine"), ("extra", "supplementary")}
    assert all(hit.method == "rrf" for hit in hits)
    assert len(embedder.batches) == 1 and set(embedder.batches[0]) == {
        spine.text, supplement.text}
