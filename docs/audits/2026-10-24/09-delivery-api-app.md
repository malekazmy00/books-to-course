# تدقيق المرحلة 09: التسليم (HTTP API، تطبيق Android للمشغّل، تقديم الكورس، المصادقة، التصدير)

- **التاريخ:** 2026-10-24
- **الفرع:** `hardening` (HEAD `db60cf7`). آخر commit يمس `packages/api` هو `37b92e6`. الـ commitان الأحدث (`5870d57` و`db60cf7`) يخصّان الترجمة (D-028) ولا يغيّران مسارات الـ API.
- **نوع التدقيق:** قراءة فقط. لم يُعدَّل أي كود أو إعداد أو بيانات، ولم تُغيَّر حالة git. الملف الوحيد المكتوب هو هذا التقرير.
- **الحاوية المنشورة المفحوصة:** `b2c-api-1`، صورتها `b2c-api:dev` وتاريخ بنائها `2026-10-13T00:25:44+03:00` (`docker image inspect`).
- **المنهج:** «مكتمل» و«ناجح» و«passed» ادعاءات تحتاج إثباتًا، لا حقائق. هذا هو منهج كتالوج أخطاء الترجمة نفسه (`outputs/translation-review-2026-10-24/TRANSLATION-ERROR-CATALOGUE.md`)، الذي وجد 57.4% قبولًا خاطئًا.

### كيف جُمعت الأدلة

| الأداة | ما فُحص |
|---|---|
| قراءة الكود | `packages/api/src/b2c_api/**`، و`android/operator-app/**`، و`docker/compose.yml`، و`docker/api/Dockerfile` |
| `docker exec -i b2c-worker-1 python -` | قراءات فقط: S3 (boto3، bucket `artifacts`) وPostgreSQL (psycopg) |
| `docker exec -i b2c-api-1 python -` | طلبات `GET` فقط إلى `127.0.0.1:8000`، بالـ token المركّب للقراءة. لم يُطبع الـ token، ولم يُرسل أي `POST/PUT/PATCH/DELETE` |
| الاختبارات | `.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/api` |
| مقارنة العقد | OpenAPI المصدر (`create_app(...).openapi()` محليًا) مقابل `/openapi.json` المنشور |

لم يُرسل أي محتوى من الكتب إلى خدمة خارجية.

---

## 1. غرض المرحلة وعقدها

### 1.1 الغرض

هذه المرحلة هي حدّ المنتج. تستقبل مخرجات كل المراحل السابقة: الكتاب المرفوع، والـ OCR، والمخطط المعتمد، والفهرس، والأسئلة، والترجمة، والصوت. ثم توصلها إلى طرفين:

1. **المشغّل/المؤلف:** عبر تطبيق Android وAPI تحكّم. يرفع الكتب، ويطلق المراحل ويراقبها، ويراجع ويصحح، ويصدّر.
2. **المتعلم:** عبر API تقديم وواجهة. يقرأ الدرس العربي، ويستمع إليه متزامنًا، ويحل الأسئلة، ويراجع بنظام SRS.

### 1.2 العقد المقصود (حسب الوثائق)

| البند | المصدر | ما يقوله |
|---|---|---|
| المدخل | `docs/02-CONTRACTS.md` §2 (`CourseRelease`، `AudioAsset`) | إصدار مجمّد، وحقوق `cleared`، و`AudioAsset` لكل درس فيه `segments` و`acx_pass` و`asr_cer` |
| المخرج | `docs/05-PHASES.md:142-144` | `/v1/courses`، و`/v1/lessons/{id}`، و`/audio` مع Range، و`/segments`، و`/review-quiz`، و`/reviews`، و`/srs/due`. ويضاف إليها واجهة RTL مع تزامن نص/صوت، وكويز، وSRS، وWCAG 2.2 AA |
| سؤال البوابة | `docs/06-DELIVERY-PLAN.md:69` | «هل يستطيع المتعلم إكمال الدرس والصوت والأسئلة والمراجعة على الأجهزة المستهدفة؟». الأدلة المطلوبة: E2E من كتابين حتى جهاز حقيقي، وأمان الجلسة، والإتاحة، ومصفوفة أجهزة، وتثبيت APK وتحديثه |
| صيغة الصوت | `docs/06-DELIVERY-PLAN.md:13` و`docs/03-TESTING.md:119,121` | MP3 مع بيانات وصفية وتوقيتات. T-AUD-01 ‏(44.1kHz، 192kbps CBR، ACX) **hard gate**. T-AUD-03 تزامن ±100ms |
| E2E | `docs/03-TESTING.md:127-130` | T-E2E-01..04: درسان ظاهران في الويب، واستئناف، و20 جملة تُتتبع للصفحة 100%، وaxe بلا مخالفات حرجة |
| مبادئ | `docs/01-ARCHITECTURE.md` §2 | الحالة في PostgreSQL، وAuthN/AuthZ قبل فتح الخدمة، وAPI بإصدارات مستقل عن العملاء |

### 1.3 ما يحتاجه المستهلكون

هذه آخر مرحلة في السلسلة، فالمستهلكون هم المتعلم والمشغّل والمراجع البشري. يحتاجون:

- **نسخة كورس ثابتة وقابلة للتتبع:** كل جملة عربية تُردّ إلى الصفحة المصدر.
- **حالة جودة صادقة لكل وحدة،** لا حالة تشغيل فقط.
- **وصول آمن بصلاحيات محدودة.**
- **تصدير يحمل الـ provenance وحالة المراجعة.**

---

## 2. التصميم كما هو منفّذ فعلًا

### 2.1 خادم API

**البنية:**

- FastAPI واحد (`packages/api/src/b2c_api/app.py`، 151 سطرًا).
- قُسّم في `67896a6` إلى 8 routers (`routers/*.py`) تتشارك `ApiContext` في `deps.py`.
- الحجم الكلي للحزمة 5,273 سطرًا.
- **عدد المسارات:** 65 في المصدر، و56 في الحاوية المنشورة (القسم 3.2).

**ثلاث طبقات منطقية تحت namespace واحد `/v1` وسياسة مصادقة واحدة:**

1. **تحكّم بالـ pipeline:** ‏`ingestions`، `indexings`، `course-qa`، `translations`، `audio`، `workflows`، `model-providers`.
2. **مكتبة المشغّل:** ‏`GET /v1/courses`، و`/v1/library/books`، و`memberships`، و`results`، و`saved-audio`.
3. **تقديم المتعلم:** ‏`/v1/released-courses`، و`GET /v1/courses/{id}`، و`/v1/lessons/*`، و`/v1/srs/due`، و`/v1/reviews` (`routers/serving.py`).

**المسار `/v1/courses/{course_id}` مزدوج المعنى:**

- `GET` يقرأ الإصدار المنشور للمتعلم (`routers/serving.py:105`).
- `PUT/PATCH` على المسار نفسه يعدّلان إعدادات المشغّل.

**مكان الحالة:**

- حالة المشغّل (الأسماء، العضويات، التقدم، checkpoints الصوت) ملفات JSON قابلة للتعديل داخل bucket الـ artifacts، تحت `_system/...` و`course-settings.json`.
- الحماية الوحيدة من التزامن هي `threading.RLock` داخل العملية (`course_library.py:12`).
- هذا يخالف «الحالة في PostgreSQL» (`docs/01-ARCHITECTURE.md` §2).

**المصادقة:**

- bearer token ثابت مشترك واحد (`app.py:71-80`).
- middleware يحمي كل شيء عدا `GET /health` (`app.py:87-95`).
- `B2C_API_AUTH_REQUIRED` قيمته الافتراضية `false`، في الكود (`app.py:71`) وفي compose (`compose.yml:145`). حيًا القيمة مفعّلة: طلب بلا token يعيد 401 (قيس).
- لا هوية مستخدم، ولا أدوار، ولا انتهاء صلاحية، ولا تدوير، ولا تمييز بين مشغّل ومتعلم.

**التعرّض للشبكة:**

- المنفذ مربوط بـ loopback (`compose.yml:165`).
- لكنه منشور للإنترنت العام عبر Tailscale Funnel: `https://desktop-3khe91s.tail7385b1.ts.net:8443` (`docs/04-OPERATIONS.md:141,157`).

**مسار المتعلم:**

- يقرأ `course-release/<key>/serving-manifest.json` (`serving.py:19-20`)، ثم يتحقق من النطاق والحقوق وQA وACX.
- يبث MP3 مع Range لنطاق واحد (`serving.py:151`، `routers/serving.py:154-205`).
- `learner_authenticator` يُمرَّر في الاختبارات فقط (`tests/unit/api/test_serving.py:94`). في الإنتاج قيمته `None` (`app.py:54,100`)، فيعيد `require_learner_id` الرمز 503 (`routers/serving.py:44-53`).
- تعليق الكود نفسه يقول: «no writer is provided here» (`serving.py:26`).

**التصدير:**

- `result_exports.py`: ZIP لـ artifacts خمس مراحل، مع تفكيك JSON إلى ملفات `.json/.md`.
- `audio_exports.py`: ZIP لمسارات MP3.
- `saved_audio.py`: تنزيل مسار واحد.
- لا يحتوي أي منها فحص حقوق أو حالة جودة. الـ grep عن `rights|suspect|qe|accept` لا يجد شيئًا، والمتاح فقط `allow_partial_qa` كخيار يسمح بالجزئي (`routers/audio.py:85,103`).

### 2.2 تطبيق Android للمشغّل

| البند | القيمة الفعلية | الدليل |
|---|---|---|
| الكود | `MainActivity.java`: ‏775 سطرًا، و221,021 بايت، وأطول سطر 5,094 حرفًا. ومعه 3 ملفات مساعدة: `TokenVault`، و`EndpointRouting`، و`BookIdentity` | `wc`، `awk length` |
| الواجهة | تُبنى برمجيًا بالكامل. لا يوجد `app/src/main/res` | `ls`: «No such file» |
| JSON والشبكة | 506 استخدامًا لـ `JSONObject`، و12 لـ `HttpURLConnection`، و83 لـ `runOnUiThread`، و83 `catch(Exception`، منها 28 باسم `ignored` | grep على `MainActivity.java` |
| الإتاحة | 0 `setContentDescription` | grep |
| الـ token | مشفّر بـ AES-GCM عبر AndroidKeyStore (`TokenVault`)، وهذا جيد. لكنه يُسلَّم لـ `DownloadManager` النظامي عبر `addRequestHeader("Authorization","Bearer "+token)`، والملف يُحفظ في `DIRECTORY_DOWNLOADS` العام | grep على `MainActivity.java` |
| الحقوق | الرفع يضع `rights_status = "internal_test_only"` ثابتًا في العميل | grep |
| العنوان | `build.gradle:2` يضبط افتراضيًا `https://desktop-3khe91s.tail7385b1.ts.net` (المنفذ 443، الذي تصفه `04-OPERATIONS.md:157` بأنه معطوب). `EndpointRouting.candidates` يضيف 8443 كبديل بعد فحص `/health` | `EndpointRouting.java` |
| التوافق | التطبيق يقرأ `/openapi.json` ويعطّل الأزرار غير المدعومة برسالة. مثال ذلك `serverRouteAvailable(".../materialize","post")` (`MainActivity.java:315`). هذا تخفيف جيد، لكنه بديل عن عقد مُصدَّر بإصدارات لا عقد فعلي | الكود |
| البناء | `minSdk 24`، و`targetSdk 35`، و`versionCode 28`، و`versionName 0.5.11`. ‏ABI ‏`armeabi-v7a` فقط (`splits`). لا `release buildType`، ولا R8، ولا `signingConfig`. توجد مكتبة native ‏(`app/src/main/cpp`) | `app/build.gradle` |
| الاختبارات | instrumentation يدوية. CI (`.github/workflows`) لا يبني Android | grep `gradle\|android` في workflows: لا نتيجة |

### 2.3 الويب

`web/` لا يحتوي إلا `.gitkeep`. **لا توجد واجهة متعلم**، فلا RTL ولا تزامن نص/صوت ولا WCAG.

### 2.4 حيث تختلف الوثائق عن الكود أو المنشور

| الوثيقة | الواقع |
|---|---|
| `02-CONTRACTS.md` §1.2: كل artifact يُسجَّل في جدول `artifacts` | في S3 يوجد **227** ملف `_manifest.json`، وفي جدول `artifacts` **83** صفًا فقط. ‏0 من صفوف `generate-mp3-track` (15) و`approve-outline` (10) و`book-upload` (13). والاتجاه المعاكس أيضًا: الجدول فيه 16 صف `ingest-parse-pdf` مقابل 12 manifest في S3، وصفّان بمرحلة اسمها `t` |
| `05-PHASES.md:143`: كتالوج المتعلم على `/v1/courses` | `GET /v1/courses` هو مكتبة المشغّل. كتالوج المتعلم صار `/v1/released-courses` (`routers/serving.py:86`)، ويعيد **404 حيًا** لأنه غير منشور |
| `03-TESTING.md:119` T-AUD-01: ‏44.1kHz و192kbps | كل المسارات 24kHz mono و96kbps (القسم 3.3) |
| `android/operator-app/README.md:5,7`: «no operator authentication»، و«Translation and Fasih TTS/MP3 are not available»، و«default API URL is http://10.0.2.2:8100» | الثلاثة قديمة: المصادقة مفعّلة، والترجمة والصوت مسارات حية، والعنوان الافتراضي HTTPS عام، و`EndpointRouting.normalize` يرفض `http` |
| سجل commits يوحي بأن hardening الـ API مدموج (`67896a6`..`37b92e6`) | الحاوية الحية تعمل بصورة 2026-10-13. لا يوجد فيها `result_exports.py` ولا `audio_exports.py`، وتنقصها 9 مسارات. **إصلاحات hardening غير منشورة** |
| `02-CONTRACTS.md` §2: ‏`AudioAsset` فيه `segments` و`acx_pass` و`asr_cer` | `generate-mp3-track` تحتوي فقط `track.mp3`، و`track.txt`، و`track.json` (المفاتيح `kind/language/model/track_id`، وأحيانًا `include_summaries`)، و`source-identity.json`. لا يوجد أي `AudioAsset` |

---

## 3. الجودة المقاسة

### 3.1 الاختبارات

- **الأمر:** `.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/api`
- **النتيجة:** **197 passed**، و0 failed، و0 skipped، وتحذير واحد، في 37.3 ثانية.

**ما لا تغطيه:**

- كل الاختبارات unit مع fakes. لا يوجد اختبار عقد بين Android والخادم.
- لا يوجد OpenAPI snapshot، ولا اختبار على الصورة المنشورة.
- `tests/e2e/` فارغ، فلا يوجد أي من T-E2E-01..04.
- اختبار إخفاء الإجابات يغطي `review-quiz` فقط (`test_serving.py:70-77`)، ولا يغطي `GET /v1/courses/{id}` ولا `GET /v1/lessons/{id}`.

**الخلاصة:** «197 passed» لا يقول شيئًا عن صحة ما يصل للمستخدم.

### 3.2 تطابق العقد بين المصدر والمنشور والعميل

**الطريقة:** `create_app(settings=Settings()).openapi()` محليًا، ومقارنته بـ `/openapi.json` الحي.

**النتيجة:** 65 مسارًا في المصدر و56 في المنشور. ‏0 مسارات في المنشور دون المصدر، و**9 مسارات في المصدر غائبة عن المنشور:**

- `/v1/released-courses`
- `/v1/courses/{course_id}/results`
- `/v1/courses/{course_id}/results/export`
- `/v1/courses/{course_id}/results/{result_id}/file`
- `/v1/courses/{course_id}/results/{result_id}/units`
- `/v1/courses/{course_id}/results/{result_id}/units/{unit_id}/file`
- `/v1/courses/{course_id}/audio/zip`
- `/v1/courses/{course_id}/audio/regenerate`
- `/v1/audio/{workflow_id}/request`

**الحالة الحية (GET):**

| المسار | الرمز |
|---|---|
| `/v1/released-courses` | 404 |
| `/v1/courses/b2c-demo/audio` | 405 |
| `/v1/srs/due?course_id=b2c-demo` | **503** «learner authentication is not configured» |
| `/v1/lessons/x?course_id=b2c-demo` | 404 «released course not found» |
| `/v1/translations/b2c-translate-eb7b4c82…` | **503** «Temporal is unavailable»، مع أن `b2c-temporal-1` حالته healthy |
| `/v1/translations/b2c-translate-79d7295a…` | **503** «Temporal is unavailable» |

- **مسار `/materialize`:** يستدعيه APK (`MainActivity.java:303,315`) وهو غير موجود لا في المصدر ولا في المنشور. التطبيق يكتشف غيابه ويعرض رسالة، فلا ينهار، لكن الميزة «ربط كتاب بكورس آخر» لا تعمل.
- **تحفّظ:** لم يُشغَّل APK على جهاز. سلوك الأزرار مستنتج من الكود والـ OpenAPI الحي (UNVERIFIED على الجهاز).

### 3.3 جودة ما يُسلَّم فعلًا

| القياس | النتيجة | طريقة القياس |
|---|---|---|
| إصدارات كورس (`course-release`) | **0** من 17,800 كائن. ولا يوجد كاتب لها في الكود | جرد S3 كامل + grep |
| مسار المتعلم | لا يعمل: 404 و503 (القسم 3.2) | GET حي |
| بنك الأسئلة المتاح للـ quiz وSRS | `lesson_questions()` يقرأ `qa.lesson_assets` فقط (`routers/serving.py:74-84`). النتيجة: **0 من 33** ملف `course-qa.json` فيه `lesson_assets` غير فارغ | قراءة كل `course-qa.json` |
| لغة الأسئلة | **2,246** سؤالًا في 33 artifact، و**0** منها غالبية أحرفه عربية (100% إنجليزي). التوزيع: 4 artifacts لكورس `b2c-demo`، و29 لكورس تجريبي `phase5-live-tidx01-20261004`. الحالات: 12 `final`، و19 `failed`، و2 `paused` | عدّ الأحرف العربية مقابل اللاتينية في كل حقل `question` |
| تسرّب الإجابات | `GET /v1/courses/{id}` يعيد `course.qa.model_dump()` كاملًا (`routers/serving.py:110-113`). و`GET /v1/lessons/{id}` يعيد `lesson_assets` و`grounded_questions` كاملة (`:120-126`). كلاهما يشمل `answer_index` و`rationale` | قراءة الكود. لا يمكن التحقق حيًا لعدم وجود release |
| نص الدرس | استجابة `/v1/lessons/{id}` تحوي `lesson` من المخطط (عنوان وبيانات وصفية) والأسئلة. **لا تحوي نص درس عربيًا** | `routers/serving.py:116-127` |
| مسارات MP3 | 15 مسارًا لكورس `b2c-demo`، مجموعها 68.7 دقيقة | تحليل frame header يدويًا (لا يوجد ffprobe في الحاويات) |
| ترميز MP3 | **15 من 15** بمواصفات 24,000Hz وmono و96kbps (MPEG-2 L3). **0 من 15** يطابق T-AUD-01 | نفس التحليل |
| بيانات MP3 الوصفية | ID3 فيه إطار `TSSE` فقط في 15 من 15: لا عنوان، ولا لغة، ولا ترخيص. لا يوجد `segments` ولا توقيتات، فـ T-AUD-03 **غير قابل للاختبار** | فحص ID3 و`track.json` |
| مسار بكتاب ولغة خطأ | `1b9186f6` (`track_id = m01-structured-analytic-techniques.l01-source-text`، و`language: ar`): 0 حرف عربي و201 حرف لاتيني (100%). جملة منه تطابق ترجمة كتاب غرامشي `2bd43b6d` (2,620 مقطعًا)، **أي محتوى من كتاب آخر** | regex + مطابقة substring |
| إنجليزي داخل مسار عربي | `8a61e10b` (`…l01-attachment`): 55 حرفًا عربيًا مقابل 888 لاتينيًا (94.2%)، و156 كلمة إنجليزية، يقرؤها صوت TTS عربي | regex |
| مسارات بلغة أو مصدر خطأ | **2 من 15 (13.3%)** | ما سبق |
| إنجليزي متبقٍ في مسارات أخرى | 4 مسارات فيها 0.8 إلى 2.1% أحرف لاتينية (مثل `2cf5607a`: 46 حرفًا و9 كلمات) | regex |
| أصل النص المنطوق | 7 مسارات `source-text` تتطابق 100% من جملها (≥ 25 حرفًا) مع **عدة** artifacts ترجمة DeepSeek (مثل `fbfa65a1` و`f814a576`). الأولى `partial`، والثانية `completed`، والنص مشترك بينها. ‏0 تطابق مع ترجمات Hy-MT2 التي راجعها الكتالوج. أما المسارات الستة `attachment` فلا تطابق أي ترجمة، لأن مصدرها الملخصات والأسئلة | مطابقة substring لكل جملة |
| تسجيل مصدر نص الصوت | `track.json` لا يذكر أي artifact ترجمة، فاضطررنا لاستنتاج المصدر بالمطابقة، والنتيجة غامضة بين عدة artifacts. **جودة ترجمة النص المنطوق غير مقيسة (UNVERIFIED)** | فحص المفاتيح |
| حالة الترجمة المصدّرة | 20 artifact `translate-content` لكورس `b2c-demo`: ‏6 `completed`، و13 `partial`، و1 بلا حالة. **كلها** `suspect_segment_ids = []`، والمقاطع فيها `{segment_id, text}` فقط، بلا QE ولا حالة مراجعة. الكتالوج يقيس 57.4% قبولًا خاطئًا على نفس نوع المخرج | قراءة كل `translation.json` |
| حالة التشغيل المعروضة | تقدم كتاب `book-aaea…`: ‏`translation` و`book_translation` حالتهما `UNKNOWN`، و`qa` حالتها `PARTIAL`، و`audio` حالتها `RUNNING`. و`GET /v1/audio/b2c-mp3-57f9…` يعيد `RUNNING` دون `completed/total/updated_at`. لا يوجد أي بُعد جودة | GET حي |
| الحقوق | التصدير والـ ZIP وتنزيل MP3 لا تفحص الحقوق. فحص الحقوق موجود فقط في مسار المتعلم المعطّل (`serving.py`) | grep |

### 3.4 الأداء الحي (ثلاث محاولات لكل نقطة)

| النقطة | الزمن |
|---|---|
| `GET /v1/courses/b2c-demo/books/book-aaea…/progress` | **11,729 و10,592 و11,953 ms** لاستجابة 14.7KB. هذه هي النقطة التي يستدعيها التطبيق دوريًا (`postDelayed`) |
| `GET /v1/courses` | 1,024 ms (5.2KB) |
| `GET /v1/library/books` | 1,150 ms (8.2KB) |
| `/health` | 2 ms، ولا يفحص Temporal ولا S3 ولا Postgres |

**السبب:**

- كل طلب يمسح prefixes كاملة في S3 ويقرأ ملفات JSON.
- لا يوجد cache ولا فهرس.
- الـ API محدود بـ 0.5 CPU و512MiB (`compose.yml:166-167`).

### 3.5 النظافة التشغيلية

- **خلط بيانات الاختبار والإنتاج:** bucket واحد يحوي 30 prefix علويًا، أغلبها canaries (`phase5-seatweaving-*`، و`crs_itest`، و`phase2-*`) بجانب كورس العمل `b2c-demo`. كذلك 21 manifest من نوع `hello-*`.
- **قاعدة البيانات:** Postgres لا يحتوي جداول SRS ولا users/tenants. الجداول الموجودة: `artifacts`، و`b2c_*`، و`llm_cache`، و`prov_*`، وجداول الميزانية.
- **التسمية:** `b2c-minio-1` يعمل بصورة `cgr.dev/chainguard/minio:latest` غير مثبتة، خلافًا للقاعدة 9 في `CLAUDE.md`. هذه ملاحظة مرحلة المنصة، وتؤثر على التسليم لأن كل بايت يُقدَّم يمر منه.

---

## 4. المشاكل مرتبة حسب الخطورة

### حرجة (Critical)

**C1. لا يوجد مسار تسليم للمتعلم يعمل، ولا شيء يُسلَّم عبره.**

- **الدليل:**
  - 0 `course-release`، ولا يوجد كاتب لها.
  - `web/` فارغ.
  - `learner_authenticator=None`، فيعيد مسار SRS الرمز 503.
  - استجابة الدرس بلا نص درس.
  - 0 `lesson_assets`، فالـ quiz والـ SRS فارغان.
  - 0 سؤال عربي.
- **الأثر على الكورس النهائي:** لا يوجد «كورس» يصل للمتعلم. ما يصل فعلًا هو MP3 وZIP عبر تطبيق المشغّل وGoogle Drive، وهذه تتجاوز كل بوابات الإصدار.
- **السبب الجذري:**
  - Phase 9 بُنيت كواجهات قراءة فوق عقد `CourseRelease` لم يُنتجه أحد.
  - التسليم الحقيقي نما عشوائيًا داخل أداة المشغّل.

**C2. التسليم الفعلي يتجاوز بوابات الجودة والحقوق، والحالة المعروضة لا تعكس الصحة.**

- **الدليل:**
  - توليد MP3 والتصدير لا يفحصان QE أو المراجعة أو الحقوق، ويقبلان الجزئي (`routers/audio.py:48-103`).
  - 13 من 20 ترجمة `partial`، وكلها `suspect=[]`، مع 57.4% قبول خاطئ مقاس في الكتالوج.
  - مساران من 15 مسار MP3 بلغة أو كتاب خطأ.
- **الأثر:** المستخدم يحصل على محتوى خاطئ بختم «مكتمل». هذا نمط فشل الترجمة نفسه، منقولًا إلى طبقة التسليم.
- **السبب الجذري:**
  - البوابة الوحيدة (`S3CourseServingCatalog.load`) على مسار غير مستخدم.
  - التصدير صُمم كقراءة artifacts خام، لا كإصدار.

**C3. مصادقة غير صالحة لخدمة منشورة على الإنترنت العام.**

- **الدليل:**
  - token مشترك واحد (`app.py:71-95`)، والخدمة منشورة عبر Funnel العام (`04-OPERATIONS.md:157`).
  - نفس الـ token يسمح بأفعال إدارية عبر `model_lifecycle.py:451-484`: تفعيل نماذج، وتعيين النموذج الافتراضي لكل مرحلة، وتسجيل مفاتيح مزودين خارجيين. ويسمح كذلك برفع كتب وتشغيل workflows مكلفة.
  - لا rate limiting ولا audit log (grep على `slowapi|audit|Idempotency|opentelemetry` في `packages/api/src`: صفر).
  - القيمة الافتراضية `false` في الكود وcompose.
  - الـ token يُسلَّم لـ `DownloadManager` النظامي.
- **الأثر:**
  - تسرب token واحد يعني تحكمًا كاملًا، وتكلفة مزودين مدفوعين، ووصولًا لكتب غير مرخصة.
  - ويرتبط ذلك بحقيقة أن 13 من 20 ترجمة لكتب حقيقية تحمل `model_id = deepseek/deepseek-v4-flash-0731`. هل نُفذت خارجيًا؟ هذا UNVERIFIED هنا، ويُحال إلى تدقيق المنصة 08 وقيد D-004/D-022. لكن الـ API يتيح ذلك من الهاتف بلا بوابة.
  - المشكلة تقابل OWASP API Top 10 2023: ‏API2 وAPI4 وAPI5.
- **السبب الجذري:** حل مؤقت لتشغيل APK عن بعد صار دائمًا.

**C4. الإجابات الصحيحة تتسرب في استجابات المتعلم.**

- **الدليل:** `routers/serving.py:110-113,120-126` تعيد `model_dump()` لنماذج داخلية فيها `answer_index` و`rationale`. الاختبار يغطي `review-quiz` فقط.
- **الأثر:** يبطل التقويم وSRS عند تفعيل مسار المتعلم.
- **السبب الجذري:** لا توجد DTOs مخصصة للمتعلم.

### عالية (High)

**H1. انحراف بين المصدر والمنشور والعميل، ولا توجد بوابة نشر.**

- **الدليل:**
  - الصورة الحية من 2026-10-13 وتنقصها 9 مسارات.
  - ما زالت تعيد 503 بدل 404 لـ workflows غير موجودة، وهو سلوك أُصلح في المصدر.
  - الـ APK يحتاج `/materialize` غير الموجود.
- **الأثر:** إصلاحات مُعلنة غير فعّالة، وميزات معطلة.
- **السبب الجذري:**
  - صورة `:dev` بلا وسم commit.
  - لا يوجد OpenAPI snapshot ولا smoke بعد النشر.
  - Android ليس في CI.
  - المشكلة تقابل OWASP API9 Improper Inventory Management.

**H2. تطبيق Android غير قابل للصيانة أو الاختبار.**

- **الدليل:**
  - ملف واحد بحجم 221KB، وأسطر تصل إلى 5,094 حرفًا.
  - 506 `JSONObject` يدويًا، و83 `catch(Exception`، منها 28 صامتة.
  - لا `res/`، ولا ViewModel، ولا Repository، ولا WorkManager، ولا Media3.
  - 28 إصدارًا (versionCode).
- **الأثر:** كل تعديل يخاطر بكسر شيء، والفشل يُبتلع بصمت، ولا إتاحة.
- **السبب الجذري:** نمو تراكمي لعميل تجريبي دون معمارية، خلافًا لـ [Guide to app architecture](https://developer.android.com/topic/architecture).

**H3. الحالة المعروضة غير صادقة أو غير متاحة.**

- **الدليل:** ترجمات `UNKNOWN`، و503 كاذب، وصوت `RUNNING` دون أي عداد أو `updated_at`، ولا يوجد بُعد جودة.
- **الأثر:** المشغّل يقرر على معلومات خاطئة.
- **السبب الجذري:** الحالة مشتقة من Temporal مباشرة، بلا heartbeat أو staleness، ولا سجل جودة لكل وحدة.

**H4. نقطة التقدم تستغرق 10.6 إلى 12.0 ثانية، وكل الفهارس مسح S3.**

- **الدليل:** القسم 3.4.
- **الأثر:**
  - انتهاء المهلة على الشبكات الضعيفة.
  - حمل مستمر على MinIO مع polling دوري.
- **السبب الجذري:**
  - الحالة في JSON داخل object storage.
  - جدول `artifacts` غير مكتمل وغير متسق (83 صفًا مقابل 227 manifest).

**H5. الصوت المُسلَّم بلا عقد `AudioAsset`، وبلا توقيتات أو loudness أو ID3، ولا يسجّل مصدر نصه.**

- **الدليل:** القسم 3.3: ‏0 من 15 يطابق T-AUD-01، و`track.json` بلا مرجع ترجمة.
- **الأثر:** لا تزامن ممكن، ولا تتبع (T-E2E-03)، ولا يمكن معرفة أي ترجمة نُطقت.
- **السبب الجذري:** مرحلة الصوت أنتجت صيغة خاصة، والتسليم قبلها كما هي.

### متوسطة (Medium)

- **M1. حالة قابلة للتعديل في S3 مع `RLock` محلي فقط** (`course_library.py:12`). النتيجة last-write-wins بلا ETag أو `If-Match`، وتنكسر مع أكثر من worker أو replica.
- **M2. لا observability في الـ API.** لا OTel instrumentation، ولا request-id، ولا metrics لكل route. و`/health` لا يفحص الاعتماديات.
- **M3. لا `Idempotency-Key` للـ POST غير الحتمية:** `corrections`، و`revisions`، وإنشاء الكورسات.
- **M4. تغيير النموذج الافتراضي للمرحلة من الهاتف بلا بوابة تقييم** (`model_lifecycle.py:476-478`).
- **M5. هوية كتاب مزدوجة.** المعرّفان `book-main` و`book-aaea5c4b…` متجاوران مع `migration_id` في استجابات المكتبة والتقدم. وأحد أعراض ذلك: مسار درس كتاب SAT يحمل نص كتاب غرامشي (`1b9186f6`).
- **M6. التصدير يمسح prefix كاملًا بطلب لكل manifest،** وأسماء الملفات غير وصفية، والملفات بلا ID3 (`result_exports.py`، `audio_exports.py`).
- **M7. التطبيق `armeabi-v7a` فقط وبتوقيع debug،** ومكتبته native بلا وظيفة. لا يمكن نشره عبر Play، بسبب متطلب 64-bit ومتطلب [16KB page size](https://developer.android.com/guide/practices/page-sizes) لـ targetSdk 35+.
- **M8. نقطة `/credentials` على الأرجح لا تعمل خلف Funnel.** الفحص `request.url.scheme != "https"` موجود في `model_lifecycle.py:482`، وuvicorn يعمل دون `--forwarded-allow-ips` (`docker/api/Dockerfile:19`). القيمة الافتراضية لهذا الخيار هي 127.0.0.1، فترويسة `X-Forwarded-Proto` القادمة من بوابة شبكة Docker لن تُعتمد. لم يُرسل طلب كتابة للتحقق: UNVERIFIED.

### منخفضة (Low)

- **L1.** README Android و`05-PHASES` قديمة (القسم 2.4).
- **L2.** `/v1/courses/{id}` مزدوج المعنى (GET للمتعلم، وPUT/PATCH للمشغّل).
- **L3.** لا `HEAD`، ولا `Cache-Control`، ولا `If-Range`، ولا presigned URLs في بث الصوت. كل البايتات تمر عبر Python محدود بـ 0.5 CPU.
- **L4.** لا RFC 9457 Problem Details. الأخطاء نصوص حرة مختلطة اللغة.
- **L5.** bucket واحد للإنتاج والتجارب، وصورة MinIO غير مثبتة.
- **L6.** العنوان الافتراضي في `build.gradle` هو المنفذ 443 المعطوب، ويُصلَّح ضمنيًا بالرجوع إلى 8443.

---

## 5. التصميم المستهدف وفق الممارسات الصناعية

### 5.1 المبدأ الحاكم: «لا شيء يُسلَّم إلا من إصدار»

كل ما يصل لمستخدم، سواء قراءة أو تشغيلًا أو ZIP أو MP3 أو EPUB، يُشتق من **Course Release** واحد:

- **ثابت (immutable).**
- **معنون بالمحتوى (content-addressed).**
- **مصحوب ببيان (manifest)** يذكر لكل وحدة:
  - بصمة المصدر والهدف.
  - verdict الجودة: QE، والمراجعة البشرية، والمراجع.
  - الصفحة المصدر.
  - إصدار النموذج والـ prompt.
  - بصمة الصوت وتوقيتاته.
  - الحقوق.

ينشئه **Release Builder** حتمي يرفض أي مدخل لا يحقق البوابات. هذا تطبيق نمط build provenance في [SLSA v1.0](https://slsa.dev/spec/v1.0/) و[in-toto](https://in-toto.io/) على المحتوى: التسليم يقرأ attestation الإصدار، لا الحالة الحية.

### 5.2 المعمارية

```
            ┌──────── Operator plane (tailnet-only; identity via Tailscale/OIDC; role=operator|reviewer|admin) ────────┐
Android op ─┤ /ops/v1: books, stage runs, review queue, corrections, release build, exports                               │
            └──────────────┬───────────────────────────────────────────────────────────────────────────────────────────┘
                           │ commands → Temporal ; state ← Postgres read models (indexed)
                    ┌──────┴───────┐
                    │ Release      │  gates: rights=cleared, translation units accepted (D-028) + reviewed sample,
                    │ Builder      │  AudioAsset complete, citations 100%, learner-safe projection → CourseRelease
                    └──────┬───────┘
                           │ immutable bundle (S3, versioned, per-environment bucket)
            ┌──────────────┴──────────── Learner plane (public; OIDC role=learner) ─────────────────────────────────────┐
Web/Android ┤ /learn/v1: releases, lessons (Arabic text + sentence ids + citations), audio (presigned URL, Range),         │
learner     │ segments (timings), quiz (no answers), reviews + SRS (Postgres, per-user)                                    │
            └──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
Exports = renderers over a release: EPUB 3 + Media Overlays; MP3 + ID3v2.4 + timings JSON; JSON/MD with provenance
```

**حدّان منفصلان (operator وlearner):**

- يمكن أن يبقيا modular monolith في FastAPI واحد بـ routers منفصلة.
- لكل حدّ بادئة، وسياسة مصادقة، وOpenAPI منفصل.
- لا يُعرض حدّ المشغّل خارج الـ tailnet.

**الحالة في PostgreSQL:**

- الجداول: `courses`، و`books`، و`memberships`، و`stage_runs`، و`unit_quality` (verdict لكل وحدة ترجمة أو صوت)، و`releases`، و`release_units`، و`srs_cards`، و`reviews`، و`audit_log`.
- الإدارة عبر Alembic migrations.
- optimistic concurrency بعمود `version`، أو `ETag` مع `If-Match`، حسب [RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110#section-13.1.1).
- S3 للـ blobs فقط.
- تسجيل `artifacts` transactional عبر نمط outbox، مع reconciler دوري يقارن S3 بالجدول.

**التقدم:**

- read model (projection) يُحدَّث من أحداث العمال، مع heartbeat وprogress.
- حقل `stale_since` يظهر إذا تجاوز الـ heartbeat المهلة. المرجع: [Temporal heartbeat timeout](https://docs.temporal.io/encyclopedia/detecting-activity-failures#heartbeat-timeout).
- المهم: يُعرض **عدد الوحدات المقبولة دلاليًا** بجانب عدد الوحدات المنتجة.

**الصوت:**

- presigned URLs قصيرة العمر من S3/MinIO، فيكون Range أصيلًا، ولا تمر البايتات عبر Python.
- `segments.json` لكل درس يربط `sid` بـ start/end.
- ID3v2.4 بحقول: `TIT2`، و`TALB`، و`TRCK`، و`TLAN=ara`، و`TCOP`، و`TXXX:release_id`. المرجع [id3.org v2.4](https://id3.org/id3v2.4.0-frames).
- قرار الصيغة يُحسم بقرار مالك موثق: إما [ACX](https://www.acx.com/help/acx-audio-submission-requirements/201456300) ‏(192kbps CBR و44.1kHz وRMS من -23 إلى -18 dB)، أو صيغة كلام أخف مبررة. وفي الحالة الثانية يُعدَّل T-AUD-01 بقرار، لا بصمت. لا يجوز أن يبقى المنتج والبوابة مختلفين.

**التصدير المعياري:**

- [EPUB 3.3](https://www.w3.org/TR/epub-33/) مع [Media Overlays 3.3](https://www.w3.org/TR/epub-mediaoverlays-33/): تزامن نص وصوت معياري ومتاح (accessible).
- JSON/Markdown مع provenance وverdict لكل وحدة، ونص النموذج منفصل عن تحرير الإنسان.

### 5.3 المصادقة والتفويض

**المراجع:** [OWASP API Security Top 10 2023](https://owasp.org/API-Security/editions/2023/en/0x11-t10/) و[RFC 9700 OAuth 2.0 Security BCP](https://www.rfc-editor.org/rfc/rfc9700) (يناير 2025).

**المشغّل (فورًا، وبلا تكلفة):**

- إزالة Funnel عن الـ API، والاكتفاء بـ [Tailscale Serve](https://tailscale.com/kb/1312/serve) داخل الـ tailnet، مع ACL للجهاز والمستخدم.
- هوية المستخدم من ترويسة `Tailscale-User-Login` التي يضيفها Serve، مع التحقق من أن المصدر هو proxy الـ tailnet فقط.
- الإبقاء على token كطبقة ثانية، مع تدويره وانتهاء صلاحيته.

**المتعلم:**

- OIDC Authorization Code + PKCE ([RFC 8252](https://www.rfc-editor.org/rfc/rfc8252)، [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636)).
- مزود ذاتي الاستضافة ومثبت النسخة: Keycloak أو Authentik أو Zitadel.
- access tokens قصيرة العمر، مع تحقق من `iss` و`aud` و`exp`.

**التفويض:**

- الأدوار: `admin`، و`operator`، و`reviewer`، و`learner`.
- فحص ملكية الكائن في كل route (BOLA، أي API1).
- إدارة النماذج والمفاتيح لـ `admin` فقط، مع `audit_log`.

**الاستهلاك:** rate limit وحدود حجم على الرفع والتصدير وبدء الـ workflows، عبر الـ proxy أو `slowapi`.

**التنزيل في Android:** لا يُسلَّم token لعمليات النظام. يُنزَّل الملف بـ presigned URL قصير العمر إلى تخزين التطبيق.

### 5.4 تصميم الـ API

- **العقد أولًا:**
  - [OpenAPI 3.1](https://spec.openapis.org/oas/v3.1.0) snapshot في المستودع.
  - CI يفشل عند كسر غير معلن، عبر [oasdiff](https://github.com/oasdiff/oasdiff).
  - عميل Kotlin مولّد (OpenAPI Generator) بدل كتابة المسارات يدويًا.
  - `GET /meta` يعيد `api_version` و`min_client_version` و`git_sha`.
- **اختبار العقد:**
  - [Schemathesis](https://schemathesis.readthedocs.io/) على الصورة المبنية من نفس الـ commit.
  - [Pact](https://docs.pact.io/) من Android.
- **الأخطاء:** [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) مع `type` ثابت، ورسالة عربية للعرض.
- **Idempotency:** ترويسة `Idempotency-Key` للـ POST غير الحتمية. المرجع [draft-ietf-httpapi-idempotency-key-header](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/)، وهو ما زال draft لكنه نمط سائد (Stripe).
- **DTOs للمتعلم:**
  - projection صريح عبر نماذج Pydantic منفصلة، مثل `LearnerQuestion` بلا `answer_index` و`rationale` و`validation`.
  - فحص آلي لقائمة حقول محظورة على مخطط learner.
- **موارد الإصدار:** `ETag` و`Cache-Control: public, max-age=…, immutable`، لأنها ثابتة.
- **Observability:**
  - `opentelemetry-instrumentation-fastapi`، وrequest-id، ومقاييس RED لكل route.
  - `/health/live` و`/health/ready` (الثاني يفحص Postgres وS3 وTemporal).
  - SLO: ‏p95 أقل من 300ms للقراءة، و5xx أقل من 0.5% على 28 يومًا. المرجع [Google SRE Workbook](https://sre.google/workbook/implementing-slos/).
- **الاعتماديات:** FastAPI `Depends` القياسي بدل `ApiContext.late`، وrouters قابلة للاختبار منفردة.

### 5.5 تطبيق Android

**المراجع:** [Guide to app architecture](https://developer.android.com/topic/architecture) و[OWASP MASVS v2](https://mas.owasp.org/MASVS/).

| الجانب | المطلوب |
|---|---|
| المعمارية | Kotlin، وJetpack Compose، وViewModel + StateFlow، وRepository، وRetrofit/OkHttp + kotlinx.serialization من العميل المولّد |
| الرفع | WorkManager مع رفع قابل للاستئناف: [tus](https://tus.io/) أو S3 multipart بـ presigned URLs |
| الصوت | [Media3 ExoPlayer](https://developer.android.com/media/media3/exoplayer) للتشغيل، وMedia3 Download للعمل دون اتصال |
| الأسرار | AndroidKeyStore (موجود وجيد، يُنقل كما هو)، ثم OIDC عبر AppAuth للمتعلم |
| الموارد والإتاحة | `res/values-ar`، وRTL أصيل، وcontentDescription. المعيار [WCAG 2.2 AA](https://www.w3.org/TR/WCAG22/)، ويُفحص بـ Accessibility Scanner وTalkBack |
| البناء | `release` buildType مع R8، وتوقيع خارج المستودع، وABIs ‏`arm64-v8a` + `armeabi-v7a`، وحذف مكتبة native |
| التوزيع | Firebase App Distribution أو Play Internal Testing، مع فحص `min_client_version` |
| الاختبار | unit (JVM) للـ ViewModels والـ Repositories، وCompose UI tests، و[Gradle Managed Devices](https://developer.android.com/studio/test/gradle-managed-devices) في CI لـ API 24 و27 و34 و35 |
| فصل التطبيقين | المشغّل والمتعلم تطبيقان منفصلان، أو flavors من codebase واحد |

### 5.6 بوابات الجودة لمرحلة التسليم

**بوابات حتمية: كل واحدة تمنع إنشاء الإصدار.**

1. **الحقوق:** كل الكتب `rights_status == cleared`، ويُتحقق منها على الخادم.
2. **الترجمة:** كل وحدة ترجمة في الإصدار مقبولة صراحة وفق D-028، بـ QE معاير أو قرار بشري. لا `partial`، ولا وحدة بلا verdict.
3. **الصوت:** كل درس له `AudioAsset` كامل:
   - `segments` تغطي 100% من `sid`.
   - loudness وpeak ضمن العتبة.
   - `asr_cer` ضمن العتبة.
   - أحرف لاتينية أقل من 2% خارج المصطلحات المحمية.
   - بصمة النص المنطوق تطابق نص الدرس في الإصدار، لا كتابًا آخر.
   - `track` يحمل مرجع artifact الترجمة.
4. **التتبع:** كل جملة عربية منشورة لها `sid`، ثم citation، ثم صفحة.
5. **عزل الإجابات:** 0 حقول إجابة في مخطط learner.
6. **تطابق العقد:** OpenAPI الحي يساوي snapshot الـ commit المنشور، وكل مسار يستدعيه العميل موجود (Pact).

**بوابات دلالية وإنسان في الحلقة (HITL):**

- **عينة طبقية قبل الإصدار:** من الجمل والأسئلة والمقاطع الصوتية، بحجم ≥ 20 جملة لكل درس، أو ما يعطي ±5% عند 95% ثقة على مستوى الإصدار.
- **قرار المراجع:** يُسجَّل في `CourseRelease.review` (القرار، والمراجع، والنسبة).
- **الاستماع:** أول وآخر 60 ثانية من كل مسار، ومواضع عشوائية. يُفحص فيها الإنجليزي، والأرقام، ولافتات الفهرس، والكتاب الخطأ.
- **الحكم الآلي المساعد:** LLM-as-judge محلي، أيًّا كان النموذج، يفرز العينة. لا يُعتمد بلا معايرة على عينة بشرية: الاتفاق مع البشر يجب أن يصل Cohen's κ ≥ 0.6 قبل الاعتماد عليه في الترتيب.

### 5.7 مقاييس التقييم والعتبات المستهدفة

| المقياس | العتبة |
|---|---|
| وحدات مسلَّمة لم تحقق البوابات 1-4 | **0%** |
| خطأ major/critical في عينة بشرية على الإصدار | major ≤ 2%، وcritical = 0 |
| مسارات صوت بلغة أو مصدر خطأ | **0** |
| T-AUD-03 | ±100ms لـ ≥ 95% من الجمل |
| التطابق التعاقدي (Pact وSchemathesis) | 100% من مسارات العميل، و0 أخطاء 5xx |
| زمن القراءة | p95 أقل من 300ms للتقدم والكتالوج. TTFB للصوت أقل من 500ms |
| الأمن | 0 مسارات بلا مصادقة غير `/health/*`. ‏[OWASP ZAP](https://www.zaproxy.org/) API scan بلا High. ‏MASVS-L1 |
| الإتاحة (T-E2E-04) | 0 مخالفات حرجة في axe وAccessibility Scanner، مع TalkBack يدوي |
| الموثوقية | crash-free sessions ≥ 99.5% |

### 5.8 الاختبار

- **Unit:** الـ DTOs، والبوابات، وRange، والـ projections. تبقى الـ 197 كخط أساس.
- **Contract:** OpenAPI snapshot + oasdiff، وSchemathesis على الصورة، وPact من Android.
- **Integration:** compose مؤقت (`-p b2c-ci`) فيه MinIO وPostgres وTemporal dev. يبني release من fixtures مرخصة ويقدّمها.
- **E2E (T-E2E-01..04):**
  - المسار الكامل: كتابان (D-017)، ثم release، ثم ويب (Playwright)، ثم Android (Gradle Managed Devices).
  - **اختبارات سلبية:**
    - ترجمة partial تُرفض.
    - كتاب `internal_test_only` يُرفض.
    - مسار بنص إنجليزي يُرفض.
    - مسار بنص كتاب آخر يُرفض.
    - طلب learner لا يرى أي حقل إجابة.
- **الأمن:** ZAP، واختبار BOLA، واختبار rate limit.
- **النشر:** الصورة موسومة بـ `git sha` وdigest، ثم smoke بعد النشر يقارن `/openapi.json` الحي بالـ snapshot.

---

## 6. تحليل الفجوة وخطة الانتقال

### 6.1 ما نُبقيه

- **`verified_ref`/`checked_ref`** وفحص scope الـ URI في `deps.py`.
- **فحوص `S3CourseServingCatalog.load`** (الحقوق، و`lesson_versions`، وQA final، وACX، وvoice). تُنقل إلى Release Builder.
- **`parse_single_byte_range`** (`serving.py:151`) واختباراته.
- **من مسار الرفع:** `upload_limits.py`، والهوية المشتقة من المحتوى للكتب.
- **من Android:** منطق `TokenVault` و`EndpointRouting`، وفكرة اكتشاف القدرات عبر OpenAPI (تتحول إلى `min_client_version`).
- **الاختبارات:** الـ 197 الحالية.

### 6.2 ما نُعيد كتابته

- **تطبيق Android بالكامل** وفق 5.5. القديم يبقى مرجعًا سلوكيًا حتى التكافؤ.
- **طبقة الحالة:** من JSON في S3 إلى Postgres.
- **مسار المتعلم:** DTOs، وrelease-only، وpresigned URLs، وOIDC.
- **التصدير:** renderers فوق الإصدار.

### 6.3 ما نحذفه

- القيمة الافتراضية `B2C_API_AUTH_REQUIRED=false`.
- Funnel للـ API الإداري.
- مكتبة native ‏`b2coperator`.
- استدعاء `/materialize` من العميل، أو تنفيذ المسار إن قرر المالك أنه مطلوب.
- canaries من bucket الإنتاج: تُنقل إلى bucket أو بيئة `research` بعد نسخة احتياطية، بقرار المالك.
- مسارا MP3 الخاطئان (`1b9186f6`، `8a61e10b`): يُحجبان من القوائم والتصدير، ولا يُحذفان من S3 بلا قرار.

### 6.4 الخطوات بالترتيب

| # | الخطوة | يعتمد على | المخاطرة |
|---|---|---|---|
| 0 | **إيقاف النزيف:** وسم كل التصدير والـ MP3 الحالية بأنها «غير مراجعة/تجريبية»، وحجب المسارات التي يتجاوز فيها النص اللاتيني 2%، وعدم مشاركة أي ملف خارجيًا | لا شيء | منخفضة |
| 1 | **الأمن:** نقل الـ API إلى Tailscale Serve (tailnet فقط)، وتدوير الـ token، وجعل المصادقة إلزامية افتراضيًا، وإضافة rate limit وaudit log، وفصل `model-providers` لدور admin | لا شيء | انقطاع الهاتف إن لم يكن على الـ tailnet، فيُختبر أولًا |
| 2 | **نشر قابل للتحقق:** CI يبني صورة بوسم sha وdigest، وOpenAPI snapshot + oasdiff، وsmoke بعد النشر. ثم نشر الـ commits المعلقة (الـ 9 مسارات و404 بدل 503) | 1 | كسر توافق APK 0.5.11، فيُختبر ضد الصورة الجديدة قبل التبديل |
| 3 | **Postgres كمصدر للحالة:** الجداول في 5.2 + Alembic، وbackfill من S3 يُتحقق منه (227 manifest = 227 صفًا، مع حل الـ 16 مقابل 12 وصفوف `t`)، وreconciler | 2 | خطأ backfill، فيُشغَّل dry-run ويُقارن |
| 4 | **حالة صادقة:** projection التقدم مع heartbeat وstaleness، وverdict الجودة لكل وحدة. الهدف p95 أقل من 300ms | 3 + `TranslationUnit` في contracts 1.2.0 (D-028) | — |
| 5 | **Release Builder:** البوابات 1-6. يتطلب `AudioAsset` حقيقيًا، ووحدات ترجمة مقبولة صراحة، ونص درس عربي بـ `sid`، وأسئلة عربية بـ `lesson_assets` | مراحل الترجمة والصوت والتأليف | **أكبر اعتماد خارجي:** لا يمكن إنشاء release صالح قبل إصلاح الترجمة |
| 6 | **Learner API:** DTOs بلا إجابات، ونص الدرس، وpresigned URLs، وSRS في Postgres، وOIDC | 5 | — |
| 7 | **التصدير كـ renderers:** EPUB 3 + Media Overlays، وMP3 + ID3 + timings، وJSON/MD مع provenance | 5 | — |
| 8 | **Android جديد:** Kotlin على العميل المولّد، والمشغّل أولًا (أعلى 5 شاشات استخدامًا)، ثم flavor المتعلم. CI بـ Gradle Managed Devices، وتوزيع عبر Firebase App Distribution | 2 (عقد مستقر) و6 | الحجم، فيُبقى القديم حتى التكافؤ |
| 9 | **واجهة ويب للمتعلم RTL:** Playwright + axe، وT-E2E-01..04 | 6 و7 | — |
| 10 | **تحديث الوثائق:** `02-CONTRACTS`، و`03-TESTING` (T-AUD-01 بعد قرار الصيغة)، و`05-PHASES`، وREADME Android | يتوازى مع كل خطوة | — |

### 6.5 اعتماديات على مراحل أخرى

- **الترجمة (D-028):** تحتاج وحدات مقبولة صراحة، وverdict QE معايرًا، وطابور مراجعة بشرية. بدونها تبقى البوابة 2 مستحيلة.
- **الصوت:** يحتاج `AudioAsset` (segments، loudness، ASR-CER)، وفحص لغة المدخل، ومرجع artifact الترجمة في `track.json`، وتطابق `track_id` مع الكتاب.
- **التأليف/QA:** يحتاج نص درس عربيًا بجمل معرَّفة، وأسئلة عربية مع `lesson_assets`. اليوم: 0 من 2,246 سؤالًا عربي، و0 `lesson_assets`.
- **الإدخال والمخطط:** يحتاجان هوية كتاب واحدة، وتنظيف أرقام الفهرس والحواشي قبل النطق.
- **المنصة:** تحتاج تثبيت الصور، وفصل البيئات، وتوضيح استخدام المزودين الخارجيين على كتب حقيقية (تدقيق 08).

---

## 7. معايير القبول (متى تُعد المرحلة «منجزة»)

كل بند قابل للقياس، ويُسجَّل دليله في `eval/reports/phase-9.md`.

1. **الإصدار:**
   - يوجد ≥ 1 `CourseRelease` لكورس D-017 (كتابان)، أنشأه Release Builder، والبوابات 1-6 ناجحة.
   - المحاولات السلبية الخمس تُرفض كلها: partial، وغير cleared، وصوت إنجليزي، وصوت من كتاب آخر، ودرس بلا segments.
2. **صفر تجاوز:** 0 مسارات تنزيل أو تصدير تقرأ من خارج release. يُثبت ذلك اختبار آلي يطلب artifact غير منشور ويتوقع 403 أو 404.
3. **جودة المحتوى:**
   - عينة بشرية من الإصدار (≥ 20 جملة لكل درس): ‏major ≤ 2%، وcritical = 0.
   - 0 مسارات صوت بلغة أو مصدر خطأ.
   - 0 قراءات لأرقام فهرس أو حواشٍ في عينة الاستماع.
4. **التتبع:** T-E2E-03 يحقق 20 من 20.
5. **الصوت:** T-AUD-03 ≥ 95% ضمن ±100ms. وT-AUD-01 100% بعد قرار الصيغة الموثق. وID3 كامل 100%.
6. **العقد:**
   - oasdiff بلا كسر غير معلن.
   - Schemathesis بلا 5xx على الصورة المنشورة.
   - Pact 100%.
   - `/openapi.json` الحي يطابق snapshot الـ commit المنشور.
7. **الأمن:**
   - 0 مسارات إدارية خارج الـ tailnet.
   - OIDC للمتعلم.
   - BOLA مختبر ومرفوض.
   - rate limit مختبر.
   - ZAP بلا High.
   - 0 حقول إجابة في استجابات learner.
   - MASVS-L1.
8. **الأداء:** p95 أقل من 300ms للتقدم والكتالوج، بعد أن كان 10.6 إلى 12.0 ثانية. TTFB للصوت أقل من 500ms.
9. **الحالة:** workflow بلا heartbeat يظهر `stale` خلال دقيقة. ولا `UNKNOWN` ولا 503 لـ workflow معروف.
10. **Android:**
    - مصفوفة الأجهزة: API 24 و27 و34 و35، على 32-bit و64-bit.
    - E2E على جهاز حقيقي: رفع، ثم مراقبة، ثم درس مع صوت متزامن، ثم quiz وSRS، ثم استعادة بعد kill.
    - crash-free ≥ 99.5%.
    - release موقّع مع R8.
    - CI يشغّل unit وUI tests.
11. **الإتاحة:** T-E2E-04 بلا مخالفات حرجة، مع فحص TalkBack موثق.
12. **الاتساق:** جدول `artifacts` يطابق manifests S3 بفرق 0 لمدة 7 أيام في الـ reconciler. والوثائق في القسم 2.4 محدثة.

---

## ملحق أ: ما لم يُتحقق منه (UNVERIFIED)

- **جودة ترجمة النص المنطوق** في 7 مسارات `source-text`: مصدرها ترجمات DeepSeek لم يراجعها الكتالوج (الكتالوج راجع Hy-MT2). لا يجوز إسقاط نسبة 57.4% عليها، ولا افتراض أنها جيدة.
- **Loudness وpeak:** لا يوجد ffmpeg/ffprobe في حاويات b2c، ولم تُثبَّت أدوات. خصائص الترميز قيست بتحليل frame headers.
- **`/credentials` خلف Funnel:** الاستنتاج من الكود فقط (M8). لم يُرسل طلب كتابة.
- **سلوك APK 0.5.11 على جهاز حقيقي:** لم يُشغَّل.
- **سبب توقف workflow الصوت `b2c-mp3-57f9…`:** لم يُفحص تاريخ Temporal.
- **تنفيذ ترجمات `deepseek/*` عبر مزود خارجي على كتب حقيقية:** لم يُتحقق منه هنا، ويُحال إلى تدقيق 08.

## ملحق ب: مراجع الممارسات (تاريخ الاطلاع 2026-10-24)

- **API:**
  - OWASP API Security Top 10 2023: https://owasp.org/API-Security/editions/2023/en/0x11-t10/
  - RFC 9700: https://www.rfc-editor.org/rfc/rfc9700
  - RFC 8252: https://www.rfc-editor.org/rfc/rfc8252
  - RFC 7636: https://www.rfc-editor.org/rfc/rfc7636
  - RFC 9110: https://www.rfc-editor.org/rfc/rfc9110
  - RFC 9457: https://www.rfc-editor.org/rfc/rfc9457
  - Idempotency-Key draft: https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/
- **العقود:**
  - OpenAPI 3.1: https://spec.openapis.org/oas/v3.1.0
  - Schemathesis: https://schemathesis.readthedocs.io/
  - Pact: https://docs.pact.io/
  - oasdiff: https://github.com/oasdiff/oasdiff
- **Android:**
  - https://developer.android.com/topic/architecture
  - MASVS: https://mas.owasp.org/MASVS/
  - Media3: https://developer.android.com/media/media3/exoplayer
  - Gradle Managed Devices: https://developer.android.com/studio/test/gradle-managed-devices
  - 16KB pages: https://developer.android.com/guide/practices/page-sizes
- **الصيغ:**
  - EPUB 3.3: https://www.w3.org/TR/epub-33/
  - Media Overlays 3.3: https://www.w3.org/TR/epub-mediaoverlays-33/
  - ID3v2.4: https://id3.org/id3v2.4.0-frames
  - ACX: https://www.acx.com/help/acx-audio-submission-requirements/201456300
- **الإتاحة:** WCAG 2.2: https://www.w3.org/TR/WCAG22/
- **Provenance:**
  - SLSA v1.0: https://slsa.dev/spec/v1.0/
  - in-toto: https://in-toto.io/
- **التشغيل:**
  - SRE Workbook: https://sre.google/workbook/implementing-slos/
  - Tailscale Serve: https://tailscale.com/kb/1312/serve
  - Temporal heartbeat: https://docs.temporal.io/encyclopedia/detecting-activity-failures
