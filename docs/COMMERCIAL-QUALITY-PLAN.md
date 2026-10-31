# Commercial quality and release plan

> **Not applicable (2026-10-31).** The project is non-commercial research (D-001) with no human review in the production line (D-033). Kept for history only; see `docs/00-RESEARCH-CHARTER.md`.

## Purpose and limits

Use this as a practical quality plan for a potential commercial release. It is aligned with ISO/IEC 25010:2023 product-quality model, ISO/IEC/IEEE 29119 software-testing process/strategy concepts, and NIST SSDF secure-development practices. Alignment is not certification or proof of compliance. A commercial release needs agreed customer requirements, measurable acceptance criteria, and auditable evidence.

## Required traceability

Every externally visible requirement must map to:
1. a product requirement and risk,
2. one or more test cases or independent evaluations,
3. an owner and reproducible environment,
4. results, thresholds, and defects,
5. a release gate decision.

No aggregate score can mask a critical failure in a language, document class, tenant boundary, security check, or recovery scenario.

## Test layers and gates

| Layer | Required evidence before commercial release |
|---|---|
| Unit and contract | Deterministic business rules, schema invariants, parser boundaries, model/API compatibility; coverage used as a gap signal, not as a pass threshold by itself |
| Integration | Real database/object store/workflow integration in isolated disposable environments; migrations, retries, idempotency, timeout and failure handling |
| End-to-end | Representative spine + supplementary books through upload, OCR, outline review, retrieval, course generation, translation, speech, and client playback |
| Content quality | Licensed adjudicated sets by language and document type; OCR CER/WER and page coverage; table/layout fidelity; citations and cross-book attribution; translation MQM/COMET used with human review; speech intelligibility and pronunciation |
| Robustness | Corrupt/encrypted/oversized PDFs, malformed metadata, page rotation/skew/blur/noise, mixed scans and text, cancellation, duplicate requests, process kills, restart and recovery |
| Security/privacy | Threat model; authn/authz and tenant isolation; upload/parser abuse and resource exhaustion; dependency/code/container scans; secret and log checks; prompt injection/data exfiltration tests; retention/deletion behavior |
| Performance/capacity | Agreed workload and hardware; throughput, p50/p95/p99 latency, queue age, peak RSS/VRAM, storage growth, concurrency, and saturation/recovery |
| Operations | Dashboards/alerts, structured redacted logs, backup/restore rehearsal, rollback, incident runbook, SLOs, support and data-retention policies |
| Client/accessibility | RTL and screen-reader user testing, WCAG 2.2 AA checks, low-bandwidth/offline behavior if promised, Android device/version matrix |
| Release engineering | Reproducible pinned build, SBOM, artifact provenance/signing, vulnerability disposition, migration plan, release notes, rollback/forward-fix plan |

## OCR engine selection (provisional)

Do not label any candidate best based only on a vendor benchmark. Compare candidates on the same rights-cleared Arabic/English corpus, under the worker's real CPU/RAM limits:

| Candidate | Role in comparison | Risks to measure |
|---|---|---|
| Docling + RapidOCR PP-OCRv5 Arabic / English | Current lightweight baseline; Arabic uses the arabic script model and English its own recognizer | Arabic line detection and right-to-left order, blur/low DPI, tables/layout fidelity, error rate and runtime |
| PaddleOCR-VL-1.6 | Higher-capacity document-parsing candidate; official project documents report robustness scenarios including scanning, warping and skew, and the project lists Arabic among supported languages | CPU-only latency/peak RAM, Arabic reading order, licensing/deployment fit, layout/citation provenance and measured quality on our documents |

Use per-stratum CER/WER and layout/provenance checks. Consider an escalation fallback only if the higher-quality model improves the hard cases enough to justify memory, speed, and operational cost. Model-card and vendor benchmark claims are leads for testing, not project results.

## Phase 2 exit gate

The current phase is ingestion and outline. It cannot pass based on the existing unit/contract count. Required evidence:
- text-PDF and scanned-PDF ingestion including Arabic and English,
- stratified licensed golden corpus with ground truth and documented rights,
- character error rate by language and scan condition, page/block provenance accuracy, heading recall, table/layout score, latency and peak memory,
- test of mixed scanned/digital pages and exact OCR source-page references,
- hostile/invalid input negative controls,
- compose-backed upload -> workflow -> parsed artifact -> outline -> human approval -> approved artifact,
- repeat/retry/restart behavior and idempotency,
- test and build results captured in eval/reports/phase-2.md.

## Commercial release gates (later phases)

A release owner must approve the requirement-to-test matrix, risk dispositions, security review, rights review, performance/load results, restore exercise, accessibility evidence, support/operations readiness, and a rollback path. Thresholds must be agreed before acceptance runs and not lowered to make a failing run pass.

## Reference standards and guidance

- ISO/IEC 25010:2023 product-quality model: https://www.iso.org/standard/78176.html
- ISO/IEC/IEEE 29119-1:2022 software-testing general concepts: https://www.iso.org/obp/ui/en/#iso:std:iso-iec-ieee:29119:-1:ed-2:v1:en
- NIST SP 800-218 Secure Software Development Framework (SSDF): https://csrc.nist.gov/pubs/sp/800/218/final
