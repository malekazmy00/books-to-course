"""Worker-facing graph router; endpoints are exclusively administrator config."""
from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any
from b2c_contracts.models import StructuredChunk
from .graph_adapters import RetrievalScope, HippoRAGAdapter, GraphRetrievalError, http_json
from .query_service import RetrievalHit

ENGINE_IDS = {"lightrag-157": "lightrag", "hipporag-2": "hipporag", "lightrag": "lightrag", "hipporag": "hipporag"}

@dataclass(frozen=True)
class GraphProviderConfig:
    base_url: str
    api_key: str = field(repr=False)
    timeout_s: float = 900

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("invalid administrator graph endpoint")
        if not self.api_key.strip() or self.timeout_s <= 0: raise ValueError("invalid graph credential or timeout")

    @staticmethod
    def from_env() -> dict[str, GraphProviderConfig]:
        providers = {}
        for engine in ("lightrag", "hipporag"):
            prefix = "B2C_" + engine.upper() + "_SERVICE_"
            url, file = os.getenv(prefix + "URL", ""), os.getenv(prefix + "KEY_FILE", "")
            if url or file:
                if not url or not file: raise ValueError("graph service requires both URL and key file")
                providers[engine] = GraphProviderConfig(url, Path(file).read_text(encoding="utf-8").strip())
        return providers

class GraphRetrievalRouter:
    def __init__(self, course_id: str, generation_id: str, providers: Mapping[str, GraphProviderConfig],
                 *, transport: Callable[..., Mapping[str, Any]] = http_json):
        if not course_id.strip() or not generation_id.strip(): raise ValueError("graph course and generation required")
        self.course_id, self.generation_id = course_id, generation_id
        if any(k not in ENGINE_IDS for k in providers): raise ValueError("unknown configured graph provider")
        self.providers = {ENGINE_IDS[k]: v for k, v in providers.items()}
        self.transport = transport

    def search_candidates(self, query: str, chunks: Sequence[StructuredChunk], *, top_k: int = 10,
                          mode: str = "lightrag", allowed_book_ids: set[str] | None = None,
                          allowed_roles: set[str] | None = None) -> list[RetrievalHit]:
        engine = ENGINE_IDS.get(mode)
        if engine is None or engine not in self.providers: raise GraphRetrievalError("selected graph service is not configured")
        if allowed_roles is not None and not allowed_roles <= {"spine", "supplementary"}: raise ValueError("invalid source roles")
        # Filter before any source is transferred for indexing or queried.
        candidates = tuple(c for c in chunks if (allowed_book_ids is None or c.book_id in allowed_book_ids)
                           and (allowed_roles is None or c.role in allowed_roles))
        if not candidates: return []
        seed = RetrievalScope(self.course_id, self.generation_id, candidates)
        scope = RetrievalScope(self.course_id, self.generation_id + ":" + seed.corpus_sha256, candidates)
        if not query.strip() or type(top_k) is not int or not 1 <= top_k <= 100: raise ValueError("invalid graph query")
        cfg = self.providers[engine]
        body = {"engine": engine, "course_id": scope.course_id, "generation_id": scope.generation_id,
                "chunks": [c.model_dump(mode="json") for c in candidates]}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + cfg.api_key}
        try:
            manifest = self.transport(cfg.base_url.rstrip("/") + "/v1/index/ensure", body, headers, cfg.timeout_s)
            class Backend:
                def retrieve(inner, requested_query, **kwargs):
                    return self.transport(cfg.base_url.rstrip("/") + "/v1/index/query",
                        body | {"query": requested_query, "top_k": kwargs["top_k"]}, headers, cfg.timeout_s)
            # Common canonical source/scope validation; LightRAG native service
            # returns normalized hits, not upstream-generated context.
            if engine == "hipporag":
                return HippoRAGAdapter(scope, manifest, backend=Backend()).search_candidates(query, candidates, top_k=top_k)
            expected = scope.manifest("lightrag", "1.5.7")
            if any(manifest.get(k) != v for k, v in expected.items()): raise GraphRetrievalError("LightRAG manifest mismatch")
            payload = Backend().retrieve(query, top_k=top_k)
            if any(payload.get(k) != v for k, v in {"course_id": scope.course_id, "generation_id": scope.generation_id,
                    "corpus_sha256": scope.corpus_sha256, "version": "1.5.7", "method": "lightrag", "fallback": False}.items()):
                raise GraphRetrievalError("LightRAG service scope mismatch")
            from .graph_adapters import _ScopedAdapter
            return _ScopedAdapter(scope, manifest, engine="lightrag", version="1.5.7")._hits(payload["hits"], top_k)
        except GraphRetrievalError: raise
        except Exception: raise GraphRetrievalError("graph service failed") from None
