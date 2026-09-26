"""packages/core: stage_key re-export, config, JSON logging."""
import io
import json
import logging

import b2c_contracts
import pytest

from b2c_core import Settings, configure_logging, get_logger, stage_key


def test_stage_key_is_reexported_from_contracts():
    assert stage_key is b2c_contracts.stage_key


def test_settings_read_b2c_env(monkeypatch):
    monkeypatch.setenv("B2C_PG_HOST", "pghost")
    monkeypatch.setenv("B2C_PG_DB", "db1")
    monkeypatch.setenv("B2C_PG_USER", "u1")
    monkeypatch.setenv("B2C_PG_PASSWORD", "p@ss/word")
    s = Settings()
    assert s.pg_host == "pghost"
    # password must be URL-escaped inside the DSN
    assert s.pg_dsn == "postgresql://u1:p%40ss%2Fword@pghost:5432/db1"


def test_settings_have_no_cloud_llm_keys():  # D-004
    names = set(Settings.model_fields)
    for bad in ("openai", "anthropic", "gemini", "azure", "bedrock"):
        assert not any(bad in n for n in names), n


def test_settings_reject_unknown_llm_scheme(monkeypatch):
    monkeypatch.setenv("B2C_LLM_BASE_URL", "ftp://llm:8080")
    with pytest.raises(ValueError):
        Settings()


def test_json_logging_emits_one_json_object_per_line():
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)
    log = get_logger("b2c.test")
    log.info("hello", extra={"stage": "ingest", "key": "abc"})
    log.debug("not shown")
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["msg"] == "hello"
    assert rec["level"] == "INFO"
    assert rec["logger"] == "b2c.test"
    assert rec["stage"] == "ingest" and rec["key"] == "abc"
    assert rec["ts"].endswith("Z")  # UTC
    logging.getLogger().handlers.clear()


def test_json_logging_includes_exception():
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)
    log = get_logger("b2c.test")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed")
    rec = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert "RuntimeError: boom" in rec["exc"]
    logging.getLogger().handlers.clear()
