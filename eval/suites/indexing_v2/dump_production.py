"""Read-only dump of the live index (Postgres) and the approved outlines (MinIO) of a course.

Run inside the worker container so it uses the stack's own credentials; it opens Postgres in
read-only mode and only GETs from MinIO. The dump holds book text: write it outside git.

  docker exec -i b2c-worker-1 python - --course b2c-demo \
      --outline 0486615360c4700acd2bffdd2b98b6733ee94d68d4bbef4699d706f6a7790d2a \
      --outline 97f28cec5b8ae56899035112ce1a1e300e771efa12e442809257088d2455c38b \
      < eval/suites/indexing_v2/dump_production.py > DATA/prod.json
"""
from __future__ import annotations

import argparse
import json
import os

import boto3
import psycopg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True)
    ap.add_argument("--outline", action="append", default=[], help="approve-outline artifact key")
    ap.add_argument("--bucket", default="artifacts")
    args = ap.parse_args()
    env = os.environ
    dsn = (f"host={env['B2C_PG_HOST']} dbname={env['B2C_PG_DB']} user={env['B2C_PG_USER']} "
           f"password={env['B2C_PG_PASSWORD']}")
    out: dict = {"course_id": args.course, "chunks": {}, "outlines": {}}
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        rows = conn.execute("SELECT book_id, payload, updated_at FROM b2c_indexed_chunks WHERE course_id = %s "
                            "ORDER BY book_id", (args.course,))
        for book_id, payload, updated in rows:
            out["chunks"].setdefault(book_id, []).append(payload)
            out.setdefault("updated_at", {})[book_id] = updated.isoformat()
    s3 = boto3.client("s3", endpoint_url=env["B2C_S3_ENDPOINT"], aws_access_key_id=env["B2C_S3_ACCESS_KEY"],
                      aws_secret_access_key=env["B2C_S3_SECRET_KEY"])
    for key in args.outline:
        obj = s3.get_object(Bucket=args.bucket, Key=f"{args.course}/approve-outline/{key}/approved-outline.json")
        out["outlines"][key] = json.loads(obj["Body"].read())
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
