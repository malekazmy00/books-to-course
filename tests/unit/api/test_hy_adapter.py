import importlib.util
from pathlib import Path
import pytest
from fastapi import HTTPException

spec=importlib.util.spec_from_file_location("hy_server",Path(__file__).parents[3]/"docker/hy-translation/server.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_hy_adapter_accepts_only_translation_preserving_segment_identity():
    target,payload=module.translation_payload([
        {"role":"system","content":"Translate faithfully from en to ar."},
        {"role":"user","content":'{"segments":[{"segment_id":"book:p1","text":"Value [[B2C_NUM_0]]"}],"glossary":{"Value":"قيمة"}}'}])
    assert target == "ar" and payload["segments"][0]["segment_id"] == "book:p1"
    assert "[[B2C_NUM_0]]" in payload["segments"][0]["text"]


@pytest.mark.parametrize("messages", [[],[{"role":"user","content":"write a poem"}],
    [{"role":"system","content":"Translate from en to ar"},
     {"role":"user","content":'{"segments":[{"segment_id":"x","text":"one"},{"segment_id":"x","text":"two"}]}'}]])
def test_hy_rejects_unstructured_or_duplicate_units(messages):
    with pytest.raises(HTTPException) as exc:
        module.translation_payload(messages)
    assert exc.value.status_code == 422


def test_hy_models_readiness_cannot_claim_unloaded_weights():
    with pytest.raises(HTTPException) as exc:
        module.models()
    assert exc.value.status_code == 503
