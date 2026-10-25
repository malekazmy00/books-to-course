"""Write docs/api/learner-v3.openapi.json from the learner API v3 code."""
from __future__ import annotations

from pathlib import Path

from b2c_api.learner.openapi import learner_openapi_text

TARGET = Path(__file__).resolve().parents[1] / "docs" / "api" / "learner-v3.openapi.json"

if __name__ == "__main__":
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(learner_openapi_text(), encoding="utf-8")
    print(f"wrote {TARGET}")
