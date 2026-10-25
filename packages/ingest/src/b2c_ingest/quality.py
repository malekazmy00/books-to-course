"""S2 gate (R3): deterministic ingest quality report for ParsedBook v2 (and v1, for baselines).

Every metric is measured against the PDF's own text layer (``layout.pdf_text_layer``), with no
model involved. The checks are the deterministic gates of docs/audits/2026-10-24/01-ingest.md
§5.4 / §7 and restructure plan §R3, with their thresholds as written there; a failed check
makes the report ``accepted=False`` (the contract refuses anything else). A check that cannot
be measured on a book (no notes, no bookmarks, no page labels) is recorded with value None.

The PDF text layer is not human gold: it has its own defects (letter-spaced small caps,
running heads). Recall/precision against it measure loss and invention, not correctness.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from b2c_contracts import IngestQualityReport, ParsedBook, ParsedBookV2, QualityCheck
from b2c_core.source_text import TERMINAL, Vocab, heading_key, normalize_chars

from .normalize import CUT_MIN_WORDS, _edge_furniture_keys, _letters_key, _strip_trailing_marker, letter_spacing_residue

REPORT_VERSION = "1"
_WORD = re.compile(r"[a-z]{3,}")
_LIGATURE = re.compile("[ﬀ-ﬆ]")
_UNEXPECTED_SCRIPT = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿-�]")
_HYPHEN_GAP = re.compile(r"\b([A-Za-z]{2,})- ([a-z]{2,})\b")
_BODY_TEXT = frozenset({"paragraph", "list_item"})

# (name, op, threshold, source). Thresholds are the audit's; they are not tuned here.
GATES: list[tuple[str, str, float, str]] = [
    ("uncovered_text_pages", "<=", 0, "01 §5.4.1 / R3: every text page yields blocks or is recorded"),
    ("word_recall", ">=", 0.995, "01 §5.4.2"),
    ("word_precision", ">=", 0.995, "01 §5.4.2"),
    ("pages_recall_below_0_98", "<=", 0, "01 §5.4.2 / §7.4"),
    ("body_blocks_over_2000_chars", "<=", 0, "01 §5.4.3 / R3"),
    ("cut_at_page_break_rate", "<=", 0.01, "01 §5.4.4 / R3"),
    ("ligatures", "<=", 0, "01 §5.4.5"),
    ("letter_spacing_residue", "<=", 0, "01 §5.4.5 / R3 (Gramsci: 0 letter-spaced headings)"),
    ("hyphen_residue_rate", "<=", 0.0005, "01 §5.4.5"),
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
    bbox: list[bool] = field(default_factory=list)
    labels: list[bool] = field(default_factory=list)
    note_refs: int = 0
    linked: int = 0
    note_label: bool = True


def _words(text: str) -> Counter:
    return Counter(_WORD.findall(text.lower()))


def _shingles(text: str, n: int = 6) -> Counter:
    """Diagnostic, not a gate: 6-letter shingles of the letters-only stream, so a text layer
    that glues words ('ofthe', 'StrongpositiveandStrongnegative') is not counted as loss."""
    letters = re.sub(r"[^a-z]", "", text.lower())
    return Counter(letters[i:i + n] for i in range(max(0, len(letters) - n + 1)))


def _reference_pages(text_layer: dict[str, Any]) -> dict[int, str]:
    """Text layer per page, ligatures normalized and line-break hyphens joined."""
    out = {}
    for p in text_layer["pages"]:
        # "mobiliza -\ntion", "domina\u00ad\ntion": join before the soft hyphen is stripped
        text = re.sub(r"(?<=[a-z]) ?[-\u00ad\u2010] *\n *(?=[a-z])", "", p.get("text") or "")
        out[int(p["page_no"])] = normalize_chars(text)[0]
    return out


def _views_v2(book: ParsedBookV2) -> list[_View]:
    return [_View(type=b.type, zone=b.zone, text=b.text, pages=[r.page_no for r in b.refs],
                  flags=list(b.quality_flags), bbox=[r.bbox is not None for r in b.refs] if b.raw_text else [],
                  labels=[r.printed_label is not None for r in b.refs],
                  note_refs=len(b.note_refs), linked=sum(r.note_id is not None for r in b.note_refs),
                  note_label=b.type not in ("footnote", "endnote") or b.note_label is not None)
            for b in book.blocks]


_V1_ZONE = {"page_header": "furniture", "page_footer": "furniture"}


def _views_v1(book: ParsedBook) -> list[_View]:
    return [_View(type=b.type, zone=_V1_ZONE.get(b.type, "body"), text=b.text,
                  pages=[r.page_no for r in b.refs], bbox=[r.bbox is not None for r in b.refs],
                  labels=[r.printed_label is not None for r in b.refs])
            for b in book.blocks]


def _metrics(views: list[_View], text_layer: dict[str, Any], *, notes_known: bool) -> dict[str, float | int | None]:
    ref_pages = _reference_pages(text_layer)
    page_count = int(text_layer["page_count"])
    text_pages = {p for p, t in ref_pages.items() if len(_WORD.findall(t.lower())) >= 3}

    covered: set[int] = set()
    cand_by_page: dict[int, Counter] = {}
    cand = Counter()
    for v in views:
        w = _words(v.text)
        cand.update(w)
        for p in set(v.pages):
            covered.add(p)
            cand_by_page.setdefault(p, Counter()).update(w)
    ref = Counter()
    for t in ref_pages.values():
        ref.update(_words(t))
    overlap = sum((ref & cand).values())
    below = 0
    for p in sorted(text_pages):
        rp = _words(ref_pages[p])
        cp = cand_by_page.get(p, Counter())
        if rp and sum((rp & cp).values()) / sum(rp.values()) < 0.98:
            below += 1

    ref_sh = Counter()
    for t in ref_pages.values():
        ref_sh.update(_shingles(t))
    cand_sh = Counter()
    for v in views:
        cand_sh.update(_shingles(v.text))
    sh_overlap = sum((ref_sh & cand_sh).values())

    body = [v for v in views if v.type in _BODY_TEXT and v.zone != "furniture"]
    lengths = [len(v.text) for v in body if v.zone == "body"]

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
    cut = sum(1 for p, v in last_on_page.items() if p < max_page and p not in continued
              and len(v.text.split()) >= CUT_MIN_WORDS and not TERMINAL.search(_strip_trailing_marker(v.text)))

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
    return {
        "pages": page_count,
        "text_pages": len(text_pages),
        "blocks": len(views),
        "uncovered_text_pages": len(text_pages - covered),
        "word_recall": round(overlap / max(1, sum(ref.values())), 5),
        "word_precision": round(overlap / max(1, sum(cand.values())), 5),
        "pages_recall_below_0_98": below,
        "letter_shingle_recall": round(sh_overlap / max(1, sum(ref_sh.values())), 5),
        "letter_shingle_precision": round(sh_overlap / max(1, sum(cand_sh.values())), 5),
        "median_body_block_chars": statistics.median(lengths) if lengths else 0,
        "body_blocks_over_2000_chars": sum(1 for n in lengths if n > 2000),
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
    metrics = _metrics(_views_v2(book), text_layer, notes_known=True)
    zones = Counter(b.zone for b in book.blocks)
    metrics.pop("zones")
    metrics.update({f"zone_{z}": n for z, n in sorted(zones.items())})
    checks = _checks(metrics)
    accepted = not any(c.passed is False for c in checks) and any(c.passed for c in checks)
    return IngestQualityReport(book_id=book.book_id, report_version=REPORT_VERSION,
                               parsed_sha256=parsed_sha256, metrics=metrics, checks=checks, accepted=accepted)


def quality_report_v1(book: ParsedBook, text_layer: dict[str, Any]) -> IngestQualityReport:
    """The same measurement on a v1 ParsedBook (baseline). v1 has no note links: n/a."""
    metrics = _metrics(_views_v1(book), text_layer, notes_known=False)
    metrics.pop("zones")
    checks = _checks(metrics)
    accepted = not any(c.passed is False for c in checks) and any(c.passed for c in checks)
    return IngestQualityReport(book_id=book.book_id, report_version=REPORT_VERSION, metrics=metrics,
                               checks=checks, accepted=accepted)
