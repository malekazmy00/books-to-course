"""b2c_episodes — derived formats (D-032, D-035..D-038; learning-companion §2, §12–§15).

The episode scripts of a course, written in ENGLISH from the English book and then translated
through the book's translation pipeline: per lesson an intro (book, episode number and title, recap
of the previous episodes, what this episode covers, its new terms), the book text (a reference to
the translated lesson units: the faithful core is never generated), a summary, the key terms, five
questions with answers, and optional "others say"; episode 00 introduces the course.

Every generated sentence is grounded (verbatim quotes of the cited lesson block, or published
dossier claims) and checked by a validator of another model family. Model calls go through injected
``complete_text`` clients (b2c_llm_gateway in production); no other layer is imported.
"""
from .arabic import BookLine, ScriptUnit, assemble_arabic, is_script_unit, script_units, with_texts
from .gates import (COVERAGE_GATE_ID, GROUNDING_GATE_ID, LENGTH_GATE_ID, VALIDATION_GATE_ID, check_grounding,
                    evaluate_en_script)
from .generate import (EpisodeGenerationFailed, ModelRole, coverage, generate_course_intro,
                       generate_lesson_episode)
from .sources import BookTextRef, CourseIntroPlan, EpisodePlan, LessonSource, SourceBlock, TermInfo

EPISODES_VERSION = "episode-scripts-2026-10-25.2"

__all__ = ["BookLine", "BookTextRef", "COVERAGE_GATE_ID", "CourseIntroPlan", "EPISODES_VERSION", "EpisodeGenerationFailed",
           "EpisodePlan", "GROUNDING_GATE_ID", "LENGTH_GATE_ID", "LessonSource", "ModelRole", "ScriptUnit", "SourceBlock",
           "TermInfo", "VALIDATION_GATE_ID", "assemble_arabic", "check_grounding", "coverage", "evaluate_en_script",
           "generate_course_intro", "generate_lesson_episode", "is_script_unit", "script_units", "with_texts"]
