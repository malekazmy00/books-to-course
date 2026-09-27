#!/usr/bin/env bash
# T-P0-04: docker inspect of every b2c container (incl. exited one-shots): Memory, NanoCpus, CpuShares.
source "$(dirname "$0")/lib.sh"
out="$EV/t_p0_04_limits.txt"
{
  stamp "docker inspect (all containers with label com.docker.compose.project=b2c)"
  ids=$(docker ps -aq --filter label=com.docker.compose.project=b2c)
  docker inspect --format '{{.Name}}|{{.HostConfig.Memory}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.CpuShares}}|{{.State.Status}}' $ids \
  | sort | python3 -c '
import sys
tm = 0; bad = []
print(f"{\"container\":<28}{\"Memory\":>14}{\"MiB\":>8}{\"NanoCpus\":>14}{\"cpus\":>7}{\"CpuShares\":>11}  state")
for line in sys.stdin:
    n, m, c, s, st = line.strip().split("|")
    m, c, s = int(m), int(c), int(s)
    tm += m
    if not (m and c and s): bad.append(n)
    print(f"{n.lstrip(\"/\"):<28}{m:>14}{m/2**20:>8.0f}{c:>14}{c/1e9:>7.2f}{s:>11}  {st}")
print(f"\nTOTAL Memory = {tm} bytes = {tm/2**30:.3f} GiB   (limit 61 GiB, D-007 as amended 2026-09-27)")
print("containers missing Memory/NanoCpus/CpuShares:", bad or "none")
print("RESULT:", "PASS" if not bad and tm <= 61*2**30 else "FAIL")
'
} 2>&1 | tee "$out"
