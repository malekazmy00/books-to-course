# مراجعة B2C المستقلة الشاملة — 2026-10-08

المراجع: وكيل مستقل project_readiness_review. تاريخ المراجعة: 8 أكتوبر 2026، بتوقيت القاهرة. هذه لقطة من مصادر تتغير أثناء الإصلاح؛ أرقام الأسطر تخص اللقطة المقروءة وليست معرفات ثابتة. نطاقها /root/projects/book2course وخدمات Docker الموسومة b2c فقط.

## الحكم التنفيذي وحدود القرار

يمتلك المشروع أساسًا فعليًا: حزم مجال منفصلة وعقود Pydantic، تخزين artifacts بمصدر وhash، PostgreSQL، Temporal، تطبيق Android حقيقي، ومراحل استخراج واسترجاع وتوليد وترجمة وصوت. أفضل اتجاه هو تقوية هذا البناء وفصل المسؤوليات تدريجيًا. لا يوجد دليل يبرر نقل المشروع فورًا إلى microservices أو Kubernetes أو إعادة كتابته بالكامل.

المخاطر الأشد في اللقطة هي حلقة إعادة المحاولة الحية الطويلة، اختلاف العامل الجاري عن المصدر، وأخطاء استعادة checkpoints ومعنى نجاح المرحلة. لا يكفي عرض RUNNING أو COMPLETED لإثبات وجود ناتج صحيح محفوظ وقابل للاستخدام. التغييرات الأمنية الأخيرة أصلحت دمج رمز التحكم داخل APK في المصدر الحالي؛ الملاحظة السابقة أصبحت تاريخًا وليست عيبًا قائمًا في النسخة 0.3.2 المقروءة.

لا يفتح التقرير مراحل أغلقها مالك المشروع إداريًا، ولا يجعل المراجعة البشرية شرطًا لإتمام الإنشاء بعدما أجّلها المالك إلى التجريب. يميز التقرير بين قرارات المالك، وسلامة المسارات التشغيلية، وجودة المخرجات التي لم تقس بعد؛ ويقترح فحوصًا آلية الآن وتقييمًا بشريًا عند التجريب. لا يصدر شهادة امتثال أو ادعاء جودة تجارية من اختبارات الوحدة.

## المنهج ومصدر الأدلة

قرئت AGENTS.md و06-DELIVERY-PLAN وPROJECT-STATUS، واستند ترتيب العمل إلى 05-PHASES. شملت القراءة API وAndroid وorchestration وindexing وcore وCompose وأدوات التشغيل والنسخ وتقارير Phase9/10. لم تعدل المراجعة التطبيق أو الخدمات ولم توقف عاملًا، ولم تستدع نموذجًا أو تستخرج سرًا.

أدلة مباشرة: المصدر وأرقام الأسطر، وجود /mnt/wsl/b2cdata كـext4 rw، inventory خدمات b2c فقط، قيمة boolean للمصادقة الحالية true. أدلة من المنسق: نتيجة اختبار 410 passed / 3 failed، import-linter 5 kept / 1 broken، وتشخيص Temporal الجاري ومحاولاته وتطابق صور الخدمات؛ تسجل بهذه الصفة ولا تنسب إلى اختبار جديد أجراه المراجع. أدلة تاريخية: restore وتقييمات ملفات phase-N؛ لم يعاد تنفيذها.

أطر المراجعة: ISO/IEC 25010:2023 لتصنيف جودة المنتج؛ OWASP ASVS 5 لضوابط API؛ MASVS للتخزين والاتصال والمصادقة المحمولة؛ توصيات Android لفصل UI/data؛ SRE لصياغة SLO قائم على تجربة المستخدم؛ WCAG 2.2 للإتاحة؛ NIST SSDF لدورة التطوير وAI RMF لمخاطر المخرجات. لا توجد شهادة أو مطابقة كاملة لهذه الأطر. الروابط في آخر التقرير.

## سجل النتائج

معنى الأولويات: P0 فقد/كشف مؤكد كارثي أو توقف شامل يتطلب إجراء فوري؛ P1 يعطل المسار الأساسي أو الاستعادة أو يهدد النشر؛ P2 تحسين لازم للتوسع/الاعتمادية/التوزيع؛ P3 تنظيم ومتابعة. لم يثبت P0 بهذه المراجعة المحدودة، ولا يعني ذلك غيابه.

### R01 — P1: إعادة محاولات QA بلا تقدم مستقر

الحالة: دليل تشغيل من المنسق، إصلاح جارٍ. Temporal QA بلغ attempt 472 مع StartToClose timeout، واللقطة الأخيرة 11/102 scopes و45/515 سؤالًا مع 9 checkpoint objects، والترجمة بلغت 446 retries. عدد المحاولات ليس عدد طلبات مزود أو قيمة استهلاك. لا تستنتج المراجعة تكلفة منها.

الأثر: إعادة العمل وتأخير المراحل التالية، وقد تعرض واجهة التقدم قيمة أقل بعد بدء محاولة جديدة. النشاط طويل والحد في qa.py:351–354 ساعة واحدة؛ المصدر الجاري مختلف عن العامل الحي، الذي لا يحتوي save_scope_checkpoint.

الإصلاح: معالجة سبب timeout، تثبيت checkpoint لكل scope ناجح، اعتماد نسخة عامل متطابقة عبر ترحيل آمن، وإظهار attempt/last_progress/queue_wait. تصنيف أخطاء دائم/مؤقت مع backoff وحد محاولات أو circuit breaker يعطي حالة needs_action بدل دوران صامت. لا توقف المراجعة العامل؛ يقرر المنسق طريقة الإصلاح الآمنة.

القبول: إكمال scope جديد محفوظ، ثم إعادة تشغيل اختبار عامل مع استعادة العدد السابق وعدم إعادة نداء النموذج للنتائج المقبولة، وارتفاع العدد حتى اكتمال المخرج. المسؤول: orchestration/operations. يعتمد على R02/R03/R04.

### R02 — P1: loader الخاص بالأسئلة يقرأ enrichment على أنه scope

الحالة: مفحوص في المصدر. qa.py:214–229 يحمل enrichment من مجلد فرعي، بينما load_scope_checkpoints:231–245 يسرد prefix الأب بما يشمل هذا المجلد، ثم يصل إلى payload[scope_result]. وجود enrichment يجعل KeyError ممكنًا أثناء الاستئناف. list_objects_v2 يفتقر كذلك إلى pagination في هذه الدوال؛ يؤثر عندما يتجاوز عدد objects الصفحة الأولى.

الإصلاح: مسارات أنواع منفصلة أو تصفية صارمة للنوع والمفتاح، schema_version/type لكل سجل، paginator، تحقق identity، والتعامل الصريح مع سجل فاسد. لا تقبل تجاوزًا صامتًا لنتيجة غير موثوقة.

القبول: checkpoint مختلط scope+enrichment يستعيد النوعين، واختبار أكثر من صفحة listing، وفساد سجل يظهر خطأ قابلًا للتشخيص. المسؤول: orchestration. يعتمد على عقد checkpoint واحد.

### R03 — P1: اكتمال مجموعة الدروس يقارن العدد العام بالعدد المحلي

الحالة: مفحوص. pedagogy/qa_generation.py:480–490 ينشئ lesson_results لجميع الدروس الناجحة، ثم يقارن len(lesson_results) بـlen(scope.lesson_ids) لمجموعة واحدة. إذا نجحت دروس من مجموعة أخرى يفسد شرط all_children_final رغم اكتمال أبناء المجموعة.

الإصلاح: membership/حالة النتائج لكل lesson_id المطلوب داخل المجموعة؛ حساب counts محلية لا عامة.

القبول: مقرر ذو مجموعتين، نتائج سابقة من المجموعة الأخرى، كل مجموعة تستخدم أبناءها فقط ولا تستدعي توليدًا زائدًا بسبب اختلاف العد. المسؤول: pedagogy. يعتمد على هوية scope مستقرة.

### R04 — P1: خلط انتهاء workflow بنجاح مخرج المنتج

الحالة: مفحوص في Android وملاحظة من المنسق. MainActivity.java:257 يرجع عند Temporal COMPLETED؛ renderWorkflow:72–74 يعرض اكتمالًا و100% دون شرط نجاح result.status. Workflow يمكنه إنهاء التنفيذ مع مخرج domain failed/partial.

الإصلاح: عقد StageOutcome موحد يجمع lifecycle_status وoutcome_status وartifact_verified وcompleted/total. UI لا ينتقل إلا إذا outcome نجح أو policy صريحة تقبل partial؛ يحتاج result/status محفوظًا على الخادم.

القبول: workflow COMPLETED مع result.status=failed يعرض فشلًا واضحًا ولا يبدأ الترجمة/الصوت؛ لا تظهر 100% نجاح بلا artifact قابل للتحقق. المسؤول: API/Android/orchestration.

### R05 — P1: وصف بدء التشغيل لا يضم overlays المستخدمة

الحالة: مفحوص. scripts/windows/start-b2c-stack.ps1:18 يبدأ compose.yml وobservability فقط؛ speech/provider/hybrid ملفات مستقلة. إعادة إنشاء الخدمات بهذه الوصفة قد تزيل الإعدادات الإضافية أو تترك dependencies المطلوبة غير مبدوءة. وجود خدماتها الآن لا يثبت حفظ وصفها بعد reboot.

الإصلاح: manifest نشر معتمد يسجل compose files/profiles/image digests، يستهلكه بدء التشغيل والبناء والنسخ/الرجوع. يختبر readiness للمزود والصوت بدل process Up فقط.

القبول: config graph نفسه قبل/بعد إعادة تشغيل اختبارية، وعودة mount/marker والخدمات المطلوبة مع hashes إعداد متطابقة. المسؤول: operations. يعتمد على اتفاق إعداد النشر مع المنسق؛ الاختبار على خدمات B2C فقط.

### R06 — P1: التنسيق الكامل للمراحل يعتمد على Activity

الحالة: مفحوص. MainActivity waitFor ينظم تسلسل طويل داخل executor؛ onDestroy يغلقه. IDs محفوظة والخادم يستمر في المهمة الجارية، لكن انتقال السلسلة إلى التالية لا يستقل عن العميل.

الإصلاح: workflow خطة إخراج على الخادم، يحفظ الكتاب الأساسي والمساند واللغة والناتج والخيارات وإصداراتها؛ العميل يقدم الطلب ويعرض/يستأنف متابعة plan_id. لا يلزم تحويل كل جزئية إلى microservice.

القبول: يغلق APK بعد بدء OCR، يستمر المسار المطلوب حتى artifact النهائي؛ يعاد فتح APK فتستعاد الحالة الصحيحة دون رفع مكرر. المسؤول: orchestration/API/Android. يعتمد على R04 وcheckpoint موحد.

### R07 — P1 عند التوزيع العام: ملكية الكتب غير مربوطة بمستخدم

الحالة: مفحوص. المصادقة العامة الحالية true، لكن operator endpoints تتلقى course_id وتتحقق من نطاق artifact وليس ملكية المستخدم. app.py middleware:195–203 مفتاح bearer عام، list/progress/source:372–460؛ learner auth منفصلة:224–233. حيازة الرمز العام تمنح الوصول لمقررات أخرى بهذه الحدود.

الإصلاح: هوية مستخدم/جهاز مع scopes وownership، تفويض لكل resource؛ رموز قابلة للإلغاء والتدوير، سجل audit بدون محتوى حساس. يمكن إبقاء تشغيل شخصي لمستخدم واحد بوصفه نطاقًا محدودًا حتى تنفيذ ذلك.

القبول: مستخدم A لا يقرأ أو يعدل أو ينزل أو يولد على مقرر B؛ إعادة تشغيل لا تفقد session policy؛ revoked token يرفض فورًا وفق السياسة. المسؤول: security/API. يعتمد على اختيار نموذج المستخدمين.

### R08 — P2: الاستئناف عبر تغيير النموذج غير مضمون

الحالة: مفحوص. checkpoint_identity يشمل model_id، والمسار يشمل workflow_id؛ API workflow identity تشمل إعدادات ونموذج. يمنع ذلك إعادة استخدام نتيجة مكتملة تلقائيًا بعد تغيير المزود.

الإصلاح: نتيجة scope مرتبطة بـsource/outline/task/prompt version، مع model provenance منفصلة؛ reuse policy معلنة تشمل قبول النتائج السابقة أو force regenerate. لا تخلط نتائج مختلفة بلا metadata.

القبول: تبديل النموذج يكمل scopes الناقصة فقط عند اختيار reuse، ويحتفظ باسم النموذج لكل نتيجة؛ force يعيد ما حدده المستخدم. المسؤول: orchestration/pedagogy؛ يعتمد على schema version.

### R09 — P2: RAG الدلالي ليس فهرس vectors دائمًا

الحالة: مفحوص. indexing.py:105 يستدعي replace_book بلا embeddings؛ postgres_store.py:3–5,25,67–75 يوفر embedding JSONB اختياريًا بلا ANN؛ query_service.py يستخدم dense_search على المرشحين، embedding cache في العملية. لذا حفظ chunks لا يثبت حفظ embeddings ولا index vector دائمًا.

الأثر: تكرار حساب embeddings عند worker restart، وفحص مرشحين خطي عند نمو المكتبة. lexical شغال هو مسار محدد؛ لا يوصف تلقائيًا بأنه LightRAG أو ANN.

الإصلاح: تثبيت نموذج/بعد embedding وفingerprint version، embedding batches قابلة للاستئناف، pgvector أو مخزن مثبت الحاجة مع tenant filters، بناء فهرس تدريجي وإبطال cache عند تحديث المصدر. تقييم RRF/reranker على نفس qrels؛ إبقاء البديل الرسومي وفق قرار المالك وتقييمه لا اسمه.

القبول: لا إعادة embedding للكتاب unchanged بعد restart، update/delete/index-version صحيحة، وأثر السرعة والجودة على مجموعة ممثلة. المسؤول: indexing. لا يحتاج إيقاف التوليد الجاري.

### R10 — P2: اختبارات المصدر وحدود الحزم غير خضراء

الحالة: نتائج المنسق محفوظة في /tmp/b2c-review-20261008-tests.txt وimports.txt. 410 passed /3 failed بسبب FakeStore دون .s3 بعد checkpoint، و5 kept/1 broken بسبب pedagogy.lesson_enrichment -> synthesis.validation:25. هذه ليست إخفاقات مزود؛ لا تحذفها أو تسميها اجتيازًا.

الإصلاح: adapter checkpoint مع interface قابل للاختبار بدل تفاصيل S3 داخل المجال؛ mocks تمثل التخزين الحقيقي؛ نقل validation المشترك إلى طبقة مناسبة أو إعادة تصميم ملكيته مع الحفاظ على import contract. اختبارات جديدة تكشف R02/R03/R04 قبل الإصلاح وتنجح بعده.

القبول: suite المتأثرة كاملة بلا fail/skip غير مبرر، import contracts كلها kept، تحقق integration مع MinIO/Temporal للميزة المتغيرة. المسؤول: core/orchestration/pedagogy/QA.

### R11 — P2: جاهزية وتعافٍ وتشغيل طويل ناقصة

الحالة: health في app.py:277–279 تعيد ok دون dependencies. queues.py HEAVY=1 يجمع QA/translation/audio. timeout طويل دون heartbeat_timeout في QA. worker القديم يختلف عن المصدر بينما API/audio مطابقان وفق المنسق.

الإصلاح: readiness bounded، queues/worker مستقل لكل نوع مع حدود concurrency والتكلفة، heartbeat وstale detector، build provenance وrollout آمن. فصل عامل عن آخر لا يعني نشرًا موزعًا معقدًا.

القبول: فشل مزود ترجمة لا يوقف حالة API أو يحبس الصوت؛ الشاشة تميز waiting_for_worker وworking وretrying وstalled، deployment يعلن image/source hashes. المسؤول: operations/orchestration/API.

### R12 — P2: النسخ الحالية لا تستعيد workflow كاملًا

الحالة: دليل تاريخي موثق phase-10، وقراءة backup-checkpoint.py:141–154. checkpoint السابق حفظ 519 صفًا و348 object ونجح isolated restore؛ يستثني Temporal/Langfuse والmodels/secrets، ليس لقطة منسقة بين DB/object وليس off-host/جدول retention.

الإصلاح: تعريف RPO/RTO، backup manifest لقاعدة Temporal والartifacts والإعدادات والمفاتيح بطريقة منفصلة آمنة، جدولة ونسخة خارج الجهاز، اختبار workflow جاري بعد الاستعادة. تطابق عدد الصفوف قبل/بعد dump لا يثبت consistency عبر الخدمات.

القبول: restore مع artifact hashes وعلاقاتها وworkflow جاري إلى بيئة فارغة، إثبات متابعة scope محفوظ؛ حدود السرية والـRPO/RTO موثقة. المسؤول: operations. يعتمد على manifest نشر.

### R13 — P2: الإتاحة والتجربة والأمان المحمولة تحتاج دليل جهاز

الحالة: APK 0.3.2 source minSdk24 وarmeabi-v7a، URL HTTPS ثابت، لا API_TOKEN BuildConfig ولا usesCleartextTraffic؛ المنسق أثبت AndroidKeystore. هذه إصلاحات مؤكدة بالمصدر/المنسق لا برهان pentest. لا توجد E2E/golden تشغيلية داخل مجلداتها، ولا .github CI؛ tests/e2e وgolden لها .gitkeep وفق inventory.

الإصلاح: مكتبة واختيار عدة كتب وتحديد spine واحد، مصدر/لغة/ناتج واضح، تقدم موجز مع counts حقيقية أو indeterminate دون نسبة مختلقة؛ تفاصيل تقنية اختيارية. UI state/ViewModel/repository بدل Activity مسؤولة عن الشبكة والتسلسل. تجربة TalkBack/text scaling/RTL/Android7/8 وانقطاع الاتصال، download MP3/playback.

القبول: سيناريو كامل متعدد الكتب على أجهزة/محاكيات مستهدفة، لا فقد تقدم بعد kill/network loss؛ النقل مشفر والرمز غير موجود داخل package أو logs. المسؤول: Android/QA/security.

### R14 — P3: تقارير الحالة وإعادة البناء لا تعكس إصدارًا واحدًا

الحالة: المصدر 47 modified و127 untracked بحسب المنسق، status headline قديم أمام التحديثات. عدم .github ليس خطأ بحد ذاته لكنه يعني أن CI غير مثبت بهذا المسار. لا يستنتج وجود secrets من dirty files.

الإصلاح: جرد تغييرات scoped، baseline commit آمن بعد secret scan بدون نشر الأسرار، lockfiles/build metadata وSBOM/signing/rollback. ملخص حالة واحد يربط owner decisions مع evidence date وsource/image ID.

القبول: clean reproducible build من revision معلوم، route/artifact compatibility، manifest إصدار وإجراء رجوع مختبر. المسؤول: release/maintainer.

## ما يستحق الحفاظ عليه

العقود المكتوبة وفصل core/gateway/layers، hash/source/role/page provenance، dedup وstage keys، reject upload type/signature/size 200MiB، mount guards create_host_path=false، secret files على الخادم، idempotent workflow IDs، وفحص scope/hash في StageLibrary. هذه نقاط أساس مفيدة ولا تحتاج generic kernel أو rings إضافية قبل حل احتياجاتها الواقعية.

## ما لم يثبت

لم يقم المراجع بتشغيل load/device E2E أو restore أو failover أو provider call أو pentest أو قياس جودة جديد. تقارير CER/ترجمة/ASR السابقة دليل بعينتها لا تعميم لكل الكتب. لا corpus gold أو labels بشرية أثناء الإنشاء؛ تؤجل معايرة الجودة مع مراجعة المالك عند التجريب. لا يصح استنتاج أن graph أفضل أو أسوأ في جميع المجالات من corpus محدود. إدخال كتابين ناجح لا يثبت scale مكتبة كبيرة.

## ملحق الإصلاح الجاري

المنسق يعالج حلقة QA الحية أثناء إعداد التقرير. R01–R04 وصحة اختبارات المصدر نتائج قبل hotfix؛ لا تسجل resolved حتى يقدم source/image hashes واختبارًا وإثبات تقدم محفوظ واستئناف. ملفات التقرير لا توقف worker ولا ترفع image ولا تغير قرارات phases. يمكن إضافة أدلة مؤرخة هنا بعد الإصلاح مع إبقاء التشخيص السابق. المرجع المكمل: docs/proposals/2026-10-08-hardening-plan.md.

## مراجع أولية

- ISO/IEC 25010:2023: https://www.iso.org/standard/78176.html
- OWASP ASVS: https://owasp.org/projects/asvs
- OWASP MASVS: https://mas.owasp.org/MASVS/
- Android architecture: https://developer.android.com/topic/architecture/recommendations
- SRE SLO: https://sre.google/workbook/implementing-slos/
- WCAG 2.2: https://www.w3.org/TR/WCAG22/
- NIST SSDF: https://csrc.nist.gov/pubs/sp/800/218/final
- NIST AI RMF: https://www.nist.gov/itl/ai-risk-management-framework
## R15 — P1: درس من عنوان وحيد بدل محتوى تعليمي — فحص حي إضافي

الحالة: مؤكد بفحص قراءة فقط للـPostgreSQL وMinIO بواسطة إعدادات العامل داخليًا، دون عرض credentials أو إجراء model call أو كتابة DB/object. هذه النتيجة مستقلة عن إصلاح timeouts؛ تسريع التوليد وحده لا يصلح مصدرًا فارغًا.

المقرر b2c-demo والكتاب book-main. chunk_id=book-main:chunk:076d42dc224a95025bf1 يحفظ text=Preface فقط، token_count=1، block_ids=[book-main:29:0] وpages=[29]. درس m06-preface.l01 في الأربعة approved-outline artifacts المحفوظة له span start=end=book-main:29:0، بينما الدرس التالي m07-pr-ef-a-c-e.l01 يبدأ book-main:30:0 وينتهي book-main:41:0. عناوين الدرسين Preface وPR EF A C E تمثل العنوان نفسه بصيغتي عرض مختلفتين.

parsed-book المحفوظ يثبت أن محتوى المقدمة لم يفقد في الاستخراج: block29 heading بطول7، block30 heading بطول11، block31 paragraph بطول1996، block32 paragraph بطول2167، block33 paragraph بطول112، block34 paragraph بطول1370، block35 paragraph بطول1015؛ النص الموضوعي موجود في span الدرس التالي. لم يحتج المراجع قراءة الكتاب كاملًا أو إعادة OCR لإثبات هذه الحالة.

سبب الجذر: outline_from_book يجمع كل heading ويحوّل roots إلى modules وlessons دون توحيد عنوان مكرر أو gate للمحتوى الموضوعي (from_book.py:57–61,72–97). next_root قطع الدرس الأول قبل الجذر التالي المتكرر. validate_outline_coverage يقيس تغطية body على مستوى المخطط ككل، لا صلاحية كل درس (127–163)، وsystem review يتحقق continuity/pages/title لكنه لا يشترط محتوى تعليمي داخل كل span (review.py:61–77). _derive_scopes يختار chunks المطابقة للspan كما ينبغي تقنيًا ثم يخصص العدد الموحد من الأسئلة حتى لو كان chunk كلمة واحدة (qa_generation.py:250–280). لذلك هذا خطأ segmentation وصلاحية scope قبل التوليد، وليس دليل ضعف مزود أو عدم دعم OCR.

الأثر: المنسق رصد محاولة5 أسئلة من عنوان فقط، trivia/near duplicates واقتباسات غير صالحة. المراجع أثبت المصدر والحدود، ولم يعد طلب التوليد أو يقيم الأسئلة المحفوظة مستقلًا. لا يجوز احتساب أسئلة عنوان مكررة نجاحًا تعليميًا أو التراجع عن صحة evidence validator لمجرد إكمال العدد.

الإصلاح المقترح: normalize العناوين المتجاورة المتكافئة بحذر ودليل المسافة/البنية، ودمج span العنوان مع مادته الفعلية مع بقاء كل block/page provenance. تصنيف frontmatter/table-of-contents/title-only بوصف واضح، مع سياسة المستخدم للإدراج بدل اعتباره درسًا افتراضيًا. بوابة substantive-content لكل scope قبل نداء النموذج، وتخصيص عدد أسئلة بحسب facts/objectives قابلة للاستناد، لا إجبار5 أسئلة من كلمة. إعادة بناء outline/index للscopes المتأثرة بهوية جديدة؛ reuse للنتائج السليمة غير المتأثرة، لا تغيير artifacts immutable أو إخفاء provenance.

القبول: fixture حي مستنسخ من blocks29–41 لا ينتج lesson أسئلة مستقل من عنوان فقط؛ المقدمة الموضوعية محفوظة ومنسوبة، coverage تظل كاملة دون تضخم دروس، والتوليد لا يستدعى على source غير كافٍ. تحقق آلي للأسئلة وquotes/duplicates يظل صارمًا، والتقرير يميز excluded/non_instructional/needs_content عن succeeded. المسؤول: outline/pedagogy/indexing؛ يعتمد على policy محتوى المقرر وقواعد إعادة استخدام scopes. لا يتطلب إعادة فتح phase إداريًا؛ هو إصلاح جودة لمسار المنتج.