# Automatic processing plan — 2026-10-08

Scope: B2C only. Pure planning implementation; no provider calls, image build,
worker changes, service restart or Android changes were performed.

Implemented `b2c_core.processing_plan.build_processing_plan` and
`plan_parsed_book`. Models use Pydantic and expose `model_dump()` for the API.

Decisions:
- A usable native text layer routes through extraction without image OCR.
  Verified scan pages are identified individually; a mixed document reports both
  routes. Completed OCR is reusable while its original scan status is retained.
- Missing/unknown pages are not silently labelled blank. Empty input needs either
  a verified imported artifact or source input.
- Arabic source to Arabic audio skips translation. English to Arabic requires it.
  Mixed/insufficient/unsupported language evidence needs user confirmation. Latin
  characters alone never establish English. Explicit source language means user
  confirmation or verified metadata. Confidence is an evidence heuristic, not a
  calibrated probability.
- Dependencies are computed from output and selected content (`book`, `course`,
  `questions`). Direct book reading requires extraction and audio; course audio
  can also include generated lesson questions. RAG output requires extraction;
  question output requires course/RAG/outline inputs.
- `available_stages` MUST come from caller-verified artifacts matching source,
  language, config and model policy. A stage name supplied by a client is not
  sufficient. Available target artifacts can be exported without recomputing
  their predecessors. Reused and skipped stages are separately reported.
- Translation/audio require a chosen output language. Unknown language decisions
  are marked `await_user` rather than guessed or executed.

Validation: `.venv/bin/pytest -q tests/unit/core/test_processing_plan.py`
passed **17 tests**; checks cover Arabic/English/mixed-language routing,
per-page mixed PDF handling, completed scan provenance, verified RAG reuse,
missing source, unknown pages, duplicate page evidence, course output without
forced questions, and translation choices without audio scheduling.

Integration handoff: call `plan_parsed_book(parsed_book, target_output,
target_language, source_language=..., available_stages=..., content_kind=...,
include_questions=...)` after loading validated artifacts. For raw PDF inspection,
call `build_processing_plan(pages=[PageEvidence(...)], ...)` with extractor/image
inspection evidence. Source PDFs should be inspected through the ingestion
adapters; this pure planner performs no PDF IO. API/app execution and raw-upload
inspection integration are outside this isolated agent's changes.
