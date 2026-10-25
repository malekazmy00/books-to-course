"""Promotion between stages (R1; plan P2, audit 08 C1).

A stage output is recorded as a ``candidate``; gates attach ``GateResult``s; ``settle`` then
promotes it to ``accepted`` (every required gate's latest result passed, produced by a clean,
known code revision) or ``rejected`` (a required gate failed). Anything else keeps it a
candidate, e.g. while a human gate is still open. Downstream stages and serving read through
``accepted_only`` / ``require_accepted`` and never through "the artifact exists".

The first wired stage is the rebuilt translation (``translate_book.assemble_translation``):
an assembly is accepted only when every unit of the book has a final, publishable decision.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from b2c_contracts import ArtifactRef, GateResult, IndexQualityReport, ProducerIdentity, StageOutputRecord
from b2c_core import get_logger
from b2c_core.revision import code_revision as running_code_revision
from b2c_core.revision import is_clean_revision
from b2c_core.stage_registry import StageOutputRegistry, TransitionRefused

log = get_logger("b2c.promotion")
SYSTEM_ACTOR = "system:promotion"


class NotAccepted(LookupError):
    """The artifact is not an accepted output (unknown, candidate, rejected or quarantined)."""


@dataclass(frozen=True)
class GatePolicy:
    """A gate and its check definition; the version is derived from the definition (P2:
    a changed list of checks cannot keep an old version)."""
    gate_id: str
    kind: str
    definition: Mapping[str, Any] = field(default_factory=dict)

    @property
    def version(self) -> str:
        raw = json.dumps({"gate_id": self.gate_id, "kind": self.kind, "definition": self.definition},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# Every unit of the book must hold a final, publishable decision: accepted or dropped. One unit
# that still needs review, was rejected, or has no result yet keeps the assembly from being used.
TRANSLATION_ASSEMBLY_POLICY = GatePolicy(
    gate_id="translate-book.unit-decisions", kind="deterministic",
    definition={"publishable_decisions": ["accepted", "dropped"], "max_needs_review": 0, "max_rejected": 0,
                "max_pending": 0, "min_units": 1, "all_units_decided": True})


# Index build (R5, audit 03 §5.4): the deterministic content gate of b2c_indexing.index_build.
# Its check list lives in the report; the policy pins the report and chunker versions, so a
# changed gate cannot reuse an old policy version.
def _index_policy() -> GatePolicy:
    from b2c_indexing.index_build import CHUNKER_VERSION, REPORT_VERSION
    from b2c_indexing.content_gate import GATE_VERSION

    return GatePolicy(gate_id="index.content-gate", kind="deterministic", definition={
        "report_version": REPORT_VERSION, "content_gate": GATE_VERSION, "chunker": CHUNKER_VERSION,
        "rule": "IndexQualityReport.accepted (every check passed)"})


INDEX_CONTENT_POLICY = _index_policy()


def evaluate_translation_assembly(counts: Mapping[str, int], *, total_units: int) -> tuple[bool, dict[str, int]]:
    rule = TRANSLATION_ASSEMBLY_POLICY.definition
    metrics = {k: int(counts.get(k, 0)) for k in ("accepted", "dropped", "needs_review", "rejected", "pending")}
    metrics["total_units"] = int(total_units)
    publishable = sum(metrics[d] for d in rule["publishable_decisions"])
    passed = (total_units >= rule["min_units"] and publishable == total_units
              and metrics["needs_review"] <= rule["max_needs_review"] and metrics["rejected"] <= rule["max_rejected"]
              and metrics["pending"] <= rule["max_pending"])
    return passed, metrics


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PromotionService:
    def __init__(self, registry: StageOutputRegistry, *, code_revision: str | None = None,
                 actor: str = SYSTEM_ACTOR, clock: Callable[[], datetime] = _now) -> None:
        self.registry = registry
        self.code_revision = code_revision or running_code_revision()
        self.actor = actor
        self.clock = clock

    # ------------------------------------------------------------------ writes
    def record_candidate(self, course_id: str, artifact: ArtifactRef, *, required_gates: Sequence[str],
                         inputs: Iterable[ArtifactRef] = (), config: Mapping[str, Any] | None = None,
                         identities: Iterable[ProducerIdentity] = ()) -> StageOutputRecord:
        """Register ``artifact`` as a candidate (idempotent: an existing row is returned as is)."""
        if not required_gates:
            raise ValueError("a candidate must name the gates it has to pass")
        record = StageOutputRecord(
            course_id=course_id, artifact=artifact, inputs=list(inputs), code_revision=self.code_revision,
            config=dict(config or {}), identities=list(identities), required_gates=list(required_gates),
            status="candidate", status_changed_at=self.clock(), status_changed_by=self.actor)
        self.registry.register(record)
        return self._get(course_id, artifact)

    def attach_gate(self, course_id: str, result: GateResult) -> bool:
        return self.registry.add_gate_result(course_id, result)

    def promote(self, course_id: str, artifact: ArtifactRef, *, actor: str | None = None) -> StageOutputRecord:
        return self.registry.transition(course_id, artifact.stage, artifact.key, "accepted",
                                        actor=actor or self.actor, code_revision=self.code_revision)

    def reject(self, course_id: str, artifact: ArtifactRef, *, reason: str,
               actor: str | None = None) -> StageOutputRecord:
        return self.registry.transition(course_id, artifact.stage, artifact.key, "rejected", reason=reason,
                                        actor=actor or self.actor, code_revision=self.code_revision)

    def settle(self, course_id: str, artifact: ArtifactRef, *, actor: str | None = None) -> StageOutputRecord:
        """Decide a candidate from its gate results; returns the (possibly unchanged) record."""
        record = self._get(course_id, artifact)
        if record.status != "candidate":
            return record
        latest = record.latest_gate_results()
        failed = sorted(g for g in record.required_gates if g in latest and not latest[g].passed)
        if failed:
            return self.reject(course_id, artifact, reason=f"required gate failed: {', '.join(failed)}", actor=actor)
        if any(g not in latest for g in record.required_gates):
            return record  # waiting for a gate (e.g. a human review)
        if not is_clean_revision(record.code_revision):
            log.warning("gates passed but the output was made by an unstamped or dirty build; left as candidate",
                        extra={"course_id": course_id, "stage": artifact.stage, "key": artifact.key,
                               "code_revision": record.code_revision})
            return record
        return self.promote(course_id, artifact, actor=actor)

    # ------------------------------------------------------------------ reads
    def _get(self, course_id: str, artifact: ArtifactRef) -> StageOutputRecord:
        record = self.registry.get(course_id, artifact.stage, artifact.key)
        if record is None:
            raise NotAccepted(f"{course_id}/{artifact.stage}/{artifact.key} is not registered")
        return record

    def accepted_only(self, course_id: str, stage: str, *, limit: int = 100) -> list[StageOutputRecord]:
        return self.registry.accepted(course_id=course_id, stage=stage, limit=limit)

    def require_accepted(self, course_id: str, artifact: ArtifactRef) -> StageOutputRecord:
        record = self.registry.get(course_id, artifact.stage, artifact.key)
        if record is None or record.status != "accepted" or (
                record.artifact.uri, record.artifact.sha256) != (artifact.uri, artifact.sha256):
            raise NotAccepted(f"{artifact.stage}/{artifact.key} is not an accepted output")
        return record

    # ------------------------------------------------------------------ stage policies
    def gate_translation_assembly(self, course_id: str, artifact: ArtifactRef, *, units_ref: ArtifactRef,
                                  counts: Mapping[str, int], total_units: int,
                                  identities: Iterable[ProducerIdentity] = (),
                                  config: Mapping[str, Any] | None = None) -> StageOutputRecord:
        """Candidate -> unit-decision gate -> accepted | rejected, for one translate-book assembly."""
        policy = TRANSLATION_ASSEMBLY_POLICY
        record = self.record_candidate(course_id, artifact, required_gates=[policy.gate_id], inputs=[units_ref],
                                       config=config, identities=identities)
        if record.status != "candidate":
            return record
        passed, metrics = evaluate_translation_assembly(counts, total_units=total_units)
        self.attach_gate(course_id, GateResult(
            stage=artifact.stage, artifact=artifact, gate_id=policy.gate_id, gate_version=policy.version,
            kind=policy.kind, passed=passed, metrics=metrics, evidence_ref=artifact.uri + "translation.json",
            decider="system", decided_by=f"policy:{policy.gate_id}", decided_at=self.clock()))
        return self.settle(course_id, artifact)

    def gate_index_build(self, course_id: str, artifact: ArtifactRef, *, report: IndexQualityReport,
                         inputs: Iterable[ArtifactRef] = (), config: Mapping[str, Any] | None = None
                         ) -> StageOutputRecord:
        """Candidate -> index content gate -> accepted | rejected, for one book's index snapshot."""
        policy = INDEX_CONTENT_POLICY
        record = self.record_candidate(course_id, artifact, required_gates=[policy.gate_id], inputs=inputs,
                                       config=config)
        if record.status != "candidate":
            return record
        metrics: dict[str, Any] = {k: v for k, v in report.metrics.items() if v is not None}
        metrics["failed_checks"] = report.failed
        self.attach_gate(course_id, GateResult(
            stage=artifact.stage, artifact=artifact, gate_id=policy.gate_id, gate_version=policy.version,
            kind=policy.kind, passed=report.accepted, metrics=metrics,
            evidence_ref=artifact.uri + "index-report.json", decider="system",
            decided_by=f"policy:{policy.gate_id}", decided_at=self.clock()))
        return self.settle(course_id, artifact)


__all__ = ["GatePolicy", "INDEX_CONTENT_POLICY", "NotAccepted", "PromotionService", "TRANSLATION_ASSEMBLY_POLICY",
           "TransitionRefused", "evaluate_translation_assembly"]
