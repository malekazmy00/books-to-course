"""Original term at its first mention (D-032 principle 3; learning-companion §2.3).

Post-translation and deterministic. Given an episode's Arabic text in reading order (the episode
intro, the accepted book text, the summary, ...) and the approved locked terms first mentioned in
the lesson (ConceptLayer ``LessonConceptList`` + the approved termbase), the first occurrence of
each term's Arabic form becomes ``المقابل العربي (original)``.

* Matching follows ``qa.checks.contains_term``: و/ف, ب/ل/ك and the article are allowed on each
  word (``وبالهيمنة`` is a mention of ``الهيمنة``).
* Idempotent: when the first occurrence is already followed by its original in brackets (inserted
  earlier, or a gloss the engine wrote) nothing is inserted; later glosses (e.g. the key-terms
  reminder, which always shows the original) do not count as the first mention.
* A term whose target form has Latin letters (do-not-translate acronyms) needs no gloss.

QA checks run on text with these glosses only with an explicit whitelist
(``CheckContext.glossed_originals``): ``strip_original_glosses`` removes exactly those glosses
before the checks see the text, so any OTHER Latin is still flagged.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .qa.checks import _WORD_RE, _ar_key, _gloss_re, _strip_article, _variants, strip_original_glosses

_LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class FirstMentionTerm:
    original: str  # source-language term (as approved in the termbase)
    target: str    # its approved Arabic form


@dataclass
class FirstMentionResult:
    texts: list[str]
    inserted: list[list[str]] = field(default_factory=list)  # per text: originals inserted there
    missing: list[str] = field(default_factory=list)          # terms never found in the texts


def find_term_spans(text: str, term: str) -> list[tuple[int, int]]:
    """Character spans of every occurrence of ``term`` in ``text`` (clitics/article allowed)."""
    term_tokens = [_strip_article(_ar_key(t)) for t in _WORD_RE.findall(term)]
    if not term_tokens:
        return []
    matches = list(_WORD_RE.finditer(text))
    keys = [_variants(_ar_key(m.group(0))) for m in matches]
    keys = [k | {_strip_article(v) for v in k} for k in keys]
    n = len(term_tokens)
    return [(matches[i].start(), matches[i + n - 1].end()) for i in range(len(matches) - n + 1)
            if all(term_tokens[k] in keys[i + k] for k in range(n))]


def find_term_span(text: str, term: str) -> tuple[int, int] | None:
    """Character span of the first occurrence of ``term`` in ``text`` (clitics/article allowed)."""
    spans = find_term_spans(text, term)
    return spans[0] if spans else None


def _ordered(terms: Iterable[FirstMentionTerm]) -> list[FirstMentionTerm]:
    """Longer terms first, so a term nested in a longer one ("التحليل المنظم" inside "تقنية التحليل
    المنظم") is not glossed inside it."""
    usable = [t for t in terms if t.target.strip() and not _LATIN.search(t.target)]
    return sorted(usable, key=lambda t: (-len(_WORD_RE.findall(t.target)), -len(t.target), t.original.casefold()))


def _walk(texts: list[str], terms: Iterable[FirstMentionTerm], *, insert: bool) -> tuple[list[list[str]], dict[str, str]]:
    """Find each term's first mention outside a longer term's mention; insert the gloss when asked."""
    protected: list[list[tuple[int, int]]] = [[] for _ in texts]
    inserted: list[list[str]] = [[] for _ in texts]
    state: dict[str, str] = {}
    for term in _ordered(terms):
        state[term.original] = "absent"
        for i, text in enumerate(texts):
            span = next((sp for sp in find_term_spans(text, term.target)
                         if not any(a < sp[1] and sp[0] < b for a, b in protected[i])), None)
            if span is None:
                continue
            gloss = _gloss_re(term.original).match(text, span[1])
            if gloss is not None:  # already glossed at its first mention (earlier pass, or the engine's own)
                protected[i].append((span[0], gloss.end()))
                state[term.original] = "glossed"
            elif insert:
                add = f" ({term.original})"
                texts[i] = f"{text[:span[1]]}{add}{text[span[1]:]}"
                protected[i] = [(a + len(add), b + len(add)) if a >= span[1] else (a, b) for a, b in protected[i]]
                protected[i].append((span[0], span[1] + len(add)))
                inserted[i].append(term.original)
                state[term.original] = "inserted"
            else:
                protected[i].append(span)
                state[term.original] = "not_glossed"
            break
    return inserted, state


def insert_first_mentions(texts: Sequence[str], terms: Iterable[FirstMentionTerm]) -> FirstMentionResult:
    out = list(texts)
    terms = list(terms)
    inserted, state = _walk(out, terms, insert=True)
    return FirstMentionResult(texts=out, inserted=inserted,
                              missing=[t.original for t in terms if state.get(t.original) == "absent"])


def first_mention_status(texts: Sequence[str], terms: Iterable[FirstMentionTerm]) -> dict[str, str]:
    """Per term: ``glossed`` (first mention carries the original), ``not_glossed`` or ``absent``."""
    return _walk(list(texts), terms, insert=False)[1]


__all__ = ["FirstMentionResult", "FirstMentionTerm", "find_term_span", "find_term_spans", "first_mention_status",
           "insert_first_mentions",
           "strip_original_glosses"]
