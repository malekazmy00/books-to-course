#!/usr/bin/env bash
# Recover only existing B2C model services that failed at boot before bind mounts.
set -euo pipefail
cd /root/projects/book2course
bash scripts/data-disk.sh check
# Only services of the profiles started at boot (PROFILES, as in the Makefile); starting
# anything else here would bypass the rule-8 budget check done by `make start`.
containers=()
[[ " ${PROFILES:-local-llm} " == *" speech "* ]] && containers+=(b2c-fasih-tts-1)
for container in "${containers[@]}"; do
  if ! docker inspect "$container" >/dev/null 2>&1; then
    printf '%s not configured; skipped\n' "$container"
    continue
  fi
  project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$container")
  if [[ "$project" != b2c ]]; then
    printf 'Refusing non-B2C container: %s\n' "$container" >&2
    exit 1
  fi
  running=$(docker inspect --format '{{.State.Running}}' "$container")
  if [[ "$running" == true ]]; then
    printf '%s already running\n' "$container"
    continue
  fi
  failure=$(docker inspect --format '{{.State.Error}}' "$container")
  if [[ "$failure" == *'mount request'* || "$failure" == *'error while mounting'* ]]; then
    docker start "$container"
  else
    printf '%s left stopped: no boot mount failure\n' "$container"
  fi
done
