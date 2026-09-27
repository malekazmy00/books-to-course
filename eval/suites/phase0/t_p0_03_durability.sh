#!/usr/bin/env bash
# T-P0-03: SIGKILL the worker while `hello_greet` (heavy queue) is running; restart it;
# the workflow must complete with the correct result, and finished activities must not re-run.
source "$(dirname "$0")/lib.sh"
out="$EV/t_p0_03_durability.txt"
name="Durable$(date +%H%M%S)"
{
  stamp "b2c-hello start --name $name --delay 40"
  wid=$($RUN worker b2c-hello start --name "$name" --course-id crs_p0_durable --delay 40 \
        | python3 -c 'import json,sys;print(json.load(sys.stdin)["workflow_id"])')
  echo "workflow_id=$wid"
  echo "waiting for hello_greet to start on the heavy queue..."
  for i in $(seq 1 30); do
    $RUN worker b2c-hello history "$wid" > /tmp/b2c-hist.json 2>/dev/null
    grep -q '"attempt"' /tmp/b2c-hist.json && python3 - <<'EOF' && break
import json,sys
h=json.load(open("/tmp/b2c-hist.json"))
sched=[e for e in h if e["event"]=="ACTIVITY_TASK_SCHEDULED"]
sys.exit(0 if len(sched)>=2 and sched[-1]["activity"]=="hello_greet" else 1)
EOF
    sleep 2
  done
  sleep 5
  wpid=$(docker inspect --format '{{.State.Pid}}' b2c-worker-1)
  echo
  stamp "kill -9 $wpid   # host PID of the worker process (b2c-worker-1), mid hello_greet"
  kill -9 "$wpid"; echo "kill exit=$?"
  sleep 2
  docker inspect --format 'b2c-worker-1 state={{.State.Status}} exitCode={{.State.ExitCode}} oomKilled={{.State.OOMKilled}}' b2c-worker-1
  # restart policy unless-stopped would bring it back itself; make it explicit and logged
  stamp "$C up -d --no-deps worker"
  $C up -d --no-deps worker 2>&1 | tail -2
  docker inspect --format 'b2c-worker-1 state={{.State.Status}} startedAt={{.State.StartedAt}} restartCount={{.RestartCount}}' b2c-worker-1
  echo
  stamp "b2c-hello wait $wid"
  $RUN worker b2c-hello wait "$wid"
  echo
  stamp "b2c-hello history $wid   # activity attempts / worker identities"
  $RUN worker b2c-hello history "$wid"
  echo
  echo "## worker log lines after restart (stage/cache_hit/attempt)"
  $C logs --no-log-prefix --since 5m worker 2>&1 | grep -E '"stage done"|worker started' | tail -10
} 2>&1 | tee "$out"
