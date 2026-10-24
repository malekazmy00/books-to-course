#!/usr/bin/env bash
# T-P0-05: external (non-b2c) containers unchanged: same IDs and same StartedAt as the baseline.
source "$(dirname "$0")/lib.sh"
before="$ROOT/eval/reports/phase-0-external-before.txt"
after="$ROOT/eval/reports/phase-0-external-after.txt"
{
  echo "# T-P0-05 after-snapshot. captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "## docker ps --format '{{.ID}} {{.Names}} {{.Status}}'  (non-b2c only)"
  for id in $(docker ps -q); do
    p=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")
    [ "$p" = "b2c" ] && continue
    docker inspect --format '{{.Id | printf "%.12s"}} {{.Name}} {{.State.Status}}' "$id"
  done
  echo "## id name StartedAt"
  for id in $(docker ps -q); do
    p=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")
    [ "$p" = "b2c" ] && continue
    docker inspect --format '{{.Id | printf "%.12s"}} {{.Name}} {{.State.StartedAt}}' "$id"
  done
} > "$after"
cat "$after"
echo
echo "## diff of (id name StartedAt) before vs after"
b=$(sed -n '/^## id name StartedAt/,$p' "$before" | grep -v '^#' | sort)
a=$(sed -n '/^## id name StartedAt/,$p' "$after" | grep -v '^#' | sort)
if [ "$b" = "$a" ]; then
  echo "identical: $(echo "$b" | wc -l) containers, same IDs and StartedAt"
  echo "RESULT: PASS"
else
  diff <(echo "$b") <(echo "$a")
  echo "RESULT: FAIL"
fi
