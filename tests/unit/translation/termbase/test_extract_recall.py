"""Recall improvements of the termbase extractor (invented words and names only).

Keyness of single words against a general-English reference, the back-of-book index as
evidence, possessive / question-mark technique names, and person typing from notes,
bibliography and eponymous adjectives.
"""
from __future__ import annotations

from b2c_translation.termbase import extract_candidates
from b2c_translation.termbase.extract import general_english_frequency

from .conftest import make_units

# Fake general-English reference (proportion of tokens), so the tests do not depend on
# the wordfreq data; invented words are absent (0.0) as they would be in wordfreq.
_REFERENCE = {
    "water": 2e-4, "river": 6e-5, "italian": 8e-5, "the": 5e-2, "of": 3e-2, "on": 7e-3,
    "each": 1e-3, "rain": 5e-5, "devil": 2e-5, "quietly": 1e-5,
}


def _ref(word: str) -> float:
    return _REFERENCE.get(word.casefold(), 0.0)


def _by_source(cands):
    return {c.source: c for c in cands}


_ROWS = [
    ("1 Ground", "prose", "The glimmerance of the river grows when the water is still and the rain is light."),
    ("1 Ground", "prose", "Writers measure the glimmerance of a pond by the water it holds and the rain it gets."),
    ("2 Craft", "prose", "A strong glimmerance needs clean water; murky water and rain kill the glimmerance."),
    ("2 Craft", "prose", "Ofeach pond they took water, and ofeach river they took rain, for the water ledger."),
    ("3 Field", "prose", "Their glimmerance faded as water rose; the rain fell on ofeach roof and the river swelled."),
    ("3 Field", "prose", "She snorkels in the river; he snorkels in the water; they snorkels in the rain, as water allows."),
    ("3 Field", "prose", "Some readers call it murmuration."),
    ("3 Field", "prose", "The murmuration of the flock is loud."),
    ("Index", "index", "murmuration, 7, 9"),
]


def test_single_lowercase_domain_word_found_by_keyness():
    got = _by_source(extract_candidates(make_units(_ROWS), general_frequency=_ref))
    assert "glimmerance" in got
    g = got["glimmerance"]
    assert g.kind == "term" and "keyness" in g.signals and g.frequency >= 4
    # common general-English words are not domain-specific however often they occur
    assert "water" not in got and "rain" not in got and "river" not in got


def test_keyness_rejects_glued_words_and_verbs():
    sources = {c.source.casefold() for c in extract_candidates(make_units(_ROWS), general_frequency=_ref)}
    assert "ofeach" not in sources      # OCR glue of two common words ("of" + "each")
    assert "snorkels" not in sources    # never in a nominal context (no determiner / "of")


def test_index_headword_single_word_is_kept_with_low_frequency():
    got = _by_source(extract_candidates(make_units(_ROWS), general_frequency=_ref))
    assert "murmuration" in got and "index" in got["murmuration"].signals


def test_default_reference_is_wordfreq_english():
    assert general_english_frequency("the") > 1e-2
    assert general_english_frequency("glimmerance") == 0.0


def test_possessive_inside_a_technique_name():
    rows = [
        ("1 A", "heading", "3.4 Hermit’s Gambit"),
        ("1 A", "prose", "Teams that use the Hermit’s Gambit argue against their own favourite answer."),
        ("2 B", "prose", "In a Hermit’s Gambit session one analyst defends the weakest option."),
        ("3 C", "prose", "Okafor wrote that the Hermit’s Gambit works best in small teams."),
        ("3 C", "prose", "Later Okafor’s Ledger listed every case, and Okafor revised it twice."),
        ("4 D", "prose", "Critics of Okafor’s Ledger say Okafor counted too little."),
    ]
    got = _by_source(extract_candidates(make_units(rows), general_frequency=_ref))
    assert got["Hermit’s Gambit"].kind == "term"
    assert got["Hermit’s Gambit"].frequency == 4
    # the possessor also occurs on its own: a person's possession, not one fixed name
    assert "Okafor’s Ledger" not in got
    assert got["Okafor"].kind == "person"


def test_question_mark_inside_a_technique_name():
    rows = [
        ("8 Reframing", "heading", "8.2.4 Why Not? Analysis"),
        ("8 Reframing", "prose", "The Why Not? Analysis starts from an outcome everyone rejects."),
        ("9 Use", "prose", "Analysts often pair it with the Why Not? Analysis when time is short."),
        ("9 Use", "prose", "Why not? Nobody asked."),
    ]
    got = _by_source(extract_candidates(make_units(rows), general_frequency=_ref))
    assert "Why Not? Analysis" in got
    assert got["Why Not? Analysis"].kind == "term"
    assert "Analysis" not in got


def test_person_typing_from_notes_and_reporting_verbs():
    rows = [
        ("1 A", "prose", "The scheme owes much to Tomas Brell and his circle."),
        ("2 B", "prose", "Later work by Tomas Brell extended the Brellian reading of archives."),
        ("2 B", "prose", "A Brellian reading treats every archive as a quarrel."),
        ("3 C", "prose", "Ilse Marten found the opposite, and Marten argued it at length."),
        ("3 C", "prose", "Students of Ilse Marten kept the habit."),
        ("Notes", "reference", "4. Tomas Brell, The Grey Archive (London: Pellow Press, 2001), 12."),
    ]
    got = _by_source(extract_candidates(make_units(rows), general_frequency=_ref))
    assert got["Tomas Brell"].kind == "person"
    assert got["Ilse Marten"].kind == "person"
    assert got["Brellian"].kind == "term" and "eponym" in got["Brellian"].signals


def test_common_nationality_adjective_is_not_a_term():
    rows = [
        ("1 A", "prose", "Many Italian towns kept the custom, and later the Italian state taxed it."),
        ("2 B", "prose", "An Italian decree of the time banned it in the south."),
        ("3 C", "prose", "Her Italian readers saw the Varnician school as foreign."),
        ("3 C", "prose", "In the end the Varnician view won."),
    ]
    got = _by_source(extract_candidates(make_units(rows), general_frequency=_ref))
    assert "Italian" not in got
    assert "Varnician" in got   # rare -ian adjective: still a candidate term
