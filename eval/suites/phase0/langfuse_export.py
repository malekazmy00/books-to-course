"""Export one trace's observations from Langfuse v4 and check the gateway span's GenAI attributes."""
import json
import os
import sys
import time

import httpx

BASE = "http://langfuse-web:3000"
auth = (os.environ["LF_PK"], os.environ["LF_SK"])
tid = os.environ["TID"]

rows = []
for _ in range(12):
    r = httpx.get(f"{BASE}/api/public/v2/observations", auth=auth, timeout=30,
                  params={"traceId": tid, "limit": 100,
                          "fields": "core,basic,usage,model,io,metadata"})
    print("HTTP", r.status_code)
    if r.status_code != 200:
        print(r.text[:2000])
        sys.exit(1)
    rows = r.json().get("data", [])
    if any(o.get("type") == "GENERATION" for o in rows):
        break
    time.sleep(5)

print(f"observations in trace {tid}: {len(rows)}")
for o in sorted(rows, key=lambda o: o.get("startTime", "")):
    print(f"  {o.get('startTime','')}  {o.get('type',''):<11} {o.get('name','')}")

gens = [o for o in rows if o.get("type") == "GENERATION"]
print("\n=== GENERATION observation(s) (raw JSON) ===")
print(json.dumps(gens, indent=2, ensure_ascii=False)[:8000])

ok = bool(gens) and all(g.get("model") or g.get("providedModelName") for g in gens)
print("\nRESULT:", "PASS" if ok else "FAIL",
      "(generation span from llm_gateway present with model)" if ok else "(no generation with model)")
sys.exit(0 if ok else 1)
