"""Speech text of one Arabic episode script (D-036..D-038; learning-companion §2.2, §13–§15).

Consumes an Arabic ``EpisodeScript`` exactly like the book front end consumes accepted units: every
line must carry an ACCEPTED translation (``NotSpeechReady`` otherwise), is normalized by the same
Arabic text normalizer (the original term at a first mention, ``الهيمنة (hegemony)``, is a Latin
parenthetical and is not read aloud; the Arabic term is), split into bounded TTS segments, and
traced to its translation units.

The three voices are announced (§2.2): each section opens with a short fixed announcement — the book
text with «نص الكتاب.», course sections with their name or «ملاحظة من الكورس:», external sections
with «بحسب مصادر خارجية:». After each question the script's thinking pause is kept
(``pause_after_ms``), then the answer follows.

A ``figure_reading`` section (D-044) is read in the course voice like the book's figure descriptions:
«وصف الشكل.» (or «وصف الجدول.»), its lines, then «انتهى وصف الشكل.».

Failure becomes knowledge (D-041, D-052):
* a book-text line of a unit that was not accepted is its translator note (``note_id``), spoken in the
  course voice like the book's notes: the announcement, then the approximate translation if offered;
* a companion line (intro, summary, terms, questions, others say) whose translation was not accepted
  is left out (recorded in ``excluded``); a section left without lines is left out with its heading;
  one course announcement (``DROPPED_NOTICE_AR``) at the end of the intro tells the listener.
A book-text line that is neither accepted nor annotated is never spoken silently (``NotSpeechReady``).
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from b2c_contracts import (BOOK_TEXT_KINDS, FIGURE_ANNOUNCEMENT_AR, FIGURE_END_AR, ArtifactRef, EpisodeScript,
                           SpeechExclusion, SpeechSegment, SpeechTextDocument, TranslatorNote)

from .frontend import FRONTEND_VERSION, NotSpeechReady, _phrases
from .lexicon import PronunciationLexicon
from .normalize import normalize_text
from .prosody import BREAK_STRENGTH, PARAGRAPH_LEVEL, PAUSE_MS, STRUCTURE_BOUNDARY, engine_profile, plan_boundaries

ANNOUNCEMENTS = {
    "book_text": "نص الكتاب.", "figure_description": "وصف الشكل:", "lesson_summary": "ملخص الدرس.",
    "key_terms": "أهم المصطلحات.", "questions": "أسئلة للمراجعة. فكّر في كل سؤال قبل أن تسمع إجابته.",
    "others_say": "بحسب مصادر خارجية:", "debate": "بحسب مصادر خارجية:", "about_book": "عن الكتاب.",
    "about_author": "عن المؤلف.", "book_method": "ملاحظة من الكورس: منهج الكتاب.",
    "course_guide": "ملاحظة من الكورس: كيف تستفيد من هذا الكورس.", "acknowledgments": "شكر.",
}
ROLE_STRUCTURE = {"text": "paragraph", "question": "question", "answer": "answer", "term": "list_item"}
EPISODE_FRONTEND_VERSION = f"{FRONTEND_VERSION}-episode2"  # 2: book-front-end phrasing, notes, dropped lines (D-052)
THINKING_BOUNDARY = "section"
DROPPED_NOTICE_AR = "ملاحظة من الكورس: حُذف من هذه الحلقة جزء لم نطمئن إلى ترجمته، ونص الكتاب يأتي كاملًا."


def build_episode_speech_text(script: EpisodeScript, *, source: ArtifactRef, lexicon: PronunciationLexicon | None = None,
                              target_chars: int = 200, max_chars: int | None = None,
                              notes: Mapping[str, TranslatorNote] | None = None,
                              notes_ref: ArtifactRef | None = None,
                              profile: str = "fasih-xtts") -> SpeechTextDocument:
    """Phrases, boundaries and pauses follow the book front end (engine phrase limits, terminal
    normalization, ``plan_boundaries``), so an episode passes the same reading-quality gate.
    ``target_chars`` is kept for callers of the first version and has no effect."""
    if script.language != "ar":
        raise ValueError("only Arabic episode scripts are spoken")
    lexicon = lexicon or PronunciationLexicon()
    engine = engine_profile(profile)
    limit_chars, limit_words = max_chars or engine.max_phrase_chars, engine.max_phrase_words
    notes = notes or {}
    book_lines = [x for sec in script.sections if sec.kind in BOOK_TEXT_KINDS for x in sec.lines]
    silent = [x.line_id for x in book_lines if x.status != "accepted" and not x.note_id]
    if silent:
        raise NotSpeechReady(f"{len(silent)} book-text line(s) are neither accepted nor annotated (first: {silent[0]})")
    missing = [x.note_id for x in book_lines if x.note_id and (x.note_id not in notes or notes_ref is None)]
    if missing:
        raise NotSpeechReady(f"translator note {missing[0]} is not available (notes and notes_ref are needed)")
    excluded = [SpeechExclusion(unit_id=(x.unit_ids or ["-"])[0], reason=f"companion_translation_not_accepted:{x.line_id}"[:120])
                for sec in script.sections if sec.kind not in BOOK_TEXT_KINDS for x in sec.lines if x.status != "accepted"]
    pieces: list[tuple[list[str], list[str], str, str, str, str, object, dict]] = []
    seen_glosses: set[str] = set()

    def add(text: str, *, unit_ids: list[str], structure: str, block_ids: list[str] | None = None,
            fields: dict | None = None) -> None:
        normalized = normalize_text(text, lexicon=lexicon, structure=structure, seen_glosses=seen_glosses, terminal=True)
        if not re.search(r"\w", normalized.text):
            return
        phrases = _phrases(normalized.text, limit_chars, limit_words, engine.min_phrase_chars)
        for i, (phrase, boundary) in enumerate(phrases):
            last = i == len(phrases) - 1
            pieces.append((unit_ids, block_ids or [], structure, text, phrase,
                           STRUCTURE_BOUNDARY[structure] if last else boundary, normalized, fields or {}))

    for section in script.sections:
        if section.kind not in BOOK_TEXT_KINDS:
            section = section.model_copy(update={"lines": [x for x in section.lines if x.status == "accepted"]})
        if not section.lines:
            if section.kind == "episode_intro" and excluded:
                add(DROPPED_NOTICE_AR, unit_ids=list(script.sections[0].lines[0].unit_ids), structure="paragraph")
            continue
        if section.kind == "figure_reading" and section.figure_kind:
            fields = {"voice": "course", "figure_id": section.figure_id}
            ids = [u for x in section.lines for u in x.unit_ids]
            add(FIGURE_ANNOUNCEMENT_AR[section.figure_kind], unit_ids=ids[:1], structure="caption",
                fields={**fields, "figure_part": "announcement"})
            for line in section.lines:
                add(line.text, unit_ids=list(line.unit_ids), structure="caption",
                    fields={**fields, "figure_part": "description"})
            add(FIGURE_END_AR[section.figure_kind], unit_ids=ids[-1:], structure="caption",
                fields={**fields, "figure_part": "end"})
            continue
        announcement = ANNOUNCEMENTS.get(section.kind)
        if announcement:
            add(announcement, unit_ids=list(section.lines[0].unit_ids), structure="heading")
        for line in section.lines:
            structure = ROLE_STRUCTURE[line.role]
            blocks = [s.block_id for s in line.sources if s.kind == "book" and s.block_id][:5]
            if line.note_id:  # D-041: the translator note, in the course voice
                n = notes[line.note_id]
                add(n.announcement_ar, unit_ids=list(line.unit_ids), structure="paragraph", block_ids=blocks,
                    fields={"voice": "course", "note_id": line.note_id, "note_part": "announcement"})
                if n.best_translation:
                    add(n.best_translation, unit_ids=list(line.unit_ids), structure=structure, block_ids=blocks,
                        fields={"voice": "course", "note_id": line.note_id, "note_part": "approximate_translation"})
                continue
            add(line.text, unit_ids=list(line.unit_ids), structure=structure, block_ids=blocks)
        if section.kind == "episode_intro" and excluded:
            add(DROPPED_NOTICE_AR, unit_ids=list(section.lines[0].unit_ids), structure="paragraph")
    if not pieces:
        raise NotSpeechReady("no spoken text in the episode script")
    boundaries = plan_boundaries([p[2] for p in pieces], [p[5] for p in pieces])
    # the thinking pause after a question (D-038): the longest boundary the reading plan allows (1.2 s)
    boundaries = [THINKING_BOUNDARY if p[2] == "question" and b in PARAGRAPH_LEVEL else b
                  for p, b in zip(pieces, boundaries)]
    segments: list[SpeechSegment] = []
    for (unit_ids, block_ids, structure, text, phrase, _, normalized, fields), boundary in zip(pieces, boundaries):
        seq = len(segments)
        segments.append(SpeechSegment(
            segment_id=f"{script.book_id}:ep{script.episode:03d}:s{seq:05d}", seq=seq, unit_ids=unit_ids,
            block_ids=block_ids, structure=structure, source_text=text, spoken_text=phrase,
            rules_applied=list(dict.fromkeys(normalized.rules)), lexicon_hits=sorted(set(normalized.lexicon_hits)),
            lexicon_misses=sorted(set(normalized.lexicon_misses)), pause_after_ms=PAUSE_MS[boundary],
            boundary_after=boundary, break_after=BREAK_STRENGTH[boundary],
            unvoiced_glosses=sorted(set(normalized.unvoiced_glosses)), **fields))
    return SpeechTextDocument(book_id=script.book_id, language="ar", frontend_version=EPISODE_FRONTEND_VERSION,
                              lexicon_digest=lexicon.digest, source=source, segments=segments, excluded=excluded,
                              notes_ref=notes_ref if any(s.note_id for s in segments) else None)


__all__ = ["ANNOUNCEMENTS", "DROPPED_NOTICE_AR", "EPISODE_FRONTEND_VERSION", "build_episode_speech_text"]
