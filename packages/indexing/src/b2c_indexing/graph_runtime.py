"""Durable canonical-source provisioning, separate from upstream graph files."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from collections import OrderedDict
from pathlib import Path
import json
import math
import os
import tempfile
import threading
from typing import Any, Protocol
from .graph_adapters import RetrievalScope, GraphRetrievalError, _digest

VERSIONS = {"lightrag": "1.5.7", "hipporag": "2.0.0a5"}

class NativeGraph(Protocol):
    def index(self, scope: RetrievalScope) -> None: ...
    def retrieve(self, query: str, top_k: int) -> list[dict[str, Any]]: ...

class GraphIndexRegistry:
    """Only trusted generated roots; persisted input is JSON, never pickle.

    After service restart a graph is reconstructed from supplied canonical
    sources into a fresh empty attempt directory. Previous upstream files are
    never loaded. Existing ready records alone cannot grant retrieval access.
    """
    def __init__(self, root: Path, factory: Callable[[str, Path], NativeGraph], *, max_loaded: int = 2):
        if root.is_symlink(): raise ValueError("graph root may not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root.resolve()
        if self.root.stat().st_mode & 0o022: raise ValueError("graph root must not be group/world writable")
        self.factory = factory
        if max_loaded < 1: raise ValueError("positive graph runtime cache bound required")
        self.max_loaded = max_loaded
        self._active: OrderedDict[str, NativeGraph] = OrderedDict()
        self._operation = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def manifest(self, engine: str, scope: RetrievalScope) -> dict[str, Any]:
        return scope.manifest(engine, VERSIONS[engine]) | {"runtime_profile_sha256": getattr(self.factory, "identity", "injected")}

    def key(self, engine: str, scope: RetrievalScope) -> str:
        if engine not in VERSIONS: raise ValueError("unsupported graph engine")
        return _digest([engine, VERSIONS[engine], scope.course_id, scope.generation_id, scope.corpus_sha256, getattr(self.factory, "identity", "injected")])

    def _directory(self, key: str) -> Path:
        path = self.root / key
        if path.is_symlink(): raise GraphRetrievalError("unsafe graph index directory")
        path.mkdir(mode=0o700, exist_ok=True)
        if path.resolve().parent != self.root: raise GraphRetrievalError("graph index escaped root")
        return path

    def _write(self, directory: Path, name: str, payload: Mapping[str, Any]) -> None:
        target = directory / name
        if target.is_symlink(): raise GraphRetrievalError("unsafe graph state file")
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(payload, out, ensure_ascii=False, sort_keys=True)
                out.flush(); os.fsync(out.fileno())
            os.replace(temporary, target)
            dir_fd = os.open(directory, os.O_RDONLY)
            try: os.fsync(dir_fd)
            finally: os.close(dir_fd)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def ensure(self, engine: str, scope: RetrievalScope) -> dict[str, Any]:
        key = self.key(engine, scope)
        with self._lock: lock = self._locks.setdefault(key, threading.Lock())
        with self._operation, lock:
            directory = self._directory(key)
            manifest = self.manifest(engine, scope)
            if key in self._active:
                self._active.move_to_end(key)
                return manifest
            while len(self._active) >= self.max_loaded:
                _, removed = self._active.popitem(last=False)
                close = getattr(removed, "close", None)
                if close: close()
            building = manifest | {"state": "building"}
            self._write(directory, "manifest.json", building)
            self._write(directory, "sources.json", {"course_id": scope.course_id,
                "generation_id": scope.generation_id,
                "chunks": [c.model_dump(mode="json") for c in scope.chunks]})
            # mkdtemp guarantees that upstream constructors cannot restore an
            # existing graph.pickle, even following a failed run/restart.
            attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=directory))
            try:
                native = self.factory(engine, attempt)
                native.index(scope)
                self._write(directory, "manifest.json", manifest)
                self._active[key] = native
                return manifest
            except Exception:
                close = getattr(locals().get("native"), "close", None)
                if close:
                    try: close()
                    except Exception: pass
                self._write(directory, "manifest.json", building | {"state": "failed", "error": "graph_index_failed"})
                raise GraphRetrievalError("graph indexing failed") from None

    def retrieve(self, engine: str, scope: RetrievalScope, query: str, top_k: int) -> dict[str, Any]:
        key = self.key(engine, scope)
        if not query.strip() or type(top_k) is not int or not 1 <= top_k <= 100:
            raise ValueError("invalid graph query")
        with self._lock: lock = self._locks.setdefault(key, threading.Lock())
        with self._operation, lock:
            native = self._active.get(key)
            if native is None: raise GraphRetrievalError("graph index is not loaded; ensure it first")
            try:
                rows = native.retrieve(query, top_k)
                known = {scope.source_id(c) for c in scope.chunks}
                if not isinstance(rows, list) or any(not isinstance(row, Mapping) or row.get("source_id") not in known for row in rows):
                    raise GraphRetrievalError("graph returned unknown source identities")
                if any(isinstance(row.get("score"), bool) or not isinstance(row.get("score"), (float, int))
                       or not math.isfinite(row["score"]) for row in rows):
                    raise GraphRetrievalError("graph returned invalid source scores")
                return {"course_id": scope.course_id, "generation_id": scope.generation_id,
                    "corpus_sha256": scope.corpus_sha256, "version": VERSIONS[engine],
                    "method": engine, "fallback": False, "hits": rows}
            except GraphRetrievalError: raise
            except Exception: raise GraphRetrievalError("graph query failed") from None


    def status(self, engine: str, scope: RetrievalScope) -> dict[str, Any]:
        key = self.key(engine, scope)
        with self._lock:
            if key in self._active: return self.manifest(engine, scope)
        file = self.root / key / "manifest.json"
        if file.is_symlink() or file.parent.is_symlink(): raise GraphRetrievalError("unsafe graph manifest path")
        if not file.exists(): return self.manifest(engine, scope) | {"state": "not_started"}
        try: saved = json.loads(file.read_text(encoding="utf-8"))
        except Exception: raise GraphRetrievalError("graph manifest unreadable") from None
        expected = self.manifest(engine, scope)
        if any(saved.get(k) != v for k, v in expected.items() if k != "state"):
            raise GraphRetrievalError("graph manifest source mismatch")
        return saved | ({"state": "rebuild_required"} if saved["state"] == "ready" else {})
