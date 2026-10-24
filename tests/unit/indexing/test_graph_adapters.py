import pytest
from b2c_contracts.models import StructuredChunk
from b2c_indexing.graph_adapters import RetrievalScope, LightRAGAdapter, HippoRAGAdapter, GraphRetrievalError


def chunk(cid, book="main", text="Canonical text"):
    return StructuredChunk(chunk_id=cid, book_id=book, role="spine", section_path=["lesson"],
        block_ids=[cid], pages=[1], text=text, token_count=2, embedding_model="none")


def setup_light(rows=None):
    scope = RetrievalScope("course", "generation", (chunk("a"), chunk("b")))
    calls = []
    def transport(url, body, headers, timeout):
        calls.append(body)
        return {"status": "success", "data": {"chunks": rows if rows is not None else [
            {"file_path": scope.source_id(scope.chunks[1]), "content": "fabricated provider text"}]}}
    adapter = LightRAGAdapter(scope, scope.manifest("lightrag", "1.5.7"), base_url="http://dedicated:9621", api_key="test", transport=transport)
    return scope, calls, adapter


def test_lightrag_returns_canonical_content_and_retrieval_only_request():
    scope, calls, adapter = setup_light()
    result = adapter.search_candidates("query", scope.chunks)
    assert result[0].chunk == scope.chunks[1]
    assert result[0].chunk.text == "Canonical text"
    assert result[0].method == "lightrag"
    assert calls[0]["only_need_context"] is True


@pytest.mark.parametrize("case", ["subset", "text", "book", "role"])
def test_scope_is_checked_before_any_provider_request(case):
    scope, calls, adapter = setup_light()
    candidates, kwargs = scope.chunks, {}
    if case == "subset": candidates = scope.chunks[:1]
    if case == "text": candidates = (chunk("a", text="changed"), scope.chunks[1])
    if case == "book": kwargs = {"allowed_book_ids": {"different"}}
    if case == "role": kwargs = {"allowed_roles": {"supplementary"}}
    with pytest.raises(GraphRetrievalError): adapter.search_candidates("query", candidates, **kwargs)
    assert calls == []


def test_unknown_lightrag_source_fails_without_lexical_fallback():
    scope, calls, adapter = setup_light([{"file_path": "other-course"}])
    with pytest.raises(GraphRetrievalError, match="outside"): adapter.search_candidates("query", scope.chunks)


@pytest.mark.parametrize("mutation", [{"state": "building"}, {"course_id": "foreign"}, {"generation_id": "old"}, {"version": "other"}, {"source_ids": []}])
def test_manifest_must_be_ready_exact_course_generation_and_version(mutation):
    scope = RetrievalScope("c", "g", (chunk("a"),))
    manifest = scope.manifest("lightrag", "1.5.7") | mutation
    with pytest.raises(GraphRetrievalError): LightRAGAdapter(scope, manifest, base_url="http://dedicated", api_key="test")


def test_source_id_preserves_duplicate_text_and_duplicate_chunk_id_across_books():
    scope = RetrievalScope("c", "g", (chunk("shared", "a"), chunk("shared", "b")))
    assert len(set(scope.manifest("hipporag", "2.0.0a5")["source_ids"])) == 2


@pytest.mark.parametrize("fault", [None, "fallback", "scope", "version", "source", "score", "error"])
def test_hippo_contract_rejects_lost_scope_fallback_and_invalid_hits(fault):
    scope = RetrievalScope("c", "g", (chunk("a"),))
    class Backend:
        def retrieve(self, query, **kwargs):
            assert kwargs == {"course_id": "c", "generation_id": "g", "corpus_sha256": scope.corpus_sha256, "top_k": 10}
            if fault == "error": raise RuntimeError("provider detail containing a secret")
            payload = {"course_id": "c", "generation_id": "g", "corpus_sha256": scope.corpus_sha256,
                "version": "2.0.0a5", "method": "hipporag", "fallback": False,
                "hits": [{"source_id": scope.source_id(scope.chunks[0]), "score": .9}]}
            if fault == "fallback": payload["fallback"] = True
            if fault == "scope": payload["course_id"] = "foreign"
            if fault == "version": payload["version"] = "new"
            if fault == "source": payload["hits"][0]["source_id"] = "unknown"
            if fault == "score": payload["hits"][0]["score"] = float("nan")
            return payload
    adapter = HippoRAGAdapter(scope, scope.manifest("hipporag", "2.0.0a5"), backend=Backend())
    if fault:
        with pytest.raises(GraphRetrievalError) as err: adapter.search_candidates("q", scope.chunks)
        assert "secret" not in str(err.value)
    else:
        assert adapter.search_candidates("q", scope.chunks)[0].chunk == scope.chunks[0]


def test_provider_timeout_is_explicit_and_redacts_exception_detail():
    scope, calls, adapter = setup_light()
    def bad(*args): raise TimeoutError("secret")
    adapter._transport = bad
    with pytest.raises(GraphRetrievalError) as err: adapter.search_candidates("q", scope.chunks)
    assert "secret" not in str(err.value)
