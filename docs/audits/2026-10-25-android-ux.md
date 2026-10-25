# تقييم تجربة الاستخدام لتطبيق Android الحالي — 2026-10-25

- **الفرع:** `hardening` (من `bfbf7f4`)، في worktree معزول.
- **المفحوص:** `android/operator-app` (الإصدار `0.5.11`، `versionCode 28`): ملف `MainActivity.java` واحد (777 سطرًا، نحو 221 KB، أطول سطر 5,094 حرفًا) ومعه `TokenVault` و`EndpointRouting` و`BookIdentity`.
- **المنهج:** قراءة الكود والـ manifest وملفات البناء، وعدّ الأنماط بـ `grep`. لم يُشغَّل التطبيق على جهاز أو محاكٍ في هذا التقييم، ولم يُختبر مع TalkBack فعليًا. كل حكم على سلوك TalkBack هنا **استنتاج من الكود** (لا توجد أي خاصية إتاحة في الكود ليُختبر سلوكها).
- **أرقام الأسطر:** تشير إلى `android/operator-app/app/src/main/java/com/book2course/operator/MainActivity.java` ما لم يُذكر غيره. الأسطر طويلة جدًا (السطر الواحد قد يحوي عشرات الدوال)، فالدليل «سطر + العبارة».
- **المرجع السابق:** تدقيق التسليم `docs/audits/2026-10-24/09-delivery-api-app.md` §2.2 وH2 وM7 و§5.5. هذا التقرير لا يكرره، بل يضيف منظور المتعلم: التنقل، وRTL، وTalkBack، والحالة، والعمل دون اتصال، والأخطاء، وإعداد الرمز.

## 1. الخلاصة

التطبيق الحالي **أداة مشغّل** لإطلاق مراحل خط الإنتاج ومراقبتها، وفيه مشغل صوت ثانوي. ليس فيه أي شيء من تجربة المتعلم التي يطلبها المقترح (`docs/proposals/2026-10-25-learning-companion.md`): لا قائمة حلقات `00..n`، ولا أقسام داخل الحلقة، ولا نص متزامن، ولا بطاقات مصطلحات، ولا وضع أسئلة، ولا علامات وملاحظات، ولا تقدم لكل حلقة وكورس.

والأهم للمتعلم الكفيف:
- **صفر** `setContentDescription`.
- لا عناوين (headings).
- أحجام بالبكسل لا بالـ dp.
- تشغيل الصوت داخل الـ Activity بلا خدمة ولا `MediaSession`، فلا تحكم من شاشة القفل ولا من سماعة Bluetooth.

**القرار:** لا يُصلَح التطبيق الحالي للمتعلم، بل يُبنى تطبيق متعلم جديد (`android/learner-app`) بـ Kotlin وCompose وMedia3، وفق توصية التدقيق 09 §5.5. **وظائف المشغّل تبقى في التطبيق القديم مؤقتًا** إلى أن يُبنى تطبيق مشغّل على العميل المولّد (خطوة 8 في خطة التدقيق 09)، لأن نقلها خارج نطاق هذه المهمة ولا يخدم المتعلم.

## 2. جرد الشاشات والتدفقات

| الشاشة (زر في الشريط السفلي) | ما فيها | الدليل |
|---|---|---|
| المكتبة | رفع PDF، واختيار الكتاب والمقرر، وأدوار الكتب (رئيسي/مساند)، ومجلدات المقررات | السطر 22 (`names={"المكتبة","إعداد المراحل","المتابعة","المخرجات","الإعدادات"}`)، والسطر 38 (`uploadStatus`، `uploadBar`) |
| إعداد المراحل | اختيار الناتج المستهدف، وسلسلة المتطلبات، وأزرار بدء المراحل، واختيار النماذج | السطر 46 (`target`، `contentKind`، `outputLanguage`، `audioMode`)، والسطر 52 (`pipelineButton`) |
| المتابعة | 5 بطاقات تقدم للمراحل مع استئناف وإعادة | السطر 54 (`names={"رفع الكتاب وOCR",…}`)، والسطر 100 (`stageBars[i]`) |
| المخرجات | معاينة النتائج، وتنزيل ZIP، ومشغل صوت (تقديم وتأخير، وسرعة 0.75–2، وتنزيل) | السطر 61 (`buildResultsScreen`)، والسطر 63 (`buildAudioScreen`)، والسطر 422 (`speeds={0.75f,…,2f}`) |
| الإعدادات | عنوان الخادم، ورمز الوصول، ومزودو النماذج والمفاتيح | السطر 80 (`secretInput`)، والسطر 193 (`تغيير الخادم`)، والسطر 199 (`ensureApiToken`) |

**التدفقات الفعلية:**
1. أول طلب يحتاج الرمز يفتح حوارًا ويحجب خيط العمل بـ `CountDownLatch` حتى يُكتب الرمز (السطر 199).
2. التنقل بين الشاشات يستبدل `View` داخل `FrameLayout` (السطر 95، `screenFrame.removeAllViews(); screenFrame.addView(pages[index]…)`). لا back stack، ولا معالجة لزر الرجوع: لا يوجد `onBackPressed` ولا `OnBackPressedCallback` (‏0 نتيجة)، فزر الرجوع يغلق التطبيق من أي شاشة.
3. التفاصيل تُعرض في حوارات: 44 موضع `new AlertDialog.Builder`. بعضها يأخذ 82–90% من ارتفاع الشاشة (السطر 128 و298، `heightPixels*0.9` و`*0.82`).
4. الاستماع:
   - المشغل في شاشة «المخرجات»: اختيار ملف من `Spinner`، ثم تشغيل (السطر 423).
   - الموضع يُحفظ في `SharedPreferences` كل 500ms أثناء التشغيل (السطر 424).
   - الحفظ أيضًا في `onPause` (السطر 507).

## 3. المشكلات

### 3.1 التنقل والبنية

| المشكلة | الأثر على المتعلم | الدليل |
|---|---|---|
| لا back stack ولا زر رجوع | الرجوع يخرج من التطبيق بدل الرجوع خطوة. ولا يعرف المستخدم أين هو في التسلسل | السطر 95، وغياب `onBackPressed` |
| خمسة تبويبات للمشغّل | المتعلم لا يحتاج أي تبويب منها: الصوت مدفون في «المخرجات» بين ZIP ومعاينة OCR | السطر 22 والسطر 61 |
| الحوارات بدل الشاشات | لا انتقال مرئي، ولا يحفظ الحوار حالته، وTalkBack يدخل نافذة جديدة كل مرة | 44 حوارًا |
| الواجهة تُبنى برمجيًا بالكامل | كل تعديل في التخطيط تعديل في سطر من 5,000 حرف. لا `res/`، ولا `values-ar`، ولا معاينة | التدقيق 09 §2.2، والسطر 20–29 |
| الحالة في متغيرات الـ Activity | لا `onSaveInstanceState` (‏0 نتيجة) ولا ViewModel. موت العملية في الخلفية يعيد التطبيق إلى الشاشة الأولى | السطر 10–16 |

### 3.2 RTL واللغة

| المشكلة | الدليل |
|---|---|
| الـ manifest بلا `android:supportsRtl="true"`، فالنظام لا يعكس التخطيط | `AndroidManifest.xml` السطر 3 |
| الاتجاه يُفرض يدويًا على بعض الحاويات فقط (`setLayoutDirection(LAYOUT_DIRECTION_RTL)`)، والحوارات والـ `Spinner` لا تتبعه | السطر 29 و276 و293 (8 مواضع فقط) |
| المحاذاة بـ `Gravity.RIGHT` (4 مواضع) لا `Gravity.START`، وصفر `START/END`، فلا ينعكس النص إذا تغيّر الاتجاه | السطر 132 (`t.setGravity(Gravity.RIGHT)`) |
| النصوص العربية في الكود لا في `strings.xml`، فلا يمكن ترجمة الواجهة ولا مراجعة صياغتها | كل الملف |
| المصطلح الأصلي (لاتيني) داخل جملة عربية بلا عزل اتجاه | لا يوجد عرض للمصطلحات أصلًا |

المرجع: [دعم اللغات وRTL](https://developer.android.com/training/basics/supporting-devices/languages) و[الترجمة والموارد](https://developer.android.com/guide/topics/resources/localization).

### 3.3 الإتاحة (TalkBack، والخط الكبير، والمتعلم الكفيف)

| المشكلة | لماذا هي حرجة | الدليل |
|---|---|---|
| **0** `setContentDescription`، و**0** أي API إتاحة (`setAccessibilityHeading`، و`announceForAccessibility`، و`importantForAccessibility`) | الأزرار النصية تُقرأ. لكن شريط التقدم (`SeekBar`) يُقرأ نسبة بلا معنى، و`ProgressBar` المراحل بلا وصف، ولا عناوين للتنقل السريع بـ TalkBack | grep على الملف |
| أحجام بالبكسل الخام | أزرار الشريط السفلي ارتفاعها 60px. على شاشة xxhdpi هذا 20dp، أقل كثيرًا من الحد الأدنى 48dp. والنص 11sp داخل 60px يُقص عند تكبير الخط | السطر 23 (`new LinearLayout.LayoutParams(0,60,1)`، `setTextSize(11)`)، والسطر 24، والسطر 29 (`setPadding(18,14,18,26)` بالبكسل) |
| لا وصف لحالة التشغيل ولا إعلان لتغيرها | الكفيف لا يعرف هل الصوت يعمل، ولا أين هو في الحلقة | السطر 423–426 |
| التبديل بين الشاشات بلا إعلان ولا عنوان | TalkBack لا يعلن الشاشة الجديدة. التركيز يبقى على زر الشريط | السطر 95 |
| `Toast` للأحداث المهمة | يختفي، وقد لا يُقرأ كاملًا | السطر 209 و267 و286 |
| الأخطاء تُكتب في سجل نصي عام | الخطأ بعيد عن العنصر الذي سببه، والكفيف لا يجده | السطر 753 (`void add(String s)`) |
| `screenOrientation="portrait"` مفروض | يمنع الوضع الأفقي، ويخالف معيار WCAG 2.2 للاتجاه (SC 1.3.4) | `AndroidManifest.xml` السطر 4 |

المراجع:
- [Compose accessibility](https://developer.android.com/develop/ui/compose/accessibility) و[Semantics](https://developer.android.com/develop/ui/compose/accessibility/semantics) و[دمج العقد وتصفيتها](https://developer.android.com/develop/ui/compose/accessibility/merging-clearing).
- [أسس الإتاحة في Android](https://developer.android.com/guide/topics/ui/accessibility/apps): هدف لمس 48dp، ووصف المحتوى، والعناوين.
- [Material 3 accessible design](https://m3.material.io/foundations/accessible-design/overview).
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) (SC 1.3.4، و1.4.4، و2.5.8، و4.1.2).
- [وحدات الكثافة dp/sp](https://developer.android.com/training/multiscreen/screendensities).

### 3.4 تشغيل الصوت

| المشكلة | الأثر | الدليل |
|---|---|---|
| `android.media.MediaPlayer` داخل الـ Activity، بلا Service | التشغيل مرتبط بعمر الشاشة. لا تحكم من الإشعار ولا من شاشة القفل ولا من Bluetooth، والنظام قد يقتل العملية في الخلفية | السطر 2 و10 و423 |
| لا `MediaSession` ولا إشعار وسائط | أزرار السماعة والسيارة لا تعمل. وTalkBack لا يرى تحكمًا موحدًا | 0 نتيجة لـ `MediaSession` و`Notification` (عدا DownloadManager) |
| لا audio focus | الصوت لا يتوقف عند مكالمة أو إشعار صوتي | 0 نتيجة لـ `AudioFocus` |
| لا «becoming noisy» | فصل السماعة يُخرج الدرس من مكبر الهاتف | — |
| حفظ الموضع كل 500ms عبر `SharedPreferences.apply()` | كتابة قرص متكررة بلا داعٍ | السطر 424 |
| السرعة تُطبَّق بـ `PlaybackParams`، والخطأ يُبتلع | تغيير السرعة قد يفشل بصمت | السطر 422 (`catch(Exception ignored)`) |
| لا أقسام ولا قفز بينها، ولا مؤقت نوم، ولا علامات | لا تنقل بأسلوب DAISY داخل الحلقة | — |

المراجع:
- [Media3 ExoPlayer](https://developer.android.com/media/media3/exoplayer).
- [التشغيل في الخلفية بـ MediaSessionService](https://developer.android.com/media/media3/session/background-playback).
- [التحكم بالتشغيل والأوامر المخصصة](https://developer.android.com/media/media3/session/control-playback).
- [Audio focus](https://developer.android.com/guide/topics/media-apps/audio-focus).
- [DAISY](https://daisy.org/activities/standards/daisy/).

### 3.5 الحالة والعمل دون اتصال والأخطاء

| المشكلة | الدليل |
|---|---|
| لا cache للاستجابات: كل شاشة تجلب من الشبكة، ودون اتصال لا يظهر شيء | لا يوجد تخزين للاستجابات. `SharedPreferences` تحفظ معرّفات العمل فقط (السطر 16: مفاتيح `"i"` و`"x"` و`"q"`…) |
| polling كل 10 ثوانٍ في الخلفية | السطر 17 (`scheduleWithFixedDelay(…,3,10,TimeUnit.SECONDS)`) |
| 27 `catch(Exception ignored)`: أخطاء تُبتلع بلا أثر للمستخدم | مثال: السطر 17 و40 و63 |
| 83 `runOnUiThread` و`ExecutorService` يدوي، وحالة مشتركة بلا تزامن واضح | السطر 6–7 |
| مؤشرات تحميل تحجب الشاشة أو لا تظهر أبدًا. لا تمييز بين «لا بيانات» و«تعذر الجلب» و«لم يُنشر بعد» | السطر 63 (`audioListStatus`) |
| التنزيل عبر `DownloadManager` النظامي، مع تسليم الرمز له، والحفظ في مجلد Downloads العام | السطر 430 (`r.addRequestHeader("Authorization","Bearer "+token)`، `setDestinationInExternalPublicDir(DIRECTORY_DOWNLOADS…)`) |

المراجع: [Offline-first data layer](https://developer.android.com/topic/architecture/data-layer/offline-first) و[دليل معمارية التطبيقات](https://developer.android.com/topic/architecture) و[WorkManager](https://developer.android.com/topic/libraries/architecture/workmanager).

### 3.6 المصادقة وإعداد الرمز

| النقطة | التقييم | الدليل |
|---|---|---|
| تشفير الرمز بـ AES-GCM عبر AndroidKeyStore | **جيد**، ويُنقل كما هو إلى التطبيق الجديد | `TokenVault.java` |
| الرمز يُطلب في حوار عند أول طلب، ويحجب خيط العمل | المستخدم لا يعرف لماذا ظهر الحوار، والطلب معلق حتى يجيب | السطر 199 |
| لا اختبار اتصال صريح قبل الحفظ، ورسالة الخطأ عامة («تحقق من اتصال الخادم ورمز الوصول») | السطر 82 |
| حقل الرمز بلا زر إظهار ولا تسمية دائمة. العنوان الافتراضي مدمج في البناء | السطر 199، و`app/build.gradle` السطر 2 |
| رفض `http` (EndpointRouting) | **جيد** ويتفق مع D-030 (tailnet وHTTPS) | `EndpointRouting.java` |
| `allowBackup="false"` | **جيد**: الرمز مربوط بمفتاح الجهاز | `AndroidManifest.xml` السطر 3 |

### 3.7 البناء

- ABI ‏`armeabi-v7a` فقط، ومعه مكتبة native بلا وظيفة (التدقيق 09، M7).
- لا `release buildType`، ولا R8.
- ثيم `Theme.Material.Light` القديم، لا Material 3.
- لا اختبارات unit، والاختبارات instrumentation فقط. وتشغيلها سُجل مرة «BLOCKED» (`UX-IMPLEMENTATION-2026-10-11.md`).

## 4. ما يحتاجه المتعلم ولا يوجد

يُقارن هنا بالمقترح §2 و§7 و§13–§15، وبالقرارات D-032 وD-036 إلى D-038:

| الحاجة | موجودة؟ |
|---|---|
| مكتبة كورسات، ثم قائمة حلقات `00..n` بعناوين وتقدم ومدة وحالة تنزيل | لا. قائمة ملفات MP3 في `Spinner` |
| أقسام الحلقة (تقدمة، ونص الكتاب، وملخص، ومصطلحات، و5 أسئلة) مع القفز بينها | لا |
| إعلان الصوت (المؤلف، والكورس، والمصدر الخارجي) | لا |
| نص متزامن مع الصوت، مع تحكم في حجم الخط والمظهر | لا |
| بطاقات المصطلحات بالأصل | لا |
| وضع أسئلة يخفي الإجابة حتى الكشف (R0) | لا. ومعاينة نتائج المشغّل تعرض الإجابات (السطر 61، «الأسئلة حسب الدرس»)، وهذا مقبول للمشغّل لا للمتعلم |
| علامات وملاحظات، واستئناف، وتقدم لكل حلقة وكورس | استئناف موضع الملف فقط |
| تنزيل للعمل دون اتصال داخل التطبيق | تنزيل إلى Downloads العام فقط |
| تشغيل في الخلفية مع إشعار وشاشة قفل | لا |

## 5. ما يُحتفظ به من القديم

- منطق `TokenVault` (AES-GCM وAndroidKeyStore)، أُعيدت كتابته بـ Kotlin في `data/config/ServerConfig.kt`.
- رفض `http` إلا للمحاكي في بناء debug.
- `allowBackup=false`.
- عدم تتبع إعادة التوجيه في الطلبات الموثقة.
- فكرة فحص توافق الخادم، وصارت `GET /v3/learner/meta` مع `min_client_version`.

## 6. ما لم يُفحص (UNVERIFIED)

- تشغيل التطبيق القديم على جهاز مع TalkBack فعليًا. الأحكام في §3.3 من الكود فقط.
- السلوك على Android 7 وARMv7 الحقيقي.
- أداء المشغل مع ملفات طويلة على شبكة ضعيفة.

## 7. المصادر (فُتحت كلها في 2026-10-25 وأعادت HTTP 200)

- https://developer.android.com/topic/architecture
- https://developer.android.com/topic/architecture/data-layer/offline-first
- https://developer.android.com/develop/ui/compose/accessibility
- https://developer.android.com/develop/ui/compose/accessibility/semantics
- https://developer.android.com/develop/ui/compose/accessibility/merging-clearing
- https://developer.android.com/develop/ui/compose/accessibility/api-defaults
- https://developer.android.com/guide/topics/ui/accessibility/apps
- https://developer.android.com/training/multiscreen/screendensities
- https://developer.android.com/training/basics/supporting-devices/languages
- https://developer.android.com/guide/topics/resources/localization
- https://developer.android.com/media/media3/exoplayer
- https://developer.android.com/media/media3/session/background-playback
- https://developer.android.com/media/media3/session/control-playback
- https://developer.android.com/media/media3/exoplayer/downloading-media
- https://developer.android.com/guide/topics/media-apps/audio-focus
- https://developer.android.com/topic/libraries/architecture/workmanager
- https://developer.android.com/develop/ui/compose/animation/shared-elements
- https://developer.android.com/guide/navigation/design/type-safety
- https://developer.android.com/develop/ui/compose/layouts/insets
- https://developer.android.com/training/articles/security-config
- https://developer.android.com/develop/ui/compose/testing
- https://m3.material.io/foundations/accessible-design/overview
- https://m3.material.io/components/sliders/accessibility
- https://www.w3.org/TR/WCAG22/
- https://daisy.org/activities/standards/daisy/
- https://robolectric.org/
