# تدقيق المرحلة 08: المنصة: Temporal والـ artifacts ومفاتيح الـ cache والـ provenance وllm_gateway والعمال والطوابير والتعافي والرصد والتشغيل وCI

- **التاريخ:** 2026-10-24
- **الفرع:** `hardening` (HEAD `ee8ea5b`)
- **النطاق:** `packages/orchestration`، `packages/core`، `packages/llm_gateway`، `packages/contracts` (`stage_key`/`ArtifactRef`)، `docker/`، `scripts/`، `Makefile`، `.github/workflows/ci.yml`.
- **المنهج:** قراءة فقط. لم أعدّل أي كود أو إعداد أو حالة git أو بيانات. القياسات تمت بسكربتات مؤقتة (في scratchpad) نُفذت داخل `b2c-worker-1` عبر `docker exec -i ... python -`. استخدمت boto3 (list/get فقط على bucket `artifacts`)، وpsycopg (SELECT فقط)، وعميل Temporal (list/describe/fetch_history فقط)، وPrometheus HTTP API (query فقط)، وClickHouse (`SELECT count` فقط، بدون قراءة محتوى). لم أرسل أي محتوى كتاب إلى خدمة خارجية.
- **مبدأ التدقيق:** حالة `COMPLETED` في Temporal، أو `cache_hit`، أو "227 passed"، لا تعني أن الناتج صحيح أو أن مصدره معروف. أثبت كتالوج أخطاء الترجمة (`outputs/translation-review-2026-10-24/TRANSLATION-ERROR-CATALOGUE.md:29`) أن 57.4% من المقاطع "المقبولة" خاطئة. السؤال هنا: هل المنصة قادرة أصلًا على تمثيل القبول، والإسناد، والتعافي، والرصد بشكل صحيح؟

---

## 1. غرض المرحلة وعقدها

### 1.1 الغرض
المنصة لا تنتج محتوى تعليميًا. وظيفتها أن تضمن لكل مرحلة محتوى (OCR → outline → indexing → QA/pedagogy → translation → speech → release) ما يلي:
1. **تنفيذ متين:** يستمر بعد سقوط worker، أو إعادة تشغيل الجهاز، أو انقطاع مزود النموذج، من غير تكرار آثار جانبية مدفوعة الثمن.
2. **artifacts غير قابلة للتغيير وقابلة لإعادة الإنتاج:** مفتاح كل artifact يحدده بالكامل ما دخل فيه: المدخلات والإعدادات والكود والنموذج والـ prompt.
3. **provenance كامل:** لأي جملة عربية أو مقطع صوتي، نعرف الصفحة المصدر، والنموذج ونسخته، والـ prompt، ومن راجعه.
4. **بوابات قبول:** لا تنتقل نتيجة إلى المرحلة التالية إلا بعد اجتياز بوابة مسجلة.
5. **وصول موحد للنماذج** عبر `llm_gateway`، مع سياسة egress وcache وميزانية وtracing.
6. **رصد وتنبيه** يكشفان العطل قبل أن يكتشفه المالك.
7. **تشغيل قابل للتكرار:** الصورة التي تعمل هي الكود الموجود في git، ومعها نسخ احتياطي واستعادة مجربة.

### 1.2 العقد كما هو موثق
- `docs/02-CONTRACTS.md` §1.1: `key = stage_key(stage, stage_version, input_shas, config, model_id, prompt_version)`. إذا كان الـ artifact موجودًا يُعاد `ArtifactRef` بدون حساب.
- `docs/02-CONTRACTS.md` §1.2: كل artifact يُسجَّل في جدول `artifacts` ومعه سجل PROV (`wasGeneratedBy`/`used`/`wasDerivedFrom`).
- `docs/02-CONTRACTS.md` §1.4: المخرج المرفوض يُعاد توليده مرتين، ثم يذهب إلى **طابور مراجعة**. القبول الصامت ممنوع.
- `docs/02-CONTRACTS.md` §2: جدول تسليم بين الطبقات له شرط قبول لكل حد (مثلًا `L7 ← L8: qe.route == auto_accept أو review.action موجود`).
- `docs/01-ARCHITECTURE.md` §2 المبادئ: 2 (الحالة في PostgreSQL والكائنات الكبيرة في object storage)، و3 (المفتاح يشمل "إصدارات الكود والنموذج والموجه")، و8 (عدم تسجيل نصوص الكتب افتراضيًا، ورصد الطوابير، وتنبيهات، ونسخ مجرّب).
- `docs/04-OPERATIONS.md` §6.2: جدول تنبيهات من 10 طبقات. §7: `pg_dump` يومي وWAL ونسخ 3-2-1 واختبار استعادة شهري.
- `docs/03-TESTING.md`: ‏T-P0-03 (durability)، وT-E2E-02 (cache hit ≥ 99% بعد kill)، وT-E2E-03 (20 جملة عربية تُتتبع إلى الصفحة 100%)، وT-OPS-01 (استعادة).
- `CLAUDE.md` القواعد 5 (LLM عبر gateway فقط)، و6 (لا محتوى كتب حقيقية لـ API خارجي)، و9 (تثبيت كل الإصدارات).

### 1.3 ما تحتاجه المراحل الأخرى من المنصة (من الكود)
- **الترجمة** تحتاج تقدمًا متينًا على مستوى المقطع، ونسبة كل مقطع إلى نموذج وprompt محددين، ومسار مراجعة بشرية (`translation.py:191-283`، و`_system/translation-overrides`).
- **QA/pedagogy** تحتاج checkpoints لكل scope، وإمكانية الإيقاف والاستئناف (`qa.py:236-378`).
- **الصوت** يحتاج أن يعرف بالضبط أي ترجمة "مكتملة" يستهلك (`audio.py:123`، ويرفض غير المكتمل).
- **الـ API/Android** يحتاجان حالة تقدم وتحكمًا (pause/resume) واستئنافًا (`controls.py`، `_system/*-progress`).
- **الإصدار (CourseRelease)** يحتاج سلسلة provenance كاملة وبوابات مجتازة. هذا غير موجود في الكود.

---

## 2. التصميم الحالي كما هو منفذ فعليًا

### 2.1 التنسيق (Temporal)
- Temporal server ‏1.32.0 وSDK ‏1.33.0. يوجد namespace واحد `default` بفترة احتفاظ **72 ساعة** (`docker/temporal/create-namespace.sh:13`، وقيست 3.0 أيام عبر `DescribeNamespace`). لا يوجد archival.
- العمال: عملية واحدة `b2c-worker` تشغّل ثلاثة Workers (light/heavy/io) بـ `ThreadPoolExecutor` مشترك (`worker.py:136-147`). يوجد كذلك audio worker منفصل (`worker.py:156-181`)، ووضع `generation` على `external-generation` (`worker.py:122-135`)، و4 عمال `hy-translation-worker-*` في overlay منفصل على طابور `hy-translation`. **عدد الطوابير الفعلي 6 أو أكثر**، بينما وثيقة Phase 0 تذكر light/heavy/io فقط (`docs/05-PHASES.md` Phase 0 مهمة 6).
- كل workflow منتج عبارة عن **activity واحدة ضخمة**: الترجمة تترجم الكتاب كله داخل activity واحدة بمهلة `start_to_close=72h` و`schedule_to_close=7d` (`translation.py:641-659`)، والـ QA بمهلة 24h (`qa.py:583-587`)، والـ MP3 بمهلة 24h (`audio.py:606`). التقدم الحقيقي يُحفظ في S3 من داخل الـ activity، وليس في تاريخ Temporal.
- إدارة إصدارات الـ workflow تتم بـ `workflow.patched` فقط (10 patches في 5 ملفات). لا يوجد Worker Versioning ولا build_id.
- الإيقاف والاستئناف: الـ activity تقرأ ملف تحكم من `_system/workflow-controls/` وترمي `StageCheckpointPaused`، ثم ينتظر الـ workflow إشارة `resume_stage` (`controls.py:23-63`).
- اختبار replay واحد (`tests/unit/orchestration/test_workflow_replay_safety.py`) يعيد تشغيل تعريفات "legacy" مكتوبة يدويًا لـ ingest وindex فقط. لا يُعاد تشغيل أي تاريخ إنتاجي حقيقي، ولا يغطي translation أو QA أو MP3.

### 2.2 مخزن الـ artifacts ومفاتيح الـ cache
- `ArtifactStore` (`core/artifacts.py`) تصميمه جيد: المسار `{course}/{stage}/{key}/`، والـ manifest يُكتب آخرًا بـ `If-None-Match: *` (`artifacts.py:97-115`)، والبصمة `content_sha256` مستقلة عن الترتيب.
- `stage_key` (`contracts/models.py:32-50`) دالة hash على JSON. لكن `stage_version` **نص يدوي** ("3"، "snapshot-2"، "import-1")، ولا يدخل فيه تلقائيًا إصدار الكود أو الـ schema أو بصمة قالب الـ prompt. هذا يخالف `docs/01` المبدأ 3.
- ثلاثة استخدامات تكسر معنى المفتاح:
  1. الترجمة تضع **بصمة الناتج** داخل مفتاح الـ cache (`translation.py:591-593`: `raw_result_sha256` و`result_sha256`). أصبح المفتاح دالة في المخرج لا في المدخل، فلم يعد idempotency key، و`get_or_compute` لم يعد يحقق "لا تُعِد الحساب".
  2. مفتاح الـ outline يتضمن `cache_bust` (`ingest.py:345-351`)، وهو باب خلفي لتجاوز الـ cache.
  3. بوابة الـ LLM تتلقى `prompt_version=f"...:workflow-{workflow_id}"` (`translation.py:550`). صار مفتاح cache الـ LLM خاصًا بكل workflow، فلا يُعاد استخدامه بين التشغيلات. كما أن المفتاح لا يشمل الـ JSON schema (`client.py:408-409`، والتعليق يعترف بذلك).
- **حالة موازية خارج العقد:** التقدم والـ checkpoints والتحكم والتعديلات البشرية وسجل الكتب كلها JSON قابلة للكتابة فوقها تحت `_system/` و`{course}/book-progress/` (`translation.py:347-427`، `qa.py:282-341`، `controls.py:9-33`، `scripts/reconcile-book-root-identity.py:12-38`). هذا يخالف `docs/01` المبدأ 2 الذي ينص على أن الحالة في PostgreSQL.

### 2.3 الـ provenance
- `ProvRecorder` (`core/prov.py`) فيه جداول PROV الأربعة وجدول `artifacts`.
- `record_activity` تستدعيها **فقط** `hello.py:56` و`ingest.py:68` و`ingest.py:328`. الترجمة والـ QA والـ indexing والـ MP3 لا تسجل أي PROV. هذا يخالف `docs/02` §1.2.
- `ArtifactStore._record` يسجل في جدول `artifacts` بشرط وجود recorder. لكن `put_stream` (`artifacts.py:170-172`) يرجع الـ artifact الموجود بدون تسجيله، وسكربتات الصيانة تنشئ `ArtifactStore` بلا recorder.
- لا يوجد provenance على مستوى الوحدة: `TranslatedSegment` لا يحتوي إلا `segment_id` و`text` (`packages/translation/.../generation.py:72-75`)، و`TranslationPack.model_id` حقل واحد للحزمة كلها (`generation.py:82`).

### 2.4 llm_gateway
- عميل OpenAI-compatible متزامن (`client.py`). فيه structured output بـ `json_schema strict`، وإعادة محاولة مرتين مع إرجاع خطأ التحقق للنموذج، وcache في Postgres لطلبات `temperature==0`، وspans بسمات `gen_ai.*`.
- منطق المزودين مكتوب **بالـ hostname** مباشرة: openrouter وkiosapi وtop-tools-ai وinceptionlabs وcodecraft (`client.py:98-104`)، مع قوائم نماذج ثابتة في الكود (`client.py:39-50`) واستثناءات لكل مزود (`client.py:329-360`).
- `capture_content=True` افتراضيًا لأي endpoint محلي (`client.py:79,96-97`). أي أن الـ prompt والناتج الكاملين (نص الكتاب) يُرسلان إلى Langfuse (`client.py:396-397,485-486`).
- كل `get`/`put` في الـ cache يفتح اتصال Postgres جديدًا (`cache.py:58-73`)، وكذلك `ProvRecorder`. العداد `usage_totals` غير محمي من التزامن، مع أن الـ gateway مشترك بين threads الـ executor.
- **مسارات تتجاوز الـ gateway:** `ingest/gemini_tables.py:45-52` (Google Gemini عبر `urllib`)، و`ingest/surya_ocr.py:16`، و`indexing/embedding_client.py:7`، و`orchestration/audio.py:266`، و`booktrans_route.py:153` (subprocess مع `OPENAI_BASE_URL`). import-linter يمنع `httpx/requests/openai/aiohttp` فقط (`pyproject.toml:151-159`)، فيمر `urllib` بلا اعتراض.

### 2.5 التشغيل (compose/ops) وCI
- الصور الخارجية مثبتة بالـ digest (جيد). أما صور المشروع فـ tags متغيرة (`b2c-worker:dev`، `b2c-api:dev`)، ومعها **صور إصلاح طبقية** تنسخ ملفًا واحدًا فوق صورة سابقة:
  - `docker/worker/Dockerfile.terminology-repair-20261013`: `FROM b2c-hy-worker:appendix-repair-20261013` ثم `COPY .../generation.py`.
  - `docker/worker/Dockerfile.recovery`: `FROM b2c-worker:dev` ثم `COPY packages/ tests/`.
  - overlays: `compose.hy-appendix-repair.yml`، و`compose.hy-validator-api-repair.yml`، و`compose.operator-ui-compat.yml`، و`compose.hy-deferred-recovery.yml`، و`compose.qa-local.yml`. الأخير مُضمَّن **افتراضيًا** في `Makefile:14`.
- سكربتات تعافٍ يدوية: `recover-lost-b2c-activities.py` يستنتج "العامل المفقود" من regex ‏`1@<hostname>` ومن أوقات تشغيل الحاويات، ثم ينفذ `ResetActivity` و`activity.fail` (`:16-24,66-71`). و`resume-qa-local.py` يبدّل النموذج ويعيد استخدام checkpoints النموذج السابق (`:140-150`). و`reconcile-book-root-identity.py` يعدّل سجل الكتاب في S3.
- CI (`.github/workflows/ci.yml`) ينفذ `make check` فقط: ruff، وimport-linter، وunit، وcontract. لا يبني الصور، ولا يشغّل integration أو replay لتواريخ حقيقية أو eval regression، ولا يفحص الثغرات.
- الرصد: OTel Collector يصدّر إلى Langfuse. Prometheus يجمع من temporal server وcadvisor وcollector. توجد **قاعدتا تنبيه فقط** (`docker/prometheus/alerts.yml`: هدف scrape متوقف، وذاكرة > 78 GiB)، ولا يوجد Alertmanager، أي أن التنبيهات لا تصل لأحد. Worker SDK لا يصدّر metrics لأنه لا يوجد `Runtime(telemetry=...)` في `worker.py`.

### 2.6 أين يختلف الكود عن الوثائق

| الوثيقة | تقول | الكود/الواقع |
|---|---|---|
| `02` §1.2 | كل artifact مسجل ومعه PROV | 83 من 227 مسجلة، وPROV موجود لـ ingest/outline/hello فقط (§3) |
| `02` §1.4 | بعد محاولتين يذهب المخرج إلى طابور مراجعة | يُرمى `InvalidLLMOutput` (non-retryable) ويفشل الـ activity. لا يوجد طابور مراجعة عام |
| `02` §1.4 | cache key = sha256(model+prompt+params) | يضاف `prompt_version`، ولا يدخل الـ schema، وفي الترجمة يدخل workflow_id |
| `01` مبدأ 3 | المفتاح يشمل إصدار الكود | `stage_version` نص يدوي، ولا git sha ولا lockfile hash |
| `01` مبدأ 2 | الحالة في PostgreSQL | الحالة في JSON قابل للكتابة تحت `_system/` في S3 |
| `01` مبدأ 8 | لا تُسجَّل نصوص الكتب افتراضيًا | `capture_content=True`. ‏Langfuse يحوي 17,269 حدثًا بمدخلات كاملة (§3) |
| `04` §6.2 | 10 طبقات تنبيه | قاعدتان، بلا Alertmanager |
| `04` §7 | pg_dump يومي، WAL، و3-2-1 | checkpoint يدوي واحد بتاريخ 2026-10-05 (`eval/reports/phase-10.md:65-73`)، ولا جدولة |
| `05` Phase 0 | طوابير light/heavy/io، و`heavy=1` يضمن ميزانية CPU | 6 طوابير أو أكثر، و4 عمال hy-translation. افتراض "heavy واحد في كل لحظة" لا يغطي الطوابير الجديدة |
| `CLAUDE.md` قاعدة 5 | LLM عبر gateway فقط | urllib إلى Gemini وSurya وembeddings، وsubprocess لـ BookTrans |
| `CLAUDE.md` قاعدة 9 | تثبيت كل صورة | صور المشروع `:dev` وصور إصلاح طبقية بلا digest ولا git sha |

---

## 3. الجودة المقاسة

كل الأرقام التالية من قياسات قراءة فقط نُفذت اليوم (2026-10-24 حوالي 13:50Z).

### 3.1 مخزن الـ artifacts
- **المجموع:** 17,800 كائنًا بحجم 165.6 MB في bucket `artifacts`.
- **96.7% من الكائنات (17,206) تقع تحت `_system/`**، أي خارج عقد الـ artifact الثابت. منها 14,217 checkpoint ترجمة (35.4 MB)، و2,574 تحت `arabic-correction`، و321 checkpoint QA، و37 progress، و30 request. هذه هي الحالة الحقيقية للمنتج، وكلها JSON قابل للكتابة فوقه وبلا manifest.
- **227 artifact لها manifest.** أعدت حساب `content_sha256` لكل ملفاتها: **0 عدم تطابق**. سلامة التخزين الثابت جيدة، وهذه نقطة يجب الحفاظ عليها.
- خلط بيئات: 29 بادئة course في نفس الـ bucket. بعضها إنتاجي (`b2c-demo`) وأغلبها تجارب (`phase5-*`، `crs_p0_*`، `crs_itest`، `gemini-table-smoke`)، ومراحل تجريبية (`phase5-raw-completions` ‏54، `hello-*` ‏21).

### 3.2 سجل الـ artifacts وPROV في Postgres
- جدول `artifacts` فيه 83 صفًا، مقابل 227 manifest في S3. **152 artifact (67%) غير مسجلة**، منها `generate-course-qa` ‏19، و`generate-mp3-track` ‏15، و`book-upload` ‏13، و`approve-outline` ‏10. وفي الاتجاه المعاكس **8 صفوف تشير إلى artifacts غير موجودة** (مثل `('c','t',...)` و`phase3-live-smoke-*`).
- PROV: ‏`prov_activity` ‏48 صفًا موزعة كالتالي: ingest-parse-pdf ‏16، build-outline ‏9، hello ‏21، outline ‏2. **صفر سجلات** لـ translate-content (20 artifact)، وgenerate-course-qa (33)، وgenerate-mp3-track (15)، وindex-approved-book (1).
- **النتيجة:** لا يمكن من قاعدة البيانات تتبع أي جملة عربية أو ملف صوتي إلى مصدره. اختبار T-E2E-03 (تتبع 20 جملة إلى الصفحة بنسبة 100%) غير قابل للتنفيذ على البيانات الحالية.

### 3.3 artifacts الترجمة: "completed" لا تعني صحيحة، والمصدر مختلط
- 20 artifact من `translate-content` كلها لـ `b2c-demo`: **13 حالتها `partial` (65%)**، و6 `completed`، وواحدة بلا حقل status (schema قديم). الـ artifacts الجزئية مخزنة كمخرجات نهائية ثابتة في نفس الـ stage، ولا يميزها في المسار إلا حقل داخل JSON.
- توزيع هوية الـ checkpoints (14,217 كائنًا):

| model | prompt_version | دفعات | مقاطع |
|---|---|---:|---:|
| deepseek/deepseek-v4-flash-0731 | phase7-translation-v4 | 8,544 | 13,092 |
| tencent/Hy-MT2-1.8B | v7-source-boundary | 2,878 | 6,513 |
| tencent/Hy-MT2-1.8B | v4 | 1,711 + 433 | 4,676 |
| deepseek | v2 / v1 | 302 / 114 | 1,208 / 468 |
| tencent/Hy-MT2-1.8B | v6-contextual-retry | 234 | 629 |
| qwen3-coder-30b | v1 | 1 | 8 |

- **5 من 37 workflow** فيها checkpoints بأكثر من هوية (model, prompt). مثال: `b2c-translate-34a38983…` فيه v4 ‏(218 دفعة) وv6 ‏(234) وv7 ‏(788).
- **قياس خلط الإسناد في أحدث artifact** (`b2c-demo/translate-content/03656fad60…`، أنشئ 2026-10-19، ويسجل `model_id=tencent/Hy-MT2-1.8B` وprompt v7). طابقت كل مقطع من `translation-raw.json` (769 مقطعًا) مع كل checkpoint له نفس `(segment_id, sha256(text))`:
  - **112 مقطعًا فقط (14.6%)** يقتصر وجود نصها على checkpoints الـ prompt المعلن (v7).
  - 611 مقطعًا (79.5%) نصها مطابق حرفيًا لنص موجود في checkpoints ‏v4 القديمة.
  - 46 مقطعًا (6.0%) نصها مطابق لنص أنتجه **deepseek** (نموذج آخر).
  - **التفسير، وهو مثبت في الكود:** الـ seeding يتجاهل `model_id` و`prompt_version` و`provider_id` و`translation_engine` عند فحص التوافق (`translation.py:471-473`). بعد ذلك تُعاد كتابة المقاطع المستأنفة كـ `batch_translations` (`generation.py:1306-1307`) في checkpoint جديد يحمل هوية التشغيل الحالي (`translation.py:396-404`، `**run_identity`). أي أن **ترجمات v4 أو deepseek تُختم بختم v7/Hy**. هذا "غسيل provenance" منهجي.
  - **حدود القياس:** تطابق النص قد يحدث مصادفة في المقاطع القصيرة جدًا (أرقام، عناوين). لذلك 79.5% حد أعلى لنسبة "موروث من v4"، و14.6% حد أدنى للمقاطع المنسوبة يقينًا إلى v7. في كل الأحوال، الحقل `model_id`/`prompt_version` المسجل في الـ artifact لا يمكن الاعتماد عليه.
- الأثر على ما قاسه كتالوج الترجمة: أي تحسين في الـ prompt (مثل v7 "source-boundary" لمعالجة التسرب) **لا يصل إلى المقاطع المحفوظة سابقًا**. أخطاء `prompt_leakage` (478 حالة في الكتالوج) تبقى في المخرجات الجديدة وتحمل اسم الـ prompt الجديد.

### 3.4 الـ MP3
- 15 artifact من `generate-mp3-track`. **لا يذكر أي منها (0/15) بصمة أي artifact ترجمة** في ملفاته (`track.json`، `source-identity.json`). مثال `2000af7ff6`: ‏`track.json` يحوي `language,kind,track_id,model="fasih-tts-v1"` فقط، و`source-identity.json` يشير إلى الـ parsed book. لا يمكن الإجابة عن سؤال "أي ترجمة نُطقت في هذا الملف؟" إلا بإعادة حساب المفاتيح.
- `model` قيمة نصية ثابتة في الكود (`audio.py:403,478`)، وليست revision أو sha للأوزان. الأوزان نفسها غير مثبتة (`docs/04-OPERATIONS.md` §5: "Fasih-TTS v1 … not pinned").

### 3.5 Temporal والعمال الآن
- workflows مرئية: 2 فقط، وكلاهما RUNNING، لأن retention ‏72h يمسح تاريخ كل workflow مغلق بعد 3 أيام.
- `b2c-translate-be560ad8…` على طابور `hy-translation`: الـ activity ‏`translate_content` في المحاولة 2، وآخر فشل `activity Heartbeat timeout`، وآخر heartbeat في 2026-10-23T18:30Z. **عدد الـ pollers على `hy-translation` = 0.** العمال الأربعة `b2c-hy-translation-worker-*` و`hy-translation-*` في حالة `Exited (127)` منذ 2026-10-23T21:38Z. السبب من `docker inspect`: `failed to fulfil mount request: open /mnt/wsl/b2cdata/build-context/text-editing: no such file or directory`، أي أن الحاويات بدأت قبل تركيب قرص البيانات، مع أن المسار موجود الآن.
- **Prometheus `api/v1/alerts` = `[]`.** لم يُطلق أي تنبيه، رغم أن طابورًا إنتاجيًا بلا عمال منذ قرابة 16 ساعة. metric ‏`temporal_num_pollers` متاحة على الخادم، لكن لا توجد قاعدة تستخدمها.
- `b2c-mp3-57f9ded6…` ينتظر إشارة استئناف منذ 2026-10-19T13:07Z، أي 5 أيام بلا تنبيه "paused أكثر من 24 ساعة".
- **الكود الذي يعمل ليس الكود الموجود في git:** `b2c-worker-1` يعمل بصورة `b2c-worker:fixes-20261012` المبنية في 2026-10-15T20:37Z. داخلها `translation.py` يحتوي **0** استدعاء لـ `workflow.patched`، ويستخدم prompt ‏`phase7-translation-v4/v5`. أما HEAD ففيه 3 patches وprompt v7. منذ بناء الصورة يوجد **81 commit**، منها `9505134 fix(translation): stop passing English prose through…` و`4fa78d3 heartbeat long activities`. الصورة بلا label بالـ git revision. معظم تاريخ git عبارة عن commits ‏"snapshot" دفعة واحدة في 2026-10-24 03:18، أي أن الكود عاش شهرًا تقريبًا خارج git.

### 3.6 الرصد والخصوصية
- Langfuse (ClickHouse `events_full`): **18,761 حدثًا، منها 17,269 بمدخلات كاملة، بإجمالي 51.8 مليون حرف** بين 2026-09-29 و2026-10-23، وبلا سياسة احتفاظ موثقة.
- OTel Collector: ‏18,116 span مستلم/مصدَّر، و**3,454 span فشل تصديرها** (`max_over_time[30d]`. احتفاظ Prometheus ‏15d، فالنافذة الفعلية ≤ 15 يومًا).
- Worker SDK metrics (latency الـ activities، فشلها، slots) غير موجودة، ولا metric باسم `b2c_*`.

### 3.7 الـ cache والـ egress
- `llm_cache`: ‏10,348 مدخلًا. منها **4,941 من `deepseek/deepseek-v4-flash-0731`** (OpenRouter، مزود بعيد) بين 2026-10-08 و2026-10-11، و4,857 من Hy-MT2، و523 من qwen3-coder-30b.
- 13 من artifacts ترجمة `b2c-demo` أنتجها deepseek. حقوق الكتاب `internal_test_only` وعنوانه "كتاب التجربة". **UNVERIFIED:** هل كان المحتوى اصطناعيًا كما تشترط D-022/القاعدة 6؟ لم أفحص النص عمدًا. المهم بنيويًا: **لا يوجد في الكود ما يربط `rights_status` أو تصنيف البيانات بقرار السماح بمزود بعيد.** الضابط الوحيد متغير بيئة `B2C_LLM_ALLOW_REMOTE` (`client.py:86-92`).

### 3.8 الاختبارات
- `pytest tests/unit/orchestration tests/unit/core tests/unit/llm_gateway tests/contract`: ‏**227 passed، 0 failed** في 22.25s. ‏`lint-imports`: ‏**6 kept، 0 broken**. اختبار الـ replay: 4 passed.
- كل هذا ناجح، ومع ذلك لم يكشف أي اختبار شيئًا مما سبق: غسيل الإسناد، وفجوة PROV بنسبة 67%، وعدم مطابقة الصورة للكود، وطابور بلا عمال، وتجاوز الـ gateway عبر urllib. هذا هو نفس نمط "gates check form" الذي وجده تدقيق الترجمة.

### 3.9 النسخ الاحتياطي
- آخر checkpoint تطبيق: 2026-10-05 (`eval/reports/phase-10.md:65-73`)، أي قبل 19 يومًا وقبل كل عمل الترجمة والـ MP3. لا جدولة، ولا WAL/PITR. قاعدة Temporal وLangfuse خارج النطاق. لا توجد نسخة off-host مشفرة.

---

## 4. المشكلات مرتبة حسب الخطورة

### حرجة (Critical)

**C1. المنصة لا تمثّل "القبول"، فحالة "completed" تعني فقط أن الشكل صحيح**
- **الدليل:** لا يوجد عقد `GateResult` ولا مفهوم ترقية (promotion) في `contracts` أو `core`. نتيجة الترجمة الجزئية تُخزن في نفس الـ stage كناتج ثابت (13 من 20 `partial`، §3.3)، وتمييزها يعتمد على حقل `status` داخل JSON يفحصه كل مستهلك بطريقته (`audio.py:123`). الـ workflow ينتهي COMPLETED مع `status: partial`. شرط `docs/02` §2 (`qe.route == auto_accept أو review.action`) غير منفذ في أي مكان. ‏`InvalidLLMOutput` يُفشل الـ activity بدل أن يوجّه إلى طابور مراجعة (`retry.py:30-33` مقابل `docs/02` §1.4).
- **الأثر على الكورس:** 57.4% من المقاطع "المقبولة" خاطئة (الكتالوج). المنصة لا تستطيع منعها من الوصول إلى الصوت والتطبيق، لأنه لا توجد حالة "قيد المراجعة" ولا "مرفوض" ولا "معتمد بشريًا" يمكن لـ MP3 أو للـ release الاعتماد عليها.
- **السبب الجذري:** الـ platform صُممت كـ "durable executor + cache"، وليس كـ "pipeline ذات بوابات وترقية". تُرك القبول لكل مرحلة، فاختصرته كل مرحلة إلى فحوص شكلية.

**C2. غسيل الـ provenance: مخرجات نماذج وprompts قديمة تُختم بهوية التشغيل الجديد**
- **الدليل:** `translation.py:471-473` (التوافق يستبعد model/prompt/engine/provider)، و`generation.py:1306-1307` (إعادة إصدار المقاطع المستأنفة كـ batch_translations)، و`translation.py:396-404` (الـ checkpoint يُختم بـ `**run_identity` الحالي). في QA: ‏`qa.py:247-249` ("enrichment_prompt_version" مجمّد عمدًا) و`allow_checkpoint_model_switch` (`qa.py:113,378`؛ `scripts/resume-qa-local.py:140-150`). القياس: 14.6% فقط من مقاطع أحدث artifact منسوبة يقينًا إلى الـ prompt المعلن، و46 مقطعًا تطابق نص deepseek (§3.3).
- **الأثر:** إصلاحات الـ prompt لا تُطبق على ما سبق ترجمته. نتائج أي تقييم تُنسب لنموذج أو prompt خاطئ، فقرارات اختيار النموذج (Phase 1/7) مبنية على بيانات ملوثة. ولا يمكن تحقيق T-E2E-03 أو أي تدقيق حقوق ("هل مر هذا النص بمزود بعيد؟").
- **السبب الجذري:** تحسين التكلفة ("لا تدفع لإعادة ترجمة مقطع محفوظ") طُبق دون نموذج بيانات للوحدة يسجل من أنتجها. الـ checkpoint عبارة عن نص فقط، بلا سجل توليد.

**C3. الكود الذي يعمل لا يمكن إعادة إنتاجه ولا يطابق git**
- **الدليل:** §3.5 (صورة 10-15 بلا patches وبـ prompt v4/v5، مقابل HEAD ‏v7 و81 commit). صور إصلاح طبقية بـ `COPY` لملف واحد (`docker/worker/Dockerfile.terminology-repair-20261013`، `Dockerfile.recovery`، `docker/api/Dockerfile.*-repair-*`)، وoverlays إصلاح في `docker/compose.*repair*.yml`، و`compose.qa-local.yml` مُضمَّن افتراضيًا (`Makefile:14`). لا يوجد OCI label بالـ revision. CI لا يبني الصور.
- **الأثر:** لا يُعرف أي كود أنتج أي artifact. نتائج "الإصلاح" المعلنة في الـ commits لم تكن تعمل في الإنتاج. الرجوع (rollback) وإعادة الإنتاج مستحيلان، وأي تدقيق جودة يقيس كودًا غير الموجود في المستودع.
- **السبب الجذري:** نمط hotfix مباشر على السيرفر بدل build → release → run منفصلة ([12-Factor: Build, release, run](https://12factor.net/build-release-run)). وغياب الـ code version من `stage_key` يخفي المشكلة، لأن الكود الجديد والقديم ينتجان نفس المفاتيح.

**C4. سجل الـ artifacts وPROV شبه غائبين في المراحل التي تهم المتعلم**
- **الدليل:** §3.2 (67% غير مسجلة، و0 PROV لـ translation/QA/MP3/index، و8 صفوف معلقة)، و§3.4 (0/15 MP3 تشير إلى الترجمة). ‏`put_stream` لا يسجل الـ artifact الموجود (`artifacts.py:170-172`).
- **الأثر:** لا يوجد تتبع من الصوت إلى الترجمة إلى الجملة الإنجليزية إلى الصفحة. لا حذف آمن لمصدر (حقوق)، ولا معرفة بما يتأثر عند اكتشاف خطأ في نموذج.
- **السبب الجذري:** الـ provenance اختياري، ويستدعيه كل activity يدويًا. لا يوجد إطار (decorator/base class) يجعله جزءًا إلزاميًا من "commit".

### عالية (High)

**H1. الحالة الحقيقية في JSON قابل للكتابة فوقه تحت `_system/` (96.7% من الكائنات)**
- **الدليل:** §3.1. الكتابة بـ `put_object` بلا شرط (`translation.py:400-404,421-425`؛ `controls.py:33-35`؛ `qa.py:284-289`). التعديلات البشرية (`_system/translation-overrides`) والمراجعات حالة لا يمكن إعادة إنتاجها، ومع ذلك بلا schema أو migrations أو transactions أو قيود فريدة.
- **الأثر:** سباقات كتابة ممكنة بين workflow وAPI. لا استعلام موحد عن الحالة. النسخ الاحتياطي لا يغطيها بشكل متسق. الـ API يعتمد على JSON حر.
- **السبب الجذري:** غياب طبقة حالة في PostgreSQL، ووضع كل شيء في S3 لأنه "يعمل".

**H2. activities أحادية ضخمة بدل fan-out، مع إعدادات heartbeat هشة**
- **الدليل:** ترجمة كتاب كامل في activity واحدة بمهلة 72h/7d (`translation.py:641-659`)، والتقدم خارج Temporal. ‏`B2C_TRANSLATION_HEARTBEAT_INTERVAL_SECONDS` يقبل حتى 300s (`translation.py:309-312`) بينما `heartbeat_timeout=2m` (`translation.py:649`)، فأي إعداد فوق 120s يقتل المحاولات السليمة. آلية الإيقاف تعتمد على رمي استثناء من داخل الـ activity (`controls.py:37-55`).
- **الأثر:** لا رؤية للتقدم على مستوى الوحدة في Temporal. فشل واحد يعيد activity بالساعات. إعادة المحاولة تعتمد على منطق دمج checkpoints يدوي معقد (هو نفسه مصدر C2). والـ retries والـ timeouts لا يمكن ضبطها لكل وحدة.
- **السبب الجذري:** استخدام Temporal كـ "مشغّل job طويل" بدل نموذج workflow-per-entity/activity-per-unit الموصى به ([Temporal docs: Workflow/Activity design، child workflows، Continue-As-New](https://docs.temporal.io/develop/python/)).

**H3. الرصد أعمى عن الأعطال الحقيقية، ويسرّب المحتوى**
- **الدليل:** طابور إنتاجي بلا pollers منذ نحو 16 ساعة دون تنبيه، وworkflow متوقف 5 أيام دون تنبيه (§3.5). قاعدتا تنبيه فقط، بلا Alertmanager. لا Worker SDK metrics. 3,454 span فُقدت. ‏17,269 حدثًا في Langfuse بنصوص كاملة (§3.6) خلافًا لـ `docs/01` مبدأ 8، ولتوصية OpenTelemetry GenAI بعدم التقاط المدخلات والمخرجات افتراضيًا ([OTel GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/)؛ تلخيص ثانوي: [TrueFoundry](https://www.truefoundry.com/blog/opentelemetry-genai-semantic-conventions)، تم الاطلاع 2026-10-24).
- **الأثر:** المالك يكتشف العطل يدويًا. نصوص الكتب، ومنها مواد قد تكون مقيدة الحقوق، مخزنة بلا سياسة احتفاظ في نظام ثانٍ.
- **السبب الجذري:** الرصد نُفذ كتثبيت أدوات (Phase 0) دون SLOs وقواعد ومستقبِل للتنبيهات.

**H4. سياسة الـ egress للنماذج البعيدة خارج الـ gateway وغير مرتبطة بالحقوق**
- **الدليل:** `gemini_tables.py:45-52` (urllib إلى Google)، و`booktrans_route.py:153` (subprocess)، و`surya_ocr.py`، و`embedding_client.py`. قاعدة import-linter تفوّت `urllib` (`pyproject.toml:157`). القرار الوحيد هو `B2C_LLM_ALLOW_REMOTE` (`client.py:86`)، ولا يرتبط بـ `rights_status`. ‏4,941 استجابة deepseek على كتاب `internal_test_only` (§3.7؛ طبيعة المحتوى UNVERIFIED).
- **الأثر:** خطر خرق القاعدة 6 وحقوق الكتب دون أثر قابل للتدقيق. لا cache ولا budget ولا tracing لمسارات BookTrans وGemini.
- **السبب الجذري:** الـ gateway "عميل HTTP" وليس "نقطة سياسة". أي مرحلة احتاجت مزودًا جديدًا تجاوزته.

**H5. مفاتيح الـ cache لا تعبّر عن هوية الحساب**
- **الدليل:** نتيجة الحساب داخل المفتاح (`translation.py:591-593`). ‏`cache_bust` (`ingest.py:345`). workflow_id داخل prompt_version (`translation.py:550`). الـ schema خارج مفتاح الـ LLM (`client.py:409`). ‏`stage_version` يدوي. نماذج الصوت بأسماء نصية (`audio.py:403`). إعدادات تُقرأ من البيئة داخل الـ activity (`audio.py:257-259`، `ingest.py:88-101`).
- **الأثر:** نفس المدخلات قد تنتج مفاتيح مختلفة (فيتكرر الدفع وتتكرر artifacts: 20 ترجمة لنفس الكتابين)، ومدخلات مختلفة (كود جديد) قد تعيد artifact قديمًا. T-E2E-02 (cache hit ≥ 99%) غير قابل للتحقيق بالترجمة.
- **السبب الجذري:** لا توجد دالة مركزية تبني الهوية تلقائيًا من (كود، lockfile، schema، قالب prompt، revision النموذج، policy البوابة).

**H6. تاريخ التنفيذ يُمحى بعد 72 ساعة**
- **الدليل:** `create-namespace.sh:13`، والقياس 3.0 أيام. لا archival. ‏`REUSE_ONLY_AFTER_FAILURE` (routers/*) يصبح بلا أثر بعد انقضاء الاحتفاظ.
- **الأثر:** لا يمكن الرجوع لتاريخ أي تشغيل أنتج artifact، ولا اختبار replay على تواريخ حقيقية، ولا تدقيق لاحق. ويمكن إعادة تشغيل workflow مكتمل بنفس المعرف بعد 3 أيام.
- **السبب الجذري:** إعداد افتراضي لم يُراجَع، وTemporal لا يُعامل كجزء من سجل التدقيق.

**H7. التعافي بعد إعادة التشغيل هش ويعتمد على سكربتات يدوية**
- **الدليل:** عمال `Exited (127)` بسبب bind mount قبل تركيب القرص (§3.5). ‏`recover-lost-b2c-activities.py` يستنتج الفقد من regex ‏`1@hostname` (`:17`) وينفذ `ResetActivity`+`fail` يدويًا. ‏`restore-model-services.sh` يعيد تشغيل حاويات فشلت. سكربتات تعدّل حالة S3 (`reconcile-book-root-identity.py:35-36`).
- **الأثر:** كل إعادة تشغيل تحتاج تدخلًا، والعمل يتوقف بصمت (§3.5).
- **السبب الجذري:** ترتيب الإقلاع غير معرَّف (القرص قبل الحاويات)، ولا توجد healthchecks تعتمد على الـ pollers، والاعتماد على `restart: unless-stopped` بدل ترتيب صريح. ومهلات heartbeat القصيرة وحدها كانت كافية لاكتشاف العامل المفقود دون سكربت.

### متوسطة (Medium)

- **M1. النسخ الاحتياطي:** يدوي، آخره قبل 19 يومًا، بلا Temporal/Langfuse ولا WAL ولا off-host مشفر (§3.9). الأثر: فقدان 14k checkpoint والمراجعات عند عطل القرص. السبب: لا جدولة ولا RPO/RTO معرّفان.
- **M2. إدارة الإصدارات وreplay:** ‏10 `workflow.patched` تتراكم، ولا Worker Versioning. Temporal يوصي حاليًا بـ Worker Versioning (Worker Deployments مع Pinned workflows) كخيار افتراضي للإنتاج، وبالـ patching لما بقي جاريًا ([Temporal: Worker Versioning](https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning)، [Python versioning](https://docs.temporal.io/develop/python/workflows/versioning)، تم الاطلاع 2026-10-24). اختبار الـ replay مكتوب يدويًا ولا يغطي translation/QA/MP3.
- **M3. تصميم الـ gateway:** تفرعات ثابتة بالـ hostname وقوائم نماذج في الكود (`client.py:39-50,98-104,329-360`). ‏`temperature` تُحذف لمزود واحد (`client.py:329-332`). اتصال Postgres جديد لكل طلب. ‏`usage_totals` غير آمن مع threads. لا حدود تزامن أو circuit breaker لكل مزود. لا hook لمدققات دلالية، فالقبول = "JSON صالح".
- **M4. خلط البيئات:** تجارب وeval وإنتاج في نفس bucket وقاعدة (§3.1). مراحل `hello-*` مسجلة في الـ worker الإنتاجي (`worker.py:138-145`).
- **M5. انتهاك قاعدة UTC:** ‏`qa.py:282,324,341` تستخدم `datetime.now().astimezone()` (توقيت محلي) خلافًا لـ `docs/02` §1.5.
- **M6. CI ضحل:** lint+unit+contract فقط (`ci.yml`). لا type-check، ولا integration مع Temporal/MinIO/PG، ولا replay، ولا build/scan للصور، ولا eval regression عند تغيير prompt أو نموذج (`docs/04` §6.3 يطلبه).
- **M7. افتراض ميزانية الموارد مكسور:** ‏`heavy=1` يضمن عدم تزامن الخدمات الثقيلة (`queues.py:3-5`)، لكن طوابير `hy-translation` (4 عمال) و`audio` و`external-generation` تعمل بالتوازي معه. **UNVERIFIED:** هل تجاوز الاستهلاك الفعلي 85 GiB؟ لم أقِس ذروة تاريخية.

### منخفضة (Low)
- **L1.** صورة MinIO بـ tag ‏`latest` (لكن مثبتة بـ digest، كما في `docs/04` §5)، وحاويات يتيمة قديمة (`b2c-phase5-*`، `serene_yalow`) تستهلك أسماء ومساحة.
- **L2.** لا search attributes (`course_id`، `book_id`، `stage`) في Temporal، فلا يمكن الاستعلام بها.
- **L3.** ‏`ensure_bucket` يبتلع أي استثناء من `head_bucket` (`artifacts.py:118-122`)، فيخفي أخطاء الصلاحيات.

---

## 5. التصميم المستهدف وفق الممارسات المعتمدة

### 5.1 المبادئ الحاكمة
1. **Build → Release → Run منفصلة** ([12-Factor](https://12factor.net/build-release-run)). لا يعمل إلا ما بناه CI من commit في git، مع digest وOCI label ‏`org.opencontainers.image.revision` ([OCI image annotations](https://github.com/opencontainers/image-spec/blob/main/annotations.md))، وإثبات بناء بمستوى SLSA Build L2 أو أعلى ([SLSA v1.0 provenance](https://slsa.dev/spec/v1.0/provenance)).
2. **Artifacts ثابتة + promotion صريح.** المخرج لا يصبح "معتمدًا" بوجوده، بل بسجل ترقية يشير إلى نتيجة بوابة. النمط معتمد في سجلات النماذج مثل MLflow Model Registry (aliases)، وفي lakeFS/DVC (refs).
3. **Provenance إلزامي بالبناء.** كل commit لـ artifact يمر عبر إطار واحد يكتب في نفس transaction: سجل الـ artifact وعلاقات PROV ([W3C PROV-O، 2013](https://www.w3.org/TR/prov-o/))، ويصدر حدث lineage متوافقًا مع [OpenLineage](https://openlineage.io/docs/) (run/job/dataset + facets).
4. **الحالة في PostgreSQL، والكائنات في S3.** الحالة القابلة للتغيير (progress، controls، reviews، overrides، promotions) في جداول لها migrations (Alembic) وقيود. ‏S3 للـ blobs الثابتة فقط.
5. **Temporal كمصدر حقيقة للتنفيذ:** workflow لكل كيان (كتاب/مرحلة)، وactivity لكل وحدة عمل صغيرة idempotent. ‏Worker Versioning. replay على تواريخ حقيقية في CI. احتفاظ يكفي للتدقيق مع archival.
6. **نقطة egress واحدة للنماذج** (LLM وembeddings وOCR-VLM وMT وTTS عبر واجهات provider) مع **سياسة بيانات**: لا مزود بعيد إلا إذا سمح تصنيف المصدر بذلك.
7. **رصد مبني على SLO** ([Google SRE Workbook: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)) مع تنبيهات تصل فعلًا، وعدم التقاط المحتوى افتراضيًا (OTel GenAI).

### 5.2 المعمارية

```
API (FastAPI) ──start/signal/update──► Temporal (namespace b2c-prod, retention ≥ 30d + archival إلى S3)
     │                                     │
     │ reads state                         ▼
     ▼                         CoursePipelineWorkflow (لكل course)
PostgreSQL (b2c schema, Alembic)           ├─ child: StageWorkflow(stage, book)   ← workflow لكل مرحلة/كتاب
  runs, units, generations,                │     ├─ plan activity  → قائمة وحدات حتمية
  artifacts, prov_*, gate_results,         │     ├─ unit activities (fan-out محدود، batch صغير، retry لكل وحدة)
  promotions, reviews, controls            │     ├─ gate activity  → GateResult artifact
                                           │     ├─ human review: wait_condition على Update/Signal
     ▲                                     │     └─ promote activity → promotions (transaction)
     │ same transaction (outbox)           └─ Continue-As-New عند تاريخ كبير
     │
stage_activity framework (core):  key = identity(...)  → exists? reuse : compute → commit(artifact+PROV+lineage)
     │
Model Gateway (llm_gateway):  provider registry + policy + prompt registry + cache + validators + OTel
     │
Providers: llama.cpp / Hy-MT / Fasih / embeddings / (بعيد فقط بإذن سياسة)
```

**تفاصيل إلزامية:**
- **الهوية (`stage_identity`)** تُحسب مركزيًا = sha256 على JSON قانوني (RFC 8785 JCS) لهذه الحقول: `stage`، و`code_digest` (sha لحزمة المرحلة + `uv.lock`)، و`contract_schema_version`، وبصمات المدخلات (ArtifactRef.sha256)، و`config` الصريح (بدون env خفي)، و`model{id, revision/sha256 للأوزان}`، و`prompt_template_sha256`، و`gate_policy_version`. ممنوع: نتيجة الحساب، وworkflow_id، و`cache_bust`.
- **الوحدة (unit)** مثل المقطع أو الدفعة أو الـ scope أو الـ track لها `unit_id` حتمي وسجل `generation`: model+revision، وprompt sha، وparams، وattempt، وgateway call id، وcache_hit، وlatency، وtokens. نتيجة الوحدة artifact صغير ثابت، أو صف في `units` يشير إلى blob.
- **إعادة الاستخدام:** checkpoint يُعاد استخدامه فقط إذا تطابقت الهوية الكاملة للوحدة. أي إعادة استخدام مقصودة عبر هويات مختلفة (مثلًا "احتفظ بترجمة v4 المعتمدة بشريًا") تتطلب **سجل reuse صريحًا** (`wasDerivedFrom` + سبب + من وافق)، وتبقى الهوية الأصلية ظاهرة. لا ختم بهوية جديدة.
- **حالات الـ artifact:** `candidate → gated_pass | gated_fail → (human_review) → approved | rejected`. المستهلك يقرأ عبر `promotions` فقط. MP3 لا يقبل إلا مدخلًا `approved`.
- **التحكم (pause/resume/cancel/review decisions):** عبر Temporal **Updates/Signals** وتُسجل في PG، بدل ملفات `_system/workflow-controls`.

### 5.3 فئات الأدوات (محايدة تجاه النماذج)
| الوظيفة | الفئة المعتمدة | ملاحظة |
|---|---|---|
| التنسيق | Temporal (قرار قائم، لا يُعاد) + Worker Versioning | لا تغيير أداة |
| الحالة | PostgreSQL + Alembic + outbox | الموجود pgvector/pg17 |
| الكائنات | S3 (MinIO) بـ conditional writes + versioning/object-lock للـ bucket الإنتاجي | MinIO يدعم Object Lock |
| Lineage | جداول PROV + أحداث OpenLineage (اختياريًا خادم Marquez لاحقًا) | |
| بوابة النماذج | gateway داخلي رفيع أو proxy من فئة OpenAI-compatible router (مثل LiteLLM) **خلف** واجهة المشروع | القرار بالقياس، لا بالاسم |
| سجل الـ prompts | قوالب في git + sha، ومزامنة اختيارية إلى Langfuse Prompt Management | المصدر هو git |
| التقييم | Langfuse datasets/experiments + eval suites في `eval/` | |
| الرصد | OTel SDK + Temporal SDK metrics (Prometheus) + Alertmanager → قناة إشعار | |
| النسخ | pgBackRest أو WAL-G (PITR) + `mc mirror`/restic مشفر off-host | |
| CI | GitHub Actions: lint، type-check (pyright/mypy)، tests، replay، compose integration، build+SBOM+Trivy، attestations | |

### 5.4 بوابات الجودة على مستوى المنصة
هذه بوابات تمنع الخطأ البنيوي. البوابات الدلالية لكل مرحلة تحددها تقارير المراحل، والمنصة تلزم بوجودها.

**حتمية (تفشل الـ commit أو الـ promotion):**
1. ممنوع commit لـ artifact بلا صفوف `prov_generated` و`prov_used`، وسجل `artifacts` في نفس الـ transaction.
2. ممنوع promotion بلا `GateResult` يشير إلى سياسة مسجلة، ومعه negative controls مجتازة في نفس التشغيل (`docs/03` §2 قاعدة 1).
3. ممنوع وحدة بلا `generation` كامل، وممنوع اختلاف هوية الوحدة عن هوية الـ artifact دون سجل reuse.
4. ممنوع تشغيل worker صورته بلا revision موجود في `main`. الـ worker يرفض البدء عند startup check.
5. ممنوع طلب إلى مزود بعيد إذا كان `data_class` للمصدر ≠ `synthetic|public_cleared_for_remote`. يُنفذ في الـ gateway، ويُدعم بعزل الشبكة: عمال بلا internet egress إلا عبر proxy مسموح.
6. ممنوع إدخال محتوى الكتب في traces إلا بـ opt-in صريح مؤقت.

**دلالية (توجّه، لا تقرر وحدها):** المنصة توفر `ValidatorPipeline` في الـ gateway وفي `stage_activity`: deterministic → QE مرجعي-حر (من فئة COMET-Kiwi/xCOMET للترجمة) → LLM-as-judge معايَر على عينة بشرية (T-TR-06) → route. ويُسجل الحكم لكل وحدة.

### 5.5 نقاط الإنسان في الحلقة (HITL)
1. **اعتماد كل promotion إلى `approved` لمراحل المحتوى** (outline، lesson، translation، audio): مراجع بشري بـ `reviewer_id`، أو سياسة نظامية مُعايرة صراحةً (كما في `OutlineApprovalProvenance`).
2. **عينة قبول إحصائية:** للدفعات الكبيرة يُستخدم أخذ عينات بالصفات ([ISO 2859-1](https://www.iso.org/standard/74706.html)) لتحديد حجم العينة البشرية لكل lot (ترجمة فصل مثلًا) وحد القبول AQL. فشل العينة يرفض الـ lot كاملًا.
3. **قرارات إعادة الاستخدام عبر هويات مختلفة** وتبديل النموذج أثناء التشغيل: تحتاج موافقة مسجلة.
4. **أي طلب egress بعيد** على بيانات غير اصطناعية: ممنوع آليًا، ولا استثناء إلا بقرار مالك مسجل في `00-DECISIONS`.

### 5.6 مقاييس التقييم وعتبات الهدف
| المقياس | التعريف | الهدف |
|---|---|---|
| PROV coverage | artifacts لها wasGeneratedBy+used ÷ كل الـ artifacts الملتزمة | **100%** |
| Registry parity | الفرق بين manifests في S3 وصفوف `artifacts` | **0** (فحص يومي آلي) |
| Unit attribution | وحدات بسجل generation كامل ÷ كل الوحدات | **100%** |
| Mixed-identity artifacts | artifacts فيها وحدات بهوية ≠ هوية الـ artifact دون سجل reuse | **0** |
| Build reproducibility | حاويات عاملة صورتها تحمل revision موجودًا في main | **100%**، و0 صور overlay |
| Idempotency | إعادة تشغيل نفس المدخلات → نفس الـ key، و`cache_hit` | **≥ 99%** (T-E2E-02)؛ مراحل حتمية: digest مطابق 100% |
| Replay safety | آخر ≥ 50 تاريخًا حقيقيًا لكل workflow type تعاد في CI | **0** non-determinism errors |
| MTTD | زمن اكتشاف طابور بلا pollers مع backlog أو workflow عالق | **≤ 10 دقائق** (يُقاس بتمرين) |
| Silent stalls | workflows في RUNNING بلا تقدم > 24h دون تنبيه | **0** |
| Alert delivery | تنبيه اختباري يصل للقناة | **100%** شهريًا |
| Content in telemetry | spans تحمل نص كتاب في الإعداد الافتراضي | **0** |
| Unauthorized egress | طلبات لمزود بعيد على مصدر غير مصرح | **0** (اختبار سلبي + سجل) |
| Backup | RPO / RTO | **RPO ≤ 15 دقيقة** (WAL) أو ≤ 24h كحد أدنى؛ **RTO ≤ 4 ساعات**؛ تمرين استعادة شهري ناجح يشمل Temporal |
| Span export loss | send_failed ÷ sent | **< 0.1%** |
| Gate integrity | promotions بلا GateResult صالح | **0** |

### 5.7 كيف تُختبر المنصة
- **Unit:** ‏`stage_identity` (تغيير كل حقل يغير المفتاح، وتغيير workflow_id أو الناتج لا يغيره). ‏`commit` ذري (فشل PG يعني عدم وجود manifest، والعكس عبر outbox). رفض reuse بهوية مختلفة.
- **Property-based (Hypothesis):** دمج الـ checkpoints لا يغير هوية أي وحدة أبدًا.
- **Negative controls للمنصة:** صورة بلا revision لا تبدأ. وحدة deepseek داخل artifact ‏Hy تُرفض. artifact بلا PROV يُرفض commit. مصدر `internal_test_only` إلى مزود بعيد يُرفض. حقن نص كتاب في span يفشل الاختبار.
- **Temporal:** ‏`WorkflowEnvironment.start_time_skipping()` لكل workflow. replay لتواريخ حقيقية مصدّرة إلى `tests/replay/histories/` (تُجمع آليًا قبل انتهاء الاحتفاظ). اختبار durability (T-P0-03) لكل workflow منتج وليس hello فقط.
- **Chaos/DR (شهري):** ‏kill -9 للعامل، وإيقاف قرص البيانات ثم تشغيله، وإعادة تشغيل الـ stack، وفقد مزود النموذج. يُقاس MTTD والاستئناف بلا تكرار دفع (cache hit ≥ 99%).
- **CI:** كل PR يمر بـ lint → type-check → unit/contract → replay → integration (compose: postgres+minio+temporal في CI) → build image + SBOM + scan → eval regression عند تغيير prompt أو نموذج (`docs/04` §6.3).

---

## 6. تحليل الفجوة وخطة الانتقال

### 6.1 ما يُحتفظ به
- `ArtifactStore` (المسار، والـ manifest الأخير، و`If-None-Match`، و`content_sha256`، و`put_stream`) مع إصلاح التسجيل. القياس أظهر 0 فساد في 227 artifact.
- Temporal كقرار، ومعه: pydantic data converter، ومعرفات workflow حتمية + `REUSE_ONLY_AFTER_FAILURE`، وheartbeat pump (`heartbeat.py`)، و`DEFAULT_RETRY` مع قائمة non-retryable.
- منطق الـ gateway الجيد: structured output، ورفض الـ endpoint البعيد بلا HTTPS/مفتاح، وتصنيف `outcome_unknown` للمهلات (`client.py:139-143,164-171`)، والميزانيات (`budget.py`).
- import-linter (مع توسيعه)، وحدود الموارد في compose، وتثبيت digests الصور الخارجية، وسكربت `backup-checkpoint.py` كأساس لتمرين الاستعادة.

### 6.2 ما يُعاد كتابته
- `translation.py` و`qa.py` و`audio.py` (الأجزاء التنسيقية): إلى StageWorkflow + unit activities + gate + promote.
- `prov.py` + `artifacts._record`: إلى إطار `stage_activity`/`commit()` بـ transaction واحد + outbox.
- `stage_key`: إلى `stage_identity` (مع إبقاء `stage_key` القديم لقراءة الـ artifacts القديمة فقط، وهذا يحتاج bump لـ `SCHEMA_VERSION` وتحديث `docs/02` حسب القاعدة 3).
- `llm_gateway`: provider registry + policy + prompt registry + validator hooks + connection pool + `capture_content=False` افتراضيًا.
- سجل الكتب `book-progress` و`_system/*`: إلى جداول PG.
- `worker.py`: عملية لكل طابور/فئة موارد، مع Worker Deployment Version، وruntime metrics، وhealth قائم على الـ pollers.

### 6.3 ما يُحذف (بعد اكتمال البديل، وبموافقة المالك على أي حذف بيانات)
- كل `docker/**/Dockerfile.*repair*` و`Dockerfile.recovery`، وoverlays ‏`compose.*repair*.yml` و`compose.qa-local.yml` و`compose.operator-ui-compat.yml` و`compose.hy-*-recovery.yml`.
- `scripts/recover-lost-b2c-activities.{py,sh}` و`scripts/resume-qa-local.py` و`scripts/reconcile-book-root-identity.py` (تحل محلها مهلات Temporal وUpdates وmigrations).
- `cache_bust`، و`allow_checkpoint_model_switch`، ومنطق `seed_translation_checkpoints` بالتوافق الجزئي، و`result_sha256` في المفتاح، وworkflow_id في prompt_version.
- `HelloPipeline` من العامل الإنتاجي (ينتقل إلى اختبارات).
- بيانات التجارب من bucket الإنتاج: **نقل لا حذف** إلى bucket ‏`b2c-eval` بعد موافقة.

### 6.4 الخطوات بالترتيب
| # | الخطوة | المخرج القابل للتحقق | يعتمد على |
|---|---|---|---|
| 0 | **تجميد الثقة:** جدول `legacy_quarantine` يعلّم كل artifacts ‏translate/QA/MP3 الحالية `unverified` (بلا حذف). وقف استهلاك "completed" كقبول في الـ API | 100% من الـ artifacts الحالية في الجدول | — |
| 1 | **Build/Release:** CI يبني صور api/worker/hy-worker/audio-worker من commit، مع OCI labels وSBOM وTrivy وattestation، ويدفعها إلى registry محلي. ‏compose يشير بالـ digest. إزالة الـ overlays | كل حاوية b2c تحمل revision في main | — |
| 2 | **ترتيب الإقلاع:** خدمات تعتمد على القرص تبدأ فقط بعد marker (systemd/automount أو healthcheck لـ "data-disk")، وhealthcheck للعامل = poller حي | 0 حاويات Exited(127) بعد 3 إعادات تشغيل متتالية | 1 |
| 3 | **الرصد الأدنى العاجل:** Temporal SDK metrics + Alertmanager + قواعد: `temporal_num_pollers==0 and backlog>0 for 5m`، وheartbeat timeouts، وworkflow فاشل، وpaused > 24h، و`otelcol_exporter_send_failed_spans` > 0 ‏(rate)، والذاكرة. ‏`capture_content=False` افتراضيًا. احتفاظ Langfuse محدد | تمرين: إيقاف عامل يولد تنبيهًا يصل خلال ≤ 10 دقائق | 1 |
| 4 | **Temporal retention ≥ 30d + archival/تصدير تواريخ إلى S3**، وجمع تواريخ replay | الاحتفاظ مقاس ≥ 30d، و≥ 50 تاريخًا مصدّرًا لكل نوع | — |
| 5 | **مخطط PG (Alembic):** runs، units، generations، artifacts (FK)، prov_*، gate_results، promotions، reviews، controls، progress، outbox. ترحيل `_system/*` و`book-progress` بالقراءة إلى الجداول، ويبقى الأصل read-only | parity 100% بين الحالة القديمة والجداول | 1 |
| 6 | **إطار `stage_activity` + `stage_identity`** (bump `SCHEMA_VERSION`، وتحديث `docs/02`). تطبيقه أولًا على ingest/outline/index (الأبسط) | PROV coverage = 100% للمراحل المنقولة | 5 |
| 7 | **إعادة كتابة الترجمة:** TranslateBookWorkflow ← child لكل فصل ← activity لكل دفعة ≤ N مقاطع. ‏generation لكل مقطع. reuse بالهوية الكاملة فقط. ‏GateResult. طابور مراجعة عبر Updates | 0 artifacts بهوية مختلطة. اختبار سلبي deepseek-in-Hy يُرفض | 6 + المرحلة 07 (البوابة الدلالية) |
| 8 | **إعادة كتابة QA وMP3** بنفس النمط. MP3 يقرأ من `promotions` فقط ويسجل الترجمة المستهلكة | 15/15 tracks جديدة تشير إلى ترجمة approved | 7 + مرحلتي pedagogy وspeech |
| 9 | **Gateway كنقطة سياسة:** provider registry (محلي/بعيد، ترخيص، احتفاظ بيانات)، و`data_class` من `rights_status`، وجعل Gemini/Surya/embeddings/BookTrans providers عبرها. توسيع import-linter ليمنع `urllib.request`/`subprocess` خارج adapters مصرّحة. عزل شبكة العمال | 0 مسارات HTTP خارج الـ gateway (فحص آلي). الاختبار السلبي لـ egress ينجح | 6 |
| 10 | **Worker Versioning** (Deployments + Pinned) والتخلص التدريجي من `workflow.patched` بعد تصريف التشغيلات القديمة | الـ patches الجديدة = 0، و10 القديمة تُزال بعد drain | 1، 4 |
| 11 | **النسخ والاستعادة:** pgBackRest/WAL-G (يشمل قاعدة Temporal) + mirror مشفر off-host للـ bucket + تمرين شهري مؤتمت | RPO/RTO مقاسان، وتمرين ناجح موثق | 5 |
| 12 | **فصل البيئات:** namespaces وbuckets وقواعد `prod`/`eval`/`test`. منع أسماء stage تجريبية في prod | 0 بادئات تجريبية في prod | 5 |
| 13 | **CI الكامل:** type-check، integration compose، replay، eval regression عند تغيير prompt أو نموذج، وحماية فرع main | كل PR يمر بالمراحل كلها | 1، 4 |

### 6.5 الاعتماديات على المراحل الأخرى
- **07 الترجمة:** تحدد السياسة الدلالية (QE، كاشف التسرب، judge معايَر). المنصة توفر الإطار والتوجيه وطابور المراجعة. الخطوة 7 لا تكتمل بدونها.
- **03 الفهرسة:** ‏`chunk_store.replace_book` يكتب حالة قابلة للتغيير في PG خارج الـ artifact (`indexing.py:111`). يجب ربطه بـ snapshot مُرقّى ومعرّف الهوية.
- **Pedagogy/QA وSpeech:** إعادة كتابة التنسيق (الخطوة 8)، وإثبات أوزان TTS بالـ sha (`docs/04` §5).
- **API/Android:** يقرآن الحالة من PG وUpdates بدل `_system/`، وتتغير نقاط التحكم.

### 6.6 المخاطر
| الخطر | التخفيف |
|---|---|
| workflows جارية أثناء الترحيل (مثل `b2c-translate-be560ad8…` و`b2c-mp3-57f9…`) | أنواع workflow جديدة بأسماء v2 على طوابير جديدة، وترك القديمة تُصرَّف أو تُلغى بقرار مالك. لا patch إضافي |
| تكلفة إعادة الترجمة بعد إلغاء الـ seeding المرن | إعادة الاستخدام مسموحة بالهوية الكاملة. الموروث يُعاد استخدامه فقط بسجل reuse صريح وبعد مروره بالبوابة الجديدة |
| ميزانية 85 GiB | Alertmanager (حوالي 64 MiB) ومكوّن backup يُحسبان في `make budget`. لا خدمات lineage ثقيلة في البداية (الجداول تكفي) |
| تعقيد زائد لفريق صغير | الإطار واحد في `core` (decorator + commit)، وكل مرحلة تكتب منطق المجال فقط |
| فقدان بيانات أثناء ترحيل `_system/` | الترحيل بالقراءة فقط مع فحص parity. الأصل يبقى read-only حتى موافقة المالك |

---

## 7. معايير القبول (متى تكون المرحلة "منتهية")

تُعتبر المنصة منتهية عندما تتحقق كل البنود التالية بأدلة في `eval/reports/platform.md`، مع أوامر ومخرجات حقيقية:

1. **Reproducibility:** ‏`docker inspect` لكل حاوية b2c عاملة يعرض `org.opencontainers.image.revision` موجودًا في `main`، وdigest يطابق ما بناه CI. **0** صور overlay أو repair في `docker/`.
2. **Provenance:** استعلام آلي يعطي PROV coverage = **100%** وregistry parity = **0 فروق** على كل الـ artifacts الجديدة. ‏T-E2E-03 ناجح: 20 جملة عربية عشوائية → generation (model+revision+prompt sha) → جملة إنجليزية → chunk → صفحة، بنسبة **100%**. وكل MP3 جديد يشير إلى ترجمة `approved`.
3. **Unit attribution:** **0** artifacts بوحدات مختلطة الهوية دون سجل reuse، مع اختبار سلبي (حقن مقطع بهوية مختلفة) يُرفض.
4. **Gates/promotion:** **0** promotions بلا `GateResult`. الـ API والـ MP3 لا يستهلكان إلا `approved`. اختبار سلبي (artifact ‏`partial` أو `gated_fail`) يُرفض عند الاستهلاك.
5. **Idempotency:** ‏T-E2E-02: ‏kill -9 أثناء ترجمة فصل ثم استئناف ⇒ cache hit ≥ **99%** وصفر طلبات نموذج مكررة لوحدات ملتزمة. تشغيل ثانٍ بنفس المدخلات ⇒ نفس المفاتيح.
6. **Temporal:** الاحتفاظ ≥ **30 يومًا** مع تصدير التواريخ. CI يعيد تشغيل ≥ 50 تاريخًا حقيقيًا لكل workflow type بـ **0** أخطاء non-determinism. Worker Versioning مفعّل.
7. **الرصد:** تمرين موثق: إيقاف عامل طابور به backlog ⇒ تنبيه يصل للقناة خلال **≤ 10 دقائق**. ‏0 workflows عالقة > 24h بلا تنبيه طوال 14 يومًا. فقد spans < **0.1%**. ‏**0** نصوص كتب في الـ traces بالإعداد الافتراضي (فحص ClickHouse بعد تشغيل كامل).
8. **Egress:** فحص آلي يثبت **0** مسارات HTTP لنماذج خارج الـ gateway. اختبار سلبي: مصدر `internal_test_only` مع مزود بعيد ⇒ رفض مسجل.
9. **Recovery:** 3 إعادات تشغيل متتالية للجهاز/الـ stack ⇒ كل العمال up وpollers > 0 خلال ≤ 15 دقيقة **بلا أي سكربت تعافٍ يدوي**.
10. **Backup/DR:** نسخ مجدول + WAL. تمرين استعادة إلى بيئة فارغة يشمل app DB وTemporal DB وbucket، بتطابق 100% للصفوف والـ hashes. ‏RPO وRTO مقاسان ضمن الهدف (§5.6). نسخة off-host مشفرة موجودة.
11. **CI:** حماية `main`. كل PR يمر بـ lint، وtype-check، وunit/contract، وreplay، وintegration، وbuild+scan، وeval regression عند تغيير prompt أو نموذج. ونتيجة آخر 10 تشغيلات خضراء.
12. **قرار المالك:** المالك يعتمد التقرير. هذه المعايير لا تُعلن "منتهية" ذاتيًا (`docs/05` القاعدة).

---

### ملحق: ما لم يُتحقق منه (UNVERIFIED)
- هل كان محتوى `b2c-demo` الذي أُرسل إلى deepseek/OpenRouter اصطناعيًا؟ (لم يُقرأ النص عمدًا.)
- ذروة استهلاك الذاكرة التاريخية مع تزامن الطوابير الجديدة (M7).
- نسبة "الموروث من v4" بدقة: التطابق النصي يعطي حدًا أعلى (79.5%) وحدًا أدنى للمنسوب يقينًا لـ v7 (14.6%). هذا مؤشر، لا حكم لكل مقطع.
- محتوى تواريخ Temporal للتشغيلات المغلقة، فقد حُذفت بسبب الاحتفاظ 72h.
- توصيات المصادر الخارجية اطّلعت عليها في 2026-10-24: صفحات Temporal Worker Versioning، وتلخيص ثانوي لـ OTel GenAI (المرجع الرسمي هو opentelemetry.io)، وOpenLineage. أما ISO 2859-1 وSLSA وW3C PROV-O و12-Factor فمعايير معروفة مستشهد بها بروابطها الرسمية دون إعادة قراءة نصها اليوم.

### المصادر
- [Temporal — Worker Versioning](https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning) · [Temporal Python — Versioning](https://docs.temporal.io/develop/python/workflows/versioning) · [Worker Versioning (Legacy, deprecated)](https://docs.temporal.io/develop/python/workflows/worker-versioning-legacy.md)
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) · [TrueFoundry summary](https://www.truefoundry.com/blog/opentelemetry-genai-semantic-conventions) · [Trae AI course page](https://learn.traeai.com/t/ai-engineering/phases/14-agent-engineering/23-otel-genai-conventions.html)
- [OpenLineage docs](https://openlineage.io/docs/) · [OpenLineage facets guide](https://openlineage.io/docs/guides/facets) · [OpenLineage examples](https://openlineage.io/docs/spec/examples)
- [W3C PROV-O (2013)](https://www.w3.org/TR/prov-o/) · [SLSA v1.0 provenance](https://slsa.dev/spec/v1.0/provenance) · [12-Factor: Build, release, run](https://12factor.net/build-release-run) · [OCI image annotations](https://github.com/opencontainers/image-spec/blob/main/annotations.md) · [Google SRE Workbook — Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/) · [ISO 2859-1](https://www.iso.org/standard/74706.html)
