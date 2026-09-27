#!/bin/sh
# One-shot: wait for the cluster, then create the namespace if missing.
# Written from scratch (upstream script has a typo in its retry guard, see V-05).
set -eu
NS="${DEFAULT_NAMESPACE:-default}"
for i in $(seq 1 60); do
  temporal operator cluster health --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1 && break
  sleep 2
done
if temporal operator namespace describe -n "$NS" --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then
  echo "namespace $NS exists"
else
  temporal operator namespace create -n "$NS" --retention 72h --address "$TEMPORAL_ADDRESS"
  echo "namespace $NS created"
fi
