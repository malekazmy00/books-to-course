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
| D-007 | ميزانية الموارد: ≤ 85 GiB RAM و≤ 40 logical CPU (عُدّلت إلى 85 في 2026-09-29 بطلب المالك) | Accepted |
| D-020 | بيانات b2c على disk منفصل (VHDX على F:، قرص فعلي مستقل) | Accepted (2026-09-27) |
| D-021 | استثناء مؤقت لاستخدام CodeCraft عبر DSH للمساعدة البرمجية فقط | Accepted temporarily (2026-09-29؛ ينتهي عند عودة Claude Code) |
| D-022 | Temporary external APIs for development/runtime tests (CodeCraft + OpenRouter free tier); local model remains default | Active until first end-to-end MVP, then reassess |
| D-023 | Persistent book source and reusable artifacts for OCR/RAG/translation/speech | Accepted requirement; implementation/gates open |
| D-025 | OCR routes: local RapidOCR default, Surya quality option, Qwen free OpenRouter option, tested DeepSeek Vision paid option; Gemini only for detected table crops | Provisional pending Phase 2 gates |
| D-008 | بدون NUMA pinning | Accepted |
| D-009 | ممنوع لمس أي مشروع تاني، ومنها RabbitMQ بتاع Firecrawl | Accepted |
| D-010 | Parsing: Docling | Accepted |
| D-011 | تقييم Graph RAG مع LightRAG/PostgreSQL | Provisional; Phase 4 benchmark required |
| D-012 | Embeddings: bge-m3، وReranker: bge-reranker-v2-m3 | Accepted |
| D-013 | Orchestration: Temporal | Accepted (Prefect 3 بديل لو فشل Phase 0 gate) |
| D-014 | Observability: OpenTelemetry + Langfuse v4 + Prometheus/Grafana | Accepted |
| D-015 | TTS خلف واجهة `TTSProvider` قابلة للتبديل، والمرشحين الستة كلهم مسموحين | Accepted (الاختيار بالقياس في Phase 8) |
| D-016 | مراجعة الترجمة: QE routing آلي + طابور مراجعة بشري | Accepted (المراجع نفسه: Open) |
| D-017 | نطاق أول تجربة: فصل من كتاب أساسي + فصل من كتاب مساند | Accepted |
| D-018 | Spaced repetition: py-fsrs (FSRS-6) | Accepted |
| D-019 | المعالجة بالإنجليزي، والترجمة للعربي الفصحى في النهاية قبل السرد | Accepted |
| D-027 | Next-book front-matter exclusions and topic-focused questions | Accepted owner requirement; source prepared, deployment pending |

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

## D-004: تشغيل محلي كافتراضي
- الوضع الافتراضي لـB2C محلي، وكل النداءات تمر عبر llm_gateway.
- الاستثناءات الحالية لاختبارات التطوير موصوفة ومحدودة في D-021 وD-022؛ لا تُفعّل في البيئة الأساسية ولا ترسل محتوى كتب حقيقيًا للخارج.
- شرط إعادة فتح القرار: نجاح الـpipeline محليًا من أولها لآخرها على نطاق D-017، وقرار صريح من صاحب المشروع.

### استثناء مؤقت D-021 (بموافقة صاحب المشروع، 2026-09-29)
- يُسمح باستخدام CodeCraft عبر DSH Harness للمساعدة في كتابة ومراجعة الكود فقط، إلى أن يعود Claude Code للعمل.
- الاستثناء لا يغيّر تشغيل B2C ولا يجيز إرسال كتب المستخدم للخارج.
- تظل قواعد عزل المشاريع الأخرى والموارد وترتيب المراحل والاختبارات والـgates سارية بالكامل.
- ينتهي الاستثناء تلقائيًا عند عودة Claude Code.

### استثناء مؤقت D-022 — API لاختبار B2C
- بموافقة صاحب المشروع، يُسمح باتصالات llm_gateway إلى CodeCraft أو OpenRouter لاختبارات التطوير حتى اكتمال أول مسار MVP من الكتاب إلى الكورس.
- OpenRouter مفعّل فقط عبر docker/compose.openrouter.yml؛ المفتاح خارج المستودع ومركب للقراءة فقط. الوضع الأساسي محلي.
- الاختبارات تستخدم بيانات اصطناعية فقط؛ لا تُرسل كتب المستخدم أو محتوى غير منشور إلى الخدمة.
- بعد اكتمال MVP، نعود لتقييم النماذج المحلية، وينتهي هذا الاستثناء ما لم يمدده المالك صراحةً. تظل قواعد D-007 وD-009 والبوابات سارية.

## D-025: مسارات OCR الخارجية وضبط OpenRouter المجاني
- يظل RapidOCR المحلي هو الافتراضي السريع، وSurya المحلي خيار الجودة.
- Qwen3.8 27B Free هو مسار الرؤية المجاني الذي اختُبر، وDeepSeek V4 Flash Vision Exp مسار مدفوع اختُبر؛ لا يوجد انتقال تلقائي بينهما.
- يفرض عامل B2C حدًا محليًا موزعًا قدره 40 طلبًا من النماذج المجانية لكل يوم UTC، وفاصلًا أدنى قدره 6 ثوانٍ بين الطلبات. يُحتسب الطلب قبل إرساله، بما في ذلك المحاولات الفاشلة.
- صفحات OCR الممسوحة تُرسل صفحة واحدة في الطلب مع كاش ثابت لكل صفحة؛ النص الطويل يُجزّأ إلى دفعات محددة بعدد الصفحات والمحارف، ويُقسم مجددًا إذا انتهى الرد بسبب حد المخرجات. النتائج المكتملة تبقى في كاش Postgres لاستكمال العمل بعد بلوغ حصة اليوم.
- Gemini يبقى محصورًا في قصاصات الجداول التي اكتشفها Docling، ومفتاحه Docker secret في RAM فقط.
- الحدود تحفظ هامشًا من الحد المنشور للحساب المجاني (50 طلبًا يوميًا و20 في الدقيقة) لكنها لا ترى استهلاك المفتاح من تطبيقات أخرى. أي 429 يوقف النشاط بعد محاولتي retry محدودتين؛ لا يوجد تجاوز أو fallback مدفوع صامت.
- كل إرسال خارجي يظل اختيارًا صريحًا ضمن D-022، والاختبارات على بيانات التقييم فقط. هذا القرار مؤقت حتى بوابة Phase 2 وموافقة المالك.

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

### تعديل D-007 (2026-09-29، بطلب المالك)
- **السياق:** حد حاوية llama-server البالغ 26 GiB لا يترك مساحة كافية لاختبارات النماذج الأكبر؛ طلب المالك رفعه إلى 50 GiB على الأقل.
- **القرار:** الحد الافتراضي لحاوية llama-server أصبح 50 GiB، وحد مجموع حدود الذاكرة لحاويات B2C أصبح ≤ 85 GiB (الحساب الافتراضي الحالي 84.25 GiB). حد CPU ≤ 40 لم يتغير. الخدمات الثقيلة تبقى غير متزامنة.
- **النتيجة:** T-P0-04 وCLAUDE.md والتنبيه التشغيلي يعكسون الميزانية الجديدة. رفع الحد سقف متاح للحاوية ولا يحجز 50 GiB فعليًا.

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

## D-011: اتجاه بحثي لـ Graph RAG (قيد التحقق)
- **الاتجاه الحالي:** تقييم LightRAG مع PostgreSQL (KV وvector وdoc status والgraph) في Phase 4؛ هذا ليس تنفيذًا معتمدًا قبل اجتياز القياس.
  - تثبيت قاعدة بيانات graph وطريقة التخزين مشروط باختبار AGE والبدائل على corpus نفسه؛ الاختيار قابل للمراجعة حتى البوابة.
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
- **عينة Phase 1:** *Introduction to Statistics* (Online Statistics Education, David M. Lane) ككتاب أساسي، فصل 5 «Probability»، مع *Think Stats 2e* (Allen B. Downey) كمصدر مساند، فصل 3 «Probability mass functions». الأول مصرح أنه public domain؛ والثاني CC BY-NC-SA 4.0، مناسب للاستخدام البحثي غير التجاري مع النسبة والمشاركة بالمثل. المصادر والـ hashes وبيانات الاستخراج في `eval/golden/phase1/source-manifest.json`؛ الملفات والنصوص المقتطفة في قرص بيانات B2C على `/mnt/wsl/b2cdata/phase1-sources/`. حالتهما `internal_test_only`، ولا يسمح ذلك بإصدار كورس عام قبل مراجعة الحقوق وفق D-002. OpenStax مستبعد حاليًا لأن شروطه المنشورة تمنع إدخال محتواه في LLM/GenAI دون إذن كتابي؛ راجع سجل Phase 1.

---

## البنود المفتوحة (Open)
| ID | البند | بيتحسم إمتى |
|---|---|---|
| O-1 | المراجع البشري للترجمة | قبل Phase 7 |
| O-2 | نموذج TTS النهائي | Phase 8 |
| O-3 | النموذج المحلي لكل مهمة | Phase 1، ويتراجع في Phases 4 و5 و7 |
| O-4 | استخدام API بعدين | بعد نجاح E2E محليًا |

## D-023: Persistent content library
Keep immutable source PDFs on B2C durable storage, find duplicates by SHA-256, and persist versioned/provenanced outputs for OCR/parse, RAG, translation, and speech. Reuse only when input hashes, tool/model versions, schemas, and material settings match. Explicit rerun creates a new result. Enforce rights, access isolation, retention, deletion, backup, and restore. Partial implementation only: MinIO and parse/outline cache exist, but keys are course/upload scoped; RAG, translation, and speech reuse is unimplemented. Gate requires hit/miss/invalidation and access/deletion/restore evidence for each stage.


## D-024: Two separately evaluated text correction stages
- **Decision:** Evaluate OCR-specific text correction first, then a separate language-proofreading pass. Preserve the raw OCR artifact and record each stage's output, edit diff, provenance, model/version/settings, and review status.
- **OCR correction:** use aligned ground-truth training/evaluation, OCR-informed corruption examples, and/or aligned independent OCR hypotheses. Do not treat language fluency as evidence that the page was read correctly.
- **Proofreading:** preserve authorial/historical spelling, technical terms, quantities, names, quotations, and meaning; generative edits remain proposals until they pass held-out evaluation.
- **Acceptance:** measure each stage independently and in sequence using CER/WER, over-correction rate, inserted/deleted content, preservation of critical tokens, latency, and review volume. Split by book/title to prevent page leakage. Keep low-error pages as regression controls and route uncertain pages for review.
- **Current state:** design approved for comparison; no correction candidate has passed. The real English corpus shows PP-OCRv6 at 3.0573% micro CER/36.61s over 10 pages. Surya 2 improved two selected pages substantially (2.067% difficult, 0% easy) but took 126.91–168.83s/page at four CPUs; candidate only. Confidence routing is blocked until parser outputs carry usable confidence.

**Modern-print candidate update (2026-10-03):** PP-OCRv6 remains the fast English baseline. Surya 2 is the current quality-mode candidate from measured prior pages, but modern-print validation did not complete within the bounded CPU run and latency remains high. OvisOCR2 did not finish the modern-page CPU smoke. A six-clean/two-synthetic-page cohort showed severe PP-OCRv6 regressions on selected table/math pages, but its PDF-extracted references are not human gold. This is a provisional speed/quality shortlist, not production acceptance; the human-adjudicated mixed-layout gate remains required.


## D-025: Optional Gemini table transcription and remote-model adapters
- Keep the default PDF path local. Gemini table transcription is an opt-in evaluation route, enabled only when a file-backed Gemini key is configured; Docling must detect a table first, and only the rendered table crop is sent. On provider failure, retain Docling's table.
- The temporary B2C runtime may enable worker egress for this experiment. Do not persist the Gemini key or make remote calls the default. Obtain representative held-out table references and verify privacy/rights before product adoption.
- OpenAI-compatible providers remain explicit opt-ins with host-file credentials. Kiosapi has a draft Compose overlay, but this supplied key received HTTP 403 and is not configured in the running worker.
- Current DeepSeek V4 Flash free slugs returned HTTP 404 from OpenRouter. Do not substitute a paid model implicitly.

## D-026: Owner-selected build decisions for Phases 5–8 (2026-10-05)
- **Owner decision:** close the current build/preparation scope of Phases 5, 6, and 7, then evaluate end-to-end product quality during app integration and trial. This does not change any recorded test result or represent release/commercial gate passage.
- **Phase 5:** accept the bounded automated lesson-enrichment implementation. The 20-item source-specific artifact passed source/schema/Bloom/option-balance/readback checks; the solver scored 20/20 with the passage and 5/20 without it; 360 unit/contract tests passed. Defer independent labeled-corpus calibration (G2/T-SYN broader claims) to app experimentation.
- **Phase 6:** close the optional HippoRAG research choice as NO-GO for adoption. Continue with the existing RAG/hybrid retrieval path; no HippoRAG runtime dependency is required for the build.
- **Phase 7:** close the current terminology-preparation checkpoint using the 709-concept Wikidata CC0 EN↔AR candidate seed plus AGROVOC for explicitly confirmed agriculture. The resolver has 14 focused passing tests. Defer translation-activity integration, provenance persistence, QE calibration, and G3/G4 acceptance to app trial; the bounded seed is not claimed as a complete universal termbase.
- **Phase 8 model choice:** select Fasih-TTS-V1 for the upcoming B2C app implementation, using 4 intra-op / 4 inter-op threads as the provisional CPU setting. The three paired runs measured median 3.45x speedup and CER 3.77% vs 5.35% at 28/4. This is a build selection, not validated course-level audio quality; integrate and evaluate in the app trial. The owner has confirmed legal clearance for the non-commercial project.

## 2026-10-08 — Per-stage model preparation and residency
Keep one user-selected local preparation default per stage; managed Ollama defaults stay warm, alternates idle out, and readiness comes from real model probes. Store external credentials only in server secret files; unknown pricing remains unknown. Stage settings do not change current workflows or claim execution selection until frozen provider profiles are connected to activities. Source evidence and deployment requirements: docs/proposals/2026-10-08-model-lifecycle.md.



D-027 — Course starts at substantive book content; questions target topic, not labels (owner request, 2026-10-09)
- The next course begins at the actual instructional introduction or first substantive chapter. Explicit publisher/editor front matter and title-only pages are excluded from lesson spans and retained in the immutable parsed source with their block IDs and policy version.
- Headings supply lesson/module labels; they are not lesson content. Do not emit a lesson without substantive body text.
- Questions must assess the concepts and explanations in the body. Reject title/name/heading recall questions while allowing conceptual questions that mention the topic itself.
- Prepare source changes and focused regressions now. Do not change or regenerate the active book's outline, QA, translation, audio, or running workers. Activate only after the current book finishes, before the next book enters outline/question generation.
- Status: accepted owner requirement; source implementation and focused regressions are prepared, but not deployed. Activation and next-book validation remain pending until the active book's translation and audio finish. See `docs/proposals/2026-10-09-course-content-boundaries.md`.

## 2026-10-09 owner direction: automatic recovery

Challenges exposed by live book processing must become bounded, automated product behavior and regression checks. Manual operator splitting/copying/reassembly is not the product path. Preserve original source units and accepted artifacts; any adaptive translation parts must reassemble and pass full-source numeric/completeness validation. Stage retries reuse persisted compatible work. New APK has explicit partial-aware recovery; network fallback is same trusted origin only and must not blindly replay a mutating request after an ambiguous failure. No human review gate is introduced for normal automatic processing.
