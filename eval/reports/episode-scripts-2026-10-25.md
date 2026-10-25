# نصوص الحلقات (الأشكال المشتقة): تشغيل حقيقي على كتاب SAT — 2026-10-25

**المراجع:**
- `docs/proposals/2026-10-25-learning-companion.md`: الأقسام §2 و§12–§15.
- القرارات: D-032، وD-035..D-038.
- العقد: `docs/02-CONTRACTS.md` §13 (‏`EpisodeScript`، الإصدار SCHEMA 1.14.0).

**الفرع:** `worktree-agent-a1869eaeadd74c612`، متفرّع من `hardening` عند `bfbf7f4`. آخر commit شُغّلت منه الأرقام هو `9f56bee`، والشجرة نظيفة، لذلك قُبلت المخرجات التي اجتازت بواباتها في سجل الترقية.

> البوابات هنا **سياسة مؤقتة** لمرحلة جديدة، وليست بوابة مرحلة من `docs/05-PHASES.md`. ولا يُعلن هنا اجتياز أي Gate رسمي.

## 1. ما نُفّذ فعلًا (مُتحقَّق)

### 1.1 الكود (commits)
| commit | المحتوى |
|---|---|
| `12797f0` | العقد `EpisodeScript` بإصدار SCHEMA 1.14.0، وتحديث docs/02 §13. الشكل واحد للنصين الإنجليزي والعربي، وفيه: ترتيب حلقات D-038، والحلقة 00، والأصوات الثلاثة، والإسناد، وخمسة أسئلة لكل منها مهلة وموضع، ونص الكتاب إحالةً لا توليدًا |
| `fe4ec1d`، `e0cdc70`، `0bf533c`، `9f56bee` | `b2c_translation.first_mention`: إدراج الأصل عند أول ذكر بطريقة حتمية لا يتغير ناتجها بالتكرار، مع قائمة بيضاء صريحة في فحوص QA (`CheckContext.glossed_originals`) |
| `2f31a84`، `81ae46a`، `dc575db` | الحزمة الجديدة `b2c_episodes`: المولّدات بالإنجليزية، والإسناد الحرفي، والمدقق من عائلة نماذج أخرى، وبوابات المراجعة والتغطية والطول، ووحدات ترجمة النص، وتجميع النص العربي |
| `e2bd7e3` | `b2c_orchestration.episodes`: سير العمل `BuildEpisodeScriptsWorkflow` ونشاطاه `build_episode_script_en` و`translate_episode_script`، والسكربت `scripts/episodes/run_episodes.py` |
| `e6d5542` | `b2c_speech.text.episode`: front end الكلام يستهلك النص العربي للحلقة |

### 1.2 الأمر والمدخلات
```
uv run python scripts/episodes/run_episodes.py --episodes 0 8 9 --parsed …/ingest-parse-pdf/e40a641e…/parsed-book.json \
  --outline …/approve-outline/8a4a7621…/approved-outline.json --units …/translation-units/718cd35e…/units.json \
  --termbase …/book-termbase-approved/84a8e5d7…/termbase.json --concepts …/concept-layer/6112e67b… \
  --dossier …/knowledge-research/a82fb1aa… --results results-snapshot.jsonl --run-id 66f03cab… \
  --profiles profiles.json --engine-profile gemma4-26b --qe-profile deepseek-mqm --work-dir /root/.cache/b2c-episodes/run
```

**المدخلات:**
- **نتائج وحدات التشغيل `66f03cab…`:** لقطة قراءة فقط من `translation_unit_results`، فيها 1655 نتيجة: 1463 مقبولة، و124 `needs_review`، و3 مرفوضة، و63 `dropped`. الترجمة ما زالت جارية.
- **القاموس المعتمد:** بصمته (digest) ‏`22eed3cc…` تطابق `termbase_digest` في كل نتائج التشغيل.
- **طبقة المفاهيم والملف المعرفي:** هما المقبولان من تشغيل بحث المعرفة. سُجّلا في السجل المحلي ببواباتهما المسجلة، وكلها ناجحة.

**النماذج** (D-029، عبر `b2c_llm_gateway`):
- **المولّد:** `deepseek/deepseek-v4-flash-0731`.
- **المدقق:** `google/gemini-3.1-flash-lite`، من عائلة مختلفة. العقد وorchestration يرفضان أن يكون المدقق من عائلة المولّد.
- **الترجمة:** ملفا تشغيل الكتاب نفسيهما: المحرك `gemma4-26b` (Ollama محلي) وQE `deepseek-mqm`. تغيّر في ملف الإعدادات **عنوان المحرك فقط** (`172.25.0.3` بدل اسم الخدمة)، لذلك تختلف بصمة الملف (`fingerprint`) عن بصمة التشغيل.

**الكلفة:** ‏**0.023$** لكل الاستدعاءات الخارجية: 117 استدعاءً لـ DeepSeek و15 لـ Gemini. أما Gemma فمحلي (136 استدعاءً).

### 1.3 اختيار الحلقات
الترقيم مأخوذ من المخطط v1 المعتمد بترتيب القراءة (D-038): 81 حلقة، والحلقة 00 هي المقدمة.
- **الحلقتان 01 و02 غير صالحتين:** فيهما وحدات `needs_review` (‏4 و13 وحدة).
- **أول الحلقات التي كل وحداتها مقبولة أو `dropped`:** 03 و06 و08 و09.
  - 03 («11.3.1 Structuring the Data») و06 («Preface»): لا تحويان أي كلمة نثر، فهما عنوان فقط أو سطر من الفهرس، ولا يمكن تلخيصهما ولا وضع أسئلة عليهما.
  - لذلك اخترنا **08 «Our Vision»** (517 كلمة، 28 وحدة) و**09 «Role of Structured Analytic Techniques»** (430 كلمة، 13 وحدة)، ومعهما **00**.

> ملاحظة: الحلقات 01 و02 و04 في المخطط v1 هي صفحة العنوان والفهرس وقائمة الأشكال. هذا خلل في المخطط v1 (تعالجه v2)، وليس في هذه المرحلة.

### 1.4 الاختبارات
`make check` ‏(rc=0):
- ruff نظيف.
- import-linter: ‏**9 عقود kept / 0 broken**، منها العقد الجديد: «episodes reaches models only through an injected gateway client».
- `tests/contract`: ‏**112 passed**.
- `tests/unit`: ‏**2059 passed / 0 failed / 0 skipped**.

الاختبارات الجديدة:
- `tests/contract/test_episode_script.py`: ‏12 اختبارًا.
- `tests/unit/episodes/`: ‏10 اختبارات.
- `tests/unit/translation/test_first_mention.py`: ‏9 اختبارات.
- `tests/unit/orchestration/test_episode_activities.py`: ‏6 اختبارات، منها سير العمل على خادم Temporal الاختباري.
- `tests/unit/speech/test_episode_speech_text.py`: ‏2 اختباران.
- الكتالوج: T-EP-01..08 في docs/03.

## 2. القياسات

### 2.1 النص الإنجليزي (`episode-script-en`)
| المقياس | 00 | 08 | 09 |
|---|---|---|---|
| جمل اقترحها المولّد | 8 | 23 | 24 |
| المقتطف موجود حرفيًا في كتلته (grounding عند التوليد) | 8 (100%) | 23 (100%) | 23 (95.8%) |
| **نسبة نجاح المدقق** (من المسنَد) | **6/8 = 75%** | **22/23 = 95.7%** | **21/23 = 91.3%** |
| `episodes.grounding` على المخرَج المخزَّن (مواضع) | 1.0 (19) | 1.0 (63) | 1.0 (48) |
| **التغطية** (مفاهيم أول ذكر في الملخص أو المصطلحات) | — | **8/8 = 1.0** | **8/8 = 1.0** |
| المستخدم: مراجعة / ملخص / مصطلحات / أسئلة | — | 2 / 6 / 8 / 5 | 3 / 6 / 7 / 5 |
| كلمات مولَّدة (دقائق سماع تقريبية عند 130 كلمة/د) | 312 (2.4) | 655 (5.0) | 677 (5.2) |
| البوابات (grounding، validation، length، coverage) | ✔ ✔ ✔ — ⇒ **accepted** | ✔ ✔ ✔ ✔ ⇒ **accepted** | ✔ ✔ ✔ ✔ ⇒ **accepted** |

**المراجعة لا تذكر إلا الحلقات السابقة.** كل مصادر سطور المراجعة في 08 و09 دروسٌ سابقة. ويفشل `episodes.grounding` إذا استشهد سطر مراجعة بالدرس الحالي، وهذا مختبَر كـ negative control.

### 2.2 النص العربي (`episode-script-ar`)
| المقياس | 00 | 08 | 09 |
|---|---|---|---|
| سطور مترجمة بـ UnitTranslator (مقبولة / needs_review) | 16 / 0 | 32 / 1 | 30 / 3 |
| نسبة القبول | **100%** | **97.0%** | **90.9%** |
| وحدات نص الكتاب (كلها مقبولة في تشغيل الكتاب) | — | 20 | 11 |
| مصطلحات أول ذكر المعتمدة المقفلة | 0 | 8 | 8 |
| أصل أُدرج عند أول ذكر / غير موجود في النص العربي | — | **8 / 0** | **7 / 1** («mental model») |
| ملاحظات QA جديدة سببها الأصل المُدرج (بالقائمة البيضاء) | 0 | 0 | 0 |
| إعادة الإدراج لا تغيّر الناتج (idempotent) | ✔ | ✔ | ✔ |
| `episodes.translation` / `episodes.first-mention` | ✔ / ✔ ⇒ **accepted** | ✘ / ✔ ⇒ rejected | ✘ / ✔ ⇒ rejected |

**أسباب `needs_review`:** هي قرارات `qa.policy` نفسها التي تُطبَّق على وحدات الكتاب.
- 08/o00: ‏`named_entity` (مرتان). كتب المحرك «أموس تفرسكي»، والمعتمد «آموس تفيرسكي».
- 09/n00: ‏`llm_mqm.terminology`.
- 09/s01 و09/k06: ‏`llm_mqm.grammar`.

لذلك لا تُرقّى الحلقتان 08 و09 العربيتان. والقرار هنا مقصود: لا يُتجاوز.

**الكلام:** مرّ النص العربي للحلقة 00 في `build_episode_speech_text` بقاموس نطق من القاموس المعتمد. النتيجة: 21 segment، و0 مخالفات (لا أرقام ولا حروف لاتينية ولا روابط). لكن بوابة الكلام **فشلت** بثلاث كلمات خارج القاموس: `Dario` و`Galasso` و`OpenAlex`. أما 08 و09 فيُرفضان قبل ذلك بـ `NotSpeechReady` لأن فيهما سطورًا غير مقبولة.

## 3. مقتطفات قصيرة

### 3.1 الحلقة 00: مقدمة الكورس
| القسم | EN | AR |
|---|---|---|
| about_book (خارجي) | According to OpenAlex and Dario E Galasso: This book is a comprehensive guide to structured analytic techniques for intelligence analysis. | وفقاً لـ OpenAlex وDario E Galasso: هذا الكتاب هو دليل شامل لتقنيات التحليل المنظم للتحليل الاستخباري. |
| book_method (الكورس) | It builds its ideas through a step-by-step process that externalizes thinking, making it shareable and critiquable. | إنه يبني أفكاره من خلال عملية خطوة بخطوة تعمل على إخراج التفكير إلى العلن، مما يجعله قابلاً للمشاركة والنقد. |
| course_guide | The first time a key term appears in an episode, you hear it in Arabic followed by the author's original term. | في المرة الأولى التي يظهر فيها مصطلح رئيسي في الحلقة، تسمعه باللغة العربية متبوعاً بالمصطلح الأصلي للمؤلف. |

**رفضها المدقق:** «The authors are Richards J. Heuer Jr. and Randolph H. Pherson, both experienced intelligence analysts». السبب: الدليل لا يذكر خبرة Heuer.

### 3.2 الحلقة 08: «Our Vision»
| السطر | EN | AR |
|---|---|---|
| مراجعة (من الحلقات السابقة) | Structured Analytic Techniques externalize an analyst's thinking through a step-by-step process, making it visible and shareable. | تعمل تقنيات التحليل الممنهج على إخراج تفكير المحلل إلى العلن من خلال عملية خطوة بخطوة، مما يجعله مرئياً وقابلاً للمشاركة. |
| المصطلحات الجديدة (أول ذكر) | New terms in this episode: Structured Analytic Technique, law enforcement, driving forces, mental model, … | مصطلحات جديدة في هذه الحلقة: تقنيات التحليل المنظم (Structured Analytic Technique)، إنفاذ القانون (law enforcement)، القوى الدافعة (driving forces)، النموذج الذهني (mental model)، … |
| مصطلح | mental model: A mental model is an alternative way of thinking about a problem that analysts need to identify and evaluate for validity. | النموذج الذهني (mental model): النموذج الذهني هو طريقة بديلة للتفكير في مشكلة ما، يحتاج المحللون إلى تحديدها وتقييم مدى صحتها. |
| سؤال ← مهلة 4000 ms ← إجابة | What are two driving forces behind this transition? / The growing complexity of international issues and the need to share information quickly across organizational boundaries. | ما هما القوى الدافعة وراء هذا التحول؟ / التعقيد المتزايد للقضايا الدولية والحاجة إلى مشاركة المعلومات بسرعة عبر الحدود التنظيمية. |
| نص الكتاب (وحدة الكتاب المقبولة `book-main:u00345`، دون إعادة ترجمة) | — | إن التحليل كما هو ممارس في مجتمعات الاستخبارات، وإنفاذ القانون، والأعمال يتطور باطراد من نشاط ذهني يقوم به بشكل أساسي محلل واحد إلى نشاط تعاوني… |

**رفضها المدقق** (من سطور المراجعة): «…are ideal for collaboration among analysts with different expertise». السبب: الدليل لا يقول «ideal».

### 3.3 الحلقة 09
- **سؤال وإجابته** (مقبولان): «ما هو الدور الأساسي لتقنيات التحليل المنظم؟» / «يتمثل دورها في التشكيك في الأحكام الحدسية من خلال تحديد مجموعة أوسع من الخيارات…».
- **أسئلة رفضها المدقق أو الإسناد:**
  - «How do structured techniques save analysts time…». السبب: المقتطف غير موجود في `book-main:47:1`.
  - «What does the author say is necessary in addition to analytic methods…». السبب: غير مدعوم.

## 4. ما غيّره التشغيل الحقيقي (أخطاء وُجدت وأُصلحت)
- **ملخص جاء فقرة واحدة طويلة** فاقتُطع وفشلت الحلقة 09. الإصلاح: كل جملة سطرٌ يُفحص وحده (`81ae46a`).
- **اسم مجلة إنجليزية في سطر الإسناد** (`Policing A Journal of Policy and Practice`) أوقع 4 سطور في `untranslated_english` في الحلقة 00. الإصلاح: يُنطق اسم صاحب الرأي وحده، ويبقى الإسناد الكامل في `attributed_to` (`dc575db`). بعده صار قبول ترجمة الحلقة 00 ‏16/16.
- **مصطلح متداخل في مصطلح أطول** («التحليل المنظم» داخل «تقنية التحليل المنظم») كسر ثبات الإدراج، ولم يُعلَّم المصطلح الأطول. الإصلاح: الأطول أولًا، ولا يُعلَّم الأقصر داخل موضع الأطول (`0bf533c`).
- **جمع المؤنث السالم** («تقنيات» مقابل «تقنية») لم يُعدّ ذكرًا للمصطلح. وأُدرج الأصل داخل عنوان الكتاب في سطر رقم الحلقة. الإصلاح في `9f56bee`.

## 5. ما لم يُنفَّذ أو بقي افتراضًا (غير متحقق)
- **لا تشغيل عبر Temporal أو worker:** اختُبر سير العمل على خادم Temporal الاختباري بأنشطة وهمية فقط. ولم يُبنَ image، وسجل الترقية كان في الذاكرة. لا كتابة إلى Postgres أو MinIO الإنتاجيين، والقراءة منهما فقط.
- **ذاكرة الترجمة** في التشغيل ملف محلي (`FileMemory`)، لا جدول `translation_memory`.
- **أوصاف الأشكال** (`figure_description`): المخطط v1 بلا `block_roles`، فبقيت التعليقات داخل `book_text` بترتيب القراءة. وقسم `figure_description` مدعوم في العقد فقط.
- **قسم «مناظرة» (`debate`):** مدعوم في العقد لكنه لا يُولَّد بعد.
- **الشكر** في الحلقة 00: لم يقترح المولّد سطر شكر من المقدمة والتصدير، فالقسم غائب. لم نتحقق هل في نص SAT شكر مستقل.
- **الأصل عند أول ذكر في نص الكتاب:** مختبَر في الوحدات، لكنه في التشغيل الحقيقي لم يقع في نص الكتاب (`inserted_in_book_text = 0`). السبب أن سطر «مصطلحات جديدة» في التقدمة هو أول ذكر لكل مصطلح.
- **العتبات** (0.6 للمدقق، و0.7 للتغطية، وحدود الطول) سياسة مؤقتة غير معايرة. ولم تُقَس دقة المدقق نفسه على عيّنة مستقلة.

## 6. ما يبقى
1. أسماء الأعلام اللاتينية في سطور الإسناد (OpenAlex، وDario Galasso) تحتاج قاموس نطق أو مدخلات كيانات معتمدة قبل أن تجتاز الحلقة 00 بوابة الكلام.
2. «سلّم التصعيد» (§5 من المقترح) لسطور النص التي انتهت `needs_review`، أي الإصلاح الموجّه ثم حاشية العجز. لم يُبنَ بعد لنصوص الحلقات، كما هو الحال لوحدات الكتاب.
3. الحلقتان 01 و02 تنتظران قبول وحداتهما في تشغيل الكتاب. والأفضل الانتقال إلى المخطط v2 (‏`episode`/`episode_title` وأدوار الكتل).
4. تشغيل `BuildEpisodeScriptsWorkflow` في worker حقيقي بعد بناء image (ممنوع الآن أثناء الترجمة الإنتاجية).
