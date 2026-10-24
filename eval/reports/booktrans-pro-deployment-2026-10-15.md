# BookTrans Pro runtime deployment - 2026-10-15

Scope: Book2Course on the D-backed B2C-On-D distro only. No AI Company resources were inspected or changed.

## Changes deployed

- Enabled the BookTrans Pro external provider in the API using docker/compose.booktrans.yml.
- Rebuilt and recreated b2c-api-1 and b2c-generation-worker-1. Compose also recreated dependencies affected by shared worker image tags: general worker, HY workers 1 and 2, and HY model services 1 and 2.
- Created the persistent, initially empty /mnt/wsl/b2cdata/state/booktrans-work directory, mounted at /var/lib/booktrans in the generation worker.
- Increased API Docker healthcheck request timeout from 2s/3s to 8s/10s. Direct /health requests returned HTTP 200 while the previous 3-second Docker check timed out.

## Verification

- Full Compose configuration validated with the active hy-translation profile and BookTrans overlay.
- Focused BookTrans route tests: 2 passed.
- Generation worker imports b2c_orchestration.booktrans_route and pypdfium2 successfully; logs confirm polling external-generation.
- Protected model-provider catalog returns booktrans-pro-external as ready; evaluation_status remains limited_test.
- API /health: HTTP 200; container health: healthy.
- Redis healthy, approximately 27 MiB / 512 MiB; Temporal healthy.
- No translation inference was started, so no model tokens were used and end-to-end translation quality remains unverified.

## Limits

- Local BookTrans + Gemma 4 remains unsupported until the Gemma4 service and worker profile are separately enabled and verified.
- External readiness confirms provider model-list availability and worker deployment only; it is not a translated-book acceptance test.
- Compose warned about pre-existing orphan containers; none were removed or altered.
