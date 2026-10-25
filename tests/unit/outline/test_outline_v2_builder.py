"""Outline v2 builder (R4 / audit 02 §5.2): bookmarks first, reconciled with the printed TOC and
headings; front/back matter excluded explicitly (never lessons); block-granular boundaries;
cleaned titles; size bounds reported; stable ids."""
import pytest

import b2c_contracts as c
from b2c_outline.v2 import OutlineConfig, OutlineUnavailable, build_outline_v2

from tests.unit.outline.v2_fixtures import book, furniture, gramsci_like, head, para, rows_of, sat_like


def build(parsed, **kw):
    return build_outline_v2(parsed, course_id="crs", **kw)


def seq(block_id: str) -> int:
    return c.v2_block_seq(block_id)


def by_title(outline, title):
    return next(l for l in outline.lessons if l.display_title == title)


# ----------------------------------------------------------------------------- SAT-like
def test_bookmarked_chapters_and_sections_become_modules_and_lessons():
    o = build(sat_like())
    assert [m.display_title for m in o.modules] == ["Chapter 1 • Getting Organized", "Chapter 2 • Exploration Techniques"]
    assert all(m.source == "pdf_outline" and m.level == 1 for m in o.modules)
    titles = [l.display_title for l in o.lessons]
    assert titles == ["Chapter 1 · Introduction", "5.1 Sorting", "5.2 Ranking, Scoring, and Prioritizing",
                      "6.1 Simple Brainstorming", "6.2 Cluster Brainstorming"]
    assert o.structure_source == "pdf_outline" and o.requires_review is True


def test_a_body_line_read_as_heading_is_not_a_lesson_when_bookmarks_exist():  # P4
    o = build(sat_like())
    sorting = by_title(o, "5.1 Sorting")
    assert seq(sorting.spine_span.start_block) == 9 and seq(sorting.spine_span.end_block) == 13
    assert "bk:b00011" in sorting.subheadings  # '5.2 SORTING' stays inside 5.1
    assert sorting.word_count == 900 + 900 + 300


def test_chapter_intro_is_its_own_lesson_not_hidden_under_the_first_section():
    o = build(sat_like())
    intro = o.modules[0].lessons[0]
    assert intro.kind == "chapter_intro" and (seq(intro.spine_span.start_block), seq(intro.spine_span.end_block)) == (6, 8)
    assert intro.source_title == "Chapter 1 • Getting Organized"
    # chapter 2 has no intro text: its heading folds into the first section, which keeps its title
    first = o.modules[1].lessons[0]
    assert first.display_title == "6.1 Simple Brainstorming" and seq(first.spine_span.start_block) == 24
    assert first.anchor_block == "bk:b00026"


def test_front_matter_and_chapter_end_apparatus_are_excluded_with_reasons():
    o = build(sat_like())
    ranges = [(seq(r.start_block), seq(r.end_block), r.zone, r.reason) for r in o.excluded_ranges]
    assert ranges == [(0, 1, "front_matter", "title_page"), (2, 3, "front_matter", "toc"),
                      (4, 5, "front_matter", "preface"),
                      (19, 21, "body", "endnotes"), (22, 23, "body", "figure_descriptions")]
    ranking = by_title(o, "5.2 Ranking, Scoring, and Prioritizing")
    assert seq(ranking.spine_span.end_block) == 18  # ends right before NOTES (furniture included)


def test_front_matter_policy_can_include_the_preface():
    o = build(sat_like(), config=OutlineConfig(include_front=frozenset({"preface"})))
    assert o.modules[0].display_title == "Preface" and o.modules[0].lessons[0].kind == "chapter"
    assert [r.reason for r in o.excluded_ranges][:2] == ["title_page", "toc"]


def test_bookmark_in_the_middle_of_a_page_bounds_the_lesson_at_block_level():  # P6
    o = build(sat_like())
    sorting, ranking = by_title(o, "5.1 Sorting"), by_title(o, "5.2 Ranking, Scoring, and Prioritizing")
    # both touch page 7: the boundary is the heading block, not the page top
    assert sorting.spine_span.pages == (5, 7) and ranking.spine_span.pages[0] == 7
    assert seq(ranking.spine_span.start_block) == seq(sorting.spine_span.end_block) + 1 == 14


def test_every_block_is_included_or_excluded():
    parsed = sat_like()
    o = build(parsed)
    covered = set()
    for item in [*(l.spine_span for l in o.lessons), *o.excluded_ranges]:
        a, b = item.seqs
        covered.update(range(a, b + 1))
    assert covered == set(range(len(parsed.blocks))) and o.block_count == len(parsed.blocks)


def test_size_bounds_are_reported_and_large_sections_split_at_child_bookmarks():
    rows = [head("Chapter 8 • Reframing", 1, level=1, bm=(1, "Chapter 8 • Reframing")),
            head("8.1 Cause and Effect", 1, level=2, bm=(2, "8.1 Cause and Effect")),
            para(1, 200),
            head("8.1.1 Outside-In Thinking", 2, level=3, bm=(3, "8.1.1 Outside-In Thinking")),
            para(2, 2500),
            head("8.1.2 Structured Analogies", 3, level=3, bm=(3, "8.1.2 Structured Analogies")),
            para(3, 2500),
            head("8.2 Short", 4, level=2, bm=(2, "8.2 Short")),
            para(4, 100),
            head("8.3 Huge", 5, level=2, bm=(2, "8.3 Huge")),
            para(5, 5000)]
    o = build(book(rows), config=OutlineConfig(size_policy=c.LessonSizePolicy(policy_id="t", min_words=300,
                                                                               max_words=4000)))
    titles = [(l.display_title, l.level, l.size_status) for l in o.lessons]
    assert titles == [("8.1 Cause and Effect", 2, "below_min"), ("8.1.1 Outside-In Thinking", 3, "ok"),
                      ("8.1.2 Structured Analogies", 3, "ok"), ("8.2 Short", 2, "below_min"),
                      ("8.3 Huge", 2, "above_max")]
    assert all(l.size_exception for l in o.lessons if l.size_status != "ok")


# ----------------------------------------------------------------------------- Gramsci-like
def test_chapter_bookmarks_with_layout_sections_and_back_matter_excluded():
    o = build(gramsci_like())
    assert [m.display_title for m in o.modules] == ["Introduction", "1. The Originality of the Living Tradition"]
    assert [r.reason for r in o.excluded_ranges] == ["title_page", "copyright", "toc", "endnotes", "bibliography",
                                                     "index"]
    assert {r.zone for r in o.excluded_ranges[-3:]} == {"back_matter"}
    lessons = [(l.display_title, l.source, l.kind) for l in o.lessons]
    assert lessons == [
        ("Introduction", "pdf_outline", "chapter_intro"),
        ("Gramsci: Lodestar of Renewal", "heading", "section"),
        ("Critical Approaches", "heading", "section"),
        ("Chapter 1 · Introduction", "pdf_outline", "chapter_intro"),
        ("Readings of Gramsci", "heading", "section"),  # 'The Philosophy of Praxis' (150 words) merged in
        ("Chapter 1 · Conclusion", "heading", "section"),
    ]
    readings = by_title(o, "Readings of Gramsci")
    assert readings.subheadings == ["bk:b00018"] and readings.word_count == 1350
    critical = by_title(o, "Critical Approaches")
    assert critical.source_title == "critic al approaches" and critical.title_source == "heading"


def test_back_matter_words_never_reach_a_lesson():
    parsed = gramsci_like()
    o = build(parsed)
    last = o.lessons[-1]
    assert seq(last.spine_span.end_block) == 22  # stops before 'Notes'
    kinds = {parsed.blocks[i].type for l in o.lessons for i in range(*(lambda a, b: (a, b + 1))(*l.spine_span.seqs))}
    assert not kinds & {"endnote", "bibliography_entry", "index_entry"}


def test_ids_are_stable_across_reparse_and_block_renumbering():
    a = build(gramsci_like())
    original = gramsci_like()
    # same book with one more running header before everything: every block id shifts by one
    shifted = book([furniture(1, "From Subordination")] + rows_of(original),
                   labels={p.page_no: p.printed_label for p in original.pages if p.printed_label})
    assert shifted.blocks[1].text == original.blocks[0].text and shifted.blocks[1].block_id != original.blocks[0].block_id
    b = build(shifted)
    assert [l.lesson_id for l in a.lessons] == [l.lesson_id for l in b.lessons]
    assert [m.module_id for m in a.modules] == [m.module_id for m in b.modules]
    assert build(gramsci_like()) == a  # deterministic
    assert all(l.prior_lessons == [] for l in a.lessons)


def test_ids_are_readable_and_unique():
    o = build(gramsci_like())
    ids = [m.module_id for m in o.modules] + [l.lesson_id for l in o.lessons]
    assert len(set(ids)) == len(ids)
    assert o.modules[0].lessons[0].lesson_id == o.modules[0].module_id + ".intro"
    assert o.lessons[2].lesson_id.startswith(o.modules[0].module_id + ".critical-approaches-")


# ----------------------------------------------------------------------------- other sources
def test_printed_toc_is_used_when_there_are_no_bookmarks():
    rows = [head("Contents", 1, zone="front_matter"),
            para(1, kind="toc_entry", zone="front_matter", text="Getting Started 2"),
            para(1, kind="toc_entry", zone="front_matter", text="Going Further 3"),
            head("Getting Started", 2, level=2), para(2, 800),
            head("Going Further", 3, level=2), para(3, 800)]
    o = build(book(rows, labels={2: "2", 3: "3"}))
    assert o.structure_source == "printed_toc"
    assert [(m.display_title, m.source) for m in o.modules] == [("Getting Started", "printed_toc"),
                                                                ("Going Further", "printed_toc")]
    assert [r.reason for r in o.excluded_ranges] == ["toc"]


def test_headings_are_the_last_resort():
    rows = [head("1 Basics", 1, level=1, level_source="numbering"), para(1, 800),
            head("1.1 Terms", 1, level=2, level_source="numbering"), para(1, 800),
            head("2 Methods", 2, level=1, level_source="numbering"), para(2, 900)]
    o = build(book(rows))
    assert o.structure_source == "heading"
    assert [(m.display_title, [l.display_title for l in m.lessons]) for m in o.modules] == [
        ("1 Basics", ["Chapter 1 · Introduction", "1.1 Terms"]), ("2 Methods", ["2 Methods"])]


def test_a_book_without_structure_is_refused():
    with pytest.raises(OutlineUnavailable):
        build(book([para(1, 800), para(2, 800)]))


def test_v1_projection_of_a_built_outline():
    parsed = sat_like()
    v1 = c.course_outline_v1_from_v2(build(parsed), parsed)
    assert [l.title for m in v1.modules for l in m.lessons][1] == "5.1 Sorting"
    assert len(v1.excluded_prefix_block_ids) == 6
