"""A source string appears once among candidates (BookTermbase refuses duplicates)."""
from b2c_translation.termbase.extract import _merge_same_source
from b2c_translation.termbase.model import TermCandidate, source_key


def cand(source, kind, freq, **kw):
    return TermCandidate(source=source, kind=kind, frequency=freq, **kw)


def test_acronym_and_entity_with_the_same_surface_merge_into_one():
    merged = _merge_same_source([
        cand("USSR", "organization", 9, signals=("capitalized",)),
        cand("USSR", "acronym", 4, expansion="Union of Soviet Socialist Republics", signals=("acronym",)),
        cand("Comintern", "organization", 3),
    ])
    assert [c.source for c in merged].count("USSR") == 1
    ussr = next(c for c in merged if c.source == "USSR")
    assert ussr.kind == "acronym" and ussr.expansion == "Union of Soviet Socialist Republics"
    assert ussr.frequency == 9 and set(ussr.signals) == {"capitalized", "acronym"}


def test_case_variants_are_the_same_source():
    merged = _merge_same_source([cand("Hegemony", "term", 3), cand("hegemony", "term", 40)])
    assert len(merged) == 1 and merged[0].source == "hegemony"


def test_distinct_sources_are_untouched():
    items = [cand("war of position", "term", 5), cand("passive revolution", "term", 4)]
    assert _merge_same_source(items) == items
    assert len({source_key(c.source) for c in _merge_same_source(items)}) == 2
