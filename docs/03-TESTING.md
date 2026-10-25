# 03 — استراتيجية الاختبار (Testing)

## 1. الأساس المنهجي
| الممارسة | مصدرها | بنطبقها إزاي |
|---|---|---|
| Test Pyramid | Mike Cohn؛ Martin Fowler "The Practical Test Pyramid" | unit كتير، وintegration أقل، وe2e قليل |
| Consumer-driven contract testing | Pact / Fowler | كل طبقة بتختبر إنها بتستقبل وبتطلّع العقد بالظبط |
| Golden-file (snapshot) testing | ممارسة شائعة للمخرجات الحتمية | ingestion وoutline وchunking |
| Retrieval evaluation | منهجية BEIR ‏(Recall@k, nDCG@k) | L3 وL4 |
| RAG faithfulness | Ragas | L5 |
| MCQ quality | Haladyna, Downing & Rodriguez (2002) item-writing guidelines | L6 linter |
| MT quality | COMET-Kiwi / xCOMET (Unbabel)؛ MQM؛ ISO 18587 | L7 |
| Audio | ACX specs؛ EBU R128 / ITU-R BS.1770 | L8 |
| Accessibility | WCAG 2.2 AA | L10 |

## 2. قواعد إلزامية
1. **Negative control لكل اختبار تقييمي:** مدخل فاسد عمدًا لازم **يفشل**. لو نجح، يبقى المقيِّم نفسه بايظ والنتيجة ملغية.
2. **TDD لكل phase:** الاختبارات بتتكتب الأول وتفشل، وبعدين التنفيذ.
3. **العتبات (thresholds) ابتدائية ❓:** بتتعاير مرة واحدة في أول gate بناءً على قياس حقيقي، وبعدها **بتتجمّد** في `eval/thresholds.yaml`. ممنوع تنزيلها عشان اختبار ينجح. لو عتبة شكلها غلط: stop and report.
4. **Golden fixtures:** لا يُفترض أن مجانية الوصول تعني سماحًا بالاستخدام الآلي. استخدم `eval/golden/phase1/source-manifest.json` لمصادر Phase 1 المسموح بها للاختبار الداخلي، واحفظ النصوص المرخصة على قرص بيانات B2C؛ مجموعة الـ 10 chunks ذات الوسوم المرجعية تظل بحاجة إلى تعليم بشري. لا تنشر مخرجات أو نصوص Think Stats خارج نطاق غير تجاري، واحتفظ بنسبة المصدر وشروط ShareAlike.
5. **Determinism:** المهام الحتمية بتشتغل بـ `temperature=0`، والتقييمات الاحتمالية بتتعاد 3 مرات ويتاخد المتوسط والانحراف.

## 3. كتالوج الاختبارات

### Phase 0: الأساس
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-C-01..08 | invariants العقود (موجودة فعلًا) | كلها pass |
| T-P0-01 | import boundaries (import-linter) | مفيش أي مخالفة |
| T-P0-02 | trace من activity تجريبية يظهر في Langfuse | span موجود بالـ attributes |
| T-P0-03 | Temporal durability: ‏`kill -9` للـ worker في نص workflow تجريبي | الـ workflow بيكمّل بعد إعادة التشغيل والنتيجة صحيحة |
| T-P0-04 | resource limits: ‏`docker inspect` لكل خدمة b2c | كل خدمة ليها `Memory` و`NanoCpus` و`CpuShares`، ومجموع الذاكرة ≤ 85GiB (D-007، عُدّل 2026-09-29) |
| T-P0-05 | isolation: الحاويات اللي برّه b2c ماتغيرتش | `docker ps` قبل وبعد: نفس الـ IDs ونفس الـ StartedAt |

### Phase 1: LLM benchmark
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-B-01 | `llama-bench` بـ threads = ‎14 و20 و28 (pp512, tg128) لكل مرشح | الأرقام متسجلة، ونختار الأسرع tg |
| T-B-02 | JSON validity في استخراج الكيانات على 30 chunk من الفصل التجريبي | نسبة JSON صالح للـ schema ≥ 95% ❓ |
| T-B-03 | جودة الاستخراج مقابل استخراج يدوي لـ 10 chunks | entity recall وprecision متسجلين (لسه مفيش عتبة، دي baseline) |
| T-B-04 | تنافس: tg بوجود حمل المشاريع التانية مقابل وقت هادي | الفرق متسجل؛ لو > 10% بيتفتح D-009 |
| T-B-05 | استهلاك الذاكرة الفعلي لكل مرشح بالـ context المستخدم | ≤ حد الخدمة في 04-OPERATIONS |
| T-B-06 | عينة عربي: ترجمة 10 فقرات | متقيّمة يدويًا (baseline) |

### L1 Ingestion
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-ING-01 | عدد الصفحات، ولكل block ‏`page_no` | 100% |
| T-ING-02 | golden (20 صفحة): heading recall / TEDS للجداول / edit distance | ≥ 0.95 / ≥ 0.85 / ≤ 0.05 ❓ |
| T-ING-03 | صفحة ممسوحة بتتحوّل لـ OCR | CER ≤ 3% ❓ |
| T-ING-04 (neg) | ترتيب عمودين متبدّل عمدًا | لازم يتكشف |
| T-ING-05 | تشغيل مرتين بيدي نفس الـ `stage_key` وcache hit | 100% |
| T-ING-06 | API upload: امتداد المحتوى، حد الحجم، source artifact، وبدء Temporal | 202 للطلب الصحيح؛ الرفض 413/415 للمدخلات غير الصالحة |
| T-ING-07 | حالة ingestion عبر Temporal workflow ID | الحالة المعروضة تطابق وصف Temporal |
| T-ING-08 (R3) | ParsedBook v2 + بوابة الجودة الحتمية: `tests/contract/test_parsed_book_v2.py`، و`tests/unit/ingest/test_normalize_v2.py`، و`tests/unit/ingest/test_quality_v2.py`، و`tests/unit/orchestration/test_ingest_v2.py` (وثائق اصطناعية) | كلها تمر في `make check` |
| T-ING-09 (R3) | الكتب الحقيقية: `eval/suites/ingest_v2/measure.py` (v1 مقابل v2، وتسميات `source_issue`)، والفحوص الستة عشر المشتقة من البوابات التسع في `01-ingest.md` §5.4 بعتباتها | التقرير `eval/reports/ingest-v2-2026-10-25.md`؛ `accepted=false` لأي كتاب يفشل فحصًا |
| T-ING-10 (D-040) | بوابة الاستخراج v2: اتفاق المحلّلَين المستقلَّين (pypdf وpdfium) صفحةً صفحة، ولا حد 2000 حرف، والفقرات المدموجة من هندسة الأسطر، و`GateResult` ‏`ingest-v2.quality` عبر خدمة الترقية: `tests/unit/ingest/test_quality_v2.py`، و`tests/unit/orchestration/test_ingest_promotion.py`، و`tests/unit/orchestration/test_ingest_v2.py`، و`tests/contract/test_parsed_book_v2.py` (`page_findings`) | كلها تمر في `make check`؛ العتبات في D-040 |
| T-ING-11 (D-040) | فصل الكلمات الملتصقة (`b2c_ingest.glued`): الإثبات من bigrams الكتاب أو المعجم، وحماية أسماء العلم والمصطلحات والروابط وCamelCase، ولا تقسيم لـ privative a- ولا للبادئات: `tests/unit/ingest/test_glued_words.py` | كلها تمر. على الكتب الحقيقية (`eval/suites/ingest_v2/glue_eval.py`، الحَكَم pdfium): 0 تقسيم خاطئ على النص السليم |
| T-ING-12 (D-040) | مفتاح التفعيل `B2C_INGEST_V2_ENABLED` معطّل افتراضيًا، ومعرّف workflow الرفع v1 لا يتغير وهو معطّل: `tests/unit/api/test_ingest_v2_switch.py` | كلها تمر |

### L2 Outline
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-OUT-01 | snapshot لكتاب فيه bookmarks | مطابق |
| T-OUT-02 | كتاب من غير TOC ينتج `llm_inferred` + `requires_review` | صح |
| T-OUT-03 | الـ spans متتالية، مش متداخلة، وبتغطي ≥ 98% من المتن | صح |
| T-OUT-04 (R4) | CourseOutline v2 والباني الحتمي: `tests/contract/test_course_outline_v2.py` (تقسيم الكتل = مدرج + مستبعد، المعرّفات، العناوين، إسقاط v1)، و`tests/unit/outline/test_outline_v2_builder.py` و`test_outline_v2_titles.py` (bookmarks أولًا، وP4 وP6، ومقدمة الفصل، والمناطق، والحجم، وثبات المعرّفات، والمصادر البديلة) | كلها تمر في `make check` |
| T-OUT-05 (R4) | البوابة الحتمية للمخطط v2: `tests/unit/outline/test_outline_v2_gate.py` (تغطية 100%، 0 عناوين مشوهة، 0 كلمات حواشٍ/مراجع/فهرس وكتل back matter داخل الدروس، استثناءات الحجم موثقة، ومقارنة بالمرجع: boundary/title F1 ≥ 0.95 بتسامح ±1 كتلة، وlevel/zone ≥ 0.95)؛ والكتب الحقيقية: `eval/suites/outline_v2/measure.py` مقابل `eval/golden/outline/{sat,gramsci}.json` (مرجع من الـ bookmarks، والمناطق مراجعة) | التقرير `eval/reports/outline-v2-2026-10-25.md`؛ `accepted=false` لأي كتاب يفشل فحصًا |
| T-OUT-06 (R4) | القبول الآلي (لا مراجع بشري): `tests/unit/outline/test_outline_v2_gate.py` (الفحص المتقاطع: اختلاف المصادر علامة لا حجب، وفقد التغطية رفض)، و`tests/unit/orchestration/test_outline_v2_activity.py` (مرشح ← بوابتان نظاميتان ← accepted/rejected عبر خدمة الترقية)، و`tests/unit/api/test_outline_v2_approval.py` (العرض، والتسليم للفهرسة للمقبول فقط)، و`test_ingest_v2.py` (الـ patch) | كلها تمر في `make check` |

### L3 Indexing
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-IDX-01 | مفيش chunk بيعدي قسم، ومفيش جدول متقسّم | 100% |
| T-IDX-02 | 50 استعلام متعلَّم: Recall@10 / nDCG@10 (dense مقابل hybrid، بـ rerank ومن غيره) | ≥ 0.85 / ≥ 0.6 ❓ — **smoke فقط** منذ R5: المجموعة المسجلة 37 نافذة ثابتة وليست ناتج الـ chunker الإنتاجي (تدقيق 03 C3). البوابة الحقيقية تنتظر qrels بالـ pooling على كتابين كاملين (R2) |
| T-IDX-03 (neg) | استعلام عن مفهوم مش موجود | أعلى score تحت العتبة |
| T-IDX-04 | تغطية الدرس (R5، تدقيق 03 C1): محتوى الدرس = كل الـ span بالبناء، لا top-k. كل حرف من كل body block داخل نطاق درس في `parts` لـ chunk واحد بالضبط، ومولّد الدرس يستلم كل chunks الدرس (`ScopeResult.source_chunk_ids`) | 0 يتيم، و0 مكرر، و0 مقطوع (truncation)، و100% من chunks الدرس تصل للمولّد |
| T-IDX-05 | بوابة المحتوى (R5، تدقيق 03 C2): الـ furniture والـ front/back matter والـ TOC والفهرس والمراجع ونص OCR للأشكال خارج فهرس المحتوى، والحواشي فئة دليل منفصلة (`note`)، ولا chunk عنوانًا فقط. النص التالف (control chars > 1%، أو تباعد حروف، أو نسبة أحرف < 0.5) محجور ومذكور بالاسم، والكلمات الملتصقة علم مشبوه. يعمل على v2 (المناطق والأنواع) ويتدهور بلطف على v1 | 0 non-body في المحتوى، و0 chunk عنوان فقط، والمحجور ≤ 2% من body |
| T-IDX-06 | الربط المساند (R5، تدقيق 03 H1): درجة صلة TF-IDF cosine (مطبَّعة بالطول، استعلام مفاهيمي من مصطلحات الدرس) وعتبة سياسة مُصدَّرة؛ كل مرشح من أعلى k له `SupplementLinkJudgement` محفوظ (`supplement-links.json`)، وما تحت العتبة لا يُربط، و«لا رابط» نتيجة مقبولة. ضابط سلبي: كتابان غير مرتبطين يعطيان 0 روابط | 0 روابط في الضابط السلبي؛ دقة الربط بحكم بشري ≥ 0.80 **غير مقاسة** (لا أحكام بشرية، `calibrated=false`) |
| T-IDX-07 | الترقية (R1/P2): كل snapshot فهرسة لكل كتاب مرشح في سجل المخرجات بالبوابة `index.content-gate` وتقرير `index-report.json`. ينجح فـ `accepted`، ويفشل فـ `rejected` ولا يُكتب أي chunk في جدول الاسترجاع (`IndexGateFailed`، غير قابل لإعادة المحاولة) | 100% |
| T-IDX-08 | التقييم على المسار الإنتاجي (تدقيق 03 C3): `eval/suites/indexing_v2/measure.py` يقيس قبل (صفوف `b2c_indexed_chunks` الحية عبر `dump_production.py` للقراءة فقط، واستهلاكها بمنطق QA القديم) وبعد (`index_book` على ParsedBook v2، ويستهلكها `_derive_scopes`) على SAT وGramsci، ومعها وسوم تدقيق الترجمة حيث وُجدت. Recall@k/nDCG لا تُحسب إلا على qrels موجودة، وغير المقاس يُذكر صراحة في المخرج (`not_measured`) | أرقام قابلة لإعادة الإنتاج بأمر واحد (`eval/reports/indexing-v2-2026-10-25.md`) |

### L4 Knowledge Graph
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-KG-01 | entity recall / precision مقابل independent source-grounded auto labels (system diagnostic؛ لا تُسمّى gold) | ≥ 0.8 / ≥ 0.7 ❓ |
| T-KG-02 | entity resolution على 50 زوجًا معلّمة آليًا من شواهد المصدر (system diagnostic؛ لا manual labels) | F1 ≥ 0.9 ❓ |
| T-KG-03 | إدراج كتاب تالت مايحذفش كيانات الأول والتاني | 100% |
| T-KG-04 | حذف كتاب مايسيبش citations له | 0 |
| T-KG-05 | عبر الكتب: مفهوم في الفصل X من الكتاب الأساسي بيسترجع مقطعه من فصل مختلف في الكتاب المساند | الحالات المتعلَّمة كلها ناجحة |
| T-KG-06 | LightRAG يرفض القراءة والاستعلام والإضافة والحذف من غير API key؛ يُقبل 401 أو 403 بعد نجاح طلب موثّق وفحص عدم تغيّر البيانات | كل المسارات المحمية 401/403؛ الموثق 200 |

### L5 Enrichment
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-SYN-01 | جملة من غير citation بتترفض (العقد) | 100% |
| T-SYN-02 | Ragas faithfulness / citation support (عينة بشرية 30 جملة) | ≥ 0.90 / ≥ 95% ❓ |
| T-SYN-03 | كشف التكرار على أزواج متعلَّمة | F1 ≥ 0.8 ❓ |
| T-SYN-04 (neg) | تناقض مزروع عمدًا في الكتاب المساند | لازم يتكشف ويتحط في `contradictions` |
| T-SYN-05 (neg) | موضوع مالوش تغطية في الكتب المساندة | صفر supplements، ومفيش أي اختلاق |

### L6 Pedagogy
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-PED-01 | الـ linter على 30 MCQ سيئة عمدًا (قواعد Haladyna) | بيكشف ≥ 90% |
| T-PED-02 | solver: بيجاوب من الدرس / من غير الدرس | ≥ 95% / ≤ 30% ❓ |
| T-PED-03 | كل درس فيه سؤال واحد على الأقل فوق مستوى `remember` | 100% |
| T-PED-04 | أسئلة المراجعة من `prior_lessons` بس | 100% |
| T-PED-05 | FSRS parity مقابل py-fsrs fixture ‏(`enable_fuzzing=False`) | مطابق |
| T-PED-06 | datetime من غير timezone بيترفض | 100% |

### L7 Translation
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-TR-01 | مطابقة الـ termbase | **100% (hard gate)** |
| T-TR-02 | الأرقام والمعادلات والـ citations محفوظة | 100% |
| T-TR-03 | COMET-Kiwi / xCOMET على 30 فقرة عليها post-edit بشري | baseline بيتسجل، والعتبة بتتعاير هنا |
| T-TR-04 (neg) | ترجمة مفسدة عمدًا (حذف نفي، أو مصطلح غلط، أو رقم متغير) | لازم تتوجّه لـ `human_review` |
| T-TR-05 | الـ routing: نسبة الجمل اللي راحت للطابور البشري | بتتسجل في `CourseRelease.human_reviewed_ratio` |
| T-TR-06 | معايرة LLM-MQM المحلي مقابل تقييم بشري MQM لـ 30 جملة | الارتباط متسجل؛ لو ضعيف، المستوى ده مايتعتمدش كبوابة لوحده |
| T-TR-07 (D-039) | مستويات القاموس: قطعة ومعتمد قبل v3 يُقرآن بنفس SHA-256 ونفس `approved_digest`؛ `hint` لا يدخل `locked_terms`/`forbidden`؛ المطابقة العربية للمقفول تتحمل «ال» والسوابق والجمع السالم والمثنى والتاء والتنوين (حالة «جماعة اجتماعية»/«الجماعات الاجتماعية»)، و`forbidden_variant` يبقى صارمًا — `tests/unit/translation/termbase/test_tiers.py`، `tests/unit/translation/qa/test_term_inflection.py` | 100% |
| T-TR-08 (D-039) | إصلاحات ضوضاء الاستخراج على قرارات الكتابين المؤقتة (D-031) | لا يضيع أكثر من 1% من المقبول |
| T-TR-09 (D-039) | مصنّف الـ termhood على عيّنة ≤ 300 من القرارات المؤقتة | إحساس ≥ 0.97 على المقبول شرطًا للتفعيل؛ معطّل افتراضيًا |

T-TR-01 يخص مدخلات `locked` (والكيانات المقفولة)؛ مدخل `hint` إشارة `terminology_hint` بدرجة minor لا تحجب القبول (D-039).

### بحث المعرفة وطبقة المفاهيم (EN، D-034/D-035؛ docs/02 §11)
| ID | الاختبار | العتبة |
|---|---|---|
| T-KR-01 | ثوابت `KnowledgeDossier` (حقيقة = مصدر درجة أولى أو مصدران، الرأي والتعارض منسوبان، عائلة المحكّم مختلفة) — `tests/contract/test_knowledge_concepts.py` | 100% (negative controls) |
| T-KR-02 | المحتوى المجلوب بيانات: سياسة النطاقات، وحذف السكربت والعقد المخفية، وحذف تعليمات الحقن والإبلاغ عنها، وحد الحجم، وفحص كل قفزة redirect — `tests/unit/research/test_safety.py` | 100% |
| T-KR-03 | تعليمات محقونة في صفحة لا تصير ادعاءً، ولا تصل إلى النموذج — `tests/unit/research/test_dossier.py` | 0 ادعاء منشور من نص محقون |
| T-KR-04 | المدقق: مقتطف غير موجود أو غير داعم ⇒ مرفوض؛ بوابتا `knowledge.citations` و`knowledge.tier-coverage` | تُقاس على الكتابين: نسبة الدعم الخام ≥ 0.5، وحد أدنى 5 ادعاءات منشورة، وحصة الدرجة الثالثة وحدها ≤ 0.25 (سياسة مؤقتة) |
| T-CL-01 | ثوابت `ConceptLayer` (مواضع لكل عبارة بصوت المؤلف، ولا دورات في المتطلبات، والملاحظات الخارجية منفصلة) | 100% |
| T-CL-02 | كل موضع يُعاد فحصه مقابل الكتاب المحلَّل (`concepts.grounding`) | grounding = 1.0 |
| T-CL-03 | تغطية مفاهيم القاموس المعتمد (`concepts.coverage`) | ≥ 0.9 (سياسة مؤقتة) |

### L8 Speech
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-DIA-01 | DER/WER على 200 جملة مشكّلة بيد خبير، مع الإعراب ومن غيره (CATT مقابل Sadeed) | baseline، والفائز بيتعتمد |
| T-DIA-02 | الـ lexicon overrides متطبقة | 100% |
| T-TTS-01 | كل provider بيعدّي نفس contract tests | 100% |
| T-TTS-02 | CER على الـ ASR ≤ ‏1.5 × CER الـ ASR على تسجيل بشري لنفس النص | ❓ |
| T-TTS-03 | ثبات الصوت: speaker similarity بين أول الدرس وآخره + استماع 10 عينات | فوق العتبة |
| T-TTS-04 (neg) | صوت مستنسخ من غير consent ساري | بيترفض (D-003) |
| T-TTS-05 | A/B: هل التشكيل بيحسّن الـ CER لكل provider؟ | النتيجة متسجلة، والقرار لكل provider |
| T-AUD-01 | ACX: ‏RMS ‏-23..-18 dBFS، وpeak ≤ -3 dBFS، وnoise floor ≤ -60 dBFS، و44.1kHz، وMP3 ‏192kbps CBR | **hard gate** |
| T-AUD-02 (neg) | ملف مضخّم عمدًا | لازم يفشل |
| T-AUD-03 | تزامن النص مع الصوت | ±100ms |
| T-AUD-04 (R7) | ملف التطبيق `app-speech-16` (توصية الخطة، قرار المالك Q2 معلّق): loudness متكامل ‎-16 LUFS ±1 LU (ITU-R BS.1770-4 / EBU R128)، وtrue peak ≤ ‎-1 dBTP، بلا clipping، ولا صمت داخلي > 1.5 ث. T-AUD-01 هو الملف `acx` في الكود نفسه (`b2c_speech.audio.qa`)، ويُختار أحدهما بـ `B2C_AUDIO_LOUDNESS_PROFILE` | 100% من المسارات بقياس مستقل؛ تحقق المرجع: جيبية 997Hz بسعة A تقرأ ‎20·log10(A)−3.01 LUFS ±0.1 عند 24/44.1/48kHz |
| T-AUD-05 (R7) | فحص كل مقطع: sample rate، وأحادي القناة، وليس صامتًا، وبلا clipping، وصمت البداية ≤ 1 ث والنهاية ≤ 1.5 ث والداخلي ≤ 1.5 ث، ومعدل 3–25 حرفًا/ث؛ وعلى مستوى المسار: نسبة ث/حرف خارج ±35% من الوسيط = بتر أو هلوسة | 0 مقاطع فاشلة في المسار المقبول |
| T-AUD-06 (R7) | واجهة الوضوح `IntelligibilityChecker` (ASR → CER/WER مقابل `spoken_text` بعد تطبيع عربي). بلا نموذج ASR مثبّت تبقى البوابة بلا نتيجة ويبقى المسار `candidate` | العتبة تُعاير مقابل T-TTS-02؛ Whisper-tiny لا يصلح بوابة |
| T-AUD-07 (R7، C4) | تخطيط كتاب كامل (~660 ألف حرف): مسارات ≤ 3600 ث تقديرًا و≤ 400 مقطع، ودفعات ≤ 8 مقاطع و≤ 2400 حرف، وكل مقطع مرة واحدة بالترتيب، والتخطيط ينتهي دائمًا؛ ومفتاح صوت المقطع = المحتوى + الصوت + revision النموذج + seed | ينتهي في < 2 ث؛ إعادة المحاولة لا تعيد إلا المقاطع الناقصة |
| T-TN-01 (R7) | golden للتطبيع العربي (`tests/unit/speech/test_speech_text_frontend.py`): حواشٍ، ومراجع `[n]` وصفحات، وأرقام أقسام، وسنوات، وعقود، وعشري، ونسب، وترتيبي، ونطاقات، واختصارات، وأسماء لاتينية من الـ lexicon | 100% من الحالات؛ ومجموعة الـ 300 حالة (audit 07 §5.5) ≥ 98% و0 حرج حين تُبنى |
| T-TN-02 (neg, R7) | وحدة `needs_review` أو `rejected` أو بلا نتيجة في مدخل النص المنطوق | يُرفض البناء كله (`NotSpeechReady`) |
| T-TN-03 (R7) | `spoken_text` في العينة: أرقام خام، و`[n]`، وحروف لاتينية، وروابط | 0؛ وكلمات لاتينية خارج الـ lexicon (`lexicon_misses`) = 0 لقبول البوابة |

### Release / E2E
| ID | الاختبار | معيار النجاح |
|---|---|---|
| T-REL-01 (neg) | release فيه كتاب مش `cleared` | بيترفض (موجود في العقد) |
| T-E2E-01 | فصل أساسي + فصل مساند (D-017) ← درسين كاملين ظاهرين في الـ web | صح |
| T-E2E-02 | kill للـ worker في النص ثم استئناف | cache hit ≥ 99% |
| T-E2E-03 | 20 جملة عربي عشوائية بتتتبّع لحد الصفحة المصدر | 100% |
| T-E2E-04 | WCAG 2.2 AA: فحص آلي (axe) + قارئ شاشة يدوي | صفر مخالفات حرجة |
| T-OPS-01 | استعادة backup على بيئة فاضية | ناجحة |

## 4. أدوات الاختبار
- pytest، وimport-linter، وRagas، وunbabel-comet (COMET-Kiwi/xCOMET)، وjiwer (WER/CER)، وffmpeg (loudnorm/ebur128)، وaxe-core.
- ❓ DeepEval وpromptfoo: مااتحققتش من حالة صيانتهم، فمش هيتضافوا غير بعد VERIFY.

## 5. تقرير كل Phase (`eval/reports/phase-N.md`)
```
## Phase N report — <date UTC>
### Executed (verified): commands + real outputs
### Not executed / assumed:
### Tests: passed X / failed Y / skipped Z  (list IDs)
### Metrics vs thresholds: table
### Versions pinned: table
### Open issues:
### Gate decision: PASS / FAIL (with reason)
```

### اختيار اختبارات استخراج المستندات
- نعتمد منهجًا متعدد الأبعاد: دقة النص + ترتيب القراءة + تخطيط الصفحة والجداول + إحالات الصفحة + زمن/ذاكرة التشغيل؛ CER وحده لا يقيس صلاحية ناتج الكتاب للكورس.
- نستخدم OmniDocBench مرجعًا لتغطية مهام parsing المختلفة، وDocVQA مرجعًا لاختبار الأسئلة المعتمدة على فهم بنية المستند. لا نعامل نتائج أي benchmark خارجي كبديل عن مجموعة كتب عربية ذات نص مرجعي بشري.
- نختار صفحات ممثلة وموسومة يدويًا من كتب رقمية ومسوح ضوئية، ونقسم النتائج حسب اللغة، والدقة، والميل والضبابية، والأعمدة، والجداول. نثبت سياسة التطبيع العربي وعتبات القبول قبل تشغيل بوابة القبول، ونعرض CER/WER الخام والمطبع منفصلين.
- مصادر المنهج: [OmniDocBench (CVPR 2025)](https://openaccess.thecvf.com/content/CVPR2025/papers/Ouyang_OmniDocBench_Benchmarking_Diverse_PDF_Document_Parsing_with_Comprehensive_Annotations_CVPR_2025_paper.pdf)، [DocVQA](https://www.docvqa.org/)، [PaddleOCR Arabic model support](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.en.md). توثيق PaddleOCR يحدد نموذج PP-OCRv5 للعربية؛ لذلك يجب تثبيت النموذج وإصداره في سجل التقييم.
