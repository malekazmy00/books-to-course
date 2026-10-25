"""Deterministic Arabic text normalization (TN) for speech (R7; audit 07 C2, §5.2 S1).

Rule order matters and is fixed: digits are unified first; material that is never spoken
(URLs, footnote markers, bracketed references, page references, Latin citations and glosses)
is removed before anything is verbalized; then abbreviations, the pronunciation lexicon and
numbers are expanded; symbols and spacing are cleaned last. Every rule that changed the text
is recorded by id, so a speech-text artifact says what was done to each segment.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .lexicon import PronunciationLexicon, spell_acronym, transliterate_latin
from .numbers import MAX_CARDINAL, cardinal, decade, ordinal, roman_to_int

NORMALIZER_VERSION = "ar-tn-1"

AR = "\u0621-\u064a\u0671-\u06d3"  # Arabic letters (incl. the tatweel)
AR_LETTERS = "\u0621-\u063a\u0641-\u064a\u0671-\u06d3"  # without the tatweel
_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹"

# Words after which a numeral is read in the oblique (genitive) form; it is also the default.
_NOMINATIVE_AFTER = {"هو", "هي", "هم", "كان", "كانت", "أصبح", "بلغ", "وصل", "يبلغ"}
_PREPOSITIONS = {"في", "من", "إلى", "الى", "على", "عن", "حتى", "منذ", "مذ", "بين", "خلال", "بعد", "قبل", "مع",
                 "نحو", "حوالي", "قرابة", "لـ", "بـ"}
# Nouns that take a following ordinal (``الفصل 1`` -> ``الفصل الأول``), by grammatical gender.
_ORDINAL_NOUNS_M = ("الفصل", "الباب", "الجزء", "المجلد", "القرن", "الدرس", "القسم", "المبحث", "المطلب",
                    "الكتاب", "المؤتمر", "العدد")
_ORDINAL_NOUNS_F = ("الطبعة", "المادة", "الوحدة", "المرحلة", "الحلقة", "الفقرة", "الدورة", "المقالة",
                    "الألفية")

_URL = re.compile(r"(?:https?://|www\.)\S+|\b10\.\d{4,9}/\S+|[\w.+-]+@[\w-]+\.[\w.-]+", re.IGNORECASE)
_BRACKET_REF = re.compile(r"\s*\[[\s\d,،;؛\-–.…ص]*\]")
_PAREN = re.compile(r"\s*\(([^()]*)\)")
_PAGE_REF = re.compile(rf"(?:\bانظر\s+)?(?<![{AR}])صص?\.?\s*\d+(?:\s*[-–]\s*\d+)?")
# 1-3 digits glued after sentence punctuation (not after a digit, so 3.5 survives) or glued to
# the end of an Arabic word: a footnote marker the extraction left in the text.
_GLUED_MARKER = re.compile(rf"(?:(?<=[^\d\s][.،؛:!?؟»”\")])|(?<=[{AR_LETTERS}]))\d{{1,3}}(?=\s|$|[.،؛:])")
_HEADING_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")
_LIST_MARKER = re.compile(r"^\s*(?:\d+|[A-Za-z]|[أبجده])[.)\-]\s+")
_DEFINITE_DIGIT = re.compile(r"ال[ـ]?(?=\d)")
_THOUSANDS = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?![\d,])")
_DECADE = re.compile(r"(\d{4})\s?(?:ات|s)(?![A-Za-z" + AR + "])")
_PERCENT = re.compile(r"[%٪]\s?(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s?[%٪]")
_ERA = re.compile(rf"(\d+)\s?(م|هـ|ه)(?![{AR}])\.?")
_RANGE = re.compile(r"(\d+)\s?[-–—]\s?(\d+)(?![\d.])")
_ORDINAL = re.compile(
    rf"({'|'.join(_ORDINAL_NOUNS_M + _ORDINAL_NOUNS_F)})\s+(?:رقم\s+)?(?:ال[ـ]?)?(\d+|[IVXLCDM]+)(?![\dA-Za-z{AR}])")
_DECIMAL = re.compile(r"\d+\.\d+")
_INTEGER = re.compile(r"\d+")
_ACRONYM = re.compile(r"(?<![A-Za-z])(?:(?:[A-Z]\.){2,}|[A-Z]{2,6}(?![A-Za-z]))")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_ABBREVIATIONS = [
    (re.compile(rf"(?<![{AR}])أ\.\s?د\.\s?"), "الأستاذ الدكتور ", "abbreviation"),
    (re.compile(rf"(?<![{AR}])د\.\s?(?=[{AR}])"), "دكتور ", "abbreviation"),
    (re.compile(rf"(?<![{AR}])ق\.\s?م\.?(?![{AR}])"), "قبل الميلاد", "abbreviation"),
    (re.compile(rf"(?<![{AR}])إلخ\.?(?![{AR}])"), "إلى آخره", "abbreviation"),
    (re.compile(rf"(?<![{AR}])ت\.\s?(?=\d)"), "توفي ", "abbreviation"),
]
_SYMBOLS = [
    (re.compile(r"\s*[•▪·●◦■□►]\s*"), "، ", "symbol"),
    (re.compile(r"\s+[—–-]\s+|\s*—\s*"), "، ", "symbol"),
    (re.compile(rf"(?<=[{AR}])\s*/\s*(?=[{AR}])"), " أو ", "symbol"),
    (re.compile(r"\s*&\s*"), " و", "symbol"),
    (re.compile(r"\.{3}|…"), ".", "symbol"),
    (re.compile(r"[«»“”„\"‘’']"), "", "quotes_removed"),
    (re.compile(r"[\[\]{}()*#_|~^=<>+\\$%\-]|٪"), " ", "symbol"),
]
_LATIN_LETTER = re.compile(r"[A-Za-z]")
_ARABIC_LETTER = re.compile(rf"[{AR}]")


@dataclass
class Normalized:
    text: str
    rules: list[str] = field(default_factory=list)
    lexicon_hits: list[str] = field(default_factory=list)
    lexicon_misses: list[str] = field(default_factory=list)

    def note(self, rule: str) -> None:
        if rule not in self.rules:
            self.rules.append(rule)


def _sub(out: Normalized, pattern: re.Pattern[str], repl, rule: str) -> None:
    new = pattern.sub(repl, out.text)
    if new != out.text:
        out.text = new
        out.note(rule)


def _previous_word(text: str, start: int) -> str:
    words = re.findall(rf"[{AR}ـ]+|\S+", text[:start])
    return words[-1] if words else ""


def _case_at(text: str, start: int) -> str:
    return "nom" if _previous_word(text, start) in _NOMINATIVE_AFTER else "gen"


def _number_words(digits: str, case: str) -> str:
    value = int(digits)
    if value > MAX_CARDINAL:
        return " ".join(cardinal(int(d)) for d in digits)  # an identifier: read digit by digit
    return cardinal(value, case=case)  # type: ignore[arg-type]


def _decimal_words(number: str, case: str) -> str:
    whole, _, frac = number.partition(".")
    zeros = len(frac) - len(frac.lstrip("0"))
    tail = " ".join(["صفر"] * zeros + ([cardinal(int(frac), case=case)] if frac.strip("0") else []))  # type: ignore[arg-type]
    return f"{_number_words(whole, case)} فاصلة {tail or 'صفر'}"


def _numeric_words(number: str, case: str) -> str:
    return _decimal_words(number, case) if "." in number else _number_words(number, case)


def _remove_unspoken(out: Normalized, structure: str) -> None:
    _sub(out, _URL, "", "url_removed")
    _sub(out, _DEFINITE_DIGIT, "", "definite_number")  # الـ19 -> 19, before marker detection
    if any(ch in out.text for ch in _SUPERSCRIPTS):
        out.text = "".join(ch for ch in out.text if ch not in _SUPERSCRIPTS)
        out.note("footnote_marker_removed")
    _sub(out, _BRACKET_REF, "", "bracket_reference_removed")

    def paren(match: re.Match[str]) -> str:
        inner = match.group(1)
        if _LATIN_LETTER.search(inner) and not _ARABIC_LETTER.search(inner):
            out.note("latin_parenthetical_removed")
            return ""
        if re.fullmatch(rf"\s*(?:انظر\s+)?(?<![{AR}])صص?\.?\s*\d[\d\s\-–،,]*", inner):
            out.note("page_reference_removed")
            return ""
        if re.fullmatch(r"[\s\d.,،\-–]*", inner) and not re.search(r"\d{4}", inner):
            # a reference/list number such as (3) or (12، 14); years such as (1939-1945) are spoken
            out.note("numeric_parenthetical_removed")
            return ""
        return " " + inner  # keep the Arabic content; the brackets themselves are not spoken

    out.text = _PAREN.sub(paren, out.text)
    _sub(out, _PAGE_REF, "", "page_reference_removed")
    _sub(out, _GLUED_MARKER, "", "footnote_marker_removed")
    if structure == "heading":
        _sub(out, _HEADING_NUMBER, "", "section_number_removed")
    elif structure == "list_item":
        _sub(out, _LIST_MARKER, "", "list_marker_removed")


def _expand_latin(out: Normalized, lexicon: PronunciationLexicon) -> None:
    pattern = lexicon.pattern()
    if pattern is not None:
        def hit(match: re.Match[str]) -> str:
            spoken = lexicon.lookup(match.group(0))
            if spoken is None:
                return match.group(0)
            out.lexicon_hits.append(re.sub(r"\s+", " ", match.group(0)))
            out.note("lexicon")
            return spoken
        out.text = pattern.sub(hit, out.text)

    def acronym(match: re.Match[str]) -> str:
        out.note("acronym_spelled")
        return spell_acronym(match.group(0).replace(".", ""))
    out.text = _ACRONYM.sub(acronym, out.text)

    def fallback(match: re.Match[str]) -> str:
        out.lexicon_misses.append(match.group(0))
        out.note("latin_fallback")
        return transliterate_latin(match.group(0))
    out.text = _LATIN_WORD.sub(fallback, out.text)


def _expand_ordinals(out: Normalized) -> None:
    """Before Latin expansion, so a Roman numeral (القرن XX) is not spelled as letters."""
    def ordinal_words(match: re.Match[str]) -> str:
        noun, value = match.group(1), match.group(2)
        number = int(value) if value.isdigit() else roman_to_int(value)
        if not number or number > 99:
            return match.group(0)  # e.g. a year after the noun: read as a cardinal later
        out.note("ordinal")
        gender = "f" if noun in _ORDINAL_NOUNS_F else "m"
        case = "gen" if _previous_word(out.text, match.start()) in _PREPOSITIONS else "nom"
        return f"{noun} {ordinal(number, gender=gender, case=case)}"  # type: ignore[arg-type]
    out.text = _ORDINAL.sub(ordinal_words, out.text)


def _expand_numbers(out: Normalized) -> None:
    _sub(out, _THOUSANDS, lambda m: m.group(0).replace(",", ""), "thousands_separator")

    def decade_words(match: re.Match[str]) -> str:
        words = decade(int(match.group(1)))
        if words is None:
            return match.group(0)
        out.note("decade")
        return words
    out.text = _DECADE.sub(decade_words, out.text)

    def percent(match: re.Match[str]) -> str:
        out.note("percent")
        number = match.group(1) or match.group(2)
        return " " + _numeric_words(number, "gen") + " بالمئة"
    out.text = _PERCENT.sub(percent, out.text)

    def era(match: re.Match[str]) -> str:
        out.note("year_era")
        previous = _previous_word(out.text, match.start())
        feminine = previous in {"سنة", "السنة", "سنتي"}
        suffix = {"م": "ميلادية" if feminine else "ميلادي"}.get(match.group(2), "هجرية" if feminine else "هجري")
        return f"{_number_words(match.group(1), 'gen')} {suffix}"
    out.text = _ERA.sub(era, out.text)

    def number_range(match: re.Match[str]) -> str:
        out.note("range")
        before = re.findall(rf"[{AR}]+", out.text[:match.start()])[-2:]
        first = _number_words(match.group(1), "gen")
        second = _number_words(match.group(2), "gen")
        if "بين" in before:
            return f"{first} و{second}"
        if before and before[-1] == "من":
            return f"{first} إلى {second}"
        return f"من {first} إلى {second}"
    out.text = _RANGE.sub(number_range, out.text)

    def decimal(match: re.Match[str]) -> str:
        out.note("decimal")
        return _decimal_words(match.group(0), _case_at(out.text, match.start()))
    out.text = _DECIMAL.sub(decimal, out.text)

    def integer(match: re.Match[str]) -> str:
        value = int(match.group(0))
        out.note("year" if len(match.group(0)) == 4 and 1000 <= value <= 2100 else "cardinal")
        return _number_words(match.group(0), _case_at(out.text, match.start()))
    out.text = _INTEGER.sub(integer, out.text)


def _clean(out: Normalized) -> None:
    for pattern, repl, rule in _SYMBOLS:
        _sub(out, pattern, repl, rule)
    text = out.text.replace("ـ", "")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.،؛:!?؟,])", r"\1", text)
    text = re.sub(r"([،؛,:])(?:\s*[،؛,:])+", r"\1", text)
    text = re.sub(r"[،؛,:]\s*([.!?؟])", r"\1", text)
    text = re.sub(r"^[\s.،؛,:!?؟]+", "", text)
    out.text = text.strip()


def normalize_text(text: str, *, lexicon: PronunciationLexicon | None = None,
                   structure: str = "paragraph") -> Normalized:
    """Spoken form of one span of accepted Arabic text."""
    lexicon = lexicon or PronunciationLexicon()
    out = Normalized(text=unicodedata.normalize("NFC", text))
    unified = out.text.translate(_DIGIT_MAP)
    unified = re.sub(r"(?<=\d)٫(?=\d)", ".", unified)
    unified = re.sub(r"(?<=\d)٬(?=\d)", ",", unified)
    if unified != out.text:
        out.text = unified
        out.note("digits_unified")
    out.text = re.sub(r"[\r\n\t]+", " ", out.text)
    _remove_unspoken(out, structure)
    for pattern, repl, rule in _ABBREVIATIONS:
        _sub(out, pattern, repl, rule)
    _expand_ordinals(out)
    _expand_latin(out, lexicon)
    _expand_numbers(out)
    _clean(out)
    return out


_VIOLATIONS = {
    "raw_digit": re.compile(r"[0-9٠-٩۰-۹]"),
    "bracket_reference": re.compile(r"\[[^\]]*\]"),
    "latin_letters": _LATIN_LETTER,
    "url": re.compile(r"https?://|www\.", re.IGNORECASE),
    "superscript": re.compile(f"[{_SUPERSCRIPTS}]"),
}


def speech_text_violations(text: str) -> list[str]:
    """What must never reach a TTS provider (plan R7: 0 raw digits, 0 ``[n]``, 0 Latin)."""
    return [name for name, pattern in _VIOLATIONS.items() if pattern.search(text)]


__all__ = ["NORMALIZER_VERSION", "Normalized", "normalize_text", "speech_text_violations"]
