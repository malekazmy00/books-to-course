"""Version-pinned adapter between B2C chunks and HippoRAG 2.0.0a5.

This module belongs to the isolated Phase 6 evaluation harness. It is not part
of the default B2C worker or runtime package. The optional HippoRAG dependency
is injected by the isolated runner; this file never imports it itself.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
from typing import Any, Protocol

HIPPORAG_VERSION = "2.0.0a5"
HIPPORAG_COMMIT = "2bfd831417202b49cda9da7973e141a456e16872"


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: str
    score: float
    scope_id: str


class HippoClient(Protocol):
    def index(self, docs: list[Any]) -> Any: ...
    def retrieve(self, queries: list[str], num_to_retrieve: int) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class _Source:
    scope_id: str
    provider_id: str
    chunk_id: str
    book_id: str
    role: str
    pages: tuple[int, ...]
    block_ids: tuple[str, ...]
    text: str


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Explicit endpoints and disposable storage for the isolated runner."""
    save_dir: Path
    allowed_root: Path
    llm_model: str
    llm_base_url: str
    embedding_model: str
    embedding_base_url: str | None = None
    embedding_provider: str = "openai"
    llm_api_key_env: str = "B2C_PHASE6_LLM_API_KEY"
    embedding_api_key_env: str = "B2C_PHASE6_EMBEDDING_API_KEY"


def require_pinned_version(actual: str) -> None:
    if actual != HIPPORAG_VERSION:
        raise RuntimeError(
            f"Phase 6 requires HippoRAG {HIPPORAG_VERSION} from commit {HIPPORAG_COMMIT}; got {actual}"
        )


def create_isolated_runtime(config: RuntimeConfig) -> tuple[HippoClient, type]:
    """Load the optional pinned package only inside its isolated trial process.

    Explicit model endpoints prevent the upstream default from silently using
    OpenAI. A fresh save directory plus force-from-scratch flags prevent loading
    a graph.pickle or index artifact from any previous/user-controlled run.
    """
    for name, value in (
        ("llm_model", config.llm_model),
        ("llm_base_url", config.llm_base_url),
        ("embedding_model", config.embedding_model),
        ("llm_api_key_env", config.llm_api_key_env),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be explicitly configured")
    if config.embedding_provider == "openai":
        if not isinstance(config.embedding_base_url, str) or not config.embedding_base_url.strip():
            raise ValueError("embedding_base_url must be explicitly configured for the OpenAI-compatible provider")
        if not isinstance(config.embedding_api_key_env, str) or not config.embedding_api_key_env.strip():
            raise ValueError("embedding_api_key_env must be explicitly configured for the OpenAI-compatible provider")
    elif config.embedding_provider == "transformers":
        if config.embedding_base_url is not None:
            raise ValueError("embedding_base_url must be omitted for the local Transformers provider")
    else:
        raise ValueError("embedding_provider must be one of: openai, transformers")
    if config.save_dir.is_symlink():
        raise ValueError("save_dir must not be a symlink")
    root = config.allowed_root.resolve()
    save_dir = config.save_dir.resolve()
    try:
        save_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("save_dir must be inside the dedicated isolated data root") from exc
    if save_dir == root or save_dir.exists():
        raise ValueError("save_dir must be a new, non-symlink directory")
    if not root.is_dir():
        raise ValueError("allowed_root must already exist as an isolated mount")
    required_envs = [config.llm_api_key_env]
    if config.embedding_provider == "openai":
        required_envs.append(config.embedding_api_key_env)
    for env_name in required_envs:
        if env_name not in os.environ:
            raise ValueError(f"required secret environment variable is absent: {env_name}")

    require_pinned_version(importlib.metadata.version("hipporag"))
    # Delayed import keeps the untrusted/optional library out of the default
    # worker and normal B2C process. The caller must run this only in the
    # disposable Phase 6 environment described by the phase report.
    from hipporag import HippoRAG
    from hipporag.utils.config_utils import BaseConfig
    from hipporag.utils.misc_utils import Chunk

    cfg = BaseConfig(
        llm_name=config.llm_model,
        llm_base_url=config.llm_base_url,
        llm_api_key_env=config.llm_api_key_env,
        embedding_model_name=config.embedding_model,
        embedding_base_url=config.embedding_base_url,
        embedding_provider=config.embedding_provider,
        embedding_api_key_env=config.embedding_api_key_env,
        force_index_from_scratch=True,
        force_openie_from_scratch=True,
        max_retry_attempts=1,
        openie_max_workers=4,
        seed=0,
        temperature=0,
    )
    client = HippoRAG(
        global_config=cfg,
        save_dir=str(save_dir),
        index_identity=f"b2c-phase6:{HIPPORAG_COMMIT}",
        llm_api_key_env=config.llm_api_key_env,
        embedding_api_key_env=config.embedding_api_key_env,
    )
    return client, Chunk


def _source_id(scope_id: str, row: Mapping[str, Any]) -> str:
    material = json.dumps(
        [scope_id, row["book_id"], row["role"], row["chunk_id"]],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "b2c:" + hashlib.sha256(material).hexdigest()


def to_hipporag_chunks(
    chunks: Sequence[Mapping[str, Any]], *, scope_id: str, chunk_type: type
) -> tuple[list[Any], dict[str, _Source]]:
    """Convert one frozen course corpus while preserving stable provenance.

    Duplicate IDs and duplicate text under different source IDs are rejected:
    HippoRAG hashes passage content internally and could otherwise collapse
    distinct B2C sources into one provider document.
    """
    if not scope_id or not scope_id.strip():
        raise ValueError("scope_id must be non-empty")
    converted: list[Any] = []
    sources: dict[str, _Source] = {}
    seen_chunk_ids: set[tuple[str, str, str]] = set()
    seen_text: dict[str, str] = {}
    for row in chunks:
        required = {"chunk_id", "book_id", "role", "pages", "text"}
        missing = required - row.keys()
        if missing:
            raise ValueError(f"chunk missing required fields: {sorted(missing)}")
        chunk_id, book_id, role, text = (
            row["chunk_id"], row["book_id"], row["role"], row["text"]
        )
        if not all(isinstance(value, str) and value.strip() for value in (chunk_id, book_id, text)):
            raise ValueError("chunk_id, book_id, and text must be non-empty strings")
        if role not in {"spine", "supplementary"}:
            raise ValueError(f"unsupported source role: {role!r}")
        identity = (book_id, role, chunk_id)
        if identity in seen_chunk_ids:
            raise ValueError(f"duplicate B2C chunk identity: {identity}")
        seen_chunk_ids.add(identity)
        previous = seen_text.get(text)
        if previous is not None:
            raise ValueError(f"HippoRAG content hash collision between {previous} and {chunk_id}")
        seen_text[text] = chunk_id
        pages = row["pages"]
        # Some frozen evaluation corpora predate block-level provenance. Keep
        # that absence explicit as an empty list instead of fabricating IDs.
        block_ids = row.get("block_ids", [])
        if not isinstance(pages, list) or not pages or any(type(page) is not int or page < 1 for page in pages):
            raise ValueError(f"invalid physical pages for {chunk_id}")
        if not isinstance(block_ids, list) or any(not isinstance(item, str) or not item for item in block_ids):
            raise ValueError(f"invalid block provenance for {chunk_id}")
        provider_id = _source_id(scope_id, row)
        metadata = {
            "b2c_scope_id": scope_id,
            "b2c_book_id": book_id,
            "b2c_role": role,
            "b2c_chunk_id": chunk_id,
            "b2c_pages": pages,
            "b2c_block_ids": block_ids,
            "b2c_section_path": row.get("section_path", []),
        }
        converted.append(chunk_type(content=text, source_id=provider_id, metadata=metadata))
        sources[provider_id] = _Source(
            scope_id, provider_id, chunk_id, book_id, role, tuple(pages), tuple(block_ids), text
        )
    if not converted:
        raise ValueError("cannot index an empty B2C corpus")
    return converted, sources


class HippoRAG2Backend:
    """Narrow GraphBackend bridge for a pre-indexed, single-course HippoRAG.

    Instantiate only inside the isolated Phase 6 runner. The underlying
    HippoRAG object must use a fresh private save directory and a fresh index.
    """

    def __init__(self, client: HippoClient, *, sources: Mapping[str, _Source]):
        if not sources:
            raise ValueError("source map must be non-empty")
        self._client = client
        self._sources = dict(sources)
        self._indexed = False

    def index(self, documents: list[Any]) -> None:
        if self._indexed:
            raise RuntimeError("backend is one-shot; use a fresh isolated index for each run")
        if len(documents) != len(self._sources):
            raise ValueError("document/source counts differ")
        self._client.index(documents)
        self._indexed = True

    def retrieve(self, query: str, *, top_k: int) -> list[Hit]:
        if not self._indexed:
            raise RuntimeError("index the frozen corpus before retrieval")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be positive")
        results = self._client.retrieve([query], num_to_retrieve=top_k)
        if not isinstance(results, list) or len(results) != 1:
            raise RuntimeError("HippoRAG returned an invalid single-query result")
        result = results[0]
        docs = result.docs
        scores = list(result.doc_scores)
        metadata = result.doc_metadata
        if len(docs) != len(scores) or metadata is None or len(docs) != len(metadata):
            raise RuntimeError("HippoRAG result lost document scores or source metadata")
        hits: list[Hit] = []
        seen: set[str] = set()
        for text, score, info in zip(docs, scores, metadata, strict=True):
            if not isinstance(info, Mapping):
                raise RuntimeError("HippoRAG result metadata is not a mapping")
            provider_id = info.get("source_id")
            source = self._sources.get(provider_id)
            if source is None:
                # No content-only fallback: it would fabricate provenance.
                continue
            expected_meta = {
                "b2c_scope_id": info.get("b2c_scope_id"),
                "b2c_book_id": info.get("b2c_book_id"),
                "b2c_role": info.get("b2c_role"),
                "b2c_chunk_id": info.get("b2c_chunk_id"),
                "b2c_pages": tuple(info.get("b2c_pages", ())),
                "b2c_block_ids": tuple(info.get("b2c_block_ids", ())),
            }
            expected = {
                "b2c_scope_id": source.scope_id,
                "b2c_book_id": source.book_id,
                "b2c_role": source.role,
                "b2c_chunk_id": source.chunk_id,
                "b2c_pages": source.pages,
                "b2c_block_ids": source.block_ids,
            }
            if text != source.text or expected_meta != expected:
                raise RuntimeError(f"HippoRAG source metadata/content mismatch for {source.chunk_id}")
            value = float(score)
            if not math.isfinite(value):
                raise RuntimeError(f"HippoRAG returned non-finite score for {source.chunk_id}")
            if source.chunk_id not in seen:
                seen.add(source.chunk_id)
                hits.append(Hit(source.chunk_id, value, source.scope_id))
        return hits[:top_k]
