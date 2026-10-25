"""Deterministic ingest gate (R3, audit 01 §5.4/§7): metrics against the PDF text layer."""
from __future__ import annotations

import sys
from pathlib import Path

from b2c_contracts import Block, PageRef, ParsedBook
from b2c_ingest.normalize import normalize_docling
from b2c_ingest.quality import GATES, quality_report, quality_report_v1

sys.path.insert(0, str(Path(__file__).parent))
from test_normalize_v2 import FILLER, META, Doc, bookmark, text_layer  # noqa: E402

P1 = FILLER + "The analyst must weigh the evidence about the"
P2 = "movement and its demands. " + FILLER


def clean_book():
    doc = (Doc(1, 2)
           .add("section_header", 1, "Introduction", top=40, bottom=60)
           .add("text", 1, P1, top=80, bottom=500)
           .add("page_footer", 1, "1", top=560, bottom=570)
           .add("text", 2, P2, top=60, bottom=500)
           .add("page_footer", 2, "2", top=560, bottom=570))
    tl = text_layer(["Introduction\n" + P1 + "\n1", P2 + "\n2"], labels=["1", "2"],
                    outline=[bookmark("Introduction", 0, 1)])
    return normalize_docling(META, [doc.json()], tl), tl


def checks(report):
    return {c.name: c for c in report.checks}


def test_clean_book_passes_every_measurable_gate():
    book, tl = clean_book()
    report = quality_report(book, tl, parsed_sha256="abc")
    assert report.accepted, report.failed
    m = report.metrics
    assert m["word_recall"] == 1.0 and m["word_precision"] == 1.0
    assert m["cut_at_page_break_rate"] == 0 and m["uncovered_text_pages"] == 0
    c = checks(report)
    assert c["note_link_rate"].passed is None  # no notes in this book: not measurable, not a pass
    assert {g[0] for g in GATES} == set(c)


def test_lost_page_and_cut_paragraph_fail_the_gate():
    doc = (Doc(1, 3).add("text", 1, P1, top=80).add("text", 3, "Unrelated start. " + FILLER, top=60))
    tl = text_layer([P1, P2, "Unrelated start. " + FILLER])
    report = quality_report(normalize_docling(META, [doc.json()], tl), tl)
    assert not report.accepted
    assert {"uncovered_text_pages", "word_recall", "cut_at_page_break_rate"} <= set(report.failed)


def test_v1_baseline_is_measured_the_same_way():
    tl = text_layer(["Introduction\neﬃcient " + P1, P2])
    v1 = ParsedBook(book_id="bk", title="T", role="spine", page_count=2, toc_source="headings", parser={},
                    blocks=[Block(block_id="bk:1:0", type="paragraph", text="eﬃcient " + P1,
                                  refs=[PageRef(book_id="bk", page_no=1)], section_path=[], ocr=False),
                            Block(block_id="bk:2:0", type="paragraph", text=P2,
                                  refs=[PageRef(book_id="bk", page_no=2)], section_path=[], ocr=False)])
    report = quality_report_v1(v1, tl)
    m = report.metrics
    assert m["ligatures"] == 1 and m["cut_at_page_break_pages"] == 1 and m["bbox_coverage"] == 0
    assert m["note_link_rate"] is None
    assert not report.accepted
