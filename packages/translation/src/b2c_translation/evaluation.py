"""Measure the deterministic QA checks on the 2026-10-24 audit gold set (D-028 §6).

    python -m b2c_translation.evaluation --gold <gold.jsonl> --out <report.md>

For every gold row a ``TranslationUnit`` is built (kind from ``block_type``) and
``DEFAULT_CHECKS`` run on (a) ``pipeline_target`` and (b) ``corrected_translation``.

Reported per split (all / calibration 70% / held-out test 30% / per book):

* recall: segments whose gold issues include a major/critical issue of a category, and
  how many of them the checks block (any major/critical deterministic issue) or flag with
  the *same* category;
* false positives: share of corrected translations (and of audit-approved ``correct``
  pipeline targets) on which some check raises a major/critical issue;
* false accepts stopped: of the accepted segments the audit judged major/critical, how
  many deterministic checks alone would have stopped.

Limitation: gold rows are pre-rebuild segments, so footnote markers are still inline in
the source (``unit.footnote_markers`` is empty) and they are compared as ordinary numbers;
no termbase/entity table existed for the audit, so the terminology and named-entity
checks are not exercised here. The report contains only counts, never book text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from b2c_contracts import QAIssue, TranslationUnit, TranslationUnitKind

from .qa.checks import DEFAULT_CHECKS, LENGTH_RATIO_BOUNDS, run_checks
from .qa.leakage import LEAK_PATTERNS_VERSION
from .qa.types import CheckContext

KIND_BY_BLOCK_TYPE: dict[str, TranslationUnitKind] = {
    "paragraph": "prose", "code": "prose", "list_item": "list_item", "heading": "heading",
    "page_header": "heading", "table": "table_cell", "caption": "caption", "footnote": "footnote",
}
# Gold category -> deterministic categories that count as "same-category" detection.
DETECTED_AS: dict[str, frozenset[str]] = {
    "date_error": frozenset({"number_error"}),
    "segmentation": frozenset({"omission", "addition"}),
    # gold markers are inline in the source, so a marker defect surfaces as a number mismatch
    "footnote_marker": frozenset({"footnote_marker", "number_error"}),
    # Latin text in the target is reported by the script checks as well
    "untranslated_english": frozenset({"untranslated_english", "foreign_script"}),
}
TEST_SHARE = 0.30
BLOCKING = ("major", "critical")


def load_gold(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def unit_from_gold(row: dict) -> TranslationUnit:
    return TranslationUnit(
        unit_id=f"{row['book']}:{row['segment_id']}", book_id=row["book"], block_ids=[row["segment_id"]],
        kind=KIND_BY_BLOCK_TYPE.get(row.get("block_type") or "", "prose"), source_text=row["source"],
        footnote_markers=[], pages=list(row.get("pages") or [0]), section_path=list(row.get("section_path") or []))


def split_of(row: dict, test_share: float = TEST_SHARE) -> str:
    digest = hashlib.sha256(f"{row['book']}|{row['segment_id']}".encode()).hexdigest()
    return "test" if int(digest[:8], 16) % 1000 < test_share * 1000 else "calibration"


def _gold_category(category: str) -> str:
    return "foreign_script" if category.startswith("other:") else category


@dataclass
class Recall:
    positives: int = 0
    detected: int = 0       # blocked by a same-category deterministic issue
    blocked_any: int = 0    # blocked by any major/critical deterministic issue


@dataclass
class FalsePositives:
    total: int = 0
    flagged: int = 0
    by_check: Counter = field(default_factory=Counter)


@dataclass
class FalseAccepts:
    total: int = 0
    stopped: int = 0


@dataclass
class EvalReport:
    recall: dict[str, dict[str, Recall]]
    false_positives: dict[str, dict[str, FalsePositives]]
    false_accepts: dict[str, FalseAccepts]
    rows_total: int = 0
    rows_skipped: int = 0
    calibration: dict[str, list[float]] = field(default_factory=dict)


def _blocking(issues: Iterable[QAIssue]) -> list[QAIssue]:
    return [i for i in issues if i.severity in BLOCKING]


def _check(unit: TranslationUnit, target: str) -> list[QAIssue]:
    return _blocking(run_checks(CheckContext(unit=unit, target=target)))


def _letters(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def evaluate(rows: Sequence[dict]) -> EvalReport:
    splits = ("all", "calibration", "test", *sorted({r["book"] for r in rows}))
    recall: dict[str, dict[str, Recall]] = {s: {} for s in splits}
    fps: dict[str, dict[str, FalsePositives]] = {s: {"corrected": FalsePositives(), "approved": FalsePositives()}
                                                  for s in splits}
    fas = {s: FalseAccepts() for s in splits}
    ratios: dict[str, list[float]] = {}
    skipped = 0
    for row in rows:
        unit = unit_from_gold(row)
        keys = ("all", split_of(row), row["book"])
        target = row.get("pipeline_target")
        if target:
            blocked = _check(unit, target)
            blocked_categories = {i.category for i in blocked}
            gold = {_gold_category(i["category"]) for i in row.get("issues", []) if i["severity"] in BLOCKING}
            for category in gold:
                same = DETECTED_AS.get(category, frozenset({category}))
                for key in keys:
                    r = recall[key].setdefault(category, Recall())
                    r.positives += 1
                    r.detected += bool(same & blocked_categories)
                    r.blocked_any += bool(blocked)
            if row.get("status") == "accepted" and row.get("verdict") in BLOCKING:
                for key in keys:
                    fas[key].total += 1
                    fas[key].stopped += bool(blocked)
            if row.get("verdict") == "correct":
                for key in keys:
                    _count_fp(fps[key]["approved"], blocked)
        else:
            skipped += 1
        corrected = row.get("corrected_translation")
        if corrected:
            blocked = _check(unit, corrected)
            for key in keys:
                _count_fp(fps[key]["corrected"], blocked)
            if split_of(row) == "calibration" and _letters(row["source"]) >= 40:
                ratios.setdefault(unit.kind, []).append(_letters(corrected) / _letters(row["source"]))
    return EvalReport(recall=recall, false_positives=fps, false_accepts=fas, rows_total=len(rows),
                      rows_skipped=skipped, calibration=ratios)


def _count_fp(fp: FalsePositives, blocked: list[QAIssue]) -> None:
    fp.total += 1
    fp.flagged += bool(blocked)
    fp.by_check.update({i.check_id for i in blocked})


def _pct(num: int, den: int) -> str:
    return f"{100 * num / den:.1f}% ({num}/{den})" if den else "n/a (0)"


def _quantiles(values: list[float]) -> str:
    if not values:
        return "n/a"
    v = sorted(values)
    pick = lambda q: v[int(q * (len(v) - 1))]
    return f"n={len(v)}, min {v[0]:.2f}, p1 {pick(0.01):.2f}, median {pick(0.5):.2f}, p99 {pick(0.99):.2f}, max {v[-1]:.2f}"


def render_markdown(report: EvalReport, *, gold_path: str = "") -> str:
    splits = list(report.recall)
    lines = [
        "# Translation v2 deterministic QA: gold-set evaluation",
        "",
        f"Gold set: `{gold_path}` ({report.rows_total} rows; {report.rows_skipped} rows without a "
        f"pipeline target skipped for (a)). Leak patterns version `{LEAK_PATTERNS_VERSION}`. "
        f"Checks: {', '.join(c.check_id for c in DEFAULT_CHECKS)}.",
        "",
        "A segment counts as *blocked* when any check raises a major or critical issue. "
        "*Same-category* means the blocking issue has the gold category (date_error -> number_error, "
        "segmentation -> omission/addition, other:* -> foreign_script, footnote_marker -> footnote_marker/number_error, "
        "untranslated_english -> untranslated_english/foreign_script).",
        "",
        "## Recall on gold issues (major/critical, segment level, `pipeline_target`)",
        "",
    ]
    for split in splits:
        lines += [f"### Split: {split}", "", "| category | positives | same-category | blocked by any check |",
                  "|---|---:|---:|---:|"]
        for category, r in sorted(report.recall[split].items(), key=lambda kv: -kv[1].positives):
            lines.append(f"| {category} | {r.positives} | {_pct(r.detected, r.positives)} | "
                         f"{_pct(r.blocked_any, r.positives)} |")
        lines.append("")
    lines += ["## False positives", "",
              "`corrected` = corrected translations from the audit; `approved` = pipeline targets the audit "
              "judged `correct`.", "",
              "| split | set | flagged (any major/critical) | leakage | top checks |", "|---|---|---:|---:|---|"]
    for split in splits:
        for name, fp in report.false_positives[split].items():
            top = ", ".join(f"{k} {v}" for k, v in fp.by_check.most_common(5)) or "-"
            lines.append(f"| {split} | {name} | {_pct(fp.flagged, fp.total)} | "
                         f"{_pct(fp.by_check.get('prompt_leakage', 0), fp.total)} | {top} |")
    lines += ["", "## False accepts stopped by deterministic checks alone", "",
              "| split | accepted major/critical segments | stopped |", "|---|---:|---:|"]
    for split in splits:
        fa = report.false_accepts[split]
        lines.append(f"| {split} | {fa.total} | {_pct(fa.stopped, fa.total)} |")
    lines += ["", "## Calibration (length ratio, corrected translations, calibration split, source >= 40 chars)", "",
              "| kind | observed non-space ratio target/source | configured bounds |", "|---|---|---|"]
    for kind, values in sorted(report.calibration.items()):
        lines.append(f"| {kind} | {_quantiles(values)} | {LENGTH_RATIO_BOUNDS.get(kind)} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gold", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = evaluate(load_gold(args.gold))
    Path(args.out).write_text(render_markdown(report, gold_path=args.gold), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
