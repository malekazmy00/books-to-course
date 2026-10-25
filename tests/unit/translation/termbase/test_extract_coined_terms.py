"""Coined technique and concept names are terms, not people (D-047). Synthetic sentences.

In book 1, Groupthink, Satisficing, Circleboarding™ and InconsistenciesFinder™ reached the
termbase as ``person`` through the "unresolved single proper noun" fallback (signal
``kind_uncertain``), so they were checked as names. The fallback now keeps three shapes as terms:
a trademark sign, a rare -ing process noun, and a closed compound of two common English words.
"""
from __future__ import annotations

from b2c_translation.termbase import EXTRACTOR_VERSION, extract_candidates
from b2c_translation.termbase.extract import coined_term, general_english_frequency

from .conftest import make_units

ROWS = [
    ("A", "prose", "Teams that ignore dissent drift into Groupthink before anyone notices."),
    ("B", "prose", "Leaders who speak first make Groupthink worse, as Brellin and Fleming observed."),
    ("C", "prose", "The cure for Groupthink is a structured devil's advocate, Brellin wrote."),
    ("A", "prose", "Choosing the first good-enough answer is called Satisficing in this field."),
    ("B", "prose", "Analysts fall back on Satisficing under time pressure; Fleming disagreed."),
    ("C", "prose", "A checklist reduces Satisficing and supports Mapboarding™ sessions with Brellin."),
    ("A", "prose", "The team used Mapboarding™ to collect every question about the case."),
    ("B", "prose", "After Mapboarding™ the analysts ranked the questions, as Fleming advised."),
]


def _by_source(cands):
    return {c.source: c for c in cands}


def test_coined_names_are_terms():
    got = _by_source(extract_candidates(make_units(ROWS)))
    for source in ("Groupthink", "Satisficing", "Mapboarding™"):
        assert got[source].kind == "term", (source, got[source])
        assert "coined" in got[source].signals


def test_unresolved_surnames_stay_people():
    got = _by_source(extract_candidates(make_units(ROWS)))
    assert got["Brellin"].kind == "person"
    assert got["Fleming"].kind == "person"


def test_coined_term_rule():
    ref = general_english_frequency
    for word in ("Groupthink", "Satisficing", "Circleboarding™", "InconsistenciesFinder™", "PlayStation"):
        assert coined_term(word, ref), word
    for word in ("Fleming", "Manning", "Nosenko", "Deborah", "Pennsylvania", "Delphi", "Havana", "Venn",
                 "Bismarck", "Garibaldi", "Newman", "Goldberg", "Excel", "Rojava"):
        assert not coined_term(word, ref), word


def test_extractor_version_records_the_rule():
    assert EXTRACTOR_VERSION == "termbase-extract-v3.1"
