"""The learner API v3 OpenAPI document (committed as docs/api/learner-v3.openapi.json).

Regenerate with ``uv run python scripts/export_learner_openapi.py``; a unit test fails when the
committed file and the code differ, so the Android client and the server cannot drift silently.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI

from b2c_api.learner.router import learner_router
from b2c_api.learner.schemas import API_VERSION


def learner_openapi() -> dict[str, Any]:
    app = FastAPI(title="Book2Course Learner API", version=API_VERSION,
                  description="Read-only learner API (tailnet-only, bearer token, D-030). Serves accepted "
                              "artifacts only; no GET response contains an answer.")
    app.include_router(learner_router(SimpleNamespace()))
    doc = app.openapi()
    doc.setdefault("components", {})["securitySchemes"] = {
        "bearerToken": {"type": "http", "scheme": "bearer"}}
    doc["security"] = [{"bearerToken": []}]
    return doc


def learner_openapi_text() -> str:
    return json.dumps(learner_openapi(), ensure_ascii=False, indent=2) + "\n"
