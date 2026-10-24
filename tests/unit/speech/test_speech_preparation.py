import importlib.util
from pathlib import Path

import pytest


def load(name, filename):
    path = Path(__file__).with_name(filename)
    if not path.is_file():
        path = Path(__file__).resolve().parents[3] / "docker/supertonic" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


punct = load("punct_test", "arabic_punctuation.py")


def test_long_token_windows_cover_tail_without_truncation():
    windows = list(punct.token_windows(1207))
    assert windows[-1][1] == 1207
    assert all(stop - start <= 480 for start, stop in windows)
    assert set(range(1207)) == {i for start, stop in windows for i in range(start, stop)}
    assert windows[1][0] < windows[0][1]


def test_provider_boundary_never_exceeds_limit_by_one():
    mod = load("render_edge_test", "audio_renderer.py")
    parts = mod.tts_segments(("أ " * 2249) + ". " + "ب", "ar")
    assert all(len(part) <= 1200 for part in parts)


def test_insertion_preserves_words_digits_existing_marks_and_whitespace():
    text = "هذا نص عربي 123.45 test URL\n\nكتاب؟ آخر"
    out = punct.insert_marks(text, [(3, "،", .99), (6, ".", .99),
        (15, "،", .99), (len(text), ".", .99)])
    assert out == "هذا، نص. عربي 123.45 test URL\n\nكتاب؟ آخر."
    assert "123.45 test URL\n\nكتاب؟" in out


def test_low_confidence_stylistic_marks_and_subword_boundaries_rejected():
    text = "الكتاب جميل"
    assert punct.insert_marks(text, [(3, ".", 1), (6, "!", 1),
        (6, "؛", 1), (6, "-", 1), (6, ".", .77)]) == text


def test_sentence_split_preserves_context_without_forced_short_requests():
    mod = load("render_test", "audio_renderer.py")
    text = ("هذه جملة عربية مفيدة. " * 240)
    parts = mod.tts_segments(text, "ar")
    assert len(parts) > 1
    assert max(map(len, parts)) <= 1200
    assert " ".join(parts) == text.strip()
    assert all(part.endswith(".") for part in parts)


def test_optional_preparation_is_cached_and_language_scoped(tmp_path, monkeypatch):
    mod = load("render_cache_test", "audio_renderer.py")
    class Fake:
        calls = 0
        def identity(self): return {"policy": "test"}
        def restore(self, text):
            self.calls += 1
            return text + "."
    fake = Fake()
    mod._punctuator = fake
    monkeypatch.setenv("B2C_SPEECH_PUNCTUATION", "naqta")
    assert mod.speech_texts(["هذا نص"], "ar", tmp_path)[0] == ["هذا نص."]
    assert mod.speech_texts(["هذا نص"], "ar", tmp_path)[0] == ["هذا نص."]
    assert fake.calls == 1
    assert mod.speech_texts(["English"], "en", tmp_path) == (["English"], {"punctuation": "off"})
    monkeypatch.setenv("B2C_SPEECH_PUNCTUATION", "off")
    assert mod.speech_texts(["هذا نص"], "ar", tmp_path)[0] == ["هذا نص"]


def test_missing_selected_model_fails_explicitly(tmp_path, monkeypatch):
    mod = load("render_missing_test", "audio_renderer.py")
    monkeypatch.setenv("B2C_SPEECH_PUNCTUATION", "naqta")
    monkeypatch.setenv("B2C_NAQTA_MODEL_DIR", str(tmp_path / "missing"))
    with pytest.raises(mod.HTTPException) as error:
        mod.speech_texts(["هذا نص"], "ar", tmp_path)
    assert error.value.status_code == 503


def test_confident_sentence_period_is_allowed_below_legacy_threshold():
    text = "النص العربي ينتهي هنا"
    assert punct.insert_marks(text, [(len(text), ".", .80)]) == text + "."
