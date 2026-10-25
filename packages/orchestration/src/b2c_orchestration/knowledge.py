"""Knowledge research and the concept layer (D-032..D-036; learning-companion §6, §10–§12).

New activities and a new workflow; nothing starts them automatically. Both produce ENGLISH content
from the English source book; translation happens later through the translation pipeline (D-035).

    research_book_knowledge   parsed book (+ termbase key concepts) -> stage "knowledge-research":
                              dossier.json (KnowledgeDossier), sources.json (sanitized fetched text the
                              citation validator checked), meta.json (metadata, planned queries),
                              gates.json; promotion candidate with gates knowledge.citations and
                              knowledge.tier-coverage (system GateResults).
    build_book_concept_layer  parsed book + outline + termbase (+ accepted dossier) -> stage
                              "concept-layer": concept-layer.json (ConceptLayer), seeds.json, gates.json;
                              promotion candidate with gates concepts.grounding and concepts.coverage.
    BookKnowledgeWorkflow     research, then the concept layer with the research artifact.

Termbase entries reach the concept layer as ``ConceptSeed``s built here (orchestration is the only
package that may import both layers): approved terms/acronyms when the termbase has approvals,
otherwise the termbase candidates (``b2c_translation.termbase.extract_candidates`` over the
translation units when no termbase artifact exists). The dossier is used only when it is an
accepted output (unless ``require_accepted_dossier=False`` during development).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from b2c_concepts import CONCEPT_LAYER_VERSION, BookIndex, ConceptModel, ConceptSeed, build_concept_layer
    from b2c_concepts.gates import evaluate_concept_layer
    from b2c_concepts.layer import DEFINE_PROMPT_VERSION, JUDGE_DEFINITION_VERSION, NOVELTY_PROMPT_VERSION
    from b2c_contracts import (ArtifactRef, ConceptLayer, CourseOutline, CourseOutlineV2, GateResult, KnowledgeDossier, ParsedBook,
                               ParsedBookV2, ProducerIdentity, TranslationUnit, stage_key)
    from b2c_research import RESEARCH_VERSION, BookMeta, ModelRole, build_dossier, extract_book_meta, plan_queries
    from b2c_research.claims import (EXTRACT_PROMPT_VERSION, JUDGE_CONFLICT_VERSION, JUDGE_CORROBORATE_VERSION,
                                     JUDGE_RELEVANCE_VERSION, JUDGE_SUPPORT_VERSION)
    from b2c_research.gates import evaluate_dossier
    from b2c_research.sources import ResearchRunner, SourceBuilder
    from b2c_translation.termbase import TERM_KINDS, BookTermbase, extract_candidates

    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .promotion import GatePolicy, NotAccepted, PromotionService
    from .retry import DEFAULT_RETRY

KNOWLEDGE_STAGE = "knowledge-research"
CONCEPT_STAGE = "concept-layer"
RESEARCH_ACTIVITY = "research_book_knowledge"
CONCEPT_ACTIVITY = "build_book_concept_layer"
WORKFLOW_NAME = "BookKnowledgeWorkflow"
FRONT_MATTER_BLOCKS = 40


class ResearchKnowledgeInput(BaseModel):
    course_id: str
    parsed_ref: dict[str, Any]
    termbase_ref: dict[str, Any] | None = None   # book-termbase(-approved) artifact (termbase.json)
    units_ref: dict[str, Any] | None = None      # translation-units artifact, for candidates without a termbase
    meta_override: dict[str, Any] | None = None  # operator-supplied title/authors/subjects (wins over the model)
    max_research_concepts: int = 8
    max_candidates: int = 60
    cache_bust: str | None = None


class BuildConceptLayerInput(BaseModel):
    course_id: str
    parsed_ref: dict[str, Any]
    outline_ref: dict[str, Any]                  # approve-outline / build-outline(-v2) artifact
    termbase_ref: dict[str, Any] | None = None
    units_ref: dict[str, Any] | None = None
    dossier_ref: dict[str, Any] | None = None
    require_accepted_dossier: bool = True
    max_candidates: int = 60
    max_model_concepts: int | None = None
    cache_bust: str | None = None


class BookKnowledgeInput(BaseModel):
    course_id: str
    parsed_ref: dict[str, Any]
    outline_ref: dict[str, Any]
    termbase_ref: dict[str, Any] | None = None
    units_ref: dict[str, Any] | None = None
    meta_override: dict[str, Any] | None = None
    max_research_concepts: int = 8
    max_candidates: int = 60
    max_model_concepts: int | None = None
    require_accepted_dossier: bool = True
    cache_bust: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ loading helpers (also used by scripts)
def load_parsed(store: Any, ref: ArtifactRef) -> ParsedBook | ParsedBookV2:
    files = set(store.list_files(ref))
    if "parsed-book-v2.json" in files:
        return ParsedBookV2.model_validate_json(store.read(ref, "parsed-book-v2.json"))
    return ParsedBook.model_validate_json(store.read(ref, "parsed-book.json"))


def load_book_index(store: Any, parsed_ref: ArtifactRef, outline_ref: ArtifactRef) -> BookIndex:
    files = set(store.list_files(outline_ref))
    parsed_files = set(store.list_files(parsed_ref))
    if "course-outline-v2.json" in files and "parsed-book-v2.json" in parsed_files:
        return BookIndex.from_parsed_v2(ParsedBookV2.model_validate_json(store.read(parsed_ref, "parsed-book-v2.json")),
                                        CourseOutlineV2.model_validate_json(store.read(outline_ref, "course-outline-v2.json")))
    book = ParsedBook.model_validate_json(store.read(parsed_ref, "parsed-book.json"))
    if "approved-outline.json" in files:
        outline = CourseOutline.model_validate(json.loads(store.read(outline_ref, "approved-outline.json"))["outline"])
    else:
        outline = CourseOutline.model_validate_json(store.read(outline_ref, "course-outline.json"))
    return BookIndex.from_parsed_v1(book, outline)


def front_blocks(book: ParsedBook | ParsedBookV2, n: int = FRONT_MATTER_BLOCKS) -> list[tuple[str, str]]:
    return [(b.block_id, b.text) for b in book.blocks
            if b.type not in ("page_header", "page_footer", "running_header", "running_footer", "page_number")][:n]


def termbase_seeds(termbase: BookTermbase | None, *, units: Sequence[TranslationUnit] | None,
                   max_candidates: int) -> tuple[list[ConceptSeed], str, list[str]]:
    """(seeds, basis, target terms for coverage). Approved terms/acronyms when there are any;
    otherwise the highest-scoring term/acronym candidates (from the termbase, else from the units)."""
    if termbase is not None:
        approved = [r for r in termbase.entries if r.status == "approved" and r.approved_by and r.kind in TERM_KINDS]
        if approved:
            seeds = [ConceptSeed(r.source, r.kind, "approved", frequency=r.candidate.frequency,
                                 definition_context=r.candidate.definition_context, expansion=r.candidate.expansion,
                                 signals=tuple(r.candidate.signals))
                     for r in approved]
            return seeds, "approved", [s.term for s in seeds]
        candidates = [r.candidate for r in termbase.entries if r.kind in TERM_KINDS and r.status != "rejected"]
    elif units is not None:
        candidates = [x for x in extract_candidates(list(units)) if x.kind in TERM_KINDS]
    else:
        return [], "candidates", []
    candidates = sorted(candidates, key=lambda x: (-_priority(x.score, x.source, x.signals), x.source.casefold()))
    candidates = candidates[:max_candidates]
    seeds = [ConceptSeed(x.source, x.kind, "candidate", frequency=x.frequency,
                         definition_context=x.definition_context, expansion=x.expansion, signals=tuple(x.signals))
             for x in candidates]
    return seeds, "candidates", [s.term for s in seeds]


def _priority(weight: float, term: str, signals: Sequence[str]) -> float:
    """Key-concept priority: book-index entries, definitions, multiword terms and acronyms with an
    expansion first; capitalised headings alone (e.g. "Origins of This Technique") last."""
    sig = set(signals)
    bonus = (1.0 * ("index" in sig) + 1.0 * ("defined" in sig) + 0.8 * ("acronym_expansion" in sig)
             + 0.6 * (len(term.split()) > 1) + 0.4 * ("keyness" in sig) + 0.4 * ("c_value" in sig))
    if "heading" in sig and not sig & {"defined", "index", "acronym_expansion"}:
        bonus -= 0.8
    return weight * max(0.2, 1.0 + bonus)


def research_concepts(seeds: Sequence[ConceptSeed], n: int, *, title: str = "") -> list[str]:
    """The key concepts researched in their field: highest priority, not just restating the title."""
    t = title.casefold()
    ranked = sorted(seeds, key=lambda s: (-_priority(s.frequency, s.term, s.signals), s.term.casefold()))
    return [s.term for s in ranked if not (t and s.term.casefold().rstrip("s") in t)][:n]


def attach_gates(promotion: PromotionService, course_id: str, ref: ArtifactRef, outcomes: Sequence[Any], *,
                 inputs: Sequence[ArtifactRef], config: dict[str, Any], identities: Sequence[ProducerIdentity],
                 evidence: str = "gates.json") -> Any:
    record = promotion.record_candidate(course_id, ref, required_gates=[o.gate_id for o in outcomes], inputs=inputs,
                                        config=config, identities=identities)
    if record.status != "candidate":
        return record
    latest = record.latest_gate_results()
    for o in outcomes:
        if o.gate_id in latest:
            continue
        policy = GatePolicy(gate_id=o.gate_id, kind=o.kind, definition=o.definition)
        promotion.attach_gate(course_id, GateResult(
            stage=ref.stage, artifact=ref, gate_id=o.gate_id, gate_version=policy.version, kind=o.kind,
            passed=o.passed, metrics=json.loads(json.dumps(o.metrics, default=str)), evidence_ref=ref.uri + evidence,
            decider="system", decided_by=f"policy:{o.gate_id}", decided_at=promotion.clock()))
    return promotion.settle(course_id, ref)


def _gates_json(outcomes: Sequence[Any]) -> dict[str, Any]:
    return {o.gate_id: {"kind": o.kind, "passed": o.passed, "metrics": o.metrics, "definition": o.definition}
            for o in outcomes}


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str).encode("utf-8")


# ------------------------------------------------------------------ activities
class KnowledgeActivities:
    def __init__(self, *, artifact_store: Any, extractor: ModelRole, judge: ModelRole,
                 concept_model: ConceptModel | None, providers: Callable[[SourceBuilder, BookMeta], list[Any]],
                 promotion: PromotionService | None = None, prov: Any | None = None,
                 clock: Callable[[], datetime] = _now, web_enabled: bool = False, workers: int = 4) -> None:
        if extractor.family == judge.family:
            raise ValueError("the citation judge must be from a different model family than the extractor")
        self.store = artifact_store
        self.extractor = extractor
        self.judge = judge
        self.concept_model = concept_model
        self.providers = providers
        self.promotion = promotion
        self.prov = prov
        self.clock = clock
        self.web_enabled = web_enabled
        self.workers = workers

    def _termbase(self, raw: dict[str, Any] | None) -> tuple[BookTermbase | None, ArtifactRef | None]:
        if not raw:
            return None, None
        ref = ArtifactRef.model_validate(raw)
        return BookTermbase.from_json(self.store.read(ref, "termbase.json")), ref

    def _units(self, raw: dict[str, Any] | None) -> list[TranslationUnit] | None:
        if not raw:
            return None
        ref = ArtifactRef.model_validate(raw)
        return [TranslationUnit.model_validate(u) for u in json.loads(self.store.read(ref, "units.json"))]

    # .............................................................. research
    @activity.defn(name=RESEARCH_ACTIVITY)
    def research(self, inp: ResearchKnowledgeInput) -> dict[str, Any]:
        with activity_heartbeats(RESEARCH_ACTIVITY):
            return self._research(inp)

    def _research(self, inp: ResearchKnowledgeInput) -> dict[str, Any]:
        parsed_ref = ArtifactRef.model_validate(inp.parsed_ref)
        termbase, tb_ref = self._termbase(inp.termbase_ref)
        identities = [self.extractor.identity, self.judge.identity]
        config = {"research_version": RESEARCH_VERSION, "meta_override": inp.meta_override,
                  "max_research_concepts": inp.max_research_concepts, "max_candidates": inp.max_candidates,
                  "web": self.web_enabled, "extractor": self.extractor.identity.model_id,
                  "judge": self.judge.identity.model_id, "cache_bust": inp.cache_bust,
                  "prompts": [EXTRACT_PROMPT_VERSION, JUDGE_SUPPORT_VERSION, JUDGE_CORROBORATE_VERSION,
                              JUDGE_CONFLICT_VERSION, JUDGE_RELEVANCE_VERSION]}
        inputs = [parsed_ref] + ([tb_ref] if tb_ref else [])
        key = stage_key(KNOWLEDGE_STAGE, RESEARCH_VERSION, [r.sha256 for r in inputs], config)

        def compute() -> dict[str, bytes]:
            book = load_parsed(self.store, parsed_ref)
            meta, problems = extract_book_meta(front_blocks(book), self.extractor.llm, override=inp.meta_override)
            seeds, basis, _ = termbase_seeds(termbase, units=self._units(inp.units_ref), max_candidates=inp.max_candidates)
            concepts = research_concepts(seeds, inp.max_research_concepts, title=meta.full_title)
            queries = plan_queries(meta, concepts, max_concepts=inp.max_research_concepts, web=self.web_enabled)
            builder = SourceBuilder()
            runner = ResearchRunner(self.providers(builder, meta), builder)
            sources = runner.run(queries, meta)
            dossier = build_dossier(book_id=book.book_id, meta=meta, sources=sources, extractor=self.extractor,
                                    judge=self.judge, queries=[q.label for q in queries], rejected=builder.rejected,
                                    clock=self.clock, concepts=concepts, workers=self.workers)
            outcomes = evaluate_dossier(dossier)
            return {"dossier.json": dossier.model_dump_json(indent=1).encode("utf-8"),
                    "sources.json": _dump([{"source_id": s.source.source_id, "url": s.source.url,
                                            "provider": s.source.provider, "tier": s.source.tier,
                                            "fetched_at": s.source.fetched_at.isoformat(), "title": s.source.title,
                                            "text": s.text} for s in sources]),
                    "meta.json": _dump({"meta": meta.__dict__, "problems": problems, "concepts": concepts,
                                        "termbase_basis": basis, "queries": [q.__dict__ for q in queries]}),
                    "gates.json": _dump(_gates_json(outcomes))}

        ref, hit = self.store.get_or_compute(inp.course_id, KNOWLEDGE_STAGE, key, compute)
        dossier = KnowledgeDossier.model_validate_json(self.store.read(ref, "dossier.json"))
        outcomes = evaluate_dossier(dossier)
        status = None
        if self.promotion is not None:
            status = attach_gates(self.promotion, inp.course_id, ref, outcomes, inputs=inputs,
                                  config={k: v for k, v in config.items() if k != "cache_bust"},
                                  identities=identities).status
        if self.prov is not None:
            self.prov.record_activity(stage=KNOWLEDGE_STAGE, stage_version=RESEARCH_VERSION,
                                      agent=self.extractor.identity.model_id, prompt_version=EXTRACT_PROMPT_VERSION,
                                      activity_id=f"act:{KNOWLEDGE_STAGE}:{ref.key[:16]}",
                                      used=[r.uri for r in inputs], derived=[(ref.uri, parsed_ref.uri)],
                                      generated=[ref.uri])
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit, "stage_output_status": status,
                "published_claims": len(dossier.published()), "metrics": dossier.metrics,
                "gates": _gates_json(outcomes)}

    # .............................................................. concept layer
    @activity.defn(name=CONCEPT_ACTIVITY)
    def concepts(self, inp: BuildConceptLayerInput) -> dict[str, Any]:
        with activity_heartbeats(CONCEPT_ACTIVITY):
            return self._concepts(inp)

    def _dossier(self, inp: BuildConceptLayerInput) -> tuple[KnowledgeDossier | None, ArtifactRef | None, str]:
        if not inp.dossier_ref:
            return None, None, "no dossier"
        ref = ArtifactRef.model_validate(inp.dossier_ref)
        if inp.require_accepted_dossier and self.promotion is not None:
            try:
                self.promotion.require_accepted(inp.course_id, ref)
            except NotAccepted:
                return None, None, "dossier not accepted"
        return KnowledgeDossier.model_validate_json(self.store.read(ref, "dossier.json")), ref, "used"

    def _concepts(self, inp: BuildConceptLayerInput) -> dict[str, Any]:
        parsed_ref = ArtifactRef.model_validate(inp.parsed_ref)
        outline_ref = ArtifactRef.model_validate(inp.outline_ref)
        termbase, tb_ref = self._termbase(inp.termbase_ref)
        dossier, dossier_ref, dossier_state = self._dossier(inp)
        model_id = self.concept_model.identity.model_id if self.concept_model else None
        config = {"concept_layer_version": CONCEPT_LAYER_VERSION, "max_candidates": inp.max_candidates,
                  "max_model_concepts": inp.max_model_concepts, "model": model_id,
                  "judge": self.judge.identity.model_id, "dossier": dossier_state,
                  "prompts": [DEFINE_PROMPT_VERSION, NOVELTY_PROMPT_VERSION, JUDGE_DEFINITION_VERSION],
                  "cache_bust": inp.cache_bust}
        inputs = [parsed_ref, outline_ref] + ([tb_ref] if tb_ref else []) + ([dossier_ref] if dossier_ref else [])
        key = stage_key(CONCEPT_STAGE, CONCEPT_LAYER_VERSION, [r.sha256 for r in inputs], config)
        index = load_book_index(self.store, parsed_ref, outline_ref)
        seeds, basis, targets = termbase_seeds(termbase, units=None if termbase else self._units(inp.units_ref),
                                               max_candidates=inp.max_candidates)

        def compute() -> dict[str, bytes]:
            layer = build_concept_layer(
                index, seeds, model=self.concept_model, termbase_basis=basis, clock=self.clock, dossier=dossier,
                termbase_sha256=termbase.sha256() if termbase else None,
                dossier_sha256=dossier_ref.sha256 if dossier_ref else None,
                max_model_concepts=inp.max_model_concepts, workers=self.workers,
                judge=ConceptModel(self.judge.llm, self.judge.identity) if self.concept_model else None)
            outcomes = evaluate_concept_layer(layer, index, target_terms=targets)
            return {"concept-layer.json": layer.model_dump_json(indent=1).encode("utf-8"),
                    "seeds.json": _dump({"basis": basis, "targets": targets,
                                         "seeds": [s.__dict__ for s in seeds]}),
                    "gates.json": _dump(_gates_json(outcomes))}

        ref, hit = self.store.get_or_compute(inp.course_id, CONCEPT_STAGE, key, compute)
        layer = ConceptLayer.model_validate_json(self.store.read(ref, "concept-layer.json"))
        outcomes = evaluate_concept_layer(layer, index, target_terms=targets)
        status = None
        if self.promotion is not None:
            identities = [self.concept_model.identity, self.judge.identity] if self.concept_model else []
            status = attach_gates(self.promotion, inp.course_id, ref, outcomes, inputs=inputs,
                                  config={k: v for k, v in config.items() if k != "cache_bust"},
                                  identities=identities).status
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit, "stage_output_status": status,
                "dossier_used": dossier is not None, "metrics": layer.metrics, "gates": _gates_json(outcomes)}


# ------------------------------------------------------------------ workflow
def _retry(maximum_attempts: int) -> RetryPolicy:
    return RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0,
                       maximum_interval=timedelta(minutes=5), maximum_attempts=maximum_attempts,
                       non_retryable_error_types=sorted({*DEFAULT_RETRY.non_retryable_error_types,
                                                         "CloudProviderNotAllowed", "ValueError"}))


KNOWLEDGE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(hours=2),
                                         schedule_to_close_timeout=timedelta(hours=6),
                                         heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(3))


@workflow.defn(name=WORKFLOW_NAME)
class BookKnowledgeWorkflow:
    """Knowledge research (EN), then the concept layer (EN) of one book. Started only on request."""

    @workflow.run
    async def run(self, inp: BookKnowledgeInput) -> dict[str, Any]:
        research: dict[str, Any] = await workflow.execute_activity(
            RESEARCH_ACTIVITY, ResearchKnowledgeInput(
                course_id=inp.course_id, parsed_ref=inp.parsed_ref, termbase_ref=inp.termbase_ref,
                units_ref=inp.units_ref, meta_override=inp.meta_override,
                max_research_concepts=inp.max_research_concepts, max_candidates=inp.max_candidates,
                cache_bust=inp.cache_bust),
            result_type=dict, **KNOWLEDGE_OPTIONS)
        concepts: dict[str, Any] = await workflow.execute_activity(
            CONCEPT_ACTIVITY, BuildConceptLayerInput(
                course_id=inp.course_id, parsed_ref=inp.parsed_ref, outline_ref=inp.outline_ref,
                termbase_ref=inp.termbase_ref, units_ref=inp.units_ref, dossier_ref=research.get("ref"),
                require_accepted_dossier=inp.require_accepted_dossier, max_candidates=inp.max_candidates,
                max_model_concepts=inp.max_model_concepts, cache_bust=inp.cache_bust),
            result_type=dict, **KNOWLEDGE_OPTIONS)
        return {"research": research, "concepts": concepts}


def knowledge_workflow_id(course_id: str, parsed_sha256: str) -> str:
    return "b2c-book-knowledge-" + hashlib.sha256(f"{course_id}|{parsed_sha256}".encode()).hexdigest()[:24]


__all__ = ["BookKnowledgeInput", "BookKnowledgeWorkflow", "BuildConceptLayerInput", "CONCEPT_ACTIVITY", "CONCEPT_STAGE",
           "KNOWLEDGE_STAGE", "KnowledgeActivities", "RESEARCH_ACTIVITY", "ResearchKnowledgeInput", "WORKFLOW_NAME",
           "attach_gates", "front_blocks", "knowledge_workflow_id", "load_book_index", "load_parsed",
           "research_concepts", "termbase_seeds"]
