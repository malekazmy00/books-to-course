"""The operator API v3 OpenAPI document (committed as docs/api/operator-v3.openapi.json).

Regenerate with ``uv run python scripts/export_operator_openapi.py``; a unit test fails when the
committed file and the code differ, so the Android client and the server cannot drift silently.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI

from b2c_api.operator.router import operator_router
from b2c_api.operator.schemas import API_VERSION


def operator_openapi() -> dict[str, Any]:
    app = FastAPI(title="Book2Course Operator API", version=API_VERSION,
                  description="Read-only stages and progress of every book on the new path (tailnet-only, "
                              "bearer token, D-030). No route changes anything.")
    app.include_router(operator_router(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))))
    doc = app.openapi()
    doc.setdefault("components", {})["securitySchemes"] = {
        "bearerToken": {"type": "http", "scheme": "bearer"}}
    doc["security"] = [{"bearerToken": []}]
    return doc


def operator_openapi_text() -> str:
    return json.dumps(operator_openapi(), ensure_ascii=False, indent=2) + "\n"
