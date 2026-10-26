"""Write docs/api/operator-v3.openapi.json from the operator API v3 code."""
from __future__ import annotations

from pathlib import Path

from b2c_api.operator.openapi import operator_openapi_text

TARGET = Path(__file__).resolve().parents[1] / "docs" / "api" / "operator-v3.openapi.json"

if __name__ == "__main__":
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(operator_openapi_text(), encoding="utf-8")
    print(f"wrote {TARGET}")
