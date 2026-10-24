from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, create_model

sys.path.insert(0, "/tmp/phase5-live-src/llm")
sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0, "/tmp/phase5-live-src")
from b2c_contracts import ArtifactRef
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_llm_gateway import LLMGateway
from b2c_llm_gateway import client as gateway_client
from b2c_llm_gateway import client as gateway_client
from b2c_pedagogy.lesson_enrichment import LessonEnrichment


MODEL_ID = "nvidia/nemotron-3-super-120b-a12b:free"
BASE_URL = "https://openrouter.ai/api/v1"


class _SolverChoices(BaseModel):
    model_config = ConfigDict(extra="forbid")




def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--lesson-id", required=True)
    parser.add_argument("--lesson-artifact-key", required=True)
    parser.add_argument("--model-id", default=MODEL_ID)
    args = parser.parse_args()
    model_id = args.model_id
    api_key = sys.stdin.read().strip()
    if not api_key:
        raise SystemExit("OpenRouter key required on stdin")

    settings = Settings()
    store = ArtifactStore.from_settings(settings)
    lesson_ref = store.exists(args.course_id, "phase5-enrichment-canary", args.lesson_artifact_key)
    if lesson_ref is None:
        raise RuntimeError("lesson enrichment artifact not found")
    lesson_bytes = store.read(lesson_ref, "lesson-enrichment.json")
    payload = json.loads(lesson_bytes)
    lesson = LessonEnrichment.model_validate(payload)
    if lesson.lesson_id != args.lesson_id:
        raise RuntimeError("lesson id mismatch")

    questions = [{
        "qid": q.qid, "stem": q.stem, "options": q.options,
        "answer_index": q.answer_index,
    } for q in lesson.questions]
    if len(questions) != 10:
        raise RuntimeError("bounded solver gate requires the ten-item canary lesson")
    public_questions = [{k: v for k, v in q.items() if k != "answer_index"} for q in questions]
    lesson_context = [sentence.text_en for sentence in lesson.draft.sentences]

    gateway_client.MAX_RETRIES = 0
    gateway_client.OPENROUTER_RETRYABLE_POOL_MARKERS = ()
    gateway_client.MAX_RETRIES = 0
    gateway_client.OPENROUTER_RETRYABLE_POOL_MARKERS = ()
    gateway = LLMGateway(BASE_URL, model_id, api_key=api_key, allow_remote=True,
                         timeout_s=90, capture_content=False, reasoning_effort="none")
    choice_types = tuple[tuple(
        Annotated[int, Field(ge=0, le=len(question["options"]) - 1)]
        for question in questions
    )]
    schema = create_model("ExactSolver10", __base__=_SolverChoices,
                          choices=(choice_types, ...))
    max_tokens = 128
    with_context_messages = [
        {"role": "system", "content": (
            "Answer each MCQ using only the supplied lesson sentences. Return one zero-based option index "
            "per question in order, in the exact JSON schema. No explanation."
        )},
        {"role": "user", "content": json.dumps({
            "lesson_sentences": lesson_context,
            "questions": public_questions,
        }, ensure_ascii=False)},
    ]
    blind_messages = [
        {"role": "system", "content": (
            "Answer each MCQ without any lesson or source context. Use general knowledge only. "
            "Return one zero-based option index per question in order, in the exact JSON schema. No explanation."
        )},
        {"role": "user", "content": json.dumps({"questions": public_questions}, ensure_ascii=False)},
    ]
    print(json.dumps({"event": "with_context_solver_started", "model_id": model_id,
                      "max_tokens": max_tokens}, sort_keys=True), file=sys.stderr, flush=True)
    with_context = gateway.structured(
        with_context_messages, schema, prompt_version="phase5-solver-with-context-v1",
        temperature=0.0, max_tokens=max_tokens,
    )
    print(json.dumps({"event": "with_context_solver_done",
                      "provider_usage": gateway.usage_totals}, sort_keys=True),
          file=sys.stderr, flush=True)
    print(json.dumps({"event": "blind_solver_started", "model_id": model_id,
                      "max_tokens": max_tokens}, sort_keys=True), file=sys.stderr, flush=True)
    blind = gateway.structured(
        blind_messages, schema, prompt_version="phase5-solver-blind-v1",
        temperature=0.0, max_tokens=max_tokens,
    )
    print(json.dumps({"event": "blind_solver_done",
                      "provider_usage": gateway.usage_totals}, sort_keys=True),
          file=sys.stderr, flush=True)

    expected = [q["answer_index"] for q in questions]
    if len(with_context.choices) != len(expected) or len(blind.choices) != len(expected):
        raise RuntimeError("solver result count mismatch")
    if any(choice < 0 or choice >= len(question["options"])
           for choices in (with_context.choices, blind.choices) for choice, question in zip(choices, questions)):
        raise RuntimeError("solver returned an out-of-range choice")
    ctx_correct = sum(a == b for a, b in zip(with_context.choices, expected))
    blind_correct = sum(a == b for a, b in zip(blind.choices, expected))
    ctx_accuracy = ctx_correct / len(expected)
    blind_accuracy = blind_correct / len(expected)
    result = {
        "course_id": args.course_id,
        "lesson_id": args.lesson_id,
        "lesson_artifact_key": lesson_ref.key,
        "lesson_artifact_sha256": lesson_ref.sha256,
        "model_id": model_id,
        "question_count": len(expected),
        "with_context_correct": ctx_correct,
        "with_context_accuracy": ctx_accuracy,
        "blind_correct": blind_correct,
        "blind_accuracy": blind_accuracy,
        "with_context_choices": list(with_context.choices),
        "blind_choices": list(blind.choices),
        "thresholds": {"with_context_min": 0.95, "blind_max": 0.30},
        "bounded_gate_pass": ctx_accuracy >= 0.95 and blind_accuracy <= 0.30,
        "provider_usage": gateway.usage_totals,
        "cost_usd": gateway.usage_totals.get("cost_usd", 0.0),
        "gold_eligible": False,
    }
    payload_bytes = json.dumps(result, sort_keys=True).encode()
    key = hashlib.sha256(
        f"{lesson_ref.sha256}:{model_id}:phase5-solver-eval-v5-10q".encode()
    ).hexdigest()
    ref = store.put(args.course_id, "phase5-live-solver-evaluation", key,
                    {"solver-evaluation.json": payload_bytes})
    readback = store.read(ref, "solver-evaluation.json")
    if readback != payload_bytes:
        raise RuntimeError("solver evaluation artifact readback mismatch")
    print(json.dumps({
        "status": "solver_eval_saved", "artifact_ref": ref.model_dump(mode="json"),
        "artifact_raw_sha256": hashlib.sha256(readback).hexdigest(), **result,
    }, sort_keys=True))


if __name__ == "__main__":
    main()

