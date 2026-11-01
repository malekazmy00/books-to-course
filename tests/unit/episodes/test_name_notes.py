"""A line for a person, place, work or event at its first mention in the course (learning-companion §10)."""
from __future__ import annotations

import b2c_contracts as c
from b2c_episodes.names import KnownName, first_mentions, insert_name_notes
from tests.unit.episodes.fixtures import NOW

GRAMSCI = KnownName("Antonio Gramsci", "Italian Marxist philosopher", "https://www.wikidata.org/wiki/Q1", 9)
ARAB = KnownName("Arab Spring", "series of protests in the Arab world", "https://www.wikidata.org/wiki/Q2", 3)
UNITS = [("bk:u1", "The preface thanks colleagues."), ("bk:u2", "Antonio Gramsci wrote in prison."),
         ("bk:u3", "Antonio Gramsci and the Arab Spring are compared."), ("bk:u4", "Nothing here.")]


def test_only_the_first_mention_in_the_course_gets_the_line():
    found = first_mentions(UNITS, [GRAMSCI, ARAB])
    assert found == {"bk:u2": [GRAMSCI], "bk:u3": [ARAB]}


def test_a_name_inside_a_word_is_not_a_mention():
    assert first_mentions([("u", "The Antonio Gramscian school.")], [GRAMSCI]) == {}


def script():
    out = c.ScriptSource(kind="outline", episode=3)
    intro = c.EpisodeSection(section_id="e003.intro", kind="episode_intro", voice="course",
                             lines=[c.ScriptLine(line_id="l1", text="Power. Episode 3: Hegemony.", sources=[out])])
    book = c.EpisodeSection(section_id="e003.book", kind="book_text", voice="author", block_ids=["b1", "b2", "b3"],
                            unit_ids=["bk:u2", "bk:u3", "bk:u4"])
    return c.EpisodeScript(book_id="bk", language="en", episode=3, kind="lesson", lesson_id="l1", title="Hegemony",
                           book_title="Power", translation_run_id="r" * 64, sections=[intro, book], created_at=NOW,
                           deficits=["summary", "key_terms", "questions"])


BLOCKS = {"bk:u2": ["b1"], "bk:u3": ["b2"], "bk:u4": ["b3"]}


def test_the_book_text_is_split_after_the_unit_and_keeps_every_unit():
    out, placed = insert_name_notes(script(), first_mentions(UNITS, [GRAMSCI, ARAB]), unit_blocks=BLOCKS)
    assert [s.kind for s in out.sections] == ["episode_intro", "book_text", "name_note", "book_text", "name_note",
                                              "book_text"]
    assert [u for s in out.sections if s.kind == "book_text" for u in s.unit_ids] == ["bk:u2", "bk:u3", "bk:u4"]
    first = out.sections[2].lines[0]
    assert first.text == "According to Wikidata, Antonio Gramsci: Italian Marxist philosopher."
    assert first.attributed_to == "Wikidata" and first.sources[0].url == GRAMSCI.url
    assert placed == ["Antonio Gramsci", "Arab Spring"]


def test_a_cap_per_episode_keeps_the_most_frequent_names():
    many = {"bk:u2": [ARAB, GRAMSCI]}
    out, placed = insert_name_notes(script(), many, unit_blocks=BLOCKS, cap=1)
    assert placed == ["Antonio Gramsci"] and sum(s.kind == "name_note" for s in out.sections) == 1


def test_no_mention_leaves_the_script_as_it_is():
    s = script()
    out, placed = insert_name_notes(s, {}, unit_blocks=BLOCKS)
    assert out is s and placed == []
