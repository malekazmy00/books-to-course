# Book2 deferred retry runtime audit — 2026-10-14

## Exact findings
Read-only inspection reconstructed the12deferred batches against the immutable parsed source and actual completed IDs. Each batch has exactly one pending segment; the others are saved. There are12actual pending segments, no stale or inflated deferred rows at inspection:13:2,14:2,16:1,17:1,18:0,20:2,26:2,37:2,42:0,46:3,47:1,48:2 under book-70c88ae9b2ba9ed6f61e813c.

632archived OTelobjects were inspected in-memory, filtered to workflow b2c-translate-34a38983393ed2772c77236afccdd6cfc39725197b65fc978bb153931b92e440. Only redacted metadata was saved in book2-deferred-prompt-metadata-2026-10-14.json; no prompt/output text or credentials are in that evidence file. All12have source-present metadata. Their archived attempt2inputs have no retry_feedback/reason_code and no constrained targeted-correction instruction. Each has2uncached historical model calls;20:2and26:2also have a later uncached initial request. Later requests for earlier failures sometimes returned old cache hits (attempts0), so these are not fresh corrective inference calls.

## Root cause
Runtime worker2uses b2c-hy-worker:dev / image sha256:d1ba4d75ab928ef953186ab6a708649e2f63eba1a2ce4604c7f9263388173140. Its generation.py SHA256 is a0b1f71c3802dd132758a56b52b4af751df3a0effa56becbcb3aac03fe4f981d and contains neither _make_retry_feedback nor _attach_retry_feedback. The repository and known-good terminology-repair image contain SHA2563c2839d0524053605c5d327e100544c554790a04280b3f341050eae5b5fa84a5 with both helpers. The HYmodelservers1/2/3contain matching feedback-enabled server code SHA256b815879e9d1c37c14596b8c4e660a257fce8a76fb4eadc0f8358ec0586998a23.

Compose appendix-repair override previously changed only hy-translation-worker, leaving worker2on its inherited dev image. Added explicit worker2image override to preserve future deployments. Compose config validates with all profiles. The running worker is not altered until a committed pause and no pending activities.

## Validation and safe rollout status
93focused tests passed: generation adaptive feedback, HYretry prompt, partial-checkpoint durability, checkpoint selection. Dedicated durable pause_after_checkpoint request for Book2body acknowledged by HTTP200at2026-10-14T15:37:03UTCas PAUSE_REQUESTED. This does not interrupt in-flight output. At15:37:56UTCits activity was still Started, so no worker/model restart was performed. Await PAUSEDwith committed checkpoint and zero pending activities before worker2-only replacement; resume same workflow from durable checkpoints afterward. Do not consider actual targeted correction fixed until newly archived retry metadata verifies it.

## Validator causes confirmed from cached responses
13:2unclosed_target_bracket;14:2,18:0,47:1,48:2numeric_placeholder_order_mismatch;16:1,20:2,37:2numeric_token_mismatch;17:1,26:2,42:0malformed_numeric_placeholder;46:3unexpected_table_placeholder plus omitted bracketed content. The preserved response for46:3contains an invented CELLmarker in prose, so bypassing validation would accept damaged content.

The source initially requests2total attempts (initial plus one correction), then queues a further deferred pass after full traversal. It does not provide2immediate corrective model calls after the initial response. The updated source retains typed feedback when saving deferred messages; the earlier suspected feedback-map-loss claim was corrected after inspecting this full path. The actual defect is the obsolete running worker image.


## Post-rollout follow-up — 2026-10-14 18:13 UTC

Checkpoint reads show Book2 body 123/2620, batch 51/804, 12 deferred, zero terminal failed/suspect; latest saved checkpoint 18:13:42 UTC. Book1 body is 232/776 with zero deferred; Book1 appendix 485/888 with 7 deferred. Book2 appendix remains 644/644 complete. Temporal still reports active translation workflows. Worker2 has not restarted (restart count 0) and still runs the feedback-enabled image/hash verified above. One Book2 request routed through the proxy/backend completed successfully at 18:13:40 UTC after ~25 minutes; while waiting, model backend CPU was near 380% and its configured request timeout is 1800 seconds. Therefore the earlier stale checkpoint was a long in-flight inference, not an idle worker; it completed and saved new progress. No interruption or replay was performed. The 12 deferred remain pending their final pass and are not counted as resolved yet.
