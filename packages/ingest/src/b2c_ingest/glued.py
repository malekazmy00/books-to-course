"""Glued-word repair (R3 S2, audit 01 §5.4.5; D-043): 'ofeach' -> 'of each'.

Where glued words come from (measured on the two real books, eval/reports/ingest-v2-gate.md):
pypdf joins words that the PDF separates by a positioning gap instead of a space character
(SAT: 1.4% of all tokens of its text layer and of the production v1 ParsedBook), and Docling
drops some em dashes (Gramsci: 'thatagainst' for 'that—against'). pdfium prints both correctly.

The repair is deterministic and only ever inserts spaces (letters, case and punctuation are
kept). A token is split only when every part is a word and one of two pieces of evidence holds:

* ``attested``: the book itself prints each adjacent pair of parts as two words (``of each``) at
  least ``ATTEST_MIN`` times and ``ATTEST_RATIO`` times more often than the glued token; a token
  that is itself a common English word (wordfreq Zipf >= ``KNOWN_ZIPF``: 'therein', 'within')
  additionally needs parts ``KNOWN_MARGIN`` Zipf more frequent than itself ('ofthe' 3.33 against
  'of'/'the' > 7).
* ``lexicon``: the token is absent from the pinned English lexicon (wordfreq 3.1.1, Zipf 0) and
  rare in the book (<= ``RARE_IN_BOOK`` uses), and its parts are a closed-class word plus common
  words (Zipf >= ``PART_ZIPF``), or all common words of >= 4 letters (Zipf >= ``PART_ZIPF_ALL``);
  never a prefix + word ('underexplored', 'nonpolar') nor a word + suffix.

A split whose adjacent parts the book itself prints hyphenated elsewhere (``national-popular``),
at least ``HYPHEN_MIN`` times, gets that hyphen instead of a space (D-045). Such glues come from
a compound broken at a line end whose hyphen the layout parser dropped ('decision-|making
community' -> 'decisionmaking'): there was never a space there. Mixed usage in the book
(SAT: 'decision-making' 12 times, 'decision making' 23) does not count against the hyphen. The
hyphen comes from the book, never from guessing: without that evidence the space stays.

In S2 an independent extraction of the same page (pdfium) can add a third piece of evidence,
``reference``: it prints the very same letters with an em or en dash between them (Docling loses
dashes at line ends). Then the dash itself is restored, also where the rules above stay silent;
a hyphen from the reference is restored only on a split the rules already chose.

Protected, never split by the rules (only the ``reference`` evidence overrides): tokens shorter than ``MIN_LEN``; ALL-CAPS and camelCase tokens
('NATO', 'McDonald', 'OutsideIn'); URLs, e-mail addresses and paths; any token the book prints
capitalised inside a sentence (proper nouns, technique names: 'Inman', 'Starbursting');
``protected`` terms (the book's termbase, single words or multi-word terms written together);
a capitalised token unless its first part is a closed-class word ('Theanalyst').
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable

SPLITTER_VERSION = "2"  # 2: hyphen kept where the book prints the compound hyphenated (D-045)
MIN_LEN = 5
MAX_PARTS = 3
ATTEST_MIN = 2
ATTEST_RATIO = 3
KNOWN_ZIPF = 2.5
KNOWN_MARGIN = 3.0
RARE_IN_BOOK = 2
PART_ZIPF = 3.5
PART_ZIPF_ALL = 4.0
HYPHEN_MIN = 2

# closed-class English words: the parts that most glued tokens are made of
FUNCTION_WORDS = frozenset("""
a an the of to in on at by for from with into onto upon and or but nor as is are was were be been
it its this that these those which who whom whose what when where while not no can may will would
should could must might shall has have had does did their they them there then than our
his him she her you your one each all any some more most such also only if very about
between through during against within without among per via
""".split())  # not 'us', 'me', 'he', 'we', 'my', 'do', 'so', 'no': mostly word pieces ('complex|us', 'in|he|ring')
# a capitalised token is split only after one of these ('Theanalyst', 'Ofeach'), never 'Be|is|singer'
CAP_FIRST = frozenset("""
the a an of in on at to for and but as it its this that these those there with from by each all some such
they if when while what which
""".split())
# a glue of prefix + word is usually a real compound the lexicon lacks
PREFIXES = frozenset("""
non un re pre post anti counter over under inter intra super sub multi self semi co de dis mis out
up down cross micro macro mega meta neo pro trans ultra extra hyper mid fore back after auto bio eco geo
socio psycho quasi pseudo proto poly mono bi tri
""".split())
SUFFIXES = frozenset("less ness ship hood like wise ward wards fold able ible ability ism ist ful ment dom ize".split())

_TOKEN = re.compile(r"[A-Za-z]+")
_CHUNK = re.compile(r"\S+")
_URLISH = re.compile(r"://|www\.|@|/|\\|\.[A-Za-z]{2,}")
_OPENERS = frozenset(".!?:;\"“”‘’'()[]•–—-\n")


def _sentence_initial(text: str, start: int) -> bool:
    """No word before ``start`` in the same sentence (a capital there says nothing about names)."""
    i = start - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if i >= 1 and text[i] == "." and text[i - 1].isupper() and (i < 2 or not text[i - 2].isalpha()):
        return False  # an initial: 'Mark R. Beissinger'
    return i < 0 or text[i] in _OPENERS


def zipf(word: str) -> float:
    """General-English frequency (wordfreq 3.1.1, pinned): Zipf scale, 3.0 = 1 per million words."""
    from wordfreq import zipf_frequency

    return zipf_frequency(word, "en")


@dataclass(frozen=True)
class GlueSplit:
    token: str
    parts: tuple[str, ...]
    rule: str  # "attested" | "lexicon" | "reference" (an em/en dash in the independent extraction)
    separators: tuple[str, ...] = ()  # between the parts; () = single spaces


Separators = dict[str, tuple[tuple[str, ...], tuple[str, ...]]]
_DASH_SEP = re.compile(r"[ \t]?(?:[\u2014\u2013][ \t]*(?:\r?\n)?|-)[ \t]?")


def reference_separators(text: str) -> Separators:
    """Dashes an independent extraction of the same page prints between words: lowercase glued
    form -> (parts, separators), e.g. 'thatagainst' -> (('that', 'against'), ('\u2014',)).

    An em or en dash counts inside a line or at its end (Docling loses exactly those at line
    ends); a hyphen only inside a line, since a line-end hyphen (pdfium U+FFFE, or '-' before a
    break) cannot say whether the compound keeps it. A glued form printed with two different
    separators on the page is dropped."""
    words = list(_TOKEN.finditer(text))
    found: dict[str, set[tuple[tuple[str, ...], tuple[str, ...]]]] = {}
    for i in range(len(words) - 1):
        for n in (2, 3):
            if i + n > len(words):
                break
            group = words[i:i + n]
            seps = tuple(text[a.end():b.start()] for a, b in zip(group, group[1:]))
            if not all(sep == " " or _DASH_SEP.fullmatch(sep) for sep in seps) or all(sep == " " for sep in seps):
                continue
            seps = tuple(sep.replace("\r", "").replace("\n", "").rstrip() if "\n" in sep else sep for sep in seps)
            parts = tuple(w.group().lower() for w in group)
            found.setdefault("".join(parts), set()).add((parts, seps))
    return {k: next(iter(v)) for k, v in found.items() if len(v) == 1}


class GlueSplitter:
    """Book statistics (token and bigram counts, mid-sentence capitals) plus the lexicon."""

    def __init__(self, texts: Iterable[str], *, lexicon: Callable[[str], float] = zipf,
                 protected: Iterable[str] = ()) -> None:
        self._lexicon = lexicon
        self._zipf_cache: dict[str, float] = {}
        self.tokens: Counter[str] = Counter()
        self.bigrams: Counter[tuple[str, str]] = Counter()
        self.hyphenated: Counter[tuple[str, str]] = Counter()  # 'national-popular' inside a line
        self.proper: set[str] = set()
        for text in texts:
            words = list(_TOKEN.finditer(text))
            for i, m in enumerate(words):
                low = m.group().lower()
                self.tokens[low] += 1
                if i + 1 < len(words):
                    gap = text[m.end():words[i + 1].start()]
                    if gap == " ":
                        self.bigrams[(low, words[i + 1].group().lower())] += 1
                    elif gap == "-":
                        self.hyphenated[(low, words[i + 1].group().lower())] += 1
                word = m.group()
                if len(word) >= MIN_LEN and word[0].isupper() and word[1:].islower() \
                        and not _sentence_initial(text, m.start()):
                    self.proper.add(low)
        self.protected = {re.sub(r"[^a-z]", "", t.lower()) for t in protected} - {""}

    def z(self, word: str) -> float:
        if word not in self._zipf_cache:
            self._zipf_cache[word] = self._lexicon(word)
        return self._zipf_cache[word]

    # ------------------------------------------------------------------ decision
    def _part_ok(self, part: str) -> bool:
        if len(part) == 1:
            return False  # not even 'a': 'a|critical' would invert 'acritical' (privative a-)
        if len(part) <= 3:
            return part in FUNCTION_WORDS
        return part in FUNCTION_WORDS or self.z(part) >= KNOWN_ZIPF or self.tokens[part] >= 3

    def _segmentations(self, low: str) -> list[tuple[str, ...]]:
        out: list[tuple[str, ...]] = []

        def walk(start: int, parts: tuple[str, ...]) -> None:
            if start == len(low):
                if len(parts) >= 2:
                    out.append(parts)
                return
            if len(parts) == MAX_PARTS:
                return
            for end in range(start + 1, len(low) + 1):
                part = low[start:end]
                if not self._part_ok(part):
                    continue
                if 0 < start and end < len(low) and len(part) < 3:
                    continue  # a short middle part: 'behavior|a|list', 'front|at|ions'
                walk(end, parts + (part,))

        walk(0, ())
        return out

    def _attested(self, low: str, parts: tuple[str, ...]) -> bool:
        pairs = [self.bigrams[(a, b)] for a, b in zip(parts, parts[1:])]
        if min(pairs) < ATTEST_MIN or min(pairs) < ATTEST_RATIO * self.tokens[low]:
            return False
        own = self.z(low)
        return own < KNOWN_ZIPF or min(self.z(p) for p in parts) - own >= KNOWN_MARGIN

    def _lexicon_ok(self, low: str, parts: tuple[str, ...]) -> bool:
        if self.z(low) > 0 or self.tokens[low] > RARE_IN_BOOK or self._inflected_word(low):
            return False
        if parts[0] in PREFIXES or parts[-1] in SUFFIXES:
            return False
        content = [p for p in parts if p not in FUNCTION_WORDS]
        if len(content) < len(parts):
            return all(self.z(p) >= PART_ZIPF for p in content)
        # two content words ('touch|lines' is a real compound): the book must print them apart once
        return all(len(p) >= 4 and self.z(p) >= PART_ZIPF_ALL for p in parts) and all(
            self.bigrams[(a, b)] >= 1 for a, b in zip(parts, parts[1:]))

    def decide(self, token: str) -> GlueSplit | None:
        if len(token) < MIN_LEN or not token.isascii() or not token.isalpha():
            return None
        capitalised = token[0].isupper()
        if not (token.islower() or (capitalised and token[1:].islower())):
            return None  # ALL-CAPS, camelCase
        low = token.lower()
        if low in self.protected or low in self.proper:
            return None
        options = sorted(self._segmentations(low), key=lambda ps: (len(ps), -min(self.z(p) for p in ps), ps))
        if not options:
            return None
        # only the preferred reading is tested: fewest parts, then the most frequent weakest part.
        # Falling back to a longer reading when it lacks evidence turns 'groupcannot' into
        # 'group can not' and 'thesedistinctions' into 'these distinct ions'.
        parts = options[0]
        if capitalised and (parts[0] not in CAP_FIRST or len(parts) > 2):
            return None
        rule = "attested" if self._attested(low, parts) else "lexicon" if self._lexicon_ok(low, parts) else None
        return GlueSplit(token, self._cut(token, parts), rule) if rule else None

    def _inflected_word(self, low: str) -> bool:
        """'ingrains', 'reframed': an inflection of a lexicon word that the lexicon lacks."""
        for suffix, repl in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("d", ""),
                             ("ing", ""), ("ing", "e"), ("ly", ""), ("er", ""), ("ers", "")):
            if low.endswith(suffix) and len(low) - len(suffix) >= 4:
                stem = low[: len(low) - len(suffix)] + repl
                if self.z(stem) >= 1.0:
                    return True
        return False

    def book_separators(self, parts: tuple[str, ...]) -> tuple[str, ...]:
        """Between each pair of parts: '-' where the book prints the pair hyphenated at least
        ``HYPHEN_MIN`` times, else a space."""
        low = [p.lower() for p in parts]
        return tuple("-" if self.hyphenated[(a, b)] >= HYPHEN_MIN else " " for a, b in zip(low, low[1:]))

    @staticmethod
    def _cut(token: str, parts: tuple[str, ...]) -> tuple[str, ...]:
        out, i = [], 0
        for p in parts:
            out.append(token[i:i + len(p)])
            i += len(p)
        return tuple(out)

    # ------------------------------------------------------------------ text
    def split(self, text: str, separators: Separators | None = None) -> tuple[str, list[GlueSplit]]:
        """Split glued tokens of ``text``; returns the new text and the splits made, in order.

        ``separators`` (``reference_separators`` of the same page from an independent parser)
        restores a dash the parser lost: a hyphen when it separates exactly the parts chosen
        here, an em/en dash also for a token the lexicon rules leave whole ('Gramsci—a')."""
        splits: list[GlueSplit] = []
        out: list[str] = []
        last = 0
        for chunk in _CHUNK.finditer(text):
            if _URLISH.search(chunk.group()):
                continue
            for m in _TOKEN.finditer(chunk.group()):
                start, end = chunk.start() + m.start(), chunk.start() + m.end()
                # a letter run glued to digits ('3the', 'p12') is not a word token; one next to a hyphen
                # is a piece of a hyphenated or line-broken word ('notwith - standing', 'sat- isfaction')
                before, after = text[max(0, start - 2):start], text[end:end + 2]
                if (before[-1:].isdigit() or after[:1].isdigit() or "-" in before or "-" in after
                        or "\u00ad" in before + after):
                    continue
                token = m.group()
                ref = (separators or {}).get(token.lower())
                found = self.decide(token)
                if ref and (found is None or ref[0] == tuple(p.lower() for p in found.parts)) \
                        and any(sep.strip() in ("\u2014", "\u2013") for sep in ref[1]):
                    # the independent extraction prints these exact letters with an em/en dash
                    # between them: that is evidence on its own, names and terms included
                    found = GlueSplit(token, self._cut(token, ref[0]), found.rule if found else "reference", ref[1])
                elif found is not None and ref and ref[0] == tuple(p.lower() for p in found.parts):
                    found = GlueSplit(found.token, found.parts, found.rule, ref[1])
                if found is None:
                    continue
                seps = found.separators or self.book_separators(found.parts)
                if not found.separators and "-" in seps:
                    found = GlueSplit(found.token, found.parts, found.rule, seps)
                out.append(text[last:start])
                out.append(found.parts[0] + "".join(sep + part for sep, part in zip(seps, found.parts[1:])))
                last = end
                splits.append(found)
        out.append(text[last:])
        return "".join(out), splits


__all__ = ["HYPHEN_MIN", "GlueSplit", "GlueSplitter", "SPLITTER_VERSION", "Separators", "reference_separators", "zipf"]
