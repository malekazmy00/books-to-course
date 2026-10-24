from __future__ import annotations

import io
import json

import pytest

from b2c_indexing.embedding_client import OpenAIEmbeddingClient


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_embedding_client_batches_and_sorts_response_by_index(monkeypatch):
    seen = []
    def fake_urlopen(request, timeout):
        seen.append((request, timeout, json.loads(request.data)))
        payload = {"data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]}
        return Response(json.dumps(payload).encode())
    monkeypatch.setattr("b2c_indexing.embedding_client.urlopen", fake_urlopen)
    client = OpenAIEmbeddingClient("http://embed:8000/v1", "test-secret", batch_size=2)
    vectors = client.embed_many(["a", "b"])
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    request, timeout, body = seen[0]
    assert request.full_url == "http://embed:8000/v1/embeddings"
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert timeout == 30 and body["input"] == ["a", "b"]
    assert client.embed_many(["a"])[0] == [1.0, 0.0]
    assert len(seen) == 1


def test_embedding_client_request_larger_than_lru_cache_returns_every_vector(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        rows = [{"index": i, "embedding": [float(int(text[1:]))]} for i, text in enumerate(body["input"])]
        return Response(json.dumps({"data": rows}).encode())

    monkeypatch.setattr("b2c_indexing.embedding_client.urlopen", fake_urlopen)
    client = OpenAIEmbeddingClient("http://embed:8000/v1", "test-secret", batch_size=73, cache_size=512)
    texts = [f"t{i}" for i in range(600)]
    vectors = client.embed_many(texts)
    assert vectors == [[float(i)] for i in range(600)]
    # Repeated keys retain input order even when the request itself exceeds the LRU.
    assert client.embed_many(["t0", "t599", "t0"]) == [[0.0], [599.0], [0.0]]


def test_embedding_client_sends_query_or_document_purpose(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(json.loads(request.data))
        return Response(json.dumps({"data": [{"index": 0, "embedding": [1.0]}]}).encode())

    monkeypatch.setattr("b2c_indexing.embedding_client.urlopen", fake_urlopen)
    for purpose in ("query", "document"):
        client = OpenAIEmbeddingClient("http://embed:8000/v1", "test-secret",
                                       model="embeddinggemma-2-text-270m", purpose=purpose)
        assert client("text") == [1.0]
    assert [body["purpose"] for body in seen] == ["query", "document"]

def test_embedding_client_rejects_bad_endpoint_and_empty_key():
    with pytest.raises(ValueError, match=r"http\(s\)"):
        OpenAIEmbeddingClient("file:///tmp/embed", "secret")
    with pytest.raises(ValueError, match="credentials"):
        OpenAIEmbeddingClient("http://embed/v1", " ")
