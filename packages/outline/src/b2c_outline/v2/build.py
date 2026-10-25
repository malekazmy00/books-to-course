"""Outline v2 builder: ParsedBook v2 -> CourseOutline v2 (R4, audit 02 §5.2), deterministic.

1. Structure evidence, strongest first: PDF bookmarks (with levels, reconciled to heading blocks in
   S2), else the printed table of contents resolved to headings, else heading blocks and their
   levels. A heading that no stronger source names is never promoted above the level that source
   covers (audit P4): it stays a sub-heading inside a lesson.
2. Zones: top-level entries classified by title (EPUB SSV-like reasons) split the book into front
   matter, body and back matter; S2 block zones fill in where there are no entries. Front and back
   matter become excluded ranges with a reason, never lessons (P2). Inside the body, apparatus
   sections (chapter-end notes, bibliographies) are excluded too. Figure and table descriptions
   are not (D-036 (1)): they stay in their lesson in reading order, marked with block roles; a
   chapter's description section printed after its excluded notes is an extra span of the
   chapter's last lesson. The preface, foreword and acknowledgments before the first chapter are
   lessons of an opening module (D-036 (2)); blocks in them that do not suit listening as they are
   (thank-you lists, tables, long lists, legal notes) carry an audio_adaptation flag and stay in
   the lesson. Copyright page, contents, lists of figures, index and bibliography stay excluded.
3. Tree: body top-level entries are modules; their children (next level of the same source, or the
   chapter's own headings when the source has no deeper level) bound lessons. Text before the
   first section is a chapter-introduction lesson, not hidden under the first section's title.
   Boundaries are heading blocks, never page tops (P6).
4. Size: words per lesson against the size policy; a lesson above the maximum is split at the
   next level of the source structure when it has one; a heading-derived section below the
   minimum is merged into the previous section of its chapter. Whatever stays outside the bounds
   carries a documented exception.
5. Titles: display titles are cleaned (whitespace, letter-spacing, small caps / ALL CAPS) and a
   generic title gets its chapter as context ("Chapter 3 · Conclusion"); the source title is kept.
6. Ids come from the book's structure (titles), not from block ids or positions (P11).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from b2c_contracts import (
    AudioAdaptation, BlockRoleRun, CourseOutlineV2, ExcludedRange, LessonExtraSpan, LessonSizePolicy, LessonV2, ModuleV2,
    OutlineSpanV2, ParsedBookV2, v2_block_seq,
)

from .reference import FURNITURE, bookmark_entries, find_heading, printed_toc_entries, title_key
from .titles import TitleVocab, clean_title, english_zipf

BUILDER_NAME = "b2c-outline-v2"
BUILDER_VERSION = "3"  # 2: figure descriptions in lessons; 3: front-matter lessons (D-036)
# D-036 (2): front matter taught as lessons of the opening module, before chapter 1
DEFAULT_FRONT_LESSONS = frozenset({"foreword", "preface", "acknowledgments"})
FRONT_MODULE_TITLE = "Introduction to the Book"  # "مقدمة الكتاب" once translated (D-035)
_LEGAL_HEADINGS = frozenset({"disclaimer", "legalnotice", "copyright", "permissions", "rightsandpermissions"})
_NOTE_HEADINGS = frozenset({"note", "notes", "references", "sources"})
LONG_LIST_ITEMS = 8  # a run of this many list items is flagged for listening
_ADAPTATION_NOTES = {
    "acknowledgements": "thanks and names: adapt for listening (e.g. say in summary who is thanked and why); "
                        "drop no one - the original text stays in the lesson",
    "table": "table: read it as sentences or summarise it row by row; the original table stays in the lesson",
    "long_list": "long list: group or summarise it for listening; the original list stays in the lesson",
    "legal_notice": "legal or copyright notice: mention it briefly; the original text stays in the lesson",
    "reference_note": "bibliographic note: mention the source briefly; the original text stays in the lesson",
}
# Owner decision Q9 is pending: 600-4,000 words is the audit's proposal (02 §5.2.4), not a decision.
DEFAULT_SIZE_POLICY = LessonSizePolicy(policy_id="words-600-4000-proposal", min_words=600, max_words=4000)
INSTRUCTIONAL = frozenset({"paragraph", "list_item", "table", "figure", "caption", "equation", "code", "footnote"})
APPARATUS = frozenset({"endnote", "bibliography_entry", "index_entry"})

_FRONT = (("cover", "cover"), ("titlepage", "title_page"), ("halftitle", "title_page"),
          ("copyright", "copyright"), ("dedication", "dedication"), ("epigraph", "epigraph"),
          ("tableofcontents", "toc"), ("contents", "toc"), ("listoffigures", "list_of_figures"),
          ("listoftables", "list_of_figures"), ("listofillustrations", "list_of_figures"),
          ("illustrations", "list_of_figures"), ("figures", "list_of_figures"), ("tables", "list_of_figures"),
          ("acknowledg", "acknowledgments"), ("foreword", "foreword"), ("preface", "preface"),
          ("abouttheauthor", "about_author"), ("abouttheeditor", "about_author"), ("praisefor", "front_matter"),
          ("frontmatter", "front_matter"))
_BACK = (("notes", "endnotes"), ("endnotes", "endnotes"), ("bibliography", "bibliography"),
         ("references", "bibliography"), ("workscited", "bibliography"), ("selectbibliography", "bibliography"),
         ("furtherreading", "bibliography"), ("index", "index"), ("glossary", "glossary"),
         ("appendix", "appendix"), ("abouttheauthor", "about_author"), ("aboutthecontributors", "about_author"),
         ("credits", "back_matter"), ("permissions", "back_matter"), ("backmatter", "back_matter"))
# exact keys of in-body apparatus headings (chapter-end notes, e-book figure descriptions ...)
_APPARATUS_HEADINGS = {"notes": "endnotes", "endnotes": "endnotes", "footnotes": "endnotes",
                       "bibliography": "bibliography", "references": "bibliography", "workscited": "bibliography",
                       "selectbibliography": "bibliography", "furtherreading": "bibliography", "index": "index",
                       "descriptionsofimagesandfigures": "figure_descriptions",
                       "descriptionofimagesandfigures": "figure_descriptions"}
_GENERIC = frozenset({"introduction", "conclusion", "conclusions", "summary", "overview", "concludingremarks",
                      "epilogue", "prologue", "exercises", "review", "keyterms", "discussionquestions"})
_COPYRIGHT = re.compile(r"©|\ball rights reserved\b|\blibrary of congress\b|\bisbn\b", re.I)
_LEAD_NUMBER = re.compile(r"^\s*(?:[Cc]hapter\s+|CHAPTER\s+)?(\d{1,3})(?=[\s.:•·])|^\s*(?:[Cc]hapter|CHAPTER)\s+([IVXLC]{1,6}|[ivxlc]{1,6})(?=[\s.:•·]|$)")
_PART = re.compile(r"^\s*(?:part|book|volume)\s+(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
                   re.I)
_CAPTION_LIKE = re.compile(r"^(?:figure|fig\.|table|exhibit|chart|map|plate)\s+\d", re.I)
_TOC_LIKE = re.compile(r"\[\s*\d{1,4}\s*\]")
_FIGURE_MARKER = re.compile(r"^\s*(?:back\s+to\s+(?:figure|table|image|exhibit)s?|return\s+to\s+(?:figure|table))\b",
                            re.I)
TAUGHT_APPARATUS = frozenset({"figure_descriptions"})  # apparatus sections that stay lesson content


class OutlineUnavailable(ValueError):
    """The book has no structure the deterministic builder can trust (needs a human or the LLM path)."""


@dataclass(frozen=True)
class OutlineConfig:
    size_policy: LessonSizePolicy = DEFAULT_SIZE_POLICY
    include_front: frozenset[str] = frozenset()  # front-matter reasons taught as modules (e.g. {"preface"})
    include_back: frozenset[str] = frozenset()  # back-matter reasons taught as modules (e.g. {"appendix"})
    # front-matter reasons taught as lessons of the opening module (D-036 (2))
    front_lessons: frozenset[str] = DEFAULT_FRONT_LESSONS

    def as_dict(self) -> dict:
        return {"size_policy": self.size_policy.model_dump(mode="json"),
                "include_front": sorted(self.include_front), "include_back": sorted(self.include_back),
                "front_lessons": sorted(self.front_lessons)}


@dataclass
class _Node:
    index: int
    level: int
    title: str
    source: str  # pdf_outline | printed_toc | heading


@dataclass
class _Piece:
    """A lesson under construction: an inclusive block range and its heading."""
    start: int
    end: int
    anchor: int
    level: int
    title: str
    source: str
    kind: str = "section"
    subheadings: list[int] = field(default_factory=list)
    exception: str | None = None
    children: list[_Node] = field(default_factory=list)
    extras: list[tuple[int, int]] = field(default_factory=list)  # extra spans (figure descriptions)


def classify(title: str) -> tuple[str, str] | None:
    """('front'|'back', reason) for a front/back matter title, None for body content."""
    key = title_key(re.sub(r"^\s*(?:\d+(?:\.\d+)*\.?|[ivxlc]+\.)\s+", "", title, flags=re.I))
    for prefix, reason in _BACK:
        if key.startswith(prefix) and (prefix != "notes" or key in ("notes", "notesandreferences")):
            return "back", reason
    for prefix, reason in _FRONT:
        if key.startswith(prefix):
            return "front", reason
    return None


def apparatus_reason(text: str) -> str | None:
    return _APPARATUS_HEADINGS.get(title_key(text))


# ----------------------------------------------------------------------------- structure evidence
def _structure(book: ParsedBookV2) -> tuple[list[_Node], str]:
    marks = [_Node(e.index, e.level, e.title, "pdf_outline") for e in bookmark_entries(book) if e.index is not None]
    marks = _dedupe(marks)
    if sum(1 for n in marks if classify(n.title) is None) >= 1 and len(marks) >= 2:
        return marks, "pdf_outline"
    toc = _dedupe([_Node(e.index, e.level, e.title, "printed_toc") for e in printed_toc_entries(book)
                   if e.index is not None])
    if len(toc) >= 2:
        return toc, "printed_toc"
    heads = [_Node(i, b.level or 1, b.text, "heading") for i, b in enumerate(book.blocks)
             if b.type == "heading" and b.zone != "furniture"]
    if not any(b.zone == "body" for b in (book.blocks[n.index] for n in heads)):
        raise OutlineUnavailable("no bookmarks, no printed table of contents and no body headings")
    return heads, "heading"


def _dedupe(nodes: list[_Node]) -> list[_Node]:
    seen: set[int] = set()
    out = []
    for n in sorted(nodes, key=lambda n: (n.index, n.level)):
        if n.index not in seen:
            seen.add(n.index)
            out.append(n)
    return out


# ----------------------------------------------------------------------------- helpers
class _Book:
    def __init__(self, book: ParsedBookV2):
        self.book = book
        self.blocks = book.blocks
        first = v2_block_seq(self.blocks[0].block_id) if self.blocks else 0
        for i, b in enumerate(self.blocks):
            if v2_block_seq(b.block_id) != first + i:
                raise OutlineUnavailable(f"block ids are not consecutive in reading order at {b.block_id}")
        words = [0] * len(self.blocks)
        for i, b in enumerate(self.blocks):
            if b.type in INSTRUCTIONAL:
                words[i] = len(b.text.split())
        self.prefix = [0]
        for w in words:
            self.prefix.append(self.prefix[-1] + w)
        self.roles: list[str | None] = [None] * len(self.blocks)  # D-036 block roles
        for i, b in enumerate(self.blocks):
            if b.type == "caption":
                self.roles[i] = "figure_caption"
        self.vocab = TitleVocab.from_texts((b.text for b in self.blocks if b.zone == "body" and b.type in INSTRUCTIONAL),
                                           lexicon=english_zipf)

    def words(self, a: int, b: int) -> int:  # inclusive
        return self.prefix[b + 1] - self.prefix[a] if b >= a else 0

    def content(self, a: int, b: int) -> list[int]:
        return [i for i in range(a, b + 1) if self.blocks[i].type not in FURNITURE]

    def pages(self, a: int, b: int) -> tuple[int, int]:
        idx = self.content(a, b) or list(range(a, b + 1))
        pages = [r.page_no for i in idx for r in self.blocks[i].refs]
        return min(pages), max(pages)

    def text(self, a: int, b: int) -> str:
        return " ".join(self.blocks[i].text for i in range(a, b + 1))


def _slug(text: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")[:40].strip("-") or "x"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:6]


def _chapter_label(module_title: str) -> str:
    m = _LEAD_NUMBER.match(module_title)
    return f"Chapter {m.group(1) or m.group(2)}" if m else module_title


# ----------------------------------------------------------------------------- builder
def build_outline_v2(book: ParsedBookV2, *, course_id: str, supplementary_book_ids: list[str] | tuple = (),
                     config: OutlineConfig = OutlineConfig(), parsed_sha256: str | None = None) -> CourseOutlineV2:
    bk = _Book(book)
    n = len(bk.blocks)
    nodes, source = _structure(book)
    excluded: list[ExcludedRange] = []
    modules: list[ModuleV2] = []

    front: list[_Piece] = []  # front-matter lessons (D-036 (2))

    def exclude(a: int, b: int, zone: str, reason: str, title: str | None, rule: str) -> None:
        if b < a:
            return
        excluded.append(ExcludedRange(start_block=bk.blocks[a].block_id, end_block=bk.blocks[b].block_id,
                                      pages=bk.pages(a, b), zone=zone, reason=reason, title=title, rule=rule))

    def front_matter(a: int, b: int, node: _Node | None) -> None:
        """Front matter before the first chapter: taught reasons become lessons, the rest is excluded."""
        for start, stop, reason, title, rule in _matter_cuts(bk, a, b, "front_matter", node, nodes):
            if reason not in config.front_lessons:
                exclude(start, stop, "front_matter", reason, title, rule)
                continue
            prev = front[-1] if front else None
            if prev is not None and prev.end + 1 == start and not rule.endswith(":entry"):
                prev.end = stop  # a heading inside a taught entry (e.g. ACKNOWLEDGMENTS in the Preface)
                continue
            entry = next((x for x in nodes if x.index == start), None)
            front.append(_Piece(start, stop, start, entry.level if entry else (bk.blocks[start].level or 1),
                                title or reason, entry.source if entry else "heading", kind="front_matter",
                                exception=reason))

    top_level = min(n_.level for n_ in nodes)
    tops = [n_ for n_ in nodes if n_.level == top_level]
    body_tops = [n_ for n_ in tops if _taught(n_, config)]
    if not body_tops:
        raise OutlineUnavailable("no body entry in the book structure")
    # top-level parts ("Part I", "Book 2") with chapters below: chapters are the modules
    if all(_PART.match(t.title) for t in body_tops) and all(any(c.level == top_level + 1 and _within(c, t, tops, n) for c in nodes)
                                   for t in body_tops):
        top_level += 1
        tops = [n_ for n_ in nodes if n_.level == top_level]
        body_tops = [n_ for n_ in tops if _taught(n_, config)]

    # segments of the top level: [anchor_i, anchor_{i+1}-1]
    starts = [t.index for t in tops]
    segments = [(t, t.index, (starts[k + 1] - 1) if k + 1 < len(tops) else n - 1) for k, t in enumerate(tops)]
    first_body = body_tops[0].index
    # the S2 zone decides where trailing back matter starts when no entry marks it
    tail = n
    for i in range(n - 1, -1, -1):
        b = bk.blocks[i]
        if b.type in FURNITURE:
            continue
        if b.zone != "back_matter":
            break
        tail = i
    prefix_end = segments[0][1] if segments else n
    # blocks before the first entry: front matter unless S2 calls them body text
    if prefix_end > 0:
        content = bk.content(0, prefix_end - 1)
        body_words = sum(len(bk.blocks[i].text.split()) for i in content
                         if bk.blocks[i].zone == "body" and bk.blocks[i].type in INSTRUCTIONAL)
        if body_words >= 100 and first_body == segments[0][1]:
            segments[0] = (segments[0][0], 0, segments[0][2])  # body text before the first chapter
        else:
            front_matter(0, prefix_end - 1, None)
    for node, a, b in segments:
        if a < first_body:
            front_matter(a, b, node)
            continue
        if not _taught(node, config):
            side = classify(node.title)
            zone = "front_matter" if side and side[0] == "front" else "back_matter"
            _split_matter(bk, a, b, zone, node, exclude, nodes, by_entries_only=zone == "back_matter")
            continue
        end = b
        if tail <= b and tail > a:
            end = tail - 1
        module = _module(bk, node, a, end, nodes, source, config, exclude, len(modules) + 1, book.book_id)
        modules.append(module)
        if end < b:
            _split_matter(bk, end + 1, b, "back_matter", None, exclude, nodes, by_entries_only=True)
    if not modules:
        raise OutlineUnavailable("the book structure has no body modules")
    if front:
        modules = [_front_module(bk, front, book.book_id, config.size_policy, top_level)] + [
            m.model_copy(update={"order": m.order + 1}) for m in modules]
    _unique_ids(modules)
    excluded = _merge_adjacent(sorted(excluded, key=lambda r: r.seqs))
    return CourseOutlineV2(
        course_id=course_id, spine_book_id=book.book_id, supplementary_book_ids=list(supplementary_book_ids),
        parsed_sha256=parsed_sha256, block_count=n,
        builder={"name": BUILDER_NAME, "version": BUILDER_VERSION, "config": _config_digest(config)},
        structure_source=source, size_policy=config.size_policy, modules=modules, excluded_ranges=excluded)


def _merge_adjacent(ranges: list[ExcludedRange]) -> list[ExcludedRange]:
    """One range per run of the same zone and reason (e.g. contents lines + their 'Contents' title)."""
    out: list[ExcludedRange] = []
    for r in ranges:
        prev = out[-1] if out else None
        if prev is not None and (prev.zone, prev.reason) == (r.zone, r.reason) and prev.seqs[1] + 1 == r.seqs[0]:
            out[-1] = prev.model_copy(update={
                "end_block": r.end_block, "pages": (min(prev.pages[0], r.pages[0]), max(prev.pages[1], r.pages[1])),
                "title": prev.title or r.title, "rule": prev.rule if prev.title or not r.title else r.rule})
            continue
        out.append(r)
    return out


def _config_digest(config: OutlineConfig) -> str:
    import json
    return hashlib.sha256(json.dumps(config.as_dict(), sort_keys=True).encode()).hexdigest()[:16]


def _taught(node: _Node, config: OutlineConfig) -> bool:
    side = classify(node.title)
    if side is None:
        return True
    return side[1] in (config.include_front if side[0] == "front" else config.include_back)


def _within(child: _Node, top: _Node, tops: list[_Node], n: int) -> bool:
    later = [t.index for t in tops if t.index > top.index]
    return top.index < child.index < (later[0] if later else n)


def _split_matter(bk: _Book, a: int, b: int, zone: str, node: _Node | None, exclude, nodes: list[_Node],
                  *, by_entries_only: bool = False) -> None:
    """Exclude [a, b] as front/back matter, one range per titled section (reason from the title)."""
    for start, stop, reason, title, rule in _matter_cuts(bk, a, b, zone, node, nodes,
                                                         by_entries_only=by_entries_only):
        exclude(start, stop, zone, reason, title, rule)


def _matter_cuts(bk: _Book, a: int, b: int, zone: str, node: _Node | None, nodes: list[_Node],
                 *, by_entries_only: bool = False) -> list[tuple[int, int, str, str | None, str]]:
    """Front/back matter [a, b] split into titled sections: (start, stop, reason, title, rule)."""
    cuts: list[tuple[int, str | None, str | None]] = []  # (start, reason, title)
    entry_titles = {x.index: x.title for x in nodes}
    if node is not None:
        side = classify(node.title)
        cuts.append((a, side[1] if side else None, node.title))
    for i in range(a, b + 1):
        blk = bk.blocks[i]
        if not by_entries_only and blk.type == "toc_entry" and cuts and cuts[-1][1] != "toc" and i > cuts[-1][0]:
            cuts.append((i, "toc", None))  # contents lines read before their heading (reading-order quirk)
            continue
        if (blk.type != "heading" and i not in entry_titles) or (by_entries_only and i not in entry_titles):
            continue
        title = entry_titles.get(i, blk.text)
        side = classify(title)
        if not cuts:
            cuts.append((a, side[1] if side else None, title if side else None))
            continue
        if side is not None and side[1] != cuts[-1][1] and i > cuts[-1][0]:
            cuts.append((i, side[1], title))
    if not cuts:
        cuts.append((a, None, None))
    out = []
    for k, (start, reason, title) in enumerate(cuts):
        stop = cuts[k + 1][0] - 1 if k + 1 < len(cuts) else b
        if reason is None:
            text = bk.text(start, stop)
            reason = "copyright" if _COPYRIGHT.search(text) else (
                "title_page" if start == 0 and zone == "front_matter" else
                ("front_matter" if zone == "front_matter" else "back_matter"))
        rule = f"{zone}:{'entry' if title and title == (node.title if node else None) else 'heading' if title else 'unlabelled'}"
        out.append((start, stop, reason, title, rule))
    return out


def _apparatus_runs(bk: _Book, a: int, b: int, boundaries: set[int]) -> list[tuple[int, int, str, str]]:
    """In-body apparatus sections (chapter-end notes, figure descriptions): heading to next boundary."""
    heads = [(i, apparatus_reason(bk.blocks[i].text)) for i in range(a + 1, b + 1)
             if bk.blocks[i].type == "heading" and apparatus_reason(bk.blocks[i].text)]
    stops = sorted(boundaries | {i for i, _ in heads} | {b + 1})
    runs = []
    for i, reason in heads:
        stop = next(s for s in stops if s > i) - 1
        runs.append((i, stop, reason, bk.blocks[i].text))
    return runs


def _module(bk: _Book, node: _Node, a: int, b: int, nodes: list[_Node], source: str, config: OutlineConfig,
            exclude, order: int, book_id: str) -> ModuleV2:
    inside = [x for x in nodes if a < x.index <= b]
    deeper = sorted({x.level for x in inside if x.level > node.level})
    child_level = deeper[0] if deeper else None
    children = [x for x in inside if x.level == child_level] if child_level is not None else []
    if not children:
        children = _heading_children(bk, a, b, {x.index for x in nodes})
    boundaries = {x.index for x in inside} | {c.index for c in children}
    all_runs = _apparatus_runs(bk, a, b, boundaries)
    run_starts = {r[0] for r in all_runs}
    children = [c for c in children if not any(r[0] <= c.index <= r[1] for r in all_runs)
                and c.index not in run_starts]
    runs = [r for r in all_runs if r[2] not in TAUGHT_APPARATUS]
    taught_runs = [r for r in all_runs if r[2] in TAUGHT_APPARATUS]
    for start, stop, reason, title in runs:
        exclude(start, stop, "body", reason, title, "body:apparatus-heading")
    for start, stop, _reason, _title in taught_runs:
        _mark_descriptions(bk, start, stop)

    def cut(start: int, stop: int) -> int:
        """End of a lesson piece: stop before the first apparatus run inside [start, stop]."""
        for r in runs:
            if start < r[0] <= stop:
                return r[0] - 1
        return stop

    module_title = clean_title(node.title, bk.vocab)
    label = _chapter_label(module_title)
    pieces: list[_Piece] = []
    first = children[0].index if children else b + 1
    intro_end = cut(a, first - 1)
    intro_words = bk.words(a, intro_end)
    for k, child in enumerate(children):
        nxt = children[k + 1].index - 1 if k + 1 < len(children) else b
        pieces.append(_Piece(child.index, cut(child.index, nxt), child.index, child.level, child.title, child.source,
                             children=[x for x in inside if x.level > child.level
                                       and child.index < x.index <= nxt]))
    if not children:
        pieces.append(_Piece(a, intro_end, node.index, node.level, node.title, node.source, kind="chapter"))
    elif intro_words > 0:
        pieces.insert(0, _Piece(a, intro_end, node.index, node.level, node.title, node.source, kind="chapter_intro"))
    else:
        pieces[0].start = a  # heading-only chapter opening: folded into the first section
    pieces = _plan_sizes(bk, pieces, config.size_policy)
    for start, stop, _reason, _title in taught_runs:  # descriptions cut off by excluded notes
        if any(p.start <= start <= p.end for p in pieces):
            continue
        owner = [p for p in pieces if p.start < start][-1]
        owner.extras.append((start, stop))
    for p in pieces:
        p.subheadings = [i for i in range(p.start, p.end + 1) if bk.blocks[i].type == "heading" and i != p.anchor
                         and not (p.kind != "chapter_intro" and i == node.index) and bk.roles[i] is None]
    module_key = title_key(node.title)
    module_id = f"{_slug(module_title)}-{_digest(book_id, 'module', module_key)}"
    lessons = []
    for k, p in enumerate(pieces, start=1):
        lessons.append(_lesson(bk, p, k, module_id, module_title, label, node, book_id, config.size_policy))
    return ModuleV2(module_id=module_id, order=order, level=node.level, source=node.source,  # type: ignore[arg-type]
                    source_title=node.title, display_title=module_title, title_source=node.source,  # type: ignore[arg-type]
                    anchor_block=bk.blocks[node.index].block_id, lessons=lessons)


def _front_module(bk: _Book, pieces: list[_Piece], book_id: str, policy: LessonSizePolicy,
                  level: int) -> ModuleV2:
    """The opening module: preface, foreword, acknowledgments ... in book order (D-036 (2))."""
    module_id = f"{_slug(FRONT_MODULE_TITLE)}-{_digest(book_id, 'module', 'frontmatter')}"
    lessons = []
    for k, p in enumerate(pieces, start=1):
        p.subheadings = [i for i in range(p.start, p.end + 1) if bk.blocks[i].type == "heading" and i != p.anchor]
        display = clean_title(p.title, bk.vocab)
        words = bk.words(p.start, p.end)
        status, exception = "ok", None
        if words < policy.min_words:
            status, exception = "below_min", "front matter of the book (D-036), kept whole"
        elif words > policy.max_words:
            status, exception = "above_max", "front matter of the book (D-036), kept whole"
        lessons.append(LessonV2(
            lesson_id=f"{module_id}.{_slug(display)}-{_digest(book_id, module_id, title_key(p.title))}", order=k,
            level=p.level, kind="front_matter", source=p.source, source_title=p.title,  # type: ignore[arg-type]
            display_title=display, title_source=p.source,  # type: ignore[arg-type]
            anchor_block=bk.blocks[p.anchor].block_id,
            spine_span=OutlineSpanV2(start_block=bk.blocks[p.start].block_id, end_block=bk.blocks[p.end].block_id,
                                     pages=bk.pages(p.start, p.end)),
            word_count=words, size_status=status, size_exception=exception,  # type: ignore[arg-type]
            subheadings=[bk.blocks[i].block_id for i in p.subheadings],
            block_roles=_role_runs(bk, [(p.start, p.end)]),
            audio_adaptations=_audio_adaptations(bk, p.start, p.end, p.exception or "")))
    first = pieces[0]
    return ModuleV2(module_id=module_id, order=1, level=level, source=first.source,  # type: ignore[arg-type]
                    source_title=FRONT_MODULE_TITLE, display_title=FRONT_MODULE_TITLE,
                    title_source=first.source, anchor_block=bk.blocks[first.anchor].block_id,  # type: ignore[arg-type]
                    lessons=lessons)


def _audio_adaptations(bk: _Book, a: int, b: int, reason: str) -> list[AudioAdaptation]:
    """Blocks of a front-matter lesson that do not suit listening as they are (D-036 (2))."""
    mode = "acknowledgements" if reason == "acknowledgments" else None
    flags: dict[int, str] = {}
    content = []
    for i in range(a, b + 1):
        blk = bk.blocks[i]
        if blk.type in FURNITURE:
            continue
        content.append(i)
        if blk.type == "heading":
            if i != a:
                key, side = title_key(blk.text), classify(blk.text)
                mode = ("acknowledgements" if side and side[1] == "acknowledgments" else
                        "legal_notice" if key in _LEGAL_HEADINGS else
                        "reference_note" if key in _NOTE_HEADINGS else None)
            continue
        if blk.type == "table":
            flags[i] = "table"
        elif mode is not None:
            flags[i] = mode
        elif _COPYRIGHT.search(blk.text):
            flags[i] = "legal_notice"
    run: list[int] = []  # long lists: runs of list items (page furniture does not break a run)
    for i in [*content, None]:
        if i is not None and bk.blocks[i].type == "list_item" and i not in flags:
            run.append(i)
            continue
        if len(run) >= LONG_LIST_ITEMS:
            flags.update({j: "long_list" for j in run})
        run = []
    out: list[AudioAdaptation] = []
    current: list | None = None
    for i in content:
        flag = flags.get(i)
        if flag is not None and current is not None and current[2] == flag:
            current[1] = i
            continue
        if current is not None:
            out.append(_adaptation(bk, *current))
        current = [i, i, flag] if flag is not None else None
    if current is not None:
        out.append(_adaptation(bk, *current))
    return out


def _adaptation(bk: _Book, a: int, b: int, reason: str) -> AudioAdaptation:
    return AudioAdaptation(start_block=bk.blocks[a].block_id, end_block=bk.blocks[b].block_id,
                           reason=reason, note=_ADAPTATION_NOTES[reason])  # type: ignore[arg-type]


def _heading_children(bk: _Book, a: int, b: int, node_idx: set[int]) -> list[_Node]:
    """A chapter's own headings, when the structure source has no deeper level (e.g. chapter-only
    bookmarks): the shallowest heading level inside the chapter, minus captions, TOC lines and
    apparatus headings."""
    heads = []
    for i in range(a + 1, b + 1):
        blk = bk.blocks[i]
        if blk.type != "heading" or i in node_idx or apparatus_reason(blk.text):
            continue
        if _CAPTION_LIKE.match(blk.text) or _TOC_LIKE.search(blk.text) or len(blk.text) > 160:
            continue
        heads.append(_Node(i, blk.level or 99, blk.text, "heading"))
    if not heads:
        return []
    level = min(h.level for h in heads)
    return [h for h in heads if h.level == level]


def _plan_sizes(bk: _Book, pieces: list[_Piece], policy: LessonSizePolicy) -> list[_Piece]:
    out: list[_Piece] = []
    for p in pieces:
        words = bk.words(p.start, p.end)
        if words > policy.max_words and p.children:
            level = min(c.level for c in p.children)
            kids = [c for c in p.children if c.level == level]
            lead_end = kids[0].index - 1
            parts = []
            if bk.words(p.start, lead_end) > 0:
                parts.append(_Piece(p.start, lead_end, p.anchor, p.level, p.title, p.source, p.kind,
                                    exception="section lead before its subsections"))
            for k, c in enumerate(kids):
                stop = kids[k + 1].index - 1 if k + 1 < len(kids) else p.end
                parts.append(_Piece(c.index, stop, c.index, c.level, c.title, c.source))
            if bk.words(p.start, lead_end) == 0:
                parts[0].start = p.start
            out.extend(parts)
            continue
        out.append(p)
    merged: list[_Piece] = []
    for p in out:
        words = bk.words(p.start, p.end)
        prev = merged[-1] if merged else None
        if (words < policy.min_words and p.source == "heading" and p.kind == "section" and prev is not None
                and prev.kind == "section" and prev.source == "heading"):
            prev.end = p.end
            continue
        merged.append(p)
    return merged


def _mark_descriptions(bk: _Book, start: int, stop: int) -> None:
    """Block roles of a figure-description section: its heading, the 'Back to Figure' markers that
    open each description, and the description text (captions, tables ...)."""
    for i in range(start, stop + 1):
        blk = bk.blocks[i]
        if blk.type in FURNITURE:
            continue
        if i == start:
            bk.roles[i] = "figure_description_heading"
        elif _FIGURE_MARKER.match(blk.text):
            bk.roles[i] = "figure_description_marker"
        else:
            bk.roles[i] = "figure_description"


def _role_runs(bk: _Book, ranges: list[tuple[int, int]]) -> list[BlockRoleRun]:
    runs: list[BlockRoleRun] = []
    for a, b in ranges:
        current: tuple[int, int, str] | None = None
        for i in range(a, b + 1):
            role = bk.roles[i]
            if role is None:
                if bk.blocks[i].type in FURNITURE and current is not None:
                    continue  # a page number inside a run does not break it
                current = None
                continue
            if current is not None and current[2] == role and runs:
                current = (current[0], i, role)
                runs[-1] = BlockRoleRun(start_block=bk.blocks[current[0]].block_id, end_block=bk.blocks[i].block_id,
                                        role=role)  # type: ignore[arg-type]
                continue
            current = (i, i, role)
            runs.append(BlockRoleRun(start_block=bk.blocks[i].block_id, end_block=bk.blocks[i].block_id,
                                     role=role))  # type: ignore[arg-type]
    return runs


def _lesson(bk: _Book, p: _Piece, order: int, module_id: str, module_title: str, label: str, node: _Node,
            book_id: str, policy: LessonSizePolicy) -> LessonV2:
    words = bk.words(p.start, p.end) + sum(bk.words(x, y) for x, y in p.extras)
    if p.kind == "chapter_intro":
        generic = title_key(module_title) in _GENERIC
        display = module_title if generic else f"{label} · Introduction"
        lesson_id = f"{module_id}.intro"
    elif p.kind == "chapter":
        display = module_title
        lesson_id = f"{module_id}.chapter"
    else:
        display = clean_title(p.title, bk.vocab)
        if title_key(display) in _GENERIC and title_key(display) != title_key(module_title):
            display = f"{label} · {display}"
        lesson_id = f"{module_id}.{_slug(display)}-{_digest(book_id, module_id, title_key(p.title))}"
    status = "ok"
    exception = None
    if words < policy.min_words:
        status = "below_min"
        exception = p.exception or {
            "chapter_intro": "chapter introduction (text before the first section)",
            "chapter": "chapter without sections in the source",
        }.get(p.kind) or ("first section of its chapter" if p.source == "heading"
                          else "kept: a section of the book's own structure")
    elif words > policy.max_words:
        status = "above_max"
        exception = "no finer structure in the source"
    title_source = node.source if p.kind != "section" else p.source
    return LessonV2(
        lesson_id=lesson_id, order=order, level=p.level, kind=p.kind,  # type: ignore[arg-type]
        source=p.source if p.kind == "section" else node.source,  # type: ignore[arg-type]
        source_title=p.title if p.kind == "section" else node.title, display_title=display,
        title_source=title_source,  # type: ignore[arg-type]
        anchor_block=bk.blocks[p.anchor].block_id,
        spine_span=OutlineSpanV2(start_block=bk.blocks[p.start].block_id, end_block=bk.blocks[p.end].block_id,
                                 pages=bk.pages(p.start, p.end)),
        extra_spans=[LessonExtraSpan(start_block=bk.blocks[x].block_id, end_block=bk.blocks[y].block_id,
                                     pages=bk.pages(x, y), role="figure_descriptions") for x, y in p.extras],
        word_count=words, size_status=status, size_exception=exception,  # type: ignore[arg-type]
        subheadings=[bk.blocks[i].block_id for i in p.subheadings],
        block_roles=_role_runs(bk, [(p.start, p.end), *p.extras]))


def _unique_ids(modules: list[ModuleV2]) -> None:
    """Disambiguate repeated titles deterministically (second 'Conclusion' -> '-2')."""
    seen: dict[str, int] = {}
    for k, m in enumerate(modules):
        count = seen.get(m.module_id, 0)
        seen[m.module_id] = count + 1
        mid = m.module_id if count == 0 else f"{m.module_id}-{count + 1}"
        lessons = []
        lseen: dict[str, int] = {}
        for lesson in m.lessons:
            lid = mid + lesson.lesson_id[len(m.module_id):]
            c = lseen.get(lid, 0)
            lseen[lid] = c + 1
            lessons.append(lesson.model_copy(update={"lesson_id": lid if c == 0 else f"{lid}-{c + 1}"}))
        modules[k] = m.model_copy(update={"module_id": mid, "lessons": lessons})


__all__ = ["BUILDER_NAME", "BUILDER_VERSION", "DEFAULT_SIZE_POLICY", "OutlineConfig", "OutlineUnavailable",
           "DEFAULT_FRONT_LESSONS", "apparatus_reason", "build_outline_v2", "classify", "find_heading"]
