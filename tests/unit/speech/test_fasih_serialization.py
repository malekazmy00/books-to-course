"""Test the vendored server's actual synchronization without loading model weights."""
import ast
from functools import wraps
from pathlib import Path
import threading
import time


def test_shared_xtts_model_is_serialized_across_concurrent_requests():
    source = Path("docker/fasih/server.py").read_text()
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "serialized_inference")
    namespace = {"wraps": wraps, "_inference_lock": threading.Lock()}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<server-lock>", "exec"), namespace)
    active = peak = 0

    @namespace["serialized_inference"]
    def synth():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.02)
        active -= 1

    threads = [threading.Thread(target=synth) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 1
    assert active == 0
    assert "@serialized_inference\n@torch.inference_mode()\ndef _synth" in source
    assert "with _inference_lock:" in source.split("def tts_stream", 1)[1]
