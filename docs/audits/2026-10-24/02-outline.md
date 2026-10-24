# تدقيق المرحلة 02: بناء المخطط (Outline) واعتماده

- التاريخ: 2026-10-24، الفرع `hardening`.
- النطاق: `packages/outline` و`OutlineActivities` و`IngestPDFWorkflow` في `packages/orchestration/src/b2c_orchestration/ingest.py`، ومسارات `/v1/ingestions/{id}/outline*` في `packages/api/src/b2c_api/routers/ingestion.py`، والعقود في `packages/contracts`، ومنتجو `ParsedBook.toc_source` ومستويات العناوين في `packages/ingest`، والعميل الوحيد (تطبيق Android للمشغل)، والـ artifacts الحقيقية في MinIO.
- المنهج: قراءة فقط. لم أعدّل أي كود أو إعداد أو بيانات. قست الجودة بسكربتات Python نفذتها داخل `b2c-worker-1` وهي تقرأ من bucket `artifacts` ومن Postgres ولا تكتب شيئًا. أعدت تشغيل كود المخطط الحالي في الذاكرة على الكتب الحقيقية، وقرأت bookmarks ملفات PDF الأصلية بـ pypdf في الذاكرة. لم يُرسل أي محتوى من الكتب إلى خدمة خارجية.
- الموقف: طبقت نفس الشك الذي طُبق في تدقيق الترجمة (`outputs/translation-review-2026-10-24/TRANSLATION-ERROR-CATALOGUE.md`). كلمة "approved" هنا لا تعني أن المخطط صحيح.

---

## 1. غرض المرحلة وعقدها

### الغرض
تحويل `ParsedBook` لكتاب أساسي (spine) إلى `CourseOutline`، أي وحدات (modules) فيها دروس (lessons). كل درس يغطي نطاقًا متصلًا من كتل المصدر (`spine_span`). بعد ذلك يُعتمد المخطط اعتمادًا موثقًا (`approve-outline`) قبل أي فهرسة أو توليد.

المخطط هو العمود الفقري لكل ما بعده. خطأ واحد في حدود درس أو في عنوانه ينتقل إلى كل المراحل اللاحقة: chunks، والأسئلة، والترجمة، والصوت، وتطبيق المتعلم.

### المدخلات
- `ParsedBook` (`packages/contracts/src/b2c_contracts/models.py:113`)، وفيه `blocks[]`. لكل كتلة `type` و`level` و`text` و`refs(page_no)` و`section_path`، وللكتاب `toc_source ∈ {pdf_outline, epub_nav, headings, llm_inferred}`.
- معرّفات الكتب المساندة، وهي لا تُستخدم في بناء المخطط نفسه.

### المخرجات
- `course-outline.json`، وهي مسودة يكون فيها دائمًا `requires_review=True` (`from_book.py:194-199`).
- `approved-outline.json`: المخطط مع `approval_provenance` من نوع `human` أو `system`، ومعه `system_review_checks` و`draft_ref` و`spine_parsed_ref` و`source_identity` (`ingestion.py:383-400`).

### العقد المعلن
- `docs/02-CONTRACTS.md:49`: الـ spans متتالية وغير متداخلة، و`llm_inferred` ⇒ `requires_review`، وGate G0.
- `docs/03-TESTING.md:59-61`:
  - T-OUT-01: snapshot لكتاب له bookmarks.
  - T-OUT-02: no-TOC ⇒ `llm_inferred` + `requires_review`.
  - T-OUT-03: تغطية ≥ 98% من المتن.
- `docs/02-CONTRACTS.md:117-118`: سياسة `b2c-outline-structural-review` تثبت سلامة البنية فقط، ولا تعني اعتمادًا تربويًا.

### ما تحتاجه المراحل اللاحقة فعلًا (من الكود)
- **الفهرسة/chunking:** تستخدم `spine_span` لكل درس كحد صلب للـ chunk، ومفتاحها `(module_id, lesson_id)` (`packages/indexing/src/b2c_indexing/chunking.py:48-54`).
- **الأسئلة:** تستخدم `expected_lesson_ids` = كل الدروس. في artifact الأسئلة `generate-course-qa/9143…/course-qa.json` توجد 114 قيمة في `expected_lesson_ids`، منها دروس الملاحق والفهرس.
- **الصوت:** يُنشئ track لكل درس، ويأخذ اسمه من `lesson.title` و`lesson.lesson_id` (`orchestration/audio.py:49, 380-406`).
- **الترجمة:** معرّفات المقاطع تُبنى على `lesson_id` (`orchestration/translation.py:61-80`).

إذن يجب أن تكون معرّفات الدروس ثابتة ودلالية، وأن تكون العناوين نظيفة وقابلة للعرض والترجمة، وأن تكون الحدود مطابقة لبنية الكتاب، وأن يُستبعد ما ليس محتوى تعليميًا.

---

## 2. التصميم كما هو منفذ فعلًا

### 2.1 مسار البناء
1. `IngestPDFWorkflow.run` يشغّل `outline_from_parsed_book` على HEAVY queue بمهلة 12 ساعة (`ingest.py:400-411`). المفتاح هو `stage_key("build-outline","4",[parsed_sha], config)` (`ingest.py:351`).
2. `outline_from_book` (`from_book.py:87-201`):
   - المستوى الجذري هو أصغر `level` بين العناوين (`:115`). كل عنوان جذري يصبح module، وأبناؤه المباشرون يصبحون دروسًا. إذا لم يكن له أبناء يصبح درسًا واحدًا (`:151-166`).
   - يحذف العنوان الجذري المكرر المتجاور، والعنوان الجذري الذي لا متن تحته (`:118-142`).
   - يستبعد front matter عبر قائمة بادئات ثابتة (`:51-84`). **هذا الاستبعاد لا يعمل إلا إذا كان أول عنوان جذري في الكتاب نفسه front matter** (`:78-84`).
   - `prior_lessons` = كل الدروس السابقة بالترتيب (`:188`).
   - `module_id` = `m{NN}-{slug(title)[:32]}` (`:157`).
   - `validate_outline_coverage`: تتابع، وعدم تداخل، وتغطية ≥ 98% من حروف المتن (`:207-250`).
3. لا يُستدعى مسار LLM (`_infer_sections` في `ingest.py:264-321`) إلا عندما **لا يوجد أي عنوان إطلاقًا** (`from_book.py:105`). في هذا المسار:
   - تُقسَّم الكتل إلى مجموعات لا تتجاوز 10 صفحات و18k حرف، وتُقسَّم كل مجموعة مستقلة إلى ≤ 32 قسمًا.
   - كل قسم يصبح module فيه درس واحد (`from_book.py:263-286`)، فلا هرمية، والأقسام تنقطع قسرًا عند حدود المجموعات.
   - لا يوجد استبعاد لـ front/back matter، و`start = 0 if index == 1` (`:267`).

### 2.2 مسار الاعتماد
- `POST /outline/approve-system` (`ingestion.py:255-276`) يبني `OutlineReview(review_type="system")` ثم:
  - `merge_bodyless_lessons` و`include_leading_blocks_in_first_lesson` (`review.py:22-100`): ترقيعان آليان يعدّلان المخطط **قبل** المراجعة.
  - `review_outline` (`review.py:101-141`): فحوص بنيوية فقط.
  - تُضبط `requires_review=False`.
- `POST /outline/approve` بنوع `human`:
  - لا يطلب إلا `reviewer_id` نصيًا (`schemas.py:12-29`)، ولا توجد مصادقة (`docs/01-ARCHITECTURE.md:42`).
  - يتحقق فقط من وجود طرفي كل span، ومن ترتيبهما داخل الدرس، ومن حدود الصفحات، ومن أن prerequisites سابقة (`ingestion.py:337-350`).
  - **لا يتحقق من التغطية ولا التداخل ولا الفجوات.** المخطط "البشري" يمكن أن يُسقط نصف الكتاب ويُعتمد رغم ذلك.
- مفتاح artifact الاعتماد يتضمن hash الحمولة، وفيها `approved_at` (`ingestion.py:394-402`). كل اعتماد مكرر ينتج artifact جديدًا: كتاب SAT وحده اعتُمد 4 مرات يوم 2026-10-05.

### 2.3 العميل الفعلي
تطبيق Android هو الواجهة الوحيدة. يستدعي `approve-system` تلقائيًا داخل `ensureOutlineOnly()` و`ensureRag()` (`android/operator-app/.../MainActivity.java:644, 649`). لا توجد شاشة تعرض المخطط للمراجعة أو تسمح بتحريره. نقطة الإنسان في الحلقة (HITL) غير موجودة عمليًا.

### 2.4 تعارضات بين التوثيق والكود

| التوثيق | الواقع |
|---|---|
| `05-PHASES.md:104`: "Human review/approval endpoint" هو المسار | الاعتماد الفعلي في الإنتاج نظامي 100%. كل سجلات `book-progress` الثلاثة: `outline.approval_provenance.review_type = system` |
| `05-PHASES.md:103`: "LLM fallback … remains open" | الكود موجود ومختبر بـ mocks فقط. لم يُشغَّل حيًا (`eval/reports/phase-2.md:52`) |
| `03-TESTING.md:59` T-OUT-01 snapshot | `tests/golden/` فارغ (0 ملفات). لا يوجد golden لأي كتاب حقيقي |
| `02-CONTRACTS.md:118`: السياسة تتحقق من "عناوين دروس موجودة بالنص" | الفحص تحصيل حاصل، لأن العنوان منسوخ من كتلة العنوان الموجودة داخل نفس الـ span (`from_book.py:182`)، ولا يمكن أن يفشل في المسار الحتمي. انظر P9 |
| docstring `from_book.py:94-99`: "first-level headings define modules; children define lessons" | يفترض هرمية لا ينتجها Docling. كل العناوين تخرج بمستوى 2 (`docling_pdf.py:307`)، فيصبح كل عنوان module بدرس واحد |
| `POLICY_VERSION = "1.0.0"` ثابت (`review.py:10`) | اعتمادات 2026-10-05 تحمل 7 فحوص، واعتمادات 2026-10-09/10 تحمل 9 فحوص، وكلها بنفس الإصدار 1.0.0. السياسة تغيرت دون رفع الإصدار |
| `01-ARCHITECTURE.md:17`: "مراجعة outline … ثم تحديد الدروس المراد إنتاجها" | لا توجد خطوة اختيار للدروس، وكل الدروس تدخل في `expected_lesson_ids` |
| `toc_source` يُفترض أن يصف مصدر البنية | `docling_pdf.py:334` يكتب `"headings" if headings else "pdf_outline"`، أي أن غياب العناوين يُوسم `pdf_outline` مع أن Docling لا يقرأ الـ bookmarks أصلًا |

---

## 3. الجودة المقيسة

### 3.1 الـ artifacts الموجودة (`b2c-demo`)

| الكتاب | parser | الصفحات | bookmarks في PDF الأصلي | draft → approved | الاعتماد |
|---|---|---|---|---|---|
| Chalcraft, *From Subordination to Revolution* (`book-70c88ae9…`) | `docling-ocr` | 415 | **14 bookmark نظيفة**: Copyright، Contents، Acknowledgments، Introduction، الفصول 1–6، Conclusion، Notes، Bibliography، Index | `build-outline/bd2fd27d…` → `approve-outline/97f28cec…` (2026-10-10) | system |
| Heuer & Pherson, *Structured Analytic Techniques* 3e (`book-main`) | `surya-2-llama.cpp` | 677 | 110 bookmark بثلاثة مستويات (14/74/22) | `build-outline/07966654…` → `approve-outline/04866153…` وثلاثة اعتمادات أخرى مكررة | system |

طريقة القياس:
- عدد الكلمات لكل درس = مجموع كلمات الكتل من أنواع `_BODY_TYPES` داخل الـ span.
- العناوين المشوهة = عنوان فيه حرف منفرد غير "a" أو "i"، أو فيه نمط `\b\w{1,2} \w{1,2} \w{1,2}\b`.
- المرجع البنيوي = bookmarks ملف PDF الأصلي (`book-upload/*.pdf`)، قرأتها بـ pypdf في الذاكرة.
- أعدت تشغيل `outline_from_book` → `merge_bodyless_lessons` → `include_leading_blocks_in_first_lesson` → `review_outline` الحالية على نفس `ParsedBook`. النتيجة: **الكود الحالي ما زال يُنتج نفس المخطط المعيب ويعتمده بحالة `approved`** للكتابين: 114 درسًا للأول، و18 module و78 درسًا للثاني.

### 3.2 كتاب Chalcraft (المخطط النشط للأسئلة)

الكتاب نفسه، حسب bookmarks وفهرسه في الصفحة 8، يتكون من مقدمة وستة فصول وخاتمة، أي 8 وحدات تعليمية، ثم Notes وBibliography وIndex.

المخطط المعتمد:
- **114 module، وكل module فيه درس واحد.** توزيع الدروس لكل module = `{1: 114}`. اختفت الهرمية بالكامل. عناوين الفصول نفسها صارت modules مستقلة بمحتوى ضئيل، مثل "The Faces and Phases of Popular Mobilization" بـ 116 كلمة (ص59)، وأقسام الفصل صارت modules شقيقة له.
- **الدقة البنيوية على مستوى الفصل = 0/8.** لا يوجد أي module يطابق فصلًا ويحتوي أقسامه.
- **72 من 114 عنوانًا (63%) مشوهة بتباعد OCR**، و97 من 114 (85%) تبدأ بحرف صغير. أمثلة:
  - `gr amsc i : lodes tar of renewal`
  - `c o u n t e r c u l t u r e`
  - `t r a s f o r m i s m o`
  - `w(h) i ther the s tat e?`

  هذه العناوين تدخل كما هي في أسماء tracks الصوت والترجمة والأسئلة.
- **8 دروس عنوانها "Conclusion" أو "conclus i on"** دون سياق الفصل الذي تنتمي إليه.
- **الملاحق دخلت كدروس:** 8 دروس من ص332 إلى ص415 (Notes وBibliography وIndex) = **25,106 كلمة = 17.3% من متن الكتاب (145,454 كلمة)، و84 صفحة من 415 (20%)**. أمثلة:
  - `abbre v i at i ons`
  - `introduc t ion`، وهو في الحقيقة هوامش المقدمة.
  - `chapter two. the faces and phases…`، وهو في الحقيقة هوامش الفصل الثاني.
  - **الدرس الأخير `conclus i on` (ص379–415) = 12,261 كلمة**، وهو هوامش الخاتمة مع **قائمة المراجع كاملة والفهرس الكامل**. عينة من ص410: `"leadership (direzione), 65-69, 134, 306-7; and autonomy, 30, …"`.
- **front matter لم يُستبعد** (`excluded_prefix = 0`):
  - m01 هو صفحة العنوان (10 كلمات).
  - m02 (ص4–8) يضم الغلاف الداخلي والناشر وحقوق النشر (`"© 2025 by John Chalcraft All rights reserved. Library of Congress…"`) والإهداء وجدول المحتويات.

  السبب أن أول عنوان جذري هو اسم الكتاب وليس "Copyright"، فيعيد `front_matter_prefix_end` القيمة 0 (`from_book.py:78-84`).
- **أحجام الدروس:** الأدنى 10 كلمات، والوسيط 1,041، وp10 = 307، وp90 = 2,459، والأقصى 12,261. عدد الدروس الأقل من 300 كلمة = 11.
- **الأثر اللاحق المؤكد:** artifact الأسئلة `generate-course-qa/9143aefc…` مرتبط بهذا المخطط (`approved_outline_sha256 = 42f38efb…`) و`expected_lesson_ids` فيه 114 قيمة تشمل `m115-conclus-i-on.l01` و`m107-abbre-v-i-at-i-ons.l01`. أي أن النظام يخطط لتوليد أسئلة على قائمة المراجع والفهرس.

**التقدير:** لو اعتُبر الدرس صحيحًا عندما يكون عنوانه نظيفًا، ويطابق قسمًا تعليميًا حقيقيًا، ويقع في الفصل الصحيح، فإن 0 من 114 درسًا يحقق الشروط الثلاثة معًا. الهرمية مفقودة في كل الدروس، و63% عناوينها مشوهة، و7% ليست محتوى تعليميًا أصلًا.

### 3.3 كتاب SAT (اعتماد 04866153)
- 20 module و81 درسًا. البنية أفضل بكثير لأن مسار Surya يقرأ الـ bookmarks.
- المقارنة مع bookmarks المستوى 1 (74 قسمًا): **61 من 74 (82%)** درسًا عنوانه يطابق bookmark ويبدأ على صفحتها. الفروقات الـ 13:
  - **أخطاء محتوى تحت عنوان خاطئ (حرجة):**
    - الدرس "5.2 Ranking, Scoring, and Prioritizing" يبدأ في ص129 بدل ص138، فيحمل محتوى 5.1 Sorting كاملًا (ص129–137).
    - الدرس "5.1 Sorting" ينتهي عند ص129، أي أنه مقدمة الفصل فقط.
    - الدرس "10.7 Decision Matrix" = **0 كلمة**.
    - الدرس "10.8 Force Field Analysis" يبدأ في ص616 بدل ص622، فيحمل محتوى 10.7.

    السبب: سطور متن مثل `'5.1 SORTING\nSorting is a basic technique…'` و`'10.7 DECISION MATRIX\nA Decision Matrix helps…'` صُنّفت عناوين بالمستوى 1 عبر الـ regex القديم `_HEADING` (`pdf_book.py:36`، المستخدم في `surya_ocr.py:242` و`openrouter_ocr.py:122` حتى مع وجود bookmarks). كل واحد منها خلق module وهميًا (m13 وm19) وأزاح الدروس.
  - **11 درسًا هي "القسم الأول" لفصل وتحمل مقدمة الفصل** (1–9 صفحات) تحت عنوان القسم. مثال: "7.1 Key Assumptions Check" يبدأ في ص245 والقسم الحقيقي يبدأ في ص253. هذا سلوك متعمد (`from_book.py:171-174`) لكنه يعرض مقدمة الفصل بعنوان خاطئ.
- **front matter كدروس:**
  - صفحة العنوان `'STRUCTURED\nANALYTIC\nTECHNIQUES'`.
  - **كتلة من جدول المحتويات صُنّفت عنوانًا** وصارت درسًا: `'11.3.1 Structuring the Data\n11.3.2 Identifying Key Drivers\n11.4 Role o…'`، بـ 0 كلمة متن.
  - "Figures" (قائمة الأشكال، 601 كلمة).
  - "Foreword".
  - "Preface" بـ 0 كلمة، و"PR EF A C E" مكرر.
- **دروس ضخمة:** "8.2 Challenge Analysis Techniques" = 59 صفحة و10,437 كلمة. "8.1" = 34 صفحة و6,988 كلمة. السبب أن المستوى 2 من الـ bookmarks (22 قسمًا فرعيًا) لا يُستخدم لتقسيم الدروس الكبيرة.
- **اعتمادات بالمستوى 1.0.0 لم تتضمن `lessons_have_body_text`،** فاعتُمدت دروس فارغة (10.7 وPreface بـ 0 كلمة).

### 3.4 ملخص معدلات الخطأ

| المقياس | Chalcraft | SAT |
|---|---|---|
| مطابقة الوحدات لفصول الكتاب | 0/8 | 11/11 فصلًا لها module، مع 2 modules وهمية إضافية |
| مطابقة الدروس للأقسام (bookmark level 1) | لا يوجد مستوى 1 في الـ bookmarks؛ الأقسام من Docling | 61/74 (82%)، منها 4 أخطاء محتوى/عنوان حرجة |
| عناوين مشوهة | 72/114 (63%) | 3/81 (أسطر متعددة أو تباعد) |
| دروس ليست محتوى تعليميًا (front/back matter أو TOC) | 10/114، منها 17.3% من كلمات المتن | 6/81 |
| دروس فارغة أو أقل من 100 كلمة | 1 | 3 |
| أكبر درس | 12,261 كلمة (مراجع + فهرس) | 10,437 كلمة |
| الاعتماد | system، "approved" | system، "approved" |

**الخلاصة:** بوابة الاعتماد النظامية أعطت "approved" لمخططين، أحدهما فاسد هيكليًا بالكامل، والآخر فيه أخطاء ربط محتوى بعناوين خاطئة. السبب نفسه الذي اكتُشف في الترجمة: البوابة تفحص الشكل فقط (تتابع، تغطية، hash) ولا تفحص الصحة.

---

## 4. المشكلات مرتبة حسب الخطورة

### حرجة (Critical)

**P1. الـ bookmarks الموجودة في PDF تُتجاهل في مسار Docling، والمستويات تخرج مسطحة.**
- **الدليل:**
  - `docling_pdf.py:249, 307`: المستوى مشتق من `depth` في `iterate_items`، ولا يوجد أي استدعاء لـ `_outline_entries`.
  - كل العناوين الـ 115 في كتاب Chalcraft بمستوى 2.
  - PDF الكتاب فيه 14 bookmark صحيحة تمامًا.
  - Docling يوثق أن العناوين مسطحة افتراضيًا وأن هناك `HeadingHierarchyOptions(enabled=True)` تستخدم الـ bookmarks والترقيم ([Docling: heading levels](https://docling-project.github.io/docling/usage/heading_levels/)). الخيار موجود في الحزمة المثبتة: `docling/datamodel/service/options.py` في `b2c-worker-1`، ولا يستخدمه الكود (`grep HeadingHierarchy packages/` بلا نتائج).
- **الأثر:** 114 "وحدة" بدل 8، وهرمية مفقودة، وتجربة متعلم بلا فصول، وأسئلة "مجموعات" بلا معنى.
- **السبب الجذري:** بناء المخطط يعتمد على مصدر واحد هش (عناوين الـ layout model) بدل دمج أقوى الإشارات المتاحة: bookmarks، ثم TOC المطبوع، ثم العناوين المرقمة، ثم الـ layout.

**P2. لا يوجد تصنيف لمناطق الكتاب (front matter / body / back matter)، فدخلت الهوامش والمراجع والفهرس كدروس.**
- **الدليل:**
  - القسم 3.2: 17.3% من الكلمات، والدرس الأخير فيه المراجع والفهرس.
  - `_FRONT_MATTER_PREFIXES` (`from_book.py:51-60`) لا يتضمن أي بادئة back matter.
  - منطق البادئة لا يعمل إلا إذا بدأ الكتاب بعنوان front matter (`:78-84`).
- **الأثر:** أسئلة وترجمة وصوت لقوائم مراجع وفهارس، وتكلفة مهدرة تقارب 17–20%، ومحتوى مضلل.
- **السبب الجذري:** لا يوجد classifier للمناطق مبني على معيار، مثل EPUB 3 Structural Semantics: `frontmatter/bodymatter/backmatter`، و`index`، و`endnotes`، و`bibliography` ([W3C Note، 2025-06-12](https://www.w3.org/TR/2025/NOTE-epub-ssv-11-20250612/)).

**P3. الاعتماد النظامي يحل محل المراجعة البشرية بالكامل، والبوابة لا تقيس الصحة.**
- **الدليل:**
  - Android يستدعي `approve-system` تلقائيًا (`MainActivity.java:644, 649`).
  - `review_outline` (`review.py:101-141`) يفحص الهوية والتتابع والصفحات وحضور العنوان فقط.
  - كل الاعتمادات الإنتاجية من نوع `system`.
  - المخططان المعيبان حصلا على `approved`.
- **الأثر:** لا توجد أي نقطة في خط الإنتاج يكتشف فيها أحد أن الكورس 114 وحدة بعناوين مشوهة تنتهي بالفهرس.
- **السبب الجذري:** خُلط بين "السلامة البنيوية" و"الصحة". لا توجد مقارنة مع مرجع (bookmarks أو TOC مطبوع)، ولا فحوص دلالية، ولا عينة بشرية إلزامية.

**P4. عناوين متن تُكتشف كعناوين بالمستوى 1 عبر regex قديم، فيُنسب المحتوى إلى عناوين خاطئة.**
- **الدليل:**
  - `pdf_book.py:33-36` يعترف في تعليقه: "It has the same false positives".
  - يُستخدم في `surya_ocr.py:242` و`openrouter_ocr.py:122` **حتى عند وجود bookmarks**.
  - في القسم 3.3: الدرس "5.2" يحمل محتوى 5.1، والدرس "10.8" يحمل محتوى 10.7، و10.7 فارغ.
- **الأثر:** المتعلم يدرس "Ranking" ويقرأ عن "Sorting"، والأسئلة والملخصات تنسب المحتوى إلى درس خاطئ. هذه أخطر فئة لأنها تمر من كل الفحوص الشكلية.
- **السبب الجذري:** عند وجود bookmarks لا يجب اكتشاف عناوين بالـ heuristic في نفس المستوى. لا يوجد تحقق من اتساق العناوين المكتشفة مع الـ bookmarks.

### عالية (High)

**P5. العناوين لا تُنظف ولا تُطبَّع.** لا يوجد إصلاح لتباعد حروف OCR، ولا لحالة الأحرف، ولا لأسطر متعددة، ولا لترقيم الفصل. 63% من عناوين Chalcraft مشوهة، رغم أن نسخًا نظيفة من نفس العناوين موجودة في الـ bookmarks وفي TOC المطبوع (ص8). العناوين تصل إلى الصوت (`audio.py:49, 406`) وإلى الترجمة.

**P6. حدود الـ bookmark داخل الصفحة غير دقيقة.** الكود يضع عنوان الـ bookmark أعلى الصفحة (`pdf_book.py:168`، و`openrouter_ocr.py` بنفس السلوك). إذا بدأ الفصل في منتصف الصفحة، تُنسب بقية الفصل السابق على تلك الصفحة إلى الدرس التالي. في Chalcraft، 64 من 115 عنوانًا ليست أول كتلة في صفحتها، أي أن البدء من منتصف الصفحة شائع. يجب ربط الـ bookmark بأقرب كتلة عنوان مطابقة نصيًا على الصفحة، أو استخدام إحداثيات `/XYZ` في الـ destination.

**P7. مسار الإنسان لا يتحقق من التغطية ولا من الهوية.** `approve` بنوع `human` لا يستدعي `validate_outline_coverage` (`ingestion.py:337-350`)، و`reviewer_id` نص حر بلا مصادقة. التقرير `eval/reports/phase-2.md:124` نفسه يذكر أن "الاعتماد البشري" نُفّذ بـ `reviewer_id = "phase2-e2e-automated-check"`. أي أن الـ provenance البشري قابل للتزوير، والمخطط "البشري" يمكن أن يُسقط محتوى.

**P8. لا توجد قيود تربوية أو حجمية على الدرس.** المدى من 0 إلى 12,261 كلمة، والمدة الصوتية من دقيقة واحدة إلى قرابة 90 دقيقة (UNVERIFIED كمدة فعلية؛ التقدير على أساس 140 كلمة في الدقيقة). لا يوجد split للدروس الكبيرة عبر المستوى الأدنى من العناوين (8.2 فيه 7 عناوين فرعية)، ولا merge للدروس القصيرة جدًا مع أخيها.

**P9. فحص "title present in source" تحصيل حاصل وفيه خلل regex.** في `review.py:133-134` يُستخدم `re.sub(r"\\s+", " ", …)` داخل raw string، فيطابق `\` متبوعًا بحرف `s` بدل المسافات. تحققت من ذلك محليًا: `'STRUCTURED\nANALYTIC'` لا يتغير. وحتى بعد الإصلاح، العنوان منسوخ من كتلة داخل الـ span، فالفحص لا يمكن أن يفشل في المسار الحتمي.

**P10. مسار no-TOC بالـ LLM ضعيف التصميم ولم يُختبر حيًا.**
- **الدليل:**
  - تقسيم مستقل لكل نافذة ≤ 10 صفحات (`ingest.py:270-321`)، فلا يمكن لقسم أن يعبر حدود النافذة.
  - module لكل قسم بلا هرمية (`from_book.py:276-285`).
  - لا يُستدعى إلا عند صفر عناوين (`from_book.py:105`)، فكتاب فيه 3 عناوين خاطئة لا يحصل على أي fallback.
  - `eval/reports/phase-2.md:52`: "live no-TOC model call and human approval remain unverified".
- لا يوجد golden ولا قياس.

### متوسطة (Medium)

- **P11. معرّفات غير مستقرة.** `module_id` = رقم + slug لعنوان OCR (`from_book.py:157`). الإصلاح أو إعادة التحليل يغيّر كل المعرّفات، فتنقطع روابط الأسئلة والترجمة والصوت. بعد `merge_bodyless_lessons` يبقى المعرف القديم بينما يتغير الترتيب: `m110-…` يأتي في الترتيب 109.
- **P12. `prior_lessons` = كل ما سبق** (`from_book.py:188`). الدرس رقم 114 له 113 متطلبًا. هذا ليس graph متطلبات بل تكرار للترتيب، ويعطي إشارة زائفة لأي منطق تكيفي أو SRS.
- **P13. إصدار السياسة لا يُرفع.** `POLICY_VERSION = "1.0.0"` ثابت مع أن مجموعة الفحوص تغيرت (القسم 2.4). بصمة الدليل لا تكفي لإعادة إنتاج القرار.
- **P14. `toc_source` يوسم خطأ في Docling** (`docling_pdf.py:334`). الـ provenance غير موثوق.
- **P15. الاعتماد غير idempotent.** `approved_at` داخل المفتاح (`ingestion.py:394-402`)، فالمخطط نفسه اعتُمد 4 مرات بأربعة artifacts.
- **P16. ترقيعات تعدّل المخطط داخل مسار الاعتماد** (`merge_bodyless_lessons` و`include_leading_blocks_in_first_lesson`، `ingestion.py:325-326`). المعتمَد ليس المسودة المعروضة، ولا يُحفظ diff بينهما.

### منخفضة (Low)

- **P17.** الاختبارات صغيرة جدًا: 14 اختبارًا اصطناعيًا، كلها خضراء، ولا golden. `docs/03-TESTING.md:59` يذكر snapshot غير موجود.
- **P18.** قائمة البادئات فيها تكرار (`"شكر وتقدير"` مرتين، `from_book.py:58`)، ولا تغطي back matter ولا الإنجليزية الشائعة (Notes، Bibliography، References، Index، Glossary، Appendix\*).
- **P19.** لا يوجد أي مقياس جودة أو تقرير لكل كتاب (عدد الدروس، توزيع الأحجام، نسبة المستبعد) في نتيجة الـ activity. الموجود فقط `module_count` و`lesson_count` (`ingest.py:370-375`).

---

## 5. التصميم المستهدف وفق المعايير الصناعية

### 5.1 المبدأ
المخطط **منتج قابل للقياس**: شجرة بنيوية تُستخرج من عدة إشارات، وتُقارن بمرجع، وتُصنَّف فيها مناطق الكتاب، ثم تُحوَّل إلى دروس بقواعد تربوية صريحة. الاعتماد له مستويان منفصلان: بوابة آلية تفحص الصحة وليس الشكل فقط، ثم مراجعة بشرية حقيقية موثقة الهوية. اليد البشرية لا تُلغى إلا عندما يثبت القياس أن الآلة تكفي، وحتى عندها تبقى عينة تدقيق.

### 5.2 المعمارية: خط من 6 خطوات حتمية، مع LLM كمقترِح فقط

1. **جمع الإشارات (Structure Evidence)** من `ParsedBook` والـ PDF:
   - (a) PDF bookmarks/outline (ISO 32000 Document Outline) مع إحداثيات الـ destination.
   - (b) TOC المطبوع: يُكتشف ويُحلَّل إلى (عنوان، مستوى، رقم صفحة مطبوعة)، ثم يُعاير الفرق بين الصفحة المطبوعة والفعلية.
   - (c) العناوين المرقمة (`1.2.3` و`Chapter N`) من الـ layout.
   - (d) عناوين الـ layout model مع `HeadingHierarchyOptions(enabled=True)` في Docling، أو ما يعادله.
   - (e) خصائص الخط عند توفرها.
2. **التوفيق (Reconciliation):**
   - ترتيب الثقة: bookmarks > TOC مطبوع > عناوين مرقمة > layout.
   - يُربط كل مدخل TOC أو bookmark بكتلة عنوان محددة عبر fuzzy match بعد تطبيع (casefold، وإزالة تباعد OCR، وNFKC)، داخل نافذة الصفحة المتوقعة. هذا يحل P6.
   - العنوان النهائي يأتي من المصدر الأنظف (bookmark أو TOC)، والموضع من كتلة الـ layout. هذا يحل P5.
   - عنوان layout لا يطابق أي مدخل في مرجع موثوق لا يُرفَّع إلى مستوى module أو lesson. يبقى عنوانًا فرعيًا داخل الدرس. هذا يحل P4.
3. **تصنيف المناطق** حسب مفردات EPUB SSV: `cover/titlepage/copyright-page/toc/dedication/acknowledgments/foreword/preface/introduction/chapter/part/conclusion/appendix/glossary/endnotes/bibliography/index`.
   - المفاتيح الحتمية: عناوين الـ bookmark والـ TOC، وأنماط النص (كثافة أرقام الصفحات في الفهرس، ونمط "Author, Title. City: Publisher, Year" في المراجع، وأرقام الهوامش).
   - Foreword وPreface وAppendix: سياسة قابلة للإعداد، ومعروضة على المراجع.
   - المستبعد يُحفظ بصيغة `excluded_ranges[{zone, start_block, end_block, reason}]`، أي نطاقات وليس بادئة فقط، مع تغطية 100% = مدرج + مستبعد صراحة. هذا يحل P2.
4. **بناء الشجرة ثم الدروس (Lesson planning):**
   - Part/Chapter → module، وSection → lesson.
   - تقسيم الدرس الذي يتجاوز الحد الأعلى عند المستوى التالي من العناوين، ودمج الدرس الأقل من الحد الأدنى مع أخيه في نفس الفصل. الحدود قابلة للضبط، مثل 600–4,000 كلمة ❓ ويحددها المالك.
   - مقدمة الفصل تصبح درسًا مستقلًا بعنوان "مقدمة الفصل N"، أو ملحقًا معلنًا بالدرس الأول، ولا تختبئ تحت عنوان القسم الأول.
   - العنوان المعروض يحمل سياقه (`"Conclusion" → "Chapter 3 · Conclusion"`).
5. **معرّفات مستقرة:** `lesson_id` = hash لـ (`book_id`، `start_block` المرساة، ومسار العنوان المطبَّع)، أو مخطط `ch03.s02` المشتق من الترقيم الأصلي. لا يُبنى المعرف على slug عنوان OCR. عند إعادة التوليد يوجد mapping صريح من المعرّفات القديمة إلى الجديدة.
6. **المتطلبات السابقة:** الافتراضي هو الترتيب فقط (`order`)، و`prior_lessons` تبقى فارغة ما لم يكن هناك دليل (مراجع صريحة في النص مثل "as discussed in Chapter 2"، أو graph من مرحلة KG)، مع evidence لكل edge.

**دور الـ LLM:** model-agnostic، structured output بـ JSON Schema، و`temperature=0`. يُستدعى في ثلاث حالات فقط:
- (i) كتاب بلا bookmarks ولا TOC مطبوع.
- (ii) فشل التوفيق في جزء محدد.
- (iii) اقتراح عناوين بديلة لعنوان OCR مشوه **مقيّد بأن يكون نفس الكلمات** (edit distance بعد إزالة المسافات = 0).

في الحالة (i):
- يعمل بنافذة منزلقة مع تداخل.
- يقترح **مرشحي حدود** فقط، ويقبلها الكود الحتمي إن انطبقت على كتل حقيقية.
- ثم تُطبَّق نفس قواعد الشجرة والحجم.
- كل مخرجاته `llm_inferred` و`requires_review`، وتمر حتمًا بإنسان.

يجب ألا يُرسل كتاب إلى مزود خارجي إلا بسياسة موجودة، وهي `B2C_OUTLINE_REMOTE_EVAL_DATA`.

### 5.3 بوابات الجودة

**حتمية (تبقى، وتُضاف إليها):**
- التتابع، وعدم التداخل، وتغطية 100% للكتل = مدرج + مستبعد صراحة.
- الصفحات تطابق الكتل.
- عدم وجود درس بلا متن، ومعرّفات فريدة.
- **جديد:**
  - لا عنوان يحتوي `\n` أو تباعد OCR (حرفان منفردان متتاليان) أو حالة أحرف شاذة.
  - حجم كل درس داخل الحدود، أو استثناء موثق.
  - كل درس داخل bodymatter.
  - لا يوجد module بدرس واحد إلا إذا كان الفصل فعلًا بلا أقسام.
  - عدد الوحدات بين 0.5× و1.5× عدد مدخلات المستوى الأعلى في المرجع.
- **مقارنة مع مرجع داخلي** (bookmarks أو TOC مطبوع) عند وجوده. البوابة تفشل إذا:
  - title-F1 < 0.95، أو
  - boundary-F1 بتسامح ±1 كتلة < 0.95، أو
  - level accuracy < 0.95.

  هذه مقاييس مسابقة ICDAR Book Structure Extraction لاستخراج الـ ToC (عنوان/مستوى/رابط/مدخل كامل) ([Doucet et al., ICDAR 2009–2013](https://pageperso.univ-lr.fr/antoine.doucet/StructureExtraction/)).
- **المطابقة الذاتية:** يُعاد بناء TOC من المخطط ويُقارن بـ TOC المطبوع المستخرج.

**دلالية (بنموذج حَكَم مستقل، تُقاس معايرتها مقابل حكم بشري):**
- **اتساق العنوان والمحتوى:** هل يصف العنوان محتوى الـ span؟ هذا يكشف P4 مباشرة.
- **تصنيف المنطقة:** هل الـ span تعليمي أم مراجع/فهرس/هوامش؟
- **اكتمال الدرس:** لا يبدأ في منتصف فكرة ولا ينتهي في منتصفها، ويُقاس بنموذج segmentation أو بحكم.
- القرار الدلالي **لا يعتمد وحده**. يرفع العلامة إلى المراجع البشري، ويُقاس الحكم نفسه بـ precision/recall على مجموعة ذهبية.

### 5.4 نقاط HITL (إلزامية حتى إثبات العكس)
- **مراجعة المخطط بشريًا قبل الفهرسة،** عبر واجهة تعرض:
  - الشجرة بجانب الـ bookmarks والـ TOC المطبوع.
  - المستبعد مع السبب.
  - تحذيرات البوابات.
  - معاينة أول وآخر فقرة من كل درس.
  - أدوات: دمج، وتقسيم عند كتلة، وإعادة تسمية، وتغيير منطقة.
  - حفظ diff بين المسودة والمعتمد.
- **تنفيذ ذلك بنمط Temporal Approval:** الـ workflow ينتظر signal أو update فيه هوية المراجع والسبب والوقت، مع timeout وتصعيد ([Temporal Approval Pattern](https://docs.temporal.io/design-patterns/approval)).
- **هوية المراجع موثقة** بـ authN (OIDC أو token لكل مراجع)، وليست نصًا حرًا. `system` يبقى نوعًا منفصلًا لا يكفي وحده لإطلاق التوليد الإنتاجي.
- **سياسة auto-approve مستقبلية** لا تُفعَّل إلا بعد أن تحقق البوابة الآلية على golden set precision ≥ 0.98 لقرار "صحيح"، مع عينة تدقيق بشرية 10% بشكل دائم.

### 5.5 مقاييس التقييم والأهداف

قيم ❓ تقترح بداية، ويقرها المالك.

| المقياس | التعريف | الهدف |
|---|---|---|
| Module (chapter) F1 | مطابقة الوحدات لفصول المرجع (عنوان + بداية ±1 كتلة) | ≥ 0.98 |
| Lesson boundary F1 | حدود الدروس مقابل الأقسام الذهبية، بتسامح ±1 كتلة | ≥ 0.95 ❓ |
| Pk / WindowDiff | خطأ التقسيم على مستوى الكتل ([Pevzner & Hearst 2002](https://www.cl.cam.ac.uk/teaching/1516/R216/Pevzner.pdf)، متاح في NLTK) | ≤ 0.05 ❓ |
| Title exact-match بعد التطبيع | مقابل الـ bookmarks أو TOC أو الذهبي | ≥ 0.97 |
| Garbled-title rate | عناوين فيها تباعد OCR أو أسطر متعددة | 0% |
| Zone classification F1 | front/body/back على مستوى الكتلة | ≥ 0.98؛ تسرب back matter إلى الدروس = 0 كلمة |
| Content-under-wrong-title | دروس يبدأ محتواها قبل قسمها أو بعده بأكثر من كتلة واحدة | 0 |
| Lesson size compliance | نسبة الدروس داخل الحدود | ≥ 95%، والباقي باستثناء موثق |
| ID stability | نسبة المعرّفات التي تبقى بعد إعادة تحليل نفس الكتاب | 100% |
| Human edit rate | تعديلات المراجع لكل 100 درس | يُتتبع؛ هدف الاتجاه ≤ 5 |

### 5.6 الاختبار
- **Golden set مرخص من 12–20 كتابًا** (جزء من `eval/golden`). يغطي:
  - PDF رقمي مع bookmarks وبدونها.
  - مسح ضوئي إنجليزي.
  - كتب بأجزاء أو فصول أو أقسام بثلاثة مستويات.
  - كتاب بلا TOC.
  - كتاب يتضمن هوامش ومراجع وفهرس وملاحق.

  لكل كتاب شجرة ذهبية موسومة بشريًا (اثنان يوسمان، ويُقاس Cohen's κ على تصنيف المنطقة، والهدف ≥ 0.8). الكتابان الحاليان (Chalcraft وSAT) يدخلان فورًا، وbookmarks كل منهما نقطة بداية للوسم.
- **Snapshot/golden tests** لمخرجات الخط الحتمي، أي T-OUT-01 فعلًا.
- **Property-based tests** (Hypothesis) لثوابت التغطية والتتابع والمعرّفات.
- **Negative tests:** regex false positive، وTOC كعنوان، وعنوان مكرر، وbookmark في منتصف الصفحة، وفهرس بلا عنوان.
- **Regression gate في CI:** فشل إذا هبط أي مقياس في 5.5 عن الـ baseline.
- **تقرير لكل كتاب** يُحفظ كـ artifact بجانب المسودة: المقاييس، والتحذيرات، وما استُبعد.

---

## 6. تحليل الفجوة وخطة الانتقال

### ما يُحتفظ به
- العقد العام (`CourseOutline` و`LessonSpan` القائم على block IDs).
- `validate_outline_coverage` كفكرة.
- `stage_key` والتخزين المحتوى-معنون.
- فصل `human` عن `system` في `OutlineApprovalProvenance`.
- رفض إرسال الكتب لمزود خارجي افتراضيًا.

### ما يُعاد كتابته
- `outline_from_book`: إلى خط إشارات → توفيق → مناطق → شجرة → دروس.
- `review_outline`: إلى بوابة صحة مقابل مرجع، بإصدار سياسة حقيقي.
- مسار `approve`: Temporal approval مع authN وتحقق تغطية موحد للمسارين.
- `_infer_sections`.

### ما يُحذف
- `include_leading_blocks_in_first_lesson` و`merge_bodyless_lessons` داخل الاعتماد. تُنقل منطقيًا إلى البناء، والمعتمد يجب أن يساوي المعروض.
- regex `_HEADING` القديم من مسارات OCR.
- `prior_lessons` الخطية.
- استدعاء `approve-system` التلقائي من Android.

### الخطوات بالترتيب

1. **تجميد فوري** (يوم واحد، تغيير سياسة):
   - إيقاف `approve-system` كطريق لإطلاق الأسئلة والترجمة والصوت في الإنتاج.
   - وسم المخططين الحاليين بأنهما غير صالحين للإنتاج.
   - لا يُحذف شيء، لأن الـ artifacts أدلة.
2. **Golden set وأداة قياس** (`eval/outline/`):
   - وسم الكتابين الحاليين أولًا. الـ bookmarks الأصلية تعطي 80% من الوسم، ويبقى تصحيح الحدود داخل الصفحة والمناطق.
   - كتابة `score_outline.py` بالمقاييس في 5.5.
   - تسجيل baseline للكود الحالي. المتوقع من القياس أعلاه: Module F1 ≈ 0 لـ Chalcraft، وTitle match ≈ 0.82 لـ SAT.
3. **إصلاح الإشارات في ingest** (اعتماد على المرحلة 01):
   - Docling يقرأ الـ bookmarks ويفعّل `HeadingHierarchyOptions`.
   - إصلاح وسم `toc_source`.
   - إزالة `_HEADING` من Surya وOpenRouter عند وجود bookmarks.
   - حفظ إحداثيات العنوان ليمكن ربط الـ bookmark بالموضع داخل الصفحة.
   - رفع إصدار الـ parser.
4. **بناء خط المخطط الجديد** (`b2c_outline` v2):
   - التوفيق، والمناطق (EPUB SSV)، والشجرة، وقواعد الحجم، والمعرّفات المستقرة.
   - تقرير جودة لكل كتاب.
   - رفع `OUTLINE_STAGE_VERSION`.
   - توسيع العقد: `excluded_ranges[]` بدل `excluded_prefix_*`، و`zone`، و`display_title` بجانب `source_title`، و`title_source`. إصدار schema جديد (1.2.0) مع migration للقديم.
5. **بوابة الاعتماد الجديدة:**
   - فحوص حتمية + مقارنة بالمرجع + حَكَم دلالي (اختياري، يرفع علامات فقط).
   - `POLICY_VERSION` يُرفع مع كل تغيير، ويُحفظ ملف الدليل نفسه وليس hash فقط.
6. **HITL:**
   - Temporal approval (signal أو update) وauthN للمراجعين.
   - واجهة مراجعة: Android أو web خفيف. **هذا شرط لإغلاق المرحلة.**
   - المسار البشري يستدعي نفس `validate_outline_coverage`.
   - مفتاح الاعتماد يُحسب على المحتوى فقط دون `approved_at`.
7. **مسار no-TOC بالـ LLM:** بعد 1–6. نوافذ متداخلة، ومقترِح حدود فقط، ومقيَّم على كتابين على الأقل من الـ golden بلا TOC.
8. **إعادة توليد الكتابين:**
   - المخطط الجديد، ثم مراجعة بشرية، ثم إعادة الفهرسة والأسئلة والترجمة والصوت.
   - خريطة معرّفات قديم→جديد لما يمكن إنقاذه. عمليًا لا شيء تقريبًا لـ Chalcraft، لأن كل الحدود تتغير.

### الاعتماديات
- **المرحلة 01 (ingest/OCR):** المستويات والإحداثيات والـ bookmarks في `ParsedBook`. الخطوة 3 شرط لـ 4.
- **المرحلة 03 (indexing):** الـ chunks تُبنى على الدروس، ولا تُعاد الفهرسة قبل اعتماد المخطط الجديد.
- **مراحل الأسئلة والترجمة والصوت:** تعتمد على `lesson_id` و`title`، وتحتاج سياسة لعرض `display_title` وترجمته (المصطلحات).
- **KG:** مصدر محتمل لـ `prior_lessons` الحقيقية لاحقًا.

### المخاطر
- **كلفة الوسم البشري للـ golden:** للتخفيف، يُبدأ من الـ bookmarks، ويُقتصر على 12 كتابًا.
- **تغيير المعرّفات يكسر artifacts قديمة:** للتخفيف، خريطة معرّفات، وتوليد جديد بدل ترقيع.
- **HeadingHierarchy في Docling قد يخطئ المستويات** (توثيقه يحذر من ذلك): للتخفيف، الـ bookmarks والـ TOC المطبوع لهما الأولوية، وHeadingHierarchy إشارة واحدة فقط.
- **إغراء العودة إلى الاعتماد النظامي لتسريع العمل:** للتخفيف، شرط في CI والـ API: التوليد الإنتاجي يرفض `approved_outline` بلا `review_type = human` وهوية موثقة، إلى أن تُعتمد سياسة auto-approve رسميًا بالأرقام.

---

## 7. معايير القبول (متى تُعتبر المرحلة منتهية)

تُعتبر المرحلة **Done** فقط عند تحقق كل ما يلي، مع تقرير في `eval/reports/` وموافقة المالك:

1. **Golden set** من ≥ 12 كتابًا مرخصًا، موسومة شجرةً ومناطقَ، مع κ ≥ 0.8 بين واسمين على تصنيف المناطق. يشمل Chalcraft وSAT وكتابين على الأقل بلا bookmarks وكتابًا بلا TOC.
2. **على الـ golden:**
   - Module/chapter F1 ≥ 0.98.
   - Lesson boundary F1 (±1 كتلة) ≥ 0.95.
   - Title exact-match بعد التطبيع ≥ 0.97.
   - Garbled-title = 0.
   - **Content-under-wrong-title = 0.**
   - Zone F1 ≥ 0.98.
   - **0 كلمة من endnotes أو bibliography أو index داخل أي درس.**
3. **حالات محددة على الكتب الحالية:**
   - Chalcraft يُنتج 8 modules: Introduction، والفصول 1–6، وConclusion، وأقسامها دروس بعناوين نظيفة. تُستبعد Copyright وContents وAcknowledgments وNotes وBibliography وIndex مع سبب.
   - SAT: "5.1 Sorting" يبدأ في ص129 ويحمل محتواه، و"10.7 Decision Matrix" غير فارغ، ولا توجد modules وهمية، ولا يُعتبر TOC أو Figures درسًا.
4. ≥ 95% من الدروس داخل حدود الحجم المعتمدة، والباقي باستثناء موثق في التقرير.
5. **ثبات المعرّفات:** إعادة تحليل نفس الملف تعطي 100% نفس `lesson_id`. وتغيير نسخة الـ parser ينتج خريطة معرّفات صريحة.
6. **الاعتماد:**
   - كل `approved-outline` إنتاجي فيه `review_type = human` بهوية موثقة (authN) وdiff محفوظ.
   - المسار البشري يمر بنفس فحوص التغطية.
   - `POLICY_VERSION` يتغير مع كل تغيير في الفحوص (اختبار يفشل إذا تغيرت قائمة الفحوص دون رفع الإصدار).
   - الاعتماد idempotent على المحتوى.
7. **اختبارات:**
   - golden snapshots لكل كتاب في الـ golden.
   - property-based tests للثوابت.
   - negative tests لكل مشكلة من P1–P16.
   - CI يفشل عند هبوط أي مقياس عن الـ baseline.
8. **تقرير جودة لكل كتاب** يُحفظ مع المسودة ويُعرض في واجهة المراجعة.
9. **مسار no-TOC:** على كتابين بلا بنية في الـ golden، boundary F1 ≥ 0.85 ❓، ويمر دائمًا بمراجعة بشرية.

---

### ملحق: سكربتات القياس (للقراءة فقط، ونُفذت داخل `b2c-worker-1`)
`/tmp/claude-0/-root/c9ec20dc-64ad-42ec-857d-86ecef50c15c/scratchpad/a2.py` و`a3.py` و`a5.py` و`a8.py` و`b1.py` و`b2.py` و`b4.py` و`b5.py`. هي مؤقتة في scratchpad الجلسة ويمكن نقلها إلى `eval/outline/` كبداية لأداة القياس.

### المصادر الخارجية
- Docling: heading levels، `HeadingHierarchyOptions` (معطّل افتراضيًا): https://docling-project.github.io/docling/usage/heading_levels/ ؛ نقاش المستويات المسطحة: https://github.com/docling-project/docling/discussions/1170 (تم الاطلاع 2026-10-24)
- ICDAR Book Structure Extraction Competition (2009/2011/2013)، Doucet وآخرون، مقاييس title/level/link/entry: https://pageperso.univ-lr.fr/antoine.doucet/StructureExtraction/
- W3C EPUB 3 Structural Semantics Vocabulary 1.1 (Note، 2025-06-12): https://www.w3.org/TR/2025/NOTE-epub-ssv-11-20250612/
- Pevzner & Hearst (2002)، "A Critique and Improvement of an Evaluation Metric for Text Segmentation"، Computational Linguistics 28(1)، WindowDiff، وPk لـ Beeferman et al. 1999: https://www.cl.cam.ac.uk/teaching/1516/R216/Pevzner.pdf ؛ NLTK: https://www.nltk.org/api/nltk.metrics.segmentation.html
- Temporal Approval Pattern: https://docs.temporal.io/design-patterns/approval
