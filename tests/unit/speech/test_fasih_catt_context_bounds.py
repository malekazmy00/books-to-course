import importlib.util
from pathlib import Path
import pytest
spec = importlib.util.spec_from_file_location("catt_bounds", Path(__file__).resolve().parents[3] / "docker/fasih/catt_bounds.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def test_long_context_preserves_all_words_and_separators():
    text = ("\u0647\u0630\u0647 sentence complete. " * 200) + "end"
    parts = module.context_parts(text)
    assert "".join(parts) == text
    assert max(map(len, parts)) <= 900
    assert parts[0].endswith(". ")

def test_diacritization_bounded_before_model_and_reassembled():
    requests = []
    class Catt:
        def diacritize_texts(self, texts, **kwargs):
            assert len(texts) == 1
            assert len(texts[0]) + 2 < 1024
            requests.extend(texts)
            return texts
    text = "\u0623\u0644\u0641\u0627\u0646 sentence. " * 200
    assert module.BoundedDiacritizer(Catt()).diacritize_texts([text]) == [text]
    assert len(requests) > 1

def test_unbroken_overlong_token_rejected_without_truncation():
    with pytest.raises(ValueError):
        module.context_parts("\u0623" * 1100)

def test_empty_model_response_never_accepted():
    class Catt:
        def diacritize_texts(self, texts, **kwargs):
            return [""]
    with pytest.raises(ValueError):
        module.BoundedDiacritizer(Catt()).diacritize_texts(["arabic"])
