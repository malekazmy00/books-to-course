"""Resume one stored partial free-model response and top up only missing MCQs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/phase5-live-src/llm")
sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0, "/tmp/phase5-live-src")

from b2c_contracts import (Block, CourseOutline, Lesson, LessonSpan, Module, PageRef,
                           ParsedBook, StructuredChunk)
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_llm_gateway import LLMGateway
from b2c_llm_gateway import client as gateway_client
from b2c_llm_gateway.errors import InvalidLLMOutput, LLMOutputTruncated
from b2c_pedagogy.lesson_enrichment import (
    _AssetsCandidate, _make_lesson_assets, _normalized_question_stem, generate_lesson_enrichments,
)

MODEL = "cohere/north-mini-code:free"
BASE_URL = "https://openrouter.ai/api/v1"
COURSE = "phase5-seatweaving-cohere-bloom20-once"
LESSON = "seatweaving.steps2-7"
SOURCE_PROMPT = "phase5-seatweaving-cohere-bloom20-v1-apply-analyze:generate:attempt-1"
PROMPT = "phase5-seatweaving-cohere-bloom20-topup-v2"


class RecordingGateway:
    """Persist every new raw completion before validation/asset assembly."""
    def __init__(self, inner, store, source_hash):
        self.inner = inner
        self.store = store
        self.source_hash = source_hash
        self.model_id = inner.model_id
        self.raw_refs = []

    @property
    def usage_totals(self):
        return self.inner.usage_totals

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
        try:
            result, parsed = self.inner.structured_with_result(
                messages, schema, prompt_version=prompt_version, temperature=temperature,
                max_tokens=max_tokens)
        except LLMOutputTruncated as exc:
            self._save(prompt_version, exc.last_output,
                       {"finish_reason": exc.finish_reason, "model_id": exc.model_id,
                        "usage": exc.usage, "truncated": True})
            raise
        except InvalidLLMOutput as exc:
            self._save(prompt_version, exc.last_output,
                       {"schema_errors": exc.errors, "attempts": exc.attempts, "usage": None})
            raise
        self._save(prompt_version, result.text,
                   {"usage": result.usage, "attempts": result.attempts})
        return parsed


class ReplayPartialGateway:
    """Reuse saved valid items locally; delegate only repair/top-up/evaluation/solver calls."""
    def __init__(self, recorder, partial_raw, source_hash, excluded_question_stems=()):
        self.recorder = recorder
        self.partial_raw = partial_raw
        self.source_hash = source_hash
        self.excluded_question_stems = list(excluded_question_stems)
        self.model_id = recorder.model_id
        self.replayed = False
        self.raw_refs = []

    @property
    def usage_totals(self):
        return self.recorder.usage_totals

    def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
        if prompt_version == PROMPT + ":generate:attempt-1":
            if self.replayed:
                raise RuntimeError("initial partial may be replayed only once")
            self.replayed = True
            return schema.model_validate_json(self.partial_raw)
        result = self.recorder.structured(messages, schema, prompt_version=prompt_version,
                                          temperature=temperature, max_tokens=max_tokens)
        self.raw_refs = self.recorder.raw_refs
        return result


def main():
    api_key = sys.stdin.read().strip()
    if not api_key:
        raise SystemExit("OpenRouter key required via stdin")
    raw_pages = [(12, Path("/tmp/phase5-live-src/seat-weaving-j016.txt").read_text(encoding="utf-8").strip()),
                 (13, Path("/tmp/phase5-live-src/seat-weaving-j017.txt").read_text(encoding="utf-8").strip())]
    original_source_hash = hashlib.sha256("\n\n".join(f"{p}\n{t}" for p, t in raw_pages).encode()).hexdigest()
    pages = [(p, re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", t)) for p, t in raw_pages]
    source_hash = hashlib.sha256("\n\n".join(f"{p}\n{t}" for p, t in pages).encode()).hexdigest()
    if source_hash != original_source_hash:
        raise RuntimeError("stored partial source and current fixture differ")

    settings = Settings()
    store = ArtifactStore.from_settings(settings)
    objects = store.s3.list_objects_v2(
        Bucket=store.bucket, Prefix=f"{COURSE}/phase5-raw-completions/", MaxKeys=100)
    stored = []
    for item in objects.get("Contents", []):
        if not item["Key"].endswith("/raw-completion.json"):
            continue
        raw_obj = store.s3.get_object(Bucket=store.bucket, Key=item["Key"])["Body"].read()
        envelope = json.loads(raw_obj)
        if envelope.get("prompt_version") == SOURCE_PROMPT:
            stored.append((item["Key"], envelope))
    if len(stored) != 1:
        raise RuntimeError(f"expected one saved partial source response; found {len(stored)}")
    _, envelope = stored[0]
    if envelope.get("source_sha256") != source_hash or envelope.get("model_id") != MODEL:
        raise RuntimeError("saved partial metadata does not match source/model")
    partial_raw = envelope.get("raw_completion", "")
    if not partial_raw:
        raise RuntimeError("saved partial completion is empty")
    try:
        partial_payload = json.loads(partial_raw)
        partial_count = len(partial_payload.get("questions", []))
    except Exception as exc:
        raise RuntimeError("saved partial completion is not valid JSON") from exc
    if partial_count != 16:
        raise RuntimeError(f"expected 16 saved questions for top-up, found {partial_count}")
    sentence_count = len(partial_payload.get("sentences", []))
    original_candidate = _AssetsCandidate.model_validate_json(partial_raw)
    parent_raw_sha = hashlib.sha256(partial_raw.encode()).hexdigest()

    artifact_digest = hashlib.sha256((source_hash + MODEL + PROMPT + "final-artifact-v1").encode()).hexdigest()
    if store.exists(COURSE, "phase5-enrichment-canary", artifact_digest) is not None:
        raise RuntimeError("final artifact for this bounded top-up run already exists; refusing another inference")

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

    question_exclusions = []
    seen_stems = set()
    retained = []
    retained_ordinals = []
    for ordinal, question in enumerate(original_candidate.questions, 1):
        stem_key = _normalized_question_stem(question.stem)
        refs = question.sentence_indices
        error = None
        if any(index < 0 or index >= sentence_count for index in refs):
            error = "citation_index_out_of_range"
        elif not stem_key or stem_key in seen_stems:
            error = "duplicate_or_empty_stem"
        else:
            try:
                single = original_candidate.model_copy(update={"questions": [question]})
                _make_lesson_assets(LESSON, single, chunks, prompt_version=PROMPT)
            except ValueError as exc:
                error = str(exc).split(": ", 1)[-1]
        if error:
            question_exclusions.append({"ordinal": ordinal, "stem": question.stem, "reason": error})
        else:
            seen_stems.add(stem_key)
            retained.append(question)
            retained_ordinals.append(ordinal)
    if len(question_exclusions) > 5:
        raise RuntimeError("bounded top-up refused: more than five source-invalid/duplicate questions: " +
                           json.dumps([{k: v for k, v in item.items() if k != "stem"} for item in question_exclusions]))
    excluded_question_stems = [item["stem"] for item in question_exclusions]
    saved_candidate = original_candidate.model_copy(update={"questions": retained})
    partial_replay_raw = saved_candidate.model_dump_json()
    adapter_envelope = json.dumps({
        "adapter": "bounded-invalid-question-exclusion-v2",
        "parent_raw_completion_sha256": parent_raw_sha, "source_sha256": source_hash,
        "excluded_questions": [{k: v for k, v in item.items() if k != "stem"} for item in question_exclusions],
        "retained_ordinals": retained_ordinals, "retained_questions": len(retained),
        "raw_completion": partial_replay_raw,
    }, ensure_ascii=False, sort_keys=True).encode()
    adapter_key = hashlib.sha256((source_hash + parent_raw_sha + "partial-reference-adapter-v2").encode()).hexdigest()
    adapter_ref = store.put(COURSE, "phase5-raw-adaptations", adapter_key, {"adapted-partial.json": adapter_envelope})
    if store.read(adapter_ref, "adapted-partial.json") != adapter_envelope:
        raise RuntimeError("adapted partial readback mismatch")
    print(json.dumps({"event": "saved_partial_verified", "source_questions": partial_count,
                      "retained_questions": len(retained),
                      "excluded_questions": [{k: v for k, v in item.items() if k != "stem"} for item in question_exclusions],
                      "source_sha256": source_hash, "model": MODEL,
                      "adapter_sha256": hashlib.sha256(adapter_envelope).hexdigest()}), flush=True)

    saved_candidate = _AssetsCandidate.model_validate_json(partial_replay_raw)
    print(json.dumps({"event": "saved_partial_preflight_pass", "retained_question_count": len(retained),
                      "excluded_question_count": len(question_exclusions),
                      "excluded_ordinals": [item["ordinal"] for item in question_exclusions],
                      "bloom_apply_analyze": sum(q.bloom in {"apply", "analyze"} for q in retained)}),
          flush=True)
    if os.getenv("PHASE5_PREFLIGHT_ONLY") == "1":
        print(json.dumps({"status": "preflight_only_pass_no_inference"}), flush=True)
        return

    gateway_client.MAX_RETRIES = 0
    gateway_client.OPENROUTER_RETRYABLE_POOL_MARKERS = ()
    inner = LLMGateway(BASE_URL, MODEL, api_key=api_key, allow_remote=True,
                       timeout_s=120, capture_content=False, reasoning_effort="none")
    recorder = RecordingGateway(inner, store, source_hash)
    gateway = ReplayPartialGateway(recorder, partial_replay_raw, source_hash, excluded_question_stems)
    try:
        [lesson] = generate_lesson_enrichments(
            outline, book, chunks, gateway, question_count=20, max_tokens=6144,
            prompt_version=PROMPT, lesson_ids=[LESSON],
            excluded_question_stems=gateway.excluded_question_stems, question_repair_limit=0)
    except Exception as exc:
        print(json.dumps({"status": "topup_or_quality_gate_failed", "error_type": type(exc).__name__,
                          "error": str(exc), "provider_usage": gateway.usage_totals,
                          "raw_completion_refs": gateway.raw_refs}, sort_keys=True), flush=True)
        raise
    payload = lesson.model_dump(mode="json")
    payload["evaluation_metadata"] = {"canary": True, "gold_eligible": False, "model_id": MODEL,
        "source": "Seat Weaving (1917), pp.12-13, stored internal_test_only OCR reference",
        "source_sha256": source_hash, "original_source_sha256": original_source_hash,
        "source_normalization": "OCR control characters replaced with spaces",
        "source_chunk_ids": [chunk.chunk_id for chunk in chunks],
        "provider_usage": gateway.usage_totals, "raw_completion_refs": gateway.raw_refs,
        "top_up_count": 20 - len(retained), "replacement_count": len(question_exclusions),
        "initial_questions_reused": len(retained),
        "invalid_raw_questions_replaced": [{k: v for k, v in item.items() if k != "stem"} for item in question_exclusions],
        "partial_source_raw_sha256": parent_raw_sha, "partial_adapter_artifact_ref": adapter_ref.model_dump(mode="json")}
    artifact_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ref = store.put(COURSE, "phase5-enrichment-canary", artifact_digest, {"lesson-enrichment.json": artifact_bytes})
    readback = store.read(ref, "lesson-enrichment.json")
    if readback != artifact_bytes:
        raise RuntimeError("lesson artifact readback mismatch")
    print(json.dumps({"status": "artifact_saved", "artifact_ref": ref.model_dump(mode="json"),
        "readback_sha256": hashlib.sha256(readback).hexdigest(),
        "question_count": len(lesson.questions), "higher_bloom": sum(q.bloom in {"apply", "analyze"} for q in lesson.questions),
        "automated_evaluation": lesson.automated_evaluation,
        "provider_usage": gateway.usage_totals, "raw_completion_refs": gateway.raw_refs}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
