"""Known Arabic equivalents of the book's terms, collected before translation (D-034; learning-companion §11).

Wikidata is searched for each termbase candidate; an entity whose English label or alias is the term
gives its Arabic Wikipedia title (the established Arabic term, first) and its Arabic label. Several
such entities are told apart by the candidate's context; a tie gives nothing. A failed lookup never
blocks the termbase. Data captured from the live API (2026-11-01) for "hegemony".
"""
from __future__ import annotations

from b2c_translation.termbase import TermCandidate
from b2c_translation.termbase.known import WikidataKnownEquivalents, clean_title

SEARCH = {"hegemony": {"search": [
    {"id": "Q47000637", "label": "Hegemony", "description": "2017 album by Samael"},
    {"id": "Q182034", "label": "hegemony",
     "description": "political, economic or military predominance of one state over other states"},
    {"id": "Q105477418", "label": "Hegemony", "description": "video game series"}]}}
ENTITIES = {
    "Q47000637": {"labels": {"en": {"value": "Hegemony"}}, "descriptions": {"en": {"value": "2017 album by Samael"}}},
    "Q182034": {"labels": {"en": {"value": "hegemony"}, "ar": {"value": "سيطرة"}},
                "descriptions": {"en": {"value": "political, economic or military predominance of one state over other states"}},
                "sitelinks": {"arwiki": {"title": "هيمنة"}}},
    "Q105477418": {"labels": {"en": {"value": "Hegemony"}}, "descriptions": {"en": {"value": "video game series"}}},
}


class FakeAPI:
    def __init__(self, search=SEARCH, entities=ENTITIES, fail=False):
        self.search, self.entities, self.fail, self.calls = search, entities, fail, []

    def __call__(self, params):
        self.calls.append(dict(params))
        if self.fail:
            raise OSError("network down")
        if params["action"] == "wbsearchentities":
            return self.search.get(params["search"].casefold(), {"search": []})
        return {"entities": {q: self.entities[q] for q in params["ids"].split("|") if q in self.entities}}


def cand(term="hegemony", context="Hegemony is the predominance of one class over others by consent.", kind="term"):
    return TermCandidate(source=term, kind=kind, frequency=9, sections=3, definition_context=context)


def test_the_arabic_wikipedia_title_comes_first_then_the_label():
    proposals = WikidataKnownEquivalents(FakeAPI()).propose(cand())
    assert [p.target for p in proposals] == ["هيمنة", "سيطرة"]
    assert all(p.provenance == "wikidata" for p in proposals)
    assert proposals[0].reference == "https://www.wikidata.org/wiki/Q182034"
    assert "arwiki" in proposals[0].note and "label" in proposals[1].note


def test_only_entities_with_arabic_count_and_an_exact_label_is_required():
    api = FakeAPI(search={"war of position": {"search": [{"id": "Q141646129", "label": "War of Positions",
                                                           "description": "1983 film"}]}}, entities={})
    assert WikidataKnownEquivalents(api).propose(cand("war of position")) == []


def test_two_arabic_entities_are_told_apart_by_context_or_give_nothing():
    two = {"Q1": {"labels": {"en": {"value": "Mercury"}, "ar": {"value": "عطارد"}},
                  "descriptions": {"en": {"value": "smallest planet in the Solar System"}}},
           "Q2": {"labels": {"en": {"value": "mercury"}, "ar": {"value": "زئبق"}},
                  "descriptions": {"en": {"value": "chemical element with symbol Hg"}}}}
    search = {"mercury": {"search": [{"id": "Q1", "label": "Mercury"}, {"id": "Q2", "label": "mercury"}]}}
    api = lambda: FakeAPI(search=search, entities=two)  # noqa: E731 - one source per book (results are remembered per term)
    chem = cand("mercury", "Mercury is a chemical element, liquid at room temperature.")
    assert [p.target for p in WikidataKnownEquivalents(api()).propose(chem)] == ["زئبق"]
    assert WikidataKnownEquivalents(api()).propose(cand("mercury", "The mercury was noted.")) == []


def test_a_failed_lookup_never_blocks_the_termbase():
    assert WikidataKnownEquivalents(FakeAPI(fail=True)).propose(cand()) == []


def test_one_lookup_per_term_is_remembered():
    api = FakeAPI()
    src = WikidataKnownEquivalents(api)
    src.propose(cand())
    src.propose(cand())
    assert len(api.calls) == 2  # one search + one entities call, then remembered


def test_disambiguation_parentheses_are_removed_and_non_arabic_titles_refused():
    assert clean_title("هيمنة (سياسة)") == "هيمنة"
    assert clean_title("Hegemony") is None and clean_title("  ") is None
