#!/usr/bin/env bash
# Create .env from .env.example with fresh random secrets. Never overwrites an existing .env.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then echo ".env exists — not touching it"; exit 0; fi

rnd() { openssl rand -hex "${1:-24}"; }
pk="pk-lf-$(rnd 16)"
sk="sk-lf-$(rnd 16)"

declare -A V=(
  [B2C_PG_PASSWORD]=$(rnd)
  [B2C_PG_TEMPORAL_PASSWORD]=$(rnd)
  [B2C_PG_LANGFUSE_PASSWORD]=$(rnd)
  [B2C_S3_SECRET_KEY]=$(rnd)
  [LANGFUSE_SALT]=$(rnd)
  [LANGFUSE_ENCRYPTION_KEY]=$(rnd 32)
  [LANGFUSE_NEXTAUTH_SECRET]=$(rnd)
  [LANGFUSE_CLICKHOUSE_PASSWORD]=$(rnd)
  [LANGFUSE_REDIS_AUTH]=$(rnd)
  [LANGFUSE_PUBLIC_KEY]=$pk
  [LANGFUSE_SECRET_KEY]=$sk
  [LANGFUSE_OTEL_AUTH]=$(printf '%s:%s' "$pk" "$sk" | base64 -w0)
  [LANGFUSE_INIT_USER_PASSWORD]=$(rnd 12)
  [GRAFANA_ADMIN_PASSWORD]=$(rnd 12)
  [B2C_LIGHTRAG_API_KEY]=$(rnd)
)

umask 077
while IFS= read -r line; do
  key=${line%%=*}
  if [[ $line =~ ^[A-Z0-9_]+= ]] && [[ -n ${V[$key]+x} ]]; then
    comment=""
    [[ $line == *"#"* ]] && comment="   #${line#*#}"
    echo "$key=${V[$key]}$comment"
  else
    echo "$line"
  fi
done < .env.example > .env
echo "wrote .env (mode 600)"
