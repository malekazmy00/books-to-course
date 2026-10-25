"""English episode scripts (D-035..D-038): grounded generation, an independent validator of another
family, recap limited to previous episodes, coverage of first mentions, listening budgets, episode
00; then the script translation units and the Arabic assembly (book text = accepted book units)."""
from __future__ import annotations

import pytest

import b2c_contracts as c
from b2c_episodes import (BookLine, EpisodeGenerationFailed, assemble_arabic, check_grounding, coverage,
                          evaluate_en_script, generate_course_intro, generate_lesson_episode, is_script_unit,
                          script_units, with_texts)
from b2c_episodes.budgets import recap_budget
from tests.unit.episodes.fixtures import CH1, LESSON, NOW, PREFACE, intro_plan, plan, roles

ALL = (PREFACE, CH1, LESSON)
BLOCK_TEXT = {b.block_id: b.text for lesson in ALL for b in lesson.blocks}
LESSON_OF = {b.block_id: lesson.lesson_id for lesson in ALL for b in lesson.blocks}
EPISODE_OF = {lesson.lesson_id: lesson.episode for lesson in ALL}


def lesson_script(**kw):
    gen, judge = roles()
    return generate_lesson_episode(kw.pop("p", None) or plan(), generator=gen, judge=judge, clock=lambda: NOW, **kw)


def kinds(script):
    return [s.kind for s in script.sections]


def test_lesson_episode_order_voices_and_five_questions():
    script, report = lesson_script()
    assert kinds(script) == ["episode_intro", "book_text", "lesson_summary", "key_terms", "questions", "others_say"]
    intro = script.sections[0].lines
    assert intro[0].text == "Power. Episode 3: Hegemony."
    assert intro[-1].text == "New terms in this episode: hegemony, war of position, civil society."
    questions = script.sections[4].lines
    assert len(questions) == 10 and all(q.pause_after_ms >= 1000 for q in questions[::2])
    assert all(any(s.kind == "book" and s.lesson_id == LESSON.lesson_id for s in a.sources) for a in questions[1::2])
    book = script.sections[1]
    assert book.lines == [] and list(book.unit_ids) == ["bk:u00010", "bk:u00011", "bk:u00012", "bk:u00013"]
    others = script.sections[5].lines
    assert others[0].attributed_to == "Wikipedia" and others[0].text.startswith("According to Wikipedia: ")
    assert script.validator_family == "google" and script.generator_family == "deepseek"


def test_grounding_and_validator_filter_candidates():
    script, report = lesson_script()
    summary = [x.text for x in script.sections[2].lines]
    assert "Hegemony was invented in 1920." not in summary          # the validator refused it
    assert "Made-up quote." not in summary                          # quote not in the lesson
    reasons = {r["text"]: r["reason"] for r in report["rejections"]}
    assert reasons["Made-up quote."].startswith("quote not found")
    assert reasons["Hegemony was invented in 1920."] == "validator: not supported"
    assert report["validator_pass_rate"] < 1.0 and report["grounding_rate"] < 1.0


def test_recap_mentions_only_previous_episodes():
    script, report = lesson_script()
    recap = [x for x in script.sections[0].lines if x.line_id.split(".")[-1].startswith("r")]
    assert recap and all(s.lesson_id in (PREFACE.lesson_id, CH1.lesson_id) for x in recap for s in x.sources)
    assert any("unknown or disallowed block 'P9'" in (r["reason"] or "") for r in report["rejections"])
    first, _ = lesson_script(p=plan(previous=()))
    assert not any(x.line_id.split(".")[-1].startswith("r") for x in first.sections[0].lines)
    assert recap_budget(0) == (0, 0) and recap_budget(1)[0] < recap_budget(5)[0] <= 90


def test_key_terms_use_the_authors_definition_and_coverage():
    script, _ = lesson_script()
    terms = script.sections[3].lines
    heg = next(x for x in terms if x.original == "hegemony")
    assert heg.text == ("hegemony: Hegemony is the combination of force and consent that a ruling group exercises.")
    assert {s.kind for s in heg.sources} == {"concept", "book"}
    cov = coverage(plan(), script)
    assert cov == {"first_mentions": 3, "covered": 3, "coverage": 1.0, "missing": []}


def test_gates_on_the_english_script():
    script, _ = lesson_script()
    grounding = check_grounding(script, block_text=BLOCK_TEXT, lesson_of=LESSON_OF, episode_of_lesson=EPISODE_OF,
                                dossier=plan().dossier)
    assert grounding["grounding_rate"] == 1.0
    out = {g.gate_id: g for g in evaluate_en_script(script, grounding=grounding, coverage=coverage(plan(), script))}
    assert set(out) == {"episodes.grounding", "episodes.validation", "episodes.length", "episodes.coverage"}
    assert all(g.passed for g in out.values()), {k: g.metrics for k, g in out.items() if not g.passed}
    # negative control: a recap line that cites the current lesson fails grounding
    data = script.model_dump()
    data["sections"][0]["lines"].append({"line_id": "e003.r09", "text": "x", "sources": [
        {"kind": "book", "lesson_id": LESSON.lesson_id, "block_id": "bk:3:1", "quote": "Hegemony rests on consent"}]})
    bad = c.EpisodeScript.model_validate(data)
    g = check_grounding(bad, block_text=BLOCK_TEXT, lesson_of=LESSON_OF, episode_of_lesson=EPISODE_OF)
    assert g["grounding_rate"] < 1.0 and "previous episode" in g["invalid"][0]


def test_same_family_validator_is_refused():
    gen, _ = roles()
    with pytest.raises(ValueError, match="family"):
        generate_lesson_episode(plan(), generator=gen, judge=gen, clock=lambda: NOW)


def test_failure_without_enough_validated_questions():
    from tests.unit.episodes import fixtures as f
    gen, judge = roles()
    gen.llm.script = lambda pv, msg: {**f.lesson_reply(), "questions": []} if pv == "ep-lesson-v1" else f.recap_reply()
    with pytest.raises(EpisodeGenerationFailed) as err:
        generate_lesson_episode(plan(), generator=gen, judge=judge, clock=lambda: NOW)
    assert err.value.report["used"]["questions"] == 0


def test_course_introduction_episode_00():
    gen, judge = roles()
    script, report = generate_course_intro(intro_plan(), generator=gen, judge=judge, clock=lambda: NOW)
    assert script.episode == 0 and script.kind == "course_intro"
    assert kinds(script) == ["about_book", "about_author", "book_method", "course_guide", "others_say",
                             "acknowledgments"]
    about = script.sections[0].lines
    assert [x.text for x in about] == ["According to Wikipedia: The book is a guide to structured analysis of power."]
    assert any(r["reason"] == "no published claim cited" for r in report["rejections"])
    assert script.sections[4].lines[0].attributed_to == "Dario Galasso (review)"
    assert script.sections[-1].lines[0].sources[0].block_id == "bk:1:2"
    assert script.sections[3].lines[0].text.startswith("This course presents the book in 3 episodes")
    g = check_grounding(script, block_text=BLOCK_TEXT, lesson_of=LESSON_OF, episode_of_lesson=EPISODE_OF,
                        allowed_lessons={PREFACE.lesson_id}, dossier=intro_plan().dossier)
    assert g["grounding_rate"] == 1.0


def tr(unit, text, decision="accepted"):
    return c.UnitTranslation(unit_id=unit.unit_id, target_text=text, origin="rule", decision=decision,
                             decided_by="policy", decided_at=NOW)


def test_script_units_and_arabic_assembly():
    script, _ = lesson_script()
    labels = {"hegemony": "الهيمنة", "war of position": "حرب المواقع", "civil society": "المجتمع المدني"}
    units = script_units(script, key_term_labels=labels, page_of=lambda b: [int(b.split(":")[1])])
    assert all(is_script_unit(su.unit) and su.unit.kind == "prose" for su in units)
    assert units[0].unit.unit_id == "bk:ep003:u00001" and units[0].unit.next_unit_id == units[1].unit.unit_id
    heg = next(su for su in units if su.label == "hegemony")
    assert heg.unit.source_text.startswith("Hegemony is the combination")  # only the definition is translated
    n_lines = sum(len(s.lines) for s in script.sections if s.kind != "book_text")
    assert len(units) == n_lines
    translations = {su.unit.unit_id: tr(su.unit, "نص عربي.") for su in units}
    translations[units[1].unit.unit_id] = tr(units[1].unit, "نص عربي يحتاج مراجعة.", "needs_review")
    book_lines = {uid: BookLine(uid, f"bk:3:{i}", "src", f"نص الكتاب {i}.", "accepted")
                  for i, uid in enumerate(script.sections[1].unit_ids)}
    book_lines["bk:u00013"] = BookLine("bk:u00013", "bk:3:3", "src", "", "dropped")
    ar = assemble_arabic(script, units, translations, book_lines=book_lines, key_term_labels=labels,
                         source_sha256="a" * 64, clock=lambda: NOW)
    assert ar.language == "ar" and kinds(ar) == kinds(script)
    assert [x.text for x in ar.sections[1].lines] == ["نص الكتاب 0.", "نص الكتاب 1.", "نص الكتاب 2."]
    term = next(x for x in ar.sections[3].lines if x.original == "hegemony")
    assert term.text == "الهيمنة (hegemony): نص عربي."
    assert ar.sections[0].lines[1].status == "needs_review"
    again = with_texts(ar, {ar.sections[1].lines[0].line_id: ("الهيمنة (hegemony) هنا.", ["hegemony"])})
    assert again.sections[1].lines[0].inserted_originals == ["hegemony"]
