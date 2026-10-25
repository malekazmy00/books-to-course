"""Length budgets for listening (EN words; Arabic is measured on the English script it translates).

An episode is heard, not read: a learner who cannot scroll back needs short, bounded sections
(learning-companion §4 «قيود ضد الإغراق», §12 «يُحدَّد حجم كل شكل مشتق بحسب الحاجة التعليمية»).
Provisional policy (2026-10-25), recorded in the ``episodes.length`` GateResult definition.

* recap: grows with the episode number, ``RECAP_WORDS_PER_EPISODE`` per previous episode, capped;
* per-line caps keep each spoken unit short enough to follow (and to translate as one unit).
"""
from __future__ import annotations

from collections.abc import Sequence

SECTION_WORDS: dict[str, int] = {
    "episode_intro": 190, "lesson_summary": 180, "key_terms": 300, "questions": 420, "others_say": 130,
    "debate": 130, "about_book": 150, "about_author": 130, "book_method": 150, "course_guide": 160,
    "acknowledgments": 60,
}
LINE_WORDS: dict[str, int] = {"text": 45, "question": 30, "answer": 50, "term": 45}
RECAP_WORDS_PER_EPISODE = 18
RECAP_MAX_WORDS = 90
RECAP_MAX_SENTENCES = 4
COVERS_MAX_WORDS = 60
SUMMARY_MAX_SENTENCES = 7
KEY_TERMS_MAX = 8
OTHERS_MAX = 3
QUESTION_CANDIDATES = 8


def words(text: str) -> int:
    return len(text.split())


def recap_budget(previous_episodes: int) -> tuple[int, int]:
    """(max words, max sentences) of the recap for an episode with this many previous episodes."""
    if previous_episodes <= 0:
        return 0, 0
    return (min(RECAP_MAX_WORDS, 25 + RECAP_WORDS_PER_EPISODE * (previous_episodes - 1)),
            min(RECAP_MAX_SENTENCES, 1 + previous_episodes // 2))


def take_within(texts: Sequence[str], max_words: int, max_items: int) -> int:
    """How many leading items fit in the budget (each item whole)."""
    total = n = 0
    for text in texts[:max_items]:
        if total + words(text) > max_words:
            break
        total += words(text)
        n += 1
    return n


def definition() -> dict[str, object]:
    return {"section_words": SECTION_WORDS, "line_words": LINE_WORDS, "recap_words_per_episode": RECAP_WORDS_PER_EPISODE,
            "recap_max_words": RECAP_MAX_WORDS, "summary_max_sentences": SUMMARY_MAX_SENTENCES,
            "key_terms_max": KEY_TERMS_MAX, "unit": "English words"}


__all__ = ["COVERS_MAX_WORDS", "KEY_TERMS_MAX", "LINE_WORDS", "OTHERS_MAX", "QUESTION_CANDIDATES", "RECAP_MAX_SENTENCES",
           "RECAP_MAX_WORDS", "SECTION_WORDS", "SUMMARY_MAX_SENTENCES", "definition", "recap_budget", "take_within",
           "words"]
