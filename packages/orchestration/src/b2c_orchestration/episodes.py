"""Episode scripts: the derived-formats stage (D-032, D-035..D-038; learning-companion §12–§15).

New activities and a new workflow; nothing starts them automatically.

    build_episode_script_en    parsed book + outline + book translation units + concept layer (+ accepted
                               dossier) -> stage "episode-script-en": script.json (EpisodeScript, en),
                               report.json, gates.json; promotion candidate with gates episodes.grounding,
                               episodes.validation, episodes.length (+ episodes.coverage for lessons).
    translate_episode_script   accepted EN script + approved termbase + the book's translation run ->
                               stage "episode-script-ar": script.json (EpisodeScript, ar), translations.json
                               (every script unit's UnitTranslation and drafts), gates.json; gates
                               episodes.translation and episodes.first-mention.
    BuildEpisodeScriptsWorkflow  per requested episode: EN script, then (if accepted) the AR script.

The Arabic script is made through the SAME translation pipeline as the book: ``UnitTranslator`` with
the book's approved termbase (locked terms, entities, forbidden variants), the run's engine and QE
profiles and the translation memory; decisions follow ``qa.policy`` exactly as for book units. The
book text is never translated again: its lines are the accepted results of the book's own run. The
original term is then inserted at its first mention in the episode (D-032 principle 3), over book
text and script lines alike, for the approved locked terms the concept layer lists as first
mentioned in the lesson; the QA checks are re-run with those glosses explicitly whitelisted.

Resumable per episode: both activities are keyed by their inputs (``stage_key``) and an existing
artifact is reused; within an episode, model calls are cached by the gateway (temperature 0) and,
with a result store, finished units are not translated again.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from b2c_concepts import BookIndex
    from b2c_contracts import (ArtifactRef, ConceptLayer, CourseOutlineV2, EpisodeScript, KnowledgeDossier, ParsedBookV2,
                               ProducerIdentity, TranslationUnit, UnitTranslation, stage_key)
    from b2c_episodes import (EPISODES_VERSION, BookLine, BookTextRef, CourseIntroPlan, EpisodeGenerationFailed,
                              EpisodePlan, LessonSource, ModelRole, SourceBlock, TermInfo, assemble_arabic,
                              check_grounding, coverage, evaluate_en_script, generate_course_intro,
                              generate_lesson_episode, script_units, with_texts)
    from b2c_episodes.gates import GateOutcome
    from b2c_episodes.generate import (INTRO_PROMPT_VERSION, JUDGE_PROMPT_VERSION, LESSON_PROMPT_VERSION,
                                       RECAP_PROMPT_VERSION)
    from b2c_translation.book_pipeline import PipelineConfig, UnitTranslator
    from b2c_translation.first_mention import FirstMentionTerm, first_mention_status, insert_first_mentions
    from b2c_translation.qa import CheckContext
    from b2c_translation.qa.checks import run_checks
    from b2c_translation.termbase import BookTermbase

    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .knowledge import attach_gates, load_book_index, load_parsed
    from .promotion import NotAccepted, PromotionService
    from .retry import DEFAULT_RETRY
    from .termbase_review import approved_snapshot
    from .translate_book import draft_record

EN_STAGE = "episode-script-en"
AR_STAGE = "episode-script-ar"
EN_ACTIVITY = "build_episode_script_en"
AR_ACTIVITY = "translate_episode_script"
WORKFLOW_NAME = "BuildEpisodeScriptsWorkflow"
AR_VERSION = "episode-scripts-ar-2026-10-25.3"
TRANSLATION_GATE_ID = "episodes.translation"
FIRST_MENTION_GATE_ID = "episodes.first-mention"
TRANSLATION_DEFINITION: dict[str, Any] = {"script_lines": "every line accepted (qa.policy)",
                                          "book_text": "every non-dropped lesson unit accepted in the book run",
                                          "max_missing_lines": 0}
FIRST_MENTION_DEFINITION: dict[str, Any] = {
    "terms": "approved locked terms first mentioned in the lesson (ConceptLayer)",
    "rule": "first occurrence in the episode's Arabic text is followed by (original)", "idempotent": True,
    "qa_whitelist": "no foreign_script/untranslated_english issue added by the inserted glosses"}
FRONT_TITLE = re.compile(r"foreword|preface|acknowledg|introduction to the book", re.I)
NOT_SPOKEN_KINDS = frozenset({"noise", "toc", "index", "reference"})


class EpisodeRefs(BaseModel):
    course_id: str
    parsed_ref: dict[str, Any]
    outline_ref: dict[str, Any]
    units_ref: dict[str, Any]          # the book's translation-units artifact
    concept_ref: dict[str, Any]        # accepted concept-layer artifact
    dossier_ref: dict[str, Any] | None = None
    translation_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class EpisodeScriptENInput(EpisodeRefs):
    episode: int = Field(ge=0)
    require_accepted_inputs: bool = True
    cache_bust: str | None = None


class EpisodeScriptARInput(EpisodeRefs):
    episode: int = Field(ge=0)
    en_ref: dict[str, Any]
    termbase_ref: dict[str, Any]       # approved termbase (stage book-termbase-approved)
    engine_profile: str
    qe_profile: str | None = None
    require_accepted_en: bool = True
    cache_bust: str | None = None


class BuildEpisodeScriptsInput(EpisodeRefs):
    episodes: list[int] = Field(min_length=1)
    termbase_ref: dict[str, Any]
    engine_profile: str
    qe_profile: str | None = None
    require_accepted_inputs: bool = True
    cache_bust: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str).encode("utf-8")


def _gates_json(outcomes: Sequence[Any]) -> dict[str, Any]:
    return {o.gate_id: {"kind": o.kind, "passed": o.passed, "metrics": o.metrics, "definition": o.definition}
            for o in outcomes}


# ------------------------------------------------------------------ the book, as the generators see it
class BookView:
    """Lessons in reading order with their episode numbers, blocks and book translation units."""

    def __init__(self, index: BookIndex, units: Sequence[TranslationUnit], *, title: str,
                 episodes: Mapping[str, int] | None = None, front: set[str] | None = None) -> None:
        self.index = index
        self.title = title
        self.units = list(units)
        self.lessons = list(index.lessons)
        self.episode_of = {lesson.lesson_id: (episodes or {}).get(lesson.lesson_id, i + 1)
                           for i, lesson in enumerate(self.lessons)}
        self.front = front if front is not None else {
            lesson.lesson_id for lesson in self.lessons if FRONT_TITLE.search(re.sub(r"\s+", "", lesson.title))
            or FRONT_TITLE.search(lesson.title)}
        self.block_lesson = {b.block_id: index.lesson_of[b.idx].lesson_id for b in index.blocks if b.idx in index.lesson_of}

    def lesson(self, episode: int):
        for lesson in self.lessons:
            if self.episode_of[lesson.lesson_id] == episode:
                return lesson
        raise KeyError(f"no lesson for episode {episode}")

    def source(self, lesson) -> LessonSource:
        blocks = tuple(SourceBlock(b.block_id, b.type, b.text, b.pages) for b in self.index.lesson_blocks()
                       if self.index.lesson_of.get(b.idx) is lesson)
        return LessonSource(lesson.lesson_id, self.episode_of[lesson.lesson_id], audio_title(lesson.title), blocks)

    def book_text(self, lesson, positions: Mapping[str, int]) -> BookTextRef:
        units = [u for u in self.units if u.kind not in NOT_SPOKEN_KINDS and u.block_ids[0] in positions
                 and lesson.start <= positions[u.block_ids[0]] <= lesson.end]
        blocks = list(dict.fromkeys(b for u in units for b in u.block_ids))
        return BookTextRef(block_ids=tuple(blocks), unit_ids=tuple(u.unit_id for u in units))


def audio_title(title: str) -> str:
    """One line, without the chapter/section numbering (as ``LessonV2.episode_title``, D-036 (3))."""
    clean = " ".join(title.replace("™", "").replace("®", "").split())
    return re.sub(r"^(?:chapter\s+)?\d+(?:\.\d+)*\.?\s+", "", clean, flags=re.I) or clean


def term_infos(layer: ConceptLayer, lesson_id: str) -> list[TermInfo]:
    entries = {x.concept_id: x for x in layer.concepts}
    lesson = next((x for x in layer.lessons if x.lesson_id == lesson_id), None)
    out = []
    for m in lesson.first_mentions if lesson else []:
        x = entries[m.concept_id]
        d = x.author_definition
        out.append(TermInfo(concept_id=x.concept_id, term=x.term, kind=x.kind, approved=x.termbase_status == "approved",
                            definition=d.spans[0] if d else None, gloss=d.gloss if d else None,
                            introduced_at=x.introduced_at,
                            external=tuple((n.claim_id, n.statement, tuple(n.urls)) for n in x.external_notes)))
    return out


# ------------------------------------------------------------------ AR gates (deterministic)
def evaluate_ar_script(script: EpisodeScript, *, units: Mapping[str, TranslationUnit],
                       book_decisions: Mapping[str, str], terms: Sequence[FirstMentionTerm],
                       pre_insertion: Mapping[str, str]) -> list[GateOutcome]:
    lines = [x for x in script.all_lines() if not all(src.kind == "outline" for src in x.sources)]
    generated = [x for s in script.sections if s.kind not in ("book_text", "figure_description") for x in s.lines]
    statuses: dict[str, int] = {}
    for x in generated:
        statuses[x.status or "none"] = statuses.get(x.status or "none", 0) + 1
    book_units = [u for s in script.sections if s.kind in ("book_text", "figure_description") for u in s.unit_ids]
    book_bad = [u for u in book_units if book_decisions.get(u) not in ("accepted", "dropped")]
    missing = list(script.metrics.get("missing_lines") or [])
    translation = GateOutcome(TRANSLATION_GATE_ID, "deterministic", passed=bool(generated)
                              and statuses.get("accepted", 0) == len(generated) and not book_bad and not missing,
                              metrics={"script_lines": len(generated), "decisions": statuses,
                                       "accepted_rate": round(statuses.get("accepted", 0) / len(generated), 4)
                                       if generated else 0.0, "book_units": len(book_units),
                                       "book_units_not_accepted": book_bad[:20], "missing_lines": missing[:20]},
                              definition=dict(TRANSLATION_DEFINITION))
    texts = [x.text for x in lines]
    state = first_mention_status(texts, terms)
    found = sum(1 for v in state.values() if v != "absent")
    glossed = sum(1 for v in state.values() if v == "glossed")
    not_glossed = [k for k, v in state.items() if v == "not_glossed"]
    again = insert_first_mentions(texts, terms)
    idempotent = again.texts == texts
    new_issues: list[str] = []
    for x in lines:
        if not x.inserted_originals or x.line_id not in pre_insertion:
            continue
        unit = units.get(x.unit_ids[0])
        if unit is None:
            continue
        before = {i.check_id for i in run_checks(CheckContext(unit=unit, target=pre_insertion[x.line_id]))}
        after = run_checks(CheckContext(unit=unit, target=x.text, glossed_originals=frozenset(x.inserted_originals)))
        new_issues += [f"{x.line_id}:{i.check_id}" for i in after if i.check_id not in before
                       and i.category in ("foreign_script", "untranslated_english")]
    inserted = sum(len(x.inserted_originals) for x in lines)
    first = GateOutcome(FIRST_MENTION_GATE_ID, "deterministic", passed=idempotent and not not_glossed and not new_issues,
                        metrics={"terms": len(terms), "found_in_text": found, "glossed_at_first_mention": glossed,
                                 "not_glossed": not_glossed[:20], "inserted": inserted, "idempotent": idempotent,
                                 "new_qa_issues_from_glosses": new_issues[:20],
                                 "inserted_in_book_text": sum(len(x.inserted_originals) for s in script.sections
                                                              if s.kind == "book_text" for x in s.lines)},
                        definition=dict(FIRST_MENTION_DEFINITION))
    return [translation, first]


# ------------------------------------------------------------------ activities
class EpisodeActivities:
    def __init__(self, *, artifact_store: Any, generator: ModelRole, judge: ModelRole, runtime: Any | None = None,
                 memory: Any | None = None, book_results: Callable[[str], Mapping[str, UnitTranslation]] | None = None,
                 promotion: PromotionService | None = None, clock: Callable[[], datetime] = _now,
                 workers: int = 3) -> None:
        if generator.family == judge.family:
            raise ValueError("the validator must be from a different model family than the generator")
        self.store = artifact_store
        self.generator = generator
        self.judge = judge
        self.runtime = runtime
        self.memory = memory
        self.book_results = book_results
        self.promotion = promotion
        self.clock = clock
        self.workers = workers

    # .............................................................. inputs
    def _ref(self, raw: Mapping[str, Any]) -> ArtifactRef:
        return ArtifactRef.model_validate(raw)

    def _accepted(self, course_id: str, ref: ArtifactRef, require: bool) -> bool:
        if not require or self.promotion is None:
            return True
        try:
            self.promotion.require_accepted(course_id, ref)
            return True
        except NotAccepted:
            return False

    def _units(self, ref: ArtifactRef) -> list[TranslationUnit]:
        return [TranslationUnit.model_validate(u) for u in json.loads(self.store.read(ref, "units.json"))]

    def _view(self, inp: EpisodeRefs) -> tuple[BookView, dict[str, int]]:
        parsed_ref, outline_ref = self._ref(inp.parsed_ref), self._ref(inp.outline_ref)
        index = load_book_index(self.store, parsed_ref, outline_ref)
        book = load_parsed(self.store, parsed_ref)
        positions = {b.block_id: i for i, b in enumerate(book.blocks)}
        episodes, front = None, None
        if "course-outline-v2.json" in set(self.store.list_files(outline_ref)) and isinstance(book, ParsedBookV2):
            outline = CourseOutlineV2.model_validate_json(self.store.read(outline_ref, "course-outline-v2.json"))
            episodes = {x.lesson_id: x.episode for x in outline.lessons if x.episode is not None} or None
            front = {x.lesson_id for x in outline.lessons if x.kind == "front_matter"}
        units = self._units(self._ref(inp.units_ref))
        return BookView(index, units, title=book.title, episodes=episodes, front=front), positions

    def _dossier(self, inp: EpisodeRefs, require: bool) -> tuple[KnowledgeDossier | None, ArtifactRef | None]:
        if not inp.dossier_ref:
            return None, None
        ref = self._ref(inp.dossier_ref)
        if not self._accepted(inp.course_id, ref, require):
            return None, None
        return KnowledgeDossier.model_validate_json(self.store.read(ref, "dossier.json")), ref

    # .............................................................. EN
    @activity.defn(name=EN_ACTIVITY)
    def build_en(self, inp: EpisodeScriptENInput) -> dict[str, Any]:
        with activity_heartbeats(EN_ACTIVITY):
            return self._build_en(inp)

    def _build_en(self, inp: EpisodeScriptENInput) -> dict[str, Any]:
        concept_ref = self._ref(inp.concept_ref)
        if not self._accepted(inp.course_id, concept_ref, inp.require_accepted_inputs):
            raise ValueError("the concept layer is not an accepted output")
        dossier, dossier_ref = self._dossier(inp, inp.require_accepted_inputs)
        inputs = [self._ref(inp.parsed_ref), self._ref(inp.outline_ref), self._ref(inp.units_ref), concept_ref] + (
            [dossier_ref] if dossier_ref else [])
        config = {"episodes_version": EPISODES_VERSION, "episode": inp.episode, "run_id": inp.translation_run_id,
                  "generator": self.generator.identity.model_id, "judge": self.judge.identity.model_id,
                  "dossier": dossier_ref is not None, "cache_bust": inp.cache_bust,
                  "prompts": [LESSON_PROMPT_VERSION, RECAP_PROMPT_VERSION, INTRO_PROMPT_VERSION, JUDGE_PROMPT_VERSION]}
        key = stage_key(EN_STAGE, EPISODES_VERSION, [r.sha256 for r in inputs], config)
        view, positions = self._view(inp)
        layer = ConceptLayer.model_validate_json(self.store.read(concept_ref, "concept-layer.json"))
        block_text = {b.block_id: b.text for b in view.index.blocks}
        lesson_of = view.block_lesson
        title = (dossier.title if dossier else None) or view.title

        def compute() -> dict[str, bytes]:
            try:
                if inp.episode == 0:
                    plan = CourseIntroPlan(book_id=view.index.book_id, book_title=title,
                                           authors=list(dossier.authors) if dossier else [], dossier=dossier,
                                           front_lessons=[view.source(x) for x in view.lessons if x.lesson_id in view.front],
                                           episode_titles=[(view.episode_of[x.lesson_id], x.title) for x in view.lessons],
                                           terms_total=len(layer.concepts))
                    script, report = generate_course_intro(plan, generator=self.generator, judge=self.judge,
                                                           clock=self.clock, workers=self.workers)
                    cov = None
                    allowed = set(view.front)
                else:
                    lesson = view.lesson(inp.episode)
                    previous = [view.source(x) for x in view.lessons if view.episode_of[x.lesson_id] < inp.episode]
                    plan = EpisodePlan(book_id=view.index.book_id, book_title=title, lesson=view.source(lesson),
                                       previous=previous, terms=term_infos(layer, lesson.lesson_id),
                                       book_text=view.book_text(lesson, positions),
                                       translation_run_id=inp.translation_run_id,
                                       last_episode=max(view.episode_of.values()), dossier=dossier)
                    script, report = generate_lesson_episode(plan, generator=self.generator, judge=self.judge,
                                                             clock=self.clock, workers=self.workers)
                    cov = coverage(plan, script)
                    allowed = None
            except EpisodeGenerationFailed as exc:
                outcomes = [GateOutcome(g, "deterministic", False, {"error": str(exc)})
                            for g in ("episodes.grounding", "episodes.validation", "episodes.length")]
                return {"report.json": _dump({**exc.report, "failed": str(exc)}), "gates.json": _dump(_gates_json(outcomes))}
            grounding = check_grounding(script, block_text=block_text, lesson_of=lesson_of,
                                        episode_of_lesson=view.episode_of, allowed_lessons=allowed, dossier=dossier,
                                        concept_ids={x.concept_id for x in layer.concepts})
            outcomes = evaluate_en_script(script, grounding=grounding, coverage=cov)
            return {"script.json": script.model_dump_json(indent=1).encode("utf-8"),
                    "report.json": _dump({**report, "coverage": cov, "grounding": grounding}),
                    "gates.json": _dump(_gates_json(outcomes))}

        ref, hit = self.store.get_or_compute(inp.course_id, EN_STAGE, key, compute)
        gates = json.loads(self.store.read(ref, "gates.json"))
        outcomes = [GateOutcome(g, v["kind"], v["passed"], v["metrics"], v.get("definition") or {}) for g, v in gates.items()]
        status = None
        if self.promotion is not None:
            status = attach_gates(self.promotion, inp.course_id, ref, outcomes, inputs=inputs,
                                  config={k: v for k, v in config.items() if k != "cache_bust"},
                                  identities=[self.generator.identity, self.judge.identity]).status
        return {"episode": inp.episode, "ref": ref.model_dump(mode="json"), "cache_hit": hit,
                "stage_output_status": status, "has_script": "script.json" in set(self.store.list_files(ref)),
                "gates": {g: v["passed"] for g, v in gates.items()}}

    # .............................................................. AR
    @activity.defn(name=AR_ACTIVITY)
    def translate(self, inp: EpisodeScriptARInput) -> dict[str, Any]:
        with activity_heartbeats(AR_ACTIVITY):
            return self._translate(inp)

    def _translator(self, inp: EpisodeScriptARInput, termbase: BookTermbase) -> UnitTranslator:
        if self.runtime is None:
            raise ValueError("no translation runtime is configured")
        return UnitTranslator(self.runtime.engine(inp.engine_profile),
                              estimator=self.runtime.estimator(inp.qe_profile, source_language="en", target_language="ar"),
                              memory=self.memory, clock=self.clock, terms=termbase.locked_terms(),
                              entities=termbase.entity_table(), forbidden=termbase.forbidden_variants(),
                              config=PipelineConfig(source_language="en", target_language="ar"))

    def _translate(self, inp: EpisodeScriptARInput) -> dict[str, Any]:
        en_ref, tb_ref, concept_ref = self._ref(inp.en_ref), self._ref(inp.termbase_ref), self._ref(inp.concept_ref)
        if not self._accepted(inp.course_id, en_ref, inp.require_accepted_en):
            raise ValueError("the English episode script is not an accepted output")
        termbase = approved_snapshot(BookTermbase.from_json(self.store.read(tb_ref, "termbase.json")))
        fingerprint = self.runtime.fingerprint(inp.engine_profile, inp.qe_profile) if self.runtime else ""
        inputs = [en_ref, tb_ref, concept_ref, self._ref(inp.units_ref)]
        config = {"ar_version": AR_VERSION, "episode": inp.episode, "run_id": inp.translation_run_id,
                  "engine_profile": inp.engine_profile, "qe_profile": inp.qe_profile, "profiles": fingerprint,
                  "cache_bust": inp.cache_bust}
        book_results = dict(self.book_results(inp.translation_run_id)) if self.book_results else {}
        book_units = {u.unit_id: u for u in self._units(self._ref(inp.units_ref))}
        script = EpisodeScript.model_validate_json(self.store.read(en_ref, "script.json"))
        book_ids = [u for s in script.sections for u in s.unit_ids]
        # the AR key includes the decisions of the book units this episode speaks (a later acceptance = new artifact)
        book_digest = hashlib.sha256(json.dumps([[u, book_results[u].decision, book_results[u].target_text]
                                                 if u in book_results else [u] for u in book_ids],
                                                ensure_ascii=False).encode()).hexdigest()
        key = stage_key(AR_STAGE, AR_VERSION, [r.sha256 for r in inputs] + [book_digest], config)
        layer = ConceptLayer.model_validate_json(self.store.read(concept_ref, "concept-layer.json"))
        locked = {k.casefold(): v for k, v in termbase.locked_terms().items()}
        terms = [FirstMentionTerm(original=t.term, target=locked[t.term.casefold()])
                 for t in term_infos(layer, script.lesson_id) if t.term.casefold() in locked] if script.lesson_id else []
        labels = {t.term: locked[t.term.casefold()]
                  for t in (term_infos(layer, script.lesson_id) if script.lesson_id else []) if t.term.casefold() in locked}

        def compute() -> dict[str, bytes]:
            units = script_units(script, key_term_labels=labels,
                                 page_of=lambda b: list(book_units[b].pages) if b in book_units else [1])
            translator = self._translator(inp, termbase)
            results, drafts = {}, {}
            limit = translator.config.context_chars
            for i, su in enumerate(units):
                before = units[i - 1].unit.source_text[-limit:] if i else None
                after = units[i + 1].unit.source_text[:limit] if i + 1 < len(units) else None
                r = translator.translate(su.unit, context_before=before, context_after=after)
                results[su.unit.unit_id] = r.translation
                drafts[su.unit.unit_id] = [draft_record(d) for d in r.drafts]
                try:
                    activity.heartbeat({"episode": inp.episode, "unit": su.unit.unit_id})
                except RuntimeError:
                    pass
            book_lines = {}
            for uid in book_ids:
                u, t = book_units.get(uid), book_results.get(uid)
                if u is None:
                    continue
                book_lines[uid] = BookLine(uid, u.block_ids[0], u.source_text, t.target_text if t else "",
                                           t.decision if t else "pending", u.kind)
            ar = assemble_arabic(script, units, results, book_lines=book_lines, key_term_labels=labels,
                                 source_sha256=en_ref.sha256, clock=self.clock)
            # the episode heading (book title, number, title) is a name, not a mention of a term
            ar_lines = [x for x in ar.all_lines() if not all(src.kind == "outline" for src in x.sources)]
            pre = {x.line_id: x.text for x in ar_lines}
            inserted = insert_first_mentions([x.text for x in ar_lines], terms)
            ar = with_texts(ar, {x.line_id: (text, ins) for x, text, ins in zip(ar_lines, inserted.texts, inserted.inserted)
                                 if ins})
            ar = EpisodeScript.model_validate({**ar.model_dump(), "metrics": {
                **ar.metrics, "first_mention_terms": len(terms), "first_mention_inserted": sum(map(len, inserted.inserted)),
                "first_mention_missing": inserted.missing, "unit_decisions": _count(t.decision for t in results.values())}})
            all_units = {su.unit.unit_id: su.unit for su in units} | book_units
            outcomes = evaluate_ar_script(ar, units=all_units, book_decisions={u: b.decision for u, b in book_lines.items()},
                                          terms=terms, pre_insertion=pre)
            return {"script.json": ar.model_dump_json(indent=1).encode("utf-8"),
                    "translations.json": _dump([{"unit": su.unit.model_dump(mode="json"), "line_id": su.line_id,
                                                 "translation": results[su.unit.unit_id].model_dump(mode="json"),
                                                 "drafts": drafts[su.unit.unit_id]} for su in units]),
                    "pre-insertion.json": _dump(pre), "gates.json": _dump(_gates_json(outcomes))}

        ref, hit = self.store.get_or_compute(inp.course_id, AR_STAGE, key, compute)
        gates = json.loads(self.store.read(ref, "gates.json"))
        outcomes = [GateOutcome(g, v["kind"], v["passed"], v["metrics"], v.get("definition") or {}) for g, v in gates.items()]
        status = None
        if self.promotion is not None:
            engine = self.runtime.engine(inp.engine_profile).identity if self.runtime else None
            identities = [ProducerIdentity(role="engine", provider=engine.provider, model_id=engine.model_id,
                                           prompt_version=engine.prompt_version)] if engine else []
            status = attach_gates(self.promotion, inp.course_id, ref, outcomes, inputs=inputs,
                                  config={k: v for k, v in config.items() if k != "cache_bust"},
                                  identities=identities).status
        return {"episode": inp.episode, "ref": ref.model_dump(mode="json"), "cache_hit": hit,
                "stage_output_status": status, "gates": {g: v["passed"] for g, v in gates.items()}}


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


# ------------------------------------------------------------------ workflow
def _retry(maximum_attempts: int) -> RetryPolicy:
    return RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0,
                       maximum_interval=timedelta(minutes=5), maximum_attempts=maximum_attempts,
                       non_retryable_error_types=sorted({*DEFAULT_RETRY.non_retryable_error_types,
                                                         "CloudProviderNotAllowed", "ValueError"}))


EPISODE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(hours=2),
                                       schedule_to_close_timeout=timedelta(hours=6),
                                       heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(3))


@workflow.defn(name=WORKFLOW_NAME)
class BuildEpisodeScriptsWorkflow:
    """Per episode: the English script (generated, grounded, validated), then its Arabic translation.
    Started only on request; a re-run reuses every finished episode (activities are keyed by input)."""

    @workflow.run
    async def run(self, inp: BuildEpisodeScriptsInput) -> dict[str, Any]:
        refs = inp.model_dump(include=set(EpisodeRefs.model_fields))
        out: dict[str, Any] = {}
        for episode in inp.episodes:
            en: dict[str, Any] = await workflow.execute_activity(
                EN_ACTIVITY, EpisodeScriptENInput(**refs, episode=episode,
                                                  require_accepted_inputs=inp.require_accepted_inputs,
                                                  cache_bust=inp.cache_bust),
                result_type=dict, **EPISODE_OPTIONS)
            entry: dict[str, Any] = {"en": en}
            usable = en.get("has_script") and (en.get("stage_output_status") == "accepted"
                                               or not inp.require_accepted_inputs)
            if usable:
                entry["ar"] = await workflow.execute_activity(
                    AR_ACTIVITY, EpisodeScriptARInput(**refs, episode=episode, en_ref=en["ref"],
                                                      termbase_ref=inp.termbase_ref, engine_profile=inp.engine_profile,
                                                      qe_profile=inp.qe_profile,
                                                      require_accepted_en=inp.require_accepted_inputs,
                                                      cache_bust=inp.cache_bust),
                    result_type=dict, **EPISODE_OPTIONS)
            else:
                entry["ar"] = {"skipped": "the English script is not accepted"}
            out[f"{episode:02d}"] = entry
        return {"episodes": out}


def episodes_workflow_id(course_id: str, run_id: str, episodes: Sequence[int]) -> str:
    raw = f"{course_id}|{run_id}|{','.join(map(str, episodes))}"
    return "b2c-episode-scripts-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


__all__ = ["AR_ACTIVITY", "AR_STAGE", "BookView", "BuildEpisodeScriptsInput", "BuildEpisodeScriptsWorkflow", "EN_ACTIVITY",
           "EN_STAGE", "EpisodeActivities", "EpisodeRefs", "EpisodeScriptARInput", "EpisodeScriptENInput",
           "FIRST_MENTION_GATE_ID", "TRANSLATION_GATE_ID", "WORKFLOW_NAME", "audio_title", "episodes_workflow_id", "evaluate_ar_script",
           "term_infos"]
