"""Mapping layer: stored (accepted) artifacts -> learner API v3 models.

This module is the only place in the learner API that knows artifact file formats. The router
hands it decoded JSON of artifacts that ``deps.require_accepted_artifact`` has already cleared.

Sources
-------
* Episode list: the accepted ``build-outline-v2`` artifact (``course-outline-v2.json``,
  ``CourseOutlineV2``). Episode 00 is the course introduction when ``course_intro_episode`` is set
  (D-037/D-038); book lessons are episodes 1..n (``LessonV2.episode``, or reading order).
* Concept cards: the accepted ``concept-layer`` artifact (``concept-layer.json``, ``ConceptLayer``).
  It is English (D-035); ``term_ar`` stays null until the translated layer exists.
* Episode content: an accepted ``episode-script`` artifact (``episode-script.json``).
* Episode audio: an accepted ``episode-audio`` artifact (``episode.mp3`` + optional ``timings.json``).

PROVISIONAL: ``episode-script`` / ``episode-audio`` are being built in parallel (EpisodeScript,
learning-companion §13–§15). Until their contract lands in ``b2c_contracts`` this module reads the
shape below and tolerates a few aliases; anything else is ignored. When the contract lands, replace
``parse_script`` / ``apply_timings`` (and the stage/file constants) and keep the API models as they
are. Answer hiding does not depend on the format being right: GET models have no answer fields,
and segments that speak an answer are blanked here.

    {"episode": 3, "title": "...", "book_title": "...", "language": "ar", "lesson_id": "...",
     "sections": [{"section_id": "s1", "kind": "intro|book_text|summary|key_terms|questions",
                   "title": "...",
                   "segments": [{"segment_id": "...", "voice": "author|course|external",
                                 "role": "text|heading|figure_description|note|question|answer|pause",
                                 "text": "...", "start_s": 0.0, "end_s": 4.2, "pages": [12],
                                 "source_label": "..."}]}],
     "key_terms": [{"term": "الهيمنة", "original": "hegemony", "definition": "...",
                    "concept_id": "cpt_..."}],
     "questions": [{"question_id": "q1", "prompt": "...", "options": null, "answer": "...",
                    "answer_index": null, "question_segment_id": "...", "answer_segment_id": "..."}]}

    timings.json: {"duration_s": 812.4, "segments": [{"segment_id": "...", "start_s": 0, "end_s": 4.2}]}

The registry record's ``config["episode"]`` (if present) names the episode an artifact belongs to,
so listing a course does not need to read every script.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from b2c_contracts import ConceptLayer, CourseOutlineV2

from b2c_api.learner.schemas import (
    AuthorQuote,
    ConceptCard,
    ConceptRelationOut,
    ExternalNote,
    KeyTerm,
    LearnerQuestion,
    Section,
    Segment,
)

MAPPING_VERSION = "learner-mapping/1 (episode-script provisional)"
OUTLINE_STAGE, OUTLINE_FILE = "build-outline-v2", "course-outline-v2.json"
CONCEPT_STAGE, CONCEPT_FILE = "concept-layer", "concept-layer.json"
SCRIPT_STAGE, SCRIPT_FILE = "episode-script", "episode-script.json"
AUDIO_STAGE, AUDIO_FILE, TIMINGS_FILE = "episode-audio", "episode.mp3", "timings.json"
COURSE_INTRO_TITLE = "مقدمة الكورس"

_SECTION_KINDS = {
    "intro": "intro", "introduction": "intro", "episode_intro": "intro", "preamble": "intro",
    "book_text": "book_text", "book": "book_text", "lesson": "book_text", "body": "book_text",
    "summary": "summary",
    "key_terms": "key_terms", "terms": "key_terms", "glossary": "key_terms",
    "questions": "questions", "qa": "questions", "quiz": "questions",
}
_VOICES = {"author": "author", "book": "author", "course": "course", "narrator": "course",
           "translator": "course", "external": "external", "source": "external"}
_ROLES = {"text", "heading", "figure_description", "note", "question", "answer", "pause"}
_SECTION_TITLES = {"intro": "تقدمة الحلقة", "book_text": "نص الكتاب", "summary": "ملخص الدرس",
                   "key_terms": "أهم المصطلحات", "questions": "أسئلة وإجابات", "other": "جزء"}


@dataclass(frozen=True)
class EpisodeRef:
    number: int
    label: str
    title: str
    kind: str
    lesson_id: str | None


def episode_label(number: int, last_episode: int) -> str:
    """Zero-padded like the MP3 file names (D-038; b2c_speech.episode_naming.episode_label)."""
    return f"{number:0{max(2, len(str(last_episode)))}d}"


def episodes_from_outline(outline_raw: dict[str, Any]) -> list[EpisodeRef]:
    outline = CourseOutlineV2.model_validate(outline_raw)
    lessons = outline.lessons
    numbers = [lesson.episode if lesson.episode is not None else i for i, lesson in enumerate(lessons, 1)]
    last = max(numbers, default=0)
    episodes: list[EpisodeRef] = []
    if outline.course_intro_episode:
        episodes.append(EpisodeRef(0, episode_label(0, last), COURSE_INTRO_TITLE, "course_intro", None))
    for number, lesson in zip(numbers, lessons, strict=True):
        episodes.append(EpisodeRef(number, episode_label(number, last), lesson.episode_title or lesson.display_title,
                                   "lesson", lesson.lesson_id))
    return episodes


def course_title(course_id: str, outline_config: dict[str, Any]) -> str:
    title = outline_config.get("book_title") or outline_config.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else course_id


def episode_number_of(config: dict[str, Any] | None, raw: dict[str, Any] | None = None) -> int | None:
    for source in (config or {}, raw or {}):
        value = source.get("episode", source.get("episode_number"))
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def number_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass
class HiddenAnswer:
    answer: str
    answer_index: int | None
    answer_segment_id: str | None
    answer_section_id: str | None


@dataclass
class ParsedScript:
    title: str | None
    book_title: str | None
    language: str
    sections: list[Section]
    key_terms: list[KeyTerm]
    questions: list[LearnerQuestion]
    answers: dict[str, HiddenAnswer] = field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        ends = [s.end_s for sec in self.sections for s in sec.segments if s.end_s is not None]
        return max(ends) if ends else None


def _questions(raw: dict[str, Any]) -> tuple[list[LearnerQuestion], dict[str, HiddenAnswer]]:
    questions: list[LearnerQuestion] = []
    answers: dict[str, HiddenAnswer] = {}
    for i, item in enumerate(raw.get("questions") or [], 1):
        if not isinstance(item, dict):
            continue
        prompt = _text(item.get("prompt")) or _text(item.get("question")) or _text(item.get("stem"))
        if prompt is None:
            continue
        qid = _text(item.get("question_id")) or _text(item.get("qid")) or f"q{i}"
        options = item.get("options")
        options = [str(o) for o in options] if isinstance(options, list) and options else None
        index = item.get("answer_index")
        index = index if isinstance(index, int) and not isinstance(index, bool) else None
        answer = _text(item.get("answer")) or _text(item.get("answer_text"))
        if answer is None and options and index is not None and 0 <= index < len(options):
            answer = options[index]
        questions.append(LearnerQuestion(
            question_id=qid, prompt=prompt, options=options,
            question_segment_id=_text(item.get("question_segment_id")),
            answer_segment_id=_text(item.get("answer_segment_id"))))
        if answer is not None:
            answers[qid] = HiddenAnswer(answer, index, _text(item.get("answer_segment_id")), None)
    return questions, answers


def parse_script(raw: dict[str, Any]) -> ParsedScript:
    """Learner view of one episode script. Segments that speak an answer have their text removed."""
    questions, answers = _questions(raw)
    answer_segments = {a.answer_segment_id for a in answers.values() if a.answer_segment_id}
    answer_texts = {a.answer for a in answers.values() if len(a.answer) >= 3}
    sections: list[Section] = []
    for si, sec in enumerate(raw.get("sections") or raw.get("parts") or [], 1):
        if not isinstance(sec, dict):
            continue
        kind = _SECTION_KINDS.get(str(sec.get("kind", "")).lower(), "other")
        section_id = _text(sec.get("section_id")) or f"s{si}"
        segments: list[Segment] = []
        for gi, seg in enumerate(sec.get("segments") or sec.get("items") or [], 1):
            if not isinstance(seg, dict):
                continue
            segment_id = _text(seg.get("segment_id")) or f"{section_id}.{gi}"
            role = str(seg.get("role") or "text").lower()
            role = role if role in _ROLES else "text"
            text = _text(seg.get("text")) or _text(seg.get("spoken_text"))
            # Text matching only inside the questions part: the lesson itself may quote an answer.
            hidden = (role == "answer" or segment_id in answer_segments
                      or (kind == "questions" and text is not None and any(a in text for a in answer_texts)))
            if hidden:
                role = "answer" if role == "text" else role
            pages = [p for p in seg.get("pages") or [] if isinstance(p, int) and p >= 1]
            segments.append(Segment(
                segment_id=segment_id, voice=_VOICES.get(str(seg.get("voice", "")).lower(), "course"),
                role=role, text=None if hidden else text, hidden=hidden,
                start_s=number_or_none(seg.get("start_s")), end_s=number_or_none(seg.get("end_s")),
                source_label=_text(seg.get("source_label")), pages=pages))
            for answer in answers.values():
                if answer.answer_segment_id == segment_id:
                    answer.answer_section_id = section_id
        starts = [s.start_s for s in segments if s.start_s is not None]
        ends = [s.end_s for s in segments if s.end_s is not None]
        sections.append(Section(section_id=section_id, kind=kind,
                                title=_text(sec.get("title")) or _SECTION_TITLES[kind],
                                start_s=min(starts) if starts else None, end_s=max(ends) if ends else None,
                                segments=segments))
    key_terms = [KeyTerm(term=term, original=_text(t.get("original")), definition=_text(t.get("definition")),
                         concept_id=_text(t.get("concept_id")))
                 for t in raw.get("key_terms") or [] if isinstance(t, dict) and (term := _text(t.get("term")))]
    return ParsedScript(title=_text(raw.get("title")), book_title=_text(raw.get("book_title")),
                        language=_text(raw.get("language")) or "ar", sections=sections, key_terms=key_terms,
                        questions=questions, answers=answers)


def apply_timings(script: ParsedScript, timings: dict[str, Any] | None) -> float | None:
    """Merge audio timings into the script's segments; return the audio duration if known."""
    if not timings:
        return script.duration_s
    by_id = {t.get("segment_id"): t for t in timings.get("segments") or [] if isinstance(t, dict)}
    for i, section in enumerate(script.sections):
        segments = []
        for segment in section.segments:
            t = by_id.get(segment.segment_id)
            if t is not None:
                segment = segment.model_copy(update={"start_s": number_or_none(t.get("start_s")), "end_s": number_or_none(t.get("end_s"))})
            segments.append(segment)
        starts = [s.start_s for s in segments if s.start_s is not None]
        ends = [s.end_s for s in segments if s.end_s is not None]
        script.sections[i] = section.model_copy(update={"segments": segments,
                                                        "start_s": min(starts) if starts else None,
                                                        "end_s": max(ends) if ends else None})
    return number_or_none(timings.get("duration_s")) or script.duration_s


def check_answer(hidden: HiddenAnswer, selected_option: int | None) -> bool | None:
    if hidden.answer_index is None or selected_option is None:
        return None
    return selected_option == hidden.answer_index


def concept_cards(layer_raw: dict[str, Any], episodes: list[EpisodeRef]) -> tuple[str, list[ConceptCard]]:
    layer = ConceptLayer.model_validate(layer_raw)
    episode_of = {e.lesson_id: e.number for e in episodes if e.lesson_id}
    terms = {c.concept_id: c.term for c in layer.concepts}
    cards = []
    for c in layer.concepts:
        definition = c.author_definition
        cards.append(ConceptCard(
            concept_id=c.concept_id, term=c.term, term_ar=None, kind=c.kind,
            gloss=definition.gloss if definition else None,
            author_quotes=[AuthorQuote(quote=s.quote, pages=list(s.pages)) for s in definition.spans] if definition else [],
            external_notes=[ExternalNote(statement=n.statement, urls=list(n.urls)) for n in c.external_notes],
            first_lesson_id=c.first_lesson_id, first_episode=episode_of.get(c.first_lesson_id),
            relations=[ConceptRelationOut(concept_id=r.target_id, term=terms.get(r.target_id, r.target_id),
                                          relation=r.relation) for r in c.relations]))
    cards.sort(key=lambda card: (card.first_episode if card.first_episode is not None else 10**6, card.term.casefold()))
    return layer.language, cards
