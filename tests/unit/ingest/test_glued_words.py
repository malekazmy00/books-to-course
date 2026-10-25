"""Glued-word repair (R3, audit 01 §5.4.5): deterministic, from the book's own bigrams and the
pinned wordfreq lexicon, with proper nouns and book terms protected.

The book texts here are synthetic. The cases mirror what the PDF text layers of the two real
books actually contain (eval/reports/ingest-v2-gate.md): pypdf glues words at line-internal
kerning gaps ('ofeach', 'theanalyst'); Docling loses em dashes ('thatagainst').
"""
from __future__ import annotations

from b2c_ingest.glued import GlueSplitter, SPLITTER_VERSION

PROSE = [
    "The analyst checks each of the assumptions. The analyst writes the list of each item.",
    "For each variable the analyst records the value of each item and the role of the analyst.",
    "Inman argued in his report that the analyst should listen. Report by Inman, 1998.",
    "Starbursting is a technique. We used Starbursting and starbursting again.",
    "The nonpolar states and the polar states disagree. Counterfactual reasoning helps therein.",
]


def splitter(extra=(), protected=()):
    return GlueSplitter(PROSE + list(extra), protected=protected)


def test_version_is_recorded():
    assert SPLITTER_VERSION


def test_attested_bigram_splits_a_glued_token():
    s = splitter()
    text, splits = s.split("The value ofeach item is listed by theanalyst.")
    assert text == "The value of each item is listed by the analyst."
    assert [(x.token, x.parts, x.rule) for x in splits] == [
        ("ofeach", ("of", "each"), "attested"), ("theanalyst", ("the", "analyst"), "attested")]


def test_sentence_initial_capital_keeps_its_case():
    text, _ = splitter().split("Theanalyst agrees.")
    assert text == "The analyst agrees."


def test_unattested_glue_of_a_function_word_uses_the_lexicon_rule():
    # 'that against' never occurs in the book; 'that' is a closed-class word, 'against' is common
    text, splits = splitter().split("a struggle thatagainst all odds")
    assert text == "a struggle that against all odds"
    assert splits[0].rule == "lexicon"


def test_three_way_glue():
    text, _ = splitter().split("the role ofeachanalyst")
    assert text == "the role of each analyst"


def test_proper_nouns_and_book_terms_are_never_split():
    s = splitter(protected=["Circleboarding"])
    for word in ("Inman", "Starbursting", "starbursting", "Circleboarding", "circleboarding"):
        assert s.split(f"We read {word} today.")[0] == f"We read {word} today.", word


def test_real_words_and_prefixed_compounds_stay_whole():
    s = splitter()
    for word in ("therein", "another", "cannot", "within", "nonpolar", "counterfactual", "everyday",
                 "underexplored", "notwithstanding", "inasmuch", "insofar"):
        assert s.split(f"it is {word} here")[0] == f"it is {word} here", word


def test_urls_emails_acronyms_camel_case_and_short_tokens_stay_whole():
    s = splitter()
    for chunk in ("www.theanalyst.org", "https://example.org/ofeach", "theanalyst@example.org",
                  "THEANALYST", "OutsideIn", "McDonald", "ofit", "C:\\ofeach"):
        assert s.split(f"see {chunk} now")[0] == f"see {chunk} now", chunk


def test_a_token_the_book_prints_often_is_a_word_of_the_book():
    # three standalone uses of 'teamsheet' make it a book word, even though team + sheet are common
    s = splitter(extra=["The teamsheet is ready. A teamsheet helps. Each teamsheet lists roles."])
    assert s.split("the teamsheet now")[0] == "the teamsheet now"


def test_splitting_is_deterministic_and_only_moves_spaces():
    s = splitter()
    src = "Ofeach item theanalyst writes, thatagainst, the ofeachanalyst."
    out, splits = s.split(src)
    assert out.replace(" ", "") == src.replace(" ", "")
    assert GlueSplitter(PROSE).split(src) == (out, splits)


def test_the_independent_parser_restores_a_lost_dash():
    """Docling drops some em dashes ('thatagainst'); pdfium prints 'that—against' on the page."""
    from b2c_ingest.glued import reference_separators

    seps = reference_separators("allowing for the fact that—against any rigid application")
    # an em dash at a line end is exactly what the layout parser loses
    assert reference_separators("the fact that—\r\nagainst any")["thatagainst"] == (("that", "against"), ("—",))
    text, splits = splitter().split("allowing for the fact thatagainst any rigid application", separators=seps)
    assert text == "allowing for the fact that—against any rigid application"
    assert splits[0].parts == ("that", "against") and splits[0].separators == ("—",)
    # an em dash in the independent extraction is evidence on its own, even where the lexicon
    # rules decline (two content words, never printed apart in the book) or a name is involved
    s = splitter()
    assert s.decide("questioneven") is None
    seps = reference_separators("a fundamental question—even today—and Inman—whose view")
    text, splits = s.split("a fundamental questioneven today and Inmanwhose view", separators=seps)
    assert text == "a fundamental question—even today and Inman—whose view"
    assert [x.rule for x in splits] == ["reference", "reference"]
    # a line-end hyphen (pdfium U+FFFE) does not say whether the compound keeps it: not used
    assert reference_separators("the national￾popular mass") == {}


def test_s2_normalization_splits_glued_words_and_keeps_the_raw_text():
    import sys
    from pathlib import Path

    from b2c_ingest.normalize import NORMALIZER_VERSION, normalize_docling

    sys.path.insert(0, str(Path(__file__).parent))
    from test_normalize_v2 import META, Doc, text_layer

    prose = " ".join(PROSE)
    doc = Doc(1, 1).add("text", 1, prose, top=60, bottom=300).add("text", 1, "The value ofeach item.", top=320)
    book = normalize_docling(META, [doc.json()], text_layer([prose + "\nThe value of each item."]))
    block = next(b for b in book.blocks if b.raw_text == "The value ofeach item.")
    assert block.text == "The value of each item."
    assert "glued_words_split" in block.normalization
    assert NORMALIZER_VERSION == "2"
