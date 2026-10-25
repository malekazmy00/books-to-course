"""Deterministic outline gate (R4 / audit 02 §5.3): coverage = included + explicitly excluded = 100%,
0 malformed titles, no apparatus or back matter in lessons, documented size exceptions, and the
tree compared with a reference outline (title / boundary / level F1, zone agreement)."""
import pytest

import b2c_contracts as c
from b2c_outline.v2 import build_outline_v2
from b2c_outline.v2.gate import GATE_DEFINITION, compare_to_reference, cross_check, outline_quality
from b2c_outline.v2.reference import RefEntry, bookmark_entries, resolve_reference

from tests.unit.outline.v2_fixtures import gramsci_like, sat_like


def build(parsed):
    return build_outline_v2(parsed, course_id="crs")


def checks(report):
    return {ch.name: ch for ch in report.checks}


def edit_lesson(outline, lesson_id, **update):
    data = outline.model_dump(mode="python")
    for m in data["modules"]:
        for lesson in m["lessons"]:
            if lesson["lesson_id"] == lesson_id:
                lesson.update(update)
    return c.CourseOutlineV2.model_validate(data)


def test_built_outlines_pass_the_machine_gate():
    for parsed in (sat_like(), gramsci_like()):
        report = outline_quality(parsed, build(parsed))
        assert report.accepted, report.failed
        ch = checks(report)
        assert ch["coverage_ratio"].value == 1.0 and ch["malformed_titles"].value == 0
        assert ch["reference_boundary_f1"].value == 1.0 and ch["reference_title_f1"].value == 1.0
        assert report.reference == "pdf_outline"
        assert report.metrics["lessons"] == build(parsed).lesson_count


def test_a_malformed_title_fails_the_gate():
    parsed = sat_like()
    o = build(parsed)
    bad = edit_lesson(o, o.lessons[1].lesson_id, display_title="5.1 s o r t i n g")
    report = outline_quality(parsed, bad)
    assert not report.accepted and "malformed_titles" in report.failed
    assert report.metrics["malformed_title_examples"] == ["5.1 s o r t i n g"]


def test_back_matter_inside_a_lesson_fails_the_gate():
    parsed = gramsci_like()
    o = build(parsed)
    data = o.model_dump(mode="python")
    last = data["modules"][-1]["lessons"][-1]
    last["spine_span"]["end_block"] = parsed.blocks[-1].block_id  # swallow notes, bibliography and index
    data["excluded_ranges"] = [r for r in data["excluded_ranges"] if r["zone"] != "back_matter"]
    report = outline_quality(parsed, c.CourseOutlineV2.model_validate(data))
    assert {"apparatus_words_in_lessons", "back_matter_blocks_in_lessons", "reference_zone_accuracy"} <= set(report.failed)


def test_reference_f1_catches_a_lost_section_boundary_and_a_wrong_level():
    parsed = sat_like()
    o = build(parsed)
    data = o.model_dump(mode="python")
    lessons = data["modules"][0]["lessons"]
    lessons[1]["spine_span"]["end_block"] = lessons[2]["spine_span"]["end_block"]  # 5.1 swallows 5.2
    del lessons[2]
    merged = c.CourseOutlineV2.model_validate(data)
    ref = outline_quality(parsed, merged)
    assert ref.metrics["reference_boundary_recall"] == pytest.approx(5 / 6, abs=1e-4)
    assert "reference_boundary_f1" in ref.failed
    relevelled = edit_lesson(o, o.lessons[1].lesson_id, level=3)
    assert "reference_level_accuracy" in outline_quality(parsed, relevelled).failed


def test_an_outline_of_another_parse_is_refused():
    o = build(sat_like())
    other = gramsci_like()
    report = outline_quality(other, o)
    assert not report.accepted and "outline_matches_book" in report.failed


def test_golden_reference_entries_resolve_by_page_and_title():
    parsed = gramsci_like()
    golden = [{"title": "Copyright", "level": 1, "page": 2, "zone": "front_matter"},
              {"title": "Introduction", "level": 1, "page": 4, "zone": "body"},
              {"title": "1. The Originality of the Living Tradition", "level": 1, "page": 6, "zone": "body"},
              {"title": "Notes", "level": 1, "page": 9, "zone": "back_matter"}]
    entries = resolve_reference(parsed, golden)
    assert [e.index for e in entries] == [2, 8, 14, 23]
    metrics = compare_to_reference(parsed, build(parsed), entries)
    assert metrics["reference_boundary_f1"] == 1.0 and metrics["reference_zone_accuracy"] == 1.0
    assert metrics["reference_entries"] == 4 and metrics["reference_unresolved"] == 0


def test_unresolved_reference_entries_count_as_misses():
    parsed = gramsci_like()
    entries = bookmark_entries(parsed) + [RefEntry("A Missing Chapter", 1, 5, "body", None)]
    metrics = compare_to_reference(parsed, build(parsed), entries)
    assert metrics["reference_unresolved"] == 1 and metrics["reference_boundary_recall"] < 1


def test_gate_definition_is_versionable():
    names = [ch["name"] for ch in GATE_DEFINITION["checks"]]
    assert len(names) == len(set(names)) and "coverage_ratio" in names and "reference_title_f1" in names
    assert all(ch["op"] in ("==", ">=", "<=") for ch in GATE_DEFINITION["checks"])


# ----------------------------------------------------------------------------- automated cross-check
# Owner decision 2026-10-25: there is no human reviewer. Acceptance needs the structure gate and an
# independent cross-check of the chosen structure against the other sources; disagreement is a
# flag (precedence bookmarks > printed TOC > headings), only lost coverage rejects.
def test_cross_check_agrees_on_consistent_books():
    for parsed in (sat_like(), gramsci_like()):
        o = build(parsed)
        result = cross_check(parsed, o, outline_quality(parsed, o))
        assert result.passed and result.flags == [], result
        assert result.metrics["chosen_source"] == "pdf_outline"
        assert result.metrics["layout_heading_agreement"] == 1.0


def test_disagreeing_printed_toc_is_flagged_not_blocking():
    parsed = gramsci_like()
    blocks = list(parsed.blocks)
    # the printed TOC names a section heading as if it were a chapter of the tree
    blocks[5] = blocks[5].model_copy(update={"text": "Conclusion 7", "raw_text": "Conclusion 7"})
    parsed = parsed.model_copy(update={"blocks": blocks})
    o = build(parsed)
    result = cross_check(parsed, o, outline_quality(parsed, o))
    assert result.passed and "printed_toc_disagrees" in result.flags
    assert "bookmarks > printed TOC > layout headings" in result.reason


def test_bookmarks_without_layout_headings_are_flagged():
    from tests.unit.outline.v2_fixtures import Row, book, head, para
    rows = [head("Chapter 1 • A", 1, level=1, bm=(1, "Chapter 1 • A")), para(1, 800),
            Row("paragraph", "Chapter 2 • B", 2, bm=(1, "Chapter 2 • B")), para(2, 800),
            Row("paragraph", "Chapter 3 • C", 3, bm=(1, "Chapter 3 • C")), para(3, 800)]
    parsed = book(rows)
    o = build(parsed)
    result = cross_check(parsed, o, outline_quality(parsed, o))
    assert result.passed and result.flags == ["layout_headings_disagree"]
    assert result.metrics["layout_heading_agreement"] == pytest.approx(1 / 3, abs=1e-3)


def test_lost_coverage_fails_the_cross_check():
    o = build(sat_like())
    other = gramsci_like()
    result = cross_check(other, o, outline_quality(other, o))
    assert not result.passed and "coverage" in result.reason
