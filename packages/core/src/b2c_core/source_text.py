"""English source-text rules shared by ingestion normalization (S2) and translation units.

Moved verbatim from ``b2c_translation.units`` (D-028), where they were proven on the
2026-10-24 translation audit, so that S2 (ParsedBook v2 normalization, R3) and the
transitional unit builder run one implementation instead of two (restructure plan §R3).
Behaviour is unchanged; names lost their leading underscore. Pure functions, no I/O.
See the docstring of ``b2c_translation.units`` for the rationale of each rule.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

# ----------------------------------------------------------------------------- regexes
SPACES = re.compile("[   -   　\t\x0b\x0c]")
INVISIBLE = re.compile("[­​‌‍⁠﻿\x00-\x08\x0e-\x1f\x7f]")
NFKC_CHARS = re.compile("[ﬀ-ﬆ！-～]")
MULTISPACE = re.compile(r"[ ]{2,}")

SPACE_BEFORE_PUNCT = re.compile(r"(?<=[A-Za-z\)\]’”])[ ]+(?=[,;:!?](?:\s|$))")
SPACE_BEFORE_PERIOD = re.compile(r"(?<=[A-Za-z])[ ]+\.(?=\s|$)(?![ ]\.)")
SPACED_APOSTROPHE = re.compile(r"(?<=[A-Za-z]) ([’']) (?=s\b)")

WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
HYPHENATED = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+-[A-Za-zÀ-ÖØ-öø-ÿ]+")
TOKEN = re.compile(r"^([(\[“\"'‘]?)([A-Za-zÀ-ÖØ-öø-ÿ]+)([.,;:!?)\]”\"'’]*)$")

TERMINAL = re.compile(r"[.!?…:;][’”\"')\]]*$")
SENTENCE_END = re.compile(r"[.!?…][’”\"')\]]*$")
ABBREV_END = re.compile(
    r"(?:\b(?:e\.g|i\.e|etc|vs|cf|al|Dr|Mr|Mrs|Ms|Prof|Jr|Sr|St|No|no|Nos|vol|Vol|pp?|ed|eds|"
    r"trans|Fig|fig|ch|Ch|chap|Gen|Col|Lt|Rev|Inc|Ltd|Co|U\.S|U\.K)\.|\b[A-Z]\.)$")
CONTINUATION_WORDS = frozenset(
    "a an the of to in on at by for from with and or but nor as that which who whom whose "
    "than into onto upon about between among through within without under over after before "
    "during if when while because although though whether is are was were be been being has "
    "have had its their his her our your this these those such not also very more most".split())

NOTE_LABEL = re.compile(r"^(\d{1,3})\.\s+(?=\S)")
BULLET = re.compile(r"^(?:[•▪■●◦‣∙·]\s*|[-–—]\s+|\(?[a-z]\)\s+|\(\d{1,2}\)\s+|\d{1,2}\)\s+)")
NUMBERED_HEADING = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})+[a-z]?\s+\S|(?:chapter|part|appendix)\s+[\divxlc]+\b)", re.I)
CAPTION = re.compile(r"^(?:Figure|Fig\.|Table|Chart|Exhibit|Map|Plate)\s+\d+(?:\.\d+)*[A-Za-z]?\s+(?P<next>\S)")
SOURCE_LINE = re.compile(r"^Source:\s")
PAGE_NUMBER = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,7}|page\s+\d{1,4})$", re.I)
PRINTER_KEY = re.compile(r"^(?:\d{1,2}\s+){4,}\d{1,2}$")
NAV_LABELS = frozenset({
    "back to figure", "back to table", "back to text", "descriptions of images and figures",
    "description of images and figures",
})
SECTION_WORDS = {  # keys without spaces: letter-spaced headings ("N O T ES") match too
    "notes": "notes", "endnotes": "notes", "footnotes": "notes",
    "bibliography": "bibliography", "references": "bibliography", "workscited": "bibliography",
    "selectbibliography": "bibliography", "index": "index",
    "contents": "toc", "tableofcontents": "toc", "figures": "toc", "listoffigures": "toc",
    "tables": "toc", "listoftables": "toc",
}

TOC_PAGE = re.compile(r"[A-Za-z)™®’”'\"?]\s+(?:\d{1,3}|[ivxlc]{1,6})$")
TOC_BRACKET = re.compile(r"\[\s*\d{1,3}\s*\]$")
LOCATOR = re.compile(r"[,;:]\s*\d{1,4}(?:[-–]\d{1,4})?(?:n\d{1,3})?(?=[,;\s.]|$)")
LOCATORS_ONLY = re.compile(r"^[\d\s,;:–\-n]+$")
INDEX_SPLIT = re.compile(r"(?<=\d) (?=[A-Za-zÀ-ÿ‘'“\"])")

YEAR = re.compile(r"\b(?:1[5-9]\d\d|20\d\d)[a-z]?\b")
PUB = re.compile(r"\(\s*[A-Z][^():]{1,60}:\s*[^()]{2,120}\)|[A-Z][a-z]+(?:,\s*[A-Z]{2})?:\s*[A-Z][^.:]{1,60},\s*(?:1[5-9]|20)\d\d")
ENDPAGE = re.compile(r"[,:]\s*(?:pp?\.\s*)?(?:\d{1,3}|[xivlc]{1,6})(?:[-–]\d{1,4})?(?:,\s*\d{1,4}(?:[-–]\d{1,4})?)*\.?$")
CITE_MARK = re.compile(
    r"\b(?:ed|eds|trans|vol|vols|no|pp|n\.d|cf|op\. cit)\.|\bet al\b|\b[Ii]bid\b|https?://|doi\.org|\bdoi:"
    r"|\b(?:Translated|Edited|Introduction|Foreword) by\b|\bPress\b|\bUniversity\b|\bJournal\b|\bReview\b|§\s?\d")
QUOTED_TITLE = re.compile(r"[“‘'\"][A-Z][^”’'\"]{3,}[”’'\"]")
AUTHOR_LEAD = re.compile(r"^(?:[A-Z][A-Za-zÀ-ÿ'’\-]+,\s+[A-Z][A-Za-zÀ-ÿ.\- ]{0,40}[.,]|[A-Z]{2,6},\s+\d|\.\s+['‘“A-Z])")
IBID = re.compile(r"^(?:[Ii]bid\.?|[Ii]d\.|[Oo]p\. cit\.|[Ll]oc\. cit\.)(?=[\s,;.]|$)")
QUOTED_SPAN = re.compile(r"“[^”]*”|‘[^’]*’|(?<![\w])'[^']*'(?![\w])|\"[^\"]*\"")
PARENS = re.compile(r"\([^()]*\)")
URL = re.compile(r"https?://\S+|www\.\S+|\S+\.(?:com|org|edu|gov|net)\S*")
CITATION_WORDS = frozenset(
    "edited translated trans accessed retrieved available press university edition introduction "
    "foreword volume volumes diss dissertation thesis paper papers series also cited quoted "
    "paraphrased reprinted published unpublished revised mimeo online last".split())

MONTHS = frozenset("january february march april may june july august september october "
                    "november december".split())
ABBREV_BEFORE_NUMBER = frozenset("p pp no nos vol vols ch chap fig figs n nn para sec art ed eds pt §".split())
QUANTITY_WORDS = frozenset(
    "percent per points point times years days months weeks hours people persons million billion "
    "thousand hundred of to more less".split())
SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

CLAUSE_BOUNDARY = re.compile(r"(?<=[,;:])\s+|\s+(?=[—–]\s)|(?<=\s[—–])\s+")


# ----------------------------------------------------------------------------- vocabulary
class Vocab:
    def __init__(self, texts: list[str]):
        words: Counter = Counter()
        compounds: Counter = Counter()
        for t in texts:
            t = URL.sub(" ", t)  # 'www.structuredanalytic...' must not attest glued words
            words.update(w.lower() for w in WORD.findall(t))
            compounds.update(w.lower() for w in HYPHENATED.findall(t))
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
def normalize_chars(text: str) -> tuple[str, bool]:
    original = text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = NFKC_CHARS.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), text)
    text = SPACES.sub(" ", text)
    text = INVISIBLE.sub("", text)
    text = "\n".join(MULTISPACE.sub(" ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{2,}", "\n", text).strip()
    changed = text != original and squash(text) != squash(original)
    return text, changed


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def fix_punct_spacing(line: str) -> str:
    line = SPACE_BEFORE_PUNCT.sub("", line)
    line = SPACE_BEFORE_PERIOD.sub(".", line)
    return SPACED_APOSTROPHE.sub(r"\1", line)


def repair_letter_spacing(line: str, vocab: Vocab) -> str:
    tokens = line.split(" ")
    if len(tokens) < 2:
        return line
    parsed = [TOKEN.match(t) for t in tokens]
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
        whole = whole_line_letter_run(parsed, tokens)
        return line if whole is None else whole
    out, pos = [], 0
    for i, j, core in merges:
        out.extend(tokens[pos:i])
        out.append(parsed[i].group(1) + core + parsed[j - 1].group(3))
        pos = j
    out.extend(tokens[pos:])
    return " ".join(out)


def whole_line_letter_run(parsed, tokens: list[str]) -> str | None:
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


def starts_item(line: str, last_label: list[int]) -> bool:
    m = NOTE_LABEL.match(line)
    if m:
        num = int(m.group(1))
        if num == 1 or num == last_label[0] + 1:
            return True
    return bool(BULLET.match(line) or NUMBERED_HEADING.match(line))


def is_soft_wrap(prev: str, nxt: str, typical: float, last_label: list[int], in_note: bool = False) -> bool:
    if starts_item(nxt, last_label):
        return False
    if prev.endswith("-") and nxt[:1].islower():
        return True
    if in_note and prev.endswith(":"):
        return True  # 'J. Doe, "How to Decide:' + subtitle on the next line
    if CAPTION.match(prev) and nxt[:1].isupper() and prev[-1] not in ",(–—-/&" \
            and prev.rsplit(" ", 1)[-1].lower() not in CONTINUATION_WORDS:
        return False  # a figure caption line is not continued by the body text below it
    if TERMINAL.search(prev):
        return bool(ABBREV_END.search(prev)) and len(prev) >= 0.6 * typical and not nxt[:1].isupper() \
            or bool(ABBREV_END.search(prev)) and len(prev) >= 0.8 * typical
    if nxt[:1].islower():
        return True
    if prev[-1] in ",(–—-/&":
        return True
    last_word = prev.rsplit(" ", 1)[-1].lower()
    if last_word in CONTINUATION_WORDS:
        return True
    return len(prev) >= 0.6 * typical


def join_hyphen(left: str, right: str, vocab: Vocab) -> tuple[str, bool]:
    """`left` ends with '-': join with `right` and decide whether the hyphen was a soft one."""
    lw = re.search(r"([A-Za-zÀ-ÿ]+)-$", left)
    rw = re.match(r"([A-Za-zÀ-ÿ]+)", right)
    if lw and rw and not re.search(r"[-/]\S*-$", left.rsplit(" ", 1)[-1]):
        joined = lw.group(1) + rw.group(1)
        compound = f"{lw.group(1)}-{rw.group(1)}".lower()
        if vocab.count(joined) >= 1 and vocab.count(joined) >= vocab.compounds.get(compound, 0):
            return left[:-1] + right, True
    return left + right, False


def split_lines(text: str, typical: float, vocab: Vocab) -> list[tuple[str, bool, list[str], bool]]:
    """Return [(chunk_text, single_line, tags, ends_with_full_line)] after re-joining wrapped lines."""
    lines = [line for line in text.split("\n") if line.strip()]
    out: list[tuple[str, bool, list[str], bool]] = []
    last_label = [0]
    cur, cur_lines, tags, last_line = "", 0, [], ""
    hard_wrapped = len(lines) >= 3

    def full(line: str) -> bool:
        return hard_wrapped and len(line) >= 0.6 * typical

    for line in lines:
        m = NOTE_LABEL.match(line)
        if cur and is_soft_wrap(last_line, line, typical, last_label, in_note=bool(NOTE_LABEL.match(cur))):
            if cur.endswith("-") and line[:1].islower():
                cur, dehyph = join_hyphen(cur, line, vocab)
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


def is_wrapped_title(lines: list[str]) -> bool:
    """A heading block whose lines are one title broken over several lines."""
    lines = [ln for ln in lines if ln]
    if len(lines) < 2 or sum(len(ln) for ln in lines) > 200:
        return False
    if any(SENTENCE_END.search(ln) for ln in lines):
        return False
    return not any(NUMBERED_HEADING.match(ln) or NOTE_LABEL.match(ln) for ln in lines[1:])


INLINE_LABEL = re.compile(r"(?<=[.;)\]’”'\"]) (\d{1,3})\.\s+(?=\S)")


def split_inline_notes(text: str) -> list[str]:
    """Split '140. Ibid., 12. 141. Ibid., 13.' into notes when labels form a run n, n+1, ...

    The first note may have lost its label in extraction ('SPN, 111. 186. SPN, 112. 187.
    Ibid.'); then at least two consecutive inline labels are required."""
    m0 = NOTE_LABEL.match(text)
    found = [(m.start() + 1, int(m.group(1))) for m in INLINE_LABEL.finditer(text)]
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


def section_word(text: str) -> str | None:
    key = re.sub(r"\s+", "", text.strip().strip(":.").lower())
    return SECTION_WORDS.get(key)


def prose_words(text: str) -> tuple[int, int]:
    t = URL.sub(" ", text)
    t = QUOTED_SPAN.sub(lambda m: " " if len(m.group(0).split()) <= 20 else m.group(0), t)
    t = PARENS.sub(" ", t)
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]*", t)
    prose = [w for w in words if w[0].islower() and len(w) >= 4 and w.lower() not in CITATION_WORDS]
    return len(prose), len(words)


def citation_score(text: str) -> int:
    return sum(bool(p.search(text)) for p in (YEAR, PUB, ENDPAGE, CITE_MARK, QUOTED_TITLE, AUTHOR_LEAD))


def is_bibliographic(text: str, *, notes_context: bool) -> bool:
    body = NOTE_LABEL.sub("", text, count=1)
    prose, total = prose_words(body)
    if IBID.match(body):
        return prose <= 2
    if total == 0:
        return False
    low_prose = prose <= max(1, int(0.12 * total))
    score = citation_score(body)
    return low_prose and score >= (1 if notes_context else 3)


def toc_like(text: str) -> bool:
    if len(text) > 160 or not re.search(r"[A-Za-z]", text):
        return False
    if SENTENCE_END.search(text) and not text.endswith("?"):
        return False
    return bool(TOC_PAGE.search(text) or TOC_BRACKET.search(text) or NUMBERED_HEADING.match(text))


def index_like(text: str) -> bool:
    if LOCATORS_ONLY.match(text):
        return bool(re.search(r"\d", text)) and not PAGE_NUMBER.match(text.strip())
    n = len(LOCATOR.findall(text))
    if n == 0 or IBID.match(text) or text.rstrip().endswith(".") or PUB.search(text):
        return False
    return n * 30 >= len(text)


def heading_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


# ----------------------------------------------------------------------------- footnote markers
GLUED_PUNCT = re.compile(
    r"(?P<pre>(?:[A-Za-z][’”\"']*|[)\]]))[.,;:!?][’”\"')\]]*(?P<num>\d{1,3})(?:,(?P<num2>\d{1,3}))?(?=\s|$|[,;])")
GLUED_DIGIT = re.compile(r"(?<=\d)[.;,:](?P<num>\d{1,3})(?=\s+[“‘\"'(\[]?[A-Z]|\s*$)")
GLUED_LETTER = re.compile(r"(?<![A-Za-z])(?P<word>[A-Za-z]*[a-z]{3})(?P<num>\d{1,3})(?=\s|$|[,;.])")
SPACED = re.compile(r"(?P<pre>[A-Za-z’”\"')\]][.!?;:][’”\"')\]]*)(?P<sp>\s)(?P<num>\d{1,3})(?=\s+[“‘\"'(\[]?[A-Z]|\s*$)")
# superscript after punctuation or a word of >= 3 letters ("x²", "m²" are maths, not notes)
# "'politics as organizing' 28 and", "Gramsci 99 and", "since 2019. 6 One": weakest evidence,
# accepted only as exactly the next note number and never after a counting/locating word.
SPACED_WEAK = re.compile(
    r"(?<=[A-Za-z’”\"')\]0-9])(?:[.!?;:,]?[’”\"')\]]?(?:\s[’”\"'])?)(?P<sp>\s)(?P<num>\d{1,3})(?=\s|$|[.,;:\-–—])")
NUMBER_CONTEXT_WORDS = frozenset(
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
SPACED_CLAUSE = re.compile(r"(?<=[A-Za-z’”\"')\]][,;:])(?:[’”\"')\]]*)(?P<sp>\s)(?P<num>\d{1,3})(?=\s+[a-z])")
SUPERSCRIPT = re.compile(r"(?:(?<=[.,;:!?’”\"')\]])|(?<=[A-Za-z]{3}))(?P<num>[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})")


class MarkerSequence:
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


def marker_candidates(text: str) -> list[tuple[int, int, int, str, bool]]:
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
        found[start] = (start, end, int(digits.translate(SUPERSCRIPTS)), digits.translate(SUPERSCRIPTS), strong)

    for m in SUPERSCRIPT.finditer(text):
        add(m.start("num"), m.end("num"), m.group("num"), True)
    for m in GLUED_PUNCT.finditer(text):
        word = re.search(r"(?<![A-Za-z0-9])([A-Za-z0-9]+)[’”\"']*$", text[: m.end("pre")])
        if word and (word.group(1).lower() in ABBREV_BEFORE_NUMBER or len(word.group(1)) == 1):
            continue  # "p.12", and cell coordinates such as "D,1; E,1" in flattened matrices
        end = m.end("num2") if m.group("num2") else m.end("num")
        if text[end:end + 1] in (",", ";"):
            continue
        add(m.start("num"), m.end("num"), m.group("num"), True)
        if m.group("num2"):  # "possibility.26,27 Digital" — two notes on one spot
            add(m.start("num2") - 1, m.end("num2"), m.group("num2"), True)
    for m in SPACED_WEAK.finditer(text):
        prev_word = re.search(r"([A-Za-z]+|\d+)[^A-Za-z\d]*$", text[: m.start("sp")])
        after = re.match(r"\s+([a-z]+)", text[m.end("num"):])
        if prev_word is None or prev_word.group(1).lower() in NUMBER_CONTEXT_WORDS:
            continue
        if prev_word.group(1).isdigit() and not re.search(r"\d{4}[.,;:][’”\"')\]]*$", text[: m.start("sp")]):
            continue  # "1992, 1996" lists; only a year closing a clause may carry a note
        if after and after.group(1) in QUANTITY_WORDS:
            continue
        if re.match(r"\s*(?::|,\s*no\.|\(\d{4}\)|\.\d)", text[m.end("num"):]):
            continue  # "Example 3: ...", journal volumes "Foresight 9, no. 1", "Studies 5 (2012)"
        add(m.start("sp"), m.end("num"), m.group("num"), "next")
    for m in SPACED_CLAUSE.finditer(text):
        before = text[max(0, m.start() - 60): m.start()]
        after = re.match(r"\s+([a-z]+)", text[m.end("num"):])
        if re.search(r"\b\d+\b", before) or (after and after.group(1) in QUANTITY_WORDS):
            continue  # "4 points for the best idea, 3 points": an enumeration of quantities
        add(m.start("sp"), m.end("num"), m.group("num"), "clause")
    for m in SPACED.finditer(text):
        word = re.search(r"([A-Za-z]+)[^A-Za-z]*$", text[: m.start("sp")])
        if word and word.group(1).lower() in ABBREV_BEFORE_NUMBER:
            continue
        after = re.match(r"\s+[“‘\"'(\[]?([A-Za-z]+)", text[m.end("num"):])
        if after and after.group(1).lower() in MONTHS:
            continue
        add(m.start("sp"), m.end("num"), m.group("num"), True)
    for m in GLUED_DIGIT.finditer(text):
        base = re.search(r"[\d.]*\d$", text[: m.start("num") - 1])
        # "in 1921.5 They" (year) and "Figure 3.6.4 Each" (two-level number + note) qualify;
        # "Figure 6.4 Engaging" / "Version 2.1 The" (single-level base) are ordinary numbers.
        if base is None or not (re.fullmatch(r"\d{4}", base.group(0)) or "." in base.group(0)):
            continue
        add(m.start("num"), m.end("num"), m.group("num"), False)
    for m in GLUED_LETTER.finditer(text):
        add(m.start("num"), m.end("num"), m.group("num"), False)
    return [found[k] for k in sorted(found)]
