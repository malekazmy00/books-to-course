# تدقيق المرحلة 05: التأليف (Synthesis): مسودات دروس مستندة إلى المصدر مع citations وتحقق

- **التاريخ:** 2026-10-24
- **الفرع:** `hardening`
- **النطاق:** `packages/synthesis`، والجزء الخاص بالمسودة في `packages/pedagogy/src/b2c_pedagogy/lesson_enrichment.py`، و`b2c_core.provenance_checks`، وطريقة الربط في `packages/orchestration`، و`eval/suites/phase5`، والـ artifacts الفعلية في MinIO وPostgreSQL.
- **المنهج:** قراءة فقط. لم يُعدَّل أي كود أو إعداد أو بيانات. قيست الـ artifacts عبر `docker exec -i b2c-worker-1 python -` باستخدام boto3 وpsycopg للقراءة فقط، ولم يُرسل أي نص من الكتب إلى خدمة خارجية. أي حكم دلالي في هذا التقرير هو حكم المدقق (مراجع واحد، وليس gold متعدد المراجعين)، وهو موسوم بذلك.
- **المبدأ:** كما في كتالوج أخطاء الترجمة (`outputs/translation-review-2026-10-24/TRANSLATION-ERROR-CATALOGUE.md`، حيث كان 57.4% من المقبول خاطئًا)، لا يُعتبر «passed» أو «final» دليلًا على الصحة.

---

## 1. غرض المرحلة وعقدها

### 1.1 الغرض
تأخذ هذه المرحلة نطاق درس معتمدًا من outline (spine span) مع مقاطع الكتاب الأساسي، ومعها شواهد من الكتب المساندة. وتنتج **نص درس تعليمي باللغة الإنجليزية**، كل جملة فيه مسندة إلى مصدر يمكن التحقق منه. هذا النص هو أساس كل ما يأتي بعده: الترجمة العربية، والتعليق الصوتي، والملخص، والمصطلحات، والأسئلة. المرجع هو `docs/01-ARCHITECTURE.md` الخطوة 5: «إنشاء أهداف الدرس والشرح والأسئلة من سياق مسترجع، مع منع النشر إذا غابت الإحالات أو فشل التحقق».

### 1.2 العقد الموثق
| البند | المصدر | النص |
|---|---|---|
| المدخل | `docs/02-CONTRACTS.md:49,51` | `CourseOutline` معتمد، و`StructuredChunk[]`، وLightRAG/ConceptEdge (أُلغي لاحقًا في Phase 4 بقرار NO-GO) |
| المخرج | `packages/contracts/src/b2c_contracts/models.py:231-243` | `LessonDraft{lesson_id, version, sentences[LessonSentence{sid,text_en,citations[chunk_id]≥1,kind}], contradictions[], faithfulness{}}` |
| شرط التسليم L5←L6 | `docs/02-CONTRACTS.md:52` | «كل جملة ليها citation ≥ 1 والـ citation موجود فعلًا، **وfaithfulness ≥ العتبة**»، وعند الفشل: «إعادة توليد ← مراجعة» |
| الاختبارات | `docs/03-TESTING.md:80-87` | T-SYN-01 رفض الجملة بلا citation؛ T-SYN-02 Ragas faithfulness ≥ 0.90 وcitation support ≥ 95% على عينة بشرية من 30 جملة؛ T-SYN-03 كشف التكرار F1 ≥ 0.8؛ T-SYN-04 كشف تناقض مزروع؛ T-SYN-05 عدم الاختلاق عند غياب التغطية |
| خصائص النجاح | `docs/01-ARCHITECTURE.md:28-29` | تغطية كل درس لمصدره الأساسي، مع إظهار مساهمات الكتب المساندة منفصلة، وعدم إصدار جملة بلا citation |

### 1.3 ما تحتاجه المراحل اللاحقة فعلًا
- **الترجمة (L7):** جمل قائمة بذاتها، بلا ضمائر إشارة معلقة ولا رؤوس صفحات ولا كود خام، ومعرفات ثابتة (`sid`)، وأرقام ومصطلحات صحيحة. كل خطأ هنا يتضاعف في العربية.
- **الصوت (L8):** نص مكتوب للسمع: تسلسل منطقي، وانتقالات، وأمثلة، وأرقام قابلة للنطق، وبلا `>>> pmf.Prob(2)`.
- **التربية (L6):** جمل تغطي أهداف التعلم كاملة، لأن الملخص والمصطلحات والأسئلة مبنية على `sid` الخاصة بالمسودة.
- **الإسناد:** مرجع على مستوى الفقرة أو العبارة (span)، وليس على مستوى chunk يتجاوز 1500 حرف، حتى يستطيع المراجع البشري التحقق بسرعة.

---

## 2. التصميم كما هو منفذ فعلًا

### 2.1 أين يوجد الكود
- `packages/synthesis` **حزمة فارغة تقريبًا**. يقول `__init__.py:1-5` «Placeholder... Implemented in Phase 5»، و`validation.py` (21 سطرًا) يعيد فقط تصدير `validate_citations` و`detect_explicit_polarity_conflicts` من `b2c_core.provenance_checks`.
- توليد المسودة الفعلي موجود في **حزمة pedagogy**، داخل `lesson_enrichment.py:681-986` (الدالة `generate_lesson_enrichments`). تولّد هذه الدالة المسودة والملخص والمصطلحات والـ MCQ **في استدعاء LLM واحد**.
- الاستدعاء من orchestration يتم داخل نشاط توليد الأسئلة (`packages/orchestration/src/b2c_orchestration/qa.py:448-477`)، وفقط إذا كان `include_lesson_enrichment=True`. القيمة الافتراضية **False** في `qa.py:104` و`packages/api/src/b2c_api/schemas.py:48`. وفي تطبيق Android يظهر الخيار كـ«إثراء الدروس: مصطلحات وأسئلة مراجعة إضافية» اختياري ومعطل افتراضيًا (`android/operator-app/.../MainActivity.java:49`).

### 2.2 التدفق الفعلي لكل درس
1. يحدد `_derive_scopes` (`qa_generation.py:252-306`) مقاطع الـ spine داخل نطاق الدرس فقط. **لا تدخل أي مقاطع مساندة** في نطاق الدرس (`qa_generation.py:295-298`).
2. يُرسل **كل** نص المقاطع دفعة واحدة في prompt واحد (`lesson_enrichment.py:758-820`) دون retrieval ودون حد لعدد التوكنات. المخرج محدود بـ `max_tokens=4096`.
3. الـ prompt (`lesson_enrichment.py:784-814`) يطلب «4-8 factual lesson sentences» تكون فيها كل جملة «one independently citable source fact». ويطلب في الاستدعاء نفسه ملخصًا ومصطلحات وMCQ، مع تعليمات تصميم الأسئلة.
4. schema مقيدة: `citations` من نوع `Literal` بمعرفات chunks النطاق (`:763-768`)، وطول الجملة من 12 إلى 600 حرف (`:39`)، وعدد الجمل من 3 إلى 20 (`:772`).
5. تصحيحات لاحقة لأخطاء النموذج: تحويل الفهارس من one-based إلى zero-based عند الاشتباه (`:447-471`)، وتصحيح `kind` تلقائيًا (`:579-582`).
6. تحقق حتمي: كل citation ضمن النطاق، واتساق الدور، و`validate_citations` (`provenance_checks.py:31-65`)، و`validate_lesson_assets` (`pedagogy/validation.py:98-152`) الذي يتحقق فقط من وجود الـ SID.
7. تحقق دلالي: **استدعاء واحد لنفس الـ gateway ونفس النموذج** (`:913-927`) يحكم بقيمة boolean على كل الأصول دفعة واحدة. العتبة `support_rate ≥ 0.90` (`:931-933`). العناصر غير المدعومة تحت العتبة **لا تُحذف ولا تُعلَّم**، بل تُنشر.
8. يُكتب `faithfulness = {"automated_support_judge": rate, "citation_support_rate": 1.0}` (`:973-976`). القيمة **1.0 ثابتة في الكود ولا تُقاس**.
9. «التناقضات» تأتي من `detect_explicit_polarity_conflicts` (`provenance_checks.py:71-118`)، ولا تكتشف إلا مقطعين متطابقين في الكلمات ويختلفان في أداة نفي فقط.

### 2.3 أين يختلف التوثيق عن الكود
| # | التوثيق | الكود والواقع |
|---|---|---|
| D1 | `02-CONTRACTS:52`: faithfulness ≥ عتبة، وإعادة توليد ثم مراجعة | لا توجد مراجعة بشرية ولا طابور. العتبة 0.90 من حكم ذاتي، و`citation_support_rate` ثابت على 1.0 (`lesson_enrichment.py:975`). في 21 من 21 artifact الحقل `faithfulness` فارغ `{}` (القسم 3) |
| D2 | `03-TESTING:10,84`: Ragas | Ragas غير موجود في `pyproject.toml` ولا في `uv.lock`، ولا يُستورد في أي حزمة |
| D3 | `03-TESTING` القاعدة 3: العتبات مجمدة في `eval/thresholds.yaml` | الملف **غير موجود** |
| D4 | `03-TESTING` القاعدة 5: التقييمات الاحتمالية تُعاد 3 مرات | حكم واحد بـ temperature=0 (`:913-927`) |
| D5 | `01-ARCHITECTURE:20,28`: إضافة شواهد الكتاب المساند مع الإسناد وإظهار مساهماته منفصلة | نطاق الدرس spine فقط (`qa_generation.py:295-298`). الجملة من نوع `supplementary` لا يمكن أن تمر لأن citation المساند خارج النطاق، فتُرفض في `lesson_enrichment.py:574-575`. هذا المسار **ميت** |
| D6 | T-SYN-03: كشف التكرار | غير منفذ |
| D7 | T-SYN-04: كشف التناقض | heuristic يعتمد على تطابق الكلمات مع اختلاف النفي فقط. في 21 artifact عدد التناقضات المسجلة 0 |
| D8 | `synthesis/__init__.py`: L5 يستورد فقط contracts وcore وgateway | الـ L5 الفعلي داخل pedagogy (L6) ومدمج مع إنتاج الأسئلة |
| D9 | docstring الدالة `validate_lesson_assets`: «coverage» | لا يوجد أي فحص تغطية (`pedagogy/validation.py:98-152`) |
| D10 | `00-DECISIONS.md:190` (D-026): «accept Phase 5... 20-item artifact passed» | الـ artifact المقصود (`phase5-seatweaving-procedure-template-v2`) مسودته نص OCR منسوخ حرفيًا مع رؤوس صفحات، وأسئلته مولدة بقالب، وليست من النموذج (القسم 3.4) |
| D11 | `01-ARCHITECTURE` الخطوة 5: الشرح يُنشأ من سياق مسترجع ثم يُترجم ويُنطق | الصوت الفعلي يقرأ **نص الكتاب المترجم** (`audio.py:509-528`، `lesson_source_text`)، ولا يقرأ `LessonDraft`. ومن المسودة لا يدخل الصوت إلا الملخص والأسئلة (`audio.py:531-548`) |

---

## 3. الجودة المقاسة

### 3.1 كيف قيست
1. فُحصت كل كائنات bucket `artifacts`: كل ملفات `course-qa.json` (33 ملفًا) وكل ملفات `lesson-enrichment.json` (21 ملفًا تحت `phase5-enrichment-canary`).
2. لكل مسودة حُسب: عدد الجمل، وعدد الـ chunks المستشهد بها، ونسبة طول المسودة إلى طول نص الـ chunks، وحقول `faithfulness` و`automated_evaluation`، وتوزيع Bloom.
3. حُسب تداخل معجمي (recall للكلمات المحتوائية) بين كل جملة والـ chunk المستشهد به، من نص المقاطع في `b2c_indexed_chunks`. هذا المقياس **ضعيف**: أعطى إنذارات كاذبة على الصيغ الرياضية، فاستُخدم للفرز فقط.
4. **مراجعة يدوية دلالية** قام بها المدقق لست مسودات مختلفة، بمقارنتها جملةً بجملة مع نص المصدر. ونص مصدر ThinkStats لدرس PMF أُخذ من `think-stats-2e:chunk:fb63d53933fa87861d2f`.
5. لتقدير أحجام الدروس في الكتاب الحقيقي (b2c-demo): رُبطت spans الـ outline المعتمد (`b2c-demo/approve-outline/97f28cec…/approved-outline.json`) بالـ chunks حسب ترتيب `page:index` في block_id. هذا **تقدير تقريبي**، وهو UNVERIFIED على مستوى الدرس الواحد.

### 3.2 هل وصلت المرحلة إلى المنتج؟ لا.
| المقياس | النتيجة |
|---|---|
| ملفات `course-qa.json` في MinIO | 33 |
| منها يحتوي `lesson_assets` غير فارغة | **0 من 33** |
| تشغيلات المنتج للكتاب الحقيقي `b2c-demo` | 4 تشغيلات: `failed` ×2 و`paused` ×2، ولا يوجد أي `final` |
| checkpoints الإثراء `_system/qa-checkpoints/*/enrichment/` | **0** |
| artifacts مسودة الدرس الموجودة | 21، وكلها canary يدوية خارج مسار المنتج |

**النتيجة:** لم تنتج المرحلة أي `LessonDraft` واحد في مسار المنتج. «الكورس» الذي يُترجم ويُنطق الآن هو نص الكتاب نفسه، وتُضاف إليه أسئلة QA (القسم 2.3، D11).

### 3.3 الـ artifacts الـ 21 (canary)
| المقياس | القيمة |
|---|---|
| الدروس المختلفة | 6: `m01.l01` و`m01.l08` (Lane، فصل الاحتمالات)، و`ch03.pmf` و`ch03.class-size-paradox` (ThinkStats)، و`seatweaving.j017` و`seatweaving.steps2-7` |
| تكرار | 14 artifact لـ tidx01 هي فعليًا 7 مسودات، كل منها محفوظة مرتين بنفس النص والطول |
| `draft.faithfulness` غير فارغ | **0 من 21** |
| `automated_evaluation.source_support_rate` موجود | **0 من 21**. لم يمر أي artifact بالحكم الدلالي الخاص بمسار المنتج |
| عدد الجمل لكل درس | من 5 إلى 13 (المتوسط في المسودات المولدة 6.5) |
| citations لكل جملة | 1 بالضبط في 100% من الجمل، وعلى مستوى chunk كامل (متوسطه 1500 إلى 2000 حرف) |
| `kind` | كله `primary`. لم تظهر أي جملة `supplementary` أو `connective` |
| `contradictions` | 0 في كل الـ artifacts |
| نسبة طول المسودة إلى المصدر (tidx01) | **من 0.14 إلى 0.24**: الدرس يحتفظ بحوالي خُمس نص المصدر |
| يمر بقاعدة Bloom الحالية (≥ 80% apply/analyze) | **2 من 21** فقط، وهما الاثنان المبنيان بقالب أو بإعادة بناء. أي أن 19 artifact ستفشل عند إعادة التحقق في `lesson_enrichment.py:727` |

### 3.4 المراجعة اليدوية (حكم المدقق، 6 مسودات)
| المسودة | الجمل | العيوب المرصودة |
|---|---|---|
| `m01.l01` (`171648bb`) | 6 | s001 وs002 نص وصفي عن الكتاب («The introductory section discusses…»)، أي محتوى غير تعليمي من فهرس الفصل. الدرس أسقط هدف التعلم 3 الموجود في المصدر («Determine whether the frequentist or subjective approach is better suited»)، وأسقط فكرة الاحتمالات nondogmatic (0 و1). نقطتا ملخص من 3 فيهما ادعاءات غير موجودة في الجمل المستشهد بها، مثل «less suitable for scientific inference» وهو تعميم زائد. مصطلح «frequentist approach» له `first_sid=s004` مع أن s004 لا تحتوي الكلمة |
| `m01.l08` (`fc941c69`) | 8 | الجمل نفسها صحيحة. نقطة ملخص واحدة تعتمد على «0.95» غير الموجود في أي جملة. 4 أسئلة موسومة `apply` هي استرجاع مباشر لرقم مذكور حرفيًا في الجملة المستشهد بها |
| `ch03.pmf` (ThinkStats، Nemotron) | 8 | **سلسلة منطقية مكسورة:** حُذفت خطوة `pmf.Mult(2, 0.5)` الموجودة في المصدر، فصار s005 يعطي Prob=0.6 ثم s006 يعطي `Total() 0.9`. المتعلم سيستنتج قيمة خاطئة. نقطة الملخص 2 تذكر `Mult` غير الموجودة في الدرس. السؤال q001 يعتمد على قائمة `[1, 2, 2, 3, 5]` غير موجودة في أي جملة من الدرس. وكود خام مثل `>>> pmf.Prob(2) 0.4` سيُترجم ويُنطق |
| `ch03.class-size-paradox` | 8 | **خطأ معنى في الملخص:** «calculated by weighting each class size by its frequency»، والصحيح كما في s003 والمصدر هو الضرب في حجم الفصل x. s002 وs008 جداول وكود (`d = { 7: 8, 12: 8, … }`) لا تصلح للتعليق الصوتي |
| `seatweaving.steps2-7` (Nemotron) | 8 | جمل بإشارات معلقة لا يُفهم مرجعها: s001 «This operation is repeated…» وs004 «The actual weaving begins with **this step**» بعد حذف رقم الخطوة. غابت الخطوات 1 و7 وعنوان الخطوة 3 عن درس عنوانه «steps 2-7». نقطتا ملخص من 4 فيهما محتوى غير موجود في الجمل |
| `seatweaving.steps2-7` procedure-template-v2 (الدليل الذي أُغلقت به Phase 5) | 13 | **ليس درسًا:** نص OCR منسوخ حرفيًا بأطوال من 124 إلى 358 حرفًا للجملة، يبدأ من منتصف جملة («12 SEAT WEAVING second peg.»)، ويحتوي رؤوس صفحات («CANING; THE SEVEN STEPS 13») و«Step 2.» معلقة. الملخص نسخ حرفية لثلاث جمل من الدرس. الـ 20 سؤالًا كلها من قالب واحد: «Which numbered step should the learner follow?» بخيارات «Step N»، وموسومة `apply` مع أنها استرجاع. بعض الأسئلة لا يمكن حلها لأن النص غامض، مثل «It may be done entirely by hand…». المصطلحات 0 |

**الإجمالي في المسودات الخمس المولدة بالنموذج (38 جملة و16 نقطة ملخص):**
- جمل غير صحيحة أو مضللة في سياقها: 1 من 38 (2.6%)، وهي `ch03.pmf:s006`.
- جمل غير صالحة كنص درس قابل للترجمة والنطق (محتوى وصفي عن الكتاب، أو إشارة معلقة، أو كود وجداول خام): **6 من 38 (حوالي 16%)**.
- نقاط ملخص غير مستلزمة من الجمل المستشهد بها: **7 من 16 (حوالي 44%)**، منها خطأ معنى صريح واحد.
- إسقاط محتوى جوهري موجود في المصدر: 4 من 5 مسودات.
- كل هذه العيوب **مرت بكل البوابات الحتمية**. لا يوجد حكم دلالي محفوظ لأي منها.

### 3.5 أحجام الدروس الحقيقية (b2c-demo، تقديري)
114 درسًا و507 chunks و194,676 توكن. الوسيط حوالي 1,338 توكن للدرس، وp90 حوالي 3,519، والحد الأعلى حوالي **23,505 توكن (52 chunk)**، والحد الأدنى **14 توكن**. التصميم الحالي يفرض من 3 إلى 8 جمل مهما كان حجم الدرس. النتيجة المتوقعة: تغطية بضعة في المئة فقط في الدروس الكبيرة، وفي الدروس شبه الفارغة إما اختلاق أو فشل في الـ schema (`min_length=3`). هذا UNVERIFIED على مستوى الدرس، لأنه لم يُشغَّل أي درس من b2c-demo.

### 3.6 الأدلة التشغيلية من التقارير
`eval/reports/phase-5.md` يسجل محاولات live فشلت بـ `LLMOutputTruncated` عند 4096 توكن، و`InvalidLLMOutput`، وHTTP 429/404/422. ويسجل أيضًا 2/3 و2/3 للحل مع السياق وبدونه، ثم 20/20 مع السياق و20/20 بدونه بسبب تلميح من طول الخيارات. البوابة النهائية «passed» تحققت فقط على الـ artifact المبني بالقالب.

---

## 4. المشكلات مرتبة حسب الخطورة

### حرجة (Critical)

**C1. المرحلة غير موجودة فعليًا في مسار المنتج، والكورس يقرأ الكتاب المترجم بدل درس مؤلَّف.**
- *الدليل:* 0 من 33 ملف course-qa فيه `lesson_assets`، و`include_lesson_enrichment=False` افتراضيًا (`qa.py:104`، `schemas.py:48`)، والصوت يقرأ `lesson_source_text` من blocks الكتاب (`audio.py:509-528`).
- *الأثر:* المنتج لا «يحول كتابًا إلى كورس» كما يعد التصميم، بل يسرد الكتاب بالعربية ويضيف أسئلة. كل وعود الإسناد في `01-ARCHITECTURE:29` لا تنطبق على النص المسموع.
- *السبب الجذري:* المسودة صُممت كـ«إثراء اختياري» داخل نشاط الأسئلة، وليست مرحلة مستقلة يعتمد عليها ما بعدها. لا يوجد قرار مالك موثق عن شكل المنتج: سرد الكتاب أم درس مؤلَّف.

**C2. لا يوجد تحقق دلالي مستقل ومعاير. «faithfulness» إما فارغ أو ثابت في الكود.**
- *الدليل:* `citation_support_rate: 1.0` ثابت في `lesson_enrichment.py:975`. الحكم من نفس النموذج المولّد عبر نفس الـ gateway (`worker.py:111-112`). حكم boolean واحد لكل الأصول دفعة واحدة (`:913-927`). العتبة 0.90 تسمح بنشر عنصر غير مدعوم من كل 10 دون حذفه (`:931-933`). 0 من 21 artifact فيها قياس. لا Ragas ولا gold ولا negative controls دلالية (`03-TESTING` القاعدة 1).
- *الأثر:* نفس نمط فشل الترجمة: البوابات تتحقق من الشكل فقط. في العينة اليدوية مرت نسبة 44% من نقاط الملخص غير المستلزمة، وخطأ معنى، وسلسلة حسابية مكسورة.
- *السبب الجذري:* الحكم الذاتي معروف بتحيزه لمخرجات النموذج نفسه (Panickssery et al., 2024, arXiv:2404.13076؛ Zheng et al., 2023, arXiv:2306.05685)، وفحص الـ SID لا يثبت الاستلزام.

**C3. الدليل الذي أُغلقت به Phase 5 (D-026) لا يصلح.**
- *الدليل:* `phase5-seatweaving-procedure-template-v2/.../lesson-enrichment.json`: المسودة نسخ OCR حرفي مع رؤوس صفحات، والأسئلة من قالب «Step N»، والمصطلحات 0 (القسم 3.4). و`00-DECISIONS.md:190` يعتمد على هذا الـ artifact.
- *الأثر:* قرار قبول المرحلة مبني على artifact لا يمثل المنتج. أي عمل لاحق يفترض أن Phase 5 «مغلقة» يقوم على أساس غير صحيح.
- *السبب الجذري:* تحسين لتجاوز البوابة (Goodhart): صُمم المولّد ليمر بمقاييس الأسئلة (توازن الطول والموضع)، لا ليعلّم.

### عالية (High)

**H1. التغطية غير مُقاسة وغير ممكنة بالتصميم الحالي.**
- *الدليل:* «Return 4-8 factual lesson sentences» (`:785`)، ونسبة الطول من 0.14 إلى 0.24، وإسقاط محتوى جوهري في 4 من 5 مسودات، ودروس حجمها يصل إلى 23k توكن. لا فحص تغطية (D9).
- *الأثر:* المتعلم يفقد أهداف تعلم كاملة، والأسئلة تُبنى على شظايا.
- *السبب الجذري:* لا توجد قائمة «وحدات معرفة» أو أهداف يُقاس عليها recall، والطول ثابت ولا يتغير مع حجم المصدر.

**H2. المسودة قائمة حقائق مصممة لخدمة الأسئلة، وليست درسًا.**
- *الدليل:* الـ prompt يطلب «one independently citable source fact» لكل جملة، و«choose a target fact whose answer has at least three distinct, similarly shaped source facts» (`:795-802`). وفي الاستدعاء نفسه تعليمات MCQ متناقضة: «Do not return options» ثم «Keep every option within 2 words…» (`:796,803,812-814`).
- *الأثر:* لا مقدمة ولا شرح ولا مثال محلول ولا انتقالات. وجمل بإشارات معلقة («this step») تنكسر عند الترجمة والنطق.
- *السبب الجذري:* دمج L5 وL6 في استدعاء واحد (D8). احتياجات الأسئلة تشكّل النص بدل أن يحدده تصميم تعليمي (أهداف، ثم شرح، ثم تطبيق).

**H3. الإسناد على مستوى chunk فقط، مع citation واحدة لكل جملة.**
- *الدليل:* 100% من الجمل بـ citation واحدة على chunk من 1500 إلى 2000 حرف، والعقد لا يحمل span أو offset (`models.py:234`).
- *الأثر:* التحقق البشري بطيء، والتحقق الآلي ضعيف لأن السياق الواسع يرفع الاستلزام الكاذب. ولا يمكن ربط الجملة العربية بعبارة المصدر بدقة.
- *السبب الجذري:* العقد لا يمثل الاقتباس. المعيار المعتمد هو الإسناد على مستوى الجملة أو العبارة (ALCE, Gao et al., 2023, arXiv:2305.14627؛ AIS, Rashkin et al., 2021, arXiv:2112.12870).

**H4. الملخص والمصطلحات لا يُتحقق من استلزامها.**
- *الدليل:* `validate_lesson_assets` يتحقق من وجود الـ SID فقط (`pedagogy/validation.py:115-126`). 7 من 16 نقطة ملخص غير مستلزمة، ومنها خطأ معنى.
- *الأثر:* الملخص يُنطق في الصوت (`audio.py:531-533`)، فيصل الخطأ إلى المتعلم مباشرة.

**H5. الكتب المساندة والتناقضات خارج التصميم عمليًا.**
- *الدليل:* D5 وD7. في 21 artifact عدد الجمل المساندة 0 وعدد التناقضات 0. ومقارنة الكتب (T-KG-05) فشلت في Phase 4.
- *الأثر:* الوعد بـ«إظهار مساهمات الكتب المساندة منفصلة» غير متحقق، ومسار `supplementary` ميت.

**H6. مُدخل ملوث: OCR ورؤوس صفحات وصيغ مشوهة.**
- *الدليل:* مقاطع Lane فيها `Base%Rates%` و`(|) = (|)()` ورقم الصفحة 212 و213 (القسم 3.4)، ومقاطع seat weaving فيها رؤوس صفحات تنتقل إلى المسودة.
- *الأثر:* إما نسخ القمامة إلى الدرس، أو «إصلاحها» من معرفة النموذج دون إسناد، كما حدث في `m01.l08:s008` حيث كُتبت الصيغة الكاملة التي لا يحتويها الـ chunk بشكل مقروء.
- *السبب الجذري:* اعتماد على مرحلتي ingest وchunking دون عقد تنظيف (dependency).

### متوسطة (Medium)

- **M1. لا يوجد حد لحجم السياق ولا تقسيم للدرس.** الدرس كله في prompt واحد، والمخرج 4096 توكن. هذا يفسر حالات `LLMOutputTruncated` المسجلة. وصفر retrieval، رغم أن التوثيق يقول «سياق مسترجع».
- **M2. ترقيع أخطاء النموذج بدل منعها:** `_normalize_proven_one_based_references` (`:447-471`) وتصحيح `kind` (`:579-582`) يخفيان عدم الامتثال للـ schema بدل قياسه.
- **M3. مجموعة eval عبارة عن 28 سكربت canary لمرة واحدة (4032 سطرًا)**، كثير منها مرتبط بنموذج معين. سكربت التدقيق يحقن مصدرًا من `/tmp/phase5-live-src` (`audit_live_enrichment_canary.py:11-12`)، فلا يمكن إعادة إنتاجه من المستودع. لا dataset ولا تقرير مقاييس مجمع.
- **M4. اختبارات الوحدة (28 اختبارًا في `tests/unit/pedagogy/test_lesson_enrichment.py`) تغطي الأسئلة في الأساس.** لا يوجد أي اختبار لجودة المسودة نفسها (إشارة معلقة، محتوى وصفي عن الكتاب، تغطية، استلزام الملخص). والحكم الدلالي مختبر بـ gateway وهمي فقط.
- **M5. artifacts قديمة غير متوافقة مع البوابات الحالية:** 19 من 21 تفشل قاعدة Bloom الحالية. لا يوجد إصدار للبوابات مرتبط بالـ artifact.
- **M6. لا يوجد طابور مراجعة بشرية ولا حالة `needs_review`.** `LessonEnrichment.status` هي `Literal["final"]` فقط (`:125`).

### منخفضة (Low)
- **L1.** `max_length=600` للجملة يسمح بفقرات كاملة (القالب أنتج جملًا بطول 358 حرفًا).
- **L2.** نص إنجليزي «Explanation: » داخل مسار صوت عربي (`audio.py:546`). هذا مسار لاحق، لكن سببه أن المرحلة لا تنتج حقولًا مخصصة للعرض.
- **L3.** `LessonDraft.version` دائمًا 1، ولا توجد سلسلة إصدارات للتحرير والمراجعة.
- **L4.** `prompt_version` لا يشمل نسخة البوابات والمدققات.

---

## 5. التصميم المستهدف وفق الممارسات المعتمدة

### 5.1 المبادئ
1. **فصل التوليد عن التحقق، وفصل المحقق عن المولّد.** المحقق من عائلة نموذج مختلفة، أو نموذج تحقق متخصص، ومعاير على gold بشري.
2. **التخطيط قبل الكتابة:** المصدر يُفكك أولًا إلى وحدات معرفة (Knowledge Units أو nuggets) وأهداف تعلم. التغطية تُقاس على هذه الوحدات، وفق منهج nugget evaluation (TREC 2024 RAG Track، Pradeep et al., 2024, arXiv:2411.09607) وFActScore (Min et al., 2023, arXiv:2305.14251).
3. **إسناد على مستوى العبارة:** كل جملة ترتبط باقتباس حرفي بـ char offsets، ويُتحقق آليًا أن الاقتباس موجود فعلًا (ALCE؛ LongCite, Zhang et al., 2024, arXiv:2409.02897).
4. **Fail-closed مع مسار مراجعة:** ما لا يجتاز يذهب إلى `needs_review` بدل أن يُنشر أو يُسقط بصمت.
5. **التصميم التعليمي أولًا:** أهداف بصيغة Bloom المنقحة (Anderson & Krathwohl, 2001)، ثم شرح ومثال وتطبيق وخلاصة (Merrill, *First Principles of Instruction*, 2002)، مع مبادئ الوسائط لمسار صوتي: segmenting وsignaling وcoherence (Mayer, *Multimedia Learning*، الطبعة الثالثة 2020).

### 5.2 المعمارية (حزمة `b2c_synthesis` مستقلة، ونشاط Temporal مستقل)

```
ApprovedLessonSpan + clean spine chunks (+ retrieved supplementary evidence)
  └─ S0 Source normalization gate  (no headers/footers/page numbers; formulas flagged)
  └─ S1 KU extraction  → KnowledgeUnit[{ku_id, claim, quote_span(chunk_id,start,end), type: core|example|procedure|meta, objective_ids}]
  └─ S2 Lesson plan    → objectives[] + sections[{purpose: intro|explain|worked_example|procedure|recap, ku_ids}]
                          (length scales with core KU count; long lessons split by section)
  └─ S3 Section drafting (constrained JSON, per section, bounded context)
        sentence{sid, text_en, kind, evidence:[{chunk_id, start, end, quote_sha}], audio_safe:bool}
  └─ S4 Verification
        D (deterministic): citation resolve; quote exact-match; numbers/entities ⊆ cited quotes;
            no meta-text / headers / dangling deixis / raw code; length/readability; KU coverage map
        S (semantic, independent): per-sentence NLI/fact-check vs cited quotes
            (MiniCheck/AlignScore-class checker + cross-family LLM judge, 3 samples, majority);
            summary/glossary entailment vs lesson sentences; cross-book contradiction NLI
        P (pedagogical rubric judge, cross-family; G-Eval-style rubric): coherence, objective alignment, audio suitability
  └─ S5 Targeted repair (only failing sentences, ≤2 rounds) → else needs_review
  └─ S6 Human review queue (SME) → approved LessonDraft v2 (versioned, signed-off)
  └─ Outputs to: pedagogy (summary/glossary/MCQ from approved sids), translation (sentence units), audio (script)
```

### 5.3 فئات الأدوات والنماذج (دون تحديد نموذج بعينه)
| الوظيفة | الفئة | أمثلة معتمدة صناعيًا وأكاديميًا |
|---|---|---|
| التوليد | LLM تعليمات قوي مع structured outputs و**constrained decoding** | JSON-Schema guided decoding في vLLM أو llama.cpp (XGrammar أو Outlines) |
| التحقق من الاستلزام لكل جملة | نموذج fact-checking صغير ومعاير | MiniCheck (Tang et al., 2024, arXiv:2404.10774)، وAlignScore (Zha et al., 2023, arXiv:2305.16739)، وSummaC (Laban et al., 2022, arXiv:2111.09525) |
| حكم LLM | **من عائلة مختلفة** عن المولّد، مع rubric وعينات متعددة | G-Eval (Liu et al., 2023, arXiv:2303.16634). الاحتياط من التحيز الذاتي (arXiv:2404.13076) |
| التحقق الذاتي قبل الإرسال | Chain-of-Verification | Dhuliawala et al., 2023, arXiv:2309.11495 |
| مقاييس RAG | faithfulness وcontext recall | Ragas (Es et al., 2023, arXiv:2309.15217)، كإطار قياس فقط وليس كبوابة وحيدة |
| harness التقييم والانحدار | تقييم على dataset ثابت داخل CI | promptfoo، أو Inspect (UK AISI)، أو DeepEval، مع حفظ النتائج في Langfuse، وهو موجود أصلًا في الـ stack |
| المراجعة البشرية | واجهة تعليقات على span مع تصنيف أخطاء | تصنيف بأسلوب MQM مكيف للمحتوى (accuracy/omission/addition/terminology/style)، وأخذ العينات وفق ISO 2859-1 (AQL) |

### 5.4 البوابات
**حتمية (D)، وكلها blocking:**
1. كل citation يرجع إلى chunk في النطاق المعتمد، والاقتباس `quote` موجود حرفيًا بعد normalization عند `[start,end]`.
2. الأرقام والكيانات المسماة والرموز في الجملة موجودة في الاقتباسات المستشهد بها. إن كانت قيمة محسوبة، يجب أن تكون العملية ومدخلاتها مسندة، وتُعلَّم بـ `derived=true`.
3. صفر رؤوس أو تذييلات صفحات، وصفر أرقام صفحات، وصفر نص وصفي عن الكتاب («this book/chapter/section discusses»).
4. صفر إشارات معلقة في أول الجملة («This/That/It/These») دون مرجع داخل نفس القسم (lint + قائمة).
5. لا كود خام ولا جداول خام في `audio_safe` sentences. الكود يُشرح بالكلام، والنص الحرفي يُحفظ في حقل عرض منفصل.
6. كل KU من نوع `core` لها جملة تغطيها أو استثناء مبرر.
7. طول الجملة لا يتجاوز 40 كلمة، وعدد الجمل يتناسب مع عدد الـ core KUs.

**دلالية (S):**
1. لكل جملة: verdict ∈ {supported, partially, unsupported, contradicted} من checker مستقل وحكم LLM من عائلة مختلفة. التجميع بالأغلبية على 3 عينات. أي `contradicted` يحجب النشر، و`unsupported` يرسل الجملة إلى repair ثم review.
2. الملخص والمصطلحات: الاستلزام من جمل الدرس المستشهد بها. أي عنصر يفشل يُحذف أو يُصلح، ولا يوجد «90% كفاية».
3. تناقض بين الكتب: NLI بين ادعاءات الـ spine والشواهد المساندة المسترجعة يُسجل في `contradictions` مع الاقتباسين.
4. Rubric تعليمي من 1 إلى 5 (وضوح، تسلسل، مواءمة مع الأهداف، ملاءمة للصوت). أي درجة أقل من 3 تذهب إلى review.

### 5.5 نقاط الإنسان في الحلقة
1. **اعتماد الأهداف وخطة الدرس (S2) لكل كتاب جديد:** عينة أول 3 دروس، ثم استثناءات فقط.
2. **طابور مراجعة SME:** كل درس فيه علم S أو P، بالإضافة إلى عينة عشوائية من الدروس المقبولة آليًا (أخذ عينات وفق ISO 2859-1 مستوى II، AQL 1.0 للأخطاء الكبرى) لقياس معدل القبول الخاطئ.
3. **معايرة المحققين:** gold بشري بمراجعين اثنين على الأقل، ويُقاس الاتفاق بـ Cohen's κ أو Krippendorff's α. لا تُعتمد بوابة آلية قبل أن يتفق المحقق مع البشر.
4. **التحرير يُنتج إصدارًا جديدًا** (`version+1`) مع `approved_by` و`approved_at`، كما في `TermbaseEntry.approved_by`.

### 5.6 مقاييس التقييم والعتبات المستهدفة (تُجمّد في `eval/thresholds.yaml`)
| المقياس | التعريف | العتبة |
|---|---|---|
| Citation resolution / quote exact-match | آلي | 100% |
| Sentence faithfulness (gold بشري) | نسبة الجمل supported | ≥ 98%، و`contradicted` = 0 |
| Critical error rate بعد البوابة | أخطاء معنى أو أرقام في عينة SME من المقبول آليًا | ≤ 1%، والحد الأعلى لفترة ثقة Wilson 95% ≤ 3% |
| Summary/glossary entailment | gold بشري | ≥ 98% |
| Core KU coverage (nugget recall) | مقابل قائمة KU بشرية | ≥ 90% لكل درس، ووسيط ≥ 95% |
| Meta/header/dangling/raw-code في نص الصوت | lint + عينة بشرية | 0 |
| حساسية المحقق للأخطاء المزروعة | نفي، تبديل رقم، تبديل كيان، إضافة غير مسندة، إسقاط شرط، تبديل citation | recall ≥ 95% لكل نوع، والدقة على الجمل السليمة ≥ 90% |
| اتفاق المحقق مع البشر | Cohen's κ على verdict الجملة | ≥ 0.70 |
| الاستقرار | 3 تشغيلات، والانحراف في faithfulness | ≤ 2 نقطة مئوية |
| Rubric تعليمي (SME) | المتوسط من 1 إلى 5 | ≥ 4.0، ولا درس أقل من 3 |
| معدل الإحالة إلى المراجعة البشرية | تشغيلي | يُقاس ويُتابع (هدف أولي ≤ 25%)، ولا يُستخدم لتخفيف العتبات |

### 5.7 الاختبار
- **Unit:** كل validator حتمي مع حالات موجبة وسالبة: header وdeixis وmeta وأرقام وquote offsets.
- **Contract:** `LessonDraft v2` من `pydantic` في الاتجاهين، والمستهلكون (pedagogy وtranslation وaudio) يقرؤونه (consumer-driven كما في `03-TESTING`).
- **Golden eval set:** 30 درسًا على الأقل من 3 كتب ونطاقات على الأقل: إحصاء رياضي (Lane/ThinkStats)، وإجرائي (Seat Weaving)، وإنساني نظري (كتاب غرامشي وSAT). لكل درس قائمة KU بشرية، ومسودة مرجعية مع حكم جملة بجملة، و**نسخ مفسدة عمدًا** (negative controls) لكل نوع خطأ في 5.6.
- **Regression في CI:** harness واحد مبني على dataset يستبدل الـ 28 سكربت، ويحفظ المقاييس لكل `prompt_version` و`gate_version` و`model_id`. أي تراجع فوق الهامش يُفشل البناء.
- **E2E:** درس واحد من b2c-demo يمر من الـ outline حتى المسودة المعتمدة ثم الترجمة ثم MP3، مع provenance كاملة من الجملة العربية حتى الاقتباس الإنجليزي.

---

## 6. تحليل الفجوات وخطة الانتقال

### 6.1 ما يُحتفظ به
- تقييد citations بـ `Literal` بمعرفات النطاق (`lesson_enrichment.py:763-768`). فكرة جيدة تُعمم على quote IDs.
- مبدأ fail-closed، والـ checkpoint والاستئناف (`qa.py:331-371`)، وإصدار الـ prompt، و`stage_key` والـ provenance.
- `validate_citations` كفحص بنيوي أولي.
- فكرة الـ blind solver وتوازن الخيارات (تبقى في pedagogy).

### 6.2 ما يُعاد كتابته
- منطق المسودة كله ينتقل من `pedagogy/lesson_enrichment.py` إلى `packages/synthesis` ويُقسم إلى: KU extraction، ثم planning، ثم drafting، ثم verification، ثم repair.
- عقد `LessonDraft` v2: objectives وsections وevidence spans و`verification` لكل جملة و`status ∈ {draft, auto_verified, needs_review, approved}` و`approved_by`. مع ترحيل `schema_version`.
- البوابة الدلالية: محقق مستقل ومعاير بدل الحكم الذاتي.
- نشاط Temporal مستقل `synthesize-lessons`، يكون مفعّلًا افتراضيًا، وتعتمد عليه الترجمة والصوت.

### 6.3 ما يُحذف
- `citation_support_rate: 1.0` الثابت (`:975`).
- `_normalize_proven_one_based_references` وتصحيح `kind` التلقائي، فيُستبدلان بمعرفات ثابتة وconstrained decoding.
- تعليمات MCQ داخل prompt المسودة (`:795-814`).
- `detect_explicit_polarity_conflicts` كـ«كاشف تناقض». يُحتفظ به كاختبار سلبي فقط.
- الـ 28 سكربت canary، بعد أرشفة أدلتها في التقارير.
- اعتبار artifact القالب `procedure-template-v2` دليل قبول.

### 6.4 الخطوات المرتبة
| # | الخطوة | يعتمد على | مخرج يمكن التحقق منه |
|---|---|---|---|
| 0 | **قرار المالك:** هل الصوت يقرأ درسًا مؤلَّفًا، أم الكتاب المترجم مع إثراء، أم الاثنين كمسارين؟ وإلغاء الاعتماد على D-026 كإغلاق لـ Phase 5 | — | سجل قرار جديد في `00-DECISIONS.md` |
| 1 | دليل annotation وgold set بـ 30 درسًا مع negative controls (TDD حسب `03-TESTING` القاعدة 2) | حقوق المصادر (golden manifest) | dataset مُصدَّر ومجمّد، وκ بين المراجعين ≥ 0.7 |
| 2 | عقد `LessonDraft` v2 و`KnowledgeUnit` وcontract tests | contracts | اختبارات عقد ناجحة، وترحيل موثق |
| 3 | بوابة تنظيف المُدخل (S0): كشف رؤوس الصفحات وأرقامها والصيغ المشوهة | **ingest/chunking** | 0 رؤوس في chunks الـ gold، وإلا يُرفض الدرس بسبب واضح |
| 4 | المحققات الحتمية (5.4-D) مع اختبارات وحدة | 2 | كشف 100% للعيوب الحتمية المزروعة |
| 5 | المحقق الدلالي المستقل ومعايرته على gold | 1 و4، وgateway متعدد النماذج | recall ≥ 95% على الأخطاء المزروعة، وκ ≥ 0.70 |
| 6 | KU extraction والتخطيط (S1 وS2) | 2 و3، و**outline** (spans سليمة واستبعاد front matter وفق D-027) | coverage map لكل درس gold |
| 7 | الكتابة لكل قسم بـ constrained decoding وsplitting حسب الحجم | 6 | لا `LLMOutputTruncated` في gold، وكل البوابات D ناجحة |
| 8 | repair الموجّه وطابور المراجعة البشرية (حالة `needs_review` وواجهة في التطبيق) | 4 و5، وAPI/Android | دورة مراجعة كاملة على 5 دروس |
| 9 | الشواهد المساندة: retrieval عبر الكتب + NLI للتناقض + جمل `supplementary` | **indexing** (فشل T-KG-05 يجب إصلاحه) | T-SYN-04 وT-SYN-05 على gold |
| 10 | ربط orchestration: نشاط مستقل، والترجمة تستهلك المسودة المعتمدة، والصوت يقرأ النص المؤلَّف | **translation وaudio** | E2E درس واحد بـ provenance كاملة |
| 11 | pedagogy تبني الملخص والمصطلحات والأسئلة من الـ sids المعتمدة فقط، مع تحقق استلزام | 10 | 0 نقاط ملخص غير مستلزمة في gold |
| 12 | harness انحدار في CI، و`eval/thresholds.yaml`، ثم إعادة تشغيل كتاب كامل وعينة SME وفق AQL | كل ما سبق | تقرير Phase 5 جديد بأرقام القسم 7 |

### 6.5 الاعتماديات على المراحل الأخرى
- **Ingest/OCR:** جودة النص والصيغ والرؤوس (H6).
- **Outline:** دروس بحجم 14 توكن أو 23k توكن تحتاج حدودًا أصح، واستبعاد front matter (D-027).
- **Indexing/KG:** retrieval مساند عبر الكتب.
- **llm_gateway:** توجيه متعدد النماذج (مولّد ومحقق من عائلتين مختلفتين)، وحفظ raw completions دائمًا، وتسجيل الاستخدام.
- **Translation:** وحدة الترجمة تصبح جملة من المسودة المعتمدة مع اقتباسها، ما يساعد QE أيضًا.
- **Audio:** يستهلك نصًا `audio_safe`.

### 6.6 المخاطر
| الخطر | التخفيف |
|---|---|
| تكلفة وزمن التحقق المتعدد على CPU محلي | محقق صغير متخصص كخط أول، وLLM judge للحالات الحدية فقط، وcache حسب `stage_key` |
| تحيز المحقق أو عدم معايرته | gold وnegative controls وκ قبل الاعتماد، وتدوير العائلات |
| عبء annotation البشري | البدء بـ 30 درسًا، وتصنيف أخطاء موحد، وأداة span-comment |
| الحقوق: النسخ الحرفي الطويل (كما في القالب) | سياسة: صياغة بالكلمات الخاصة واقتباسات قصيرة محدودة، تُفحص آليًا بنسبة التطابق الحرفي |
| «تحسين للبوابة» مرة أخرى (Goodhart) | عينة SME عشوائية دائمة من المقبول آليًا، ومقياس القبول الخاطئ هو البوابة العليا |
| تضاعف الأخطاء في العربية | لا تُرسل للترجمة إلا جمل `approved` أو `auto_verified` |

---

## 7. معايير القبول (متى تُعتبر المرحلة «منتهية»)

تُعتبر المرحلة مقبولة فقط إذا تحققت كل البنود التالية معًا، ووقّع المالك على التقرير:

1. **في المنتج:** تشغيل كامل لكتاب حقيقي (b2c-demo أو كتاب بديل) ينتج `LessonDraft` v2 لـ 100% من الدروس غير التمهيدية. كل درس حالته `approved` أو `auto_verified`، و0 دروس `failed` دون سبب موثق. وتستهلك الترجمة والصوت هذه المسودات (أو قرار مالك صريح بغير ذلك، الخطوة 0).
2. **الإسناد:** 100% citations قابلة للحل، و100% اقتباسات مطابقة حرفيًا لـ offsets، و0 جمل بلا evidence.
3. **الصحة على gold (30 درسًا على الأقل، 3 نطاقات):** faithfulness الجملة ≥ 98%، و`contradicted` = 0، واستلزام الملخص والمصطلحات ≥ 98%.
4. **الصحة على عينة SME من المقبول آليًا** (ISO 2859-1، مستوى II، AQL 1.0): معدل الأخطاء الكبرى والحرجة ≤ 1%، والحد الأعلى لـ Wilson 95% ≤ 3%. هذا هو المقياس الذي فشلت فيه الترجمة (57.4%)، ويجب قياسه هنا قبل أي ادعاء.
5. **التغطية:** recall للـ core KU ≥ 90% لكل درس gold، والوسيط ≥ 95%.
6. **الملاءمة للصوت:** 0 رؤوس صفحات، و0 نص وصفي عن الكتاب، و0 إشارات معلقة، و0 كود خام في نص الصوت (lint على كل الكتاب وعينة بشرية).
7. **المحققات:** recall ≥ 95% لكل نوع خطأ مزروع، وκ ≥ 0.70 مع البشر، والمحقق من عائلة مختلفة عن المولّد. كل ذلك موثق مع `model_id` والإصدار.
8. **الاستقرار:** 3 تشغيلات على gold بانحراف ≤ 2 نقطة مئوية في faithfulness والتغطية.
9. **التعليم:** متوسط rubric الـ SME ≥ 4.0 من 5، ولا درس أقل من 3.
10. **الحوكمة:** `eval/thresholds.yaml` موجود ومجمد، وharness الانحدار في CI أخضر، وT-SYN-01..05 كلها PASS بأدلة artifact. والتقرير يذكر نسخ النماذج والـ prompts والبوابات، ويعلن صراحة الحالات التي لم تُقَس.

---

### ملحق: مفاتيح الأدلة
- مسودات canary: `artifacts/phase5-live-tidx01-20261004/phase5-enrichment-canary/{171648bb…,fc941c69…}/lesson-enrichment.json`، و`artifacts/phase5-thinkstats2-local-canary/phase5-enrichment-canary/{4934f701…,b7dd700f…}/lesson-enrichment.json`، و`artifacts/phase5-seatweaving-sequence-canary/phase5-enrichment-canary/6b1424e0…/lesson-enrichment.json`، و`artifacts/phase5-seatweaving-procedure-template-v2/phase5-enrichment-canary/0a1dd1fa…/lesson-enrichment.json`.
- مصدر ThinkStats لدرس PMF: `b2c_indexed_chunks.chunk_id = think-stats-2e:chunk:fb63d53933fa87861d2f`.
- مصادر Lane: `lane-ch5-probability:chunk:{3e821501…,efde0f15…,36b6881e…,64bb3bfc…,e56edfd6…,6f07f837…,b3601d84…}`.
- Course QA للمنتج: `artifacts/b2c-demo/generate-course-qa/{38d9a570…,9143aefc…,ae3be1a5…,f0e8bbb4…}/course-qa.json`، وكلها `lesson_assets=[]`.
- Outline المعتمد: `artifacts/b2c-demo/approve-outline/97f28cec…/approved-outline.json` (114 درسًا).
