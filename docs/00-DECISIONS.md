# 00 — سجل القرارات (Decision Log)

الحالات:
- **Accepted:** نهائي، ومايتفتحش تاني من غير طلب صريح من صاحب المشروع.
- **Deferred:** مؤجل لحد ما يتحقق شرط محدد.
- **Open:** لسه ماتحسمش.

كل قرار مكتوب بالترتيب ده: السياق ← القرار ← النتائج. ده نمط Architecture Decision Records (Michael Nygard, 2011).

| ID | القرار | الحالة |
|---|---|---|
| D-001 | المنصة **بحثية غير تجارية** | Accepted |
| D-002 | حقوق نشر الكتب مسؤولية المكتب القانوني | Accepted |
| D-003 | استنساخ الصوت بموافقة مكتوبة من فناني voice-over | Accepted |
| D-004 | **نماذج محلية فقط** في المرحلة الحالية | Accepted (الـ API مؤجل: Deferred) |
| D-005 | مرشحو الـ LLM المحليين | Accepted (الاختيار النهائي لكل مهمة بعد Phase 1) |
| D-006 | llama-server مخصص للمشروع، منفصل عن Ollama الموجود | Accepted |
| D-007 | ميزانية الموارد: ≤ 61 GiB RAM و≤ 40 logical CPU (عُدّلت من 60 في 2026-09-27) | Accepted |
| D-020 | بيانات b2c على disk منفصل (VHDX على F:، قرص فعلي مستقل) | Accepted (2026-09-27) |
| D-008 | بدون NUMA pinning | Accepted |
| D-009 | ممنوع لمس أي مشروع تاني، ومنها RabbitMQ بتاع Firecrawl | Accepted |
| D-010 | Parsing: Docling | Accepted |
| D-011 | Graph RAG: LightRAG فوق PostgreSQL | Accepted (نهج الـ graph مش قابل للتراجع) |
| D-012 | Embeddings: bge-m3، وReranker: bge-reranker-v2-m3 | Accepted |
| D-013 | Orchestration: Temporal | Accepted (Prefect 3 بديل لو فشل Phase 0 gate) |
| D-014 | Observability: OpenTelemetry + Langfuse v4 + Prometheus/Grafana | Accepted |
| D-015 | TTS خلف واجهة `TTSProvider` قابلة للتبديل، والمرشحين الستة كلهم مسموحين | Accepted (الاختيار بالقياس في Phase 8) |
| D-016 | مراجعة الترجمة: QE routing آلي + طابور مراجعة بشري | Accepted (المراجع نفسه: Open) |
| D-017 | نطاق أول تجربة: فصل من كتاب أساسي + فصل من كتاب مساند | Accepted |
| D-018 | Spaced repetition: py-fsrs (FSRS-6) | Accepted |
| D-019 | المعالجة بالإنجليزي، والترجمة للعربي الفصحى في النهاية قبل السرد | Accepted |

---

## D-001: الغرض بحثي غير تجاري
**النتائج:**
- التراخيص غير التجارية مسموح بيها: OmniVoice ‏(CC-BY-NC)، وHiggs TTS 3 ‏(research/non-commercial)، وFasih ‏(CPML)، وNLLB-200 ‏(CC-BY-NC)، ونماذج COMET-Kiwi/xCOMET.
- كل provider أو model لازم يسجّل ترخيصه في `capabilities()`، وواجهة التطبيق تعرض الـ attribution المطلوب.

**مخاطرة R-LIC:** لو المنصة اتفتحت للعامة، أو دخلها تمويل تجاري أو اشتراكات، لازم نراجع كل ترخيص NC ونوقف أي نموذج مايسمحش.

## D-002: حقوق الكتب
- الحقل `rights_status` في `BookUpload` إجباري، وقيمه: `cleared` أو `pending` أو `internal_test_only`.
- الـ pipeline يرفض نشر أي `CourseRelease` فيه كتاب حالته مش `cleared` (اختبار T-REL-01).

## D-003: موافقة الأصوات
- لكل فنان كيان `VoiceConsent`: الاسم، ونطاق الاستخدام، والتاريخ، ومرجع الوثيقة الموقعة، وتاريخ الانتهاء لو موجود.
- **ممنوع** استخدام `VoiceSpec` لصوت مستنسخ من غير `consent_id` ساري (اختبار T-TTS-04).
- عينات الأصوات تتخزن في bucket منفصل بصلاحيات محدودة.

## D-004: محلي فقط
- كل النداءات بتعدي على `llm_gateway` بواجهة OpenAI-compatible، وبتروح لـ llama-server المحلي.
- مفيش مفاتيح API سحابية في `.env`. والـ gateway متصمم بحيث إن إضافة provider سحابي بعدين تبقى تعديل إعدادات بس.
- **شرط إعادة فتح القرار:** نجاح الـ pipeline محليًا من أولها لآخرها على نطاق D-017، وقرار صريح من صاحب المشروع.

## D-005: مرشحو الـ LLM
| النموذج | ملاحظة |
|---|---|
| Qwen3-30B-A3B-Instruct-2507 (GGUF Q4_K_M) | المرشح الأساسي، MoE (~3B active) |
| qwen3-coder-next (~80B MoE, ~51GB) | للمهام الأعقد. محتاج إجراء قياس خاص لأنه بياكل معظم الميزانية (شوف 04-OPERATIONS) |
| gemma4 (~9.6GB) | للمهام الخفيفة زي التصنيف والفحوصات |

**قياسات سابقة على نفس السيرفر** (من تقرير جلسة سابقة):
- qwen3-coder:30b ≈ 6.35 t/s في التوليد
- qwen3-coder-next ≈ 7.6 t/s
- gemma4 ≈ 10.25 t/s
- glm-4.7-flash ≈ 11.8 t/s، لكنه أضعف في المنطق (5/10)

القياسات دي اتعملت عبر Ollama **قبل إصلاح ENV-001**، وعلى مهام استدلال وكود مش مهامنا. عشان كده هي **مرجع بس**، وهتتعاد في Phase 1 على مهامنا الفعلية.

## D-006: llama-server مخصص
- نستخدم صورة llama.cpp الرسمية `ghcr.io/ggml-org/llama.cpp:server` ‏(VERIFY الـ tag والـ digest).
- ممكن نعيد استخدام ملفات GGUF المنزّلة في Ollama ❓. Ollama بيخزنها كـ blobs، فمحتاجين نتحقق (VERIFY) إزاي نحدد الملف الصحيح. البديل: نحمّل GGUF من Hugging Face بـ revision مثبت.

## D-007: ميزانية الموارد
- تفاصيل كل خدمة في `04-OPERATIONS.md`.
- الخدمات التقيلة (LLM وDocling والـ embeddings وTTS) ماتشتغلش مع بعض في نفس الوقت. بنحقق ده بـ task queue واحدة اسمها `heavy` بـ concurrency = 1.
- الخدمات التقيلة بتاخد `cpu_shares: 512`، أقل من الافتراضي 1024. كده المشاريع التانية بتكسب وقت التنافس على المعالج، ودي آلية Docker CPU shares الرسمية.

### تعديل D-007 (2026-09-27، بموافقة صاحب المشروع)
- **السياق:** خدمات المراقبة (otel-collector وprometheus وgrafana وcadvisor) ماكانتش في جدول 04-OPERATIONS §2. استهلاكها المقاس فعليًا في Phase 0 خلّى المجموع النهائي ≈ 60.25 GiB.
- **القرار:** الميزانية بقت ≤ 61 GiB RAM. حد الـ CPU زي ما هو (≤ 40).
- **النتيجة:** T-P0-04 بيتحقق من ≤ 61 GiB، وجدول 04 §2 فيه بند مراقبة.

## D-020: disk منفصل لبيانات b2c
- **السياق:** في Phase 0 اتقاس إن الـ disk بتاع WSL بطيء في fsync (≈ 37ms/op)، وإن الـ ext4 journal مشترك مع كل المشاريع. b2c وهو idle رفع `/proc/pressure/io` من ≈ 3% لـ ≈ 12–19%. وأثناء عاصفة I/O سببها b2c، الـ healthcheck بتاع مشروع تاني أعاد إنشاء حاويتين بتوعه.
- **القرار:** بيانات حاويات b2c (postgres وclickhouse وminio وredis وprometheus وgrafana) تتخزن على VHDX مستقل في `F:\wsl-b2c\b2c-data.vhdx`، على قرص فعلي مختلف عن C: (اللي عليه disk بتاع WSL) وعن E: (اللي عليه النماذج). الـ VHDX بيتركّب بـ `wsl --mount --vhd … --name b2cdata` على `/mnt/wsl/b2cdata`.
- **النتائج:**
  - الـ bind mounts بتاعة compose بتستخدم `create_host_path: false`، فالحاويات مابتشتغلش أبدًا على مسار فاضي لو الـ disk مش متركّب.
  - `make up` بيتأكد من وجود marker file قبل التشغيل.
  - بعد كل ريستارت لويندوز، لازم الـ disk يتركّب تاني (Scheduled Task).
  - Docker data-root وimages بتاعة المشاريع التانية ماتغيّرتش.

## D-008: بدون NUMA pinning
WSL2 بيعرض NUMA node واحد، مع إن الجهاز dual-socket. عدد الـ threads هيتحدد بالقياس على ثلاث قيم: 14 و20 و28.

## D-009: عدم لمس المشاريع التانية
- RabbitMQ بتاع Firecrawl بيستهلك حوالي 3 أنوية بسبب Erlang busy-wait. **مفيش أي تعديل عليه.**
- **شرط إعادة فتح القرار:** لو قياس Phase 1 أظهر فرق سرعة أكبر من 10% بين وجود حمل RabbitMQ وغيابه، بمقارنة موثقة.

## D-011: LightRAG
- **التخزين:** PostgreSQL لكل أنواع التخزين (KV وvector وdoc status وgraph).
  - ⚠️ Apache AGE ليه مشاكل موثقة (apache/age#1721). VERIFY في Phase 4، والبديل Neo4j Community للـ graph بس.
- **Concept Layer:** طبقة خاصة بينا فوق LightRAG للعلاقات التعليمية محددة النوع (prerequisite_of, example_of, defines, …).
- **HippoRAG 2:** مقارنة اختيارية في Phase 6 خلف واجهة `GraphRetriever`، ومش أساس. السبب إنه لسه alpha، وفيه مشكلة `eval()` موثقة في تدقيق RAGU.
- **أمان:** LightRAG server افتراضيًا بيسمع على 0.0.0.0 من غير auth. **لازم** يشتغل بـ API key وعلى شبكة Docker داخلية بس.

## D-015: TTS
- مرشحين CPU الأول: **Supertonic 3** و**OmniVoice** ‏(VERIFY أداء OmniVoice على CPU).
- مرشحين GPU بعدين: Chatterbox Multilingual V3، وSILMA TTS v1، وHiggs TTS 3، وFasih TTS V1.
- الاختيار بمقاييس Phase 8: CER بالـ ASR، واستماع بشري، وACX checks.

## D-016: مراجعة الترجمة
- ثلاث مستويات بالترتيب:
  1. فحوصات حتمية.
  2. QE آلي: COMET-Kiwi + xCOMET + LLM-judge بتصنيف MQM على نمط GEMBA-MQM.
  3. طابور مراجعة بشري للجمل المشكوك فيها بس.
- ❓ GEMBA-MQM الأصلي اتقاس بـ GPT-4، وجودته بنموذج محلي مش مقاسة، فهتتعاير في Phase 7.
- للتوثيق البحثي: نسبة الجمل اللي اتراجعت بشريًا بتتسجل في كل `CourseRelease`.
- **Open O-1:** مين المراجع البشري.

## D-017: نطاق أول تجربة
- فصل واحد من كتاب أساسي + فصل واحد من كتاب مساند في نفس الموضوع.
- **Golden fixtures للاختبارات:** كتب OpenStax، وترخيصها CC BY 4.0 بيسمح بالاستخدام والتعديل. المقترح: *Introductory Statistics* ككتاب أساسي، و*Introductory Business Statistics* ككتاب مساند. VERIFY الترخيص على صفحة كل كتاب.

---

## البنود المفتوحة (Open)
| ID | البند | بيتحسم إمتى |
|---|---|---|
| O-1 | المراجع البشري للترجمة | قبل Phase 7 |
| O-2 | نموذج TTS النهائي | Phase 8 |
| O-3 | النموذج المحلي لكل مهمة | Phase 1، ويتراجع في Phases 4 و5 و7 |
| O-4 | استخدام API بعدين | بعد نجاح E2E محليًا |
