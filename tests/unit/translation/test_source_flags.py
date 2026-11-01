"""Extraction flags reach the translation units (D-052 point 4): failed ingest checks name their
pages, and the normalizer's residual block defects name their blocks."""
from __future__ import annotations

import b2c_contracts as c
from b2c_translation.notes import REASON_AR, reasons_for
from b2c_translation.source_quality import SOURCE_ISSUE_ID, UNCLEAR_BLOCK_FLAGS, flag_units, source_issue


def unit(uid, pages, blocks):
    return c.TranslationUnit(unit_id=uid, book_id="bk", block_ids=blocks, kind="prose", source_text="Text.",
                             pages=pages, section_path=[])


def check(name, passed):
    return c.QualityCheck(name=name, passed=passed, value=0.0 if passed else 1.0, op="<=", threshold=0.0)


def report(accepted, findings, failed=()):
    checks = [check(n, n not in failed) for n in ("pages_below_agreement", "glued_token_rate", "word_count")]
    return c.IngestQualityReport.model_construct(checks=checks, accepted=accepted, page_findings=findings)


V1_FLAGS = {"bk:2:0": ["letter_spacing_residue"], "bk:3:0": ["note_without_label"]}


def test_pages_of_failed_checks_and_unclear_blocks_flag_their_units():
    units = [unit("bk:u1", [1], ["bk:1:0"]), unit("bk:u2", [2], ["bk:2:0"]), unit("bk:u3", [3], ["bk:3:0"])]
    rep = report(False, {"pages_below_agreement": [1], "glued_token_rate": [3]}, failed=("pages_below_agreement",))
    out = {u.unit_id: u.source_flags for u in flag_units(units, block_flags=V1_FLAGS, report=rep)}
    assert out == {"bk:u1": ["page:pages_below_agreement"], "bk:u2": ["block:letter_spacing_residue"], "bk:u3": []}
    assert "note_without_label" not in UNCLEAR_BLOCK_FLAGS     # structure, not the reading of the text


def test_an_accepted_parse_flags_only_unclear_blocks():
    units = [unit("bk:u1", [1], ["bk:1:0"]), unit("bk:u2", [2], ["bk:2:0"])]
    rep = report(True, {"pages_below_agreement": [1]})
    out = {u.unit_id: u.source_flags for u in flag_units(units, block_flags=V1_FLAGS, report=rep)}
    assert out == {"bk:u1": [], "bk:u2": ["block:letter_spacing_residue"]}


def test_the_source_issue_gives_the_learner_the_source_reason():
    issue = source_issue(["page:pages_below_agreement"])
    assert issue.check_id == SOURCE_ISSUE_ID and issue.severity == "major"
    assert reasons_for([issue]) == ["source"]
    assert "لم يُستخرج من الكتاب بثقة" in REASON_AR["source"]


def test_v1_block_ids_get_the_flags_of_their_v2_blocks():
    from b2c_translation.source_quality import v1_block_flags
    blocks = [c.BlockV2(block_id=f"bk:b{i:05d}", type="paragraph", zone="body", text=t, raw_text=t, ocr=False,
                        refs=[c.PageRef(book_id="bk", page_no=p)], section_path=[], quality_flags=f)
              for i, (p, t, f) in enumerate([(1, "A.", []), (1, "B.", ["letter_spacing_residue"]), (2, "C.", ["x"])])]
    book = c.ParsedBookV2(book_id="bk", title="T", role="spine", page_count=2, toc_source="pdf_outline",
                          pages=[c.PageInfo(page_no=1, status="content"), c.PageInfo(page_no=2, status="content")],
                          blocks=blocks, parser={"name": "f", "version": "1"}, normalizer={"name": "n", "version": "1"})
    assert v1_block_flags(book) == {"bk:1:1": ["letter_spacing_residue"], "bk:2:0": ["x"]}
    assert [b.block_id for b in c.parsed_book_v1_from_v2(book).blocks] == ["bk:1:0", "bk:1:1", "bk:2:0"]
