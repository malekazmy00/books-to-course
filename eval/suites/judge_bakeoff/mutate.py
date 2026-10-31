"""Planted translation errors for measuring a QE judge without human labels (charter §3.5).

Each mutation turns a translation judged correct into one with a known error of a known MQM
category. A mutation is deterministic for a given (text, seed) and returns None when it does not
apply (e.g. no digits for ``number``). A judge "detects" a mutation when it reports at least one
major or critical error on the mutated target.
"""
from __future__ import annotations

import random
import re

# Antonym pairs common in expository Arabic; swapping one reverses the meaning.
ANTONYMS = [
    ("أكثر", "أقل"), ("يزيد", "ينقص"), ("زيادة", "نقص"), ("قبل", "بعد"), ("صحيح", "خاطئ"),
    ("ممكن", "مستحيل"), ("دائما", "أبدا"), ("دائمًا", "أبدًا"), ("كبير", "صغير"), ("أعلى", "أدنى"),
    ("يقبل", "يرفض"), ("نجاح", "فشل"), ("قوي", "ضعيف"), ("الأول", "الأخير"), ("داخل", "خارج"),
    ("يسمح", "يمنع"), ("مع", "ضد"), ("سهل", "صعب"), ("إيجابي", "سلبي"), ("جميع", "بعض"),
]
_DIGITS = re.compile(r"[0-9٠-٩]+")
_SENTENCE = re.compile(r"(?<=[.!؟?؛])\s+")
_WORD = re.compile(r"\S+")
_LATIN = re.compile(r"[A-Za-z]{3,}(?:\s+[A-Za-z]{3,}){2,}")

MUTATIONS = ("number", "antonym", "omission", "addition", "untranslated")


def _bump_digits(token: str) -> str:
    def bump(ch: str) -> str:
        if "0" <= ch <= "9":
            return str((int(ch) + 3) % 10)
        if "٠" <= ch <= "٩":
            return chr(ord("٠") + (ord(ch) - ord("٠") + 3) % 10)
        return ch
    return "".join(bump(c) for c in token)


def number(target: str, rng: random.Random, **_: str) -> str | None:
    found = list(_DIGITS.finditer(target))
    if not found:
        return None
    m = rng.choice(found)
    return target[:m.start()] + _bump_digits(m.group()) + target[m.end():]


def antonym(target: str, rng: random.Random, **_: str) -> str | None:
    options = []
    for a, b in ANTONYMS:
        for word, other in ((a, b), (b, a)):
            for m in re.finditer(rf"(?<![\w]){re.escape(word)}(?![\w])", target):
                options.append((m.start(), m.end(), other))
    if not options:
        return None
    start, end, other = rng.choice(sorted(options))
    return target[:start] + other + target[end:]


def omission(target: str, rng: random.Random, **_: str) -> str | None:
    """Drop one sentence, or the second half of a single long sentence (>= 25% of the words)."""
    sentences = [s for s in _SENTENCE.split(target.strip()) if s]
    if len(sentences) >= 2:
        drop = rng.randrange(len(sentences))
        return " ".join(s for i, s in enumerate(sentences) if i != drop)
    words = _WORD.findall(target)
    if len(words) < 12:
        return None
    return " ".join(words[: len(words) // 2]) + "."


def addition(target: str, rng: random.Random, *, donor: str = "", **_: str) -> str | None:
    """Append a sentence taken from an unrelated translation (invented content)."""
    donor_sentences = [s for s in _SENTENCE.split(donor.strip()) if len(_WORD.findall(s)) >= 5]
    if not donor_sentences:
        return None
    return target.rstrip() + " " + rng.choice(donor_sentences)


def untranslated(target: str, rng: random.Random, *, source: str = "", **_: str) -> str | None:
    """Replace the last third of the target with the matching English words of the source."""
    words = _WORD.findall(target)
    src_words = _WORD.findall(source)
    if len(words) < 9 or len(src_words) < 9 or not _LATIN.search(source):
        return None
    keep = len(words) * 2 // 3
    tail = " ".join(src_words[len(src_words) * 2 // 3:])
    return " ".join(words[:keep]) + " " + tail


FUNCTIONS = {"number": number, "antonym": antonym, "omission": omission,
             "addition": addition, "untranslated": untranslated}


def mutate(kind: str, target: str, *, seed: int, source: str = "", donor: str = "") -> str | None:
    rng = random.Random(f"{seed}:{kind}:{target}")
    out = FUNCTIONS[kind](target, rng, source=source, donor=donor)
    return None if out is None or out.strip() == target.strip() else out
