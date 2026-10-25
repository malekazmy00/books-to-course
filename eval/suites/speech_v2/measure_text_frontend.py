#!/usr/bin/env python3
"""R7 speech text front end on real accepted translation units (read-only export).

Usage:
    uv run python eval/suites/speech_v2/measure_text_frontend.py --export run.json --out report.json

``run.json`` is a read-only export of one translate-book run: ``units`` (the translation-units
artifact), ``results`` (rows of ``translation_unit_results``: unit_id, decision, decided_by,
translation) and ``termbases`` (approved termbase snapshots of the course). Only ``accepted``
units are normalized. Reports normalization coverage (spoken text with 0 raw digits / ``[n]`` /
Latin / URLs, and 0 Latin words outside the lexicon), rule usage, lexicon hits/misses, and
whether the whole-book speech text can be built (it must refuse while units are not accepted).
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from b2c_contracts import ArtifactRef, TranslationUnit, UnitTranslation
from b2c_speech.text import (SPOKEN_KINDS, NotSpeechReady, PronunciationLexicon, build_speech_text, normalize_text,
                             speech_text_violations, split_for_tts, strip_restored_markers, wrong_language)
from b2c_translation.termbase import BookTermbase

PATTERNS = {
    "digits": re.compile(r"[0-9٠-٩]"), "year": re.compile(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)"),
    "percent": re.compile(r"[%٪]"), "decimal": re.compile(r"\d[.,٫]\d"),
    "bracket_ref": re.compile(r"\[[\d\s,،\-–]+\]"), "latin_word": re.compile(r"[A-Za-z]{2,}"),
    "url": re.compile(r"https?://|www\.", re.I), "parenthesis": re.compile(r"[()]"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--examples", type=int, default=40)
    args = ap.parse_args()
    data = json.loads(args.export.read_text())
    units = [TranslationUnit.model_validate(u) for u in data["units"]]
    by_id = {u.unit_id: u for u in units}
    results = {r["unit_id"]: UnitTranslation.model_validate(r["translation"]) for r in data["results"]}
    termbase = None
    for snapshot in data["termbases"].values():
        candidate = BookTermbase.from_json(snapshot["json"])
        if candidate.book_id == data["book_id"]:
            termbase = candidate
    lexicon = PronunciationLexicon.from_mappings(terms=termbase.locked_terms() if termbase else None,
                                                 entities=termbase.entity_table() if termbase else None)
    decisions = Counter(t.decision for t in results.values())
    accepted = [uid for uid, t in results.items() if t.decision == "accepted" and uid in by_id]
    spoken_units, excluded = [], Counter()
    before, rules, violations, misses, hits = Counter(), Counter(), Counter(), Counter(), Counter()
    clean = clean_no_miss = 0
    segments = 0
    max_segment = 0
    examples = []
    for uid in accepted:
        unit = by_id[uid]
        structure = SPOKEN_KINDS.get(unit.kind)
        if structure is None:
            excluded[f"not_spoken_kind:{unit.kind}"] += 1
            continue
        text, stripped = strip_restored_markers(results[uid].target_text, unit.footnote_markers)
        if wrong_language(text, "ar"):
            excluded["wrong_language"] += 1
            continue
        spoken_units.append(uid)
        for name, pattern in PATTERNS.items():
            if pattern.search(text):
                before[name] += 1
        if unit.footnote_markers:
            before["footnote_marker"] += 1
        out = normalize_text(text, lexicon=lexicon, structure=structure)
        rules.update(set(out.rules) | ({"footnote_marker_removed"} if stripped else set()))
        found = speech_text_violations(out.text)
        violations.update(found)
        misses.update(out.lexicon_misses)
        hits.update(out.lexicon_hits)
        clean += not found
        clean_no_miss += not found and not out.lexicon_misses
        chunks = split_for_tts(out.text) if out.text.strip() else []
        segments += len(chunks)
        max_segment = max([max_segment, *map(len, chunks)])
        if (found or out.lexicon_misses or set(out.rules) & {"year", "percent", "decimal", "ordinal", "decade", "range",
                                                            "lexicon", "acronym_spelled", "footnote_marker_removed",
                                                            "bracket_reference_removed"}) and len(examples) < args.examples:
            examples.append({"unit_id": uid, "kind": unit.kind, "text": text[:400], "spoken": out.text[:400],
                             "rules": out.rules, "violations": found, "lexicon_misses": out.lexicon_misses})
    source = ArtifactRef(stage="translate-book", key="export", uri="s3://artifacts/export/", sha256="0" * 64,
                         created_at=datetime.now(timezone.utc))
    try:
        build_speech_text(units, results, book_id=data["book_id"], source=source, lexicon=lexicon)
        whole_book = "built"
    except NotSpeechReady as exc:
        whole_book = f"refused: {exc}"
    n = len(spoken_units)
    report = {
        "run_id": data["run_id"][:12], "book_id": data["book_id"], "units_total": len(units),
        "results": dict(decisions), "units_without_result": len(units) - len(results),
        "accepted_units": len(accepted), "accepted_spoken_units": n, "accepted_excluded": dict(excluded),
        "lexicon_entries": len(lexicon), "termbase_found": termbase is not None,
        "units_with_pattern_before": dict(before),
        "rules_used_by_units": dict(rules.most_common()),
        "coverage_no_violations": round(clean / n, 4) if n else None,
        "coverage_no_violations_no_lexicon_miss": round(clean_no_miss / n, 4) if n else None,
        "units_with_violation": dict(violations), "lexicon_misses_distinct": len(misses),
        "lexicon_misses_top": misses.most_common(40), "lexicon_hits_top": hits.most_common(20),
        "tts_segments": segments, "max_segment_chars": max_segment, "whole_book_speech_text": whole_book,
        "examples": examples,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k not in ("examples", "lexicon_misses_top")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
