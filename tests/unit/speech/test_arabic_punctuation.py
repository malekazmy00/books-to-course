import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "docker" / "supertonic"))
from arabic_punctuation import ensure_short_terminal_mark


def test_short_arabic_text_gets_terminal_period():
    text = "\u0642\u0627\u0644 \u0627\u0644\u0645\u062d\u0644\u0644 \u0627\u0646 \u0627\u0644\u0642\u0631\u0627\u0631 \u0645\u0647\u0645 \u062c\u062f\u0627"
    result = ensure_short_terminal_mark(text)
    assert result == text + "."


def test_existing_terminal_mark_and_trailing_space_are_preserved():
    text = "\u0645\u0631\u062d\u0628\u0627\u064b.  \n"
    assert ensure_short_terminal_mark(text) == text


def test_long_or_non_arabic_text_is_unchanged():
    assert ensure_short_terminal_mark("A" * 241) == "A" * 241
    assert ensure_short_terminal_mark("English sentence") == "English sentence"


def test_fallback_does_not_change_words_and_is_idempotent():
    text = "\u064a\u0631\u0649 \u0627\u0644\u0645\u062d\u0644\u0644 \u0627\u0644\u0642\u0631\u0627\u0631"
    result = ensure_short_terminal_mark(text)
    assert result.endswith(".")
    assert result[:-1].split() == text.split()
    assert ensure_short_terminal_mark(result) == result
