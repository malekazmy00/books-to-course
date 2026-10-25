# أوصاف الأشكال والجداول للسماع، والترميز المتدفق للحلقة MP3 (2026-10-25)

- **القرار:** D-044 في `docs/00-DECISIONS.md`. أصله قرار المالك في 2026-10-25: الصور والأشكال والجداول تُقرأ في الصوت بوصفها، لأن المستمع قد يكون كفيفًا.
- **العقد:** SCHEMA 1.20.0، وهو موثّق في `docs/02-CONTRACTS.md` §17.
- **الاختبارات:** المعرّفات T-FIG-01..08 وT-AUD-14..15 في `docs/03-TESTING.md`.
- **الفرع:** `feat/figure-descriptions-episode-mp3`، المبني على `hardening` عند `92c2d32`.
- **حدود العمل:**
  - لا تغيير حي في Postgres أو MinIO، ولا بناء صور، ولا لمس لحاويات خارج b2c.
  - مع الحاويات نُفّذ أمران فقط:
    - قراءة `/api/tags` و`/api/show` من `b2c-gemma4-ollama-1`؛
    - ثلاثة طلبات وصف بالصورة إلى `b2c-gemma4-ollama-1`.
  - كل تشغيل كان بـ `nice -n 15`.

## 1. ما نُفّذ

| البند | الموضع |
|---|---|
| العقد 1.20.0: ‏`FigureDescriptionsDocument` و`FigureDescription` و`FigureGrounding` و`FigureSkip`، و`SpeechSegment.figure_id/figure_part`، و`SpeechTextDocument.figures_ref`، وقسم `figure_reading` | `packages/contracts/src/b2c_contracts/models.py` |
| الجرد، وقراءة الجدول الصغير، وبوابة الإسناد، والموضع، والوصف (مع الصورة)، وفحص اللغة، والرجوع الآمن، وإدخال الأقسام في الحلقة | `packages/episodes/src/b2c_episodes/figures.py` |
| الكلام: الوصف المقبول يُقرأ بعد وحدته بصوت الكورس معلنًا ومختومًا | `packages/speech/src/b2c_speech/text/frontend.py` و`text/episode.py` |
| ‏`complete_vision`: الصورة قبل النص، ومفتاح الـ cache ببصمة الصورة لا ببايتاتها | `packages/llm_gateway/src/b2c_llm_gateway/client.py` |
| activities ‏`describe_figures_en` و`translate_figures`، و`BuildFigureDescriptionsWorkflow`، وقص صورة الشكل من الـ PDF | `packages/orchestration/src/b2c_orchestration/figures.py` |
| ‏`figures_ref` في مدخل الكلام v2 ومدخل الحلقة الإنجليزية | `speech_v2.py` و`episodes.py` |
| التجميع والتطبيع والترميز المتدفق | `packages/speech/src/b2c_speech/audio/stream.py` |
| ‏`speech_v2_encode_episode` و`EncodeEpisodeMp3Workflow` على طابور الصوت | `packages/orchestration/src/b2c_orchestration/episode_mp3.py` و`worker.py` |

## 2. المصادر (القاعدة 10؛ قُرئت في 2026-10-25)

### 2.1 هل يقبل Gemma 4 في Ollama الصور؟ نعم.

- **المصدر الرسمي:** صفحة [ollama.com/library/gemma4](https://ollama.com/library/gemma4).
  - فيها: «Gemma 4 models are multimodal, handling text and image input».
  - الوسم 26b (‏A4B، MoE) مدرج بمدخل «Text, Image».
  - مشفّر الرؤية نحو 550M.
  - الصفحة توصي بوضع الصورة قبل النص. ميزانية رموز الصورة 70/140/280/560/1120.
- **الخدمة المحلية:** ‏`/api/tags` و`/api/show` على `b2c-gemma4-ollama-1` (Ollama 0.34.0).
  - النموذج `gemma4-b2c:26b-a4b-it-qat-t18` قدراته `["completion","vision","tools","thinking"]`.
  - فيه `projector_info`: ‏`clip.has_vision_encoder: true` و`projector_type: gemma4v` و`image_size: 224`.
- **تجربة فعلية:** طلب بصورة الشكل 2.1، قُصّت من الـ PDF.
  - أعاد وصفًا صحيحًا فيه محورا Qualitative/Quantitative وKnown/Unknown Data، وهما غير مذكورين في نص الفقرة (§4.3).
  - استغرق 706 ث، لأن الطلب انتظر خلف طابور الترجمة ذي الفتحة الواحدة.

### 2.2 إرشادات الوصف

- **[DIAGRAM Center — Image Description Guidelines](http://diagramcenter.org/table-of-contents-2.html)**
  - كتبها NCAM/WGBH مع DIAGRAM/Benetech، وهي أساس أداة Poet.
  - الشهادة غير مطابقة للنطاق، فقُرئت النصوص بـ `curl -k` لأنها قراءة عامة.
  - **الإرشادات العامة** ([?p=2097](http://diagramcenter.org/?p=2097)):
    - «Context is Key»؛
    - «Be Concise … Don't repeat information presented in the main or adjacent texts»؛
    - «Be Objective … Don't interpret or analyze the material»؛
    - «General to Specific»؛
    - «Write out abbreviations and symbols»؛
    - «Include color only when it is significant».
  - **الجداول** ([?p=2189](http://diagramcenter.org/?p=2189)): الجدول البسيط يُقدَّم ببياناته. والجدول المعقد «can be broken into three separate tables … to aid in navigation and comprehension».
  - **الإطار المضمَّن (Insets):** «Determine if the graph can be sufficiently summarized in a sentence or two. If not, provide the data in a table with row and column headings».
  - **المخططات العلائقية** ([?p=2139](http://diagramcenter.org/?p=2139)): «Focus on the data … not on its appearance»، والشجرة «from top to bottom and from left to right».
- **[NLS — The Art and Science of Audio Book Production](https://www.loc.gov/nls/who-we-are/guidelines-and-specifications/the-art-and-science-of-audio-book-production/)**
  - من مسؤوليات القارئ: «Writing accurate, concise descriptions for illustrations, graphs, charts, and tables».
  - لم أجد في وثائق NLS العامة قاعدة لقراءة الجدول صفًا صفًا. صفحة [المواصفات](https://www.loc.gov/nls/who-we-are/guidelines-and-specifications/) لا تتناول ذلك.
- **[DAISY Structure Guidelines — Tables](https://daisy.org/?p=2453)**
  - فيها: «Tables normally read across from left to right».
  - وفيها: «A table usually has column headings».
  - رابطا SG-DAISY3 القديمان يعيدان 404.
- **ما بُني على المصادر:**
  - ترتيب القراءة: صف بعد صف، من اليسار إلى اليمين، باسم كل عمود.
  - الجدول الكبير يُلخَّص ولا يُقرأ كاملًا.
  - الوصف يبدأ بالعام ثم الخاص.
- **العتبة رقم هندسي من عندي، لا من المصادر:**
  - الجدول الصغير: ≤ 8 صفوف، و≤ 4 أعمدة، و≤ 24 خلية، و≤ 160 كلمة.
  - 160 كلمة نحو دقيقة سماع، وهو ما يمكن متابعته بلا تنقّل.
  - العتبة موثّقة في `SMALL_TABLE` وفي تعريف بوابة `figures.grounding`.
- **تعارض موثَّق:**
  - DIAGRAM يوصي بذكر عدد العناصر، مثل «a center circle and five smaller circles».
  - قاعدة المالك لا تسمح برقم غير موجود في الدليل.
  - طُبّقت قاعدة المالك: البرومبت v2 يطلب تسمية الأجزاء لا عدّها، والبوابة ترفض العدد غير المطبوع.
  - أما العدد في قراءة الجدول («3 columns») فيحسبه الكود من الجدول نفسه، ولا يمر على النموذج.

## 3. التصميم باختصار

- **الوصف بصوت الكورس:**
  - يُكتب بالإنجليزية من الدليل (D-035).
  - سطره الأول حتمي: «Figure 2.1: System 1 and System 2 Thinking.».
  - في الكلام يُعلن بـ «وصف الشكل.» أو «وصف الجدول.»، ويُختم بـ «انتهى وصف الشكل.».
  - يُقرأ بعد الوحدة التي تذكر الشكل قرب صفحته (±3 صفحات)، وإلا فبعد آخر وحدة منطوقة في صفحته.
- **بوابة الإسناد (`check_figure_grounding`):**
  - تشمل الأرقام، بما فيها الكسرية والمفصولة بفواصل، وكلمات الأعداد (two..billion وdozen وthird…).
  - وتشمل الأسماء، أي كل كلمة تبدأ بحرف كبير داخل الجملة، أو اختصار، أو اسم متعدد الكلمات.
  - أول كلمة في الجملة وحدها لا تُعد اسمًا، وكذلك كلمات الوظيفة بحرف كبير بعد نقطتين أو اقتباس.
  - تُفحص أسطر النموذج فقط. الأسطر الحتمية (العنوان، وأسطر الجدول، ووصف الكتاب الحرفي) مسندة بطبيعتها.
  - الوثيقة الإنجليزية في العقد لا تقبل وصفًا رسب.
- **سلّم الوصف:**
  1. محاولة أولى.
  2. إن رسب الوصف: محاولة ثانية مع تسمية ما رسب.
  3. إن أجاب النموذج بغير الإنجليزية: يُعاد بطلب الإنجليزية.
  4. وإلا يُرجع إلى `caption_only`: العنوان، ووصف الكتاب حرفيًا إن طابق وكان ≤ 150 كلمة.
- **الجرد:**
  - كتل `figure` و`table` في المتن، مع أقرب تعليق «Figure x.y» (≤ 3 كتل، و±1 صفحة).
  - الفقرات التي تذكره، حتى 3.
  - وصف الكتاب المطبوع (أقسام «Back to Figure»)، بشرط تطابق الكلمات (≥ 3 كلمات مشتركة و≥ 35%).
  - يُتخطى: المقدمة، وجداول الفهرس، والشكل بلا رقم وبأقل من 8 كلمات، ويُعد زخرفة.

## 4. القياس على SAT (الكتاب 1)، قراءة فقط

**المدخلات:**
- ParsedBook v2 لـ SAT (`gate/sat-v2new.json`، 2813 block)، من وسائط الاستخراج الموجودة.
- وحدات الترجمة الإنتاجية للكتاب: 4560 وحدة، من لقطة run الحلقات.
- القاموس المعتمد.
- `source.pdf`، لقص صور الأشكال.

**البيئة:** ‏artifact store محلي في الـ scratchpad، وregistry في الذاكرة. السكربت `scratchpad/fig/measure.py` يستدعي activities الفرع نفسها.

**النماذج (D-029):**
- الوصف: ‏`deepseek/deepseek-v4-flash-0731` عبر OpenRouter، لنحو 90 شكلًا.
- الصورة: ثلاثة طلبات إلى Gemma المحلي فقط، حتى لا يُغرق طابوره.
- المفتاح لم يُطبع.

### 4.1 الجرد

| المقياس | القيمة |
|---|---:|
| كتل `figure` و`table` في الكتاب كله | 107 |
| **أشكال وجداول في المتن (موصوفة)** | **90** (78 شكلًا و12 جدولًا) |
| مُتخطّاة | 17 (10 مقدمة، أي الغلاف والشعارات وجداول الفهرس؛ و7 زخرفة) |
| بعنوان مطبوع «Figure x.y» | 26 |
| لها فقرة تشير إليها | 23 |
| طابقها وصف الكتاب المطبوع | 38 |
| جداول صغيرة / كبيرة بعتبة `SMALL_TABLE` | 1 / 11 |
| لها موضع (`after_unit_id`) | 90 من 90 |
| موضعها بالإشارة / بالصفحة | 19 / 71 |
| المسافة بين صفحة الشكل وصفحة الوحدة: 0 / 1 / 2 / 3 | 78 / 9 / 2 / 1 |

**ملاحظة:** 64 شكلًا بلا عنوان مطبوع. هذا عيب استخراج معروف، ذكره D-043: «صفحات عناوين صور مفقودة». لذلك يكون سطرها الأول «A figure without a printed title.»، وموضعها بالصفحة لا بالإشارة.

### 4.2 الوصف والبوابة (ثلاث جولات على البيانات نفسها)

| الجولة | وصف | ملخص جدول | جدول صفًا صفًا | رجوع للعنوان | أخطاء نموذج | ما فحصته البوابة |
|---|---:|---:|---:|---:|---:|---:|
| 1. البرومبت v1 | 53 | 3 | 1 | **33** | 5 | 522 رقمًا واسمًا |
| 2. البرومبت v2، بلا عدّ ذاتي | 64 | 2 | 1 | 23 | 3 | 608 |
| 3. فحص اللغة، وقسمة السطر المتعدد الجمل، والعمود الفارغ | **66** | **3** | **1** | **20** | **0** | 626 |

**نتيجة البوابة في الوثيقة المنشورة (الجولة 3):**
- `figures.grounding` ناجحة.
- 90 من 90 موصوفة، و**0 رقم أو اسم غير مسند**.
- 32 شكلًا رسب مرة على الأقل:
  - 12 نجحت في المحاولة الثانية؛
  - 20 رجعت إلى العنوان (`caption_only`).

**ما أوقفته البوابة (كل الرسوب عبر الجولات، من `scratchpad/fig/why.out`):**
- **أعداد عدّها النموذج بنفسه:** ‏four ‏16، two ‏16، five ‏7، six ‏6، three ‏5، وغيرها. هذا أكثر الرسوب.
  - أصلحه البرومبت v2 جزئيًا.
  - المسألة تعارض بين DIAGRAM وقاعدة المالك (§2.2).
- **معرفة عامة لا يقولها الكتاب** (الشكل fig027، شبكة 11 سبتمبر، ص235):
  - «Flight 77/93»، «Pentagon»، «Pennsylvania»، «World Trade Center»؛
  - «Zacarias Moussaoui»، «Ramzi Binalshibh»، «Khalid»؛
  - «American Airlines»، «United».
  - النتيجة: رجع الشكل إلى العنوان، ولم يُقرأ شيء مخترع.
- **أخرى:**
  - كلمات إسبانية في fig020 («Ataque»، «Qui…»).
  - وصف كامل بالإسبانية في fig011. لم تلحظه البوابة في الجولة 2 لأنه بلا أسماء ولا أرقام، فأُضيف فحص اللغة الحتمي `is_english`.
  - «Roman»، «SWOT»، «TeamACH»، «URLs»: كلها غير موجودة في الدليل.

**التكلفة:** الجولة 2: ‏122 طلبًا إلى OpenRouter، و85,939 رمز دخل و17,837 رمز خرج، أي 0.0087 دولار. الجولة 3: طلب واحد جديد، والباقي من الـ cache.

### 4.3 الصورة مع Gemma 4 المحلي

- **الشكل 2.1 (fig002)، صورة 86,741 بايت:**
  - البوابة: نجحت من المحاولة الأولى، وفحصت 39 رقمًا واسمًا.
  - الزمن: 706 ث، منها الانتظار في الطابور.
  - النص بالصورة (برومبت v1):
    > «… System 2 Thinking is represented by a larger, central shape containing four categories: Critical Thinking, Structured Analysis, Empirical Analysis, and Quasi-Quantitative Analysis. The diagram is organized along two axes: Qualitative and Quantitative on the vertical axis, and Known Data and Known and Unknown Data on the horizontal axis. Critical Thinking and Structured Analysis are categorized as Qualitative, while Empirical Analysis and Quasi-Quantitative Analysis are categorized as Quantitative.»
  - **الفرق عن الوصف بلا صورة (deepseek، §4.4):** وصف بنية المحورين، وهي في الصورة لا في النص. قارن الصورة المقصوصة `scratchpad/fig/fig002.png`.
- **fig004** (ص92، بلا عنوان مطبوع، صورة 229,633 بايت):
  - رسب مرة («seven»، «third»: عدّ ذاتي)، ثم نجح في المحاولة الثانية بالصورة، وفحصت البوابة 8. الزمن 188 ث.
  - وصف مراحل Exploration ثم Diagnostic ثم Reframing ثم Foresight ثم Decision Support بأدواتها. نص OCR لهذا الشكل 266 كلمة مشوشة الترتيب، والصورة رتبته.
- **fig003 (الشكل 2.4):** رسب مرتين بالعدد نفسه («three»، «third»)، فرجع إلى العنوان. لم تُحسب الصورة مستعملة (`image_used=false`)، لأن الوصف لم يُنشر.
- **المجموع:** 5 طلبات إلى Gemma (4,236 رمز دخل و716 رمز خرج). الطلبات اللاحقة كانت أسرع من الأول (171–188 ث) لأن الطابور خفّ.
- **الخلاصة:** الصورة تضيف بنية لا يحملها النص، مثل المحاور وترتيب المراحل. لكن تشغيلها على الكتاب كله ينتظر طابورًا حرًا أو نافذة جدولة (§7).

### 4.4 خمسة أمثلة: الإنجليزية والعربية

- **طريق الترجمة:** ‏`translate_figures`، أي `UnitTranslator` بالقاموس المعتمد لـ SAT.
  - المحرك deepseek عبر OpenRouter، والـ QE ‏`deepseek-mqm`. لم يُستعمل Gemma للترجمة حتى لا يُزاحَم.
- **البوابة:** ‏`figures.translation` ناجحة: 5 أوصاف، و32 سطرًا، و0 مفقود.
- **القرارات:** 31 سطرًا `accepted` وسطر واحد `needs_review`، وهو عنوان الشكل 9.6. لذلك:
  - الوصف fig061 **لن يُقرأ** في الصوت (`figure_not_accepted`).
  - الأوصاف الأربعة الأخرى مقبولة.

**(1) الشكل 2.1 (fig002، ص65، `description`).** موضعه بعد `book-main:u00522`، والبوابة فحصت 7 بلا رسوب.

| EN | AR |
|---|---|
| Figure 2.1: System 1 and System 2 Thinking. | الشكل 2.1: نظام التفكير 1 ونظام التفكير 2. |
| This figure is a diagram that contrasts System 1 and System 2 thinking. | هذا الشكل عبارة عن رسم تخطيطي يقارن بين التفكير بالنظام الأول والتفكير بالنظام الثاني. |
| System 1 thinking is labeled intuitive judgment. | التفكير في النظام الأول يُسمى الحكم البديهي. |
| System 2 thinking is divided into four categories: critical thinking, structured analysis, quasi-quantitative analysis, and empirical analysis. | ينقسم التفكير من النوع الثاني إلى أربع فئات: التفكير النقدي، والتحليل المنظم، والتحليل شبه الكمي، والتحليل التجريبي. |
| Critical thinking is qualitative, uses known data, and includes getting started, source validation, argumentation, and presentation. | التفكير النقدي نوعي، ويستخدم البيانات المعروفة، ويتضمن البدء، والتحقق من المصادر، والحجج، والعرض. |
| (سطران آخران بالنمط نفسه) | … |

**(2) الشكل 9.6 (fig061، ص508، `description`).** موضعه بعد الوحدة التي تذكره، والبوابة فحصت 6.

| EN | AR |
|---|---|
| Figure 9.6: Alternative Futures Analysis: Cuba. | الشكل 9.6: تحليل المستقبلات البديلة: كوبا. **(needs_review)** |
| The figure is an alternative futures analysis for Cuba, using two drivers: effectiveness of government and strength of civil society. | الشكل هو تحليل المستقبلات البديلة لكوبا، باستخدام محركين: فعالية الحكومة وقوة المجتمع المدني. |
| The effectiveness of government ranges from fully operational to marginalized. | تتراوح فعالية الحكومة من التشغيل الكامل إلى التهميش. |
| The combination of marginalized government and nonexistent civil society is labeled 'Glueless in Havana.' | مزيج الحكومة المهمّشة والمجتمع المدني غير الموجود يُصنَّف تحت عنوان "بلا تماسك في هافانا". |

**(3) جدول بلا عنوان مطبوع (fig077، ص594، `table_rows`).** حتمي بلا نموذج، والعمودان الفارغان يُذكران مرة واحدة.

| EN | AR |
|---|---|
| The table has 3 columns, ACTOR, LEVEL OFINTEREST Low, Moderate, or High and IMPACT P = Mostly Positive 0 = Neutral or Mixed N = Mostly Negative, and 7 rows. | الجدول يحتوي على 3 أعمدة، الفاعل، مستوى الاهتمام منخفض، متوسط، أو مرتفع، والأثر P = إيجابي في الغالب 0 = محايد أو مختلط N = سلبي في الغالب، و7 صفوف. |
| The columns LEVEL OFINTEREST … are empty in every row. | الأعمدة "مستوى الاهتمام" … فارغة في كل صف. |
| Row 1. ACTOR: Me. | الصف 1. الفاعل: أنا. |
| Row 5. ACTOR: Colleagues or counterparts elsewhere in my organization. | الصف 5. الفاعل: الزملاء أو النظراء في أماكن أخرى من مؤسستي. |

**(4) مصفوفة متغيرات (fig040، ص348، `table_summary`).** البوابة فحصت 33.

| EN | AR |
|---|---|
| The table has 7 columns, Variable 1, …, Variable 6, and 2 rows. | يحتوي الجدول على 7 أعمدة، المتغير 1، … والمتغير 6، وصفين. |
| This table lists six variables and shows the relationships between the first two variables and the others. | يعرض هذا الجدول ستة متغيرات ويوضح العلاقات بين المتغيرين الأولين والمتغيرات الأخرى. |
| For Variable 1, the relationship with itself is nil, with Variable 2 is neutral, with Variable 3 is positive, … with Variable 5 is strong negative … | بالنسبة للمتغير 1، العلاقة مع نفسه معدومة، ومع المتغير 2 محايدة، ومع المتغير 3 إيجابية، … ومع المتغير 5 سلبية قوية … |

**(5) شكل فريق التحليل (fig011، ص118، `caption_only`).**
- هذا هو الرجوع الآمن بعد إجابة بالإسبانية ثم رسوب.
- السطر الثاني هو وصف الكتاب المطبوع حرفيًا (107 كلمات).

| EN | AR |
|---|---|
| A figure without a printed title. | شكل بدون عنوان مطبوع. |
| The book describes it as follows: Leader articulates goals, establishes team, and enforces accountability. Facilitator identifies appropriate techniques … | يصف الكتاب ذلك على النحو التالي: يقوم القائد بتوضيح الأهداف، وتشكيل الفريق، وفرض المساءلة. يحدد الميسر التقنيات المناسبة … |

**ملاحظات على الأمثلة:**
- **(أ)** عناوين أعمدة fig077 تحمل نص OCR كما استُخرج («OFINTEREST»، «P = … 0 = …»):
  - الحروف اللاتينية فيها (P وN) ستُسقط بوابة `speech.text-frontend` ما لم يكن لها مدخل في الـ lexicon.
  - لم أشغّل بناء الكلام على هذه الأسطر بعينها، فهذا غير مُتحقق.
- **(ب)** fig040 عمودها الأول بلا اسم، فيُقال «7 أعمدة» وتُسمّى 6 منها. وصفّان فقط لأن الاستخراج قطع المصفوفة.
- **(ج)** «نظام التفكير 1» ترجمة حرفية للعنوان، بينما يقول النص «النظام الأول». هذا قرار قاموس.

## 5. الترميز المتدفق وربط الحلقة بـ MP3

**قياس الذروة:**
- الحلقة: ساعة واحدة (3602.4 ث)، من 3 مسارات WAV بـ 24 kHz و16 بت، كل منها 57.6 MB، مع وقفة 1200 ms بينها.
- الكلام اصطناعي: عبارات 6 ث ووقفات 0.6 ث.
- الأداة: `scratchpad/fig/mem_episode.py`.
- الذاكرة: ‏`ru_maxrss` للعملية كلها، ومعها Python وnumpy وscipy وlameenc.

| الطريقة | ذروة RSS | الزمن | الجهارة | true peak | الحجم |
|---|---:|---:|---:|---:|---:|
| ‏`encode_episode_stream` بكتل 10 ث (الافتراضي) | **202.4 MiB** | 231.5 ث | ‎-16.0 LUFS | ‎-5.43 dBTP | 43,229,402 بايت، CBR |
| ‏`encode_episode_stream` بكتل 2 ث | **147.6 MiB** | 398.1 ث | ‎-16.0 | ‎-5.43 | 43,229,402 |
| ‏`encode_episode` في الذاكرة (D-042)، على المسارات نفسها | **2135.1 MiB** | 259.6 ث | ‎-16.0 | ‎-5.43 | 43,229,402 |

- **مقارنة بقياس D-042:** قاس D-042 ذروة 2.75 GiB لحلقة ساعة. ذلك الرقم يضم نحو 0.8 GiB من مقاطع المصدر الباقية في النشاط. القياس هنا بلا مقاطع.
- **حجم الملف:** الترميزان أعطيا الحجم نفسه بالبايت، وبالكسب نفسه (20.134 dB).

**الاستنتاج:**
- الذاكرة ثابتة تقريبًا: لا تكبر بطول الحلقة، بل بحجم الكتلة.
- اختبار `test_memory_does_not_grow_with_the_episode` يثبت ذلك بـ tracemalloc: ذروة 60 ث < 1.5 × ذروة 10 ث.
- الكسب والجهارة والمدة تطابق الترميز في الذاكرة على الإشارة نفسها. يثبت ذلك `test_streamed_episode_matches_the_in_memory_one`: فرق الكسب ≤ 1e-6 dB.
- حد عامل الصوت 2 GiB صار يتسع لحلقة ساعة، ويبقى هامش كبير.

**الربط:** ‏`speech_v2_encode_episode` ضمن `EncodeEpisodeMp3Workflow` على طابور `audio`.
- **المدخل:** مسارات `speech-track` بالترتيب. كل مسار يجب أن يكون من المقرر نفسه و`accepted` في الـ registry، وإلا يُرفع `NotAcceptedTrack` أو `InvalidSpeechInput`.
- **التنزيل:** كل `track.wav` يُنزَّل إلى ملف مؤقت (`download_to_path`؛ المجلد `B2C_AUDIO_TMPDIR`)، ثم يُرمَّز تدفقيًا.
- **الرفع:** ‏`put_stream` لـ `episode.mp3` و`episode.json`، والـ hash يُحسب أثناء الكتابة. ثم تُحذف الملفات المؤقتة.
- **artifact `episode-mp3`:** مرشح ببوابتين:
  - `speech.episode-loudness`: ملف `app-speech-16`، أي ‎-16 ±1 LUFS وtrue peak ≤ ‎-1 dBTP، مقيسة بعد الكسب وقبل الترميز؛
  - `speech.episode-format`: ‏MPEG-1 Layer III، وأحادي، وCBR، وID3v2.3، و0 بايت زائد.
- **التسمية:** ‏`01 - العنوان.mp3`، وTRCK «k/total».
- **التشغيل:** لا يبدأ تلقائيًا. يُسجَّل في `run_audio_worker`، ولا يتسلسل من `GenerateSpeechV2Workflow`، وهذا مُختبر.

## 6. الاختبارات

**commit الاختبارات أولًا (`1d491a3`):**
- خمسة ملفات فشلت عند الجمع: `ModuleNotFoundError: b2c_episodes.figures`، و`b2c_orchestration.episode_mp3`، و`b2c_speech.audio.stream`، وغيرها.
- ملف الـ gateway فشل بـ `AttributeError` على `complete_vision`.
- اختبارات العقد فشلت على `SCHEMA_VERSION` والأنواع الجديدة.

**أُضيفت لاحقًا، بعد تنفيذ الكود (لم تفشل أولًا):**
- اختبارات الجولة الثانية في `tests/unit/episodes/test_figure_descriptions.py`، وهي:
  - الرموز بالأرقام؛
  - البرومبت بلا عدّ؛
  - الإسبانية؛
  - العمود الفارغ؛
  - قسمة السطر.
- `tests/unit/orchestration/test_figure_activities.py`.

**عُدّل اختبار قائم بسبب سلوك مقصود:**
- `test_worker_wiring.py`: تسجيل workflow الحلقة ونشاطها على طابور الصوت.
- `test_episode_stream.py`: مسارات بحواف 40 ms، لأن المسارات تُقص قبل التجميع (D-042). كان الـ fixture الأول بحواف 0.2/0.3 ث، فتجاوز مجموع الوقفة 1.5 ث.

| الملف | اختبارات |
|---|---:|
| `tests/contract/test_figure_descriptions.py` | 9 |
| `tests/unit/episodes/test_figure_descriptions.py` | 16 |
| `tests/unit/episodes/test_episode_figures.py` | 3 |
| `tests/unit/speech/test_speech_figures.py` | 6 |
| `tests/unit/speech/test_episode_stream.py` | 12 |
| `tests/unit/llm_gateway/test_complete_vision.py` | 4 |
| `tests/unit/orchestration/test_episode_mp3.py` | 3 |
| `tests/unit/orchestration/test_figure_activities.py` | 2 |
| **المجموع الجديد** | **55** |

**`make check` (بعد آخر تعديل):**
- ruff: «All checks passed!».
- import-linter: «Contracts: 9 kept, 0 broken».
- contract: ‏**152 passed**.
- unit: ‏**2391 passed، 5 skipped، 0 failed** (326 ث).

## 7. ما لم يُنفذ أو بقي مفتوحًا (UNVERIFIED)

1. **لا صوت حقيقي:** لم يُركَّب TTS على أوصاف حقيقية، ولم يُسمع شيء. لم يُجرَّب مستمع كفيف.
2. **20 من 90 شكلًا في SAT تُقرأ بعنوانها فقط.**
   - السبب الأكبر: 64 شكلًا بلا عنوان مطبوع في الاستخراج (عيب D-043)، ونص OCR مشوّش.
   - تحسين الاستخراج، أو الوصف بالصورة للجميع، يرفع النسبة.
   - الصورة مع Gemma تستغرق نحو 12 دقيقة للشكل خلف طابور الترجمة، أي نحو 18 ساعة لـ 90 شكلًا ما دام الطابور مشغولًا. لم تُشغَّل على الكتاب كله.
3. **عدد العناصر:** إرشاد DIAGRAM («five smaller circles») يتعارض مع قاعدة «لا رقم غير موجود». قرر المالك (2026-10-26) العدّ الحتمي بالكود، ونُفّذ في D-049 (eval/reports/figure-counts.md).
4. **تكرار وصف الكتاب المطبوع:** وصف SAT المطبوع يُقرأ في نهاية الفصل (D-036 (1)). صار الآن يُستعمل أيضًا دليلًا، أو يُقرأ حرفيًا في الرجوع الآمن. منع التكرار لم يُنفذ.
5. **الحروف اللاتينية في أوصاف الجداول** (رموز OCR مثل P/N) قد تُسقط بوابة الكلام. لم يُتحقق منها على وثيقة كاملة.
6. **التسجيل في العامل:**
   - activities الأشكال، مثل activities الحلقات، غير مسجلة في عامل. تعمل بسكربت.
   - صورة العامل لم يُعد بناؤها. فيها pypdfium2 لقص الصور، لكنها لا تحمل الكود الجديد.
   - عامل الصوت الجاري لا يحمل `stream.py` ولا الـ workflow الجديد.
7. **الجهارة النهائية:** ‎-16 مقابل ‎-19 LUFS للأحادي، وهو قرار المالك Q2. الجهارة بعد الترميز لم تُقس، لأنه لا يوجد مفكّك MP3.
8. **الترجمة:** الأمثلة تُرجمت بـ deepseek (تطوير، D-029)، لا بمحرك Gemma الإنتاجي.
