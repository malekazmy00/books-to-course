from b2c_contracts.models import StructuredChunk
from b2c_core.arabic_text import normalize_arabic, strip_arabic_marks
from b2c_indexing import estimate_tokens, lexical_search


def test_normaliser_strips_marks_tatweel_superscript_alef_and_unifies_alef():
    assert normalize_arabic("كَتَبَ الطَّالِبُ") == "كتب الطالب"
    assert normalize_arabic("الـــعلم") == "العلم"
    assert normalize_arabic("هٰذا") == "هذا"
    assert normalize_arabic("أحمد إلى آمن ٱلكتاب") == "احمد الى امن الكتاب"
    # Decomposed hamza (alef + U+0654) composes under NFKC and is then unified.
    assert normalize_arabic("أ") == "ا"
    assert normalize_arabic("Soil ﬁeld") == "Soil field"
    assert strip_arabic_marks("أَ") == "أ"


def test_diacritics_do_not_inflate_token_estimates():
    assert estimate_tokens("كَتَبَ الطَّالِبُ") == 2
    assert estimate_tokens("كتب الطالب") == 2
    assert estimate_tokens("Hello, world.") == 4


def _chunk(chunk_id, text):
    return StructuredChunk(chunk_id=chunk_id, book_id="b", role="spine", section_path=["s"],
                           block_ids=[chunk_id], pages=[1], text=text, token_count=1, embedding_model="m")


def test_lexical_search_matches_across_diacritics_and_alef_forms():
    chunks = [_chunk("c1", "كَتَبَ الطَّالِبُ الدرسَ"), _chunk("c2", "نص آخر عن الزراعة")]
    hits = lexical_search("كتب الطالب", chunks)
    assert [chunk.chunk_id for chunk, _ in hits] == ["c1"]
    assert [c.chunk_id for c, _ in lexical_search("إلى", [_chunk("c3", "ذهب الى السوق")])] == ["c3"]
