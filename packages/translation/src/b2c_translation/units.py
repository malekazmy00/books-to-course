"""ParsedBook -> TranslationUnits (D-028; docs/proposals/2026-10-24-translation-rebuild.md §3-4).

Deterministic and pure: the same ParsedBook always gives the same units, nothing is read
from or written to the outside world, and no model is involved.

Pipeline (each step records what it changed in ``TranslationUnit.normalization``):

1. **Character normalization** (``unicode_normalized``): Latin ligatures and full-width
   ASCII are mapped with NFKC *per character*; exotic spaces become a plain space; soft
   hyphens, zero-width and control characters are removed. NFKC is deliberately not applied
   to the whole string because it rewrites superscripts, fractions and ™ (``x²`` -> ``x2``,
   ``½`` -> ``1⁄2``), which would change the meaning of numbers.
   ``ocr_punct_spacing``: ``word ,`` / ``word .`` / ``Oliver ’ s`` are closed up; spaced
   ellipses (``. . .``) are left alone.
2. **Letter-spaced OCR repair** (``ocr_letter_spacing``): small-caps and tracked headings
   come out of PDF extraction as ``the art i cul at i on of s truggl es``. A run of tokens is
   merged only if (a) the run contains at least one *fragment* — a single letter other than
   ``a``/``A``/``I``, or a token that is not itself attested in the book (< 3 occurrences),
   and (b) the merged form is attested un-spaced elsewhere in the same book (>= 2
   occurrences, >= 3 letters; 2-letter results need >= 20). Rule (a) is what keeps real
   words apart (``in to``, ``some times``, ``Plan B is`` are never merged); rule (b) is the
   "joined form is clearly a word" test, using the book itself as the dictionary as the
   audit catalogue §3 recommends, so domain terms (``trasformismo``) are covered and nothing
   is merged into a word the book never uses.
3. **Line structure inside a block**: hard-wrapped lines are re-joined; a line break is kept
   as a paragraph boundary after terminal punctuation, before a new list/note item or a
   numbered heading, and after short heading-like lines. ``dehyphenated``: ``analy-\\nsis``
   becomes ``analysis`` when the joined word is attested in the book more often than the
   hyphenated form; otherwise the hyphen is kept (``re-\\nported`` -> ``re-ported``).
4. **Classification** of every chunk into ``TranslationUnitKind`` from ``Block.type`` plus
   textual heuristics (running heads, page numbers, e-book navigation labels, TOC runs,
   back-of-book index locators, notes/bibliography/Ibid., captions, inline headings) and a
   neighbour-smoothing pass for notes and bibliography lists.
5. **Footnote markers**: reference numbers glued to words/punctuation (``story.3``,
   ``nature.' 48``, ``(see Figure 1.6).11``, ``in 1921.5 They``) are removed from the text
   into ``footnote_markers``. Candidates must continue the running note sequence of the
   book (notes restart per chapter), which is what separates them from years, quantities,
   list numbers and page references.
6. **Page-break joins** (``joined_page_break``): a prose/list/note chunk without terminal
   punctuation that is continued on the next page (or the one after) — next chunk starts
   in lower case, or the first ends with a comma/hyphen/function word — becomes one
   logical paragraph; the unit keeps all block ids and pages.
7. **Segmentation**: logical paragraphs longer than ``max_unit_chars`` are split into
   sentences with pySBD 0.3.4 (rule-based, Golden-Rules tested, handles abbreviations),
   then consecutive sentences are packed up to ``max_unit_chars``. A unit always ends at a
   footnote marker, so restoring markers is "append in order". A sentence longer than
   ``2 * max_unit_chars`` is split at clause boundaries (``split_clause``).

``build_units_with_provenance`` also returns, per block, the exact pieces of text each unit
took from it, which the property tests use to prove every character is covered once.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from b2c_contracts import Block, ParsedBook, TranslationUnit

DEFAULT_MAX_UNIT_CHARS = 400

# ----------------------------------------------------------------------------- regexes
_SPACES = re.compile("[   -   　\t\x0b\x0c]")
_INVISIBLE = re.compile("[­​‌‍⁠﻿\x00-\x08\x0e-\x1f\x7f]")
_NFKC_CHARS = re.compile("[ﬀ-ﬆ！-～]")
_MULTISPACE = re.compile(r"[ ]{2,}")

_SPACE_BEFORE_PUNCT = re.compile(r"(?<=[A-Za-z\)\]’”])[ ]+(?=[,;:!?](?:\s|$))")
_SPACE_BEFORE_PERIOD = re.compile(r"(?<=[A-Za-z])[ ]+\.(?=\s|$)(?![ ]\.)")
_SPACED_APOSTROPHE = re.compile(r"(?<=[A-Za-z]) ([’']) (?=s\b)")

_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
_HYPHENATED = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+-[A-Za-zÀ-ÖØ-öø-ÿ]+")
_TOKEN = re.compile(r"^([(\[“\"'‘]?)([A-Za-zÀ-ÖØ-öø-ÿ]+)([.,;:!?)\]”\"'’]*)$")

_TERMINAL = re.compile(r"[.!?…:;][’”\"')\]]*$")
_SENTENCE_END = re.compile(r"[.!?…][’”\"')\]]*$")
_ABBREV_END = re.compile(
    r"(?:\b(?:e\.g|i\.e|etc|vs|cf|al|Dr|Mr|Mrs|Ms|Prof|Jr|Sr|St|No|no|Nos|vol|Vol|pp?|ed|eds|"
    r"trans|Fig|fig|ch|Ch|chap|Gen|Col|Lt|Rev|Inc|Ltd|Co|U\.S|U\.K)\.|\b[A-Z]\.)$")
_CONTINUATION_WORDS = frozenset(
    "a an the of to in on at by for from with and or but nor as that which who whom whose "
    "than into onto upon about between among through within without under over after before "
    "during if when while because although though whether is are was were be been being has "
    "have had its their his her our your this these those such not also very more most".split())

_NOTE_LABEL = re.compile(r"^(\d{1,3})\.\s+(?=\S)")
_BULLET = re.compile(r"^(?:[•▪■●◦‣∙·]\s*|[-–—]\s+|\(?[a-z]\)\s+|\(\d{1,2}\)\s+|\d{1,2}\)\s+)")
_NUMBERED_HEADING = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})+[a-z]?\s+\S|(?:chapter|part|appendix)\s+[\divxlc]+\b)", re.I)
_CAPTION = re.compile(r"^(?:Figure|Fig\.|Table|Chart|Exhibit|Map|Plate)\s+\d+(?:\.\d+)*[A-Za-z]?\s+(?P<next>\S)")
_SOURCE_LINE = re.compile(r"^Source:\s")
_PAGE_NUMBER = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,7}|page\s+\d{1,4})$", re.I)
_PRINTER_KEY = re.compile(r"^(?:\d{1,2}\s+){4,}\d{1,2}$")
_NAV_LABELS = frozenset({
    "back to figure", "back to table", "back to text", "descriptions of images and figures",
    "description of images and figures",
})
_SECTION_WORDS = {  # keys without spaces: letter-spaced headings ("N O T ES") match too
    "notes": "notes", "endnotes": "notes", "footnotes": "notes",
    "bibliography": "bibliography", "references": "bibliography", "workscited": "bibliography",
    "selectbibliography": "bibliography", "index": "index",
    "contents": "toc", "tableofcontents": "toc", "figures": "toc", "listoffigures": "toc",
    "tables": "toc", "listoftables": "toc",
}

_TOC_PAGE = re.compile(r"[A-Za-z)™®’”'\"?]\s+(?:\d{1,3}|[ivxlc]{1,6})$")
_TOC_BRACKET = re.compile(r"\[\s*\d{1,3}\s*\]$")
_LOCATOR = re.compile(r"[,;:]\s*\d{1,4}(?:[-–]\d{1,4})?(?:n\d{1,3})?(?=[,;\s.]|$)")
_LOCATORS_ONLY = re.compile(r"^[\d\s,;:–\-n]+$")
_INDEX_SPLIT = re.compile(r"(?<=\d) (?=[A-Za-zÀ-ÿ‘'“\"])")

_YEAR = re.compile(r"\b(?:1[5-9]\d\d|20\d\d)[a-z]?\b")
_PUB = re.compile(r"\(\s*[A-Z][^():]{1,60}:\s*[^()]{2,120}\)|[A-Z][a-z]+(?:,\s*[A-Z]{2})?:\s*[A-Z][^.:]{1,60},\s*(?:1[5-9]|20)\d\d")
_ENDPAGE = re.compile(r"[,:]\s*(?:pp?\.\s*)?(?:\d{1,3}|[xivlc]{1,6})(?:[-–]\d{1,4})?(?:,\s*\d{1,4}(?:[-–]\d{1,4})?)*\.?$")
_CITE_MARK = re.compile(
    r"\b(?:ed|eds|trans|vol|vols|no|pp|n\.d|cf|op\. cit)\.|\bet al\b|\b[Ii]bid\b|https?://|doi\.org|\bdoi:"
    r"|\b(?:Translated|Edited|Introduction|Foreword) by\b|\bPress\b|\bUniversity\b|\bJournal\b|\bReview\b|§\s?\d")
_QUOTED_TITLE = re.compile(r"[“‘'\"][A-Z][^”’'\"]{3,}[”’'\"]")
_AUTHOR_LEAD = re.compile(r"^(?:[A-Z][A-Za-zÀ-ÿ'’\-]+,\s+[A-Z][A-Za-zÀ-ÿ.\- ]{0,40}[.,]|[A-Z]{2,6},\s+\d|\.\s+['‘“A-Z])")
_IBID = re.compile(r"^(?:[Ii]bid\.?|[Ii]d\.|[Oo]p\. cit\.|[Ll]oc\. cit\.)(?=[\s,;.]|$)")
_QUOTED_SPAN = re.compile(r"“[^”]*”|‘[^’]*’|(?<![\w])'[^']*'(?![\w])|\"[^\"]*\"")
_PARENS = re.compile(r"\([^()]*\)")
_URL = re.compile(r"https?://\S+|www\.\S+|\S+\.(?:com|org|edu|gov|net)\S*")
_CITATION_WORDS = frozenset(
    "edited translated trans accessed retrieved available press university edition introduction "
    "foreword volume volumes diss dissertation thesis paper papers series also cited quoted "
    "paraphrased reprinted published unpublished revised mimeo online last".split())

_MONTHS = frozenset("january february march april may june july august september october "
                    "november december".split())
_ABBREV_BEFORE_NUMBER = frozenset("p pp no nos vol vols ch chap fig figs n nn para sec art ed eds pt §".split())
_QUANTITY_WORDS = frozenset(
    "percent per points point times years days months weeks hours people persons million billion "
    "thousand hundred of to more less".split())
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

_CLAUSE_BOUNDARY = re.compile(r"(?<=[,;:])\s+|\s+(?=[—–]\s)|(?<=\s[—–])\s+")


# ----------------------------------------------------------------------------- data
@dataclass
class _Chunk:
    block: Block
    order: int  # position of the block in the book
    text: str
    kind: str = "prose"
    tags: list[str] = field(default_factory=list)
    markers: list[tuple[int, str]] = field(default_factory=list)  # (offset in text, marker)
    single_line: bool = True
    first_in_block: bool = False
    last_in_block: bool = False
    labelled: bool = False  # starts with a note label / bullet / numbered item
    notes_context: bool = False
    ends_full_line: bool = False  # hard-wrapped block whose last line runs to the margin

    @property
    def pages(self) -> list[int]:
        return [r.page_no for r in self.block.refs]


@dataclass
class _Paragraph:
    chunks: list[_Chunk]
    kind: str
    tags: list[str]


@dataclass
class UnitBuild:
    """Units plus provenance used for testing and evaluation."""

    units: list[TranslationUnit]
    coverage: dict[str, list[tuple[str, str]]]  # block_id -> [(unit_id, text piece), ...]
    marker_blocks: list[tuple[str, str]]  # (marker, block_id) in reading order
    stats: Counter


# ----------------------------------------------------------------------------- vocabulary
class _Vocab:
    def __init__(self, texts: list[str]):
        words: Counter = Counter()
        compounds: Counter = Counter()
        for t in texts:
            t = _URL.sub(" ", t)  # 'www.structuredanalytic...' must not attest glued words
            words.update(w.lower() for w in _WORD.findall(t))
            compounds.update(w.lower() for w in _HYPHENATED.findall(t))
        self.words = words
        self.compounds = compounds

    def count(self, w: str) -> int:
        return self.words.get(w.lower(), 0)

    def is_fragment(self, token: str) -> bool:
        if len(token) == 1:
            return token not in ("a", "A", "I")
        return self.count(token) < 3

    def is_word(self, merged: str) -> bool:
        n = self.count(merged)
        return n >= 20 if len(merged) == 2 else (len(merged) >= 3 and n >= 2)


# ----------------------------------------------------------------------------- normalization
def _normalize_chars(text: str) -> tuple[str, bool]:
    original = text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _NFKC_CHARS.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), text)
    text = _SPACES.sub(" ", text)
    text = _INVISIBLE.sub("", text)
    text = "\n".join(_MULTISPACE.sub(" ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{2,}", "\n", text).strip()
    changed = text != original and _squash(text) != _squash(original)
    return text, changed


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _fix_punct_spacing(line: str) -> str:
    line = _SPACE_BEFORE_PUNCT.sub("", line)
    line = _SPACE_BEFORE_PERIOD.sub(".", line)
    return _SPACED_APOSTROPHE.sub(r"\1", line)


def _repair_letter_spacing(line: str, vocab: _Vocab) -> str:
    tokens = line.split(" ")
    if len(tokens) < 2:
        return line
    parsed = [_TOKEN.match(t) for t in tokens]
    n = len(tokens)
    # best[i] = (score, merges) for tokens[i:]; score rewards fragments absorbed into words
    best: list[tuple[int, list[tuple[int, int, str]]]] = [(0, [])] * (n + 1)
    for i in range(n - 1, -1, -1):
        cand = (best[i + 1][0], best[i + 1][1])
        if parsed[i] is not None:
            core = parsed[i].group(2)
            frags = 1 if vocab.is_fragment(core) else 0
            singles = 1 if len(core) == 1 and frags else 0
            nonfrag = 1 - frags
            for j in range(i + 1, min(n, i + 30)):
                p = parsed[j]
                if p is None:
                    break
                if p.group(1) or (j > i and parsed[j - 1].group(3)):
                    break  # punctuation may only open the first / close the last token
                core += p.group(2)
                f = vocab.is_fragment(p.group(2))
                frags += f
                singles += 1 if f and len(p.group(2)) == 1 else 0
                nonfrag += 0 if f else 1
                # without a stray single letter, every token must be unattested on its own
                strict_ok = singles or (nonfrag == 0 and vocab.count(core) >= 3)
                if frags and strict_ok and vocab.is_word(core):
                    score = frags * 10 - 1 + best[j + 1][0]
                    if score > cand[0]:
                        cand = (score, [(i, j + 1, core), *best[j + 1][1]])
        best[i] = cand
    merges = best[0][1]
    # A repair must absorb the whole letter-spaced run: if a stray fragment of <= 2 letters is
    # left right next to a merge ("N O T ES" -> "NOT ES"), the merge is a guess; drop it.
    absorbed = {k for i, j, _ in merges for k in range(i, j)}

    def stray(k: int) -> bool:
        if k < 0 or k >= n or k in absorbed or parsed[k] is None:
            return False
        core = parsed[k].group(2)
        return len(core) <= 2 and vocab.is_fragment(core)

    merges = [(i, j, core) for i, j, core in merges
              if not (stray(i - 1) and not parsed[i].group(1)) and not (stray(j) and not parsed[j - 1].group(3))]
    if not merges:
        whole = _whole_line_letter_run(parsed, tokens)
        return line if whole is None else whole
    out, pos = [], 0
    for i, j, core in merges:
        out.extend(tokens[pos:i])
        out.append(parsed[i].group(1) + core + parsed[j - 1].group(3))
        pos = j
    out.extend(tokens[pos:])
    return " ".join(out)


def _whole_line_letter_run(parsed, tokens: list[str]) -> str | None:
    """'A C K N O W L ED G M EN T S' on a line of its own: tracked small caps of one word.

    Used only when the book never spells the word un-spaced (so rule (b) cannot apply) and
    the *whole line* is the run: >= 4 tokens, all of 1-2 letters, >= 3 single letters and at
    least one 2-letter token (the tracking artefact that pairs letters), one letter case,
    and not an alphabetic sequence such as matrix column labels 'A B C D E'."""
    if not 4 <= len(tokens) <= 20 or any(p is None or p.group(1) or p.group(3) for p in parsed):
        return None
    cores = [p.group(2) for p in parsed]
    singles = sum(len(c) == 1 for c in cores)
    if any(len(c) > 2 for c in cores) or singles < 3:
        return None
    if not any(len(c) == 2 for c in cores) and singles < 6:
        return None  # 'x y z' stays; 'c o n t e n t s' (>= 6 single letters) is a tracked word
    word = "".join(cores)
    if not (word.isupper() or word.islower()):
        return None
    letters = word.lower()
    if all(ord(b) - ord(a) == 1 for a, b in zip(letters, letters[1:])):
        return None
    return word


# ----------------------------------------------------------------------------- lines -> chunks
def _starts_item(line: str, last_label: list[int]) -> bool:
    m = _NOTE_LABEL.match(line)
    if m:
        num = int(m.group(1))
        if num == 1 or num == last_label[0] + 1:
            return True
    return bool(_BULLET.match(line) or _NUMBERED_HEADING.match(line))


def _is_soft_wrap(prev: str, nxt: str, typical: float, last_label: list[int], in_note: bool = False) -> bool:
    if _starts_item(nxt, last_label):
        return False
    if prev.endswith("-") and nxt[:1].islower():
        return True
    if in_note and prev.endswith(":"):
        return True  # 'J. Doe, "How to Decide:' + subtitle on the next line
    if _CAPTION.match(prev) and nxt[:1].isupper() and prev[-1] not in ",(–—-/&" \
            and prev.rsplit(" ", 1)[-1].lower() not in _CONTINUATION_WORDS:
        return False  # a figure caption line is not continued by the body text below it
    if _TERMINAL.search(prev):
        return bool(_ABBREV_END.search(prev)) and len(prev) >= 0.6 * typical and not nxt[:1].isupper() \
            or bool(_ABBREV_END.search(prev)) and len(prev) >= 0.8 * typical
    if nxt[:1].islower():
        return True
    if prev[-1] in ",(–—-/&":
        return True
    last_word = prev.rsplit(" ", 1)[-1].lower()
    if last_word in _CONTINUATION_WORDS:
        return True
    return len(prev) >= 0.6 * typical


def _join_hyphen(left: str, right: str, vocab: _Vocab) -> tuple[str, bool]:
    """`left` ends with '-': join with `right` and decide whether the hyphen was a soft one."""
    lw = re.search(r"([A-Za-zÀ-ÿ]+)-$", left)
    rw = re.match(r"([A-Za-zÀ-ÿ]+)", right)
    if lw and rw and not re.search(r"[-/]\S*-$", left.rsplit(" ", 1)[-1]):
        joined = lw.group(1) + rw.group(1)
        compound = f"{lw.group(1)}-{rw.group(1)}".lower()
        if vocab.count(joined) >= 1 and vocab.count(joined) >= vocab.compounds.get(compound, 0):
            return left[:-1] + right, True
    return left + right, False


def _split_lines(text: str, typical: float, vocab: _Vocab) -> list[tuple[str, bool, list[str], bool]]:
    """Return [(chunk_text, single_line, tags, ends_with_full_line)] after re-joining wrapped lines."""
    lines = [line for line in text.split("\n") if line.strip()]
    out: list[tuple[str, bool, list[str], bool]] = []
    last_label = [0]
    cur, cur_lines, tags, last_line = "", 0, [], ""
    hard_wrapped = len(lines) >= 3

    def full(line: str) -> bool:
        return hard_wrapped and len(line) >= 0.6 * typical

    for line in lines:
        m = _NOTE_LABEL.match(line)
        if cur and _is_soft_wrap(last_line, line, typical, last_label, in_note=bool(_NOTE_LABEL.match(cur))):
            if cur.endswith("-") and line[:1].islower():
                cur, dehyph = _join_hyphen(cur, line, vocab)
                if dehyph:
                    tags.append("dehyphenated")
            else:
                cur = f"{cur} {line}"
            cur_lines += 1
        else:
            if cur:
                out.append((cur, cur_lines == 1, tags, full(last_line)))
            cur, cur_lines, tags = line, 1, []
            if m and (int(m.group(1)) == 1 or int(m.group(1)) == last_label[0] + 1):
                last_label[0] = int(m.group(1))
        last_line = line
    if cur:
        out.append((cur, cur_lines == 1, tags, full(last_line)))
    return out


def _is_wrapped_title(lines: list[str]) -> bool:
    """A heading block whose lines are one title broken over several lines."""
    lines = [ln for ln in lines if ln]
    if len(lines) < 2 or sum(len(ln) for ln in lines) > 200:
        return False
    if any(_SENTENCE_END.search(ln) for ln in lines):
        return False
    return not any(_NUMBERED_HEADING.match(ln) or _NOTE_LABEL.match(ln) for ln in lines[1:])


_INLINE_LABEL = re.compile(r"(?<=[.;)\]’”'\"]) (\d{1,3})\.\s+(?=\S)")


def _split_inline_notes(text: str) -> list[str]:
    """Split '140. Ibid., 12. 141. Ibid., 13.' into notes when labels form a run n, n+1, ...

    The first note may have lost its label in extraction ('SPN, 111. 186. SPN, 112. 187.
    Ibid.'); then at least two consecutive inline labels are required."""
    m0 = _NOTE_LABEL.match(text)
    found = [(m.start() + 1, int(m.group(1))) for m in _INLINE_LABEL.finditer(text)]
    cuts: list[int] = []
    if m0:
        expected = int(m0.group(1)) + 1
        for pos, num in found:
            if num == expected:
                cuts.append(pos)
                expected += 1
    else:
        for i, (pos, num) in enumerate(found):
            run = [pos]
            expected = num + 1
            for pos2, num2 in found[i + 1:]:
                if num2 == expected:
                    run.append(pos2)
                    expected += 1
            if len(run) >= 2:
                cuts = run
                break
    if not cuts:
        return [text]
    bounds = [0, *cuts, len(text)]
    return [text[a:b].strip() for a, b in zip(bounds, bounds[1:]) if text[a:b].strip()]


# ----------------------------------------------------------------------------- classification
def _section_word(text: str) -> str | None:
    key = re.sub(r"\s+", "", text.strip().strip(":.").lower())
    return _SECTION_WORDS.get(key)


def _prose_words(text: str) -> tuple[int, int]:
    t = _URL.sub(" ", text)
    t = _QUOTED_SPAN.sub(lambda m: " " if len(m.group(0).split()) <= 20 else m.group(0), t)
    t = _PARENS.sub(" ", t)
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]*", t)
    prose = [w for w in words if w[0].islower() and len(w) >= 4 and w.lower() not in _CITATION_WORDS]
    return len(prose), len(words)


def _citation_score(text: str) -> int:
    return sum(bool(p.search(text)) for p in (_YEAR, _PUB, _ENDPAGE, _CITE_MARK, _QUOTED_TITLE, _AUTHOR_LEAD))


def _is_bibliographic(text: str, *, notes_context: bool) -> bool:
    body = _NOTE_LABEL.sub("", text, count=1)
    prose, total = _prose_words(body)
    if _IBID.match(body):
        return prose <= 2
    if total == 0:
        return False
    low_prose = prose <= max(1, int(0.12 * total))
    score = _citation_score(body)
    return low_prose and score >= (1 if notes_context else 3)


def _toc_like(text: str) -> bool:
    if len(text) > 160 or not re.search(r"[A-Za-z]", text):
        return False
    if _SENTENCE_END.search(text) and not text.endswith("?"):
        return False
    return bool(_TOC_PAGE.search(text) or _TOC_BRACKET.search(text) or _NUMBERED_HEADING.match(text))


def _toc_candidate(c: _Chunk) -> bool:
    # a single-line heading block is a real heading, never part of a contents run
    if c.block.type == "heading" and c.first_in_block and c.last_in_block:
        return False
    return c.kind in ("prose", "heading", "list_item") and _toc_like(c.text)


def _index_like(text: str) -> bool:
    if _LOCATORS_ONLY.match(text):
        return bool(re.search(r"\d", text)) and not _PAGE_NUMBER.match(text.strip())
    n = len(_LOCATOR.findall(text))
    if n == 0 or _IBID.match(text) or text.rstrip().endswith(".") or _PUB.search(text):
        return False
    return n * 30 >= len(text)


def _heading_like(chunk: _Chunk) -> bool:
    t = chunk.text
    if not chunk.single_line or len(t) > 90 or len(t) < 2:
        return False
    if t[-1] in ".,;:" or not (t[0].isupper() or t[0].isdigit()):
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    if all(c.isupper() for c in letters):
        return len(letters) >= 3
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’\-]*", t) if len(w) >= 4]
    if not words:
        return len(t) <= 30
    caps = sum(w[0].isupper() for w in words) / len(words)
    return caps >= 0.6 and len(t) <= 60


def _content_kind(chunk: _Chunk) -> str:
    t = chunk.text
    btype = chunk.block.type
    if btype in ("page_header", "page_footer"):
        return "noise"
    # a bare number is a page number only at the edge of a block (inside a block it is
    # table/matrix content, which must not be dropped)
    edge = chunk.first_in_block or chunk.last_in_block
    if (_PAGE_NUMBER.match(t) and edge) or _PRINTER_KEY.match(t) or t.lower().strip(" .") in _NAV_LABELS:
        return "noise"
    if btype == "table":
        return "table_cell"
    if btype in ("caption", "figure"):
        return "caption"
    if btype == "equation":
        return "noise"
    if btype == "footnote":
        return "reference" if _is_bibliographic(t, notes_context=True) else "footnote"
    if btype == "heading" and chunk.first_in_block:
        return "heading"
    if _IBID.match(_NOTE_LABEL.sub("", t, count=1)):  # an Ibid. note, with or without comment
        return "reference" if _is_bibliographic(t, notes_context=True) else "footnote"
    if chunk.notes_context and _NOTE_LABEL.match(t):
        return "reference" if _is_bibliographic(t, notes_context=True) else "footnote"
    if _is_bibliographic(t, notes_context=False):
        return "reference"
    m = _CAPTION.match(t)
    if (m and m.group("next")[0].isupper() and len(t) <= 250) or (_SOURCE_LINE.match(t) and len(t) <= 250):
        return "caption"
    if btype == "list_item" or _BULLET.match(t) or (_NOTE_LABEL.match(t) and not chunk.single_line):
        return "list_item"
    if _heading_like(chunk) and not (chunk.last_in_block and chunk.first_in_block and btype != "heading"
                                     and len(t) > 40):
        return "heading"
    return "prose"


def _classify(chunks: list[_Chunk]) -> None:
    for c in chunks:
        c.kind = _content_kind(c)

    flow = [c for c in chunks if c.kind != "noise"]

    # TOC: runs of >= 3 toc-like chunks, or toc-like chunks right after a "Contents" heading.
    i = 0
    while i < len(flow):
        if _toc_candidate(flow[i]):
            j = i
            while j < len(flow) and (_toc_candidate(flow[j]) or flow[j].kind == "caption" and _toc_like(flow[j].text)):
                j += 1
            after_contents = i > 0 and _section_word(flow[i - 1].text) == "toc"
            if j - i >= 3 or after_contents:
                for c in flow[i:j]:
                    c.kind = "toc"
            i = max(j, i + 1)
        else:
            i += 1

    # Back-of-book index: locator-dense chunks, accepted when dense or next to another one.
    idx = [c.kind in ("prose", "list_item", "heading", "reference") and _index_like(c.text) for c in flow]
    for k, c in enumerate(flow):
        if not idx[k]:
            continue
        dense = len(_LOCATOR.findall(c.text)) >= 3
        near = (k > 0 and idx[k - 1]) or (k + 1 < len(flow) and idx[k + 1])
        locators_only = bool(_LOCATORS_ONLY.match(c.text))
        if (dense and not locators_only) or near:
            c.kind = "index"

    # Runs of sequentially numbered items ("162. ...", "163. ...") are endnotes.
    k = 0
    while k < len(flow):
        m = _NOTE_LABEL.match(flow[k].text)
        j = k + 1
        if m and flow[k].kind in ("prose", "list_item", "reference", "heading"):
            expected = int(m.group(1)) + 1
            while j < len(flow):
                mj = _NOTE_LABEL.match(flow[j].text)
                if not mj or int(mj.group(1)) != expected:
                    break
                expected += 1
                j += 1
            cited = sum(_citation_score(_NOTE_LABEL.sub("", c.text, count=1)) >= 2 for c in flow[k:j])
            # numbered method steps ("5. Analyze direct impacts.") are sequential too; endnotes
            # are told apart by their citations
            if j - k >= 2 and cited * 2 >= j - k:
                for c in flow[k:j]:
                    c.notes_context = True
                    if c.kind != "reference":
                        c.kind = "reference" if _is_bibliographic(c.text, notes_context=True) else "footnote"
        k = j

    # Runs of citation-shaped items with at least one unambiguous reference (bibliography,
    # Ibid., full citation) are reference lists: short forms such as "Doe, Title, 141." in
    # such a run are references too, although alone they would be too weak a signal.
    def citation_shaped(c: _Chunk) -> bool:
        if c.kind == "reference":
            return True
        if c.kind not in ("prose", "list_item") or len(c.text) > 600:
            return False
        return _is_bibliographic(c.text, notes_context=True)

    k = 0
    while k < len(flow):
        if not citation_shaped(flow[k]):
            k += 1
            continue
        j = k
        while j < len(flow) and citation_shaped(flow[j]):
            j += 1
        strong_run = j - k >= 3 and all(_citation_score(_NOTE_LABEL.sub("", c.text, count=1)) >= 2 for c in flow[k:j])
        if (j - k >= 2 and any(c.kind == "reference" for c in flow[k:j])) or strong_run:
            for c in flow[k:j]:
                if c.kind != "reference":
                    c.kind = "reference"
                    c.tags.append("kind_from_context")
        k = j

    # Notes / bibliography lists: smooth items that sit between notes or references.
    for _ in range(3):
        changed = False
        for k, c in enumerate(flow):
            if c.kind not in ("prose", "list_item"):
                continue
            prev = flow[k - 1].kind if k > 0 else None
            nxt = flow[k + 1].kind if k + 1 < len(flow) else None
            notes = {"reference", "footnote"}
            edge = {None, "heading", "toc", "index"}
            window = [x.kind for x in flow[max(0, k - 3):k] + flow[k + 1:k + 4]]
            dense = sum(kd in notes for kd in window) >= 4  # inside an endnotes/bibliography run
            if (prev in notes and nxt in notes | edge) or (nxt in notes and prev in edge) or dense:
                if _is_bibliographic(c.text, notes_context=True):
                    c.kind = "reference"
                elif c.block.type in ("list_item", "code") or _NOTE_LABEL.match(c.text) or dense:
                    c.kind = "footnote"
                else:
                    continue
                c.tags.append("kind_from_context")
                changed = True
        if not changed:
            break

    # Index entries wrapped over several chunks ("movement-oriented labour NGOs", "MST. See
    # Landless Workers Movement", a bare "284") sit between index chunks: fill gaps of up to
    # 5 short chunks inside the index region.
    pos = [k for k, c in enumerate(flow) if c.kind == "index"]
    for p, q in zip(pos, pos[1:]):
        if 1 < q - p <= 6:
            for c in flow[p + 1:q]:
                if c.kind in ("prose", "list_item", "heading", "reference") and len(c.text) <= 200 \
                        and not (c.block.type == "heading" and c.first_in_block):
                    c.kind = "index"
                    c.tags.append("kind_from_context")
    if pos:
        lo, hi = flow[pos[0]].order, flow[pos[-1]].order
        for c in chunks:  # bare locator lines that looked like page numbers
            if c.kind == "noise" and lo <= c.order <= hi and _PAGE_NUMBER.match(c.text) and c.text.isdigit() \
                    and c.block.type not in ("page_header", "page_footer"):
                c.kind = "index"
                c.tags.append("kind_from_context")

    # A heading repeated by the first line(s) of the next block (outline heading + printed
    # heading, "6.3 Nominal Group Technique" / "6.3 NOMINAL GROUP TECHNIQUE") is a duplicate.
    for k, c in enumerate(flow):
        if not (c.block.type == "heading" and c.first_in_block and c.last_in_block):
            continue
        key = _heading_key(c.text)
        acc = ""
        for d in flow[k + 1:k + 4]:
            if d.block is c.block or d.kind not in ("heading", "prose", "toc") or len(d.text) > 120:
                break
            if d.block is not flow[k + 1].block:
                break
            acc += _heading_key(d.text)
            if not key.startswith(acc):
                break
            if acc == key:
                for e in flow[k + 1:k + 4]:
                    e.kind = "noise"
                    e.tags.append("duplicate_heading")
                    if e is d:
                        break
                break


def _heading_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


# ----------------------------------------------------------------------------- footnote markers
_GLUED_PUNCT = re.compile(
    r"(?P<pre>(?:[A-Za-z][’”\"']*|[)\]]))[.,;:!?][’”\"')\]]*(?P<num>\d{1,3})(?:,(?P<num2>\d{1,3}))?(?=\s|$|[,;])")
_GLUED_DIGIT = re.compile(r"(?<=\d)[.;,:](?P<num>\d{1,3})(?=\s+[“‘\"'(\[]?[A-Z]|\s*$)")
_GLUED_LETTER = re.compile(r"(?<![A-Za-z])(?P<word>[A-Za-z]*[a-z]{3})(?P<num>\d{1,3})(?=\s|$|[,;.])")
_SPACED = re.compile(r"(?P<pre>[A-Za-z’”\"')\]][.!?;:][’”\"')\]]*)(?P<sp>\s)(?P<num>\d{1,3})(?=\s+[“‘\"'(\[]?[A-Z]|\s*$)")
# superscript after punctuation or a word of >= 3 letters ("x²", "m²" are maths, not notes)
# "'politics as organizing' 28 and", "Gramsci 99 and", "since 2019. 6 One": weakest evidence,
# accepted only as exactly the next note number and never after a counting/locating word.
_SPACED_WEAK = re.compile(
    r"(?<=[A-Za-z’”\"')\]0-9])(?:[.!?;:,]?[’”\"')\]]?(?:\s[’”\"'])?)(?P<sp>\s)(?P<num>\d{1,3})(?=\s|$|[.,;:\-–—])")
_NUMBER_CONTEXT_WORDS = frozenset(
    "chapter chapters section sections page pages part parts volume volumes vol vols figure figures "
    "table tables number numbers no nos p pp n nn in of than about over under nearly around approximately "
    "some the a an and or to from between by at on for with within top after before since until during "
    "each every all only just almost least most fewer more less up down per its their his her our we they "
    "he she it is are was were be been article articles verse verses line lines step steps phase phases "
    "stage stages level levels group groups question questions item items point points rule rules type "
    "types system systems model models class grade grades version versions edition editions issue issues "
    "book books note notes footnote ftn appendix q sec art para paras col cols room flight route "
    "year years day days week weeks month months hour hours minute minutes age aged ages "
    "example examples case cases scenario scenarios option options exercise exercises hypothesis "
    "zero one two three four five six seven eight nine ten score scores rating ratings value values "
    "january february march april may june july august september october november december".split())
# "', 10 for the concept" — a note after a clause; only accepted as the very next note number
_SPACED_CLAUSE = re.compile(r"(?<=[A-Za-z’”\"')\]][,;:])(?:[’”\"')\]]*)(?P<sp>\s)(?P<num>\d{1,3})(?=\s+[a-z])")
_SUPERSCRIPT = re.compile(r"(?:(?<=[.,;:!?’”\"')\]])|(?<=[A-Za-z]{3}))(?P<num>[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})")


class _MarkerSequence:
    """Footnote numbers run 1, 2, 3, ... and restart per chapter; candidates must fit."""

    def __init__(self) -> None:
        self.last = 0

    def accept(self, value: int, strong: bool | str) -> bool:
        in_run = self.last < value <= self.last + 5
        restart = value == 1 or (value <= 3 and value < self.last - 3)  # new chapter, note 1 missed
        if strong == "clause":  # weaker evidence: must be the next or next-but-one number
            ok = self.last < value <= self.last + 2 and value >= 2
        elif strong == "next":  # weakest evidence: exactly the next number
            ok = value == self.last + 1 and value >= 2
        else:
            ok = (strong and (in_run or restart)) or (not strong and value == self.last + 1 and value >= 2)
        if ok:
            self.last = value
        return ok


def _marker_candidates(text: str) -> list[tuple[int, int, int, str, bool]]:
    """(start, end, value, digits, strong) — start/end delimit what is removed from text."""
    found: dict[int, tuple[int, int, int, str, bool]] = {}

    rank = {True: 3, "clause": 2, False: 1, "next": 0}

    def add(start, end, digits, strong):
        # the same number can match several patterns; keep the strongest evidence class
        clashing = [k for k, v in found.items() if v[0] < end and start < v[1]]
        if any(rank[found[k][4]] >= rank[strong] for k in clashing):
            return
        for k in clashing:
            del found[k]
        found[start] = (start, end, int(digits.translate(_SUPERSCRIPTS)), digits.translate(_SUPERSCRIPTS), strong)

    for m in _SUPERSCRIPT.finditer(text):
        add(m.start("num"), m.end("num"), m.group("num"), True)
    for m in _GLUED_PUNCT.finditer(text):
        word = re.search(r"(?<![A-Za-z0-9])([A-Za-z0-9]+)[’”\"']*$", text[: m.end("pre")])
        if word and (word.group(1).lower() in _ABBREV_BEFORE_NUMBER or len(word.group(1)) == 1):
            continue  # "p.12", and cell coordinates such as "D,1; E,1" in flattened matrices
        end = m.end("num2") if m.group("num2") else m.end("num")
        if text[end:end + 1] in (",", ";"):
            continue
        add(m.start("num"), m.end("num"), m.group("num"), True)
        if m.group("num2"):  # "possibility.26,27 Digital" — two notes on one spot
            add(m.start("num2") - 1, m.end("num2"), m.group("num2"), True)
    for m in _SPACED_WEAK.finditer(text):
        prev_word = re.search(r"([A-Za-z]+|\d+)[^A-Za-z\d]*$", text[: m.start("sp")])
        after = re.match(r"\s+([a-z]+)", text[m.end("num"):])
        if prev_word is None or prev_word.group(1).lower() in _NUMBER_CONTEXT_WORDS:
            continue
        if prev_word.group(1).isdigit() and not re.search(r"\d{4}[.,;:][’”\"')\]]*$", text[: m.start("sp")]):
            continue  # "1992, 1996" lists; only a year closing a clause may carry a note
        if after and after.group(1) in _QUANTITY_WORDS:
            continue
        if re.match(r"\s*(?::|,\s*no\.|\(\d{4}\)|\.\d)", text[m.end("num"):]):
            continue  # "Example 3: ...", journal volumes "Foresight 9, no. 1", "Studies 5 (2012)"
        add(m.start("sp"), m.end("num"), m.group("num"), "next")
    for m in _SPACED_CLAUSE.finditer(text):
        before = text[max(0, m.start() - 60): m.start()]
        after = re.match(r"\s+([a-z]+)", text[m.end("num"):])
        if re.search(r"\b\d+\b", before) or (after and after.group(1) in _QUANTITY_WORDS):
            continue  # "4 points for the best idea, 3 points": an enumeration of quantities
        add(m.start("sp"), m.end("num"), m.group("num"), "clause")
    for m in _SPACED.finditer(text):
        word = re.search(r"([A-Za-z]+)[^A-Za-z]*$", text[: m.start("sp")])
        if word and word.group(1).lower() in _ABBREV_BEFORE_NUMBER:
            continue
        after = re.match(r"\s+[“‘\"'(\[]?([A-Za-z]+)", text[m.end("num"):])
        if after and after.group(1).lower() in _MONTHS:
            continue
        add(m.start("sp"), m.end("num"), m.group("num"), True)
    for m in _GLUED_DIGIT.finditer(text):
        base = re.search(r"[\d.]*\d$", text[: m.start("num") - 1])
        # "in 1921.5 They" (year) and "Figure 3.6.4 Each" (two-level number + note) qualify;
        # "Figure 6.4 Engaging" / "Version 2.1 The" (single-level base) are ordinary numbers.
        if base is None or not (re.fullmatch(r"\d{4}", base.group(0)) or "." in base.group(0)):
            continue
        add(m.start("num"), m.end("num"), m.group("num"), False)
    for m in _GLUED_LETTER.finditer(text):
        add(m.start("num"), m.end("num"), m.group("num"), False)
    return [found[k] for k in sorted(found)]


def _extract_markers(chunk: _Chunk, seq: _MarkerSequence) -> None:
    text = chunk.text
    out, pos, markers = [], 0, []
    for start, end, value, digits, strong in _marker_candidates(text):
        if start < pos or not seq.accept(value, strong):
            continue
        out.append(text[pos:start])
        markers.append((sum(len(p) for p in out), digits))
        pos = end
    if not markers:
        return
    out.append(text[pos:])
    chunk.text = "".join(out)
    chunk.markers = markers
    chunk.tags.append("footnote_markers_extracted")


# ----------------------------------------------------------------------------- segmentation
@lru_cache(maxsize=1)
def _segmenter():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        import pysbd

        return pysbd.Segmenter(language="en", clean=False)


_NO_SPLIT_AFTER = re.compile(r"(?:\b(?:vols?|nos?|pp?|ed|eds|cf|e\.g|i\.e|ch|chap|fig|figs|n|para|sec|art|Vol|No|Fig)\.)\s*$")


def _sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    segment = text[start:end]
    spans: list[tuple[int, int]] = []
    cursor = 0
    for sent in _segmenter().segment(segment):
        s = sent.strip()
        if not s:
            continue
        at = segment.find(s, cursor)
        if at < 0:  # pySBD changed the text; keep the remainder whole
            spans.append((start + cursor, end))
            cursor = len(segment)
            break
        spans.append((start + at, start + at + len(s)))
        cursor = at + len(s)
    if cursor < len(segment) and segment[cursor:].strip():
        spans.append((start + cursor, end))
    # glue back splits after reference abbreviations ("vol. | 2") pySBD sometimes makes
    merged: list[tuple[int, int]] = []
    for a, b in spans:
        nxt = text[a:b].lstrip()[:1]
        if merged and not re.search(r"\w", text[a:b]):  # a stray closing quote/bracket
            merged[-1] = (merged[-1][0], b)
        elif merged and _NO_SPLIT_AFTER.search(text[merged[-1][0]:merged[-1][1]]) and (nxt.isdigit() or nxt.islower()):
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _clause_split(text: str, a: int, b: int, limit: int) -> list[tuple[int, int, str]]:
    """Split text[a:b] into pieces <= limit at clause boundaries, else at spaces."""
    pieces: list[tuple[int, int, str]] = []
    start = a
    while b - start > limit:
        window = text[start:start + limit + 1]
        cut = None
        for m in _CLAUSE_BOUNDARY.finditer(window):
            if m.start() > 0:
                cut = (m.start(), m.end())
        tag = "split_clause"
        if cut is None:
            sp = window.rfind(" ")
            if sp <= 0:
                break
            cut, tag = (sp, sp + 1), "split_whitespace"
        pieces.append((start, start + cut[0], tag))
        start += cut[1]
    pieces.append((start, b, "split_clause" if pieces else ""))
    return pieces


# ----------------------------------------------------------------------------- the builder
def build_translation_units(book: ParsedBook, *, max_unit_chars: int = DEFAULT_MAX_UNIT_CHARS) -> list[TranslationUnit]:
    return build_units_with_provenance(book, max_unit_chars=max_unit_chars).units


def build_units_with_provenance(book: ParsedBook, *, max_unit_chars: int = DEFAULT_MAX_UNIT_CHARS) -> UnitBuild:
    if max_unit_chars < 40:
        raise ValueError("max_unit_chars must be >= 40")
    stats: Counter = Counter()
    normalized = [_normalize_chars(b.text) for b in book.blocks]
    vocab = _Vocab([t for t, _ in normalized])
    typical = _typical_line_length([t for t, _ in normalized])

    chunks = _make_chunks(book, normalized, vocab, typical, stats)
    _classify(chunks)
    _mark_notes_sections(chunks)

    seq = _MarkerSequence()
    for c in chunks:
        if c.kind in ("prose", "list_item", "caption"):
            _extract_markers(c, seq)

    paragraphs = _paragraphs(chunks, vocab, stats)
    return _emit(book, paragraphs, max_unit_chars, stats)


def _typical_line_length(texts: list[str]) -> float:
    lengths = sorted(len(line) for t in texts if t.count("\n") >= 2 for line in t.split("\n") if len(line) >= 10)
    if len(lengths) < 20:
        return 70.0
    return float(lengths[int(len(lengths) * 0.75)])


def _make_chunks(book: ParsedBook, normalized, vocab: _Vocab, typical: float, stats: Counter) -> list[_Chunk]:
    chunks: list[_Chunk] = []
    for order, (block, (text, char_changed)) in enumerate(zip(book.blocks, normalized)):
        block_tags = ["unicode_normalized"] if char_changed else []
        if block.type == "table":
            chunks.append(_Chunk(block, order, text, tags=list(block_tags), single_line=False,
                                 first_in_block=True, last_in_block=True))
            continue
        lines = []
        for line in text.split("\n"):
            fixed = _fix_punct_spacing(line)
            line_tags = []
            if fixed != line:
                line_tags.append("ocr_punct_spacing")
            repaired = _repair_letter_spacing(fixed, vocab)
            if repaired != fixed:
                line_tags.append("ocr_letter_spacing")
                stats["ocr_letter_spacing_lines"] += 1
            lines.append((repaired, line_tags))
        joined_text = "\n".join(ln for ln, _ in lines)
        if block.type == "heading" and _is_wrapped_title([ln for ln, _ in lines]):
            pieces = [(" ".join(ln for ln, _ in lines if ln), True, [], False)]
        else:
            pieces = _split_lines(joined_text, typical, vocab)
        out: list[_Chunk] = []
        for piece_text, single, tags, full_line in pieces:
            piece_tags = list(block_tags) + list(tags)
            for ln, tg in lines:
                if tg and ln and ln in piece_text:
                    piece_tags.extend(tg)
            notes = _split_inline_notes(piece_text)
            for note in notes:
                c = _Chunk(block, order, note, tags=list(dict.fromkeys(piece_tags)), single_line=single)
                c.labelled = bool(_NOTE_LABEL.match(note) or _BULLET.match(note))
                c.notes_context = len(notes) > 1  # a run of sequentially numbered notes
                c.ends_full_line = full_line
                out.append(c)
            if len(notes) > 1:
                stats["inline_note_splits"] += 1
        if not out:
            continue
        out[0].first_in_block = True
        out[-1].last_in_block = True
        stats["dehyphenated"] += sum(c.tags.count("dehyphenated") for c in out)
        chunks.extend(out)
    return chunks


def _mark_notes_sections(chunks: list[_Chunk]) -> None:
    """Inside a 'Notes' section (heading line or block), numbered items are notes."""
    in_notes = False
    for c in chunks:
        word = _section_word(c.text) if c.kind in ("heading", "prose", "noise") else None
        if word is not None:
            in_notes = word in ("notes", "bibliography")
            continue
        if c.block.type == "heading" and c.first_in_block:
            in_notes = False
            continue
        if in_notes and c.kind in ("prose", "list_item", "heading"):
            c.notes_context = True
            if _NOTE_LABEL.match(c.text) or c.kind == "list_item":
                c.kind = "reference" if _is_bibliographic(c.text, notes_context=True) else "footnote"


_JOINABLE = frozenset({"prose", "list_item", "footnote", "reference"})


def _paragraphs(chunks: list[_Chunk], vocab: _Vocab, stats: Counter) -> list[_Paragraph]:
    paragraphs: list[_Paragraph] = []
    last_open: _Paragraph | None = None
    for c in chunks:
        if c.kind == "noise":
            paragraphs.append(_Paragraph([c], "noise", list(c.tags)))
            continue  # noise never breaks a page-break join
        if last_open is not None and _continues(last_open.chunks[-1], c):
            prev = last_open.chunks[-1]
            if prev.text.endswith("-") and c.text[:1].islower():
                _, dehyph = _join_hyphen(prev.text, c.text, vocab)
                if dehyph:  # the block boundary now falls inside the re-joined word
                    prev.text = prev.text[:-1]
                    last_open.tags.append("dehyphenated")
                    stats["dehyphenated"] += 1
                    c.tags.append("_glue")  # no space at the join
            last_open.chunks.append(c)
            last_open.tags.extend(t for t in c.tags if t != "_glue")
            last_open.tags.append("joined_page_break")
            stats["page_break_joins"] += 1
            if last_open.kind in ("footnote", "reference"):
                text = " ".join(ch.text for ch in last_open.chunks)
                last_open.kind = "reference" if _is_bibliographic(text, notes_context=True) else "footnote"
            continue
        para = _Paragraph([c], c.kind, list(c.tags))
        paragraphs.append(para)
        last_open = para if c.kind in _JOINABLE and c.last_in_block else None
        if c.kind not in _JOINABLE:
            last_open = None
    return paragraphs


def _continues(a: _Chunk, b: _Chunk) -> bool:
    if a.kind not in _JOINABLE or b.kind not in _JOINABLE or not b.first_in_block:
        return False
    if b.labelled or _NOTE_LABEL.match(b.text) or _BULLET.match(b.text):
        return False
    if a.markers and a.markers[-1][0] >= len(a.text.rstrip()):
        return False  # a marker closed the sentence
    gap = min(b.pages) - max(a.pages)
    if not 1 <= gap <= 2:
        return False
    at = a.text.rstrip()
    if not at or _TERMINAL.search(at):
        return False
    first = b.text[:1]
    if first.islower():
        return True
    if a.ends_full_line and (first.isalpha() or first in "“‘\"'("):
        return True  # the page ended on a full line without closing the sentence
    if at[-1] in ",(–—-/&":
        return True
    return at.rsplit(" ", 1)[-1].lower() in _CONTINUATION_WORDS and (first.isalpha() or first in "“‘\"'(")


def _emit(book: ParsedBook, paragraphs: list[_Paragraph], limit: int, stats: Counter) -> UnitBuild:
    raw: list[dict] = []  # pre-units in reading order
    marker_blocks: list[tuple[str, str]] = []

    for para in paragraphs:
        # build the logical paragraph text and the chunk spans in it
        text, spans, markers = "", [], []
        for c in para.chunks:
            if text and "_glue" not in c.tags:
                text += " "
            start = len(text)
            text += c.text
            spans.append((start, len(text), c))
            markers.extend((start + off, m, c.block.block_id) for off, m in c.markers)
        kind = para.kind
        tags = list(dict.fromkeys(t for t in para.tags if t != "_glue"))
        for a, b, unit_markers, extra in _pieces(text, kind, markers, limit):
            piece_text = text[a:b].strip()
            if not piece_text:
                if unit_markers and raw:  # marker after empty text: attach to previous unit
                    raw[-1]["markers"].extend(m for m, _ in unit_markers)
                    marker_blocks.extend((m, bid) for m, bid in unit_markers)
                continue
            # trim to the visible span
            lead = len(text[a:b]) - len(text[a:b].lstrip())
            a2, b2 = a + lead, a + lead + len(piece_text)
            block_ids, pages, parts = [], [], []
            for s, e, c in spans:
                lo, hi = max(s, a2), min(e, b2)
                if lo < hi or (s == e and a2 <= s <= b2):
                    part = text[lo:hi]
                    if part.strip() or s == e:
                        if c.block.block_id not in block_ids:
                            block_ids.append(c.block.block_id)
                        pages.extend(c.pages)
                        parts.append((c.block.block_id, part))
            raw.append({
                "kind": kind, "text": piece_text, "block_ids": block_ids,
                "pages": sorted(set(pages)), "section_path": list(para.chunks[0].block.section_path),
                "markers": [m for m, _ in unit_markers], "tags": list(dict.fromkeys(tags + extra)),
                "parts": parts,
            })
            marker_blocks.extend((m, bid) for m, bid in unit_markers)

    units: list[TranslationUnit] = []
    cov: dict[str, list[tuple[str, str]]] = {b.block_id: [] for b in book.blocks}
    ids = [f"{book.book_id}:u{i:05d}" for i in range(len(raw))]
    linked = [i for i, r in enumerate(raw) if r["kind"] != "noise"]
    prev_of, next_of = {}, {}
    for x, y in zip(linked, linked[1:]):
        next_of[x], prev_of[y] = ids[y], ids[x]
    for i, r in enumerate(raw):
        units.append(TranslationUnit(
            unit_id=ids[i], book_id=book.book_id, block_ids=r["block_ids"], kind=r["kind"],
            source_text=r["text"], footnote_markers=r["markers"], pages=r["pages"],
            section_path=r["section_path"], prev_unit_id=prev_of.get(i), next_unit_id=next_of.get(i),
            normalization=r["tags"],
        ))
        for bid, part in r["parts"]:
            cov[bid].append((ids[i], part))
        stats[f"kind:{r['kind']}"] += 1
        stats["footnote_markers"] += len(r["markers"])
    return UnitBuild(units=units, coverage=cov, marker_blocks=marker_blocks, stats=stats)


def _pieces(text: str, kind: str, markers: list[tuple[int, str, str]], limit: int):
    """Yield (start, end, [(marker, block_id)], extra_tags) covering text[0:len] in order."""
    n = len(text)
    if kind == "table_cell":
        yield from _table_pieces(text)
        return
    if kind == "index":
        bounds = [0] + [m.start() + 1 for m in _INDEX_SPLIT.finditer(text)] + [n]
        for a, b in zip(bounds, bounds[1:]):
            if b - a <= limit:
                yield a, b, [], []
                continue
            # a long entry with sub-entries: pack sub-entries ("...; consciousness, 53") up to limit
            start = prev_cut = a
            for s2, _ in _split_keep(text[a:b], re.compile(r"(?<=;)\s+"))[1:]:
                if a + s2 - start > limit and prev_cut > start:
                    yield start, prev_cut, [], ["split_index_subentries"]
                    start = prev_cut
                prev_cut = a + s2
            if b - start > limit and prev_cut > start:
                yield start, prev_cut, [], ["split_index_subentries"]
                start = prev_cut
            yield start, b, [], ["split_index_subentries"] if start > a else []
        return
    if kind in ("noise", "toc", "heading") or (n <= limit and not markers):
        yield 0, n, [], []
        return
    # cut points: sentence ends (only needed for long text) and marker positions
    marker_at: dict[int, list[tuple[str, str]]] = {}
    for off, m, bid in markers:
        marker_at.setdefault(off, []).append((m, bid))
    if n > limit:
        if kind == "reference":
            spans = _split_keep(text, re.compile(r"(?<=;)\s+"))
        else:
            spans = _sentence_spans(text, 0, n)
    else:
        spans = [(0, n)]
    # segments = sentences further cut at marker offsets
    cuts = sorted(set([0, n] + [s for s, _ in spans] + list(marker_at)))
    segs = list(zip(cuts, cuts[1:])) or [(0, n)]
    # pack segments; a segment ending at a marker closes the unit
    cur_a = cur_b = None
    for a, b in segs:
        seg_len = len(text[a:b].strip())
        closes = b in marker_at
        if seg_len > 2 * limit:
            if cur_a is not None:
                yield cur_a, cur_b, [], []
                cur_a = None
            parts = _clause_split(text, a, b, limit)
            for k, (pa, pb, tag) in enumerate(parts):
                last = k == len(parts) - 1
                split_tag = [tag or "split_clause"] if len(parts) > 1 else []
                yield pa, pb, (marker_at.get(b, []) if last and closes else []), split_tag
            continue
        if cur_a is None:
            cur_a, cur_b = a, b
        elif len(text[cur_a:b].strip()) <= limit or not re.search(r"\w", text[a:b]):
            cur_b = b
        else:
            yield cur_a, cur_b, [], []
            cur_a, cur_b = a, b
        if closes:
            yield cur_a, cur_b, marker_at[b], ["split_at_footnote_marker"] if b < n else []
            cur_a = None
    if cur_a is not None:
        yield cur_a, cur_b, [], []


def _split_keep(text: str, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    out, start = [], 0
    for m in pattern.finditer(text):
        out.append((start, m.start()))
        start = m.start()
    out.append((start, len(text)))
    return out


def _table_pieces(text: str):
    rows = [line for line in text.split("\n")]
    if not any(r.strip().startswith("|") for r in rows):
        yield 0, len(text), [], ["table_unparsed"]
        return
    pos = 0
    any_cell = False
    for line in rows:
        line_start = text.find(line, pos)
        pos = line_start + len(line)
        if re.fullmatch(r"[\s|:\-]+", line):
            continue
        for m in re.finditer(r"[^|]+", line):
            if m.group(0).strip():
                any_cell = True
                yield line_start + m.start(), line_start + m.end(), [], []
    if not any_cell:
        yield 0, len(text), [], ["table_unparsed"]
