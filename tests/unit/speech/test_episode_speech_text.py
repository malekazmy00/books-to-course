"""The Arabic episode script is consumable by the speech front end: accepted lines only, voices
announced, the inserted original term not read aloud, the thinking pause after each question kept."""
from datetime import datetime, timezone

import pytest

import b2c_contracts as c
from b2c_speech.text.episode import build_episode_speech_text
from b2c_speech.text.frontend import NotSpeechReady, evaluate_speech_text

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
BOOK = c.ScriptSource(kind="book", lesson_id="l1", block_id="bk:2:1", quote="Hegemony is force plus consent.")
REF = c.ArtifactRef(stage="episode-script-ar", key="k", uri="s3://artifacts/c/episode-script-ar/k/", sha256="a" * 64,
                    created_at=NOW)


def line(i, text, role="text", status="accepted", **kw):
    return c.ScriptLine(line_id=f"l{i:03d}", text=text, role=role, sources=[BOOK], unit_ids=[f"bk:ep003:u{i:05d}"],
                        status=status, **kw)


def script(status="accepted"):
    qa = []
    for i in range(5):
        qa += [line(20 + 2 * i, "على ماذا تقوم الهيمنة؟", "question", pause_after_ms=4000),
               line(21 + 2 * i, "تقوم على الرضا أكثر من القوة.", "answer")]
    sections = [
        c.EpisodeSection(section_id="s1", kind="episode_intro", voice="course",
                         lines=[line(1, "الحلقة الثالثة: الهيمنة.")]),
        c.EpisodeSection(section_id="s2", kind="book_text", voice="author", block_ids=["bk:2:1"], unit_ids=["bk:u00001"],
                         lines=[c.ScriptLine(line_id="b1", text="الهيمنة (hegemony) مزيج من القوة والرضا.",
                                             sources=[BOOK], unit_ids=["bk:u00001"], status=status,
                                             inserted_originals=["hegemony"])]),
        c.EpisodeSection(section_id="s3", kind="lesson_summary", voice="course", lines=[line(2, "الهيمنة قوة ورضا.")]),
        c.EpisodeSection(section_id="s4", kind="key_terms", voice="course", lines=[
            line(3, "الهيمنة (hegemony): مزيج من القوة والرضا.", "term", original="hegemony",
                 ).model_copy(update={"sources": [c.ScriptSource(kind="concept", concept_id="cpt_000000000001"), BOOK]})]),
        c.EpisodeSection(section_id="s5", kind="questions", voice="course", lines=qa),
    ]
    return c.EpisodeScript(book_id="bk", language="ar", episode=3, kind="lesson", lesson_id="l1", title="الهيمنة",
                           book_title="كتاب", sections=sections, translation_run_id="r" * 64,
                           source_script_sha256="b" * 64, created_at=NOW)


def test_episode_script_to_speech_text():
    doc = build_episode_speech_text(script(), source=REF)
    passed, metrics = evaluate_speech_text(doc)
    assert passed, metrics
    spoken = [s.spoken_text for s in doc.segments]
    assert "نص الكتاب" in spoken[1] and "ملخص الدرس" in " ".join(spoken)
    book = next(s for s in doc.segments if s.unit_ids == ["bk:u00001"] and s.structure == "paragraph")
    assert "hegemony" not in book.spoken_text and "الهيمنة" in book.spoken_text
    assert "latin_gloss_unvoiced" in book.rules_applied  # rule renamed by the speech readability merge
    questions = [s for s in doc.segments if s.structure == "question"]
    assert len(questions) == 5 and all(s.pause_after_ms == 4000 for s in questions)


def test_only_accepted_lines_are_spoken():
    with pytest.raises(NotSpeechReady):
        build_episode_speech_text(script(status="needs_review"), source=REF)
