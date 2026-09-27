"""Sum mem_limit / cpus over the resolved compose config (reads `docker compose config --format json` on stdin)."""
import json
import sys

services = json.load(sys.stdin)["services"]
total_mem = total_cpu = 0.0
missing = []
for name, s in sorted(services.items()):
    mem = int(s.get("mem_limit", 0))
    cpu = float(s.get("cpus", 0))
    shares = s.get("cpu_shares")
    if not (mem and cpu and shares):
        missing.append(name)
    total_mem += mem
    total_cpu += cpu
    print(f"{name:<20} mem_limit={mem / 2**20:8.0f} MiB  cpus={cpu:5.2f}  cpu_shares={shares}")
print(f"{'TOTAL':<20} mem_limit={total_mem / 2**30:8.3f} GiB  cpus={total_cpu:5.2f}")
print("services missing a limit:", missing or "none")
sys.exit(1 if missing else 0)
