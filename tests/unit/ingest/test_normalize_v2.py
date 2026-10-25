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


def test_cjk_from_cover_ocr_is_flagged_and_long_paragraphs_are_not():
    # D-043: a paragraph is not defective for being long (the 2,000-character flag is gone)
    doc = Doc(1, 1).add("text", 1, "由").add("text", 1, ("A sentence of prose. " * 120).strip(), top=200)
    book = normalize_docling(META, [doc.json()], text_layer(["a"]))
    assert "unexpected_script" in book.blocks[0].quality_flags
    assert len(book.blocks[1].text) > 2000 and book.blocks[1].quality_flags == []


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


# ----------------------------------------------------------------------------- D-045 fixes
# Each case mirrors a defect the ingest-v2.quality gate named on the real books
# (eval/reports/ingest-v2-gate.md §3.3, §5; eval/reports/ingest-v2-fixes.md).
def text_layer2(pdfium, pypdf=None, image_boxes=None, outline=()):
    """pdf-text-layer/2: pypdf text, pdfium text and pdfium raster-image boxes (top-left)."""
    tl = text_layer(pypdf if pypdf is not None else pdfium, outline=outline)
    tl["format"] = "pdf-text-layer/2"
    for i, page in enumerate(tl["pages"]):
        page["text_pdfium"] = pdfium[i]
        page["image_boxes"] = (image_boxes or {}).get(i + 1, [])
    return tl


P1 = ["The analyst weighs the evidence for each hypothesis with great care and",
      "records every judgment in a matrix that the whole team can review and",
      "challenge before the final report goes out to the decision makers today.",
      "That is the first step of the method."]
P2 = ["Structured techniques then help the team test the strongest hypothesis",
      "against the evidence that is most diagnostic for the question at hand and",
      "identify the gaps that further collection should fill in the next round.",
      "The second step closes the loop."]


def test_two_paragraphs_in_one_layout_item_are_split_at_the_text_layer_paragraph_end():
    # SAT: 64 paragraph ends of the text layer fell inside one block (Docling items spanning two
    # paragraphs). The text layer shows the end: a short line closing a sentence, then a full
    # line opening one.
    doc = Doc(1, 1).add("text", 1, " ".join(P1 + P2), top=60, bottom=300)
    book = normalize_docling(META, [doc.json()], text_layer2(["\n".join(P1 + P2)]))
    paras = [b for b in book.blocks if b.type == "paragraph"]
    assert [p.text for p in paras] == [" ".join(P1), " ".join(P2)]
    assert [p.raw_text for p in paras] == [" ".join(P1), " ".join(P2)]
    assert all("split_at_paragraph_end" in p.normalization for p in paras)
    assert all([r.page_no for r in p.refs] == [1] and p.refs[0].bbox is not None for p in paras)


def test_a_short_line_that_does_not_end_a_sentence_is_not_a_paragraph_end():
    p1 = P1[:-1] + ["That is the first step of the"]
    p2 = ["method that structured techniques then use to test the strongest idea"] + P2[1:]
    doc = Doc(1, 1).add("text", 1, " ".join(p1 + p2), top=60, bottom=300)
    book = normalize_docling(META, [doc.json()], text_layer2(["\n".join(p1 + p2)]))
    assert [b.text for b in book.blocks] == [" ".join(p1 + p2)]


def _picture(doc, page, top, bottom, children=(), captions=()):
    ref = f"#/pictures/{len(doc.d['pictures'])}"
    doc.d["pictures"].append({
        "self_ref": ref, "parent": {"$ref": "#/body"}, "content_layer": "body", "label": "picture",
        "children": [{"$ref": c} for c in children], "captions": [{"$ref": c} for c in captions],
        "prov": [{"page_no": page, "bbox": {"l": 60, "t": H - top, "r": 360, "b": H - bottom,
                                            "coord_origin": "BOTTOMLEFT"}, "charspan": [0, 0]}]})
    doc.d["body"]["children"].append({"$ref": ref})
    return ref


def _child_text(doc, parent, label, page, text, top, bottom):
    ref = f"#/texts/{len(doc.d['texts'])}"
    doc.d["texts"].append({
        "self_ref": ref, "parent": {"$ref": parent}, "children": [], "content_layer": "body",
        "label": label, "orig": text, "text": text,
        "prov": [{"page_no": page, "bbox": {"l": 60, "t": H - top, "r": 360, "b": H - bottom,
                                            "coord_origin": "BOTTOMLEFT"}, "charspan": [0, len(text)]}]})
    return ref


def test_caption_of_a_picture_is_kept_as_a_caption_block():
    # SAT pp. 581, 620: 'Figure 10.1 Opportunities Incubator' was a caption child of the picture
    # and was dropped with the picture's other children
    doc = Doc(1, 1).add("text", 1, FILLER, top=20, bottom=60)
    cap = _child_text(doc, "#/pictures/0", "caption", 1, "Figure 10.1 Opportunities Incubator", 420, 430)
    inner = _child_text(doc, "#/pictures/0", "text", 1, "Client Perception", 120, 130)
    _picture(doc, 1, 100, 400, children=[cap, inner], captions=[cap])
    book = normalize_docling(META, [doc.json()], text_layer2([FILLER + "\nFigure 10.1 Opportunities Incubator"]))
    assert [(b.type, b.text) for b in book.blocks] == [
        ("paragraph", FILLER.strip()), ("figure", "Client Perception"),
        ("caption", "Figure 10.1 Opportunities Incubator")]
    assert book.blocks[2].refs[0].bbox == (60.0, 420.0, 360.0, 430.0)


def test_a_numbered_figure_title_set_as_a_section_header_is_a_caption():
    # SAT: 36 'Figure 9.11.1 ...' captions came out as headings (and section titles)
    doc = (Doc(1, 1).add("section_header", 1, "9.11 The Method", top=20, bottom=30)
           .add("text", 1, FILLER, top=40, bottom=80)
           .add("section_header", 1, "Figure 9.11.3B The INUS Condition", top=300, bottom=310)
           .add("section_header", 1, "Table 2 Indicators by Scenario", top=320, bottom=330)
           .add("text", 1, FILLER, top=340, bottom=380))
    book = normalize_docling(META, [doc.json()], text_layer2(["x"]))
    assert [(b.type, b.text) for b in book.blocks if b.type in ("heading", "caption")] == [
        ("heading", "9.11 The Method"), ("caption", "Figure 9.11.3B The INUS Condition"),
        ("caption", "Table 2 Indicators by Scenario")]
    assert all(b.section_path == ["9.11 The Method"] for b in book.blocks[1:])


def test_bookmark_pointing_below_the_last_text_of_a_page_matches_the_heading_opening_the_next():
    # SAT pp. 550, 667: the bookmark's /XYZ destination lies under the page's last line; the
    # heading is printed at the top of the next page (and was inserted on the wrong page)
    doc = (Doc(1, 2).add("text", 1, FILLER, top=40, bottom=140)
           .add("section_header", 2, "11.4 ROLE OF STRUCTURED TECHNIQUES IN 2030", top=40, bottom=60)
           .add("text", 2, FILLER, top=80, bottom=160))
    outline = [bookmark("11.4 Role of Structured Techniques in 2030", 0, 1, top=H - 300)]
    book = normalize_docling(META, [doc.json()], text_layer2(["x", "y"], outline=outline))
    heads = [b for b in book.blocks if b.type == "heading"]
    assert [(h.text, [r.page_no for r in h.refs], h.raw_text) for h in heads] == [
        ("11.4 Role of Structured Techniques in 2030", [2], "11.4 ROLE OF STRUCTURED TECHNIQUES IN 2030")]
    assert book.outline[0].block_id == heads[0].block_id
    assert "heading_from_outline" not in heads[0].normalization


def test_bookmark_inside_the_text_of_its_page_does_not_look_at_the_next_page():
    doc = (Doc(1, 2).add("text", 1, FILLER, top=40, bottom=400)
           .add("section_header", 2, "Role of Techniques", top=40, bottom=60)
           .add("text", 2, FILLER, top=80, bottom=160))
    outline = [bookmark("Role of Techniques", 0, 1, top=H - 300)]
    book = normalize_docling(META, [doc.json()], text_layer2(["x", "y"], outline=outline))
    synthetic = [b for b in book.blocks if "heading_from_outline" in b.normalization]
    assert [(b.text, b.refs[0].page_no) for b in synthetic] == [("Role of Techniques", 1)]


def test_ocr_text_read_from_a_raster_figure_is_one_figure_block_not_prose():
    # SAT pp. 72, 433: Docling OCR'd a raster figure into headings and paragraphs ('Pathmays to
    # Unlikety Outcome', 'ae e a sad eat'); the text layer prints none of those words
    pdfium = ("Selected indicators are listed here for the analyst to watch closely.\n"
              "Figure 8.2.5 Conflict in the Arctic\nSource: Pherson Associates, 2019.")
    doc = (Doc(1, 1)
           .add("section_header", 1, "Pathmays to Unlikety Outcome", top=60, bottom=70)
           .add("text", 1, "ae e a sad eat asdl ses eet t s ae circumstances caused by sea ice melting.",
                top=80, bottom=120)
           .add("text", 1, "Selected indicators are listed here for the analyst to watch closely.",
                top=130, bottom=150)
           .add("section_header", 1, "Figure 8.2.5 Conflict in the Arctic", top=420, bottom=430)
           .add("text", 1, "Source: Pherson Associates, 2019.", top=440, bottom=450))
    tl = text_layer2([pdfium], image_boxes={1: [[60.0, 50.0, 360.0, 400.0]]})
    book = normalize_docling(META, [doc.json()], tl)
    assert [(b.type, b.text[:24]) for b in book.blocks] == [
        ("figure", "Pathmays to Unlikety Out"),
        ("paragraph", "Selected indicators are "),  # printed in the text layer: real text over the image
        ("caption", "Figure 8.2.5 Conflict in"), ("paragraph", "Source: Pherson Associat")]
    fig = book.blocks[0]
    assert "image_ocr_text" in fig.normalization and len(fig.refs) == 1
    assert fig.raw_text == ("Pathmays to Unlikety Outcome\n"
                            "ae e a sad eat asdl ses eet t s ae circumstances caused by sea ice melting.")
    assert not any(b.quality_flags for b in book.blocks)


def test_page_text_and_figure_ocr_in_one_layout_item_are_separated():
    # SAT p. 556: one Docling item held the page's first lines (box 1) and the OCR text of the
    # raster figure under them (box 2, same page); the figure words were counted as page prose
    top = "key decision makers interested in the topic. A good set of indicators helps."
    # the second box also covers the page's next text line, above the figure: it stays text
    line = "It can also alert you to developments in time for decision makers."
    ocr = "Short circuit Flammable material Oxygen Fire Spark Wiring cluster Heat Fuel Ignition"
    doc = Doc(1, 1).add("text", 1, top + " " + line + " " + ocr, top=20, bottom=40)
    doc.d["texts"][-1]["prov"] = [
        {"page_no": 1, "bbox": {"l": 60, "t": H - 20, "r": 360, "b": H - 40, "coord_origin": "BOTTOMLEFT"},
         "charspan": [0, len(top)]},
        {"page_no": 1, "bbox": {"l": 60, "t": H - 100, "r": 300, "b": H - 300, "coord_origin": "BOTTOMLEFT"},
         "charspan": [len(top) + 1, len(top) + 2 + len(line) + len(ocr)]}]
    tl = text_layer2([top + "\n" + line + "\nFigure 9.11.3B The INUS Condition"],
                     image_boxes={1: [[60.0, 90.0, 320.0, 310.0]]})
    book = normalize_docling(META, [doc.json()], tl)
    assert [(b.type, b.text) for b in book.blocks] == [("paragraph", top + " " + line), ("figure", ocr)]
    assert [r.bbox for r in book.blocks[0].refs] == [(60.0, 20.0, 360.0, 40.0), (60.0, 100.0, 300.0, 300.0)]


def test_ocr_text_of_a_scanned_page_stays_text():
    # a page without a text layer is OCR'd as a whole: its text is the page, not a figure
    doc = Doc(1, 1).add("text", 1, FILLER, top=80, bottom=200)
    tl = text_layer2([""], image_boxes={1: [[0.0, 0.0, 400.0, 600.0]]})
    book = normalize_docling(META, [doc.json()], tl)
    assert [b.type for b in book.blocks] == ["paragraph"]


def test_endnote_runs_restart_after_a_long_run():
    # SAT: notes 1..11 of chapter 1, then chapter 2's notes 1, 2, 3 each opened a run of their own
    # (the restart rule compared with the old run's highest label), so no chapter-2 marker linked
    doc = (Doc(1, 4)
           .add("section_header", 1, "Chapter One", top=20, bottom=30)
           .add("text", 1, " ".join(f"Claim {w}.{n}" for n, w in enumerate(
               ["one", "two", "three", "four", "five", "six"], start=1)) + " " + FILLER, top=40)
           .add("section_header", 2, "NOTES", top=20, bottom=30))
    for n in range(1, 7):
        doc.add("list_item", 2, f"Doe, Book One, {n}.", top=30 + 20 * n, bottom=40 + 20 * n, marker=f"{n}.")
    doc.add("section_header", 3, "Chapter Two", top=20, bottom=30)
    doc.add("text", 3, "First claim.1 Second claim.2 Third claim.3 " + FILLER, top=40)
    doc.add("section_header", 4, "NOTES", top=20, bottom=30)
    for n in range(1, 4):
        doc.add("list_item", 4, f"Roe, Book Two, {n}.", top=30 + 20 * n, bottom=40 + 20 * n, marker=f"{n}.")
    outline = [bookmark("Chapter One", 0, 1), bookmark("Chapter Two", 0, 3)]
    book = normalize_docling(META, [doc.json()], text_layer2(["a", "b", "c", "d"], outline=outline))
    by_note = {n.note_id: n for n in book.notes}
    text_of = {b.block_id: b.text for b in book.blocks}
    ch2 = next(b for b in book.blocks if b.text.startswith("First claim"))
    assert [text_of[by_note[r.note_id].block_ids[0]] for r in ch2.note_refs] == [
        "Roe, Book Two, 1.", "Roe, Book Two, 2.", "Roe, Book Two, 3."]


def test_notes_run_on_inside_the_previous_note_are_split_back_from_the_next_label():
    # SAT p. 119: '... &type=pdf 2. Hackman and Woolley, Teams, 8. 3. Ibid.' stayed inside note 1
    # (no punctuation before '2.'), so notes 2 and 3 were lost; the next item is note 4
    doc = (Doc(1, 1).add("section_header", 1, "Notes", top=20, bottom=30)
           .add("list_item", 1, "Hackman, Teams, Report 5, http://example.org/download?doc=1&type=pdf "
                                "2. Hackman and Woolley, Teams, 8. 3. Ibid.", top=40, bottom=80, marker="1.")
           .add("list_item", 1, "Pherson, Essence of Collaboration.", top=90, bottom=100, marker="4."))
    book = normalize_docling(META, [doc.json()], text_layer2(["a"], outline=[bookmark("Notes", 0, 1)]))
    assert [(b.note_label, b.text) for b in book.blocks if b.type == "endnote"] == [
        ("1", "Hackman, Teams, Report 5, http://example.org/download?doc=1&type=pdf"),
        ("2", "Hackman and Woolley, Teams, 8."), ("3", "Ibid."), ("4", "Pherson, Essence of Collaboration.")]


def test_note_text_of_a_table_is_a_caption_not_a_footnote():
    # SAT p. 594: the legend under a table ('Actors: A list of ...') was a footnote without label
    doc = Doc(1, 1).add("text", 1, FILLER, top=20, bottom=60)
    note = _child_text(doc, "#/tables/0", "footnote", 1, "Actors: A list of the key individuals.", 300, 310)
    doc.d["tables"].append({
        "self_ref": "#/tables/0", "parent": {"$ref": "#/body"}, "content_layer": "body", "label": "table",
        "children": [{"$ref": note}], "captions": [],
        "data": {"grid": [[{"text": "ACTOR"}, {"text": "IMPACT"}], [{"text": "Me"}, {"text": ""}]]},
        "prov": [{"page_no": 1, "bbox": {"l": 60, "t": H - 100, "r": 360, "b": H - 290,
                                         "coord_origin": "BOTTOMLEFT"}, "charspan": [0, 0]}]})
    doc.d["body"]["children"].append({"$ref": "#/tables/0"})
    book = normalize_docling(META, [doc.json()], text_layer2(["a"]))
    assert [(b.type, b.text[:12]) for b in book.blocks] == [
        ("paragraph", FILLER[:12]), ("table", "| ACTOR | IM"), ("caption", "Actors: A li")]
    assert not book.notes


def test_abbreviations_list_inside_the_notes_section_is_not_an_endnote():
    # Gramsci p. 333: the abbreviations table runs on to the first notes page; its last row
    # ('SSG Antonio Gramsci, ...') became an endnote without a label
    doc = (Doc(1, 2).add("section_header", 1, "Notes", top=20, bottom=30)
           .add("section_header", 1, "abbreviations", top=40, bottom=50)
           .add("text", 1, "SPN Antonio Gramsci, Selections from the Prison Notebooks (London, 1971).",
                top=60, bottom=80)
           .add("text", 2, "SSG Antonio Gramsci, Subaltern Social Groups (New York, 2021).", top=20, bottom=40)
           .add("section_header", 2, "introduction", top=50, bottom=60)
           .add("list_item", 2, "Gramsci to his sister-in-law, 19 February 1927.", top=70, bottom=80, marker="1.")
           .add("list_item", 2, "Boggs, The Two Revolutions, 261.", top=90, bottom=100, marker="2."))
    book = normalize_docling(META, [doc.json()], text_layer2(["a", "b"], outline=[bookmark("Notes", 0, 1)]))
    assert [(b.type, b.note_label, b.text[:12]) for b in book.blocks if b.type != "heading"] == [
        ("paragraph", None, "SPN Antonio "), ("paragraph", None, "SSG Antonio "),
        ("endnote", "1", "Gramsci to h"), ("endnote", "2", "Boggs, The T")]
    assert not any("note_without_label" in b.quality_flags for b in book.blocks)


def test_letter_spaced_heading_takes_its_word_boundaries_from_the_text_layer():
    # Gramsci pp. 31, 279, 294: pypdf prints small caps one letter apart and words two spaces
    # apart ('c r i t i c a l  a p p r o a c h e s'); the vocabulary repair left 'critic al'
    pypdf = ["The approaches differ.\nc r i t i c a l  a p p r o a c h e s\nw (h) i t h e r  t h e  s t a t e ?"]
    doc = (Doc(1, 1).add("text", 1, "The approaches differ. " + FILLER, top=20, bottom=60)
           .add("section_header", 1, "cr i t i c al approaches", top=80, bottom=90)
           .add("text", 1, FILLER, top=100, bottom=140)
           .add("section_header", 1, "w(h) i ther the s tat e?", top=160, bottom=170)
           .add("text", 1, FILLER, top=180, bottom=220))
    book = normalize_docling(META, [doc.json()], text_layer2(["x"], pypdf=pypdf))
    heads = [b for b in book.blocks if b.type == "heading"]
    assert [h.text for h in heads] == ["critical approaches", "w(h)ither the state?"]
    assert all("ocr_letter_spacing" in h.normalization for h in heads)
    assert not any("letter_spacing_residue" in b.quality_flags for b in book.blocks)
