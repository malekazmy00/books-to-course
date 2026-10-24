"""Audit a persisted Phase 5 course-QA artifact without printing its content."""
from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_CHECKS = (
    "exact_chunk_id",
    "exact_source_quote",
    "answer_supported",
    "question_clear_and_answerable",
    "relevant_to_scope",
    "deduplicated",
)


def _normalize_question(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in normalized).split())


def audit(payload: dict[str, Any], raw: bytes, expected_scopes: int | None,
          expected_items: int | None) -> dict[str, Any]:
    findings: list[str] = []
    scopes = payload.get("scopes")
    if not isinstance(scopes, list):
        scopes = []
        findings.append("scopes_missing_or_invalid")

    items = [item for scope in scopes if isinstance(scope, dict)
             for item in scope.get("items", []) if isinstance(item, dict)]
    lessons = [scope for scope in scopes if scope.get("kind") == "lesson"]
    lesson_questions = [item for scope in lessons for item in scope.get("items", [])
                        if isinstance(item, dict)]

    if payload.get("status") != "final":
        findings.append("course_status_not_final")
    if any(scope.get("status") != "final" or scope.get("errors") for scope in scopes):
        findings.append("scope_failed_or_has_errors")
    expected_lessons = set(payload.get("expected_lesson_ids", []))
    covered_lessons = set(payload.get("covered_lesson_ids", []))
    if expected_lessons != covered_lessons:
        findings.append("lesson_coverage_mismatch")
    if expected_scopes is not None and len(scopes) != expected_scopes:
        findings.append("scope_count_mismatch")
    if expected_items is not None and len(items) != expected_items:
        findings.append("item_count_mismatch")

    check_counts = Counter()
    for item in items:
        checks = item.get("automated_checks")
        if not isinstance(checks, dict):
            findings.append("automated_checks_missing")
            continue
        for check in REQUIRED_CHECKS:
            if checks.get(check) is True:
                check_counts[check] += 1
            else:
                findings.append(f"check_failed:{check}")
        if not item.get("question", "").strip() or not item.get("answer", "").strip():
            findings.append("question_or_answer_empty")
        if not item.get("evidence_chunk_ids") or not item.get("evidence_quote", "").strip():
            findings.append("evidence_missing")
        if not item.get("book_id") or item.get("role") not in {"spine", "supplementary"}:
            findings.append("source_identity_invalid")
        if not item.get("pages") or any(not isinstance(page, int) or page < 1 for page in item["pages"]):
            findings.append("page_reference_invalid")

    normalized_questions = [_normalize_question(item.get("question", ""))
                           for item in lesson_questions]
    duplicate_lesson_questions = len(normalized_questions) - len(set(normalized_questions))
    if duplicate_lesson_questions:
        findings.append("duplicate_questions_within_lesson_scopes")

    return {
        "result": "PASS" if not findings else "FAIL",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "course_status": payload.get("status"),
        "scope_count": len(scopes),
        "final_scope_count": sum(scope.get("status") == "final" for scope in scopes),
        "lesson_scope_count": len(lessons),
        "item_count": len(items),
        "lesson_item_count": len(lesson_questions),
        "lesson_duplicate_count": duplicate_lesson_questions,
        "check_pass_counts": dict(check_counts),
        "findings": sorted(set(findings)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="course-qa.json path")
    parser.add_argument("--expected-scopes", type=int)
    parser.add_argument("--expected-items", type=int)
    args = parser.parse_args()
    raw = args.artifact.read_bytes()
    payload = json.loads(raw)
    result = audit(payload, raw, args.expected_scopes, args.expected_items)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
