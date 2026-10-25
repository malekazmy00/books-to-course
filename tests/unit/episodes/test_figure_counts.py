"""Element counts of a figure, counted by code, not by the model (D-049, owner decision 2026-10-26).

DIAGRAM Center recommends saying how many parts a figure has («a center circle and five smaller
circles»). The model miscounts (SAT: «six» question words for seven, «four» legend entries for five),
so the grounding gate refused every count it made. The code now counts deterministically from the
figure's own text (labels/OCR) and table, by kind, and only when it is sure; the counts go into the
evidence, so the gate accepts them, and any other count is still refused."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import b2c_contracts as c
from b2c_episodes import ModelRole
from b2c_episodes.figures import (FIGURE_PROMPT_VERSION, FIGURE_SYSTEM, FigureBlock, check_figure_grounding,
                                  collect_figures, count_figure_elements, describe_figures)
from tests.unit.research.fakes import ScriptedLLM

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
B = "bk"


def fb(seq, type_, text, page=10):
    return FigureBlock(f"{B}:b{seq:05d}", type_, text, page, "body", ("Chapter 6",))


def one(text, *, type_="figure", caption=None):
    blocks = [fb(1, type_, text)] + ([fb(2, "caption", caption)] if caption else [])
    figures, _ = collect_figures(blocks, book_id=B)
    assert len(figures) == 1
    return figures[0]


def counts(fig):
    return {k.what: k.count for k in count_figure_elements(fig)}


# ------------------------------------------------------------------ figures: what the code is sure of
def test_question_words_are_counted():
    fig = one("Who? Why? What? So What? Where? How? When?")
    got = count_figure_elements(fig)
    assert counts(fig) == {"questions": 7}
    assert got[0].items == ("Who?", "Why?", "What?", "So What?", "Where?", "How?", "When?")


def test_labelled_questions_after_a_title_are_counted():
    fig = one("Motive, Opportunity, and Means (MOM) Motive: What are the goals of the deceiver? "
              "Channels: What means are available to the deceiver? Risks: What consequences would follow? "
              "Costs: Would the deceiver need to sacrifice information? Feedback: Does the deceiver have feedback?")
    assert counts(fig) == {"questions": 5}


def test_a_starburst_whose_questions_ocr_broke_apart_is_not_counted():
    fig = one("Has any group threatened such an attack? Could it have occurred by accident? To OHM Exactly what "
              "agent was used? incite terror, or cause Was it lethal enough? WHY? Attack on Subway HOW? WHRE")
    assert "questions" not in counts(fig)


def test_numbered_labels_are_counted_only_without_gaps():
    fig = one("% Weight Option 1 Option 2 Option 3 Criterion 1 30% Criterion 2 10% Criterion 3 20% "
              "Criterion 4 20% Totals 100%")
    got = counts(fig)
    assert got["labels Option 1 to Option 3"] == 3 and got["labels Criterion 1 to Criterion 4"] == 4
    gap = one("Step 1 collect Step 2 sort Step 4 decide and report the result to the client")
    assert not any(k.startswith("labels Step") for k in counts(gap))


def test_lettered_labels_and_prefixed_numbers_are_counted():
    fig = one("Faults Pros Cons Fixes Fault Pro A Pro A Con A Fix Con A Pro B Con B Pro C Con C Pro D Con D")
    got = counts(fig)
    assert got["labels Pro A to Pro D"] == 4 and got["labels Con A to Con D"] == 4
    hyp = one("Enter Hypotheses H1: Domestic group H2: International terrorists H3: Disgruntled worker "
              "H4: A lone actor R9: Georgia militia")
    assert counts(hyp)["labels H1 to H4"] == 4


def test_a_lettered_key_is_counted_but_a_bare_run_of_letters_is_not():
    # SAT fig087: OCR dropped «G» from the heading run «A B C D E F H» of a matrix whose rows go to «J»; a bare
    # run of letters cannot show that it is complete, so it gets no count (found on SAT after the first tests)
    run = one("A B C D E F H A Increased use of techniques + + C Availability of tools + J Lack of openness")
    assert count_figure_elements(run) == []
    key = one("B C C A Key Drivers A. The role of neighboring states B. The capability of security forces "
              "C. The political environment")
    assert counts(key) == {"lettered items A to C": 3}


def test_series_found_on_sat_glued_numbers_and_unnumbered_parts():
    # added after the first SAT review: «Why2» is «Why 2» (fig035); «Baseline Scenario» beside «Scenario 1» and
    # «Scenario 2» means more scenarios than numbers (fig060)
    glued = one("Who 1 What 1 Why 1 Permutation 1 Why2 Permutation 2 Why3 Permutation 3")
    assert counts(glued)["labels Why 1 to Why 3"] == 3 and counts(glued)["labels Permutation 1 to Permutation 3"] == 3
    mixed = one("Scenario 1 More inclusive policies Baseline Scenario President under fire Scenario 2 "
                "Junior officers stage a coup Wild-Card Scenario War breaks out")
    assert count_figure_elements(mixed) == []


def test_legend_entries_are_counted():
    fig = one("Client Perception: Actor Assessment Priority Tier Preference Legend: DD = Very Desirable; "
              "D = Desirable; N = Neutral; U= Undesirable; UU = Very Undesirable")
    got = count_figure_elements(fig)
    assert counts(fig) == {"legend entries": 5}
    assert got[0].items == ("DD", "D", "N", "U", "UU")
    assert "legend entries" not in counts(one("Weights 3.5×30=105 3×30=90 2×10=20 Totals 100% 332.5 322.5"))


def test_a_two_by_two_matrix_named_by_the_evidence_has_four_quadrants():
    fig = one("Fully operational EFFECTIVENESS OF GOVERNMENT Robust STRENGTH OF CIVIL SOCIETY Marginalized",
              caption="Figure 9.6 A 2 × 2 Matrix of Alternative Futures")
    assert counts(fig)["quadrants"] == 4
    assert "quadrants" not in counts(one("Fully operational EFFECTIVENESS OF GOVERNMENT Robust STRENGTH Marginalized"))
    # SAT fig014: a multiplication «2×2=4» in a scoring grid is not a 2 × 2 matrix
    assert "quadrants" not in counts(one("Scores 3×8=24 2×2=4 2×3=6 1×6=6 weighted ranking of options"))


def test_scrambled_text_gets_no_count():
    fig = one("Review Summarizing Recall Lectures Television Groups Radio Books Noting Negotiations Chairing "
              "Summaries LEARNING MEETINGS EDUCATION PROBLEM PLANNING PRESENTATIONS")
    assert count_figure_elements(fig) == []


# ------------------------------------------------------------------ tables
GROUPED = ("| | | 2008 | 2008 | 2009 |\n| --- | --- | --- | --- | --- |\n"
           "| | | First Quarter | Second Quarter | First Quarter |\n"
           "| Social change | Ethnic discontent | Negligible | Low | Low |\n"
           "| Social change | Riots | Low | Low | Moderate |\n"
           "| Economic factors | Capital flight | Low | Low | Low |\n"
           "| Economic factors | Inflation | Low | Moderate | Low |\n"
           "| Economic factors | Shortages | Low | Low | Low |\n"
           "| Economic factors | Debt | Low | Low | Low |")


def test_table_rows_named_columns_and_row_groups_are_counted():
    fig = one(GROUPED, type_="table")
    got = count_figure_elements(fig)
    by = {k.what: k for k in got}
    assert by["data rows"].count == 6          # the second header row is not a data row
    assert by["named columns"].count == 3       # the two label columns have no name
    assert by["row groups"].count == 2 and by["row groups"].items == ("Social change", "Economic factors")


def test_a_merged_note_row_is_not_a_data_row():
    # SAT fig032: the note under a cross-impact matrix is extracted as a row repeating one text in every cell
    note = "Variable 2 has the greatest effect."
    text = ("| | Variable 1 | Variable 2 |\n| --- | --- | --- |\n| Variable 1 | | + |\n| Variable 2 | - | |\n"
            f"| {note} | {note} | {note} |")
    assert counts(one(text, type_="table"))["data rows"] == 2


def test_row_groups_are_not_counted_when_a_group_cell_is_empty():
    text = GROUPED.replace("| Social change | Ethnic discontent |", "| | Ethnic discontent |")
    assert "row groups" not in counts(one(text, type_="table"))


# ------------------------------------------------------------------ evidence, gate and prompt
def test_counts_are_evidence_and_other_counts_still_fail():
    fig = one("Who? Why? What? So What? Where? How? When?")
    assert "questions: 7" in fig.evidence()
    assert check_figure_grounding(["The figure lists seven question words."], fig.evidence()).passed
    bad = check_figure_grounding(["The figure lists six question words."], fig.evidence())
    assert bad.ungrounded == ["six"]


def test_counts_reach_the_model_and_the_description_passes_first_time():
    seen: list = []

    def reply(pv, msg):
        seen.append(msg)
        return {"lines": ["The figure lists seven question words, from who to when."]}
    llm = ScriptedLLM(reply, "deepseek/x")
    role = ModelRole(llm, c.ProducerIdentity(role="figure-describer", model_id="deepseek/x"), "deepseek")
    figures, _ = collect_figures([fb(1, "figure", "Who? Why? What? So What? Where? How? When?")], book_id=B)
    doc = describe_figures(figures, generator=role, units=[], book_id=B, clock=lambda: NOW)
    payload = json.loads(seen[0][seen[0].index("{"):])
    assert payload["counts"] == [{"what": "questions", "count": 7,
                                  "items": ["Who?", "Why?", "What?", "So What?", "Where?", "How?", "When?"]}]
    d = doc.descriptions[0]
    assert d.mode == "description" and len(seen) == 1 and d.grounding.passed
    assert doc.metrics["counted"] == 1 and doc.metrics["counts"] == {"questions": 1}


def test_the_prompt_allows_the_code_counts_and_nothing_else():
    assert FIGURE_PROMPT_VERSION == "figure-describe-v3"
    assert "Do not count the parts yourself" in FIGURE_SYSTEM
    assert '"counts"' in FIGURE_SYSTEM
