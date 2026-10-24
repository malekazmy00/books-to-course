"""Arabic-equivalent sources for termbase candidates, and build priority (D-028 part E)."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from b2c_contracts import EngineIdentity
from b2c_translation.glossary import ContextualGlossary
from b2c_translation.multisource_terms import WikidataConcept, WikidataConceptIndex
from b2c_translation.termbase import (
    PROPOSER_TEMPLATE,
    BookTermbase,
    ConceptIndexSource,
    GatewayTermProposer,
    GlossarySource,
    MemoryTermSource,
    PriorTermbaseSource,
    TermCandidate,
    TransliterationSource,
    build_book_termbase,
    parse_proposal,
    transliterate_name,
)

from .conftest import FakeGateway, make_units

IDENTITY = EngineIdentity(provider="local", model_id="local-model", prompt_version="x")


def _cand(source, kind="term", context="Signal Mapping is a technique that arranges weak signals on a grid."):
    return TermCandidate(source=source, kind=kind, frequency=3, sections=2, unit_ids=("b:u1",),
                         definition_context=context)


# ------------------------------------------------------------------ glossary
def test_glossary_source_exact_casefold_match():
    g = ContextualGlossary(domain_id="sat", mappings={"signal mapping": "خريطة الإشارات"})
    src = GlossarySource(g)
    [p] = src.propose(_cand("Signal Mapping"))
    assert p.target == "خريطة الإشارات" and p.provenance == "glossary"
    assert p.reference == f"sat@{g.sha256}"
    assert src.propose(_cand("Signal Map")) == []


# ------------------------------------------------------------------ concept index (Wikidata)
def _concept(en, ar, definition):
    return WikidataConcept(
        concept_id="Q1", uri="http://www.wikidata.org/entity/Q1", en_preferred=en, ar_preferred=ar,
        en_alternates=(), ar_alternates=(), linked_en_lemmas=(), linked_ar_lemmas=(),
        definition_en=definition, definition_ar="تعريف", domain_item_ids=(), sense_uris=(),
        license="CC0 1.0 (Wikidata structured data)", release="2026-09-30", source_dump_sha256="0" * 64)


def test_concept_source_requires_matching_context():
    index = WikidataConceptIndex([_concept("signal mapping", "خريطة الإشارات",
                                           "technique that arranges weak signals on a grid")],
                                 source_sha256="a" * 64)
    src = ConceptIndexSource(wikidata=index)
    [p] = src.propose(_cand("Signal Mapping"))
    assert p.provenance == "wikidata" and p.target == "خريطة الإشارات"
    assert p.reference == "http://www.wikidata.org/entity/Q1"
    assert p.source_approved_by == "g3-policy-wikidata-agrovoc-context-v1"
    # no definition context -> no context match -> nothing proposed
    assert src.propose(_cand("Signal Mapping", context=None)) == []
    assert src.propose(_cand("Signal Mapping", context="A dinner recipe with olives.")) == []


# ------------------------------------------------------------------ prior termbase
def test_prior_termbase_source_only_approved():
    prior = BookTermbase.new("old-book", source_language="en", target_language="ar")
    prior.add_candidate(_cand("Signal Mapping"), [])
    prior.add_candidate(_cand("Premortem"), [])
    prior.approve("Signal Mapping", "خريطة الإشارات", reviewer="rev")
    src = PriorTermbaseSource([prior])
    [p] = src.propose(_cand("signal mapping"))
    assert p.provenance == "prior_termbase" and p.source_approved_by == "rev"
    assert p.reference.startswith("old-book@")
    assert src.propose(_cand("Premortem")) == []


# ------------------------------------------------------------------ translation memory
@dataclass
class _Entry:
    origin: str
    target_text: str
    scope: str = ""


class _FakeMemory:
    def __init__(self, table):
        self.table = table
        self.seen = []

    def lookup_entry(self, unit, source_language, target_language):
        self.seen.append((unit.source_text, unit.book_id, source_language, target_language))
        return self.table.get(unit.source_text)


def test_memory_source_uses_human_entries_only():
    mem = _FakeMemory({"Signal Mapping": _Entry("human", "خريطة الإشارات"),
                       "Premortem": _Entry("engine", "ما قبل الموت")})
    src = MemoryTermSource(mem, book_id="b", source_language="en", target_language="ar")
    [p] = src.propose(_cand("Signal Mapping"))
    assert p.provenance == "translation_memory" and p.target == "خريطة الإشارات"
    assert src.propose(_cand("Premortem")) == []
    assert mem.seen[0] == ("Signal Mapping", "b", "en", "ar")


# ------------------------------------------------------------------ gateway proposer
def test_proposer_requires_local_gateway_confirmation(gateway):
    with pytest.raises(ValueError):
        GatewayTermProposer(gateway, IDENTITY, local_gateway_confirmed=False)


def test_proposer_channel_separation_and_one_equivalent(gateway: FakeGateway):
    gateway.replies = ["خريطة الإشارات"]
    proposer = GatewayTermProposer(gateway, IDENTITY, local_gateway_confirmed=True)
    [p] = proposer.propose(_cand("Signal Mapping"))
    assert p.target == "خريطة الإشارات" and p.provenance == "gateway_proposer"
    call = gateway.calls[0]
    system, user = call["messages"]
    assert system["role"] == "system" and user["role"] == "user"
    assert system["content"] == PROPOSER_TEMPLATE.text
    assert "Signal Mapping" not in system["content"]
    assert call["prompt_version"] == PROPOSER_TEMPLATE.version_id
    assert call["temperature"] == 0.0
    data = json.loads(user["content"])
    assert data == {"term": "Signal Mapping", "kind": "term",
                    "context": "Signal Mapping is a technique that arranges weak signals on a grid."}
    assert p.reference == f"local/local-model@{PROPOSER_TEMPLATE.version_id}"


@pytest.mark.parametrize("raw,expected", [
    ("خريطة الإشارات", "خريطة الإشارات"),
    ("  خريطة الإشارات\n", "خريطة الإشارات"),
    ("NONE", None),
    ("", None),
    ("خريطة\nالإشارات", None),                      # two lines
    ("Translation: خريطة الإشارات", None),          # label / Latin
    ("خريطة الإشارات أو رسم الإشارات", None),        # alternatives
    ("خريطة الإشارات / رسم الإشارات", None),
    ("«خريطة الإشارات»", None),                     # quotes are not stripped silently
    ("خريطة " * 12, None),                          # too long
    ('{"target": "خريطة"}', None),
])
def test_parse_proposal_is_strict(raw, expected):
    assert parse_proposal(raw) == expected


def test_proposer_invalid_output_yields_no_proposal(gateway: FakeGateway):
    gateway.replies = ["Here is: Signal map"]
    proposer = GatewayTermProposer(gateway, IDENTITY, local_gateway_confirmed=True)
    assert proposer.propose(_cand("Signal Mapping")) == []


# ------------------------------------------------------------------ transliteration
@pytest.mark.parametrize("name,expected", [
    ("Randolph Pherson", "راندولف فيرسون"),
    ("Richards J. Heuer Jr.", "ريتشاردز ج. هيور جونيور"),
    ("Edward", "إدوارد"),
    ("Victor", "فيكتور"),
    ("Charles", "تشارلز"),
    ("Paul", "بول"),
])
def test_transliteration_convention(name, expected):
    assert transliterate_name(name) == expected


def test_transliteration_source_only_for_people():
    src = TransliterationSource()
    [p] = src.propose(_cand("Paul", kind="person"))
    assert p.provenance == "transliteration" and p.target == "بول"
    assert src.propose(_cand("Signal Mapping")) == []


# ------------------------------------------------------------------ build
def test_build_book_termbase_priority_and_nothing_approved(gateway: FakeGateway):
    units = make_units()
    g = ContextualGlossary(domain_id="sat", mappings={"Signal Mapping": "خريطة الإشارات"})
    gateway.replies = ["عرض"] * 200
    proposer = GatewayTermProposer(gateway, IDENTITY, local_gateway_confirmed=True)
    tb = build_book_termbase(units, sources=[proposer, TransliterationSource(), GlossarySource(g)],
                             source_language="en", target_language="ar")
    assert tb.book_id == "synthetic-tb"
    assert tb.locked_terms() == {} and tb.entity_table() == {}
    assert all(r.status == "pending" and r.approved_by is None for r in tb.entries)
    rec = tb.get("Signal Mapping")
    assert rec.proposals[0].provenance == "glossary"
    # proposer is only asked when nothing earlier resolved the candidate (default)
    asked = {json.loads(c["messages"][1]["content"])["term"] for c in gateway.calls}
    assert "Signal Mapping" not in asked
    person = tb.get("Lena Vasquez")
    assert person.kind == "person"
    assert [p.provenance for p in person.proposals] == ["gateway_proposer", "transliteration"]


def test_build_rejects_mixed_books():
    units = make_units() + make_units(book_id="other")
    with pytest.raises(ValueError):
        build_book_termbase(units, sources=[], source_language="en", target_language="ar")


def test_proposer_template_version_is_locked():
    # changing the wording requires a new version (same rule as engines/test_prompts.py)
    assert PROPOSER_TEMPLATE.version_id == "termbase.propose.system@v1"
    assert PROPOSER_TEMPLATE.sha256 == "3eb0109ce5dcd54433d6998a8b517e8b1bd0c684c44ae18fc503ec16600321b2"
    assert PROPOSER_TEMPLATE.placeholders == set()


def test_approved_termbase_feeds_unit_translator_mappings():
    from b2c_translation.book_pipeline import terms_in

    tb = build_book_termbase(make_units(), sources=[TransliterationSource()],
                             source_language="en", target_language="ar")
    tb.approve("Signal Mapping", "خريطة الإشارات", reviewer="rev")
    tb.approve("Lena Vasquez", "لينا فاسكيز", reviewer="rev")
    unit_text = "Dr. Lena Vasquez taught Signal Mapping."
    assert terms_in(unit_text, tb.locked_terms()) == {"Signal Mapping": "خريطة الإشارات"}
    assert terms_in(unit_text, tb.entity_table()) == {"Lena Vasquez": "لينا فاسكيز"}
