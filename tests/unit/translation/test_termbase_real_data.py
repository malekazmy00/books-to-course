"""Checks against the real, licensed termbase releases on the b2c data disk.

Marked ``real_termbase``; each test skips when its dataset is not mounted, so the
unit suite stays hermetic. Location follows B2C_TERMBASE_DIR (same default as runtime).
"""
import pytest

from b2c_contracts import GlossaryTerm
from b2c_translation.agrovoc import AGROVOC_DATA_SHA256, default_agrovoc_dataset
from b2c_translation.multisource_terms import WIKIDATA_CANDIDATE_SHA256, default_wikidata_dataset
from b2c_translation.pipeline import (
    AgrovocIndex,
    WikidataConceptIndex,
    compile_agrovoc_termbase,
    compile_multisource_termbase,
)

pytestmark = pytest.mark.real_termbase


def _require(path):
    if not path.is_file():
        pytest.skip(f"real termbase dataset not available: {path}")
    return path


def test_real_agrovoc_matches_stamped_hash_and_resolves_soil():
    _require(default_agrovoc_dataset())
    index = AgrovocIndex.from_jsonl()  # verifies AGROVOC_DATA_SHA256
    assert index.source_sha256 == AGROVOC_DATA_SHA256
    glossary = [GlossaryTerm(
        term_en="soil",
        definition_en="Loose and unconsolidated outer layer of the earth crust made up of small particles.",
        first_sid="book1:p18:b3", lesson_id="l1",
    )]
    result = compile_agrovoc_termbase(glossary, index, agriculture_domain_confirmed=True)
    assert [e.term_ar for e in result.entries] == ["تربة"]
    assert result.audit[0].concept_uri == "http://aims.fao.org/aos/agrovoc/c_7156"


def test_real_wikidata_matches_stamped_hash_and_resolves_probability():
    _require(default_wikidata_dataset())
    wikidata = WikidataConceptIndex.from_jsonl()  # verifies WIKIDATA_CANDIDATE_SHA256
    assert wikidata.source_sha256 == WIKIDATA_CANDIDATE_SHA256
    glossary = [GlossaryTerm(
        term_en="probability",
        definition_en="measure of the expectation that an event will occur or a statement is true",
        first_sid="book:p1:b1", lesson_id="stats-l1",
    )]
    result = compile_multisource_termbase(glossary, wikidata)
    assert [e.term_ar for e in result.entries] == ["احتمال"]
    assert result.audit[0].concept_uri.endswith("/Q9492")
