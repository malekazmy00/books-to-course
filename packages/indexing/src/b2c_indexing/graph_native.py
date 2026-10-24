"""Pinned native graph runtimes, imported only in the separate graph service.

Each constructor receives an empty generated directory. No previous pickle is
loaded. Canonical IDs tag upstream passage content to prevent identical-text
passages from different sources collapsing into one upstream content hash.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from importlib.metadata import version, PackageNotFoundError
import os
from pathlib import Path
import threading
from typing import Any
from .graph_adapters import RetrievalScope, GraphRetrievalError, _digest
from urllib.parse import urlsplit
from .graph_runtime import VERSIONS

@dataclass(frozen=True)
class NativeRuntimeConfig:
    llm_base_url: str
    llm_model: str
    llm_key_file: str
    embedding_base_url: str
    embedding_model: str
    embedding_key_file: str
    embedding_dim: int = 1024
    timeout_s: float = 900

    def __post_init__(self):
        for base_url in (self.llm_base_url, self.embedding_base_url):
            url = urlsplit(base_url)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("invalid administrator graph model endpoint")
        if self.embedding_dim < 1 or self.timeout_s <= 0: raise ValueError("invalid graph runtime limits")

    @classmethod
    def from_env(cls):
        def require(name):
            value = os.environ.get("B2C_GRAPH_" + name, "").strip()
            if not value: raise ValueError("required graph runtime configuration absent")
            return value
        return cls(require("LLM_BASE_URL"), require("LLM_MODEL"), require("LLM_KEY_FILE"),
                   require("EMBEDDING_BASE_URL"), require("EMBEDDING_MODEL"), require("EMBEDDING_KEY_FILE"),
                   int(os.environ.get("B2C_GRAPH_EMBEDDING_DIM", "1024")),
                   float(os.environ.get("B2C_GRAPH_TIMEOUT_S", "900")))

    def secrets(self):
        keys = [Path(p).read_text(encoding="utf-8").strip() for p in (self.llm_key_file, self.embedding_key_file)]
        if not all(keys): raise GraphRetrievalError("empty graph runtime credential")
        return keys


def _tag(scope: RetrievalScope, chunk) -> str:
    return "B2C source identity: " + scope.source_id(chunk) + "\n\n" + chunk.text

class NativeRuntimeFactory:
    def __init__(self, config: NativeRuntimeConfig, *, enabled_engine: str | None = None):
        if enabled_engine is not None and enabled_engine not in VERSIONS: raise ValueError("invalid configured graph engine")
        self.config, self.enabled_engine = config, enabled_engine

    @property
    def identity(self):
        return _digest(["native-graph-v1-source-tags", self.config.llm_base_url, self.config.llm_model,
                        self.config.embedding_base_url, self.config.embedding_model, self.config.embedding_dim])

    def health(self):
        engines = {}
        for engine in ([self.enabled_engine] if self.enabled_engine else list(VERSIONS)):
            try: actual = version("lightrag-hku" if engine == "lightrag" else "hipporag")
            except PackageNotFoundError: actual = None
            engines[engine] = {"version": actual, "expected_version": VERSIONS[engine],
                               "package_ready": actual == VERSIONS[engine]}
        return {"status": "ok" if any(v["package_ready"] for v in engines.values()) else "unavailable",
                "engines": engines, "runtime_profile_sha256": self.identity, "llm_model": self.config.llm_model, "embedding_model": self.config.embedding_model}
    def __call__(self, engine: str, directory: Path):
        if directory.is_symlink() or not directory.is_dir() or any(directory.iterdir()):
            raise GraphRetrievalError("native graph runtime requires an empty generated directory")
        if self.enabled_engine is not None and engine != self.enabled_engine: raise GraphRetrievalError("graph engine disabled on this service")
        if engine == "lightrag": return NativeLightRAG(self.config, directory)
        if engine == "hipporag": return NativeHippoRAG(self.config, directory)
        raise GraphRetrievalError("unsupported graph runtime")

class NativeLightRAG:
    _loop_guard = threading.Lock()
    _shared_loop = None
    def __init__(self, config: NativeRuntimeConfig, directory: Path):
        if version("lightrag-hku") != VERSIONS["lightrag"]: raise GraphRetrievalError("LightRAG version mismatch")
        from lightrag import LightRAG
        from lightrag.utils import EmbeddingFunc
        from lightrag.llm.openai import openai_complete_if_cache, openai_embed
        llm_key, embed_key = config.secrets()
        async def llm(prompt, system_prompt=None, history_messages=None, **kwargs):
            return await openai_complete_if_cache(config.llm_model, prompt, system_prompt=system_prompt,
                history_messages=history_messages or [], base_url=config.llm_base_url,
                api_key=llm_key, timeout=int(config.timeout_s), **kwargs)
        async def embed(texts):
            return await openai_embed.func(texts, model=config.embedding_model,
                base_url=config.embedding_base_url, api_key=embed_key)
        self.config = config
        with self._loop_guard:
            if type(self)._shared_loop is None:
                type(self)._shared_loop = asyncio.new_event_loop()
                threading.Thread(target=type(self)._shared_loop.run_forever, daemon=True, name="b2c-lightrag-loop").start()
            self.loop = type(self)._shared_loop
        self.rag = LightRAG(working_dir=str(directory), workspace=directory.parent.name,
            llm_model_func=llm, llm_model_name=config.llm_model,
            embedding_func=EmbeddingFunc(embedding_dim=config.embedding_dim, max_token_size=8192, model_name=config.embedding_model, func=embed),
            entity_extract_max_gleaning=0, llm_model_max_async=1, max_parallel_insert=1)
        self._scope = None

    def _run(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try: return future.result(timeout=self.config.timeout_s)
        except Exception:
            future.cancel()
            raise

    def index(self, scope: RetrievalScope) -> None:
        from lightrag.kg.shared_storage import initialize_pipeline_status
        async def build():
            await self.rag.initialize_storages()
            await initialize_pipeline_status(workspace=self.rag.workspace)
            ids = [scope.source_id(c) for c in scope.chunks]
            await self.rag.ainsert([_tag(scope, c) for c in scope.chunks], ids=ids, file_paths=ids)
            statuses = await self.rag.aget_docs_by_ids(ids)
            if set(statuses) != set(ids) or any(getattr(s.status, "value", s.status) != "processed" for s in statuses.values()):
                raise GraphRetrievalError("LightRAG index is incomplete")
        self._run(build())
        self._scope = scope

    def retrieve(self, query: str, top_k: int) -> list[dict[str, Any]]:
        from lightrag import QueryParam
        payload = self._run(self.rag.aquery_data(query,
            QueryParam(mode="mix", top_k=top_k, chunk_top_k=top_k, only_need_context=True, enable_rerank=False)))
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise GraphRetrievalError("LightRAG retrieval did not succeed")
        chunks = payload.get("data", {}).get("chunks")
        if not isinstance(chunks, list): raise GraphRetrievalError("LightRAG result lacks chunks")
        return [{"source_id": row["file_path"], "score": 1 / (i + 1)} for i, row in enumerate(chunks)]

    def close(self):
        self._run(self.rag.finalize_storages())

class NativeHippoRAG:
    def __init__(self, config: NativeRuntimeConfig, directory: Path):
        if version("hipporag") != VERSIONS["hipporag"]: raise GraphRetrievalError("HippoRAG version mismatch")
        from hipporag import HippoRAG
        from hipporag.utils.config_utils import BaseConfig
        llm_key, embed_key = config.secrets()
        # Only this isolated service reads these values; the caller never sends
        # credentials or runtime paths in the HTTP body.
        os.environ["B2C_GRAPH_RUNTIME_LLM_KEY"] = llm_key
        os.environ["B2C_GRAPH_RUNTIME_EMBED_KEY"] = embed_key
        cfg = BaseConfig(llm_name=config.llm_model, llm_base_url=config.llm_base_url,
            llm_api_key_env="B2C_GRAPH_RUNTIME_LLM_KEY", embedding_model_name=config.embedding_model,
            embedding_base_url=config.embedding_base_url, embedding_provider="openai",
            embedding_api_key_env="B2C_GRAPH_RUNTIME_EMBED_KEY", force_index_from_scratch=True,
            force_openie_from_scratch=True, max_retry_attempts=1, openie_max_workers=1, seed=0, temperature=0)
        self.client = HippoRAG(global_config=cfg, save_dir=str(directory),
            index_identity="b2c-production-graph-v1", llm_api_key_env="B2C_GRAPH_RUNTIME_LLM_KEY",
            embedding_api_key_env="B2C_GRAPH_RUNTIME_EMBED_KEY")
        self._sources = {}

    def index(self, scope: RetrievalScope) -> None:
        from hipporag.utils.misc_utils import Chunk
        documents = []
        for chunk in scope.chunks:
            sid = scope.source_id(chunk)
            tagged = _tag(scope, chunk)
            metadata = {"source_id": sid, "b2c_course_id": scope.course_id,
                "b2c_generation_id": scope.generation_id, "b2c_corpus_sha256": scope.corpus_sha256}
            documents.append(Chunk(content=tagged, source_id=sid, metadata=metadata))
            self._sources[sid] = (tagged, metadata)
        self.client.index(documents)
        # The patched pinned runtime persists a one-to-one canonical map.
        loaded = list(self.client.chunk_metadata.values())
        if {row.get("source_id") for row in loaded} != set(self._sources):
            raise GraphRetrievalError("HippoRAG source map is incomplete")

    def retrieve(self, query: str, top_k: int) -> list[dict[str, Any]]:
        results = self.client.retrieve([query], num_to_retrieve=top_k)
        if not isinstance(results, list) or len(results) != 1: raise GraphRetrievalError("HippoRAG invalid result count")
        result = results[0]
        if not getattr(result, "graph_seeds", None):
            raise GraphRetrievalError("HippoRAG fell back to dense-only retrieval")
        if result.doc_metadata is None: raise GraphRetrievalError("HippoRAG lost source metadata")
        rows = []
        for text, metadata, score in zip(result.docs, result.doc_metadata, result.doc_scores, strict=True):
            sid = metadata.get("source_id")
            if sid not in self._sources or (text, metadata) != self._sources[sid]:
                raise GraphRetrievalError("HippoRAG source content or metadata mismatch")
            rows.append({"source_id": sid, "score": float(score)})
        return rows




    def close(self):
        self.client.close()
