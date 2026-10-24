"""Candidate extraction from TranslationUnits (D-028 part E): pure and deterministic."""
from __future__ import annotations

from b2c_translation.termbase import TermCandidate, extract_candidates

from .conftest import make_units


def _by_source(cands):
    return {c.source: c for c in cands}


def test_extraction_is_deterministic_and_pure(units):
    first = extract_candidates(units)
    second = extract_candidates(list(units))
    assert first == second
    assert all(isinstance(c, TermCandidate) for c in first)


def test_capitalised_technique_names_are_terms_with_frequency_and_spread(units):
    got = _by_source(extract_candidates(units))
    sm = got["Signal Mapping"]
    assert sm.kind == "term"
    assert sm.frequency >= 4
    assert sm.sections >= 3
    assert len(sm.unit_ids) <= 5 and all(u.startswith("synthetic-tb:u") for u in sm.unit_ids)


def test_defined_term_keeps_definition_context(units):
    got = _by_source(extract_candidates(units))
    pm = got["Pre-Mortem Review"]
    assert pm.kind == "term"
    assert "defined" in pm.signals
    assert pm.definition_context.startswith("A Pre-Mortem Review is a method")


def test_lowercase_multiword_term_found_statistically(units):
    got = _by_source(extract_candidates(units))
    assert "confirmation drift" in got
    assert got["confirmation drift"].kind == "term"
    assert got["confirmation drift"].frequency >= 5


def test_index_headword_single_word_concept(units):
    got = _by_source(extract_candidates(units))
    assert "ledgerism" in got and "index" in got["ledgerism"].signals


def test_acronym_with_expansion(units):
    got = _by_source(extract_candidates(units))
    arh = got["ARH"]
    assert arh.kind == "acronym"
    assert arh.expansion == "Analysis of Rival Hypotheses"
    assert arh.frequency >= 4
    assert got["Analysis of Rival Hypotheses"].kind == "term"


def test_named_entities_are_typed(units):
    got = _by_source(extract_candidates(units))
    assert got["Lena Vasquez"].kind == "person"
    assert got["Marcus T. Hollander Jr."].kind == "person"
    assert got["Vasquez"].kind == "person"
    assert got["Hollander"].kind == "person"
    assert got["Northwind Institute for Policy Research"].kind == "organization"
    assert got["Vantor University"].kind == "organization"
    assert got["Kelburn Square"].kind == "place"
    assert got["Portugal"].kind == "place"
    assert got["The Quiet Ledger"].kind == "work"


def test_no_noise_candidates(units):
    sources = {c.source for c in extract_candidates(units)}
    # sentence-initial common words, honorifics alone, references-only material
    for junk in ("Analysts", "Dr", "Dr.", "Some", "Run", "In", "A Pre-Mortem Review", "Northwind Press",
                 "Rival Hypotheses Revisited"):
        assert junk not in sources
    assert not any(s.startswith(("The Analysis", "In the")) for s in sources)


def test_reference_and_index_units_do_not_count_as_occurrences(units):
    got = _by_source(extract_candidates(units))
    # "Vasquez" occurs in 4 prose units, plus one reference and one index unit (not counted)
    assert got["Vasquez"].frequency == 4


def test_all_caps_headings_do_not_produce_phrases():
    rows = [("A", "heading", "CHAPTER ONE THE WIDE FIELD"),
            ("A", "prose", "The wide field is quiet."), ("B", "prose", "The wide field is calm.")]
    sources = {c.source for c in extract_candidates(make_units(rows))}
    assert "CHAPTER ONE THE WIDE FIELD" not in sources
    assert not any(s.isupper() and " " in s for s in sources)


def test_kinds_are_restricted():
    allowed = {"term", "acronym", "person", "organization", "place", "work"}
    assert {c.kind for c in extract_candidates(make_units())} <= allowed


def test_accented_names_and_unresolved_proper_nouns():
    rows = [("A", "prose", "Later, Frédéric Okonjo wrote that the Brellin school had failed."),
            ("B", "prose", "Critics of Brellin rarely read Okonjo carefully."),
            ("C", "prose", "Some still follow Brellin today, as Okonjo noted.")]
    got = _by_source(extract_candidates(make_units(rows)))
    assert got["Frédéric Okonjo"].kind == "person"
    assert got["Brellin"].kind == "person" and "kind_uncertain" in got["Brellin"].signals
    assert got["Okonjo"].kind == "person"
