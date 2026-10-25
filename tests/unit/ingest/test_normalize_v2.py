"""S2 normalization (R3): parse intermediate -> ParsedBook v2.

All inputs are synthetic (no book text in git); each case mirrors a defect measured on the
real books in docs/audits/2026-10-24/01-ingest.md §3 (SAT: blocks cut at page breaks;
Gramsci: letter-spaced headings, lost endnote numbers, flat heading levels, notes coded as
`code`; all books: running heads and page numbers dropped or leaked into the body).
"""
from __future__ import annotations

import json

import pytest

from b2c_contracts import ParsedBookV2, parsed_book_v1_from_v2
from b2c_ingest.normalize import (
    NORMALIZER_VERSION, BookMeta, normalize_docling, normalize_text_layer,
)

H = 600.0  # page height (PDF points, bottom-left origin as Docling exports it)
META = BookMeta(book_id="bk", title="T", role="spine")
FILLER = ("The articulation of struggles shapes living philology and the analysis of the "
          "movement. Living philology is a method. The articulation of struggles matters. ")


class Doc:
    """Builds a minimal DoclingDocument export (texts/groups/tables/pictures + body tree)."""

    def __init__(self, first: int, last: int):
        self.d = {"schema_name": "DoclingDocument", "version": "1.8.0", "name": "x",
                  "furniture": {"self_ref": "#/furniture", "children": []},
                  "body": {"self_ref": "#/body", "children": []},
                  "groups": [], "texts": [], "pictures": [], "tables": [],
                  "key_value_items": [], "form_items": [],
                  "pages": {str(p): {"page_no": p, "size": {"width": 400.0, "height": H}}
                            for p in range(first, last + 1)},
                  "b2c_batch": {"format": "docling-document-batch/1", "first_page": first, "last_page": last}}

    def add(self, label, page, text, top=100.0, bottom=None, left=60.0, right=360.0, layer=None, **extra):
        ref = f"#/texts/{len(self.d['texts'])}"
        bottom = top + 40 if bottom is None else bottom
        layer = layer or ("furniture" if label in ("page_header", "page_footer") else "body")
        self.d["texts"].append({
            "self_ref": ref, "parent": {"$ref": "#/body"}, "children": [], "content_layer": layer,
            "label": label, "orig": text, "text": text,
            "prov": [{"page_no": page, "bbox": {"l": left, "t": H - top, "r": right, "b": H - bottom,
                                                "coord_origin": "BOTTOMLEFT"}, "charspan": [0, len(text)]}],
            **extra})
        self.d["body"]["children"].append({"$ref": ref})
        return self

    def json(self):
        return json.loads(json.dumps(self.d))


def text_layer(pages, labels=None, outline=()):
    return {"format": "pdf-text-layer/1", "pypdf_version": "6.1.3", "page_count": len(pages),
            "has_page_labels": labels is not None, "outline": list(outline),
            "pages": [{"page_no": i + 1, "label": (labels[i] if labels else None), "text": t,
                       "has_images": False, "width": 400.0, "height": H} for i, t in enumerate(pages)]}


def bookmark(title, depth, page, top=None):
    return {"title": title, "depth": depth, "page_index": page - 1, "top": top}


def body(book: ParsedBookV2):
    return [b for b in book.blocks if b.zone != "furniture"]


def test_paragraph_cut_at_page_break_becomes_one_block_with_both_pages():
    doc = (Doc(1, 2)
           .add("text", 1, FILLER)
           .add("text", 1, "Theory is interpretive and at some level it makes meaning, not", top=480, bottom=540)
           .add("page_footer", 1, "40", top=560, bottom=570, left=60, right=70)
           .add("page_footer", 1, "The Living Tradition", top=560, bottom=570, left=170, right=260)
           .add("text", 2, "laws. It discovers connections.", top=60, bottom=90)
           .add("page_footer", 2, "41", top=560, bottom=570, left=60, right=70))
    tl = text_layer(["x", "y"], labels=["40", "41"])
    book = normalize_docling(META, [doc.json()], tl)
    merged = [b for b in book.blocks if b.text.startswith("Theory")]
    assert len(merged) == 1
    m = merged[0]
    assert m.text == "Theory is interpretive and at some level it makes meaning, not laws. It discovers connections."
    assert [r.page_no for r in m.refs] == [1, 2]
    assert [r.printed_label for r in m.refs] == ["40", "41"]
    assert all(r.bbox is not None for r in m.refs)
    assert "joined_page_break" in m.normalization and "cut_at_page_break" not in m.quality_flags
    assert m.raw_text == "Theory is interpretive and at some level it makes meaning, not\nlaws. It discovers connections."
    furniture = [(b.type, b.text) for b in book.blocks if b.zone == "furniture"]
    assert ("page_number", "40") in furniture and ("running_footer", "The Living Tradition") in furniture
    assert not any("Living Tradition" in b.text for b in body(book))


def test_unjoinable_page_end_is_flagged_not_hidden():
    doc = (Doc(1, 2).add("section_header", 1, "Introduction", top=20, bottom=30).add("text", 1, FILLER)
           .add("text", 1, "A sentence that runs on for a while and then ends without", top=480)
           .add("section_header", 2, "New Section", top=60, bottom=80)
           .add("text", 2, "Fresh start.", top=90))
    book = normalize_docling(META, [doc.json()], text_layer(["x", "y"]))
    cut = next(b for b in book.blocks if b.text == "A sentence that runs on for a while and then ends without")
    assert "cut_at_page_break" in cut.quality_flags


def test_letter_spaced_heading_is_repaired_and_takes_its_level_from_the_bookmark():
    doc = (Doc(1, 3)
           .add("section_header", 1, "c o n t e n t s", top=40, bottom=60)
           .add("text", 1, "Introduction 1", top=80).add("text", 1, "The Living Tradition 15", top=120)
           .add("text", 1, "Notes 40", top=160)
           .add("section_header", 2, "Introduction", top=40, bottom=60)
           .add("text", 2, FILLER, top=80)
           .add("section_header", 2, "l i v i ng ph i lology", top=300, bottom=320)
           .add("text", 2, FILLER, top=330)
           .add("section_header", 3, "n otes", top=40, bottom=60)
           .add("text", 3, "1. Ibid., 4.", top=80))
    outline = [bookmark("Contents", 0, 1), bookmark("Introduction", 0, 2), bookmark("Notes", 0, 3)]
    book = normalize_docling(META, [doc.json()], text_layer(["a", "b", "c"], outline=outline))
    heads = [(b.text, b.level, b.level_source, b.zone) for b in book.blocks if b.type == "heading"]
    assert heads == [
        ("Contents", 1, "pdf_outline", "front_matter"),
        ("Introduction", 1, "pdf_outline", "body"),
        ("living philology", 2, "layout", "body"),
        ("Notes", 1, "pdf_outline", "back_matter"),
    ]
    assert [e.block_id is not None for e in book.outline] == [True, True, True]
    assert book.toc_source == "pdf_outline"
    toc = [b for b in book.blocks if b.type == "toc_entry"]
    assert [b.text for b in toc] == ["Introduction 1", "The Living Tradition 15", "Notes 40"]
    assert all(b.zone == "front_matter" for b in toc)
    sub = next(b for b in book.blocks if b.text.startswith("The articulation") and b.refs[0].page_no == 2
               and b.refs[0].bbox[1] > 300)
    assert sub.section_path == ["Introduction", "living philology"]


def test_bookmark_without_a_layout_heading_inserts_a_marked_heading():
    doc = Doc(1, 1).add("text", 1, FILLER, top=80)
    book = normalize_docling(META, [doc.json()], text_layer(["a"], outline=[bookmark("Preface", 0, 1)]))
    h = book.blocks[0]
    assert (h.type, h.text, h.raw_text, h.level) == ("heading", "Preface", "", 1)
    assert "heading_from_outline" in h.normalization
    assert book.outline[0].block_id == h.block_id


def test_endnotes_keep_labels_and_markers_link_to_them():
    doc = (Doc(1, 3)
           .add("section_header", 1, "Introduction", top=40, bottom=60)
           .add("text", 1, "Hegemony rests on consent.1 It is also organised force.2 " + FILLER, top=80)
           .add("section_header", 2, "Chapter One", top=40, bottom=60)
           .add("text", 2, "Movements articulate demands.1 " + FILLER, top=80)
           .add("section_header", 3, "Notes", top=40, bottom=60)
           .add("section_header", 3, "Introduction", top=70, bottom=80)
           .add("list_item", 3, "Femia, Political Thought, 52, 53.", top=90, bottom=100,
                marker="1.", enumerated=True)
           .add("code", 3, "2. Ibid., 119-124.", top=110, bottom=120)
           .add("section_header", 3, "Chapter One", top=130, bottom=140)
           .add("text", 3, "1. SPN, 243; Q13, §7.", top=150, bottom=160))
    outline = [bookmark("Introduction", 0, 1), bookmark("Chapter One", 0, 2), bookmark("Notes", 0, 3)]
    book = normalize_docling(META, [doc.json()], text_layer(["a", "b", "c"], outline=outline))
    notes = [b for b in book.blocks if b.type == "endnote"]
    assert [(b.note_label, b.text, b.zone) for b in notes] == [
        ("1", "Femia, Political Thought, 52, 53.", "back_matter"),
        ("2", "Ibid., 119-124.", "back_matter"),
        ("1", "SPN, 243; Q13, §7.", "back_matter"),
    ]
    intro = next(b for b in book.blocks if b.text.startswith("Hegemony"))
    assert intro.text.startswith("Hegemony rests on consent. It is also organised force. ")
    by_id = {n.note_id: n for n in book.notes}
    links = [(r.label, by_id[r.note_id].block_ids[0]) for r in intro.note_refs]
    assert links == [("1", notes[0].block_id), ("2", notes[1].block_id)]
    ch1 = next(b for b in book.blocks if b.text.startswith("Movements"))
    assert [by_id[r.note_id].block_ids[0] for r in ch1.note_refs] == [notes[2].block_id]
    # the v1 projection gives the markers and labels back
    v1 = parsed_book_v1_from_v2(book)
    assert any(b.text.startswith("Hegemony rests on consent.1 It") for b in v1.blocks)


def test_chapter_end_notes_link_to_their_chapter_despite_a_stray_marker():
    doc = (Doc(1, 5)
           .add("text", 1, "Published by the Press in London.1 All rights reserved. " + FILLER, top=80)
           .add("section_header", 2, "Chapter One", top=20, bottom=30)
           .add("text", 2, "First claim.1 Second claim.2 " + FILLER, top=40)
           .add("section_header", 3, "NOTES", top=20, bottom=30)
           .add("list_item", 3, "Doe, Book One, 4.", top=40, bottom=50, marker="1.")
           .add("list_item", 3, "Doe, Book One, 9.", top=60, bottom=70, marker="2.")
           .add("section_header", 4, "Chapter Two", top=20, bottom=30)
           .add("text", 4, "Third claim.1 Fourth claim.2 " + FILLER, top=40)
           .add("section_header", 5, "NOTES", top=20, bottom=30)
           .add("list_item", 5, "Roe, Book Two, 4.", top=40, bottom=50, marker="1.")
           .add("list_item", 5, "Roe, Book Two, 9.", top=60, bottom=70, marker="2."))
    outline = [bookmark("Chapter One", 0, 2), bookmark("Chapter Two", 0, 4)]
    book = normalize_docling(META, [doc.json()], text_layer(["a", "b", "c", "d", "e"], outline=outline))
    by_note = {n.note_id: n for n in book.notes}
    text_of = {b.block_id: b.text for b in book.blocks}

    def targets(prefix):
        b = next(b for b in book.blocks if b.text.startswith(prefix))
        return [text_of[by_note[r.note_id].block_ids[0]] for r in b.note_refs]

    assert targets("First claim") == ["Doe, Book One, 4.", "Doe, Book One, 9."]
    assert targets("Third claim") == ["Roe, Book Two, 4.", "Roe, Book Two, 9."]


def test_run_of_notes_in_one_item_is_split():
    doc = (Doc(1, 1).add("section_header", 1, "Notes", top=40, bottom=60)
           .add("code", 1, "140. Ibid., 119-124. 141. Ibid., 125. 142. SPN, 12.", top=80))
    book = normalize_docling(META, [doc.json()], text_layer(["a"], outline=[bookmark("Notes", 0, 1)]))
    assert [(b.type, b.note_label, b.text) for b in book.blocks if b.type == "endnote"] == [
        ("endnote", "140", "Ibid., 119-124."), ("endnote", "141", "Ibid., 125."), ("endnote", "142", "SPN, 12.")]


def test_note_run_continues_after_a_label_without_preceding_punctuation():
    doc = (Doc(1, 1).add("section_header", 1, "Notes", top=40, bottom=60)
           .add("code", 1, "5. Forecasting website, www.example.org/chart.pdf 6. Russell Frenske, "
                           "A Taxonomy. 7. Kai Larson, Studies.", top=80))
    book = normalize_docling(META, [doc.json()], text_layer(["a"], outline=[bookmark("Notes", 0, 1)]))
    assert [b.note_label for b in book.blocks if b.type == "endnote"] == ["5", "6", "7"]


def test_bibliography_and_index_are_typed_back_matter():
    doc = (Doc(1, 2)
           .add("section_header", 1, "Introduction", top=40, bottom=60).add("text", 1, FILLER, top=80)
           .add("section_header", 2, "Bibliography", top=40, bottom=60)
           .add("text", 2, "Femia, Joseph. Gramsci's Political Thought. Oxford: Clarendon Press, 1981.", top=70, bottom=80)
           .add("section_header", 2, "Index", top=200, bottom=220)
           .add("text", 2, "hegemony, 4, 12, 57-59", top=230, bottom=240)
           .add("text", 2, "passive revolution, 33, 120", top=250, bottom=260))
    outline = [bookmark("Introduction", 0, 1), bookmark("Bibliography", 0, 2), bookmark("Index", 0, 2)]
    book = normalize_docling(META, [doc.json()], text_layer(["a", "b"], outline=outline))
    kinds = [(b.type, b.zone) for b in book.blocks if b.refs[0].page_no == 2 and b.type != "heading"]
    assert kinds == [("bibliography_entry", "back_matter"), ("index_entry", "back_matter"),
                     ("index_entry", "back_matter")]


def test_cjk_from_cover_ocr_is_flagged_and_long_blocks_are_flagged():
    doc = Doc(1, 1).add("text", 1, "由").add("text", 1, ("A sentence of prose. " * 120).strip(), top=200)
    book = normalize_docling(META, [doc.json()], text_layer(["a"]))
    assert "unexpected_script" in book.blocks[0].quality_flags
    assert "long_block" in book.blocks[1].quality_flags


def test_ligatures_are_normalized_and_raw_text_kept():
    doc = Doc(1, 1).add("text", 1, "The eﬃcient ﬁnal analysis.")
    book = normalize_docling(META, [doc.json()], text_layer(["a"]))
    b = book.blocks[0]
    assert b.text == "The efficient final analysis." and "ﬃ" in b.raw_text
    assert "unicode_normalized" in b.normalization


def test_page_without_layout_items_is_reported_and_labels_fall_back_to_page_numbers():
    doc = (Doc(1, 3).add("text", 1, FILLER).add("page_footer", 1, "7", top=560, bottom=570)
           .add("page_footer", 3, "9", top=560, bottom=570))
    book = normalize_docling(META, [doc.json()], text_layer(["a", "", "c"]))
    assert [p.status for p in book.pages] == ["content", "empty", "furniture_only"]
    assert [p.printed_label for p in book.pages] == ["7", None, "9"]


def test_normalization_is_deterministic_and_versioned():
    doc = Doc(1, 2).add("text", 1, FILLER).add("text", 1, "cut", top=480).add("text", 2, "here.", top=60)
    tl = text_layer(["a", "b"])
    a = normalize_docling(META, [doc.json()], tl).model_dump_json()
    b = normalize_docling(META, [doc.json()], tl).model_dump_json()
    assert a == b
    assert json.loads(a)["normalizer"]["version"] == NORMALIZER_VERSION


def test_batches_are_read_in_page_order():
    first = Doc(1, 1).add("text", 1, "First page text, which goes")
    second = Doc(2, 2).add("text", 2, "on to the second page.", top=60)
    book = normalize_docling(META, [second.json(), first.json()], text_layer(["a", "b"]))
    assert body(book)[0].text == "First page text, which goes on to the second page."


def test_tracked_small_caps_headings_are_resegmented_with_known_words():
    doc = (Doc(1, 1)
           .add("section_header", 1, "popul ar forces and subalt ern groups", top=40, bottom=60)
           .add("section_header", 1, "gramsci a san original thinker", top=70, bottom=80)
           .add("section_header", 1, "space and time", top=90, bottom=100)
           .add("text", 1, "Popular forces and subaltern groups: Gramsci was an original thinker "
                           "of space and time. " * 3, top=120))
    book = normalize_docling(META, [doc.json()], text_layer(["a"]))
    heads = [b.text for b in book.blocks if b.type == "heading"]
    assert heads == ["popular forces and subaltern groups", "gramsci as an original thinker", "space and time"]
    assert not any("letter_spacing_residue" in b.quality_flags for b in book.blocks)


def test_docling_item_wrongly_joined_across_pages_is_cut_at_its_page_spans():
    doc = Doc(1, 3)
    text = "c o n t e n t s This book owes much to the insight of colleagues."
    doc.add("text", 1, text)
    doc.d["texts"][-1]["prov"] = [
        {"page_no": 1, "bbox": {"l": 60, "t": 500, "r": 200, "b": 480, "coord_origin": "BOTTOMLEFT"}, "charspan": [0, 15]},
        {"page_no": 3, "bbox": {"l": 60, "t": 560, "r": 360, "b": 300, "coord_origin": "BOTTOMLEFT"}, "charspan": [16, len(text)]},
    ]
    book = normalize_docling(META, [doc.json()], text_layer(["a", "b", "c"]))
    assert [(b.text, [r.page_no for r in b.refs]) for b in book.blocks] == [
        ("contents", [1]), ("This book owes much to the insight of colleagues.", [3])]


def test_ebook_figure_descriptions_and_back_links_are_figure_apparatus():
    doc = (Doc(1, 2)
           .add("section_header", 1, "Introduction", top=20, bottom=30).add("text", 1, FILLER, top=40)
           .add("section_header", 2, "Descriptions of Images and Figures", top=20, bottom=30)
           .add("section_header", 2, "Back to Figure", top=40, bottom=50)
           .add("text", 2, "The cells in each row show the impact of the variable on each other variable.", top=60)
           .add("section_header", 2, "Back to Figure", top=200, bottom=210)
           .add("text", 2, "The mechanisms of engagement range from conflict to cooperation.", top=220))
    book = normalize_docling(META, [doc.json()], text_layer(["a", "b"]))
    page2 = [(b.type, b.text[:14]) for b in book.blocks if b.refs[0].page_no == 2]
    assert page2 == [("heading", "Descriptions o"), ("caption", "Back to Figure"), ("caption", "The cells in e"),
                     ("caption", "Back to Figure"), ("caption", "The mechanisms")]


# ----------------------------------------------------------------------------- text-layer path
def test_text_layer_path_removes_running_heads_and_joins_pages():
    head = "Structured Analytic Techniques"
    WORDS = [None, "first", "second", "third", "fourth", "fifth", "sixth"]
    pages = []
    for p in range(1, 7):
        lines = [f"{head}", f"{p + 10}"] if p % 2 else [f"{p + 10}", "Chapter Title Here"]
        tail = f"The analyst must structure the {WORDS[p]} problem in such a way that the"
        body_lines = [FILLER.strip(), tail]
        if p > 1:
            body_lines = ["evidence is weighed. " + FILLER.strip(), tail]
        pages.append("\n".join(lines[:1] + body_lines + lines[1:]))
    pages[-1] = pages[-1].replace("in such a way that the", "in the end.")
    book = normalize_text_layer(META, text_layer(pages))
    assert not any(head in b.text or "Chapter Title Here" in b.text for b in body(book))
    assert any(b.type == "running_header" and b.text == head for b in book.blocks)
    assert any(b.type == "running_footer" and b.text == "Chapter Title Here" for b in book.blocks)
    assert sum(b.type == "page_number" for b in book.blocks) == 6
    joined = [b for b in body(book) if "such a way that the evidence is weighed" in b.text]
    assert len(joined) == 5 and all(len(b.refs) == 2 for b in joined)
    assert all(r.bbox is None for b in book.blocks for r in b.refs)


@pytest.mark.parametrize("path", ["docling", "text"])
def test_output_validates_as_contract(path):
    if path == "docling":
        book = normalize_docling(META, [Doc(1, 1).add("text", 1, FILLER).json()], text_layer(["a"]))
    else:
        book = normalize_text_layer(META, text_layer([FILLER]))
    assert ParsedBookV2.model_validate_json(book.model_dump_json()) == book
