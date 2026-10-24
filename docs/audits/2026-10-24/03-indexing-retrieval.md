# تدقيق المرحلة 03: التقسيم (Chunking) والفهرسة والـ Embeddings والاسترجاع

- **التاريخ:** 2026-10-24
- **الفرع:** `hardening`
- **النطاق:** `packages/indexing`، و`packages/orchestration/.../indexing.py`، واستهلاك الاسترجاع في `qa.py` و`qa_generation.py`، و`eval/suites/phase3`، و`eval/reports/phase-3*.md`، والبيانات الحية في Postgres (`b2c_indexed_chunks`) وMinIO (`artifacts`).
- **المنهج:** قراءة فقط. كل القياسات تمت بسكربتات مؤقتة داخل `b2c-worker-1` في وضع قراءة فقط عبر psycopg وboto3، وباستخدام دوال المشروع نفسها (`chunk_book` و`QueryRetrievalService` و`_derive_scopes`) لمحاكاة ما يحدث فعلًا. لم يُرسل أي نص من الكتب لخدمة خارجية.
- **مبدأ التدقيق:** عبارة "PASS" أو "completed" لا تعني أن النتيجة صحيحة. تدقيق الترجمة (`outputs/translation-review-2026-10-24/TRANSLATION-ERROR-CATALOGUE.md`) وجد أن 57% من الترجمات "المقبولة" كانت خاطئة لأن البوابات فحصت الشكل فقط. هذه المرحلة فيها النمط نفسه، وسأوضحه بالأرقام.

---

## 1. غرض المرحلة وعقدها

### 1.1 الغرض
تحويل `ParsedBook` (الكتاب الأساسي spine والكتب المساندة supplementary) والمخطط المعتمد `CourseOutline` إلى وحدات نصية قابلة للاسترجاع ومُسندة لمصدرها (`StructuredChunk`). وظيفتها بعد ذلك:
1. تزويد مراحل التأليف (الإثراء والأسئلة والملخصات) بنص **كامل ونظيف** لكل درس.
2. ربط كل درس بأدلة **ذات صلة فعلية** من الكتب المساندة.
3. توفير citation قابل للتحقق (كتاب، دور، صفحة، block).
4. (لاحقًا) الإجابة عن أسئلة المتعلم، وقد تكون أسئلة عربية على مصدر إنجليزي.

### 1.2 العقد كما هو موثق
- `docs/02-CONTRACTS.md:50`: `L3 ← L4 | StructuredChunk[] | لا يوجد chunk يعبر حدود section_path، وembedding_model ثابت | retry`.
- `docs/01-ARCHITECTURE.md:112`: كل chunk مفهرس يجب أن يحمل `course_id` و`book_id` والـ role، ومرجع الصفحة/الـ block، وhash، وإصدارات الـ parser والنموذج، وحقوق الوصول.
- `docs/05-PHASES.md:110-112`: chunker يراعي البنية، بطول 300–800 token، وoverlap بين 10 و15%. الاسترجاع bge-m3 **dense+sparse** مع `bge-reranker-v2-m3`، ومقارنة dense مقابل hybrid مع rerank وبدونه.
- `docs/00-DECISIONS.md:28` ‏(D-012): bge-m3 للـ embeddings وbge-reranker-v2-m3 للـ reranker، وحالته Accepted.
- `docs/03-TESTING.md:66-67`: ‏T-IDX-01 (لا يوجد chunk يعبر قسمًا ولا جدول مقسوم، 100%)، وT-IDX-02 ‏(Recall@10 ≥ 0.85 وnDCG@10 ≥ 0.6 على 50 استعلامًا موسومًا).
- `docs/06-DELIVERY-PLAN.md:63,92`: Recall@k وMRR/nDCG، ودقة الإحالات، واختبارات خلط المصادر والعزل والتحديث والحذف.

### 1.3 ما تحتاجه المراحل اللاحقة فعلًا (من الكود)
- **QA/Pedagogy** (`packages/pedagogy/src/b2c_pedagogy/qa_generation.py:252-330`): تحتاج تقسيم كل درس إلى chunks لا تعبر حدوده، ثم "ترتيبها" بالاسترجاع (`:607-616`).
- **Lesson enrichment** (`packages/orchestration/src/b2c_orchestration/qa.py:454-466`): تحتاج أفضل 5 مقاطع مساندة لكل درس (`conflict_candidates`).
- **KG** (`packages/kg/src/b2c_kg/extraction.py`): تستهلك `StructuredChunk`.
- **الترجمة والصوت لا تستهلكان chunks الفهرسة**؛ تعملان على وحدات خاصة بهما. لذلك جودة النص الذي يدخل الكورس تأتي من ingestion مباشرة، وعيوب ingestion تصل للفهرس كما هي (انظر §3).

---

## 2. التصميم المنفذ فعلًا (وليس الموثق)

| المكون | ما يفعله الكود فعلًا | الدليل |
|---|---|---|
| Chunker | يجمع blocks متتالية داخل "قسم" (lesson span من المخطط المعتمد للـ spine، أو `section_path` للمساند) حتى `target_tokens=500`. الـ block ذرّي: لا يُقسم أبدًا مهما كان حجمه. الـ overlap يحمل blocks **كاملة** فقط إذا اتسعت لـ 12% × 500 = 60 token | `packages/indexing/src/b2c_indexing/chunking.py:69-117` |
| عدّ الـ tokens | تقدير بـ regex ‏(`\w+|[^\w\s]`) وليس tokenizer النموذج | `chunking.py:9-18` |
| الحد الأدنى للحجم | **لا يوجد**. لا دمج للـ chunks الصغيرة، ولا حد أعلى للـ block الكبير | `chunking.py:91-115` |
| نوع الـ block | **يُتجاهل**. ‏`Block.type` فيه `page_header` و`page_footer` و`footnote` و`caption`، لكن الـ chunker لا يستبعد أو يميز أيًا منها | `packages/contracts/src/b2c_contracts/models.py:91-104`، و`chunking.py:26-33` |
| هوية الـ chunk | ‏`sha256(book_id + block_ids)[:20]`. لا يدخل فيها النص ولا إصدار الـ chunker أو الـ parser | `chunking.py:21-23` |
| `embedding_model` | القيمة الافتراضية `"unassigned"` في الـ chunker وفي مدخل الـ workflow، ولا يمررها أي مستدعٍ | `chunking.py:70`، و`orchestration/indexing.py:181` |
| التخزين | جدول `b2c_indexed_chunks`: ‏payload بصيغة JSONB، وعمود `embedding` بصيغة **JSONB** (وليس `vector`). لا يوجد فهرس ANN ولا فهرس FTS | `packages/indexing/src/b2c_indexing/postgres_store.py:1-31` |
| الكتابة | `replace_book`: ‏DELETE ثم INSERT لكل (course, book) داخل transaction. لا يوجد GC للكتب التي خرجت من الكورس | `postgres_store.py:67-82` |
| Lexical | BM25 مكتوب بـ Python. يحمّل كل chunks النطاق ويحسب IDF **على مجموعة المرشحين فقط** (قد تكون 1–4 chunks). بدون stemming أو stopwords، والتطبيع casefold فقط | `packages/indexing/src/b2c_indexing/retrieval.py:13-40` |
| Dense | **يعيد حساب embedding لكل passage مع كل استعلام**، لأنه لا توجد vectors مخزنة. الـ cache من نوع LRU بحجم 512 | `retrieval.py:41-59`، و`embedding_client.py:12-58` |
| Hybrid | RRF ‏(k=60) بين lexical وdense. bge-m3 sparse **غير مستخدم** | `retrieval.py:60-73` |
| Reranker | **غير موجود في المسار الإنتاجي**. استُخدم فقط في سكربتات eval | `grep rerank packages` يعطي `provenance_checks.py:114` فقط (قيمة ثابتة `rerank_score=1.0`) |
| الإعداد الحي | `B2C_RETRIEVAL_MODE=lexical`، و`B2C_EMBEDDING_BASE_URL` فارغ، و`B2C_RETRIEVAL_TOP_K=20` | `docker exec b2c-worker-1 env` |
| استخدام الاسترجاع في الإنتاج | (أ) داخل كل درس: الاستعلام = عنوان النطاق + عناوين الدروس، والنتيجة أفضل 20 chunk **من chunks الدرس نفسه**. (ب) للمساند: الاستعلام = العنوان + أول 600 حرف من أول 4 chunks، ثم top-5 بدون أي عتبة | `qa_generation.py:607-616`، و`qa.py:459-466` |
| Graph | ‏LightRAG/HippoRAG (حوالي 600 سطر في `graph_*.py`) ما زالت موصولة بمسار QA عبر `retrieval_mode in {"lightrag","hipporag"}` رغم قرار NO-GO | `qa.py:383-399`، و`docs/05-PHASES.md:152` |

### 2.1 تعارضات بين التوثيق والكود
1. **"bge-m3 dense+sparse + reranker" (‏D-012، و05-PHASES:111)** مقابل الإنتاج الفعلي: lexical فقط. لا يوجد أي vector مخزن (0 من 1206 صفوف، §3.1)، والـ sparse غير منفذ، ولا يوجد reranker.
2. **"embedding_model ثابت" (02-CONTRACTS:50)، والشكل `"BAAI/bge-m3@<revision>"` (models.py:186)** مقابل القيمة الفعلية `"unassigned"` في 100% من الصفوف.
3. **"300–800 token" (05-PHASES:110)**: لا يوجد حد أدنى، والحد الأعلى غير مفروض على الـ block الواحد. القياس: chunks حجمها 1 token، وأخرى حجمها 1480 (§3.2).
4. **"hash، وإصدارات الـ parser/النموذج، وحقوق الوصول" (01-ARCHITECTURE:112)**: الـ `StructuredChunk` لا يحمل أيًا منها (models.py:177-186).
5. **"T-IDX-02 PASS" (eval/reports/phase-3.md:196-207)**: هذا التقييم **لم يقِس الـ chunker الإنتاجي أصلًا**. مجموعة التقييم 37 chunk بطول ثابت تقريبًا (383–393 كلمة)، ومعرّفاتها من نوع `lane-intro-stats:ch01`. أما `chunk_book` فينتج معرّفات `{book}:chunk:{hash}` (§3.5).
6. **"QueryRetrievalService ... not exposed as an HTTP endpoint" (phase-3.md:20)**: صحيح، لكن الاسترجاع يُستخدم داخل QA كـ **مرشح محتوى**، وهذا الاستخدام غير موثق كمخاطرة (§3.3).
7. **D-027 (استبعاد front/back matter)** موثق ومقبول، لكنه غير فعّال على الكتاب الحي: 2620 من 2620 block داخل الدروس، بما فيها الفهرس والحواشي (§3.4).

---

## 3. الجودة المقاسة

### 3.1 حالة الفهرس الحي (Postgres)
استعلام قراءة فقط على `b2c_indexed_chunks`:

| course | book | role | chunks | vectors | updated |
|---|---|---|---:|---:|---|
| phase5-live-tidx01-20261004 | lane-ch5-probability | spine | 41 | 0 | 2026-10-04 |
| phase5-live-tidx01-20261004 | think-stats-2e | supplementary | 261 | 0 | 2026-10-04 |
| b2c-demo | book-main | spine | 397 | 0 | 2026-10-05 |
| b2c-demo | book-70c88ae9b2ba9ed6f61e813c | spine | 507 | 0 | 2026-10-10 |

- **0 من 1206** صفوف لها embedding. امتداد `vector 0.8.6` مثبت لكنه غير مستخدم. الفهارس الموجودة btree فقط (pkey وscope).
- الكورس `b2c-demo` فيه **كتابان spine** في الوقت نفسه. المخطط المعتمد الأخير (`b2c-demo/approve-outline/97f28cec.../approved-outline.json`، ‏spine=`book-70c...`) لا يذكر `book-main`. صفوف `book-main` (397) يتيمة: لا يوجد لها snapshot في `b2c-demo/index-approved-book/` (يوجد snapshot واحد فقط، `970875ba...`، وهو لـ `book-70c`). الـ QA يرشّح الكتب حسب المخطط، لذلك لا تتسرب حاليًا، لكن الفهرس لا يملك GC ولا مصدر حقيقة واحدًا.
- لوحظ أيضًا أن parsed artifact ‏`0d97db6f` (book-main) و`469d6f0f` (book-70c) لهما نفس عدد الـ blocks ‏(2620). هذا مؤشر على أن الكتاب نفسه موجود بهويتين مختلفتين، وهو أمر يخص مرحلة الرفع/الهوية. **UNVERIFIED** إن كانا متطابقين نصيًا.

### 3.2 توزيع الأحجام وسلامة النص (كل الـ chunks الحية)
`estimate_tokens` هو مقدّر المشروع نفسه:

| book | n | min / p5 / p50 / p95 / max | <50 | >800 | chunks لها block مكرر (overlap) |
|---|---:|---|---:|---:|---|
| book-70c (Docling OCR) | 507 | 6 / 154 / 410 / 498 / 653 | 3 | 0 | 279 block مكرر من 2620 |
| book-main | 397 | 1 / 155 / 359 / 563 / 814 | 8 | 1 | 50 / 776 |
| lane-ch5 (pypdf) | 41 | 92 / 110 / 412 / 1141 / 1480 | 0 | 3 | 0 / 42 |
| think-stats-2e (pypdf) | 261 | 1 / 2 / 338 / 496 / 715 | 20 | 0 | 1 / 421 |

ملاحظات مقاسة:
- **الـ overlap شبه منعدم** في الكتب التي blocks فيها فقرات كاملة: ‏lane ‏0 block مشترك، وthink-stats ‏1. السبب أن الـ overlap يحمل blocks كاملة لا تتجاوز 60 token (`chunking.py:96-107`). نسبة الـ 10–15% الموثقة لا تتحقق فعليًا.
- **chunks عبارة عن عنوان فقط:** مثل `'Preface'` ‏(1 token)، و`'Distributions'`، و`'Plotting histograms'` ‏(2 token)، و`'abbre v i at i ons'`. في think-stats ‏p5 = 2 tokens، أي أن 5% من الـ chunks عناوين فقط.
- **نص تالف بالكامل يدخل الفهرس:** في lane-ch5 يوجد 9 من 41 chunk (22%) نصها بايتات تحكم ناتجة عن font encoding معطوب. مثال: أكبر chunk ‏(1480 "token"، صفحة 36) يبدأ بـ `'\x14\x1e\x1e-\x01 )$#\x01$\x18\x14...'`. هذا النص "مقبول" بكل البوابات الحالية.
- **كلمات مفككة من OCR:** في book-70c يوجد 66 من 507 chunk (13%) فيها النمط `xx i yy` مثل `'af t er the spr i ng'` و`'the art i cul at i on of s truggl es'`. ونفس التفكك موجود في `section_path` الذي يصبح عنوان الدرس (`'conclus i on'`، و`'the war of pos i t i on'`). في lane-ch5 مثال: `'N O T g e t t i n g a 1 o n t h e f i r s t t h r o w'`. هذا يكسر BM25 تمامًا، لأن `position` تصبح tokens ‏`pos`،`i`،`t`،`i`،`on`.
- **hyphen breaks** (كلمات مقطوعة بشرطة عند نهاية السطر): ‏174 من 261 chunk في think-stats ‏(67%)، و56 من 397 في book-main. المقياس regex تقريبي.
- **تكرار ترويسات بنيوية:** في book-main تتكرر `'When to Use It'` و`'The Method'` في 53 chunk. هذه عناوين داخلية يجب أن تكون metadata وليست نص استرجاع.

### 3.3 محتوى غير تعليمي داخل الدروس
صنّفت الـ chunks بقواعد heuristic، ثم تحققت يدويًا من عينات. فئة "toc" في book-70c فيها false positives بسبب علامات الحذف `. . .` داخل الاقتباسات، لذلك لم أعتمدها:

| book | body | index-like | notes/bibliography | garbled | fragment <20 كلمة | نسبة غير التعليمي |
|---|---:|---:|---:|---:|---:|---|
| book-70c | 418 | 22 | 45 | 0 | 2 | **≈13–18%** |
| book-main | 381 | 0 | 6 | 0 | 8 | ≈4% |
| lane-ch5 | 32 | 0 | 0 | 9 | 0 | **22%** |
| think-stats-2e | 224 | 9 (+8 TOC بنقاط قيادية، تحققت منها) | 0 | 0 | 20 | **≈14%** |

أمثلة تحققت منها: chunk صفحة 409–410 نصه `'142-43, 174, 183, 215, 241, 277-78, 280; popular culture, 96; ...'` (فهرس الكتاب)، وchunk صفحة 365 نصه `'SSG, 10; Q25, §5.\n\nSPN, 52, ftn 2. ...'` (حواشٍ). **الاثنان داخل درس اسمه `conclus i on`، والدرس فيه 52 chunk**. أي أن فهرس الكتاب وحواشيه صارت محتوى درس يُولّد منه أسئلة. القياس: 2620 من 2620 block داخل lesson spans، والدرس الأكبر 52 chunk، و17 درسًا من 114 فيها chunk واحد فقط.

### 3.4 الاسترجاع داخل الدرس يحذف محتوى بصمت (قياس مباشر)
أعدت تشغيل منطق `qa_generation.py:607-616` حرفيًا على الكورس الحي (`b2c-demo`، ‏114 درسًا، و507 chunk، وmode=`lexical`، و`top_k=20`، وهي الإعدادات المسجلة في `b2c-demo/generate-course-qa/9143aefc.../course-qa.json`):
- في **23 من 114 درسًا (20%)** حصل التوليد على chunks أقل مما يحتويه الدرس.
- **76 من 507 chunk (15%) من محتوى الدروس لم تصل لمولّد الأسئلة.**
- السبب: ‏`lexical_search` يعيد فقط الـ chunks التي score لها > 0، والاستعلام هو عنوان الدرس فقط. عندما يكون العنوان مفككًا (`'contr ad i c tory consc i ousness'`) يُحذف 3 chunks من 5. أمثلة: `conservat i sm` ‏3→1، و`cr i t i c al approaches` ‏12→9، و`my th close at hand` ‏4→2.
- هذا أسوأ نوع من الأعطال: لا يرفع خطأ، والـ provenance سليم، والأسئلة "grounded"، لكن **تغطية الدرس ناقصة**. ونطاق الدرس معروف مسبقًا بالضبط من المخطط، فلا يوجد سبب لـ "استرجاع" داخله أصلًا.

### 3.5 ربط الكتب المساندة: النتائج غير ذات صلة ولا توجد عتبة
محاكاة `qa.py:459-466` على الكورس `phase5-live-tidx01-20261004` ‏(Lane فصل Probability كـ spine، وThink Stats كاملًا كمساند)، بأفضل 5 لكل درس:

| درس spine | أعلى نتيجة مساندة | تقييمي |
|---|---|---|
| Introduction | `Relationships between variables`، ثم `Preface` | غير ذي صلة |
| Permutations and Combinations | `Hypothesis testing > Replication` | غير ذي صلة |
| Poisson Distribution | `Estimation > Sampling distributions` | غير ذي صلة (Think Stats فيه فصل Analytic methods/Poisson، ولم يظهر) |
| Base Rates | `Hypothesis testing > Power` | ضعيف |
| Exercises | `Estimation > Guess the variance` | عشوائي |

- كل الـ scores بين 25 و67، أي أعلى بكثير من عتبة الامتناع المعايرة 11.02 (`phase-3.md:156`). معنى ذلك أن العتبة لم تكن لتمنع شيئًا. السبب أن الاستعلام 2400 حرف من النص الحرفي بدون stopwords، فيطابق أي نص إحصائي.
- هذه المقاطع تدخل الإثراء كـ "مصادر مساندة/متعارضة". حكمي هنا نوعي على 10 دروس. دقة الربط الكمية **UNVERIFIED** لعدم وجود qrels بشرية، لكن 0 من 10 دروس كانت نتيجتها الأولى واضحة الصلة.

### 3.6 تقييم T-IDX-02 المسجل لا يصلح كدليل
- **المجموعة:** 37 chunk ‏(`/mnt/wsl/b2cdata/phase1-sources/lane-primary-30-chunks.jsonl` + `thinkstats-supplement-7-chunks.jsonl`) بطول 383–393 كلمة، وهي **نوافذ ثابتة وليست ناتج `chunk_book`**. أي أن التقييم لا يقيس الـ chunker الإنتاجي ولا الأعطال المذكورة في §3.2.
- **Recall@10 على 37 مستندًا شبه بلا معنى:** الاسترجاع العشوائي يعطي 10/37 ≈ 0.27، وأي نظام لديه أدنى إشارة يصل لـ 0.95. هذا ما حدث بالضبط: lexical ‏0.969، وdense ‏0.949 (`phase-3.md:200-205`).
- **انحياز للمطابقة الحرفية:** الأسئلة كلها `direct`، وكل سؤال له qrel واحد فقط، وولّدها LLM من نص الـ chunk نفسه. قِست تداخل كلمات المحتوى بين السؤال والـ chunk الذهبي: median ‏0.70، و16 من 50 سؤالًا ≥ 0.8. هذا يفسر لماذا يبدو lexical قويًا.
- **لا توجد أسئلة:** عربية، ولا paraphrase حقيقية (20 paraphrase موجودة في auto-audit-v2 لكنها خارج حساب T-IDX-02)، ولا multi-hop، ولا cross-book (T-KG-05 أنتج 0 qrel)، ولا "ربط درس بمقطع مساند"، وهي المهمة الإنتاجية الفعلية.
- **الحكم:** "PASS" هنا يعني "شكل التجربة صحيح"، ولا يعني أن الاسترجاع جيد على كتب حقيقية. هذا نفس نمط فشل بوابات الترجمة.

### 3.7 الاختبارات الآلية
`tests/unit/indexing`: الـ tests الخاصة بالـ chunker والاسترجاع (حوالي 21 اختبارًا في `test_outline_chunking.py` و`test_indexing_baselines.py` و`test_query_service.py` و`test_postgres_store.py`) كلها تفحص **الشكل والهوية والنطاق**: الحتمية، وعدم عبور الأقسام، وفلاتر book/role، والـ idempotency. **لا يوجد اختبار واحد لجودة المحتوى**: نص تالف، أو chunk عنوان فقط، أو حد أدنى/أعلى للحجم، أو تغطية الدرس، أو صلة الربط المساند.

---

## 4. المشاكل مرتبة حسب الخطورة

### حرجة (Critical)

**C1. "استرجاع" داخل نطاق الدرس يحذف 15% من محتوى الدروس بصمت**
- الدليل: §3.4 ‏(23/114 درسًا، و76/507 chunk)، و`qa_generation.py:607-616`، و`retrieval.py:36-38` (يعيد score>0 فقط).
- الأثر على الكورس: أسئلة وإثراء لا يغطيان الدرس كاملًا، دون أي مؤشر في التقارير. ويزيد الأثر في الدروس ذات العناوين المفككة من OCR.
- السبب الجذري: خلط بين مهمتين. **coverage** (نطاق معروف ويجب استهلاكه كاملًا) و**retrieval** (بحث عن دليل غير معروف المكان). الاستعلام عنوان، وIDF يُحسب على 1–52 مستندًا فقط.

**C2. لا توجد بوابة جودة محتوى: نص تالف وفهارس وحواشٍ وعناوين فقط تُفهرس كمحتوى تعليمي**
- الدليل: §3.2 و§3.3 (22% تالف في lane، و≈13–18% فهرس/حواشٍ في book-70c، وchunks من 1–2 token). و`chunking.py` يتجاهل `Block.type`.
- الأثر: أسئلة ودروس مبنية على فهرس الكتاب أو بايتات معطوبة، وتلوث BM25 وأي embedding لاحق.
- السبب الجذري: الـ chunker يثق في ingestion بالكامل. لا يوجد contract-level content validation، ولا تصنيف للـ blocks (body/front/back/notes/index)، وD-027 غير مطبق على الكتاب الحي.

**C3. ادعاء "Phase 3 research gate PASS" مبني على تقييم لا يقيس النظام الإنتاجي**
- الدليل: §3.6.
- الأثر: قرارات معمارية (lexical كافٍ، والـ reranker لا يضيف recall) مبنية على corpus من 37 نافذة ثابتة وأسئلة بنسبة تطابق حرفي 0.70.
- السبب الجذري: لا توجد مجموعة تقييم بشرية، ولا pooling، ولا corpus بحجم كتاب حقيقي، وتقييم المهمة الخطأ (QA عام بدل coverage وربط مساند).

### عالية (High)

**H1. ربط الكتب المساندة غير ذي صلة، وبدون عتبة أو حكم صلة**
- الدليل: §3.5، و`qa.py:459-466` ‏(`allow_no_hits=True` بدون threshold)، والـ scores 25–67 أعلى من كل عتبة.
- الأثر: الإثراء يقدّم "مصادر مساندة/متعارضة" لا علاقة لها بالدرس، فيُضلّل المتعلم أو يُدخل تناقضات مصطنعة.
- السبب الجذري: استعلام نصي طويل بدون stopwords أو stemming، ولا يوجد dense أو reranker، ولا توجد عتبة صلة أو LLM-judge.

**H2. القرار D-012 (bge-m3 + reranker) غير منفذ، وdense مصمم بشكل لا يصلح للإنتاج**
- الدليل: 0/1206 vector، و`embedding` من نوع JSONB، ولا يوجد فهرس HNSW، و`dense_search` يعيد embedding كل passage مع كل استعلام (`retrieval.py:52-55`)، والـ LRU بحجم 512 أصغر من كتاب واحد (507 chunk).
- الأثر: لا يوجد بحث دلالي، ولا cross-lingual (عربي→إنجليزي). مسار hybrid لو فُعّل سيكلف O(N) embeddings لكل استعلام.
- السبب الجذري: التأجيل تحت شعار "model-agnostic" تحول إلى غياب للتنفيذ. الفهرسة يجب أن تكون offline مع vectors مخزنة.

**H3. هوية الـ chunk ونسخه لا تضمن قابلية التتبع**
- الدليل: `chunk_id = sha256(book_id + block_ids)` (`chunking.py:21-23`)، ولا text hash، ولا chunker/parser/embedding version في الـ payload (`models.py:177-186`)، و`embedding_model="unassigned"`.
- الأثر: إذا أُعيد parsing بنفس block IDs ‏(`book:page:seq`) بنص مختلف، يبقى `chunk_id` كما هو بينما يتغير النص، فتشير الـ citations القديمة لنص مختلف دون أن يُكتشف ذلك. ولا يمكن معرفة أي إصدار أنتج أي chunk.
- السبب الجذري: الـ provenance مطلوب في 01-ARCHITECTURE:112، لكنه غير موجود في العقد.

**H4. الفهرس ليس له مصدر حقيقة ولا GC**
- الدليل: ‏397 صفًا يتيمًا لـ `book-main` في `b2c-demo` دون snapshot أو مخطط حالي (§3.1). و`replace_book` يعمل لكل كتاب على حدة ولا يحذف كتبًا أُزيلت من الكورس.
- الأثر: أي مستهلك يقرأ `list_chunks(course_id)` بدون فلتر كتب سيرى كتابًا قديمًا. الـ QA يحمي نفسه بفلتر، لكن ليس كل المستهلكين يفعلون.
- السبب الجذري: لا يوجد مفهوم **index version/generation** لكل كورس مع تبديل ذري (alias swap).

### متوسطة (Medium)

**M1. الأحجام لا تحترم العقد:** ‏chunk بحجم 1480، وchunks بحجم 1–2 token، والـ overlap فعليًا 0 في كتب الفقرات (§3.2). السبب: block ذرّي بلا تقسيم احتياطي (sentence split)، ولا دمج للصغير، وoverlap على مستوى block وليس جملة.

**M2. عدّ الـ tokens تقديري وليس tokenizer النموذج** (`chunking.py:13-18`). الحدود الفعلية لنماذج الـ embedding/reranker (8192 لـ bge-m3، و512 أو 2048 لنماذج أصغر) لا تُفحص، وقد يحدث truncation صامت إذا تغير النموذج.

**M3. BM25 بدائي:** بدون English analyzer (stemming/stopwords)، وIDF على مجموعة المرشحين بدل الـ corpus، وتحميل كل الـ chunks في الذاكرة لكل استعلام (`retrieval.py:22-25`، و`query_service.py:38`). لا يتوسع مع الحجم ولا يعالج الصيغ الصرفية (probability/probabilities).

**M4. كود graph ‏(LightRAG/HippoRAG) ما زال موصولًا بالإنتاج رغم NO-GO** (`qa.py:383-399`، و`graph_*.py`). يزيد سطح الهجوم والصيانة (05-PHASES:130 يذكر مشكلة `eval()`).

**M5. الـ metadata البنيوية مدفونة في النص:** العناوين الداخلية المتكررة ('When to Use It' ×53) وعناوين الأقسام لا تُستخدم كحقول. لا يوجد contextual header لكل chunk (عنوان الكتاب، والفصل، والقسم)، وهذه ممارسة تحسن الاسترجاع بوضوح (انظر §5).

### منخفضة (Low)

**L1.** ‏`QueryRetrievalService.search` و`search_candidates` يكرران نفس منطق التحقق (`query_service.py:24-62`).
**L2.** ‏`ensure_schema()` يُستدعى داخل كل activity (`orchestration/indexing.py:242`) بدل migrations مُدارة.
**L3.** ‏`min_score` معاير على corpus واحد وscorer واحد. وجوده في الـ API يعطي انطباعًا بأمان غير موجود.

---

## 5. التصميم المستهدف وفق معايير الصناعة

### 5.1 مبدأ أساسي: فصل ثلاث مهام
| المهمة | الطريقة الصحيحة | لماذا |
|---|---|---|
| **Coverage** (محتوى الدرس) | استهلاك **كل** الـ chunks داخل lesson span بالترتيب، بلا استرجاع | النطاق معروف حتميًا من المخطط المعتمد. الاسترجاع هنا يحذف فقط (C1) |
| **Linking** (درس ← مقاطع مساندة) | hybrid retrieval، ثم reranker، ثم عتبة صلة معايرة، ثم LLM-judge لصلة/تعارض، مع "لا يوجد" كنتيجة مقبولة | المهمة بحث فعلي عبر كتب، والدقة أهم من الاستدعاء |
| **QA/tutor** (سؤال متعلم، قد يكون عربيًا) | hybrid متعدد اللغات + reranker + abstention | يحتاج cross-lingual dense |

### 5.2 خط الفهرسة (offline، حتمي، ومُصدَّر بإصدار)
1. **Pre-chunk content gate (حتمي):**
   - تصنيف كل block إلى `body | front_matter | back_matter(index, bibliography, notes) | boilerplate(header/footer) | heading`، باستخدام `Block.type` + قواعد + D-027. ما ليس body يُحفظ بإصدار لكنه **لا يدخل** فهرس المحتوى (يمكن إتاحته كـ citation-only).
   - رفض أو حجر الـ blocks التالفة: نسبة control chars > 1%، أو نسبة tokens من حرف واحد > X%، أو نسبة الكلمات غير القاموسية > Y%. هذه علامات على letter-spacing من OCR أو font encoding معطوب. الـ block المحجور يرجع لـ ingestion، والـ chunker لا يُصلح النص.
   - إصلاح hyphenation وletter-spacing **في ingestion** وليس هنا، لكن الـ gate هنا يقيس ويرفض.
2. **Structure-aware + tokenizer-aware chunking:**
   - المرجع: Docling `HybridChunker` (من `docling-core`، والمشروع يستخدم Docling 2.131 أصلًا)، أو ما يعادله. يعمل بشكل هرمي حسب البنية، ثم يقسم الـ block الكبير على حدود الجمل، ثم يدمج الصغير المتجاور داخل نفس القسم، ويعدّ الـ tokens بـ **tokenizer نموذج الـ embedding نفسه**.
   - الحجم: 256–512 token للاسترجاع (dense models تتدهور مع النصوص الطويلة)، مع overlap جُملي 10–15%. وأهم من ذلك: **الأحجام تُختار بالقياس** على مجموعة التقييم (§5.5)، لا بالافتراض. مرجع: Chroma Research, *Evaluating Chunking Strategies for Retrieval* (Jul 2024): https://research.trychroma.com/evaluating-chunking
   - لا يوجد chunk بعنوان فقط. العنوان يصبح metadata + contextual header.
3. **Contextual header لكل chunk:** سطر ثابت مثل `Book › Chapter › Section` يُضاف للنص المرسل للـ embedding/BM25 (وليس للنص المعروض). وبشكل اختياري يُضاف ملخص سياقي قصير يولده LLM ويُخزن بإصدار. مرجع: Anthropic, *Introducing Contextual Retrieval* (Sep 2024): ‏https://www.anthropic.com/news/contextual-retrieval. التقرير يذكر انخفاض فشل الاسترجاع بنسبة 49% مع contextual embeddings + contextual BM25، و67% مع reranking.
4. **هوية ونسخ:** ‏`chunk_id = hash(book_sha256, block_ids, normalized_text_sha256, chunker_version)`. والـ payload يحمل `text_sha256` و`parser{name,version,options_hash}` و`chunker_version` و`embedding_model@revision` و`index_generation` و`rights_status`.
5. **التخزين في Postgres (لا حاجة لخدمة جديدة):**
   - عمود `embedding vector(d)` (أو `halfvec`) مع فهرس **HNSW** ‏(pgvector ≥ 0.5، والمثبت 0.8.6): https://github.com/pgvector/pgvector
   - Lexical: ‏`tsvector` بـ `english` config وفهرس GIN، أو BM25 حقيقي عبر امتداد مثل ParadeDB `pg_search`. البديل الأبسط مكتبة بايثون مثبتة مثل `bm25s`، لكن الفهرس يُبنى مرة واحدة لا لكل استعلام.
   - `index_generation` لكل كورس، مع تبديل ذري (pointer واحد) وGC للأجيال والكتب القديمة.
6. **Embeddings (model-agnostic مع شروط):** فئة multilingual dense ‏(EN↔AR) بطول سياق ≥ 512، وتدعم أوامر query/document. الحالي حسب D-012 ‏bge-m3: https://arxiv.org/abs/2402.03216 ‏(Chen et al., 2024)، وفيه dense+sparse+multi-vector. الاختيار يتم بالقياس على مجموعة المشروع، مع الاستئناس بـ MMTEB/MTEB (https://huggingface.co/spaces/mteb/leaderboard) وMIRACL للعربية (https://arxiv.org/abs/2210.09984، ‏2022).

### 5.3 خط الاسترجاع (online)
1. Scope filter (course وbooks وroles وrights) **في SQL قبل الترتيب**، وهذا منفذ جزئيًا ويجب الإبقاء عليه.
2. مرشحون: BM25 top-50 + dense top-50.
3. دمج: RRF ‏(k=60). مرجع: Cormack, Clarke & Büttcher, SIGIR 2009: https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf. هذا هو الافتراضي في Elasticsearch/OpenSearch/Vespa.
4. Cross-encoder reranker على top-20–50. ‏bge-reranker-v2-m3 مرشح بحسب D-012، أو أي cross-encoder متعدد اللغات.
5. **Abstention/threshold** على score الـ reranker (معاير لكل مهمة). وللربط المساند: LLM-judge يصنف `supports | contradicts | related | irrelevant`، وما ليس supports أو contradicts لا يدخل الإثراء.
6. Query construction للربط: استعلامات مفهومية (عنوان + 3–5 مفاهيم مستخرجة/مصطلحات من الـ termbase) بدل 2400 حرف خام.

### 5.4 بوابات الجودة
**حتمية (تمنع التقدم):**
- 0 chunks تعبر lesson/section (موجودة).
- 0 chunks تحتوي control chars > 1%، و0 chunks أقل من 50 token (إلا آخر chunk في قسم صغير، مع وسم صريح)، و0 chunks أكبر من max tokens بالـ tokenizer الفعلي، و0 truncation.
- تغطية: كل body block داخل lesson spans يظهر في chunk واحد على الأقل (100%)، ولا يوجد non-body block في فهرس المحتوى.
- `embedding_model` ≠ `unassigned`، وعدد الـ vectors = عدد الـ chunks، والـ dimension ثابت، والـ norm في المدى المتوقع.
- اتساق الفهرس: مجموعة الكتب في الـ generation = مجموعة الكتب في المخطط المعتمد، و0 صفوف يتيمة.
- Retrieval regression: المقاييس على المجموعة الثابتة لا تنخفض أكثر من 2 نقطة عن baseline.

**دلالية (عينات + LLM-judge معاير ضد البشر):**
- Lesson-link precision@5 (هل المقطع المساند ذو صلة؟) ونسبة "contradicts" الصحيحة.
- Context precision/recall على أسئلة RAG. مرجع: Ragas (Es et al., 2023، ‏https://arxiv.org/abs/2309.15217)، وARES (Saad-Falcon et al., 2023، ‏https://arxiv.org/abs/2311.09476). ويجب **معايرة الحكم الآلي ضد عينة بشرية** (Cohen's κ ≥ 0.7) قبل الاعتماد عليه، وهو ما لم يحدث في الترجمة.

### 5.5 التقييم: المنهجية والعتبات المستهدفة
- **المنهجية:** BEIR/TREC (Thakur et al., NeurIPS 2021، ‏https://arxiv.org/abs/2104.08663). qrels متدرجة (0–3)، و**pooling** من عدة أنظمة (BM25 وdense وhybrid وrerank top-20) ثم حكم بشري على الـ pool، لأن qrel واحد لكل سؤال يعاقب الأنظمة التي تجد دليلًا صحيحًا آخر.
- **Corpus:** على الأقل كتابان كاملان حقيقيان (أكثر من 300 chunk لكل كتاب) منتجان بالـ chunker الإنتاجي نفسه. لا نوافذ ثابتة.
- **الأسئلة (حد أدنى 200، منها 50 أو أكثر بحكم بشري):** direct ‏(≤30%)، وparaphrase بتداخل حرفي < 0.4، وconceptual، وmulti-hop، و**عربية على مصدر إنجليزي (≥ 20%)**، وغير قابلة للإجابة (≥ 15%)، و**ربط درس→مساند** (لكل درس في الكورس المرجعي).
- **العتبات المقترحة (تُثبّت بعد baseline):**

| المقياس | المهمة | الهدف |
|---|---|---|
| Recall@20 (مرحلة المرشحين) | QA | ≥ 0.95 |
| nDCG@10 بعد rerank | QA | ≥ 0.75 |
| MRR@10 | QA | ≥ 0.70 |
| Recall@10 للاستعلامات العربية | QA cross-lingual | ≥ 0.85 |
| Lesson-link precision@5 (بشري) | Linking | ≥ 0.80 |
| نسبة الدروس ذات رابط مساند خاطئ في الإثراء | Linking | ≤ 5% |
| False-answer rate على غير القابلة للإجابة | QA | ≤ 5% (مع حد ثقة 95% أعلى ≤ 8%) |
| Lesson coverage (chunks المستهلكة / chunks الدرس) | Coverage | = 100% |
| p95 latency (hybrid+rerank، ‏GPU أو CPU معلن) | QA | ≤ 1.5 s |

### 5.6 نقاط تدخل بشري (HITL)
1. مراجعة **تصنيف front/back matter** لكل كتاب جديد: صفحة واحدة تعرض حدود body المقترحة (دقيقتان لكل كتاب).
2. **عينة 20 chunk عشوائية** لكل كتاب تُعرض على المراجع: نظيف أو تالف أو غير تعليمي. البوابة تمنع النشر إذا تجاوز التالف 2%.
3. مراجعة روابط المساند المقترحة لكل درس (قبول/رفض بنقرة)، وتصبح هذه الأحكام qrels تراكمية.
4. حكم بشري على pool التقييم (§5.5) مرة لكل تغيير جوهري في النموذج أو الـ chunker.

### 5.7 الاختبار
- Unit: حالات content gate (control chars، وletter-spacing، وفهرس، وحواشٍ)، وsentence-split للـ block الكبير، ودمج الصغير، وtokenizer-aware sizing، واستقرار `chunk_id` مع تغير النص.
- Property-based (Hypothesis): لأي `ParsedBook` عشوائي، الـ invariants (تغطية 100% للـ body، ولا عبور للحدود، والحجم ضمن المدى، والـ overlap ضمن 10–15%).
- Golden snapshot: chunks لثلاثة كتب مرجعية مجمّدة، ويظهر الـ diff في الـ PR.
- Integration: indexing workflow → generation جديد → تبديل ذري → GC، والتحقق من 0 يتيم.
- Retrieval eval في CI (subset سريع) + full nightly، مع منع الدمج إذا حصل regression.

---

## 6. تحليل الفجوة وخطة الترحيل

### 6.1 ما يبقى
- حدود lesson span من المخطط المعتمد كـ hard boundaries (`chunking.py:36-66`)، والتحقق من الترتيب والتداخل.
- عزل النطاق (course/book/role) والتحقق الدفاعي في `query_service.py:61-63`، ومنع خلط الهوية في RRF (`retrieval.py:60-73`).
- `replace_book` بـ transaction، وحتمية الـ snapshot عبر `stage_key` (`orchestration/indexing.py:248-264`).
- صرامة قبول المدخلات في الـ activity (`orchestration/indexing.py:196-240`).
- سكربتات eval (`retrieval_eval.py` و`full_ranker_eval.py`) كهيكل، مع تغيير المجموعة.

### 6.2 ما يُعاد كتابته
- `chunking.py`: ‏content gate + tokenizer-aware hybrid chunking + sentence overlap + هوية جديدة.
- `retrieval.py` و`query_service.py`: يُستبدلان بـ SQL-backed BM25/FTS + pgvector HNSW + RRF + reranker + abstention، وتُحذف إعادة حساب الـ embeddings لكل استعلام.
- `postgres_store.py`: ‏migration لـ `vector(d)`، و`tsvector`، و`index_generation`، و`text_sha256`، وGC.
- `StructuredChunk` (contracts): حقول provenance جديدة مع `schema_version` جديد ومسار ترحيل.
- استدعاء الاسترجاع في `qa_generation.py:607-616`: **يُزال** للـ lesson scope (coverage كامل). وللـ group/course scopes تُستخدم تجزئة حتمية أو تلخيص هرمي بدل الحذف.
- `qa.py:454-466` (الربط المساند): يُعاد بناؤه وفق §5.3 مع judge وعتبة.

### 6.3 ما يُحذف
- `graph_adapters.py` و`graph_native.py` و`graph_runtime.py` و`graph_router.py` و`graph_api.py`، ومسار `lightrag/hipporag` في `qa.py:383-399` (NO-GO موثق). تُنقل لفرع أرشيف.
- `min_score` اللكسيكي المعاير على corpus واحد، ومسار `embeddinggemma2-lite` إلا إذا فاز في التقييم.
- مجموعة T-IDX-02 الحالية (37 نافذة) كبوابة. تبقى smoke test فقط.

### 6.4 خطوات مرتبة
1. **(فوري، دون إعادة كتابة) إيقاف الحذف الصامت:** تعطيل `scope_retriever` للـ lesson scopes (تمرير كل الـ chunks)، وإيقاف `conflict_candidates` أو تعليمها "غير محكومة". *يعتمد على:* لا شيء. *المخاطرة:* دفعات LLM أكبر، و`_partition_chunks` يعالجها أصلًا.
2. **بناء مجموعة التقييم الحقيقية** (§5.5)، مع pooling وحكم بشري على 50 سؤالًا على الأقل، ومعايرة judge آلي (κ). بدونها كل ما بعدها تخمين.
3. **Content gate + تصنيف body/non-body** في الـ indexing، وتقرير لكل كتاب (نسب التالف والفهرس والحواشي). *يعتمد على:* إصلاحات ingestion (letter-spacing، وfont encoding، وhyphenation). إن لم تُصلح هناك، يحجر الـ gate الـ blocks ويُوقف الكتاب بدل تمريرها.
4. **Chunker جديد** (tokenizer-aware، وsentence split/merge، وcontextual header، وهوية جديدة)، وتقرير golden diff على الكتب الثلاثة.
5. **Schema migration** ‏(pgvector HNSW + FTS/BM25 + generation + GC)، وتنظيف الـ 397 صفًا اليتيمة ضمن migration مُراجع.
6. **Offline embedding** داخل الـ indexing workflow على heavy queue، مع تخزين vectors و`embedding_model@revision`.
7. **Hybrid + reranker + abstention**، ومقارنة على مجموعة الخطوة 2، ثم تثبيت المرشح الفائز (bge-m3 + bge-reranker-v2-m3 أو بديل) بقرار مكتوب جديد.
8. **إعادة بناء الربط المساند** مع LLM-judge معاير وHITL.
9. حذف كود graph، وتوحيد الـ API، وإدخال eval في CI.

### 6.5 الاعتماديات على مراحل أخرى
- **Ingestion (01/02):** letter-spacing ‏(`'pos i t i on'`)، وfont encoding (lane ص35–36)، وhyphenation، وتصنيف `page_header/footer/footnote`. الفهرسة لا تستطيع إصلاح النص.
- **Outline (02):** ‏D-027 غير فعّال (فهرس الكتاب داخل درس `conclusion`، و17 درسًا بـ chunk واحد، وعناوين مفككة). الـ outline يجب أن يستبعد back matter، وأن يمرر حدود body.
- **Pedagogy/Enrichment (04/05):** تغيير طريقة استهلاك الـ chunks (coverage كامل)، والتعامل مع "لا يوجد مساند".
- **Identity/Upload:** نفس الكتاب بهويتين (`book-main` و`book-70c`، ‏2620 block لكل منهما). **UNVERIFIED**.

### 6.6 مخاطر
- إعادة الفهرسة تغيّر `chunk_id`، فتصبح citations وcheckpoints الـ QA القديمة غير صالحة. الحل: generation جديد + جدول mapping قديم→جديد + إعادة توليد الكورس المرجعي.
- تكلفة الـ embedding/reranker على CPU (التقرير المسجل: 11.6 s لكل استعلام مع rerank على CPU، ‏`phase-3.md:205`). الحل: rerank على top-20 فقط، أو GPU، أو reranker أصغر، بقرار مقاس.
- الـ content gate قد يحجر كتبًا كاملة رديئة المسح، وهذا مقصود. يجب أن يكون الفشل صريحًا ومرئيًا للمالك.

---

## 7. معايير القبول (متى تُعتبر المرحلة "منتهية")

تُقاس كلها على **كورس مرجعي من كتابين كاملين على الأقل** (spine + supplementary) مُفهرسين بالكود الإنتاجي:

1. **سلامة المحتوى:** 0% chunks فيها control chars > 1%. وفي عينة بشرية من 50 chunk عشوائية لكل كتاب: ≤ 2% تالف و0% فهرس/حواشٍ/TOC داخل فهرس المحتوى.
2. **الحجم:** 100% من الـ chunks بين 64 والحد الأعلى المعلن بالـ tokenizer الفعلي (باستثناء موسوم ≤ 1%)، و0 truncation، والـ overlap الفعلي المقاس في [8%, 16%].
3. **التغطية:** 100% من body blocks داخل lesson spans موجودة في chunk، و100% من chunks الدرس تصل لمولّد الدرس (يُقاس من سجل التشغيل)، و0 non-body blocks في نطاقات الدروس.
4. **Provenance:** 100% من الصفوف تحمل `text_sha256` و`chunker_version` و`parser.version` و`embedding_model@revision` ≠ unassigned و`index_generation`. وتعديل نص block مع ثبات ID يغيّر `chunk_id` (اختبار).
5. **اتساق الفهرس:** 0 صفوف يتيمة، وعدد الـ vectors = عدد الـ chunks، والتبديل للـ generation الجديد ذري (اختبار integration).
6. **الاسترجاع** (مجموعة ≥ 200 سؤال، منها ≥ 50 بحكم بشري، وqrels بالـ pooling): ‏Recall@20 ≥ 0.95، وnDCG@10 ≥ 0.75، وRecall@10 للعربي ≥ 0.85، وfalse-answer ≤ 5% على ≥ 60 سؤالًا غير قابل للإجابة.
7. **الربط المساند:** precision@5 بحكم بشري ≥ 0.80 على كل دروس الكورس المرجعي، والدروس التي لا مساند لها تُعيد "لا يوجد" (لا روابط مفروضة).
8. **الـ judge الآلي** المستخدم في البوابات الدلالية معاير ضد البشر بـ κ ≥ 0.7 على ≥ 100 حكم.
9. **الأداء:** p95 ≤ 1.5 s للاستعلام (hybrid+rerank) على العتاد المعلن، وزمن فهرسة كتاب من 500 صفحة موثق.
10. **CI:** اختبارات property-based وgolden وretrieval-regression تعمل وتمنع الدمج عند الفشل، وكود graph محذوف من مسار الإنتاج.

---

### ملحق: سكربتات القياس
سكربتات القياس المؤقتة (قراءة فقط) موجودة في `/tmp/claude-0/-root/c9ec20dc-64ad-42ec-857d-86ecef50c15c/scratchpad/` ‏(`q1.py`–`q4.py` لـ Postgres، و`s1.py`–`s6.py` لـ MinIO والمحاكاة). شُغّلت عبر `docker exec -i b2c-worker-1 python - < script`.
