"""Rule 8 on the live stack: `make budget-live` sums the limits of every running b2c container.

`make budget` only sees the services the selected profiles would start; containers started from
overlays outside the boot path (LightRAG, extra model servers) escaped it (status 2026-10-31).
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check-budget.py"

spec = importlib.util.spec_from_file_location("check_budget", SCRIPT)
check_budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_budget)

GIB = 2**30


def container(name: str, mem_gib: float, cpus: float, shares: int = 512) -> dict:
    return {"Name": f"/{name}",
            "HostConfig": {"Memory": int(mem_gib * GIB), "NanoCpus": int(cpus * 1e9), "CpuShares": shares}}


def run(containers: list[dict], *env: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--running"], input=json.dumps(containers),
                          capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/bin", **dict(e.split("=", 1) for e in env)})


def test_running_rows_come_from_docker_inspect_limits():
    rows, missing = check_budget.rows_from_inspect([container("b2c-api-1", 2, 1.5)])
    assert rows == [("b2c-api-1", 2.0, 1.5)] and missing == []


def test_running_container_without_limits_is_reported():
    rows, missing = check_budget.rows_from_inspect([container("b2c-x-1", 0, 0, shares=0)])
    assert rows == [("b2c-x-1", 0.0, 0.0)]
    assert missing == ["b2c-x-1: mem_limit", "b2c-x-1: cpus", "b2c-x-1: cpu_shares"]


def test_running_stack_within_budget_passes():
    out = run([container("b2c-api-1", 2, 1), container("b2c-llm-1", 50, 16)])
    assert out.returncode == 0, out.stderr
    assert "total: 52.00/85 GiB, 17.00/40 cpus, 2 running containers" in out.stdout


def test_running_stack_over_budget_fails():
    out = run([container("b2c-llm-1", 50, 16), container("b2c-fasih-tts-1", 50, 24)])
    assert out.returncode == 1
    assert "exceed the b2c budget" in out.stderr


def test_budget_live_target_inspects_only_running_b2c_containers():
    out = subprocess.run(["make", "-n", "-C", str(ROOT), "budget-live"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "label=com.docker.compose.project=b2c" in out.stdout
    assert "check-budget.py --running" in out.stdout
