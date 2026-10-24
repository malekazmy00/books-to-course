# بحث مستقل لتحسين أداء Book2Course — 2026-10-08

الحالة: اقتراح قابل للتنفيذ، مبني على قراءة المصدر والوثائق ومراجع رسمية. لم تُجرَ تجربة تحميل أو استدعاءات نماذج أو قياسات جديدة في هذه المراجعة، ولم تُغيَّر خدمات أو إعدادات أو بيانات أو كود. التغيير الوحيد هو هذا التقرير. النطاق B2C فقط. لا يغيّر التقرير قرارات المالك السابقة بشأن إغلاق مراحل الإنشاء، ولا يفرض مراجعة بشرية أثناء الإنشاء؛ التقييم البشري مؤجل لتجريب المنتج.

## نطاق الأدلة وحدود الاستنتاج

قُرئت `AGENTS.md` و`docs/06-DELIVERY-PLAN.md` و`docs/PROJECT-STATUS.md` و`docs/05-PHASES.md`، وكذلك مقترح `docs/proposals/2026-10-08-hardening-plan.md`. المستودع `/root/projects/book2course`، وHEAD عند القراءة `a6da91a3ba69e1f5c1ec7fea60ad2c87903bd785`. ملفات العمل الحالية قد تتجاوز ذلك الالتزام؛ الأدلة أدناه تخص المصدر المقروء، ولا تثبت أن صورة العامل الحية تحتوي النسخة نفسها. أرقام الزمن التاريخية المنقولة من تقارير المشروع ليست قياسات جديدة.

المخطط الحالي للتحويل هو: رفع/OCR → مخطط معتمد → فهرسة → أسئلة grounded لكل scope → إثراء الدروس/MCQ/solver → artifact نهائي → ترجمة → صوت. النتيجة المهمة: المرحلة التي يراها المستخدم «أسئلة» تضم عدة مراحل توليد وتحقق، ويمكن أن تنتهي الأسئلة القصيرة وتبقى أنشطة الإثراء. كما أن عامل التوليد الخارجي يجمع الأسئلة والترجمة في slot واحد؛ تأخر ترجمة في الطابور لا يعني بطء نموذج الترجمة نفسه.

توجد فقرات تاريخية متعارضة داخل وثائق الحالة، منها بقاء ملخص أعلى الصفحة على Phase 2 وملاحظات أقدم بأن TTS غير موصول، بينما المصدر الحالي يحتوي renderer وخادم Fasih. تُستخدم الأدلة البرمجية والمقاطع المؤرخة لكل استنتاج، ولا تُستنتج حالة تشغيل حالية من ملخص قديم.

## عنق الزجاجة المثبت في المصدر

| المسار | دليل المصدر | الأثر والاستنتاج |
|---|---|---|
| عامل خارجي | `packages/orchestration/src/b2c_orchestration/worker.py:94–100`: `ThreadPoolExecutor(max_workers=1)`، وتسجيل QA والترجمة مع `max_concurrent_activities=1` | دورة أسئلة طويلة تمنع الترجمة من أخذ slot على هذا العامل. زيادة حد واحد لن تجعل حلقات المهمة الداخلية متوازية. |
| QA والتدقيق | `packages/pedagogy/src/b2c_pedagogy/qa_generation.py:474–548`: scopes ثم rounds ثم batches بالتتابع؛ `_generate_batch` ثم `_judge_batch`؛ `351–375` يستخدمان gateway نفسه | زمن الدفعة يشمل توليدًا ثم تحققًا توليديًا. اسم نموذج خارجي سريع وحده لا يلغي عدد الاستدعاءات المتسلسل. |
| إعادة المحاولة | `qa_generation.py:333–347`: خطأ عنصر واحد يرفع استثناء الدفعة؛ `526–547` يعيد جولات الباقي | أخطاء ID/الاقتباس قد تهدر العناصر الجيدة في الدفعة قبل مرحلة القبول. التحسين يجب أن يحافظ على الاختبارات لكل عنصر بدلاً من خفض الجودة. |
| الإثراء | `packages/orchestration/src/b2c_orchestration/qa.py:360–395` يؤخر الإثراء حتى QA final؛ `packages/pedagogy/src/b2c_pedagogy/lesson_enrichment.py:726,809,871,900,932` | بعد QA توجد دورة متسلسلة للدروس تشمل proposal وإصلاحات وتحقق دعم وsolver بالدرس وblind solver. العد البسيط للأسئلة لا يصف العمل المتبقي. |
| checkpoint واكتشاف التعطل | `qa.py:240` يرسل heartbeat، لكن workflow عند `487–489` يحدد 24/48 ساعة وثلاث محاولات دون `heartbeat_timeout`. `translation.py:195–199` كذلك دون heartbeat timeout | heartbeat الحالي ليس مؤقت كشف تعطل ما دام timeout معطلًا. المخرج المقبول محفوظ، لكن انهيار العامل قد لا يُكتشف سريعًا. لا يُقترح اختصار whole-book timeout عشوائيًا. |
| RAG كثيف | `packages/indexing/src/b2c_indexing/query_service.py:38,65–69` يحمل المقاطع ويعيد ترتيب كل المرشحين؛ `retrieval.py:41–45` يطلب passage embeddings؛ `postgres_store.py:25` يخزن embedding كـJSONB ولا يقدم بحث vector | embeddings تعتمد على cache داخل العملية؛ restart أو تجاوز cache يعيد الحساب. lexical يعيد بناء terms/DF لكل بحث عند `retrieval.py:18–20`. هذا مناسب لعينة صغيرة، ويتضخم مع الكتب المتعددة. |
| حد cache خطر عند التوسع | `packages/indexing/src/b2c_indexing/embedding_client.py:12,46–50`: cache 512، eviction أثناء تعبئة النتيجة ثم lookup لكل النصوص | إن طلب `embed_many` أكثر من 512 نصًا فريدًا قد يُحذف أولها قبل return ويحدث KeyError. هذا استنتاج مباشر من المسار، لم يُشغّل هنا لإثبات runtime. معالجة correctness أولاً قبل benchmark. |
| ترجمة | `packages/translation/src/b2c_translation/generation.py:68–70,122`: ثمانية segments ثابتة واستدعاء متسلسل؛ `131–135` يحفظ الأرقام | ثمانية مقاطع ليست حد tokens: قد تنتج دفعة طويلة أو صغيرة جدًا. لا يجب إزالة فحص الأرقام لتسريعها. checkpoint عند `translation.py:45–55` مرتبط بالمصدر والنموذج وإعدادات الإخراج. |
| OCR | `packages/ingest/src/b2c_ingest/docling_pdf.py:183–196` ينشئ converter لكل مستند مع OCR/table؛ `openrouter_ocr.py:84` و`surya_ocr.py:195` يتتابعان صفحة صفحة | يلزم فصل native-text/layout/OCR/table في القياس. OCR خارجي مجاني تحت حدود طلبات؛ سرعة مسار CPU المحلي لا تتحسن بمجرد إعطاء RAM أكثر. إعادة استخدام converter قد تقلل warmup بشرط ثبات الخيارات وسلامة التزامن. |
| TTS | `packages/orchestration/src/b2c_orchestration/audio.py:23–31` ينتظر MP3 كاملًا في الذاكرة؛ `docker/supertonic/audio_renderer.py:18–35` WAVs مؤقتة واستدعاء متسلسل لكل piece ثم FFmpeg stdout؛ `docker/fasih/server.py:111–117` XTTS chunks متسلسلة | يوجد cache لكل track، لكن تعطل renderer وسط track يعيد القطع المؤقتة. الصوت يُbuffer مرتين؛ يزداد أثر الذاكرة مع tracks طويلة. لا حاجة لتوازٍ عشوائي حول XTTS المشترك. |
| Android | `android/operator-app/app/src/main/java/com/book2course/operator/MainActivity.java:5,13,283–285,364–373`: io منفرد وwaitFor/poll/sleep داخله؛ req عند `90` read timeout 30 دقيقة | المتابعة أو أمر المكتبة قد ينتظر خلف العملية الطويلة في executor نفسه. إيقاف Activity يوقف التنسيق التالي على الهاتف ولو بقي workflow الحالي في السيرفر. هذا يمس الإحساس بالسرعة ومرونة التشغيل، وليس throughput النموذج وحده. |

## الفرص بالترتيب: الأثر والجهد والمخاطر

الأثر هنا تقدير هندسي إلى أن تقاس baseline، وليس وعدًا بنسبة تسريع. الجهد صغير/متوسط/كبير بالنسبة للبنية الحالية.

| الأولوية | الإجراء المقترح | الأثر المتوقع | الجهد | الخطر وضابطه |
|---|---|---|---|---|
| P0 | مقاييس لكل طلب/دفعة ووحدة محفوظة، وحالة queued/running/retrying/partial مع last-progress | تشخيص السبب وتجنب إعادة العمل؛ يميز انتظار queue من inference | صغير | حفظ metadata فقط، دون أسرار أو نصوص الكتب في logs |
| P0 | إصلاح صحة نتائج embed_many فوق 512 وفصل output buffer عن LRU؛ حفظ embeddings بإصدار/hash | يزيل خطر فشل ويمنع إعادة embedding عند restart | متوسط | لا خلط بين dimension/normalization/prompt/model revisions |
| P1 | جعل scopes/دروس أنشطة محدودة مستقلة، مع حفظ كل نتيجة قبل تجميع final؛ توسيع الخارجي 1→2→3 فقط في canary | مرشح أكبر لتقليل wall time وإتاحة translation مستقلة | متوسط/كبير | dedup وعداد usage وquota/cache mutable غير آمنة بمجرد إضافة threads؛ تجميع deterministic وميزانية موحدة ضروريان |
| P1 | إنقاذ المرشحين الصحيحين، وإصلاح العناصر المرفوضة فقط؛ مرجع quote_option_id بدل إعادة كتابة الاقتباس الطويل | يقلل regeneration والتوكنز المهدرة | متوسط | الكود يحل الاقتباس إلى المصدر exact ويخزن hash؛ لا يقبل citation حرة أو يلغي التحقق الدلالي |
| P1 | فصل نموذج توليد/نموذج تحقق، واختبار prompt/output budgets وتعطيل reasoning حيث ينجح | تقليل زمن التحقق وتوكنزه | متوسط | لا اعتماد شامل لنموذج قرار صغير قبل بيانات ممثلة؛ conditional converse والنفي/العربي حالات حرجة |
| P1 | checkpoints لترجمة/audio segments وسلوك تبديل نموذج واضح | يخفض تكلفة الأعطال وسط دفعة/track ويتيح أول مخرج أسرع | متوسط | المصدر/النص/اللغة/voice/المصطلحات/prompt جزء من identity؛ provenance لكل جزء |
| P2 | precompute retrieval features، ثم exact filtered vector search قبل تجربة HNSW | query time أكثر ثباتًا مع زيادة الكتب | متوسط | ANN قد يفقد نتائج مع filtering؛ benchmark عزل/recall قبل adoption |
| P2 | Docling warmed converter وقياس threaded batches، وتوجيه الجدول/الصفحة الصعبة فقط | تقليل warmup ومعالجة غير لازمة | متوسط | API الخيارات تعتمد الإصدار المثبت؛ لا تنزيل upgrade حي أو توازٍ غير محدود |
| P2 | خطة تنفيذ محفوظة على الخادم؛ Android repository/status executor مستقل ومتابعة lifecycle مع backoff | استجابة الواجهة واستمرار المراحل بعد غلق الهاتف | متوسط/كبير | POST idempotent، reconciliation للworkflow IDs، no duplicate dispatch |
| P3 | تجارب embedding/model/graph جديدة على corpus نفسه | تحسين جودة/سرعة محتمل، حسب المهمة | متوسط | لا تجعل تنزيل نموذج جديد أو graph كبير بديلًا لعلاج التسلسل والتخزين |

## التطبيق المقترح بحسب المسار

### orchestration، التزامن والاستعادة

احتفظ بـTemporal؛ لا يوجد دليل هنا يبرر استبداله. الأنسب تقسيم النشاط الطويل إلى وحدات bounded ذات هوية: `source_hash + outline_hash + scope_id + task/prompt/config_version`، وحفظ accepted payload/provenance/cost قبل commit. fan-out محدود للأسئلة المستقلة؛ group/course تأتي بعد children، والجمع/إزالة التكرار في ترتيب ثابت. لا تشغّل ThreadPool حول `all_questions` و`accepted_questions` المشتركة مباشرة. استخدم executor/gateway آمنًا أو clients مستقلة وحساب تكلفة/requests ذريًا، مع limiter على المزود/المفتاح يشمل كل workers.

heartbeat timeout يتجاوز أقصى زمن مسموح للطلب + الكتابة إلى storage + margin؛ نبضة بين مراحل request/checkpoint، أو مراقب مستقل إذا أُريد كشف crash أثناء طلب طويل. نبضة «العامل حي» تختلف عن «النتائج تتقدم»؛ watchdog يرى الاثنين ويعرض سبب wait. لا يعاد start-to-close لنشاط الكتاب الحالي؛ خطة rollout مستقبلية تتطلب queue جديدة/version routing أو انتهاء المهمة الحية وفق [S1/S2].

`ArtifactStore.put` في `packages/core/src/b2c_core/artifacts.py:105–119` يفحص exists ثم يكتب files ثم manifest. manifest-last مفيد، لكنه لا يمثل lock بين كاتبين لنفس key. قبل إضافة concurrency: writer lease/advisory lock أو conditional commit مع immutable content keys، ثم checksum/reconciliation لDB/S3. دعم شروط S3 في MinIO المثبت يجب إثباته؛ لا تفترض transaction موزعة [S8].

### QA والتأليف

قِس كل من generation/judge/repair/enrichment/with-lesson solver/blind solver مستقلاً. أصلح scopes بلا محتوى كافٍ من المخطط/التقسيم؛ لا تطلب خمسة أسئلة من عنوان فقط ثم تسدد failure باستدعاءات جديدة. احسب المتاح من evidence options قبل اختيار target count؛ عند عدم الكفاية تكون حالة partial/needs-source صريحة وإعادة المحاولة على فشل transient فقط.

التصحيح المرشح: schema enum للمصدر وquote_option_id، ثم الكود ينسخ quote exact من النسخة المعتمدة؛ قبول العناصر التي اجتازت كل الشروط واستدعاء repair للأخطاء المحددة فقط. استبدال judge بقرار صغير يمكن تسريعه، لكن ملف المقارنة الحالي في workspace (`outputs/eval/jev-hard-decision-summary.json`) أظهر على 24 حالة اصطناعية Jev 24/24 وMercury22/24 وClef19/24 وKev16/24؛ Mercury وافق على converse خاطئ بثقة عالية. هذه أدلة تشخيصية تمنع افتراض أن confidence وحدها بوابة صحة. اختبر judge كامل متطلبات المشروع: دعم الإجابة، answerability، relevance-to-scope، ثم solver منفصل عند الحاجة. النموذج/المعيار الذي يجتاز جزءًا لا يُعلن صالحًا لكل الأجزاء [S9].

إثراء كل درس بعد QA ذلك الدرس يمكن أن يتيح ترجمة/صوتًا جزئيًا بدلاً من حجب الكتاب كله، بشرط artifact جزئي versioned ومعرّف للمستخدم. لا تغيّر سياسة عدد الأسئلة أو الاختبارات من أجل ادعاء اكتمال.

### RAG

أولاً احفظ passage vectors مرة واحدة مع text hash/model revision/dim/pooling/normalization/prompt. أكمل API store لقراءتها بدل JSONB الذي لا يستخدمه مسار query الحالي. query vector فقط يحسب عند الاستعلام، وcache ليس الحقيقة. ابنِ lexical statistics/index مرة لكل نسخة corpus، وأبقِ spine/supplement filters قبل ranking. ابدأ exact vector فوق المرشحين المصفّين للدرس، أو exact SQL filtered بحثًا لحجم المشروع الحالي؛ HNSW تجربة عند corpus أكبر، مع مقارنة filtered recall وEXPLAIN وإعداد iterative scans إذا supported [S3].

EmbeddingGemma مرشح للتقييم وليس تغييرًا مطلوبًا قبل حل إعادة الحساب؛ prompts الخاصة query/document وإصدارات embedding مطلوبة لمقارنة عادلة [S10]. قيّم BGE-M3 والمرشح على نفس chunks/labels، وبنفس mode/top-k؛ الاختبار الصغير أو الاسم الأحدث لا يكفي.

### الترجمة

استبدل قاعدة ثمانية مقاطع بpacker يحترم token estimate وحجم الإخراج ويحتفظ boundaries؛ اجمع القصيرة وافصل الطويلة عند جمل سليمة، وخرائط segment IDs ثابتة. احقن المصطلحات المستخدمة في الدفعة فقط بدل termbase كامل إن كبر. checkpoint لكل segment متحقق أو مجموعة صغيرة atomic؛ queue مستقلة مع quota مشتركة. قبل إعادة استخدام قطع من نموذج سابق: المصدر/prompt/glossary/numeric/citation validation ثابتة، مع retention واضح لمنشئ كل قطعة. تغيير model لا يستلزم هدر المقبول، ولكنه أيضًا لا يبرر silent reuse بلا سياسة.

### OCR والصوت

OCR: قِس render/native extraction/layout/table/OCR مستقلاً؛ metadata لا يثبت جودة native text لمجرد كثرة الحروف. احتفظ confidence/provenance وجرّب bad-text heuristics على نص digital فاسد. cache صفحة by input image hash/model/options ثم assembled book؛ Surya لا يظهر في المصدر cache صفحة مستقلًا مماثلاً لـLLM gateway، فيبقى هدفًا للتجربة. Docling يدعم stage batching/threaded pipeline، لكن الوثائق أحدث من lock؛ تحقق خيارات النسخة أولاً ثم sweep صغير، دون تغيير preprocessing/scale في نفس تجربة concurrency [S4].

الصوت: track cache قائم ويُحتفظ به. أضف segment WAV artifact durable قبل assembly؛ فشل آخر جزء لا يعيد السابق. assembly من الملفات/stream إلى temp file ثم upload_stream، لا كامل MP3 داخل memory عند الطرفين. حفظ timestamps/durations/provenance مع manifest. لا تُشغّل عدة inference calls متزامنة حول `_model` المشترك قبل اختبار serialization/thread-safety؛ البديل queue واحدة وprefetch طبيعي للنص/المعالجة الأمامية. Fasih compose الحالي يحدد OMP/MKL=4، بينما `docker/fasih/server.py` لا يطبق صراحة helper inter-op في `packages/speech/.../fasih_runtime.py`. تحقق build context الفعلي الذي يأتي من data disk، ولا تدّعِ أن runtime خاطئ من هذا الفرق وحده. اختبارات threads 2/4/6/8 مقترحة وبنفس corpus/seeds؛ PyTorch يحذر من oversubscription [S5].

### Android

يبقى backend صاحب خطة المراحل؛ الهاتف يرسل plan وينشترك في progress، وليس دورة ست ساعات مسؤولة عن إرسال المرحلة اللاحقة. مؤقت status مستقل عن command io، in-flight request واحد، backoff عند disconnect، إلغاء watchers مع lifecycle دون إلغاء server workflow. ملفات كبيرة عبر streaming/download API، snapshots مقسمة للدروس، تحديث UI محدود عند تغير البيانات. هذه التحسينات تحافظ على Android7/8 وARMv7 ولا تتطلب تغيير لغة التطبيق أو إطار الواجهة [S6].

## برنامج القياس والتجارب الآمنة

لا تنفذ هذه التجارب على العامل الحي الآن. جهز copies من accepted artifacts، queues وبوادر مخزن بأسماء فريدة في B2C، وmodel/request budget ضيق مسجل. لا تدع canary يتنافس على native TTS/embedding CPU إلا بعد تثبيت سعة متاحة؛ لا تبدأ services تلقائيًا من هذا التقرير.

1. **Baseline مصغر ممثل:** 12 درسًا من كتابين أساسي/مساعد، أطوال قصيرة/متوسطة/طويلة وEN/AR؛ 20 صفحة native/scan/table ومخرجات صوت 12 قطعة بأرقام/مصطلحات. freeze hashes والإعدادات قبل المقارنة. هذه عينة أداء أولية وليست gold للجودة أو معيارًا عالميًا.
2. **مقاييس:** queue wait/inference/checkpoint/validation/repair/assembly أزمنة منفصلة، p50/p95؛ prompts/outputs/reasoning tokens، التكلفة per accepted question/per source word، requests/retries/cache hits، CPU/RSS، عدد الوحدات المحفوظة. اشمل cold/warm وrestart على بيئة معزولة فقط.
3. **Concurrency sweep:** نفس المدخلات 1 ثم2 ثم3 external in-flight، 3 تكرارات؛ target مقترح تحسن wall time ≥30% مقارنة1، cost/accepted item لا يزيد أكثر من10%، بلا فقد أو duplicate أو quota leakage. إذا زادت429/queue wait أو انخفضت الجودة، تخفّض concurrency. هذه حدود تجريبية مقترحة للاتفاق وليست industry mandate.
4. **Quality invariants:** 100% cite/source/role/page وID preservation؛ لا زيادة في critical false-supported/number errors على مجموعة ثابتة، والحالات الحرجة تُفحص آليًا منفصلة. النموذج المقارن لا يكون هو المرجع الوحيد الذي يقرر قبوله. المراجعة البشرية تظل مؤجلة لتجريب المستخدم وفق القرار الحالي.
5. **Recovery:** kill/process failure، timeout، network loss،429،storage write failure،model switch بعد accepted units؛ النتيجة المطلوبة صفر فقد للوحدات committed، وعدم طلب LLM/TTS مجددًا للوحدات ذات هوية متوافقة. اختبر concurrent writers لنفس artifact key وLRU513/1024 input قبل بحث الأداء.
6. **Retrieval:** 397 ثم1000 ثم5000 مقطع في corpus معزول؛ cold/warm وrestart؛ passage embedding calls بعد warmup=0 للمقاطع الثابتة، Recall/nDCG لا ينخفضان عن baseline ضمن tolerance محدد مسبقًا، وعزل الكتب/الأدوار100%. ANN لا يعتمد ما لم تحافظ filters على recall.
7. **Android:** API24/27، upload كبير، navigation أثناء polling/ملف تحميل، airplane-mode/reconnect/background/recreate؛ target UI responsive، عدم duplicate dispatch، الحالة تتجدد بعد reconnect خلال دورتين متابعة. measured success يحتاج emulator/device traces، لا screenshot فقط.

## تسلسل التنفيذ وشروط القبول

- **A: قياس وصحة** — instrument الوحدات، إصلاح LRU output، acceptance regression، manifest writer ownership، checkpoint/recovery. لا تعديل العامل الحي؛ prepare source وcanary evidence.
- **B: throughput خارجي** — split bounded units، budget limiter ذري، slots منفصلة للترجمة، concurrency sweep؛ ثبّت أفضل نقطة مقاسة. الإصدار الحالي يظل قادرًا على استعادة checkpoints المقبولة دون migration destructive.
- **C: تقليل الطلبات** — quote IDs، item salvage/repair، packer ترجمة وprompt reductions؛ اختبر معايير الدعم والمصطلحات والاستشهادات قبل adoption.
- **D: مخرجات تدريجية وتجربة مستخدم** — per-lesson artifacts ثم translation/audio durable segments، خطة خادم ومراقبة الهاتف؛ تثبت export/display/resume لمخرجات جزئية.
- **E: نمو حجم corpus** — vectors/lexical persistence، exact search أولاً، HNSW/reranker/model replacements فقط حسب فائدتها المقاسة.

كل حزمة لها feature flag/queue version، مقارنة outputs، migration backward compatible إن لزمت، image/source manifest وrollback. لا يُنشر تغيير concurrency أو activity contract على تشغيل حي قائم. تعريف النجاح هو انخفاض الزمن/الهدر مع ثبات invariants واجتياز الاستعادة وتخزين مخرج فعلي، وليس توثيق خطة أو build ناجح. المجهولات المتبقية: حصة provider الحالية، صورة runtime/build context الفعلية، traces لكل substage، جودة corpus مستقلة، سعة CPU/GPU المتاحة، ومقادير زمن network. لا تُملأ هذه المجهولات بأرقام مفترضة.

## مصادر أولية وتاريخ الاطلاع

اطلاع جميع الروابط التالية: **2026-10-08**. المصادر التالية تدعم أنماط التنفيذ، وليس ادعاء نسبة تسريع على جهاز B2C. لا اقتباسات مطولة.

- **S1 — Temporal:** [Detecting Activity failures](https://docs.temporal.io/encyclopedia/detecting-activity-failures)؛ heartbeat timeout منفصل عن start-to-close، ونبضات الأنشطة الطويلة والتقدم والاستعادة.
- **S2 — Temporal:** [Worker Versioning](https://docs.temporal.io/worker-versioning)؛ إدارة تغييرات العمال/workflows بصورة متوافقة مع الأنشطة الممتدة. تحقق دعم نسخة Temporal المثبتة قبل التطبيق.
- **S3 — pgvector:** [المستودع الرسمي: filtering،multitenancy،iterative scans](https://github.com/pgvector/pgvector/blob/master/README.md)؛ ANN filter selectivity قد ينقص عدد النتائج، وخيارات exact/partition/iterative تحتاج قياسًا.
- **S4 — Docling:** [Pipeline options](https://docling-project.github.io/docling/reference/pipeline_options/) و[GPU support/stage concurrency](https://docling-project.github.io/docling/usage/gpu/)؛ batch/threaded options على الإصدارات المدعومة. دعم CUDA لا يثبت وجود GPU متاح لدينا.
- **S5 — PyTorch:** [CPU threading and inference](https://docs.pytorch.org/docs/2.14/notes/cpu_threading_torchscript_inference.html)؛ thread tuning وتجنب oversubscription. هذه وثائق حديثة؛ تثبت المبادئ ولا تفترض أن2.14 مثبتة في صورة Fasih2.3.1.
- **S6 — Android Developers:** [Better performance through threading](https://developer.android.com/topic/performance/threads)؛ جعل UI responsive وفصل العمل الطويل عن العرض/lifecycle.
- **S7 — OpenRouter:** [Credit and rate limits](https://openrouter.ai/docs/api_reference/limits) و[Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)؛ معدل/حصة/ميزانية الطلبات المتزامنة منفصلة؛ schema يحتاج model/provider capability. لا تنسخ quota من جدول عام وتعتبرها رصيد الحساب الحالي.
- **S8 — Amazon S3:** [Conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)؛ منع overwrites بمراجعة توافق MinIO المختبر، مع ownership/verification لملفات artifact المتعددة.
- **S9 — OpenRouter:** [Jev typed Decisions](https://openrouter.ai/blog/tutorials/how-to-use-jev/)؛ القرارات المحددة والمقاييس الرقمية مقابل التوليد، ومعايرة thresholds على بيانات المهمة. [Kev original repository](https://github.com/jaredpalmer/kev)؛ architecture/weights/evaluation، ولا يكفي تشابه الواجهة لتساوي الجودة.
- **S10 — Google:** [EmbeddingGemma introduction](https://developers.googleblog.com/en/introducing-embeddinggemma/)؛ مرشح embeddings للأجهزة المحلية؛ لا يستبدل retrieval/storage ولا ينتج OCR/TTS أو QA بنفسه.
- **S11 — HTTPX:** [Client connection pooling](https://www.python-httpx.org/advanced/clients/)؛ gateway الحالي يستخدم Client بالفعل، فلا يُقترح إنشاء client جديد لكل طلب. embedding/audio urllib الحاليان مرشحان للمقارنة مع pooling بعد القياس.

