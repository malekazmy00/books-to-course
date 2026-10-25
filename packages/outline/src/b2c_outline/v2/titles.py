"""Display titles: clean OCR/layout heading text, and detect titles that are still malformed.

Deterministic and book-local: the only lexicon is the book's own body text (``TitleVocab``), so
"critic al" becomes "critical" only when the book uses "critical". Bookmark titles are usually
clean already; layout headings are where letter-spacing (tracked small caps), lowercase small
caps, ALL CAPS and multi-line titles come from (audit 02 P5).
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
_ALPHA_TOKEN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ()]+$")
# lowercased in a title unless first/last or after a colon (Chicago-style title case)
MINOR_WORDS = frozenset(
    "a an the and but or nor for so yet of in on at to by as via vs per from into onto upon with without "
    "over under than".split())
# OCR splits words before these endings ("critic al", "gramsci an"); joined only into a book word
_SUFFIXES = frozenset("al als an ans ic ics ism isms ist ists ity ion ions tion tions ive ous ment ments ness "
                      "ed er ers es ing ly s".split())
_ROMAN_CONTEXT = frozenset({"part", "chapter", "volume", "book", "appendix", "section"})
_TOC_LOCATOR = re.compile(r"\[\s*\d{1,4}\s*\]")
_NUMBERED = re.compile(r"(?:^|\s)\d{1,2}(?:\.\d{1,2})+\.?\s+[A-Z]")
MAX_TITLE_CHARS = 160
LEXICON_ZIPF = 2.5  # 'whither' 2.52; non-words built from fragments ('ither', 'anorganic') are < 2


def english_zipf(word: str) -> float:
    """General-English frequency (wordfreq 3.1.1, pinned): Zipf scale, 3.0 = 1 per million words."""
    from wordfreq import zipf_frequency

    return zipf_frequency(word, "en")


@dataclass
class TitleVocab:
    """Word counts of the book body (lowercased), and how often each word is printed in capitals.

    ``lexicon`` (general-English Zipf frequency) only backs up a join that a stray letter already
    marks as letter-spacing ('w(h) i ther' -> 'w(h)ither') when the book body never uses the word.
    """
    words: Counter = field(default_factory=Counter)
    upper: Counter = field(default_factory=Counter)
    lexicon: Callable[[str], float] | None = None

    @classmethod
    def from_texts(cls, texts: Iterable[str], lexicon: Callable[[str], float] | None = None) -> "TitleVocab":
        vocab = cls(lexicon=lexicon)
        for text in texts:
            for w in _WORD.findall(text):
                vocab.words[w.lower()] += 1
                if len(w) > 1 and w.isupper():
                    vocab.upper[w.lower()] += 1
        return vocab

    def count(self, word: str) -> int:
        return self.words.get(word.lower(), 0)

    def is_word(self, word: str) -> bool:
        return len(word) >= 3 and self.count(word) >= 2

    def is_acronym(self, word: str) -> bool:
        low = word.lower()
        return self.upper.get(low, 0) >= 2 and self.upper[low] * 2 >= self.words.get(low, 0)


def _letters(token: str) -> str:
    return re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", token)


def _is_fragment(token: str, vocab: TitleVocab) -> bool:
    if not _ALPHA_TOKEN.match(token):
        return False
    letters = _letters(token)
    if not letters:
        return False
    if len(letters) == 1:
        return letters.lower() not in ("a", "i") or "(" in token
    return len(letters) <= 4 and vocab.count(letters) == 0


def _join_single_letter_runs(tokens: list[str]) -> list[str]:
    """'c o u n t e r c u l t u r e', 'A C K N O W L ED G M EN T S': one tracked word."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        j = i
        singles = 0
        while j < len(tokens) and re.fullmatch(r"[A-Za-z]{1,2}", tokens[j]):
            singles += len(tokens[j]) == 1
            j += 1
        if j - i >= 3 and singles >= 3:
            out.append("".join(tokens[i:j]))
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return out


def _join_fragments(tokens: list[str], vocab: TitleVocab) -> list[str]:
    """Merge a fragment with its neighbours when the letters form a word of the book."""
    changed = True
    while changed:
        changed = False
        best: tuple[int, int, int] | None = None  # (length, i, j)
        for i in range(len(tokens)):
            for j in range(i + 1, min(len(tokens), i + 5)):
                window = tokens[i:j + 1]
                if not all(_ALPHA_TOKEN.match(t) for t in window):
                    break
                frag = any(_is_fragment(t, vocab) for t in window)
                suffix = (j == i + 1 and len(_letters(tokens[i])) >= 3 and tokens[j].lower() in _SUFFIXES)
                if not (frag or suffix):
                    continue
                joined = "".join(_letters(t) for t in window)
                stray = any((len(_letters(t)) == 1 and t.islower() and t != "a") or "(" in t for t in window)
                known = vocab.is_word(joined) or (
                    frag and stray and vocab.lexicon is not None and len(joined) >= 5
                    and vocab.lexicon(joined.lower()) >= LEXICON_ZIPF)
                if known and (best is None or len(joined) > best[0]):
                    best = (len(joined), i, j)
        if best is not None:
            _, i, j = best
            tokens = [*tokens[:i], "".join(tokens[i:j + 1]), *tokens[j + 1:]]
            changed = True
    return tokens


def _case_word(word: str, position: int, after_colon: bool, last: bool, vocab: TitleVocab | None) -> str:
    if not re.search(r"[A-Za-z]", word):
        return word
    letters = _letters(word)
    if vocab is not None and word.isupper() and len(letters) > 1 and vocab.is_acronym(letters):
        return word
    low = word.lower()
    if position > 0 and not after_colon and not last and low in MINOR_WORDS:
        return low
    parts = low.split("-")
    return "-".join(p[:1].upper() + p[1:] if p and p not in MINOR_WORDS or k == 0 else p
                    for k, p in enumerate(parts))


def _recase(text: str, vocab: TitleVocab | None) -> str:
    letters = _letters(text)
    if len(letters) <= 3 or not (letters.islower() or letters.isupper()):
        return text
    tokens = text.split(" ")
    words = [k for k, t in enumerate(tokens) if re.search(r"[A-Za-z]", t)]
    out = []
    after_colon = False
    for k, token in enumerate(tokens):
        if re.search(r"[A-Za-z]", token):
            out.append(_case_word(token, words.index(k), after_colon, k == words[-1], vocab))
        else:
            out.append(token)
        after_colon = token.endswith(":") or token in (":", "—", "–", "·", "•")
    return " ".join(out)


def clean_title(text: str, vocab: TitleVocab | None = None) -> str:
    """One clean display line: whitespace collapsed, letter-spacing repaired, small caps / ALL CAPS
    re-cased (words the book prints in capitals stay so). Mixed-case titles keep their casing."""
    vocab = vocab or TitleVocab()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=\w) \((s|es)\)", r"(\1)", text)
    tokens = _join_single_letter_runs(text.split(" ")) if text else []
    tokens = _join_fragments(tokens, vocab)
    text = re.sub(r"\s+([:;,?!])", r"\1", " ".join(tokens))
    return _recase(text, vocab)


def malformed_title(text: str) -> list[str]:
    """Reasons a display title is not acceptable (empty list = clean)."""
    reasons: list[str] = []
    if not text.strip():
        return ["empty"]
    if "\n" in text or "\r" in text:
        reasons.append("multiline")
    if len(text) > MAX_TITLE_CHARS:
        reasons.append("too_long")
    if _TOC_LOCATOR.search(text) or len(_NUMBERED.findall(text)) >= 2:
        reasons.append("toc_like")
    tokens = re.split(r"\s+", text.strip())
    spaced = False
    for k, token in enumerate(tokens):
        core = token.strip(".,;:!?\"'“”‘’()[]")
        single = len(core) == 1 and core.isalpha()
        if single and k + 1 < len(tokens):
            nxt = tokens[k + 1].strip(".,;:!?\"'“”‘’()[]")
            if len(nxt) == 1 and nxt.isalpha():
                spaced = True
        if single and core.islower() and core != "a":
            previous = tokens[k - 1].lower().strip(".,:") if k else ""
            if not (core == "i" and previous in _ROMAN_CONTEXT):
                spaced = True
    if spaced:
        reasons.append("letter_spacing")
    first = re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", text)
    if first is not None and first.group(0).islower():
        reasons.append("lowercase_start")
    return reasons


__all__ = ["MINOR_WORDS", "TitleVocab", "clean_title", "english_zipf", "malformed_title"]
