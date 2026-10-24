"""Build a book termbase before translation: extract candidates, collect proposals."""
from __future__ import annotations

from collections.abc import Sequence

from b2c_contracts import TranslationUnit

from .extract import EXTRACTOR_VERSION, extract_candidates
from .model import BookTermbase, Proposal
from .sources import GatewayTermProposer, ProposalSource


def build_book_termbase(units: Sequence[TranslationUnit], *, sources: Sequence[ProposalSource],
                        source_language: str, target_language: str,
                        proposer_only_if_unresolved: bool = True) -> BookTermbase:
    """Every entry starts ``pending`` with ``approved_by=None`` (Gate G3).

    Proposals are ordered by provenance priority regardless of the order of ``sources``.
    A ``GatewayTermProposer`` is only asked about candidates no other non-rule source
    resolved (unless ``proposer_only_if_unresolved=False``), which keeps model calls and
    book text sent to the model to the minimum.
    """
    book_ids = {u.book_id for u in units}
    if len(book_ids) != 1:
        raise ValueError(f"a termbase is built from exactly one book, got {sorted(book_ids)}")
    termbase = BookTermbase.new(book_ids.pop(), source_language=source_language,
                                target_language=target_language, extractor_version=EXTRACTOR_VERSION)
    proposers = [s for s in sources if isinstance(s, GatewayTermProposer)]
    others = [s for s in sources if not isinstance(s, GatewayTermProposer)]
    for candidate in extract_candidates(units):
        proposals: list[Proposal] = []
        for source in others:
            proposals += source.propose(candidate)
        resolved = any(p.provenance != "transliteration" for p in proposals)
        if proposers and not (proposer_only_if_unresolved and resolved):
            for proposer in proposers:
                proposals += proposer.propose(candidate)
        termbase.add_candidate(candidate, proposals)
    return termbase
