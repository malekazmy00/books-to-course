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
print("{:<28}{:>14}{:>8}{:>14}{:>7}{:>11}  state".format("container", "Memory", "MiB", "NanoCpus", "cpus", "CpuShares"))
for line in sys.stdin:
    n, m, c, s, st = line.strip().split("|")
    m, c, s = int(m), int(c), int(s)
    tm += m
    if not (m and c and s): bad.append(n)
    print("{:<28}{:>14}{:>8.0f}{:>14}{:>7.2f}{:>11}  {}".format(n.lstrip("/"), m, m/2**20, c, c/1e9, s, st))
print(f"\nTOTAL Memory = {tm} bytes = {tm/2**30:.3f} GiB   (limit 85 GiB, D-007 as amended 2026-09-29)")
print("containers missing Memory/NanoCpus/CpuShares:", bad or "none")
print("RESULT:", "PASS" if not bad and tm <= 85*2**30 else "FAIL")
'
} 2>&1 | tee "$out"
