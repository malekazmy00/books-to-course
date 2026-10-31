# HY worker 2 terminology image rollout - 2026-10-31

## Scope and preflight

B2C only. The project data disk was mounted at /mnt/wsl/b2cdata (/dev/sde, ext4). The B2C Compose configuration validated with project name b2c and the project .env. Temporal had no open translation workflow; the only open translation/audio-family execution was the long-lived MP3 workflow. HY worker 2 had only a startup log and no active translation workflow was present. No inference was replayed.

## Change

docker/compose.hy-translation.yml now pins hy-translation-worker-2 to b2c-hy-worker:terminology-repair-20261013. This uses the already-present local image and changes only worker 2.

The worker was recreated with Compose using --no-deps --no-build --pull never. Worker 1 was left running unchanged; workers 3 and 4 remain stopped.

## Verification

- docker compose config --quiet: passed with the B2C project .env.
- docker inspect b2c-hy-translation-worker-2-1: image b2c-hy-worker:terminology-repair-20261013, state running.
- Worker log confirms queue hy-translation and model tencent/Hy-MT2-1.8B; the base URL remains the HY proxy.
- Main API health endpoint returned HTTP 200.
- Temporal still showed no open translation workflow after the rollout. No saved inference or checkpoint was replayed.

## Remaining issues

- HY workers 3 and 4 are not running.
- The latest known old HY checkpoint counters are dated 2026-10-27 and have not been refreshed because the translation workflows are no longer queryable by their former IDs.
- Gemma4 final translation artifacts remain in MinIO, while their final assembly statuses are rejected; this rollout did not alter or reassemble those outputs.
- No change was made to any project outside Book2Course.
