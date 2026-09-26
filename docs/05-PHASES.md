# 05 — خطة التنفيذ المرحلية (Phases & Gates)

## القاعدة
- مفيش Phase جديدة غير لما الـ gate بتاع اللي قبلها ينجح، والتقرير يتكتب في `eval/reports/phase-N.md` بالصيغة اللي في `03-TESTING.md` §5.
- صاحب المشروع هو اللي بيعتمد نتيجة الـ gate، مش Claude Code.

| Phase | الهدف | Gate |
|---|---|---|
| 0 | الأساس: repo، والعقود، والبنية التحتية، والمراقبة، والعزل | T-C-*، T-P0-01..05 |
| 1 | قياس النماذج المحلية على مهامنا | T-B-01..06 + اختيار نموذج لكل مهمة |
| 2 | Ingestion + Outline | T-ING-*، T-OUT-* |
| 3 | Chunking + Indexing | T-IDX-* |
| 4 | Knowledge Graph (LightRAG + Concept Layer) | T-KG-* |
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
| T-P0-04 | ناتج `docker inspect` للـ limits، والمجموع ≤ 60GiB |
| T-P0-05 | `docker ps --format '{{.ID}} {{.Names}} {{.Status}}'` لكل الحاويات الخارجية قبل وبعد |
| استهلاك فعلي | `docker stats --no-stream` لخدمات b2c وهي idle |

**لو Temporal كان تقيل أو معقد في التشغيل:** وقّف واكتب تقرير. البديل Prefect 3 (D-013) محتاج موافقة صاحب المشروع.

---

## Phase 1: قياس النماذج المحلية

### المدخلات
- **الفصل التجريبي:** VERIFY. الافتراضي فصل من OpenStax *Introductory Statistics*، أو فصل يوفّره صاحب المشروع (ولازم يكون `rights_status` بتاعه ≠ pending).
- **المرشحين (D-005):**
  - Qwen3-30B-A3B-Instruct-2507 Q4_K_M
  - gemma4
  - qwen3-coder-next، بإجراء القياس الخاص في 04-OPERATIONS §2

### المهام
1. **T-B-01 (السرعة):** لكل مرشح: `llama-bench -t 14,20,28 -p 512 -n 128 -r 3` (VERIFY الـ flags). سجّل pp وtg.
2. **T-B-02 (صلاحية JSON):**
   - استخرج نص الفصل مؤقتًا بـ Docling، أو باستخراج بسيط لو Docling لسه مش جاهز. سجّل الطريقة المستخدمة.
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
- Docling مع OCR لكل صفحة: الصفحة بتتحوّل لـ OCR لو كثافة الحروف المستخرجة أقل من عتبة.
- `ParsedBook`، وبعدها `CourseOutline` بالترتيب: bookmarks ← headings ← LLM.
- Golden: ‏20 صفحة متعلَّمة من الـ fixtures.

### Phase 3: Chunking + Indexing
- chunker structure-aware بطول 300–800 token ومع overlap بين 10 و15% ❓.
- bge-m3 dense+sparse، وbge-reranker-v2-m3.
- مقارنة dense وhybrid، بالـ rerank ومن غيره (T-IDX-02).

### Phase 4: Knowledge Graph
- LightRAG server بـ API key وعلى شبكة داخلية، وتخزينه PostgreSQL. VERIFY مشكلة AGE، والبديل Neo4j للـ graph.
- entity types مخصصة، وentity resolution، وConcept Layer.
- قياس زمن إدراج الفصل التجريبي (بدون وعود زمنية مسبقة).

### Phase 5: Enrichment + Pedagogy (EN)
- إثراء كل درس، وvalidator للـ citations، وRagas faithfulness.
- ملخص، وglossary، وأسئلة. الأسئلة بتعدّي على linter بقواعد Haladyna، وsolver بالدرس ومن غيره، وتصنيف Bloom.
- py-fsrs cards. وبعدها Gate G2.

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
