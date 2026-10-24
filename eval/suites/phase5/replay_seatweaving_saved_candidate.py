"""Exercise the real Phase 5 lesson-enrichment path on stored Seat Weaving pages."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/phase5-live-src/llm")
sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0, "/tmp/phase5-live-src")

from b2c_contracts import (ArtifactRef, Block, CourseOutline, Lesson, LessonSpan, Module, PageRef,
                           ParsedBook, StructuredChunk)
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_llm_gateway import LLMGateway
from b2c_llm_gateway import client as gateway_client
from b2c_llm_gateway.errors import InvalidLLMOutput
from b2c_pedagogy.lesson_enrichment import generate_lesson_enrichments

MODEL = "apodex/apodex-1.1-mini:free"
BASE_URL = "https://openrouter.ai/api/v1"
COURSE = "phase5-seatweaving-apodex-mini-product-canary"
LESSON = "seatweaving.steps2-7"
PROMPT = "phase5-seatweaving-apodex-mini-v13-role-aligned-citations"
SAVED_RAW_KEY = "f7e4b44cf2c9f1143aac1cc500664d3761596e4c235a8f8d40aaa18b06f8dfe3"
SAVED_RAW_SHA256 = "44ad1501027f1fae7366467083115ed7e7fd3483321a7cadc2a0950db2d9ea8a"
SAVED_GENERATION_USAGE = {"api_calls": 1, "cost_reported_calls": 1, "cost_usd": 0.0,
                          "input_tokens": 2499, "output_tokens": 3365}


class RecordingGateway:
    """Persist every successful or invalid raw completion before returning/raising."""
    def __init__(self, inner, store, source_hash, replay_text, replay_ref):
        self.inner = inner
        self.store = store
        self.source_hash = source_hash
        self.model_id = inner.model_id
        self.replay_text = replay_text
        self.replay_pending = True
        self.raw_refs = [replay_ref]

    @property
    def usage_totals(self):
        totals = dict(self.inner.usage_totals)
        for field, value in SAVED_GENERATION_USAGE.items():
            current = totals.get(field)
            if isinstance(value, (int, float)) and isinstance(current, (int, float)):
                totals[field] = value + current
            elif current is None:
                totals[field] = value
        return totals

    def _save(self, prompt_version, text, metadata):
        raw = json.dumps({"model_id": MODEL, "prompt_version": prompt_version,
                          "source_sha256": self.source_hash, **metadata,
                          "raw_completion": text}, ensure_ascii=False, sort_keys=True).encode()
        key = hashlib.sha256((self.source_hash + prompt_version + text).encode()).hexdigest()
        ref = self.store.put(COURSE, "phase5-raw-completions", key, {"raw-completion.json": raw})
        if self.store.read(ref, "raw-completion.json") != raw:
            raise RuntimeError("raw completion readback mismatch")
        self.raw_refs.append({**ref.model_dump(mode="json"),
                              "raw_sha256": hashlib.sha256(raw).hexdigest()})

    def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
        if self.replay_pending and schema.__name__ == "ExactQuestions":
            self.replay_pending = False
            return schema.model_validate_json(self.replay_text)
        try:
            result, parsed = self.inner.structured_with_result(
                messages, schema, prompt_version=prompt_version, temperature=temperature,
                max_tokens=max_tokens)
        except InvalidLLMOutput as exc:
            self._save(prompt_version, exc.last_output,
                       {"schema_errors": exc.errors, "attempts": exc.attempts, "usage": None})
            raise
        self._save(prompt_version, result.text,
                   {"usage": result.usage, "attempts": result.attempts})
        return parsed


def main():
    key = sys.stdin.read().strip()
    if not key:
        raise SystemExit("OpenRouter key required via stdin")
    raw_pages = [(12, Path("/tmp/phase5-live-src/seat-weaving-j016.txt").read_text(encoding="utf-8").strip()),
                 (13, Path("/tmp/phase5-live-src/seat-weaving-j017.txt").read_text(encoding="utf-8").strip())]
    original_source_hash = hashlib.sha256("\n\n".join(f"{p}\n{t}" for p, t in raw_pages).encode()).hexdigest()
    pages = [(p, re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", t)) for p, t in raw_pages]
    source_hash = hashlib.sha256("\n\n".join(f"{p}\n{t}" for p, t in pages).encode()).hexdigest()
    book_id = "seat-weaving-1917"
    blocks = [Block(block_id=f"seatweaving-j{page}", type="paragraph", text=text,
                    refs=[PageRef(book_id=book_id, page_no=page)],
                    section_path=["Caning; The Seven Steps"], ocr=False) for page, text in pages]
    book = ParsedBook(book_id=book_id, title="Seat Weaving (1917), pp.12-13", role="spine",
        page_count=2, toc_source="headings", parser={"name": "stored-groundtruth", "version": "internal-test-only"},
        blocks=blocks)
    chunks = [StructuredChunk(chunk_id=f"seat-weaving-1917:j{page}", book_id=book_id, role="spine",
        section_path=["Caning; The Seven Steps"], block_ids=[f"seatweaving-j{page}"], pages=[page],
        text=text, token_count=max(1, len(text.split())), embedding_model="evaluation-source-not-embedded")
        for page, text in pages]
    outline = CourseOutline(course_id=COURSE, spine_book_id=book_id, supplementary_book_ids=[],
        requires_review=False, modules=[Module(module_id="m1", title="Caning", order=1, lessons=[
            Lesson(lesson_id=LESSON, title="Caning: The Seven Steps", order=1,
                   spine_span=LessonSpan(start_block="seatweaving-j12", end_block="seatweaving-j13",
                                         pages=(12, 13)), provenance="headings")])])
    store = ArtifactStore.from_settings(Settings())
    raw_ref = ArtifactRef(stage="phase5-raw-completions", key=SAVED_RAW_KEY,
        uri=f"s3://artifacts/{COURSE}/phase5-raw-completions/{SAVED_RAW_KEY}/",
        sha256="addf01698643a03b5ecacf99bf3768cd041b55ea0219f5f4256164f25a73e460",
        created_at="2026-10-05T05:09:18.207251Z")
    raw_bytes = store.read(raw_ref, "raw-completion.json")
    if hashlib.sha256(raw_bytes).hexdigest() != SAVED_RAW_SHA256:
        raise RuntimeError("saved raw object SHA-256 mismatch")
    raw_envelope = json.loads(raw_bytes)
    raw_completion = raw_envelope["raw_completion"]
    replay_ref = {**raw_ref.model_dump(mode="json"), "raw_sha256": SAVED_RAW_SHA256}
    gateway_client.MAX_RETRIES = 0
    gateway_client.OPENROUTER_RETRYABLE_POOL_MARKERS = ()
    inner = LLMGateway(BASE_URL, MODEL, api_key=key, allow_remote=True,
                       timeout_s=120, capture_content=False, reasoning_effort="none")
    gateway = RecordingGateway(inner, store, source_hash, raw_completion, replay_ref)
    print(json.dumps({"event": "saved_candidate_replay_started", "model": MODEL,
                      "source_sha256": source_hash, "original_source_sha256": original_source_hash}),
          file=sys.stderr, flush=True)
    try:
        [lesson] = generate_lesson_enrichments(
            outline, book, chunks, gateway, question_count=20, max_tokens=6144,
            prompt_version=PROMPT, lesson_ids=[LESSON])
    except Exception as exc:
        print(json.dumps({"status": "product_path_failed", "error_type": type(exc).__name__,
                          "error": str(exc), "provider_usage": gateway.usage_totals,
                          "raw_completion_refs": gateway.raw_refs}, sort_keys=True), flush=True)
        raise
    payload = lesson.model_dump(mode="json")
    payload["evaluation_metadata"] = {"canary": True, "gold_eligible": False, "model_id": MODEL,
        "source": "Seat Weaving (1917), pp.12-13, stored internal_test_only OCR reference",
        "source_sha256": source_hash, "original_source_sha256": original_source_hash,
        "source_normalization": "OCR control characters replaced with spaces",
        "source_chunk_ids": [chunk.chunk_id for chunk in chunks],
        "provider_usage": gateway.usage_totals, "raw_completion_refs": gateway.raw_refs}
    artifact_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256((source_hash + MODEL + PROMPT + "final-artifact-v1").encode()).hexdigest()
    ref = store.put(COURSE, "phase5-enrichment-canary", digest, {"lesson-enrichment.json": artifact_bytes})
    readback = store.read(ref, "lesson-enrichment.json")
    if readback != artifact_bytes:
        raise RuntimeError("lesson artifact readback mismatch")
    print(json.dumps({"status": "artifact_saved", "artifact_ref": ref.model_dump(mode="json"),
        "readback_sha256": hashlib.sha256(readback).hexdigest(),
        "question_count": len(lesson.questions), "higher_bloom": sum(q.bloom != "remember" for q in lesson.questions),
        "automated_evaluation": lesson.automated_evaluation,
        "provider_usage": gateway.usage_totals, "raw_completion_refs": gateway.raw_refs}, sort_keys=True))


if __name__ == "__main__":
    main()
