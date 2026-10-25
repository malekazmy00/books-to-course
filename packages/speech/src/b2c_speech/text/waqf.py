"""Pausal forms (al-waqf) for DIACRITIZED Arabic text.

At a pause MSA drops the final short vowel and nunation (sukun): الكِتَابُ -> الكِتَابْ; tāʾ marbūṭa
is read as h: مَدْرَسَةٌ -> مَدْرَسَهْ; nunation with fatḥa on a seat alif becomes a long ā: كِتَابًا ->
كِتَابَا (pausa; McCarthy, "Pausal phonology and morpheme realization"). A narrator stops this way,
and the pause form also removes the case ending, the least reliable part of automatic
diacritization (CATT, arXiv 2407.03236: WER 20.06% with case endings vs 11.31% without, WikiNews).

Applied only where the plan pauses (before ``. ، ؛ : ! ؟`` and at the end of a segment) and
only to words that carry tashkeel: an undiacritized word is returned unchanged, because the
engine's diacritizer would overwrite any partial marking (docker/fasih: CATT strips tashkeel).
"""
from __future__ import annotations

import re

FATHATAN, DAMMATAN, KASRATAN = "ً", "ٌ", "ٍ"
FATHA, DAMMA, KASRA, SHADDA, SUKUN = "َ", "ُ", "ِ", "ّ", "ْ"
MARKS = set("ًٌٍَُِّْٰ")
VOWELS = {FATHATAN, DAMMATAN, KASRATAN, FATHA, DAMMA, KASRA}
_PAUSE_WORD = re.compile(r"[ء-غف-ْٰٱ]+(?=\s*(?:[.،؛:!؟]|$))")


def pausal_form(word: str) -> str:
    if not any(ch in MARKS for ch in word):
        return word  # undiacritized: leave it to the engine
    end = len(word)
    while end > 0 and word[end - 1] in MARKS:
        end -= 1
    if end == 0:
        return word
    stem, last, marks = word[:end - 1], word[end - 1], word[end:]
    if last in "اى" and not marks and stem.endswith(FATHATAN):
        return stem[:-1] + FATHA + last  # كِتَابًا -> كِتَابَا
    if last in "اى" and FATHATAN in marks:
        return stem + FATHA + last  # the other spelling: كتاباً
    if not any(m in VOWELS for m in marks):
        return word  # already pausal (sukun) or a bare long vowel: فِي
    if last == "ة":
        return stem + "ه" + SUKUN
    return stem + last + (SHADDA if SHADDA in marks else "") + SUKUN


def apply_waqf(text: str) -> str:
    """Pausal form on every word that stands before a pause."""
    return _PAUSE_WORD.sub(lambda m: pausal_form(m.group(0)), text)


__all__ = ["apply_waqf", "pausal_form"]
