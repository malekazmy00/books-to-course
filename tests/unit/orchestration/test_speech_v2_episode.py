"""Speech v2 for one episode (D-036..D-038, D-042): the audio comes out lesson by lesson.

``episode_ref`` (an Arabic episode script) replaces the book assembly as the speech source. The
episode's book-text notes are spoken through the book's translator notes (D-041); companion lines whose
translation was not accepted are left out and announced (D-052). The episode need not be accepted:
whatever it holds that is accepted is spoken, nothing is withheld silently.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import b2c_contracts as c
from b2c_contracts import ArtifactRef
from temporalio.exceptions import ApplicationError

from tests.unit.orchestration import test_speech_v2 as base
from tests.unit.orchestration.test_speech_v2 import acts, termbase_ref

store, results, promo = base.store, base.results, base.promo  # the same fixtures
NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
BOOK = c.ScriptSource(kind="book", lesson_id="l1", block_id="bk:2:1", quote="Gramsci wrote on hegemony.")


def line(i, text, role="text", status="accepted", **kw):
    return c.ScriptLine(line_id=f"l{i:03d}", text=text, role=role, sources=[BOOK], unit_ids=[f"bk:ep003:u{i:05d}"],
                        status=status, **kw)


def episode_script(summary_status="accepted"):
    qa = []
    for i in range(5):
        qa += [line(20 + 2 * i, "على ماذا تقوم الهيمنة؟", "question", pause_after_ms=4000),
               line(21 + 2 * i, "تقوم على الرضا.", "answer")]
    sections = [
        c.EpisodeSection(section_id="s1", kind="episode_intro", voice="course", lines=[line(1, "الحلقة الثالثة: الهيمنة.")]),
        c.EpisodeSection(section_id="s2", kind="book_text", voice="author", block_ids=["bk:2:1"], unit_ids=["bk:u00001"],
                         lines=[c.ScriptLine(line_id="b1", text="كتب غرامشي عن الهيمنة.", sources=[BOOK],
                                             unit_ids=["bk:u00001"], status="accepted")]),
        c.EpisodeSection(section_id="s3", kind="lesson_summary", voice="course",
                         lines=[line(2, "الهيمنة قوة ورضا.", status=summary_status)]),
        c.EpisodeSection(section_id="s4", kind="key_terms", voice="course", lines=[
            line(3, "الهيمنة: مزيج من القوة والرضا.", "term", original="hegemony").model_copy(
                update={"sources": [c.ScriptSource(kind="concept", concept_id="cpt_000000000001"), BOOK]})]),
        c.EpisodeSection(section_id="s5", kind="questions", voice="course", lines=qa),
    ]
    return c.EpisodeScript(book_id="bk", language="ar", episode=3, kind="lesson", lesson_id="l1", title="الهيمنة",
                           book_title="كتاب", sections=sections, translation_run_id="r" * 64,
                           source_script_sha256="b" * 64, created_at=NOW)


def put_episode(store, script, course="c1"):
    return store.put(course, "episode-script-ar", "e" * 64, {"script.json": script.model_dump_json().encode()})


def episode_input(ref, **kw):
    data = dict(course_id="c1", episode_ref=ref.model_dump(mode="json"), require_intelligibility=False)
    data.update(kw)
    return base.GenerateSpeechV2Input(**data)


def test_one_episode_becomes_its_own_speech_text(store, results, promo):
    ref = put_episode(store, episode_script())
    a = acts(store, results, promo)
    prepared = a.prepare(episode_input(ref, termbase_ref=termbase_ref(store).model_dump(mode="json")))
    assert prepared.status == "accepted" and prepared.gate_passed, prepared.gate_metrics
    doc = a._doc(ArtifactRef.model_validate(prepared.speech_text_ref))
    assert doc.source == ref and doc.segments[0].segment_id.startswith("bk:ep003:")
    spoken = " ".join(s.spoken_text for s in doc.segments)
    assert "نص الكتاب" in spoken and "غرامشي" in spoken and "ملخص الدرس" in spoken
    assert prepared.plan.total_segments == len(doc.segments)
    record = promo.require_accepted("c1", ArtifactRef.model_validate(prepared.speech_text_ref))
    assert [r.stage for r in record.inputs] == ["episode-script-ar", "book-termbase-approved"]
    assert record.config["speech_source"] == "episode"


def test_an_episode_with_an_untranslated_companion_line_still_speaks(store, results, promo):
    ref = put_episode(store, episode_script(summary_status="needs_review"))
    a = acts(store, results, promo)
    prepared = a.prepare(episode_input(ref, lexicon_overrides={"Gramsci": "غرامشي"}))
    assert prepared.status == "accepted"
    doc = a._doc(ArtifactRef.model_validate(prepared.speech_text_ref))
    assert any(e.reason.startswith("companion_translation_not_accepted") for e in doc.excluded)
    assert "حُذف من هذه الحلقة" in " ".join(s.spoken_text for s in doc.segments)


def test_exactly_one_speech_source(store, results, promo):
    ref = put_episode(store, episode_script())
    with pytest.raises(ValueError):
        base.GenerateSpeechV2Input(course_id="c1")
    with pytest.raises(ValueError):
        base.GenerateSpeechV2Input(course_id="c1", episode_ref=ref.model_dump(mode="json"),
                                   assembly_ref=ref.model_dump(mode="json"))


def test_an_episode_of_another_course_or_stage_is_refused(store, results, promo):
    a = acts(store, results, promo)
    foreign = put_episode(store, episode_script(), course="other")
    with pytest.raises(ApplicationError) as err:
        a.prepare(episode_input(foreign))
    assert err.value.non_retryable and err.value.type == "InvalidSpeechInput"
    wrong = store.put("c1", "episode-script-en", "d" * 64, {"script.json": b"{}"})
    with pytest.raises(ApplicationError):
        a.prepare(episode_input(wrong))
