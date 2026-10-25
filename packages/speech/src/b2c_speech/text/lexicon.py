"""Pronunciation lexicon for Latin words left in Arabic text (R7; audit 07 C2, H1).

Sources, in priority order: explicit ``overrides`` (a reviewed speech lexicon), the book's
approved entity table, then its approved locked terms (``BookTermbase.entity_table()`` /
``locked_terms()``; the termbase is built and approved by S4, Gate G3). An entry whose Arabic
form still contains Latin letters (a do-not-translate term) is not usable for speech and is left
out, so the word falls to the fallback below.

Fallback for a Latin word that is not in the lexicon (documented, counted, never silent):
- an acronym (2-6 capitals, optionally dotted: ``FBI``, ``U.S.``) is spelled with Arabic letter
  names (``إف بي آي``);
- any other word is transliterated by the grapheme rules in ``transliterate_latin`` and recorded
  as a lexicon miss; the speech-text gate fails while misses remain (plan R7: 0 Latin words
  outside the lexicon), so a reviewer adds them to the lexicon instead.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field

_LATIN = re.compile(r"[A-Za-z]")
_WS = re.compile(r"\s+")

LETTER_NAMES = {
    "A": "إيه", "B": "بي", "C": "سي", "D": "دي", "E": "إي", "F": "إف", "G": "جي", "H": "إتش", "I": "آي",
    "J": "جيه", "K": "كيه", "L": "إل", "M": "إم", "N": "إن", "O": "أو", "P": "بي", "Q": "كيو", "R": "آر",
    "S": "إس", "T": "تي", "U": "يو", "V": "في", "W": "دبليو", "X": "إكس", "Y": "واي", "Z": "زد",
}

# Grapheme rules for the transliteration fallback (longest first). Deliberately simple: the
# output is understandable, not correct; every use is reported as a lexicon miss.
_DIGRAPHS = [("tion", "شن"), ("sch", "ش"), ("sh", "ش"), ("ch", "تش"), ("th", "ث"), ("ph", "ف"), ("gh", "غ"),
             ("kh", "خ"), ("ck", "ك"), ("qu", "كو"), ("oo", "و"), ("ou", "و"), ("ee", "ي"), ("ea", "ي"),
             ("ie", "ي"), ("ai", "اي"), ("ay", "اي"), ("ei", "اي"), ("au", "او"), ("eu", "و")]
_SINGLE = {"a": "ا", "b": "ب", "c": "ك", "d": "د", "e": "ي", "f": "ف", "g": "غ", "h": "ه", "i": "ي", "j": "ج",
           "k": "ك", "l": "ل", "m": "م", "n": "ن", "o": "و", "p": "ب", "q": "ك", "r": "ر", "s": "س", "t": "ت",
           "u": "و", "v": "ف", "w": "و", "x": "كس", "y": "ي", "z": "ز"}


def _key(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def spell_acronym(token: str) -> str:
    return " ".join(LETTER_NAMES[ch] for ch in token.upper() if ch in LETTER_NAMES)


def transliterate_latin(word: str) -> str:
    """Rough Latin -> Arabic letters (fallback only)."""
    text = word.casefold()
    out: list[str] = []
    i = 0
    while i < len(text):
        for src, dst in _DIGRAPHS:
            if text.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            ch = text[i]
            if ch in _SINGLE:
                mapped = _SINGLE[ch]
                if i == 0 and ch in "aeiou":
                    mapped = {"a": "أ", "e": "إ", "i": "إ", "o": "أو", "u": "أو"}[ch]
                elif i == len(text) - 1 and ch == "e" and len(text) > 2:
                    mapped = ""  # silent final e
                out.append(mapped)
            i += 1
    result = "".join(out)
    return re.sub(r"(.)\1+", r"\1", result) or word


@dataclass(frozen=True)
class PronunciationLexicon:
    """source form (case-insensitive) -> spoken Arabic form, with the origin of each entry."""
    entries: tuple[tuple[str, str, str], ...] = ()  # (source key, spoken, origin), sorted
    _pattern: re.Pattern[str] | None = field(default=None, compare=False, repr=False)

    @classmethod
    def from_mappings(cls, *, terms: Mapping[str, str] | None = None, entities: Mapping[str, str] | None = None,
                      overrides: Mapping[str, str] | None = None) -> PronunciationLexicon:
        merged: dict[str, tuple[str, str]] = {}
        for origin, mapping in (("term", terms), ("entity", entities), ("override", overrides)):
            for source, spoken in (mapping or {}).items():
                key, value = _key(source), _WS.sub(" ", unicodedata.normalize("NFC", spoken)).strip()
                if not key or not value or not _LATIN.search(key) or _LATIN.search(value):
                    continue  # nothing to pronounce, or not speakable as Arabic
                merged[key] = (value, origin)  # later sources win: override > entity > term
        entries = tuple(sorted((k, v, o) for k, (v, o) in merged.items()))
        return cls(entries=entries)

    @property
    def digest(self) -> str:
        raw = json.dumps([list(e) for e in self.entries], ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def __len__(self) -> int:
        return len(self.entries)

    def pattern(self) -> re.Pattern[str] | None:
        if not self.entries:
            return None
        if self._pattern is None:
            sources = sorted((e[0] for e in self.entries), key=len, reverse=True)
            body = "|".join(re.escape(s).replace(r"\ ", r"\s+") for s in sources)
            object.__setattr__(self, "_pattern",
                               re.compile(rf"(?<![A-Za-z])(?:{body})(?![A-Za-z])", re.IGNORECASE))
        return self._pattern

    def lookup(self, surface: str) -> str | None:
        key = _key(surface)
        for source, spoken, _ in self.entries:
            if source == key:
                return spoken
        return None


__all__ = ["LETTER_NAMES", "PronunciationLexicon", "spell_acronym", "transliterate_latin"]
