from fastapi.testclient import TestClient
from b2c_indexing.embeddinggemma_api import create_app, DIMENSIONS, MODEL_ID, windows


def test_prefixes_and_dimensions_and_authentication():
    seen = []
    def factory():
        def encode(text):
            seen.append(text)
            return [1.0] + [0.0] * (DIMENSIONS - 1)
        return encode
    with TestClient(create_app(factory, api_key="unit-test")) as client:
        assert client.get("/health").json()["dimensions"] == 768
        assert client.post("/v1/embeddings", json={"input": "hi"}).status_code == 401
        headers = {"Authorization": "Bearer unit-test"}
        for purpose in ["query", "document"]:
            result = client.post("/v1/embeddings", headers=headers,
                json={"input": ["hello", "world"], "purpose": purpose, "model": MODEL_ID})
            assert result.status_code == 200
            assert len(result.json()["data"]) == 2
            assert len(result.json()["data"][0]["embedding"]) == DIMENSIONS
        assert seen[0] == "task: search query | text: hello"
        assert seen[2] == "task: search result | text: hello"
        assert client.post("/v1/embeddings", headers=headers,
            json={"input": "hi", "model": "BAAI/bge-m3"}).status_code == 422
        assert client.post("/v1/embeddings", headers=headers,
            json={"input": "hi", "dimensions": 1024}).status_code == 422
        assert client.post("/v1/embeddings", headers=headers,
            json={"input": ["a"] * 33}).status_code == 422


def test_bad_engine_vector_fails_without_exposing_internals():
    with TestClient(create_app(lambda: lambda _: [float("nan")] * DIMENSIONS,
                               api_key="unit-test")) as client:
        response = client.post("/v1/embeddings", headers={"Authorization": "Bearer unit-test"},
                               json={"input": "hello"})
        assert response.status_code == 502


def test_windowing_keeps_long_words_and_all_content():
    text = "abc " + "x" * 1600 + " def"
    parts = windows(text)
    assert all(len(part) <= 768 for part in parts)
    assert "".join(parts) == text.replace(" ", "")
