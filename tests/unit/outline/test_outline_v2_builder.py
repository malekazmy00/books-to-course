"""Outline v2 builder (R4 / audit 02 §5.2): bookmarks first, reconciled with the printed TOC and
headings; front/back matter excluded explicitly (never lessons); block-granular boundaries;
cleaned titles; size bounds reported; stable ids."""
import pytest

import b2c_contracts as c
from b2c_outline.v2 import OutlineConfig, OutlineUnavailable, build_outline_v2

from tests.unit.outline.v2_fixtures import (
    book, furniture, gramsci_like, gramsci_like_with_acknowledgments, head, para, rows_of, sat_like,
)


def build(parsed, **kw):
    return build_outline_v2(parsed, course_id="crs", **kw)


def seq(block_id: str) -> int:
    return c.v2_block_seq(block_id)


def by_title(outline, title):
    return next(l for l in outline.lessons if l.display_title == title)


# ----------------------------------------------------------------------------- SAT-like
def test_bookmarked_chapters_and_sections_become_modules_and_lessons():
    o = build(sat_like())
    assert [m.display_title for m in o.modules] == ["Introduction to the Book", "Chapter 1 • Getting Organized",
                                                    "Chapter 2 • Exploration Techniques"]
    assert all(m.source == "pdf_outline" and m.level == 1 for m in o.modules)
    titles = [l.display_title for l in o.lessons]
    assert titles == ["Preface", "Chapter 1 · Introduction", "5.1 Sorting", "5.2 Ranking, Scoring, and Prioritizing",
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
    intro = o.modules[1].lessons[0]
    assert intro.kind == "chapter_intro" and (seq(intro.spine_span.start_block), seq(intro.spine_span.end_block)) == (6, 8)
    assert intro.source_title == "Chapter 1 • Getting Organized"
    # chapter 2 has no intro text: its heading folds into the first section, which keeps its title
    first = o.modules[2].lessons[0]
    assert first.display_title == "6.1 Simple Brainstorming" and seq(first.spine_span.start_block) == 24
    assert first.anchor_block == "bk:b00026"


def test_front_matter_and_chapter_end_notes_are_excluded_with_reasons():
    o = build(sat_like())
    ranges = [(seq(r.start_block), seq(r.end_block), r.zone, r.reason) for r in o.excluded_ranges]
    assert ranges == [(0, 1, "front_matter", "title_page"), (2, 3, "front_matter", "toc"),
                      (19, 21, "body", "endnotes")]
    ranking = by_title(o, "5.2 Ranking, Scoring, and Prioritizing")
    assert seq(ranking.spine_span.end_block) == 18  # ends right before NOTES (furniture included)


def test_figure_descriptions_stay_in_the_lesson_in_reading_order_with_block_roles():  # D-036 (1)
    o = build(sat_like())
    ranking = by_title(o, "5.2 Ranking, Scoring, and Prioritizing")
    # the chapter's descriptions follow its NOTES: they are read at the end of the chapter's last
    # lesson (the notes stay excluded) and count toward the lesson
    assert [(e.seqs, e.role) for e in ranking.extra_spans] == [((22, 23), "figure_descriptions")]
    assert [(r.seqs, r.role) for r in ranking.block_roles] == [((22, 22), "figure_description_heading"),
                                                               ((23, 23), "figure_description")]
    assert ranking.word_count == 1200 + 1000 + 30
    assert "bk:b00022" not in ranking.subheadings


def test_figure_markers_captions_and_descriptions_inside_a_lesson_are_marked():
    rows = [head("Chapter 1 • Basics", 1, level=1, bm=(1, "Chapter 1 • Basics")),
            head("1.1 Terms", 1, level=2, bm=(2, "1.1 Terms")),
            para(1, 700),
            para(1, 4, kind="figure", text="Box A Box B"),
            para(1, 7, kind="caption", text="Figure 1.1 Two Boxes and an Arrow"),
            para(2, 300),
            head("Descriptions of Images and Figures", 2, level=3),  # 6: no notes before it
            para(2, 3, kind="caption", text="Back to Figure"),
            para(2, 40, kind="caption"),
            para(2, 3, kind="caption", text="Back to Figure"),
            para(2, 30, kind="caption"),
            head("1.2 Methods", 3, level=2, bm=(2, "1.2 Methods")),
            para(3, 800)]
    o = build(book(rows))
    terms = by_title(o, "1.1 Terms")
    assert terms.spine_span.seqs == (0, 10) and terms.extra_spans == []
    assert [(r.seqs, r.role) for r in terms.block_roles] == [
        ((4, 4), "figure_caption"), ((6, 6), "figure_description_heading"), ((7, 7), "figure_description_marker"),
        ((8, 8), "figure_description"), ((9, 9), "figure_description_marker"), ((10, 10), "figure_description")]
    assert terms.word_count == 700 + 4 + 7 + 300 + 3 + 40 + 3 + 30  # heading words do not count
    assert o.excluded_ranges == []


def test_front_matter_policy_can_include_the_preface():
    o = build(sat_like(), config=OutlineConfig(include_front=frozenset({"preface"})))
    assert o.modules[0].display_title == "Preface" and o.modules[0].lessons[0].kind == "chapter"
    assert [r.reason for r in o.excluded_ranges][:2] == ["title_page", "toc"]
    assert "preface" not in {l.kind for l in o.lessons}


def test_preface_foreword_and_acknowledgments_become_lessons_before_chapter_1():  # D-036 (2)
    rows = [head("Copyright", 1, level=1, zone="front_matter", bm=(1, "Copyright")),
            para(1, 30, zone="front_matter", text="© 2020 SAGE. All rights reserved. ISBN 978-1-5063-6893-1"),
            head("Contents", 2, level=1, zone="front_matter", bm=(1, "Contents")),
            para(2, kind="toc_entry", zone="front_matter", text="Chapter 1 • Basics 9"),
            head("Foreword by John McLaughlin", 3, level=1, zone="front_matter", bm=(1, "Foreword by John McLaughlin")),
            para(3, 400, zone="front_matter"),  # 5
            head("Preface", 4, level=1, zone="front_matter", bm=(1, "Preface")),  # 6
            para(4, 500, zone="front_matter"),
            para(4, 40, zone="front_matter", kind="table", text="| Edition | Year |\n| --- | --- |\n| 1 | 2010 |"),
            head("ACKNOWLEDGMENTS", 5, level=2, zone="front_matter"),  # 9: inside the Preface bookmark range
            para(5, 200, zone="front_matter"),
            furniture(5),
            para(5, 60, zone="front_matter"),
            head("DISCLAIMER", 6, level=2, zone="front_matter"),  # 13
            para(6, 50, zone="front_matter"),
            head("Chapter 1 • Basics", 7, level=1, bm=(1, "Chapter 1 • Basics")),  # 15
            para(7, 900)]
    o = build(book(rows))
    front = o.modules[0]
    assert front.order == 1 and front.display_title == "Introduction to the Book"
    assert [(l.display_title, l.kind, l.spine_span.seqs) for l in front.lessons] == [
        ("Foreword by John McLaughlin", "front_matter", (4, 5)), ("Preface", "front_matter", (6, 14))]
    preface = front.lessons[1]
    assert preface.word_count == 500 + 15 + 200 + 60 + 50  # the table has 15 words
    assert preface.size_status == "ok"
    assert [(a.seqs, a.reason) for a in preface.audio_adaptations] == [
        ((8, 8), "table"), ((10, 12), "acknowledgements"), ((14, 14), "legal_notice")]
    assert all(a.note for a in preface.audio_adaptations)
    foreword = front.lessons[0]
    assert foreword.size_status == "below_min" and "front matter" in foreword.size_exception
    assert foreword.audio_adaptations == []
    # copyright and contents stay excluded with their reasons
    assert [(r.seqs, r.reason) for r in o.excluded_ranges] == [((0, 1), "copyright"), ((2, 3), "toc")]
    assert o.modules[1].display_title == "Chapter 1 • Basics" and o.modules[1].order == 2


def test_a_standalone_acknowledgments_section_is_a_lesson_marked_for_audio_adaptation():  # D-036 (2)
    o = build(gramsci_like_with_acknowledgments())
    ack = o.modules[0].lessons[0]
    assert (ack.display_title, ack.kind) == ("Acknowledgments", "front_matter")
    assert [(a.seqs, a.reason) for a in ack.audio_adaptations] == [((9, 9), "acknowledgements")]
    assert "acknowledgments" not in {r.reason for r in o.excluded_ranges}


def test_long_lists_in_front_matter_are_marked_and_kept():
    rows = [head("Preface", 1, level=1, zone="front_matter", bm=(1, "Preface")),
            para(1, 300, zone="front_matter"),
            *[para(1, 6, zone="front_matter", kind="list_item") for _ in range(9)],  # 2..10: credits
            para(2, 100, zone="front_matter"),
            para(2, 20, zone="front_matter", kind="list_item"),  # a short list stays as is
            head("Chapter 1 • Basics", 3, level=1, bm=(1, "Chapter 1 • Basics")),
            para(3, 900)]
    o = build(book(rows))
    preface = o.modules[0].lessons[0]
    assert [(a.seqs, a.reason) for a in preface.audio_adaptations] == [((2, 10), "long_list")]
    assert preface.word_count == 300 + 54 + 100 + 20


def test_front_lessons_policy_can_keep_the_preface_excluded():
    o = build(sat_like(), config=OutlineConfig(front_lessons=frozenset()))
    assert "preface" in {r.reason for r in o.excluded_ranges}
    assert o.modules[0].display_title == "Chapter 1 • Getting Organized"


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
    for item in [*(l.spine_span for l in o.lessons), *(e for l in o.lessons for e in l.extra_spans),
                 *o.excluded_ranges]:
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
    assert [l.title for m in v1.modules for l in m.lessons][2] == "5.1 Sorting"
    assert len(v1.excluded_prefix_block_ids) == 4  # the preface is a lesson (D-036)
    assert [l.title for m in v1.modules for l in m.lessons][0] == "Preface"
