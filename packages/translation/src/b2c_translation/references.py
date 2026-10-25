"""Deterministic handling of ``kind="reference"`` units (D-028 principle 2).

Bibliographic entries, ``Ibid.``-style notes and short-form citations are never sent to a
model. Following Arabic academic practice the citation itself (author names, titles,
journal names, places, publishers, numbers, URLs/DOIs) stays in its original language;
only the connective abbreviations are rendered in their conventional Arabic forms.

Sources for the Arabic forms (checked 2026-10-24):

* ACRPS — المركز العربي للأبحاث ودراسة السياسات، «دليل التحرير»، فصل «طريقة توثيق الهوامش
  والمراجع» (pp. 46-51), https://tabayyun.dohainstitute.org/ar/Documents/editorial-guidelines.pdf :
  ``المرجع نفسه`` / ``المرجع نفسه، ص رقم`` for Ibid.; page numbers ``ص رقم`` and ranges
  ``ص رقم-رقم`` (one ``ص`` also for ranges); ``د. م.`` / ``د. ن.`` / ``د. ت.`` for no
  place / publisher / date; edition ``ط رقم``; volume ``مج رقم``; issue ``العدد رقم``;
  ``ترجمة`` before the translator; the editor as ``(محرر)`` after the name; ``يُنظر`` for
  "see"; ``وآخرون`` for more than three authors.
* ``مرجع سابق`` for *op. cit.*, ``قارن`` for *cf.* and ``مذكور في`` for "cited in" are not
  in the ACRPS guide (it prescribes short-form repeats instead of op. cit.); they are the
  forms commonly used in Arabic university citation guides and are marked as such below.

Policy (owner, 2026-10-25, D-048): follow the practice of established Arabic academic translation
(ACRPS): a *foreign-language* note keeps its connectives in its own language (``Ibid., p. number``),
so a reference unit is copied verbatim (``render_reference_unit``). The Arabic connective table
below stays available (``translate_connectives=True``) for references written in Arabic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from b2c_contracts import TranslationUnit

ACRPS = "ACRPS دليل التحرير pp. 46-51 (2026-10-24)"
COMMON = "common Arabic university citation practice; not in ACRPS (2026-10-24)"


@dataclass(frozen=True)
class Connective:
    name: str
    pattern: re.Pattern[str]
    arabic: str  # replacement template (may use \1 for a captured number)
    source: str


def _c(name: str, pattern: str, arabic: str, source: str, flags: int = 0) -> Connective:
    return Connective(name, re.compile(pattern, flags), arabic, source)


_NUM = r"([0-9ivxlcdmIVXLCDM]+(?:\s*[-–]\s*[0-9ivxlcdmIVXLCDM]+)?)"

# Order matters: longer / more specific connectives first.
CONNECTIVES: tuple[Connective, ...] = (
    # a final "Ibid." also closes the note, so its period stays ("المرجع نفسه.")
    _c("ibid_final", r"(?<![\w-])[Ii]bid\.(?=\s*$)", "المرجع نفسه.", ACRPS),
    _c("ibid", r"(?<![\w-])[Ii]bid\.?(?![\w-])", "المرجع نفسه", ACRPS),
    _c("op_cit", r"(?<![\w-])op\.\s?cit\.", "مرجع سابق", COMMON, re.I),
    _c("loc_cit", r"(?<![\w-])loc\.\s?cit\.", "الموضع نفسه", COMMON, re.I),
    _c("see_also", r"(?<![\w-])[Ss]ee also(?![\w-])", "يُنظر أيضًا", ACRPS),
    _c("see", r"(?<![\w-])(?:See|see)(?=\s)", "يُنظر", ACRPS),
    _c("cf", r"(?<![\w-])[Cc]f\.", "قارن", COMMON),
    _c("cited_in", r"(?<![\w-])cited in(?![\w-])", "مذكور في", COMMON),
    _c("quoted_in", r"(?<![\w-])quoted in(?![\w-])", "مقتبس في", COMMON),
    _c("et_al", r"(?<![\w-])et al\.?", "وآخرون", ACRPS),
    _c("edition", r"(?<![\w-])(\d+)(?:st|nd|rd|th) ed\.", r"ط \1", ACRPS),
    _c("editor_role", r",\s*eds?\.,", " (محرر)،", ACRPS),
    _c("edited_by", r"(?<![\w-])(?:[Ee]dited by|eds?\.)(?=\s+[A-Z])", "تحرير", ACRPS),
    _c("translated_by", r"(?<![\w-])(?:[Tt]ranslated by|trans\.)(?=\s+[A-Z])", "ترجمة", ACRPS),
    _c("volume", r"(?<![\w-])[Vv]ols?\.\s?" + _NUM, r"مج \1", ACRPS),
    _c("issue", r"(?<![\w-])[Nn]o\.\s?(\d+)", r"العدد \1", ACRPS),
    _c("pages", r"(?<![\w-])pp?\.\s?" + _NUM, r"ص \1", ACRPS),
    _c("no_date", r"(?<![\w-])n\.\s?d\.", "د. ت.", ACRPS),
    _c("no_place", r"(?<![\w-])n\.\s?p\.", "د. م.", ACRPS),
    _c("chapter", r"(?<![\w-])chap\.\s?(\d+)", r"الفصل \1", COMMON),
    _c("phd", r"(?<![\w-])PhD diss\.", "أطروحة دكتوراه", ACRPS),
)

_ARABIC = r"[؀-ۿ]"
# A Latin comma touching a rendered Arabic connective (optionally followed by its number)
# becomes the Arabic comma, as in ACRPS examples ("المرجع نفسه، ص 12").
_COMMA_AFTER = re.compile(rf"({_ARABIC}\.?(?:\s[0-9ivxlcdm]+(?:[-–][0-9ivxlcdm]+)?)?),(?=\s|$)")
_COMMA_BEFORE = re.compile(rf",(\s+{_ARABIC})")
_PROTECTED = re.compile(r"https?://\S+|doi\.org/\S+|\bdoi:\S+|www\.\S+", re.I)


def render_reference(source_text: str, *, translate_connectives: bool = True) -> str:
    """Return the Arabic-side rendering of a reference unit. Pure; never calls a model."""
    if not translate_connectives:
        return source_text
    protected: list[str] = []

    def _protect(m: re.Match[str]) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    text = _PROTECTED.sub(_protect, source_text)
    for rule in CONNECTIVES:
        text = rule.pattern.sub(rule.arabic, text)
    text = _COMMA_AFTER.sub(r"\1،", text)
    text = _COMMA_BEFORE.sub(r"،\1", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)


def render_reference_unit(unit: TranslationUnit, *, translate_connectives: bool = False) -> str:
    if unit.kind != "reference":
        raise ValueError(f"unit {unit.unit_id} is {unit.kind!r}, not 'reference'")
    return render_reference(unit.source_text, translate_connectives=translate_connectives)
