"""A short brief of the book and its field for the translation engine (D-034; learning-companion §11a:
«سياق المجال: يغذّي محرك الترجمة»).

Built deterministically from the knowledge dossier researched in the book's language BEFORE translation:
the title and authors, then verified facts of the book card and its context (no viewpoints, no author
biography), cut at a sentence within ``max_chars``. The engine gets it as background in the system
message (template ``chat.domain``), never as text to translate.
"""
from __future__ import annotations

from b2c_contracts import KnowledgeDossier

BRIEF_SECTIONS = ("book_card", "context")


def domain_brief(dossier: KnowledgeDossier | None, *, max_chars: int = 500) -> str:
    if dossier is None:
        return ""
    head = f"Book: {dossier.title}" + (f", by {', '.join(dossier.authors)}" if dossier.authors else "") + "."
    out = head[:max_chars]
    for section in BRIEF_SECTIONS:
        for claim in dossier.published():
            if claim.section != section or claim.status != "verified" or claim.kind != "fact":
                continue
            sentence = " ".join(claim.statement.split())
            sentence = sentence if sentence.endswith((".", "!", "?")) else sentence + "."
            if len(out) + 1 + len(sentence) > max_chars:
                return out
            out = f"{out} {sentence}"
    return out


__all__ = ["BRIEF_SECTIONS", "domain_brief"]
