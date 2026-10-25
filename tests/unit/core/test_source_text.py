"""Shared English source-text rules (R3: one implementation for S2 and translation units)."""
from b2c_core import source_text as st
from b2c_translation import units


def test_translation_units_use_the_shared_rules_not_a_copy():
    assert units._repair_letter_spacing is st.repair_letter_spacing
    assert units._normalize_chars is st.normalize_chars
    assert units._marker_candidates is st.marker_candidates
    assert units._Vocab is st.Vocab


def test_ligatures_and_letter_spacing_are_repaired_with_the_book_vocabulary():
    vocab = st.Vocab(["the articulation of struggles and the analysis of struggles"] * 3)
    text, changed = st.normalize_chars("eﬃcient ﬁnal")
    assert (text, changed) == ("efficient final", True)
    assert st.repair_letter_spacing("the art i cul at i on of s truggl es", vocab) == "the articulation of struggles"
    # words the book never uses un-spaced are left alone
    assert st.repair_letter_spacing("in to some times", vocab) == "in to some times"


def test_note_marker_sequence_accepts_running_numbers_only():
    seq = st.MarkerSequence()
    assert seq.accept(1, True) and seq.accept(2, True)
    assert not seq.accept(1999, True)
