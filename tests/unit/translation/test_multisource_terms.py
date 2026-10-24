from pathlib import Path

from b2c_contracts import GlossaryTerm
from b2c_translation.pipeline import (
    AgrovocIndex,
    WikidataConceptIndex,
    compile_multisource_termbase,
    term_injection_block,
)

ROOT = Path("/mnt/wsl/b2cdata/eval-datasets/termbases")
WIKIDATA = ROOT / "wikidata-lexemes/wikidata_concept_en_ar_candidates.jsonl"
AGROVOC = ROOT / "agrovoc/agrovoc_en_ar_candidates.jsonl"


def test_general_wikidata_layer_resolves_non_agriculture_probability():
    agrovoc = AgrovocIndex.from_jsonl(AGROVOC)
    wikidata = WikidataConceptIndex.from_jsonl(WIKIDATA)
    assert not agrovoc.lookup("probability")
    glossary = [GlossaryTerm(
        term_en="probability",
        definition_en="measure of the expectation that an event will occur or a statement is true",
        first_sid="book:p1:b1",
        lesson_id="stats-l1",
    )]
    result = compile_multisource_termbase(glossary, wikidata, agrovoc=agrovoc)
    assert len(result.entries) == 1
    assert result.entries[0].term_ar == "احتمال"
    assert result.audit[0].source == "wikidata"
    assert result.audit[0].concept_uri.endswith("/Q9492")
    assert result.audit[0].license == "CC0 1.0 (Wikidata structured data)"
    assert "probability => احتمال" in term_injection_block("Probability is measured by a number.", result.entries)


def test_agrovoc_remains_specialist_layer_and_context_selects_it():
    agrovoc = AgrovocIndex.from_jsonl(AGROVOC)
    wikidata = WikidataConceptIndex.from_jsonl(WIKIDATA)
    glossary = [GlossaryTerm(
        term_en="soil",
        definition_en="Loose and unconsolidated outer layer of the earth crust made up of small particles.",
        first_sid="book:p18:b3",
        lesson_id="agri-l1",
    )]
    result = compile_multisource_termbase(
        glossary, wikidata, agrovoc=agrovoc, agriculture_domain_confirmed=True,
    )
    assert len(result.entries) == 1
    assert result.entries[0].term_ar == "تربة"
    assert result.audit[0].source == "agrovoc"
    assert result.audit[0].concept_uri.endswith("/c_7156")


def test_wikidata_candidate_requires_context_and_arabic_description():
    wikidata = WikidataConceptIndex.from_jsonl(WIKIDATA)
    item = GlossaryTerm(term_en="probability", definition_en="a type of wooden boat.",
                        first_sid="b1", lesson_id="l1")
    result = compile_multisource_termbase([item], wikidata)
    assert not result.entries
    assert result.audit[0].reason == "no_context_supported_candidate"

