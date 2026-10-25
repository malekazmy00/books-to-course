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
"""
from __future__ import annotations

from b2c_contracts import ArtifactRef, EpisodeScript, SpeechSegment, SpeechTextDocument

from .frontend import FRONTEND_VERSION, PAUSE_AFTER_MS, SENTENCE_PAUSE_MS, NotSpeechReady
from .lexicon import PronunciationLexicon
from .normalize import normalize_text
from .segment import split_for_tts

ANNOUNCEMENTS = {
    "book_text": "نص الكتاب.", "figure_description": "وصف الشكل:", "lesson_summary": "ملخص الدرس.",
    "key_terms": "أهم المصطلحات.", "questions": "أسئلة للمراجعة. فكّر في كل سؤال قبل أن تسمع إجابته.",
    "others_say": "بحسب مصادر خارجية:", "debate": "بحسب مصادر خارجية:", "about_book": "عن الكتاب.",
    "about_author": "عن المؤلف.", "book_method": "ملاحظة من الكورس: منهج الكتاب.",
    "course_guide": "ملاحظة من الكورس: كيف تستفيد من هذا الكورس.", "acknowledgments": "شكر.",
}
ROLE_STRUCTURE = {"text": "paragraph", "question": "question", "answer": "answer", "term": "list_item"}
EPISODE_FRONTEND_VERSION = f"{FRONTEND_VERSION}-episode1"


def build_episode_speech_text(script: EpisodeScript, *, source: ArtifactRef, lexicon: PronunciationLexicon | None = None,
                              target_chars: int = 200, max_chars: int = 300) -> SpeechTextDocument:
    if script.language != "ar":
        raise ValueError("only Arabic episode scripts are spoken")
    lexicon = lexicon or PronunciationLexicon()
    not_ready = [x.line_id for x in script.all_lines() if x.status != "accepted"]
    if not_ready:
        raise NotSpeechReady(f"{len(not_ready)} line(s) are not accepted (first: {not_ready[0]})")
    segments: list[SpeechSegment] = []

    def add(text: str, *, unit_ids: list[str], structure: str, pause_ms: int, block_ids: list[str] | None = None) -> None:
        normalized = normalize_text(text, lexicon=lexicon, structure=structure)
        chunks = split_for_tts(normalized.text, target_chars=target_chars, max_chars=max_chars)
        for i, chunk in enumerate(chunks):
            seq = len(segments)
            segments.append(SpeechSegment(
                segment_id=f"{script.book_id}:ep{script.episode:03d}:s{seq:05d}", seq=seq, unit_ids=unit_ids,
                block_ids=block_ids or [], structure=structure, source_text=text, spoken_text=chunk,
                rules_applied=list(dict.fromkeys(normalized.rules)), lexicon_hits=sorted(set(normalized.lexicon_hits)),
                lexicon_misses=sorted(set(normalized.lexicon_misses)),
                pause_after_ms=pause_ms if i == len(chunks) - 1 else SENTENCE_PAUSE_MS))

    for section in script.sections:
        if not section.lines:
            continue
        announcement = ANNOUNCEMENTS.get(section.kind)
        if announcement:
            add(announcement, unit_ids=list(section.lines[0].unit_ids), structure="heading",
                pause_ms=PAUSE_AFTER_MS["heading"])
        for line in section.lines:
            structure = ROLE_STRUCTURE[line.role]
            pause = max(line.pause_after_ms, PAUSE_AFTER_MS[structure])
            add(line.text, unit_ids=list(line.unit_ids), structure=structure, pause_ms=min(pause, 5000),
                block_ids=[s.block_id for s in line.sources if s.kind == "book" and s.block_id][:5])
    if not segments:
        raise NotSpeechReady("no spoken text in the episode script")
    return SpeechTextDocument(book_id=script.book_id, language="ar", frontend_version=EPISODE_FRONTEND_VERSION,
                              lexicon_digest=lexicon.digest, source=source, segments=segments)


__all__ = ["ANNOUNCEMENTS", "EPISODE_FRONTEND_VERSION", "build_episode_speech_text"]
