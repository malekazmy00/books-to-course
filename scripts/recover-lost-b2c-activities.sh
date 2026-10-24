#!/usr/bin/env bash
set -euo pipefail
cd /root/projects/book2course
bash scripts/data-disk.sh check
container_info=$(docker ps -q --filter label=com.docker.compose.project=b2c | xargs -r docker inspect --format '{{.Config.Hostname}} {{.State.StartedAt}}')
live_json=$(printf '%s\n' "$container_info" | python3 -c 'import sys,json; from datetime import datetime; rows=[r.split() for r in sys.stdin if r.strip()]; print(json.dumps({host:datetime.fromisoformat(start.replace("Z","+00:00")).timestamp() for host,start in rows}))')
docker exec -i -e "B2C_LIVE_CONTAINER_STARTS=$live_json" b2c-api-1 python - < scripts/recover-lost-b2c-activities.py
