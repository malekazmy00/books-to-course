from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_AUDIT_PATH = Path(__file__).resolve().parents[3] / "eval/suites/phase5/audit_qa_artifact.py"
_SPEC = spec_from_file_location("phase5_artifact_audit", _AUDIT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_AUDIT = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AUDIT)
REQUIRED_CHECKS = _AUDIT.REQUIRED_CHECKS
audit = _AUDIT.audit


def _artifact() -> dict:
    checks = {name: True for name in REQUIRED_CHECKS}
    item = {
        "question": "What is the main concept?",
        "answer": "The supported concept.",
        "evidence_chunk_ids": ["chunk-1"],
        "evidence_quote": "A source sentence supporting this answer.",
        "book_id": "book-1",
        "role": "spine",
        "pages": [1],
        "automated_checks": checks,
    }
    return {
        "status": "final",
        "expected_lesson_ids": ["lesson-1"],
        "covered_lesson_ids": ["lesson-1"],
        "scopes": [{"kind": "lesson", "status": "final", "errors": [], "items": [item]}],
    }


def test_phase5_artifact_audit_accepts_complete_grounded_lesson_item() -> None:
    payload = _artifact()
    raw = json.dumps(payload).encode()

    result = audit(payload, raw, expected_scopes=1, expected_items=1)

    assert result["result"] == "PASS"
    assert result["lesson_item_count"] == 1
    assert not result["findings"]


def test_phase5_artifact_audit_rejects_missing_check_and_wrong_coverage() -> None:
    payload = _artifact()
    payload["covered_lesson_ids"] = []
    payload["scopes"][0]["items"][0]["automated_checks"]["answer_supported"] = False

    result = audit(payload, json.dumps(payload).encode(), expected_scopes=2, expected_items=1)

    assert result["result"] == "FAIL"
    assert "lesson_coverage_mismatch" in result["findings"]
    assert "check_failed:answer_supported" in result["findings"]
    assert "scope_count_mismatch" in result["findings"]
