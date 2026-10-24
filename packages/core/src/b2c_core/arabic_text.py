"""Shared Arabic text normalisation for matching, token estimates and lexical search.

Never apply this to text that is stored or shown: it removes diacritics and
merges alef variants, which is only appropriate for comparison keys.
"""
from __future__ import annotations

import re
import unicodedata

# Harakat, tanween, shadda, sukun, Quranic marks and hamza/madda combining
# marks (U+064B-U+065F), superscript alef (U+0670) and tatweel (U+0640).
ARABIC_MARKS_RE = re.compile("[ً-ٰٟـ]")

_ALEF_VARIANTS = str.maketrans({
    "آ": "ا",  # alef with madda above
    "أ": "ا",  # alef with hamza above
    "إ": "ا",  # alef with hamza below
    "ٱ": "ا",  # alef wasla
})


def strip_arabic_marks(text: str) -> str:
    """Remove Arabic diacritics, superscript alef and tatweel only."""
    return ARABIC_MARKS_RE.sub("", text)


def normalize_arabic(text: str) -> str:
    """NFKC, strip diacritics/superscript alef/tatweel, and unify alef forms.

    Non-Arabic text is only NFKC-normalised. Case is preserved; callers that
    need case-insensitive keys should casefold the result.
    """
    return strip_arabic_marks(unicodedata.normalize("NFKC", text)).translate(_ALEF_VARIANTS)


__all__ = ["ARABIC_MARKS_RE", "normalize_arabic", "strip_arabic_marks"]
