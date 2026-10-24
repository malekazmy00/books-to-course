# Phase 10 — Hardening preparation report

Date: 2026-10-05
Disposition: PREPARATION IN PROGRESS; Phase 10 acceptance NOT PASSED.

## Scope and safety boundary
Reviewed the B2C delivery plan, current status, phase work order, and operations/test specifications before editing. Work stayed inside /root/projects/book2course and B2C-labeled local tooling. Confirmed /mnt/wsl/b2cdata is mounted read-write from /dev/sde. Compose validation used project name b2c. No services were started, stopped, restarted, or reconfigured live; no inference or external API was called; no files or containers belonging to ai company were inspected or touched. The Phase 2 release gate is still open, and no complete D-017 run was initiated.

## Existing-state audit

| Area | Existing evidence | Finding |
|---|---|---|
| Prometheus | Compose definition, scrape config for Prometheus/OTel/Temporal/cAdvisor; cAdvisor relabels to retain B2C only | No Prometheus process is currently running. Scrapes cover infrastructure, not API/worker/product metrics. No alert rule file was wired. |
| Grafana | Compose definition and a Prometheus datasource provisioning file | No Grafana process is currently running; no dashboards were provisioned. |
| Alerts | docs/04-OPERATIONS.md defines resource/application thresholds | There was no rules file, Alertmanager service/contact point, notification route, or alert delivery evidence. |
| Backups | docs/04-OPERATIONS.md describes daily pg_dump/WAL, object-store copy to another disk and off-device encrypted copy as requirements | No backup/restore script or backup directory was found on the B2C data disk (searched to depth 3). The output destination/credentials/key custody are undefined. Same-disk copies would not meet the stated 3-2-1 requirement. |
| T-OPS-01 | docs/03-TESTING.md table labels “restore backup to an empty environment” successful | No matching command/output/report artifact was found. This is an unsupported historical status claim; it is not credited as verified in this Phase 10 review. |
| D-017 full run | Phase2 upload/outline and bounded later-phase results exist | No full D-017 book-to-served-course evidence was found. Current status says Phase 2 release gate is open; its accepted outline, complete quality gates, Phase9 delivery and audio path are unavailable. Per instruction, no full run/inference was started. |

## Implemented preparation

1. Added docker/prometheus/alerts.yml and wired it as a read-only rule file into the B2C Prometheus container:
   - B2CScrapeTargetDown: fires after 2 minutes for the configured Prometheus, OTel, Temporal, or cAdvisor scrape target being down.
   - B2CContainerMemoryBudgetHigh: fires after 5 minutes above the existing 78 GiB B2C working-set budget.
2. Added a read-only provisioned Grafana dashboard for total B2C memory against the budget, CPU usage, configured scrape health, and count of firing B2C alerts. Assigned a stable Prometheus datasource UID and mounted dashboard files read-only.
3. The alerts evaluate locally in Prometheus and appear on the dashboard. Alert notification delivery is not configured: no Alertmanager/contact point is present, and no external notification integration was introduced. Disk >80%, application p95/quality, database health and Temporal retry thresholds remain unimplemented because the required host/app metrics or verified series are not currently scraped.
4. No operational runtime change has taken effect; Prometheus/Grafana were not running and the stack was not restarted.

## Non-destructive validation (executed)
- findmnt -M /mnt/wsl/b2cdata: /dev/sde ext4 rw.
- B2C-filtered docker ps: API, worker, Temporal, Postgres, MinIO, cAdvisor, OTel and Langfuse components are present; Prometheus and Grafana are absent. The command filtered on com.docker.compose.project=b2c.
- docker compose -p b2c --env-file .env -f docker/compose.yml -f docker/compose.observability.yml config -q: exit 0.
- pytest tests/unit/ops/test_phase10_monitoring_config.py -q: 3 passed. Checks YAML/JSON structure, B2C datasource identity and read-only mount/provision paths.
- Offline promtool check config using the already-cached, pinned Prometheus image with --network none, --read-only, and a read-only config mount: success; one rule file and two rules validated. The ephemeral validator was removed automatically; no service container was created.
- No live Prometheus query or Grafana UI check was possible because those services are stopped.

## Remaining acceptance work
- Define and provision a notification receiver/contact method, then perform a controlled alert-firing and recovery check without sending real external messages.
- Add verified app, database, disk, workflow and quality metrics plus appropriate alert rules and dashboard panels; agree thresholds against measured baselines.
- Define an off-host backup destination, encryption/key custody, retention, and consistent Postgres/WAL plus MinIO/object backup procedure. Produce a real backup; restore it into an isolated empty B2C test environment and verify database rows, artifact hashes and application readability. Do not use the source data disk as the only backup destination.
- Resolve unsupported T-OPS-01 status by either locating its original evidence or rerunning the test when a valid isolated destination is ready.
- Close Phase2 first, complete Phase9's usable D-017 serving path, then execute the complete D-017 acceptance run and document exact book/hash, chapter coverage, provenance, course artifacts, player/API results, error handling, and resource measurements. Do not bypass rights_status=internal_test_only for publishing.
- Execute load/failure, restart, rollback, and restore drills with thresholds agreed before the run; verify persistence after reboot where appropriate.

## Gate decision
Phase 10 is NOT PASSED. Dashboard and two local alert rules are prepared and statically/Promtool validated, but they are not live-tested. Backup/restore, outbound alert delivery, full D-017 run, and hardening drills remain unverified. This preparation does not mark Phase 2 or the release path as passed.

## Operational recovery and checkpoint evidence - 2026-10-05

This update supersedes the earlier stopped-service observations, without deleting their history. The owner authorized restoration of needed B2C services after reboot. Root restored the stack and preserved the persistent Grafana datasource UID PBFA97CFB590B2093; this work independently verified the live monitoring path and kept that identity.

### Runtime results
- Exact B2C VHDX mount verified: /mnt/wsl/b2cdata, /dev/sde, ext4 rw; disk marker present. B2C Compose config validation passed with both base and observability files.
- Prometheus reports all four configured scrape targets up: Prometheus, OTel Collector, Temporal and cAdvisor. Both deployed rules are healthy and inactive.
- Authenticated Grafana API readback returned the provisioned b2c-ops dashboard; every panel points to persistent datasource UID PBFA97CFB590B2093. Grafana health reports database ok.
- A real data query found an additional reboot fault: cAdvisor HTTP scrape was up but B2C memory data was absent because Docker factory registration timed out during boot. Restarting only b2c-cadvisor-1 after the stack was ready fixed registration. Prometheus now returns 15 B2C memory series, and no other project label, totaling 19,850,215,424 bytes at the recorded observation. The installed B2C autostart helper now restarts only B2C cAdvisor after Compose startup and requires a non-empty B2C-only Prometheus memory-series query before reporting success. The Task Scheduler run returned result 0; query count was 15.
- Evidence: eval/reports/phase10/reboot-runtime-evidence.json. No credentials were recorded.

### Project-owned checkpoint and isolated restore
Added scripts/backup-checkpoint.py. It validates the exact mount/marker, B2C Compose, and source container labels, then takes a custom-format pg_dump of the application database plus a streaming tar of the artifacts bucket. File/object SHA-256 hashes and exact public-table row counts are recorded. Source database row counts must remain stable before/after the dump. Credentials stay in the existing container environment and are never printed or placed in arguments/manifests.

The restore-check action validates checkpoint hashes before restoration, starts a generated PostgreSQL container on --network none with no published ports and a 2 GiB tmpfs, restores with --exit-on-error, and compares exact per-table counts. Objects are restored only into a newly generated b2c-restore-check-* MinIO bucket, read back and hashed, then read through ArtifactStore. The generated bucket and PostgreSQL container are removed in finally blocks. Source database and artifacts bucket are never overwritten.

Executed checkpoint:
- Destination: C:\Users\User\Documents\B2C-Backups\checkpoint-20261005T1215Z, outside the F: VHDX.
- App DB dump: 189,612 bytes; SHA-256 169a380b25cab15cec7d2f95ce4ebe373dbe3abe904370563a2da72415ee606e.
- Artifact archive: 8,990,720 bytes; SHA-256 820f7307200f6874d25924fd78f5bb0d547b3e362336f9288d7533550d2061f7.
- Restore PASS: 11 public tables, all 519 exact database rows matched by table; all 348 object hashes/readbacks passed; 168 ArtifactRefs and their files were readable using the project's ArtifactStore.
- Empty restore-container inventory confirmed cleanup. Temporary bucket cleanup completed.
- Windows ACL explicitly limits the checkpoint directory/files to User, SYSTEM and local Administrators; no Users/Everyone grants. Verified WSL can still read the protected checkpoint after ACL application.
- Non-sensitive checkpoint metadata and restore result are copied to eval/reports/phase10/manifest.json and restore-check.json.

This is a bounded local app-data checkpoint and isolated logical-restore drill. It is NOT a consistent cross-service transaction, WAL/PITR backup, Temporal/Langfuse backup, model/secret backup, disaster-recovery proof, or encrypted off-host 3-2-1 backup. No daily scheduler was installed; automatic retention and off-host destinations still need completion. The first initial checkpoint at checkpoint-20261005T1210Z also passed object/table restore but was superseded by the second checkpoint's exact row-count comparison.

### Focused validation
- Monitoring config tests: 3 passed. Expectations now use the persistent Grafana UID and ensure the panel/provisioning identities match.
- Added tests/fixtures/ops/prometheus_alerts.test.yml: synthetic target-down firing after the hold time, recovery, memory-budget firing/recovery, and exclusion of an unrelated synthetic project all passed through pinned Prometheus promtool. The first test invocation needed a writable tmpfs /tmp; rerun with --tmpfs /tmp:rw,size=64m passed. No live service was disrupted to fire an alert.
- Live monitoring, authenticated dashboard readback, and actual local checkpoint/restore passed.
- git diff --check passed.

### Current closure status
Monitoring deployment and bounded app DB/artifact recovery are now operationally demonstrated. T-OPS-01 has new scoped evidence for empty PostgreSQL restore plus isolated artifact readback; this does not retroactively validate the older unsupported broad status. Phase 10 release acceptance remains NOT PASSED: notification delivery, missing-data alerting, host/disk/application-quality metrics, encrypted off-host backup/retention and full-system recovery, load/failure/rollback drills, and complete D-017/app delivery evidence remain outside these executed checks. No inference, external messages, or AI Company operations were performed.

