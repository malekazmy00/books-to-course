"""S2 (R3): deterministic, versioned normalization of a parse intermediate into ParsedBook v2.

Input is one of the S1 intermediates (``layout.py``): Docling's DoclingDocument export per
page batch, or the PDF text layer alone. Both are first reduced to *fragments* (one layout
item, or one unwrapped paragraph of the text layer, with page + bbox), then one pipeline runs:

1. characters: ligatures / full-width forms (per-character NFKC), invisible characters, spaced
   punctuation; then letter-spaced OCR/small-caps repair against the book's own vocabulary
   (rules shared with translation units, ``b2c_core.source_text``); then glued words are split
   (``glued.py``: the book's own bigrams + the pinned wordfreq lexicon, proper nouns protected),
   with the dash the pdfium extraction of the same page prints between the parts, if any;
2. furniture: Docling page headers/footers, plus text repeated at page edges (>= 3 pages and
   mostly at an edge), become running_header / running_footer / page_number blocks; nothing
   is dropped;
3. headings: PDF bookmarks are matched to a heading (or short text item) on their page by a
   spacing-insensitive key; a matched heading takes the bookmark's level and clean title, an
   unmatched bookmark inserts a heading marked ``heading_from_outline``; other headings get a
   level below the enclosing bookmark level (or from their numbering when there is no outline);
4. zones and apparatus: front / body / back matter from top-level heading titles; inside
   Contents / Notes / Bibliography / Index sections items become toc_entry / endnote /
   bibliography_entry / index_entry; outside them, runs of TOC lines, index locators,
   numbered citation notes and bibliography entries are recognised from the text;
5. notes: runs of notes in one item are split, labels come from the list marker or the
   "87." prefix; a paragraph or note cut by a page or column break is joined (multi-page
   refs; hyphen at the break removed when the joined word is attested in the book);
6. markers: in-text note numbers are found with the note-sequence rules, linked to the
   footnote on the same page or to the endnote of the matching notes run, and removed from
   ``text`` (kept as ``NoteRef``); weak candidates that link to nothing are left in the text;
7. quality flags per block (cut at page break, letter-spacing / ligature residue, unexpected
   script, note without label). Block length is not a defect (D-043): a 2,500-character
   academic paragraph is one paragraph.

Same intermediate + same NORMALIZER_VERSION => byte-identical ParsedBook v2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from b2c_contracts import (
    BlockV2, Note, NoteRef, OutlineEntry, PageInfo, PageRef, ParsedBookV2,
)
from .glued import GlueSplitter, Separators, reference_separators
from b2c_core.source_text import (
    BULLET, CAPTION, CONTINUATION_WORDS, NAV_LABELS, NOTE_LABEL, PAGE_NUMBER, TERMINAL, MarkerSequence, Vocab,
    citation_score, fix_punct_spacing, heading_key, index_like, is_bibliographic,
    join_hyphen, marker_candidates, normalize_chars, repair_letter_spacing, section_word,
    split_inline_notes, split_lines, toc_like,
)

NORMALIZER_NAME = "b2c-normalize"
NORMALIZER_VERSION = "3"  # 3: paragraph ends, figure captions, image OCR, note runs, hyphens (D-045)
CUT_MIN_WORDS = 8  # shorter page-final lines (epigraph attributions, labels) are not cut prose

_FRONT_KEYS = ("cover", "titlepage", "halftitle", "copyright", "contents", "tableofcontents",
               "dedication", "acknowledg", "preface", "foreword", "listof", "figures", "tables",
               "abbreviations", "epigraph", "praisefor", "aboutthisbook", "frontmatter", "series")
_BACK_KEYS = ("notes", "endnotes", "bibliography", "references", "workscited", "selectbibliography",
              "furtherreading", "index", "glossary", "appendix", "abouttheauthor", "aboutthecontributors",
              "abouttheeditor", "credits", "permissions", "backmatter", "afterword")
_UNEXPECTED_SCRIPT = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\ue000-\uf8ff\ufffd]")
_LIGATURE = re.compile("[\ufb00-\ufb06]")
_TRAILING_MARKER = re.compile(r"(?<=[.!?…:;’”\"')\]])\s?\d{1,3}$")
_FOOTNOTE_LABEL = re.compile(r"^(\d{1,3}|[*†‡§])\s+(?=\S)")
_URLISH = re.compile(r"://|www\.|\.pdf\b|%20|\S+/\S+/\S+")
_SPACED_CLOSING_QUOTE = re.compile(r'(?<=[.!?,;:]) "(?=\s|$)')
_QUOTES = "“‘\"'(["
_FLOAT_KINDS = frozenset({"figure", "caption", "table", "equation", "footnote"})
_FURNITURE = frozenset({"running_header", "running_footer", "page_number"})
_DOCLING_KIND = {
    "title": "heading", "section_header": "heading", "text": "paragraph", "paragraph": "paragraph",
    "list_item": "list_item", "footnote": "footnote", "caption": "caption", "table": "table",
    "picture": "figure", "chart": "figure", "formula": "equation", "code": "code",
    "reference": "bibliography_entry", "checkbox_selected": "list_item", "checkbox_unselected": "list_item",
    "document_index": "toc_entry",
}


@dataclass(frozen=True)
class BookMeta:
    book_id: str
    title: str
    role: str  # "spine" | "supplementary"


@dataclass
class _Frag:
    refs: list[tuple[int, tuple[float, float, float, float] | None]]  # (page, top-left bbox)
    label: str
    raw: str
    source: str
    marker: str | None = None
    ends_full_line: bool = False
    text: str = ""
    tags: list[str] = field(default_factory=list)
    kind: str = ""
    level: int | None = None
    level_source: str | None = None
    outline_idx: int | None = None
    note_label: str | None = None
    zone: str = "body"
    section: str | None = None  # toc / notes / bibliography / index section the item sits in
    parts: list[tuple[str, tuple[int, tuple[float, float, float, float] | None]]] = field(default_factory=list)
    # ^ the same-page pieces (text, ref) of a layout item kept together as one fragment

    @property
    def page(self) -> int:
        return self.refs[0][0]

    @property
    def last_page(self) -> int:
        return self.refs[-1][0]


@dataclass
class _Item:
    frags: list[_Frag]
    text: str
    kind: str
    tags: list[str]
    flags: list[str] = field(default_factory=list)
    note_refs: list[tuple[int, str, str | None]] = field(default_factory=list)  # (offset, label, note key)
    note_key: str | None = None

    @property
    def head(self) -> _Frag:
        return self.frags[0]


# ----------------------------------------------------------------------------- fragments
def _bbox(prov: dict[str, Any], height: float | None) -> tuple[float, float, float, float] | None:
    box = prov.get("bbox")
    if not box:
        return None
    left, right, top, bottom = float(box["l"]), float(box["r"]), float(box["t"]), float(box["b"])
    if str(box.get("coord_origin", "")).upper().endswith("BOTTOMLEFT") and height is not None:
        top, bottom = height - top, height - bottom
    return (round(min(left, right), 2), round(min(top, bottom), 2),
            round(max(left, right), 2), round(max(top, bottom), 2))


def _table_markdown(table: dict[str, Any]) -> str:
    grid = (table.get("data") or {}).get("grid") or []
    rows = []
    for row in grid:
        cells = [re.sub(r"\s+", " ", str(cell.get("text", ""))).strip() for cell in row]
        rows.append("| " + " | ".join(cells) + " |")
    if len(rows) >= 2:
        rows.insert(1, "| " + " | ".join("---" for _ in grid[0]) + " |")
    return "\n".join(rows)


def _docling_frags(doc: dict[str, Any]) -> list[_Frag]:
    first = int((doc.get("b2c_batch") or {}).get("first_page", 0))
    heights = {int(k): float(v["size"]["height"]) for k, v in (doc.get("pages") or {}).items()
               if v.get("size")}
    out: list[_Frag] = []
    seen: set[str] = set()

    def node(ref: str) -> dict[str, Any] | None:
        parts = ref.split("/")
        if len(parts) != 3 or parts[1] not in ("texts", "groups", "tables", "pictures", "key_value_items", "form_items"):
            return None
        items = doc.get(parts[1]) or []
        idx = int(parts[2])
        return items[idx] if idx < len(items) else None

    def refs_of(item: dict[str, Any]) -> list[tuple[int, tuple[float, float, float, float] | None]]:
        return [(int(p["page_no"]), _bbox(p, heights.get(int(p["page_no"])))) for p in item.get("prov") or []
                if int(p.get("page_no", 0)) >= 1]

    def descendant_texts(item: dict[str, Any]) -> list[dict[str, Any]]:
        found = []
        for child in item.get("children") or []:
            c = node(child["$ref"])
            if c is None or child["$ref"] in seen:
                continue
            seen.add(child["$ref"])
            if "text" in c and c.get("label") != "caption":
                found.append(c)
            found.extend(descendant_texts(c))
        return found

    def walk(ref: str) -> None:
        if ref in seen:
            return
        item = node(ref)
        if item is None:
            return
        seen.add(ref)
        kind = ref.split("/")[1]
        source = f"p{first:04d}{ref}"
        layer = item.get("content_layer", "body")
        if layer == "invisible":
            return
        if kind == "groups":
            if item.get("label") == "inline":
                texts = [node(c["$ref"]) for c in item.get("children") or []]
                texts = [t for t in texts if t is not None and t.get("text")]
                for c in item.get("children") or []:
                    seen.add(c["$ref"])
                refs = [r for t in texts for r in refs_of(t)]
                if texts and refs:
                    out.append(_Frag(refs=_dedupe_refs(refs), label="text",
                                     raw=" ".join(str(t["text"]) for t in texts), source=source))
                return
            for child in item.get("children") or []:
                walk(child["$ref"])
            return
        refs = refs_of(item)
        if kind == "tables":
            if refs:
                out.append(_Frag(refs=refs, label="table", raw=_table_markdown(item), source=source))
        elif kind == "pictures":
            # the picture's captions are its children too (D-045: SAT lost 20 'Figure n.n' titles
            # with the picture's inner text); they become caption fragments, above or below it
            cap_refs = [c["$ref"] for c in item.get("captions") or []]
            cap_refs += [c["$ref"] for c in item.get("children") or []
                         if (node(c["$ref"]) or {}).get("label") == "caption" and c["$ref"] not in cap_refs]
            captions = []
            for cref in cap_refs:
                cap = node(cref)
                if cap is None or cref in seen or not str(cap.get("text", "")).strip() or not refs_of(cap):
                    continue
                seen.add(cref)
                captions.append(_Frag(refs=refs_of(cap), label="caption", raw=str(cap["text"]),
                                      source=f"p{first:04d}{cref}"))
            inner = descendant_texts(item)
            if refs:
                top = refs[0][1][1] if refs[0][1] else None
                above = [c for c in captions if top is not None and c.refs[0][1] is not None
                         and c.refs[0][0] == refs[0][0] and c.refs[0][1][1] < top]
                out.extend(above)
                out.append(_Frag(refs=refs, label="picture", source=source,
                                 raw=" ".join(str(t.get("text", "")) for t in inner if t.get("text"))))
                out.extend(c for c in captions if c not in above)
            else:
                out.extend(captions)
        elif kind == "texts":
            label = str(item.get("label", "text"))
            if layer in ("furniture", "background") and label not in ("page_header", "page_footer"):
                label = "page_header"
            if layer == "notes" and label == "text":
                label = "footnote"
            parent = str((item.get("parent") or {}).get("$ref", ""))
            if label == "footnote" and parent.startswith(("#/tables/", "#/pictures/")):
                label = "caption"  # a table's or figure's legend, not a note of the page (SAT p. 594)
            if refs and str(item.get("text", "")).strip():
                marker = item.get("marker") if label == "list_item" else None
                pieces = _split_by_prov(str(item.get("text", "")), item.get("prov") or [], refs)
                for n, (piece, piece_refs, parts) in enumerate(pieces):
                    out.append(_Frag(refs=piece_refs, label=label, raw=piece, parts=parts if len(parts) > 1 else [],
                                     source=source if len(pieces) == 1 else f"{source}[{n}]",
                                     marker=str(marker) if marker and n == 0 else None))
        for child in item.get("children") or []:
            walk(child["$ref"])

    for child in (doc.get("body") or {}).get("children") or []:
        walk(child["$ref"])
    for child in (doc.get("furniture") or {}).get("children") or []:
        walk(child["$ref"])
    return out


def _split_by_prov(text: str, prov: list[dict[str, Any]], refs) -> list[tuple[str, list, list]]:
    """Docling joins some items across pages itself (and sometimes wrongly: a TOC title with the
    next page's acknowledgements). Cut the item back into one fragment per page at its prov
    charspans, so S2 decides the joins with its own rules; the cut moves to the nearest space
    (charspans can be off by the characters Docling removed when it de-hyphenated)."""
    pages = [r[0] for r in refs]
    if len(refs) < 2 or len(prov) != len(refs):
        return [(text, refs, [])]
    cuts = [0]
    for p in prov[1:]:
        start = int((p.get("charspan") or [0, 0])[0])
        start = min(max(start, cuts[-1] + 1), len(text))
        near = [i for i in range(max(cuts[-1] + 1, start - 4), min(len(text), start + 3)) if text[i] == " "]
        space = min(near, key=lambda i: (abs(i - (start - 1)), i)) if near else text.find(" ", start)
        cuts.append(start if space < 0 or space - start > 40 else space)
    cuts.append(len(text))
    pieces: list[tuple[str, list, list]] = []
    for (a, b), ref in zip(zip(cuts, cuts[1:]), refs):
        piece = text[a:b].strip()
        if not piece:
            continue
        if pieces and pieces[-1][1][-1][0] == ref[0]:  # same page, other column: keep together
            pieces[-1] = (pieces[-1][0] + " " + piece, pieces[-1][1] + [ref], pieces[-1][2] + [(piece, ref)])
        else:
            pieces.append((piece, [ref], [(piece, ref)]))
    if len(set(pages)) < 2:
        # one page: the item stays whole as before; its pieces are kept for the image-text check
        return [(text, refs, pieces[0][2] if len(pieces) == 1 else [])]
    return pieces or [(text, refs, [])]


def _dedupe_refs(refs):
    out = []
    for page, box in refs:
        if out and out[-1][0] == page and box is not None and out[-1][1] is not None:
            a = out[-1][1]
            out[-1] = (page, (min(a[0], box[0]), min(a[1], box[1]), max(a[2], box[2]), max(a[3], box[3])))
        elif not out or out[-1] != (page, box):
            out.append((page, box))
    return out


def _letters_key(line: str) -> str:
    return re.sub(r"[\W\d_]+", "", line.lower())


def _edge_furniture_keys(pages_lines: list[list[str]], edge: int = 2,
                         page_numbers: list[int] | None = None) -> set[str]:
    """Letter keys of lines that repeat at page edges like running heads do: on >= 3 pages,
    >= 80% of uses at an edge, and on nearby pages (>= 2 pairs at most 2 pages apart).
    The last rule keeps recurring section titles that happen to open pages ('When to Use
    It' in a book whose every technique starts a new page) in the text."""
    numbers = page_numbers or list(range(len(pages_lines)))
    at_edge: dict[str, list[int]] = {}
    anywhere: dict[str, int] = {}
    for p, lines in zip(numbers, pages_lines):
        for i, line in enumerate(lines):
            key = _letters_key(line)
            if len(key) < 3 or len(line) > 120:
                continue
            anywhere[key] = anywhere.get(key, 0) + 1
            if i < edge or i >= len(lines) - edge:
                pages = at_edge.setdefault(key, [])
                if not pages or pages[-1] != p:
                    pages.append(p)
    return {k for k, pages in at_edge.items() if len(pages) >= 3 and len(pages) >= 0.8 * anywhere[k]
            and sum(1 for a, b in zip(pages, pages[1:]) if b - a <= 2) >= 2}


def _typical_line_length(pages: list[str]) -> float:
    lengths = sorted(len(line) for t in pages for line in t.split("\n") if len(line) >= 10)
    if len(lengths) < 20:
        return 70.0
    return float(lengths[int(len(lengths) * 0.75)])


def _text_layer_frags(text_layer: dict[str, Any], outline_keys: dict[int, set[str]]) -> list[_Frag]:
    pages = text_layer["pages"]
    pages_lines = [[normalize_chars(ln)[0] for ln in p["text"].split("\n") if ln.strip()] for p in pages]
    furniture = _edge_furniture_keys(pages_lines)
    vocab = Vocab(["\n".join(lines) for lines in pages_lines])
    typical = _typical_line_length(["\n".join(lines) for lines in pages_lines])
    from .pdf_book import _is_heading_line

    out: list[_Frag] = []
    for p, (page, lines) in enumerate(zip(pages, pages_lines)):
        page_no = int(page["page_no"])
        n = len(lines)
        body: list[str] = []
        tail: list[_Frag] = []

        def flush() -> None:
            for chunk, _single, tags, full in split_lines("\n".join(body), typical, vocab):
                label = "list_item" if BULLET.match(chunk) else "text"
                out.append(_Frag(refs=[(page_no, None)], label=label, raw=chunk, ends_full_line=full,
                                 source=f"page{page_no}", tags=[t for t in tags]))
            body.clear()

        for i, line in enumerate(lines):
            edge_top, edge_bottom = i < 2, i >= n - 2
            if (edge_top or edge_bottom) and (PAGE_NUMBER.match(line) or _letters_key(line) in furniture):
                top = edge_top and not body
                frag = _Frag(refs=[(page_no, None)], label="page_header" if top else "page_footer",
                             raw=line, source=f"page{page_no}:line{i}")
                (out if top else tail).append(frag)
                continue
            key = heading_key(line)
            is_head = key in outline_keys.get(page_no, set()) if outline_keys else _is_heading_line(line)
            if is_head and len(line) <= 140:
                flush()
                out.append(_Frag(refs=[(page_no, None)], label="section_header", raw=line,
                                 source=f"page{page_no}:line{i}"))
                continue
            body.append(line)
        flush()
        out.extend(tail)
    return out


# ----------------------------------------------------------------------------- text-layer evidence (D-045)
_LETTER_TOKEN = re.compile(r"[a-z]+")
_PARA_END = re.compile(r"[.!?][\"”’)]?$")
_PARA_START = re.compile(r"[\"“‘(]?[A-Z]")
_IMAGE_TEXT_LABELS = frozenset({"text", "paragraph", "section_header", "title", "list_item", "footnote",
                                "checkbox_selected", "checkbox_unselected"})
IMAGE_OVERLAP_MIN = 0.5  # share of an item's box inside raster images (as in the quality gate)
IMAGE_TEXT_ATTESTED_MAX = 0.5  # an item whose words the text layer prints at least this often is text


def _reference_text(page: dict[str, Any]) -> str:
    """The page's independent extraction: pdfium when present, else pypdf."""
    text = page.get("text_pdfium") if page.get("text_pdfium") is not None else page.get("text")
    return (text or "").replace("\ufffe", "").replace("\x02", "").replace("\r\n", "\n")


def paragraph_ends(page_text: str) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Paragraph ends the text layer shows by its line geometry: a line shorter than 80% of the
    page's typical line that closes a sentence, followed by a full-length line that opens one
    (the rule of the quality gate's merged-paragraph check, D-043). Returns (last 3-4 words of the
    short line, first 3 words of the next) per end; pages of fewer than 5 full lines say nothing."""
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    full = sorted(len(ln) for ln in lines if len(ln) >= 30)
    if len(full) < 5:
        return []
    typical = full[len(full) // 2] if len(full) % 2 else (full[len(full) // 2 - 1] + full[len(full) // 2]) / 2
    out = []
    for a, b in zip(lines, lines[1:-1]):  # the page's last line does not open a paragraph here
        if len(a) < 0.8 * typical <= len(b) and _PARA_END.search(a) and _PARA_START.match(b):
            head, tail = _LETTER_TOKEN.findall(a.lower())[-4:], _LETTER_TOKEN.findall(b.lower())[:3]
            if len(head) >= 3 and len(tail) >= 3:
                out.append((tuple(head), tuple(tail)))
    return out


def _split_at_paragraph_ends(frags: list[_Frag], text_layer: dict[str, Any]) -> list[_Frag]:
    """Cut a body item that holds the end of one paragraph and the start of the next (SAT: 64
    such ends inside Docling items). The cut needs the text layer's paragraph end on the item's
    page, the same words on both sides of it in the item, a sentence end before it and a
    capital after it. Both pieces keep the item's page box (the layout model gave one box)."""
    ends = {int(p["page_no"]): paragraph_ends(_reference_text(p)) for p in text_layer["pages"]}
    sep = r"[^A-Za-z]+"
    out: list[_Frag] = []
    for f in frags:
        pages = {p for p, _ in f.refs}
        if f.label not in ("text", "paragraph", "list_item") or len(pages) != 1 or not ends.get(f.page):
            out.append(f)
            continue
        cuts = set()
        for head, tail in ends[f.page]:
            pattern = (r"(?<![A-Za-z])" + sep.join(map(re.escape, head)) + r"(?P<gap>[^A-Za-z]+)"
                       + sep.join(map(re.escape, tail)) + r"(?![A-Za-z])")
            for m in re.finditer(pattern, f.raw, re.I):
                gap = m.group("gap")
                spaces = [i for i, c in enumerate(gap) if c.isspace()]
                if not spaces:
                    continue
                cut = m.start("gap") + spaces[-1]
                left, right = f.raw[:cut].rstrip(), f.raw[cut:].lstrip()
                if TERMINAL.search(_strip_trailing_marker(left)) and _PARA_START.match(right):
                    cuts.add(cut)
        if not cuts:
            out.append(f)
            continue
        bounds = [0, *sorted(cuts), len(f.raw)]
        pieces = [f.raw[a:b].strip() for a, b in zip(bounds, bounds[1:])]
        for n, piece in enumerate(p for p in pieces if p):
            out.append(_Frag(refs=list(f.refs), label=f.label if n == 0 else "text", raw=piece,
                             source=f"{f.source}:para{n}", marker=f.marker if n == 0 else None,
                             tags=f.tags + ["split_at_paragraph_end"]))
    return out


def _overlap(box: tuple[float, float, float, float], images: list[list[float]]) -> float:
    area = max(1e-6, (box[2] - box[0]) * (box[3] - box[1]))
    covered = 0.0
    for left, top, right, bottom in images:
        w, h = min(box[2], right) - max(box[0], left), min(box[3], bottom) - max(box[1], top)
        if w > 0 and h > 0:
            covered += w * h
    return covered / area


def _group_image_ocr(frags: list[_Frag], text_layer: dict[str, Any]) -> list[_Frag]:
    """Text the layout model OCR'd from a raster figure on a page that has a text layer (SAT pp.
    72, 433: a figure read as headings and paragraphs, 'Pathmays to Unlikety Outcome') is the
    figure's content: consecutive such items become one figure fragment. An item counts when its
    box lies inside the page's raster images and the text layer does not print most of its words
    (text over an image that the text layer has stays text). Scanned pages (no text layer, or one
    image covering the page) are left alone: there the OCR text is the page."""
    images: dict[int, list[list[float]]] = {}
    words: dict[int, set[str]] = {}
    for p in text_layer["pages"]:
        page_words = set(re.findall(r"[a-z]{3,}", _reference_text(p).lower()))
        area = float(p.get("width") or 0) * float(p.get("height") or 0)
        boxes = [b for b in p.get("image_boxes") or []
                 if not area or (b[2] - b[0]) * (b[3] - b[1]) < 0.9 * area]
        if boxes and len(page_words) >= 3:
            images[int(p["page_no"])] = boxes
            words[int(p["page_no"])] = page_words

    def is_image_text(f: _Frag) -> bool:
        if f.label not in _IMAGE_TEXT_LABELS or f.page not in images or any(b is None for _, b in f.refs) \
                or len({p for p, _ in f.refs}) != 1:
            return False
        if any(_overlap(b, images[f.page]) < IMAGE_OVERLAP_MIN for _, b in f.refs):
            return False
        mine = re.findall(r"[a-z]{3,}", f.raw.lower())
        return not mine or sum(w in words[f.page] for w in mine) < IMAGE_TEXT_ATTESTED_MAX * len(mine)

    def separate(f: _Frag) -> list[_Frag]:
        """A layout item whose same-page pieces include OCR text of a raster figure (SAT p. 556:
        the page's first lines and the figure below them in one item): the text pieces stay one
        fragment, the image pieces become their own (grouped below)."""
        if not f.parts:
            return [f]
        flags = [is_image_text(_Frag(refs=[ref], label=f.label, raw=text, source=f.source)) for text, ref in f.parts]
        if all(flags) or not any(flags):
            return [f]
        kept: list[tuple[str, Any]] = []
        images_out: list[_Frag] = []
        for i, ((t, r), img) in enumerate(zip(f.parts, flags)):
            if not img:
                kept.append((t, r))
                continue
            # the image piece's box may also cover a text line above the figure: sentences at its
            # start that the text layer prints stay with the text (SAT p. 556)
            sentences = re.split(r"(?<=[.!?])\s+", t)
            lead = 0
            while lead < len(sentences) - 1:
                mine = re.findall(r"[a-z]{3,}", sentences[lead].lower())
                if len(mine) < 3 or sum(w in words[f.page] for w in mine) < 0.8 * len(mine):
                    break
                lead += 1
            if lead:
                kept.append((" ".join(sentences[:lead]), r))
            images_out.append(_Frag(refs=[r], label=f.label, raw=" ".join(sentences[lead:]),
                                    source=f"{f.source}@{i}", tags=list(f.tags)))
        text_frag = _Frag(refs=[r for _, r in kept], label=f.label, raw=" ".join(t for t, _ in kept),
                          source=f.source, marker=f.marker, tags=list(f.tags))
        return [text_frag, *images_out]

    frags = [g for f in frags for g in separate(f)]
    out: list[_Frag] = []
    run: list[_Frag] = []

    def flush() -> None:
        if run:
            out.append(_Frag(refs=_dedupe_refs([r for f in run for r in f.refs]), label="picture",
                             raw="\n".join(f.raw for f in run), source="+".join(f.source for f in run),
                             tags=["image_ocr_text"]))
            run.clear()

    for f in frags:
        if is_image_text(f) and (not run or run[-1].page == f.page):
            run.append(f)
            continue
        flush()
        if is_image_text(f):
            run.append(f)
        else:
            out.append(f)
    flush()
    return out


def _spaced_lines(text_layer: dict[str, Any]) -> dict[int, dict[str, list[str]]]:
    """Letter-spaced lines of the pypdf extraction whose word gaps are wider than its letter gaps
    ('c r i t i c a l  a p p r o a c h e s'): letters without spaces (lowercase) -> the words."""
    out: dict[int, dict[str, list[str]]] = {}
    for p in text_layer["pages"]:
        for line in (p.get("text") or "").splitlines():
            line = line.strip()
            tokens = line.split()
            if "  " not in line or len(tokens) < 4 or sum(len(t) == 1 for t in tokens) < 0.6 * len(tokens):
                continue
            found = [normalize_chars(re.sub(r"\s+", "", w))[0] for w in re.split(r"\s{2,}", line)]
            if len(found) >= 2 and all(found):
                out.setdefault(int(p["page_no"]), {})["".join(found).lower()] = found
    return out


def _respace(text: str, words: list[str]) -> str:
    """The characters of ``text`` (spaces dropped) regrouped into the lengths of ``words``."""
    chars = re.sub(r"\s+", "", text)
    out, i = [], 0
    for w in words:
        out.append(chars[i:i + len(w)])
        i += len(w)
    return " ".join(out)


# ----------------------------------------------------------------------------- pipeline steps
_HEADING_LABELS = frozenset({"title", "section_header", "page_header", "page_footer"})
_SHORT_COMMON = 20  # a word of <= 3 letters counts as a word only if the book uses it this often
# English words of 2-3 letters that are never taken for pieces of a tracked word
SHORT_WORDS = frozenset("""
of to in on at by as an or is it be we he me my us so no do go up if am
the and for are was can may has had his her its our out off all any now old too via per not but
new war art law age act end key two one six ten man men day way far few own say see use why how
who yes red big low top job aid god sex tax oil son set put run met led won lay bad due etc
she him they them who did get got let lot non pre sub pro mid neo
""".split())


def zipf(word: str) -> float:
    """General-English frequency (wordfreq 3.1.1, pinned): Zipf scale, 3.0 = 1 per million words."""
    from wordfreq import zipf_frequency

    return zipf_frequency(word, "en")


class _LexiconVocab(Vocab):
    """Book vocabulary (body text only, so spaced headings do not attest their own fragments),
    plus the general English lexicon for the *merged* form of a letter-spaced run ('conservat
    i sm' when the book prints 'conservatism' fewer than twice). Rule (a) of the repair is
    unchanged: only runs that contain fragments are merged."""

    def __init__(self, texts: list[str], lexicon):
        super().__init__(texts)
        self._lexicon = lexicon

    def is_word(self, merged: str) -> bool:
        return super().is_word(merged) or (self._lexicon is not None and len(merged) >= 5
                                           and self._lexicon(merged.lower()) >= 3.0)


def spacing_fragments(text: str, vocab: Vocab, lexicon=zipf) -> list[str]:
    """Tokens of `text` that look like pieces of a tracked (letter-spaced) word: a stray letter,
    a 2-3 letter token the book body rarely uses, or a longer token that is neither in the book
    nor in the English lexicon. Capitalised and all-caps tokens (names, acronyms) never count."""
    out = []
    tokens = [t.strip(".,;:!?()[]{}\"“”‘’'*•–—-") for t in re.sub(r"\([a-z]{1,2}\)", "", text).split()]
    for k, token in enumerate(tokens):
        if not token.isalpha() or not token.isascii():
            continue  # '3.3B', 'Practitioner’s', URLs, numbers
        if token[0].isupper() and len(token) > 1:
            continue
        low = token.lower()
        if len(low) == 1:
            neighbours = [tokens[j] for j in (k - 1, k + 1) if 0 <= j < len(tokens)]
            if token.isupper() and not any(len(n) == 1 and n.isalpha() for n in neighbours):
                continue  # 'Plan B', 'Option C': a lone capital is a label, not tracking
            if low not in ("a", "i"):
                out.append(token)
        elif len(low) <= 3:
            if low not in SHORT_WORDS and vocab.count(low) < _SHORT_COMMON:
                out.append(token)
        elif vocab.count(low) < 2 and (lexicon is None or lexicon(low) < 2.0):
            out.append(token)
    return out


def _known_cost(word: str, vocab: Vocab, lexicon) -> float | None:
    n = vocab.count(word)
    if len(word) <= 3:
        known = n >= _SHORT_COMMON or word in SHORT_WORDS
        return 9.0 - max(lexicon(word) if lexicon else 0.0, 3.0) if known else None
    z = lexicon(word) if lexicon else 0.0
    if z >= 3.0 or (len(word) >= 7 and z >= 2.0):
        return 9.0 - z
    return 6.0 if n >= 2 else None


def retrack(text: str, vocab: Vocab, lexicon=zipf) -> str:
    """Re-segment the letters of tracked heading text ('subt err ane an world', 'gramsci a san
    original thinker') into the cheapest sequence of known words (book words, English words);
    a chunk with no fragment, or whose letters cannot be fully segmented, is left unchanged.
    Only spaces move: letters, case and punctuation stay as they are."""
    parts = re.split(r"([^A-Za-z ]+)", text)
    out = []
    for part in parts:
        if not part or not re.search(r"[A-Za-z]", part) or not spacing_fragments(part, vocab, lexicon):
            out.append(part)
            continue
        letters = part.replace(" ", "")
        low = letters.lower()
        n = len(low)
        best: list[tuple[float, int] | None] = [None] * (n + 1)
        best[0] = (0.0, 0)
        for i in range(1, n + 1):
            for j in range(max(0, i - 25), i):
                if best[j] is None:
                    continue
                cost = _known_cost(low[j:i], vocab, lexicon)
                if cost is None:
                    continue
                total = best[j][0] + cost
                if best[i] is None or total < best[i][0]:
                    best[i] = (total, j)
        if best[n] is None:
            out.append(part)
            continue
        cuts, i = [], n
        while i > 0:
            j = best[i][1]
            cuts.append((j, i))
            i = j
        words = " ".join(letters[a:b] for a, b in reversed(cuts))
        lead = " " if part.startswith(" ") else ""
        trail = " " if part.endswith(" ") else ""
        out.append(lead + words + trail)
    return "".join(out)


def _tracked_word(text: str, vocab: Vocab) -> str | None:
    """'c o n t e n t s' / 'A C K N O W L ED G M EN T S': a whole item that is one tracked word.
    The pairwise repair would stop at the first known prefix ('content s')."""
    tokens = text.split(" ")
    if len(tokens) < 4 or any(not re.fullmatch(r"[A-Za-z]{1,2}", t) for t in tokens) \
            or sum(len(t) == 1 for t in tokens) < 3:
        return None
    word = "".join(tokens)
    if not (word.isupper() or word.islower()) and not (word[0].isupper() and word[1:].islower()):
        return None
    return word if vocab.is_word(word.lower()) or vocab.count(word) >= 1 else None


def _normalize_text(frags: list[_Frag], lexicon=zipf, references: dict[int, Separators] | None = None,
                    spaced: dict[int, dict[str, list[str]]] | None = None) -> Vocab:
    for f in frags:
        text, changed = normalize_chars(f.raw)
        if changed:
            f.tags.append("unicode_normalized")
        if f.label not in ("table", "code"):
            text = re.sub(r"\s*\n\s*", " ", text)
        fixed = "\n".join(fix_punct_spacing(line) for line in text.split("\n"))
        fixed = _SPACED_CLOSING_QUOTE.sub('"', fixed)  # Docling: 'and "science. "' -> 'and "science."'
        if fixed != text:
            f.tags.append("ocr_punct_spacing")
        f.text = fixed
    body = [f.text for f in frags if f.label not in _HEADING_LABELS]
    vocab = _LexiconVocab(body, lexicon)
    for f in frags:
        if f.label in ("table", "code", "formula", "picture"):
            continue
        key = re.sub(r"\s+", "", f.text).lower()
        words = next((spaced[p][key] for p in dict.fromkeys(p for p, _ in f.refs)
                      if spaced and key in spaced.get(p, {})), None) if len(f.text) <= 200 else None
        if words is not None:
            # the text layer prints this very line letter-spaced with wider gaps between words
            # (Gramsci small caps): take the word boundaries from it, not from the vocabulary
            respaced = _respace(f.text, words)
            if respaced != f.text:
                f.tags.append("ocr_letter_spacing")
                f.text = respaced
            continue
        repaired = _tracked_word(f.text, vocab) or repair_letter_spacing(f.text, vocab)
        if f.label in _HEADING_LABELS and len(repaired) <= 200:
            retracked = retrack(repaired, vocab, lexicon)
            if retracked != repaired:
                repaired = re.sub(r"(?<=\w) (?=['’]s\b)", "", retracked)
        if repaired != f.text:
            f.tags.append("ocr_letter_spacing")
            f.text = repaired
    splitter = GlueSplitter([f.text for f in frags if f.label not in _HEADING_LABELS], lexicon=lexicon)
    for f in frags:
        if f.label in ("code", "formula"):
            continue
        seps: Separators = {}
        for page in dict.fromkeys(p for p, _ in f.refs):
            seps.update((references or {}).get(page, {}))
        split, found = splitter.split(f.text, seps)
        if found:
            f.tags.append("glued_words_split")
            f.text = split
    return vocab


def _base_kind(f: _Frag) -> str:
    if f.label in ("page_header", "page_footer"):
        if PAGE_NUMBER.match(f.text.strip()):
            return "page_number"
        return "running_header" if f.label == "page_header" else "running_footer"
    nav = f.text.lower().strip(" .")
    if nav in NAV_LABELS and not nav.startswith("description"):
        f.tags.append("navigation_label")
        return "caption"  # e-book link label ('Back to Figure'): belongs to the figure apparatus
    if f.label in ("section_header", "title") and _URLISH.search(f.text):
        return "paragraph"  # a wrapped URL of a note, not a heading
    if f.label in ("section_header", "title") and CAPTION.match(f.text):
        f.tags.append("caption_from_heading")
        return "caption"  # 'Figure 9.11.1 Using Indicators ...' set large: a title of a figure (D-045)
    return _DOCLING_KIND.get(f.label, "paragraph")


def _mark_repeated_edges(frags: list[_Frag]) -> None:
    """Docling path: body items repeated at page edges are running heads the model missed."""
    by_page: dict[int, list[_Frag]] = {}
    for f in frags:
        if f.kind not in _FURNITURE:
            by_page.setdefault(f.page, []).append(f)
    pages = sorted(by_page)
    keys = _edge_furniture_keys([[f.text for f in by_page[p]] for p in pages], edge=1, page_numbers=pages)
    for p in pages:
        items = by_page[p]
        for i, f in enumerate(items):
            # headings never: 'NOTES' opens every chapter's notes at a page top
            if (i == 0 or i == len(items) - 1) and f.kind == "paragraph" and _letters_key(f.text) in keys \
                    and section_word(f.text) is None:
                f.kind = "running_header" if i == 0 else "running_footer"
                f.tags.append("furniture_by_repetition")


def _points_past_page(entry: dict[str, Any], page: int, frags: list[_Frag], by_page: dict[int, list[int]],
                      heights: dict[int, float]) -> bool:
    """A bookmark whose /XYZ top lies at or below the bottom of the last text on its page (or on a
    page without text) points past that page's content: what it marks starts the next page."""
    top = entry.get("top")
    if top is None or page not in heights:
        return False
    boxes = [frags[i].refs[0][1] for i in by_page.get(page, [])
             if frags[i].kind not in _FURNITURE and frags[i].refs[0][1] is not None]
    if len(boxes) < len([i for i in by_page.get(page, []) if frags[i].kind not in _FURNITURE]):
        return False  # some item has no box: the page's text extent is unknown
    return heights[page] - float(top) >= max((b[3] for b in boxes), default=0.0) - 2.0


def _match_outline(frags: list[_Frag], outline: list[dict[str, Any]], heights: dict[int, float]) -> list[_Frag]:
    """Attach bookmarks to heading fragments; insert headings for unmatched ones."""
    by_page: dict[int, list[int]] = {}
    for i, f in enumerate(frags):
        by_page.setdefault(f.page, []).append(i)
    used: set[int] = set()
    inserts: list[tuple[int, _Frag]] = []
    for k, entry in enumerate(outline):
        page = int(entry["page_index"]) + 1
        key = heading_key(entry["title"])
        if not key:
            continue
        best: tuple[tuple, int] | None = None
        candidates = [(i, 0) for i in by_page.get(page, [])]
        if _points_past_page(entry, page, frags, by_page, heights):
            # the destination lies below the page's last text: the heading opens the next page
            # (SAT pp. 550, 667); only the first two items of that page can be it
            nxt = [i for i in by_page.get(page + 1, []) if frags[i].kind not in _FURNITURE]
            candidates += [(i, 1) for i in nxt[:2]]
        for i, shift in candidates:
            f = frags[i]
            if i in used or f.kind in _FURNITURE or f.kind in ("table", "figure"):
                continue
            hk = heading_key(f.text)
            if not hk:
                continue
            exact = hk == key
            partial = len(hk) >= 4 and len(key) >= 4 and (key.endswith(hk) or key.startswith(hk) or hk.startswith(key)) \
                and min(len(hk), len(key)) >= 0.5 * max(len(hk), len(key))
            short_text = f.kind != "heading" and len(f.text) <= 150 and not f.text.rstrip().endswith(".")
            if not (exact or partial) or not (f.kind == "heading" or short_text):
                continue
            distance = 0.0
            if not shift and entry.get("top") is not None and f.refs[0][1] is not None and page in heights:
                distance = abs((heights[page] - float(entry["top"])) - f.refs[0][1][1])
            rank = (shift, 0 if exact else 1, 0 if f.kind == "heading" else 1, distance, i)
            if best is None or rank < best[0]:
                best = (rank, i)
        level = int(entry["depth"]) + 1
        if best is not None:
            f = frags[best[1]]
            used.add(best[1])
            if f.kind != "heading":
                f.tags.append("heading_from_outline_match")
            if best[0][0]:
                f.tags.append("outline_destination_on_previous_page")
            f.kind, f.level, f.level_source, f.outline_idx = "heading", level, "pdf_outline", k
            title = re.sub(r"\s+", " ", entry["title"]).strip()
            if heading_key(title) == heading_key(f.text) and title != f.text:
                f.text = title
                f.tags.append("title_from_outline")
            continue
        synthetic = _Frag(refs=[(page, None)], label="section_header", raw="", source=f"outline:{k}",
                          text=re.sub(r"\s+", " ", entry["title"]).strip(), tags=["heading_from_outline"],
                          kind="heading", level=level, level_source="pdf_outline", outline_idx=k)
        pos = None
        top = entry.get("top")
        for i in by_page.get(page, []):
            f = frags[i]
            if f.kind in _FURNITURE:
                continue
            if top is None or f.refs[0][1] is None or page not in heights or \
                    f.refs[0][1][1] >= heights[page] - float(top) - 2:
                pos = i
                break
        if pos is None:
            pos = next((i for i, f in enumerate(frags) if f.page > page), len(frags))
            if by_page.get(page):
                pos = by_page[page][-1] + 1
        inserts.append((pos, synthetic))
    result = list(frags)
    for pos, frag in sorted(inserts, key=lambda x: x[0], reverse=True):
        result.insert(pos, frag)
    return result


_NUMBERING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+\S")
_CHAPTER = re.compile(r"^(?:chapter|part)\s+(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)


def _heading_levels(frags: list[_Frag], has_outline: bool) -> None:
    current = 1
    for f in frags:
        if f.kind != "heading":
            continue
        if f.level_source == "pdf_outline":
            current = f.level or 1
            continue
        if has_outline:
            f.level, f.level_source = min(current + 1, 6), "layout"
            continue
        m = _NUMBERING.match(f.text)
        if _CHAPTER.match(f.text):
            f.level, f.level_source = 1, "numbering"
        elif m and "." in m.group(1):
            f.level, f.level_source = min(m.group(1).count(".") + 1, 6), "numbering"
        elif section_word(f.text) is not None:
            f.level, f.level_source = 1, "section_word"
        else:
            f.level, f.level_source = (1 if f.label == "title" else 2), "layout"


def _title_class(text: str) -> str | None:
    key = heading_key(re.sub(r"^(?:\d+(?:\.\d+)*\.?|[ivxlc]+\.)\s+", "", text.strip(), flags=re.I))
    if any(key.startswith(k) for k in _BACK_KEYS):
        return "back"
    if any(key.startswith(k) for k in _FRONT_KEYS):
        return "front"
    return "body" if key else None


_ABBREVIATIONS = frozenset({"abbreviations", "listofabbreviations", "abbreviationsused"})


def _apparatus_word(text: str) -> str | None:
    """Apparatus sections the shared section words do not name: a list of abbreviations is
    neither notes nor body text (its rows are 'SSG Antonio Gramsci, Subaltern Social Groups')."""
    return "abbreviations" if re.sub(r"[^a-z]", "", text.lower()) in _ABBREVIATIONS else None


def _zones_and_sections(frags: list[_Frag]) -> None:
    levels = [f.level for f in frags if f.kind == "heading" and f.level is not None]
    top = min(levels) if levels else 1
    state = "front_matter"
    sections: list[tuple[str, int]] = []  # open apparatus sections, innermost last
    saw_body = False
    for f in frags:
        if f.kind == "heading":
            level = f.level or top
            if level <= top:
                cls = _title_class(f.text)
                if cls == "body" and state == "front_matter":
                    state, saw_body = "body", True
                elif cls == "back" and state in ("body", "front_matter") and (saw_body or state == "body"):
                    state = "back_matter"
            word = section_word(f.text) or ("figure_descriptions" if f.text.lower().strip(" .")
                                             in NAV_LABELS else None) or _apparatus_word(f.text)
            # a heading closes the sections at its level or deeper; an enclosing one stays open
            # (D-045: Gramsci's 'abbreviations' inside 'Notes' ends at 'introduction', and the
            # notes that follow are still in the notes section)
            while sections and level <= sections[-1][1]:
                sections.pop()
            if word is not None:
                sections.append((word, level))
        section = sections[-1] if sections else None
        f.zone = "furniture" if f.kind in _FURNITURE else state
        f.section = section[0] if section is not None and f.kind != "heading" else None
    if not saw_body:
        for f in frags:
            if f.zone == "front_matter":
                f.zone = "body"


def _apparatus_kinds(frags: list[_Frag]) -> None:
    flow = [f for f in frags if f.kind not in _FURNITURE]
    for f in flow:
        if f.kind in ("paragraph", "list_item", "code", "footnote", "bibliography_entry"):
            if f.section == "toc" and f.kind != "footnote":
                f.kind = "toc_entry"
            elif f.section == "index" and f.kind != "footnote":
                f.kind = "index_entry"
            elif f.section == "bibliography" and f.kind != "footnote":
                f.kind = "bibliography_entry"
            elif f.section == "notes":
                f.kind = "endnote"
            elif f.section == "figure_descriptions" and f.kind != "footnote":
                f.kind = "caption"  # long text descriptions of figures (accessible e-books)
        if f.kind == "code" and len(split_inline_notes(f.text)) > 1:
            f.kind = "endnote"
    text_kinds = ("paragraph", "list_item")
    # TOC runs (>= 3 contents-like lines), anywhere (chapter mini-TOCs too)
    i = 0
    while i < len(flow):
        j = i
        while j < len(flow) and flow[j].kind in text_kinds and toc_like(flow[j].text):
            j += 1
        if j - i >= 3:
            for f in flow[i:j]:
                f.kind = "toc_entry"
                f.tags.append("kind_from_text")
        i = max(j, i + 1)
    # back-of-book index: locator-dense items, dense or next to another one
    idx = [f.kind in text_kinds and index_like(f.text) for f in flow]
    for k, f in enumerate(flow):
        if idx[k] and (len(re.findall(r"[,;:]\s*\d", f.text)) >= 3 or (k and idx[k - 1]) or (k + 1 < len(flow) and idx[k + 1])):
            f.kind = "index_entry"
            f.tags.append("kind_from_text")
    # runs of sequentially numbered notes with citations (chapter-end notes)
    k = 0
    while k < len(flow):
        m = NOTE_LABEL.match(flow[k].text) if flow[k].kind in text_kinds else None
        j = k + 1
        if m:
            expected = int(m.group(1)) + 1
            while j < len(flow) and flow[j].kind in text_kinds:
                mj = NOTE_LABEL.match(flow[j].text)
                if not mj or int(mj.group(1)) != expected:
                    break
                expected += 1
                j += 1
            cited = sum(citation_score(NOTE_LABEL.sub("", f.text, count=1)) >= 2 for f in flow[k:j])
            if j - k >= 2 and cited * 2 >= j - k:
                for f in flow[k:j]:
                    f.kind = "endnote"
                    f.tags.append("kind_from_text")
        k = j
    # bibliography runs (>= 3 citation-shaped items) outside the body text
    k = 0
    while k < len(flow):
        j = k
        while j < len(flow) and flow[j].kind in text_kinds and flow[j].zone != "body" \
                and is_bibliographic(flow[j].text, notes_context=False):
            j += 1
        if j - k >= 3:
            for f in flow[k:j]:
                f.kind = "bibliography_entry"
                f.tags.append("kind_from_text")
        k = max(j, k + 1)


def _split_note_sequence(pieces: list[str]) -> list[str]:
    """Continue a run of notes inside one item when the next label is not preceded by
    punctuation ('...methodsselectionchart.pdf 6. Russell W. Frenske, ...'): a piece labelled n
    is cut before ' n+1. ' followed by a capital or a quote, repeatedly."""
    out: list[str] = []
    for piece in pieces:
        while True:
            m = NOTE_LABEL.match(piece)
            if not m:
                break
            nxt = re.search(rf"(?<=\S) ({int(m.group(1)) + 1})\. (?=[A-Z“\"‘'])", piece[m.end():])
            if not nxt:
                break
            cut = m.end() + nxt.start() + 1
            out.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        out.append(piece)
    return out


def _split_notes(frags: list[_Frag]) -> list[_Frag]:
    out: list[_Frag] = []
    for f in frags:
        if f.kind not in ("endnote", "footnote"):
            out.append(f)
            continue
        pieces = _split_note_sequence(split_inline_notes(f.text))
        for n, piece in enumerate(pieces):
            g = f if len(pieces) == 1 else _Frag(
                refs=list(f.refs), label=f.label, raw=f.raw if n == 0 else "", source=f"{f.source}#note{n}",
                marker=f.marker if n == 0 else None, text=piece, tags=f.tags + ["split_inline_notes"],
                kind=f.kind, zone=f.zone, section=f.section)
            label = None
            if g.marker:
                mk = g.marker.strip().rstrip(".)").strip()
                if re.fullmatch(r"\d{1,3}|[a-zA-Z*†‡§]", mk):
                    label = mk
            m = NOTE_LABEL.match(g.text) or (_FOOTNOTE_LABEL.match(g.text) if g.kind == "footnote" else None)
            if m and (label is None or label == m.group(1)):
                label = m.group(1)
                g.text = g.text[m.end():].strip()
            g.note_label = label
            out.append(g)
    return _split_back(out)


def _split_back(frags: list[_Frag]) -> list[_Frag]:
    """Labels a note run lost inside the previous note (D-045, SAT p. 119: '... &type=pdf 2.
    Hackman and Woolley, Teams, 8. 3. Ibid.' then the item labelled 4): when the next note is
    labelled k and the note before it holds ' k-1. ', ' k-2. ', ... (each before a capital or a
    quote, counting down without a gap to the label above the previous one), cut them out."""
    out: list[_Frag] = []
    last_label = 0  # highest label of the current run of notes, for an unlabelled continuation
    for f in frags:
        if f.kind == "heading":
            last_label = 0
        prev = next((g for g in reversed(out) if g.kind not in _FURNITURE), None)
        k = int(f.note_label) if f.kind in ("endnote", "footnote") and (f.note_label or "").isdigit() else None
        if k is not None and prev is not None and prev.kind == f.kind:
            own = int(prev.note_label) if (prev.note_label or "").isdigit() else None
            lower = own if own is not None else last_label
            text, found = prev.text, []
            want = k - 1
            while want > lower:
                hits = list(re.finditer(rf"(?<=\S) {want}\. (?=[A-Z“\"‘'])", text))
                if not hits:
                    break
                found.append((want, text[hits[-1].start() + 1:].strip()))
                text = text[:hits[-1].start()].rstrip()
                want -= 1
            if found and text:
                prev.text = text
                for n, (label, piece) in enumerate(reversed(found)):
                    out.append(_Frag(refs=list(prev.refs), label=prev.label, raw="", source=f"{prev.source}#back{n}",
                                     text=piece[len(str(label)) + 1:].strip(), tags=prev.tags + ["split_inline_notes"],
                                     kind=prev.kind, zone=prev.zone, section=prev.section, note_label=str(label)))
        if k is not None:
            last_label = k
        out.append(f)
    return out


def _stream(kind: str) -> str | None:
    return {"paragraph": "body", "list_item": "body", "endnote": "endnote", "footnote": "footnote",
            "bibliography_entry": "bibliography"}.get(kind)


def _strip_trailing_marker(text: str) -> str:
    return _TRAILING_MARKER.sub("", text.rstrip())


def _merge(frags: list[_Frag], vocab: Vocab, text_pages: frozenset[int] = frozenset()) -> list[_Item]:
    flow_idx = [i for i, f in enumerate(frags) if f.kind not in _FURNITURE and f.kind not in _FLOAT_KINDS]
    first_on_page: dict[int, int] = {}
    last_on_page: dict[int, int] = {}
    for i in flow_idx:
        first_on_page.setdefault(frags[i].page, i)
        last_on_page[frags[i].last_page] = i
    index = {id(f): i for i, f in enumerate(frags)}

    items: list[_Item] = []
    open_: dict[str, _Item] = {}
    for f in frags:
        stream = _stream(f.kind)
        if f.kind in _FURNITURE or f.kind in _FLOAT_KINDS:
            if f.kind == "footnote":
                target = open_.get("footnote")
                if target is not None and f.note_label is None and f.page - target.frags[-1].last_page in (0, 1):
                    _join(target, f, vocab, "joined_note_continuation")
                    continue
                item = _Item([f], f.text, f.kind, list(f.tags))
                items.append(item)
                open_["footnote"] = item
                continue
            items.append(_Item([f], f.text, f.kind, list(f.tags)))
            continue
        target = open_.get(stream) if stream else None
        if target is not None and _continues(target, f, index, first_on_page, last_on_page, text_pages):
            gap = f.page - target.frags[-1].last_page
            tag = "joined_page_break" if gap >= 1 else ("joined_note_continuation" if stream in ("endnote", "footnote")
                                                        else "joined_column")
            _join(target, f, vocab, tag)
            continue
        item = _Item([f], f.text, f.kind, list(f.tags))
        items.append(item)
        if f.kind == "heading" or f.kind in ("toc_entry", "index_entry"):
            open_.pop("body", None)
            open_.pop("bibliography", None)
            if f.kind == "heading":
                open_.pop("endnote", None)
        if stream:
            open_[stream] = item
    return items


def _continues(target: _Item, f: _Frag, index, first_on_page, last_on_page, text_pages) -> bool:
    prev = target.frags[-1]
    gap = f.page - prev.last_page
    if not 0 <= gap <= 2 or any(p in text_pages for p in range(prev.last_page + 1, f.page)):
        return False  # never join over a skipped page that has text
    if target.kind in ("endnote", "footnote"):
        return f.note_label is None and not BULLET.match(f.text) and (gap == 0 or f.section == "notes" or
                                                                       not TERMINAL.search(target.text))
    if f.marker or BULLET.match(f.text) or NOTE_LABEL.match(f.text):
        return False
    at = _strip_trailing_marker(target.text)
    if not at or TERMINAL.search(at) or not f.text:
        return False
    first = f.text[0]
    if first.islower():
        return True
    if at.endswith("-") and first.isalpha():
        return True
    if at[-1] in ",(–—/&":
        return True
    if at.rsplit(" ", 1)[-1].lower() in CONTINUATION_WORDS and (first.isalpha() or first in _QUOTES):
        return True
    i_prev, i_next = index[id(prev)], index[id(f)]
    page_break = gap >= 1 and last_on_page.get(prev.last_page) == i_prev and first_on_page.get(f.page) == i_next
    # the page ended inside running prose (not on a short title-like line)
    bare = at.rstrip("'’\"”)]")
    prose_end = len(at) >= 40 and bool(bare) and (bare[-1].islower() or bare[-1] in ",;–—-(")
    return page_break and prose_end and (first.isalpha() or first in _QUOTES)


def _join(target: _Item, f: _Frag, vocab: Vocab, tag: str) -> None:
    left = target.text.rstrip()
    right = f.text.lstrip()
    if left.endswith("-") and right[:1].islower():
        joined, dehyph = join_hyphen(left, right, vocab)
        if dehyph:
            target.tags.append("dehyphenated")
        target.text = joined
    else:
        target.text = f"{left} {right}"
    target.frags.append(f)
    target.tags.extend(t for t in f.tags if t not in target.tags)
    target.tags.append(tag)


def _extract_and_link_markers(items: list[_Item]) -> None:
    notes = [it for it in items if it.kind in ("endnote", "footnote") and it.head.note_label]
    if not notes:
        return
    for n, it in enumerate(notes):
        it.note_key = f"k{n}"
    # endnote runs: a label restart or a heading inside the notes starts a new run
    runs: list[dict[str, _Item]] = []
    last = None
    for it in items:
        if it.kind == "heading" and it.head.zone != "body":
            last = None
            continue
        if it.kind != "endnote" or not it.head.note_label:
            continue
        label = it.head.note_label
        value = int(label) if label.isdigit() else None
        if last is None or (value is not None and value < last and value <= 3):  # a new chapter of notes
            runs.append({})
            last = None  # D-045: the new run counts from its own first label, not the old run's last
        runs[-1].setdefault(label, it)
        last = max(last or 0, value) if value is not None else last
    footnotes: dict[int, dict[str, _Item]] = {}
    for it in items:
        if it.kind == "footnote" and it.head.note_label:
            for page, _ in it.frags[0].refs[:1]:
                footnotes.setdefault(page, {}).setdefault(it.head.note_label, it)

    seq = MarkerSequence()
    found: list[tuple[_Item, int, int, str, object, int]] = []  # item, start, end, digits, strength, run
    run = -1
    for it in items:
        if it.kind not in ("paragraph", "list_item", "caption") or it.head.zone == "furniture":
            continue
        for start, end, value, digits, strong in marker_candidates(it.text):
            before = seq.last
            if not seq.accept(value, strong):
                continue
            if run < 0 or value <= before:
                run += 1
            found.append((it, start, end, digits, strong, run))

    # align body marker runs with endnote runs: an order-preserving, one-to-one matching that
    # maximises the number of labels found, where a notes run never precedes the text that
    # cites it (chapter-end notes) and a run counts only if half its labels are there; stray
    # markers (a '1' on the copyright page) then cannot shift every chapter by one
    body_runs: dict[int, list[str]] = {}
    body_last_page: dict[int, int] = {}
    for it, _, _, digits, _, r in found:
        body_runs.setdefault(r, []).append(digits)
        body_last_page[r] = max(body_last_page.get(r, 0), it.head.page)
    order = sorted(body_runs)
    run_first_page = [min(it.head.page for it in run.values()) for run in runs]

    def score(r: int, k: int) -> int:
        labels = body_runs[r]
        hits = sum(lab in runs[k] for lab in labels)
        return hits if hits * 2 >= len(labels) and run_first_page[k] >= body_last_page[r] else 0

    m, n = len(order), len(runs)
    best = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for k in range(n - 1, -1, -1):
            take = score(order[i], k)
            best[i][k] = max(best[i + 1][k], best[i][k + 1], take + best[i + 1][k + 1] if take else 0)
    run_map: dict[int, int] = {}
    i = k = 0
    while i < m and k < n:
        take = score(order[i], k)
        if take and best[i][k] == take + best[i + 1][k + 1]:
            run_map[order[i]] = k
            i, k = i + 1, k + 1
        elif best[i][k] == best[i + 1][k]:
            i += 1
        else:
            k += 1

    kept: dict[int, list[tuple[int, int, str, str | None]]] = {}
    for it, start, end, digits, strong, r in found:
        target = None
        for page, _ in it.frags[0].refs + [ref for fr in it.frags[1:] for ref in fr.refs]:
            for p in (page, page + 1):
                if digits in footnotes.get(p, {}):
                    target = footnotes[p][digits]
                    break
            if target is not None:
                break
        if target is None and r in run_map:
            # the mapped notes run, then unmapped runs up to the next mapped one (a notes run
            # can be cut in two by a stray heading or an out-of-order label)
            later = [k for k in run_map.values() if k > run_map[r]]
            for k in range(run_map[r], min(later) if later else len(runs)):
                target = runs[k].get(digits)
                if target is not None:
                    break
        if target is None and strong is not True:
            continue  # weak evidence that links to nothing stays text
        kept.setdefault(id(it), []).append((start, end, digits, target.note_key if target else None))
    for it in items:
        marks = kept.get(id(it))
        if not marks:
            continue
        out, pos = [], 0
        refs = []
        for start, end, digits, key in sorted(marks):
            if start < pos:
                continue
            out.append(it.text[pos:start])
            refs.append((sum(len(p) for p in out), digits, key))
            pos = end
        out.append(it.text[pos:])
        it.text = "".join(out)
        it.note_refs = refs
        it.tags.append("note_markers_extracted")


def letter_spacing_residue(text: str, heading: bool, vocab: Vocab, lexicon=zipf) -> bool:
    """Heading: any tracked-word fragment left. Body: a run of 3+ stray single letters."""
    if heading:
        return bool(spacing_fragments(text, vocab, lexicon))
    tokens = text.split()
    singles = [bool(re.fullmatch(r"[A-Za-z]", t)) and t not in ("a", "A", "I") for t in tokens]
    return any(a and b and c for a, b, c in zip(singles, singles[1:], singles[2:]))


# ----------------------------------------------------------------------------- assembly
def _build(meta: BookMeta, frags: list[_Frag], text_layer: dict[str, Any], parser: dict[str, str],
           heights: dict[int, float]) -> ParsedBookV2:
    page_count = int(text_layer["page_count"])
    frags = [f for f in frags if 1 <= f.page <= page_count]
    references = {int(p["page_no"]): reference_separators(p["text_pdfium"])
                  for p in text_layer["pages"] if p.get("text_pdfium")}
    frags = _group_image_ocr(frags, text_layer)
    frags = _split_at_paragraph_ends(frags, text_layer)
    vocab = _normalize_text(frags, references=references, spaced=_spaced_lines(text_layer))
    for f in frags:
        f.kind = _base_kind(f)
    _mark_repeated_edges(frags)
    outline = [e for e in text_layer.get("outline") or [] if 0 <= int(e["page_index"]) < page_count]
    frags = _match_outline(frags, outline, heights)
    _heading_levels(frags, bool(outline))
    _zones_and_sections(frags)
    _apparatus_kinds(frags)
    frags = _split_notes(frags)
    text_pages = frozenset(int(p["page_no"]) for p in text_layer["pages"]
                           if len(re.findall(r"[A-Za-z]{3,}", p.get("text") or "")) >= 3)
    items = _merge(frags, vocab, text_pages)
    _extract_and_link_markers(items)

    # printed labels: /PageLabels, else the page-number furniture printed on the page
    labels: dict[int, str] = {}
    if text_layer.get("has_page_labels"):
        labels = {int(p["page_no"]): p["label"] for p in text_layer["pages"] if p.get("label")}
    else:
        for it in items:
            if it.kind == "page_number":
                labels.setdefault(it.head.page, it.text.strip())
    native_chars = {int(p["page_no"]): len(re.sub(r"\s+", "", p.get("text") or "")) for p in text_layer["pages"]}
    ocr_pages = {f.page for f in frags if native_chars.get(f.page, 0) < 8 and f.raw}

    flow = [i for i, it in enumerate(items) if it.kind not in _FURNITURE and it.kind not in _FLOAT_KINDS]
    last_flow_on_page: dict[int, int] = {}
    continued: set[int] = set()  # pages a body item runs off into the next page
    for i in flow:
        last_flow_on_page[items[i].frags[-1].last_page] = i
        pages = [ref[0] for f in items[i].frags for ref in f.refs]
        continued.update(p for p in pages if p < max(pages))
    max_flow_page = max((items[i].frags[0].page for i in flow), default=0)

    blocks: list[BlockV2] = []
    note_ids: dict[str, str] = {}
    note_items = [it for it in items if it.note_key is not None]
    for n, it in enumerate(note_items, start=1):
        note_ids[it.note_key] = f"{meta.book_id}:n{n:04d}"
    block_of_item: dict[int, str] = {}
    section_path: list[str] = []
    cited: dict[str, list[str]] = {}
    outline_block: dict[int, str] = {}
    for seq, it in enumerate(items, start=1):
        block_id = f"{meta.book_id}:b{seq:05d}"
        block_of_item[id(it)] = block_id
        head = it.head
        if it.kind == "heading":
            level = head.level or 1
            section_path = section_path[: level - 1] + [it.text]
            if head.outline_idx is not None:
                outline_block[head.outline_idx] = block_id
        flags = list(it.flags)
        if it.kind in ("paragraph", "list_item"):
            last_page = it.frags[-1].last_page
            if head.zone == "body" and last_flow_on_page.get(last_page) == seq - 1 and last_page < max_flow_page \
                    and last_page not in continued and len(it.text.split()) >= CUT_MIN_WORDS \
                    and not TERMINAL.search(_strip_trailing_marker(it.text)):
                flags.append("cut_at_page_break")
        if it.kind in ("heading", "paragraph", "list_item") and \
                letter_spacing_residue(it.text, it.kind == "heading", vocab):
            flags.append("letter_spacing_residue")
        if _LIGATURE.search(it.text):
            flags.append("ligature_residue")
        if _UNEXPECTED_SCRIPT.search(it.text):
            flags.append("unexpected_script")
        if it.kind in ("endnote", "footnote") and not head.note_label:
            flags.append("note_without_label")
        refs = []
        for f in it.frags:
            for page, box in f.refs:
                refs.append(PageRef(book_id=meta.book_id, page_no=page, printed_label=labels.get(page), bbox=box))
        note_refs = []
        for offset, label, key in it.note_refs:
            nid = note_ids.get(key) if key else None
            note_refs.append(NoteRef(label=label, offset=offset, note_id=nid))
            if nid:
                cited.setdefault(nid, []).append(block_id)
        blocks.append(BlockV2(
            block_id=block_id, type=it.kind, zone="furniture" if it.kind in _FURNITURE else head.zone,  # type: ignore[arg-type]
            level=(head.level or 1) if it.kind == "heading" else None,
            level_source=(head.level_source or "layout") if it.kind == "heading" else None,  # type: ignore[arg-type]
            text=it.text, raw_text="\n".join(f.raw for f in it.frags),
            refs=refs, section_path=list(section_path) if it.kind not in _FURNITURE else [],
            ocr=any(f.page in ocr_pages for f in it.frags),
            note_label=head.note_label if it.kind in ("endnote", "footnote") else None,
            note_refs=note_refs, normalization=list(dict.fromkeys(it.tags)),
            quality_flags=list(dict.fromkeys(flags)), source_items=[f.source for f in it.frags],
        ))
    notes = [Note(note_id=note_ids[it.note_key], kind=it.kind, label=it.head.note_label,  # type: ignore[arg-type]
                  block_ids=[block_of_item[id(it)]], cited_by=list(dict.fromkeys(cited.get(note_ids[it.note_key], []))))
             for it in note_items]

    status: dict[int, str] = {}
    for b in blocks:
        for r in b.refs:
            if b.zone != "furniture":
                status[r.page_no] = "content"
            else:
                status.setdefault(r.page_no, "furniture_only")
    pages = [PageInfo(page_no=p, printed_label=labels.get(p), status=status.get(p, "empty"), ocr=p in ocr_pages)  # type: ignore[arg-type]
             for p in range(1, page_count + 1)]
    entries = [OutlineEntry(title=re.sub(r"\s+", " ", e["title"]).strip(), level=int(e["depth"]) + 1,
                            page_no=int(e["page_index"]) + 1, block_id=outline_block.get(k))
               for k, e in enumerate(outline)]
    return ParsedBookV2(
        book_id=meta.book_id, title=meta.title, role=meta.role, page_count=page_count,  # type: ignore[arg-type]
        toc_source="pdf_outline" if outline else "headings", pages=pages, outline=entries,
        blocks=blocks, notes=notes, parser=parser,
        normalizer={"name": NORMALIZER_NAME, "version": NORMALIZER_VERSION},
    )


def normalize_docling(meta: BookMeta, batches: list[dict[str, Any]], text_layer: dict[str, Any],
                      parser: dict[str, str] | None = None) -> ParsedBookV2:
    """DoclingDocument batches (any order) + the PDF text layer -> ParsedBook v2."""
    def first_page(doc: dict[str, Any]) -> int:
        batch = doc.get("b2c_batch") or {}
        if "first_page" in batch:
            return int(batch["first_page"])
        return min((int(k) for k in (doc.get("pages") or {})), default=0)

    frags: list[_Frag] = []
    heights: dict[int, float] = {}
    for doc in sorted(batches, key=first_page):
        frags.extend(_docling_frags(doc))
        for k, v in (doc.get("pages") or {}).items():
            if v.get("size"):
                heights[int(k)] = float(v["size"]["height"])
    order = {id(f): i for i, f in enumerate(frags)}
    frags.sort(key=lambda f: (f.page, order[id(f)]))
    return _build(meta, frags, text_layer, parser or {"name": "docling-layout"}, heights)


def normalize_text_layer(meta: BookMeta, text_layer: dict[str, Any],
                         parser: dict[str, str] | None = None) -> ParsedBookV2:
    """The PDF text layer alone (no layout model) -> ParsedBook v2 (no bbox)."""
    outline_keys: dict[int, set[str]] = {}
    for e in text_layer.get("outline") or []:
        outline_keys.setdefault(int(e["page_index"]) + 1, set()).add(heading_key(e["title"]))
    frags = _text_layer_frags(text_layer, outline_keys)
    heights = {int(p["page_no"]): float(p.get("height") or 0) for p in text_layer["pages"]}
    return _build(meta, frags, text_layer,
                  parser or {"name": "pypdf-text-layer", "version": str(text_layer.get("pypdf_version", ""))},
                  heights)
