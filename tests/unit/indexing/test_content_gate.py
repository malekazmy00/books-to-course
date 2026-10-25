"""T-IDX-05: the content gate keeps only body text in the teaching index (audit 03 C2)."""
from b2c_contracts import BlockV2, Note, PageInfo, ParsedBookV2
from b2c_contracts.models import Block, PageRef, ParsedBook
from b2c_indexing.content_gate import gate_blocks, text_flags

B = "bk"


def v2block(seq, text, type="paragraph", zone="body", **kw):
    if type == "heading":
        kw.setdefault("level", 1)
    return BlockV2(block_id=f"{B}:b{seq:05d}", type=type, zone=zone, text=text, raw_text=text,
                   refs=[PageRef(book_id=B, page_no=1)], section_path=kw.pop("section_path", ["Ch"]),
                   ocr=False, **kw)


def v2book(blocks, notes=()):
    return ParsedBookV2(book_id=B, title="T", role="spine", page_count=1, toc_source="pdf_outline",
                        pages=[PageInfo(page_no=1, status="content")], blocks=blocks, notes=list(notes),
                        parser={"name": "fixture", "version": "1"}, normalizer={"name": "n", "version": "1"})


PROSE = ("The hegemony of a class rests on consent as much as on force, and the state organises "
         "that consent through schools, churches and the press.")


def test_v2_zones_and_types_decide_the_evidence_class():
    blocks = [
        v2block(1, "Contents", type="toc_entry", zone="front_matter"),
        v2block(2, "Preface text that thanks many colleagues for their help.", zone="front_matter"),
        v2block(3, "Chapter One", type="heading"),
        v2block(4, PROSE, note_refs=[{"label": "1", "offset": 10, "note_id": f"{B}:n0001"}]),
        v2block(5, "12", type="page_number", zone="furniture"),
        v2block(6, "1. Prison Notebooks, Q13, §17.", type="endnote", zone="back_matter", note_label="1"),
        v2block(7, "Smith, J. 1990. Hegemony. London: Verso.", type="bibliography_entry", zone="back_matter"),
        v2block(8, "hegemony, 12, 40-42, 77", type="index_entry", zone="back_matter"),
        v2block(9, "Figure label text read by OCR", type="figure"),
        v2block(10, "   ", zone="body"),
    ]
    notes = [Note(note_id=f"{B}:n0001", kind="endnote", label="1", block_ids=[f"{B}:b00006"],
                  cited_by=[f"{B}:b00004"])]
    verdicts = {g.block_id.rsplit("b", 1)[1]: g for g in gate_blocks(v2book(blocks, notes))}
    assert verdicts["00001"].evidence == "excluded" and verdicts["00001"].reason == "toc"
    assert verdicts["00002"].reason == "front_matter"
    assert verdicts["00003"].evidence == "body" and verdicts["00003"].heading
    assert verdicts["00004"].evidence == "body" and not verdicts["00004"].quarantined
    assert verdicts["00005"].reason == "furniture"
    assert verdicts["00006"].evidence == "note" and verdicts["00006"].cited_by == (f"{B}:b00004",)
    assert verdicts["00007"].reason == "bibliography"
    assert verdicts["00008"].reason == "index"
    assert verdicts["00009"].reason == "figure_text"
    assert verdicts["00010"].reason == "empty"


def test_corrupted_text_is_flagged():
    assert "control_chars" in text_flags("\x14\x1e\x1e-\x01 )$#\x01$\x18\x14 abc def ghi")
    assert "letter_spacing" in text_flags(
        "af t er the spr i ng the art i cul at i on of s truggl es in the war of pos i t i on")
    assert "letter_spacing" in text_flags("N O T g e t t i n g a 1 o n t h e f i r s t t h r o w")
    assert "low_alpha" in text_flags("142-43, 174, 183, 215, 241, 277-78, 280; 96; 12, 14, 15, 99")
    assert text_flags(PROSE) == ()


def test_glued_words_need_book_vocabulary():
    vocab_text = ("the analyst should consider strong positive evidence and strong negative evidence "
                  "of the case; the analyst weighs strong positive and strong negative signals ") * 3
    flags = text_flags("Mark Strongpositive and Strongnegative ofthe evidence theanalyst found.",
                       vocab_texts=[vocab_text])
    assert "glued_words" in flags
    assert "glued_words" not in text_flags(PROSE, vocab_texts=[vocab_text, PROSE])


def test_quarantine_only_for_corrupt_not_for_suspect_text():
    blocks = [v2block(1, PROSE), v2block(2, "af t er the spr i ng the art i cul at i on of s truggl es "
                                            "in the war of pos i t i on and the con j unc t ure")]
    gated = gate_blocks(v2book(blocks))
    assert not gated[0].quarantined
    assert gated[1].quarantined and gated[1].evidence == "body" and "letter_spacing" in gated[1].flags


def test_v1_book_degrades_to_type_and_text_heuristics():
    def b(i, text, type="paragraph", path=("Ch",)):
        return Block(block_id=f"{B}:1:{i}", type=type, text=text, refs=[PageRef(book_id=B, page_no=1)],
                     section_path=list(path), ocr=False)
    book = ParsedBook(book_id=B, title="T", role="spine", page_count=1, toc_source="headings",
                      parser={"name": "fixture", "version": "1"}, blocks=[
                          b(0, "RUNNING HEAD", type="page_header"),
                          b(1, PROSE),
                          b(2, "1. Prison Notebooks, Q13, §17.", type="footnote"),
                          b(3, "hegemony, 12, 40-42, 77\nhistoricism, 3, 19-20\nideology, 5, 8, 120",
                            type="list_item"),
                          b(4, "1 Introduction 1\n2 The War of Position 37\n3 Hegemony 81", type="list_item"),
                          b(5, "Anything listed under an index heading", type="paragraph", path=("Index",)),
                      ])
    verdicts = gate_blocks(book)
    assert [v.evidence for v in verdicts] == ["excluded", "body", "note", "excluded", "excluded", "excluded"]
    assert [v.reason for v in verdicts] == ["furniture", None, None, "index", "toc", "index"]
    assert all(v.zone is None for v in verdicts)
