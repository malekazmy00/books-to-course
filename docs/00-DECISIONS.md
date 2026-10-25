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
| D-028 | Translation rebuild: source-only engine input, rule-based references, sentence units, footnotes restored by code, locked termbase, deterministic + semantic QA, explicit acceptance, human review queue, TM; model choice deferred | Accepted 2026-10-24 (owner); see docs/proposals/2026-10-24-translation-rebuild.md |
| D-029 | واجهات النماذج الخارجية مسموحة **أثناء التطوير** لإثبات فكرة بسرعة بدل النموذج المحلي البطيء، حتى على نص الكتب الحقيقية، بقرار المالك. التشغيل الإنتاجي يبقى محليًا افتراضيًا. الإرسال السابق إلى OpenRouter/DeepSeek تم بقرار المالك، وتُحفظ سجلاته ولا تُحذف | Accepted 2026-10-24 (owner) |
| D-030 | الـ API متاح داخل الـ tailnet فقط (Tailscale Serve)، ولا نشر عام (Funnel مُغلق منذ 2026-10-24)؛ والتوثيق برمز إلزامي افتراضيًا | Accepted 2026-10-24 (owner) |
| D-031 | قاموس الكتابين يُعتمد **مؤقتًا** بإيجنت ذكاء اصطناعي بدل مراجع بشري، بمعرّف مراجع `ai-provisional-*` يميّزه، ويُعاد مراجعته بشريًا لاحقًا؛ Gate G3 لا تُعدّ مجتازة بهذا الاعتماد. الفاحص الدلالي أثناء التطوير: DeepSeek عبر OpenRouter (`deepseek-mqm`، D-029)، والمحرك Gemma 4 محليًا | Accepted 2026-10-25 (owner) |
| D-032 | شكل المنتج: نواة أمينة (ترجمة الكتاب كاملًا مرجعًا) + طبقة مفاهيم (المصطلح بالأصل عند أول ذكر، وتعريف المؤلف، والعلاقات) + أشكال مشتقة مرنة (ملخص، حوار، مناظرة/نقد، أسئلة) مسندة إلى مواضع الكتاب؛ ثلاثة أصوات مميزة (المؤلف/الكورس/مصدر خارجي)؛ مرحلتان جديدتان: الحواشي والرفيق البحثي. التفاصيل: docs/proposals/2026-10-25-learning-companion.md | Accepted 2026-10-25 (owner) |
| D-033 | لا مراجعة بشرية في خط الإنتاج: الجودة آلية بمحكّمين من عائلات نماذج مختلفة وسلّم تصعيد آلي؛ ما يبقى فيه شك يُعلَن للمتعلم كحاشية في موضعه مع النص الأصلي ولا يوقف الإنتاج ولا يُحجب. يحل محل طابور المراجعة البشرية في D-028، ويجعل D-031 (اعتماد القاموس بالنماذج) دائمًا | Accepted 2026-10-25 (owner) |
| D-034 | البحث مرحلتان: «بحث المعرفة» قبل الترجمة بلغة الكتاب (المؤلف، والسياق، والتلقي، والمفاهيم في مجالها؛ يفيد الترجمة نفسها ثم يُترجم بالقاموس نفسه)، و«بحث اللغة» بعد الترجمة بالعربية (المصطلحات غير المحسومة والمواضع المتعثرة، وتصير حواشي). المقابلات العربية المعروفة تُجمع قبل الترجمة لأن القاموس يُثبَّت قبلها | Accepted 2026-10-25 (owner) |
| D-035 | كل المحتوى الإثرائي (بطاقة الكتاب، والمؤلف، والمفاهيم، والنقد، والمناظرة، والملخص، والحوار، والأسئلة) يُنتَج بالإنجليزية من النص الأصلي ثم يُترجم بمسار الترجمة نفسه وقاموسه؛ العربية لما يخص الترجمة فقط (القاموس، والترجمة، وحواشي العجز، والمصادر العربية). يدقّق D-034: بحث المعرفة كله بالإنجليزية | Accepted 2026-10-25 (owner) |
| D-036 | (1) أوصاف الأشكال والجداول تبقى داخل الدرس (مفيدة للمتعلم الكفيف). (2) المقدمة (Preface/Foreword) جزء من الدروس، وتُكيَّف للسماع إن احتوت ما لا يناسبه (قوائم شكر، جداول). (3) قبل كل درس «تقدمة حلقة»: اسم الكتاب، ورقم الحلقة وعنوانها، ومراجعة سريعة للحلقات السابقة؛ تُنتَج بالإنجليزية ثم تُترجم (D-035) | Accepted 2026-10-25 (owner) |
| D-037 | الحلقة 1 «مقدمة الكورس» تُولَّد (بالإنجليزية ثم تُترجم): الكتاب والكاتب، وأهمية الكتاب وهدفه، ومنهجيته، وهيكل الكورس وطريقة الاستفادة منه، وفي آخرها الشكر مختصرًا. حلقات الكتاب تبدأ من 2. الشكر المستقل لا يصير درسًا (مصدر لمقدمة الكورس)، والشكر داخل المقدمة يبقى نصه في الدرس ولا يُقرأ هناك | Accepted 2026-10-25 (owner) |
| D-038 | (1) تسمية الحلقات بأرقام مسبوقة بأصفار لمشغلات MP3 على الكروت: `00` مقدمة الكورس، ثم `01`..`n` لحلقات الكتاب (العرض يكبر إلى 3 أرقام من 100 حلقة)، مع رقم المسار في ID3، ونسخ الملفات إلى الكرت بالترتيب (و`fatsort` للإصلاح). (2) نهاية كل حلقة: ملخص الدرس، ثم تذكير بأهم المصطلحات، ثم خمسة أسئلة بإجاباتها | Accepted 2026-10-25 (owner) |
| D-039 | استخراج المصطلحات v3: مدخل القاموس ثلاثة مستويات `locked`/`hint`/`dropped` بدل قبول/رفض (SCHEMA 1.15.0)، وفحص مقفول يتحمل التصريف العربي، وإصلاحات حتمية لضوضاء الاستخراج، ومصنّف termhood عبر llm_gateway **معطّل افتراضيًا**. القواميس المعتمدة الحالية = locked وبصماتها لا تتغير. التفاصيل: docs/proposals/2026-10-25-term-extraction-v3.md وeval/reports/term-extraction-v3.md | Accepted for implementation 2026-10-25؛ تفعيل المصنّف Deferred |
| D-040 | مرحلة حواشي المترجم: كل وحدة ترجمة غير مقبولة (`needs_review`/`rejected`) تصير `TranslatorNote` في موضعها بصوت الكورس، فيها أفضل ترجمة متاحة موسومة «ترجمة تقريبية» (أو لا شيء إن لم تصلح ترجمة)، والنص الأصلي، وسبب مفهوم للمتعلم بلا مصطلحات تقنية (SCHEMA 1.16.0). بوابة `translator-notes.coverage` تثبت أن كل وحدة مقبولة أو مُسقطة أو لها ملاحظة. بوابة التجميعة الصارمة وشرط الكلام لم يتغيرا بعد. التفاصيل: eval/reports/translator-notes.md | Accepted for implementation 2026-10-25؛ ربط مرحلة الكلام بالحواشي Open |
| D-041 | الحواشي تصل إلى الصوت والحلقة: مسار قبول بديل صريح للكلام (`notes_ref` = حواشي مقبولة ببوابة `translator-notes.coverage` ومطابقة للنتائج الحالية) بجانب المسار الصارم الذي لم يتغير؛ الوحدة ذات الحاشية تُنطق بصوت الكورس: الإعلان ثم الترجمة التقريبية، ولا يُقرأ الأصل الإنجليزي؛ الحلقة تأخذ نص الوحدة غير المقبولة من حاشيتها؛ ولا تُعرض ترجمة تقريبية لمشكلة معنى/نفي حرجة أو أصل بلا كلمات أو نص طويل لأصل قصير (SCHEMA 1.18.0). التفاصيل: eval/reports/translator-notes.md §6 | Accepted for implementation 2026-10-25 (coordinator, owner principle D-033) |
| D-042 | مخرجات الصوت النهائية للمتعلم: (1) ملف الحلقة MP3 أحادي، MPEG-1 Layer III، ‏44.1kHz، ‏CBR ‏96kbps افتراضيًا (64 مسموح)، بعد تطبيع الجهارة إلى ملف `app-speech-16` قبل الترميز؛ (2) وسوم ID3v2.3 (لا 2.4) بنص UTF-16 مع BOM: ‏TIT2 وTALB وTRCK «k/total» وTPE1 وغلاف اختياري؛ (3) قص صمت حواف كل مقطع إلى حارس 40ms وطرح الحارس من الوقفة فتصير الوقفة المخططة هي المسموعة؛ (4) حد المسار بالمدة لا بالعدد، و`max_track_seconds` ‏1800 ث افتراضيًا لأن ذاكرة التجميع المقيسة ≈ 2.1 GiB لساعة وحد عامل الصوت 2 GiB؛ (5) `scripts/copy-course-to-card.py` ينسخ بالترتيب مع fsync ويتحقق من ترتيب مدخلات FAT. بلا تغيير عقود. التفاصيل: eval/reports/mp3-delivery.md | Accepted for implementation 2026-10-25؛ الجهارة النهائية (Q2، ‎-16 مقابل ‎-19 LUFS للأحادي) Open للمالك |
| D-043 | بوابة الاستخراج v2 تقيس اتفاق محلّلَين مستقلَّين: كل صفحة تُقارن بطبقة النص كما يستخرجها pypdf وpdfium، ولا تفشل إلا إذا خالفتهما معًا (F1 كلمات < 0.95). أُزيل حد الـ 2000 حرف، وحل محله فحص الفقرات المدموجة من هندسة أسطر طبقة النص (≤ 1%). فصل حتمي للكلمات الملتصقة في S2 (bigrams الكتاب + wordfreq، مع حماية أسماء العلم والمصطلحات، وشرطة مسترجعة من pdfium). الحكم `GateResult` ‏`ingest-v2.quality` (SCHEMA 1.16.0: `page_findings`). المفتاح `B2C_INGEST_V2_ENABLED` **معطّل افتراضيًا**. التفاصيل: eval/reports/ingest-v2-gate.md | Accepted for implementation 2026-10-25؛ التفعيل الإنتاجي Deferred (قائمة الجاهزية) |
| D-046 | فحص الأسماء v2: وحدات المراجع والفهرس تُنسخ بالقاعدة ولا تُفحص فحص أسماء أو مصطلحات؛ الاسم في الحاشية ومصدر الشكل يُقبل بتهجئته الأصلية؛ الاسم في المصدر بحالة أحرفه؛ وتطبيع محدود لتهجئة الاسم المنقول (همزات، حروف فارسية، حرف مد واحد، جيم للـ g، الحرف الأول بحرف واحد) مع إعادة استعمال مطابق D-039؛ وإصلاح خلل لواحق D-039. يزيل 302 من 481 إنذارًا مخزنًا دون إسقاط أي خطأ حقيقي | Accepted 2026-10-25 (تنفيذ بتكليف؛ تبديل أدوات الربط في المراجع: Open) |
| D-048 | المراجع الأجنبية تبقى كما هي بالكامل، بما فيها أدوات الربط (`Ibid., p.`)، اتباعًا لأسلوب الترجمات الأكاديمية المعتمدة (المركز العربي، «دليل التحرير» ص 46–51). قرار المالك 2026-10-25 | Accepted |
| D-045 | إصلاحات S2 للعيوب التي سمّتها بوابة D-043 (`NORMALIZER_VERSION=3`): قطع عناصر Docling عند نهايات فقرات طبقة النص، واسترجاع عناوين الأشكال (أبناء الصورة، و`Figure n.n` المعلَّم عنوانًا، وbookmark يشير إلى ما بعد آخر نص في صفحته)، وجمع نص OCR من صورة نقطية في `figure`، وإصلاح سلاسل الحواشي، وحدود الكلمات المتباعدة من فراغات pypdf المزدوجة، والواصلة بدليل من الكتاب (≥ 2). تصحيح قياس `outline_match_rate` للعنوان المطبوع أعلى الصفحة التالية (`REPORT_VERSION=3`) بلا تغيير عتبة. SAT وGramsci يجتازان `ingest-v2.quality`. التفاصيل: eval/reports/ingest-v2-fixes.md | Accepted for implementation 2026-10-25؛ التفعيل الإنتاجي Deferred |
| D-044 | (1) الأشكال والجداول **تُقرأ في الصوت بوصفها** (قرار المالك 2026-10-25، للمستمع الكفيف)، بدل استبعادها من الكلام: وصف إنجليزي بصوت الكورس لكل شكل/جدول (يُترجم بـ UnitTranslator والقاموس المعتمد)، من العنوان ونص الشكل والفقرات المشيرة ووصف الكتاب المطبوع والصورة إن رآها النموذج، بإرشادات DIAGRAM/NCAM؛ بوابة إسناد حتمية (لا رقم ولا اسم خارج الدليل)؛ الجدول الصغير (≤ 8 صفوف، ≤ 4 أعمدة، ≤ 24 خلية، ≤ 160 كلمة) صفًا صفًا والكبير ملخص؛ يُقرأ بعد وحدة الإشارة إليه معلنًا «وصف الشكل.» ومختومًا «انتهى وصف الشكل.» (SCHEMA 1.20.0). (2) ترميز الحلقة MP3 متدفق بذاكرة ثابتة، وموصول بـ workflow ‏`EncodeEpisodeMp3Workflow` بعد الصوت ببوابتي الجهارة (‎-16 LUFS) والتنسيق، لا يبدأ تلقائيًا. التفاصيل: eval/reports/figure-descriptions.md | Accepted for implementation 2026-10-25 (owner principle)؛ التفعيل على الكتب Open |
| D-047 | الأسماء المعتمدة في الوحدة تُرسل إلى المحرك بقالب `chat.entities@v1` (بلا تغيير للـ prompt حين لا أسماء)؛ وحقل تهجئات مقبولة للأسماء `accepted_variants_ar` يقبله الفحص ولا يُحقن (SCHEMA 1.22.0، البصمات القديمة لا تتغير)؛ وقاعدة استخراج v3.1 تُبقي الأسماء المصوغة (™، و-ing نادر، ومركّب من كلمتين شائعتين) مصطلحات لا أشخاصًا. تجربة 30 وحدة على Gemma المحلي: إنذارات الأسماء 33 ← 0 | Accepted 2026-10-25 (تنفيذ بتكليف؛ التفعيل في تشغيل جديد) |

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

## D-039: استخراج المصطلحات v3 — مستويات المدخل، وفحص التصريف، وتنظيف الضوضاء، ومصنّف termhood (2026-10-25)

**السياق:** 1959 قرارًا مؤقتًا على قاموسي الكتابين (حكم آلي، D-031، لا بشري) رفضت 757 مرشحًا: 560 «مفردات عامة» و197 ضوضاء. النظام لا يعرف إلا قفلًا صارمًا أو رفضًا، والفحص الصارم أنذر كذبًا على صيغ مصرَّفة («جماعة اجتماعية» مقابل «الجماعات الاجتماعية»). الخطة: docs/proposals/2026-10-25-term-extraction-v3.md.

**القرار:**
- مستويات المدخل: `locked` (يُفحص بصرامة؛ كل اعتماد قبل v3)، و`hint` (ترجمة مفضّلة في الـ prompt، وغيابها إشارة minor فقط)، و`dropped` (= مرفوض). العقد `TermbaseEntry.tier` (SCHEMA 1.15.0). الحقول الجديدة تُحذف من القطعة وهي فارغة، و`approved_digest` لا يضيف `hints` إلا إن وُجد hint: بصمة القاموسين المجمَّدين لم تتغير (تحقق بقراءة فقط: نفس البايتات ونفس البصمة المسجلة في `translation_unit_results`).
- `terminology` للمقفول يطابق التصريف (ال، و ف ب ل ك، الجمع السالم والمثنى، التاء المفتوحة قبل الضمير، تطابق النسبة، ألف تنوين النصب). `forbidden_variant` يبقى صارمًا. الجمع المكسَّر خارج النطاق (يحتاج معجمًا).
- إصلاحات حتمية في الاستخراج (`termbase-extract-v3`) قيست على القرارات المؤقتة: يختفي 91 مرفوضًا ويضيع 3 مقبولة (0.25%، الحد 1%).
- مصنّف termhood (`EngineProfile.classify_termhood`، معطّل افتراضيًا، محلي فقط في الإنتاج). اقتراحه لا يعتمد ولا يرفض شيئًا؛ «dropped» يعفي المرشح من المقترح ولا يحجب البوابة.

**النتائج:**
- لا يتغير شيء في الترجمة الجارية؛ التغيير للكتب القادمة أو إعادة تشغيل مقصودة.
- المصنّف حقق إحساسًا 0.989 (178/180) على عيّنة 300 بنموذج `deepseek-mqm` (D-029)، لكن الحد الأدنى لفترة الثقة 95% ≈ 0.960 والذهب آلي؛ يبقى معطّلًا حتى قياس على حكم بشري أو عيّنة أكبر، وبالنموذج المحلي الذي سيعمل به.
- `docker/translation-profiles.json` لم يُعدَّل: الملف مركّب في العامل الجاري الذي لا يعرف الحقل الجديد.

## D-040: مرحلة حواشي المترجم — العجز الشفاف بدل الحجب (2026-10-25)

**السياق:** D-033 ألغى المراجعة البشرية من المنتج، والمتعلم قد يكون كفيفًا لا يستطيع المراجعة. لكن مسار الترجمة (D-028) ما زال ينتهي بحالتي `needs_review` و`rejected` اللتين كانتا تعنيان «ينتظر إنسانًا»، وبوابة التجميعة `translate-book.unit-decisions` ترفض الكتاب كله بوحدة واحدة منهما. في تشغيل الكتاب 1 الجاري (book-main) كانت 139 وحدة من 1825 وحدة محسومة (7.6%) في هذه الحالة عند القياس. الاقتراح: docs/proposals/2026-10-25-learning-companion.md §4 و§5 (الدرجة 5).

**القرار:**
- عقد جديد (SCHEMA 1.16.0، docs/02-CONTRACTS.md §14): `TranslatorNote` و`TranslatorNotesDocument`. الملاحظة مثبتة في موضع الوحدة، وصوتها `course` إلزامًا، وتبدأ بعبارة «ملاحظة من الكورس:»، ولا يقبل العقد أي حرف لاتيني في ما يسمعه المتعلم. الوثيقة تحاسب على كل وحدة: مقبولة + مُسقطة + لها ملاحظة + معلّقة = المجموع.
- دالة نقية في طبقة الترجمة (`b2c_translation.notes.build_translator_notes`) تختار أفضل ترجمة بين النهائية والمسودات. لا تُعرض أبدًا ترجمة فيها مشكلة حرجة أو فارغة. وتشتق السبب من فئات المشاكل الحاجبة في الترجمة المعروضة، بترتيب ثابت: تعذّر الترجمة، ثم المعنى، ثم النقص أو الزيادة، ثم كلام غير مترجم، ثم الأرقام، ثم الأسماء، ثم المصطلحات، ثم الصياغة، ثم «تعذّر التحقق».
- نشاط `build_translator_notes` بعد `assemble_translation` في `TranslateBookWorkflow` و`AssembleBookTranslationWorkflow`، خلف `workflow.patched("translator-notes-v1")`، فالتواريخ المسجلة قبله تُعاد كما هي. يُخزَّن المخرج في المرحلة `translator-notes`. والبوابة `translator-notes.coverage` حتمية بقرار `system`، تُعيد الحساب من `translation_unit_results` لا من الوثيقة. شروطها: 0 وحدة غير مقبولة بلا ملاحظة، و0 وحدة بلا نتيجة، و0 ملاحظة على وحدة مقبولة.

**النتائج:**
- لا تضيع وحدة بصمت. والمرحلة الجديدة تُقبل لكتاب فيه وحدات مشكوك فيها، بينما التجميعة الصارمة تبقى مرفوضة كما كانت.
- **لم يتغير بعد:** مرحلة الكلام ما زالت تشترط تجميعة `translate-book` مقبولة ووحدات مقبولة فقط. ربطها بالحواشي (نطق `announcement_ar` ثم الترجمة التقريبية بصوت الكورس، وعدم قراءة الأصل الإنجليزي) قرار تالٍ، **Open**. وكذلك سقف كثافة الحواشي، ونقل الزائد إلى المسرد، والتحقق الآلي من صدق الحاشية بمحكّم مستقل (§4 من الاقتراح).
- القياس على الكتاب 1 كشف أن «أفضل ترجمة متاحة» قد تكون هلوسة كاملة. مثال ذلك عنوان «NOT ES» الذي لم تكشفه إلا علامة نفي، لأن فحص الطول لا يعمل على المصادر القصيرة. فالحاشية تقول الصدق («قد لا تنقل معنى الأصل»)، لكن عرض هذا النص ضار. الحل في سلّم التصعيد (§5) قبل الحاشية، لا في الحاشية نفسها.
- لا تغيير في العامل الجاري ولا في قاعدة البيانات الحية. القياس تم بقراءة فقط (SELECT في معاملة read-only، وGET للـ artifact)، وبُنيت الملاحظات خارج الحاوية بكود هذا الفرع.

## D-041: الحواشي تصل إلى الصوت والحلقة — مسار قبول بديل صريح (2026-10-25)

**السياق:** بعد D-040 صارت لكل وحدة غير مقبولة حاشية، لكن الكلام ما زال يشترط تجميعة `translate-book` مقبولة بالبوابة الصارمة (0 `needs_review`)، والحلقة تُسقط بوابة `episodes.translation` بأي وحدة كتاب غير مقبولة. فالكتاب يبقى خارج الصوت (الحلقتان 01 و02 من SAT توقفتا لهذا). وكشف القياس أن «أفضل ترجمة» قد تكون مختلقة: «NOT ES» (عنوان «NOTES» مكسور) عاد فقرة لا علاقة لها بالأصل، ثلاث مرات في الكتاب 1.

**القرار:**
- **الكلام:** `GenerateSpeechV2Input.notes_ref` مسار قبول **بديل وصريح**. شرطه: artifact ‏`translator-notes` بحالة `accepted` (بوابة `translator-notes.coverage`: كل وحدة مقبولة أو مُسقطة أو لها حاشية، و0 بلا نتيجة)، وللتشغيل والوحدات نفسها، و`results_digest` يساوي نتائج الوحدات الآن. عندها لا تُشترط التجميعة الصارمة. وبلا `notes_ref` يبقى المسار القديم بشروطه ومفاتيح artifacts نفسها. ويُسجَّل المسار في config ‏(`acceptance_path`)، والحواشي ضمن inputs السجل.
- **النطق:** الوحدة ذات الحاشية تُنطق بصوت الكورس (`SpeechSegment.voice = course`): الإعلان ثم الترجمة التقريبية إن عُرضت. الأصل الإنجليزي لا يُقرأ. وبلا ترجمة صالحة يُنطق الإعلان فقط.
- **الحلقة:** سطر نص الكتاب لوحدة غير مقبولة هو حاشيتها (`ScriptLine.note_id`)، تُبنى بالدالة نفسها (`note_for_unit`). والبوابة تعدّها مغطاة، والوحدة بلا نتيجة ما زالت تُسقطها.
- **الحجب:** لا تُعرض ترجمة تقريبية (`TranslatorNote.withheld`) في ثلاث حالات: مشكلة `meaning_error`/`polarity_error` حرجة، أو أصل بلا كلمة من ثلاثة أحرف، أو كل المرشحين نصوص أطول من 3× الأصل + 40 حرفًا لأصل ≤ 40 حرفًا. يُعرض الإعلان، والأصل في النص المكتوب فقط.
- العقد 1.18.0 (docs/02 §16).

**النتائج:**
- على لقطة الكتاب 1 (1825 وحدة محسومة): المسار الصارم يرفض. ومسار الحواشي ينطق 1371 وحدة: 1296 مقبولة و75 حاشية، منها 72 بترجمة تقريبية و3 بالإعلان فقط (الثلاث «NOT ES» المحجوبة).
- **ما زال مفتوحًا:**
  - تمييز صوت الكورس فعليًا في TTS (صوت أو نغمة)؛ الحقل موجود ولم يُربط بالمحرك.
  - سقف كثافة الحواشي.
  - تمرير المسودات لحلقات `run_episodes.py`، فلقطتها لا تحمل `drafts_json`.
  - ربط واجهة الـ API / العامل بتمرير `notes_ref`.

## D-042: مخرجات الصوت النهائية للمتعلم — MP3 وID3v2.3 وقص الصمت وحد المسار بالمدة والنسخ إلى الكرت (2026-10-25)

**السياق:** D-038 حدد تسمية الحلقات لمشغلات MP3 الرخيصة، لكن لم يكن هناك ترميز MP3 في مسار speech v2، ولا وسوم، ولا أداة نسخ. وتقرير قراءة الكلام (eval/reports/speech-readability-2026-10-25.md) ترك بندين مفتوحين: صمت حواف مقاطع XTTS يضاف إلى الوقفة المخططة (البند 3)، وحد 400 مقطع صار يقصّر المسار إلى نحو 43 دقيقة بعد التقطيع إلى عبارات (البند 5).

**القرار:**
- **الترميز:** ‏`b2c_speech.audio.mp3` عبر `lameenc==1.8.4` (LAME مضمّن، فلا حاجة لـ ffmpeg في صورة العامل). أحادي، و**MPEG-1 فقط** (32/44.1/48 kHz)، لأن 22.05/24 kHz هي MPEG-2 LSF ولا تفكها كل المشغلات القديمة؛ فيُعاد أخذ العينات من 24 إلى 44.1 kHz على كتل محدودة. CBR ‏96kbps افتراضيًا: أدنى المدى الموصى به للأحادي عند Apple ‏(96–128)، و64 مسموح (داخل مدى Apple لخلاصات RSS، 64–128). المصدر: podcasters.apple.com/support/audio-requirements (قُرئ 2026-10-25). الـ CBR يُثبت بفحص رأس كل frame (`mp3_frame_probe`)، لأن lameenc لا يكتب frame ‏Info/Xing.
- **الجهارة:** تطبيع قبل الترميز (Apple: «preconditioning … before the encoding process») إلى `app-speech-16`: ‏‎-16 LUFS ±1 وtrue peak ≤ ‎-1 dBTP، مع هامش 0.5 dB إضافي لتجاوز المرمّز. الحلقة التي لا تبلغ الهدف تُرفض (`EpisodeLoudnessError`) ما لم يُطلب `strict=False`. ملاحظة: AES TD1008 يجعل ‎-18 LUFS مرجع الكلام، وبعض الأدلة يقترح ‎-19 LUFS للملف الأحادي. الاختيار النهائي بقي قرار المالك المعلّق Q2.
- **الوسوم:** ‏ID3v2.3 بـ `mutagen==1.48.1`، وكل إطار نصي بترميز ‎$01 مع BOM (id3.org/id3v2.3.0 §3.3). سبب عدم استعمال 2.4: Philips تقول «ID3 version 2 tags with unsynchronization scheme or UTF8 encoding will not be supported»، وتقارير المستخدمين تؤكد أن كثيرًا من المشغلات لا يقرأ 2.4. الإطارات: TIT2 «NN - العنوان»، وTALB الكتاب، وTRCK «k/total» (يبدأ من 1)، وTPE1 اختياري، وAPIC (غلاف أمامي) اختياري.
- **قص الصمت:** ‏`b2c_speech.audio.trim`. يُقص كل مقطع إلى حارس 40ms من حافته الأصلية، ويُحسب على بوابة ‎-50 dBFS نفسها التي يستعملها فحص المقطع، مع fade طوله 5ms عند القطع. ويُدرج المجمّع صمتًا رقميًا مقداره `pause_after_ms − الحارسين`، فيصير الصمت المسموع بين مقطعين هو المخطط. توقيتات `segments.json` تسجّل المقصوص والمحفوظ والمدرج. ‏`SPEECH_TRACK_VERSION` = 2.
- **حد المسار:** صار `max_segments_per_track` اختياريًا (None افتراضيًا)، و`PLAN_VERSION` = `speech-plan-2`. القيمة الافتراضية لـ `max_track_seconds` ‏1800 ث، وهي مبنية على قياس الذاكرة لا على الذوق:
  - ذروة RSS لتجميع مسار: 1.16 GiB لـ30 دقيقة، و1.62 GiB لـ45، و2.11 GiB لـ60. هذا بعد جعل BS.1770 وtrue peak وكتابة WAV تعمل على كتل.
  - حد عامل الصوت `mem_limit: 2g`.
  - لا يُرفع الحد إلا مع رفع الذاكرة.
- **النسخ إلى الكرت:** ‏`scripts/copy-course-to-card.py` ينسخ ملفًا ملفًا بترتيب رقم الحلقة، ومع fsync للملف وللمجلد، ثم يقرأ المجلد ويتحقق من ترتيب المدخلات.
  - يرفض مجلدًا فيه ملفات mp3 سابقة، ويرفض أسماء بلا رقم.
  - يحذّر إن لم يكن نظام الملفات vfat أو exfat، ولا يدّعي التحقق من الترتيب حينها.
  - فيه خيار `--ascii-only`، وهدف `--image` لصورة FAT عبر mtools، بلا تركيب ولا صلاحيات.

**النتائج:**
- لا تغيير في `b2c_contracts`، ولا في SCHEMA_VERSION.
- الصور لم يُعد بناؤها: العامل الجاري لا يحمل lameenc وmutagen، والكود الجديد لا يُستورد في مسار نشاط قائم إلا `trim` (numpy فقط).
- ترميز حلقة كاملة من عدة مسارات لم يُوصل بعد بـ workflow. ‏`encode_episode` يأخذ PCM واحدًا، وترميز حلقة ساعة قيس بذروة 2.75 GiB (منها ≈ 0.8 GiB مقاطع المصدر الباقية)، فيلزم تجميع متدفق قبل ربطه بعامل الصوت.
---
## D-043: بوابة الاستخراج v2 باتفاق محلّلَين مستقلَّين، وفصل الكلمات الملتصقة، ومفتاح تفعيل معطّل (2026-10-25)

**السياق:** قارنت بوابة R3 النص المستخرج بمرجع واحد هو طبقة النص كما يقرؤها pypdf، بعتبة استدعاء ≥ 0.98 لكل صفحة، وبحد 2000 حرف للـ block. لكن pypdf نفسه يلصق الكلمات عند فجوات الـ kerning: في SAT وُجد 1,700 token ملتصق (1.4%) لا أثر لها في pdfium. فرسبت قراءة Docling الصحيحة لأن مرجعها معيب (139 صفحة < 0.98). وحد الـ 2000 حرف يُسقط فقرات أكاديمية حقيقية (Gramsci: ‏31 فقرة، وp90 ‏1,667 حرفًا). الأرقام كلها في eval/reports/ingest-v2-gate.md.

**القرار:**
- `text-layer.json` ‏(`pdf-text-layer/2`) يحمل استخراجين مستقلين لكل صفحة: pypdf ‏(`text`) وpdfium ‏(`text_pdfium`، ‏pypdfium2 5.13.0 المثبت في الـ worker)، ومعهما صناديق الصور النقطية. أما Docling فيقرأ عبر docling-parse، وهو محرك ثالث.
- **اتفاق الصفحة:** F1 الكلمات (≥ 3 أحرف) بين نص الصفحة المستخرج وكل استخراج، وتُحتسب أعلى القيمتين. كلمات الـ block العابر للصفحات تُوزَّع بقطع يحفظ الترتيب ويطابق كل صفحة. نص الصور (داخل صندوق صورة، أو OCR، أو figure) يُحسب للاستدعاء ولا يُحسب «اختلاقًا».
- **العتبة 0.95:** على Gramsci، حيث الاستخراجان سليمان، اتفق pypdf وpdfium بـ ≥ 0.95 في 408 من 409 صفحات (الوسيط 1.0، والمئين الأول 0.978). الاستثناء الوحيد افتتاحية فصل بحروف متباعدة. ما دون 0.95 خارج ضجيج الاستخراج.
- **«مسافات فقط»:** صفحة تتفق حروفها (shingles من 6 أحرف ≥ 0.95) وتختلف حدود كلماتها، مثل حروف متباعدة في الاستخراجين أصلحها S2. هذه لا تُعد فشلًا، فللكلمات الملتصقة والمتباعدة فحوصها.
- **الفحوص الجديدة:** `independent_references ≥ 2` (لا قبول بمرجع واحد)، و`cross_parser_word_recall/precision ≥ 0.995`، و`pages_below_agreement = 0`. ثم `glued_token_rate ≤ 0.001` (عتبة التدقيق §5.4.5)، والحَكَم فيه pdfium. ثم `merged_paragraph_rate ≤ 0.01`: نهاية فقرة في طبقة النص، أي سطر قصير (< 80% من السطر المعتاد) ينتهي بجملة ويليه سطر كامل يبدأ بجملة، تقع داخل block واحد من المتن. العتبة 1% على قياس `cut_at_page_break_rate ≤ 1%`، أي الخطأ المعاكس.
- **أُزيلت فحوص:** `word_recall/word_precision` بمرجع واحد، و`pages_recall_below_0_98`، و`body_blocks_over_2000_chars`. وأُزيلت علامة `long_block` من S2 ‏(`NORMALIZER_VERSION=2`).
- **الحكم:** `GateResult(gate_id="ingest-v2.quality")`، و`gate_version` بصمة `GATE_DEFINITION` (تتغير بتغير أي فحص أو عتبة). خدمة الترقية تقبل `ingest-parse-v2` أو ترفضه. `IngestQualityReport.page_findings` (SCHEMA 1.16.0) يسمّي صفحات كل فحص للمراجعة.
- **فصل الكلمات الملتصقة** (`b2c_ingest.glued`، S2) حتمي. يقبل القطع بأحد ثلاثة أدلة:
  - bigram مطبوع في الكتاب نفسه (≥ 2 و≥ 3 أضعاف الكلمة الملتصقة)؛
  - أو معجم wordfreq 3.1.1: كلمة غائبة عنه، مع كلمة وظيفية أو كلمتين شائعتين طُبعتا منفصلتين مرة في الكتاب؛
  - أو شرطة em/en يطبعها pdfium بين الحروف نفسها.

  وهو محمي في الحالات التالية:
  - أسماء العلم: الكلمة مكتوبة بحرف كبير داخل جملة في أي موضع من الكتاب؛
  - المصطلحات الممرَّرة؛
  - CamelCase والأحرف الكبيرة كلها والروابط وأجزاء الكلمات المقسومة بشرطة؛
  - privative a- (لا يُفصل حرف واحد)، والبادئات واللواحق؛
  - تُختبر القراءة المفضلة وحدها، بلا تراجع إلى قراءة أطول.

**النتائج:**
- الحَكَم pdfium. على طبقة نص SAT ‏(pypdf): 1,009 قطعًا، منها 1,008 مؤكدة وواحد بحد خاطئ، و0 تقسيمات لكلمة صحيحة. على نص Gramsci السليم (146,438 token): 0 تقسيمات. ومراجعتي اليدوية: 80/80 صحيحة من SAT، و64/65 حدًا صحيحًا من Gramsci v2 مع 52 شرطة مسترجعة.
- النتيجة الحالية: SAT v2 يرسب في 5 فحوص، وGramsci v2 في فحصين (الأرقام في التقرير). البوابة الجديدة لا تُنجح كتابًا آليًا، بل تسمّي عيوبه الحقيقية: صفحات عناوين صور مفقودة، و64 فقرة مدموجة، وبقايا الحروف المتباعدة، والحواشي.
- `B2C_INGEST_V2_ENABLED=false` افتراضيًا، ومعرّف الرفع v1 لم يتغير. الترجمة الجارية على ParsedBook v1 لا تتأثر. التفعيل مؤجل حتى تُغلق قائمة الجاهزية (التقرير §5).

## D-046: فحص الأسماء v2 — المراجع تُنسخ ولا تُفحص، وتهجئة الاسم المنقول تُطبَّع بقواعد محدودة (2026-10-25)

**السياق:** فحص `named_entity` كان سبب نحو نصف حواشي المترجم في الكتاب 1: ‏70 من 139 سببها الأساسي «الأسماء»، منها 34 مرجعًا ببليوغرافيًا (eval/reports/translator-notes.md). صنّفتُ الإنذارات المخزنة كلها في الكتابين الجاريين بقراءة فقط: 481 إنذارًا في 180 وحدة. منها 157 خطأً حقيقيًا مستقلًا و47 تكرارًا لها، و255 إنذارًا كاذبًا تزيله القواعد أدناه، و22 كاذبًا يبقى ويحتاج تعديلًا في القاموس. التفاصيل في eval/reports/named-entity-check.md.

**القرار:**
- **وحدات `reference` و`index` (`RULE_VERBATIM_KINDS`):** نصها يُنسخ من الأصل بالقاعدة. فالمرجع يبقى بلغته الأصلية، ولا يتغير فيه إلا أدوات الربط، كما في D-028 المبدأ 2. لذلك لا يُطلب فيها مقابل عربي لاسم ولا لمصطلح: تتخطاها `named_entity` و`terminology` (ومعها `forbidden_variant`) و`terminology_hint`. المصدر: المركز العربي للأبحاث ودراسة السياسات، «دليل التحرير»، ص 46–51، قُرئ في 2026-10-25. أمثلته توثّق المرجع الأجنبي بلغته وأدواته (`Ibid., p.`، و`pp.`، و`ed.`). وص 51 يفصل المحتوى العربي عن الأجنبي في الهامش نفسه بسطر. وص 48: مصدر الجدول أو الشكل يوثَّق «بالطريقة نفسها المعتمدة في كتابة بيانات المراجع». أما ISO 690 فلم أتحقق من نصه (مدفوع)، وChicago (‏14.99 بحسب نقل ثانوي) يُبقي العنوان الأصلي ويضيف الترجمة بين معقوفين اختياريًا.
- **أدوات الربط في المراجع:** لم تتغير. ما زالت تصير عربية («تحرير»، و«ص»، و«المرجع نفسه»)، وهو اختيار D-028. والصيغة الصارمة للمركز العربي (أدوات أجنبية أيضًا) متاحة بـ `translate_connectives=False`. تغيير الافتراضي قرار للمالك، وهو **Open**.
- **الحواشي ومصادر الأشكال (`CITATION_KINDS` = `footnote` و`caption`):** هذه تُقرأ ولا تُنطق. فإذا بقي الاسم فيها بتهجئته الأصلية حرفًا بحرف، كما في التوثيق، فلا إنذار. أما في النص المنطوق (`prose` و`heading` و`list_item`) وفي `toc`، فالاسم اللاتيني يبقى إنذارًا.
- **الاسم في المصدر:** يُطابق بحالة أحرفه، أو بالأحرف الكبيرة كلها كما في العناوين. فالكلمة العادية ليست اسم علم: «convention hall» ليست Stuart Hall، و«'prison notebooks'» ليست كتاب غرامشي.
- **الاسم داخل اسم أطول:** إذا ورد الاسم داخل اسم معتمد أطول يُفحص في الوحدة نفسها، يُبلَّغ عنه مرة واحدة، من خلال الاسم الأطول.
- **المطابقة العربية (`contains_name`):**
  - تعيد استعمال السوابق و«ال» والجذوع من مطابق المصطلحات (D-039).
  - تضيف تطبيعًا إملائيًا: الهمزة على الواو والياء والسطر، والحروف الفارسية (پ ڤ گ چ ڨ). ويبقى من `_ar_key` توحيد الألف، وة/ه، وى/ي، والتشكيل، والتطويل.
  - **حرف مد واحد** (ا و ي) يُكتب أو يُحذف داخل الكلمة، بثلاثة شروط: أن يبقى الحرفان الأول والأخير، وأن تبقى الحروف الصامتة نفسها بترتيبها، وأن يكون طول الاسم المعتمد 4 أحرف على الأقل.
  - الجيم مكان الغين لصوت g.
  - الحرف الأول من الاسم (initial) يُقبل بحرف عربي واحد، بشرط أن يقابل الحرف اللاتيني نفسه.
  - كل ما سوى ذلك يبقى إنذارًا: حرف زائد أو ناقص، أو حرفا مد، أو ترتيب معكوس لحروف المد («هيور» لـ«هوير»)، أو قراءة أخرى للاسم.
- **إصلاح خلل في D-039:** مجموعة ضمائر فحص النفي كانت تحمل اسم مجموعة لواحق المطابق نفسه، فتطغى عليها. فصار المطابق يقطع «ي» و«ه» و«ك» ضمائرَ، فيقرأ «بردويلي» كأنها «بردويل». سُمّيت مجموعة النفي باسم مستقل. القياس: لا يتغير أي إنذار مصطلحات مخزن في الكتابين، وكل اختبارات D-039 تنجح.

**النتائج:**
- **على المسودات المخزنة:** لا يُضاف أي إنذار، ويختفي 302 من 481 إنذار أسماء (278 من 430 في الكتاب 1، و24 من 51 في الكتاب 2)، و41 إنذار مصطلحات على المراجع. وكل إنذار صُنّف حقيقيًا ما زال يُكشف، وعلى أمثلته الحقيقية اختبارات في `tests/unit/translation/qa/test_named_entity.py`.
- **حواشي الكتاب 1 الـ 139** (اللقطة نفسها، 1825 وحدة):
  - 37 تختفي يقينًا: مراجع تقبلها السياسة بالقاعدة.
  - 23 أخرى تصير طبقتها الحتمية نظيفة، فتذهب إلى المحكّم الدلالي. نسبة نجاحه في هذا التشغيل 92.2%.
  - التقدير ≈ 58، والحد الأعلى 60.
  - 8 يسقط منها سبب «الأسماء» وتبقى بسبب آخر، و71 لا تتغير.
- **الكتاب 2 (13 حاشية حتى الآن):** 5 تذهب إلى المحكّم الدلالي، والتقدير ≈ 4.
- **لا تغيير حي.** العامل الجاري يحمل الكود القديم، والتغيير يسري على تشغيل جديد أو إعادة تشغيل مقصودة. ولا تغيير في `b2c_contracts`، فلم يُرفع SCHEMA_VERSION.
- **ما زال مفتوحًا:**
  - جدول الأسماء المعتمد **لا يُرسل إلى المحرك**. ‏`EngineRequest.terms` فيه المصطلحات المقفولة فقط، فالمحرك يخمّن التهجئة ويصححها الإصلاح لاحقًا. إرساله يقلل الإنذارات الحقيقية من جذرها، لكنه يغيّر الـ prompt، فيحتاج قياسًا.
  - البدائل المقبولة التي لا تغطيها القواعد تحتاج حقل بدائل في القاموس (تغيير عقد). أمثلتها «جرونينجن» و«كاترين» و«كاهنمان» و«دار نشر جامعة كاليفورنيا»، و«شيرمان» الزائدة في «مدرسة شيرمان كنت».
  - مصطلحات مسجلة أسماءً، مثل Groupthink وSatisficing. والجمع المكسَّر خارج النطاق كما في D-039.


## D-048: المراجع الأجنبية تُنسخ كما هي — أسلوب المركز العربي (2026-10-25)

- **القرار (المالك):** وحدة `reference` تُنسخ حرفيًا، بما في ذلك أدوات الربط (Ibid., p., op. cit., ed.). هذا يلغي جزء D-028 الذي حوّل أدوات الربط إلى «المرجع نفسه، ص». جدول الصيغ العربية باقٍ للمراجع المكتوبة بالعربية (`translate_connectives=True`).
- **المصدر:** المركز العربي للأبحاث ودراسة السياسات، «دليل التحرير»، ص 46–51: المرجع الأجنبي يبقى بلغته.
- **التنفيذ:** `render_reference_unit(..., translate_connectives=False)` افتراضيًا، والاختباران معدّلان ليثبتا ذلك.
- **أثره على التشغيلين الجاريين:** وحدات المراجع التي تُرجمت قبل نشر العامل الجديد تحمل الصيغ العربية. وهي قاعدية بلا نموذج، فتُعاد بالقاعدة عند النشر (إعادة عرض حتمية) حتى يتسق الكتاب.
## D-045: إصلاحات S2 التي سمّتها بوابة D-043 — الكتابان يجتازان `ingest-v2.quality` بعتباتها (2026-10-25)

**السياق:** سمّت بوابة D-043 عيوبًا حقيقية في ParsedBook v2: ‏64 نهاية فقرة داخل blocks في SAT ‏(11.4%)، وعناوين أشكال مفقودة أو في صفحة خاطئة، وربط حواشٍ 0.899 في SAT، وحاشية بلا رقم في كل كتاب، وبقايا حروف متباعدة (5 و3)، و9 كلمات مفصولة بفراغ صوابها واصلة. الأرقام والمراجعة اليدوية في eval/reports/ingest-v2-fixes.md.

**القرار (S2، `NORMALIZER_VERSION=3`، `SPLITTER_VERSION=2`):**
- **نهايات الفقرات:** يُقطع عنصر Docling عند نهاية فقرة تُظهرها هندسة أسطر طبقة النص في صفحته (قاعدة فحص البوابة نفسها: سطر قصير يُغلق جملة يليه سطر كامل يفتح جملة). يشترط القطع وجود الكلمات نفسها على جانبيه في العنصر، ونهاية جملة قبله، وحرفًا كبيرًا بعده. القطعتان تحملان صندوق العنصر نفسه، والعلامة `split_at_paragraph_end`. ولأن S2 والبوابة يستعملان القاعدة نفسها، فدليل صحة القطع هو المراجعة اليدوية لا رقم البوابة وحده.
- **عناوين الأشكال:** (1) عنوان الصورة (`captions` وأبناء الصورة بوسم caption) يصير block من نوع `caption`، وكان يُسقط مع نص الصورة الداخلي (20 في SAT). (2) `section_header` يطابق `Figure|Table|… n.n عنوان` يصير `caption` (`caption_from_heading`، ‏36 في SAT). (3) bookmark وجهته `/XYZ` تحت آخر نص في صفحته يطابق العنوان في أول عنصرين من الصفحة التالية (`outline_destination_on_previous_page`)، بدل عنوان مُدرج في الصفحة الخاطئة يكرر العنوان الحقيقي (23 من 110 في SAT).
- **نص OCR من صورة نقطية:** عناصر صندوقها داخل صور pdfium النقطية (≥ 50%)، ولا تطبع طبقة النص أغلب كلماتها (< 50%)، في صفحة لها طبقة نص، تُجمع في block ‏`figure` (`image_ocr_text`). والجملة في أول قطعة كهذه تبقى نصًا إذا طبعت طبقة النص ≥ 80% من كلماتها. الصفحة الممسوحة (بلا طبقة نص، أو صورة تغطي ≥ 90% منها) لا تُمس.
- **الحواشي:** (1) سلسلة endnotes جديدة تبدأ عدّها من رقمها الأول. كانت تقارن بأعلى رقم في السلسلة السابقة، فتتفتت سلاسل الفصول [1] [2] [3..] ولا ترتبط بها علامات المتن. (2) أرقام سلسلة ابتلعتها الحاشية السابقة تُستخرج بالعد التنازلي من رقم الحاشية التالية. (3) حاشية Docling داخل جدول أو صورة تصير `caption`. (4) قائمة الاختصارات داخل قسم Notes قسم قائم، والأقسام مكدّسة، فيعود قسم Notes بعدها.
- **الحروف المتباعدة:** سطر pypdf الذي يفصل الحروف بفراغ والكلمات بفراغين يعطي حدود الكلمات للعنوان المطابق له حرفًا بحرف.
- **الواصلة:** قطع الكلمات الملتصقة يضع واصلة بدل الفراغ إذا طبع الكتاب الصيغة الموصولة نفسها مرتين على الأقل (`HYPHEN_MIN=2`). ولا يُشترط أن تكون الصيغة الأكثر، فالالتصاق أصلًا من واصلة نهاية سطر أسقطها Docling.

**تصحيح قياس في البوابة (`REPORT_VERSION=3`، لا تغيير في أي عتبة):** كان `outline_match_rate` يعدّ bookmark مطابقًا إذا وُجد عنوان بمفتاحه في صفحة الـ bookmark، حتى لو كان عنوانًا أدرجه S2 هناك وهو غير مطبوع فيها. وكان يعدّ العنوان الحقيقي في الصفحة التالية غير مطابق. صار يقبل أيضًا عنوانًا في الصفحة التالية **بشرط** أن تطبعه طبقة النص في أول 3 أسطر من تلك الصفحة (`outline_matched_on_next_page`). بالقاعدة القديمة يسجل SAT المصحح 0.791، وبالجديدة 1.0. العتبة 0.95 لم تتغير. `GATE_DEFINITION` يحمل القاعدة، فتغيرت `gate_version`.

**النتائج:**
- SAT v2: الفحوص الفاشلة 5 → 0. ‏`merged_paragraph_rate` ‏0.114 → 0.0018، و`pages_below_agreement` ‏5 → 0، و`note_link_rate` ‏0.899 → 0.966، و`letter_spacing_residue` ‏5 → 0، و`notes_without_label` ‏1 → 0.
- Gramsci v2: الفحوص الفاشلة 2 → 0.
- لا عقد تغيّر، فـ SCHEMA باقٍ 1.19.0. المفتاح `B2C_INGEST_V2_ENABLED` باقٍ معطّلًا. بقية موانع التفعيل في eval/reports/ingest-v2-gate.md §5 (البنود 2–9) قائمة.
---
## D-044: الأشكال والجداول تُقرأ في الصوت بوصفها، وترميز الحلقة MP3 متدفق وموصول بعد الصوت (2026-10-25)

**السياق:**
- كان الكلام v2 يستبعد وحدات `caption` و`table_cell` بنوعها، ولا يقرأ من الشكل شيئًا. وكان قسم `figure_description` في الحلقة يحمل وصف الكتاب المطبوع فقط (D-036 (1))، وهو موجود في كتاب واحد من اثنين.
- قرار المالك (2026-10-25): الشكل والجدول **يُقرآن بوصفهما** حتى يفهمهما مستمع قد يكون كفيفًا. لا يكفي نطق العنوان.
- `encode_episode` (D-042) يحمل الحلقة كلها في الذاكرة، وقيست ذروته 2.75 GiB لساعة. هذا فوق حد عامل الصوت (2 GiB)، ولم يكن موصولًا بـ workflow.

**القرار:**
- **وصف إنجليزي لكل شكل وجدول بصوت الكورس.** يُكتب بالإنجليزية أولًا ثم يُترجم (D-035).
  - **مصادره:** العنوان، ونص الشكل (labels/OCR)، والفقرات التي تشير إليه، ووصف الكتاب المطبوع إن طابق كلمات الشكل، والصورة إن كان النموذج يرى.
  - **الإرشادات:** إرشادات DIAGRAM Center/NCAM (السياق، والإيجاز، والموضوعية، ومن العام إلى الخاص، ووصف البيانات لا المظهر).
  - **الإعلان:** يُقرأ بعد وحدة الإشارة إليه، أو بعد آخر وحدة في صفحته. يبدأ بـ «وصف الشكل.» أو «وصف الجدول.»، وينتهي بـ «انتهى وصف الشكل.».
- **بوابة الإسناد (`figures.grounding`، حتمية):**
  - لا رقم (أرقامًا أو كلمات) ولا اسم في سطر من النموذج إلا وهو في الدليل.
  - إذا رسب الوصف يُعاد مرة واحدة مع تسمية ما رسب فيه.
  - إذا رسب ثانية، أو فشل النموذج، أو أجاب بغير الإنجليزية: يُرجع إلى العنوان، ووصف الكتاب المطبوع حرفيًا إن وُجد (≤ 150 كلمة). لا يُخترع شيء.
- **الجداول:**
  - الجدول الصغير يُقرأ صفًا صفًا بأسماء الأعمدة، حتميًا بلا نموذج. الحد: ≤ 8 صفوف، و≤ 4 أعمدة، و≤ 24 خلية، و≤ 160 كلمة، أي نحو دقيقة سماع.
  - العمود الفارغ في كل الصفوف (استمارة) يُذكر مرة واحدة.
  - الجدول الكبير: يُذكر حجمه وأعمدته، ثم ملخص من النموذج تحت البوابة نفسها.
- **الترجمة:** ‏`UnitTranslator` بالقاموس المعتمد ومحرك التشغيل وQE (stage `figure-descriptions-ar`، بوابة `figures.translation`). الكلام يقرأ الوصف الذي كل أسطره مقبولة، وغيره يُسجَّل `figure_not_accepted` ولا يُقرأ.
- **الحلقة:** قسم `figure_reading` بصوت الكورس يقسم نص الكتاب بعد وحدة الإشارة. أسطره تُترجم مع أسطر الحلقة.
- **هذا القرار يغيّر قرار الاستبعاد السابق:**
  - وحدات `caption` و`table_cell` نفسها ما زالت لا تُنطق بنوعها، والذي يُقرأ هو الوصف.
  - بند «أوصاف الأشكال ما زالت مستبعدة» في `docs/proposals/2026-10-25-speech-readability.md` صار متجاوزًا.
- **الترميز المتدفق:** ‏`b2c_speech.audio.stream.encode_episode_stream` بتمريرتين على كتل:
  - التمريرة الأولى تقيس: BS.1770 وtrue peak، ثم كسب واحد كما في `normalize_loudness`.
  - التمريرة الثانية: الكسب، ثم قياس ما يُرمَّز، ثم resampling، ثم LAME إلى ملف، ثم ID3 مع hash.
- **الربط بعد الصوت:**
  - النشاط `speech_v2_encode_episode` في `EncodeEpisodeMp3Workflow` على طابور الصوت.
  - مدخله مسارات `speech-track` **مقبولة** بالترتيب، ومخرجه artifact ‏`episode-mp3` ببوابتي `speech.episode-loudness` (‎-16 LUFS ±1، وtrue peak ≤ ‎-1 dBTP، الافتراضي الحالي للمالك) و`speech.episode-format` (MPEG-1 Layer III أحادي CBR، وID3v2.3).
  - التسمية 00/01/02 كما في D-038.
  - لا يبدأ تلقائيًا، ولا يتسلسل من `GenerateSpeechV2Workflow`.
- العقد 1.20.0 (docs/02 §17).

**النتائج (التقرير: eval/reports/figure-descriptions.md):**
- **SAT:** ‏90 شكلًا وجدولًا في المتن (78 شكلًا و12 جدولًا)، وتُخطّي 17 (10 مقدمة و7 زخرفة).
  - توزيع القراءة: 66 وصفًا، و3 ملخصات جداول، وجدول واحد صفًا صفًا، و20 رجوعًا إلى العنوان.
  - في الوثيقة المنشورة 0 رقم أو اسم غير مسند (بوابة ناجحة).
  - البوابة أوقفت أسماء وأرقامًا من معرفة النموذج العامة، منها أرقام رحلات 11 سبتمبر وأسماء خاطفين، وأعدادًا عدّها النموذج بنفسه.
- **الذاكرة:** ذروة ترميز حلقة ساعة:
  - 202 MiB بكتل 10 ث، و148 MiB بكتل 2 ث؛
  - مقابل 2135 MiB للترميز في الذاكرة على المسارات نفسها (و2.75 GiB في D-042 مع مقاطع المصدر)؛
  - بالحجم نفسه بالبايت، وبالكسب نفسه.
- **الصورة:** Gemma 4 المحلي يقبل الصور (`capabilities: vision`، وprojector ‏gemma4v). جُرّب على 3 أشكال بـ 5 طلبات فقط.

**ما زال مفتوحًا:**
- 20 من 90 شكلًا بلا وصف (العنوان فقط). سبب ذلك عناوين صور مفقودة في الاستخراج، ونص OCR مشوّش.
- لم يُنفذ منع تكرار وصف الكتاب المطبوع، الذي يُقرأ في نهاية الفصل (D-036 (1)) بعد أن قُرئ في موضعه.
- أنشطة الأشكال لم تُسجَّل في عامل، فهي تعمل بسكربت كالحلقات.
- لم تُعَد بناء الصور.
- لم يُستمع إلى صوت حقيقي.
## D-047: الأسماء المعتمدة تصل إلى المحرك، وتهجئات مقبولة في القاموس، والمصطلحات المسجلة أسماءً تصير مصطلحات (2026-10-25)

**السياق:** بعد D-046 بقي 157 إنذار أسماء حقيقيًا في الكتابين. أغلبها لأن جدول الأسماء المعتمد كان يُفحص بعد الترجمة، لكنه لا يُرسل إلى المحرك: `EngineRequest.terms` فيه المصطلحات المقفولة فقط، فالمحرك يخمّن التهجئة («هيور» بدل المعتمد «هوير»). وبقي 22 إنذارًا كاذبًا يحتاج بديلًا في القاموس. وكانت أربعة مصطلحات تُفحص فحص أسماء: Groupthink وSatisficing وCircleboarding™ وInconsistenciesFinder™. وصلت إلى القاموس بنوع `person` عبر قاعدة الاحتياط «اسم علم مفرد غير محسوم».

**القرار:**
- **الأسماء في الطلب:** `EngineRequest.entities` يحمل أسماء القاموس المعتمدة المقفولة التي تظهر في الوحدة، بمطابقة حالة الأحرف كما في الفحص (`names_in`، D-046).
  - `ChatEngine` يرسلها في قالب جديد مُصدَّر `chat.entities@v1` («Approved names … write it in Arabic script exactly as given»)، وبصمته مقفلة في `test_prompts.py`.
  - `PlainMTEngine` يضيفها إلى قالب Hy-MT2 الموثَّق للترجمات المرجعية.
  - الاسم المقفول مصطلحًا أيضًا يُرسل مرة واحدة. وإعادة المحاولة جملةً جملةً ترسل أسماء الجملة نفسها.
  - **بلا اسم في الوحدة لا يتغير الـ prompt ولا رقم نسخته**، فلا تتأثر ذاكرة التخزين المؤقت للوحدات بلا أسماء.
- **التهجئات المقبولة (SCHEMA 1.22.0، docs/02 §18):** حقل `TermbaseEntry.accepted_variants_ar`، ويقابله `TermRecord.accepted_variants`، وعمود المخزن `accepted_variants_json`، والحقل `accepted_variants` في `POST …/termbase/approve`.
  - تُقبل للأسماء المقفولة فقط، ولا تكون لاتينية، ولا تساوي الهدف، ولا تكون محظورة، وتُمسح عند الرفض.
  - فحص `named_entity` يقبل الهدف أو أي تهجئة مقبولة، بقواعد D-046 نفسها.
  - **لا تُحقن في الـ prompt أبدًا:** المحرك يرى الصيغة المعتمدة وحدها.
  - الحقل يُحذف من القطعة وهو فارغ، و`approved_digest` لا يضيف `entity_variants` إلا إن وُجدت، بأسلوب D-039. تحققتُ على القاموسين الجاريين: نفس البايتات، ونفس البصمة المسجلة في `translation_unit_results`.
- **قاعدة في الاستخراج (`termbase-extract-v3.1`):** حين يصل المستخرج إلى الاحتياط «اسم علم مفرد غير محسوم = شخص»، يبقى الاسم مصطلحًا (`term`، إشارة `coined`) في ثلاث حالات:
  - علامة ™ أو ®؛
  - اسم عملية بـ -ing نادر في الإنكليزية العامة (zipf < 3)؛
  - مركّب مغلق من كلمتين إنكليزيتين شائعتين (كل منهما ≥ 4 أحرف وzipf ≥ 4).
  - على القاموسين الجاريين: تغيّر نوع خمسة مدخلات معتمدة فقط، هي الأربعة المقصودة وPlayStation (منتج، كان `person`). وأضافت القاعدة ثلاثة مرشحين جددًا للمراجعة: Storyline وModerates وFiguresBack. ولم يتغير أي اسم شخص.
  - `merge_extraction` يحدّث عمود `kind` وحده ويبقي الاعتماد. فالانتقال يحدث عند إعادة بناء القاموس، لا بتعديل يدوي.

**النتائج:**
- **التجربة:** 30 وحدة من وحدات الإنذار الحقيقي في الكتاب 1. تُرجمت كل وحدة مرتين على Gemma المحلي بالتسلسل، بلا كتابة في المخزن ولا الذاكرة ولا ذاكرة التخزين المؤقت (`scripts/entity-prompt-trial.py`).

  | القالب | وحدات فيها إنذار أسماء | إنذارات الأسماء | وحدات فيها إنذار حاجب من أي نوع |
  |---|---|---|---|
  | القديم | 29 | 33 | 29 |
  | الجديد | **0** | **0** | 2 (مصطلح واحد، ولاتيني من الأصل في وحدة واحدة) |

  - لم تظهر أي وحدة إنذارَ أسماء جديدًا بالقالب الجديد.
  - متوسط زمن الوحدة: 37.0 ث بالقديم و38.2 ث بالجديد، والطابور مشغول بالكتابين.
  - التقرير: eval/reports/engine-entities.md.
- **لا تغيير حي:** العامل الجاري يحمل الكود القديم والقالب القديم. يسري التغيير على تشغيل جديد أو إعادة تشغيل مقصودة.
- **ما زال مفتوحًا:**
  - الاسم اللاتيني في وحدات بيانات الفهرسة المصنفة `prose`.
  - إدخال التهجئات الفعلية في القاموسين (F13: «جرونينجن» و«كاترين» و«كاهنمان») قرار مراجعة، لم يُنفذ.
  - المحكّم الدلالي لم يُشغَّل في التجربة.
