"""Gold-set evaluation harness (D-028 §6) on a synthetic fixture."""
from __future__ import annotations

from pathlib import Path

from b2c_translation import evaluation as E

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "translation_qa" / "gold_synthetic.jsonl"


def test_unit_from_gold_row_maps_block_type_to_kind():
    rows = E.load_gold(FIXTURE)
    kinds = [E.unit_from_gold(r).kind for r in rows]
    assert kinds[:4] == ["prose", "prose", "heading", "list_item"]
    unit = E.unit_from_gold(rows[0])
    assert unit.footnote_markers == [] and unit.book_id == "bookA"


def test_split_is_deterministic_and_roughly_thirty_percent():
    rows = [{"book": "b", "segment_id": f"s:{i}"} for i in range(2000)]
    splits = [E.split_of(r) for r in rows]
    assert splits == [E.split_of(r) for r in rows]
    share = splits.count("test") / len(splits)
    assert 0.25 < share < 0.35


def test_evaluate_counts_recall_false_positives_and_false_accepts():
    report = E.evaluate(E.load_gold(FIXTURE))
    leak = report.recall["all"]["prompt_leakage"]
    assert (leak.positives, leak.detected) == (1, 1)
    numbers = report.recall["all"]["number_error"]
    assert (numbers.positives, numbers.detected) == (1, 1)
    assert report.recall["all"]["meaning_error"].positives == 1
    fp = report.false_positives["all"]["corrected"]
    assert fp.total == 3 and fp.flagged == 0
    fa = report.false_accepts["all"]
    assert fa.total == 3 and fa.stopped == 2
    assert report.rows_skipped == 1  # rejected row without a target


def test_markdown_report_has_numbers_but_no_text(tmp_path):
    out = tmp_path / "r.md"
    assert E.main(["--gold", str(FIXTURE), "--out", str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    assert "prompt_leakage" in md and "القبول الخاطئ" in md and "المعايرة" in md
    for row in E.load_gold(FIXTURE):
        assert row["source"] not in md
        if row["pipeline_target"]:
            assert row["pipeline_target"] not in md
