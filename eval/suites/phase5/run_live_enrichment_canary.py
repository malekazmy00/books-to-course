"""Run one bounded live lesson-enrichment canary against OpenRouter.

The API key is read from stdin, never from argv, the environment, or a repository
file. The result is stored as a separate canary artifact, not as a production
course-qa artifact. Run inside the B2C worker runtime so its DB/MinIO settings
and network are reused without restarting the service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import psycopg
from b2c_contracts import ArtifactRef, CourseOutline, ParsedBook
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_indexing import PostgresChunkStore


DEFAULT_MODEL_ID = "nvidia/nemotron-3-super-120b-a12b:free"
BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 4096
DEFAULT_PROMPT_PRICE_PER_TOKEN = 0.0
DEFAULT_COMPLETION_PRICE_PER_TOKEN = 0.0
MAX_COST_USD = 0.02


def latest_approved_outline(store: ArtifactStore, course_id: str) -> ArtifactRef:
    response = store.s3.list_objects_v2(
        Bucket=store.bucket, Prefix=f"{course_id}/approve-outline/", MaxKeys=1000
    )
    manifests = [item["Key"] for item in response.get("Contents", [])
                 if item["Key"].endswith("/_manifest.json")]
    refs = []
    for key in manifests:
        content = store.s3.get_object(Bucket=store.bucket, Key=key)["Body"].read()
        ref = ArtifactRef.model_validate_json(content)
        if ref.stage == "approve-outline":
            refs.append(ref)
    if not refs:
        raise RuntimeError("no approved outline artifact found for the requested course")
    return max(refs, key=lambda ref: ref.created_at)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--lesson-id", required=True)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--prompt-price-per-token", type=float, default=DEFAULT_PROMPT_PRICE_PER_TOKEN)
    parser.add_argument("--completion-price-per-token", type=float, default=DEFAULT_COMPLETION_PRICE_PER_TOKEN)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--probe-only", action="store_true", help="validate source/schema/store path without inference")
    args = parser.parse_args()
    api_key = sys.stdin.read().strip()
    model_id = args.model_id
    prompt_price_per_token = args.prompt_price_per_token
    completion_price_per_token = args.completion_price_per_token
    if not api_key and not (args.dry_run or args.probe_only):
        raise SystemExit("OpenRouter key must be provided on stdin")

    # The worker image may predate the unmerged module under test. Add the mounted
    # current source directory to the installed package's search path when present.
    import b2c_pedagogy
    sys.path.insert(0, "/tmp/phase5-live-src/llm")
    sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
    from b2c_llm_gateway import LLMGateway
    source_dir = Path("/tmp/phase5-live-src")
    if source_dir.exists():
        b2c_pedagogy.__path__.insert(0, str(source_dir))
    from b2c_pedagogy.lesson_enrichment import (
        _AssetsCandidate, _MCQCandidate, _Sentence, _exact_list_schema, _make_lesson_assets,
    )
    from b2c_pedagogy.qa_generation import _derive_scopes
    from b2c_llm_gateway import client as gateway_client
    from pydantic import Field, create_model
    from typing import Literal

    settings = Settings()
    store = ArtifactStore.from_settings(settings)
    approved_ref = latest_approved_outline(store, args.course_id)
    approved = json.loads(store.read(approved_ref, "approved-outline.json"))
    outline = CourseOutline.model_validate(approved["outline"])
    if outline.course_id != args.course_id:
        raise RuntimeError("approved outline belongs to another course")
    parsed_ref = ArtifactRef.model_validate(approved["spine_parsed_ref"])
    spine = ParsedBook.model_validate_json(store.read(parsed_ref, "parsed-book.json"))
    chunks = PostgresChunkStore(lambda: psycopg.connect(settings.pg_dsn)).list_chunks(
        args.course_id, book_ids={outline.spine_book_id}
    )

    # A single paid-provider request is allowed for this bounded canary. Do not
    # let gateway validation retries exceed it.
    gateway_client.MAX_RETRIES = 0
    gateway_client.OPENROUTER_RETRYABLE_POOL_MARKERS = ()
    scopes, _ = _derive_scopes(
        outline, spine, chunks, lesson_question_count=10, group_question_count=1,
        course_question_count=1, outline_approved=True,
    )
    matches = [scope for scope in scopes if scope.kind == "lesson" and scope.scope_id == args.lesson_id]
    if len(matches) != 1:
        raise RuntimeError("requested lesson is not in the approved outline")
    scope = matches[0]
    question_count = 10
    allowed_citation_ids = tuple(dict.fromkeys(chunk.chunk_id for chunk in scope.chunks))
    citation_id = Literal.__getitem__(allowed_citation_ids)
    PinnedSentence = create_model(
        "PinnedSentence", __base__=_Sentence,
        citations=(list[citation_id], Field(min_length=1)),
    )
    PinnedAssets = create_model(
        "PinnedAssets", __base__=_AssetsCandidate,
        sentences=(list[PinnedSentence], Field(min_length=3, max_length=20)),
    )
    schema = _exact_list_schema(PinnedAssets, "questions", _MCQCandidate, question_count)
    messages = [{"role": "system", "content": (
            "Create a concise English lesson grounded only in supplied chunks. Return 4-8 factual lesson sentences, "
            "at least three summary points, only explicitly supported glossary terms, and exactly ten MCQs. "
            "Every lesson sentence cites supplied chunk IDs and uses primary for spine evidence. Every summary, "
            "glossary entry, and MCQ cites sentence indices. Use 3-5 distinct options; Bloom must be above remember "
            "for at least 80% of MCQs; no all/none-of-the-above options or duplicate questions. "
            "Prefer text-specific details, author qualifications, examples, and contrasts unique to these chunks. "
            "Do not ask generic textbook questions solvable from general domain knowledge alone. Every MCQ must "
            "require the lesson's particular claim or example and use plausible distractors. Each answer must be "
            "directly supported by one cited lesson sentence; require no external knowledge, unstated arithmetic, or "
            "generic textbook formula. Build distractors from nearby but different claims in the chunks, not absurd "
            "alternatives. Avoid stems that repeat or reveal the correct option. Return JSON only."
        )}, {"role": "user", "content": json.dumps({
            "lesson_id": scope.scope_id, "lesson_title": scope.scope_title,
            "requested_question_count": question_count, "question_count": question_count,
            "allowed_citation_ids": list(allowed_citation_ids),
            "source_chunks": [{"chunk_id": chunk.chunk_id, "book_id": chunk.book_id,
                               "role": chunk.role, "pages": chunk.pages, "text": chunk.text}
                              for chunk in scope.chunks],
        }, ensure_ascii=False)}]
    request_bound = len(json.dumps({
        "model": model_id, "messages": messages, "max_tokens": MAX_TOKENS,
        "temperature": 0.0, "response_format": {"type": "json_schema",
        "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}},
    }, ensure_ascii=False).encode("utf-8"))
    reserved_max_cost = (request_bound * prompt_price_per_token
                         + MAX_TOKENS * completion_price_per_token)
    if reserved_max_cost > MAX_COST_USD:
        raise RuntimeError("conservative request cost bound exceeds the $0.02 canary cap")
    digest = hashlib.sha256(
        f"{approved_ref.sha256}:{model_id}:{args.lesson_id}:phase5-live-canary-generation-only-v10".encode()
    ).hexdigest()
    existing = store.exists(args.course_id, "phase5-enrichment-canary", digest)
    if existing is not None:
        saved = json.loads(store.read(existing, "lesson-enrichment.json"))
        print(json.dumps({"status": saved.get("status"), "cache_hit": True,
                          "artifact_ref": existing.model_dump(mode="json"),
                          "provider_usage": saved.get("evaluation_metadata", {}).get("provider_usage", {})},
                         sort_keys=True))
        return
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "model_id": model_id,
                          "source_chunk_count": len(scope.chunks),
                          "request_bound_bytes": request_bound,
                          "reserved_max_cost_usd": reserved_max_cost,
                          "cost_cap_usd": MAX_COST_USD, "cache_hit": False}, sort_keys=True))
        return
    if args.probe_only:
        print(json.dumps({"status": "probe_pass", "model_id": model_id, "schema_name": schema.__name__,
                          "schema_properties": len(schema.model_json_schema().get("properties", {})),
                          "source_chunk_count": len(scope.chunks), "cache_hit": False}, sort_keys=True))
        return
    gateway = LLMGateway(
        BASE_URL, model_id, api_key=api_key, allow_remote=True,
        timeout_s=120, capture_content=False, reasoning_effort="none",
    )
    print(json.dumps({"event": "inference_started", "model_id": model_id,
                      "max_tokens": MAX_TOKENS, "reserved_max_cost_usd": reserved_max_cost}, sort_keys=True),
          file=sys.stderr, flush=True)
    try:
        candidate = gateway.structured(
            messages, schema, prompt_version="phase5-live-canary-generation-only-v10",
            temperature=0.0, max_tokens=MAX_TOKENS,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "failed", "error_type": type(exc).__name__,
            "model_id": model_id, "provider_usage": gateway.usage_totals,
            "reserved_max_cost_usd": reserved_max_cost, "artifact_saved": False,
        }, sort_keys=True))
        raise SystemExit(2) from None
    print(json.dumps({"event": "inference_succeeded", "model_id": model_id,
                      "provider_usage": gateway.usage_totals}, sort_keys=True), file=sys.stderr, flush=True)
    try:
        lesson = _make_lesson_assets(scope.scope_id, candidate, scope.chunks,
                                     prompt_version="phase5-live-canary-generation-only-v10")
    except ValueError as exc:
        message = str(exc)
        safe_diagnostic = (message[:180] if len(message) <= 180 and
                           re.fullmatch(r"[A-Za-z0-9_.,: /-]+", message) else type(exc).__name__)
        print(json.dumps({"status": "failed_local_gate", "gate": "lesson_asset_validation",
                          "diagnostic": safe_diagnostic,
                          "provider_usage": gateway.usage_totals, "artifact_saved": False},
                         sort_keys=True), flush=True)
        raise SystemExit(3) from None
    higher_bloom = sum(question.bloom != "remember" for question in lesson.questions)
    bloom_pass = bool(lesson.questions) and higher_bloom / len(lesson.questions) >= 0.8
    if not bloom_pass:
        print(json.dumps({"status": "failed_local_gate", "gate": "bloom_above_remember",
                          "higher_bloom_count": higher_bloom, "question_count": len(lesson.questions),
                          "provider_usage": gateway.usage_totals, "artifact_saved": False},
                         sort_keys=True), flush=True)
        raise SystemExit(3)
    payload = lesson.model_dump(mode="json")
    payload["status"] = "evaluation_only"
    payload["automated_evaluation"] = {
        "citation_resolution_pass": True, "mcq_linter_pass": True,
        "bloom_above_remember_pass": bloom_pass, "solver_gate": "not_run_due_to_request_cap",
    }
    payload["evaluation_metadata"] = {
        "canary": True, "gold_eligible": False, "model_id": model_id,
        "generation_only": True,
        "provider_usage": gateway.usage_totals,
        "reserved_max_cost_usd": reserved_max_cost,
        "cost_estimate_usd": (
            gateway.usage_totals.get("cost_usd")
            if gateway.usage_totals.get("cost_usd") is not None
            else (gateway.usage_totals.get("input_tokens", 0) * prompt_price_per_token
                  + gateway.usage_totals.get("output_tokens", 0) * completion_price_per_token)
        ),
        "source_chunk_ids": sorted({citation for sentence in lesson.draft.sentences
                                     for citation in sentence.citations}),
    }
    print(json.dumps({"event": "artifact_write_started", "stage": "phase5-enrichment-canary"},
                     sort_keys=True), file=sys.stderr, flush=True)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    result_ref = store.put(args.course_id, "phase5-enrichment-canary", digest,
                           {"lesson-enrichment.json": serialized})
    readback = store.read(result_ref, "lesson-enrichment.json")
    readback_sha256 = hashlib.sha256(readback).hexdigest()
    if readback != serialized:
        raise RuntimeError("MinIO canary readback hash mismatch")
    print(json.dumps({
        "status": "evaluation_only", "course_id": args.course_id, "lesson_id": args.lesson_id,
        "model_id": model_id, "artifact_ref": result_ref.model_dump(mode="json"),
        "cache_hit": False, "readback_sha256": readback_sha256,
        "lesson_sentences": len(lesson.draft.sentences),
        "summary_points": len(lesson.summary.points), "glossary_terms": len(lesson.glossary),
        "mcqs": len(lesson.questions),
        "bloom_levels": sorted({question.bloom for question in lesson.questions}),
        "automated_evaluation": lesson.automated_evaluation,
        "provider_usage": gateway.usage_totals,
        "source_chunk_ids": payload["evaluation_metadata"]["source_chunk_ids"],
        "gold_eligible": False,
    }, sort_keys=True))



if __name__ == "__main__":
    main()
