"""Deterministic translation checks (D-028, layer 1 of the MQM quality gate).

Every check is a pure ``Check = Callable[[CheckContext], list[QAIssue]]``; none calls a
model. ``DEFAULT_CHECKS`` is the ordered registry with stable ``check_id``s and the MQM
categories each check may emit. Severities follow the 2026-10-24 error catalogue:

* critical — evidence that the target is not a translation of the unit (empty target,
  leaked instructions, most of the source missing);
* major    — verifiable defects that block acceptance (numbers, footnote markers, locked
  terms/entities, foreign scripts, untranslated or unexplained Latin text, length/sentence
  omission or addition signals, dropped negation, broken brackets, duplicated passages);
* minor    — style signals that never block on their own (quotes, adjacent repeated
  phrase, negation added without a source cue).

Calibrated constants (length bounds, run lengths) were fixed on the calibration split
(70%) of the audit gold set and verified on the held-out 30%; see
``eval/reports/translation-v2-qa-2026-10-24.md``. They are not to be loosened to pass a
gate (CLAUDE.md rule 2).
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from b2c_contracts import QACategory, QAIssue, Severity
from b2c_core.arabic_text import normalize_arabic

from .leakage import leakage
from .types import Check, CheckContext

# ------------------------------------------------------------------ text helpers
_ARABIC_LETTER = "ء-غف-يٱ-ۓ"
ARABIC_LETTER_RE = re.compile(f"[{_ARABIC_LETTER}]")
LATIN_WORD_RE = re.compile(r"[A-Za-zÀ-ɏ]+(?:['’][A-Za-z]+)?")
_WORD_RE = re.compile(rf"[{_ARABIC_LETTER}ً-ْٰ]+|[A-Za-zÀ-ɏ]+(?:['’][A-Za-z]+)?|\d+")
_URLISH_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w.+-]+@[\w-]+\.[\w.]+\b|\b10\.\d{4,9}/\S+|"
                        r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|edu|gov|int|info|io|uk|de|fr|it)\b\S*", re.I)

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")


def normalize_digits(text: str) -> str:
    """Arabic-Indic and Persian digits -> ASCII; ``٫`` -> ``.``; ``٬`` -> ``,``."""
    return text.translate(_DIGITS)


def _issue(check_id: str, category: QACategory, severity: Severity, **evidence) -> QAIssue:
    return QAIssue(check_id=check_id, category=category, severity=severity, origin="deterministic",
                   evidence=evidence)


def _letters(text: str) -> int:
    return len(re.sub(r"\s", "", text))


# ------------------------------------------------------------------ empty / leakage
def empty_target(ctx: CheckContext) -> list[QAIssue]:
    if ctx.target.strip():
        return []
    return [_issue("empty_target", "omission", "critical", reason="blank target for a non-blank source")]


# ------------------------------------------------------------------ scripts
_FOREIGN_SCRIPTS = {
    "cjk": "぀-ヿ㐀-䶿一-鿿가-힯豈-﫿",
    "cyrillic": "Ѐ-ӿ",
    "hebrew": "֐-׿",
    "greek": "Ͱ-Ͽ",
    "devanagari": "ऀ-ॿ",
    "thai": "฀-๿",
    # Arabic-script letters of Urdu/Persian/Kurdish that standard Arabic never uses.
    # (پ چ ژ گ ڤ are tolerated: they occur in Arabic transliteration of foreign names.)
    "non_arabic_letters": "کگںھہۃیےۓٹڈڑ",
}
_FOREIGN_RE = {name: re.compile(f"[{ranges}]") for name, ranges in _FOREIGN_SCRIPTS.items()}
_ESCAPE_RE = re.compile(r"\\[nrt]|\\u[0-9a-fA-F]{4}")


_GLUED_RE = re.compile(rf"([{_ARABIC_LETTER}]+)([A-Za-z]+)|([A-Za-z]+)([{_ARABIC_LETTER}]+)")
# Arabic proclitics that may legitimately precede a Latin name ("وHeuer", "لـCIA").
_PROCLITICS = frozenset({"و", "ف", "ب", "ل", "ك", "ال", "وال", "بال", "فال", "كال", "لل", "ولل", "وب", "ول", "وك"})


def _glued_tokens(text: str) -> list[str]:
    """Mixed-script tokens: Arabic and Latin letters glued inside one word.

    Allowed: a proclitic before a capitalised Latin name/acronym. Flagged: Latin followed by
    Arabic letters ("بانيسter", "ريفولوتi") and Arabic glued to a lowercase English word
    ("للanalysts").
    """
    out = []
    for m in _GLUED_RE.finditer(_URLISH_RE.sub(" ", text)):
        if m.group(1) is not None:
            arabic, latin = m.group(1), m.group(2)
            if arabic in _PROCLITICS and latin[:1].isupper():
                continue
        out.append(m.group(0))
    return out


def foreign_script(ctx: CheckContext) -> list[QAIssue]:
    source, target = ctx.unit.source_text, ctx.target
    found: dict[str, list[str]] = {}
    for name, regex in _FOREIGN_RE.items():
        chars = sorted({c for c in regex.findall(target) if c not in source})
        if chars:
            found[name] = chars[:10]
    escapes = sorted({e for e in _ESCAPE_RE.findall(target) if e not in source})
    if escapes:
        found["literal_escape"] = escapes
    glued = sorted({g for g in _glued_tokens(target) if g not in source})
    if glued:
        found["mixed_script_token"] = glued[:10]
    if not found:
        return []
    return [_issue("foreign_script", "foreign_script", "major", scripts=found)]


def _latin_tokens(text: str) -> list[str]:
    return LATIN_WORD_RE.findall(_URLISH_RE.sub(" ", text))


# Kinds whose rule-based output legitimately keeps the original-language text.
_VERBATIM_KINDS = frozenset({"reference", "toc", "index", "noise"})


def latin_not_in_source(ctx: CheckContext) -> list[QAIssue]:
    """Latin words in the target must come from the source (names, citations, glosses)."""
    if ctx.unit.kind in _VERBATIM_KINDS:
        return []
    source = ctx.unit.source_text
    known = {t.casefold() for t in LATIN_WORD_RE.findall(source)}
    squeezed = re.sub(r"\s+", "", source).casefold()  # OCR letter-spaced words rejoined
    stray = []
    for token in _latin_tokens(ctx.target):
        key = token.casefold()
        if key in known or (len(key) >= 4 and key in squeezed):
            continue
        stray.append(token)
    if not stray:
        return []
    return [_issue("latin_not_in_source", "foreign_script", "major", tokens=sorted(set(stray))[:20])]


# ------------------------------------------------------------------ untranslated English
UNTRANSLATED_MAX_RUN = 6         # consecutive copied English words that read as prose
UNTRANSLATED_MAX_SHARE = 0.5     # Latin letters / (Latin + Arabic letters)
_EN_FUNCTION = frozenset("the of and to a an in is are was were that this for with as on by from be "
                         "it its or not which who".split())


def _longest_latin_prose_run(text: str) -> list[str]:
    """Longest run of consecutive Latin words (punctuation allowed, Arabic/digits break it)."""
    best: list[str] = []
    run: list[str] = []
    for token in _WORD_RE.findall(_URLISH_RE.sub(" ", text)):
        if LATIN_WORD_RE.fullmatch(token):
            run.append(token)
            if len(run) > len(best):
                best = list(run)
        else:
            run = []
    return best


# A Latin-only span in parentheses/quotes is an original-language gloss (titles, terms),
# standard practice in Arabic academic translation; it is not counted as an untranslated run.
_LATIN_GLOSS_RE = re.compile(r"\([^()\u0600-\u06FF]*\)|“[^”\u0600-\u06FF]*”|\"[^\"\u0600-\u06FF]*\"|"
                             r"«[^»\u0600-\u06FF]*»|\[[^\]\u0600-\u06FF]*\]")


def untranslated_english(ctx: CheckContext) -> list[QAIssue]:
    if ctx.unit.kind in _VERBATIM_KINDS:
        return []
    source_words = LATIN_WORD_RE.findall(ctx.unit.source_text)
    if len(source_words) < 2:
        return []
    full = _URLISH_RE.sub(" ", ctx.target)
    target = _LATIN_GLOSS_RE.sub(" ", full)
    if not ARABIC_LETTER_RE.search(target):
        target = full  # nothing but glosses: judge the whole text
    latin = sum(len(t) for t in LATIN_WORD_RE.findall(target))
    arabic = len(ARABIC_LETTER_RE.findall(target))
    share = latin / (latin + arabic) if latin + arabic else 0.0
    run = _longest_latin_prose_run(target)
    function_words = sum(1 for w in run if w.casefold() in _EN_FUNCTION)
    long_run = len(run) >= UNTRANSLATED_MAX_RUN and function_words >= 2
    if share >= UNTRANSLATED_MAX_SHARE and latin >= 8 or long_run:
        return [_issue("untranslated_english", "untranslated_english", "major",
                       latin_share=round(share, 3), longest_run_words=len(run))]
    return []


# ------------------------------------------------------------------ numbers
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_RANGE_RE = re.compile(r"\b(\d{2,4})(\s*[–—-]\s*)(\d{1,3})\b(?![.,]?\d)")
_ORDINAL_OR_DECADE_RE = re.compile(r"^(?:['’]?s\b|st\b|nd\b|rd\b|th\b)", re.I)
_DECADE_SUFFIX_RE = re.compile(r"^['’]?s\b", re.I)
# Arabic decade words: التسعينيات، تسعينات، الثمانينيات ...
_AR_DECADE_WORD_RE = re.compile(r"(?:عشرين|ثلاثين|اربعين|خمسين|ستين|سبعين|ثمانين|تسعين)(?:يات|ات)")
SMALL_NUMBER_WORD_LIMIT = 10  # 0..10 are usually written as words in Arabic


def _expand_ranges(text: str) -> str:
    """'207-9' -> '207-209', '1929–35' -> '1929–1935' (abbreviated second bound)."""
    def repl(m: re.Match[str]) -> str:
        a, sep, b = m.group(1), m.group(2), m.group(3)
        if len(b) >= len(a):
            return m.group(0)
        full = a[: len(a) - len(b)] + b
        return f"{a}{sep}{full}" if int(full) > int(a) else m.group(0)
    return _RANGE_RE.sub(repl, text)


def _canonical(token: str) -> list[str]:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", token):
        token = token.replace(",", "")
    parts = token.split(",") if "," in token else [token]
    out = []
    for part in parts:
        if re.fullmatch(r"\d+", part):
            out.append(str(int(part)))
        elif re.fullmatch(r"\d+\.\d+", part):
            out.append(part.rstrip("0").rstrip(".") if "." in part else part)
        else:
            out.append(part)
    return out


def _numbers(text: str) -> tuple[Counter[str], set[str], set[str]]:
    """Return (required multiset, optional values, decade values) of the numbers in ``text``.

    Decades ('1990s') and ordinals ('20th') are optional: Arabic usually writes them as
    words (التسعينيات، القرن العشرين).
    """
    text = _expand_ranges(normalize_digits(text))
    required: Counter[str] = Counter()
    optional: set[str] = set()
    decades: set[str] = set()
    for m in _NUMBER_RE.finditer(text):
        values = _canonical(m.group(0))
        if _ORDINAL_OR_DECADE_RE.match(text[m.end():]):
            optional.update(values)
            if _DECADE_SUFFIX_RE.match(text[m.end():]) and re.fullmatch(r"\d{3}0", m.group(0)):
                decades.update(values)
        else:
            required.update(values)
    return required, optional, decades


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                     "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split())
         if w != "_"}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
             "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "twentieth": 20,
             "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "hundredth": 100}


def _number_word_values(text: str) -> set[str]:
    """Values of English number words ('sixty-six' -> 66, 'two hundred' -> 200, 'third' -> 3)."""
    multipliers = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
    values: set[int] = set()
    previous: int | None = None
    for word in re.findall(r"[a-z]+(?:-[a-z]+)?", text.casefold()):
        parts = word.split("-")
        value: int | None = None
        if len(parts) == 2 and parts[0] in _TENS:
            unit = _UNITS.get(parts[1], _ORDINALS.get(parts[1]))
            value = None if unit is None else _TENS[parts[0]] + unit
        else:
            value = _UNITS.get(word, _TENS.get(word, _ORDINALS.get(word)))
        if word in multipliers and previous is not None:
            previous *= multipliers[word]
            values.add(previous)
        elif value is not None:
            values.add(value)
            previous = value
        else:
            previous = None
    return {str(v) for v in values}


def _marker_counts(markers: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for marker in markers:
        marker = normalize_digits(marker)
        if re.fullmatch(r"\d+", marker):
            counts.update(_canonical(marker))
    return counts


def numbers(ctx: CheckContext) -> list[QAIssue]:
    """Numbers compared as a multiset after digit normalisation (catalogue §2.10)."""
    src_req, src_opt, src_decades = _numbers(ctx.unit.source_text)
    tgt_req, tgt_opt, _ = _numbers(ctx.target)
    target_all = tgt_req + Counter(tgt_opt)
    target_all -= _marker_counts(ctx.unit.footnote_markers)
    word_values = _number_word_values(ctx.unit.source_text)
    missing: list[str] = []
    for value, count in src_req.items():
        short = count - target_all.get(value, 0)
        if short > 0 and not (value.isdigit() and int(value) <= SMALL_NUMBER_WORD_LIMIT):
            missing.extend([value] * short)
    extra: list[str] = []       # values the source does not contain at all
    surplus: list[str] = []     # more occurrences of a source value (e.g. a bilingual gloss)
    for value, count in target_all.items():
        if value in src_opt or value in word_values:
            continue  # optional values (decades, ordinals, number words) may appear freely
        if value not in src_req:
            extra.extend([value] * count)
        elif count > src_req[value]:
            surplus.extend([value] * (count - src_req[value]))
    # A decade ('1970s') written as the bare year ('1970') is a date error (catalogue §2.10),
    # unless the target also names the decade in words (التسعينيات).
    as_year = sorted(d for d in src_decades if tgt_req.get(d, 0) > src_req.get(d, 0)
                     and not _AR_DECADE_WORD_RE.search(normalize_arabic(ctx.target)))
    minor = [_issue("numbers", "number_error", "minor", surplus=sorted(surplus, key=_num_key))] if surplus else []
    if not missing and not extra and not as_year:
        return minor
    evidence: dict[str, list[str]] = {}
    if as_year:
        evidence["decade_as_year"] = as_year
    if missing:
        evidence["missing"] = sorted(missing, key=_num_key)
    if extra:
        evidence["extra"] = sorted(extra, key=_num_key)
    return [_issue("numbers", "number_error", "major", **evidence), *minor]


def _num_key(value: str) -> tuple[float, str]:
    try:
        return float(value), value
    except ValueError:
        return float("inf"), value


# ------------------------------------------------------------------ footnote markers
def footnote_markers(ctx: CheckContext) -> list[QAIssue]:
    """Markers are restored by appending them, in order, at the end of the target."""
    markers = ctx.unit.footnote_markers
    if not markers:
        return []
    tail = r"\s*".join(re.escape(normalize_digits(m)) for m in markers)
    if re.search(rf"(?<![\w]){tail}\s*$", normalize_digits(ctx.target)):
        return []
    return [_issue("footnote_markers", "footnote_marker", "major", expected=list(markers))]


# ------------------------------------------------------------------ length ratio
# Non-whitespace character ratio target/source. Calibrated on corrected and audit-approved
# translations (calibration split): observed ranges for sources >= 40 chars were
# prose 0.55-1.37, list_item 0.60-1.49, heading 0.57-0.90 (headings < 40 chars 0.42-1.92).
# Bounds sit outside the observed ranges so that only gross omission/addition fires.
LENGTH_RATIO_BOUNDS: dict[str, tuple[float, float]] = {
    "prose": (0.50, 1.60),
    "list_item": (0.50, 1.70),
    "heading": (0.35, 2.00),
    "caption": (0.50, 1.70),
    "table_cell": (0.40, 2.00),
    "footnote": (0.50, 1.70),
}
SHORT_SOURCE_CHARS = 40                       # below this the ratio is noisy:
SHORT_SOURCE_BOUNDS = (0.35, 2.50)            # observed 0.42-1.92 on short sources
MIN_SOURCE_CHARS = 12                         # below this no ratio is computed


def length_ratio(ctx: CheckContext) -> list[QAIssue]:
    bounds = LENGTH_RATIO_BOUNDS.get(ctx.unit.kind)
    src = _letters(ctx.unit.source_text)
    tgt = _letters(ctx.target)
    if bounds is None or src < MIN_SOURCE_CHARS or tgt == 0:
        return []
    low, high = bounds if src >= SHORT_SOURCE_CHARS else SHORT_SOURCE_BOUNDS
    ratio = tgt / src
    if ratio < low:
        severity: Severity = "critical" if ratio < low / 2 else "major"
        return [_issue("length_ratio", "omission", severity, ratio=round(ratio, 3), bounds=[low, high])]
    if ratio > high:
        return [_issue("length_ratio", "addition", "major", ratio=round(ratio, 3), bounds=[low, high])]
    return []


# ------------------------------------------------------------------ sentence alignment
_EN_ABBREVIATIONS = frozenset("""mr mrs ms dr prof st vs etc e.g i.e al fig figs ch chap vol vols no nos p pp
ed eds trans rev jr sr inc ltd co u.s u.k cf ibid op cit ff n nn sec art para approx est dept univ
jan feb mar apr jun jul aug sep sept oct nov dec""".split())
_EN_BOUNDARY_RE = re.compile(r"([.!?…]+)[\"'”’)\]]*\s+(?=[\"'“‘(\[]*[A-Z0-9])")
_AR_BOUNDARY_RE = re.compile(r"([.!?؟…]+)[\"'”’»)\]]*(?:\s+|$)")
SENTENCE_MIN_SOURCE = 3          # only units with 3+ source sentences are compared
SENTENCE_OMISSION_RATIO = 0.5    # target sentences <= 50% of source sentences
SENTENCE_ADDITION_FACTOR = 2.0   # target sentences >= 2x source + 2


def count_source_sentences(text: str) -> int:
    count = 0
    last = 0
    for m in _EN_BOUNDARY_RE.finditer(text):
        before = text[last:m.start()].split()
        word = before[-1].casefold().rstrip(".") if before else ""
        if m.group(1) == "." and (word in _EN_ABBREVIATIONS or re.fullmatch(r"[a-z]", word)
                                   or re.fullmatch(r"(?:[a-z]\.)+[a-z]", word)):
            continue
        count += 1
        last = m.end()
    return count + (1 if text[last:].strip() else 0)


def count_target_sentences(text: str) -> int:
    count = 0
    last = 0
    for m in _AR_BOUNDARY_RE.finditer(text):
        before = text[last:m.start()].split()
        word = before[-1] if before else ""
        if m.group(1) == "." and (len(word) <= 1 or word.casefold().rstrip(".") in _EN_ABBREVIATIONS
                                   or re.fullmatch(r"\d+", word) and m.end() < len(text)
                                   and text[m.end():m.end() + 1].isdigit()):
            continue
        if not text[last:m.start()].strip():
            last = m.end()
            continue
        count += 1
        last = m.end()
    return count + (1 if text[last:].strip() else 0)


def sentence_alignment(ctx: CheckContext) -> list[QAIssue]:
    src = count_source_sentences(ctx.unit.source_text)
    tgt = count_target_sentences(ctx.target)
    if src >= SENTENCE_MIN_SOURCE and tgt <= src * SENTENCE_OMISSION_RATIO:
        severity: Severity = "critical" if tgt <= src * 0.25 else "major"
        return [_issue("sentence_alignment", "omission", severity, source_sentences=src, target_sentences=tgt)]
    if tgt >= src * SENTENCE_ADDITION_FACTOR + 2:
        return [_issue("sentence_alignment", "addition", "major", source_sentences=src, target_sentences=tgt)]
    return []


# ------------------------------------------------------------------ repetition
REPEATED_PASSAGE_NGRAM = 8
_CONNECTORS = frozenset({"او", "و", "ثم", "،", ","})


def _ar_tokens(text: str) -> list[str]:
    return [t.casefold() for t in _WORD_RE.findall(normalize_arabic(text))]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[.!?؟…]+", text) if s.strip()]


def _dup_sentences(text: str) -> int:
    keys = [" ".join(_ar_tokens(s)) for s in _sentences(text)]
    keys = [k for k in keys if len(k.split()) >= 5]
    return sum(c - 1 for c in Counter(keys).values() if c > 1)


def _max_ngram_repeat(tokens: list[str], n: int) -> int:
    grams = Counter(tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1))
    return max(grams.values(), default=0)


def repetition(ctx: CheckContext) -> list[QAIssue]:
    source, target = ctx.unit.source_text, ctx.target
    src_tokens = _ar_tokens(source)
    tgt_tokens = _ar_tokens(target)
    source_repeats = _dup_sentences(source) > 0 or _max_ngram_repeat(src_tokens, 6) > 1
    if not source_repeats:
        if _dup_sentences(target) > 0 or _max_ngram_repeat(tgt_tokens, REPEATED_PASSAGE_NGRAM) > 1:
            return [_issue("repetition", "repetition", "major", reason="duplicated passage")]
    words = [t for t in tgt_tokens if not t.isdigit()]
    for n in (3, 2, 1):
        for i in range(len(words) - 2 * n + 1):
            first = words[i:i + n]
            for gap in (0, 1):
                j = i + n + gap
                if gap and (j > len(words) or words[i + n] not in _CONNECTORS):
                    continue
                second = words[j:j + n]
                if first == second and (n > 1 or gap or len(first[0]) >= 3) and \
                        not _source_has_adjacent_repeat(src_tokens):
                    return [_issue("repetition", "repetition", "minor", reason="adjacent repeated phrase",
                                   phrase=" ".join(first))]
    return []


def _source_has_adjacent_repeat(tokens: list[str]) -> bool:
    return any(tokens[i] == tokens[i + 1] for i in range(len(tokens) - 1))


# ------------------------------------------------------------------ terms and entities
_CONJ = ("و", "ف")
_PREP = ("ب", "ل", "ك")


def _ar_key(token: str) -> str:
    return normalize_arabic(token).replace("ة", "ه").replace("ى", "ي").casefold()


def _variants(token: str) -> set[str]:
    """The token with any of: conjunction (و/ف), preposition (ب/ل/ك), article (ال/لل) removed."""
    out = {token}
    stems = {token}
    for prefixes in (_CONJ, _PREP):
        stems |= {s[1:] for s in stems if len(s) > 2 and s.startswith(prefixes)}
    stems |= {s[2:] for s in stems if len(s) > 3 and s.startswith("لل")}
    stems |= {s[2:] for s in stems if len(s) > 3 and s.startswith("ال")}
    out |= stems
    return out


def _strip_article(token: str) -> str:
    return token[2:] if len(token) > 3 and token.startswith("ال") else token


def contains_term(target: str, term: str) -> bool:
    """True when ``term`` occurs in ``target`` allowing و/ف + ب/ل/ك + ال clitics."""
    term_tokens = [_strip_article(_ar_key(t)) for t in _WORD_RE.findall(term)]
    if not term_tokens:
        return True
    tokens = [_variants(_ar_key(t)) for t in _WORD_RE.findall(target)]
    n = len(term_tokens)
    for i in range(len(tokens) - n + 1):
        if all(term_tokens[k] in tokens[i + k] or term_tokens[k] in {_strip_article(v) for v in tokens[i + k]}
               for k in range(n)):
            return True
    return False


def _source_has(source: str, term: str) -> bool:
    words = [re.escape(w) for w in term.split()]
    if not words:
        return False
    pattern = r"(?<![\w])" + r"[\s-]+".join(words) + r"(?:s|es|'s|’s)?(?![\w])"
    return re.search(pattern, source, re.I) is not None


def _table_check(ctx: CheckContext, table: Mapping[str, str], check_id: str,
                 category: QACategory) -> list[QAIssue]:
    issues = []
    for src_term, tgt_term in table.items():
        if _source_has(ctx.unit.source_text, src_term) and not contains_term(ctx.target, tgt_term):
            issues.append(_issue(check_id, category, "major", term=src_term, expected=tgt_term))
    return issues


def terminology(ctx: CheckContext) -> list[QAIssue]:
    issues = _table_check(ctx, ctx.terms, "terminology", "terminology")
    # A ruled-out rendering is wrong even next to the locked form (e.g. a gloss in brackets).
    for src_term, variants in ctx.forbidden.items():
        if not _source_has(ctx.unit.source_text, src_term):
            continue
        for variant in variants:
            if contains_term(ctx.target, variant):
                issues.append(_issue("forbidden_variant", "terminology", "major", term=src_term,
                                     variant=variant, expected=ctx.terms.get(src_term)))
    return issues


def named_entity(ctx: CheckContext) -> list[QAIssue]:
    return _table_check(ctx, ctx.entities, "named_entity", "named_entity")


# ------------------------------------------------------------------ brackets and quotes
_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"))


def _bracket_state(text: str) -> dict[str, tuple[int, bool]]:
    state = {}
    for opening, closing in _PAIRS:
        depth, underflow = 0, False
        for ch in text:
            if ch == opening:
                depth += 1
            elif ch == closing:
                depth -= 1
                underflow = underflow or depth < 0
        state[opening + closing] = (depth, underflow)
    return state


def brackets(ctx: CheckContext) -> list[QAIssue]:
    src = _bracket_state(ctx.unit.source_text)
    tgt = _bracket_state(ctx.target)
    bad = [pair for pair, (depth, under) in tgt.items()
           if (depth != 0 or under) and (depth, under) != src[pair]]
    if not bad:
        return []
    return [_issue("brackets", "structure_formatting", "major", unbalanced=bad)]


def quotes(ctx: CheckContext) -> list[QAIssue]:
    src, tgt = ctx.unit.source_text, ctx.target
    problems = []
    if tgt.count("«") != tgt.count("»"):
        problems.append("guillemets")
    src_double = src.count('"') + src.count("“") + src.count("”")
    tgt_double = tgt.count('"') + tgt.count("“") + tgt.count("”")
    if tgt_double % 2 and not src_double % 2:
        problems.append("double_quotes")
    if not problems:
        return []
    return [_issue("quotes", "quotation", "minor", unbalanced=problems)]


# ------------------------------------------------------------------ polarity
_EN_NEGATION_RE = re.compile(
    r"\b(?:not|never|none|nobody|nothing|nowhere|neither|nor|cannot|without|hardly|seldom|rarely|"
    r"unless|no(?!\.\s*\d))\b|n['’]t\b", re.I)
# Arabic negation / privation cues (normalised, after و/ف clitic removal). Bare "ما" is not
# a cue: it is far more often relative/interrogative, which would hide dropped negations.
AR_NEGATION_CUES = frozenset({
    "لا", "لم", "لن", "ليس", "ليست", "ليسا", "ليستا", "ليسوا", "لست", "لسنا", "لستم", "لسن",
    "غير", "بغير", "لغير", "دون", "بدون", "لدون", "بلا", "عدم", "بعدم", "لعدم", "وعدم", "قلما", "نادرا",
    "لولا", "الا", "سوي", "كلا", "لئلا", "انعدام", "غياب", "بغياب", "يخلو", "تخلو", "خلو", "ولا", "ولم",
    "ولن", "فلا", "فلم", "فلن", "ما لم",
})


_NEGATION_STEMS = ("بدون", "دون", "غير", "بغير", "ليس", "لولا", "عدم", "بعدم", "انعدام", "غياب", "بغياب")
_PRONOUN_SUFFIXES = frozenset({"ه", "ها", "هم", "هما", "هن", "ي", "ك", "كم", "نا"})


def _negation_stem(token: str) -> bool:
    """'بدونها', 'غيرهم', 'لولاها': a negation/privation stem with a pronoun suffix."""
    return any(token.startswith(stem) and token[len(stem):] in _PRONOUN_SUFFIXES for stem in _NEGATION_STEMS)


def _ar_negations(text: str) -> int:
    count = 0
    for token in _WORD_RE.findall(normalize_arabic(text)):
        key = token.replace("ى", "ي")
        if key[:1] in _CONJ and len(key) > 2:
            key = key[1:] if key[1:] in AR_NEGATION_CUES or _negation_stem(key[1:]) else key
        if key in AR_NEGATION_CUES or _negation_stem(key):
            count += 1
    return count


def polarity(ctx: CheckContext) -> list[QAIssue]:
    """Negation-cue mismatch is a routing flag (catalogue §2.3): the semantic gate decides."""
    src = len(_EN_NEGATION_RE.findall(ctx.unit.source_text))
    tgt = _ar_negations(ctx.target)
    if src and not tgt:
        return [_issue("polarity", "polarity_error", "major", source_cues=src, target_cues=tgt,
                       reason="negation dropped")]
    if not src and tgt >= 2:
        return [_issue("polarity", "polarity_error", "minor", source_cues=src, target_cues=tgt,
                       reason="negation without a source cue")]
    return []


# ------------------------------------------------------------------ registry
@dataclass(frozen=True)
class RegisteredCheck:
    check_id: str
    categories: tuple[QACategory, ...]
    run: Check


DEFAULT_CHECKS: tuple[RegisteredCheck, ...] = (
    RegisteredCheck("empty_target", ("omission",), empty_target),
    RegisteredCheck("prompt_leakage", ("prompt_leakage",), leakage),
    RegisteredCheck("foreign_script", ("foreign_script",), foreign_script),
    RegisteredCheck("latin_not_in_source", ("foreign_script",), latin_not_in_source),
    RegisteredCheck("untranslated_english", ("untranslated_english",), untranslated_english),
    RegisteredCheck("numbers", ("number_error",), numbers),
    RegisteredCheck("footnote_markers", ("footnote_marker",), footnote_markers),
    RegisteredCheck("length_ratio", ("omission", "addition"), length_ratio),
    RegisteredCheck("sentence_alignment", ("omission", "addition"), sentence_alignment),
    RegisteredCheck("repetition", ("repetition",), repetition),
    RegisteredCheck("terminology", ("terminology",), terminology),
    RegisteredCheck("named_entity", ("named_entity",), named_entity),
    RegisteredCheck("brackets", ("structure_formatting",), brackets),
    RegisteredCheck("quotes", ("quotation",), quotes),
    RegisteredCheck("polarity", ("polarity_error",), polarity),
)


def _gloss_re(original: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(w) for w in original.split())
    return re.compile(r"\s*\(\s*" + body + r"\s*\)", re.IGNORECASE)


def strip_original_glosses(text: str, originals: Iterable[str]) -> str:
    """Remove the whitelisted ``(original)`` glosses (and only those) from ``text``."""
    for original in sorted(set(originals), key=len, reverse=True):
        if original.strip():
            text = _gloss_re(original).sub("", text)
    return text


def run_checks(ctx: CheckContext, checks: Iterable[RegisteredCheck] = DEFAULT_CHECKS) -> list[QAIssue]:
    """Run checks in registry order. An empty target short-circuits after ``empty_target``.

    Whitelisted first-mention glosses (``ctx.glossed_originals``) are removed first."""
    if ctx.glossed_originals:
        ctx = replace(ctx, target=strip_original_glosses(ctx.target, ctx.glossed_originals))
    issues: list[QAIssue] = []
    for check in checks:
        found = check.run(ctx)
        issues.extend(found)
        if check.check_id == "empty_target" and found:
            break
    return issues


__all__ = ["AR_NEGATION_CUES", "DEFAULT_CHECKS", "LENGTH_RATIO_BOUNDS", "RegisteredCheck", "brackets",
           "contains_term", "count_source_sentences", "count_target_sentences", "empty_target",
           "footnote_markers", "foreign_script", "latin_not_in_source", "leakage", "length_ratio",
           "named_entity", "normalize_digits", "numbers", "polarity", "quotes", "repetition", "run_checks",
           "sentence_alignment", "strip_original_glosses", "terminology", "untranslated_english"]
