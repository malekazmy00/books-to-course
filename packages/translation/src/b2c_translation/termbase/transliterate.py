"""Rule-based English->Arabic transliteration of person names (proposals only).

Convention (documented; every output is a proposal with ``approved_by=None`` until a
reviewer approves it):

* Marefa style guide, "تعريب الأسماء الأجنبية" (https://www.marefa.org/MOS:FOREIGN,
  checked 2026-10-24): P -> ب, Ch -> تش, V -> ف; foreign names take a hamza-carrying
  alif at the start (إدوارد); avoid surplus vowel letters.
* Hard G -> غ (``hard_g``), the form used in the project's audited corrected translations
  (e.g. غرامشي, TRANSLATION-ERROR-CATALOGUE.md 2026-10-24). Marefa prefers ج (Egyptian
  usage); pass ``hard_g="ج"`` to follow it. Soft g (before e/i/y) -> ج.
* Long vowel letters for a/i/o/u (ا/ي/و/و); medial e -> ي; silent final e (and the e of
  a final -es) dropped; final -er -> ر (Heuer -> هيور); final s after a voiced consonant -> ز.
* Initials keep the Arabic letter of their first sound plus a period (J. -> ج., H. -> هـ.);
  Jr./Sr. -> جونيور/الأب.

English orthography is not phonemic, so names of other origins (Italian sci, French
silent letters, ...) need the reviewer: this is a deterministic first draft, not a
claim of the established Arabic form.
"""
from __future__ import annotations

import re

TRANSLITERATION_RULES_VERSION = "translit-en-ar-v1"

_DIGRAPHS: tuple[tuple[str, str], ...] = (
    ("tch", "تش"), ("sch", "ش"), ("sh", "ش"), ("ch", "تش"), ("th", "ث"), ("ph", "ف"),
    ("ck", "ك"), ("qu", "كو"), ("kh", "خ"), ("dj", "ج"),
    ("ee", "ي"), ("ea", "ي"), ("oo", "و"), ("ou", "و"), ("eu", "يو"), ("au", "و"),
    ("ai", "اي"), ("ay", "اي"), ("ei", "اي"), ("ie", "ي"), ("aw", "او"), ("ow", "او"), ("oa", "و"),
)
_SINGLE = {
    "a": "ا", "b": "ب", "d": "د", "e": "ي", "f": "ف", "h": "ه", "i": "ي", "j": "ج", "k": "ك",
    "l": "ل", "m": "م", "n": "ن", "o": "و", "p": "ب", "q": "ك", "r": "ر", "s": "س", "t": "ت",
    "u": "و", "v": "ف", "w": "و", "x": "كس", "y": "ي", "z": "ز",
}
_INITIAL_VOWEL = {"a": "أ", "e": "إ", "i": "إ", "o": "أو", "u": "أو"}
_INITIAL_LETTER = {
    "A": "أ", "B": "ب", "C": "ك", "D": "د", "E": "إ", "F": "ف", "G": "غ", "H": "هـ", "I": "إ",
    "J": "ج", "K": "ك", "L": "ل", "M": "م", "N": "ن", "O": "أ", "P": "ب", "Q": "ك", "R": "ر",
    "S": "س", "T": "ت", "U": "أ", "V": "ف", "W": "و", "X": "إكس", "Y": "ي", "Z": "ز",
}
_SUFFIXES = {"jr": "جونيور", "sr": "الأب"}
_PARTICLES = {"de": "دي", "van": "فان", "von": "فون", "der": "دير", "la": "لا", "le": "لو", "da": "دا",
              "di": "دي", "del": "ديل", "du": "دو", "al": "آل", "bin": "بن", "ibn": "ابن", "abu": "أبو"}
_VOICED = set("bdglmnrv")
_VOWELS = set("aeiouy")


def _word(word: str, hard_g: str) -> str:
    w = word.lower()
    # silent final e, including the e of a final -es after a consonant (Charles, James)
    if len(w) > 3 and w.endswith("es") and w[-3] not in _VOWELS:
        w = w[:-2] + "s"
    elif len(w) > 3 and w.endswith("e") and w[-2] not in _VOWELS:
        w = w[:-1]
    out: list[str] = []
    i = 0
    while i < len(w):
        rest = w[i:]
        if i == 0 and rest[0] in _INITIAL_VOWEL and not any(rest.startswith(d) for d, _ in _DIGRAPHS[10:]):
            out.append(_INITIAL_VOWEL[rest[0]])
            i += 1
            continue
        if rest == "er" and i > 0:
            out.append("ر")
            break
        if i == 0 and rest.startswith("gh"):
            out.append("غ")
            i += 2
            continue
        if rest.startswith("gh"):
            i += 2  # silent medial/final gh (Vaughan, Hugh)
            continue
        for digraph, arabic in _DIGRAPHS:
            if rest.startswith(digraph):
                if i == 0 and digraph[0] in _VOWELS:
                    arabic = "أ" + arabic.lstrip("ا")
                out.append(arabic)
                i += len(digraph)
                break
        else:
            ch = rest[0]
            nxt = rest[1] if len(rest) > 1 else ""
            if i > 0 and ch == w[i - 1] and ch not in _VOWELS:
                i += 1  # collapse doubled consonants
                continue
            if ch == "c":
                out.append("س" if nxt in ("e", "i", "y") else "ك")
            elif ch == "g":
                out.append("ج" if nxt in ("e", "i", "y") else hard_g)
            elif ch == "s" and i == len(w) - 1 and i > 0 and w[i - 1] in _VOICED:
                out.append("ز")
            elif ch == "y" and i == 0:
                out.append("ي")
            elif ch in _SINGLE:
                out.append(_SINGLE[ch])
            i += 1
    return "".join(out)


def transliterate_name(name: str, *, hard_g: str = "غ") -> str:
    """Transliterate a person name token by token (see module docstring for the convention)."""
    parts = []
    for token in name.split():
        bare = token.strip(".,")
        low = bare.lower()
        if low in _SUFFIXES:
            parts.append(_SUFFIXES[low])
        elif len(bare) == 1 and bare.isalpha():
            parts.append(_INITIAL_LETTER.get(bare.upper(), bare) + ".")
        elif low in _PARTICLES and parts:
            parts.append(_PARTICLES[low])
        else:
            pieces = [_word(p, hard_g) for p in re.split(r"[-’']", bare) if p]
            parts.append("-".join(p for p in pieces if p))
    return " ".join(p for p in parts if p)
