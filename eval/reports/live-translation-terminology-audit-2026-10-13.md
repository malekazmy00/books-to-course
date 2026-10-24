# Live translation terminology audit — 2026-10-13

Read-only direct inspection of every currently deferred appendix unit: 14 units, 28 original cached responses at 13:23 UTC. All responses have a terminology mismatch. There are no current terminal appendix failures; deferred units automatically retry after traversal. Active checkpoint progressed from 87 to 94/888 during inspection.

Book1 body: queued child now exposes 76/776 inherited successful checkpoints, 700 remaining, one historic failed ID. Book2 body: queued 41/2620. Book2 appendix: 644/644 checkpointed, publication-only child queued.

## Individual findings

| ID | Source | First raw response | Cause |
|---|---|---|---|
| m01-structured-analytic-techniques.l01:lesson-qa:1:question | What is the primary purpose of the book 'Structured Analytic Techniques for Intelligence Analysis'? | ما هو الغرض الرئيسي من كتاب “تقنيات التحليل المنظم لتحليل الاستخبارات”? | literal terminology_mismatch; both responses inspected |
| m01-structured-analytic-techniques.l01:lesson-qa:1:answer | The primary purpose of the book is to provide a comprehensive and accessibly written text on analytic techniques and the analytic process for intelligence analysis. | الهدف الرئيسي من الكتاب هو تقديم نص شامل وسهل القراءة حول تقنيات التحليل وعملية التحليل في تحليل الاستخبارات. | literal terminology_mismatch; both responses inspected |
| m01-structured-analytic-techniques.l01:lesson-qa:5:answer | The intended audience includes students of intelligence analysis, novices, professionals, analysts, practitioners, stakeholders, and leaders. | يتضمن الجمهور المستهدف طلاب تحليل الاستخبارات، المبتدئين، المحترفين، المحللين، الممارسين، أصحاب المصلحة، والقادة. | literal terminology_mismatch; both responses inspected |
| m02-structured-analytic-techniques-f.l01:lesson-qa:1:question | Who are the authors of the book Structured Analytic Techniques for Intelligence Analysis? | من هم مؤلفو الكتاب "تقنيات التحليل المنظم للتحليل الاستخباراتي"? | literal terminology_mismatch; both responses inspected |
| m02-structured-analytic-techniques-f.l01:lesson-qa:2:answer | T itle: Structured analytic techniques for intelligence analysis / by Randolph H. | العنوان: تقنيات التحليل المنظم للتحليل الاستخباراتي / بقلم راندولف هـ. | literal terminology_mismatch; both responses inspected |
| m02-structured-analytic-techniques-f.l01:lesson-qa:3:question | What year was the book Structured Analytic Techniques for Intelligence Analysis published? | في أي عام تم نشر الكتاب "تقنيات التحليل المنظم للتحليل الاستخباراتي"? | literal terminology_mismatch; both responses inspected |
| m02-structured-analytic-techniques-f.l01:lesson-qa:5:question | What publishing company is associated with the book Structured Analytic Techniques for Intelligence Analysis? | ما هي شركة النشر المرتبطة بالكتاب "تقنيات التحليل المنظم للتحليل الاستخباراتي"? | literal terminology_mismatch; both responses inspected |
| m05-foreword-by-john-mclaughlin.l01:lesson-qa:2:question | What is one key reason intelligence analysis has become more important? | ما هو أحد الأسباب الرئيسية التي جعلت تحليل الاستخبارات أكثر أهمية؟ | literal terminology_mismatch; both responses inspected |
| m07-pr-ef-a-c-e.l01:lesson-qa:3:question | What approach was later subsumed by a broader effort to transform intelligence analysis? | ما هي النهج التي تم دمجها لاحقًا في جهد أوسع لتحويل تحليل الاستخبارات؟ | literal terminology_mismatch; both responses inspected |
| m07-pr-ef-a-c-e.l01:lesson-qa:3:answer | The approach was later subsumed by a broader effort to transform the tradecraft of intelligence analysis by using what have become known as Structured Analytic Techniques. | تم دمج النهج لاحقًا في جهد أوسع لتحويل تقنيات تحليل الاستخبارات باستخدام ما يُعرف بتقنيات التحليل المنظم. | literal terminology_mismatch; both responses inspected |
| m07-pr-ef-a-c-e.l01:lesson-qa:4:question | What is the purpose of Structured Analytic Techniques in intelligence analysis? | ما هو الغرض من تقنيات التحليل المنظم في تحليل الاستخبارات؟ | literal terminology_mismatch; both responses inspected |
| m08-chapter-1-introduction-and-overv.l02:lesson-qa:1:question | What are Structured Analytic Techniques primarily classified as? | ما هي التقنيات التحليلية المنظمة مصنفة بشكل أساسي ك؟ | literal terminology_mismatch; both responses inspected |
| m08-chapter-1-introduction-and-overv.l03:lesson-qa:5:question | What did the Sherman Kent School for Intelligence Analysis consolidate in 2000? | ما الذي قامت مدرسة شيرمان كينت لتحليل الاستخبارات بتوحيده في [[B2C_NUM_1]]؟ | literal terminology_mismatch; both responses inspected |
| m08-chapter-1-introduction-and-overv.l05:lesson-qa:2:answer | The techniques have wide applicability to tactical military analysis, law enforcement intelligence analysis, homeland security, business consulting, the medical profession, financial planning, cyber analysis, and complex decision making in any field. | تتمتع هذه التقنيات بتطبيق واسع على التحليل العسكري التكتيكي، وتحليل الاستخبارات في إنفاذ القانون، والأمن الداخلي، والاستشارات التجارية، والمهنة الطبية، والتخطيط المالي، والتحليل الإلكتروني، واتخاذ القرارات المعقدة في أي مجال. | literal terminology_mismatch; both responses inspected |

Book1 body book-main:1:2: source FOR INTELLIGENCE ANALYSIS; all three cached responses are لأغراض تحليل الاستخبارات. Numeric, structural and completeness validation pass. Historic failure is the same exact-term glossary mismatch.

## Remediation and evidence

Validator recognizes explicitly enumerated Arabic realizations of the selected glossary target: intelligence noun/adjective, definite analysis phrase, and equivalent structured-technique phrase. No general root stemming or inferred domain substitution. Wrong intelligence senses الحكمة/الذكاء remain rejected when the selected sense is استخبارات. Preferred target stays in the model prompt; validator accepts equivalent meaning. Alternate technique phrase is equivalent but raw question grammar needs the separate Arabic correction stage.

109 focused source tests passed. Source-only until live rollout is recorded below. No inference invoked, saved outputs mutated or workflow interrupted during audit.

## Safe rollout progress

All 31 reviewed raw translations (28 appendix, 3 body) pass the corrected terminology validator with zero inference. 111 focused source tests passed, including pause propagation regressions for partial per-item checkpoint and full checkpoint. Seven broad recovery catch paths now preserve StageCheckpointPaused as control flow rather than treating it as a translation retry. Initial pause record reached PAUSED at batch38; after subsequent dispatch prevention, all four translation executions were verified with zero pending activities. HY worker was then frozen before replacement. Thin image builds copy only generation.py over existing operational HY/API images. Live publish/resume evidence follows.

## Deployed and resumed — 17:47 UTC

Initial Compose validation failed because the hy-translation profile was omitted; no container was changed by that failed command. Corrected command activates declared profiles for configuration validation and explicitly updates only hy-translation-worker and api with --no-deps. Both recreated and started on terminology-repair-20261013 images. Source and both runtime generation.py SHA256 match 19ba606a90a749595c3e02b9efd8e296b76010e834ac23bf6cd21219efb3c595.

The same four workflow IDs were signalled through /v1/workflows/{id}/resume; each returned RESUME_REQUESTED. No child workflow created. Saved ID/text equality against baseline is verified for all: body1 76/776, appendix1 113/888, body2 41/2620, appendix2 644/644. Zero suspect at verification. Body2 now STARTED; other three activities SCHEDULED. Deferred appendix translations are semantically/validator repaired and all 28 raw cached responses pass, but their durable acceptance awaits this serial worker's queue. Do not claim these 14 IDs checkpointed until that is observed.

## Final recovery evidence — 18:10 UTC

The deferred set grew to 17 while original code traversed two more batches; all 34 raw cached responses were inspected and passed corrected terminology checks. All 17 original deferred IDs now have valid durable checkpoints. Appendix1 increased from 113 to 130, then 131/888; zero failed, zero suspect, zero deferred at recovery. Two deferred units reused existing cache, while remaining missing units used new bounded HY requests due to changed subset request keys. This was not inference-free recovery. Thirteen new raw cache entries observed after 18:01:35 have zero intersection with the 113 already-saved appendix IDs. Their saved ID/text baseline remains identical.

Body1 75-versus-76 discrepancy traced to book-main:41:0: legitimate للاستخبارات was not recognized because Arabic ل + ال contracts to لل. Explicit contracted-prefix recognition plus strict wrong-sense tests were added; 112 translation/orchestration tests passed. Both live translation modules now hash 14827b4898b19e718629f7712215bf4629e7443fb229d40ecca62f56146c13ba.

The API base image also lacked the prior source-only inherited-checkpoint projection. Updated app.py reconciles stale queued child progress against validated source-scoped immutable checkpoints when no activity is STARTED; it does not replace counters of an active activity. Two queue/active regression cases plus existing status/control/HY tests pass (15 total). API image stale-progress-repair-20261013 deployed independently; live Book1 status now exposes 76/776 saved and 700 remaining.

All four same IDs are resumed with control ACTIVE. Current serial HY slot belongs to Book2 body (STARTED), 41/2620 durable, one deferred batch8. Body1 queued76/776, appendix1 queued131/888, appendix2 publication queued644/644. All original saved baseline values unchanged; no suspect units. API healthy, HY model healthy, HY worker up and polling, no Docker-paused worker.

Book2 body unit book-70c88ae9b2ba9ed6f61e813c:13:2 independently audited: source 440-character Gramsci prison anecdote contains [Whereupon the Sicilian]; both full original responses at17:50:59 and17:54:31 omit the closing bracket. Structural validator correctly rejects unclosed_target_bracket. It is deferred and traversal continues to next unit; no guessed closing bracket inserted. Existing adaptive recovery handles this original alone after traversal, and checkpoints only its full validated original result.
