"""S2 gate (R3, redesigned in D-043): deterministic ingest quality report for ParsedBook v2
(and v1, for baselines), and the gate decision as a ``GateResult``.

Text fidelity is a cross-parser agreement. The PDF text layer is extracted twice by independent
engines (``layout.pdf_text_layer``: pypdf and pdfium); the parsed book comes from a third
(Docling's docling-parse, or OCR). Every page is scored by word F1 (words of >= 3 letters)
against each extraction and keeps its best score: a page passes when the parse agrees with at
least one independent extraction at >= ``PAGE_AGREEMENT_MIN``. A single extraction is not a
reference: one engine's defects (pypdf glues words at kerning gaps: 1.4% of SAT's tokens)
would fail a correct parse, as v1 of this gate did on SAT. Words of a block that spans pages
are attributed to its pages by the order-preserving split that best matches each page's
reference; words read from a raster image (inside a pdfium image box, OCR blocks, figure text)
count for recall but are not "invented" text. A page whose letters agree but whose word
boundaries do not (letter-spaced small caps in both extractions, repaired by S2) is recorded
as spacing-only, not as a failure: glued and letter-spaced words have their own checks.

Thresholds and their measured basis are in GATE_DEFINITION and docs/00-DECISIONS.md D-043. A
failed check makes the report ``accepted=False`` (the contract refuses anything else); a check
that cannot be measured on a book (no notes, no bookmarks, no page labels) has value None.
Per-page checks name their pages in ``page_findings`` for review.

The text layer is not human gold: agreement measures loss, invention and order-independent
word identity, not correctness of reading order or of OCR'd images.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from datetime import datetime

from b2c_contracts import ArtifactRef, GateResult, IngestQualityReport, ParsedBook, ParsedBookV2, QualityCheck
from b2c_core.source_text import TERMINAL, Vocab, heading_key, normalize_chars

from .normalize import CUT_MIN_WORDS, _edge_furniture_keys, _letters_key, _strip_trailing_marker, letter_spacing_residue

REPORT_VERSION = "2"  # 2: cross-parser agreement, glued-token rate, no 2,000-character limit (D-043)
GATE_ID = "ingest-v2.quality"
# Per-page word F1 below which a page disagrees with an extraction. Basis (eval/reports/
# ingest-v2-gate.md): pypdf and pdfium, two correct extractions of the same healthy text layer
# (Gramsci), agree at >= 0.95 on 408 of 409 text pages (median 1.0, 1st percentile 0.978); the one
# exception is a letter-spaced chapter opener. Below 0.95 a page is outside extraction noise.
PAGE_AGREEMENT_MIN = 0.95
LETTER_AGREEMENT_MIN = 0.95  # same bound on 6-letter shingles: a page above it differs in spacing only
IMAGE_OVERLAP_MIN = 0.5  # share of a fragment's box inside raster images to count it as image text
_WORD = re.compile(r"[a-z]{3,}")
_LETTERS = re.compile(r"[a-z]+")
_LIGATURE = re.compile("[ﬀ-ﬆ]")
_UNEXPECTED_SCRIPT = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿-�]")
_HYPHEN_GAP = re.compile(r"\b([A-Za-z]{2,})- ([a-z]{2,})\b")
_BODY_TEXT = frozenset({"paragraph", "list_item"})

# (name, op, threshold, source). The audit's thresholds, except text fidelity (D-043): the single-
# reference word recall/precision and per-page recall >= 0.98 are replaced by cross-parser
# agreement, and the 2,000-character block limit is removed (a long academic paragraph is one
# paragraph; Gramsci has 31 of them, p90 of body paragraphs 1,667 characters).
GATES: list[tuple[str, str, float, str]] = [
    ("independent_references", ">=", 2, "D-043: two independent extractions of the text layer (pypdf, pdfium)"),
    ("uncovered_text_pages", "<=", 0, "01 §5.4.1 / R3: every text page yields blocks or is recorded"),
    ("cross_parser_word_recall", ">=", 0.995, "01 §5.4.2, against the best-agreeing extraction per page (D-043)"),
    ("cross_parser_word_precision", ">=", 0.995, "01 §5.4.2, image text excluded (D-043)"),
    ("pages_below_agreement", "<=", 0,
     f"D-043: pages whose word F1 is < {PAGE_AGREEMENT_MIN} against every extraction (not spacing-only)"),
    ("cut_at_page_break_rate", "<=", 0.01, "01 §5.4.4 / R3"),
    ("merged_paragraph_rate", "<=", 0.01,
     "D-043, replaces the 2,000-character limit (01 §5.4.3): paragraph ends of the text layer inside a block"),
    ("ligatures", "<=", 0, "01 §5.4.5"),
    ("letter_spacing_residue", "<=", 0, "01 §5.4.5 / R3 (Gramsci: 0 letter-spaced headings)"),
    ("hyphen_residue_rate", "<=", 0.0005, "01 §5.4.5"),
    ("glued_token_rate", "<=", 0.001, "01 §5.4.5 (glued words <= 0.1%), judged by the extractions (D-043)"),
    ("running_head_leak_page_rate", "<=", 0.005, "01 §5.4.5"),
    ("note_link_rate", ">=", 0.95, "01 §5.4.6"),
    ("notes_without_label", "<=", 0, "01 §5.4.6"),
    ("outline_match_rate", ">=", 0.95, "01 §5.4.7"),
    ("bbox_coverage", ">=", 1.0, "01 §5.4.8"),
    ("printed_label_coverage", ">=", 1.0, "01 §5.4.8 (when the PDF has page labels)"),
    ("unflagged_unexpected_script_blocks", "<=", 0, "01 §5.4.9"),
]


@dataclass
class _View:
    type: str
    zone: str
    text: str
    pages: list[int]
    flags: list[str] = field(default_factory=list)
    boxes: list[tuple[int, tuple[float, float, float, float] | None]] = field(default_factory=list)
    ocr: bool = False
    bbox: list[bool] = field(default_factory=list)
    labels: list[bool] = field(default_factory=list)
    note_refs: int = 0
    linked: int = 0
    note_label: bool = True


def _words(text: str) -> Counter:
    return Counter(_WORD.findall(text.lower()))


def _shingles(letters: str, n: int = 6) -> Counter:
    """6-letter shingles of a letters-only stream: blind to where the spaces are."""
    return Counter(letters[i:i + n] for i in range(max(0, len(letters) - n + 1)))


def _reference_pages(text_layer: dict[str, Any], field_name: str = "text") -> dict[int, str]:
    """One extraction per page, ligatures normalized and line-break hyphens joined (pypdf:
    'mobiliza -\ntion'; pdfium marks a soft line-end hyphen as U+FFFE)."""
    out = {}
    for p in text_layer["pages"]:
        if field_name not in p:
            continue
        text = (p.get(field_name) or "").replace("\ufffe", "").replace("\x02", "").replace("\r\n", "\n")
        # "domina\u00ad\ntion": join before the soft hyphen is stripped
        text = re.sub(r"(?<=[a-z]) ?[-\u00ad\u2010] *\n *(?=[a-z])", "", text)
        out[int(p["page_no"])] = normalize_chars(text)[0]
    return out


def _letter_tokens(text: str) -> list[str]:
    return _LETTERS.findall(text.lower())


def _f1(overlap: int, a: int, b: int) -> float:
    return 2 * overlap / (a + b) if a + b else 1.0


def _best_split(tokens: list[str], left: Counter, right: Counter) -> int:
    """Cut point k maximizing matches of tokens[:k] in ``left`` plus tokens[k:] in ``right``."""
    n = len(tokens)
    pre, seen = [0] * (n + 1), Counter()
    for i, t in enumerate(tokens):
        seen[t] += 1
        pre[i + 1] = pre[i] + (seen[t] <= left[t])
    suf, seen = [0] * (n + 1), Counter()
    for i in range(n - 1, -1, -1):
        seen[tokens[i]] += 1
        suf[i] = suf[i + 1] + (seen[tokens[i]] <= right[tokens[i]])
    return max(range(n + 1), key=lambda k: (pre[k] + suf[k], -k))


def _attribute(tokens: list[str], pages: list[int], ref: dict[int, Counter]) -> list[tuple[int, list[str]]]:
    """Order-preserving split of a block's tokens over its pages, best matching each page."""
    out = []
    for j, page in enumerate(pages[:-1]):
        rest: Counter = Counter()
        for q in pages[j + 1:]:
            rest.update(ref.get(q, Counter()))
        k = _best_split(tokens, ref.get(page, Counter()), rest)
        out.append((page, tokens[:k]))
        tokens = tokens[k:]
    out.append((pages[-1], tokens))
    return out


def _inside(box: tuple[float, float, float, float], images: list[list[float]]) -> float:
    area = max(1e-6, (box[2] - box[0]) * (box[3] - box[1]))
    covered = 0.0
    for left, top, right, bottom in images:
        w, h = min(box[2], right) - max(box[0], left), min(box[3], bottom) - max(box[1], top)
        if w > 0 and h > 0:
            covered += w * h
    return covered / area


def _image_text(v: "_View", images: dict[int, list[list[float]]]) -> bool:
    if v.ocr or v.type == "figure":
        return True
    boxed = [(p, b) for p, b in v.boxes if b is not None]
    return bool(boxed) and len(boxed) == len(v.boxes) and all(
        _inside(b, images.get(p, [])) >= IMAGE_OVERLAP_MIN for p, b in boxed)


_PARA_END = re.compile(r"[.!?][\"”’)]?$")
_PARA_START = re.compile(r"[\"“‘(]?[A-Z]")


def _paragraph_ends(text_layer: dict[str, Any]) -> dict[tuple[str, ...], int]:
    """Paragraph boundaries the text layer shows by its line geometry: a line shorter than 80% of
    the page's typical line that ends a sentence, followed by a full-length line that starts one.
    Signature: last 3-4 words of the short line + first 3 of the next -> page. A boundary inside a
    block counts only when the block continues after it (the 7th/8th token exists)."""
    field_name = "text_pdfium" if any("text_pdfium" in p for p in text_layer["pages"]) else "text"
    out: dict[tuple[str, ...], int] = {}
    for page in text_layer["pages"]:
        raw = (page.get(field_name) or "").replace("\ufffe", "").replace("\x02", "")
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        full = [len(ln) for ln in lines if len(ln) >= 30]
        if len(full) < 5:
            continue
        typical = statistics.median(full)
        for a, b in zip(lines, lines[1:-1]):  # the page's last line does not open a paragraph here
            if len(a) < 0.8 * typical <= len(b) and _PARA_END.search(a) and _PARA_START.match(b):
                head, tail = _letter_tokens(a)[-4:], _letter_tokens(b)[:3]
                if len(head) >= 3 and len(tail) >= 3:
                    out[tuple(head + tail)] = int(page["page_no"])
    return out


def _merged_paragraphs(views: list["_View"], text_layer: dict[str, Any]) -> tuple[int, int, list[int]]:
    """(boundaries found inside a body block, boundaries in the text layer, their pages)."""
    ends = _paragraph_ends(text_layer)
    inside, pages = 0, set()
    for v in views:
        # body zone only, as for cuts: a copyright page or an index is set line by line
        if v.type not in _BODY_TEXT or v.zone != "body":
            continue
        tokens = _letter_tokens(v.text)
        for i in range(len(tokens)):
            for n in (6, 7):  # 3-4 words of the short line + 3 of the next
                page = ends.get(tuple(tokens[i:i + n])) if i + n < len(tokens) else None
                if page is not None:
                    inside += 1
                    pages.add(page)
    return inside, len(ends), sorted(pages)


def _concat_index(tokens: list[str]) -> set[str]:
    """2-3 adjacent words written together; pieces of one letter ('t echnological', a letter-
    spaced extraction) do not count as words."""
    out = set()
    for i in range(len(tokens) - 1):
        if len(tokens[i]) < 2 or len(tokens[i + 1]) < 2:
            continue
        out.add(tokens[i] + tokens[i + 1])
        if i + 2 < len(tokens) and len(tokens[i + 2]) >= 2:
            out.add(tokens[i] + tokens[i + 1] + tokens[i + 2])
    return out


def _cross_parser(views: list["_View"], text_layer: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[int]]]:
    """Per-page agreement of the parse with each independent extraction (module docstring)."""
    refs = {name: pages for name, pages in (("pypdf", _reference_pages(text_layer, "text")),
                                            ("pdfium", _reference_pages(text_layer, "text_pdfium"))) if pages}
    images = {int(p["page_no"]): p.get("image_boxes") or [] for p in text_layer["pages"]}
    ref_letters = {name: {p: _letter_tokens(t) for p, t in pages.items()} for name, pages in refs.items()}
    ref_words = {name: {p: Counter(w for w in toks if len(w) >= 3) for p, toks in pages.items()}
                 for name, pages in ref_letters.items()}
    text_pages = sorted({p for pages in ref_words.values() for p, c in pages.items() if sum(c.values()) >= 3})

    touch: dict[int, Counter] = {}
    image_words = 0
    exact: dict[str, dict[int, list[str]]] = {name: {} for name in refs}
    for v in views:
        pages = list(dict.fromkeys(v.pages))
        tokens = _letter_tokens(v.text)
        if _image_text(v, images):
            # read from a picture: it may recover words of the text layer (recall) but is not
            # held against precision; a block over several pages credits each of them
            words = Counter(w for w in tokens if len(w) >= 3)
            image_words += sum(words.values())
            for p in pages:
                touch.setdefault(p, Counter()).update(words)
            continue
        for name in refs:
            for page, part in (_attribute(tokens, pages, ref_words[name]) if len(pages) > 1 else [(pages[0], tokens)]):
                exact[name].setdefault(page, []).extend(part)

    below, spacing_only, disagree, glued_pages = [], [], [], []
    rec_hit = rec_all = prec_hit = prec_all = glued = cand_tokens = 0
    scores = []
    concat = {name: {p: _concat_index(t) for p, t in pages.items()} for name, pages in ref_letters.items()}
    token_sets = {name: {p: set(t) for p, t in pages.items()} for name, pages in ref_letters.items()}
    for p in text_pages:
        best = None
        for name in refs:
            ref = ref_words[name].get(p, Counter())
            cand_exact = Counter(w for w in exact[name].get(p, []) if len(w) >= 3)
            r_hit = sum((ref & (cand_exact + touch.get(p, Counter()))).values())
            p_hit = sum((cand_exact & ref).values())
            n_ref, n_cand = sum(ref.values()), sum(cand_exact.values())
            recall = r_hit / n_ref if n_ref else 1.0
            precision = p_hit / n_cand if n_cand else 1.0
            f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
            if best is None or f1 > best[0]:
                best = (f1, name, r_hit, n_ref, p_hit, n_cand)
        f1, name, r_hit, n_ref, p_hit, n_cand = best
        rec_hit, rec_all, prec_hit, prec_all = rec_hit + r_hit, rec_all + n_ref, prec_hit + p_hit, prec_all + n_cand
        scores.append(f1)
        if len(refs) == 2:
            a, b = ref_words["pypdf"].get(p, Counter()), ref_words["pdfium"].get(p, Counter())
            if _f1(sum((a & b).values()), sum(a.values()), sum(b.values())) < PAGE_AGREEMENT_MIN:
                disagree.append(p)
        if f1 < PAGE_AGREEMENT_MIN:
            letters_ok = False
            for ref_name in refs:
                cand = _shingles("".join(exact[ref_name].get(p, [])))
                ref = _shingles("".join(ref_letters[ref_name].get(p, [])))
                if _f1(sum((cand & ref).values()), sum(cand.values()), sum(ref.values())) >= LETTER_AGREEMENT_MIN:
                    letters_ok = True
            (spacing_only if letters_ok else below).append(p)
    # glued: a parsed token the judge extraction never prints but prints as 2-3 adjacent words.
    # The judge is pdfium when present: it spaces words by glyph geometry, while pypdf glues at
    # kerning gaps (SAT: 1,700 glued tokens in pypdf, 0 in pdfium) and letter-spaces small caps.
    judge = "pdfium" if "pdfium" in refs else next(iter(refs), None)
    for p, toks in (exact[judge].items() if judge else []):
        cand_tokens += len(toks)
        hits = sum(1 for t in toks if len(t) >= 4 and t not in token_sets[judge].get(p, ())
                   and t in concat[judge].get(p, ()))
        glued += hits
        if hits:
            glued_pages.append(p)
    metrics = {
        "independent_references": len(refs),
        "text_pages": len(text_pages),
        "cross_parser_word_recall": round(rec_hit / rec_all, 5) if rec_all else 0.0,
        "cross_parser_word_precision": round(prec_hit / prec_all, 5) if prec_all else 0.0,
        "pages_below_agreement": len(below),
        "pages_spacing_only": len(spacing_only),
        "min_page_agreement": round(min(scores), 4) if scores else None,
        "median_page_agreement": round(statistics.median(scores), 4) if scores else None,
        "reference_disagreement_pages": len(disagree) if len(refs) == 2 else None,
        "image_text_words": image_words,
        "glued_tokens": glued,
        "glued_token_rate": round(glued / cand_tokens, 6) if cand_tokens else 0.0,
    }
    findings = {"pages_below_agreement": below, "glued_token_rate": sorted(glued_pages)}
    return metrics, findings


def _views_v2(book: ParsedBookV2) -> list[_View]:
    return [_View(type=b.type, zone=b.zone, text=b.text, pages=[r.page_no for r in b.refs], ocr=b.ocr,
                  boxes=[(r.page_no, r.bbox) for r in b.refs],
                  flags=list(b.quality_flags), bbox=[r.bbox is not None for r in b.refs] if b.raw_text else [],
                  labels=[r.printed_label is not None for r in b.refs],
                  note_refs=len(b.note_refs), linked=sum(r.note_id is not None for r in b.note_refs),
                  note_label=b.type not in ("footnote", "endnote") or b.note_label is not None)
            for b in book.blocks]


_V1_ZONE = {"page_header": "furniture", "page_footer": "furniture"}


def _views_v1(book: ParsedBook) -> list[_View]:
    return [_View(type=b.type, zone=_V1_ZONE.get(b.type, "body"), text=b.text, ocr=b.ocr,
                  pages=[r.page_no for r in b.refs], bbox=[r.bbox is not None for r in b.refs],
                  boxes=[(r.page_no, r.bbox) for r in b.refs],
                  labels=[r.printed_label is not None for r in b.refs])
            for b in book.blocks]


def _metrics(views: list[_View], text_layer: dict[str, Any], *, notes_known: bool
             ) -> tuple[dict[str, float | int | None], dict[str, list[int]]]:
    ref_pages = _reference_pages(text_layer)
    page_count = int(text_layer["page_count"])
    text_pages = {p for p, t in ref_pages.items() if len(_WORD.findall(t.lower())) >= 3}
    covered = {p for v in views for p in v.pages}
    cand = Counter()
    for v in views:
        cand.update(_words(v.text))
    agreement, findings = _cross_parser(views, text_layer)
    findings["uncovered_text_pages"] = sorted(text_pages - covered)
    merged, paragraph_ends, merged_pages = _merged_paragraphs(views, text_layer)
    findings["merged_paragraph_rate"] = merged_pages

    body = [v for v in views if v.type in _BODY_TEXT and v.zone != "furniture"]
    lengths = [len(v.text) for v in body if v.zone == "body"]
    # diagnostic for the failure the 2,000-character limit was meant to catch (v1: block = page):
    # body pages of >= 150 words where one block holds >= 90% of the page's body words
    per_page: dict[int, Counter] = {}
    for i, v in enumerate(body):
        if v.zone == "body" and len(set(v.pages)) == 1:
            per_page.setdefault(v.pages[0], Counter())[i] = len(_WORD.findall(v.text.lower()))
    full = [c for c in per_page.values() if sum(c.values()) >= 150]
    single_block_pages = sum(1 for c in full if max(c.values()) >= 0.9 * sum(c.values()))

    # cut at page break: last body-text block ending on a page, not continued, no sentence end
    # (body zone only: title pages, dedications and epigraph pages are not running prose)
    last_on_page: dict[int, _View] = {}
    continued: set[int] = set()
    for v in body:
        if v.zone != "body":
            continue
        last_on_page[max(v.pages)] = v
        continued.update(p for p in v.pages if p < max(v.pages))
    body_pages = sorted({p for v in body if v.zone == "body" for p in v.pages})
    max_page = max(body_pages, default=0)
    cut_pages = sorted(p for p, v in last_on_page.items() if p < max_page and p not in continued
                       and len(v.text.split()) >= CUT_MIN_WORDS and not TERMINAL.search(_strip_trailing_marker(v.text)))
    cut = len(cut_pages)
    findings["cut_at_page_break_rate"] = cut_pages

    vocab = Vocab([v.text for v in views if v.type in _BODY_TEXT])
    residue = sum(1 for v in views if v.type in ("heading", "paragraph", "list_item")
                  and letter_spacing_residue(v.text, v.type == "heading", vocab))
    hyphen_hits = sum(1 for v in views if v.zone != "furniture"
                      for m in _HYPHEN_GAP.finditer(v.text) if vocab.count(m.group(1) + m.group(2)) >= 1)
    words_total = sum(cand.values()) or 1

    # running heads from the text layer's own edge repetition, independent of the parser
    lines = [[ln.strip() for ln in t.split("\n") if ln.strip()] for _, t in sorted(ref_pages.items())]
    keys = _edge_furniture_keys(lines, page_numbers=sorted(ref_pages))
    heads = sorted({re.sub(r"\s+", " ", re.sub(r"\d+", "", ln)).strip().lower()
                    for page in lines for ln in page[:2] + page[-2:] if _letters_key(ln) in keys}, key=len)
    heads = [h for h in heads if len(h) >= 8]
    leak_pages = set()
    for v in views:
        if v.zone == "furniture" or v.type not in _BODY_TEXT:
            continue
        # a leaked running head sits at a block edge or next to its page number; the same words
        # inside prose (a chapter title quoted in the text) are not a leak
        low = re.sub(r"\s+", " ", v.text.lower()).strip()
        if any(low.startswith(h) or low.endswith(h) or re.search(rf"\b\d{{1,4}} {re.escape(h)}|{re.escape(h)} \d{{1,4}}\b", low)
               for h in heads):
            leak_pages.update(v.pages)

    refs_total = sum(v.note_refs for v in views)
    notes = [v for v in views if v.type in ("footnote", "endnote")]

    outline = text_layer.get("outline") or []
    heads_by_page: dict[int, list[str]] = {}
    for v in views:
        if v.type == "heading":
            heads_by_page.setdefault(v.pages[0], []).append(heading_key(v.text))
    matched = 0
    for e in outline:
        key = heading_key(e["title"])
        page = int(e["page_index"]) + 1
        if any(k == key or (len(k) >= 4 and (key.endswith(k) or key.startswith(k) or k.startswith(key)))
               for k in heads_by_page.get(page, [])):
            matched += 1

    bbox = [b for v in views for b in v.bbox]
    labels = [b for v in views for b in v.labels]
    has_labels = bool(text_layer.get("has_page_labels"))
    findings["running_head_leak_page_rate"] = sorted(leak_pages)
    metrics = {
        "pages": page_count,
        "blocks": len(views),
        "uncovered_text_pages": len(text_pages - covered),
        **agreement,
        "text_layer_text_pages": len(text_pages),
        "median_body_block_chars": statistics.median(lengths) if lengths else 0,
        "max_body_block_chars": max(lengths, default=0),
        "single_block_full_pages": single_block_pages,
        "full_body_pages": len(full),
        "text_layer_paragraph_ends": paragraph_ends,
        "merged_paragraphs": merged,
        "merged_paragraph_rate": round(merged / paragraph_ends, 5) if paragraph_ends else None,
        "cut_at_page_break_pages": cut,
        "cut_at_page_break_rate": round(cut / max(1, len(body_pages)), 5),
        "ligatures": sum(len(_LIGATURE.findall(v.text)) for v in views),
        "letter_spacing_residue": residue,
        "hyphen_residue_rate": round(hyphen_hits / words_total, 6),
        "running_head_leak_page_rate": round(len(leak_pages) / max(1, len(text_pages)), 5),
        "note_markers": refs_total if notes_known else None,
        "note_link_rate": (round(sum(v.linked for v in views) / refs_total, 5)
                           if notes_known and refs_total else None),
        "notes": len(notes),
        "notes_without_label": sum(1 for v in notes if not v.note_label) if notes_known else None,
        "outline_entries": len(outline),
        "outline_match_rate": round(matched / len(outline), 5) if outline else None,
        "bbox_coverage": round(sum(bbox) / len(bbox), 5) if bbox else 0.0,
        "printed_label_coverage": round(sum(labels) / len(labels), 5) if has_labels and labels else None,
        "unflagged_unexpected_script_blocks": sum(1 for v in views if _UNEXPECTED_SCRIPT.search(v.text)
                                                  and "unexpected_script" not in v.flags),
        "furniture_blocks": sum(1 for v in views if v.zone == "furniture"),
        "zones": None,
    }
    return metrics, {k: v for k, v in findings.items() if v}


def _checks(metrics: dict[str, Any]) -> list[QualityCheck]:
    checks = []
    for name, op, threshold, source in GATES:
        value = metrics.get(name)
        if value is None:
            checks.append(QualityCheck(name=name, value=None, op=op, threshold=threshold, passed=None,  # type: ignore[arg-type]
                                       detail=f"not measurable on this book; {source}"))
            continue
        ok = {"<=": value <= threshold, ">=": value >= threshold, "==": value == threshold}[op]
        checks.append(QualityCheck(name=name, value=float(value), op=op, threshold=threshold,  # type: ignore[arg-type]
                                   passed=ok, detail=source))
    return checks


def quality_report(book: ParsedBookV2, text_layer: dict[str, Any], parsed_sha256: str | None = None) -> IngestQualityReport:
    metrics, findings = _metrics(_views_v2(book), text_layer, notes_known=True)
    zones = Counter(b.zone for b in book.blocks)
    metrics.pop("zones")
    metrics.update({f"zone_{z}": n for z, n in sorted(zones.items())})
    checks = _checks(metrics)
    accepted = not any(c.passed is False for c in checks) and any(c.passed for c in checks)
    return IngestQualityReport(book_id=book.book_id, report_version=REPORT_VERSION,
                               parsed_sha256=parsed_sha256, metrics=metrics, checks=checks, accepted=accepted,
                               page_findings=findings)


def quality_report_v1(book: ParsedBook, text_layer: dict[str, Any]) -> IngestQualityReport:
    """The same measurement on a v1 ParsedBook (baseline). v1 has no note links: n/a."""
    metrics, findings = _metrics(_views_v1(book), text_layer, notes_known=False)
    metrics.pop("zones")
    checks = _checks(metrics)
    accepted = not any(c.passed is False for c in checks) and any(c.passed for c in checks)
    return IngestQualityReport(book_id=book.book_id, report_version=REPORT_VERSION, metrics=metrics,
                               checks=checks, accepted=accepted, page_findings=findings)


# ----------------------------------------------------------------------------- gate decision
GATE_DEFINITION: dict[str, Any] = {
    "gate_id": GATE_ID,
    "report_version": REPORT_VERSION,
    "references": ["pypdf text layer", "pdfium text layer"],
    "page_agreement_min": PAGE_AGREEMENT_MIN,
    "letter_agreement_min": LETTER_AGREEMENT_MIN,
    "image_overlap_min": IMAGE_OVERLAP_MIN,
    "checks": [{"name": n, "op": op, "threshold": t} for n, op, t, _ in GATES],
    "rule": "IngestQualityReport.accepted: every measurable check passed, at least one passed",
}


def gate_version() -> str:
    """Digest of the gate definition: a changed check or threshold cannot keep an old version."""
    import hashlib
    import json

    raw = json.dumps({"gate_id": GATE_ID, "kind": "deterministic", "definition": GATE_DEFINITION},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def gate_result(report: IngestQualityReport, artifact: ArtifactRef, *, decided_at: datetime) -> GateResult:
    """The system gate decision on one ``ingest-parse-v2`` artifact (promotion, R1)."""
    metrics: dict[str, Any] = {k: v for k, v in report.metrics.items() if v is not None}
    metrics["failed_checks"] = report.failed
    metrics["page_findings"] = {k: v[:200] for k, v in report.page_findings.items()}
    return GateResult(stage=artifact.stage, artifact=artifact, gate_id=GATE_ID, gate_version=gate_version(),
                      kind="deterministic", passed=report.accepted, metrics=metrics,
                      evidence_ref=artifact.uri + "ingest-quality.json", decider="system",
                      decided_by=f"policy:{GATE_ID}", decided_at=decided_at)
