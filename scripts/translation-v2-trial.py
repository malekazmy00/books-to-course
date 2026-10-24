"""Trial run of the rebuilt translation path (D-028) on a sample of real blocks.

Runs inside the b2c worker container (needs MinIO, Postgres and the engine profile's model):

    docker exec -i -e B2C_TRANSLATION_PROFILES_FILE=/etc/b2c/translation-profiles.json \
        b2c-worker-1 python - < scripts/translation-v2-trial.py < sample.json > trial.jsonl

stdin: JSON {"engine": "<profile>", "qe": "<profile>|null", "books": {"<parsed-book key>": ["block_id", ...]}}
stdout: one JSON line per unit with the unit, every draft (text, issues, decision, lever) and the
final UnitTranslation. Results are not written to the run store or the translation memory: this
is a measurement, not a production run.
"""
from __future__ import annotations

import json
import sys
import time

import boto3
from b2c_contracts import ParsedBook
from b2c_core.config import Settings
from b2c_llm_gateway.cache import PostgresLLMCache
from b2c_orchestration.translation_profiles import ProfileRuntime, load_translation_profiles
from b2c_orchestration.worker import translation_gateway_factory
from b2c_translation.book_pipeline import PipelineConfig, UnitTranslator
from b2c_translation.units import build_translation_units


def main() -> None:
    request = json.load(sys.stdin)
    s = Settings()
    runtime = ProfileRuntime(load_translation_profiles(s),
                             gateway_factory=translation_gateway_factory(PostgresLLMCache(s.pg_dsn)))
    translator = UnitTranslator(runtime.engine(request["engine"]), estimator=runtime.estimator(request.get("qe")),
                                config=PipelineConfig(max_attempts=3))
    s3 = boto3.client("s3", endpoint_url=s.s3_endpoint, aws_access_key_id=s.s3_access_key,
                      aws_secret_access_key=s.s3_secret_key)
    for key, block_ids in request["books"].items():
        book = ParsedBook.model_validate_json(s3.get_object(Bucket=s.s3_bucket, Key=key)["Body"].read())
        units = build_translation_units(book)
        wanted = set(block_ids)
        for index, unit in enumerate(units):
            if not wanted.intersection(unit.block_ids):
                continue
            before = units[index - 1].source_text[-400:] if index > 0 else None
            after = units[index + 1].source_text[:400] if index + 1 < len(units) else None
            started = time.monotonic()
            try:
                result = translator.translate(unit, context_before=before, context_after=after)
            except Exception as exc:  # measurement: record and continue
                print(json.dumps({"unit": unit.model_dump(mode="json"), "error": f"{type(exc).__name__}: {exc}"},
                                 ensure_ascii=False), flush=True)
                continue
            print(json.dumps({
                "unit": unit.model_dump(mode="json"),
                "seconds": round(time.monotonic() - started, 1),
                "drafts": [{"text": d.text, "lever": d.lever, "decision": d.decision.decision,
                            "next_action": d.decision.next_action,
                            "issues": [i.model_dump(mode="json") for i in d.issues],
                            "qe": None if d.qe is None else {"passed": d.qe.passed,
                                                             "result": d.qe.result.model_dump(mode="json")}}
                           for d in result.drafts],
                "translation": result.translation.model_dump(mode="json"),
            }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
