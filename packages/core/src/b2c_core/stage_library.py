"""Validated portable stage artifacts; source identity survives export/import."""
from __future__ import annotations
import hashlib
import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from b2c_contracts import ArtifactRef, BookUpload, ParsedBook, stage_key
from b2c_contracts.models import StructuredChunk
from .artifacts import ArtifactStore, content_sha256

class StageBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal[1] = 1
    stage: Literal["ocr", "rag"]
    book_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: dict[str, Any]
    payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()

class StageLibrary:
    def __init__(self, store: ArtifactStore):
        self.store = store

    def export_artifact(self, course_id: str, ref: ArtifactRef) -> dict[str, bytes]:
        """Export committed artifacts, rejecting foreign scope or corruption."""
        prefix = self.store._prefix(course_id, ref.stage, ref.key)
        if ref.uri != f"s3://{self.store.bucket}/{prefix}":
            raise ValueError("artifact is outside the requested course")
        committed = self.store.exists(course_id, ref.stage, ref.key)
        if committed != ref:
            raise ValueError("artifact is not committed or its manifest changed")
        files = {name: self.store.read(ref, name) for name in self.store.list_files(ref)}
        if content_sha256(files) != ref.sha256:
            raise ValueError("artifact content digest mismatch")
        return files

    def import_ocr(self, course_id: str, upload: BookUpload, bundle: StageBundle) -> ArtifactRef:
        self._check_identity(upload, bundle, "ocr")
        parsed = ParsedBook.model_validate(bundle.payload)
        if parsed.book_id != upload.book_id or parsed.role != upload.role:
            raise ValueError("parsed-book identity or source role mismatch")
        self._check_blocks(parsed)
        recorded_digest = parsed.parser.get("source_file_sha256")
        if recorded_digest and recorded_digest != upload.file_sha256:
            raise ValueError("imported OCR PDF fingerprint mismatch")
        parsed = parsed.model_copy(update={"parser": {**parsed.parser,
            "source_file_sha256": upload.file_sha256}})
        key = stage_key("ingest-parse-pdf", "import-1", [upload.file_sha256],
                        {"config": bundle.config, "payload_sha256": bundle.payload_sha256,
                         "upload": upload.model_dump(mode="json")})
        return self.store.put(course_id, "ingest-parse-pdf", key, {
            "parsed-book.json": parsed.model_dump_json().encode(),
            "import-bundle.json": bundle.model_dump_json().encode(),
        })

    def import_rag(self, course_id: str, upload: BookUpload, bundle: StageBundle,
                   parsed: ParsedBook, *, parsed_sha256: str, outline_sha256: str,
                   chunk_store: Any) -> ArtifactRef:
        self._check_identity(upload, bundle, "rag")
        self._check_blocks(parsed)
        if parsed.book_id != upload.book_id or parsed.role != upload.role:
            raise ValueError("parsed source identity mismatch")
        if (bundle.config.get("parsed_sha256") != parsed_sha256 or
                bundle.config.get("outline_sha256") != outline_sha256):
            raise ValueError("RAG source or outline fingerprint mismatch")
        chunks = [StructuredChunk.model_validate(item) for item in bundle.payload.get("chunks", [])]
        if not chunks or len({c.chunk_id for c in chunks}) != len(chunks):
            raise ValueError("RAG requires non-empty unique chunks")
        blocks = {b.block_id: b for b in parsed.blocks}
        embedding_model = bundle.config.get("embedding_model")
        if not isinstance(embedding_model, str) or not embedding_model:
            raise ValueError("RAG embedding model must be recorded")
        for chunk in chunks:
            if chunk.book_id != upload.book_id or chunk.role != upload.role:
                raise ValueError("chunk identity or role mismatch")
            if chunk.embedding_model != embedding_model:
                raise ValueError("chunk embedding model fingerprint mismatch")
            if any(block_id not in blocks for block_id in chunk.block_ids):
                raise ValueError("chunk references unknown source blocks")
            expected_pages = {r.page_no for bid in chunk.block_ids for r in blocks[bid].refs}
            if set(chunk.pages) != expected_pages:
                raise ValueError("chunk page provenance does not match source blocks")
            if not chunk.text.strip():
                raise ValueError("chunk text is empty")
            source_text = " ".join(blocks[bid].text for bid in chunk.block_ids)
            if " ".join(chunk.text.split()) not in " ".join(source_text.split()):
                raise ValueError("chunk text is not grounded in its source blocks")
        target_tokens = bundle.config.get("target_tokens", 500)
        overlap_ratio = bundle.config.get("overlap_ratio", 0.12)
        if (type(target_tokens) is not int or not 1 <= target_tokens <= 800 or
                type(overlap_ratio) not in (int, float) or not 0 <= overlap_ratio < 0.5):
            raise ValueError("invalid RAG chunking settings")
        key = stage_key("index-approved-book", "snapshot-2", [parsed_sha256, outline_sha256],
                        {"book_id": upload.book_id, "role": upload.role,
                         "target_tokens": target_tokens, "overlap_ratio": float(overlap_ratio),
                         "embedding_model": embedding_model})
        ref, _ = self.store.get_or_compute(course_id, "index-approved-book", key,
            lambda: {"chunks.json": json.dumps([c.model_dump(mode="json") for c in chunks],
                                                ensure_ascii=False).encode("utf-8"),
                     "rag-bundle.json": bundle.model_dump_json().encode()})
        existing_chunks = [StructuredChunk.model_validate(item) for item in json.loads(
            self.store.read(ref, "chunks.json"))]
        if existing_chunks != chunks:
            raise ValueError("an incompatible RAG snapshot already exists for these settings")
        chunk_store.ensure_schema()
        chunk_store.replace_book(course_id, upload.book_id, chunks)
        return ref

    @staticmethod
    def _check_identity(upload: BookUpload, bundle: StageBundle, stage: str) -> None:
        if bundle.stage != stage or bundle.book_id != upload.book_id:
            raise ValueError("stage or book identity mismatch")
        if bundle.source_sha256 != upload.file_sha256:
            raise ValueError("source file fingerprint mismatch")
        if payload_digest(bundle.payload) != bundle.payload_sha256:
            raise ValueError("imported payload digest mismatch")

    @staticmethod
    def _check_blocks(parsed: ParsedBook) -> None:
        if not parsed.blocks or len({b.block_id for b in parsed.blocks}) != len(parsed.blocks):
            raise ValueError("OCR requires non-empty unique source blocks")
        if any(r.book_id != parsed.book_id or r.page_no > parsed.page_count
               for block in parsed.blocks for r in block.refs):
            raise ValueError("OCR page provenance is outside the source book")

