# 05 — خطة التنفيذ المرحلية (Phases & Gates)

> **تاريخية كخطة عمل (2026-10-31).** ترقيم Phase 0–10 لم يعد ترتيب العمل؛ يبقى مرجعًا لمعرّفات الاختبارات. ترتيب العمل في `docs/00-RESEARCH-CHARTER.md` وخطة إعادة الهيكلة المعدَّلة.

## القاعدة
- لا تُعتمد نتيجة phase أو إصدار يتجاوز بوابة المرحلة السابقة؛ يوثّق التقرير في `eval/reports/phase-N.md` بالصيغة اللي في `03-TESTING.md` §5.
- يجوز بتوجيه صاحب المشروع مواصلة تنزيل النماذج والبحث والتجهيز، أو بناء محدد في مرحلة لاحقة بالتوازي. هذا لا يعني اجتياز gate سابق ولا يسمح بإصدار يتجاوزه.
- صاحب المشروع هو اللي بيعتمد نتيجة الـ gate، مش Claude Code.

| Phase | الهدف | Gate |
|---|---|---|
| 0 | الأساس: repo، والعقود، والبنية التحتية، والمراقبة، والعزل | T-C-*، T-P0-01..05 |
| 1 | قياس النماذج المحلية على مهامنا | T-B-01..06 + اختيار نموذج لكل مهمة |
| 2 | Ingestion + Outline | T-ING-*، T-OUT-* |
| 3 | Chunking + Indexing | T-IDX-* |
| 4 | Knowledge Graph comparison (provisional LightRAG + Concept Layer) | T-KG-* |
| 5 | Enrichment + Pedagogy (EN) | T-SYN-*، T-PED-*، Gate G2 |
| 6 | (اختياري) HippoRAG 2 خلف GraphRetriever | مقارنة على نفس الـ golden |
| 7 | Translation + QE + review queue | T-TR-*، Gates G3/G4 |
| 8 | Diacritization + TTS + Audio QA | T-DIA-*، T-TTS-*، T-AUD-*، Gate G5 |
| 9 | Serving (API + web + SRS) | T-E2E-01..04 |
| 10 | Hardening: dashboards، وalerts، وbackups | T-OPS-01 + تشغيل كامل على نطاق D-017 |

**نطاق كل الـ phases لحد 9:** فصل من الكتاب الأساسي + فصل من الكتاب المساند (D-017). التوسع لكتب كاملة بيجي بعد Phase 10.

---

## Phase 0: الأساس

### المهام
1. **الـ Monorepo:** Python ≥ 3.11 و`uv` workspace (VERIFY)، وحزم زي `01-ARCHITECTURE.md` §10.
2. **`packages/contracts`:** موجودة بالفعل. اختباراتها `tests/contract/` لازم تنجح زي ما هي، وممنوع تعديلها لتنجح.
3. **`packages/core`:**
   - `stage_key`، ويتعاد تصديرها من العقود.
   - `ArtifactStore`: ‏S3-compatible.
   - `ProvRecorder`: جداول PROV.
   - config ‏(pydantic-settings) ولوج مهيكل (JSON).
4. **`packages/llm_gateway`:**
   - OpenAI-compatible client بيكلّم llama-server.
   - cache في Postgres.
   - structured output بـ JSON schema من Pydantic.
   - إعادة المحاولة مرتين عند المخرج غير الصالح، وبعدها `InvalidLLMOutput`.
   - OTel spans بـ GenAI attributes.
   - **مفيش provider سحابي** (D-004).
5. **`docker/compose.yml`:**
   - postgres(pgvector)، وtemporal + ui، وllm (llama-server)، وotel-collector، وprometheus، وgrafana، وcadvisor.
   - `docker/compose.observability.yml`: ‏Langfuse v4 من الـ compose الرسمي، مع الـ limits.
   - كل خدمة لازم يكون ليها `mem_limit` و`cpus` و`cpu_shares` حسب `04-OPERATIONS.md` §2.
6. **`packages/orchestration`:**
   - workflow تجريبي `HelloPipeline` من 3 activities idempotent.
   - task queues: ‏`light` و`heavy` (max_concurrent_activities=1) و`io`.
7. **Makefile:** بالأوامر اللي في `CLAUDE.md`.
8. **import-linter:** بقواعد الحدود.

### الـ Gate
| الاختبار | الدليل المطلوب في التقرير |
|---|---|
| T-C-01..08 | ناتج pytest |
| T-P0-01 | ناتج import-linter |
| T-P0-02 | screenshot أو export لـ trace من Langfuse |
| T-P0-03 | log لـ `kill -9` واستكمال الـ workflow |
| T-P0-04 | ناتج `docker inspect` للـ limits، والمجموع ≤ 85GiB |
| T-P0-05 | `docker ps --format '{{.ID}} {{.Names}} {{.Status}}'` لكل الحاويات الخارجية قبل وبعد |
| استهلاك فعلي | `docker stats --no-stream` لخدمات b2c وهي idle |

**لو Temporal كان تقيل أو معقد في التشغيل:** وقّف واكتب تقرير. البديل Prefect 3 (D-013) محتاج موافقة صاحب المشروع.

---

## Phase 1: قياس النماذج المحلية

### المدخلات
- **الفصلان التجريبيان (D-017):** فصل 5 «Probability» من *Introduction to Statistics* (Online Statistics Education / David M. Lane) ككتاب أساسي، وفصل 3 «Probability mass functions» من *Think Stats 2e* (Allen B. Downey) كمصدر مساند. الحقوق والمصادر والـ hashes وطريقة الاستخراج مسجلة في `eval/golden/phase1/source-manifest.json`. تُمرر الكتب بـ `rights_status=internal_test_only`؛ هذا يسمح بالتقييم الداخلي فقط ولا يجيز نشر CourseRelease. ملفات المصدر على قرص بيانات B2C المنفصل في `/mnt/wsl/b2cdata/phase1-sources/`.
- **المرشحين (D-005):**
  - Qwen3-30B-A3B-Instruct-2507 Q4_K_M
  - gemma4
  - qwen3-coder-next، بإجراء القياس الخاص في 04-OPERATIONS §2

### المهام
1. **T-B-01 (السرعة):** لكل مرشح: `llama-bench -t 14,20,28 -p 512 -n 128 -r 3` (VERIFY الـ flags). سجّل pp وtg.
2. **T-B-02 (صلاحية JSON):**
   - استخرج نص PDF بأداة المشروع `python -m b2c_ingest.pdf_text INPUT.pdf OUTPUT.txt` (اعتماد `pypdf` مثبت في `uv.lock`). هذا fallback للنص الرقمي فقط؛ الصفحات الممسوحة تتحول إلى Docling/OCR عند جاهزيته. سجّل الطريقة والصفحات المستخرجة.
   - قسّمه لـ 30 chunk.
   - اطلب استخراج الكيانات والعلاقات بـ JSON schema مبني على `EntityType`.
   - احسب نسبة المخرجات الصالحة.
3. **T-B-03 (جودة الاستخراج):**
   - 10 chunks فيها استخراج يدوي مرجعي في `eval/golden/phase1/`. صاحب المشروع أو Claude بيجهزوه، ويتعلّم إنه مرجع.
   - احسب entity recall وprecision.
4. **T-B-04 (التنافس):** tg في وقت عادي، وبعدين وقت أحمال المشاريع التانية المعتادة. **ممنوع** إيقاف أي مشروع تاني للمقارنة، ولو مفيش وقت هادي طبيعي، سجّل ده.
5. **T-B-05 (الذاكرة):** RSS فعلي لكل مرشح بـ `-c 16384`.
6. **T-B-06 (العربي):** 10 فقرات مترجمة لكل مرشح، ومحفوظة للتقييم اليدوي.
7. **الناتج:** جدول قرار لكل مهمة (استخراج، كتابة درس، أسئلة، ترجمة، تصنيف)، مع النموذج المقترح والدليل.

### الـ Gate
- كل أرقام T-B-01..06 متسجلة، ومعاها الأوامر والمخرجات الحقيقية.
- صاحب المشروع وافق على اختيار النموذج لكل مهمة (O-3).
- قرار D-009 (RabbitMQ) اتراجع بناءً على T-B-04.

---

## Phases 2–10: ملخص المهام

### Phase 2: Ingestion + Outline
- pypdf text parser plus Docling OCR fallback with explicit per-book OCR language (English or Arabic), source-page provenance, and table/layout extraction.
- ParsedBook then deterministic CourseOutline; bookmarks and headings are supported. LLM fallback for books without structure remains open.
- Human review/approval endpoint persists a separately versioned approved artifact.
- Golden evaluation: licensed representative pages spanning native text, Arabic/English scans, mixed pages, low resolution, skew, blur, tables, and multi-column ordering. Measure CER/WER, page/block provenance, heading recall, table/layout fidelity, latency, and peak memory.
- Current state (2026-10-02): upload/status API, pypdf path, Docling OCR fallback with explicit EN/AR routing, outline generation, and approval route exist. The earlier 73 unit/contract tests and 6/0 import-boundary check passed before the latest parser change. A compose-backed 37-page digital-PDF workflow completed through Temporal, MinIO, outline, and test approval. Arabic visual-to-logical normalization reduced the clean synthetic CER to 0.0928; the degraded sample remains 0.7113. Both are synthetic diagnostics, not acceptance. Postgres, MinIO, Temporal, API, and worker were restored after mounting the existing VHDX. The VHDX is currently rw at /mnt/wsl/b2cdata; the elevated at-logon Scheduled Task was registered and manually verified on 2026-10-02; reboot verification remains.
- Gate: not passed. See docs/PROJECT-STATUS.md, docs/COMMERCIAL-QUALITY-PLAN.md, and eval/reports/phase-2.md.

### Phase 3: Chunking + Indexing
- chunker structure-aware بطول 300–800 token ومع overlap بين 10 و15% ❓.
- bge-m3 dense+sparse، وbge-reranker-v2-m3.
- مقارنة dense وhybrid، بالـ rerank ومن غيره (T-IDX-02).
- **Gate status (2026-10-05): bounded internal research gate CLOSED; product/commercial acceptance OPEN.** T-IDX-01 passed its fixed-corpus system structural review, T-IDX-02 passed provisional metrics on 49 answerable items from 50 independently machine-judged questions, and T-IDX-03 passed only as a limited lexical diagnostic. None is human gold or commercial validation. See `eval/reports/phase-3.md`.

### Phase 4: Knowledge Graph comparison (research decision closed; production gate not passed)
- قورِن RAG مع LightRAG على corpus من 37 مقطعًا و50 سؤالًا آليًا موثق المصدر. LightRAG mix لم يتفوق على RRF hybrid في Recall@10 أو nDCG@10، وكانت entity-agreement F1=0.3119 وentity-resolution F1=0.7273، دون عتبات الجودة.
- T-KG-05 لم ينتج أي cross-book qrel مقبول من 39 سؤالًا أساسيًا؛ مولّد السؤال abstained في العينة المحددة. لذلك لا يوجد إثبات cross-book.
- T-KG-03 وT-KG-04 وT-KG-06 اجتازت فحوصها المعزولة. كل التسميات آلية وليست gold.
- **القرار: إغلاق بحث Phase 4 بتوصية NO-GO لإضافة graph إلى مسار الإنتاج؛ قبول المرحلة لم يجتز. استمر بمسار RAG.** يعاد فتح البحث مستقبلًا فقط بمرجع entities مستقل ومجموعة cross-book مدعومة ومقاييس ER مجتازة.
- التفاصيل والأدلة والتكلفة في eval/reports/phase-4.md; لا AI Company services/files were touched.

### Phase 5: Enrichment + Pedagogy (EN)
- إثراء كل درس، وvalidator للـ citations، وRagas faithfulness.
- ملخص، وglossary، وأسئلة. الأسئلة بتعدّي على linter بقواعد Haladyna، وsolver بالدرس ومن غيره، وتصنيف Bloom.
- py-fsrs cards. وبعدها Gate G2.
- اتصال التوليد الأولي لأسئلة الدروس/المجموعات/الكورس موثق في `eval/reports/phase-5.md`: يبدأ بعد اعتماد المخطط والفهرسة عبر `POST /v1/courses/{course_id}/qa/generate`؛ اكتماله لا يغلق G2.
- قرار 2026-10-05: إغلاق شريحة تنفيذ Phase 5 الآلية بعد اجتياز artifact المصدر/الـlint/solver المحدود و360 اختبارًا. يظل Gate G2 غير مجتاز إلى أن تتوفر معايرة مستقلة على corpus موسوم؛ لا يعني هذا القرار ادعاء جودة سكانية أو تجارية. التفاصيل في `eval/reports/phase-5.md`.

### Phase 6 (اختياري): HippoRAG 2
- ورا `GraphRetriever`، وفي حاوية معزولة. لازم مراجعة أمنية لمشكلة `eval()` قبل التشغيل.

### Phase 7: Translation
- بناء الـ termbase من الـ glossaries، وبعده Gate G3.
- ترجمة بـ chunks حوالي 500 كلمة مع حقن المصطلحات، وبعدها QE routing (D-016)، وطابور المراجعة، وGate G4.
- **Open O-1:** لازم يتحسم المراجع قبل ما Phase دي تبدأ.

### Phase 8: Speech
- CATT مقابل Sadeed (T-DIA-01).
- providers: ‏Supertonic 3 وOmniVoice على CPU. وتسجيل consent للأصوات المستنسخة.
- assembly، وloudnorm بمرورين (2-pass)، وACX، وASR CER. وبعدها Gate G5.

### Phase 9: Serving
- FastAPI: ‏`/v1/courses`، و`/v1/lessons/{id}`، و`/audio` مع Range، و`/segments`، و`/review-quiz`، و`/reviews`، و`/srs/due`.
- واجهة RTL، وتزامن النص مع الصوت، وكويز، وSRS، وWCAG 2.2 AA.

### Phase 10: Hardening
- dashboards، وalerts، وbackups، واختبار استعادة.
- تشغيل كامل على نطاق D-017، وتقرير نهائي.

## Owner decision: build-scope disposition (2026-10-05)
- **Phase 5 — CLOSED for the bounded build implementation.** Its 20-question artifact and solver/source checks passed on the current canary and 360 unit/contract tests passed. G2 independent labeled-corpus calibration is deferred to app trial; this is not commercial-quality acceptance.
- **Phase 6 — CLOSED as an optional research decision (NO-GO for HippoRAG adoption).** Keep the existing RAG/hybrid path; no HippoRAG component is required for app build.
- **Phase 7 — CLOSED for terminology preparation.** Use the current general Wikidata CC0 candidate seed plus agriculture-confirmed AGROVOC resolver. End-to-end translation activity, provenance, calibrated QE, and G3/G4 quality gates are deferred to app integration/trial; not claimed passed.
- **Phase 8 — Fasih selected for app integration.** Provisional CPU thread setting 4/4 is supported by three paired full-CATT runs. Phase 8 audio-quality/release gates remain for app experimentation.
- Phase 2 remains the current release/acceptance gate. Work on Phases 9 and 10 is preparation in parallel and does not imply release readiness.
