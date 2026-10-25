"""Sentence segmentation into bounded TTS chunks (R7; audit 07 C4, M1).

Every chunk is at most ``max_chars`` long and the function always terminates: each step
consumes at least one character (sentence -> clause -> word -> hard cut).
"""
from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+")
_CLAUSE_END = re.compile(r"(?<=[،,;؛:])\s+")


def _bounded(piece: str, max_chars: int) -> list[str]:
    if len(piece) <= max_chars:
        return [piece]
    clauses = [c for c in _CLAUSE_END.split(piece) if c]
    if len(clauses) > 1:
        return [part for clause in clauses for part in _bounded(clause, max_chars)]
    words = piece.split()
    if len(words) > 1:
        out, current = [], ""
        for word in words:
            for part in _bounded(word, max_chars):
                if not current:
                    current = part
                elif len(current) + 1 + len(part) <= max_chars:
                    current += " " + part
                else:
                    out.append(current)
                    current = part
        if current:
            out.append(current)
        return out
    return [piece[i:i + max_chars] for i in range(0, len(piece), max_chars)]  # one unbroken token


def split_for_tts(text: str, *, target_chars: int = 200, max_chars: int = 300) -> list[str]:
    """Sentences, merged greedily up to ``target_chars`` and never above ``max_chars``."""
    if not 0 < target_chars <= max_chars:
        raise ValueError("need 0 < target_chars <= max_chars")
    pieces = [p for sentence in _SENTENCE_END.split(text.strip()) if sentence.strip()
              for p in _bounded(sentence.strip(), max_chars)]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) <= target_chars:
            current += " " + piece
            continue
        if current:
            chunks.append(current)
        current = piece
    if current:
        chunks.append(current)
    return chunks


# Words that open a clause in Arabic expository prose: a phrase break before them falls on a
# syntactic boundary (relative clause, coordination, contrast, cause, time, circumstance).
CLAUSE_WORDS = frozenset({
    "ثم", "أو", "بل", "لكن", "لكنّ", "ولكن", "بينما", "حيث", "إذ", "إذا", "كما", "التي", "الذي", "الذين",
    "اللذين", "اللتين", "اللواتي", "اللاتي", "مما", "ممن", "لأن", "لأنه", "لأنها", "حتى", "عندما", "حين",
    "حينما", "إلا", "أي", "وهو", "وهي", "وهم", "وكان", "وكانت", "والتي", "والذي", "والذين", "وإن", "فإن",
    "وإذا", "وبينما", "وحيث", "كي", "لكي", "بحيث", "مثل", "سواء", "خاصة", "بما", "وذلك", "ذلك",
})
# A phrase must not end on a word that announces what follows (preposition, particle,
# demonstrative, quantifier): "في | وكالة" breaks a unit the listener cannot parse.
NO_PHRASE_END = frozenset({
    "في", "من", "إلى", "الى", "على", "عن", "مع", "بين", "حول", "عند", "لدى", "نحو", "دون", "ضد", "خلال", "منذ",
    "قبل", "بعد", "عبر", "تحت", "فوق", "أمام", "وراء", "ضمن", "لدى", "أن", "إن", "أنّ", "إنّ", "لا", "لم", "لن",
    "قد", "ما", "هذا", "هذه", "ذلك", "تلك", "هؤلاء", "كل", "بعض", "أي", "غير", "مثل", "و", "ثم", "أو", "يا", "كان",
    "كانت", "ليس", "أكثر", "أقل", "أحد", "إحدى", "عام", "سنة", "القرن", "الفصل",
})
_NUMBER_WORDS = frozenset({
    "واحد", "واحدة", "اثنان", "اثنين", "اثنا", "اثني", "اثنتان", "اثنتين", "ثلاثة", "ثلاث", "أربعة", "أربع",
    "خمسة", "خمس", "ستة", "ست", "سبعة", "سبع", "ثمانية", "ثماني", "تسعة", "تسع", "عشرة", "عشر", "أحد", "إحدى",
    "عشرون", "عشرين", "ثلاثون", "ثلاثين", "أربعون", "أربعين", "خمسون", "خمسين", "ستون", "ستين", "سبعون", "سبعين",
    "ثمانون", "ثمانين", "تسعون", "تسعين", "مئة", "مائة", "مئتان", "مئتين", "ألف", "ألفان", "ألفين", "آلاف", "ألفًا",
    "مليون", "مليونان", "مليونين", "ملايين", "مليار", "مليارات", "فاصلة", "صفر", "بالمئة",
})
_PUNCT_BREAK = "،؛:"


def _is_number_word(word: str) -> bool:
    w = word.rstrip("،؛:.!؟")
    return w in _NUMBER_WORDS or (w.startswith("و") and w[1:] in _NUMBER_WORDS) or w.endswith("مئة")


def _strength(prev: str, nxt: str) -> float:
    """How good a phrase break between ``prev`` and ``nxt`` is (higher is better)."""
    if prev[-1] in _PUNCT_BREAK:
        return 3
    if prev in NO_PHRASE_END or (_is_number_word(prev) and _is_number_word(nxt)):
        return 0
    if _is_number_word(prev):
        return 0.5  # a numeral and its counted noun: ثمانية وعشرين | عامًا
    if nxt in CLAUSE_WORDS:
        return 2
    if nxt.startswith("وال") or nxt.startswith("و") and nxt[1:2] in "أإ":
        return 1.5  # coordination of a definite noun / a new verb phrase: والتحقق، وأدار
    return 1
_SENTENCE = re.compile(r"(?<=[.!؟])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def split_phrases(text: str, *, max_chars: int, max_words: int, min_chars: int = 1) -> list[str]:
    """Split one sentence into speakable phrases of at most ``max_chars`` / ``max_words``.

    Recursive and balanced: cut at the strongest boundary available (punctuation, then a clause
    word, then a coordination, then any space; never after a preposition/particle or inside a
    number) whose two sides are both at least a quarter long, the one closest to
    the middle, then recurse. Every step makes both sides shorter, so it always terminates;
    ``" ".join(result)`` gives the input back (single-spaced input).
    """
    if max_chars < 1 or max_words < 1:
        raise ValueError("phrase limits must be positive")
    text = text.strip()
    words = text.split()
    if len(text) <= max_chars and len(words) <= max_words:
        return [text] if text else []
    if len(words) == 1:
        return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
    lengths = [len(w) for w in words]
    total = sum(lengths) + len(words) - 1
    floor = min(total // 4, max_chars // 4, max(min_chars, 1) * 2)
    candidates = []
    left = -1
    for i in range(1, len(words)):
        left += lengths[i - 1] + 1
        right = total - left - 1
        candidates.append((i, left, right, _strength(words[i - 1], words[i])))
    pool = [c for c in candidates if c[1] >= floor and c[2] >= floor] or candidates
    best = max(c[3] for c in pool)
    i = min((c for c in pool if c[3] == best), key=lambda c: (abs(c[1] - c[2]), c[0]))[0]
    head, tail = " ".join(words[:i]), " ".join(words[i:])
    return (split_phrases(head, max_chars=max_chars, max_words=max_words, min_chars=min_chars)
            + split_phrases(tail, max_chars=max_chars, max_words=max_words, min_chars=min_chars))


__all__ = ["CLAUSE_WORDS", "split_for_tts", "split_phrases", "split_sentences"]
