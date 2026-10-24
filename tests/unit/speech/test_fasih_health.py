"""Fasih /health must expose a CATT diacritizer load failure (no model weights needed)."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3] / "docker/fasih/server.py"


def _health_payload():
    tree = ast.parse(SOURCE.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "health_payload")
    namespace = {"MODEL_ID": "Fasih-TTS-V1", "DEVICE": "cpu"}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<fasih-health>", "exec"), namespace)
    return namespace["health_payload"]


def test_catt_failure_reports_degraded_and_no_diacritizer():
    payload = _health_payload()(True, False, "FileNotFoundError")
    assert payload["status"] == "degraded"
    assert payload["diacritizer"] is False
    assert payload["diacritizer_error"] == "FileNotFoundError"


def test_healthy_and_loading_states():
    health = _health_payload()
    assert health(True, True, None) == {"status": "ok", "model": "Fasih-TTS-V1", "device": "cpu",
                                        "diacritizer": True}
    assert health(False, False, None)["status"] == "loading"


def test_load_records_diacritizer_outcome_and_health_uses_it():
    source = SOURCE.read_text()
    load = source.split("def load():", 1)[1].split("\n@app", 1)[0]
    assert "_diacritizer_loaded, _diacritizer_error = False, type(e).__name__" in load
    assert "_diacritizer_loaded, _diacritizer_error = True, None" in load
    assert "return health_payload(_model is not None, _diacritizer_loaded, _diacritizer_error)" in source
