"""Figure and table descriptions for listening (D-044). New activities and workflow; nothing starts them.

    describe_figures_en      parsed book (+ source PDF for page images, optional) + the book's translation
                             units -> stage "figure-descriptions": figures.json (FigureDescriptionsDocument,
                             en), gates.json; candidate with gate ``figures.grounding`` (every figure found is
                             described, every model line passed the deterministic grounding gate)
    translate_figures        accepted EN document + approved termbase -> stage "figure-descriptions-ar":
                             figures.json (ar), translations.json; gate ``figures.translation`` (every line
                             has a translation; qa.policy decides acceptance; speech reads only descriptions
                             whose lines are all accepted)
    BuildFigureDescriptionsWorkflow  EN, then (if accepted) AR. Started only on request.

The Arabic document is made through the SAME pipeline as the book: ``UnitTranslator`` with the book's
approved termbase, the run's engine/QE profiles and the translation memory (as the episode scripts).
Speech (``GenerateSpeechV2Input.figures_ref``) and the episode scripts (``EpisodeScriptENInput.figures_ref``)
consume the accepted documents.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import ArtifactRef, FigureDescriptionsDocument, ProducerIdentity, TranslationUnit, stage_key
    from b2c_episodes import ModelRole
    from b2c_episodes.figures import (FIGURE_PROMPT_VERSION, FIGURES_GROUNDING_GATE, FIGURES_TRANSLATION_GATE,
                                      FIGURES_VERSION, GROUNDING_DEFINITION, TRANSLATION_DEFINITION,
                                      FigureCandidate, assemble_figures_arabic, collect_figures, describe_figures,
                                      figure_blocks, figure_units)
    from b2c_episodes.gates import GateOutcome
    from b2c_translation.book_pipeline import PipelineConfig, UnitTranslator
    from b2c_translation.termbase import BookTermbase

    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .knowledge import attach_gates, load_parsed
    from .promotion import NotAccepted, PromotionService
    from .retry import DEFAULT_RETRY
    from .termbase_review import approved_snapshot
    from .translate_book import draft_record

EN_STAGE = "figure-descriptions"
AR_STAGE = "figure-descriptions-ar"
EN_ACTIVITY = "describe_figures_en"
AR_ACTIVITY = "translate_figures"
WORKFLOW_NAME = "BuildFigureDescriptionsWorkflow"
AR_VERSION = "figure-descriptions-ar-2026-10-25.1"
PAGE_IMAGE_DPI = 110  # a whole-page render cropped to the figure box (Gemma 4 sees 280-1120 tokens per image)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str).encode("utf-8")


def _gates_json(outcomes: list[GateOutcome]) -> dict[str, Any]:
    return {o.gate_id: {"kind": o.kind, "passed": o.passed, "metrics": o.metrics, "definition": o.definition}
            for o in outcomes}


class FiguresENInput(BaseModel):
    course_id: str
    parsed_ref: dict[str, Any]
    units_ref: dict[str, Any]
    source_ref: dict[str, Any] | None = None  # the uploaded source PDF (page images for a vision model)
    source_name: str = "source.pdf"
    use_images: bool = False
    cache_bust: str | None = None


class FiguresARInput(BaseModel):
    course_id: str
    en_ref: dict[str, Any]
    termbase_ref: dict[str, Any]
    engine_profile: str
    qe_profile: str | None = None
    require_accepted_en: bool = True
    cache_bust: str | None = None


class BuildFiguresInput(FiguresENInput):
    termbase_ref: dict[str, Any]
    engine_profile: str
    qe_profile: str | None = None
    require_accepted_en: bool = True


def evaluate_figures_en(doc: FigureDescriptionsDocument) -> GateOutcome:
    m = doc.metrics
    figures, described = int(m.get("figures", 0)), int(m.get("described", 0))
    grounded = all(d.grounding is not None and d.grounding.passed for d in doc.descriptions)
    passed = described == figures and grounded
    return GateOutcome(FIGURES_GROUNDING_GATE, "deterministic", passed, metrics={
        "figures": figures, "described": described, "skipped": m.get("skipped"), "modes": m.get("modes"),
        "gate_failures": m.get("gate_failures"), "model_errors": m.get("model_errors"),
        "images_used": m.get("images_used"), "placed": m.get("placed"),
        "checked_tokens": sum(d.grounding.checked for d in doc.descriptions if d.grounding)},
        definition=dict(GROUNDING_DEFINITION))


def evaluate_figures_ar(doc: FigureDescriptionsDocument) -> GateOutcome:
    m = doc.metrics
    missing = list(m.get("missing") or [])
    return GateOutcome(FIGURES_TRANSLATION_GATE, "deterministic", passed=not missing, metrics={
                           "descriptions": len(doc.descriptions), "descriptions_total": m.get("descriptions_total"),
                           "descriptions_accepted": m.get("descriptions_accepted"),
                           "line_decisions": m.get("line_decisions"), "missing": missing[:20]},
                       definition=dict(TRANSLATION_DEFINITION))


class PdfPageImages:
    """Figure image = the page rendered with pypdfium2 and cropped to the figure's bounding box (PNG)."""

    def __init__(self, pdf_bytes: bytes, boxes: dict[str, tuple[int, tuple[float, float, float, float] | None]],
                 *, dpi: int = PAGE_IMAGE_DPI) -> None:
        self.pdf_bytes, self.boxes, self.dpi = pdf_bytes, boxes, dpi

    def __call__(self, fig: FigureCandidate) -> bytes | None:
        import io

        import pypdfium2 as pdfium
        block = next((b for b in fig.blocks if b.type in ("figure", "table")), None)
        if block is None or block.block_id not in self.boxes:
            return None
        page_no, bbox = self.boxes[block.block_id]
        doc = pdfium.PdfDocument(self.pdf_bytes)
        try:
            page = doc[page_no - 1]
            scale = self.dpi / 72
            image = page.render(scale=scale).to_pil()
            if bbox is not None:  # PageRef.bbox: (x0, top, x1, bottom) in points, top-left origin
                x0, top, x1, bottom = bbox
                image = image.crop((max(0, int(x0 * scale) - 8), max(0, int(top * scale) - 8),
                                    min(image.width, int(x1 * scale) + 8), min(image.height, int(bottom * scale) + 8)))
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()
        finally:
            doc.close()


class FigureActivities:
    def __init__(self, *, artifact_store: Any, generator: ModelRole | None, runtime: Any | None = None,
                 memory: Any | None = None, promotion: PromotionService | None = None,
                 clock: Callable[[], datetime] = _now) -> None:
        self.store = artifact_store
        self.generator = generator
        self.runtime = runtime
        self.memory = memory
        self.promotion = promotion
        self.clock = clock

    def _ref(self, raw: dict[str, Any]) -> ArtifactRef:
        return ArtifactRef.model_validate(raw)

    def _units(self, ref: ArtifactRef) -> list[TranslationUnit]:
        return [TranslationUnit.model_validate(u) for u in json.loads(self.store.read(ref, "units.json"))]

    # .............................................................. EN
    @activity.defn(name=EN_ACTIVITY)
    def describe(self, inp: FiguresENInput) -> dict[str, Any]:
        with activity_heartbeats(EN_ACTIVITY):
            return self._describe(inp)

    def _describe(self, inp: FiguresENInput) -> dict[str, Any]:
        parsed_ref, units_ref = self._ref(inp.parsed_ref), self._ref(inp.units_ref)
        source_ref = self._ref(inp.source_ref) if inp.source_ref and inp.use_images else None
        inputs = [parsed_ref, units_ref] + ([source_ref] if source_ref else [])
        gen = self.generator
        config = {"figures_version": FIGURES_VERSION, "prompt": FIGURE_PROMPT_VERSION,
                  "generator": gen.identity.model_id if gen else None, "images": source_ref is not None,
                  "cache_bust": inp.cache_bust}
        key = stage_key(EN_STAGE, FIGURES_VERSION, [r.sha256 for r in inputs], config)

        def compute() -> dict[str, bytes]:
            book = load_parsed(self.store, parsed_ref)
            units = self._units(units_ref)
            figures, skipped = collect_figures(figure_blocks(book), book_id=book.book_id)
            image_for = None
            if source_ref is not None:
                boxes = {b.block_id: (b.refs[0].page_no, b.refs[0].bbox) for b in book.blocks}
                image_for = PdfPageImages(self.store.read(source_ref, inp.source_name), boxes)

            def beat(fid: str) -> None:
                try:
                    activity.heartbeat({"figure": fid})
                except RuntimeError:
                    pass
            doc = describe_figures(figures, skipped=skipped, generator=gen, units=units, book_id=book.book_id,
                                   clock=self.clock, image_for=image_for, parsed_ref=parsed_ref, units_ref=units_ref,
                                   on_figure=beat)
            return {"figures.json": doc.model_dump_json(indent=1).encode("utf-8"),
                    "gates.json": _dump(_gates_json([evaluate_figures_en(doc)]))}

        ref, hit = self.store.get_or_compute(inp.course_id, EN_STAGE, key, compute)
        return self._settle(inp.course_id, ref, hit, inputs, {k: v for k, v in config.items() if k != "cache_bust"},
                            [gen.identity] if gen else [])

    def _settle(self, course_id: str, ref: ArtifactRef, hit: bool, inputs: list[ArtifactRef], config: dict[str, Any],
                identities: list[ProducerIdentity]) -> dict[str, Any]:
        gates = json.loads(self.store.read(ref, "gates.json"))
        outcomes = [GateOutcome(g, v["kind"], v["passed"], v["metrics"], v.get("definition") or {})
                    for g, v in gates.items()]
        status = None
        if self.promotion is not None:
            status = attach_gates(self.promotion, course_id, ref, outcomes, inputs=inputs, config=config,
                                  identities=identities).status
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit, "stage_output_status": status,
                "gates": {g: v["passed"] for g, v in gates.items()},
                "metrics": {g: v["metrics"] for g, v in gates.items()}}

    # .............................................................. AR
    @activity.defn(name=AR_ACTIVITY)
    def translate(self, inp: FiguresARInput) -> dict[str, Any]:
        with activity_heartbeats(AR_ACTIVITY):
            return self._translate(inp)

    def _translate(self, inp: FiguresARInput) -> dict[str, Any]:
        en_ref, tb_ref = self._ref(inp.en_ref), self._ref(inp.termbase_ref)
        if inp.require_accepted_en and self.promotion is not None:
            try:
                self.promotion.require_accepted(inp.course_id, en_ref)
            except NotAccepted as exc:
                raise ValueError(f"the English figure descriptions are not accepted: {exc}") from exc
        if self.runtime is None:
            raise ValueError("no translation runtime is configured")
        termbase = approved_snapshot(BookTermbase.from_json(self.store.read(tb_ref, "termbase.json")))
        fingerprint = self.runtime.fingerprint(inp.engine_profile, inp.qe_profile)
        config = {"ar_version": AR_VERSION, "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile,
                  "profiles": fingerprint, "cache_bust": inp.cache_bust}
        key = stage_key(AR_STAGE, AR_VERSION, [en_ref.sha256, tb_ref.sha256], config)

        def compute() -> dict[str, bytes]:
            doc = FigureDescriptionsDocument.model_validate_json(self.store.read(en_ref, "figures.json"))
            units = figure_units(doc)
            translator = UnitTranslator(
                self.runtime.engine(inp.engine_profile),
                estimator=self.runtime.estimator(inp.qe_profile, source_language="en", target_language="ar"),
                memory=self.memory, clock=self.clock, terms=termbase.locked_terms(), entities=termbase.entity_table(),
                forbidden=termbase.forbidden_variants(), config=PipelineConfig(source_language="en", target_language="ar"))
            limit = translator.config.context_chars
            results, drafts = {}, {}
            for i, (u, fid, _) in enumerate(units):
                # context: the lines of the same description only (each description stands alone)
                before = units[i - 1][0].source_text[-limit:] if i and units[i - 1][1] == fid else None
                after = units[i + 1][0].source_text[:limit] if i + 1 < len(units) and units[i + 1][1] == fid else None
                r = translator.translate(u, context_before=before, context_after=after)
                results[u.unit_id] = r.translation
                drafts[u.unit_id] = [draft_record(d) for d in r.drafts]
                try:
                    activity.heartbeat({"unit": u.unit_id})
                except RuntimeError:
                    pass
            ar = assemble_figures_arabic(doc, units, results, source_sha256=en_ref.sha256, clock=self.clock)
            return {"figures.json": ar.model_dump_json(indent=1).encode("utf-8"),
                    "translations.json": _dump([{"unit": u.model_dump(mode="json"), "figure_id": fid, "line_id": line,
                                                 "translation": results[u.unit_id].model_dump(mode="json"),
                                                 "drafts": drafts[u.unit_id]} for u, fid, line in units]),
                    "gates.json": _dump(_gates_json([evaluate_figures_ar(ar)]))}

        ref, hit = self.store.get_or_compute(inp.course_id, AR_STAGE, key, compute)
        engine = self.runtime.engine(inp.engine_profile).identity
        return self._settle(inp.course_id, ref, hit, [en_ref, tb_ref],
                            {k: v for k, v in config.items() if k != "cache_bust"},
                            [ProducerIdentity(role="engine", provider=engine.provider, model_id=engine.model_id,
                                              prompt_version=engine.prompt_version)])


def figure_activities(acts: FigureActivities) -> list[Any]:
    return [acts.describe, acts.translate]


def _retry(maximum_attempts: int) -> RetryPolicy:
    return RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0,
                       maximum_interval=timedelta(minutes=5), maximum_attempts=maximum_attempts,
                       non_retryable_error_types=sorted({*DEFAULT_RETRY.non_retryable_error_types,
                                                         "CloudProviderNotAllowed", "ValueError"}))


FIGURE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(hours=4),
                                      heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(3))


@workflow.defn(name=WORKFLOW_NAME)
class BuildFigureDescriptionsWorkflow:
    """The English descriptions, then (if accepted) their Arabic translation. Started only on request."""

    @workflow.run
    async def run(self, inp: BuildFiguresInput) -> dict[str, Any]:
        en: dict[str, Any] = await workflow.execute_activity(
            EN_ACTIVITY, FiguresENInput(**inp.model_dump(include=set(FiguresENInput.model_fields))),
            result_type=dict, **FIGURE_OPTIONS)
        if en.get("stage_output_status") != "accepted" and inp.require_accepted_en:
            return {"en": en, "ar": {"skipped": "the English descriptions are not accepted"}}
        ar: dict[str, Any] = await workflow.execute_activity(
            AR_ACTIVITY, FiguresARInput(course_id=inp.course_id, en_ref=en["ref"], termbase_ref=inp.termbase_ref,
                                        engine_profile=inp.engine_profile, qe_profile=inp.qe_profile,
                                        require_accepted_en=inp.require_accepted_en, cache_bust=inp.cache_bust),
            result_type=dict, **FIGURE_OPTIONS)
        return {"en": en, "ar": ar}


def figures_workflow_id(course_id: str, parsed_sha: str) -> str:
    return "b2c-figures-" + hashlib.sha256(f"{course_id}|{parsed_sha}".encode()).hexdigest()[:24]


__all__ = ["AR_ACTIVITY", "AR_STAGE", "BuildFigureDescriptionsWorkflow", "BuildFiguresInput", "EN_ACTIVITY", "EN_STAGE",
           "FigureActivities", "FiguresARInput", "FiguresENInput", "PdfPageImages", "WORKFLOW_NAME",
           "evaluate_figures_ar", "evaluate_figures_en", "figure_activities", "figures_workflow_id"]
