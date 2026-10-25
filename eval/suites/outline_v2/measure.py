"""Outline before (v1, the approved outlines in MinIO) vs after (v2 from ParsedBook v2), R4.

    uv run python eval/suites/outline_v2/measure.py --name gramsci --v2 parsed-book-v2.json \
        --reference eval/golden/outline/gramsci.json --before before.json --out gramsci.json

``--before`` is a JSON file {name: {"approved": approved-outline.json, "parsed": parsed-book.json}}
copied read-only from the artifact store. Both outlines are scored with the same page-level
metrics against the reference (bookmarks + reviewed zones), because v1 and v2 do not share block
ids; the v2 outline is also scored at block level (b2c_outline.v2.gate) and gated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path

from b2c_contracts import ParsedBookV2
from b2c_outline.v2 import build_outline_v2
from b2c_outline.v2.gate import compare_to_reference, outline_quality
from b2c_outline.v2.reference import resolve_reference, title_key
from b2c_outline.v2.titles import malformed_title

V1_FURNITURE = {"page_header", "page_footer"}
V2_FURNITURE = {"running_header", "running_footer", "page_number"}


def _lessons_v1(approved: dict, parsed: dict) -> tuple[list[dict], list[dict]]:
    blocks = [(b["type"], min(r["page_no"] for r in b["refs"]), len(b["text"].split())) for b in parsed["blocks"]]
    pos = {b["block_id"]: i for i, b in enumerate(parsed["blocks"])}
    modules = []
    for m in approved["outline"]["modules"]:
        lessons = [{"title": lesson["title"], "a": pos[lesson["spine_span"]["start_block"]],
                    "b": pos[lesson["spine_span"]["end_block"]], "kind": "section"} for lesson in m["lessons"]]
        modules.append({"title": m["title"], "lessons": lessons})
    return modules, blocks


def _lessons_v2(outline, book: ParsedBookV2) -> tuple[list[dict], list[tuple]]:
    pos = {b.block_id: i for i, b in enumerate(book.blocks)}
    blocks = [(b.type, min(r.page_no for r in b.refs), len(b.text.split())) for b in book.blocks]
    modules = []
    for m in outline.modules:
        lessons = [{"title": lesson.display_title, "a": pos[lesson.spine_span.start_block],
                    "b": pos[lesson.spine_span.end_block], "kind": lesson.kind,
                    "anchor": pos[lesson.anchor_block]} for lesson in m.lessons]
        modules.append({"title": m.display_title, "lessons": lessons, "anchor": pos[m.anchor_block]})
    return modules, blocks


def page_metrics(modules: list[dict], blocks: list[tuple], reference: list[dict], furniture: set[str]) -> dict:
    """Page-level scores comparable between v1 and v2 outlines."""
    body = [e for e in reference if e["zone"] == "body"]
    front_end = min(e["page"] for e in body)
    back = [e["page"] for e in reference if e["zone"] == "back_matter" and e["page"] > front_end]
    back_start = min(back) if back else 10 ** 6
    top = min(e["level"] for e in body)

    def first_page(lesson: dict) -> int:
        a, b = lesson["a"], lesson["b"]
        pages = [blocks[i][1] for i in range(a, b + 1) if blocks[i][0] not in furniture and blocks[i][0] != "heading"]
        return blocks[lesson.get("anchor", a)][1] if lesson.get("anchor") is not None else (min(pages) if pages else blocks[a][1])

    lessons = [lesson for m in modules for lesson in m["lessons"]]
    words = []
    leak_words = 0
    leak_lessons = 0
    for lesson in lessons:
        w = sum(blocks[i][2] for i in range(lesson["a"], lesson["b"] + 1)
                if blocks[i][0] not in furniture and blocks[i][0] != "heading")
        lw = sum(blocks[i][2] for i in range(lesson["a"], lesson["b"] + 1)
                 if blocks[i][0] not in furniture and (blocks[i][1] < front_end or blocks[i][1] >= back_start))
        words.append(w)
        leak_words += lw
        leak_lessons += lw > 0 and lw * 2 >= w
    total_content = sum(w for t, _, w in blocks if t not in furniture and t != "heading")
    # chapters: a module opens on the chapter's page with the chapter's title
    chapters = [e for e in body if e["level"] == top]
    def module_page(m):
        if m.get("anchor") is not None:
            return blocks[m["anchor"]][1]
        return min(first_page(lesson) for lesson in m["lessons"])
    matched = 0
    used = set()
    for e in chapters:
        for k, m in enumerate(modules):
            if k not in used and abs(module_page(m) - e["page"]) <= 1 and (
                    title_key(m["title"]) == title_key(e["title"])
                    or title_key(e["title"]).endswith(title_key(m["title"])) and len(title_key(m["title"])) >= 8):
                used.add(k)
                matched += 1
                break
    p = matched / len(modules) if modules else 0.0
    r = matched / len(chapters) if chapters else 1.0
    sections = [e for e in body if e["level"] == top + 1]
    sec_ok = sec_wrong = 0
    for e in sections:
        same = [lesson for lesson in lessons if title_key(lesson["title"]) == title_key(e["title"])]
        if any(first_page(lesson) == e["page"] for lesson in same):
            sec_ok += 1
        elif same:
            sec_wrong += 1
    malformed = [t for t in [m["title"] for m in modules] + [lesson["title"] for lesson in lessons] if malformed_title(t)]
    lowercase_or_spaced = [t for t in malformed if set(malformed_title(t)) & {"letter_spacing", "lowercase_start",
                                                                               "multiline", "toc_like"}]
    sizes = sorted(words)
    return {
        "modules": len(modules), "lessons": len(lessons),
        "single_lesson_modules": sum(1 for m in modules if len(m["lessons"]) == 1),
        "chapter_module_precision": round(p, 4), "chapter_module_recall": round(r, 4),
        "chapter_module_f1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "reference_chapters": len(chapters),
        "reference_sections": len(sections), "sections_title_and_page_match": sec_ok,
        "sections_title_on_wrong_page": sec_wrong,
        "malformed_titles": len(lowercase_or_spaced), "titles": len(modules) + len(lessons),
        "front_back_words_in_lessons": leak_words,
        "front_back_share_of_content_words": round(leak_words / total_content, 4) if total_content else 0.0,
        "lessons_mostly_front_back_matter": leak_lessons,
        "lesson_words_min": sizes[0], "lesson_words_median": statistics.median(sizes),
        "lesson_words_p90": sizes[int(len(sizes) * 0.9)], "lesson_words_max": sizes[-1],
        "lessons_under_100_words": sum(1 for s in sizes if s < 100),
        "lessons_over_8000_words": sum(1 for s in sizes if s > 8000),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--v2", required=True, type=Path)
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--before", type=Path)
    ap.add_argument("--course-id", default="b2c-demo")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    raw = args.v2.read_bytes()
    book = ParsedBookV2.model_validate_json(raw)
    ref_doc = json.loads(args.reference.read_text(encoding="utf-8"))
    reference = ref_doc["entries"]
    outline = build_outline_v2(book, course_id=args.course_id, parsed_sha256=hashlib.sha256(raw).hexdigest())
    again = build_outline_v2(book, course_id=args.course_id, parsed_sha256=hashlib.sha256(raw).hexdigest())
    out_bytes = outline.model_dump_json().encode()
    report = outline_quality(book, outline, outline_sha256=hashlib.sha256(out_bytes).hexdigest())
    golden = compare_to_reference(book, outline, resolve_reference(book, reference))
    modules, blocks = _lessons_v2(outline, book)
    result = {
        "name": args.name, "parsed_v2_sha256": hashlib.sha256(raw).hexdigest(),
        "reference": {"file": str(args.reference), "entries": len(reference),
                      "source_file_sha256": ref_doc.get("source_file_sha256")},
        "after": {"page_level": page_metrics(modules, blocks, reference, V2_FURNITURE),
                  "block_level_vs_golden": golden, "gate": json.loads(report.model_dump_json()),
                  "deterministic": again.model_dump_json().encode() == out_bytes,
                  "outline_sha256": hashlib.sha256(out_bytes).hexdigest(),
                  "excluded_ranges": [{"reason": r.reason, "zone": r.zone, "pages": list(r.pages), "title": r.title}
                                      for r in outline.excluded_ranges]},
    }
    if args.before:
        before = json.loads(args.before.read_text(encoding="utf-8"))[args.name]
        modules_v1, blocks_v1 = _lessons_v1(before["approved"], before["parsed"])
        result["before"] = {"approved_key": before.get("approved_key"),
                            "review_type": before["approved"]["approval_provenance"]["review_type"],
                            "page_level": page_metrics(modules_v1, blocks_v1, reference, V1_FURNITURE)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.out.with_suffix(".outline.json")).write_bytes(out_bytes)
    print(json.dumps({k: result[k] for k in ("name",)} | {
        "before": result.get("before", {}).get("page_level"), "after": result["after"]["page_level"],
        "golden": golden, "gate_failed": report.failed, "accepted": report.accepted}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
