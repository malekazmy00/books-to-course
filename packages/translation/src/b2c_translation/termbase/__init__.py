"""Per-book locked termbase and named-entity table (D-028 part E).

Build before translation (``build_book_termbase``), approve by a human (Gate G3),
then feed ``locked_terms()`` / ``entity_table()`` to ``UnitTranslator(terms=, entities=)``.
"""
from .build import build_book_termbase
from .extract import EXTRACTOR_VERSION, TRANSLATABLE_KINDS, extract_candidates
from .model import (
    ARTIFACT_FORMAT,
    ARTIFACT_FORMAT_VERSION,
    ENTITY_KINDS,
    PROVENANCE_PRIORITY,
    TERM_KINDS,
    BookTermbase,
    ConsistencyIssue,
    Proposal,
    TermbaseError,
    TermCandidate,
    TermRecord,
)
from .sources import (
    PROPOSER_TEMPLATE,
    ConceptIndexSource,
    GatewayTermProposer,
    GlossarySource,
    MemoryTermSource,
    PriorTermbaseSource,
    ProposalSource,
    TransliterationSource,
    parse_proposal,
)
from .termhood import (
    FEW_SHOT_EXAMPLES,
    TERMHOOD_TEMPLATE,
    TermhoodClassifier,
    TermhoodVerdict,
    candidate_contexts,
    parse_termhood,
)
from .transliterate import TRANSLITERATION_RULES_VERSION, transliterate_name

__all__ = [
    "ARTIFACT_FORMAT", "ARTIFACT_FORMAT_VERSION", "ENTITY_KINDS", "EXTRACTOR_VERSION", "FEW_SHOT_EXAMPLES",
    "PROPOSER_TEMPLATE", "TERMHOOD_TEMPLATE", "TermhoodClassifier", "TermhoodVerdict", "candidate_contexts",
    "parse_termhood",
    "PROVENANCE_PRIORITY", "TERM_KINDS", "TRANSLATABLE_KINDS", "TRANSLITERATION_RULES_VERSION",
    "BookTermbase", "ConceptIndexSource", "ConsistencyIssue", "GatewayTermProposer", "GlossarySource",
    "MemoryTermSource", "PriorTermbaseSource", "Proposal", "ProposalSource", "TermCandidate",
    "TermRecord", "TermbaseError", "TransliterationSource", "build_book_termbase", "extract_candidates",
    "parse_proposal", "transliterate_name",
]
