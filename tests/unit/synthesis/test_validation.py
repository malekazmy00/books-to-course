from b2c_contracts import LessonDraft, LessonSentence, StructuredChunk
from b2c_synthesis.validation import validate_citations


def draft(citations: list[str]) -> LessonDraft:
    return LessonDraft(
        lesson_id="m01.l01",
        version=1,
        sentences=[
            LessonSentence(
                sid="m01.l01:s01",
                text_en="A probability is a number between zero and one.",
                citations=citations,
                kind="primary",
            )
        ],
        faithfulness={"ragas_faithfulness": 0.9, "citation_support_rate": 1.0},
    )


def chunk(chunk_id: str, role: str = "spine") -> StructuredChunk:
    return StructuredChunk(
        chunk_id=chunk_id,
        book_id="book-1",
        role=role,
        section_path=["Probability"],
        block_ids=["book-1:1:0"],
        pages=[1],
        text="A probability is a number between zero and one.",
        token_count=10,
        embedding_model="fixture",
    )


def test_citation_validator_accepts_existing_primary_evidence() -> None:
    report = validate_citations(draft(["chunk-1"]), [chunk("chunk-1")])

    assert report.valid
    assert report.findings == ()


def test_citation_validator_rejects_unknown_chunk_ids() -> None:
    report = validate_citations(draft(["not-in-index"]), [chunk("chunk-1")])

    assert not report.valid
    assert [finding.code for finding in report.findings] == ["citation.unknown_chunk"]


def test_citation_validator_rejects_primary_sentence_grounded_only_in_supplement() -> None:
    report = validate_citations(draft(["supplement"]), [chunk("supplement", "supplementary")])

    assert not report.valid
    assert [finding.code for finding in report.findings] == ["citation.role_mismatch"]
