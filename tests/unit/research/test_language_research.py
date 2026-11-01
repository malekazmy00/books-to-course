"""Language research in Arabic, after translation (D-034, learning-companion §11b): what the termbase
could not settle becomes knowledge — an honest status per term and a "read in Arabic" list.

A term is established when an independent source agreed with its Arabic equivalent (locked, or an
Arabic Wikipedia title from D-034). For the others, the Arabic Wikipedia page with exactly the chosen
Arabic title is checked to be the SAME concept (its Wikidata item names the English term); otherwise the
learner is told plainly that no settled Arabic equivalent is known, with the original to search for.
"""
from __future__ import annotations

from b2c_research.language import LanguageTerm, research_language

PAGES = {"هيمنة": {"title": "هيمنة", "wikibase_item": "Q182034"},
         "تمفصل": None,                                                    # no such article
         "تفكير جماعي": {"title": "تفكير جماعي", "wikibase_item": "Q190011"},
         "مرونة": {"title": "مرونة (فيزياء)", "wikibase_item": "Q62932"}}   # another concept
ITEMS = {"Q182034": ["hegemony"], "Q190011": ["groupthink"], "Q62932": ["elasticity"]}


class Fake:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def page(self, title):
        self.calls += 1
        if self.fail:
            raise OSError("down")
        return PAGES.get(title)

    def english(self, qid):
        return ITEMS.get(qid, [])


def term(source, target, *, established=False, kind="term", frequency=5, url=None):
    return LanguageTerm(source=source, target=target, kind=kind, frequency=frequency, established=established,
                        arabic_wikipedia=url)


def test_established_terms_need_no_lookup_and_give_their_reading():
    f = Fake()
    out = research_language([term("hegemony", "هيمنة", established=True, url="https://ar.wikipedia.org/wiki/هيمنة")],
                            page=f.page, english=f.english)
    [t] = out["terms"]
    assert t["status"] == "established" and f.calls == 0
    assert out["read_in_arabic"] == [{"title": "هيمنة", "url": "https://ar.wikipedia.org/wiki/هيمنة",
                                      "terms": ["hegemony"]}]


def test_an_unsettled_term_found_as_the_same_concept_in_arabic_wikipedia():
    out = research_language([term("groupthink", "تفكير جماعي")], page=Fake().page, english=Fake().english)
    [t] = out["terms"]
    assert t["status"] == "found_in_arabic_wikipedia"
    assert t["arabic_wikipedia"] == "https://ar.wikipedia.org/wiki/تفكير_جماعي"


def test_a_page_of_another_concept_is_not_taken():
    out = research_language([term("resilience", "مرونة")], page=Fake().page, english=Fake().english)
    assert out["terms"][0]["status"] == "not_established"


def test_not_established_is_said_plainly_with_the_original_to_search():
    out = research_language([term("articulation", "تمفصل")], page=Fake().page, english=Fake().english)
    [t] = out["terms"]
    assert t["status"] == "not_established" and t["arabic_wikipedia"] is None
    assert "لا نعرف مقابلًا عربيًا مستقرًا" in t["note_ar"] and "«تمفصل»" in t["note_ar"]
    assert t["search_original"] == "articulation"
    assert out["metrics"] == {"terms": 1, "established": 0, "found_in_arabic_wikipedia": 0, "not_established": 1,
                              "lookup_failed": 0}


def test_a_failed_lookup_is_recorded_not_raised():
    out = research_language([term("groupthink", "تفكير جماعي")], page=Fake(fail=True).page, english=Fake().english)
    assert out["terms"][0]["status"] == "not_established" and out["metrics"]["lookup_failed"] == 1


def test_the_most_frequent_unsettled_terms_are_researched_first_within_the_cap():
    f = Fake()
    terms = [term(f"t{i}", "تمفصل", frequency=i) for i in range(10)]
    out = research_language(terms, page=f.page, english=f.english, max_lookups=3)
    assert f.calls == 3 and [t["source"] for t in out["terms"][:3]] == ["t9", "t8", "t7"]
    assert all(t["status"] == "not_established" for t in out["terms"])
