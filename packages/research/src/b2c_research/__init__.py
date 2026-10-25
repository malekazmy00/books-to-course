"""b2c_research — knowledge research in the book's language (EN), before translation (D-034, D-035).

Official/open APIs first (OpenAlex, Wikidata, Wikipedia, Open Library), then a pluggable web search
(b2c-owned SearXNG, or a paid API when configured). Fetched content is data: sandboxed plain-text
extraction, instruction-like text removed, size limits, domain policy, reliability tiers, fetch dates.
Claims carry verbatim quotes, are checked by a citation validator (quote present + an independent
judge of another model family) and assembled into a ``KnowledgeDossier`` (b2c_contracts 1.7.0).

Model calls go through an injected ``complete_text`` client (b2c_llm_gateway in production). This
package never imports another layer; only ``b2c_research.http`` imports an HTTP client.
"""
from .claims import ModelRole
from .dossier import build_dossier
from .gates import CITATIONS_GATE_ID, TIER_GATE_ID, evaluate_dossier
from .meta import BookMeta, extract_book_meta
from .planner import PlannedQuery, plan_queries

RESEARCH_VERSION = "knowledge-research-2026-10-25.1"

__all__ = ["BookMeta", "CITATIONS_GATE_ID", "ModelRole", "PlannedQuery", "RESEARCH_VERSION", "TIER_GATE_ID",
           "build_dossier", "evaluate_dossier", "extract_book_meta", "plan_queries"]
