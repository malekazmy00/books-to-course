# تدقيق المرحلة 4: Knowledge Graph (استخراج الكيانات والعلاقات، محوّلا LightRAG/HippoRAG، الاسترجاع عبر الرسم)

- التاريخ: 2026-10-24
- الفرع: `hardening`
- نوع التدقيق: قراءة فقط. لم يُعدَّل أي كود أو إعداد أو بيانات. كل استعلامات Postgres وS3 كانت قراءة من داخل `b2c-worker-1`، ولم يُرسَل أي نص من الكتب إلى خدمة خارجية.
- منهج الشك: عبارة "passed" أو "completed" في التقارير السابقة لا تُعتبر دليلًا على الصحة. هذا هو الدرس من تدقيق الترجمة (`outputs/translation-review-2026-10-24/TRANSLATION-ERROR-CATALOGUE.md`). كل ادعاء هنا مربوط بملف وسطر، أو بمفتاح artifact مع القياس، أو بنتيجة اختبار. ما لم أستطع التحقق منه موسوم بـ **UNVERIFIED**.

---

## 1. غرض المرحلة وعقدها

### 1.1 الغرض كما تقصده الوثائق
- `docs/01-ARCHITECTURE.md:18`: الفهرسة تكون "dense/sparse/graph خلف واجهة موحدة". الاسترجاع يبدأ من الكتاب الأساسي (spine) ثم يضيف شواهد الكتاب المساند مع ذكر المصدر.
- `docs/01-ARCHITECTURE.md:112`: "الكيانات والعلاقات ومقترحات الدمج والمراجعات artifacts بإصدارات".
- `docs/00-DECISIONS.md:132-138` (D-011): LightRAG اتجاه مؤقت. فوقه "Concept Layer" خاصة بالمشروع لعلاقات تعليمية محددة النوع (`prerequisite_of`, `example_of`, `defines`…). HippoRAG 2 مقارنة اختيارية فقط.

### 1.2 العقد المكتوب
- `docs/02-CONTRACTS.md:51`: التسليم L4 → L5 هو "LightRAG doc_ids + `ConceptEdge[]`"، بشرط `doc_status=processed` لكل كتاب و"كل edge ليه evidence"، والفشل يذهب إلى retry / G1.
- `packages/contracts/src/b2c_contracts/models.py:189-214` يعرّف:
  - `EntityType` (9 أنواع) و`ConceptEdgeType` (6 أنواع).
  - `ConceptEdge` (src, dst, type, `evidence_chunk_ids≥1`, `confidence∈[0,1]`, classifier).
  - `EntityMergeProposal` (canonical, aliases, cosine, judge_verdict, decision).
- `docs/02-CONTRACTS.md:121-127` (§5): `GraphRetriever` بالشكل `insert_book / delete_book / retrieve(query, top_k) -> [(chunk_id, score)]`.

### 1.3 ما تحتاجه المراحل اللاحقة فعلًا
هذه الاحتياجات مستنتجة من الكود وتقارير المراحل الأخرى:

| المستهلك | ما يحتاجه من هذه المرحلة | الحالة اليوم |
|---|---|---|
| الترجمة (L7) | مخزون مصطلحات ومفاهيم لكل كتاب (concept inventory)، يصير termbase يُفرض على الترجمة. | غير موجود. كتالوج الترجمة يسجّل **716 مقطعًا مقبولًا** بخطأ مصطلحي كبير أو حرج و**215** بخطأ في أسماء الأعلام (`TRANSLATION-ERROR-CATALOGUE.md:82-84`). السبب الجذري المذكور هناك: "الـ glossary المرسل لا يغطي الجهاز المفاهيمي لكل كتاب" (`:376`). المعالجة المقترحة: "termbase لكل كتاب يُبنى قبل الترجمة" (`:385`). هذا هو المنتج الطبيعي لهذه المرحلة. |
| الـ outline والـ pedagogy (L5/L6) | علاقات `prerequisite_of` للتحقق من ترتيب الدروس، وglossary لكل درس، والمفاهيم المشمولة في الأسئلة. | `prior_lessons` في الـ outline تأتي من البنية فقط (`packages/outline/src/b2c_outline/review.py:123`). لا شيء يستهلك `ConceptEdge` (grep على `packages/` لا يجد استخدامًا خارج `packages/kg`). |
| الإثراء من الكتب المساندة (enrichment) | ربط مفهوم في الكتاب الأساسي بمقطعه في الكتاب المساند (T-KG-05). | غير منفّذ، ولم يُقس (انظر §3). |
| الاسترجاع للأسئلة (QA) | مرشحون إضافيون عبر الرسم لأسئلة multi-hop. | مسار اختياري موصول ومعطّل فعليًا (§2.3). |

---

## 2. التصميم كما هو منفّذ فعلًا

### 2.1 حزمة `packages/kg` (Concept Layer)
- **`concepts.py`**: `ConceptEvidence(chunk_id, book_id, role, pages)` و`ConceptRelationStore` (put / put_many / list_relations / delete_book) و`InMemoryConceptRelationStore`. الدمج idempotent بحسب chunk. الحذف بحسب الكتاب يُبقي العلاقة إذا دعمها كتاب آخر. التصميم جيد ويستحق الإبقاء.
- **`postgres_concepts.py`**:
  - جدولان فقط هما `b2c_concept_relations` و`b2c_concept_evidence` (`:21`, `:33`)، والـ DDL مضمّن في الكود بلا migrations.
  - **لا يوجد جدول للكيانات أو الأسماء البديلة أو الإشارات (mentions)**. الكيانات التي لا تدخل في علاقة لا تُحفظ إطلاقًا.
- **`extraction.py`** (`ConceptExtractionService`):
  - استدعاء LLM واحد لكل chunk بلا سياق القسم أو الكتاب (`:187`).
  - prompt إنجليزي (`:193-202`) يطلب `evidence_quote` منسوخًا حرفيًا.
  - التحقق: الاقتباس substring حرفي من الـ chunk (`:302-303`)، والاسم داخل الاقتباس.
  - **بوابة العلاقة regex** (`_relation_type_is_explicit`, `:323-353`): تقبل العلاقة فقط إذا ظهر في الاقتباس نمط صريح مثل `X is part of Y` و`X is an example of Y` و`X must be learned before Y`، مع تجاور حرفي بين الطرفين والكلمة الدالة.
  - هوية الكيان هي `type:normalized_name` (`:134-138`).
  - `confidence` ثابتة 0.5 (`UNSCORED_CONFIDENCE`, `:24`).
- **`graph_retriever.py`**: يحتوي مكوّنين.
  1. `GraphRetrieverAdapter`: محوّل HippoRAG قديم من Phase 6، لا يستخدمه إلا `tests/unit/eval/test_phase6_bridge.py`.
  2. `ConceptGraphRetriever`: مطابقة نصية حرفية بين السؤال واسم الكيان، ثم توسعة hop واحد، بدرجات ثابتة 1.0 و0.5 (`:152`). يقرأ **كل** علاقات النطاق في كل استعلام (`:132`).

### 2.2 وحدات `packages/indexing/.../graph_*` (خدمات LightRAG/HippoRAG)
- **`graph_adapters.py`**:
  - `RetrievalScope` يحسب `corpus_sha256` و`source_id` لكل chunk.
  - `LightRAGAdapter` و`HippoRAGAdapter` يتحققان من الـ manifest والنطاق والدرجات. هندسة العزل والتحقق هنا متينة.
  - درجة LightRAG تُستبدل بـ `1/(rank+1)` (`:154`).
- **`graph_native.py`**:
  - يشغّل LightRAG 1.5.7 مع `entity_extract_max_gleaning=0` (`:109`).
  - يشغّل HippoRAG `2.0.0a5` (alpha) من مصدر مرقّع محليًا خارج المستودع (`docker/compose.graph-services.yml`, `B2C_HIPPORAG_SOURCE_DIR`).
  - استدعاءات الـ LLM تتم مباشرة عبر `openai_complete_if_cache` (`:94`) وعميل HippoRAG، **خارج `llm_gateway`**.
  - يُلصق `"B2C source identity: b2c:<sha256>"` في نص كل chunk قبل الفهرسة (`_tag`, `:54-55`).
  - الأسرار تُكتب في `os.environ` (`:153-154`).
- **`graph_runtime.py`**:
  - `GraphIndexRegistry` يبني فهرسًا جديدًا في دليل فارغ عند كل `ensure` لمفتاح جديد.
  - كاش بحد أقصى فهرسين محمّلين (`:27`)، وقفل عام واحد `_operation` يسلسل كل العمليات لكل المقررات (`:36`, `:72`, `:109`).
  - بعد إعادة التشغيل يجب إعادة البناء كاملًا.
- **`graph_router.py`**:
  - `generation_id` يتضمن بصمة **مجموعة المرشحين** (`:57-58`). لذلك كل مجموعة مرشحين مختلفة لكل نطاق (درس أو مجموعة أو مقرر) تولّد **فهرس رسم جديدًا يُبنى كاملًا بالـ LLM**.
  - مهلة الطلب الافتراضية 900 ثانية (`:19`).
- **`graph_api.py`**: خدمة FastAPI موثّقة بـ Bearer وHMAC compare.

### 2.3 التوصيل في الإنتاج
- `packages/orchestration/src/b2c_orchestration/qa.py:384-399`: عندما `retrieval_mode ∈ {lightrag, hipporag}` يُنشأ `GraphRetrievalRouter`، ويُمرَّر له **مرشحو النطاق فقط** (`candidates`). الرسم إذن يُبنى على مقاطع الدرس وحده، فلا يمكنه أصلًا الربط عبر الدروس أو عبر الكتب.
- `worker.py:109-114` يقرأ `GraphProviderConfig.from_env()`. **متغيرات البيئة غير معرّفة في `b2c-worker-1`** (فحص `docker exec b2c-worker-1 env` لم يُرجع أي متغير `LIGHTRAG/HIPPORAG/GRAPH`)، وخدمات الرسم غير مشغّلة (`docker ps`).
- `docker/compose.graph-services.yml:5` يجعل LLM الرسم **افتراضيًا** `https://openrouter.ai/api/v1` مع `deepseek-v4-flash`. الخدمتان على شبكة `b2c_edge` التي تسمح بالخروج (`:48`, `:74`). تشغيل هذا الـ overlay يرسل نصوص الكتب الحقيقية كاملة إلى API خارجي، وهذا يخالف القاعدة 6 في `CLAUDE.md`.
- **لا توجد Activity أو Workflow** في `packages/orchestration` تستدعي `ConceptExtractionService` أو تكتب `ConceptEdge`. grep على المستودع لا يجد مستدعيًا لها إلا الاختبارات وسكربتات `eval/`.

### 2.4 تعارضات بين الوثائق والكود

| الوثيقة | تقول | الكود يفعل |
|---|---|---|
| `02-CONTRACTS.md:51` | L4→L5 تسلّم `LightRAG doc_ids + ConceptEdge[]` | لا شيء يُسلَّم. لا artifact للرسم في S3، والجداول فارغة (§3). |
| `02-CONTRACTS.md:121-127` | `GraphRetriever.insert_book/delete_book/retrieve` | في الكود ثلاثة أشكال مختلفة: `b2c_kg.GraphRetriever.search(query, chunks, …)` و`_ScopedAdapter.search_candidates(...)` و`NativeGraph.index/retrieve`. لا يطابق أيٌّ منها العقد. |
| `00-DECISIONS.md:136` | Concept Layer "فوق LightRAG" | منفصلان تمامًا. كيانات LightRAG لا تصير `ConceptEdge` أبدًا. |
| `05-PHASES.md:119` و`phase-6.md` | Phase 4 وPhase 6 قراراهما **NO-GO** لإدخال الرسم في مسار الإنتاج | في 2026-10-09 وُصل مساران لـ LightRAG وHippoRAG داخل توليد الأسئلة (`qa.py:384`)، ومعهما compose overlay افتراضيه API خارجي (`eval/reports/graph-integration-2026-10-09.md`). |
| `01-ARCHITECTURE.md:112` | الكيانات والعلاقات ومقترحات الدمج artifacts بإصدارات | `EntityMergeProposal` غير مستخدم في أي مكان. لا artifact كيانات. |
| `03-TESTING.md:73-74` | T-KG-01/02 تُقاس مقابل "independent source-grounded auto labels (… لا تُسمّى gold)" | أُعيد تعريف البوابة لتقيس توافق نموذج مع نموذج. لا يوجد مرجع بشري، فالبوابة لا تقيس الصحة. |

---

## 3. الجودة المقاسة

### 3.1 ما أنتجته المرحلة فعلًا في الإنتاج
| القياس | الطريقة | النتيجة |
|---|---|---|
| علاقات المفاهيم المخزنة | `select count(*) … from b2c_concept_relations / b2c_concept_evidence` (psycopg، قراءة) | **0 / 0** صفوف، 0 نطاقات |
| artifacts الرسم في S3 | سرد كامل للـ bucket `artifacts`: 17,800 كائنًا، وبحث regex عن `graph|kg|concept|entit|lightrag|hippo` | **0** artifacts رسم. الـ 16 تطابقًا كلها `source-identity.json`، وهو تطابق كاذب على "entit". |
| مسار الاسترجاع المستخدم فعلًا | قراءة كل `course-qa.json` (33) و`input.json` (7) | `retrieval_mode` إما `None` أو `lexical`. **صفر** تشغيلات بمسار رسم. |
| الخدمات | `docker ps` | لا `lightrag` ولا `hipporag` مشغّلة |
| حجم المتون الحقيقية التي كان يجب أن يُبنى لها رسم | `b2c_indexed_chunks` | `b2c-demo`: 397 + 507 chunk (spine فقط، بلا كتاب مساند). `phase5-live-tidx01`: 41 spine + 261 supplementary. المجموع 1,206. |

**الخلاصة: ناتج هذه المرحلة في الإنتاج يساوي صفرًا.** كل ما ورد في التقارير من "PASS" يخص تجارب معزولة على متن تجريبي من 37 أو 87 مقطعًا.

### 3.2 هل تستطيع بوابة العلاقات قبول أي شيء من الكتب الحقيقية؟
**الطريقة:** عددتُ داخل الـ worker، دون أي LLM، الـ chunks التي تحتوي **العبارة الدالة وحدها** من أنماط `extraction.py:329-353`. هذا حد أعلى متفائل، لأن البوابة تشترط فوقه تجاور الطرفين حرفيًا. للمقارنة عددتُ عبارات طبيعية ترفضها البوابة.

| الكتاب (chunks) | prerequisite_of | defines | example_of | part_of | applies_to | contrasts_with | "for example / such as" (مرفوضة) | "refers to / is called / means" (مرفوضة) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| book-main (397) | **0** | 8 | 7 | 4 | 5 | 1 | 151 | 37 |
| book-70c88… (507) | **0** | 4 | 1 | 5 | 2 | 11 | 92 | 70 |
| lane-ch5 (41) | **0** | 0 | 0 | 0 | 2 | 0 | 9 | 7 |
| think-stats-2e (261) | **0** | 0 | 1 | 1 | 4 | 1 | 55 | 69 |

النتائج:
- **`prerequisite_of`، أهم علاقة تعليمية، لا يمكن قبولها في أي chunk من 1,206.** الكتب لا تكتب "X must be learned before Y".
- حتى في الحد الأعلى، أقل من 6.3% من chunks الكتاب الأساسي تحتوي أي عبارة مقبولة.
- الأنماط لا تسمح بأداة التعريف أو التنكير قبل الطرف الثاني. `\bsource\b\s+is\s+part\s+of\s+\btarget\b` يرفض "X is part of **the** Y" ما لم يتضمن اسم الكيان "the".
- اتجاه `defines` متناقض داخل الكود نفسه (`:330-332`):
  - "X defines Y" يعطي X→Y.
  - "X is defined as Y" يعطي أيضًا X→Y، مع أن Y هنا هو التعريف وX هو المُعرَّف.
  - "Y is defined by X" يعطي X→Y.
  - النمط الأول والثاني يعطيان المعنيين المتعاكسين بالاتجاه نفسه.

### 3.3 جودة الكيانات التي اعتُبرت "مرجعًا" لـ T-KG-01
**الطريقة:** قرأتُ `/mnt/wsl/b2cdata/phase4-kg-eval-20261004/phase4-deepseek-3book-entities-final.json`: 386 كيانًا "مقبولًا"، كلها `source_exact_validation_failures: 0`. طبقتُ عليها قواعد حتمية لكشف الضوضاء البنيوية، وهي حد أدنى للخطأ، ثم راجعتُ يدويًا عينة عشوائية من 40.

| فئة الضوضاء | العدد |
|---|---:|
| مراجع أشكال أو تمارين ("Figure 3.21"، "Guided Practice 3.33") مصنفة `example` | 52 |
| رموز قصيرة ≤ 3 أحرف ليست صيغًا ("P2"، "P3"، "n") | 28 |
| أرقام حرفية ("0.0231"، "0.0233") مصنفة `concept` | 14 |
| اسم المؤلف من ترويسة الصفحة ("David M. Lane" ×6) مصنف `person` | 6 |
| **مجموع الكيانات المعلَّمة (بعد إزالة التكرار بين الفئات)** | **94 / 386 = 24.4%** |

- **11 اسمًا ظهر بأكثر من نوع**، مثل `probability` (concept/definition) و`variance` (concept/formula) و`random variable` و`expected value`. بما أن الهوية `type:name` (`extraction.py:134`)، يتحول كل واحد منها إلى عقدتين منفصلتين، أو يُرفض بسبب `entity_type_conflict` (`:235`).
- صيغ مفرد وجمع تتحول إلى عقد مختلفة: `permutation/permutations` و`tree diagram/tree diagrams`.
- في العينة اليدوية (40) ظهرت أيضًا كيانات سياقية لا تصلح مفاهيم مقرر: "mortgage" و"DeKalb County, GA" و"no cancer" و"breast cancer"، كلها `concept`.
- **الاستنتاج:** "تحقق الاقتباس الحرفي = 100%" فحص **شكلي**، تمامًا كما حدث في الترجمة. ربع "المرجع" على الأقل ضوضاء. ومع ذلك استُخدم هذا المرجع لحساب precision/recall لـ LightRAG (`eval/reports/phase4/phase4-deepseek-vs-lightrag-entity-agreement-final.json`: P=0.245, R=0.429, F1=0.312). هذا الرقم توافق بين نموذجين، ولا يقيس جودة.

### 3.4 تقييم الاسترجاع عبر الرسم: هل كان قادرًا على التمييز؟
- **المتن:** 37 chunk، والتقييم عند k=10. أي نظام يعيد **27% من المتن كله** في كل استعلام، لذلك Recall@10 قريب من السقف لأي طريقة (lexical 0.98).
- **الأسئلة:** 50 سؤالًا ولّدها نموذج من نفس الـ chunks (`phase3-retrieval-50-diagnostic.json → limits`: "automatic qrels are derived from the same source citations used to generate questions; they are not independent labels"). كل سؤال له chunk صحيح واحد، ولا توجد أسئلة multi-hop أو عبر الكتب، وهي بالضبط الأسئلة التي يُفترض أن يفيد فيها الرسم (Han et al. 2025، أدناه).
- **أرقام LightRAG `mix` تطابق BGE-M3 dense حتى الخانة الرابعة:**
  - على 50 سؤالًا: 0.960 / 0.7918 / 0.8326 (`eval/reports/phase-4.md:235`).
  - على 44 سؤالًا: 0.9535 / 0.8036 / 0.8398 (`phase-6.md`: "reproduces Phase 4's dense metrics exactly").
  - تطابق ثلاثة مقاييس على مجموعتين مؤشر قوي على أن ترتيب LightRAG الفعلي هو ترتيب البحث المتجهي، وأن الرسم لم يغيّر ترتيب الإجابة الصحيحة. **UNVERIFIED على مستوى كل سؤال**، لأن rankings الـ dense لكل سؤال غير محفوظة. محفوظ فقط `metrics_at_10` في `phase3-retrieval-50-diagnostic.json`.
- **T-KG-05 (عبر الكتب):** صفر حالات مقبولة، فلم يُقس أصلًا (`phase-4.md:240`).

### 3.5 جدوى الاستخراج وكلفته
- **qwen3-coder-30b محليًا:** فشل في 2 من 2 chunk تجريبي، بزمن 64 و95 ثانية لكل chunk (`phase-4.md:148`).
- **Qwen free:** 7 من 15 اقتباسًا حرفيًا فقط (`phase-4.md:266`).
- **DeepSeek canary:** 9/9 كيانات و4/5 علاقات اجتازت **الفحص الشكلي** (`phase-6.md:94`). لم يُقَس صواب أي علاقة مقابل حكم بشري في أي مرحلة. لا يوجد مقياس relation precision في أي سكربت تحت `eval/suites/phase4`.
- **LightRAG محليًا:** 140 إلى 394 ثانية لكل chunk مع gleaning=0 (`phase-4.md:180`). بناء رسم لكتاب من 397 chunk يستغرق نحو 15 إلى 43 ساعة. هذا استقراء من عينتين، **UNVERIFIED**.
- مع تصميم `graph_router.py:57-58` (فهرس لكل مجموعة مرشحين) يتكرر هذا البناء لكل نطاق، ومهلة الطلب 900 ثانية، فالفشل مؤكد عمليًا عند أول نطاق حقيقي.

### 3.6 الاختبارات
`pytest -q tests/unit/kg tests/unit/indexing/test_graph_runtime.py tests/unit/indexing/test_graph_adapters.py tests/unit/eval/test_phase6_bridge.py`: **96 passed** في 1.4 ثانية. كلها تستخدم نماذج وخلفيات مزيفة (`FakeGateway` وtransports وهمية)، و**لا يقيس أيٌّ منها جودة الاستخراج أو صواب العلاقات**. هي تثبت سلامة العقود والعزل فقط.

---

## 4. المشكلات مرتبة

### حرجة (Critical)

**C1. المرحلة غير موجودة في الإنتاج، والعقد L4→L5 وهمي.**
- الدليل: §3.1. الجداول فارغة، صفر artifacts، لا Activity تستدعي `ConceptExtractionService`، و`02-CONTRACTS.md:51` يعد بتسليم لا يحدث.
- الأثر على المقرر:
  - لا يوجد مخزون مفاهيم أو termbase، فالترجمة تعمل بلا ذاكرة مصطلحية. هذا سبب جذري مباشر لـ 716 مقطعًا مقبولًا بخطأ مصطلحي كبير أو حرج و215 بخطأ أعلام (كتالوج الترجمة).
  - لا تحقق من ترتيب المتطلبات المسبقة بين الدروس.
  - لا glossary مفاهيمي للدروس.
  - لا ربط بين الكتاب الأساسي والمساند.
- السبب الجذري: صُمّمت المرحلة كـ "تجربة استرجاع" (هل يحسّن الرسم Recall؟)، لا كمنتِج لمعرفة يستهلكها ما بعدها. حين خسر الرسم أمام hybrid RRF، أُغلقت المرحلة كلها بقرار NO-GO، ولم يُبنَ البديل الذي تحتاجه المراحل الأخرى.

**C2. الجودة لم تُقس قط مقابل حكم بشري، والبوابات أُعيد تعريفها لتقيس الشكل.**
- الدليل:
  - `03-TESTING.md:73-74`: المرجع "auto labels، لا تُسمّى gold".
  - T-KG-01 يقيس توافق DeepSeek مع LightRAG، و24.4% من "المرجع" ضوضاء (§3.3).
  - T-KG-02 يعتمد على 50 زوجًا "automatically adjudicated".
  - صواب العلاقات غير مقيس إطلاقًا.
  - تقييم الاسترجاع غير قادر على التمييز (§3.4).
- الأثر: أي "PASS" مستقبلي على هذه البوابات لا يعني أن الرسم صحيح. هذا تكرار مباشر لنمط الترجمة، حيث كان 57% من "المقبول" خطأً.
- السبب الجذري: غياب مجموعة gold بشرية، وغياب إرشادات توسيم (annotation guideline)، والخلط بين "مصدرية الاقتباس" و"صحة الدلالة".

### عالية (High)

**H1. بوابة العلاقات بالـ regex تجعل الـ recall صفرًا بنيويًا، وتحتوي خطأ اتجاه.**
- الدليل: §3.2. `prerequisite_of` = 0 في 1,206 chunk. أداة التعريف تُسقط الأنماط. اتجاه `defines` متناقض (`extraction.py:330-332`).
- الأثر: حتى مع نموذج مثالي، الرسم الناتج شبه فارغ من العلاقات التعليمية. وما يُقبل من `defines` قد يكون معكوس الاتجاه.
- السبب الجذري: محاولة استبدال التحقق الدلالي (verifier/NLI أو مراجعة بشرية) بمطابقة نصية سطحية.

**H2. لا توجد هوية كيانات ولا entity resolution ولا تخزين للكيانات.**
- الدليل:
  - الهوية `type:normalized_name` (`extraction.py:134`).
  - 11 اسمًا بأنواع متعددة، وانقسامات مفرد وجمع (§3.3).
  - `EntityMergeProposal` غير مستخدم.
  - لا جدول كيانات أو aliases (`postgres_concepts.py:21,33`).
- الأثر: عقد مكررة، وروابط مقطوعة بين الكتب، وغياب أساس لأي termbase.
- السبب الجذري: تخطي طبقة canonicalization بالكامل.

**H3. مسار الرسم الموصول في QA يخالف قرار NO-GO وقواعد المشروع، وغير قابل للتشغيل على كتاب حقيقي.**
- الدليل:
  - `qa.py:384-399`.
  - `compose.graph-services.yml:5`: LLM افتراضي خارجي، وشبكة `b2c_edge`.
  - `graph_native.py:94`: استدعاء خارج `llm_gateway` (يخالف القاعدة 5).
  - `graph_router.py:57-58`: فهرس لكل مجموعة مرشحين.
  - `graph_runtime.py:27,36`: كاش من فهرسين وقفل عام.
  - مهلة 900 ثانية مقابل ساعات من البناء.
- الأثر: المسار خامل اليوم لعدم وجود env. لو فُعّل فسيرسل نص الكتاب إلى OpenRouter، ويتجاوز سجل الكلفة (`graph-integration-2026-10-09.md`، القيد 2)، ثم يفشل بالمهلة.
- السبب الجذري: إضافة وصلات تشغيلية قبل وجود دليل قيمة، دون مراجعة مقابل D-004 والقواعد 5 و6.

**H4. تقييم الاسترجاع عاجز عن التمييز، ومع ذلك بُني عليه قرار المرحلة.**
- الدليل: §3.4. 37 وثيقة عند k=10، وأسئلة دائرية، ولا multi-hop، ولا عبر الكتب، وأرقام LightRAG مطابقة لأرقام dense.
- الأثر: القرار السلبي ربما صحيح، لكنه لم يختبر الحالة التي يُفترض أن ينفع فيها الرسم. وأي قرار إيجابي مستقبلي على هذا الإطار سيكون بلا قيمة أيضًا.

**H5. `confidence` ثابتة 0.5.**
- الدليل: `extraction.py:24`.
- الأثر: لا يمكن ترتيب العلاقات أو تصفيتها أو توجيه المراجعة بحسب الثقة. حقل العقد `ConceptEdge.confidence` بلا معنى.

### متوسطة (Medium)
- **M1. ازدواج التجريدات:** `b2c_kg.graph_retriever.GraphRetrieverAdapter` (لا يستخدمه إلا eval) و`b2c_indexing.graph_adapters` و`graph_router` و`graph_runtime` و`graph_native` و`graph_api`، إضافة إلى عقد ثالث في `02-CONTRACTS §5`. النتيجة نحو 1,000 سطر من بنية تحتية لمسار بلا مستخدم (`wc -l`: 685 سطرًا في `graph_*` و~600 في kg، يشمل محوّلات ضرورية).
- **M2. تلويث نص الفهرسة:** `"B2C source identity: b2c:<sha>"` يُلصق في كل نص قبل استخراج LightRAG/HippoRAG وقبل التضمين (`graph_native.py:54-55`). قد يظهر كيانًا أو يشوّه المتجهات. **الأثر الكمي UNVERIFIED**، والتقرير الأصلي نفسه يعترف بذلك (`graph-integration-2026-10-09.md`، القيد 3).
- **M3. استخراج لكل chunk منفردًا** بلا سياق القسم أو الكتاب، وبلا gleaning (`extraction.py:187`, `graph_native.py:109`). يضيع الإسناد المرجعي (coreference) والعلاقات العابرة لحدود الـ chunks.
- **M4. `ConceptGraphRetriever`:** مسح كامل للعلاقات في كل استعلام (`graph_retriever.py:132`)، ومطابقة حرفية فقط بلا aliases ولا عربية، ودرجات ثابتة.
- **M5. HippoRAG 2.0.0a5 alpha** من مصدر مرقّع خارج المستودع (`/mnt/wsl/b2cdata/phase6-assets/runtime-src/...`). البناء غير قابل لإعادة الإنتاج من المستودع، وهذا يخالف روح القاعدة 9. وأسرار تُكتب في `os.environ` (`graph_native.py:153-154`).
- **M6. مخطط Postgres** بـ `CREATE TABLE IF NOT EXISTS` داخل الكود، بلا أداة migrations ولا إصدار مخطط. الجدولان موجودان في قاعدة الإنتاج وهما فارغان.

### منخفضة (Low)
- **L1.** درجة LightRAG تُستبدل بالرتبة (`graph_adapters.py:154`)، فتضيع معايرة الدمج مع مسترجعات أخرى.
- **L2.** خدمات الرسم على `b2c_edge` (خروج إلى الإنترنت) دون حاجة عند استخدام نموذج محلي.
- **L3.** Phase 4 وPhase 6 أرسلتا نصوص كتب مفتوحة الترخيص (Lane وThink Stats وOpenIntro) إلى OpenRouter. D-022 يقصر الاستثناء على "synthetic-data tests". **UNVERIFIED** هل وافق المالك صراحة على هذه النصوص. التقارير تذكر سقف كلفة معتمدًا فقط.

---

## 5. التصميم المستهدف حسب المعايير الصناعية

### 5.1 المبدأ: إعادة تعريف المرحلة
الأدبيات الحديثة تتفق على أن GraphRAG لا يتفوق على RAG العادي إلا في أسئلة multi-hop والتلخيص الشامل، وأنه يخسر في الأسئلة أحادية القفزة والتفاصيل الدقيقة:
- Han et al., *RAG vs. GraphRAG: A Systematic Evaluation and Key Insights*, arXiv:2502.11371 (2025-02، v3 2026-03): https://arxiv.org/abs/2502.11371
- Xiang et al., *When to use Graphs in RAG* (GraphRAG-Bench), arXiv:2506.05690, ICLR 2026: https://arxiv.org/abs/2506.05690. يوثّق أن GraphRAG "frequently underperforms vanilla RAG" وأن أداءه شديد الاعتماد على قدرة النموذج.

قياسات المشروع نفسها تؤكد هذا (§3.4). لذلك **ينبغي أن تكون المرحلة "طبقة معرفة الكتاب" (Book Knowledge Model)** ومنتجاتها الأساسية:
1. **Concept Inventory لكل كتاب:** مفاهيم canonical، مع aliases، وتعريف مقتبس بمواضع (span offsets)، وموضع أول ظهور، ونوع. يغذّي termbase الترجمة (G3)، وglossary الدروس، وقائمة الأعلام.
2. **Pedagogical Relation Graph:** `prerequisite_of` و`defines` و`part_of` و`example_of` و`contrasts_with`، لكل منها شواهد وثقة معايَرة ومصدر استخراج، مع طابور مراجعة بشرية.
3. **Cross-book Concept Alignment:** ربط مفهوم في الكتاب الأساسي بمقابله في الكتاب المساند.
4. **الاسترجاع:** يبقى hybrid RRF مع reranker هو الافتراضي. التوسعة عبر الرسم (concept expansion) تجربة اختيارية **فقط** إذا تفوقت على مجموعة multi-hop موسومة بشريًا (§5.5).

### 5.2 المعمارية (pipeline)
```
ParsedBook (+ char offsets, styles, back-of-book index, glossary pages)
  └─► [A] Candidate generation (حتمي، بلا LLM)
        - فهرس آخر الكتاب، والمصطلحات الغامقة/المائلة، والعناوين، وأقسام glossary
        - noun-phrase chunking + term extraction (C-value / TF-IDF)
  └─► [B] LLM structured extraction عبر llm_gateway فقط
        - نافذة سياق = قسم (section)، لا chunk معزول
        - مخرجات بمواضع (start,end) لا بنسخ اقتباس؛ تحقق offsets حتمي
        - علاقات مفتوحة الصياغة ثم mapping إلى الأنواع المغلقة
  └─► [C] Verifier مستقل (نموذج أو NLI مختلف): "هل الـ span يستلزم العلاقة بهذا الاتجاه؟"
        → confidence معايَرة (calibration على gold)
  └─► [D] Entity Resolution: blocking → قواعد (lemma, acronym, plural) → تشابه تضمين
        → LLM judge للمنطقة الرمادية → EntityMergeProposal(auto_merge | review | reject)
  └─► [E] Prerequisite inference: ترتيب الظهور في الـ spine + اعتماد تعريفي
        (تعريف B يذكر A ⇒ A prereq B، على نمط RefD) + حكم LLM → DAG check
  └─► [F] Cross-book alignment (تضمين + judge + مراجعة)
  └─► [G] Gates حتمية + دلالية → Review queue (HITL) → artifacts بإصدارات
Storage: PostgreSQL (موجود): concepts, aliases, mentions, relations, evidence,
         merge_decisions, review_decisions؛ traversal بـ recursive CTE.
```
مرجعيات هذه الخطوات:
- **LLM-based KG construction:** Microsoft GraphRAG (Edge et al., arXiv:2404.16130, 2024): https://arxiv.org/abs/2404.16130، وLightRAG (Guo et al., arXiv:2410.05779, 2024): https://arxiv.org/abs/2410.05779، وHippoRAG 2 (Gutiérrez et al., arXiv:2502.14802, 2025): https://arxiv.org/abs/2502.14802. كلها تستخدم نمط extract → merge → index. الدرس المأخوذ منها هو فصل الاستخراج عن الدمج، لا استيراد المحرّك نفسه.
- **Prerequisite relations:** RefD (Liang et al., EMNLP 2015): https://aclanthology.org/D15-1193/، وLectureBank (Li et al., AAAI 2019): https://arxiv.org/abs/1811.12181.
- **Entity resolution:** نموذج Fellegi–Sunter، والأداة المعتمدة Splink من وزارة العدل البريطانية (MoJ): https://github.com/moj-analytical-services/splink.
- **قيود الرسم وخصائص المصدر:** W3C SHACL (2017): https://www.w3.org/TR/shacl/، وW3C PROV-O (2013): https://www.w3.org/TR/prov-o/. تكفي إعادة تطبيق القيود نفسها كقيود SQL ونماذج Pydantic.
- **termbase:** W3C SKOS (2009) للمفاهيم والـ aliases: https://www.w3.org/TR/skos-reference/، وISO 30042:2019 (TBX) لتبادل المصطلحات.
- **التخزين:** بحجم مئات إلى آلاف العقد لكل كتاب، يكفي PostgreSQL مع recursive CTE. Neo4j أو AGE تعقيد تشغيلي بلا مبرر، ومخاطر AGE موثقة في D-011.

### 5.3 فئات الأدوات والنماذج (محايدة للنموذج)
- **نموذج استخراج:** LLM يدعم structured output أو constrained decoding بـ JSON schema، محلي افتراضيًا (D-004)، ويمر عبر `llm_gateway` مع ledger الكلفة.
- **Verifier:** نموذج **مختلف** عن المستخرج، أو مُصنّف NLI، لتجنب أن يصدّق النموذج نفسه.
- **Embeddings:** نموذج التضمين المعتمد في Phase 3 (BGE-M3) لـ ER والمحاذاة عبر الكتب.
- **NLP حتمي:** tokenizer وlemmatizer (مثل spaCy) لأشباه الجمل الاسمية وتطبيع المفرد والجمع.
- **أداة توسيم بشري:** Label Studio أو Argilla، وكلاهما مفتوح المصدر ومعتمد.

### 5.4 بوابات الجودة
**حتمية (تُرفض العناصر عند الفشل، ويُسجَّل `reason_code`):**
1. offsets صالحة، والاسم داخل الـ span، والـ span داخل block له page ref.
2. قائمة رفض للكيانات: أرقام حرفية، ومراجع Figure/Table/Exercise/Guided Practice، ورموز ≤ 3 أحرف خارج نوع `formula`، وأسماء ترويسات وتذييلات متكررة.
3. domain/range لكل نوع علاقة، مثل `example_of: example → concept|method`.
4. `prerequisite_of` يجب أن يكون DAG بنسبة 100%. أي حافة تشير إلى درس لاحق في الـ outline تُرفع للمراجعة ولا تُرفض.
5. كل علاقة لها ≥ 1 evidence وclassifier provenance (model, prompt_version, verifier).
6. فحص تسرب الـ prompt في الحقول النصية، وهو الدرس المباشر من الترجمة.
7. الحذف والإضافة لكل كتاب (T-KG-03/04) وعزل النطاق: تُبقى الاختبارات الحالية.

**دلالية:**
- verifier entailment لكل علاقة، وconfidence معايَرة: ECE ≤ 0.05 على gold.
- recall مخزون المفاهيم مقابل **فهرس آخر الكتاب**. هذا مرجع silver حتمي ومستقل عن النماذج، وغير موجود اليوم.
- تدقيق بشري بعينة عشوائية لكل كتاب (§5.5).

### 5.5 نقاط التدخل البشري (HITL)
1. **قبل التشغيل:** إرشادات توسيم مكتوبة. توسيم gold لفصلين من كل كتاب (نحو 150 مفهومًا و200 علاقة و200 زوج ER)، ثلثها موسوم مرتين لحساب Cohen's κ.
2. **لكل كتاب:**
   - مراجعة مقترحات الدمج في المنطقة الرمادية.
   - مراجعة حواف `prerequisite_of` المتعارضة مع الـ outline.
   - تدقيق عينة عشوائية من 100 علاقة.
3. **termbase (G3):** اعتماد المقابل العربي لكل مفهوم canonical مرة واحدة لكل كتاب (`approved_by` إلزامي حسب `02-CONTRACTS.md`).

### 5.6 المقاييس والعتبات المستهدفة
| المقياس | المرجع | العتبة |
|---|---|---|
| Concept precision | gold بشري | ≥ 0.90 (حد Wilson الأدنى 95% ≥ 0.85) |
| Concept recall مقابل فهرس الكتاب | index terms | ≥ 0.80 |
| Concept recall | gold بشري | ≥ 0.80 |
| Relation precision لكل نوع | gold بشري أو عينة | ≥ 0.85 (Wilson ≥ 0.80) |
| `prerequisite_of` precision | gold بشري | ≥ 0.80 |
| ER pairwise F1 | 200 زوج بشري تشمل سلبيات صعبة | ≥ 0.90 |
| Cross-book alignment precision | gold بشري | ≥ 0.85 |
| Provenance validity (offsets والصفحة والكتاب) | حتمي | 100% |
| DAG validity | حتمي | 100% |
| ضوضاء بنيوية مقبولة (فئات §3.3) | حتمي | 0% |
| معايرة الثقة (ECE) | gold | ≤ 0.05 |
| اتفاق الموسّمين κ | مزدوج | ≥ 0.70 |
| (اختياري) graph expansion مقابل hybrid+reranker | مجموعة multi-hop وعبر الكتب موسومة بشريًا، متن ≥ 300 chunk | ΔnDCG@10 ≥ +0.03 مع paired bootstrap p<0.05. وإلا يُحذف المسار. |

### 5.7 الاختبار
- **Unit:** property-based بـ Hypothesis لتحقق offsets والتطبيع، وجداول حالات لـ domain/range وDAG.
- **Mutation tests للبوابات:** حقن عناصر سيئة معروفة (رقم كـ concept، علاقة معكوسة، تسرب prompt، مرجع Figure، مفرد وجمع منفصلان). يجب أن تلتقطها البوابات **100%**. هذا يمنع تكرار حالة "البوابة تمر لأنها لا تفحص شيئًا".
- **Contract tests** للـ artifacts الجديدة، مع رفع `SCHEMA_VERSION`.
- **Golden regression** في `make eval PHASE=4`: نموذج وprompt مجمّدان. أي تغيير في أحدهما يتطلب إعادة القياس على gold ومقارنة paired.
- **Integration:** كتاب حقيقي كامل (397 chunk) end-to-end، بزمن وكلفة مسجلين وسقف معلن.

---

## 6. تحليل الفجوة وخطة الترحيل

### 6.1 ما يُبقى
- نموذج `ConceptEvidence` ومنطق `delete_book` وعزل `scope_id` (`concepts.py`)، واختبارات T-KG-03/04 المقابلة.
- نمط `PostgresConceptRelationStore`: معاملة واحدة لكل batch، ورفض البيانات الوصفية المتضاربة. يُبقى بعد نقله إلى migrations وتوسيعه بجداول الكيانات.
- مبدأ "الـ provenance تُشتق من الـ chunk ولا تُقبل من النموذج" (`extraction.py`)، وتصميم `ExtractionRejection` بأكواد مغلقة لا تحوي نص المصدر.
- تقارير Phase 4 وPhase 6 كسجل بحثي (مع إضافة ملاحظة §3.4 عليها).

### 6.2 ما يُعاد كتابته
- `extraction.py`: يُحذف `_relation_type_is_explicit`. تُستبدل آلية `evidence_quote` بـ offsets. يُضاف سياق القسم والـ verifier والثقة المعايرة. يُستخدم `llm_gateway` حصرًا.
- هوية الكيانات: `concept_id` مستقر (UUID أو hash لـ canonical + book scope)، مع جدول aliases وإنتاج `EntityMergeProposal`.
- `ConceptGraphRetriever` (إن بقي): يقرأ بـ SQL مفهرس لا بمسح كامل، ويطابق عبر الـ aliases.
- العقود: `02-CONTRACTS.md` §2 و§5، و`models.py`: إضافة `Concept` و`ConceptAlias` و`ConceptMention(span)`، وتعديل `ConceptEdge` (evidence spans وconfidence معايرة وverifier). يُرفع `SCHEMA_VERSION` من 1.2.0.

### 6.3 ما يُحذف من مسار التشغيل
يُحفظ في git history أو أرشيف `eval/`:
- `packages/indexing/src/b2c_indexing/graph_native.py` و`graph_api.py` و`graph_runtime.py` و`graph_router.py` و`graph_adapters.py`.
- `docker/graph/*` و`docker/compose.graph-services.yml` و`docker/compose.lightrag.yml`.
- فرع `lightrag/hipporag` في `qa.py:384-399` وقراءة `GraphProviderConfig` في `worker.py:109-114`.
- `b2c_kg.graph_retriever.GraphRetrieverAdapter` و`eval/suites/phase6/hipporag2_bridge.py` (أرشيف).
- يمكن إعادة إدخال محرك جاهز لاحقًا **كأداة تقييم معزولة** إذا أثبت تجربة §5.6 قيمة.

### 6.4 الخطوات المرتبة
1. **احتواء فوري (لا يحتاج قرارًا تصميميًا):** تعطيل مسار الرسم في QA، وحذف الافتراضي الخارجي من `compose.graph-services.yml` أو حذف الملف. توثيق ذلك في `PROJECT-STATUS.md`.
2. **ADR جديد يحل محل D-011:** "المرحلة 4 = Book Knowledge Model لخدمة termbase وglossary وprerequisites. الاسترجاع عبر الرسم خارج النطاق حتى يثبت قيمته." يتطلب موافقة المالك.
3. **إرشادات التوسيم والـ gold البشري** (§5.5). هذه الخطوة تسبق أي كود استخراج، فبدونها تتكرر المشكلة C2.
4. **العقود والمخطط:** نماذج Pydantic جديدة، وAlembic migrations لجداول concepts وaliases وmentions وrelations وevidence وdecisions.
5. **[A] مولّد المرشحين الحتمي.** يُقاس recall مقابل فهرس الكتاب قبل أي LLM.
6. **[B]+[C] الاستخراج والتحقق** عبر `llm_gateway` محليًا، مع mutation tests للبوابات.
7. **[D] ER** و**[E] prerequisites** و**[F] المحاذاة عبر الكتب.**
8. **Activity في orchestration** تنتج `concept-inventory.json` و`concept-graph.json` و`termbase-candidates.json`، وبوابة G1-KG، وطابور مراجعة.
9. **ربط المستهلكين:**
   - termbase الترجمة: فرض المصطلحات حسب ظهورها في المصدر (يتوافق مع توصية كتالوج الترجمة §2.6).
   - glossary الـ pedagogy.
   - فحص الـ outline ضد `prerequisite_of`.
10. **(اختياري) تجربة concept expansion** على مجموعة multi-hop بشرية. اعتمادها مشروط بالعتبة في §5.6.

### 6.5 الاعتماديات على مراحل أخرى
- **L1 (parsing):** char offsets لكل block، وأنماط الخط (bold/italic)، واكتشاف صفحات الفهرس والـ glossary في الكتاب، وإزالة الترويسات والتذييلات. بدون ذلك تبقى أسماء المؤلفين وأرقام الصفحات ضوضاء.
- **L3 (chunking/indexing):** `chunk_id` مستقر، والتمييز بين `section_path` والـ role.
- **L2 (outline):** ترتيب الدروس للتحقق من DAG.
- **`llm_gateway`:** structured output وledger كلفة وbudget لدفعات طويلة.
- **الترجمة (L7) وG3:** استهلاك termbase واعتماد المقابل العربي.

### 6.6 المخاطر
- **زمن الاستخراج المحلي على CPU:** 64 إلى 95 ثانية لكل chunk في pilot سابق، أي نحو 7 إلى 10 ساعات لكتاب من 400 chunk. التخفيف: المولّد الحتمي يقلص الاستدعاءات، والمعالجة على مستوى القسم batch ليلية، وcheckpointing.
- **كلفة التوسيم البشري:** نحو 2 إلى 3 أيام عمل خبير لكل كتابين في البداية. هذا هو الثمن الحتمي لمعرفة الجودة الحقيقية.
- **الكتب الإنسانية** (مثل كتاب غرامشي في الترجمة): مفاهيمها مجردة وتعريفاتها ضمنية. أنماط الاستخراج المبنية على كتب الإحصاء لن تعمم، فيلزم gold من كل جنس من الكتب.
- **مقاومة حذف الكود الموجود:** ~1,000 سطر واختبارات تمر. الحجة المضادة: لا مستخدم ولا قيمة مقاسة، والمسار يخالف القواعد 5 و6.

---

## 7. معايير القبول (متى تُعتبر المرحلة "منتهية")

المرحلة منتهية فقط عندما يتحقق **كل** ما يلي على **كتابين حقيقيين على الأقل من جنسين مختلفين** (تقني وإنساني)، والأدلة مسجلة في `eval/reports/phase-4-*.md`:

1. **وجود في الإنتاج:** لكل كتاب مُعالج توجد artifacts `concept-inventory.json` و`concept-graph.json` و`termbase-candidates.json` بإصدار، وصفوف مقابلة في Postgres. العدد صفر اليوم.
2. **gold بشري:** إرشادات توسيم منشورة، وgold بالحجم المحدد في §5.5، وκ ≥ 0.70 على الجزء المزدوج.
3. **العتبات** في جدول §5.6، كلها مقاسة على gold البشري، مع حدود Wilson.
4. **البوابات الحتمية:** صفر عناصر مقبولة من فئات الضوضاء في §3.3، و100% provenance validity، و100% DAG.
5. **Mutation suite للبوابات:** كل عنصر سيئ محقون يُلتقط (100%)، ويعمل في CI.
6. **الاستهلاك الفعلي:**
   - الترجمة تستخدم termbase المعتمد.
   - إعادة قياس فئة `terminology` في كتالوج الترجمة على الكتب نفسها تُظهر انخفاض المقاطع المقبولة بخطأ مصطلحي كبير أو حرج بنسبة ≥ 70% عن 716.
   - الـ outline يُفحص ضد `prerequisite_of`.
7. **الامتثال:**
   - صفر استدعاءات LLM خارج `llm_gateway` (فحص import-linter أو grep في CI على `openai`/`lightrag.llm` خارج الـ gateway).
   - صفر إرسال لنص كتاب حقيقي إلى endpoint خارجي في الإعداد الافتراضي.
   - كل الحزم والنماذج مثبتة بإصدارات في `04-OPERATIONS.md`.
8. **العمليات:** معالجة كتاب من ~400 chunk تكتمل ضمن سقف زمني وكلفة معلنين مسبقًا، مع استئناف من checkpoint بعد إعادة التشغيل دون إعادة استخراج المكتمل.
9. **الوثائق متطابقة مع الكود:**
   - `02-CONTRACTS.md` §2 و§5.
   - `00-DECISIONS.md` (ADR جديد).
   - `03-TESTING.md` (T-KG-01/02 تعود إلى مرجع بشري، وتُحذف عبارة "لا تُسمّى gold" كبديل).
   - `01-ARCHITECTURE.md:112`.
10. **إن بقي أي مسار استرجاع عبر الرسم:** يجتاز شرط ΔnDCG@10 في §5.6 على مجموعة multi-hop وعبر الكتب موسومة بشريًا، ومتن ≥ 300 chunk. وإلا يُحذف.

---

### ملحق: ما نُفّذ للقياس
- **Postgres (قراءة):** عدّ `b2c_concept_relations` و`b2c_concept_evidence` و`b2c_indexed_chunks` حسب المقرر والكتاب والدور. وعدّ عبارات الأنماط الدالة على 1,206 chunk داخل الـ worker.
- **S3 (قراءة):** سرد 17,800 مفتاحًا، وقراءة 33 `course-qa.json` و7 `input.json`.
- **ملفات تقييم:**
  - `eval/reports/phase4/*.json`
  - `/mnt/wsl/b2cdata/phase4-kg-eval-20261004/phase4-deepseek-3book-entities-final.json` (386 كيانًا)
  - `/mnt/wsl/b2cdata/phase1-sources/phase3-retrieval-50-diagnostic.json`
- **اختبارات:** `pytest -p no:cacheprovider -q tests/unit/kg tests/unit/indexing/test_graph_runtime.py tests/unit/indexing/test_graph_adapters.py tests/unit/eval/test_phase6_bridge.py` = 96 passed.
- **UNVERIFIED:**
  - تطابق ترتيب LightRAG مع dense على مستوى كل سؤال.
  - الأثر الكمي للـ source tag.
  - زمن بناء الرسم لكتاب كامل.
  - موافقة المالك الصريحة على إرسال نصوص الكتب المفتوحة إلى OpenRouter في Phase 4 وPhase 6.
