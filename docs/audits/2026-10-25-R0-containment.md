# R0 — الاحتواء: ما نُفِّذ وكيف تحقّقنا وما بقي (2026-10-25)

المرجع: `docs/audits/2026-10-24/00-RESTRUCTURE-PLAN.md` §R0. هذه الوثيقة تغطي خمسة بنود فقط من R0
(البنود 2 و3 و4 و5 و8 و10 في جدول الخطة، مرتبة هنا كما كُلِّفت). بقية بنود R0 خارج هذا العمل
(انظر «ما بقي»). **لم يُنشر شيء**: كل التغييرات في الكود والاختبارات فقط، والحاويات العاملة لم تُمس.

الفرع: `worktree-agent-ae12d15389b825616` فوق `hardening@64ad01c`.

## 1. ما نُفِّذ

| # | البند | المصدر | commit |
|---|---|---|---|
| 1 | لا إجابة في أي استجابة للمتعلم | 06 C4، 09 C4 | `a0a2ff5` |
| 2 | `approve-system` غير متاح افتراضيًا، وتطبيق Android لا يعتمد المخطط آليًا | 02 P3 | `5998b2b` |
| 3 | الصوت والتصدير لا يستهلكان ترجمة غير مقبولة صراحةً | 07 C3، 09 C2 | `02fac9f` |
| 4 | `capture_content=False` افتراضيًا في spans الـ LLM | 08 H3 | `f912243` |
| 5 | تنبيهات Prometheus الدنيا لـ Temporal | 08 H3، R0 بند 10 | `d29da62` |

### 1.1 تسريب الإجابات (`a0a2ff5`)
- كل مسارات المتعلم (`/v1/released-courses`، `/v1/courses/{id}`، `/v1/lessons/{id}`، `review-quiz`،
  `segments`، `/v1/srs/due`، `GET/POST /v1/reviews`) تُرجع الآن **إسقاطًا بقائمة سماح (allowlist)** بدل
  `model_dump()`:
  - السؤال: `qid, lesson_id, purpose, type, bloom, stem, options` فقط.
  - سؤال Q/A المؤسَّس: `qa_id, question, pages` فقط.
  - مقاطع الصوت: `sid, start_s, end_s` فقط (نص المقطع قد ينطق الإجابة).
- لا يصل للمتعلم أبدًا: `answer_index`، `answer_text`، `rationale`، `evidence_sids`،
  `option_evidence_sids`، `validation`، `answer`، `evidence_quote`، `evidence_chunk_ids`،
  `automated_checks`، `automated_evaluation`.
- مسارات المشغّل/المراجعة (QA checkpoints، تصدير النتائج) لم تتغير، وتبقى خلف الـ token.

### 1.2 الاعتماد الآلي للمخطط (`5998b2b`)
- إعداد جديد `B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL` (`Settings.allow_system_outline_approval`) قيمته
  الافتراضية `false`.
- عند التعطيل: `POST .../outline/approve-system` و`POST .../outline/approve` مع `review_type=system`
  يعيدان **409** مع `message_ar` و`message_en` واسم الإعداد. لا يُكتب شيء ولا يبدأ workflow.
- الاعتماد البشري (`/outline/approve` مع `review_type=human` و`reviewer_id`) كما هو.
- Android (`MainActivity.java`): أُزيل الاستدعاءان التلقائيان في `ensureOutlineOnly()` و`ensureRag()`.
  التطبيق يتوقف الآن برسالة عربية تطلب اعتمادًا بشريًا للمخطط.

### 1.3 الصوت والتصدير (`02fac9f`)
- **مصدر مقبول:** تجميعة الترجمة المعاد بناؤها (`translate-book`، مرحلة `ASSEMBLY_STAGE`). تُنطق فقط
  الكتل ذات `complete=true`، والكتلة المدموجة (`merged_into`) تُنطق مع مالكها. يُرفض الطلب إذا وُجدت
  كتلة غير مقبولة، أو كانت التجميعة مبنية من `ParsedBook` آخر، أو كانت لغتها لا تطابق لغة الصوت.
- **مصدر قديم:** ترجمة `translate-content` (TranslateContentWorkflow) تُرفض بـ **409** ما لم يحمل الطلب
  `allow_unverified_legacy=true`. عند السماح الصريح يُسجَّل ذلك في:
  - هوية الـ workflow (فلا يعيد استخدام تشغيل قديم غير موسوم)،
  - `GenerateMP3Input.allow_unverified_legacy`،
  - سجل التقدم `_system/audio-progress/*` (`unverified_legacy`)،
  - الـ manifest ومفتاح مرحلته،
  - كل مسار (`unverified_legacy`)، وملف `verification.json` داخل كل artifact صوتي جديد.
- **أمان Temporal replay:** كود `GenerateMP3Workflow` لم يتغير. التغيير في تحقق المدخلات في الـ API
  والـ activity فقط، والحقل الجديد له قيمة افتراضية.
- **التصدير:**
  - ZIP الصوت، والمسار الواحد، وملف الكتاب الكامل، وقائمة صوت الكورس، ومكتبة `saved-audio` كلها
    تخفي أو ترفض أي artifact صوتي ليس فيه `verification.json`. هذا يشمل **كل الصوت الموجود قبل
    R0**، إضافة إلى أي صوت موسوم `unverified_legacy`.
  - مع `allow_unverified_legacy=true` (أو `include_unverified_legacy=true` للقوائم) يُقدَّم المحتوى
    موسومًا: الترويسة `X-B2C-Verification: unverified_legacy`، وفي الـ ZIP يُضاف
    `UNVERIFIED-LEGACY.txt` وmanifest موسوم.
  - تصدير النتائج: أُضيف `translate-book`. ونتائج `translate-content` مخفية من الاكتشاف ومن التصدير
    الافتراضي، ومرفوضة في file/units/unit/export إلا بالسماح الصريح، ومعه تُوسم في `provenance.json`.

### 1.4 التقاط المحتوى في الـ traces (`f912243`)
- `LLMGateway(capture_content=...)` قيمته الافتراضية الآن `False`. النقاط البعيدة لا تلتقط أبدًا كما
  كان.
- إعداد صريح `B2C_LLM_CAPTURE_CONTENT` (`Settings.llm_capture_content`، افتراضيًا `false`). الـ worker
  يمرره للبوابة الرئيسية ولكل بوابات ملفات ترجمة `translate-book`.
- ملاحظة في `docs/04-OPERATIONS.md`.

### 1.5 التنبيهات (`d29da62`)
أسماء الـ metrics قُرئت من Prometheus الحي في 2026-10-25 (قراءة فقط عبر datasource proxy في Grafana).

- **حقيقة مهمة:** `temporal_num_pollers` موجود، لكن يصدّره فقط عمال النظام الداخليون لخادم Temporal
  (`client_name="temporal_go"`). عمال b2c (Python) لا يصدّرون SDK metrics، **فلا يمكن استخدامه لطوابير
  b2c**. استُخدمت بدلًا منه metrics الخادم.
- `B2CTaskQueueBacklogWithoutPollers` (critical، `for: 5m`): الشرط `approximate_backlog_count > 0` على
  طابور في namespace ‏`default` مع غياب أي `poll_success` أو `poll_timeouts` لمدة 5 دقائق. المعنى: لا
  عامل يستطلع الطابور، أو كل خانات العمال مشغولة.
- `B2CNoActivityProgress24h` (warning): هذا **بديل تقريبي (proxy) على مستوى الـ namespace**، وليس قياسًا
  لكل workflow على حدة. يُطلق إذا وُجدت heartbeats خلال 24 ساعة دون أي `activity_success`. لا شيء في
  هذا الـ stack يصدّر تقدم كل workflow. وقد يُطلق أيضًا لـ activity طويلة مشروعة، مثل صوت كتاب كامل.

## 2. كيف تحقّقنا (منفَّذ فعلًا)

- `make check` على الفرع، بعد آخر commit للكود:
  - ruff: `All checks passed!`
  - import-linter: `Contracts: 6 kept, 0 broken.`
  - contract: `20 passed`
  - unit: `1566 passed, 4 warnings`
  - خط الأساس قبل العمل: `1531 passed`
- **الاختبارات الجديدة** (كُتبت أولًا وفشلت قبل التنفيذ):
  - `tests/unit/api/test_learner_answer_leakage.py`: يمر على كل مسارات المتعلم ببنك أسئلة فيه قيم sentinel.
    ويفشل إذا أُضيف مسار متعلم دون أن يُفحص.
  - `tests/unit/api/test_outline_system_approval_gate.py`
  - `tests/unit/orchestration/test_audio_translation_acceptance.py`
  - `tests/unit/api/test_unverified_sources_refused.py`
  - `tests/unit/llm_gateway/test_content_capture_default.py`
  - `tests/unit/ops/test_r0_alert_rules.py`
- **التنبيهات**، عبر الصورة المثبتة `prom/prometheus:v3.15.0@sha256:efd7…` مع `docker run --rm --network none`
  (قراءة فقط):
  - `promtool check rules alerts.yml` ⇒ `SUCCESS: 4 rules found`
  - `promtool test rules alerts.test.yml` ⇒ `SUCCESS`. يغطي حالات الإطلاق وعدم الإطلاق.
  - التعبيران قُيِّما على بيانات Prometheus الحية: `status: success` ونتيجة فارغة، أي لا تنبيه الآن.
  - أظهر هذا التقييم خطأ «vector cannot contain metrics with the same labelset» في صيغة أولى، فصُحِّحت
    الصيغة وأُضيفت للاختبار سلاسل `poll_success` و`poll_timeouts` معًا.
- **تعديلات على اختبارات قائمة** (ليست إضعافًا):
  - الاختبارات التي كانت تختبر المسار القديم (اعتماد نظامي، ترجمة `translate-content` للصوت) صارت تفعّل
    السماح الصريح.
  - fixtures التصدير صارت تحمل `verification.json`.
  - قائمة التنبيهات المتوقعة في `test_phase10_monitoring_config.py` أُضيف إليها التنبيهان الجديدان.

## 3. ما لم يُنفَّذ أو لم يُتحقق منه

- **لم يُنشر شيء.** الحاويات `api` و`worker` الحالية ما زالت تعمل بالكود القديم.
  - النشر يتطلب إعادة بناء `api` والـ worker بعد انتهاء الترجمات الجارية، مع إعادة تحميل Prometheus
    ليقرأ `alerts.yml`.
  - لم يُجرَ تمرين «إيقاف عامل ⇒ تنبيه خلال ≤ 10 دقائق».
  - لم يُتحقق من أن الـ traces الجديدة خالية من نص الكتب.
- **Android:** لم يُبنَ الـ APK. تغيير Java صغير وتمت مراجعته بالقراءة فقط، ولم يُترجَم (compile).
  - لا توجد شاشة مراجعة واعتماد للمخطط داخل التطبيق.
  - طلبات الصوت والتنزيل التي يرسلها التطبيق بترجمة قديمة ستُرد بـ 409 بعد النشر.
- **أثر النشر على العمل الجاري:**
  - أي `GenerateMP3Workflow` جارٍ على ترجمة `translate-content` سيفشل تحقق مدخلاته عند إعادة محاولة
    الـ activity بعد نشر الـ worker الجديد. هذا مقصود (احتواء)، لكن الأفضل النشر بعد انتهاء هذه
    التشغيلات.
  - كل الصوت المحفوظ قبل R0 سيختفي من القوائم والتصدير الافتراضي حتى يُقبل صراحةً. هذا يشمل الصوت
    المنطوق من نص غير مترجم.
- **لا Alertmanager:** التنبيهات تظهر في Prometheus `/alerts` فقط ولا تصل لأي قناة.
- **سياسة احتفاظ Langfuse** للبيانات الموجودة لم تُحدَّد، والـ traces القديمة التي فيها نصوص باقية.
- **لا توجد ترجمة v2 لأسئلة QA.** لذلك صوت الكورس العربي من QA إنجليزي (المرفقات) ما زال يحتاج
  `allow_unverified_legacy=true`، ويُوسم بناءً على ذلك.

## 4. ما بقي من R0 (خارج هذا العمل)

| البند | الحالة |
|---|---|
| 1 | حجر الـ artifacts القديمة (`legacy_quarantine`)، يعمل عليه وكيل آخر بالتوازي. هنا استُخدم وسم `verification.json` وغيابه = غير موثَّق، دون جداول. |
| 3 | حجب المسارين `1b9186f6` و`8a61e10b` بالاسم: لم يُعالجا بالاسم، لكنهما يُحجبان ضمنًا لأنهما بلا سجل تحقق (UNVERIFIED على البيانات الحية). |
| 6 | Funnel وTailscale Serve وتدوير الـ token |
| 7 | فرع `lightrag/hipporag` |
| 9 | إعادة البناء والنشر |
| 11 | ترتيب الإقلاع بعد تركيب القرص |
| 12 | تصحيح الوثائق |
| 13 | قرار المالك Q4 |
