# Translation mixed-English recovery execution ‚Äî 2026-10-10

## Outcome

The automatic completeness gate selected the saved translation units for retry; no segment IDs were hand-picked for the retry request. The original workflow had 776 checkpointed segments: 765 passed the new gate and 11 were flagged for long English prose left in Arabic output.

The first resume reused the accepted checkpoints and automatically retried the 11 suspects. Ten passed. The gate flagged one remaining unit (`book-main:636:0`), whose source and output were inspected: the opening title and sentences remained in English. A second forced resume from that checkpoint retried the one remaining suspect without rerunning the other 775 accepted units.

Final verification through the API:

- Workflow `b2c-translate-973bbf3ed34ec7e77ae3244dd7d037e055b82a190b342a6c4a6bd886d988ff5e`: `COMPLETED`.
- Saved translation units: 776 / 776.
- Automatic completeness-gate suspects: 0.
- Failed or deferred units: 0.
- Previously mixed-English unit `book-main:636:0`: now Arabic and passes the gate.
- Model route retained from the parent run: `deepseek/deepseek-v4-flash-0731` on `external-generation`.

## Data association finding

The translation source hash is `719c00096d1658f53980a48b4410a8cec30009e47908c0daeb9a3918352253d3` (parsed artifact key `e40a641ee1016034e6f24b1c191c32725472dc3edad6ae6ad0aecf9db0d67dcf`). At verification time, the current `book-main` progress record pointed to parsed source hash `85efaece6070160f26ab60332944c9f9fc0d341bb68df924780272db74115a2b`; a second registered book ID was also present. Since the current `book-main` record and the completed translation do not share a source hash, do not attach downstream audio to this translation until the book-to-source association is reconciled.

## Runtime recovery

The B2C data disk is mounted at `/mnt/wsl/b2cdata` and its marker is present. B2C Postgres, Redis, MinIO, Temporal, API, worker, and generation worker are healthy/running. No other Temporal workflow was running before the translation resume; afterward, the only running workflow was the resumed translation. AI Company source/configuration/data were not modified.

## Android build

APK v0.5.6, ARMv7 (32-bit), built successfully and copied to the workspace deliverables folder and Google Drive. A connected Android emulator was not available in this environment, so this build was not installed or instrumented here.

## New book indexing follow-up+ßuÁ‚ùÁT 2026-10-10

The current progress API now identifies the newly uploaded book as `book-70c88ae9b2ba9ed6f61e813c`. Its latest ingest is `b2c-ingest-9f82296b6b3f1b0e5abae9b12cf02f56cf7745ef4ca200f43eaeb00a6fbf088b`, parsed source SHA `95271e047a8dd76c7c420bd3f2334c3f7fb62fc16c371f6a2b1b17270db1c236`. Its outline approval and indexing workflow `b2c-index-f19d0516494e7f3c312a5da3285df214b24cce5bbd6182913826afd76d1a25d6` both report `COMPLETED`; the index result contains 507 chunks.

The separate legacy ID `book-main` still resolves to a different ingest workflow and parsed source SHA `85efaece6070160f26ab60332944c9f9fc0d341bb68df924780272db74115a2b`, with ingest completed and no downstream outline/index entries in its progress record. The completed 776-unit translation still has a different source SHA (see Data association finding). Therefore it remains unsafe to launch audio against either record without resolving the translation's source-to-book association.

At this check the B2C API and core containers were running, but the Langfuse web container was restarting. Temporal CLI was not installed in the Temporal container, and an attempted SDK query used an unresolved DNS alias; the active-workflow list was not independently confirmed in this follow-up.
