import hashlib
from pathlib import Path

import pytest

from b2c_contracts import GlossaryTerm
from b2c_translation import agrovoc as agrovoc_module
from b2c_translation.pipeline import (
    AgrovocIndex,
    compile_agrovoc_termbase,
    term_injection_block,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/termbases"
DATASET = FIXTURES / "agrovoc/agrovoc_en_ar_candidates.jsonl"
FIXTURE_SHA256 = hashlib.sha256(DATASET.read_bytes()).hexdigest()


def _index() -> AgrovocIndex:
    # Fixture rows are not the pinned AGROVOC release: opt out of the release pin
    # explicitly; the loader still records the hash of what it actually read.
    return AgrovocIndex.from_jsonl(DATASET, expected_sha256=None)


def test_agrovoc_prefers_context_supported_arabic_term():
    index = _index()
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
    assert result.audit[0].source_hash == FIXTURE_SHA256
    injected = term_injection_block("Soil retains water.", result.entries)
    assert "soil => تربة" in injected


def test_agrovoc_refuses_out_of_domain_or_context_mismatch():
    index = _index()
    item = GlossaryTerm(term_en="soil", definition_en="A musical score for a solo performer.",
                        first_sid="b1", lesson_id="l1")
    out_of_domain = compile_agrovoc_termbase([item], index, agriculture_domain_confirmed=False)
    assert not out_of_domain.entries
    assert out_of_domain.audit[0].reason == "domain_not_confirmed"

    mismatch = compile_agrovoc_termbase([item], index, agriculture_domain_confirmed=True)
    assert not mismatch.entries
    assert mismatch.audit[0].reason == "context_definition_mismatch"


def test_agrovoc_never_resolves_without_bilingual_definition():
    index = _index()
    item = GlossaryTerm(term_en="soil inoculation", definition_en="Applying microbes to soil.",
                        first_sid="b1", lesson_id="l1")
    result = compile_agrovoc_termbase([item], index, agriculture_domain_confirmed=True)
    assert not result.entries
    assert result.audit[0].reason == "bilingual_definition_evidence_missing"


def test_agrovoc_ambiguous_arabic_preferred_label_is_unresolved():
    item = GlossaryTerm(term_en="water", definition_en="Liquid compound of hydrogen and oxygen.",
                        first_sid="b1", lesson_id="l1")
    result = compile_agrovoc_termbase([item], _index(), agriculture_domain_confirmed=True)
    assert not result.entries
    assert result.audit[0].reason == "ambiguous_arabic_preferred_label"


def test_loader_records_actual_file_hash():
    assert _index().source_sha256 == FIXTURE_SHA256


def test_pinned_release_hash_mismatch_fails_loudly():
    # Default: loading anything other than the stamped release is refused.
    with pytest.raises(ValueError, match="SHA-256"):
        AgrovocIndex.from_jsonl(DATASET)
    with pytest.raises(ValueError, match="SHA-256"):
        AgrovocIndex.from_jsonl(DATASET, expected_sha256="0" * 64)
    assert AgrovocIndex.from_jsonl(DATASET, expected_sha256=FIXTURE_SHA256).source_sha256 == FIXTURE_SHA256


def test_termbase_dir_is_configurable(monkeypatch):
    monkeypatch.setenv("B2C_TERMBASE_DIR", str(FIXTURES))
    assert agrovoc_module.termbase_dir() == FIXTURES
    assert agrovoc_module.default_agrovoc_dataset() == DATASET
    # The default dataset is always the pinned release; the fixture is not it.
    with pytest.raises(ValueError, match="SHA-256"):
        AgrovocIndex.from_jsonl()
    monkeypatch.delenv("B2C_TERMBASE_DIR")
    assert agrovoc_module.termbase_dir() == Path("/mnt/wsl/b2cdata/eval-datasets/termbases")
