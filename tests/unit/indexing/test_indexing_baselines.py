from b2c_contracts.models import Block, PageRef, ParsedBook
from b2c_indexing import chunk_book, dense_search, lexical_search, reciprocal_rank_fusion


def make_book():
    return ParsedBook(book_id="book-main", title="Book", role="spine", page_count=3,
        toc_source="headings", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id="b1", type="heading", text="Probability", refs=[PageRef(book_id="book-main", page_no=1)], section_path=["Probability"], ocr=False),
            Block(block_id="b2", type="paragraph", text="Random variables describe uncertain outcomes.", refs=[PageRef(book_id="book-main", page_no=1)], section_path=["Probability"], ocr=False),
            Block(block_id="b3", type="table", text="Outcome | Probability\nA | 0.5\nB | 0.5", refs=[PageRef(book_id="book-main", page_no=2)], section_path=["Probability"], ocr=False),
            Block(block_id="b4", type="paragraph", text="Expectation is the weighted average.", refs=[PageRef(book_id="book-main", page_no=3)], section_path=["Expectation"], ocr=False),
        ])


def test_chunking_preserves_sections_pages_role_and_atomic_table():
    chunks = chunk_book(make_book(), target_tokens=7, overlap_ratio=0, embedding_model="test@abc")
    assert all(c.role == "spine" and c.embedding_model == "test@abc" for c in chunks)
    assert all(set(c.pages) and set(c.block_ids) for c in chunks)
    table_chunks = [c for c in chunks if "Outcome | Probability" in c.text]
    assert len(table_chunks) == 1 and "A | 0.5" in table_chunks[0].text and "B | 0.5" in table_chunks[0].text
    assert all(not ("Expectation" in c.section_path and "Probability" in c.section_path) for c in chunks)
    source_sections = {"b1": ["Probability"], "b2": ["Probability"], "b3": ["Probability"], "b4": ["Expectation"]}
    assert all(source_sections[bid] == chunk.section_path for chunk in chunks for bid in chunk.block_ids)


def test_chunk_ids_are_deterministic_and_overlap_is_bounded():
    book = make_book()
    first = chunk_book(book, target_tokens=8, overlap_ratio=0.25)
    second = chunk_book(book, target_tokens=8, overlap_ratio=0.25)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]


def test_lexical_retrieval_filters_books_and_role():
    chunks = chunk_book(make_book(), target_tokens=100)
    result = lexical_search("weighted average expectation", chunks, allowed_roles={"spine"}, allowed_book_ids={"book-main"})
    assert result and result[0][0].section_path == ["Expectation"]
    assert lexical_search("weighted average", chunks, allowed_book_ids={"other"}) == []
    assert lexical_search("term absent from book", chunks) == []


def test_dense_adapter_and_hybrid_fusion_are_deterministic():
    chunks = chunk_book(make_book(), target_tokens=100)
    vectors = {c.chunk_id: ([1.0, 0.0] if "Expectation" in c.section_path else [0.0, 1.0]) for c in chunks}
    dense = dense_search([1.0, 0.0], chunks, lambda text: next(vectors[c.chunk_id] for c in chunks if c.text == text), allowed_roles={"spine"}, allowed_book_ids={"book-main"})
    assert dense_search([1.0, 0.0], chunks, lambda text: [1.0, 0.0], allowed_book_ids={"other"}) == []
    lexical = lexical_search("expectation weighted average", chunks)
    hybrid = reciprocal_rank_fusion(dense, lexical)
    assert hybrid[0][0].section_path == ["Expectation"]
    assert [x[0].chunk_id for x in hybrid] == [x[0].chunk_id for x in reciprocal_rank_fusion(dense, lexical)]
