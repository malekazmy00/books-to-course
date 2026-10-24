"""Small injectable OpenAI-compatible embeddings client with bounded batching."""
from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Sequence
from urllib.request import Request, urlopen


class OpenAIEmbeddingClient:
    def __init__(self, base_url: str, api_key: str, *, model: str = "BAAI/bge-m3",
                 purpose: str | None = None, timeout_s: float = 30.0,
                 batch_size: int = 32, cache_size: int = 512):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("embedding base_url must be http(s)")
        if not api_key.strip() or timeout_s <= 0 or batch_size < 1 or cache_size < 1:
            raise ValueError("embedding credentials, timeout, batch_size and cache_size must be valid")
        self._url = base_url.rstrip("/") + "/embeddings"
        if purpose not in (None, "query", "document"):
            raise ValueError("embedding purpose must be query or document")
        self._key, self._model, self._purpose = api_key, model, purpose
        self._timeout, self._batch_size = timeout_s, batch_size
        self._cache_size = cache_size
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    def __call__(self, text: str) -> Sequence[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding inputs must be non-empty strings")
        missing = list(dict.fromkeys(text for text in texts if text not in self._cache))
        results: dict[str, list[float]] = {text: self._cache[text] for text in texts if text in self._cache}
        for start in range(0, len(missing), self._batch_size):
            batch = missing[start:start + self._batch_size]
            payload = {"model": self._model, "input": batch}
            if self._purpose is not None:
                payload["purpose"] = self._purpose
            body = json.dumps(payload).encode("utf-8")
            req = Request(self._url, data=body, method="POST", headers={
                "Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
            })
            with urlopen(req, timeout=self._timeout) as response:
                payload = json.loads(response.read())
            rows = payload.get("data")
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise ValueError("embedding endpoint returned an invalid vector batch")
            rows = sorted(rows, key=lambda item: item.get("index", -1))
            for text, row in zip(batch, rows):
                vector = row.get("embedding")
                if not isinstance(vector, list) or not vector:
                    raise ValueError("embedding endpoint returned an empty vector")
                self._cache[text] = vector
                results[text] = vector
                self._cache.move_to_end(text)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        # Keep this call's full result independent of the bounded cross-call LRU.
        # A request larger than cache_size must not evict its own earlier batches.
        return [results[text] for text in texts]
