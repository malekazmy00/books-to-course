"""DEFAULT_RETRY and docs/02 §1.3 must describe the same policy."""
from __future__ import annotations

import re
from pathlib import Path

from b2c_llm_gateway import FreeTokenQuotaExceeded, InvalidLLMOutput
from b2c_orchestration.retry import DEFAULT_RETRY

DOC = Path(__file__).resolve().parents[3] / "docs" / "02-CONTRACTS.md"


def _section_1_3() -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("### 1.3")
    return text[start:text.index("### 1.4", start)]


def test_deterministic_gateway_failures_are_not_retried():
    # A lifetime token cap does not refill, and InvalidLLMOutput is raised only
    # after the gateway's own schema-repair attempts at temperature 0.
    assert FreeTokenQuotaExceeded.__name__ in DEFAULT_RETRY.non_retryable_error_types
    assert InvalidLLMOutput.__name__ in DEFAULT_RETRY.non_retryable_error_types


def test_docs_retry_table_matches_retry_module():
    section = _section_1_3()
    row = next(line for line in section.splitlines() if line.startswith("| non_retryable"))
    documented = set(re.findall(r"`([A-Za-z]+)`", row))
    assert documented == set(DEFAULT_RETRY.non_retryable_error_types)
    assert f"| maximum_attempts | {DEFAULT_RETRY.maximum_attempts} |" in section
    assert f"| backoff_coefficient | {DEFAULT_RETRY.backoff_coefficient} |" in section
