"""Index v2 measurement (R5, T-IDX-04..08): the production index (before) vs the v2 pipeline
(after) on real books. Every number comes from the code that runs in production:

* before = the live ``b2c_indexed_chunks`` rows (dump_production.py), measured with the legacy
  consumer logic (lexical lesson retrieval exactly as qa_generation/qa.py ran it);
* after  = ``b2c_indexing.index_book`` on ParsedBook v2 (and on the same v1 input), consumed
  by ``b2c_pedagogy.qa_generation._derive_scopes``; linking with ``link_supplementary``.

Labelled data used where it exists: the translation audit's per-segment ``source_issue``
labels (non-body and OCR-noise segments of the production v1 books). Retrieval qrels for
these two books do not exist, so Recall@k / nDCG are NOT computed (reported as such).

Writes counts only (no book text) to --out.

  uv run python eval/suites/indexing_v2/measure.py --prod DATA/prod.json \
     --book sat:book-main:0486...:DATA/sat-v1.json:DATA/sat-v2.json \
     --book gramsci:book-70c...:97f2...:DATA/gramsci-v1.json:DATA/gramsci-v2.json \
     --gold /mnt/wsl/b2cdata/eval-datasets/translation-audit-2026-10-24/gold.jsonl --out out.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from collections import Counter
from pathlib import Path

from b2c_contracts import CourseOutline, ParsedBookV2, StructuredChunk
from b2c_contracts.models import LessonSpan, ParsedBook
from b2c_indexing import chunking, index_book, lexical_search
from b2c_indexing.content_gate import CORRUPT_FLAGS, build_vocab, gate_blocks, text_flags
from b2c_indexing.linking import SUPPLEMENT_LINK_POLICY, LinkPolicy, link_supplementary
from b2c_pedagogy.qa_generation import _derive_scopes

NON_BODY_LABELS = {"toc_or_index", "reference_list", "header_footer"}
SAMPLE_SEED = "indexing-v2-2026-10-25"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def dist(values) -> dict:
    return {"n": len(values), "min": min(values, default=None), "p5": pct(values, .05), "p50": pct(values, .5),
            "p95": pct(values, .95), "max": max(values, default=None)}


def lesson_spans(outline: CourseOutline, ids: list[str]) -> dict[str, set[str]]:
    pos = {b: i for i, b in enumerate(ids)}
    out = {}
    for m in outline.modules:
        for lesson in m.lessons:
            a, z = pos[lesson.spine_span.start_block], pos[lesson.spine_span.end_block]
            out[lesson.lesson_id] = set(ids[a:z + 1])
    return out


# ----------------------------------------------------------------------------- before
def measure_before(chunks: list[StructuredChunk], v1: ParsedBook, outline: CourseOutline,
                   labels: dict[str, str]) -> dict:
    by_id = {b.block_id: b for b in v1.blocks}
    gated = {g.block_id: g for g in gate_blocks(v1)}
    vocab = build_vocab(b.text for b in v1.blocks)
    tokens = [chunking.estimate_tokens(c.text) for c in chunks]
    block_use = Counter(b for c in chunks for b in c.block_ids)
    heading_only = sum(1 for c in chunks if all(by_id[b].type == "heading" for b in c.block_ids if b in by_id))
    non_body_chunks = sum(1 for c in chunks if any(gated[b].evidence != "body" for b in c.block_ids if b in gated))
    corrupt = sum(1 for c in chunks if CORRUPT_FLAGS.intersection(text_flags(c.text, vocab=vocab)))
    glued = sum(1 for c in chunks if "glued_words" in text_flags(c.text, vocab=vocab))
    labelled_chunks = sum(1 for c in chunks if any(labels.get(b) in NON_BODY_LABELS for b in c.block_ids))
    # C1: the lesson "retrieval" exactly as qa.py + qa_generation ran it (lexical, top_k=20,
    # query = scope title + lesson titles; no hit -> the lexical route falls back to all candidates)
    spans = lesson_spans(outline, [b.block_id for b in v1.blocks])
    titles = {lesson.lesson_id: lesson.title for m in outline.modules for lesson in m.lessons}
    dropped_lessons = dropped_chunks = in_lessons = 0
    for lid, ids in spans.items():
        own = [c for c in chunks if set(c.block_ids) & ids]
        if not own:
            continue
        in_lessons += len(own)
        hits = lexical_search(" ".join([titles[lid], titles[lid]]), own, top_k=20)
        kept = len(hits) if hits else len(own)
        if kept < len(own):
            dropped_lessons += 1
            dropped_chunks += len(own) - kept
    return {
        "chunks": len(chunks), "tokens": dist(tokens),
        "chunks_under_50_tokens": sum(t < 50 for t in tokens), "chunks_over_800_tokens": sum(t > 800 for t in tokens),
        "heading_only_chunks": heading_only,
        "blocks_in_more_than_one_chunk": sum(1 for n in block_use.values() if n > 1),
        "chunks_with_non_body_by_gate": non_body_chunks,
        "chunks_with_non_body_by_audit_labels": labelled_chunks,
        "chunks_with_corrupt_text": corrupt, "chunks_with_glued_words": glued,
        "embedding_model_values": sorted({c.embedding_model for c in chunks}),
        "lessons": len(spans), "lesson_chunks": in_lessons,
        "c1_lessons_losing_chunks": dropped_lessons, "c1_chunks_not_reaching_generator": dropped_chunks,
    }


# ----------------------------------------------------------------------------- outline mapping
def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def map_outline(outline: CourseOutline, v1: ParsedBook, v2: ParsedBookV2) -> tuple[CourseOutline, dict]:
    """Carry the production lesson boundaries over to v2 blocks (text match near the page)."""
    v1_by_id = {b.block_id: b for b in v1.blocks}
    v2_text = [_squash(b.text) for b in v2.blocks]
    v2_page = [b.refs[0].page_no for b in v2.blocks]
    starts, exact, fallback = [], 0, 0
    cursor = 0
    lessons = [(m, lesson) for m in outline.modules for lesson in m.lessons]
    for _, lesson in lessons:
        src = v1_by_id[lesson.spine_span.start_block]
        key, page = _squash(src.text)[:25], src.refs[0].page_no
        found = None
        if key:
            for i in range(cursor, len(v2.blocks)):
                if v2_page[i] > page + 2:
                    break
                if v2_page[i] >= page - 1 and v2.blocks[i].zone != "furniture" and (
                        v2_text[i].startswith(key) or (v2_text[i] and key.startswith(v2_text[i][:25]))
                        or key in v2_text[i]):
                    found = i
                    break
        if found is None:
            found = next((i for i in range(cursor, len(v2.blocks)) if v2_page[i] >= page
                          and v2.blocks[i].zone != "furniture"), cursor)
            fallback += 1
        else:
            exact += 1
        found = max(found, starts[-1] + 1 if starts else 0)
        starts.append(found)
        cursor = found + 1
    if starts:
        starts[0] = 0  # the production outline starts at the first block of the book
    ids = [b.block_id for b in v2.blocks]
    modules, n = [], 0
    for m in outline.modules:
        new_lessons = []
        for lesson in m.lessons:
            a = starts[n]
            z = (starts[n + 1] - 1) if n + 1 < len(starts) else len(ids) - 1
            n += 1
            new_lessons.append(lesson.model_copy(update={"spine_span": LessonSpan(
                start_block=ids[a], end_block=ids[max(a, z)], pages=(v2_page[a], v2_page[max(a, z)]))}))
        modules.append(m.model_copy(update={"lessons": new_lessons}))
    mapped = outline.model_copy(update={"modules": modules, "spine_book_id": v2.book_id,
                                        "excluded_prefix_block_ids": []})
    return mapped, {"lessons": len(lessons), "matched_by_text": exact, "matched_by_page": fallback}


# ----------------------------------------------------------------------------- after
def measure_after(book, outline: CourseOutline) -> tuple[dict, list[StructuredChunk]]:
    t = time.time()
    build = index_book(book, outline=outline)
    seconds = round(time.time() - t, 2)
    body = [c for c in build.chunks if c.evidence_class == "body"]
    spine = book  # v1 or v2: _derive_scopes reads only book_id, role and blocks[].block_id
    lessons_ok = [lid for lid in build.lesson_blocks]
    # consumer side: what the lesson generator receives (lesson scopes of _derive_scopes)
    reach = {"lessons_checked": 0, "lessons_mismatch": 0, "chunks_expected": 0, "chunks_received": 0}
    try:
        trimmed = outline.model_copy(update={"modules": [m.model_copy(update={"lessons": [
            lesson for lesson in m.lessons if lesson.lesson_id in lessons_ok]}) for m in outline.modules
            if any(lesson.lesson_id in lessons_ok for lesson in m.lessons)]})
        scopes, _ = _derive_scopes(trimmed, spine, body, lesson_question_count=1, group_question_count=1,
                                   course_question_count=1, outline_approved=True)
        for scope in scopes:
            if scope.kind != "lesson":
                continue
            expected = [c.chunk_id for c in body if c.lesson_id == scope.scope_id]
            got = [c.chunk_id for c in scope.chunks]
            reach["lessons_checked"] += 1
            reach["chunks_expected"] += len(expected)
            reach["chunks_received"] += len(got)
            reach["lessons_mismatch"] += sorted(expected) != sorted(got)
    except ValueError as exc:
        reach["error"] = str(exc)[:200]
    m = build.report.metrics
    return {"seconds": seconds, "accepted": build.report.accepted, "failed_checks": build.report.failed,
            "metrics": m, "chunks_under_50_tokens": sum(c.token_count < 50 for c in body),
            "chunks_over_800_tokens": sum(c.token_count > 800 for c in body),
            "lessons_without_body_ids": [lid for m_ in outline.modules for lid in
                                         [lesson.lesson_id for lesson in m_.lessons] if lid not in build.lesson_blocks],
            "consumer_coverage": reach}, build.chunks


_WORDS = re.compile(r"[a-z]{3,}")


def map_segments(v1: ParsedBook, v2: ParsedBookV2, segment_ids: set[str]) -> dict[str, str | None]:
    """v1 segment -> the v2 block on the same page (+-1) that is the same unit (None = no one-to-one match)."""
    by_page: dict[int, list] = {}
    for b in v2.blocks:
        for page in {r.page_no for r in b.refs}:
            by_page.setdefault(page, []).append(b)
    out: dict[str, str | None] = {}
    for b in v1.blocks:
        if b.block_id not in segment_ids:
            continue
        words = Counter(_WORDS.findall(b.text.lower()))
        best, best_hit = None, 0
        for page in {r.page_no + d for r in b.refs for d in (-1, 0, 1)}:
            for cand in by_page.get(page, []):
                hit = sum((words & Counter(_WORDS.findall(cand.text.lower()))).values())
                if hit > best_hit or (hit == best_hit and best is not None and cand.block_id < best.block_id):
                    best, best_hit = cand, hit
        # one-to-one only: >= 60% of the words of each side are shared (a v1 segment that merged
        # a mini-TOC with body text has no single v2 counterpart and stays unmapped)
        ok = best is not None and best_hit >= 0.6 * sum(words.values()) and \
            best_hit >= 0.6 * len(_WORDS.findall(best.text.lower()))
        out[b.block_id] = best.block_id if ok else None
    return out


def label_check(v1: ParsedBook, v2: ParsedBookV2, labels: dict[str, str | None], before: list[StructuredChunk],
                after_v2: list[StructuredChunk]) -> dict:
    """Audit-labelled non-body and OCR-noise segments: do they reach lesson content?"""
    gated = {g.block_id: g for g in gate_blocks(v1)}
    nb = {b for b, lab in labels.items() if lab in NON_BODY_LABELS and b in gated}
    ocr = {b for b, lab in labels.items() if lab == "ocr_noise" and b in gated}
    in_before = {b for c in before for b in c.block_ids}
    in_after = {b for c in after_v2 if c.evidence_class == "body" for b in c.block_ids}
    mapped = map_segments(v1, v2, nb | ocr)
    return {
        "labelled_non_body": len(nb),
        "labelled_non_body_in_production_chunks": len(nb & in_before),
        "labelled_non_body_kept_out_by_v1_gate": sum(gated[b].evidence != "body" or gated[b].quarantined for b in nb),
        "labelled_non_body_mapped_to_v2": sum(mapped[b] is not None for b in nb),
        "labelled_non_body_in_v2_lesson_content": sum(mapped[b] in in_after for b in nb if mapped[b]),
        "labelled_ocr_noise": len(ocr),
        "labelled_ocr_noise_in_production_chunks": len(ocr & in_before),
        "labelled_ocr_noise_flagged_by_v1_gate": sum(bool(gated[b].flags) for b in ocr),
    }


# ----------------------------------------------------------------------------- linking
def by_lesson(chunks):
    out: dict[str, list[StructuredChunk]] = {}
    for c in chunks:
        if c.evidence_class == "body" and c.lesson_id:
            out.setdefault(c.lesson_id, []).append(c)
    return out


def as_supp(chunks):
    return [c.model_copy(update={"role": "supplementary"}) for c in chunks if c.evidence_class == "body"]


def old_links(lessons, titles, supp):
    """qa.py before R5: title + 600 chars of the first 4 chunks, lexical top-5, no threshold."""
    n = []
    for lid, chunks in lessons.items():
        query = titles.get(lid, lid) + " " + " ".join(c.text[:600] for c in chunks[:4])
        n.append(len(lexical_search(query, supp, top_k=5)))
    return n


def linking(name_a, chunks_a, titles_a, name_b, chunks_b) -> dict:
    lessons, supp = by_lesson(chunks_a), as_supp(chunks_b)
    _, judged = link_supplementary(lessons, supp, policy=LinkPolicy(threshold=0.0, top_k=5))
    top1 = [j.score for j in judged if j.rank == 1]
    linked, _ = link_supplementary(lessons, supp)
    old = old_links(lessons, titles_a, supp)
    return {"pair": f"{name_a}->{name_b}", "lessons": len(lessons),
            "old_links_total": sum(old), "old_lessons_with_links": sum(1 for x in old if x),
            "new_links_total": sum(len(v) for v in linked.values()),
            "new_lessons_with_links": sum(1 for v in linked.values() if v),
            "top1_score": dist(top1)}


def self_reference(chunks) -> dict:
    """Score scale: a lesson against the whole book (its own chunks included) as candidates."""
    lessons, supp = by_lesson(chunks), as_supp(chunks)
    _, judged = link_supplementary(lessons, supp, policy=LinkPolicy(threshold=0.0, top_k=1))
    own = {c.chunk_id: c.lesson_id for c in chunks}
    return {"lessons": len(lessons), "top1_is_own_chunk": sum(own.get(j.chunk_id) == j.lesson_id for j in judged),
            "top1_score": dist([j.score for j in judged])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", type=Path, required=True)
    ap.add_argument("--book", action="append", required=True, help="name:book_id:outline_key:v1.json:v2.json")
    ap.add_argument("--gold", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sample-dir", type=Path, help="write the 20-chunk human review sample here (outside git)")
    args = ap.parse_args()
    prod = json.loads(args.prod.read_text())
    labels: dict[str, str | None] = {}
    if args.gold:
        for line in args.gold.read_text().splitlines():
            row = json.loads(line)
            issue = (row.get("source_issue") or {}).get("type")
            labels[row["segment_id"]] = issue
    out: dict = {"inputs": {"prod_dump_sha256": sha256(args.prod), "prod_updated_at": prod.get("updated_at"),
                            "gold_sha256": sha256(args.gold) if args.gold else None},
                 "books": {}, "linking": [], "self_reference": {},
                 "not_measured": ["recall@k/nDCG@k: no retrieval qrels exist for these two books (R2 pending)",
                                  "lesson-link precision@5: no human relevance labels; threshold uncalibrated",
                                  "embeddings/vectors: index v2 still lexical-only (H2 open)"]}
    after_chunks: dict[str, list[StructuredChunk]] = {}
    titles_by_book: dict[str, dict[str, str]] = {}
    for spec in args.book:
        name, book_id, okey, v1_path, v2_path = spec.split(":")
        v1 = ParsedBook.model_validate_json(Path(v1_path).read_text())
        v2 = ParsedBookV2.model_validate_json(Path(v2_path).read_text())
        outline = CourseOutline.model_validate(prod["outlines"][okey]["outline"])
        chunks = [StructuredChunk.model_validate(p) for p in prod["chunks"][book_id]]
        book_labels = {k: v for k, v in labels.items() if k.startswith(book_id + ":")}
        mapped, mapping = map_outline(outline, v1, v2)
        after_v2, chunks_v2 = measure_after(v2, mapped)
        after_v1, _ = measure_after(v1, outline)
        after_chunks[name] = chunks_v2
        titles_by_book[name] = {lesson.lesson_id: lesson.title for m in outline.modules for lesson in m.lessons}
        out["books"][name] = {
            "book_id": book_id, "outline_key": okey,
            "inputs": {"v1_sha256": sha256(Path(v1_path)), "v2_sha256": sha256(Path(v2_path)),
                       "v1_blocks": len(v1.blocks), "v2_blocks": len(v2.blocks)},
            "before_production": measure_before(chunks, v1, outline, book_labels),
            "after_v2": after_v2, "after_v1_same_input": after_v1, "outline_mapping_to_v2": mapping,
            "audit_labels": label_check(v1, v2, book_labels, chunks, chunks_v2),
        }
        out["self_reference"][name] = self_reference(chunks_v2)
        if args.sample_dir:  # plan §2.2 S5 HITL: 20 random body chunks per book for a human reader
            body = sorted((c for c in chunks_v2 if c.evidence_class == "body"), key=lambda c: c.chunk_id)
            pick = random.Random(f"{name}:{SAMPLE_SEED}").sample(body, min(20, len(body)))
            args.sample_dir.mkdir(parents=True, exist_ok=True)
            (args.sample_dir / f"{name}-review-sample.jsonl").write_text("".join(
                json.dumps({"chunk_id": c.chunk_id, "lesson_id": c.lesson_id, "pages": c.pages,
                            "flags": c.quality_flags, "text": c.text, "verdict": None}, ensure_ascii=False) + "\n"
                for c in pick))
            out["books"][name]["review_sample"] = {"size": len(pick), "seed": SAMPLE_SEED, "reviewed": False}
    names = list(after_chunks)
    for a in names:
        for b in names:
            if a != b:
                out["linking"].append(linking(a, after_chunks[a], titles_by_book[a], b, after_chunks[b]))
    out["link_policy"] = {"threshold": SUPPLEMENT_LINK_POLICY.threshold, "top_k": SUPPLEMENT_LINK_POLICY.top_k,
                          "version": SUPPLEMENT_LINK_POLICY.version, "calibrated": False}
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=1, ensure_ascii=False)[:6000])


if __name__ == "__main__":
    main()
