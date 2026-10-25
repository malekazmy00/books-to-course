#!/usr/bin/env python3
"""Register every legacy outline/QA/translation/MP3 artifact as `quarantined` ("legacy pre-gate").

R0 item 1 / audit 08 §6.4 step 0. Dry run by default: lists the bucket and reads manifests
only (a read-only S3 proxy refuses any write), and never connects to Postgres. `--apply`
inserts registry rows (idempotent: existing rows are kept). No object is ever written or deleted.

    uv run python scripts/quarantine-legacy-artifacts.py                 # dry run, JSON report
    uv run python scripts/quarantine-legacy-artifacts.py --apply         # write registry rows

S3/Postgres settings come from the usual B2C_* environment (b2c_core.Settings);
`--endpoint` overrides B2C_S3_ENDPOINT.
"""
from __future__ import annotations

import argparse
import json
import sys

from b2c_core import Settings
from b2c_core.legacy_quarantine import LEGACY_STAGES, ReadOnlyS3, quarantine_legacy, scan_bucket
from b2c_core.stage_registry import StageOutputRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write registry rows (default: dry run)")
    parser.add_argument("--endpoint", default=None, help="S3 endpoint (overrides B2C_S3_ENDPOINT)")
    parser.add_argument("--bucket", default=None, help="bucket (default: B2C_S3_BUCKET)")
    parser.add_argument("--stages", default=",".join(sorted(LEGACY_STAGES)),
                        help="comma-separated stages to quarantine")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.endpoint:
        settings = settings.model_copy(update={"s3_endpoint": args.endpoint})
    import boto3
    from botocore.config import Config
    client = boto3.client("s3", endpoint_url=settings.s3_endpoint, aws_access_key_id=settings.s3_access_key,
                          aws_secret_access_key=settings.s3_secret_key, region_name=settings.s3_region,
                          config=Config(s3={"addressing_style": "path"}))
    bucket = args.bucket or settings.s3_bucket
    stages = frozenset(s.strip() for s in args.stages.split(",") if s.strip())
    scan = scan_bucket(ReadOnlyS3(client), bucket, stages=stages)
    registry = StageOutputRegistry(settings.pg_dsn) if args.apply else None
    report = quarantine_legacy(scan, registry, apply=args.apply)
    report.update(bucket=bucket, stages=sorted(stages), invalid_manifest_keys=scan.invalid[:50])
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
