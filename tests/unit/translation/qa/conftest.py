"""Shared factories for the translation QA tests (D-028). All text here is synthetic."""
from __future__ import annotations

import pytest

from b2c_contracts import TranslationUnit
from b2c_translation.qa.types import CheckContext


def _unit(source: str, *, kind: str = "prose", markers: list[str] | None = None,
          unit_id: str = "bk:u00001") -> TranslationUnit:
    return TranslationUnit(unit_id=unit_id, book_id="bk", block_ids=["b1"], kind=kind,
                           source_text=source, footnote_markers=markers or [], pages=[1],
                           section_path=["Chapter 1"])


@pytest.fixture
def make_unit():
    return _unit


@pytest.fixture
def make_ctx():
    def factory(source: str, target: str, *, kind: str = "prose", markers: list[str] | None = None,
                terms: dict[str, str] | None = None, entities: dict[str, str] | None = None,
                fingerprints: frozenset[str] = frozenset()) -> CheckContext:
        return CheckContext(unit=_unit(source, kind=kind, markers=markers), target=target,
                            terms=terms or {}, entities=entities or {},
                            leak_fingerprints=fingerprints)
    return factory
