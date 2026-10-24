"""One-request live enrichment canary from the stored Think Stats 2e Chapter 3 source."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
from typing import Literal
import b2c_pedagogy
sys.path.insert(0, "/tmp/phase5-live-src/llm")
sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
b2c_pedagogy.__path__.insert(0, "/tmp/phase5-live-src")
from pydantic import Field, create_model
from b2c_contracts import (Block, CourseOutline, Lesson, LessonSpan, Module, PageRef,
                           ParsedBook, StructuredChunk)
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_llm_gateway import LLMGateway
from b2c_llm_gateway import client as gateway_client
from b2c_pedagogy.lesson_enrichment import (_AssetsCandidate, _MCQCandidate, _Sentence,
                                             _exact_list_schema, _make_lesson_assets)
from b2c_pedagogy.qa_generation import _derive_scopes

MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
BASE_URL = "https://openrouter.ai/api/v1"
INPUT_PRICE = 0.0
OUTPUT_PRICE = 0.0
MAX_TOKENS = 4096
COURSE = "phase5-thinkstats2-local-canary"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-raw-key")
    args = parser.parse_args()
    key = sys.stdin.read().strip()
    if not args.reuse_raw_key and not key:
        raise SystemExit("API key required via stdin")
    src = Path("/tmp/phase5-live-src")
    parsed_bytes = (src / "thinkstats-parsed.json").read_bytes()
    chunks_bytes = (src / "thinkstats-chunks.jsonl").read_bytes()
    parsed = ParsedBook.model_validate_json(parsed_bytes).model_copy(update={"role": "spine"})
    raw_chunks = [json.loads(line) for line in chunks_bytes.splitlines()
                  if line.strip() and json.loads(line).get("chunk_id") == "think-stats-2e:ch04"]
    chunks = []
    for item in raw_chunks:
        pages = set(item["pages"])
        block_ids = [block.block_id for block in parsed.blocks
                     if any(ref.page_no in pages for ref in block.refs)]
        chunks.append(StructuredChunk.model_validate({
            "chunk_id": item["chunk_id"], "book_id": item["book_id"], "role": "spine",
            "section_path": item["section_path"], "block_ids": block_ids,
            "pages": item["pages"], "text": item["text"],
            "token_count": max(1, len(item["text"].split())),
            "embedding_model": "evaluation-source-not-embedded",
        }))
    selected_pages = set(raw_chunks[0]["pages"])
    blocks = [block for block in parsed.blocks
              if any(ref.page_no in selected_pages for ref in block.refs)]
    if not blocks:
        raise SystemExit("stored class-size chunk has no parsed blocks in its pages")
    start, end = min(selected_pages), max(selected_pages)
    lesson = Lesson(lesson_id="ch03.class-size-paradox", title="The Class Size Paradox", order=1,
                    spine_span=LessonSpan(start_block=blocks[0].block_id,
                                          end_block=blocks[-1].block_id,
                                          pages=(start, end)), provenance="headings")
    outline = CourseOutline(course_id=COURSE, spine_book_id=parsed.book_id,
                            supplementary_book_ids=[], requires_review=False,
                            modules=[Module(module_id="m01", title="The Class Size Paradox", order=1,
                                            lessons=[lesson])])
    scopes, _ = _derive_scopes(outline, parsed, chunks, lesson_question_count=10,
                               group_question_count=1, course_question_count=1,
                               outline_approved=True)
    scope = next(scope for scope in scopes if scope.kind == "lesson")
    source_ids = tuple(dict.fromkeys(chunk.chunk_id for chunk in scope.chunks))
    citation = Literal.__getitem__(source_ids)
    Sentence = create_model("StoredSourceSentence", __base__=_Sentence,
                            citations=(list[citation], Field(min_length=1)))
    Assets = create_model("StoredSourceAssets", __base__=_AssetsCandidate,
                          sentences=(list[Sentence], Field(min_length=3, max_length=20)))
    schema = _exact_list_schema(Assets, "questions", _MCQCandidate, 10)
    source_payload = [{"chunk_id": c.chunk_id, "book_id": c.book_id, "role": c.role,
                       "pages": c.pages, "text": c.text} for c in scope.chunks]
    messages = [
        {"role": "system", "content": (
            "Generate a concise English lesson using only supplied source chunks: 4-8 cited factual sentences, all marked primary because this evaluation scope has one spine source. Never mark any sentence supplementary. "
            "at least 3 summary points, source-supported glossary entries, and exactly 10 MCQs. Each question must target a different, non-obvious condition, exception, code example, or exact claim from this class-size-paradox chunk. Avoid generic probability questions, repeated facts, and calculations. Each cited sentence must preserve every premise needed to answer. At least 8 MCQs "
            "must be understand/apply/analyze/evaluate/create (not remember). Each MCQ's answer must be directly "
            "supported by one cited lesson sentence; prefer obscure source-specific examples/details that require "
            "reading this chapter, not general textbook knowledge. Do not require unstated calculation. Use 4 "
            "distinct, plausible option texts with no A/B/C labels; one correct answer index; no duplicates, "
            "answer leakage, or all/none choices. All summaries/glossary/MCQs cite generated sentence indices. "
            "Return the exact requested schema only."
        )},
        {"role": "user", "content": json.dumps({"lesson_id": scope.scope_id,
            "lesson_title": scope.scope_title, "question_count": 10,
            "allowed_citation_ids": list(source_ids), "source_chunks": source_payload}, ensure_ascii=False)},
    ]
    request_bound = len(json.dumps({"model": MODEL, "messages": messages, "max_tokens": MAX_TOKENS,
        "response_schema": schema.model_json_schema()}, ensure_ascii=False).encode())
    max_cost = request_bound * INPUT_PRICE + MAX_TOKENS * OUTPUT_PRICE
    if max_cost > 0.006 and not args.reuse_raw_key:
        raise SystemExit("conservative live generation bound exceeds remaining canary budget")
    store = ArtifactStore.from_settings(Settings())
    if args.reuse_raw_key:
        raw_ref = store.exists(COURSE, "phase5-raw-completions", args.reuse_raw_key)
        if raw_ref is None:
            raise SystemExit("requested raw completion artifact is unavailable")
        raw_readback = store.read(raw_ref, "raw-completion.json")
        raw_record = json.loads(raw_readback)
        candidate = schema.model_validate_json(raw_record["raw_completion"])
        usage_totals = raw_record["usage_totals"]
    else:
        gateway_client.MAX_RETRIES = 0
        gateway_client.OPENROUTER_RETRYABLE_POOL_MARKERS = ()
        gateway = LLMGateway(BASE_URL, MODEL, api_key=key, allow_remote=True,
                             timeout_s=120, capture_content=False, reasoning_effort="none")
        print(json.dumps({"event":"generation_started", "model":MODEL,
                          "max_tokens":MAX_TOKENS,"reserved_max_usd":round(max_cost,8)},file=sys.stderr,flush=True))
        result, candidate = gateway.structured_with_result(
            messages, schema, prompt_version="phase5-thinkstats2-class-size-v1",
            temperature=0.0, max_tokens=MAX_TOKENS)
        usage_totals = gateway.usage_totals
        raw_digest = hashlib.sha256((result.text + MODEL + "phase5-thinkstats2-class-size-v1").encode()).hexdigest()
        raw_payload = json.dumps({"model_id": MODEL, "usage": result.usage,
            "usage_totals": usage_totals, "raw_completion": result.text},
            ensure_ascii=False, sort_keys=True).encode()
        raw_ref = store.put(COURSE, "phase5-raw-completions", raw_digest,
                            {"raw-completion.json": raw_payload})
        raw_readback = store.read(raw_ref, "raw-completion.json")
        if raw_readback != raw_payload:
            raise RuntimeError("raw completion artifact readback mismatch")
    # The stored source is the sole spine source in this synthetic evaluation scope.
    # Normalize only the generated source-role label (not text or evidence) to that known role.
    candidate = candidate.model_copy(update={"sentences": [
        sentence.model_copy(update={"kind": "primary"}) for sentence in candidate.sentences
    ]})
    assets = _make_lesson_assets(scope.scope_id, candidate, scope.chunks,
                                 prompt_version="phase5-thinkstats2-class-size-v1")
    higher = sum(q.bloom != "remember" for q in assets.questions)
    if higher / len(assets.questions) < 0.8:
        raise SystemExit("automated Bloom gate failed")
    payload = assets.model_dump(mode="json")
    payload["evaluation_metadata"] = {"canary":True,"gold_eligible":False,"model_id":MODEL,
        "source_pdf":"Think Stats 2e, Chapter 3.4 (class size paradox); internal research-only source",
        "provider_usage":usage_totals,
        "source_chunk_ids":sorted(source_ids),"raw_completion_artifact_ref":raw_ref.model_dump(mode="json"),
        "raw_completion_sha256":hashlib.sha256(raw_readback).hexdigest()}
    raw = json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()
    digest = hashlib.sha256((hashlib.sha256(parsed_bytes).hexdigest()+":"+
        hashlib.sha256(chunks_bytes).hexdigest()+":"+MODEL+":phase5-thinkstats2-class-size-v2").encode()).hexdigest()
    ref = store.put(COURSE,"phase5-enrichment-canary",digest,{"lesson-enrichment.json":raw})
    readback = store.read(ref,"lesson-enrichment.json")
    if readback != raw:
        raise RuntimeError("MinIO artifact readback mismatch")
    validated = json.loads(readback)
    print(json.dumps({"status":"source_artifact_saved","course_id":COURSE,
        "lesson_id":assets.lesson_id,"artifact_ref":ref.model_dump(mode="json"),
        "readback_sha256":hashlib.sha256(readback).hexdigest(),
        "source_sha256":hashlib.sha256(parsed_bytes).hexdigest(),
        "question_count":len(assets.questions),"higher_bloom_count":higher,
        "sentence_count":len(assets.draft.sentences),"summary_count":len(assets.summary.points),
        "glossary_count":len(assets.glossary),"source_chunk_ids":sorted(source_ids),
        "provider_usage":usage_totals,"raw_completion_artifact_ref":raw_ref.model_dump(mode="json"),
        "raw_completion_sha256":hashlib.sha256(raw_readback).hexdigest(),"status_contract":validated["status"]},sort_keys=True))

if __name__ == "__main__":
    main()
