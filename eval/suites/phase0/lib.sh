# shared helpers for Phase 0 evidence scripts
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
EV="$ROOT/eval/reports/phase-0-evidence"
mkdir -p "$EV"
C="docker compose -p b2c --env-file .env -f docker/compose.yml -f docker/compose.observability.yml"
# one-off container on the b2c network with the eval scripts mounted
RUN="$C run --rm --no-deps -v $ROOT/eval:/app/eval:ro"
stamp() { echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ) \$ $*"; }
