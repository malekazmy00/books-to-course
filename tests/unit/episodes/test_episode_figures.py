"""Figure descriptions in the episode script (D-044): a ``figure_reading`` section (course voice) is
inserted in the book text right after the unit the description is placed after; the book text
section is split there. Its lines are generated lines like any other: they are translated through
the book's pipeline, and the speech front end reads them announced and closed."""
from __future__ import annotations

from datetime import datetime, timezone

import b2c_contracts as c
from b2c_episodes import generate_lesson_episode, script_units
from b2c_episodes.figures import insert_figure_sections
from b2c_episodes.gates import check_grounding, check_length

from .fixtures import NOW, plan, roles

CAP = c.ScriptSource(kind="book", block_id="bk:3:2", quote="The war of position is a long struggle")


def figures_en(after="bk:u00011"):
    d = c.FigureDescription(
        figure_id="fig007", kind="figure", label="Figure 3.1", caption="The war of position", pages=[3],
        block_ids=["bk:3:9"], after_unit_id=after, mode="description",
        lines=[c.ScriptLine(line_id="fig007.l00", text="Figure 3.1: The war of position.", sources=[CAP]),
               c.ScriptLine(line_id="fig007.l01", text="The figure shows civil society around the state.",
                            sources=[CAP])],
        grounding=c.FigureGrounding(passed=True, checked=0))
    return c.FigureDescriptionsDocument(book_id="bk", language="en", descriptions=[d],
                                        created_at=datetime(2026, 10, 25, tzinfo=timezone.utc))


def unit_blocks():
    return {f"bk:u0001{i}": [f"bk:3:{i}"] for i in range(4)}


def test_insert_splits_the_book_text_after_the_anchor_unit():
    gen, judge = roles()
    script, _ = generate_lesson_episode(plan(), generator=gen, judge=judge, clock=lambda: NOW)
    out, placed = insert_figure_sections(script, figures_en(), unit_blocks=unit_blocks())
    kinds = [s.kind for s in out.sections]
    assert kinds[:4] == ["episode_intro", "book_text", "figure_reading", "book_text"]
    first, fig, second = out.sections[1:4]
    assert first.unit_ids == ["bk:u00010", "bk:u00011"] and second.unit_ids == ["bk:u00012", "bk:u00013"]
    assert first.block_ids == ["bk:3:0", "bk:3:1"] and second.block_ids == ["bk:3:2", "bk:3:3"]
    assert fig.figure_id == "fig007" and fig.voice == "course" and fig.figure_kind == "figure"
    assert [x.line_id for x in fig.lines] == ["e003.f00.l00", "e003.f00.l01"]
    assert placed == {"fig007": "e003.f00"}
    # generated lines: they become translation units like the rest of the script
    units = script_units(out)
    assert any(u.unit.section_path[2] == "figure_reading" for u in units)
    # the figure section has its own grounding gate (figure-descriptions); the episode gates skip it
    g = check_grounding(out, block_text={}, lesson_of={}, episode_of_lesson={})
    assert not any(i.startswith("e003.f00") for i in g["invalid"])
    assert "e003.f00" not in check_length(out)["section_words"]


def test_a_figure_anchored_outside_the_lesson_is_not_inserted():
    gen, judge = roles()
    script, _ = generate_lesson_episode(plan(), generator=gen, judge=judge, clock=lambda: NOW)
    out, placed = insert_figure_sections(script, figures_en(after="bk:u09999"), unit_blocks=unit_blocks())
    assert placed == {} and [s.kind for s in out.sections] == [s.kind for s in script.sections]


def test_episode_speech_reads_the_figure_section_announced_and_closed():
    from b2c_speech.text.episode import build_episode_speech_text
    ref = c.ArtifactRef(stage="episode-script-ar", key="k", uri="s3://a/c/episode-script-ar/k/", sha256="a" * 64,
                        created_at=NOW)
    gen, judge = roles()
    script, _ = generate_lesson_episode(plan(), generator=gen, judge=judge, clock=lambda: NOW)
    out, _ = insert_figure_sections(script, figures_en(), unit_blocks=unit_blocks())
    data = out.model_dump()
    data.update(language="ar", source_script_sha256="b" * 64)
    n = 0
    for s in data["sections"]:
        if s["kind"] in ("book_text",):
            s["lines"] = [{"line_id": f"{s['section_id']}.{u[-5:]}", "text": "نص الكتاب المترجم.",
                           "sources": [CAP.model_dump()], "unit_ids": [u], "status": "accepted"} for u in s["unit_ids"]]
            continue
        for x in s["lines"]:
            n += 1
            x.update(text="سطر مترجم.", unit_ids=[f"bk:ep003:u{n:05d}"], status="accepted",
                     original=x.get("original"))
    ar = c.EpisodeScript.model_validate(data)
    doc = build_episode_speech_text(ar, source=ref)
    fig = [s for s in doc.segments if s.figure_id == "fig007"]
    assert fig[0].figure_part == "announcement" and fig[0].spoken_text.startswith("وصف الشكل")
    assert fig[-1].figure_part == "end" and fig[-1].spoken_text.startswith("انتهى وصف الشكل")
    assert all(s.voice == "course" for s in fig)
