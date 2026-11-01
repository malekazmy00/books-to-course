"""CoursePipelineWorkflow: one book through the new line (docs/00-RESEARCH-CHARTER.md §4).

Each stage is its own workflow, started here as a child, so its history, retries and resume stay
where they are; this workflow orders them as the decisions do and passes each stage's outputs on:

    IngestPDFV2Workflow            ParsedBook v2 + outline v2; a refused parse continues with its
                                   findings (D-052)
    prepare_translation_units      the book's translation units (activity of the translate-book queue)
    BookKnowledgeWorkflow          knowledge research + concept layer in the book's language, BEFORE
                                   translation (D-034, D-035), from the units (no termbase yet)
    TranslateBookWorkflow          termbase by model vote with known Arabic equivalents (D-033/D-034/
                                   D-050), translation judged by the ladder (D-051), translator notes (D-040)
    language_research              Arabic language research AFTER translation (D-034, §11b): an honest
                                   status per term and a "read in Arabic" list
    BuildFigureDescriptionsWorkflow  figure/table descriptions for listening (D-044), optional
    BuildEpisodeScriptsWorkflow    episode scripts 00..n, EN then AR (D-036..D-038); missing companion
                                   parts are declared, the book text is never withheld (D-052)
    GenerateSpeechV2Workflow       x episode: the audio comes out lesson by lesson, each episode from its
                                   own Arabic script (notes spoken, untranslated companion lines dropped)
    EncodeEpisodeMp3Workflow       one tagged MP3 per episode, 00..n (D-038, D-042, D-044)

No step waits on a person, and a refused gate never stops the book (D-033, D-052): the line goes on
with what is available and records each refusal in ``findings`` (stage + finding) so it becomes
knowledge (notes, declared deficits) rather than a silent loss. Only a stage that produced nothing at
all (no parse, no translation run) ends the run, with ``status="failed"`` and the reason.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from .language_research import ACTIVITY as LANGUAGE_ACTIVITY
    from .language_research import LANGUAGE_OPTIONS, LanguageResearchInput
    from .queues import AUDIO, KNOWLEDGE, LIGHT, TRANSLATE_BOOK
    from .translate_book import PREPARE_OPTIONS, PrepareUnitsInput, TranslateBookInput, translation_run_id

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
    speech: bool = True                 # speech and MP3 per episode
    require_intelligibility: bool = True
    ingest_queue: str = LIGHT
    translate_queue: str = TRANSLATE_BOOK
    companion_queue: str = KNOWLEDGE    # knowledge, figures and episode scripts
    audio_queue: str = AUDIO            # speech and MP3 encoding
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
        self._state: dict[str, Any] = {"phase": "starting", "stages": {}, "findings": []}

    @workflow.query(name=PIPELINE_QUERY)
    def state(self) -> dict[str, Any]:
        return self._state

    def _finding(self, stage: str, finding: str) -> None:
        self._state["findings"].append({"stage": stage, "finding": finding})

    async def _child(self, stage: str, name: str, arg: Any, queue: str) -> dict[str, Any] | None:
        """Run one stage; a stage that fails outright is recorded and returns None (never re-raised)."""
        self._state["phase"] = stage
        self._state["stages"][stage] = {"status": "running"}
        wid = f"{workflow.info().workflow_id}-{stage}"
        try:
            result: dict[str, Any] = await workflow.execute_child_workflow(
                name, arg, id=wid, task_queue=queue, result_type=dict, **CHILD_OPTIONS)
        except ChildWorkflowError as exc:
            cause = exc.cause.__class__.__name__ if exc.cause is not None else "ChildWorkflowError"
            self._state["stages"][stage] = {"status": "failed", "workflow_id": wid, "error": cause}
            self._finding(stage, f"stage_failed:{cause}")
            return None
        self._state["stages"][stage] = {"status": "done", "workflow_id": wid}
        return result

    def _end(self, out: dict[str, Any], status: str, reason: str | None = None) -> dict[str, Any]:
        self._state["phase"] = "done" if status != "failed" else "failed"
        return {**out, "status": status, "reason": reason, "findings": list(self._state["findings"])}

    @workflow.run
    async def run(self, inp: CoursePipelineInput) -> dict[str, Any]:
        out: dict[str, Any] = {"course_id": inp.course_id, "book_id": inp.upload.get("book_id")}

        # 1. ingest v2 + outline v2 — a refused parse continues with its findings (D-052)
        ingest = await self._child("ingest", "IngestPDFV2Workflow", {
            "course_id": inp.course_id, "upload": inp.upload, "source_ref": inp.source_ref,
            "supplementary_book_ids": inp.supplementary_book_ids, "cache_bust": inp.cache_bust,
            "continue_on_findings": True}, inp.ingest_queue)
        parsed = (ingest or {}).get("parsed_book") or {}
        if not parsed.get("ref"):
            return self._end(out, "failed", "the book could not be parsed at all")
        out["parsed_ref"] = parsed["ref"]
        for finding in (ingest or {}).get("findings") or []:
            self._finding("ingest", finding)
        outline = (ingest or {}).get("outline") or {}
        out["outline_ref"] = outline.get("ref")
        if not outline.get("ref"):
            self._finding("outline", "outline_missing")
        elif not _accepted(outline):
            self._finding("outline", "outline_not_accepted")

        # 2. the translation units (the same run id the translation will use)
        book = TranslateBookInput(course_id=inp.course_id, source_artifact_ref=parsed["ref"], source_language="en",
                                  target_language="ar", engine_profile=inp.engine_profile, qe_profile=inp.qe_profile,
                                  require_approved_termbase=True, termbase_review="models",
                                  termbase_min_frequency=inp.termbase_min_frequency)
        self._state["phase"] = "prepare"
        prepared: dict[str, Any] = await workflow.execute_activity(
            "prepare_translation_units", PrepareUnitsInput(run_id=translation_run_id(book), inp=book),
            task_queue=inp.translate_queue, result_type=dict, **PREPARE_OPTIONS)
        units_ref = prepared["units_ref"]
        out["units_ref"] = units_ref

        # 3. knowledge research + concept layer in the book's language, BEFORE translation (D-034)
        concepts: dict[str, Any] = {}
        research: dict[str, Any] = {}
        if outline.get("ref"):
            knowledge = await self._child("knowledge", "BookKnowledgeWorkflow", {
                "course_id": inp.course_id, "parsed_ref": parsed["ref"], "outline_ref": outline["ref"],
                "units_ref": units_ref, "meta_override": inp.meta_override, "require_accepted_dossier": False,
                "cache_bust": inp.cache_bust}, inp.companion_queue)
            concepts, research = (knowledge or {}).get("concepts") or {}, (knowledge or {}).get("research") or {}
            if knowledge is not None and not _accepted(research):
                self._finding("knowledge", "research_not_accepted")
            if knowledge is not None and not _accepted(concepts):
                self._finding("knowledge", "concepts_not_accepted")
        out.update(concept_ref=concepts.get("ref"), dossier_ref=research.get("ref"))

        # 4. translation: termbase by model vote, judged by the ladder, translator notes
        translated = await self._child("translate", "TranslateBookWorkflow", book.model_dump(mode="json"),
                                       inp.translate_queue)
        if translated is None:
            return self._end(out, "failed", "the translation run failed")
        termbase_ref = (translated.get("termbase") or {}).get("approved_ref")
        notes = translated.get("translator_notes") or {}
        out.update(translation_run_id=translated.get("run_id"), termbase_ref=termbase_ref,
                   notes_ref=notes.get("artifact"))
        if notes.get("status") != "accepted":
            self._finding("translate", "translator_notes_not_accepted")

        # 5. language research in Arabic, after translation (D-034, learning-companion §11b)
        if termbase_ref:
            self._state["phase"] = "language"
            try:
                language: dict[str, Any] = await workflow.execute_activity(
                    LANGUAGE_ACTIVITY, LanguageResearchInput(course_id=inp.course_id, termbase_ref=termbase_ref),
                    task_queue=inp.companion_queue, result_type=dict, **LANGUAGE_OPTIONS)
                out["language_research_ref"] = language.get("ref")
                self._state["stages"]["language"] = {"status": "done", "metrics": language.get("metrics")}
            except ActivityError as exc:
                self._finding("language", f"language_research_failed:{exc.cause.__class__.__name__}")
        else:
            self._finding("language", "no_approved_termbase")

        # 6. figure descriptions for listening (optional)
        figures_en_ref = None
        if inp.figures:
            figures = await self._child("figures", "BuildFigureDescriptionsWorkflow", {
                "course_id": inp.course_id, "parsed_ref": parsed["ref"], "units_ref": units_ref,
                "source_ref": inp.source_ref, "use_images": inp.figure_images, "termbase_ref": termbase_ref,
                "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile,
                "cache_bust": inp.cache_bust}, inp.companion_queue)
            en, ar = (figures or {}).get("en") or {}, (figures or {}).get("ar") or {}
            figures_en_ref = en.get("ref") if _accepted(en) else None
            out.update(figures_en_ref=figures_en_ref, figures_ar_ref=ar.get("ref") if _accepted(ar) else None)
            if figures is not None and not _accepted(en):
                self._finding("figures", "figure_descriptions_not_accepted")

        # 7. episode scripts 00..n — every input that exists is used; what is missing is declared
        if not (outline.get("ref") and concepts.get("ref")):
            return self._end(out, "translated", "no outline or concept layer: episodes cannot be built")
        strict = _accepted(outline) and _accepted(concepts)
        episodes = inp.episodes if inp.episodes is not None else list(range(int(outline.get("lesson_count", 0)) + 1))
        scripts = await self._child("episodes", "BuildEpisodeScriptsWorkflow", {
            "course_id": inp.course_id, "parsed_ref": parsed["ref"], "outline_ref": outline["ref"],
            "units_ref": units_ref, "concept_ref": concepts["ref"], "dossier_ref": research.get("ref"),
            "figures_ref": figures_en_ref, "translation_run_id": translated.get("run_id"), "episodes": episodes,
            "termbase_ref": termbase_ref, "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile,
            "require_accepted_inputs": strict, "cache_bust": inp.cache_bust}, inp.companion_queue)
        per_episode = (scripts or {}).get("episodes") or {}
        accepted = sorted(k for k, v in per_episode.items() if _accepted((v or {}).get("ar")))
        missing = sorted(f"{e:02d}" for e in episodes if f"{e:02d}" not in accepted)
        for label in missing:
            self._finding("episodes", f"episode_{label}_not_accepted")
        if "episodes" in self._state["stages"]:
            self._state["stages"]["episodes"].update(requested=len(episodes), accepted=len(accepted))
        out.update(episodes=per_episode, episodes_accepted=accepted)
        if not inp.speech:
            return self._end(out, "scripts_ready")

        # 8. speech, episode by episode, then one MP3 per episode (D-036..D-038, D-042)
        last_episode = max(episodes) if episodes else 0
        encode: list[dict[str, Any]] = []
        for label in sorted(per_episode):
            ar = (per_episode[label] or {}).get("ar") or {}
            if not ar.get("ref"):
                self._finding("speech", f"episode_{label}_has_no_arabic_script")
                continue
            spoken = await self._child(f"speech-{label}", "GenerateSpeechV2Workflow", {
                "course_id": inp.course_id, "episode_ref": ar["ref"], "termbase_ref": termbase_ref,
                "notes_ref": notes.get("artifact"), "require_intelligibility": inp.require_intelligibility,
                "activity_task_queue": inp.audio_queue}, inp.audio_queue)
            tracks = [t for t in (spoken or {}).get("tracks") or [] if t.get("artifact")]
            for t in tracks:
                if t.get("status") != "accepted":
                    self._finding("speech", f"episode_{label}_track_{t.get('track_id')}_{t.get('status')}")
            if not tracks:
                self._finding("speech", f"episode_{label}_no_track:{(spoken or {}).get('status')}")
                continue
            encode.append({"course_id": inp.course_id, "track_refs": [t["artifact"] for t in tracks],
                           "episode": int(label), "last_episode": last_episode,
                           "title": ar.get("title") or f"{int(label):02d}",
                           "book_title": ar.get("book_title") or str(inp.upload.get("title") or out["book_id"]),
                           # a track its gates did not accept is still the lesson; it is recorded above
                           "require_accepted_tracks": False})
        if not encode:
            return self._end(out, "scripts_ready", "no episode produced audio")
        mp3 = await self._child("mp3", "EncodeEpisodeMp3Workflow",
                                {"episodes": encode, "activity_task_queue": inp.audio_queue}, inp.audio_queue)
        out["mp3"] = mp3
        for item in (mp3 or {}).get("episodes") or []:
            if item.get("status") != "accepted":
                self._finding("mp3", f"episode_{int(item.get('episode', -1)):02d}_mp3_{item.get('status')}")
        return self._end(out, "audio_ready" if mp3 else "scripts_ready")


__all__ = ["CoursePipelineInput", "CoursePipelineWorkflow", "PIPELINE_QUERY", "WORKFLOW_NAME",
           "course_pipeline_workflow_id"]
