"""Which code is running (P8 / audit 08 C3).

Images are stamped at build time (``make build``/``make up`` pass ``git rev-parse HEAD`` as the
``B2C_GIT_REVISION`` build arg; the Dockerfiles turn it into an env var and the OCI label
``org.opencontainers.image.revision``). Every registry entry records this value, and the
processes log it at startup. A missing or malformed stamp reads as ``"unknown"``; output made by
an unknown or ``-dirty`` revision can be registered but never accepted.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping

REVISION_ENV = "B2C_GIT_REVISION"
UNKNOWN = "unknown"
_REVISION = re.compile(r"[0-9a-f]{40}(-dirty)?")


def code_revision(environ: Mapping[str, str] | None = None) -> str:
    value = (os.environ if environ is None else environ).get(REVISION_ENV, "").strip().lower()
    return value if _REVISION.fullmatch(value) else UNKNOWN


def is_clean_revision(revision: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{40}", revision) is not None


def log_startup_revision(logger: logging.Logger, service: str,
                         environ: Mapping[str, str] | None = None) -> str:
    revision = code_revision(environ)
    clean = is_clean_revision(revision)
    logger.log(logging.INFO if clean else logging.WARNING, "%s starting at code revision %s", service, revision,
               extra={"service": service, "code_revision": revision, "clean": clean})
    return revision


__all__ = ["REVISION_ENV", "UNKNOWN", "code_revision", "is_clean_revision", "log_startup_revision"]
