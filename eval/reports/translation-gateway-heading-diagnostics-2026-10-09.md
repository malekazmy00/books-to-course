# Gateway heading-only response investigation —2026-10-09

Scope: native Book2Course only. Read scoped-v2 private ledger of154failed/source units and current gateway implementation. No external inference, no production mutations/restarts, no translation engine edits. Sanitized counts are in translation-gateway-heading-diagnostics-2026-10-09.json.

## Observed
153units have a reconstructed latest full-source request. All153 requested8192output tokens and reasoning_effort=none.95long sources have a latest target shorter than45%of source length.18distinct multi-segment cached responses duplicated identical text across different IDs. These are diagnostic-slice counts, not overall model-quality statistics.

Examples31/34/71:1/92:1/104:1 contain1996/1370/3198/695/4169source characters in their actual reconstructed request entries. Their resulting targets contain12/16/40/35/45characters; reported completion budgets consumed30/30/76/74/76tokens rather than approaching8192. The two-ID responses for71/92/104 repeated heading content across heading and body IDs.128:0is different: its latest full-source target has1484characters for1787source characters, so that latest response is not heading-only.

## Execution-path evidence
LLMGateway.structured_with_result builds schema/params then _run computes the response-cache key. _call sends the supplied messages and params unchanged to the endpoint. The structured translation route does not call chat_book_pages, where separate splitting exists. There is no gateway input truncation or max_tokens replacement in this route.

The schema constrains IDs and number of output segments; text permits1to24000characters. A heading-only value therefore passes schema validation. Semantic numeric/completeness validation occurs later in translation code. _generate checks schema and retries invalid JSON, adding the prior output and schema error. A schema-valid short value receives no JSON repair and enters cache; the later translation gate rejects it.

The exact cause of the initial model/provider emitting heading-only output cannot be established from retained cache records. All153full-source latest records lack originalfinish_reason and schema-repair attempt count. Smallreported output lengths and generousrequested budgets do not prove how the provider terminated. No claim of context exhaustion, provider refusal or provider outage is justified by this evidence.

## Prepared telemetry repair
Gateway cache now retains originalfinish_reason and generation_attempts; cache hits expose those fields. Legacy cache entries retain unknownmetadata rather than fabricatingfinish_reason=stop. OTel records originalgenerationattempt count and finish reasons without additional content logging. Existing cache keys, request construction and semantic gates are unchanged. Historicalmetadata cannot be reconstructed retroactively.

47gateway testsPASS, including actual full-input/max_tokens forwarding, schema-repair metadata surviving cache, legacy unknownmetadata and the existinggateway corpus. Nodeployment. This repair improves future diagnosis; it does not itself fix semantic omissions. The independently prepared translation unit/error-specific corrective path is the relevant functional repair.
