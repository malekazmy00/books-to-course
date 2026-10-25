"""P8 / audit 08 C3: images are stamped with the commit they are built from (checked with `make -n`)."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SHA = "0123456789abcdef0123456789abcdef01234567"
pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="needs make")


def make_n(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["make", "-n", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=60)


def test_build_passes_the_head_revision_to_compose():
    out = make_n("build", f"GIT_REVISION={SHA}", "GIT_DIRTY=")
    assert out.returncode == 0, out.stderr
    assert f"B2C_GIT_REVISION={SHA} docker compose -p b2c" in out.stdout


def test_up_passes_the_head_revision_to_compose():
    out = make_n("up", f"GIT_REVISION={SHA}", "GIT_DIRTY=")
    assert out.returncode == 0, out.stderr
    assert f"B2C_GIT_REVISION={SHA} docker compose -p b2c" in out.stdout and "up -d --build" in out.stdout


@pytest.mark.parametrize("goal", ["build", "up"])
def test_dirty_tree_is_refused_unless_forced(goal):
    refused = make_n(goal, f"GIT_REVISION={SHA}", "GIT_DIRTY= M packages/x.py")
    assert refused.returncode != 0 and "dirty" in refused.stderr
    forced = make_n(goal, f"GIT_REVISION={SHA}", "GIT_DIRTY= M packages/x.py", "FORCE_DIRTY=1")
    assert forced.returncode == 0, forced.stderr
    assert f"B2C_GIT_REVISION={SHA}-dirty" in forced.stdout


def test_missing_revision_is_refused():
    out = make_n("build", "GIT_REVISION=", "GIT_DIRTY=")
    assert out.returncode != 0 and "revision" in out.stderr


def test_other_targets_do_not_need_a_clean_tree():
    assert make_n("lint", f"GIT_REVISION={SHA}", "GIT_DIRTY= M x").returncode == 0


def test_project_images_take_the_revision_build_arg_and_label():
    for dockerfile in ("docker/api/Dockerfile", "docker/worker/Dockerfile", "docker/worker/Dockerfile.audio",
                       "docker/worker/Dockerfile.recovery"):
        text = (ROOT / dockerfile).read_text()
        assert "ARG B2C_GIT_REVISION" in text, dockerfile
        assert "LABEL org.opencontainers.image.revision=$B2C_GIT_REVISION" in text, dockerfile
        assert "ENV B2C_GIT_REVISION=$B2C_GIT_REVISION" in text, dockerfile
    compose = yaml.safe_load((ROOT / "docker/compose.yml").read_text())
    for service in ("api", "worker", "audio-worker"):
        build = compose["services"][service]["build"]
        assert build["args"]["B2C_GIT_REVISION"] == "${B2C_GIT_REVISION:-unknown}", service
    # the default `make up` file set swaps the worker to Dockerfile.recovery: it must be stamped too
    overlay = yaml.safe_load((ROOT / "docker/compose.qa-local.yml").read_text())
    assert overlay["services"]["worker"]["build"]["args"]["B2C_GIT_REVISION"] == "${B2C_GIT_REVISION:-unknown}"
