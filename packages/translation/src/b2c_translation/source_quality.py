"""Extraction defects reach the learner as knowledge (D-052 point 4).

The ingest v2 gate names the pages of each failed check (``IngestQualityReport.page_findings``) and the
normalizer marks residual defects on blocks (``BlockV2.quality_flags``). A translation unit on such a
page, or made of such a block, carries ``source_flags``; when its translation is accepted it is still
offered as a translator note with the reason ``source`` («النص الأصلي هنا لم يُستخرج من الكتاب بثقة»),
because the translation may faithfully follow a defective source.

Only failed checks flag pages (a passing check's listed pages are within its threshold), and only block
flags about the reading of the text count (not structure, e.g. a note without its label).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from b2c_contracts import IngestQualityReport, ParsedBookV2, QAIssue, TranslationUnit

SOURCE_ISSUE_ID = "source.unclear"
UNCLEAR_BLOCK_FLAGS = frozenset({"letter_spacing_residue", "ligature_residue", "unexpected_script",
                                 "cut_at_page_break"})


def v1_block_flags(book: ParsedBookV2) -> dict[str, list[str]]:
    """Quality flags by v1 block id: the v1 projection numbers blocks per first page in reading order
    (``parsed_book_v1_from_v2``), so the same walk gives the same ids."""
    out: dict[str, list[str]] = {}
    per_page: dict[int, int] = {}
    for b in book.blocks:
        page = b.refs[0].page_no
        seq = per_page.get(page, 0)
        per_page[page] = seq + 1
        if b.quality_flags:
            out[f"{book.book_id}:{page}:{seq}"] = list(b.quality_flags)
    return out


def flag_units(units: Sequence[TranslationUnit], *, block_flags: Mapping[str, Sequence[str]],
               report: IngestQualityReport | None) -> list[TranslationUnit]:
    failed = set(report.failed) if report is not None and not report.accepted else set()
    page_checks: dict[int, set[str]] = {}
    for check, pages in (report.page_findings if report is not None else {}).items():
        if check in failed:
            for page in pages:
                page_checks.setdefault(page, set()).add(check)
    out = []
    for u in units:
        flags = {f"page:{c}" for p in u.pages for c in page_checks.get(p, ())}
        flags |= {f"block:{f}" for b in u.block_ids for f in block_flags.get(b, ()) if f in UNCLEAR_BLOCK_FLAGS}
        out.append(u.model_copy(update={"source_flags": sorted(flags)}) if flags else u)
    return out


def source_issue(flags: Sequence[str]) -> QAIssue:
    return QAIssue(check_id=SOURCE_ISSUE_ID, category="other", severity="major", origin="deterministic",
                   evidence={"source_flags": list(flags)})


__all__ = ["SOURCE_ISSUE_ID", "UNCLEAR_BLOCK_FLAGS", "flag_units", "source_issue", "v1_block_flags"]
