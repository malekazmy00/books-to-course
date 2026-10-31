"""CoursePipelineWorkflow: one book through the new line (docs/00-RESEARCH-CHARTER.md §4).

Each stage is its own workflow, started here as a child, so its history, retries and resume stay
where they are today; this workflow only orders them and passes each stage's outputs on:

    IngestPDFV2Workflow            ParsedBook v2 (gate ingest-v2.quality) + outline v2 (structure gate
                                   + independent cross-check, promoted automatically)
    TranslateBookWorkflow          units, termbase decided by model vote (D-033/D-050), translation
                                   judged by the escalation ladder (D-051), translator notes (D-040)
    BookKnowledgeWorkflow          knowledge research + concept layer (EN, D-034/D-035)
    BuildFigureDescriptionsWorkflow  figure/table descriptions for listening (D-044), optional
    BuildEpisodeScriptsWorkflow    episode scripts 00..n, EN then AR (D-036..D-038)

No step waits on a person. The line stops only where a stage's own gate refuses its output, and the
result says where (``stopped_at``) and why. Episode speech and MP3 encoding follow in a later
increment (status ``scripts_ready``).
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .queues import KNOWLEDGE, LIGHT, TRANSLATE_BOOK

WORKFLOW_NAME = "CoursePipelineWorkflow"
PIPELINE_QUERY = "course_pipeline_state"
# children keep their own activity retries; the child workflow itself is not re-run blindly
CHILD_OPTIONS: dict[str, Any] = dict(retry_policy=RetryPolicy(maximum_attempts=1),
                                     execution_timeout=timedelta(days=14))


class CoursePipelineInput(BaseModel):
    course_id: str = Field(min_length=1, max_length=80)
    upload: dict[str, Any]          # BookUpload (book_id, role, title, rights_status, ocr_language, ...)
    source_ref: dict[str, Any]      # the stored source PDF
    supplementary_book_ids: list[str] = []
    engine_profile: str = "gemma4-26b"
    qe_profile: str | None = "ladder-mqm"
    termbase_min_frequency: int = Field(default=1, ge=1, le=1000)
    figures: bool = True
    figure_images: bool = False
    episodes: list[int] | None = None   # None: 00 (course introduction) and every lesson episode
    meta_override: dict[str, Any] | None = None
    ingest_queue: str = LIGHT
    translate_queue: str = TRANSLATE_BOOK
    companion_queue: str = KNOWLEDGE    # knowledge, figures and episode scripts
    cache_bust: str | None = None


def course_pipeline_workflow_id(inp: CoursePipelineInput) -> str:
    raw = "|".join([inp.course_id, str(inp.upload.get("book_id", "")), str(inp.source_ref.get("sha256", "")),
                    inp.engine_profile, inp.qe_profile or "", inp.cache_bust or ""])
    return "b2c-course-pipeline-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _accepted(result: dict[str, Any] | None) -> bool:
    return bool(result) and result.get("stage_output_status") == "accepted"


@workflow.defn(name=WORKFLOW_NAME)
class CoursePipelineWorkflow:
    def __init__(self) -> None:
        self._state: dict[str, Any] = {"phase": "starting", "stages": {}}

    @workflow.query(name=PIPELINE_QUERY)
    def state(self) -> dict[str, Any]:
        return self._state

    async def _child(self, stage: str, name: str, arg: Any, queue: str) -> dict[str, Any]:
        self._state["phase"] = stage
        self._state["stages"][stage] = {"status": "running"}
        wid = f"{workflow.info().workflow_id}-{stage}"
        result: dict[str, Any] = await workflow.execute_child_workflow(name, arg, id=wid, task_queue=queue,
                                                                       result_type=dict, **CHILD_OPTIONS)
        self._state["stages"][stage] = {"status": "done", "workflow_id": wid}
        return result

    def _stop(self, stage: str, reason: str, out: dict[str, Any]) -> dict[str, Any]:
        self._state["phase"] = "stopped"
        self._state["stages"].setdefault(stage, {})["status"] = "refused"
        return {**out, "status": "stopped", "stopped_at": stage, "reason": reason}

    @workflow.run
    async def run(self, inp: CoursePipelineInput) -> dict[str, Any]:
        out: dict[str, Any] = {"course_id": inp.course_id, "book_id": inp.upload.get("book_id")}
        # 1. ingest v2 + outline v2 (automatic gates)
        ingest = await self._child("ingest", "IngestPDFV2Workflow", {
            "course_id": inp.course_id, "upload": inp.upload, "source_ref": inp.source_ref,
            "supplementary_book_ids": inp.supplementary_book_ids, "cache_bust": inp.cache_bust}, inp.ingest_queue)
        parsed = ingest.get("parsed_book") or {}
        out["parsed_ref"] = parsed.get("ref")
        if ingest.get("status") != "accepted":
            return self._stop("ingest", "the ingest-v2.quality gate did not accept the parse", out)
        outline = ingest.get("outline") or {}
        out["outline_ref"] = outline.get("ref")
        if not _accepted(outline):
            return self._stop("outline", "the outline structure gate or its cross-check did not accept it", out)

        # 2. translation: termbase by model vote, judged by the ladder, translator notes
        translated = await self._child("translate", "TranslateBookWorkflow", {
            "course_id": inp.course_id, "source_artifact_ref": parsed["ref"], "source_language": "en",
            "target_language": "ar", "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile,
            "require_approved_termbase": True, "termbase_review": "models",
            "termbase_min_frequency": inp.termbase_min_frequency}, inp.translate_queue)
        termbase_ref = (translated.get("termbase") or {}).get("approved_ref")
        notes = translated.get("translator_notes") or {}
        out.update(translation_run_id=translated.get("run_id"), units_ref=translated.get("units_ref"),
                   termbase_ref=termbase_ref, notes_ref=notes.get("artifact"))
        if notes.get("status") != "accepted":
            return self._stop("translate", "the translator-notes coverage gate did not accept the run", out)

        # 3. knowledge research + concept layer (EN)
        knowledge = await self._child("knowledge", "BookKnowledgeWorkflow", {
            "course_id": inp.course_id, "parsed_ref": parsed["ref"], "outline_ref": outline["ref"],
            "termbase_ref": termbase_ref, "units_ref": translated.get("units_ref"),
            "meta_override": inp.meta_override, "cache_bust": inp.cache_bust}, inp.companion_queue)
        concepts, research = knowledge.get("concepts") or {}, knowledge.get("research") or {}
        out.update(concept_ref=concepts.get("ref"), dossier_ref=research.get("ref"))
        if not _accepted(concepts):
            return self._stop("knowledge", "the concept layer was not accepted by its gates", out)

        # 4. figure descriptions for listening (optional)
        figures_en_ref = None
        if inp.figures:
            figures = await self._child("figures", "BuildFigureDescriptionsWorkflow", {
                "course_id": inp.course_id, "parsed_ref": parsed["ref"], "units_ref": translated.get("units_ref"),
                "source_ref": inp.source_ref, "use_images": inp.figure_images, "termbase_ref": termbase_ref,
                "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile,
                "cache_bust": inp.cache_bust}, inp.companion_queue)
            en, ar = figures.get("en") or {}, figures.get("ar") or {}
            figures_en_ref = en.get("ref") if _accepted(en) else None
            out.update(figures_en_ref=figures_en_ref, figures_ar_ref=ar.get("ref") if _accepted(ar) else None)
            self._state["stages"]["figures"].update(en=en.get("stage_output_status"),
                                                   ar=ar.get("stage_output_status"))

        # 5. episode scripts 00..n
        episodes = inp.episodes if inp.episodes is not None else list(range(int(outline.get("lesson_count", 0)) + 1))
        scripts = await self._child("episodes", "BuildEpisodeScriptsWorkflow", {
            "course_id": inp.course_id, "parsed_ref": parsed["ref"], "outline_ref": outline["ref"],
            "units_ref": translated.get("units_ref"), "concept_ref": concepts["ref"],
            "dossier_ref": research.get("ref"), "figures_ref": figures_en_ref,
            "translation_run_id": translated.get("run_id"), "episodes": episodes, "termbase_ref": termbase_ref,
            "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile,
            "cache_bust": inp.cache_bust}, inp.companion_queue)
        per_episode = scripts.get("episodes") or {}
        accepted = sorted(k for k, v in per_episode.items() if _accepted((v or {}).get("ar")))
        self._state["stages"]["episodes"].update(requested=len(episodes), accepted=len(accepted))
        out.update(episodes=per_episode, episodes_accepted=accepted)
        self._state["phase"] = "done"
        return {**out, "status": "scripts_ready", "stopped_at": None}


__all__ = ["CoursePipelineInput", "CoursePipelineWorkflow", "PIPELINE_QUERY", "WORKFLOW_NAME",
           "course_pipeline_workflow_id"]
