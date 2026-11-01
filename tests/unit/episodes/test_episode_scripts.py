"""English episode scripts (D-035..D-038): grounded generation, an independent validator of another
family, recap limited to previous episodes, coverage of first mentions, listening budgets, episode
00; then the script translation units and the Arabic assembly (book text = accepted book units)."""
from __future__ import annotations

import pytest

import b2c_contracts as c
from b2c_episodes import (BookLine, assemble_arabic, check_grounding, coverage,
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


def test_without_five_validated_questions_the_gap_is_declared_not_the_episode_lost():
    """D-033/D-052: the questions are missing, so the episode declares it; the book text stays."""
    from tests.unit.episodes import fixtures as f
    gen, judge = roles()
    gen.llm.script = lambda pv, msg: {**f.lesson_reply(), "questions": []} if pv == "ep-lesson-v2" else f.recap_reply()
    script, report = generate_lesson_episode(plan(), generator=gen, judge=judge, clock=lambda: NOW)
    assert report["used"]["questions"] == 0 and script.deficits == ["questions"]
    assert kinds(script) == ["episode_intro", "book_text", "lesson_summary", "key_terms", "others_say"]
    note = script.sections[0].lines[-1]
    assert note.text.startswith("A note from the course:") and "questions" in note.text
    assert "author's text" in note.text


def test_a_failed_generation_still_gives_the_book_text_episode():
    gen, judge = roles()

    def broken(pv, msg):
        raise RuntimeError("provider down")
    gen.llm.script = broken
    script, report = generate_lesson_episode(plan(), generator=gen, judge=judge, clock=lambda: NOW)
    # the key terms survive: they are the author's own definitions from the concept layer, no model
    assert kinds(script) == ["episode_intro", "book_text", "key_terms", "others_say"]
    assert script.deficits == ["summary", "questions"]
    assert any(e.startswith("lesson:") for e in report["errors"])
    assert "no summary and questions" in script.sections[0].lines[-1].text


def test_a_declared_gap_passes_the_english_gates():
    from tests.unit.episodes import fixtures as f
    gen, judge = roles()
    gen.llm.script = lambda pv, msg: {**f.lesson_reply(), "questions": []} if pv == "ep-lesson-v2" else f.recap_reply()
    p = plan()
    script, _ = generate_lesson_episode(p, generator=gen, judge=judge, clock=lambda: NOW)
    g = check_grounding(script, block_text=BLOCK_TEXT, lesson_of=LESSON_OF, episode_of_lesson=EPISODE_OF,
                        dossier=p.dossier)
    outcomes = {o.gate_id: o for o in evaluate_en_script(script, grounding=g, coverage=coverage(p, script))}
    assert all(o.passed for o in outcomes.values()), {k: o.metrics for k, o in outcomes.items() if not o.passed}
    assert outcomes["episodes.validation"].metrics["deficits"] == ["questions"]
    assert outcomes["episodes.coverage"].metrics["deficits"] == ["questions"]


def test_an_episode_without_a_model_passes_the_english_gates():
    """Without a summary, uncovered first mentions are part of the declared gap, not a failure."""
    gen, judge = roles()
    gen.llm.script = lambda pv, msg: (_ for _ in ()).throw(RuntimeError("down"))
    p = plan()
    script, _ = generate_lesson_episode(p, generator=gen, judge=judge, clock=lambda: NOW)
    g = check_grounding(script, block_text=BLOCK_TEXT, lesson_of=LESSON_OF, episode_of_lesson=EPISODE_OF,
                        dossier=p.dossier)
    outcomes = {o.gate_id: o for o in evaluate_en_script(script, grounding=g, coverage=coverage(p, script))}
    assert all(o.passed for o in outcomes.values()), {k: o.metrics for k, o in outcomes.items() if not o.passed}


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
    # spoken: the person, not the (English) venue
    assert script.sections[4].lines[0].text.startswith("According to Dario Galasso: ")
    assert script.sections[-1].lines[0].sources[0].block_id == "bk:1:2"
    assert script.sections[3].lines[0].text.startswith("This course presents the book in 3 episodes")
    g = check_grounding(script, block_text=BLOCK_TEXT, lesson_of=LESSON_OF, episode_of_lesson=EPISODE_OF,
                        allowed_lessons={PREFACE.lesson_id}, dossier=intro_plan().dossier)
    assert g["grounding_rate"] == 1.0


def test_a_multi_sentence_item_becomes_separately_checked_lines():
    from tests.unit.episodes import fixtures as f
    gen, judge = roles()
    reply = f.lesson_reply()
    reply["summary"] = [{"text": "Hegemony combines force and consent. Hegemony was invented in 1920.",
                         "evidence": f.ev_b("B2", "the combination of force and consent")}]
    gen.llm.script = lambda pv, msg: reply if pv == "ep-lesson-v2" else f.recap_reply()
    script, report = generate_lesson_episode(plan(), generator=gen, judge=judge, clock=lambda: NOW)
    assert [x.text for x in script.sections[2].lines] == ["Hegemony combines force and consent."]


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


def test_book_text_takes_a_translator_note_for_a_unit_that_was_not_accepted():
    """D-041: a book unit that was not accepted is spoken as a course-voice note, not left out."""
    script, _ = lesson_script()
    labels = {"hegemony": "الهيمنة", "war of position": "حرب المواقع", "civil society": "المجتمع المدني"}
    units = script_units(script, key_term_labels=labels, page_of=lambda b: [int(b.split(":")[1])])
    translations = {su.unit.unit_id: tr(su.unit, "نص عربي.") for su in units}
    ids = script.sections[1].unit_ids
    assert len(ids) >= 3
    announce = f"{c.TRANSLATOR_NOTE_LEAD_AR} تعذّر التحقق من دقة ترجمة هذه الفقرة. وما يلي ترجمة تقريبية."
    with_best = c.TranslatorNote(
        note_id=f"{ids[1]}:note", unit_id=ids[1], book_id="bk", block_ids=["bk:3:1"], pages=[3], unit_kind="prose",
        unit_decision="needs_review", reasons=["unverified"], reason_ar="تعذّر التحقق من دقة ترجمة هذه الفقرة.",
        source_text="src 1", best_translation="نص الكتاب التقريبي.",
        best_translation_label_ar=c.BEST_TRANSLATION_LABEL_AR, best_translation_from="final", announcement_ar=announce)
    bare = (f"{c.TRANSLATOR_NOTE_LEAD_AR} تعذّرت ترجمة هذه الفقرة ترجمةً موثوقة. "
            "والنص الأصلي محفوظ في النسخة المكتوبة من الكورس.")
    without = c.TranslatorNote(
        note_id=f"{ids[2]}:note", unit_id=ids[2], book_id="bk", block_ids=["bk:3:2"], pages=[3], unit_kind="prose",
        unit_decision="rejected", reasons=["no_translation"], reason_ar="تعذّرت ترجمة هذه الفقرة ترجمةً موثوقة.",
        source_text="NOT ES", withheld="fabrication_risk", announcement_ar=bare)
    book_lines = {uid: BookLine(uid, f"bk:3:{i}", "src", "", "dropped") for i, uid in enumerate(ids)}
    book_lines[ids[0]] = BookLine(ids[0], "bk:3:0", "src 0", "نص الكتاب 0.", "accepted")
    book_lines[ids[1]] = BookLine(ids[1], "bk:3:1", "src 1", "نص الكتاب التقريبي.", "needs_review", note=with_best)
    book_lines[ids[2]] = BookLine(ids[2], "bk:3:2", "NOT ES", "نص مختلق طويل.", "rejected", note=without)
    ar = assemble_arabic(script, units, translations, book_lines=book_lines, key_term_labels=labels,
                         source_sha256="a" * 64, clock=lambda: NOW)
    lines = ar.sections[1].lines
    assert [x.note_id for x in lines] == [None, f"{ids[1]}:note", f"{ids[2]}:note"]
    assert lines[1].text == f"{announce} نص الكتاب التقريبي." and lines[1].status == "needs_review"
    assert lines[2].text == bare and "مختلق" not in lines[2].text  # nothing offered: announcement only
    assert lines[2].sources[0].quote == "NOT ES"  # the original stays in the written script
    assert ar.metrics["missing_lines"] == []


def test_a_book_line_not_accepted_and_without_a_note_is_kept_as_before():
    script, _ = lesson_script()
    units = script_units(script, key_term_labels={}, page_of=lambda b: [1])
    translations = {su.unit.unit_id: tr(su.unit, "نص عربي.") for su in units}
    ids = script.sections[1].unit_ids
    book_lines = {uid: BookLine(uid, f"bk:3:{i}", "src", "نص.", "needs_review") for i, uid in enumerate(ids)}
    ar = assemble_arabic(script, units, translations, book_lines=book_lines, key_term_labels={},
                         source_sha256="a" * 64, clock=lambda: NOW)
    assert all(x.note_id is None and x.status == "needs_review" for x in ar.sections[1].lines)


# ------------------------------------------------------------------ D-038 (2): the answer says where it is
def test_each_answer_says_where_it_is_in_the_lesson():
    from b2c_episodes.generate import answer_location
    from tests.unit.episodes.fixtures import blk
    assert answer_location(LESSON, "bk:3:1") == "This is early in the lesson."
    assert answer_location(LESSON, "bk:3:3") == "This is near the end of the lesson."
    with_parts = c_lesson = LESSON.__class__(LESSON.lesson_id, 3, "Hegemony", (
        blk(3, 0, "Hegemony", "heading"), blk(3, 1, "Text one about force."),
        blk(3, 2, "The war of position", "heading"), blk(3, 3, "A long struggle inside civil society.")))
    assert answer_location(with_parts, "bk:3:3") == "This is in the part of the lesson on The war of position."
    assert c_lesson is with_parts
    script, _ = lesson_script()
    answers = [x for x in script.sections[4].lines if x.role == "answer"]
    assert all(a.text.endswith(".") and "lesson" in a.text.split(". ")[-1] for a in answers)


# ------------------------------------------------------------------ the debate (D-032 §10): optional, chosen per episode
def debate_plan():
    from tests.unit.episodes import fixtures as f
    d = f.dossier()
    extra = [f.claim(10, "concept_in_field", "Hegemony explains why the ruled consent to rule.", src=f.REVIEW_SRC,
                     kind="viewpoint", status="attributed", attributed_to="Perry Anderson in New Left Review",
                     concept="hegemony", conflict_group="g1"),
             f.claim(11, "concept_in_field", "Hegemony overstates consent and understates coercion.", src=f.REVIEW_SRC,
                     kind="viewpoint", status="attributed", attributed_to="Nicholas Abercrombie (critique)",
                     concept="hegemony", conflict_group="g1")]
    return plan().__class__(**{**plan().__dict__, "dossier": d.model_copy(update={"claims": [*d.claims, *extra]})})


def test_no_debate_unless_asked():
    gen, judge = roles()
    script, _ = generate_lesson_episode(debate_plan(), generator=gen, judge=judge, clock=lambda: NOW)
    assert "debate" not in kinds(script)


def test_the_debate_puts_attributed_opposing_views_side_by_side():
    gen, judge = roles()
    script, report = generate_lesson_episode(debate_plan(), generator=gen, judge=judge, clock=lambda: NOW, debate=True)
    assert kinds(script)[-1] == "debate"
    lines = script.sections[-1].lines
    assert [x.text for x in lines] == ["According to Perry Anderson: Hegemony explains why the ruled consent to rule.",
                                       "According to Nicholas Abercrombie: Hegemony overstates consent and understates coercion."]
    assert all(x.attributed_to and x.sources[0].kind == "dossier" for x in lines)
    assert report["debate"] == {"requested": True, "views": 2}


def test_a_debate_needs_two_people_holding_views():
    gen, judge = roles()
    script, report = generate_lesson_episode(plan(), generator=gen, judge=judge, clock=lambda: NOW, debate=True)
    assert "debate" not in kinds(script) and report["debate"] == {"requested": True, "views": 0}
