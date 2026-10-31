"""Planted errors for judge calibration (charter §3.5): each mutation is deterministic and real."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("judge_mutate", ROOT / "eval/suites/judge_bakeoff/mutate.py")
mutate_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mutate_mod)
mutate = mutate_mod.mutate

TARGET = "ارتفع عدد الطلاب إلى 250 طالبًا في عام 2019. وكان التحسن أكبر من المتوقع بكثير. لذلك يرى المؤلف أن النتائج أكثر دقة."
SOURCE = ("The number of students rose to 250 in 2019. The improvement was much larger than expected. "
          "The author therefore argues that the results are more accurate.")
DONOR = "تتناول هذه الفقرة تاريخ الإمبراطورية العثمانية في القرن السادس عشر بالتفصيل."


def test_mutations_are_deterministic():
    for kind in mutate_mod.MUTATIONS:
        assert mutate(kind, TARGET, seed=7, source=SOURCE, donor=DONOR) == \
            mutate(kind, TARGET, seed=7, source=SOURCE, donor=DONOR)


def test_number_changes_one_number_only():
    out = mutate("number", TARGET, seed=1)
    assert out != TARGET and len(out) == len(TARGET)
    assert ("250" in out) != ("2019" in out)


def test_number_does_not_apply_without_digits():
    assert mutate("number", "لا أرقام هنا.", seed=1) is None


def test_antonym_reverses_a_known_pair():
    out = mutate("antonym", TARGET, seed=1)
    assert out is not None and ("أقل" in out)


def test_omission_drops_a_sentence():
    out = mutate("omission", TARGET, seed=1)
    assert out is not None and len(out) < len(TARGET) and out.count(".") == TARGET.count(".") - 1


def test_addition_appends_unrelated_content():
    out = mutate("addition", TARGET, seed=1, donor=DONOR)
    assert out.startswith(TARGET) and "العثمانية" in out


def test_untranslated_leaves_english_in_the_target():
    out = mutate("untranslated", TARGET, seed=1, source=SOURCE)
    assert out is not None and "accurate" in out


def test_identity_is_never_returned():
    assert mutate("antonym", "نص بلا أضداد معروفة هنا", seed=1) is None
