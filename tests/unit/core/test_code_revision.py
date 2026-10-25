"""Code revision of the running image (P8 / audit 08 C3)."""
import logging

from b2c_core.revision import REVISION_ENV, code_revision, is_clean_revision, log_startup_revision

SHA = "0123456789abcdef0123456789abcdef01234567"


def test_revision_comes_from_the_build_stamp():
    assert code_revision({REVISION_ENV: SHA}) == SHA
    assert code_revision({REVISION_ENV: SHA + "-dirty"}) == SHA + "-dirty"
    assert code_revision({REVISION_ENV: SHA.upper()}) == SHA


def test_missing_or_malformed_stamp_is_unknown():
    assert code_revision({}) == "unknown"
    assert code_revision({REVISION_ENV: ""}) == "unknown"
    assert code_revision({REVISION_ENV: "main"}) == "unknown"
    assert code_revision({REVISION_ENV: "abc123"}) == "unknown"


def test_only_a_known_clean_revision_is_clean():
    assert is_clean_revision(SHA)
    assert not is_clean_revision(SHA + "-dirty")
    assert not is_clean_revision("unknown")


def test_startup_log_names_the_revision(caplog):
    logger = logging.getLogger("b2c.test.revision")
    with caplog.at_level(logging.INFO, logger="b2c.test.revision"):
        rev = log_startup_revision(logger, "worker", environ={REVISION_ENV: SHA})
    assert rev == SHA
    record = caplog.records[-1]
    assert record.code_revision == SHA and record.service == "worker" and record.clean is True


def test_unstamped_startup_is_a_warning(caplog):
    logger = logging.getLogger("b2c.test.revision")
    with caplog.at_level(logging.INFO, logger="b2c.test.revision"):
        log_startup_revision(logger, "api", environ={})
    assert caplog.records[-1].levelno == logging.WARNING
