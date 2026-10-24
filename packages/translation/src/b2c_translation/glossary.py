"""Explicit immutable contextual terminology snapshots; no global sense substitution."""
from __future__ import annotations
import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ContextualGlossary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    domain_id: str = Field(min_length=1, max_length=100)
    mappings: dict[str, str] = Field(default_factory=dict, max_length=500)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_snapshot(self):
        normalized = {}
        for term, target in self.mappings.items():
            term, target = term.strip(), target.strip()
            if not term or not target or len(term) > 160 or len(target) > 240:
                raise ValueError("invalid contextual glossary term")
            if term.casefold() in {key.casefold() for key in normalized}:
                raise ValueError("duplicate contextual glossary term")
            normalized[term] = target
        digest = hashlib.sha256(json.dumps(
            {"domain_id": self.domain_id, "mappings": normalized},
            sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if self.sha256 is not None and self.sha256 != digest:
            raise ValueError("contextual glossary checksum mismatch")
        object.__setattr__(self, "mappings", normalized)
        object.__setattr__(self, "sha256", digest)
        return self

def glossary_identity(snapshot: ContextualGlossary | None) -> str:
    if snapshot is None:
        return "none"
    # Pydantic frozen models cannot prevent in-place dict mutation; revalidate
    # before using the supplied checksum as a cache identity.
    return ContextualGlossary.model_validate(snapshot.model_dump()).sha256
