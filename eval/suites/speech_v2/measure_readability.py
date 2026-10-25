#!/usr/bin/env python3
"""Reading quality of the speech text front end on real accepted units (offline, no TTS call).

Usage:
    uv run python eval/suites/speech_v2/measure_readability.py --export run.json --out report.json

``run.json`` is the read-only export described in ``measure_text_frontend.py`` (``units``,
``results`` and ``termbases``); only ``accepted`` results are used. The script builds the
speech-text document of the accepted spoken units with the current front end and reports, from
the source (accepted target text) and from the spoken segments:

- punctuation problems in the source (Latin comma/semicolon/question mark inside Arabic, missing
  final punctuation, nested or unbalanced brackets, repeated punctuation, dashes, quotes);
- parenthesised/bracketed Latin glosses («الهيمنة (hegemony)») and what the front end did;
- spoken sentence length (words) and phrase/segment length (chars) distributions;
- the estimated re-chunking inside Fasih (its XTTS chunker cuts DIACRITIZED text at 160 chars
  with a fixed 120 ms gap and hard-wraps when there is no ``. ؟ ! ؛ ،`` boundary);
- the pause plan per boundary type, headings/lists, and characters a voice cannot speak.
Nothing here calls a model: it is a deterministic, offline measurement.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from b2c_contracts import ArtifactRef, TranslationUnit, UnitTranslation
from b2c_speech.text import SPOKEN_KINDS, PronunciationLexicon, build_speech_text
from b2c_translation.termbase import BookTermbase

AR = "ء-يٱ-ۓ"
FASIH_CHUNK = 160  # docker/fasih tts.text.chunk.chunk_text(max_chars=160), applied after CATT
DIACRITIZED_EXPANSION = 1.8  # estimate: ~0.85 marks per letter once CATT fully diacritizes (UNVERIFIED)
SOURCE_CHECKS = {
    "latin_comma_in_arabic": re.compile(rf"[{AR}]\s*,\s*[{AR}]"),
    "latin_semicolon_in_arabic": re.compile(rf"[{AR}]\s*;"),
    "latin_question_mark": re.compile(rf"[{AR}]\s*\?"),
    "nested_parentheses": re.compile(r"\([^()]*\([^()]*\)[^()]*\)"),
    "repeated_punctuation": re.compile(r"([.،,؛;:!?؟])\s*\1"),
    "dash": re.compile(r"[—–]"),
    "quotes": re.compile(r"[«»“”\"]"),
    "ellipsis": re.compile(r"\.{3}|…"),
    "space_before_punctuation": re.compile(rf"[{AR}]\s+[.،؛:!؟]"),
}
LATIN_GLOSS = re.compile(r"[(\[]([^()\[\]]*[A-Za-z][^()\[\]]*)[)\]]")
ALLOWED_SPOKEN = re.compile(rf"[{AR}ً-ْٰ\s.،؛:!؟]")


def _pct(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(q * len(ordered)))])


def _dist(values: list[int]) -> dict[str, float]:
    return {"n": len(values), "p50": float(median(values)) if values else 0.0, "p90": _pct(values, 0.9),
            "p95": _pct(values, 0.95), "max": float(max(values, default=0))}


def _fasih_split(text: str) -> tuple[int, bool]:
    """(chunks, hard-wrapped) for the estimated diacritized length of one TTS request."""
    est = len(text) * DIACRITIZED_EXPANSION
    if est <= FASIH_CHUNK:
        return 1, False
    limit = FASIH_CHUNK / DIACRITIZED_EXPANSION
    pieces = [p for p in re.split(r"(?<=[.؟!؛])\s+", text) if p]
    chunks, hard = 0, False
    for piece in pieces:
        if len(piece) <= limit:
            chunks += 1
            continue
        for clause in [c for c in re.split(r"(?<=،)\s*", piece) if c]:
            if len(clause) <= limit:
                chunks += 1
            else:
                hard = True
                chunks += int(len(clause) // limit) + 1
    return chunks, hard


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--examples", type=int, default=12)
    args = ap.parse_args()
    data = json.loads(args.export.read_text())
    units = [TranslationUnit.model_validate(u) for u in data["units"]]
    results = {r["unit_id"]: UnitTranslation.model_validate(r["translation"]) for r in data["results"]}
    termbase = None
    for snapshot in data["termbases"].values():
        candidate = BookTermbase.from_json(snapshot["json"])
        if candidate.book_id == data["book_id"]:
            termbase = candidate
    lexicon = PronunciationLexicon.from_mappings(terms=termbase.locked_terms() if termbase else None,
                                                 entities=termbase.entity_table() if termbase else None)
    accepted = [u for u in units if u.unit_id in results and results[u.unit_id].decision == "accepted"]
    spoken_units = [u for u in accepted if u.kind in SPOKEN_KINDS]
    source = ArtifactRef(stage="translate-book", key="export", uri="s3://artifacts/export/", sha256="0" * 64,
                         created_at=datetime.now(timezone.utc))
    doc = build_speech_text(accepted, {u.unit_id: results[u.unit_id] for u in accepted}, book_id=data["book_id"],
                            source=source, lexicon=lexicon)

    # ------------------------------------------------------------ source text
    problems: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    glosses: Counter[str] = Counter()
    gloss_examples: list[str] = []
    for u in spoken_units:
        text = results[u.unit_id].target_text
        for name, pattern in SOURCE_CHECKS.items():
            if pattern.search(text):
                problems[name] += 1
                examples.setdefault(name, [])
                if len(examples[name]) < 3:
                    m = pattern.search(text)
                    examples[name].append(text[max(0, m.start() - 40):m.end() + 40])
        if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
            problems["unbalanced_brackets"] += 1
        if u.kind == "prose" and not re.search(r"[.!؟?…:»\"”)]\s*$", text.strip()):
            problems["prose_without_final_punctuation"] += 1
        if u.kind == "heading" and re.search(r"[.!؟?:]\s*$", text.strip()):
            problems["heading_with_final_punctuation"] += 1
        for m in LATIN_GLOSS.finditer(text):
            inner = m.group(1).strip()
            if re.search(rf"[{AR}]", inner):
                continue
            kind = ("citation" if re.search(r"\d|,|:|et al", inner) else
                    "acronym" if re.fullmatch(r"(?:[A-Z]\.?){2,6}s?", inner) else
                    "term" if len(inner.split()) <= 3 else "title_or_phrase")
            glosses[kind] += 1
            if len(gloss_examples) < args.examples:
                gloss_examples.append(text[max(0, m.start() - 30):m.end() + 5])

    # ------------------------------------------------------------ spoken text
    sentence_words: list[int] = []
    by_unit: dict[str, list[str]] = {}
    for s in doc.segments:
        by_unit.setdefault(s.unit_ids[0], []).append(s.spoken_text)
    for parts in by_unit.values():
        for sentence in re.split(r"(?<=[.!؟])\s+", " ".join(parts)):
            if sentence.strip():
                sentence_words.append(len(sentence.split()))
    seg_chars = [len(s.spoken_text) for s in doc.segments]
    seg_words = [len(s.spoken_text.split()) for s in doc.segments]
    fasih = [_fasih_split(s.spoken_text) for s in doc.segments]
    pauses: Counter[str] = Counter()
    boundaries: Counter[str] = Counter()
    unspeakable: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    rules_by_unit: dict[str, set[str]] = {}
    unterminated = 0
    for i, s in enumerate(doc.segments):
        boundary = getattr(s, "boundary_after", None) or "unset"
        boundaries[boundary] += 1
        pauses[f"{boundary}:{s.pause_after_ms}"] += 1
        unspeakable.update(ch for ch in s.spoken_text if not ALLOWED_SPOKEN.match(ch))
        rules_by_unit.setdefault(s.unit_ids[0], set()).update(s.rules_applied)
        if boundary not in ("phrase",) and not re.search(r"[.!؟:]$", s.spoken_text):
            unterminated += 1
    for unit_rules in rules_by_unit.values():
        rules.update(unit_rules)
    gloss_spoken = []
    for uid, unit_rules in rules_by_unit.items():
        if unit_rules & {"latin_gloss_voiced", "latin_gloss_unvoiced"} and len(gloss_spoken) < args.examples:
            text = " | ".join(s.spoken_text for s in doc.segments if s.unit_ids[0] == uid)
            gloss_spoken.append({"source": results[uid].target_text[:240], "spoken": text[:300],
                                 "unvoiced": sorted({g for s in doc.segments if s.unit_ids[0] == uid
                                                     for g in getattr(s, "unvoiced_glosses", [])})})
    reading = None
    try:
        from b2c_speech.text import evaluate_reading_quality
        passed, metrics = evaluate_reading_quality(doc, "fasih-xtts")
        reading = {"passed": passed, **{k: v for k, v in metrics.items() if k != "pause_plan_errors"},
                   "pause_plan_errors_sample": metrics["pause_plan_errors"][:5]}
    except ImportError:  # front end before the readability work
        pass
    heading_idx = [i for i, s in enumerate(doc.segments) if s.structure == "heading"]
    before_heading = [doc.segments[i - 1].pause_after_ms for i in heading_idx if i > 0
                      and doc.segments[i - 1].structure != "heading"]
    report = {
        "run_id": data["run_id"][:12], "book_id": data["book_id"], "accepted_units": len(accepted),
        "spoken_units": len(spoken_units), "spoken_by_kind": dict(Counter(u.kind for u in spoken_units)),
        "excluded": dict(Counter(e.reason for e in doc.excluded)),
        "source_problems_units": dict(problems.most_common()), "source_problem_examples": examples,
        "latin_glosses": dict(glosses), "latin_gloss_examples": gloss_examples,
        "gloss_rules_units": {k: v for k, v in rules.items() if "gloss" in k or "parenthetical" in k or "citation" in k},
        "rules_units": dict(rules.most_common()), "gloss_spoken_examples": gloss_spoken,
        "reading_quality_gate": reading,
        "spoken_sentence_words": _dist(sentence_words),
        "sentences_over_25_words": sum(w > 25 for w in sentence_words),
        "sentences_over_35_words": sum(w > 35 for w in sentence_words),
        "sentences_over_50_words": sum(w > 50 for w in sentence_words),
        "segments": len(doc.segments), "segment_chars": _dist(seg_chars), "segment_words": _dist(seg_words),
        "fasih_requests_rechunked": sum(c > 1 for c, _ in fasih),
        "fasih_internal_chunks": sum(c for c, _ in fasih), "fasih_hardwrap_segments": sum(h for _, h in fasih),
        "segments_unterminated_at_sentence_or_more": unterminated,
        "boundaries": dict(boundaries), "pause_plan": dict(sorted(pauses.items())),
        "headings": len(heading_idx), "pause_before_heading_ms": dict(Counter(before_heading)),
        "pause_after_heading_ms": dict(Counter(doc.segments[i].pause_after_ms for i in heading_idx)),
        "unspeakable_chars": dict(unspeakable.most_common(20)),
        "frontend_version": doc.frontend_version,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("examples")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
