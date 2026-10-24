"""Scoped retrieval-only boundaries for separately provisioned graph indexes.

No upstream package, pickle, indexing, or generated text enters the B2C worker.
The caller supplies a trusted ready manifest for a dedicated course generation.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from b2c_contracts.models import StructuredChunk
from .query_service import RetrievalHit


class GraphRetrievalError(RuntimeError):
    """Explicit graph failure; callers must not silently replace the engine."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _identity(chunk: StructuredChunk) -> tuple[str, str, str]:
    return chunk.book_id, chunk.role, chunk.chunk_id


@dataclass(frozen=True)
class RetrievalScope:
    course_id: str
    generation_id: str
    chunks: tuple[StructuredChunk, ...]

    def __post_init__(self) -> None:
        if not self.course_id.strip() or not self.generation_id.strip() or not self.chunks:
            raise ValueError("a non-empty course, generation and canonical corpus are required")
        if len({_identity(c) for c in self.chunks}) != len(self.chunks):
            raise ValueError("duplicate canonical source identity")

    @property
    def corpus_sha256(self) -> str:
        return _digest(sorted((c.model_dump(mode="json") for c in self.chunks),
                              key=lambda c: (c["book_id"], c["role"], c["chunk_id"])))

    def source_id(self, chunk: StructuredChunk) -> str:
        return "b2c:" + _digest([self.course_id, self.generation_id, *_identity(chunk)])

    def manifest(self, engine: str, version: str) -> dict[str, Any]:
        return {"course_id": self.course_id, "generation_id": self.generation_id,
                "corpus_sha256": self.corpus_sha256, "engine": engine,
                "version": version, "state": "ready",
                "source_ids": sorted(self.source_id(c) for c in self.chunks)}


class _ScopedAdapter:
    def __init__(self, scope: RetrievalScope, manifest: Mapping[str, Any], *, engine: str, version: str):
        expected = scope.manifest(engine, version)
        if any(manifest.get(k) != v for k, v in expected.items()):
            raise GraphRetrievalError("graph index manifest does not match the ready canonical scope")
        self.scope, self.engine = scope, engine
        self._fingerprint = scope.corpus_sha256
        self._sources = {scope.source_id(c): c.model_copy(deep=True) for c in scope.chunks}

    def _validate(self, query: str, candidates: Sequence[StructuredChunk], top_k: int,
                  allowed_book_ids: set[str] | None, allowed_roles: set[str] | None) -> dict[str, StructuredChunk]:
        if not isinstance(query, str) or not query.strip() or type(top_k) is not int or not 1 <= top_k <= 100:
            raise ValueError("non-empty query and top_k between 1 and 100 required")
        if allowed_roles is not None and not allowed_roles <= {"spine", "supplementary"}:
            raise ValueError("invalid source roles")
        # A smaller allowlist must have its own dedicated graph: post-filtering
        # alone would expose excluded passages to graph query expansion.
        actual = RetrievalScope(self.scope.course_id, self.scope.generation_id, tuple(candidates))
        if actual.corpus_sha256 != self._fingerprint or self.scope.corpus_sha256 != self._fingerprint:
            raise GraphRetrievalError("candidate scope differs from the dedicated graph index")
        if any((allowed_book_ids is not None and c.book_id not in allowed_book_ids) or
               (allowed_roles is not None and c.role not in allowed_roles) for c in candidates):
            raise GraphRetrievalError("allowlist excludes indexed graph sources; a narrower index is required")
        return self._sources

    def _hits(self, rows: Sequence[Mapping[str, Any]], top_k: int) -> list[RetrievalHit]:
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise GraphRetrievalError("graph returned invalid source records")
            source_id = row.get("source_id")
            if not isinstance(source_id, str) or source_id not in self._sources:
                raise GraphRetrievalError("graph returned a source outside the canonical scope")
            score = row.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise GraphRetrievalError("graph returned an invalid score")
            if source_id not in seen:
                seen.add(source_id)
                result.append(RetrievalHit(self._sources[source_id].model_copy(deep=True), float(score), self.engine))
        return result[:top_k]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GraphRetrievalError("graph endpoint redirects are forbidden")


def http_json(url: str, body: Mapping[str, Any], headers: Mapping[str, str], timeout_s: float) -> Mapping[str, Any]:
    req = Request(url, data=json.dumps(body).encode(), method="POST", headers=dict(headers))
    try:
        with build_opener(_NoRedirect()).open(req, timeout=timeout_s) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise GraphRetrievalError("graph response exceeds size limit")
        payload = json.loads(data)
        if not isinstance(payload, Mapping):
            raise GraphRetrievalError("graph response is not an object")
        return payload
    except GraphRetrievalError:
        raise
    except Exception:
        raise GraphRetrievalError("graph HTTP request failed") from None


class LightRAGAdapter(_ScopedAdapter):
    """LightRAG 1.5.7 /query/data over one trusted dedicated scope endpoint.

    Its deployment must assign every indexed document file_path to source_id().
    Never bind a multi-course or incomplete upstream workspace to this adapter.
    """
    def __init__(self, scope: RetrievalScope, manifest: Mapping[str, Any], *, base_url: str,
                 api_key: str, timeout_s: float = 120,
                 transport: Callable[..., Mapping[str, Any]] = http_json):
        super().__init__(scope, manifest, engine="lightrag", version="1.5.7")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid graph endpoint")
        if not api_key.strip() or timeout_s <= 0:
            raise ValueError("graph credential and positive timeout required")
        self._url, self._key, self._timeout, self._transport = base_url.rstrip("/") + "/query/data", api_key, timeout_s, transport

    def search_candidates(self, query: str, candidates: Sequence[StructuredChunk], *, top_k: int = 10,
                          allowed_book_ids: set[str] | None = None, allowed_roles: set[str] | None = None) -> list[RetrievalHit]:
        self._validate(query, candidates, top_k, allowed_book_ids, allowed_roles)
        try:
            payload = self._transport(self._url, {"query": query, "mode": "mix", "top_k": top_k,
                "only_need_context": True, "enable_rerank": False},
                {"Content-Type": "application/json", "X-API-Key": self._key}, self._timeout)
            if payload.get("status") != "success" or not isinstance(payload.get("data"), Mapping):
                raise GraphRetrievalError("LightRAG did not return successful retrieval data")
            chunks = payload["data"].get("chunks")
            if not isinstance(chunks, list):
                raise GraphRetrievalError("LightRAG response lacks source chunks")
            rows = [{"source_id": c.get("file_path"), "score": 1.0 / (i + 1)}
                    for i, c in enumerate(chunks) if isinstance(c, Mapping)]
            if len(rows) != len(chunks):
                raise GraphRetrievalError("LightRAG returned malformed chunks")
            return self._hits(rows, top_k)
        except GraphRetrievalError:
            raise
        except Exception:
            raise GraphRetrievalError("LightRAG retrieval failed") from None


class ScopedHippoBackend(Protocol):
    """Trusted service facade; no untrusted library or serialization in worker."""
    def retrieve(self, query: str, *, course_id: str, generation_id: str,
                 corpus_sha256: str, top_k: int) -> Mapping[str, Any]: ...


class HippoRAGAdapter(_ScopedAdapter):
    """Pinned HippoRAG service contract; fallback must be explicitly reported."""
    def __init__(self, scope: RetrievalScope, manifest: Mapping[str, Any], *, backend: ScopedHippoBackend):
        super().__init__(scope, manifest, engine="hipporag", version="2.0.0a5")
        self._backend = backend

    def search_candidates(self, query: str, candidates: Sequence[StructuredChunk], *, top_k: int = 10,
                          allowed_book_ids: set[str] | None = None, allowed_roles: set[str] | None = None) -> list[RetrievalHit]:
        self._validate(query, candidates, top_k, allowed_book_ids, allowed_roles)
        try:
            payload = self._backend.retrieve(query, course_id=self.scope.course_id,
                generation_id=self.scope.generation_id, corpus_sha256=self._fingerprint, top_k=top_k)
            expected = {"course_id": self.scope.course_id, "generation_id": self.scope.generation_id,
                        "corpus_sha256": self._fingerprint, "version": "2.0.0a5", "method": "hipporag"}
            if not isinstance(payload, Mapping) or any(payload.get(k) != v for k, v in expected.items()) or payload.get("fallback") is not False:
                raise GraphRetrievalError("HippoRAG response scope, version or retrieval method mismatch")
            rows = payload.get("hits")
            if not isinstance(rows, list):
                raise GraphRetrievalError("HippoRAG response lacks source hits")
            return self._hits(rows, top_k)
        except GraphRetrievalError:
            raise
        except Exception:
            raise GraphRetrievalError("HippoRAG retrieval failed") from None
