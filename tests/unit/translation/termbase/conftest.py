"""Synthetic book for the termbase tests (invented names and techniques, no real book content)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from b2c_contracts import TranslationUnit
from b2c_translation.engines.base import TextCompletion

BOOK_ID = "synthetic-tb"

# (section, kind, text)
_UNITS: list[tuple[str, str, str]] = [
    ("1 Foundations", "heading", "1.2 Signal Mapping"),
    ("1 Foundations", "prose",
     "Signal Mapping is a technique that arranges weak signals on a shared grid before any estimate is made."),
    ("1 Foundations", "prose",
     "Analysts who skip Signal Mapping often fall into confirmation drift when the evidence is thin."),
    ("1 Foundations", "prose",
     "The Analysis of Rival Hypotheses (ARH) was first described by Dr. Lena Vasquez in 1994."),
    ("1 Foundations", "prose",
     "Vasquez argued that confirmation drift grows when teams meet at the Northwind Institute for Policy Research."),
    ("2 Practice", "heading", "2.1 The Pre-Mortem Review"),
    ("2 Practice", "prose",
     "A Pre-Mortem Review is a method in which the team imagines that its plan has already failed."),
    ("2 Practice", "prose",
     "Marcus T. Hollander Jr. used the Pre-Mortem Review and ARH with students at Vantor University."),
    ("2 Practice", "prose",
     "Hollander wrote that confirmation drift is the main enemy of Signal Mapping in Portugal and in Kelburn Square."),
    ("2 Practice", "list_item", "Run ARH again after each new report of confirmation drift."),
    ("3 Cases", "prose",
     "In the novel 'The Quiet Ledger' the clerks practise what Vasquez called 'slow counting'."),
    ("3 Cases", "prose",
     "Vasquez's students at Vantor University applied ARH to The Quiet Ledger and found confirmation drift again."),
    ("3 Cases", "prose",
     "the clerks met in Kelburn Square, where Hollander had once lived, to practise Signal Mapping."),
    ("3 Cases", "reference",
     "Vasquez, L. 1994. Rival Hypotheses Revisited. Northwind Press."),
    ("Index", "index", "Vasquez, Lena, 3, 12, 40"),
    ("Index", "index", "confirmation drift, 4, 9-11"),
    ("Index", "index", "ledgerism, 22"),
    ("3 Cases", "prose", "Some readers dismiss ledgerism as a fashion, but ledgerism shaped the clerks."),
]


def make_units(rows=_UNITS, book_id: str = BOOK_ID) -> list[TranslationUnit]:
    units = []
    for seq, (section, kind, text) in enumerate(rows):
        units.append(TranslationUnit(
            unit_id=f"{book_id}:u{seq:05d}", book_id=book_id, block_ids=[f"b{seq}"], kind=kind,
            source_text=text, pages=[seq + 1], section_path=[section]))
    return units


@pytest.fixture
def units() -> list[TranslationUnit]:
    return make_units()


@dataclass
class FakeGateway:
    replies: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def complete_text(self, messages, *, prompt_version, max_tokens, temperature=0.0):
        self.calls.append({"messages": [dict(m) for m in messages], "prompt_version": prompt_version,
                           "max_tokens": max_tokens, "temperature": temperature})
        text = self.replies.pop(0) if self.replies else "NONE"
        return TextCompletion(text=text, model="local-model", finish_reason="stop")


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()
