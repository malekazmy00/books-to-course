from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from b2c_contracts import TranslationUnit
from b2c_translation.engines.base import TextCompletion


@dataclass
class FakeGateway:
    """Records complete_text calls; replays queued replies (text, finish_reason)."""
    replies: list[tuple[str, str]] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def complete_text(self, messages, *, prompt_version, max_tokens, temperature=0.0):
        self.calls.append({"messages": [dict(m) for m in messages], "prompt_version": prompt_version,
                           "max_tokens": max_tokens, "temperature": temperature})
        text, finish = self.replies.pop(0) if self.replies else ("ترجمة", "stop")
        return TextCompletion(text=text, model="served-model", finish_reason=finish,
                              usage={"input_tokens": 10, "output_tokens": 5})


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def make_unit():
    def _make(text: str, kind: str = "prose", **kw) -> TranslationUnit:
        return TranslationUnit(unit_id="book:u00001", book_id="book", block_ids=["b1"], kind=kind,
                               source_text=text, pages=[1], section_path=["Ch 1"], **kw)
    return _make
