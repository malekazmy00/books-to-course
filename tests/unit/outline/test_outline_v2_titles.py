"""Title cleaning and malformed-title detection (audit 02 P5, gate '0 malformed titles')."""
import pytest

from b2c_outline.v2.titles import TitleVocab, clean_title, english_zipf, malformed_title

VOCAB = TitleVocab.from_texts([
    "critical approaches to gramscian theory are historical; the historical bloc is critical. "
    "Gramscian thought, critical critical historical historical gramscian gramscian whither whither "
    "an analyst can be often right. The state of the art. A SWOT matrix; SWOT again."])


@pytest.mark.parametrize("raw, clean", [
    ("STRUCTURED\nANALYTIC  TECHNIQUES", "Structured Analytic Techniques"),
    ("critic al approaches", "Critical Approaches"),
    ("gramsci an theory as historicist and organic", "Gramscian Theory as Historicist and Organic"),
    ("the historic al bloc", "The Historical Bloc"),
    ("c o u n t e r c u l t u r e", "Counterculture"),
    ("gramsci: lodestar of renewal", "Gramsci: Lodestar of Renewal"),
    ("what is the party to do?", "What Is the Party to Do?"),
    ("CHAPTER 1 INTRODUCTION AND OVERVIEW", "Chapter 1 Introduction and Overview"),
    ("5.2 Ranking, Scoring, and Prioritizing", "5.2 Ranking, Scoring, and Prioritizing"),  # already clean
    ("Chapter 1 • Introduction and Overview", "Chapter 1 • Introduction and Overview"),
    ("6.4 Circleboarding™", "6.4 Circleboarding™"),
    ("the culminating phase (s) of the war", "The Culminating Phase(s) of the War"),
    ("an organic formulation", "An Organic Formulation"),  # 'an' is a word here: no merge
    ("often right", "Often Right"),
])
def test_clean_title(raw, clean):
    assert clean_title(raw, VOCAB) == clean


def test_a_stray_letter_join_may_use_the_english_lexicon():
    # the book body never prints 'whither'; the lone lowercase 'i' marks letter-spacing
    with_lexicon = TitleVocab(words=VOCAB.words, upper=VOCAB.upper, lexicon=english_zipf)
    assert clean_title("w(h) i ther the state?", with_lexicon) == "W(h)ither the State?"
    assert clean_title("an organic formulation", with_lexicon) == "An Organic Formulation"
    plain = TitleVocab.from_texts(["the state of things"])
    assert clean_title("w(h) i ther the state?", plain) == "W(h) I Ther the State?"  # no evidence, no guess


def test_clean_title_keeps_acronyms_and_mixed_case_words():
    assert clean_title("SWOT Analysis", VOCAB) == "SWOT Analysis"
    # an all-caps title is re-cased word by word; words the book itself prints in capitals stay so
    assert clean_title("10.4 SWOT ANALYSIS", VOCAB) == "10.4 SWOT Analysis"


@pytest.mark.parametrize("title, reason", [
    ("STRUCTURED\nANALYTIC", "multiline"),
    ("gr amsc i : lodes tar of renewal", "letter_spacing"),
    ("c o u n t e r c u l t u r e", "letter_spacing"),
    ("t r a s f o r m i s m o", "letter_spacing"),
    ("conclus i on", "letter_spacing"),
    ("abbre v i at i ons", "letter_spacing"),
    ("readings of gramsci", "lowercase_start"),
    ("11.3.1 Structuring the Data 11.3.2 Identifying Key Drivers 11.4 Role o", "toc_like"),
    ("3.1 The Six Families [ 35 ] 3.2 Core Techniques", "toc_like"),
    ("   ", "empty"),
    ("x" * 200, "too_long"),
])
def test_malformed_titles_are_detected(title, reason):
    assert reason in malformed_title(title)


@pytest.mark.parametrize("title", [
    "Chapter 1 • Introduction and Overview", "5.2 Ranking, Scoring, and Prioritizing", "Gramsci: Lodestar of Renewal",
    "W(h)ither the State?", "The Culminating Phase(s) of the War", "Plan B and Option C", "Is a Role of AI in IT Clear?",
    "Chapter 3 · Conclusion", "8.2.4 What If? Analysis", "Introduction",
])
def test_clean_titles_are_not_flagged(title):
    assert malformed_title(title) == []
