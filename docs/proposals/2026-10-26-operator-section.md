# قسم «المراحل والتقدم» في تطبيق Android — 2026-10-26

- **الحالة:** منفَّذ في الفرع `feat/operator-section` (من `hardening` عند `5988f12`). الـ API الجديد **لم يُنشر** بعد؛ النشر بعد الدمج.
- **طلب المالك (2026-10-26):** أن يرى في التطبيق الجديد المراحل والتقدم بنفس تفاصيل آخر نسخة من تطبيق المشغّل القديم (0.5.10 «restored-ui»)، لكن بطريقة أفضل وأكثر انسيابية، حتى لو لم يكتمل شيء.
- **المحتوى:** §1 جرد القديم من الكود، §2 خريطة التحويل، §3 الـ API ‏`/v3/operator`، §4 القسم في التطبيق، §5 الأفعال المؤجلة، §6 الاختبارات، §7 البناء والتسليم والمحاكي، §8 ما لم يُنفَّذ.

## 1. جرد التطبيق القديم (operator-app 0.5.10/0.5.11)

### 1.0 المصدر والمنهج

- **الكود المقروء:** `android/operator-app` بنسخة `0.5.11` (`versionCode 28`، ‏`minSdk 24`، ‏`targetSdk 35`). الملفات: `MainActivity.java` (777 سطرًا، قُرئ كاملًا بعد تقطيع الأسطر الطويلة)، و`TokenVault.java`، و`EndpointRouting.java`، و`BookIdentity.java`، و`AndroidManifest.xml`، و`app/build.gradle`.
- **الدليل:** `Lnn` يعني السطر nn في `MainActivity.java`، ومعه عبارة من الكود. الأسطر طويلة (السطر الواحد فيه دوال كثيرة)، فالعبارة هي ما يحدد الموضع.
- **أسماء الحالات المعروضة:** دالة `statusName` (L339) تحوّل حالة Temporal إلى نص عربي، وهي مستعملة في كل الشاشات:

  | حالة Temporal | النص المعروض |
  |---|---|
  | `RUNNING` | قيد التنفيذ |
  | `PAUSED` | متوقفة مؤقتًا |
  | `PAUSE_REQUESTED` | بانتظار نقطة الحفظ للإيقاف المؤقت |
  | `COMPLETED` | مكتملة |
  | `FAILED` | فشلت |
  | `CANCELED` | أُلغيت |
  | `TERMINATED` | أُوقفت |
  | `TIMED_OUT` | انتهت المهلة |
  | `CONTINUED_AS_NEW` | مستمرة |
  | غير ذلك | النص الخام كما هو، أو «غير معروف» إن كان فارغًا |

#### مقارنة الـ APK المسلَّم بالمصدر

**الـ APK:** `outputs/Book2Course-operator-v0.5.10-restored-ui-armv7-2026-10-14.apk`. استخرجتُ `classes.dex` و`classes2.dex` إلى scratchpad. ثم قارنتُ جدول السلاسل في الـ dex بكل سلسلة حرفية في المصدر، وأسماء الدوال في الـ dex (`dexdump`) بأسماء الدوال في المصدر.

| البند | النتيجة |
|---|---|
| هوية الـ APK (`aapt dump badging`) | `versionName 0.5.10`، و`versionCode 27`، و`debuggable=true`. التسمية `B2C تجربة المراحل`، و`allowBackup=false`، والاتجاه `portrait`، وإذن `INTERNET` فقط. العنوان الافتراضي `https://desktop-3khe91s.tail7385b1.ts.net`. هذا كله يطابق المصدر عدا رقمي الإصدار |
| السلاسل ذات الدلالة (عربية أو تبدأ بـ `/`) | 869 في المصدر و859 في الـ dex. الفرق محصور في البنود الثلاثة التالية |
| في الـ dex لا في المصدر | `/outline/approve-system` وحده. هذا فرق commit ‏`5998b2b` المعروف: في 0.5.10 تستدعي `ensureOutlineOnly()` و`ensureRag()` المسار `POST /v1/ingestions/{id}/outline/approve-system` آليًا، وترسل `target_tokens` و`overlap_ratio` من حقلي «حجم المقطع» و«نسبة التداخل»، و`supplementary_workflow_ids` |
| في المصدر لا في الـ dex: فرق `5998b2b` | رسالة `HUMAN_OUTLINE_APPROVAL_REQUIRED` (L646) |
| في المصدر لا في الـ dex: **فروق غير موثقة** | خمس ميزات موجودة في المصدر منذ commit اللقطة `440f50a`، أي قبل `5998b2b`. **فهي ليست في الـ APK المسلَّم:** (1) فحص قدرات الخادم عبر `GET /openapi.json` بالدوال `refreshServerCapabilitiesBlocking` و`schemaFieldAvailable` و`serverRouteAvailable` (L773–775) ورسائلها الثلاث. في 0.5.10 ترسل `applyQAOptions` الحقلين `include_lesson_summaries` و`review_assessment_scopes` دائمًا دون فحص، ويستدعي `materializeNext` المسار `/materialize` دون فحص. (2) عدادات «تصحيح العربية · مقبول · مرفوض · الجاري تصحيحه» في `translationTrackDetail` (L118). (3) سطر «مقاطع الصوت الجاري» من `renderer_progress` (L184). (4) `applySavedAudioFolder` (L776)، أي عرض `tracks` المحفوظة حين لا توجد `workflows`. (5) نص مسار `/v1/courses/{course_id}/memberships/{book_id}/materialize` الذي يُبحث عنه في الـ OpenAPI (L315) |
| أسماء الدوال | كل دوال `MainActivity` في المصدر موجودة في الـ dex، عدا الأربع المذكورة: `applySavedAudioFolder` و`refreshServerCapabilitiesBlocking` و`schemaFieldAvailable` و`serverRouteAvailable` |
| ما لم يُتحقق منه | المقارنة على مستوى السلاسل وأسماء الدوال فقط، لا على منطق كل تعليمة bytecode. قد توجد فروق منطقية لا تغيّر أي سلسلة نصية. ولم يُشغَّل الـ APK على جهاز |

**الخلاصة:** المصدر = 0.5.10 + ‏`5998b2b` + خمس إضافات صغيرة (البنود 1–5 أعلاه)، وهذه الإضافات لم تُبنَ في أي APK مسلَّم. **رقم 0.5.11 لم يُسلَّم APK قط.** الجرد التالي يصف المصدر، ويشير إلى الفرق مع 0.5.10 حيث يوجد.

### 1.1 الهيكل والتنقل

| العنصر | الوصف | الدليل |
|---|---|---|
| Activity واحدة | `MainActivity extends Activity`. الواجهة كلها مبنية برمجيًا، بلا `res/layout` | L3، L19–27 |
| الشريط السفلي | 5 أزرار: المكتبة، وإعداد المراحل، والمتابعة، والمخرجات، والإعدادات. التبويب المختار بلون `blue` وخلفية `0xffe8eee5` وخط عريض | L22 (`names={"المكتبة","إعداد المراحل","المتابعة","المخرجات","الإعدادات"}`)، وL95 |
| بناء الشاشات | تُبنى الخمس مرة واحدة عند الإقلاع (`pages[i]=buildScreen(i)`). التبديل يستبدل الـ View داخل `FrameLayout`. لا back stack | L25، وL95 (`screenFrame.removeAllViews()`) |
| تبويب «المخرجات» | شاشتان في صفحة واحدة: الصوت ثم النتائج | L30 (`else if(which==3){buildAudioScreen();buildResultsScreen();}`) |
| أثر جانبي لـ `renderScreen` | يحدّث `courseLabel`. ويحمّل قائمة النماذج تلقائيًا عند فتح «الإعدادات» إن وُجد رمز | L95 (`if(index==4&&!modelCatalogLoaded…)loadModelCatalog()`) |
| سجل التشغيل | الحقل `log` (TextView) **لا يُنشأ أبدًا**. فكل `add()` يكتب في `SharedPreferences["log"]` فقط (آخر 10,000 حرف)، ولا يُرى إلا عبر «عرض تفاصيل التشغيل» | L10، وL753 (`p.edit().putString("log",…)`) |

### 1.2 الشاشات: الحقول والقيم والأزرار

#### (أ) «المكتبة» — `buildLibraryScreen` ‏(L34–42)

| العنصر | النوع | ما يعرضه أو ينفّذه فعلًا | الدليل |
|---|---|---|---|
| ترويسة | نص | «Book2Course» و«حوّل كتابك إلى مادة تقرأها وتسمعها وتتعلم منها» | L35 |
| «مكتبتي» | عنوان | تحته: «كل كتبك متاحة هنا. اختر مقررًا…» | L36 |
| معرّف المقرر، ومعرّف الكتاب | EditText مخفيان (`GONE`) | يحملان `course_id` و`book_id` الحاليين. يبقى عنوانا الحقلين ظاهرين لأن `edit()` تضيف label قبل الحقل | L36 (`course.setVisibility(View.GONE)`)، وL134 |
| عنوان الكتاب | EditText ظاهر | قيمته من prefs `title`، ويُرسل عند الرفع وحفظ الاسم | L36 |
| `courseLabel` | نص | «المقرر: <course_name>» | L36 |
| «اختيار مقرر» | زر | `manageCourses`: ‏`GET /v1/courses`، ثم حوار «اختيار مقرر» | L36، وL268 |
| «مقرر جديد» | زر | `editCourseName(true)`: حوار، ثم `POST /v1/courses`، ثم `switchCourse` | L36، وL267 |
| «كتب هذا المقرر» | زر | `showCourseMembers`: ‏`GET /v1/courses/{cid}/memberships` | L36، وL264 |
| «تعديل اسم المقرر» | زر | `editCourseName(false)`: ‏`PATCH /v1/courses/{cid}`، ويحفظ `course_name` في prefs | L36، وL267 |
| «حفظ اسم الكتاب المختار» | زر | `saveBookName`: ‏`PUT /v1/courses/{cid}/books/{bid}/name`، ويحدّث `title` في prefs | L37، وL756–761 |
| «كل الكتب المحفوظة» | زر | `loadBooks`: ‏`GET /openapi.json`، ثم `GET /v1/library/books`، ثم حوار «كل الكتب» | L38، وL255 |
| «رفع PDF للمقرر» | زر | `choose`: يفتح منتقي ملفات `application/pdf` فقط. **لا يرفع شيئًا.** عند الاختيار (`onActivityResult`، ‏requestCode 1): يُولّد `book-<8 hex>`، ويُمسح من prefs `i/x/q/a/tr/btr/mp3/qa_history/qa_scope_map`، وتصير `qa_lesson_ids=[]`. الرفع الفعلي يحدث عند «ابدأ حتى الناتج المختار» | L38، وL203، وL506 |
| «تنسيق الكتب المحفوظة بهذا المقرر» | زر | `loadCourseBooks`: ‏`GET /v1/courses/{cid}/books`، ثم حوار «مكتبة الكتب» (اختيار الأساسي والمساندة) | L38، وL256 |
| «المراحل وحالة كتاب محفوظ» | زر | ينتقل إلى «المتابعة»، ثم `loadBooksForMonitor` (`GET /v1/courses/{cid}/books`) | L38، وL271 |
| `file` | نص | «لم يتم اختيار كتاب»، أو «✓ كتاب محفوظ ومحدد»، أو «كتاب محفوظ على الخادم»، أو «تم اختيار n كتب من المكتبة»، أو اسم الـ PDF | L38، وL333، وL334، وL506 |
| `uploadStatus` و`uploadBar` | نص + ProgressBar ‏(0–100) | «لم يبدأ رفع PDF بعد»، ثم «جارٍ رفع PDF إلى الخادم · n%»، ثم «اكتمل رفع PDF · بدأ تجهيز OCR». يحدّثهما أيضًا `renderWorkflow` للمرحلة 0: «اكتمل رفع PDF وOCR · …» | L38، وL205، وL243–250، وL189 |
| ملاحظة | نص | «عند اختيار PDF جديد يُنشأ له معرّف مستقل لتظل حالة OCR وRAG خاصة بهذا الملف.» | L38 |
| `bookRoleSummary` | نص | «الدور المحدد: كتاب مساعد…» أو «…كتاب أساسي…» | L204 |
| `compositionSummary` | نص | «الأساسي: <title> · الكتب المساندة: n» | L40، وL333، وL334 |
| `role` | Spinner | «دور الكتاب في هذا الكورس: أساسي / مساعد». يحفظ في prefs `course-role:<cid>:<bid>` القيمة `spine` أو `supplementary`، ويُرسل `role` عند الرفع | L39، وL237 |
| `lang` | Spinner | «لغة الكتاب: English / العربية». يُرسل `ocr_language` عند الرفع. ويُرسل `source_language` لخطة المعالجة إن لمسه المستخدم. وهو احتياط لـ `source_language` في الترجمة | L39، وL237، وL571، وL683 |

#### (ب) «إعداد المراحل» — `buildSetupScreen` ‏(L43–53)

| العنصر | النوع | الخيارات أو القيمة، وما يحدث | الدليل |
|---|---|---|---|
| `ocr` | Spinner | «OCR حسب إعداد الخادم / RapidOCR / Surya / OpenRouter Vision». يصير `ocr_engine` عند الرفع (`""/rapidocr/surya/openrouter`) | L43، وL239 |
| `target` | Spinner | «نص OCR / الفهرسة وRAG / الدروس والأسئلة / الترجمة / الصوت MP3». يُحفظ في prefs `target` | L44–46 |
| `contentKind` | Spinner | «محتوى الكتاب / محتوى الكورس وأسئلته». يُحفظ في `content_kind` | L44 |
| `outputLanguage` | Spinner | «English / العربية». يُحفظ في `output_language` | L44 |
| `audioMode` | Spinner | «كورس: الدرس وأسئلته / الكتاب دروسًا بلا أسئلة / الكتاب كاملًا في ملف واحد». يُحفظ في `audio_mode` | L44 |
| `plan` | نص | «المسار المطلوب: رفع PDF ← OCR ← …» بحسب الاختيار، ومعه «الخادم يتحقق من لغة المصدر بعد OCR، ويتخطى الترجمة عند تطابق اللغة.» | L509–518 |
| `summaryToggle` | CheckBox | «ملخص مستقل موثق لكل درس (اختياري)». يُحفظ في `include_lesson_summaries`. يُعطَّل إن لم يذكره الـ OpenAPI | L49، وL775 |
| `enrichmentToggle` | CheckBox | «إثراء الدروس: مصطلحات وأسئلة مراجعة إضافية». يُحفظ في `include_lesson_enrichment` | L49 |
| `assessmentToggle` | CheckBox | «اختبارات مراجعة: Midterm 1 وTerm 1 وMidterm 2 وTerm 2 وFinal». يُحفظ في `include_review_assessments` و`review_assessment_scopes` | L49 |
| «اختيار اختبارات المراجعة المطلوبة» | زر | حوار اختيار متعدد، ثم prefs `review_assessment_scopes` | L50، وL59 |
| `qaBatchSummary` | نص | «النطاق: كل الدروس» أو «النطاق المحدد: n درسًا» | L51، وL444 |
| «اختيار الدروس» | زر | `chooseQALessonBatch`: ‏`GET /v1/ingestions/{id}/outline`، ثم حوار، ثم prefs `qa_lesson_ids` | L51، وL447–452 |
| «كل الدروس» | زر | `qa_lesson_ids=[]` | L51، وL445 |
| «حجم المقطع (300–800)» = 500، و«نسبة التداخل (0–0.49)» = 0.12 | EditText | **لا تُرسل في المصدر الحالي.** كانت تُرسل إلى `approve-system` في 0.5.10 | L51، و`git show 5998b2b` |
| `retrieval` | Spinner | «Lexical / Hybrid»، ويصير `retrieval_mode` في طلب الأسئلة | L51، وL666 |
| `lq` = 5، و`gq` = 5، و`cq` = 10 | EditText | عدد الأسئلة: لكل درس، ولمجموعة دروس، وللاختبار النهائي. تُرسل في `qa/generate` | L51، وL666 |
| `tokens` = 4096 | EditText | `max_tokens` للأسئلة. وللترجمة `max(8192, القيمة)` | L51، وL666، وL683 |
| «OCR · …» و«المخطط · …» حتى «الصوت · …» (7) | أزرار | تعرض `model_default:<stage>` أو «إعداد الخادم». الضغط ينقل إلى «الإعدادات»، ويختار المرحلة، ويحمّل النماذج | L52، وL90 |
| «ابدأ حتى الناتج المختار» | زر رئيسي | `startSelectedPath` ← `runSelectedPath`. انظر 1.3 | L53، وL550–630 |

#### (ج) «المتابعة» — `buildStagesScreen` ‏(L54–58) و`addStageCard` ‏(L97–111)

| العنصر | النوع | ما يعرضه أو ينفّذه | الدليل |
|---|---|---|---|
| «تقدم المعالجة» | عنوان | «اختر كتابًا ثم تابع كل مرحلة واستأنفها من هنا.» | L54 |
| `stageBookLabel` | نص | «الكتاب الحالي: <title> · <book_id>» | L54، وL334 |
| «اختيار كتاب للمراحل والمتابعة» | زر | `loadBooksForMonitor`، ثم حوار «اختيار كتاب للمتابعة» | L54، وL271 |
| `stageConnectionStatus` | نص | «الاتصال لم يُفحص»، أو «متصل بالخادم · المنفذ 443/8443»، أو «تعذر الاتصال…» | L54، وL194، وL196 |
| «فحص الاتصال» | زر | `checkConnection` ← `readyBase(true)` ← `GET /health` على 443، ثم 8443 | L54، وL200 |
| 5 بطاقات مراحل | بطاقة | «رفع الكتاب وOCR»، و«المخطط والفهرسة RAG»، و«الدروس والأسئلة»، و«الترجمة»، و«إنتاج الصوت» | L54 |
| في كل بطاقة | عنوان، وحالة، ونسبة، وشريط، وتفاصيل | الحالة الافتراضية «لم تبدأ». النسبة «0%»، أو «النسبة غير متاحة» (والشريط indeterminate إن كانت الحالة «قيد التنفيذ»). التفاصيل «بانتظار اختيار الكتاب أو بدء المسار» | L99–101، وL140–142 |
| «بدء / متابعة هذه المرحلة» (×5) | زر | `startStage(i)`: يضبط `target=i` (و`contentKind=course` للأسئلة)، ثم `startSelectedPath`. **يشغّل كل ما يسبق المرحلة** | L102، وL520–525 |
| «إيقاف مؤقت بعد الحفظ» و«استئناف من آخر نقطة حفظ» (البطاقات 0 و1 و3 و4) | زران مخفيان | يظهران بحسب `GET /v1/workflows/{id}/control`. ينفّذان `POST /v1/workflows/{id}/pause` أو `/resume` (بلا body) | L103، وL168–179 |
| «استئناف من البيانات المحفوظة» (البطاقات 0 و1 و4) | زر مخفي | يظهر إن كانت الحالة نهائية فاشلة و`resume-capability.resume_available` صحيحة. نصه: «…باستخدام المراحل المحفوظة»، أو «…الفهرسة المحفوظة»، أو «…مع الاحتفاظ بملفات الصوت المنجزة». ينفّذ `POST /v1/workflows/{id}/resume` مع `book_id`، فيبدأ workflow جديدًا ويحفظ معرّفه | L104، وL146–157 |
| «عرض النص المحفوظ» (بطاقة 0) | زر | `previewText`، ثم حوار «الكتاب · OCR» | L105، وL386 |
| «عرض الفهرسة المحفوظة» (بطاقة 1) | زر | `GET /v1/indexings/{id}/chunks`، ثم حوار عرض | L106، وL431–441 |
| أزرار الأسئلة (بطاقة 2) | 3 أزرار مخفية + زر | «إيقاف بعد نقطة الحفظ» (`POST /v1/course-qa/{id}/pause`)، و«استئناف من نقاط الحفظ» (`POST …/resume`)، و«المتابعة بالجزء المحفوظ» (`continueFromPartialQA`)، و«عرض الدروس والأسئلة المحفوظة». الظهور بحسب الحالة: RUNNING أو PAUSED أو نهائية | L107، وL158 |
| «عرض الملفات الصوتية المنجزة» (بطاقة 4) | زر | `loadAudioTracks`، ثم الانتقال إلى «المخرجات» | L108، وL412 |
| بطاقة الترجمة: مساران | عنوان، ونص، وشريط، و3 أزرار لكل مسار | «متن الكتاب الأصلي · مقسّمًا للدروس» (`bookTranslation`) و«ملحق الدرس · الملخص والأسئلة والإجابات» (`translation`). الزر المخفي «استئناف المقاطع غير المحفوظة» أو «فحص إتاحة الاستئناف»، و«عرض مقاطع المتن/الملحق وتصحيحها»، و«مراجعة آخر إعادة ترجمة» | L109، وL112، وL122 |
| «تحديث الحالات» | زر | `showProgress` ← `refreshStatusesBlocking`، ويفعّل `progressWatching` | L54، وL341 |
| «نسخ معرّفات التشغيل» | زر | ينسخ `ids()` إلى الحافظة: رفع، وفهرسة، وأسئلة، وترجمة الملحق، وترجمة نص الكتاب، وصوت | L54، وL751–752 |
| «إيقاف بعد حفظ الدرس» و«استئناف من آخر حفظ» | زران | `pauseQA` و`resumeQA` (تكرار لأزرار البطاقة 2) | L55، وL736–748 |
| «استخدام المنجز ومتابعة الناتج المختار» | زر | `continueFromPartialQA`: إن كانت الأسئلة `PAUSED` يضبط `continue_partial_qa=true` مؤقتًا، ثم يشغّل `runSelectedPath` | L56، وL749 |
| `state` | نص | معرّفات التشغيل الستة (`ids()`) | L57، وL384 |

**قيم البطاقات (كيف تُحسب):**

| القيمة | المصدر | الدليل |
|---|---|---|
| الحالة | `statusName(status)` من `GET /v1/{ingestions,indexings,course-qa,translations,audio}/{id}`. وقيم محلية إضافية: «لم تبدأ»، و«تعذر التحديث»، و«المخطط معتمد» (فهرسة غائبة مع مخطط معتمد)، و«محفوظ جزئيًا» (ترجمة مكتملة بفجوات)، و«قيد التنفيذ» (يضعها التطبيق قبل الانتظار) | L181–189، وL379، وL381 |
| النسبة | 100 عند `COMPLETED` (عدا ترجمة فيها فجوات). وإلا أول زوج متوفر من: `processed_scopes/total_scopes`، و`completed_scopes/total_scopes`، و`completed_lessons/total_lessons`، و`completed_pages/total_pages`، و`processed_pages/total_pages`، و`indexed_chunks/total_chunks`، و`completed_segments/total_segments`، و`completed_tracks/track_count` | L143، وL189 |
| التفاصيل | `progress.message`، و«حاليًا: <scope>»، و«الأسئلة المحفوظة: n / هدف»، و«النطاقات الناجحة/الفاشلة/الإجمالي»، و«الدروس المغطاة»، و«الدروس المكتملة»، و«الصفحات: n / m». للصوت: «الدرس الجاري… مقاطع محفوظة/متبقية/فاشلة»، و«مقاطع الصوت الجاري» (المصدر فقط)، و«ملفات محفوظة/متبقية/فاشلة». و«الوقت: n د». ومن النتيجة: «صفحات الكتاب» و«المقاطع المحفوظة» (OCR)، و«المقاطع المفهرسة» (RAG). و«سبب التوقف: …». وإن خلت التفاصيل: «اكتملت المرحلة وحُفظت نتيجتها.» أو «العمل مستمر؛ يجري تحديث الحالة تلقائيًا.» | L184–188 |
| تفاصيل مسار الترجمة | «ناجح d/t»، و«جارٍ»، و«مؤجل»، و«فاشل مؤكد»، و«غير محفوظ / يحتاج استئناف»، و«الوحدة b/B». ثم «تصحيح العربية p/t · مقبول · مرفوض · الجاري تصحيحه» (المصدر فقط)، و`current_unit`، و`model_id`، و«الخطأ» أو «آخر خطأ». الحالة الخاصة: «محفوظ جزئيًا · يحتاج استئناف» و«لا يوجد تقدم حديث؛ يعاد الفحص» | L116–118 |
| مصدر معرّفات المراحل | سجل الخادم `GET /v1/courses/{cid}/books/{bid}/progress` (`stages.ingest/index/qa/translation/book_translation/audio`، و`outline.approved_outline_ref`). تُكتب نسخة منه محليًا | L343–347، وL369–375 |

#### (د) «المخرجات» — الصوت `buildAudioScreen` ‏(L63) والنتائج `buildResultsScreen` ‏(L61)

| العنصر | النوع | ما يعرضه أو ينفّذه | الدليل |
|---|---|---|---|
| «الصوت» | عنوان | «استمع للدرس، غيّر السرعة، واحفظ ملف MP3 على جهازك.» | L63 |
| `audioListStatus` | نص | «اختر مقررًا ثم حدّث…»، أو «مجلد: <name> · ملفات محفوظة: n · <status>»، أو «لا توجد ملفات صوت محفوظة بعد»، أو «اختر نسخة إنتاج واحدة…» | L63، وL413–414 |
| `audioSelection` | Spinner | الملفات: «<display_filename> · <title>» | L414 |
| «مجلدات المقررات» | زر | `manageCourses` (تكرار لزر المكتبة) | L63 |
| «تحديث قائمة الصوت» | زر | `GET /v1/courses/{cid}/audio`. إن وُجدت عدة workflows يظهر حوار اختيار «إنتاج n · status · m ملف محفوظ» | L63، وL413 |
| «تنزيل الملف المحدد» | زر | DownloadManager لمسار المقطع (`/v1/audio/{wf}/tracks/{id}` أو `/file` أو `download_path` تاريخي) إلى Downloads | L63، وL420–421، وL430 |
| «تنزيل المقرر ZIP» | زر | `GET /v1/audio/{wf}` للتحقق (مكتمل، ولا ملفات فاشلة أو ناقصة)، ثم DownloadManager لـ `zip_url` (`/v1/courses/{cid}/audio/zip?workflow_id=`) | L63، وL415 |
| «إعادة صوت الدرس المحدد فقط» | زر | `regenerateSelectedLesson`: تحقق بعدة GETs (progress، و`outline`، و`ingestions`، و`processing-plan`، و`course-qa`، و`translations`)، ثم حواران، ثم `POST /v1/courses/{cid}/audio/regenerate` | L63، وL417–418 |
| `audioSeek` | SeekBar | موضع التشغيل، ويُحدَّث كل 500ms | L63، وL424 |
| «سرعة التشغيل» | Spinner | «0.75× / 1.0× / 1.25× / 1.5× / 1.75× / 2.0×» (الافتراضي 1.0×) ← `PlaybackParams` | L63، وL422 |
| «السابق»، و«تشغيل / إيقاف»، و«التالي»، و«إيقاف الصوت» | أزرار | `MediaPlayer` يبث `base()+path` بترويسة `Authorization`. يستأنف من prefs `audio_pos:<wf>:<idx>`، ويحفظ الموضع | L63، وL423–426 |
| «النتائج والقراءة» | عنوان | «اعرض الكتاب والنص المستخرج، أو نزّل ناتج مرحلة مكتملة.» | L61 |
| «كل النتائج المحفوظة للمقرر» | زر | `GET /v1/courses/{cid}/results`، ثم سلسلة حوارات (ملفات، ثم معاينة أو تنزيل أو وحدات) | L61، وL483–488 |
| «تنزيل كل النتائج ZIP» | زر | `POST /v1/courses/{cid}/results/export` بـ `{"result_ids":[]}` إلى ملف مؤقت، ثم `ACTION_CREATE_DOCUMENT` للحفظ | L61، وL489 |
| `resultStage` | Spinner | «OCR والنص المستخرج / RAG والفهرسة / الدروس والأسئلة / الترجمة / الصوت» | L61 |
| «فتح PDF مع الزوم» | زر | `GET /v1/courses/{cid}/books/{bid}/source` إلى `cache/book2course-preview.pdf`، ثم `PdfRenderer`، ويحفظ prefs `reader_page:<bid>` | L61، وL393–404 |
| «عرض نص OCR» | زر | `GET /v1/ingestions/{id}/text?offset&limit=100` | L61، وL386–391 |
| «عرض ناتج المرحلة» | زر | بحسب `resultStage`: OCR يعرض النص. RAG: ‏`/chunks`. الأسئلة: «الأسئلة حسب الدرس». الترجمة: `GET /v1/translations/{translation}` أو `/checkpoints`. **ملاحظة:** يستعمل معرّف الملحق `translation` لا المتن `bookTranslation`. الصوت: `/manifest`. العرض بحد 120,000 حرف | L61، وL431–441 |
| «تنزيل ناتج المرحلة» | زر | يجمع الناتج كاملًا ويحفظه عبر `ACTION_CREATE_DOCUMENT`: `book2course-ocr.txt` (كل الصفحات بدفعات 100)، و`-rag-chunks.json`، و`-questions.json`، و`-translation.json`. وللصوت: حوار مقاطع أو `/file` | L61، وL490–501 |
| «الأسئلة حسب الدرس» | زر | يجمع `qa_history` مع `stages.qa` من progress. ثم `GET /v1/course-qa/{id}` أو `/checkpoints`، ويدمج الدروس، ويعرض حوار «الدروس · اختر درسًا» | L61، وL469–479 |
| `readerPage` و`readerBody` | نص | ثابتان لا يتحدثان أبدًا («اختر كتابًا لعرض محتواه.») | L61 |

#### (هـ) «الإعدادات» — `buildSettingsScreen` و`buildModelSettings` ‏(L66–79)

| العنصر | النوع | ما يعرضه أو ينفّذه | الدليل |
|---|---|---|---|
| «عنوان خادم Book2Course» | EditText | القيمة `configuredBase()` من prefs `api_base_url` أو `BuildConfig.API_BASE_URL`. يقبل HTTPS فقط، بلا مسار ولا بيانات دخول | L66، وL191، و`EndpointRouting.normalize` |
| «حفظ عنوان الخادم» | زر | `saveEndpoint`. إن تغيّر الـ host يظهر حوار «تغيير الخادم» ويُحذف `api_token_ciphertext`. ثم يحفظ `api_base_url` ويصفّر المنفذ النشط | L66، وL193 |
| ملاحظة | نص | «يفحص التطبيق قناتي HTTPS على المنفذين 443 و8443…» | L66 |
| «فحص الاتصال بالخادم» | زر | `GET /health` على البدائل (`EndpointRouting.candidates`: 8443 للـ host الموثوق فقط) | L66، وL195–200 |
| `connectionStatus` | نص | كما في «المتابعة» (الدالة نفسها) | L66، وL194 |
| `modelStage` | Spinner | «استخراج OCR / المخطط / RAG والتضمين / الأسئلة / إثراء الدروس (اختياري) / الترجمة / الصوت» | L70 |
| `modelChoice` | Spinner | المزودون المختبرون فقط (`tested` و`limited_test`) لهذه المرحلة: «label · نموذج مرحلة/تضمين/محرك RAG · محلي/خارجي · اختبار محدود». أو «اضغط تحديث النماذج…» أو «لا توجد خيارات مختبرة…» | L71، وL83 |
| `modelStateLabel` | نص | «بدء التجهيز»، أو «تحميل في الذاكرة»، أو «جاهز الآن»، أو «فشل؛ يمكن إعادة المحاولة»، أو «يلزم إعداد تشغيل على الخادم»، أو «الخدمة غير متاحة»، أو «غير محمّل / لم يُفحص». ثم الرسالة، ثم «متاح للمهمات الجديدة» أو «لم يُوصَل…» | L71، وL88 |
| `modelCostLabel` | نص | «مجاني وفق آخر تحقق مسجل»، أو «مدفوع»، أو «التكلفة… لم تُتحقق». ومعها «إدخال/إخراج $x / مليون رمز · تحقق: <تاريخ>» | L71، وL86 |
| `modelEvidenceLabel` | نص | «مختبر»، أو «مختبر على عينة محدودة»، أو «لا توجد نتيجة اختبار مسجلة»، ومعها الملاحظة | L72، وL88 |
| «تحديث النماذج» | زر | `GET /v1/model-providers`، ويحفظ `model_default:<stage>` و`translation_provider_id` في prefs | L72، وL82، وL89 |
| «فحص الجاهزية» أو «تجهيز وفحص» | زر | `POST /v1/model-providers/{id}/activate`، ثم `watchModel` (`GET /v1/model-providers/{id}` كل 2 ث، حتى 600 مرة) | L72، وL93–94 |
| «ربط أو تغيير مفتاح API للمزود المحدد» | زر (للمزود الخارجي فقط) | حوار «إعداد مزود خارجي»، ثم `PUT /v1/model-providers/{id}/credentials` ‏`{model,key}` | L73، وL80 |
| «اعتماد للمهمات الجديدة» | زر | `PUT /v1/model-providers/{id}/default/{stage}` | L75، وL93 |
| «معلومات التطبيق» | نص | «يدعم هذا الإصدار Android 7 وما بعده، ونسخة ARMv7.» | L66 |
| «عرض تفاصيل التشغيل» | زر | حوار بنص prefs `log` | L66، وL202 |
| رمز الوصول | — | **لا حقل له في الشاشة.** يُطلب بحوار «إعداد اتصال آمن» عند أول طلب، أو بعد 401، أو بعد تغيير الـ host | L199، وL225 |

### 1.3 مسار «ابدأ حتى الناتج المختار» (`runSelectedPath`، L562–630)

| الخطوة | ما يحدث | الدليل |
|---|---|---|
| 1. الرفع | إن اختير PDF جديد: `POST /v1/books` (multipart: ‏`metadata_json` + `file`)، فيُخزَّن الملف ويبدأ workflow الاستخراج. يستبدل `book_id` بما يعيده الخادم، ويصفّر بقية المعرّفات | L563، وL233–254 |
| 2. حالة الاستخراج | `refreshStatusesBlocking`، ثم `resumeTerminalStageBlocking(0)` (يبدأ workflow جديدًا إن فشل ويتيح الاستئناف)، ثم `waitFor(/v1/ingestions/…)` كل 5 ث، حتى 6 ساعات | L564–567، وL148–155، وL709–730 |
| 3. الخطة | `GET /v1/ingestions/{id}/processing-plan?…`. إن كان `requires_user_input` يتوقف. ويحدد الحاجة للترجمة من `stages[].action ∈ {run, reuse}` | L568–578، وL348 |
| 4. المخطط والفهرسة | يحتاج `approved_outline_ref` في سجل الكتاب. وإلا يتوقف برسالة «المخطط يحتاج اعتمادًا بشريًا…». **0.5.10:** ‏`POST …/outline/approve-system` تلقائيًا، فتبدأ الفهرسة. ثم `waitFor(/v1/indexings/…)` | L644–655 |
| 5. الأسئلة | `POST /v1/courses/{cid}/qa/generate` (المخطط المعتمد، و`index_workflow_id`، والأعداد، و`retrieval_mode`، و`lesson_ids`، وخيارات الملخص والاختبارات)، ثم الانتظار. عند `FAILED` يظهر حوار «توليد الأسئلة لم يكتمل» بثلاثة خيارات | L656–671، وL687–707، وL716–724 |
| 6. الترجمة | `POST /v1/courses/{cid}/translations`: للكورس مساران (الملحق من ناتج الأسئلة، والمتن من `parsed_book`)، وللكتاب المتن فقط. الاستئناف يعيد إرسال `resume_request` مع `resume_from_workflow_id` | L589–605، وL673–686 |
| 7. الصوت | `POST /v1/courses/{cid}/audio` (`source_artifact_ref`، و`language`، و`approved_outline_ref`، و`source_qa_artifact_ref`، و`book_audio_layout` بقيمة `lessons` أو `full_book`)، ثم `waitFor(/v1/audio/…)` | L606–627 |
| القفل | أثناء التشغيل يُعطَّل كل حقل وزر بدء (`setPipelineContextLocked`)، ويُرفض تغيير الكتاب (`rejectBookContextChange`) | L540–549 |

### 1.4 الحوارات (44 حوارًا، أي 44 استدعاء `new AlertDialog.Builder(this)`)

| # | العنوان | يُفتح من | المحتوى، والأزرار وأثرها | الدليل |
|---|---|---|---|---|
| 1 | اختبارات مراجعة مستقلة عن أسئلة كل درس | زر «اختيار اختبارات المراجعة…» | اختيار متعدد: ميدتيرم 1، وتيرم 1، وميدتيرم 2، وتيرم 2، والنهائي. «حفظ الاختيار» يكتب prefs | L59 |
| 2 | إعداد مزود خارجي | زر ربط المفتاح | حقلا معرّف النموذج ومفتاح API (كلمة سر). «حفظ وفحص» ينفّذ `PUT …/credentials` | L80 |
| 3 | «متن الكتاب/الملحق · محفوظ x / y» | «عرض مقاطع…» | قائمة المقاطع: «n · محفوظ/غير محفوظ · تصحيح محفوظ · أول 65 حرفًا». الضغط على مقطع يفتح #9 | L125 |
| 4 | إعادة ترجمة المقطع | `showRetranslationReview` قبل وجود نتيجة | الحالة والتفصيل. «تحديث الحالة» | L127 |
| 5 | اختر النسخة لمراجعتها | المصدر نفسه، مع وجود تعديلات | «ترجمة النموذج الأصلية» و«التعديل n · created_at»، ويُفتح بعدها #6 | L127 |
| 6 | «مراجعة المقطع · <seg>» | #4 أو #5 | الأصل، والترجمة الحالية، وحقل تحرير. زر «حفظ تعديل جديد للمراجعة» (`POST …/revisions`). «اعتماد النسخة المعروضة» (`POST …/accept`). «إغلاق دون اعتماد» | L128 |
| 7 | اختيار النسخة التي ستطبق عند الاستئناف | #9 | «الرجوع لترجمة النموذج الأصلية» و«النسخة n · origin · المحددة حاليًا» | L129 |
| 8 | «الترجمة الأصلية» أو «مراجعة النسخة المحفوظة» | #7 | معاينة. «اختيار هذه النسخة» ينفّذ `POST …/revision-selection` | L129 |
| 9 | «<segment_id>» | #3 | النص الأصلي، و«المراجع: citations»، و«الترجمة · محفوظة/لم تُحفظ بعد»، وحقل تحرير (فعّال فقط إن كانت الترجمة نهائية). أزرار: «إعادة ترجمة هذا المقطع فقط» (`POST …/retranslations`)، و«اختيار نسخة محفوظة أو الرجوع للأصل»، و«مراجعة نسخة هذا المقطع وتعديلها»، و«تنزيل ترجمة هذا المقطع» (`ACTION_CREATE_DOCUMENT`). «حفظ التصحيح» ينفّذ `POST …/corrections` | L130 |
| 10 | تغيير الخادم | «حفظ عنوان الخادم» مع host جديد | «حفظ» يحذف الرمز ويحفظ العنوان | L193 |
| 11 | إعداد اتصال آمن | أول طلب، أو بعد 401 | حقل الرمز (كلمة سر). «حفظ ومتابعة» يشفّره عبر `TokenVault.write`. «لاحقًا». خيط الطلب ينتظر حتى 5 دقائق | L199 |
| 12 | تفاصيل التشغيل | «عرض تفاصيل التشغيل» | نص prefs `log` | L202 |
| 13 | (عنوان متغير) | `showActionMessage` | رسالة + «إغلاق». تستعمله كل الأخطاء ونتائج الأفعال، منها: «تعذر <الفعل>»، و«المعالجة جارية»، و«سياق المعالجة محفوظ»، و«استئناف الترجمة»، و«التحكم في المرحلة» | L208 |
| 14 | كل الكتب · مستقلة عن اسم المقرر | «كل الكتب المحفوظة» | بحث، وبطاقة لكل كتاب: «العنوان» و«المصدر: <ملف> · محفوظ على الخادم». زر «فتح ومتابعة» (`selectBook`، ثم «المتابعة»)، وزر «إضافة للمقرر» يفتح #15 | L257–262 |
| 15 | دور الكتاب في المقرر | #14 | «كتاب أساسي / كتاب مساعد»، ثم `PUT /v1/courses/{cid}/memberships/{bid}` | L260، وL266 |
| 16 | «<اسم المقرر>» (بلا كتب) | «كتب هذا المقرر» | «المكتبة» أو «رفع PDF» أو «إغلاق» | L264 |
| 17 | «كتب <اسم المقرر>» | المصدر نفسه | «العنوان · أساسي/مساعد · من مقرر مصدر». «إضافة كتب» أو «رفع PDF» | L264 |
| 18 | «<عنوان الكتاب>» | #17 | «فتح الكتاب ومراحله المحفوظة»، و«اجعله أساسيًا»، و«اجعله مساعدًا» (`PUT memberships`)، و«تعديل اسم الكتاب الظاهر» يفتح #19 | L264 |
| 19 | تعديل اسم الكتاب | #18 | «حفظ» ينفّذ `PATCH /v1/courses/{cid}/memberships/{bid}` ‏`{display_name}` | L265 |
| 20 | «إنشاء مقرر جديد» أو «تعديل اسم المقرر» | أزرار المقرر | «حفظ» ينفّذ `POST /v1/courses` أو `PATCH /v1/courses/{cid}` | L267 |
| 21 | اختيار مقرر | «اختيار مقرر» أو «مجلدات المقررات» | «الاسم · n كتب»، ثم `switchCourse` (يصفّر المعرّفات ويكتب prefs). و«مقرر جديد» | L268–269 |
| 22 | مكتبة الكتب | «تنسيق الكتب المحفوظة…» | تلميح، وزر «اختيار كتاب لنافذة متابعة الحالة»، وبحث، و«الكتب المختارة: n من m»، وبطاقات اختيار («دور الملف عند رفعه…»). «استخدام الكتب المختارة» | L272–290 |
| 23 | تحديد دور الكتاب في هذا الكورس | #22 بكتاب واحد مختلف | «جعله الكتاب الأساسي» أو «إضافته كتابًا مساعدًا…» | L287 |
| 24 | اختر الكتاب الأساسي؛ ستصبح الكتب الأخرى مساندة | #22 بعدة كتب | قائمة، ثم `applyComposition` | L289 |
| 25 | اختيار كتاب للمتابعة | «اختيار كتاب للمراحل…» أو #22 | بطاقة لكل كتاب، فيها «اختيار ومتابعة المراحل» (أو «عرض متابعة مستقلة» أثناء التشغيل)، و«عرض الحالة دون تغيير كتاب المعالجة» | L292–296 |
| 26 | «متابعة · <العنوان>» | #25 | «الدور · المقرر · المعرف»، وحالة 7 مراحل من `progress`: رفع PDF وOCR، والمخطط، والفهرسة وRAG، والدروس والأسئلة، وترجمة الملحق، وترجمة متن الكتاب، والصوت. ومعها «نتيجة من رفع PDF أقدم»، وللترجمة «محفوظ x/y · فشل · يحتاج فحص اكتمال». زر «تحديث الحالة الآن»، وpolling كل 15 ث ما دام الحوار مفتوحًا | L297–301 |
| 27 | تجهيز كتب المقرر | اختيار كتاب من مقرر آخر | حالة ربط لكل كتاب (`/materialize`، ثم انتظار الاستخراج كل 3 ث). لا يُلغى. «إخفاء المتابعة» | L304–331 |
| 28 | الكتاب · OCR | «عرض نص OCR» أو «عرض النص المحفوظ» | «المقاطع a–b من n»، و«صفحة [..] · type» مع النص، و«السابق» و«التالي» (100 مقطع)، و«A−» و«A+». «تم» | L386–391 |
| 29 | «<عنوان الكتاب>» (عارض PDF) | «فتح PDF مع الزوم» | «صفحة x من n»، وتكبير بالقرص من 1× إلى 5×، وSeekBar للصفحات، و«السابق» و«التالي» | L400–407 |
| 30 | «<اسم المقرر>» (نسخ الصوت) | «تحديث قائمة الصوت» مع عدة إنتاجات | اختيار مفرد: «إنتاج n · الحالة · m ملف محفوظ» | L413 |
| 31 | إعادة صوت هذا الدرس وملحقه فقط | «إعادة صوت الدرس المحدد فقط» | شرح. «فحص المصادر الحالية» | L418 |
| 32 | اعتماد مصدر إعادة الصوت | #31 | الدرس، ومصدر `uri` مع SHA-256. «إعادة هذا الدرس» ينفّذ `POST …/audio/regenerate` | L418 |
| 33 | محتوى المرحلة / الأجزاء المحفوظة | «عرض ناتج المرحلة» أو «عرض الفهرسة المحفوظة» | JSON مُنسّق، أو نص المقاطع المفهرسة (≤ 120 ألف حرف) | L441 |
| 34 | اختر دروس دفعة الأسئلة | «اختيار الدروس» | اختيار متعدد «الوحدة · الدرس [id]». «حفظ النطاق» | L451 |
| 35 | «<عنوان الدرس>» | #36 | «محتوى الدرس»، و«ملخص الدرس»، و«أسئلة الدرس وإجاباتها · n»، و«اختبار الدرس · n»، و«A−» و«A+». «تصدير أسئلة هذا الدرس» | L459–468 |
| 36 | الدروس · اختر درسًا | «الأسئلة حسب الدرس» | «درس/اختبار · العنوان · n سؤال · محتوى الدرس متاح» | L479 |
| 37 | «نتائج <cid>» | «كل النتائج المحفوظة للمقرر» | «stage · created_at · n ملف» | L483 |
| 38 | «<stage>» | #37 | أسماء الملفات | L484 |
| 39 | «<اسم الملف>» | #38 | «معاينة الملف»، و«تنزيل الملف»، و«الدروس والمقاطع منفصلة» (للـ JSON) | L484 |
| 40 | «<اسم الملف>» (معاينة) | #39 أو #42 | أول 1 MB، محوَّل إلى نص مقروء (`readableSavedRecord`) | L485–487 |
| 41 | «مقاطع a–b من n» | #39 | الوحدات (40 في الصفحة)، و«التالي» و«السابق» | L488 |
| 42 | «<الوحدة>» | #41 | «معاينة هذا المقطع» أو «تنزيل هذا المقطع فقط» | L488 |
| 43 | تنزيل المقاطع الصوتية المكتملة | «تنزيل ناتج المرحلة» للصوت | «العنوان · MP3»، ثم DownloadManager | L494 |
| 44 | توليد الأسئلة لم يكتمل | `waitFor` أو `resumeQA` عند `FAILED` | «المحفوظ حاليًا: n سؤالًا…». الأزرار: «استئناف من آخر حفظ» (`POST /v1/course-qa/{id}/resume`)، و«المتابعة بالمحفوظ»، و«توقف الآن» | L687–707 |

### 1.5 مسارات الـ API التي يستدعيها التطبيق

**الآثار على الخادم:** تحققتُ منها في `packages/api/src/b2c_api` (routers و`app.py`). كل الطلبات تحمل `Authorization: Bearer <token>`. والـ middleware في `app.py` يطلب الرمز نفسه لكل المسارات عدا `GET /health`.

| # | الطريقة والمسار | أين يُستدعى | ما يفعله على الخادم |
|---|---|---|---|
| 1 | `GET /health` | `healthProbe` ← `readyBase` (L195–196) | قراءة؛ بلا رمز |
| 2 | `GET /openapi.json` | `refreshServerCapabilitiesBlocking` (L775)، **في المصدر فقط** | قراءة مخطط FastAPI |
| 3 | `POST /v1/books` (multipart) | `uploadBlocking` (L240) | **كتابة:** يخزّن الـ PDF ويبدأ workflow الاستخراج (`books.py:154/260`) |
| 4 | `GET /v1/library/books` | `loadBooks` (L255) | قراءة |
| 5 | `GET /v1/courses` | `manageCourses` (L268) | قراءة |
| 6 | `POST /v1/courses` | `editCourseName(true)` (L267) | **كتابة:** مقرر جديد |
| 7 | `PATCH /v1/courses/{cid}` | `editCourseName(false)` (L267) | **كتابة:** اسم المقرر |
| 8 | `GET /v1/courses/{cid}/books` | L256، وL264، وL271، وL328، وL355 | قراءة: كتالوج كتب المقرر |
| 9 | `GET /v1/courses/{cid}/memberships` | `showCourseMembers` (L264)، و`materializeNext` (L317) | قراءة |
| 10 | `PUT /v1/courses/{cid}/memberships/{bid}` | L264، وL266، وL319 | **كتابة:** عضوية الكتاب ودوره |
| 11 | `PATCH /v1/courses/{cid}/memberships/{bid}` | `renameMembership` (L265) | **كتابة:** `display_name` |
| 12 | `POST /v1/courses/{cid}/memberships/{bid}/materialize` | `materializeNext` (L320) | **لا يوجد هذا المسار في الخادم الحالي.** المصدر يفحص الـ OpenAPI أولًا فيتوقف برسالة. و0.5.10 يستدعيه مباشرة فيفشل. خطة إعادة الهيكلة (§5) تُدرجه في ما يُحذف |
| 13 | `PUT /v1/courses/{cid}/books/{bid}/name` | `saveBookName` (L760) | **كتابة:** اسم الكتاب |
| 14 | `GET /v1/courses/{cid}/books/{bid}/progress` | L300، وL321، وL329، وL373، وL473 | قراءة سجل `book-progress` من S3 |
| 15 | `GET /v1/courses/{cid}/books/{bid}/source` | `downloadBookPdf` (L396) | قراءة الـ PDF |
| 16 | `GET /v1/courses/{cid}/books/{bid}/saved-audio` | `loadSavedAudioTracks` (L764)، **كود ميت** | قراءة |
| 17 | `GET /v1/ingestions/{id}[?recovery_course_id=]` | L151، وL326، وL380، وL418، وL589، وL593، وL609، وL623، و`waitFor` | قراءة حالة Temporal (أو استرداد من artifact محفوظ) |
| 18 | `GET /v1/ingestions/{id}/text?course_id&offset&limit=100` | L391، وL496 | قراءة |
| 19 | `GET /v1/ingestions/{id}/processing-plan?…` | L570، وL418 | قراءة: حساب خطة |
| 20 | `GET /v1/ingestions/{id}/outline` | L449، وL418 | قراءة |
| 21 | `POST /v1/ingestions/{id}/outline/approve-system` | **0.5.10 فقط** (`ensureOutlineOnly` و`ensureRag`) | **كتابة + بدء workflow** (اعتماد المخطط ثم `IndexApprovedBookWorkflow`). اليوم يعيد 409 افتراضيًا (`allow_system_outline_approval=false`، ‏`ingestion.py:300`) |
| 22 | `GET /v1/indexings/{id}` | L151، وL380، و`waitFor` | قراءة |
| 23 | `GET /v1/indexings/{id}/chunks` | L436، وL497 | قراءة |
| 24 | `POST /v1/courses/{cid}/qa/generate` | `startAndWaitQA` (L668) | **بدء workflow:** ‏`GenerateCourseQAWorkflow` |
| 25 | `GET /v1/course-qa/{id}[?recovery_…]` | L380، وL433، وL475، وL586، وL588، وL660، وL739، وL749 | قراءة |
| 26 | `GET /v1/course-qa/{id}/checkpoints` | L438، وL475، وL498 | قراءة |
| 27 | `POST /v1/course-qa/{id}/pause` | `pauseQA` (L736) | **كتابة:** طلب إيقاف بعد نقطة الحفظ |
| 28 | `POST /v1/course-qa/{id}/resume` | `resumeQA` (L745)، و`waitFor` (L720) | **بدء workflow جديد** من نقاط الحفظ (`qa.py:327`) |
| 29 | `POST /v1/courses/{cid}/translations` | L124، وL679، وL685 | **بدء workflow:** ‏`TranslateContentWorkflow` (المسار القديم) |
| 30 | `GET /v1/translations/{id}` | L114، وL123، وL124، وL301، وL383، وL417، وL599، و`waitFor` | قراءة |
| 31 | `GET /v1/translations/{id}/units` | `previewTranslationUnits` (L125) | قراءة |
| 32 | `GET /v1/translations/{id}/checkpoints` | L438، وL499 | قراءة |
| 33 | `POST /v1/translations/{id}/corrections` | #9 (L130) | **كتابة:** تصحيح يدوي (للترجمة النهائية فقط) |
| 34 | `POST /v1/translations/{id}/retranslations` | `queueUnitRetranslation` (L126) | **بدء workflow:** ‏`RetranslateSegmentWorkflow` |
| 35 | `GET /v1/translations/{id}/retranslations/{cand}` | L127 | قراءة |
| 36 | `POST …/retranslations/{cand}/revisions` | #6 (L128) | **كتابة:** نسخة معدّلة |
| 37 | `POST …/retranslations/{cand}/accept` | #6 (L128) | **كتابة:** اعتماد نسخة |
| 38 | `POST /v1/translations/{id}/revision-selection` | #8 (L129) | **كتابة:** اختيار نسخة |
| 39 | `POST /v1/courses/{cid}/audio` | `runSelectedPath` (L625) | **بدء workflow:** ‏`GenerateMP3Workflow` (الصوت القديم) |
| 40 | `POST /v1/courses/{cid}/audio/regenerate` | #32 (L418) | **بدء workflow:** ‏`GenerateMP3Workflow` لدرس واحد |
| 41 | `GET /v1/audio/{id}` | L151، وL380، وL415، و`waitFor` | قراءة |
| 42 | `GET /v1/audio/{id}/manifest` | L437، وL494، وL626 | قراءة |
| 43 | `GET /v1/audio/{id}/tracks/{track}` و`GET /v1/audio/{id}/file` | `audioPath` (L420)، و`downloadTrack` (L427)، وL494 | قراءة/بث MP3 (MediaPlayer أو DownloadManager) |
| 44 | `GET /v1/courses/{cid}/audio` | `loadAudioTracks` (L413) | قراءة: المسارات القديمة غير الموثقة مخفية افتراضيًا (`include_unverified_legacy=false`) |
| 45 | `GET /v1/courses/{cid}/audio/zip?workflow_id=` (`zip_url`) و`download_path` التاريخي (`/v1/courses/…/saved-audio/{key}`) | L415، وL420 | قراءة/تنزيل |
| 46 | `GET /v1/courses/{cid}/results` | L483 | قراءة |
| 47 | `GET /v1/courses/{cid}/results/{rid}/file?name[&preview=true]` | L484، وL487 | قراءة |
| 48 | `GET /v1/courses/{cid}/results/{rid}/units?name&offset&limit=40` و`…/units/{uid}/file?name` | L488 | قراءة |
| 49 | `POST /v1/courses/{cid}/results/export` | L489 | بناء ZIP وبثه. لا يغيّر الحالة، ويرفض المحتوى القديم غير الموثق افتراضيًا (`LEGACY_REFUSAL`) |
| 50 | `GET /v1/workflows/{id}/control` | L172، وL176 | قراءة: `temporal_status` وحالة التحكم |
| 51 | `POST /v1/workflows/{id}/pause` | `controlStage` (L176) | **كتابة + signal:** إيقاف عند نقطة الحفظ |
| 52 | `POST /v1/workflows/{id}/resume` | بلا body: `controlStage` (L176). مع `book_id`: L154 وL157 | بلا body: signal ‏`resume_stage` لـ workflow حي متوقف. لـ workflow نهائي فاشل: **بدء workflow جديد** (`workflow_resume.py:60`) |
| 53 | `GET /v1/workflows/{id}/resume-capability` | L146، وL152، وL157 | قراءة |
| 54 | `GET /v1/model-providers` و`GET /v1/model-providers/{id}` | L82، وL94 | قراءة |
| 55 | `POST /v1/model-providers/{id}/activate` | L93 | **أثر تشغيلي:** تجهيز النموذج وتحميله |
| 56 | `PUT /v1/model-providers/{id}/default/{stage}` | L93 | **كتابة:** الافتراضي للمهمات الجديدة |
| 57 | `PUT /v1/model-providers/{id}/credentials` | L80 | **كتابة سر** في مخزن أسرار الخادم |

**المجموع:** 57 سطرًا، تغطي نحو 62 مسارًا (بعض الأسطر تجمع مسارات متقاربة). **منها 25 تكتب أو تبدأ workflow أو تؤثر في التشغيل**، وأحدها `/materialize` غير موجود في الخادم أصلًا.

### 1.6 الخلفية: polling والتخزين والتنزيل والرمز

| البند | التفاصيل | الدليل |
|---|---|---|
| polling عام | `statusTimer`: بعد 3 ث، ثم كل 10 ث. يستدعي `refreshStatusesBlocking` إن كان `progressWatching` مفعّلًا، ولا مسار يعمل، ولا PDF جديد، و`ingest` غير فارغ. **كل دورة:** ‏`GET progress` + ‏`GET` لكل مرحلة (حتى 5) + `GET /control` و`resume-capability` عند الحاجة | L17، وL369–385 |
| انتظار مسار | `waitFor`: ‏`GET` كل 5 ث، حتى 6 ساعات لكل مرحلة | L709–730 |
| حوار المتابعة المستقلة | كل 15 ث ما دام مفتوحًا | L298 |
| النماذج | `watchModel`: كل 2 ث، حتى 600 مرة، وفقط و«الإعدادات» مفتوحة | L94 |
| الربط | `waitMaterialized`: كل 3 ث، حتى 600 مرة | L325–327 |
| المشغّل | يحفظ الموضع كل 500ms في prefs، وفي `onPause` | L424، وL507 |
| الخيوط | `io` و`monitorIo` (كل منهما single-thread executor) و`statusTimer`. تُغلق في `onDestroy` | L6–9، وL508 |
| الشبكة | `HttpURLConnection` بلا تتبع redirects. ‏connect 10 ث، وread ‏120 ث للـ GET و1800 ث لغيره. عند 401: يحذف الرمز ويطلبه مرة واحدة. الـ GET يعاد على المنفذ البديل عند 502/503/504 أو خطأ اتصال. **غير الـ GET لا يعاد** («لم يُعد التطبيق إرسال طلب التشغيل») | L213–229 |
| المنفذ البديل | `EndpointRouting`: الـ origin المضبوط، ثم `:8443`، وذلك للـ host ‏`desktop-3khe91s.tail7385b1.ts.net` فقط. يُحفظ المختار 30 ث | `EndpointRouting.java`، وL196 |
| الرمز | `TokenVault`: ‏AES/GCM بمفتاح AndroidKeyStore ‏`book2course.mobile.api.token.v1`، ومخزَّن Base64 في prefs `api_token_ciphertext`. عند فشل فك التشفير يُحذف. يُحذف أيضًا عند 401 وعند تغيير الـ host | `TokenVault.java`، وL193، وL225 |
| SharedPreferences ‏(`b2c`) | **المعرّفات:** `i` و`x` و`q` و`a` و`tr` و`btr` و`mp3`، و`progress:<cid>:<bid>:<ingest>`. **السياق:** `course` و`course_name` و`book` و`title` و`swmap` و`composition:*` و`course-role:*`. **الإعداد:** `target` و`content_kind` و`output_language` و`audio_mode` و`include_lesson_summaries` و`include_lesson_enrichment` و`include_review_assessments` و`review_assessment_scopes`. **الأسئلة:** `qa_lesson_ids` و`qa_history` و`qa_scope_map` و`continue_partial_qa`. **النماذج:** `model_default:*` و`translation_provider_id`. **الترجمة:** `translation_candidate_*`. **القراءة والصوت:** `reader_page:<bid>` و`audio_pos:<wf>:<idx>`. **عام:** `log` و`api_base_url` و`api_token_ciphertext`. **لا cache لأي استجابة** | L16، وL337، وL350، وغيرها |
| التنزيل | `DownloadManager` إلى `Downloads` العام، مع ترويسة `Authorization` (`enqueueDownload`). التصدير عبر `ACTION_CREATE_DOCUMENT` (requestCode 2) من نص في الذاكرة أو ملف مؤقت في `cacheDir` | L430، وL457، وL489، وL506 |
| هوية الكتاب | `BookIdentity.publicId` = `book-` + أول 24 حرفًا من `file_sha256`. والمعرّف القديم يُحوَّل تلقائيًا (`resolveLegacyBookIdentity`) | `BookIdentity.java`، وL352–368 |
| كود ميت | `upload`، و`pollI`، و`approve`، و`pollX`، و`generate`، و`pollQ`، و`loadSavedAudioTracks`، و`remotePhase`، و`dec`، و`artifactSupplementaryWorkflowMap` (لا يستدعيها شيء). وحقلا `chunk` و`overlap` لا يُقرآن منذ `5998b2b` | grep على أسماء الدوال |

**العدّ:** 5 تبويبات، و44 حوارًا، و77 موضع `btn(…)`. بعض هذه المواضع تتكرر في كل بطاقة، فيصير مجموع أزرار الشاشات نحو 100 زر. ويضاف إليها 67 زر حوار (`setPositive/Negative/NeutralButton`)، و12 Spinner، و3 CheckBox، و10 EditText ظاهرة.

## 2. خريطة التحويل

**الوجهة:** قسم قراءة فقط اسمه «المراحل والتقدم»، تبويب سفلي ثانٍ بجانب «تعلّم» في `android/learner-app` (Kotlin/Compose). مصدره API جديد قراءة فقط `/v3/operator`: ‏`meta`، و`books`، و`…/stages`، و`…/stages/{stage_id}`.

**القاعدة:** كل زر يبدأ أو يوقف أو يستأنف أو يعتمد أو يرفع أو يصدّر أو يعدّل يأخذ أحد حكمين:
- **مؤجَّل:** مرحلة لاحقة تحتاج موافقة المالك.
- **يُسقط:** إن منعه قرار. القرار يُذكر بالاسم.

**القرارات المرجعية:**

| الرمز | ما ينص عليه |
|---|---|
| **R0** | خطة إعادة الهيكلة `docs/audits/2026-10-24/00-RESTRUCTURE-PLAN.md` §R0 بند 2: «إيقاف `approve-system` كطريق لإطلاق المراحل اللاحقة، وإزالة استدعائه التلقائي من Android». نُفّذ في `5998b2b`. وفي جدول §5 «ما يُحذف»: «استدعاء `approve-system` التلقائي من Android»، و«استدعاء `/materialize`»، و«عرض المسارات legacy في `/saved-audio`» |
| **D-028** | «Translation rebuild: source-only engine input, …, explicit acceptance, human review queue, TM» (Accepted 2026-10-24). الاقتراح `docs/proposals/2026-10-24-translation-rebuild.md` يقول: «`generation.py` الحالي يبقى للمسار القديم حتى تنتهي الـ workflows الجارية، ثم يُحذف». و`TranslateContentWorkflow` القديم يبقى للـ replay فقط |
| **D-033** | «لا مراجعة بشرية في خط الإنتاج… يحل محل طابور المراجعة البشرية في D-028» |
| **D-040** | مرحلة حواشي المترجم: كل وحدة `needs_review`/`rejected` تصير `TranslatorNote`، وبوابة `translator-notes.coverage` |
| **D-041 وD-042 وD-044** | الصوت الجديد (speech v2، و`notes_ref`، وMP3 ‏ID3v2.3، وترميز الحلقة MP3 «موصول بعد الصوت») |
| **D-032 وD-034 وD-035 وD-038** | طبقة المفاهيم، وبحث المعرفة، والمحتوى الإثرائي بالإنجليزية ثم يُترجم، وختام كل حلقة: ملخص ومصطلحات وخمسة أسئلة |

**ما لم أجده في `00-DECISIONS.md`:** قرارًا يذكر حرفيًا `translate-content` أو `/v1/translations` أو `/v1/courses/{id}/audio` بأنها «ممنوعة». الاستبدال مستنتج من D-028 (الترجمة) ومن D-041 وD-042 وD-044 (الصوت)، ومن R0 بند 5: «منع الصوت والتصدير من استهلاك ترجمة أو QA حالتها ليست final/completed».

### 2.1 المراحل القديمة ← المراحل الجديدة

| المرحلة القديمة (بطاقة في «المتابعة») | ما حلّ محلها في `/v3/operator/…/stages` | ملاحظة |
|---|---|---|
| رفع الكتاب وOCR (`/v1/ingestions`) | **الاستخراج** `ingest-parse-pdf`، مع بوابة `ingest-v2.quality` (D-043) | «صفحات الكتاب» و«المقاطع المحفوظة» تصيران تفاصيل المرحلة |
| المخطط (`approved_outline_ref`، و«المخطط معتمد») | **المخطط** `build-outline-v2`، وحالته من GateResult | لا اعتماد من التطبيق (R0) |
| الفهرسة وRAG (`/v1/indexings`) | لا مقابل مباشر. **المعرفة/المفاهيم** `knowledge-research` و`concept-layer` (D-034، وD-032) هي ما يُعرض في موضعها | «المقاطع المفهرسة» تُسقط |
| الدروس والأسئلة (`/v1/course-qa`) | **الحلقات** `episode-script-en` و`episode-script-ar`. الأسئلة جزء من ختام الحلقة (D-038 (2))، والمحتوى الإثرائي إنجليزي ثم يُترجم (D-035) | عدادات النطاقات والدروس تُستبدل بعدد الحلقات المكتملة |
| الترجمة: مسار «متن الكتاب» (`/v1/translations`، ‏`TranslateContentWorkflow`) | **قاموس المصطلحات** `book-termbase` (مرشحون، ومعتمد، ومرفوض)، ثم **الترجمة** `translate-book` (`translation_unit_results`: مقبول، وحاشية = needs_review، ومُسقط، ومرفوض، ومعدل بالساعة، ووقت متبقٍ)، ثم **حواشي المترجم** `translator-notes` | D-028، وD-040 |
| الترجمة: مسار «ملحق الدرس» (الملخص والأسئلة) | لا مرحلة مستقلة. المحتوى الإثرائي يُترجم بالمسار نفسه وقاموسه (D-035)، فيظهر ضمن الحلقات | يُسقط بوصفه مسارًا منفصلًا |
| إنتاج الصوت (`/v1/audio`، ‏`GenerateMP3Workflow`) | **الصوت** `speech-text` و`speech-track`، ثم **MP3** ‏`episode-mp3` | D-041، وD-042، وD-044 |

### 2.2 العناصر القديمة ← مكانها الجديد

| العنصر القديم | المكان الجديد، أو الحكم | السبب |
|---|---|---|
| **التنقل والإطار** | | |
| الشريط السفلي بخمسة تبويبات (L22) | تبويبان في التطبيق الجديد: «تعلّم» و«المراحل والتقدم» | القسم الجديد قراءة فقط. والتبويبات الأخرى إما أفعال (مؤجَّلة) أو متعلم (موجود) |
| حوارات بدل الشاشات (44) | شاشات Compose داخل القسم: قائمة الكتب، ثم مراحل الكتاب، ثم تفاصيل المرحلة | تقييم 2026-10-25 §3.1 |
| `log` وحوار «تفاصيل التشغيل» (#12) | يُسقط. تحل محله حالة خطأ واضحة في كل شاشة | السجل غير مرئي أصلًا (L10، وL753) |
| `showActionMessage` (#13) | رسائل الخطأ في موضع العنصر (`AppError` الموجود) | — |
| **المكتبة** | | |
| قائمة الكتب: «كل الكتب المحفوظة» (#14)، و«كتب المقرر» (#17)، و«مكتبة الكتب» (#22)، و«اختيار كتاب للمتابعة» (#25) | **`GET /v3/operator/books`:** العنوان، و`book_id` و`course_id` وsha مختصر، وتاريخ الرفع، والمرحلة الحالية، والنسبة الإجمالية، مرتبة بالأحدث نشاطًا. البحث بالعنوان (#14 و#22) **لم يُنفَّذ** في 0.2.0 (القائمة قصيرة) | قراءة |
| `bookDisplayTitle` و`bookSecondaryIdentity` (اسم الملف بدل «كتاب التجربة»، وآخر 8 أحرف من المعرف عند تكرار الاسم) (L526–539) | يُنقل منطق العرض: العنوان، ثم sha مختصر عند التكرار | يحمي من التباس الكتب المتشابهة |
| `BookIdentity.publicId` (`book-<sha24>`) | الهوية تأتي جاهزة من `/v3/operator/books` | لا اشتقاق على الهاتف |
| «اختيار مقرر»، و«مقرر جديد»، و«تعديل اسم المقرر»، و«كتب هذا المقرر» (#16–#18، و#20، و#21) | **مؤجَّل** (كتابة: ‏`POST`/`PATCH /v1/courses`). يُعرض `course_id` في بطاقة الكتاب فقط | مرحلة لاحقة تحتاج موافقة المالك |
| «إضافة للمقرر»، و«اجعله أساسيًا/مساعدًا»، و`role`، و«تنسيق الكتب…» (#15، و#18، و#22–#24) | **مؤجَّل** (كتابة: `PUT memberships`) | مرحلة لاحقة تحتاج موافقة المالك |
| «تعديل اسم الكتاب» (#19)، و«حفظ اسم الكتاب المختار» | **مؤجَّل** (كتابة: `PATCH memberships` و`PUT …/name`) | مرحلة لاحقة تحتاج موافقة المالك |
| «رفع PDF للمقرر»، و`uploadStatus`، و`uploadBar`، و`lang`، و`ocr` | **مؤجَّل** (`POST /v1/books` يبدأ workflow) | مرحلة لاحقة تحتاج موافقة المالك |
| ربط كتاب من مقرر آخر: «تجهيز كتب المقرر» (#27، ‏`/materialize`) | **يُسقط** | المسار غير موجود في الخادم، وR0 (§5 «ما يُحذف»: «استدعاء `/materialize`») |
| **إعداد المراحل** | | |
| الشاشة كلها: `target`، و`contentKind`، و`outputLanguage`، و`audioMode`، والخيارات الإضافية، ودفعة الأسئلة، والحقول الرقمية، و«ابدأ حتى الناتج المختار» | **مؤجَّل** (تبدأ workflows). الأجزاء المرتبطة بالمسار القديم (RAG و`retrieval`، و`chunk` و`overlap`، و`lq`/`gq`/`cq`، والاختبارات Midterm/Term، و`audioMode`) **تُسقط** ولا تُنقل حتى في المرحلة اللاحقة، لأن مراحلها استُبدلت (2.1) | مرحلة لاحقة تحتاج موافقة المالك، وD-028، وD-038 |
| `plan` (نص «المسار المطلوب…») | **يُستبدل:** ترتيب المراحل وأسماؤها من `GET /v3/operator/meta`، معروضًا خطًا رأسيًا في شاشة مراحل الكتاب | قراءة |
| أزرار «نموذج كل مرحلة» (7، ‏L52) | يُسقط بوصفه زرًا. اسم المحرك أو النموذج يظهر قراءةً في تفاصيل التشغيل (تشغيلات الترجمة في `…/stages/{stage_id}`) | — |
| **المتابعة** | | |
| بطاقات المراحل الخمس (L97–111) | **شاشة مراحل الكتاب:** تسع بطاقات بالترتيب الجديد (2.1). في كل بطاقة: الحالة (لم تبدأ، أو جارية، أو مكتملة، أو فشلت، أو بانتظار مرحلة سابقة)، والبوابة (GateResult)، وآخر تحديث، وحالة الـ workflow في Temporal ونشاطه الحالي | `GET …/stages` |
| `statusName` (L339) | يُنقل بوصفه أساسًا لنصوص حالة Temporal. لكن القيم المعروضة للمرحلة هي الخمس الجديدة، وحالة Temporal سطر ثانوي | — |
| النسبة والشريط (`phasePercent`، وL143) | النسبة تأتي من الخادم لكل مرحلة. للترجمة: (مقبول + حاشية + مُسقط + مرفوض) ÷ الوحدات، مع المعدل بالساعة والوقت المتبقي | لا اشتقاق من أزواج عدادات متفرقة على الهاتف |
| تفاصيل البطاقة: الرسالة، والنطاق الحالي، والصفحات، والوقت، و«سبب التوقف» (L184–188) | في `GET …/stages/{stage_id}`: أسباب الرفض، ونتائج البوابات، وتشغيلات الترجمة، ونشاط Temporal الحالي | قراءة |
| مسارا الترجمة: «ناجح/جارٍ/مؤجل/فاشل/غير محفوظ» و«تصحيح العربية» (L112–118) | **يُستبدل** بعدادات `translation_unit_results` (مقبول، وحاشية، ومُسقط، ومرفوض) ومعدلها | D-028، وD-040. و«تصحيح العربية» مرحلة في المسار القديم فقط |
| «عرض مقاطع المتن/الملحق» (#3) وحوار المقطع (#9) للقراءة | **تفاصيل الترجمة:** عيّنة آخر الوحدات المترجمة (الأصل، والترجمة، والقرار) | قراءة |
| «حفظ التصحيح»، و«إعادة ترجمة هذا المقطع»، و«اختيار نسخة»، و«مراجعة آخر إعادة ترجمة»، و«اعتماد النسخة» (#4–#9: ‏`corrections` و`retranslations` و`revisions` و`accept` و`revision-selection`) | **يُسقط** | D-033: لا مراجعة بشرية في خط الإنتاج، والشك يصير حاشية (D-040). وهذه المسارات على `TranslateContentWorkflow` القديم (D-028) |
| «تنزيل ترجمة هذا المقطع» | **مؤجَّل** (تصدير) | مرحلة لاحقة تحتاج موافقة المالك |
| قائمة حواشي المترجم | **جديد:** في تفاصيل `translator-notes` | D-040 |
| «بدء / متابعة هذه المرحلة» (×5) | **مؤجَّل** | مرحلة لاحقة تحتاج موافقة المالك |
| «إيقاف مؤقت بعد الحفظ» و«استئناف من آخر نقطة حفظ» (`/v1/workflows/{id}/pause\|resume`) | **مؤجَّل.** تُعرض حالة التحكم (PAUSED أو PAUSE_REQUESTED) قراءةً ضمن حالة Temporal | مرحلة لاحقة تحتاج موافقة المالك |
| «استئناف من البيانات المحفوظة» (`resume` مع `book_id` يبدأ workflow جديدًا)، و«استئناف المقاطع غير المحفوظة»، و«فحص إتاحة الاستئناف» | **مؤجَّل.** الترجمة القديمة **تُسقط** | مرحلة لاحقة تحتاج موافقة المالك. وD-028 للترجمة |
| أزرار الأسئلة: «إيقاف بعد نقطة الحفظ»، و«استئناف…»، و«المتابعة بالجزء المحفوظ»، وحوار «توليد الأسئلة لم يكتمل» (#44) | **يُسقط** | مرحلة الأسئلة القديمة استُبدلت بالحلقات (D-038) |
| «اعتماد المخطط» الآلي (`approve-system`، في 0.5.10) | **يُسقط** | R0 بند 2 (`5998b2b`) |
| حوار «متابعة · <كتاب>» المستقل (#26) | **هو شاشة مراحل الكتاب نفسها.** القسم كله لا يغيّر شيئًا، فلا حاجة لتمييز «متابعة مستقلة» | — |
| «نتيجة من رفع PDF أقدم» (L301) | **يُنقل بوصفه مفهومًا:** «آخر تحديث» لكل مرحلة، ووسم المرحلة التي سبقت آخر استخراج | قراءة |
| `stageBookLabel` و`state` و«نسخ معرّفات التشغيل» | **يُنقل جزئيًا:** الهوية في رأس الشاشة وبطاقة الكتاب. نسخ `workflow_id` **لم يُنفَّذ** في 0.2.0 (فعل محلي لا يمس الخادم، يضاف لاحقًا) | قراءة |
| «فحص الاتصال» و`stageConnectionStatus` | ينتقل إلى شاشة الإعداد الموجودة. والقسم يعرض حالة «تعذر الاتصال» عند الفشل | — |
| **المخرجات** | | |
| مشغل الصوت: Spinner، وSeekBar، والسرعة 0.75–2.0، والسابق والتالي، وحفظ الموضع (L63، وL422–426) | **قسم المتعلم:** المشغّل Media3 (`PlaybackService` و`PlayerScreen`) | تقييم 2026-10-25 §3.4 |
| «تنزيل الملف المحدد» للاستماع | **قسم المتعلم:** `EpisodeDownloads` | — |
| «تحديث قائمة الصوت» ونسخ الإنتاج (#30) | **يُسقط** من القسم. حالة `episode-mp3` تظهر في بطاقة المرحلة، والاستماع في «تعلّم» | الصوت القديم استُبدل (D-041، وD-042، وD-044)، وR0 (حجب legacy في `/saved-audio`) |
| «تنزيل المقرر ZIP»، و«تنزيل كل النتائج ZIP»، و«تنزيل ناتج المرحلة»، و«تصدير أسئلة هذا الدرس» | **مؤجَّل** (تصدير) | مرحلة لاحقة تحتاج موافقة المالك. والتصدير يرفض القديم غير الموثق افتراضيًا (R0 بند 5) |
| «إعادة صوت الدرس المحدد فقط» (#31، و#32) | **يُسقط** | يبدأ `GenerateMP3Workflow` القديم، وقد استُبدل (D-041، وD-042، وD-044) |
| «كل النتائج المحفوظة للمقرر» ومعاينتها (#37–#42) | **يُسقط** | نتائج المسار القديم (OCR وRAG وQA والترجمة القديمة)، وR0 بند 1 يحجرها `unverified`. نتائج المسار الجديد تظهر في تفاصيل المراحل |
| «عرض نص OCR» (#28) و«فتح PDF مع الزوم» (#29) | **يؤجَّل** (قراءة، لكن خارج `/v3/operator` في هذه النسخة) | يمكن إضافة عيّنة صفحات لتفاصيل `ingest-parse-pdf` لاحقًا |
| «عرض ناتج المرحلة» (#33)، و«عرض الفهرسة المحفوظة»، و«الأسئلة حسب الدرس» (#35، و#36) | **يُسقط** | مراحل قديمة (RAG وQA). محتوى الحلقة يُعرض في قسم المتعلم |
| **الإعدادات** | | |
| «عنوان خادم Book2Course»، و«حفظ عنوان الخادم»، و«تغيير الخادم» (#10)، و«فحص الاتصال بالخادم» | **شاشة الإعداد الموجودة** في التطبيق الجديد (`SetupScreen` و`ServerConfigStore`)، بالعنوان نفسه | — |
| الرمز: حوار «إعداد اتصال آمن» (#11) و`TokenVault` | **شاشة الإعداد الموجودة** بالرمز نفسه. الخادم يقبل bearer token واحدًا لكل المسارات (middleware في `app.py`)، فلا رمز منفصل للمشغّل. التخزين: `KeystoreTokenCipher` (منقول من `TokenVault`، بمفتاح `book2course.learner.api.token.v1`) | — |
| المنفذ البديل 8443 (`EndpointRouting.candidates`) | **يؤجَّل.** لا يوجد في التطبيق الجديد. يُنقل إن احتاجه الاتصال عبر الـ tailnet | — |
| خيارات النماذج: «تحديث النماذج»، و«فحص الجاهزية»، و«ربط مفتاح API»، و«اعتماد للمهمات الجديدة» (#2، و`/v1/model-providers/*`) | **مؤجَّل** كلها: ‏`activate` و`default` و`credentials` كتابة أو أثر تشغيلي. ولا قائمة نماذج للقراءة في هذه النسخة | مرحلة لاحقة تحتاج موافقة المالك |
| «معلومات التطبيق» | شاشة الإعدادات في التطبيق الجديد | — |
| **الخلفية** | | |
| polling كل 10 ث، و`waitFor` كل 5 ث، ومتابعة كل 15 ث | يُستبدل: تحديث عند فتح الشاشة، وسحب للتحديث، وتحديث تلقائي كل 15 ث **ما دامت الشاشة ظاهرة فقط** (`repeatOnLifecycle(RESUMED)`)، بطلب شرطي `If-None-Match`، والخادم يخزّن كل نتيجة 20 ث | الطلبات كلها `GET` قراءة |
| `SharedPreferences` لمعرّفات العمل وسياق الكتاب (`i/x/q/tr/btr/mp3`، و`progress:*`، و`composition:*`، و`course-role:*`، و`qa_*`) | **يُسقط.** الحالة كلها من الخادم (`/v3/operator`)، ولا معرّفات تُحفظ محليًا | السجل على الخادم هو المصدر |
| لا cache للاستجابات | `ResponseCache` الموجود في التطبيق الجديد، لعرض آخر حالة دون اتصال مع وسم «آخر تحديث» | تقييم 2026-10-25 §3.5 |
| `DownloadManager` و`ACTION_CREATE_DOCUMENT` | **مؤجَّل** (تصدير). تنزيل الحلقات للمتعلم في `EpisodeDownloads` | — |
| `resolveLegacyBookIdentity` (L352–368) | **يُسقط** | الهوية من الخادم |

## 3. الـ API للقراءة `/v3/operator`

**الكود:** `packages/api/src/b2c_api/operator/` على نمط `/v3/learner`:

| الملف | الدور |
|---|---|
| `schemas.py` | نماذج الاستجابة (العقد). لا حقل فيها لمدخلات التشغيل (`engine_profile`، و`qe_profile`، و`profile_fingerprint`) ولا لـ URI تخزين ولا DSN ولا رمز. مقاييس البوابة تُختصر إلى قيم مفردة |
| `reader.py` | **المكان الوحيد** الذي يقرأ Postgres والتخزين وTemporal (`OperatorReader` واجهة، و`PostgresOperatorReader` للإنتاج) |
| `progress.py` | حساب المراحل (دوال صرفة): الحالة، والبوابة، والأعداد، والمعدل، والوقت المتبقي |
| `router.py` | المسارات، وتخزين مؤقت 20 ث، وETag/304 |
| `openapi.py` + `scripts/export_operator_openapi.py` | يولّد `docs/api/operator-v3.openapi.json`، واختبار انحراف يفشل إن اختلف الملف عن الكود |

**المسارات (كلها `GET`، خلف الرمز نفسه؛ middleware ‏`app.py`):**

| المسار | يعيد |
|---|---|
| `/v3/operator/meta` | المراحل التسع بالترتيب، واسم كل منها، ومراحل السجل التي تُقرأ لها، ومتطلباتها، و`cache_ttl_s=20`، و`refresh_interval_s=15` |
| `/v3/operator/books` | مكتبة الكتب من كل المقررات: العنوان، والهوية (`course_id`، و`book_id`، وsha مختصر)، وتاريخ الرفع، والدور، والمرحلة الحالية وحالتها، والنسبة الكلية، وعدد المراحل المكتملة. مرتبة بالأحدث نشاطًا |
| `/v3/operator/courses/{course_id}/books/{book_id}/stages` | المراحل التسع لكتاب واحد |
| `/v3/operator/courses/{course_id}/books/{book_id}/stages/{stage_id}` | تفاصيل مرحلة: المخرجات وبواباتها، وتشغيلات الترجمة، وعيّنة آخر 10 وحدات مترجمة (أصل، وترجمة، وقرار)، وحواشي المترجم (أول 50)، وأسباب الرفض (50) |

**المراحل بالمسار الجديد، ومصدر كل منها:**

| # | `stage_id` | الاسم | المصدر | تنتظر |
|---|---|---|---|---|
| 1 | `extraction` | الاستخراج | `ingest-parse-pdf` في السجل؛ وإلا `parsed_artifact_ref` في مكتبة الكتب (مخرَج قديم «بلا بوابة») وworkflow الاستخراج | — |
| 2 | `outline` | المخطط | `build-outline-v2` | الاستخراج |
| 3 | `termbase` | قاموس المصطلحات | `book_termbase_entries` (المرشحون = معتمد + مرفوض + معلّق، الحاضرة فقط) و`book_termbase_builds`؛ والبوابة من `book-termbase-approved` | الاستخراج |
| 4 | `translation` | الترجمة | `translation_runs` و`translation_unit_results` لآخر تشغيل للكتاب: مقبول (`accepted`)، وحاشية (`needs_review`، D-040)، ومُسقط (`dropped`)، ومرفوض (`rejected`)، والمتبقي = عدد الوحدات − المحسوم. المعدل = الوحدات المحسومة في آخر ساعة، والوقت المتبقي = المتبقي ÷ المعدل. والبوابة من `translate-book` لهذا التشغيل | قاموس المصطلحات |
| 5 | `translator_notes` | حواشي المترجم | `translator-notes` لهذا التشغيل، وعدد الحواشي حتى الآن = حاشية + مرفوض | الترجمة |
| 6 | `knowledge` | المعرفة والمفاهيم | `knowledge-research` ثم `concept-layer` | قاموس المصطلحات |
| 7 | `episodes` | الحلقات | `episode-script-en` ثم `episode-script-ar`؛ والإجمالي = حلقات المخطط v2 | المخطط، والحواشي، والمعرفة |
| 8 | `audio` | الصوت | `speech-text` ثم `speech-track` | الحلقات |
| 9 | `mp3` | MP3 | `episode-mp3`؛ والإجمالي = حلقات المخطط | الصوت |

**قواعد الحالة:** مكتملة، أو جارية، أو فشلت، من بيانات المرحلة نفسها (سجل الترقية، أو أعداد الوحدات، أو حالة الـ workflow). المرحلة بلا بيانات تكون **«بانتظار مرحلة سابقة»** ما دام أحد متطلباتها غير مكتمل، و**«لم تبدأ»** إن اكتملت متطلباتها. والترجمة «فشلت» إذا فشل الـ workflow أو انتهى قبل حسم كل الوحدات.

**البوابة:** من `stage_output_gates` (آخر `GateResult` لكل بوابة): ناجحة (`accepted`)، أو فاشلة (`rejected` أو بوابة لم تنجح)، أو قيد الفحص (`candidate`)، أو «لا توجد نتيجة مسجّلة».

**ربط مخرجات السجل بالكتاب:** بـ `config.book_id`، أو `config.run_id` (تشغيل ترجمة للكتاب)، أو بالنَّسَب: مخرج أحد مدخلاته (`inputs`) مخرجٌ للكتاب، تكرارًا حتى الثبات. المقرر ذو الكتاب الواحد تُنسب كل مخرجاته لكتابه.

**Temporal:** `describe` لـ workflow الاستخراج ولآخر workflow ترجمة (`b2c-translate-book-<run_id>`) بمهلة 3 ث: الحالة (جارٍ، أو منتهٍ، أو فاشل، أو غير معروف) والنشاط الحالي من `pending_activities`. تعذّر Temporal لا يُسقط الصفحة: حالته «غير معروف». والـ workflow الذي انتهت مدة حفظه في Temporal يظهر بلا حالة.

**الأداء (قاعدة البيانات تخدم الترجمة الجارية):**
- اتصال قراءة فقط: `default_transaction_read_only=on` و`statement_timeout=5000`. لا `ensure_schema` ولا DDL.
- كل استعلام مقيد بمفتاح مفهرس: `stage_outputs (course_id, …)`، و`stage_output_gates` (فهرس UNIQUE يبدأ بـ `course_id`)، و`translation_unit_results (run_id, decision, seq)`، و`book_termbase_entries (book_id, …, status)`، و`translation_runs` بـ `course_id` (جدول صغير، صفّان الآن).
- التجميع في القاعدة: `GROUP BY run_id, decision`. وأحدث الوحدات: المفاتيح أولًا (`LIMIT 10`) ثم قراءة JSON لهذه الصفوف فقط. وكل قائمة بحدّ (`LIMIT`).
- تخزين مؤقت في الخادم 20 ث لكل مفتاح، مع قراءة واحدة جارية لكل مفتاح؛ فأي عدد من الهواتف يتحدث كل 15 ث يكلّف قراءة واحدة لكل مفتاح كل 20 ث.
- ETag قوي و304 عند `If-None-Match`.
- **قياس حي (قراءة فقط، 2026-10-26):** شُغّل `PostgresOperatorReader` نفسه داخل حاوية `b2c-api-1` ضد Postgres وTemporal والتخزين الحقيقيين لكل المقررات الـ 29. نجحت كل الاستعلامات. والنتيجة للكتابين في `b2c-demo`: الترجمة 2,852 من 4,560 بمعدل 196 وحدة/ساعة، و500 من 5,167 بلا تقدم في آخر ساعة. وسجل الترقية (`stage_outputs`) **فارغ** في القاعدة الحية، فكل البوابات الآن «لا توجد نتيجة مسجّلة».

## 4. القسم في التطبيق (`android/learner-app` 0.2.0)

**التنقل:** شريط سفلي بتبويبين على الشاشتين الرئيسيتين: «تعلّم» (قسم المتعلم كما هو) و«المراحل». ثم: مكتبة الكتب ← مراحل الكتاب ← تفاصيل المرحلة. التبديل بين التبويبين تلاشٍ، والدخول والرجوع انزلاق يتبع اتجاه التخطيط (من اليمين إلى اليسار)، وكل تبويب يحفظ موضعه (`saveState`/`restoreState`). زر الرجوع يرجع خطوة.

**الشاشات (`ui/operator/`):**

| الشاشة | ما فيها |
|---|---|
| مكتبة الكتب | لكل كتاب بطاقة: العنوان، و`course_id · sha`، وتاريخ الرفع، و«الآن: <المرحلة>» مع شارة حالة، وشريط التقدم الكلي، و«اكتمل N من 9 مراحل · %» |
| مراحل الكتاب | خط زمني رأسي: دائرة مرقمة (✓ للمكتملة، ✗ للفاشلة)، ثم بطاقة لكل مرحلة: الاسم والحالة، وشريط تقدم، و«المنجز من الإجمالي · % · وحدة/ساعة»، و«المتبقي: 8 س 42 د»، وشرائح الأعداد (مقبول، وحاشية، ومُسقط، ومرفوض، ومتبقٍ، والإجمالي؛ أو المرشحون والمعتمد والمرفوض)، والبوابة، وحالة Temporal ونشاطه، وآخر تحديث، وملاحظة («بانتظار: …») |
| تفاصيل المرحلة | البطاقة نفسها، ثم: تشغيلات الترجمة، وآخر الوحدات المترجمة (الأصل LTR، والترجمة، والقرار، وملاحظات الفحص)، وحواشي المترجم «(أول 50 من 214)»، وأسباب الرفض (العنوان بالعربية «حذف · حرِج» ونص الفحص الأصلي)، والمخرجات وبواباتها ومقاييسها |

**الانسيابية:**
- البيانات المخزنة (`ResponseCache` في `files/operator-cache`) تظهر فورًا، ثم طلب شرطي `If-None-Match`.
- لا مؤشر يحجب الشاشة: مؤشر السحب للتحديث يظهر فقط عند سحب المستخدم أو أول تحميل بلا بيانات.
- تحديث تلقائي كل 15 ث **والشاشة ظاهرة فقط** (`repeatOnLifecycle(RESUMED)` على مدخل التنقل)، صامت بلا أي مؤشر.
- أشرطة التقدم تنتقل بسلاسة إلى القيم الجديدة (600ms)، والقوائم تحرّك عناصرها (`animateItem`).
- الخادم الذي لم يُنشر عليه `/v3/operator` بعد (404): رسالة واضحة «يلزم نشر تحديث الخادم»، وقسم المتعلم يعمل.

**إمكانية الوصول (TalkBack):** كل بطاقة كتاب وكل مرحلة **عقدة واحدة** تُقرأ جملة كاملة، مثل: «4. الترجمة: جارية. 2,852 من 4,560. 63٪. مقبول 2,540، حاشية 208، مُسقط 100، مرفوض 4، متبقٍ 1,708. المتبقي: نحو 8 ساعات و42 دقيقة. البوابة: لا توجد نتيجة مسجّلة. حالة Temporal: جارٍ، النشاط الحالي: translate_unit_batch. آخر تحديث قبل دقيقة»، مع إجراء «عرض التفاصيل». العناوين `heading()`. الحالة نص ولون معًا. الأهداف ≥ 48dp. والعناوين اللاتينية بعزل اتجاه (`TextDirection.Content` وU+2068/U+2069).

**الوحدة:** تطبيق واحد. لا تغيير في شاشات المتعلم عدا الشريط السفلي على شاشته الرئيسية. العميل: `data/api/OperatorApi.kt` (Retrofit، GET فقط)، و`OperatorModels.kt`، و`data/OperatorRepository.kt`، و`domain/OperatorFormat.kt`. الرمز والخادم من شاشة الإعداد نفسها.

## 5. الأفعال المؤجلة (مرحلة لاحقة تحتاج موافقة المالك)

هذا القسم قراءة فقط. ما يلي **لم يُنفَّذ** ويحتاج قرارًا من المالك قبل أي عمل:

| الفعل | المسار الموجود في الخادم | ملاحظة |
|---|---|---|
| رفع كتاب | `POST /v1/books` | يبدأ workflow استخراج |
| بدء الترجمة بالمسار الجديد | `POST /v2/courses/{id}/book-translations` | يحتاج اختيار ملفي المحرك والجودة |
| إنهاء مراجعة القاموس، واعتماد/رفض مصطلح | `POST /v2/book-translations/{run}/termbase/*` | قرار بشري على القاموس |
| إعادة التجميع وبناء الحواشي | `POST /v2/book-translations/{run}/assemblies` | |
| بدء المعرفة والحلقات والصوت وMP3 | لا مسارات API لها بعد (تُشغَّل من الـ worker/السكربتات) | تحتاج مسارات جديدة |
| إيقاف مؤقت واستئناف | `/v1/workflows/{id}/pause\|resume` | للمسار القديم؛ يلزم تعميمه |
| تصدير/تنزيل النواتج | `/v1/courses/{id}/results/*`، و`/audio/zip` | R0 بند 5: لا تصدير لما ليس final |
| نسخ `workflow_id`، والبحث في المكتبة | — | أفعال محلية صغيرة لم تُضف في 0.2.0 |

## 6. الاختبارات

**الـ API (pytest، الاختبارات أولًا):** `tests/unit/api/test_operator_v3.py`، 14 اختبارًا. كُتبت مع العقد قبل ربط المسارات بالتطبيق، وفشل أول تشغيل لها (`create_app` بلا `operator_reader`)، ثم نجحت بعد الربط:
- كل المسارات GET؛ والمراحل التسع بالترتيب.
- المكتبة: العنوان، والهوية، وتاريخ الرفع، والمرحلة الحالية، والترتيب بالأحدث.
- المراحل: مخرَج قديم بلا بوابة، والقاموس بأعداده، والترجمة بأعدادها ونسبتها ومعدلها ووقتها المتبقي ونشاط Temporal، وحواشي تشغيل أقدم لا تُكمل مرحلة التشغيل الحالي، و«بانتظار».
- بوابات السجل، والنَّسَب بين الكتب، وفشل الـ workflow.
- التفاصيل: العيّنة بالأصل، والحواشي، وأسباب الرفض بنص الفحص، والتشغيلات، وأسباب رفض المصطلحات والمخطط.
- 404 و422، و503 بلا تفاصيل، والتخزين المؤقت بين 15 و30 ث، وETag/304.
- لا مدخلات تشغيل ولا URI ولا DSN في أي استجابة.
- مطابقة `docs/api/operator-v3.openapi.json` للكود (اختبار الانحراف)، والعقد GET فقط.
- القارئ: اتصال قراءة فقط مع `statement_timeout`، وكل SQL `SELECT` مع `WHERE`، وتجميع `GROUP BY run_id, decision`، وكل قراءة لجدول الوحدات محدودة.

**Android (JVM وRobolectric على sdk 24 و35):**

| الملف | ما يغطيه |
|---|---|
| `OperatorApiClientTest` (MockWebServer) | المسارات، والرمز، و304، وتجاهل الحقول الجديدة |
| `OperatorOpenApiConformanceTest` | النماذج مقابل العقد الملتزم، والعميل GET فقط |
| `OperatorRepositoryTest` | المخزَّن أولًا ثم 304، والاحتفاظ بالبيانات دون اتصال، و404، وغياب الإعداد، والتحديث الصامت بلا مؤشر |
| `OperatorFormatTest` | الطوابع الزمنية، و«قبل …»، والوقت المتبقي مختصرًا ومنطوقًا، والأرقام |
| `OperatorScreensTest` (sdk 24 و35) | ما يقرؤه TalkBack لبطاقة الكتاب ولكل مرحلة، والضغط، وعدم حجب البيانات المخزنة، ورسالة الخادم غير المحدَّث، والتفاصيل، والتبويبان |

## 7. البناء والتسليم والمحاكي

- **الإصدار:** `0.2.0` (`versionCode 4`).
- **التوقيع الأساسي:** مفتاح الإصدار (`learner-signing.properties`) عبر `apksigner` بـ `--min-sdk-version 21 --v1-signing-enabled true --v2-signing-enabled true`، مع `extractNativeLibs=true` كما في 0.1.2 `compat-v1v2`. ملفات `META-INF/LEARNER-.SF` و`.RSA` موجودة. نسخة مفتاح debug لم تُبنَ (لم تعد لازمة).
- **خطأ وُجد على المحاكي فقط:** `String.format(Locale.ROOT, "%,d")` يرمي `ArithmeticException` على Android 7.0 (حجم التجميع 0)، ولا يظهر على JVM ولا Robolectric. أُصلح بـ `Locale.US` قبل البناء النهائي.
- **المحاكي:** `B2C_QA_API24` (Android 7.0) و`B2C_QA_API27` (Android 8.1)، بـ `-dns-server 100.100.100.100,192.168.1.1`:
  - نسخة **release universal** على الخادم الحقيقي عبر Tailscale: الإعداد ينجح، وتبويب «المراحل» يعرض «يلزم نشر تحديث الخادم» لأن `/v3/operator` لم يُنشر.
  - نسخة **debug universal** (`com.book2course.learner.debug`) على خادم معاينة محلي (`http://10.0.2.2:8199`) يشغّل كود `/v3/operator` نفسه على **لقطة بيانات حقيقية مقروءة** (قراءة فقط) من Postgres وTemporal والتخزين. الـ release لا يقبل HTTP، فلهذا استُعملت debug للبيانات.
  - التحديث التلقائي مقيس: 3 طلبات `stages` في 46 ث والشاشة ظاهرة (كلها 304)، و**0** في 46 ث بعد زر Home.
  - اللقطات وملخصات شجرة الإتاحة: `/root/projects/book2course/eval/android/2026-10-26-operator-section/` (22 ملفًا). المجلد `eval/android/` مستثنى في `.gitignore`، فهي على القرص لا في git.

## 8. ما لم يُنفَّذ أو لم يُتحقق منه

- نشر الـ API (`/v3/operator`) على الخادم: لم يُنشر؛ المالك ينشره بعد الدمج.
- TalkBack فعليًا على جهاز: المحاكيان بلا TalkBack. ما يُقرأ مثبت باختبارات Robolectric وبشجرة الإتاحة (`uiautomator dump`).
- جهاز ARMv7 حقيقي: لم يُجرَّب (المحاكيان x86_64).
- البوابات الحقيقية: سجل الترقية فارغ في القاعدة الحية، فلم تُعرض بوابة حقيقية؛ العرض مغطى بالاختبارات.
- حالات Temporal القديمة: workflows الاستخراج القديمة انتهت مدة حفظها في Temporal، فتظهر بلا حالة.
- البحث في المكتبة ونسخ `workflow_id`، وكل الأفعال في §5.
