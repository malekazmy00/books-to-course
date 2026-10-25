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


def find_term_span(text: str, term: str) -> tuple[int, int] | None:
    """Character span of the first occurrence of ``term`` in ``text`` (clitics/article allowed)."""
    term_tokens = [_strip_article(_ar_key(t)) for t in _WORD_RE.findall(term)]
    if not term_tokens:
        return None
    matches = list(_WORD_RE.finditer(text))
    keys = [_variants(_ar_key(m.group(0))) for m in matches]
    keys = [k | {_strip_article(v) for v in k} for k in keys]
    n = len(term_tokens)
    for i in range(len(matches) - n + 1):
        if all(term_tokens[k] in keys[i + k] for k in range(n)):
            return matches[i].start(), matches[i + n - 1].end()
    return None


def insert_first_mentions(texts: Sequence[str], terms: Iterable[FirstMentionTerm]) -> FirstMentionResult:
    out = list(texts)
    inserted: list[list[str]] = [[] for _ in out]
    missing: list[str] = []
    for term in terms:
        if not term.target.strip() or _LATIN.search(term.target):
            continue
        for i, text in enumerate(out):
            span = find_term_span(text, term.target)
            if span is None:
                continue
            if _gloss_re(term.original).match(text, span[1]):
                break  # already glossed at its first mention (inserted earlier, or the engine's own gloss)
            out[i] = f"{text[:span[1]]} ({term.original}){text[span[1]:]}"
            inserted[i].append(term.original)
            break
        else:
            missing.append(term.original)
    return FirstMentionResult(texts=out, inserted=inserted, missing=missing)


__all__ = ["FirstMentionResult", "FirstMentionTerm", "find_term_span", "insert_first_mentions",
           "strip_original_glosses"]
