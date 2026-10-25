"""Build a book termbase before translation: extract candidates, collect proposals."""
from __future__ import annotations

from collections.abc import Sequence

from b2c_contracts import TranslationUnit

from .extract import EXTRACTOR_VERSION, extract_candidates
from .model import BookTermbase, Proposal
from .sources import GatewayTermProposer, ProposalSource
from .termhood import TermhoodClassifier, candidate_contexts


def build_book_termbase(units: Sequence[TranslationUnit], *, sources: Sequence[ProposalSource],
                        source_language: str, target_language: str,
                        proposer_only_if_unresolved: bool = True,
                        classifier: TermhoodClassifier | None = None) -> BookTermbase:
    """Every entry starts ``pending`` with ``approved_by=None`` (Gate G3).

    Proposals are ordered by provenance priority regardless of the order of ``sources``.
    A ``GatewayTermProposer`` is only asked about candidates no other non-rule source
    resolved (unless ``proposer_only_if_unresolved=False``), which keeps model calls and
    book text sent to the model to the minimum.

    With a ``classifier`` (v3, D-039; off by default) every candidate first gets a termhood
    suggestion (``TermCandidate.termhood``: locked / hint / dropped, with a reason) and a
    candidate suggested ``dropped`` is never sent to the model proposer. Nothing is approved
    or rejected by the suggestion.
    """
    book_ids = {u.book_id for u in units}
    if len(book_ids) != 1:
        raise ValueError(f"a termbase is built from exactly one book, got {sorted(book_ids)}")
    termbase = BookTermbase.new(book_ids.pop(), source_language=source_language,
                                target_language=target_language, extractor_version=EXTRACTOR_VERSION)
    proposers = [s for s in sources if isinstance(s, GatewayTermProposer)]
    others = [s for s in sources if not isinstance(s, GatewayTermProposer)]
    candidates = extract_candidates(units)
    if classifier is not None:
        contexts = candidate_contexts(candidates, {u.unit_id: u.source_text for u in units})
        verdicts = classifier.classify(candidates, contexts)
        candidates = [c.model_copy(update={"termhood": verdicts[c.source].tier,
                                           "termhood_reason": verdicts[c.source].reason})
                      if c.source in verdicts else c for c in candidates]
    for candidate in candidates:
        proposals: list[Proposal] = []
        for source in others:
            proposals += source.propose(candidate)
        resolved = any(p.provenance != "transliteration" for p in proposals)
        if proposers and candidate.termhood != "dropped" and not (proposer_only_if_unresolved and resolved):
            for proposer in proposers:
                proposals += proposer.propose(candidate)
        termbase.add_candidate(candidate, proposals)
    return termbase
