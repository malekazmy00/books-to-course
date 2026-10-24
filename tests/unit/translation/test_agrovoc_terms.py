from pathlib import Path

from b2c_contracts import GlossaryTerm
from b2c_translation.pipeline import (
    AgrovocIndex,
    compile_agrovoc_termbase,
    term_injection_block,
)

DATASET = Path("/mnt/wsl/b2cdata/eval-datasets/termbases/agrovoc/agrovoc_en_ar_candidates.jsonl")


def test_agrovoc_real_dataset_prefers_context_supported_arabic_term():
    index = AgrovocIndex.from_jsonl(DATASET)
    glossary = [GlossaryTerm(
        term_en="soil",
        definition_en="Loose and unconsolidated outer layer of the earth crust made up of small particles.",
        first_sid="book1:p18:b3",
        lesson_id="l1",
    )]
    result = compile_agrovoc_termbase(glossary, index, agriculture_domain_confirmed=True)
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.term_ar == "تربة"
    assert entry.approved_by == "g3-policy-agrovoc-en-ar-v1"
    assert result.audit[0].status == "accepted"
    assert result.audit[0].concept_uri == "http://aims.fao.org/aos/agrovoc/c_7156"
    injected = term_injection_block("Soil retains water.", result.entries)
    assert "soil => تربة" in injected


def test_agrovoc_refuses_out_of_domain_or_context_mismatch():
    index = AgrovocIndex.from_jsonl(DATASET)
    item = GlossaryTerm(term_en="soil", definition_en="A musical score for a solo performer.",
                        first_sid="b1", lesson_id="l1")
    out_of_domain = compile_agrovoc_termbase([item], index, agriculture_domain_confirmed=False)
    assert not out_of_domain.entries
    assert out_of_domain.audit[0].reason == "domain_not_confirmed"

    mismatch = compile_agrovoc_termbase([item], index, agriculture_domain_confirmed=True)
    assert not mismatch.entries
    assert mismatch.audit[0].reason == "context_definition_mismatch"


def test_agrovoc_never_resolves_without_bilingual_definition():
    index = AgrovocIndex.from_jsonl(DATASET)
    item = GlossaryTerm(term_en="soil inoculation", definition_en="Applying microbes to soil.",
                        first_sid="b1", lesson_id="l1")
    result = compile_agrovoc_termbase([item], index, agriculture_domain_confirmed=True)
    assert not result.entries
    assert result.audit[0].reason == "bilingual_definition_evidence_missing"

