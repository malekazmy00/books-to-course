# مقترح تحسين B2C والتسليم المتدرج — 2026-10-08

الحالة: مقترح تنفيذ مبني على المراجعة المستقلة، لا يغيّر قرارات المالك بإغلاق المراحل. المصدر: eval/reports/2026-10-08-comprehensive-review.md. لا يشترط تدخلًا بشريًا أثناء الإنشاء؛ التقييم البشري مؤجل لتجريب التطبيق وفق توجيه المالك. الأرقام التالية أهداف تجريبية مقترحة تحتاج baseline واعتمادًا؛ ليست معيارًا عالميًا أو قياسات منجزة.

## النتيجة المطلوبة

المستخدم يختار كتابًا أساسيًا واحدًا ومصادر مساندة، أو كتابًا للقراءة، ويحدد ناتجًا: OCR أو RAG أو أسئلة/مقرر أو ترجمة أو MP3. الخادم ينفذ المتطلبات اللازمة فقط، ويحفظ نتائج كل مرحلة ويستأنف المفقود بعد تغير الشبكة/الجهاز/العامل أو النموذج وفق policy. عند صوت الكتاب يمكن الحفاظ على النص وتقسيمه لفصول/دروس تشغيل دون تأليف، وباللغة الأصلية أو ترجمة مختارة؛ عند صوت المقرر تكون ملفات الدروس والأسئلة بإجاباتها منفصلة ومنسوبة لمصادرها.

## البناء المستهدف القابل للتوسع

نحافظ على modular backend بالحزم الحالية؛ API خفيفة للتقديم والتفويض، application services تنسق المكتبة والخطط، domain policies نقية، infrastructure adapters للتخزين وTemporal والمزودين. العقود مشتركة بإصدارات ثابتة. routes حسب books/plans/indexing/qa/translation/audio/serving؛ فصل الملف لا يبرر إعادة صياغة كل سلوك.

PlanWorkflow على الخادم ينظم المراحل ويحتفظ بالخطة والأسباب. workers مستقلة عبر queues: ingest، retrieval/index، generation، translation، audio؛ concurrency لكل مهمة وميزانية موارد ومزود. يمكنها مشاركة المضيف وCompose؛ microservices/Kubernetes تؤجل حتى تثبت حاجة نشر مستقلة أو تعدد أجهزة. لا ننقل مسؤولية الخطة إلى شاشة Android.

مخازن الحقائق: PostgreSQL للمكتبة والخطط والملكية وstage state ونتائج scopes المقبولة، S3/MinIO لartifacts الكبيرة immutable، Temporal لتاريخ التنفيذ والأنشطة؛ checkpoint يربط الاثنين مع outbox/reconciliation. لا تدّعي كتابة DB+S3 أنها transaction واحدة. manifest commit يكتب بعد data verification، reconciler يكشف orphan/pending references. embeddings وindexes تستمر عبر workers؛ cache تسريع مشتق لا حقيقة مخرج.

Android: شاشة Library ثم CourseBuilder ثم Workspace ثم Outputs. UI state منفصلة عن network/data repository؛ حالة مصونة عبر lifecycle، العميل يعمل polling محدود أو stream مع backoff، وتغيير الشاشة لا يوقف خطة الخادم. يمكن تنفيذ الفصل داخل Java الحالية تدريجيًا؛ لا يلزم تحويل Kotlin/Compose كشرط لإنجاز APK.

## عقود أساسية مطلوبة

- ProcessingPlan: plan_id، schema_version، selected_books/dominant spine، source/target language، output/content_kind، include_questions، reuse/force per stage، resolved dependencies، source/config hashes، cost/quota policy.
- StageOutcome: lifecycle (queued/running/retrying/terminal)، outcome (succeeded/partial/failed/needs_action)، completed_units/total_units/unit_kind، last_progress_at، attempt، queue_wait، artifact_refs verified. unknown_total يبقى indeterminate.
- ScopeCheckpoint: schema/type/task/source/outline/prompt hashes، scope_id، validated payload hash، model provenance/usage، committed_at. reuse policy لا تربط صلاحية الناتج باسم النموذج وحده، force واضح للمستخدم.
- LibraryBundle: source identity والrole/pages/blocks/config، validation/import report، stage fingerprints؛ لا نقبل graph/vector dump غير متوافق بصمت. استيراد OCR والراغ يعرض ما يمكن إعادة استخدامه وما يحتاج تحويلًا.
- DeploymentManifest: revision/image digest/compose files/profiles، migrations، artifact schema compatibility، secret references فقط، health/rollback commands، timestamps.

## تسلسل التنفيذ ومعايير الخروج

هذه حزم إصلاح وليست إعادة ترقيم phases أو إعادة فتحها.

### H0 — استقرار المسار الحي وحفظ النتائج

المسؤول: orchestration/QA/operations. النطاق R01–R04/R10. يصلح timeout/retry loader المختلط وgroup membership، mocks وحدود imports، ثم يثبت ترحيل العامل لمصدر معلوم دون فقد scope المقبول. يميز success/partial/failed في API/UI. retry مؤقت bounded مع jitter، permanent input/model schema non-retryable، quota needs_action بدون دفع أو تبديل صامت.

القبول: suite المتأثرة green وحدود imports kept؛ تجربة mixed checkpoints وgroups، scope جديد محفوظ ثم استئناف من ذاته، الإكمال النهائي محفوظ وAPI يقرأه. الانتهاء يتطلب نتيجة حيّة بعد hotfix؛ build وحده ليس خروجًا. يعتمد على خطة انتقال العامل التي يقودها root.

### H1 — وصف تشغيل واحد واستعادة بعد restart

المسؤول: operations. النطاق R05/R11/R12. تجميع overlays/profiles في manifest، تبني mount/start/build/readiness أدواته. حفظ source/image/version في diagnostics بدون secrets، ووصف backups/Temporal وحالة المراحل.

القبول: config graph قبل وبعد restart مماثل، mount marker سليم، API جاهزة والworkers الصحيحة متصلة، لا استعمال موارد المشروع الآخر. يبقى restart التجريبي بعد انتهاء/حماية العمل الحي، لا أمرًا مباشرًا من هذا المقترح.

### H2 — خطة الخادم والمكتبة والاستيراد

المسؤول: API/orchestration/core/Android. النطاق R06/R08. خطة idempotent تحفظ spine واحدًا وsupplements، تسلسل متطلبات stage، اللغة المختلطة تتطلب اختيارًا واضحًا بدل تخمين. إعادة استخدام artifact مشروطة source/config/manifest؛ export أي مرحلة مكتملة، import تحقق وهويّة. لا يعيد وجود RAG صالح تشغيل OCR إذا parsed provenance متاحة.

القبول: يغلق APK أثناء OCR ثم يكتمل المسار؛ كتاب عربي أصلًا يصل MP3 بلا ترجمة؛ كتاب إنجليزي يصل صوت أصلي أو عربي بحسب الاختيار؛ كتاب مع مساعد لا يضاعف عدد الدروس تلقائيًا؛ اختيار OCR-only لا يولد أسئلة. النموذج الجديد يكمل scopes الناقصة عند reuse ويحتفظ provenance لكل نتيجة.

### H3 — استرجاع دائم وموارد مستقلة

المسؤول: indexing/operations. النطاق R09/R11. checkpoint embeddings batches، version/model/dimension، إدخال vector index بعد baseline، query filters للcourse/book/role؛ RRF/rerank اختيار واضح. worker queues تمنع monopolization بين generation والترجمة والصوت؛ parallelism يقاس بحسب المزود والRAM.

القبول: restart لا يعيد embedding unchanged corpus؛ update/delete يغير النتائج، foreign scope مرفوض، الكاش قابل للإبطال؛ مقاييس ranking/latency/memory على نفس samples. فهرس دائم لا يعني تغيير اختيار LightRAG/hippo الإداري؛ تجاربهما منفصلة وخلف adapter ثابت.

### H4 — تحسين APK وإتاحة المستخدم

المسؤول: Android/QA. النطاق R04/R06/R13. اعتماد تصميم library/workspace/outputs، multi-select مصادر وتعيين spine، خيار book-as-is مقابل course، اللغة الأصلية/target، stage outcome وضوحه. Progress badge ونسبة موثقة أو عدد، سجل تفاصيل اختياري؛ زر اتصال فقط، إعداد الخادم ثابت. advanced options لا تغطي المسار الأساسي.

القبول: Android7/8 armeabi-v7a مع upload/download/playback، text scaling وTalkBack وRTL وأهداف لمس واضحة، re-open/network recovery، مكتبة وoutputs قابلة للوصول. APK build/signature/hash وDrive readback أدلة توزيع، ليست بديلًا عن device test. لا تغيير في API providers مدفوع/مجاني دون policy معلنة.

### H5 — أمان ونشر قابل لإعادة البناء

المسؤول: security/release/maintainer. النطاق R07/R12/R14. auth الافتراضي آمن، ownership/device scopes، revocation/audit/rate limits، سياسة احتفاظ وحذف وتوضيح إرسال الكتب لمزود خارجي. نسخة APK الحالية Keystore/HTTPS أساس يُختبر. CI أو runner محلي موثق، secret scan وdependency/SBOM، release signing والrollback/schema migration.

القبول: cross-user/resource negatives، رمز مُلغى مرفوض، لا سر داخل APK/repo/logs، reproducible source/image manifest، backup/restore workflow حقيقي، اختبار rollback ببيانات نسخة سابقة. التوزيع الشخصي يمكن أن يسبق multi-user publishing بوصف نطاقه الحقيقي.

## مصفوفة قياس تشغيل أولية

تُقاس بعد H0 وتعدل بأدلة قبل اعتمادها؛ ليست وعد أداء على الكتب كلها. الزمن الذي يستغرقه provider يعرض منفصلًا عن صحة النظام.

| المقياس | هدف أولي مقترح | طريقة القياس/الشريحة |
|---|---|---|
| API read/status p95 | ≤2s عند concurrency 10 | metadata/status بدون OCR؛ warm/cold وnetwork منفصلان، error rate |
| ظهور تقدم بعد إنجاز وحدة | ≤15s | artifact commit timestamp حتى عرضه بالAPK، لا heartbeat صوري |
| كشف stagnation | ≤5min بلا heartbeat أو تقدم وفق نوع النشاط | bounded controlled stall؛ حدود أطول للنموذج تقرر من baseline |
| استئناف وحدات مقبولة | 100% محفوظة في controlled fault matrix | scope hashes/counts وprovider call dedup؛ تغيير النموذج/worker/network |
| قبول المدخلات السلبية | كل الحالات المقصودة مرفوضة بلا side effects | invalid type/signature، >200MiB، source mismatch، foreign course |
| readiness | يميز تبعية غير متاحة عن liveness | Temporal/DB/S3 unavailable fault بدون shutdown إنتاجي |
| RPO | مقترح ≤15min للstate، artifact وفق cadence | backup interval + سجل checkpoint؛ يحتاج تكلفة/سعة واعتماد |
| RTO | مقترح ≤60min للخدمات الأساسية | isolated restore من manifest مع Temporal والartifacts، لا قياس dump فقط |
| availability | يبدأ القياس دون وعد نسبة | status/submit/download SLI لمدة 7 أيام، ثم يعين هدف مع owner |
| التكلفة | ledger لكل request + stage/plan، token reconciliation | actual provider usage/cache/retries؛ لا يحسب attempt كنداء |

يختبر الحمل على 1 ثم 5 ثم 10 clients، وjobs queues 1/3/5 دون ملء inference باختبارات مكررة. corpus منتخب ثابت قبل القياس: native clean، scan English، scan Arabic، mixed/table، مصدران متداخلان؛ كتاب كبير ممثل عند استقرار العينة. الزيادة تتوقف عند خطأ سلامة/ميزانية، وتوثق cold/warm/model/hardware/chosen output. لا يستنتج 99.9% من اختبار قصير.

## مصفوفة الفشل والاستعادة

| الحدث | السلوك المطلوب | دليل القبول |
|---|---|---|
| غلق APK/kill process | الخطة مستمرة بالخادم، المزامنة عند الفتح | same plan_id، counts/artifacts ثابتة |
| network loss بعد قبول upload | query idempotent ولا duplicate processing | source hash/request id ولا إعادة upload إلزامية |
| worker restart بعد scope commit | يبدأ المفقود ويحمل المقبول | checkpoints hashes ونداءات المزود قبل/بعد |
| crash بين S3 وDB | reconciliation يكمل/يعزل pending record | manifest غير مكتمل لا يعلن success |
| provider429/quota | backoff ثم needs_action، لا paid fallback صامت | request ledger وخيار المستخدم |
| provider5xx/timeout | bounded retry، heartbeat، no unbounded loop | retry count/next attempt/last progress |
| تغيير model | reuse المقبول أو force بحسب policy | mixed model provenance محفوظة دون خلط خفي |
| source/outline update | invalidate المراحل المتأثرة فقط | source/config DAG fingerprints |
| data disk unavailable | start يفشل واضحًا، لا مجلد بديل فارغ | exact mount/marker وcreate_host_path guards |
| restore كامل | source/artifacts/workflow readable ويمكن متابعة المهمة | isolated environment، hashes/reference integrity |

## قياس جودة المحتوى أثناء الإنشاء ثم التجريب

الآن: provenance exact-page/source/role، citation resolution، coverage/count invariants، حفظ numbers/terms/names، schema/QA solver/linter، MP3 decode/duration/metadata، ASR/alignment مؤشرات مساعدة، automated evaluator مع version/seed/model وتفصيل failures. لا تتحول درجات نموذج يقيّم نفسه إلى gold.

عند التجريب: عينة ثابتة متعددة الكتب واللغات يراجعها المالك أو خبراء لاحقًا؛ CER/WER مقابل نص موثق للOCR، retrieval relevance، صحة السؤال/الإجابة والمساند، حفظ المعنى/المصطلحات للترجمة، الاستماع للنطق. thresholds تخصص حسب corpus/use case وتُثبت قبل المقارنة، لا حد دقة عام اعتباطي. الحالات الحساسة وغير الواضحة تظهر flags الآن، ويمكن للمستخدم اختيار متابعة الاستخدام التجريبي وفق policy.

## خطة refactor عملية وقابلة للرجوع

1. تثبيت baseline من المصدر الحالي بعد الإصلاح، جرد dirty files scoped وsecret scan؛ عدم تشغيل scripts WorkBuddy على book2course-work أو commit شامل بلا مراجعة.
2. استخراج API routers دون تغيير signatures/paths/response schemas؛ route parity + OpenAPI contract diff + integration path.
3. استخراج application services للتخزين/plans/checkpoint، إبقاء سياسات المجال النقية؛ إصلاح dependency cycle دون إضافة kernel عام.
4. فصل Android data/session/stage presentation، إبقاء نصوص RTL والABI/minSdk؛ device regression لمسارات library/outputs.
5. كل PR/patch صغير يحمل evidence وrollback؛ migrations additive ثم backfill ثم switch، لا حذف artifacts السابقة أو cache identity دون خطة.

التوازي الآمن: Android العرض، API contracts، embedding persistence، ops manifest/security review يمكن تطويرها مستقلًا بعد تحديد العقود. H0 العامل الحي وH1 تشغيله ينسقهما owner/root لمنع تعارض نشر. لا يغير أي وكيل صورة عامل يستخدمها آخر؛ deployment owner واحد.

## سجل التسليم لكل حزمة

لكل H: requirements، source revision/image digest، tests والفشل السابق والإصلاح، live evidence artifacts hashes، provisional metrics، privacy/provider/cost policy، rollback، owner، dependencies، status implemented/deployed/verified. عبارة completed محفوظة لا تعني قبول جودة شاملة. ملف الحالة الرئيسي يبقى مسؤولية المنسق، والتقرير الحالي proposal فقط.

## مراجع

[Android architecture](https://developer.android.com/topic/architecture/recommendations)، [OWASP ASVS](https://owasp.org/projects/asvs)، [MASVS](https://mas.owasp.org/MASVS/)، [SRE SLO](https://sre.google/workbook/implementing-slos/)، [WCAG2.2](https://www.w3.org/TR/WCAG22/)، [NIST SSDF](https://csrc.nist.gov/pubs/sp/800/218/final)، [AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)، [ISO25010:2023](https://www.iso.org/standard/78176.html). استُخدمت إطارًا للتنظيم والمراجعة؛ لا ادعاء امتثال أو شهادة.