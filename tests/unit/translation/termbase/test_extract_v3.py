"""Deterministic noise fixes of term extraction v3 (D-039). Synthetic text only.

1. Citation segments in notes (author, title, publisher, year, pages) do not produce candidates.
2. Months, weekdays, compass directions, nationalities and languages are never typed as people.
3. A capitalised fragment that mostly occurs inside a longer kept candidate is dropped.
"""
from __future__ import annotations

from b2c_translation.termbase import EXTRACTOR_VERSION, extract_candidates
from b2c_translation.termbase.extract import mask_citations

from .conftest import make_units


def _by_source(cands):
    return {c.source: c for c in cands}


def test_extractor_version_is_v3():
    assert EXTRACTOR_VERSION == "termbase-extract-v3"


# ------------------------------------------------------------------ 1. citations
def test_mask_citations_keeps_commentary_and_masks_citation_segments():
    text = ("Vacca, Alternative Modernities, xii, 37. Connotative links are 'evocative' ties in common sense. "
            "Esteve Morera, 'Gramsci and Democracy', Canadian Journal of Political Science 23, no. 1 (1990): 34-35.")
    masked = mask_citations(text)
    assert "Connotative links are 'evocative' ties in common sense." in masked
    for gone in ("Alternative Modernities", "Canadian Journal"):
        assert gone not in masked
    assert "Esteve Morera" in masked and "Vacca" in masked     # authors may be kept people
    assert "'Gramsci and Democracy'" in masked                 # quoted titles: the review kept some


def test_mask_citations_keeps_publishers_and_institutions():
    for text, kind in (("— Charles E. Wilson, University of Detroit Mercy", "list_item"),
                       ("— C. K. Lamont, University of Groningen; associate professor", "list_item"),
                       ("republished in The Community Model, Counterdrug Coordinating Group, 2003.", "caption")):
        assert mask_citations(text, kind) == text


def test_mask_citations_handles_publication_parentheses_in_prose():
    text = "See Randolph Pherson, Handbook of Analytic Tools (Tysons, VA: Pherson Associates, 2019), 12."
    masked = mask_citations(text)
    assert "VA" not in masked
    assert "Pherson Associates" in masked and "Handbook of Analytic Tools" in masked   # kept by the review
    plain = "The team met in Tysons to review the Signal Mapping grid (see chapter 3)."
    assert mask_citations(plain) == plain


def test_mask_citations_short_title_lists():
    text = "Major readers include Boggs, Two Revolutions; Femia, Political Thought; Salamini, Political Praxis. 138."
    masked = mask_citations(text, "footnote")
    for gone in ("Two Revolutions", "Political Thought", "Political Praxis"):
        assert gone not in masked


def test_mask_citations_leaves_ordinary_sentences_alone():
    for text in ("Signal Mapping is a technique that arranges weak signals on a shared grid.",
                 "In 1994, Vasquez argued that confirmation drift grows, as page 12 shows.",
                 "The numbers rose from 10 to 20, 30."):
        assert mask_citations(text) == text


_CITATION_ROWS = [
    ("1 Foundations", "prose", "Signal Mapping is a technique that arranges weak signals on a shared grid."),
    ("1 Foundations", "prose", "Analysts who skip Signal Mapping fall into confirmation drift."),
    ("2 Practice", "prose", "Signal Mapping helps when the evidence is thin and confirmation drift grows."),
    ("2 Practice", "prose", "Teams still argue about confirmation drift in every review."),
    ("Notes", "footnote", "Vacca, Alternative Modernities, xii, 37. Signal Mapping is discussed there too."),
    ("Notes", "footnote", "Vacca, Alternative Modernities, 41."),
    ("Notes", "footnote", "Esteve Morera, 'Gramsci and Democracy', Canadian Journal of Political Science 23, "
                          "no. 1 (1990): 34-35."),
    ("Notes", "footnote", "Morera, 'Gramsci and Democracy', Canadian Journal of Political Science 23, no. 2 "
                          "(1991): 12."),
    ("Notes", "list_item", "Randolph Pherson, Handbook of Analytic Tools (Tysons, VA: Pherson Associates, 2019), 12."),
    ("Notes", "list_item", "Randolph Pherson, Analytic Writing (Tysons, VA: Pherson Associates, 2020), 3."),
    ("Notes", "list_item", "Randolph Pherson, Analytic Briefing (Tysons, VA: Pherson Associates, 2021), 7."),
]


def test_citation_fragments_are_not_candidates_but_terms_in_notes_still_count():
    cands = _by_source(extract_candidates(make_units(_CITATION_ROWS)))
    for gone in ("Alternative Modernities", "Canadian Journal of Political Science", "VA", "Gramsci and Democracy"):
        assert gone not in cands, gone
    assert "Signal Mapping" in cands
    assert cands["Signal Mapping"].frequency == 4        # the footnote mention still counts


# ------------------------------------------------------------------ 2. not people
_NOT_PEOPLE_ROWS = [
    ("1 History", "prose", "The attacks of September 11 changed the agency, and in September the review began."),
    ("1 History", "prose", "By late September, analysts had translated the guide into Polish and Spanish."),
    ("2 Today", "prose", "The Polish edition and the Spanish edition followed in September and in October."),
    ("2 Today", "prose", "Readers in October praised the Polish team, and the Spanish team agreed."),
    ("2 Today", "prose", "Winds from the South and the North shaped the Arctic routes; Iraqi and Cuban officials met."),
    ("3 Later", "prose", "Officials from the South met Iraqi and Cuban delegates; the Arctic plan moved North."),
    ("3 Later", "prose", "Vasquez argued that the plan failed. Vasquez wrote again on Monday."),
    ("3 Later", "prose", "Later, Vasquez said the Monday meeting mattered; Vasquez insisted on it."),
]


def test_months_directions_nationalities_and_languages_are_not_people():
    cands = extract_candidates(make_units(_NOT_PEOPLE_ROWS))
    people = {c.source for c in cands if c.kind == "person"}
    for word in ("September", "October", "Monday", "Polish", "Spanish", "South", "North", "Iraqi", "Cuban",
                 "Arctic"):
        assert word not in people, word
    assert "Vasquez" in people


# ------------------------------------------------------------------ 3. nested fragments
_FRAGMENT_ROWS = [
    ("1 Tools", "prose", "The Program Evaluation and Review Technique (PERT) schedules dependent tasks."),
    ("1 Tools", "prose", "Teams use the Program Evaluation and Review Technique with Gantt charts."),
    ("2 Use", "prose", "A Program Evaluation and Review Technique chart shows the critical path."),
    ("2 Use", "prose", "Planners revisit the Program Evaluation and Review Technique each week."),
    ("2 Use", "prose", "Every Review Technique has limits, as planners know."),
    ("3 Cases", "prose", "High Impact/Low Probability Analysis is a technique that imagines unlikely events."),
    ("3 Cases", "prose", "Teams apply High Impact/Low Probability Analysis to rare shocks."),
    ("4 More", "prose", "Use High Impact/Low Probability Analysis when the stakes are high."),
    ("4 More", "prose", "A High Impact/Low Probability Analysis ends with indicators."),
]


def test_fragments_of_a_longer_kept_name_are_dropped():
    cands = _by_source(extract_candidates(make_units(_FRAGMENT_ROWS)))
    assert "Program Evaluation and Review Technique" in cands
    assert "High Impact/Low Probability Analysis" in cands       # a slash joins two capitalised names
    for fragment in ("Program Evaluation", "Review Technique", "High Impact", "Low Probability",
                     "Low Probability Analysis"):
        assert fragment not in cands, fragment
