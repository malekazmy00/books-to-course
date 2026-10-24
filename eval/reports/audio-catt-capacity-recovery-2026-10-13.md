# Audio CATT capacity recovery — 2026-10-13

Scope: B2C on B2C-On-D. No inference, no service/workflow restart, no saved artifact mutation.

## Cause
Fasih logs show deterministic CATT RuntimeError: input tensor length 3055, positional embedding capacity 1024. The renderer accepts 4500-char chunks and Fasih originally invoked diacritization before its 160-char synthesis chunker. Healthy service status does not detect input overflow. Renderer turns this provider 500 into 502, producing repeated Temporal retries.

## Fix
Wrap the Fasih diacritizer at its normalized/number-expanded input boundary. Sentence-first contexts are capped at 900 characters, leaving BOS/EOS margin below 1024. Preserve every character/space, diacritize contexts serially, then rejoin before XTTS synthesis chunking. Thus context splits do not create added audio pauses. Reject unbroken oversized tokens and empty model output rather than truncate.

Files: docker/fasih/catt_bounds.py, server.py, Dockerfile; tests/unit/speech/test_fasih_catt_context_bounds.py.

## Evidence
14 focused tests passed (track generation/order/checkpoints/lineage plus new context coverage): bounded context reconstruction, <=900 char inputs, oversized token rejection, empty response rejection; saved audio lineage/discovery tests. Read-only authenticated live status and manifest endpoints both HTTP 200, current 0/135; saved-audio endpoint HTTP 200 with 12 identity-bound historical tracks. Exact current APK UI error cannot be asserted without device reproduction; the backend display endpoints are working.

## Deployment
Pending isolated Fasih deployment. Build context is /mnt/wsl/b2cdata/build-context/fasih; sync the three source files there. Update Fasih only after checking pending audio activity is scheduled and renderer/model idle; do not restart HY or other workers. Preserve renderer cache and all existing audio. No new inference was issued by this audit.

## Deployment and client update
Fasih was recreated alone after an immediate Temporal pending-state guard returned SCHEDULED. No HY/shared worker was restarted and no inference was requested by the operator audit. New image manifest SHA256:15d34c0bfaa7f717fff006a357e8d3bf5313c0cde93dff9fdc790ebfb83f5a2c. Source/build context synchronized. Health verified HTTP200 status ok/model Fasih-TTS-V1/device cpu; Docker healthy. Startup read5.90GB and completed; old font-cache warnings were nonfatal. CATT initialization did not log unavailability. Future build files include writable MPLCONFIGDIR/XDG_CACHE_HOME settings.

Android update-list now falls back to exact-book historical saved tracks when current manifest contains zero tracks, clearly labeling them historical comparison outputs and current count0. Existing context guards and immutable download_path lineage are retained. Java compilation passed. ARMv7 APK assembled successfully; SHA256798ce343b803df630fa1ecffb61fd44433bf99c0365f6317e076c6c01ba648dd. No device test occurred. Live historical download verified HTTP200,14,115,500bytes with ID3 header.
