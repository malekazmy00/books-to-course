"""Run a bounded local-provider smoke against the frozen Phase 1 chapter fixture.

This is a manual operations helper; it makes no provider selection or network
configuration changes and writes only the generated diagnostic artifact.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from b2c_contracts import Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook, StructuredChunk
from b2c_llm_gateway import LLMGateway
from b2c_pedagogy.qa_generation import generate_course_qa, persist_course_qa


def main() -> None:
    root = Path("/data")
    source = root / "lane-primary-30-chunks.jsonl"
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    blocks, chunks = [], []
    for index, row in enumerate(rows):
        block_id = f"lane-block-{index + 1}"
        refs = [PageRef(book_id=row["book_id"], page_no=page) for page in row["pages"]]
        blocks.append(Block(block_id=block_id, type="paragraph", text=row["text"], refs=refs,
            section_path=row.get("section_path", []), ocr=False))
        chunks.append(StructuredChunk(chunk_id=row["chunk_id"], book_id=row["book_id"], role=row["role"],
            section_path=row.get("section_path", []), block_ids=[block_id], pages=row["pages"],
            text=row["text"], token_count=max(1, len(row["text"].split())), embedding_model="source-fixture"))
    page_min = min(page for row in rows for page in row["pages"])
    page_max = max(page for row in rows for page in row["pages"])
    book = ParsedBook(book_id="lane-intro-stats", title="Lane Introduction to Statistics chapter 5 test chunks",
        role="spine", page_count=page_max, toc_source="headings", blocks=blocks,
        parser={"name": "phase1-source-chunk-fixture", "version": "1"})
    outline = CourseOutline(course_id="phase3-qa-smoke", spine_book_id=book.book_id,
        supplementary_book_ids=[], requires_review=False, modules=[Module(module_id="probability-unit",
            title="Probability", order=1, lessons=[Lesson(lesson_id="probability-chapter",
                title="Probability concepts", order=1,
                spine_span=LessonSpan(start_block=blocks[0].block_id, end_block=blocks[-1].block_id,
                    pages=(page_min, page_max)), provenance="headings")])])
    start = time.perf_counter()
    gateway = LLMGateway("http://llm:8080/v1", "qwen3-coder-30b", timeout_s=2400, allow_remote=False)
    try:
        result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
            group_question_count=1, course_question_count=1, max_regeneration_rounds=1,
            max_input_chars=18000, max_tokens=1200)
    finally:
        gateway._http.close()
    result_path = root / "phase3-course-qa-smoke.json"
    persist_course_qa(result, result_path)
    summary = {"result_path": str(result_path), "elapsed_seconds": round(time.perf_counter() - start, 2),
        "model": "qwen3-coder-30b", "source": "Phase 1 Lane chapter 5 internal_test_only chunk file",
        "source_chunks": len(chunks), "status": result.status, "scope_count": len(result.scopes),
        "total_items": sum(len(scope.items) for scope in result.scopes),
        "scopes": [{"scope_id": scope.scope_id, "kind": scope.kind, "status": scope.status,
            "attempts": scope.attempts, "items": [item.model_dump(mode="json") for item in scope.items],
            "errors": scope.errors} for scope in result.scopes], "errors": result.errors}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
