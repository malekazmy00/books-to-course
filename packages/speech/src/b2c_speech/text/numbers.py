"""Arabic number words for speech (MSA), deterministic and dependency-free.

``case`` is "nom" (nominative: ـان / ـون) or "gen" (oblique, genitive/accusative: ـين). Syntactic
case is not resolved by a rule-based front end; callers pass "gen" after prepositions and nouns in
construct (``في عام 2011`` -> ``ألفين وأحد عشر``) and use it as the default reading otherwise,
which is the usual reading of numerals in running MSA prose. ``gender`` is that of the counted
noun ("m" by default). Spelling of 100 is ``مئة`` (modern orthography).
"""
from __future__ import annotations

from typing import Literal

Case = Literal["nom", "gen"]
Gender = Literal["m", "f"]

_ONES_M = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ONES_F = ["", "واحدة", "اثنتان", "ثلاث", "أربع", "خمس", "ست", "سبع", "ثماني", "تسع"]
_TENS = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمئة", "أربعمئة", "خمسمئة", "ستمئة", "سبعمئة", "ثمانمئة", "تسعمئة"]
# singular, dual nom, dual gen, plural (3-10), accusative singular (11-99)
_SCALES = [
    (10 ** 9, ("مليار", "ملياران", "مليارين", "مليارات", "مليارًا")),
    (10 ** 6, ("مليون", "مليونان", "مليونين", "ملايين", "مليونًا")),
    (10 ** 3, ("ألف", "ألفان", "ألفين", "آلاف", "ألفًا")),
]
_ORD_M = ["", "الأول", "الثاني", "الثالث", "الرابع", "الخامس", "السادس", "السابع", "الثامن", "التاسع", "العاشر"]
_ORD_F = ["", "الأولى", "الثانية", "الثالثة", "الرابعة", "الخامسة", "السادسة", "السابعة", "الثامنة", "التاسعة",
          "العاشرة"]
MAX_CARDINAL = 10 ** 12 - 1


def _oblique(word: str, case: Case) -> str:
    return word[:-2] + "ين" if case == "gen" and word.endswith("ون") else word


def _below_100(n: int, case: Case, gender: Gender) -> str:
    if n == 0:
        return ""
    tens, unit = divmod(n, 10)
    if n < 10:
        if n == 2:
            word = "اثنان" if gender == "m" else "اثنتان"
            return word[:-2] + "ين" if case == "gen" else word
        return (_ONES_M if gender == "m" else _ONES_F)[n]
    if n == 10:
        return "عشرة" if gender == "m" else "عشر"
    if n == 11:
        return "أحد عشر" if gender == "m" else "إحدى عشرة"
    if n == 12:
        if gender == "m":
            return "اثني عشر" if case == "gen" else "اثنا عشر"
        return "اثنتي عشرة" if case == "gen" else "اثنتا عشرة"
    if n < 20:
        return _ONES_M[unit] + " عشر" if gender == "m" else _ONES_F[unit] + " عشرة"
    ten = _oblique(_TENS[tens], case)
    if unit == 0:
        return ten
    if unit == 1:
        first = "واحد" if gender == "m" else "إحدى"
    elif unit == 2:
        first = _below_100(2, case, gender)
    else:
        first = (_ONES_M if gender == "m" else _ONES_F)[unit]
    return f"{first} و{ten}"


def _below_1000(n: int, case: Case, gender: Gender) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        word = _HUNDREDS[hundreds]
        parts.append("مئتين" if hundreds == 2 and case == "gen" else word)
    if rest:
        parts.append(_below_100(rest, case, gender))
    return _join(parts)


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    return parts[0] + "".join(" و" + p for p in parts[1:]) if parts else ""


def _scale_phrase(k: int, forms: tuple[str, str, str, str, str], case: Case) -> str:
    singular, dual_nom, dual_gen, plural, accusative = forms
    if k == 1:
        return singular
    if k == 2:
        return dual_gen if case == "gen" else dual_nom
    if k <= 10:
        return f"{_below_100(k, case, 'm')} {plural}"
    rest = k % 100
    if k == 200:
        return ("مئتي " if case == "gen" else "مئتا ") + singular
    if rest == 0:
        return f"{_below_1000(k, case, 'm')} {singular}"
    if rest <= 2:  # e.g. 101 000: "مئة ألف وألف"
        return f"{_below_1000(k - rest, case, 'm')} {singular} و{_scale_phrase(rest, forms, case)}"
    if rest <= 10:
        return f"{_below_1000(k, case, 'm')} {plural}"
    return f"{_below_1000(k, case, 'm')} {accusative}"


def cardinal(n: int, *, case: Case = "gen", gender: Gender = "m") -> str:
    """Cardinal number words; 0 <= n < 10**12."""
    if n < 0 or n > MAX_CARDINAL:
        raise ValueError(f"number out of range for speech: {n}")
    if n == 0:
        return "صفر"
    parts = []
    for size, forms in _SCALES:
        k, n = divmod(n, size)
        if k:
            parts.append(_scale_phrase(k, forms, case))
    if n:
        parts.append(_below_1000(n, case, gender))
    return _join(parts)


def ordinal(n: int, *, gender: Gender = "m", case: Case = "nom") -> str:
    """Definite ordinal adjective (``الفصل الأول``, ``الطبعة الثانية``); n >= 100 falls back to the
    cardinal, which is how such numbers are read in practice (``الصفحة مئة وعشرون``)."""
    if n <= 0:
        raise ValueError("ordinals start at 1")
    table = _ORD_M if gender == "m" else _ORD_F
    if n <= 10:
        return table[n]
    if n == 11:
        return "الحادي عشر" if gender == "m" else "الحادية عشرة"
    if n < 20:
        return table[n - 10] + (" عشر" if gender == "m" else " عشرة")
    if n < 100:
        tens, unit = divmod(n, 10)
        ten = "ال" + _oblique(_TENS[tens], case)
        if unit == 0:
            return ten
        first = ("الحادي" if gender == "m" else "الحادية") if unit == 1 else table[unit]
        return f"{first} و{ten}"
    return cardinal(n, case=case, gender=gender)


_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_OUT = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
              (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def roman_to_int(text: str) -> int | None:
    """Value of a canonical Roman numeral, or None (``IIII`` and ``IC`` are not canonical)."""
    if not text or any(ch not in _ROMAN for ch in text):
        return None
    total = 0
    for i, ch in enumerate(text):
        value = _ROMAN[ch]
        total += -value if i + 1 < len(text) and _ROMAN[text[i + 1]] > value else value
    rebuilt, rest = "", total
    for value, symbol in _ROMAN_OUT:
        while rest >= value:
            rebuilt, rest = rebuilt + symbol, rest - value
    return total if rebuilt == text and total > 0 else None


_DECADES = {20: "عشرينيات", 30: "ثلاثينيات", 40: "أربعينيات", 50: "خمسينيات", 60: "ستينيات",
            70: "سبعينيات", 80: "ثمانينيات", 90: "تسعينيات"}


def decade(year: int) -> str | None:
    """``1980`` -> ``ثمانينيات القرن العشرين``; None when the year is not a nameable decade."""
    century, within = divmod(year, 100)
    if year % 10 or within not in _DECADES or century < 1:
        return None
    return f"{_DECADES[within]} القرن {ordinal(century + 1, case='gen')}"


__all__ = ["MAX_CARDINAL", "cardinal", "decade", "ordinal", "roman_to_int"]
