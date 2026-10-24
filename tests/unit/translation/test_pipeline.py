from b2c_translation.generation import translation_completeness_issue
import pytest
from b2c_contracts import GlossaryTerm, TermbaseEntry
from b2c_translation.pipeline import (build_termbase, chunk_text,
 deterministic_translation_check, termbase_gate, translation_needs_review,
 term_injection_block)

def test_chunks_target_and_preserves_words():
    src = " ".join(f"word{i}" for i in range(1200))
    parts = chunk_text(src, target_words=500)
    assert [p.words for p in parts] == [500, 500, 200]
    assert " ".join(p.text for p in parts).split() == src.split()

def test_oversized_sentence_preserved():
    sentence = " ".join(f"word{i}" for i in range(20)) + "."
    assert chunk_text(sentence, target_words=5)[0].text == sentence

def test_chunk_rejects_invalid_target():
    with pytest.raises(ValueError):
        chunk_text("text", target_words=0)

def test_termbase_requires_mapping_and_never_implicitly_approves():
    glossary = [GlossaryTerm(term_en="mean", definition_en="average", first_sid="s1", lesson_id="l1")]
    draft = build_termbase(glossary, {" Mean ": "\u0627\u0644\u0645\u062a\u0648\u0633\u0637"})
    assert draft[0].approved_by is None
    assert termbase_gate(draft) == (False, ["approval_missing:mean"])
    with pytest.raises(ValueError, match="missing Arabic term mapping"):
        build_termbase(glossary, {})

def test_duplicate_glossary_rejected():
    glossary = [GlossaryTerm(term_en="x", definition_en="a", first_sid="s", lesson_id="l"),
                GlossaryTerm(term_en=" X ", definition_en="b", first_sid="t", lesson_id="l")]
    with pytest.raises(ValueError, match="duplicate"):
        build_termbase(glossary, {"x": "y"})

def test_qe_detects_term_and_number_faults_without_autoaccept():
    term = TermbaseEntry(term_en="mean", term_ar="\u0627\u0644\u0645\u062a\u0648\u0633\u0637",
      definition_en="average", forbidden_variants_ar=["\u0627\u0644\u0645\u0639\u062f\u0644"], approved_by="reviewer")
    bad = deterministic_translation_check("Mean is 12.", "\u0627\u0644\u0645\u0639\u062f\u0644 13.", [term])
    assert bad.deterministic_pass is False and bad.route == "human_review"
    assert set(bad.term_violations) == {"missing_term:mean", "forbidden_variant:mean", "numeric_token_mismatch"}
    good = deterministic_translation_check("Mean is 12.", "\u0627\u0644\u0645\u062a\u0648\u0633\u0637 12.", [term])
    assert good.deterministic_pass is True and good.route == "human_review"

def test_g4_requires_review_or_autoaccept():
    qe = deterministic_translation_check("A sentence.", "\u062c\u0645\u0644\u0629.", [])
    assert translation_needs_review(qe, review=None)
    assert not translation_needs_review(qe, review={"action": "accept"})


def test_term_injection_only_uses_approved_matching_entries():
    term = TermbaseEntry(term_en="mean", term_ar="\u0627\u0644\u0645\u062a\u0648\u0633\u0637",
        definition_en="average", approved_by="reviewer")
    block = term_injection_block("The mean is useful.", [term])
    assert "mean => \u0627\u0644\u0645\u062a\u0648\u0633\u0637" in block
    assert term_injection_block("Other concept.", [term]) == ""
    draft = term.model_copy(update={"approved_by": None})
    with pytest.raises(ValueError, match="G3"):
        term_injection_block("The mean is useful.", [draft])


def test_translation_completeness_flags_substantially_truncated_long_segments():
    source = "This is a source sentence with important details. " * 12
    translated = "Brief incomplete output. " * 4
    issue = translation_completeness_issue(source, translated)
    assert issue is not None
    assert issue["reason"] == "suspiciously_short_translation"
    assert issue["length_ratio"] < 0.45


def test_translation_completeness_flags_long_english_left_untranslated():
    source = ("The operational method identifies the assumptions behind the analysis "
              "and explains how the evidence supports each conclusion. " * 5)
    issue = translation_completeness_issue(source, source, target_language="ar")
    assert issue is not None
    assert issue["reason"] == "suspiciously_untranslated_english"
    arabic = "\u062a\u062d\u062f\u062f \u0627\u0644\u0637\u0631\u064a\u0642\u0629 \u0627\u0644\u0627\u0641\u062a\u0631\u0627\u0636\u0627\u062a \u0627\u0644\u062a\u064a \u062a\u0642\u0641 \u062e\u0644\u0641 \u0627\u0644\u062a\u062d\u0644\u064a\u0644 \u0648\u062a\u0634\u0631\u062d \u0643\u064a\u0641 \u062a\u062f\u0639\u0645 \u0627\u0644\u0623\u062f\u0644\u0629 \u0643\u0644 \u0627\u0633\u062a\u0646\u062a\u0627\u062c. "
    assert translation_completeness_issue(source, arabic * 4, target_language="ar") is None


def test_translation_completeness_flags_long_english_lines_inside_mixed_arabic_output():
    source = "The analytical process identifies assumptions and explains how evidence supports each conclusion. " * 5
    arabic = "تحدد العملية التحليلية الافتراضات وتشرح كيف تدعم الأدلة كل استنتاج. " * 6
    untranslated_block = (
        "The method also requires analysts to record uncertainty and revisit each judgment when new evidence becomes available. "
        "Each analyst should compare the evidence, explain the reasoning, and update the assessment when the facts change. The team must preserve the evidence and explain each revision clearly."
    )
    wrapped_with_url = "\n".join((untranslated_block[:120], untranslated_block[120:], "Source: https://example.org/report"))
    issue = translation_completeness_issue(source, arabic + "\n" + wrapped_with_url, target_language="ar")
    assert issue is not None
    assert issue["reason"] == "suspiciously_untranslated_english"
    assert issue["quality_assessment"] == "deterministic_mixed_script_triage"
    assert issue["untranslated_english_block_count"] == 1
    assert translation_completeness_issue(source, arabic + "\nThe CIA and NATO", target_language="ar") is None


def test_translation_completeness_does_not_flag_short_or_reasonably_compressed_text():
    assert translation_completeness_issue("A short sentence.", "Short.") is None
    source = "A paragraph with the relevant facts and a complete explanation. " * 5
    translated = "A complete paragraph with the facts and explanation. " * 4
    assert translation_completeness_issue(source, translated) is None
