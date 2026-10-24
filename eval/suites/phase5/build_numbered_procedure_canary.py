import sys, json, re, hashlib, collections
from pathlib import Path
sys.path.extend(["/app/packages/core/src", "/app/packages/contracts/src"])
sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_contracts import StructuredChunk
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0, "/tmp/phase5-live-src")
from b2c_pedagogy.lesson_enrichment import (
    LessonEnrichment, _AssetsCandidate, _GlossaryCandidate, _MCQProposal,
    _Sentence, _SummaryPoint, _build_numbered_procedure_mcqs, _make_lesson_assets,
)

OLD_COURSE = "phase5-seatweaving-saved-raw-rebuilt-v1"
OLD_KEY = "77700e9b931e1f7a65dd6eea03318ceb6c887f395b38333e843382c2e41ddb95"
COURSE = "phase5-seatweaving-procedure-template-v2"
LESSON_ID = "seatweaving.steps2-7"
store = ArtifactStore.from_settings(Settings())
old_key = f"{OLD_COURSE}/phase5-enrichment-canary/{OLD_KEY}/lesson-enrichment.json"
old_bytes = store.s3.get_object(Bucket=store.bucket, Key=old_key)["Body"].read()
old = LessonEnrichment.model_validate_json(old_bytes)
source_pages = [
    (12, Path("/tmp/phase5-live-src/seat-weaving-j016.txt").read_text(encoding="utf-8").strip()),
    (13, Path("/tmp/phase5-live-src/seat-weaving-j017.txt").read_text(encoding="utf-8").strip()),
]
source_hash = hashlib.sha256("\n\n".join(f"{page}\n{text}" for page, text in source_pages).encode()).hexdigest()
expected_source_hash = "69d16728b42a67cacea11b9ebbbeb8962de28f3088a10a4d1d9d628d9d1e681a"
if source_hash != expected_source_hash:
    raise RuntimeError("Seat Weaving source fixture hash changed")
chunks = [StructuredChunk(
    chunk_id=f"seat-weaving-1917:j{page}", book_id="seat-weaving-1917", role="spine",
    section_path=["Caning; The Seven Steps"], block_ids=[f"seatweaving-j{page}"], pages=[page],
    text=text, token_count=max(1, len(text.split())), embedding_model="evaluation-source-not-embedded",
) for page, text in source_pages]
sentences = [_Sentence(text_en=item.text_en, citations=item.citations, kind=item.kind)
             for item in old.draft.sentences]
sid_to_index = {item.sid: index for index, item in enumerate(old.draft.sentences)}
summary_points = [
    _SummaryPoint(text=text, sentence_indices=[sid_to_index[sid] for sid in sids])
    for text, sids in zip(old.summary.points, old.summary.point_sids, strict=True)
]
glossary = [
    _GlossaryCandidate(term_en=item.term_en, definition_en=item.definition_en,
                       sentence_index=sid_to_index[item.first_sid])
    for item in old.glossary
]
questions = _build_numbered_procedure_mcqs(sentences, 20)
if questions is None:
    raise RuntimeError("source lacks twenty unambiguous numbered-step details")
candidate = _AssetsCandidate(sentences=sentences, summary_points=summary_points,
                             glossary=glossary, questions=questions)
lesson = _make_lesson_assets(LESSON_ID, candidate, chunks, prompt_version="phase5-numbered-procedure-v2")
lesson_bytes = lesson.model_dump_json(indent=2).encode()
validated = LessonEnrichment.model_validate_json(lesson_bytes)
if len(validated.questions) != 20:
    raise RuntimeError("final question count mismatch")
option_spreads = [max(map(len, q.options)) - min(map(len, q.options)) for q in validated.questions]
positions = collections.Counter(q.answer_index for q in validated.questions)
bloom = collections.Counter(q.bloom for q in validated.questions)
if max(option_spreads) > 8:
    raise RuntimeError("length lint failed")
if max(positions.values()) - min(positions.values()) > 2:
    raise RuntimeError("answer position distribution failed")
if sum(bloom[name] for name in ("apply", "analyze")) < 16:
    raise RuntimeError("Bloom threshold failed")
question_provenance = []
for question in validated.questions:
    label = question.options[question.answer_index]
    evidence = [next(sentence.text_en for sentence in validated.draft.sentences if sentence.sid == sid)
                for sid in question.evidence_sids]
    question_provenance.append({
        "qid": question.qid, "correct_step_label": label,
        "target_sentence_sids": question.evidence_sids,
        "target_source_excerpt_sha256": hashlib.sha256(" ".join(evidence).encode()).hexdigest(),
        "option_source_sids": question.option_evidence_sids,
    })
provenance = {
    "status": "source_derived_procedure_template_canary",
    "gold_eligible": False,
    "solver_status": "pending",
    "source_sha256": source_hash,
    "source_pages": [12, 13],
    "source_chunks": [item.chunk_id for item in chunks],
    "input_artifact_sha256": hashlib.sha256(old_bytes).hexdigest(),
    "question_count": len(validated.questions),
    "bloom_distribution": dict(bloom),
    "answer_position_distribution": dict(positions),
    "max_option_character_spread": max(option_spreads),
    "question_provenance": question_provenance,
    "generator": "deterministic numbered-step association builder; choices use explicit source labels only",
    "source_and_contract_audit": "passed by _make_lesson_assets and LessonEnrichment schema validation",
    "artifact_sha256": hashlib.sha256(lesson_bytes).hexdigest(),
}
prov_bytes = json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2).encode()
artifact_key = hashlib.sha256(lesson_bytes + prov_bytes).hexdigest()
ref = store.put(COURSE, "phase5-enrichment-canary", artifact_key,
                {"lesson-enrichment.json": lesson_bytes, "rebuild-provenance.json": prov_bytes})
lesson_readback = store.read(ref, "lesson-enrichment.json")
prov_readback = store.read(ref, "rebuild-provenance.json")
if lesson_readback != lesson_bytes or prov_readback != prov_bytes:
    raise RuntimeError("MinIO readback mismatch")
LessonEnrichment.model_validate_json(lesson_readback)
print(json.dumps({
    "status": "source_lint_readback_pass_solver_pending",
    "artifact_ref": ref.model_dump(mode="json"),
    "lesson_sha256": hashlib.sha256(lesson_readback).hexdigest(),
    "provenance_sha256": hashlib.sha256(prov_readback).hexdigest(),
    "source_sha256": source_hash,
    "question_count": len(validated.questions),
    "bloom_distribution": dict(bloom),
    "answer_position_distribution": dict(positions),
    "max_option_character_spread": max(option_spreads),
}, sort_keys=True))
