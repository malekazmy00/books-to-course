# تدقيق المرحلة 06: التربويات (Pedagogy): إثراء الدروس، توليد الأسئلة (MCQ وQ/A)، بوابات الـ solver والتقييم، وSRS

**التاريخ:** 2026-10-24
**الفرع:** `hardening`
**النطاق:** `packages/pedagogy`، و`packages/orchestration/src/b2c_orchestration/qa.py`، ومسارات API (`routers/qa.py` و`routers/serving.py` و`srs.py`)، و`eval/suites/phase5`، و`eval/reports/phase-5*`، والـ artifacts الحقيقية في MinIO/Postgres.
**المنهج:** قراءة فقط. لم يُعدَّل أي كود أو إعداد أو بيانات. القياس تم بسكربتات Python نُفذت داخل `b2c-worker-1` بـ boto3 وpsycopg في وضع القراءة فقط، ثم راجعتُ يدويًا عينة عشوائية ثابتة البذرة (seed). لم يُرسل أي محتوى من الكتب إلى خدمة خارجية.

**المنطلق:** كشف تدقيق الترجمة أن 57.4% من المقاطع "المقبولة" خاطئة (`outputs/translation-review-2026-10-24/TRANSLATION-ERROR-CATALOGUE.md` §1.1)، لأن البوابات كانت تفحص الشكل فقط. طبقتُ الشك نفسه هنا. النتيجة باختصار أن كلمتي "final" و"passed" في هذه المرحلة لا تعنيان "صحيح"، وأن مسار الـ MCQ لم يعمل في الإنتاج أصلًا.

---

## 1. غرض المرحلة وعقدها

### 1.1 الغرض
تحويل الدرس المعتمد (span من الكتاب الأساسي "spine" مع مصادر مساندة) إلى أصول تعليمية قابلة للتقييم:
- **L5 (Enrichment):** مسودة الدرس `LessonDraft`، وفيها جمل لكل جملة منها citation.
- **L6 (Pedagogy):** ملخص `LessonSummary`، ومسرد `GlossaryTerm[]`، وأسئلة `Question[]` (MCQ عليها وسم Bloom ودليل)، واختبار مراجعة من `prior_lessons`، وجدولة FSRS لكل متعلم.

### 1.2 العقد كما هو موثق
| الحد | الـ Artifact | الشرط (docs/02-CONTRACTS.md:52-53) |
|---|---|---|
| L5 ← L6 | `LessonDraft` | لكل جملة citation واحد على الأقل، والـ citation موجود فعلًا، وfaithfulness ≥ العتبة |
| L6 ← L7 | `LessonSummary`، `GlossaryTerm[]`، `Question[]` | الأسئلة عدّت الـ solver والـ linter |

العتبات في `docs/03-TESTING.md:80-96`:
- T-SYN-02: Ragas faithfulness ≥ 0.90 / citation support ≥ 95% على عينة بشرية من 30 جملة.
- T-PED-01: الـ linter يكشف ≥ 90% من 30 MCQ معيبة.
- T-PED-02: solver ≥ 95% بالدرس و≤ 30% بدونه.
- T-PED-03: سؤال واحد على الأقل فوق `remember` في كل درس.
- T-PED-04: أسئلة المراجعة من `prior_lessons` فقط.
- T-PED-05: FSRS parity.
- T-PED-06: رفض الـ datetime الذي بلا timezone.

### 1.3 ما تحتاجه المراحل اللاحقة
- **الترجمة (L7):** نصوص إنجليزية مستقرة ومقطعة (stem، والخيارات، والإجابة، والتعليل)، تُترجم كل واحدة منها segment مستقلًا (`orchestration/translation.py:57-86`).
- **الصوت (L8):** "ملحق" لكل درس فيه الأسئلة والإجابات بالعربية (`orchestration/audio.py:531-561`).
- **الخدمة (L9):** بنك أسئلة بمعرّفات ثابتة (`qid`) لـ `/review-quiz` و`/srs/due` و`/reviews` (`routers/serving.py:130-275`).
- **ما يلزم ضمنيًا ولم يُكتب في أي عقد:** أن تكون الإجابة المعلَّمة صحيحة، وأن يكون السؤال مفهومًا بالعربية وحده، وأن تغطي الأسئلة أهداف الدرس، وأن يبقى `qid` مرتبطًا بالمحتوى نفسه عبر الإصدارات.

---

## 2. التصميم كما هو منفذ فعليًا

### 2.1 مساران متوازيان للأسئلة، وواحد منهما فقط يعمل في الإنتاج

**(أ) مسار Grounded Q/A (سؤال قصير وإجابة حرة)**، في `qa_generation.py`:
- `_derive_scopes` (:252-350) تشتق نطاقات الدرس والوحدة والكورس، ونطاقات الامتحانات اختياريًا، من الـ outline.
- `_generate_batch` (:374-428): الـ LLM يولد سؤالًا وإجابة و`evidence_quote` يُنسخ حرفيًا من `quote_options`. الفحوص حتمية: الاقتباس substring حرفي، وجملة خبرية، وليس سؤال عنوان (regex)، ولا تكرار.
- `_judge_batch` (:442-468): **نفس الـ gateway ونفس النموذج** يحكم على مخرجاته (:633 و:639) بثلاث قيم boolean.
- نطاقات group/course: إذا كانت كل الدروس الأبناء final **ترث أول N سؤال من بنك الدروس الأبناء** (:595-606)، ولا تولد أسئلة تكاملية.

**(ب) مسار Lesson Enrichment (درس، ملخص، مسرد، MCQ)**، في `lesson_enrichment.py`:
- نداء واحد يولد الجمل والملخص والمسرد و"اقتراحات" الأسئلة (stem وanswer_text فقط) (:784-825).
- خياران لبناء الخيارات حتمياً:
  - `_build_source_grounded_mcqs` (:313-370): **المشتتات هي إجابات الأسئلة الأخرى في الدرس نفسه**، أي عبارات صحيحة في المصدر لكنها تجيب عن أسئلة أخرى.
  - `_build_numbered_procedure_mcqs` (:377-444): قالب ثابت "A learner must carry out this instruction… Which numbered step…؟" بخيارات `Step N` وBloom=`apply` مثبت في الكود (:435).
- بوابات: linter (`validation.py`)، وتوزيع Bloom ≥ 80% apply/analyze (`validation.py:83-97`)، وتوازن الطول ≤ 8 أحرف (:550، و`validation.py:76`)، وتوازن مواقع الإجابة، ثم تقييم وحل بالسياق (:913) وحل أعمى (:945) **بنفس الـ gateway**، والعتبات support ≥ 0.90 (:932) وwith ≥ 0.95 / blind ≤ 0.30 (:961).
- يعمل فقط إذا كان `include_lesson_enrichment=True`، والقيمة الافتراضية `False` (`orchestration/qa.py:104`، و`api/schemas.py:48`).

**(ج) SRS:** `srs.py` يغلف py-fsrs بالمعاملات الافتراضية ومن غير fuzzing. `api/srs.py` ينشئ الجداول عند أول استخدام. التقييم (rating 1-4) والوقت (`reviewed_at`) يأتيان من العميل (`api/schemas.py:120-132`).

### 2.2 نقاط اختلاف الوثائق عن الكود
| الوثيقة | الكود | الدليل |
|---|---|---|
| Ragas faithfulness (03-TESTING T-SYN-02؛ 02-CONTRACTS `faithfulness`) | لا يوجد Ragas. يُكتب `automated_support_judge` من حكم النموذج على نفسه، و`citation_support_rate: 1.0` ثابت | `lesson_enrichment.py:973-976` |
| T-PED-03: سؤال واحد على الأقل فوق remember | ≥ 80% apply/analyze. هذا يدفع النموذج إلى وسم أسئلة التذكر بـ apply، والقالب يضع `apply` ثابتًا | `validation.py:83`، `lesson_enrichment.py:435` |
| Phase 5 "CLOSED" (05-PHASES:151، D-026) على أساس 20/20 و5/20 | الـ artifact الذي أغلق المرحلة **قالبي ولم يولده LLM**، والـ blind solver اختار الخيار 0 في الأسئلة العشرين كلها (التفاصيل في §3.3) | `eval/reports/phase-5.md` "Final automated source-grounded gate"؛ solver-evaluation key `3f84497a…` |
| "الأسئلة عدّت الـ solver" شرط L6←L7 | مسار الإنتاج (Grounded Q/A) **ليس فيه solver ولا MCQ**، والترجمة والصوت يستهلكان artifact حالته `failed` | §3.1 |
| Haladyna (03-TESTING:11) | الـ linter يغطي 5 قواعد بـ regex (`validation.py:28-81`)، و30/30 على fixture اصطناعي مزروع من القواعد الخمس نفسها | `phase-5.md` "Automated gate expansion" |
| `enrichment_prompt_version` يتبع الـ prompt الحالي | مجمَّد عمدًا على `v2` بينما الكود `v4`، فـ checkpoints الـ prompt القديم تُقبل بعد إعادة تحقق حتمي فقط، من غير solver | `orchestration/qa.py:249`، `lesson_enrichment.py:34` |
| "Phase 5 (EN)" ثم ترجمة | لا يوجد عقد لترجمة MCQ كوحدة واحدة. الـ stem والخيارات والإجابة تُترجم segments منفصلة | `translation.py:70-80` |

---

## 3. الجودة المقاسة

### 3.1 ما الموجود فعلًا في الإنتاج
قائمة كاملة بـ `generate-course-qa/*/course-qa.json` في bucket `artifacts` (33 artifact):

| Artifact | الحالة | الدروس (متوقع/مغطى) | العناصر | النماذج | `lesson_assets` |
|---|---|---|---|---|---|
| `b2c-demo/…/38d9a570…` (Structured Analytic Techniques) | **failed** | 81/79 | 444 (394 qa_id فريد) | deepseek-v4-flash 369 + qwen3-coder-30b 75 | **0** |
| `b2c-demo/…/f0e8bbb4…` (كتاب غرامشي) | **failed** | 114/104 | 1059 (569 فريد) | deepseek-v4-flash | **0** |
| `b2c-demo/…/ae3be1a5…` | paused | 114/51 | 253 | deepseek | 0 |
| `b2c-demo/…/9143aefc…` | paused | 114/65 | 322 | deepseek 253 + qwen 69 | 0 |
| `phase5-live-tidx01…/6f876b92…` | final | 10/10 | 65 (50 فريد) | deepseek | 0 |

ما يترتب على هذا:
- **لا يوجد MCQ ولا ملخص ولا مسرد في أي artifact إنتاجي:** `lesson_assets=0` في كل artifacts الـ course-qa، وعدد checkpoints الـ enrichment تحت `_system/qa-checkpoints/*/enrichment/` صفر.
- **جداول SRS غير موجودة في Postgres:** `to_regclass('learner_srs_cards')` و`learner_review_history` يرجعان NULL. لا توجد releases (لا مفاتيح release)، فمسارات `/review-quiz` و`/srs/*` لم تُستخدم.
- **artifact خلط نماذج في نتيجة واحدة:** في `38d9` نموذجان. وفي `orchestration/qa.py:437` تُبنى `accepted_precompleted_model_ids` من الـ checkpoints نفسها، فيصبح فحص النموذج في `qa_generation.py:539` تحصيل حاصل.
- **مرحلة لاحقة استهلكت artifact حالته `failed`:** ترجمة الكورس `translate-content/d6bff832…` مصدرها `38d9…` (حالته failed، ونطاق الكورس failed). والسجل `book-progress/f24408f7…` يربط الصوت بها.

### 3.2 جودة الـ Grounded Q/A الحقيقية (862 عنصر درس من artifact‏ين)
**الطريقة:** مقاييس حتمية على كل العناصر (سكربت `qa_measure.py` و`patterns.py` في الـ scratchpad)، ثم مراجعة يدوية لعينة عشوائية من 50 عنصرًا (25 من كل كتاب، seed=20261024)، حكمتُ فيها على كل عنصر بالمقارنة مع `evidence_quote`.

| المقياس | 38d9 (346) | f0e8 (516) |
|---|---|---|
| أسئلة تبدأ بـ what / according (تذكر) | 77% | 77% |
| السؤال يشارك ≥ 80% من كلماته مع الاقتباس (cloze شبه حرفي) | 17.6% | 18.8% |
| < 50% من كلمات الإجابة موجودة في الاقتباس (دعم الاقتباس ضعيف) | 12.7% | 11.2% |
| الاقتباس جزء مقطوع من جملة | 32.4% | 2.5% |
| صياغة تعتمد على الوثيقة ("the text/the author/following paragraphs") | 5.5% | 21.9% |
| أسئلة عن عنوان أو heading (تخالف D-027) | 3.2% (11) | 0.4% |
| front/back matter (ISBN، الناشر، الفهرس) | 2 | 2 (فهرس) |
| اقتباس من حاشية (footnote) | — | 3.9% |
| chunks المصدر التي استُخدمت دليلًا مرة واحدة على الأقل | 169/397 = **42.6%** | 281/507 = **55.4%** |

**المراجعة اليدوية للعينة (50 عنصرًا):**
- **7/50 (14%) معيبة بوضوح ولا تصلح للمتعلم.** أمثلة:
  - "What publishing company is associated with the book…؟" (38d9-5).
  - "What heading is given for the Decision Matrix section in Chapter 10؟" وإجابته "10.7 Decision Matrix" (38d9-16). هذا التفاف على regex العناوين في `qa_generation.py:136-141`.
  - "According to the authors, what do the following paragraphs provide examples of؟" (38d9-9).
  - "What topics are listed under the index entry for Marxism؟ (Note: This question is based on the index entry…)" (f0e8-8). الإجابة فيها "Gramscian Marxism, spatial Marxism"، وهي غير موجودة في الاقتباس.
  - f0e8-16: الاقتباس هو "the originality of the living" فقط، والإجابة تفسير مخترع: "dynamic and creative body of thought that remains relevant and innovative".
- **5/50 أخرى إجابتها تتجاوز الاقتباس** (38d9-0 و18 و21، وf0e8-10 و17). قد تكون مدعومة في نص الـ chunk الكامل (UNVERIFIED).
- **≥ 45/50 أسئلة تذكر حرفي** (Bloom remember). مثال: "what is easy for analysts to do…؟" وإجابته "go astray".

**المحكِّم الذاتي لا يرفض شيئًا تقريبًا:** في نطاقات الفشل وحدها سُجل رفض 2 عنصر (38d9) و15 عنصرًا (f0e8). أما النطاقات الناجحة فـ**تحذف سجل الأخطاء** (`qa_generation.py:691-692` تبني `ScopeResult` من غير `errors`)، فلا يمكن حساب معدل الرفض الفعلي. وكل الأمثلة المعيبة أعلاه مرّت بالحكم `answer_supported=True` و`question_clear_and_answerable=True`.

**ترجمة الأسئلة:** عينة من 12 زوجًا من `translate-content/d6bff832…` (888 segment، و692 منها QA). الترجمة هنا أفضل منها في متن الكتاب، لكن الأسماء الاصطلاحية غير متسقة: "Complexity Manager" ← "مدير التعقيد" بينما "Key Drivers Generation™" بقي بالإنجليزية. والأسئلة المعيبة تُرجمت كما هي، مثل "ما هو اسم القسم المرقّم 10.7؟". لم أراجع الترجمة كلها، وهذا من نطاق المرحلة 07.

### 3.3 جودة مسار الـ MCQ (canaries فقط)
| Canary | المصدر | الأسئلة | blind (عتبة ≤ 0.30) | ملاحظة |
|---|---|---|---|---|
| thinkstats2 `4934f701` | Nemotron | 10 | **0.80** | فشل |
| thinkstats2 `b7dd700f` | Nemotron | 10 | **0.90** | فشل |
| seatweaving-j017 | Nemotron | 10 | **0.90** | فشل |
| seatweaving-sequence | Nemotron | 10 | **0.80** | فشل |
| saved-raw-rebuilt-v1 (`_build_source_grounded_mcqs`) | إعادة بناء | 20 | **1.00 (20/20)** | المشتتات غير متجانسة، مثلًا ['two strands','a needle','a twist','Step 7']. والإجابة يمكن معرفتها من `option_evidence_sids == evidence_sids` في 13/20 |
| procedure-template-v2 (`_build_numbered_procedure_mcqs`) | **قالب، بلا LLM** | 20 | 0.25 | **"النجاح" الوحيد، وهو مصطنع:** الـ blind solver أجاب `0` في الأسئلة العشرين كلها، ومواقع الإجابة موزعة 5/5/5/5 حتميًا، فالنتيجة 0.25 بالضرورة |

- **كل MCQ ولّده LLM فشل بوابة الـ blind** (0.80 إلى 1.00)، أي أن الأسئلة تُحل من غير قراءة الدرس.
- **البوابة الوحيدة التي "نجحت"** بُنيت على: (1) قالب يسأل "أي رقم خطوة"، وهو تذكر وُسم apply؛ (2) solver متدهور يعطي الخيار نفسه لكل سؤال؛ (3) توازن مواقع يجعل نتيجة ذلك السلوك 0.25 دائمًا. ومن أسئلته سؤال لا معنى له: "It may be done entirely by hand… but it is slow work. Which numbered step should the learner follow؟". وأول جملة في "الدرس" فيها ضوضاء ترويسة الصفحة: "12 SEAT WEAVING second peg…".
- **بيانات المصدر تكشف الإجابة للمتعلم:** `learner_question` (`api/serving.py:177-182`) يحذف `answer_index` لكنه يبقي `evidence_sids` و`option_evidence_sids`. في الباني المعتمد على المصدر، `option_evidence_sids[answer] == evidence_sids` بالبناء (`lesson_enrichment.py:362-367` و:648). قستُ ذلك: 13/20 في rebuilt-v1 و5/20 في template-v2 يمكن تحديد إجابتها بمقارنة القائمتين وحدها.

### 3.4 بنية الأسئلة على مستوى الوحدة والكورس
- `6f876b92`: سؤال الوحدة m01 (10 دروس) = 5/5 من الدرس m01.l01. سؤال الكورس = 5 من l01 و5 من l02. **سبب ذلك** `list(child_bank.values())[:N]` في `qa_generation.py:600`. فاختبار الكورس يغطي أول درسين من 10 فقط.
- `f0e8`: الوحدات الـ114 فيها درس واحد لكل وحدة (group sizes = {1: 114})، و490 من 543 عنصرًا على مستوى الوحدة والكورس نسخ من أسئلة الدروس. السبب الجذري في الـ outline، والأثر هنا. ومعرّفات الدروس فيها تقطيع OCR مثل `m40-a-whol-e-subt-err-ane-an-world`، وهي تدخل في الـ prompt والاسترجاع كـ `scope_title`.

### 3.5 الاختبارات
- اختبارات `tests/unit/pedagogy/` كلها mock: 82 دالة اختبار، ولا توجد gold set (لا يوجد `eval/golden/phase5`، والموجود `eval/golden/phase1` فقط).
- `eval/suites/phase5` فيه 28 سكربت canary لنموذج واحد في كل مرة، ولا يوجد harness تقييم قابل للتكرار.

---

## 4. المشكلات مرتبة حسب الخطورة

### حرجة (Critical)

**C1. البوابات الدلالية تحكم فيها النماذج على نفسها، فهي ختم مطاطي.**
- **الدليل:** نفس `gateway` يولد ويحكم (`qa_generation.py:633/639`، و`lesson_enrichment.py:821/913/945`). سؤال ISBN والفهرس والعنوان والتفسير المخترع كلها `answer_supported=True`. الرفض المسجل 17 مقابل 862 عنصرًا مقبولًا. الأدبيات توثق self-preference bias (Panickssery et al., NeurIPS 2024).
- **الأثر:** نحو 14% من الأسئلة الحالية معيبة بوضوح وتُنطق صوتيًا للمتعلم، ونحو 10% غيرها دليلها ضعيف.
- **السبب الجذري:** تصميم "LLM يتحقق من LLM" من غير معايرة على gold set وبلا نموذج مستقل.

**C2. مسار MCQ وSRS لم يعمل في الإنتاج، ومع ذلك أُعلن إغلاق Phase 5.**
- **الدليل:** `lesson_assets=0` في كل artifacts، وجداول SRS غير موجودة، و`include_lesson_enrichment=False` افتراضيًا. الـ "CLOSED" في `05-PHASES.md:151` يستند إلى artifact قالبي (§3.3).
- **الأثر:** المتعلم لا يحصل على MCQ ولا مراجعة متباعدة. وحالة المشروع المعلنة مضللة.
- **السبب الجذري:** Goodhart، أي تحسين الكود ليعبر البوابة بدل تحسين جودة السؤال.

**C3. بوابة الـ blind solver يمكن تجاوزها بالبناء، وقد نجحت بسلوك متدهور.**
- **الدليل:** blind choices = `[0]*20`، والتوزيع 5/5/5/5، والنتيجة 0.25 (`solver-evaluation.json` `3f84497a…`). لا يوجد فحص لتنوع إجابات الـ solver ولا اختبار إحصائي. ومع n=5 (القيمة الافتراضية) تعني العتبة 0.30 أن 1/5 مسموح، أي ما يساوي الصدفة تقريبًا.
- **الأثر:** البوابة لا تقيس قابلية التخمين.
- **السبب الجذري:** عتبة نقطية على عينة صغيرة، ونموذج solver واحد، وعدم مقارنة النتيجة بخط صدفة له فاصل ثقة.

**C4. كشف الإجابة للمتعلم.**
- **الدليل:** (أ) `/v1/courses/{id}` يعيد `course_qa` كاملًا ومعه `answer_index` و`rationale` (`routers/serving.py:112`)، و`/v1/lessons/{id}` يعيد `lesson_assets` الخام (:125). (ب) `learner_question` يبقي `option_evidence_sids` التي تحدد الإجابة (§3.3).
- **الأثر:** الاختبار وSRS بلا قيمة قياسية، ولا يمكن الوثوق بأي تقييم ذاتي.

### عالية (High)

**H1. المشتتات صحيحة في المصدر وغير متجانسة** (`_build_source_grounded_mcqs`).
- **الدليل:** blind 20/20، وخيارات مثل ['two strands','a needle','a twist','Step 7'].
- **الأثر:** إجابات متعددة قد تكون صحيحة (مشتت صحيح يجيب سؤالًا آخر)، أو إجابة واضحة لاختلاف نوعها عن البقية. هذا يخالف Haladyna: "make all distractors plausible" و"keep choices homogeneous in content and grammatical structure".

**H2. الـ Bloom وسم يضعه المولِّد ولا يتحقق منه أحد، والبوابة تكافئ الوسم الخاطئ.**
- **الدليل:** 80% apply/analyze في `validation.py:83`، والقالب يضع `bloom="apply"` ثابتًا في `lesson_enrichment.py:435`، ونحو 90% من الـ Grounded Q/A تذكر حرفي (§3.2).
- **الأثر:** لا يوجد تقييم للفهم أو التطبيق فعليًا.

**H3. الأسئلة على مستوى الوحدة والكورس منسوخة من أول الدروس فقط** (`qa_generation.py:600`، و§3.4).
- **الأثر:** الامتحانات النهائية لا تمثل الكورس.

**H4. تغطية المحتوى نحو 43-55% من chunks المصدر**، ولا يوجد ربط بأهداف تعلم.
- **السبب الجذري:** `_allocate_batch_counts` يوزع عددًا ثابتًا (5) على الـ batches، ولا توجد learning objectives في العقد.

**H5. التوليد بالإنجليزية ثم ترجمة segment لكل جزء.**
- **الدليل:** `translation.py:70-80`، و`audio.py:546` يكتب `"Explanation: "` بالإنجليزية داخل ملحق عربي.
- **الأثر:** المشتتات قد تتباين بعد الترجمة، والمصطلح يُترجم بطريقتين، ولا يوجد فحص بعد الترجمة لاتساق المفتاح وتوازن الطول. وجودة الترجمة نفسها في المرحلة 07 (57% قبول خاطئ).

**H6. `qid` موضعي وليس مبنيًا على المحتوى** (`lesson_enrichment.py:630`: `{lesson}:q{index:03d}`)، وجدول SRS مفتاحه `question_id`.
- **الأثر:** عند إعادة توليد الدرس يُربط تاريخ مراجعة المتعلم بسؤال مختلف من غير أن ينتبه أحد.

**H7. استهلاك artifacts حالتها `failed` أو `paused`، وخلط النماذج والإصدارات في artifact واحد.**
- **الدليل:** §3.1، و`orchestration/qa.py:437`، والـ prompt versions v1 وv3 مختلطة في `6f876b92`.
- **الأثر:** لا يمكن نسب الجودة إلى نسخة محددة، ولا إعادة إنتاجها.

### متوسطة (Medium)

- **M1.** فلتر سؤال العنوان والـ front/back matter مبني على regex ويُلتف عليه: 11 سؤال عنوان، وأسئلة ISBN والفهرس. الأصل أن يُستبعد ذلك في مرحلة الـ outline، كما تطلب D-027.
- **M2.** حذف `errors` من النطاقات الناجحة يجعل معدل الرفض والإعادة غير قابل للقياس (`qa_generation.py:691`).
- **M3.** `enrichment_prompt_version` مجمد على v2 (`orchestration/qa.py:249`)، فيُعاد استخدام checkpoints قديمة من غير solver.
- **M4.** SRS:
  - `reviewed_at` والتقييم يرسلهما العميل من غير حدود زمنية (`schemas.py:120-132`)، ولا يوجد idempotency key، فالإرسال المكرر يُحسب مراجعتين.
  - الـ rating تقييم ذاتي غير مشتق من صحة الإجابة.
  - المعاملات افتراضية ولا يوجد optimizer.
  - الـ DDL يُنفذ وقت التشغيل بدل migrations.
- **M5.** عتبة طول الخيارات المطلقة (≤ 8 أحرف) لا معنى لها بعد الترجمة، وتدفع نحو خيارات متدهورة مثل "Step N".
- **M6.** نطاقات `assessment` (midterm/term/final) تُولد بالطريقة نفسها، من غير blueprint ولا توزيع صعوبة.

### منخفضة (Low)

- **L1.** ملفان كبيران متشابكان (986 و738 سطرًا)، و`lesson_enrichment` يستورد دوال خاصة من `qa_generation` (`_derive_scopes`).
- **L2.** أكثر من 28 سكربت canary لنماذج بعينها في `eval/suites/phase5` بدل harness واحد.
- **L3.** `persist_course_qa` غير مستخدم في المسار الإنتاجي.

---

## 5. التصميم المستهدف وفق معايير الصناعة

### 5.1 المبادئ
1. **فصل المولِّد عن المحقِّق.** لا يحكم نموذج على مخرجاته. التحقق متعدد الطبقات: حتمي، ثم NLI/grounding متخصص، ثم LLM محكِّم من عائلة أخرى معاير على gold set، ثم إنسان على عينة.
2. **البدء من الأهداف (backward design).** هدف تعلم قابل للقياس (فعل Bloom ومحتوى) يأتي قبل السؤال. وما يُقاس هو التغطية والمحاذاة (alignment)، لا عدد الأسئلة.
3. **الوحدة الذرية هي "item" بمعرّف مبني على المحتوى**، يمر بدورة حياة واضحة: draft ← validated ← reviewed ← released ← retired. والمعيار المقترح للتبادل هو 1EdTech QTI 3.0.
4. **إنتاج العربية هو المنتج.** يُولَّد الـ item كوحدة واحدة ويُترجم كوحدة واحدة (stem وخيارات ومفتاح وتعليل في طلب واحد بمسرد مثبت)، أو يُولَّد بالعربية مباشرة من المصدر مع الاقتباس الإنجليزي دليلًا. وتُعاد البوابات الحتمية والدلالية بعد الترجمة.

### 5.2 المعمارية (model-agnostic)
```
Approved Lesson Span (spine chunks + aligned supplementary)
   │
   ├─(1) Objective Extractor  ──► LearningObjective[] {oid, bloom_verb, content, evidence_sids}
   │        (LLM-A, structured output)    └─ gate: كل هدف له دليل حرفي؛ يغطي ≥ X% من الجمل الجوهرية
   ├─(2) Lesson Draft / Summary / Glossary  (LLM-A) ── gate: NLI entailment لكل جملة (MiniCheck-class)
   ├─(3) Item Generator (per objective, per item type: MCQ / short answer / cloze / ordering)
   │        LLM-A يولّد stem + key + rationale + distractors مع "misconception" لكل مشتت
   ├─(4) Deterministic validators (Haladyna/IWF rules، تطابق الأدلة، طول/تجانس، لا كشف للمفتاح)
   ├─(5) Semantic validators (independent):
   │        a. Grounding: entailment(key | evidence) ≥ τ  و  ¬entailment(distractor_i | evidence-as-answer-to-stem)
   │        b. Answerability/uniqueness: ensemble solvers (≥2 عائلات نماذج) بالسياق = المفتاح بالإجماع
   │        c. Guessability: ensemble blind solvers، مع خيارات مُعاد ترتيبها عشوائيًا وعدة seeds؛ دقة لا تتجاوز خط الصدفة إحصائيًا
   │        d. IWF judge (LLM-B من عائلة مختلفة، rubric من 19 بندًا على طريقة SAQUET) معاير على gold set
   │        e. Bloom classifier مستقل (LLM-B) يقارن الوسم المعلن
   ├─(6) Item bank (Postgres): item_id = hash(content,evidence,version)، الحالة، المقاييس، الـ lineage
   ├─(7) Translation as unit + post-translation gates (key alignment، المصطلحات، الطول)
   ├─(8) Human review queue (عينة طبقية + كل ما هو borderline)
   └─(9) Release snapshot → Serving (لا يرسل المفتاح ولا الدليل قبل الإجابة) → FSRS
```

**فئات الأدوات المقترحة (من غير اختيار نموذج):**
- **Structured output** بـ JSON Schema كما هو اليوم.
- **Grounding/NLI checker صغير محلي**، من فئة MiniCheck (Tang et al., EMNLP 2024) أو AlignScore، لكل ادعاء مقابل دليله.
- **LLM-judge من عائلة مختلفة عن المولِّد**، معاير بـ Cohen's κ مقابل البشر.
- **Ensemble solvers** من عائلتين على الأقل، أحدهما صغير ومحلي.
- **أداة تقييم دفعات قابلة للتكرار** (eval harness)، تحفظ المدخلات والمخرجات والنماذج والـ prompts وتحسب المقاييس بفواصل ثقة.

### 5.3 بوابات الجودة
**حتمية (fail-closed):**
- الاقتباس substring حرفي، والدليل موجود، والمفتاح فريد، والخيارات من 3 إلى 5.
- لا "all/none of the above"، ولا مفتاح في الـ stem.
- لا front/back matter: يُعالج من الـ outline بوسم `block.region ∈ {front, body, back}` بدل regex.
- قواعد Haladyna القابلة للفحص الآلي.
- توازن الطول **نسبيًا** (مثلًا max/min ≤ 1.5 بالكلمات) ويُطبق بعد الترجمة أيضًا.
- معرّفات مبنية على المحتوى.

**دلالية (احتمالية ومعايرة):**
- grounding ≥ τ لكل ادعاء، و**المشتت غير مستلزم من الدليل** كإجابة عن الـ stem.
- اتفاق الـ solvers بالسياق.
- guessability: دقة الـ blind ensemble على الدفعة ≤ الصدفة + هامش، باختبار binomial (p < 0.05 للرفض) على **مستوى الدرس أو الكورس** لا على 5 أسئلة.
- IWF judge.
- Bloom المعلن = Bloom المصنَّف، أو يُعاد وسمه.

### 5.4 نقاط تدخل الإنسان (HITL)
1. اعتماد أهداف التعلم لكل وحدة. هذا رخيص لأنه سطر لكل هدف.
2. مراجعة عينة طبقية، مثلًا 10% من الـ items أو 30 لكل كتاب على الأقل، وكل item علّمه أي محقق بـ borderline.
3. بناء gold set (نحو 300 item موسومة بالـ IWF والمفتاح الصحيح وBloom) بمراجعَين واتفاق κ ≥ 0.6، لمعايرة المحكِّمات.
4. بعد الإطلاق: مراجعة الـ items التي تنحرف إحصاءاتها (§5.5).

### 5.5 مقاييس التقييم والعتبات المستهدفة
| المقياس | الطريقة | العتبة |
|---|---|---|
| صحة المفتاح (key correctness) | مراجعة بشرية لعينة | ≥ 98% |
| معدل الـ items المعيبة (أي IWF جوهري) | بشري على العينة / IWF judge معاير | ≤ 5% |
| grounding لكل جملة درس وكل مفتاح | NLI checker، معاير بشريًا | ≥ 0.95 support |
| تفرد الإجابة | ensemble بالسياق | ≥ 0.95 اتفاق مع المفتاح |
| guessability | blind ensemble، 3 ترتيبات | ≤ 1/k + 0.10، وp-value (binomial) لا يرفض الصدفة |
| تغطية الأهداف | items لكل هدف | 100% من الأهداف لها ≥ 1 item، و≥ 80% من الجمل الجوهرية مغطاة |
| توزيع Bloom المصنَّف (لا المعلن) | مصنِّف مستقل + عينة بشرية | ≥ 40% فوق remember، معايرة κ ≥ 0.6 |
| اتساق الترجمة | فحص المفتاح بعد الترجمة + مراجعة | 100% حتمي؛ ≥ 95% بشري |
| إحصاءات بعد الإطلاق (CTT) | ردود المتعلمين | p-value للسؤال 0.3–0.9، point-biserial ≥ 0.2، كل مشتت يُختار ≥ 5% |
| SRS | py-fsrs Optimizer بعد ≥ 1000 مراجعة | log-loss/RMSE أفضل من الافتراضي، desired_retention 0.9 |

### 5.6 الاختبار
- **وحدة:** كل validator حتمي له fixtures إيجابية وسلبية، منها حالات الالتفاف الحقيقية المسجلة هنا (ISBN، index، "What heading is given…").
- **Gold set (eval):** نحو 300 item حقيقية من الكتابين، موسومة بشريًا، تُقاس عليها precision/recall لكل محقق. يُرفض أي محقق recall‏ه على الـ items المعيبة < 0.85.
- **Mutation/adversarial:** حقن مفاتيح خاطئة، ومشتتات صحيحة، ومفاتيح مكشوفة، ثم قياس كشفها.
- **Regression:** تشغيل الـ harness عند تغيير prompt أو نموذج، ويُمنع الدمج إذا انخفض أي مقياس عن العتبة.
- **E2E:** من كتاب إلى item bank ثم release ثم API، مع التأكد من أن payload المتعلم لا يحوي أي حقل يكشف المفتاح.

---

## 6. تحليل الفجوة وخطة الترحيل

### 6.1 ما يُحتفظ به وما يُعاد كتابته وما يُحذف
| العنصر | القرار | السبب |
|---|---|---|
| `_derive_scopes` (اشتقاق النطاقات من الـ outline، ورفض عبور الحدود) | **يُحتفظ به** ويُنقل إلى وحدة scope مستقلة | منطق provenance سليم |
| فحص الاقتباس الحرفي + `quote_options` | **يُحتفظ به** | بوابة حتمية مفيدة |
| Temporal checkpoints / pause / resume | **يُحتفظ به** مع تصحيح الهوية (prompt version حقيقي، ونموذج واحد لكل artifact) | البنية التشغيلية جيدة |
| `_judge_batch` و`evaluate-and-solve` و`blind-solver` بنفس الـ gateway | **يُعاد كتابتها** كمحققات مستقلة | C1 وC3 |
| `_build_source_grounded_mcqs` | **يُحذف** | H1 |
| `_build_numbered_procedure_mcqs` | **يُحذف** (أو يبقى item type "ordering" صريحًا بوسم remember) | C2 وC3 |
| `lint_bloom_distribution` (80%) | **يُستبدل** بمصنِّف مستقل وهدف توزيع | H2 |
| وراثة group/course من أول N | **يُحذف**، وتُبنى الامتحانات من blueprint | H3 |
| `lesson_enrichment.py` و`qa_generation.py` | **إعادة هيكلة** إلى `objectives/`، `items/generate`، `items/validate/{deterministic,semantic}`، `bank/`، `assessments/` | L1 |
| `learner_question` ومسارات serving | **تُعاد كتابتها** بـ allowlist للحقول | C4 |
| SRS | **يُحتفظ بـ py-fsrs**، وتُضاف migrations وidempotency وrating مشتق وoptimizer | M4 |
| سكربتات canary (28) | **تُحذف** بعد بناء الـ harness | L2 |

### 6.2 الخطوات بالترتيب
0. **إيقاف فوري للنزيف (يوم أو يومان):**
   - إخفاء `answer_index` و`rationale` و`*_evidence_sids` من كل payload للمتعلم.
   - منع الترجمة والصوت من استهلاك artifact حالته ليست `final`.
   - تصحيح `05-PHASES.md` وD-026 لتقول إن Phase 5 **غير مغلقة**.
1. **التصحيحات الصغيرة:**
   - حفظ `errors` لكل نطاق، والرفض والإعادة كمقاييس.
   - نموذج واحد وprompt version واحد لكل artifact، أو تسجيل صريح لكل item مع منع الخلط في release.
   - `qid` مبني على المحتوى.
   - تحديث `enrichment_prompt_version`.
2. **Gold set والـ harness:** بناء نحو 300 item موسومة من الكتابين الحاليين، يمكن البدء بالـ 862 عنصرًا الحالية لأنها أمثلة سلبية جاهزة. ثم harness يحسب §5.5.
3. **المحققات المستقلة:** NLI grounding checker، وensemble solvers، وIWF judge من عائلة أخرى. المعايرة على الـ gold قبل أي استخدام.
4. **أهداف التعلم في العقد:** إضافة `LearningObjective` إلى `b2c_contracts` (major version)، والمولِّد يعمل لكل هدف.
5. **مولد الـ items الجديد:** المشتتات من misconceptions، مع فحص "المشتت غير مستلزم". أنواع متعددة (MCQ وshort answer وcloze).
6. **Item bank وrelease snapshots** في Postgres، مع حالات lifecycle.
7. **الترجمة كوحدة** مع بوابات ما بعد الترجمة، بالتنسيق مع المرحلة 07.
8. **الامتحانات من blueprint:** توزيع على الأهداف والصعوبة وBloom.
9. **SRS المكتمل:** migrations، وrating مشتق من الصحة والزمن، وidempotency، وحدود زمنية للـ `reviewed_at`، وOptimizer بعد تراكم البيانات.
10. **HITL queue وتحليلات CTT بعد الإطلاق.**

### 6.3 الاعتماديات على مراحل أخرى
- **Outline (المرحلة 03 تقريبًا):** وسم front/body/back، ووحدات ذات معنى (لا 114 وحدة كل منها درس واحد)، وعناوين نظيفة من تقطيع OCR. من غير ذلك ستبقى أسئلة ISBN والفهرس والنطاقات المكررة.
- **Parsing/OCR:** إزالة ترويسات الصفحات من الـ chunks، مثل "12 SEAT WEAVING".
- **Translation (07):** دعم ترجمة item كوحدة بمسرد، وإصلاح البوابات الدلالية هناك.
- **Audio (08):** نطق الأسئلة من الـ item bank المعتمد فقط، وإزالة "Explanation:" الإنجليزية.
- **Serving (09):** allowlist للحقول، ومصادقة المتعلم، وrelease snapshots.

### 6.4 المخاطر
- **تكلفة الاستدلال:** الـ ensemble والمحكِّم المستقل تقريبًا يضاعفان أو يثلثان النداءات. التخفيف: checker صغير محلي، والـ ensemble فقط على ما يعبر البوابات الرخيصة.
- **Goodhart من جديد:** أي بوابة آلية ستُحسَّن ضدها. التخفيف: gold set مخفية، وعينة بشرية دورية لا تُعرض على المولِّد.
- **العينة البشرية تحتاج وقت المالك أو مراجعًا متخصصًا:** إذا لم يتوفر ذلك لا يمكن ادعاء الجودة، ويُعلن ذلك صراحة.
- **كسر التوافق:** تغيير `Question` و`qid` يكسر العملاء الحاليين (Android). يُعالج بـ versioning للعقد.

---

## 7. معايير القبول (متى تكون المرحلة "منتهية")
تُعتبر المرحلة منتهية فقط إذا تحققت **كل** الشروط التالية على الكتابين الحاليين، وعلى كتاب ثالث لم يُستخدم في التطوير (held-out):

1. **صحة المفتاح:** ≥ 98% على عينة بشرية طبقية حجمها ≥ 150 item لكل كتاب (فاصل ثقة 95% حده الأدنى ≥ 95%).
2. **الـ items المعيبة (IWF جوهري أو front/back matter أو سؤال عنوان أو صياغة تعتمد على الوثيقة):** ≤ 5% بشريًا، وصفر لأسئلة ISBN والفهرس والناشر والـ heading.
3. **Grounding:** ≥ 95% من جمل الدرس ومن المفاتيح مدعومة بدليلها (NLI معاير، recall ≥ 0.85 على الـ items المعيبة في الـ gold set)، وتؤكده العينة البشرية.
4. **قابلية التخمين:** دقة الـ blind ensemble على كل كتاب ≤ 1/k + 0.10، مع إثبات أن الـ solvers لم تتدهور إلى إجابة ثابتة (entropy اختيارات ≥ 1.5 bit لأربعة خيارات).
5. **تفرد الإجابة:** اتفاق ensemble بالسياق مع المفتاح ≥ 95%.
6. **التغطية:** 100% من أهداف التعلم المعتمدة لها item واحد على الأقل، و≥ 80% من جمل المحتوى الجوهرية لها item.
7. **Bloom:** المصنِّف المستقل (κ ≥ 0.6 مقابل البشر) يرى ≥ 40% من الأسئلة فوق remember، والفرق بين الوسم المعلن والمصنَّف ≤ 10%.
8. **الامتحانات:** كل امتحان وحدة أو كورس يغطي ≥ 80% من دروس نطاقه حسب الـ blueprint، وصفر وراثة "أول N".
9. **العربية:** 100% من الـ items المترجمة تعبر البوابات الحتمية بعد الترجمة (المفتاح، الطول النسبي، المصطلح)، و≥ 95% صحيحة في مراجعة بشرية.
10. **الأمان:** اختبار E2E يثبت أن payload المتعلم لا يحتوي `answer_index` ولا `answer_text` ولا `rationale` ولا `*_evidence_sids` قبل الإجابة.
11. **SRS:** الجداول عبر migrations، وidempotency مختبر، و`qid` مبني على المحتوى، وrelease جديد لا يعيد ربط card بسؤال مختلف (اختبار).
12. **قابلية التكرار:** كل artifact يُنتج بنموذج واحد وprompt version واحد مسجلين، والـ harness يعيد حساب كل المقاييس أعلاه بأمر واحد، ويُرفض أي artifact حالته ليست `final` في المراحل اللاحقة.

---

### المراجع
- Haladyna, Downing & Rodriguez (2002). *A Review of Multiple-Choice Item-Writing Guidelines*. Applied Measurement in Education 15(3). https://doi.org/10.1207/S15324818AME1503_5
- Moore, Costello, Nguyen & Stamper (2024). *An Automatic Question Usability Evaluation Toolkit (SAQUET)*، 19-criterion IWF rubric. https://arxiv.org/abs/2405.20529
- Tang, Laban & Durrett (2024). *MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents*. EMNLP 2024. https://aclanthology.org/2024.emnlp-main.499
- Panickssery, Bowman & Feng (2024). *LLM Evaluators Recognize and Favor Their Own Generations*. NeurIPS 2024. https://papers.nips.cc/paper_files/paper/2024/hash/7f1f0218e45f5414c79c0679633e47bc-Abstract-Conference.html
- Alberti et al. (2019). *Synthetic QA Corpora Generation with Roundtrip Consistency*. ACL 2019. https://aclanthology.org/P19-1620
- open-spaced-repetition / py-fsrs (Scheduler + Optimizer). https://github.com/open-spaced-repetition/py-fsrs ، وhttps://github.com/open-spaced-repetition/awesome-fsrs
- 1EdTech QTI 3.0. https://www.1edtech.org/standards/qti
- Anderson & Krathwohl (2001). *A Taxonomy for Learning, Teaching, and Assessing* (Revised Bloom).
- Classical Test Theory item analysis (p-value، point-biserial، distractor analysis): معيار سائد في القياس التربوي. لم أتحقق من مصدر واحد محدد على الإنترنت لهذه العتبات (UNVERIFIED كاستشهاد، لكنها ممارسة معروفة).

### أشياء لم أتحقق منها (UNVERIFIED)
- هل الإجابات التي تتجاوز الاقتباس (5/50) مدعومة في نص الـ chunk الكامل.
- معدل رفض المحكِّم الفعلي في النطاقات الناجحة، لأن الأخطاء تُحذف.
- جودة الترجمة الكاملة للأسئلة (692 segment)، فقد راجعتُ 12 فقط.
- سلوك SRS مع بيانات حقيقية، لأنه لا توجد بيانات.
