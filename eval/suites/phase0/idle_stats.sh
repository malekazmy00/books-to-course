#!/usr/bin/env bash
# Real idle consumption of b2c services.
source "$(dirname "$0")/lib.sh"
out="$EV/idle_docker_stats.txt"
{
  stamp "docker stats --no-stream (b2c containers only)"
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' \
    $(docker ps -q --filter label=com.docker.compose.project=b2c)
  echo
  docker stats --no-stream --format '{{.MemUsage}}' $(docker ps -q --filter label=com.docker.compose.project=b2c) \
  | python3 -c '
import sys,re
u={"B":1,"KiB":2**10,"MiB":2**20,"GiB":2**30}
t=0
for l in sys.stdin:
    v,unit=re.match(r"([\d.]+)(\w+)",l.split("/")[0].strip()).groups(); t+=float(v)*u[unit]
print(f"TOTAL b2c memory in use (idle): {t/2**30:.2f} GiB")'
} 2>&1 | tee "$out"
