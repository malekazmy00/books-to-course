"""Run the Phase 1 structured-JSON validity benchmark through the local B2C gateway."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from b2c_contracts.models import EntityType
from b2c_llm_gateway import LLMGateway


class ExtractedEntity(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: EntityType
    description: str = Field(max_length=400)


class ExtractedRelation(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    kind: Literal["prerequisite_of", "example_of", "defines", "part_of", "contrasts_with", "applies_to"]
    evidence: str = Field(min_length=1, max_length=300)


class ChunkExtraction(BaseModel):
    entities: list[ExtractedEntity] = Field(max_length=12)
    relations: list[ExtractedRelation] = Field(max_length=12)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True, help="B2C local llama-server OpenAI-compatible base URL")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")

    rows = [json.loads(line) for line in args.chunks.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = rows[:args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    gateway = LLMGateway(args.base_url, args.model_id, timeout_s=600, capture_content=False)
    valid = 0
    started = time.monotonic()
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        for index, row in enumerate(rows, 1):
            prompt = (
                "Extract only important statistical concepts from the provided textbook excerpt. "
                "Use the required JSON schema. Each entity kind must match the project's EntityType. "
                "Relations must be explicitly supported by the text; evidence must be a short exact excerpt. "
                "Do not add outside facts. If none are present, return empty arrays.\n\n"
                f"Book role: {row['role']}\nSection: {' > '.join(row['section_path'])}\n"
                f"Excerpt:\n{row['text']}"
            )
            t0 = time.monotonic()
            try:
                result = gateway.structured(
                    [{"role": "user", "content": prompt}], ChunkExtraction,
                    prompt_version="phase1-json-extraction-v1", temperature=0.0, max_tokens=1200,
                )
                payload = {"chunk_id": row["chunk_id"], "status": "valid", "result": result.model_dump(), "seconds": round(time.monotonic()-t0, 3)}
                valid += 1
            except Exception as exc:  # record failure as evaluation data; continue the suite
                payload = {"chunk_id": row.get("chunk_id", f"row-{index}"), "status": "invalid", "error_type": type(exc).__name__, "seconds": round(time.monotonic()-t0, 3)}
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"{index}/{len(rows)} {payload['status']} seconds={payload['seconds']}", flush=True)
    elapsed = time.monotonic() - started
    print(f"valid={valid}/{len(rows)} rate={valid/len(rows):.3%} elapsed_seconds={elapsed:.1f} model={args.model_id} output={args.output}")


if __name__ == "__main__":
    main()
