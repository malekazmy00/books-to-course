# BookTrans Pro as an alternate translation route

## Scope

BookTrans Pro is an opt-in translation engine beside the existing B2C engine. It
uses the same translation request, immutable source artifact IDs, course glossary,
Temporal workflow, and result artifact format. The API publishes separate choices
for the configured external generation provider and local Gemma 4 when their
respective workers are enabled and ready. The Android model screen can make either
choice the translation default for newly started or explicitly resumed work.

Changing the selected default does not mutate a workflow that is already running.
To change engines for a book, start a resume/new translation request with the
selected provider; verified prior B2C checkpoints are eligible for reuse, while
BookTrans keeps its own durable scout/translation/edit/verification files.

## Processing and safeguards

The BookTrans route:

1. Preserves each source segment ID and source language in its native book
   manifest, and rejects duplicate IDs or mismatched returned IDs.
2. Uses content-addressed persistent workspaces scoped to source, provider model,
   endpoint, target language, book glossary/domain, and prompt version. The API key
   is passed through the child process environment, never in command arguments or
   persisted prompt/work files.
3. Seeds complete validated B2C checkpoints to avoid repeating translation work
   when switching routes. BookTrans still runs its later editing/verification
   passes on the saved translation.
4. Sends the assembled output through the existing B2C segment completeness,
   numeric, citation/identity, glossary, checkpoint, and Arabic correction path.
5. Persists a BookTrans pass-usage summary with the result artifact. Missing
   provider cost/token values remain explicitly unreported rather than inferred.

The worker image installs BookTrans runtime dependencies from its vendored MIT
source tree. The route is disabled unless `B2C_BOOKTRANS_PRO_ENABLED=true`.
External and Gemma 4 routes are separately exposed and queue-bound; there is no
client-supplied arbitrary endpoint or secret.

## Validation and rollout status

Focused unit tests cover source ID retention, durable route checkpoints, API-key
handling, glossary injection, saved-checkpoint seeding, engine selection, and
provider readiness. Compose files are configuration overlays only. This change
has not been deployed, the worker image has not been rebuilt, and no live model
inference has been run. Before production rollout, build the worker image, enable
the external route with the approved API profile, and run a small non-production
translation fixture through BookTrans and B2C's final gates. Enable the Gemma 4
choice only when its model service and task-queue worker are healthy.
