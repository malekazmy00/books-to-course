#!/usr/bin/env bash
# T-P0-02: run HelloPipeline (real llm via llm_gateway), then export its trace from Langfuse
# through the v4 Observations API (V-09).
source "$(dirname "$0")/lib.sh"
out="$EV/t_p0_02_langfuse_trace.txt"
set -a; source .env; set +a
name="Trace$(date +%H%M%S)"
{
  stamp "b2c-hello run --name $name --course-id crs_p0_trace"
  $RUN worker b2c-hello run --name "$name" --course-id crs_p0_trace | tee /tmp/b2c-hello-run.json
  tid=$(python3 -c 'import json;print(json.load(open("/tmp/b2c-hello-run.json"))["trace_id"])')
  echo
  echo "trace_id=$tid — waiting 20s for collector batch + Langfuse ingestion"
  sleep 20
  stamp "GET /api/public/v2/observations?traceId=$tid (from inside b2c network)"
  $RUN -e LF_PK="$LANGFUSE_PUBLIC_KEY" -e LF_SK="$LANGFUSE_SECRET_KEY" -e TID="$tid" worker \
    python /app/eval/suites/phase0/langfuse_export.py
} 2>&1 | tee "$out"
