# بحث المعرفة وطبقة المفاهيم (EN) — تشغيل حقيقي على الكتابين — 2026-10-25

المرجع: `docs/proposals/2026-10-25-learning-companion.md` (§2، §6، §10–§13)، والقرارات D-029 وD-032..D-036، والعقود `docs/02-CONTRACTS.md` §11 (SCHEMA 1.7.0).
الفرع: `worktree-agent-a00f286c44c1139b6` (من `hardening` عند `8f68ed4`). آخر commit شُغّلت منه الأرقام: `7584023` (شجرة نظيفة؛ لذلك قُبلت المخرجات في سجل الترقية).

> هذه بوابات **سياسة مؤقتة** لمرحلتين جديدتين، وليست بوابة مرحلة من `docs/05-PHASES.md`. لا يُعلن هنا اجتياز أي Gate رسمي.

## 1. ما نُفّذ فعلًا (مُتحقَّق)

### 1.1 المدخلات (قراءة فقط من MinIO)
صُدّرت المخرجات من `b2c-worker-1` بـ boto3 (قراءة فقط) إلى `/root/.cache/b2c-knowledge/minio` (خارج git).

| الكتاب | الكتاب المحلَّل | المخطط | القاموس |
|---|---|---|---|
| SAT (`book-main`) | `ingest-parse-pdf/e40a641e…` (776 block، 677 صفحة) | `approve-outline/8a4a7621…` (v1، 81 درسًا) | `book-termbase-approved/84a8e5d7…` (263 مصطلحًا/اختصارًا معتمدًا) |
| Gramsci (`book-70c88ae9b2ba9ed6f61e813c`) | `ingest-parse-pdf/469d6f0f…` (2620 block، 415 صفحة) | `approve-outline/97f28cec…` (v1، 114 درسًا) | **لا قاموس** في MinIO ولا في Postgres ⇒ مرشحو `extract_candidates` من `translation-units/4723451d…` (أعلى 80) |

**هوية كتاب Gramsci** (استُخرجت آليًا من صفحات العنوان، وكل قيمة مُسندة حرفيًا إلى نص الواجهة): *From Subordination to Revolution: A Gramscian Theory of Popular Mobilization*، John Chalcraft، University of California Press، 2025، ISBN ‏9780520416819 / 9780520416826 / 9780520416840. وتؤكدها OpenAlex (`W4414449588`) وOpen Library (`OL44542054W`) وWikidata (`Q112751171`).

### 1.2 الأوامر
```
uv run python scripts/knowledge/run_knowledge.py --name sat --parsed …/parsed-book.json \
  --outline …/approved-outline.json --termbase …/termbase.json --work-dir /root/.cache/b2c-knowledge/run
uv run python scripts/knowledge/run_knowledge.py --name gramsci --parsed … --outline … \
  --units …/units.json --work-dir /root/.cache/b2c-knowledge/run
uv run python scripts/knowledge/injection_probe.py --work-dir /root/.cache/b2c-knowledge/run
```
السكربت يشغّل **نفس كود الأنشطة** (`KnowledgeActivities._research/_concepts`) بمخزن ملفات محلي، وسجل ترقية في الذاكرة (نفس `PromotionService` و`GateResult`)، وcache محلي لاستجابات النماذج والجلب. الملخصات: `/root/.cache/b2c-knowledge/run/summary-{sat,gramsci}.json`.

النماذج (D-029، OpenRouter، مفتاح خارج المستودع): المستخرج `deepseek/deepseek-v4-flash-0731` (`reasoning_effort=none`)، والمحكّم المستقل `google/gemini-3.1-flash-lite` (عائلة مختلفة؛ يرفض العقد وorchestration العائلة نفسها). كل الاستدعاءات عبر `b2c_llm_gateway.complete_text`.
**الكلفة:** ‏0.2126$ مسجلة في الـ cache لـ 1041 استدعاءً ناجحًا، وحوالي 0.10$ لتشغيل أول فاشل (انظر §4) ⇒ نحو **0.31$** إجمالًا. الزمن (SAT، بلا cache): البحث 197 ث، والمفاهيم 341 ث.

### 1.3 الاختبارات
`make check` (rc=0): ruff نظيف، وimport-linter **8 عقود kept / 0 broken**، و`tests/contract` **86 passed**، و`tests/unit` **1815 passed / 0 failed / 0 skipped**. الاختبارات الجديدة (68): `tests/contract/test_knowledge_concepts.py` (17)، و`tests/unit/research/` (39)، و`tests/unit/concepts/` (8)، و`tests/unit/orchestration/test_knowledge_activities.py` (4، منها workflow على خادم Temporal الاختباري).

## 2. القياسات

### 2.1 بحث المعرفة (`knowledge-research`)
| المقياس | SAT | Gramsci |
|---|---|---|
| المصادر المقبولة (الدرجة 1 / 2 / 3) | 13 (10 / 3 / 0) | 15 (14 / 1 / 0) |
| المزوّدون | OpenAlex 6، Wikipedia 5، Open Library 1، Wikidata 1 | Wikipedia 8، OpenAlex 5، Open Library 1، Wikidata 1 |
| صفحات بحث رفضها المحكّم (غير متعلقة) | 3: Advanced Combat Helmet (لـ ACH)، Red Hat (الشركة)، Federal Intelligence Service | 3: Popular Mobilization Forces، Word formation، Relation |
| ادعاءات مستخرجة (خام) | 140 | 196 |
| المقتطف موجود في النص المجلوب | 136 (97.1%) | 193 (98.5%) |
| **نسبة نجاح مدقق الإسناد** (موجود + المحكّم يراه داعمًا) | **122/140 = 87.1%** | **183/196 = 93.4%** |
| منشور: حقيقة `verified` / منسوب `attributed` | 90 / 32 | 129 / 54 |
| منها متعارض (مجموعات `conflict_group`) | 10 ادعاءات في 4 مجموعات | 12 في 6 مجموعات |
| مرفوض (مقتطف غير موجود / غير داعم / طويل) | 18 (3 / 14 / 1) | 13 (3 / 10 / 0) |
| المنشور حسب أفضل درجة دليل | الدرجة 1: 113، الدرجة 2: 9 | الدرجة 1: 175، الدرجة 2: 8 |
| تعليمات محقونة أُزيلت من صفحات حقيقية | 0 | 0 |
| قراءات تالية | 15 | 7 |
| البوابتان | `knowledge.citations` ✔، `knowledge.tier-coverage` ✔ ⇒ **accepted** | ✔ / ✔ ⇒ **accepted** |

بحث الويب العام **لم يُستخدم** في التشغيل: SearXNG الخاص بـ b2c معرَّف لكنه غير مُشغَّل، والواجهة المدفوعة غير مضبوطة. لذلك «التلقي» محدود بمراجعات OpenAlex (3 لـ SAT، 1 لـ Gramsci) ولا توجد مصادر من الدرجة الثالثة.

### 2.2 اختبار الحقن الحي (`scripts/knowledge/injection_probe.py`)
الصفحة `tests/fixtures/research/review_with_injection.html` (تعليمات في `<script>` و`<meta>` وعقد مخفية وتعليق HTML وجملة ظاهرة «Ignore all previous instructions… Nobel Prize»):
| المسار | نص محقون وصل للنموذج؟ | عناصر حقن أُزيلت | ادعاءات منشورة | منها محقون |
|---|---|---|---|---|
| الإنتاج (استخراج معزول + تنقية) | لا | 1 | 2 | **0** |
| التنقية معطّلة عمدًا | نعم | — | 2 | **0** |
والادعاءان المنشوران هما الرأيان الحقيقيان في الصفحة (فصل الرضا هو الأكثر أصالة؛ ومعالجة الحركات الإسلامية موجزة)، منسوبين للمراجِع.

### 2.3 طبقة المفاهيم (`concept-layer`)
| المقياس | SAT | Gramsci |
|---|---|---|
| أساس المفاهيم | القاموس المعتمد (263) | مرشحو القاموس (80) |
| عدد المفاهيم في الطبقة | 263 | 80 |
| **التغطية** (`concepts.coverage`) | **263/263 = 1.0** | **80/80 = 1.0** |
| تعريفات المؤلف المقبولة | 117 (رُفض 40 لعدم وجود المقتطف، و61 رفضها المحكّم: «ليس تعريفًا») | 24 (6 غير موجود، 46 رفضها المحكّم) |
| علاقات مقبولة (related / part_of / contrasts_with / prerequisite) | 351 (269 / 56 / 25 / 1) | 158 (121 / 17 / 20 / 0) |
| عبارات اقترحها النموذج ← قُبلت بعد التحقق من المواضع | 1144 ← 468 (**40.9%**) | 461 ← 182 (**39.5%**) |
| **grounding** على المخرج المخزَّن (`concepts.grounding`) | **1.0** (2225 موضعًا، 731 عبارة، 0 غير صالح) | **1.0** (3559 موضعًا، 262 عبارة، 0) |
| ملاحظات خارجية (صوت «مصدر خارجي») / حكم جِدّة مُعلن | 34 / 3 | 69 / 6 |
| دروس فيها مفاهيم جديدة | 78 من 81 | 114 من 114 |
| الحالة | **accepted** | **accepted** |

## 3. أمثلة (مقتطفات قصيرة)

### 3.1 حقائق موثّقة (`verified`)
1. SAT/book_card: «The book is authored by Richards J. Heuer and Randolph H. Pherson.» ← «Authors: Richards J. Heuer; Randolph H. Pherson» (OpenAlex W1602715654، الدرجة 1، جُلب 2026-10-25)، ومعه دليل ثانٍ من مراجعة *Politique étrangère* (الدرجة 2).
2. SAT/book_card: «The book has been cited 269 times according to OpenAlex.» ← «Cited by count in OpenAlex: 269».
3. Gramsci/book_card: «The book presents a new theory of popular mobilization.» ← «John Chalcraft presents a new theory of popular mobilization.» (OpenAlex W4414449588).
4. Gramsci/concept_in_field (hegemony): «Hegemony was a term previously used by Marxists such as Vladimir Lenin…» ← Wikipedia «Antonio Gramsci».
5. SAT/concept_in_field: «Structured analytic techniques are described as the ideal process for guiding the interaction of analysts within a small team…» ← ملخص الكتاب في OpenAlex.

### 3.2 آراء منسوبة (`attributed`)
1. «The book is hugely important to the development of intelligence analysis in UK policing.» — reviewer Dario E Galasso, *Policing: A Journal of Policy and Practice*.
2. «It is the only text currently available that comprehensively describes… structured analytic techniques.» — المراجع نفسه.
3. «The book is equally useful as a desktop reference, as a manual and as a training tool.» — المراجع نفسه.
4. «Gramsci's Prison Notebooks are considered a highly original contribution to 20th-century political theory.» — Wikipedia.
5. Ambassador Robert D. Blackwill: أوراق التحليل «nonadhesive» — منسوب له عبر Wikipedia «Intelligence analysis».

### 3.3 تعارضات محفوظة آراءً منسوبة (لم تُدمج في حقيقة)
1. سنة نشر SAT: «2010» (OpenAlex، الطبعة الأولى) ↔ «CQ Press… 2011… 343 pages» (مراجعة) ↔ ناشر «Virtual Defense Library» (OpenAlex) ↔ «CQ Press» (Open Library) — مجموعة واحدة؛ السبب الحقيقي اختلاف الطبعات.
2. عدد تقنيات SAT: «50 techniques» (OpenAlex) ↔ «66 structured techniques» (مراجعة Policing).
3. DOI كتاب Gramsci: `10.1525/9780520416840` ↔ `10.2307/jj.33476835` (سجلان في OpenAlex).
4. تاريخ ميلاد Chalcraft في Wikidata: 1970-03-28 ↔ 1970-01-01.
5. «passive revolution»: «coined by Antonio Gramsci» ↔ «first used by Vincenzo Cuoco» (Wikipedia؛ في التشغيل الأول قبل تعديل اختيار المفاهيم).

### 3.4 مرفوضات المدقق
1. «The book was published in 2025 by University of California Press.» — المقتطف «Publication year: 2025 ... Published by…» مركّب بنقاط حذف ⇒ غير موجود حرفيًا.
2. «The Italian Communist Party was actively involved in resistance to… Mussolini» — المحكّم: المقتطف (عن «The party») لا يدعم الادعاء بصيغته.
3. «Johnston suggests the three major components…» — مقتطف غير موجود في النص المجلوب.
4. SAT: «Each structured technique involves a step-by-step process…» — المحكّم: غير داعم.
5. صفحات رفضها محكّم الصلة قبل الاستخراج: Advanced Combat Helmet (لـ ACH)، Red Hat، Popular Mobilization Forces.

### 3.5 تعريفات المؤلف بمواضعها (صوت المؤلف + `gloss` بصوت الكورس)
1. **Key Assumptions Check** — `book-main:77:0` ص77: «Making explicit and questioning the assumptions that guide an analyst’s interpretation of evidence».
2. **Red Hat** — `book-main:376:1` ص376: «Red Hat Analysis is anticipating the behavior of another individual or group by trying to replicate how they think.»
3. **Quadrant Crunching™** — `book-main:388:1` ص388: «…a systematic procedure for identifying all the potentially feasible combinations among several sets of variables.»
4. **popular mobilization** — `book-70c88…:12:1` ص12: «…defined as the many ways in which subordinated groups rearrange their relationships to challenge and overcome social domination.»
5. **phase** — `book-70c88…:75:4` ص75: «A phase, then, is a configuration of relationships.» (أسقط المحكّم الـ gloss لأنه زاد على المقتطف.)

### 3.6 علاقات المفاهيم (كل علاقة بموضعها)
1. Cluster Brainstorming —contrasts_with→ Nominal Group Technique (ص89).
2. Premortem Analysis —contrasts_with→ Structured Self-Critique (ص401: «right-brained… left-brained»).
3. Scenarios Generation —part_of→ Foresight Analysis (ص91).
4. phase —part_of→ domination / autonomy (ص22: «four major phases: the torturous contradictions of domination, the wellsprings of autonomy…»).
5. Cognitive Bias —contrasts_with→ Structured Analytic Technique (ص47: «…help mitigate cognitive biases…»).

### 3.7 ملاحظات خارجية وحكم الجِدّة (صوت «مصدر خارجي» فقط)
1. ACH: `author_novel` — مستند إلى ملاحظات Wikipedia «Analysis of competing hypotheses» (Heuer أحد مؤلفَي الكتاب).
2. Cognitive Bias: `established` — «introduced by Amos Tversky and Daniel Kahneman in 1972».
3. subaltern: `established` — «Antonio Gramsci coined the term subaltern…» (ليس من ابتكار Chalcraft؛ صُحّح بعد التشغيل الثاني، §4).
4. civil society: `established` — تمييز Gramsci بين المجتمع السياسي والمدني.
5. facilitator: `established` — تعريف Wikipedia «Facilitator».

### 3.8 قوائم المفاهيم لكل درس (أول ذكر في الدرس)
1. SAT «1.2 Role of Structured Analytic Techniques»: Structured Analytic Technique، Key Assumptions Check، Analysis of Competing Hypotheses، Cognitive Bias، Misapplied Heuristics، Intuitive Traps…
2. SAT «1.3 History of Structured Analytic Techniques»: … Jack Davis، alternative analysis، Mirror Imaging، CIA (24 مفهومًا).
3. SAT «1.1 Our Vision»: law enforcement، driving forces، mental model، Digital Disinformation…
4. Gramsci «structure of the book»: Gramscian، theory، phase، popular mobilization، contradiction، domination، autonomy.
5. Gramsci «after the spring»: popular uprising، revolution، nationalism، socialist، labour…

## 4. ما تغيّر بسبب التشغيل الحقيقي (أخطاء وُجدت وأُصلحت)
- التشغيل الأول: Wikipedia أعاد HTTP 429 (9 طلبات)، و225/263 استدعاءً للمفاهيم فشل لأن DeepSeek استهلك `max_tokens` في التفكير ⇒ فاصل لكل مضيف وإعادة محاولة 429/503 مع `Retry-After`، و`reasoning_effort=none` (commit `4d2ddeb`).
- التشغيل الثاني قبل صفحات خاطئة («Advanced Combat Helmet» لـ ACH، «100 Days of Deception»، «Popular Mobilization Forces») وأنتج منها ادعاءات «مُسندة» صحيحة الاقتباس — وهذا بالضبط «غسيل الإسناد» في §6 من المقترح ⇒ محكّم صلة قبل الاستخراج، ومحكّم تعريفات، وعلاقة لا تُقبل إلا إذا سمّى المقتطف المفهومين (`89cbb76`).
- حكم «author_novel» لـ subaltern كان خطأً (Gramsci ليس مؤلف الكتاب) ⇒ يُعطى المحكّم أسماء مؤلفي الكتاب، والملاحظة الخارجية يجب أن تسمّي المصطلح (`7584023`).

## 5. ما لم يُنفَّذ أو بقي افتراضًا (غير متحقق)
- **لا تشغيل عبر Temporal أو الـ worker**: `BookKnowledgeWorkflow` اختُبر على خادم Temporal الاختباري بأنشطة وهمية فقط؛ لم يُبنَ أي image ولم يُشغَّل `b2c-knowledge-worker` (ممنوع docker build/up على الخادم الإنتاجي).
- **سجل الترقية في الذاكرة** أثناء التشغيل الحقيقي (لا كتابة إلى Postgres الإنتاجي).
- SearXNG: الصورة مثبتة بـ digest (من Docker Hub API) لكن لم تُسحب ولم تُشغَّل؛ الواجهة المدفوعة غير مضبوطة.
- **Open Syllabus غير مستخدم**: لا API عامة؛ شروطه (https://blog.opensyllabus.org/terms-and-policies، مراجعة 2023-05-04، فُحصت 2026-10-25) تحصر الوصول البحثي في أجزاء مجهولة الهوية من البيانات باتفاق.
- العتبات (0.5 دعم خام، 5 ادعاءات، 0.25 للدرجة الثالثة، 0.9 تغطية) **سياسة مؤقتة غير معايرة** بأحكام مستقلة؛ ولم يُقَس اتفاق المحكّم مع حكم بشري أو ثالث.
- المحكّم نفسه قد يخطئ (قبل 87–93% من الادعاءات)؛ لم تُقَس دقته على عيّنة مستقلة.

## 6. المخاطر والقيود المفتوحة
1. **كتاب Gramsci بلا قاموس معتمد**: مرشحو القاموس فيهم كلمات عامة («theory»، «party»، «relation»)، فالتغطية 1.0 هنا لا تعني جودة المفاهيم. المطلوب: اعتماد قاموسه (D-031/D-033) ثم إعادة التشغيل.
2. **مخطط v1**: الدروس تشمل صفحات المديح والفهارس (SAT `m01`، `PR EF A C E`)، فبعض «أول ذكر» يقع في المديح؛ وعناوين دروس Gramsci متباعدة الحروف. يزول بمخطط v1→v2 (يعمل عليه وكيل آخر في `packages/outline`؛ لم أعدّله).
3. **الطبعات**: OpenAlex يعيد سجل الطبعة الأولى لـ SAT (2010)؛ الكتاب المحلَّل هو الطبعة الثالثة (2021). يظهر ذلك تعارضًا منسوبًا لا حقيقة، لكن مطابقة الطبعة بالـ ISBN لم تُنفَّذ.
4. **القراءات التالية** من «أكثر الأعمال المستشهِدة اقتباسًا» قد تكون بعيدة الموضوع (مثل ورقة عن الوقود الأحفوري تستشهد بـ SAT)؛ تحتاج فلتر صلة كما في صفحات البحث.
5. نسبة قبول عبارات النموذج في طبقة المفاهيم ~40%: المدقق يحمي الإسناد (grounding 1.0)، لكن نصف العمل يُهدر؛ يمكن تحسين المطالبة أو نموذج أقوى.
6. OpenAlex بلا مفتاح: ميزانية يومية مشتركة صغيرة لكل IP ($0.10)؛ يُنصح بـ `B2C_OPENALEX_API_KEY_FILE`.
7. علاقات `prerequisite` نادرة جدًا (1 و0): رسم المتطلبات السابقة شبه فارغ رغم طلبه في المطالبة.

## 7. كيف يُشغَّل
- تطوير (بلا Temporal): `scripts/knowledge/run_knowledge.py` كما في §1.2 (`--help` للخيارات؛ `--use-unaccepted-dossier` للتطوير فقط).
- إنتاج (عند القرار): `uv run b2c-knowledge-worker` مع `B2C_KNOWLEDGE_{EXTRACTOR,JUDGE}_{BASE_URL,MODEL_ID,FAMILY,API_KEY_FILE,ALLOW_REMOTE,REASONING_EFFORT}`، ثم بدء `BookKnowledgeWorkflow` على queue `knowledge` بمدخل `BookKnowledgeInput(course_id, parsed_ref, outline_ref, termbase_ref?, units_ref?)`. التفاصيل: `docs/04-OPERATIONS.md` §5.4.
