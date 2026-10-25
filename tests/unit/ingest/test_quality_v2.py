"""Deterministic ingest gate (R3, audit 01 §5.4/§7; redesigned in D-040): the parsed text is
compared page by page with two independent extractions of the PDF text layer (pypdf and
pdfium), and the gate decision is a GateResult."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from b2c_contracts import ArtifactRef, Block, GateResult, PageRef, ParsedBook
from b2c_ingest.normalize import normalize_docling
from b2c_ingest.quality import (
    GATE_DEFINITION, GATE_ID, GATES, PAGE_AGREEMENT_MIN, gate_result, gate_version, quality_report, quality_report_v1,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_normalize_v2 import FILLER, META, Doc, bookmark, text_layer  # noqa: E402

P1 = FILLER + "The analyst must weigh the evidence about the"
P2 = "movement and its demands. " + FILLER
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def two_parsers(tl, pdfium=None, images=None):
    """Add the second, independent extraction (pdfium) the text-layer activity records."""
    for i, page in enumerate(tl["pages"]):
        page["text_pdfium"] = page["text"] if pdfium is None else pdfium[i]
        page["image_boxes"] = (images or {}).get(page["page_no"], [])
    tl["format"] = "pdf-text-layer/2"
    tl["pdfium_version"] = "5.13.0"
    return tl


def clean_book():
    doc = (Doc(1, 2)
           .add("section_header", 1, "Introduction", top=40, bottom=60)
           .add("text", 1, P1, top=80, bottom=500)
           .add("page_footer", 1, "1", top=560, bottom=570)
           .add("text", 2, P2, top=60, bottom=500)
           .add("page_footer", 2, "2", top=560, bottom=570))
    tl = two_parsers(text_layer(["Introduction\n" + P1 + "\n1", P2 + "\n2"], labels=["1", "2"],
                                outline=[bookmark("Introduction", 0, 1)]))
    return normalize_docling(META, [doc.json()], tl), tl


def checks(report):
    return {c.name: c for c in report.checks}


def test_clean_book_passes_every_measurable_gate():
    book, tl = clean_book()
    report = quality_report(book, tl, parsed_sha256="abc")
    assert report.accepted, report.failed
    m = report.metrics
    assert m["independent_references"] == 2
    assert m["cross_parser_word_recall"] == 1.0 and m["cross_parser_word_precision"] == 1.0
    assert m["pages_below_agreement"] == 0 and m["min_page_agreement"] == 1.0
    assert m["cut_at_page_break_rate"] == 0 and m["uncovered_text_pages"] == 0
    c = checks(report)
    assert c["note_link_rate"].passed is None  # no notes in this book: not measurable, not a pass
    assert {g[0] for g in GATES} == set(c)
    assert report.page_findings == {}


def test_the_2000_character_block_limit_is_gone():
    names = {g[0] for g in GATES}
    assert "body_blocks_over_2000_chars" not in names
    long_para = " ".join([FILLER] * 14).strip()  # ~2,300 characters, one real paragraph
    assert len(long_para) > 2000
    doc = Doc(1, 1).add("text", 1, long_para, top=60, bottom=560)
    tl = two_parsers(text_layer([long_para]))
    report = quality_report(normalize_docling(META, [doc.json()], tl), tl)
    assert report.accepted, report.failed
    assert "body_blocks_over_2000_chars" not in report.metrics


PARA_A = ("The analysts gathered every report they could find about the movement and read them all "
          "with care before the meeting began in the large room on the second floor of the old "
          "building near the river where the team had worked for many years. They agreed.")
PARA_B = ("Then the second paragraph opens with a full line of prose that continues for a while so "
          "that the line geometry of the page shows a short line followed by a long one here.")


def _lines(text, width=80):
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    return out + [line]


def test_two_paragraphs_in_one_block_are_found_without_a_length_limit():
    """D-040: what the 2,000-character limit stood for (one block holding several paragraphs) is
    measured directly: a paragraph end of the text layer (short last line, then a full line that
    opens a sentence) inside one block."""
    page = "\n".join(_lines(PARA_A) + _lines(PARA_B) + ["Closing line of the page."])
    tl = two_parsers(text_layer([page]))
    merged = Doc(1, 1).add("text", 1, PARA_A + " " + PARA_B + " Closing line of the page.", top=60, bottom=500)
    report = quality_report(normalize_docling(META, [merged.json()], tl), tl)
    assert report.metrics["text_layer_paragraph_ends"] == 1 and report.metrics["merged_paragraphs"] == 1
    assert "merged_paragraph_rate" in report.failed and report.page_findings["merged_paragraph_rate"] == [1]
    split = (Doc(1, 1).add("text", 1, PARA_A, top=60, bottom=200).add("text", 1, PARA_B, top=210, bottom=400)
             .add("text", 1, "Closing line of the page.", top=410, bottom=430))
    report = quality_report(normalize_docling(META, [split.json()], tl), tl)
    assert report.metrics["merged_paragraphs"] == 0 and "merged_paragraph_rate" not in report.failed


def test_one_text_extraction_is_not_enough():
    book, tl = clean_book()
    for page in tl["pages"]:
        del page["text_pdfium"]
    report = quality_report(book, tl)
    assert report.metrics["independent_references"] == 1
    assert report.failed == ["independent_references"]
    assert not report.accepted


def test_a_glued_text_layer_is_outvoted_by_the_independent_parser():
    """SAT: pypdf glues words at kerning gaps ('ofthe', 'theanalyst'); pdfium spaces them."""
    book, tl = clean_book()
    glued = tl["pages"][0]["text"].replace("of the ", "ofthe ").replace("the analysis", "theanalysis")
    tl["pages"][0]["text"] = glued  # pypdf
    report = quality_report(book, tl)
    m = report.metrics
    assert m["reference_disagreement_pages"] == 1  # the two parsers disagree on page 1 ...
    assert m["pages_below_agreement"] == 0  # ... and the parsed text agrees with one of them
    assert report.accepted, report.failed


def test_lost_text_fails_the_page_and_names_it():
    doc = (Doc(1, 3).add("text", 1, P1, top=80).add("text", 2, P2[:60], top=60)
           .add("text", 3, "Unrelated start. " + FILLER, top=60))
    tl = two_parsers(text_layer([P1, P2, "Unrelated start. " + FILLER]))
    report = quality_report(normalize_docling(META, [doc.json()], tl), tl)
    assert not report.accepted
    assert {"pages_below_agreement", "cross_parser_word_recall"} <= set(report.failed)
    assert report.page_findings["pages_below_agreement"] == [2]
    assert report.metrics["min_page_agreement"] < PAGE_AGREEMENT_MIN


def test_a_paragraph_across_pages_is_attributed_word_by_word():
    book, tl = clean_book()
    joined = [b for b in book.blocks if "about the movement" in b.text]
    assert len(joined) == 1 and {r.page_no for r in joined[0].refs} == {1, 2}
    report = quality_report(book, tl)
    assert report.metrics["min_page_agreement"] == 1.0


def test_text_read_from_an_image_is_not_counted_as_invented():
    """Docling OCRs pictures; a figure's words are not in any text layer."""
    doc = (Doc(1, 1).add("text", 1, FILLER.strip(), top=60, bottom=300)
           .add("text", 1, "Exploration matrix quadrant scenario indicator weight option criterion.", top=320,
                bottom=500))
    tl = two_parsers(text_layer([FILLER.strip()]), images={1: [[50.0, 310.0, 370.0, 520.0]]})
    report = quality_report(normalize_docling(META, [doc.json()], tl), tl)
    assert report.metrics["image_text_words"] == 8
    assert report.metrics["cross_parser_word_precision"] == 1.0
    assert report.accepted, report.failed


def test_letter_spaced_references_do_not_fail_a_repaired_page():
    """Gramsci title page: both parsers print 'j o h n c h a l c r a f t'; S2 repairs it."""
    doc = Doc(1, 1).add("text", 1, P1 + " John Chalcraft university press", top=60)
    raw = P1 + "\nj o h n c h a l c r a f t u n i v e r s i t y p r e s s"
    tl = two_parsers(text_layer([raw]))
    report = quality_report(normalize_docling(META, [doc.json()], tl), tl)
    assert report.metrics["pages_spacing_only"] == 1
    assert report.metrics["pages_below_agreement"] == 0


def test_glued_words_are_measured_against_the_independent_parser():
    # neither token is one the S2 splitter repairs ('ofthe' is in wordfreq; a capitalised first part
    # that is not a function word is never split), so the measurement sees both
    text = P1.replace("of the movement", "ofthe movement").replace("Living philology is", "Livingphilology is")
    doc = Doc(1, 1).add("text", 1, text, top=60)
    tl = two_parsers(text_layer([P1]))
    report = quality_report(normalize_docling(META, [doc.json()], tl), tl)
    assert report.metrics["glued_tokens"] == 2
    assert "glued_token_rate" in report.failed
    assert report.page_findings["glued_token_rate"] == [1]


def test_gate_result_records_the_decision_and_the_policy():
    book, tl = clean_book()
    report = quality_report(book, tl)
    artifact = ArtifactRef(stage="ingest-parse-v2", key="k" * 64, uri="s3://artifacts/c1/ingest-parse-v2/" + "k" * 64 + "/",
                           sha256="s" * 64, created_at=NOW)
    result = gate_result(report, artifact, decided_at=NOW)
    assert isinstance(result, GateResult)
    assert result.gate_id == GATE_ID and result.kind == "deterministic" and result.decider == "system"
    assert result.passed is report.accepted is True
    assert result.gate_version == gate_version() and result.gate_version.startswith("sha256:")
    assert result.evidence_ref == artifact.uri + "ingest-quality.json"
    assert result.metrics["failed_checks"] == [] and result.metrics["independent_references"] == 2
    thresholds = {c["name"]: c["threshold"] for c in GATE_DEFINITION["checks"]}
    assert thresholds["pages_below_agreement"] == 0 and GATE_DEFINITION["page_agreement_min"] == PAGE_AGREEMENT_MIN


def test_v1_baseline_is_measured_the_same_way():
    tl = two_parsers(text_layer(["Introduction\neﬃcient " + P1, P2]))
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
