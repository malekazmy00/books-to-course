# B2C reboot recovery — 2026-10-05

## Outcome
The Windows/WSL reboot did not lose or replace the B2C data disk. The existing VHDX at `F:\wsl-b2c\b2c-data.vhdx` (68,119,691,264 bytes) mounted at `/mnt/wsl/b2cdata` as `/dev/sde` ext4 read/write. The `.b2c-data-disk` marker and `models/` directory were present. Existing PostgreSQL (100 MiB), MinIO (12 MiB), ClickHouse (4.3 GiB), Redis, Prometheus (88 MiB), and Grafana (1.6 MiB) data directories remained on that mount.

## Cause and repair
On the first post-reboot check, default B2C containers had exited because Docker tried to open bind mounts before the logon disk-mount task had completed. No volume was formatted, initialized, deleted, or replaced. Once the mount was verified, the existing `b2c` Compose project was brought up against its existing data.

Grafana initially restarted because the newly provisioned dashboard/config referenced UID `b2c-prometheus`, while the persistent Grafana database already contained the Prometheus data source with UID `PBFA97CFB590B2093`. The provisioning file and dashboard were aligned to the existing UID and Grafana was recreated. Grafana `/api/health` then returned HTTP 200.

## Durable reboot behavior
- Existing scheduled task `B2C-Data-Disk-Mount`: AtLogOn, elevated, last result 0; attaches and validates only the configured existing VHDX.
- Added `B2C-Stack-Start`: AtLogOn + 1 minute, user context, waits up to 15 minutes for the disk marker/models and Docker daemon, rechecks the mount, then starts only the project's default Compose stack (`-p b2c`, no build, no orphan removal), restarts only B2C cAdvisor, and gates success on a non-empty B2C-only memory-series query. The scheduled task was manually launched after registration and returned result 0.
- Source: `scripts/windows/start-b2c-stack.ps1`; installed copy/log: `%ProgramData%\B2C\start-b2c-stack.ps1` and `start-b2c-stack.log`.

## Post-recovery evidence
- Compose validation: `docker compose -p b2c --env-file .env -f docker/compose.yml -f docker/compose.observability.yml config -q` succeeded.
- API `/health`: HTTP 200; Temporal UI: HTTP 200; Grafana `/api/health`: HTTP 200; Langfuse `/api/public/health`: HTTP 200; Prometheus `/-/healthy`: healthy.
- PostgreSQL, Temporal, LLM, Langfuse web, ClickHouse, MinIO, Redis and API report healthy where health checks are defined; worker, Grafana, Prometheus, cAdvisor, OTel Collector and Temporal UI are running.
- No AI Company containers or files were touched.

## Scope note
The optional standalone LightRAG compose profile was not started as part of the default stack: it is a separate research path and exceeds the default stack resource allocation. The earlier failed LightRAG container invocation reported that the disk mount and local model path were unavailable at launch; the model path is now present. Its service readiness and research evaluation remain a separate Phase 4/6 task, not a prerequisite for B2C core startup.

