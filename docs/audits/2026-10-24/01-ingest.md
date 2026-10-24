# تدقيق المرحلة 01: الاستخراج (Ingestion: PDF/OCR → ParsedBook)

- **التاريخ:** 2026-10-24
- **الفرع:** `hardening`، وآخر commit هو `5870d57`
- **النطاق:** `packages/ingest`، و`packages/orchestration/src/b2c_orchestration/ingest.py`، و`docker/worker`، و`eval/suites/phase2`، و`eval/reports` (phase-2* وocr*)، والعقود في `packages/contracts`.
- **طريقة العمل:** قراءة فقط. لم أعدّل أي كود أو إعداد أو بيانات. قرأت artifacts حقيقية من MinIO عبر `docker exec -i b2c-worker-1 python -` باستخدام boto3، وقارنتها بطبقة النص الأصلية في ملفات PDF المصدرية المخزنة. لم أرسل أي محتوى من الكتب إلى خدمة خارجية.
- **المبدأ:** لا تعني "COMPLETED" أن الناتج صحيح. كل رقم في هذا التقرير مقيس، والطريقة مذكورة معه. وما لم أتحقق منه موسوم بـ **UNVERIFIED**.

---

## 1. غرض المرحلة وعقدها

### 1.1 الغرض
تحوّل هذه المرحلة ملف PDF إنجليزيًا، رقميًا أو ممسوحًا، إلى `ParsedBook`، وهو تمثيل منظّم للكتاب. يجب أن يحفظ هذا التمثيل أربعة أشياء:
1. **النص كاملًا ودقيقًا**، دون فقد أو تكرار أو تشويه.
2. **البنية**: العناوين بمستوياتها، والفقرات، والقوائم، والجداول، والأشكال وتعليقاتها، والحواشي، والمراجع، وفهرس المحتويات، والفهرس الأبجدي، والترويسات والتذييلات.
3. **ترتيب القراءة**، بما فيه دمج الفقرات التي تعبر الصفحات والأعمدة.
4. **الإسناد (provenance)**: الصفحة الفعلية، ورقم الصفحة المطبوع، وbbox، ومصدر النص (طبقة نص أو OCR) ودرجة الثقة.

### 1.2 العقد الحالي (`packages/contracts/src/b2c_contracts/models.py:70-117`)
- **المدخل:** `BookUpload`، وفيه `book_id` و`role` و`file_sha256` و`mime` و`title` و`rights_status` و`ocr_language ∈ {en, ar}`، إضافة إلى ملف المصدر في MinIO.
- **المخرج:** `ParsedBook`، وفيه `book_id` و`title` و`role` و`page_count` و`toc_source` و`blocks[]` و`parser: dict[str,str]`.
  - كل `Block` فيه: `block_id = f"{book_id}:{page}:{seq}"`، و`type` من 11 نوعًا (heading، paragraph، list_item، table، figure، caption، equation، code، footnote، page_header، page_footer)، و`level` و`text`، و`refs[PageRef(page_no, printed_label?, bbox?)]`، و`section_path` و`ocr`، و`confidence?`.
- **البوابة التعاقدية** (`docs/02-CONTRACTS.md:48`): «كل block ليه `refs` ≥ 1، و`page_count` صحيح». هذه البوابة تفحص الشكل فقط.

### 1.3 ما تحتاجه المراحل اللاحقة فعلًا
| المرحلة اللاحقة | ما تحتاجه من الاستخراج |
|---|---|
| Outline (L2) | عناوين صحيحة الهرمية، وحدود أقسام دقيقة، وتمييز المواد الأمامية والخلفية (TOC، الشكر، المراجع، الفهرس) عن المتن. |
| Indexing/RAG | فقرات منطقية غير مقطوعة، ونص نظيف بلا ligatures ولا كلمات ملتصقة، وصفحة مصدر صحيحة لكل مقطع. |
| الترجمة | وحدات بحجم الفقرة، وأنواع خاصة (حاشية، مرجع، فهرس، TOC، ترويسة)، وعلامات الحواشي مربوطة بمواضعها. الـ contract الجديد `TranslationUnitKind` (`models.py:327-330`) يفترض وجود footnote وreference وtoc وindex وnoise، لكن الاستخراج لا ينتجها. |
| التأليف/QA/الصوت | إسناد صفحات صحيح للاستشهاد، ورقم الصفحة المطبوع لا الفعلي فقط، ونص قابل للنطق بلا أرقام صفحات ولا ترويسات داخل الجمل. |

---

## 2. التصميم كما هو منفّذ فعلًا

### 2.1 مسار التنفيذ
يحدد `IngestPDFActivities._parse_pdf` (`orchestration/.../ingest.py:84-229`) المحرّك من `B2C_OCR_ENGINE`، وقيمته في الحاوية الحالية `rapidocr`:

- **`rapidocr`، وهو الافتراضي** (`ingest.py:201-205`): يجرّب `parse_pdf_book` (pypdf) أولًا. إذا وُجدت **صفحة واحدة** فيها صورة وأقل من 8 محارف نص (`pdf_book.py:233-238`)، يُطلق `OCRRequired` ويُعاد **الكتاب كله** عبر `parse_pdf_with_docling`.
- **`surya`** (`ingest.py:174-186`): المنطق نفسه، مع فرق واحد: يمرّ الكتاب كله على `parse_pdf_with_surya`، فتؤخذ الصفحات النصية بـ pypdf كما هي، وتُرسل إلى Surya الصفحات «المتناثرة التي فيها صورة» فقط (`surya_ocr.py:196-200`).
- **`openrouter`** (`ingest.py:157-173`): الصفحة الممسوحة كلها تصبح block واحدًا، ونوعه paragraph أو table (`openrouter_ocr.py:357-380`).
- **Gemini للجداول** (`docling_pdf.py:260-277`): HTTP مباشر عبر `urllib` (`gemini_tables.py`) خارج `llm_gateway`.

الحصيلة: **ثلاث دلالات مختلفة لـ ParsedBook** بحسب المحرّك وبحسب وجود صفحة غلاف مصوّرة.
- **مسار pypdf:** block يساوي «نصًا بين سطرين فارغين»، وpypdf نادرًا ما ينتج سطرًا فارغًا، فيصبح الـ block عمليًا صفحة كاملة.
- **مسار Docling:** block يساوي عنصر layout.
- **مسار OpenRouter:** block يساوي صفحة.

### 2.2 تفاصيل مهمة في الكود
| الموضع | السلوك الفعلي |
|---|---|
| `pdf_book.py:123-127` `_paragraphs` | يقسّم على `\n\s*\n` فقط، ويُبقي فواصل الأسطر الصلبة داخل النص، ولا يعالج قطع الكلمات بالواصلة ولا الترويسات ولا الـ ligatures. |
| `pdf_book.py:180-182` | يضع عناوين الـ bookmarks دائمًا في أعلى الصفحة، حتى لو بدأ القسم في منتصفها. |
| `surya_ocr.py:242`، `openrouter_ocr.py:383` | ما زالا يستخدمان `_HEADING.match` القديم (prefix regex)، والكود نفسه يقرّ بأن له false positives (`pdf_book.py:45-48`). |
| `docling_pdf.py:249` | `document.iterate_items(traverse_pictures=True)` دون `included_content_layers`. تحققت في الحاوية أن `DEFAULT_CONTENT_LAYERS = {BODY}` في docling-core 2.99.0، فتُسقط الترويسات والتذييلات (furniture) وأي طبقة notes بصمت، دون أي تسجيل. |
| `docling_pdf.py:255-256, 317-321` | يؤخذ `prov[0]` فقط: صفحة واحدة لكل block حتى لو امتد على صفحتين، ولا يُخزَّن bbox رغم توفره. |
| `docling_pdf.py:307` | `level = depth+1`. عمليًا كل العناوين في المستوى 2 (انظر §3). |
| `docling_pdf.py:334` | `toc_source="headings" if headings else "pdf_outline"`: منطق خاطئ، لأن مسار Docling لا يقرأ الـ PDF outline أصلًا. |
| كل المسارات | `confidence` و`printed_label` لا يُملآن أبدًا. |
| `ingest.py:124, 141` | مفتاح الكاش يتضمن `upload` كاملًا، وفيه `book_id` و`title`، فالملف نفسه بـ SHA مختلف الـ book_id يُعالج من جديد. |
| `ingest.py:386-394` | activity واحدة مدتها حتى 24 ساعة للكتاب كله، دون دفعات صفحات ولا checkpoint جزئي. |
| `ingest.py:228` | المخرج `page_count` و`block_count` فقط. **لا يوجد أي تقرير جودة ولا بوابة جودة.** |

### 2.3 الكود المنشور يختلف عن الـ repo
قارنت الملفات داخل `b2c-worker-1`، وصورتها `b2c-worker:fixes-20261012`، بنسخ HEAD:
- `docling_pdf.py` و`pdf_book.py` و`gemini_tables.py` و`orchestration/ingest.py`: **مختلفة**.
- `surya_ocr.py` و`openrouter_ocr.py`: متطابقة.

النسخة المنشورة من `gemini_tables.py` ترسل مفتاح Gemini في **query string** في الـ URL. هذه الثغرة أُصلحت في commit `2ade43c` لكنها لم تُنشر. ويحمل الكود القديم والجديد كلاهما `STAGE_VERSION="6"`، فالـ artifacts الحالية لا يمكن إعادة إنتاجها من HEAD، والـ stage_key لا يعكس تغيّر الكود.

### 2.4 تعارضات الوثائق مع الكود
| الوثيقة | ما تقوله | الواقع |
|---|---|---|
| `00-DECISIONS.md` D-010 | «Parsing: Docling — Accepted» | المسار الافتراضي للكتب الرقمية هو pypdf بلا layout، وDocling مجرد fallback يُفعَّل بصفحة غلاف. |
| `05-PHASES.md:102` | «table/layout extraction» | مسار pypdf لا يستخرج جداول ولا أشكالًا ولا layout. صفر blocks من نوع figure في كل الـ artifacts. |
| `02-CONTRACTS.md:48` | «retry على دفعات صفحات» | لا توجد دفعات. activity واحدة للكتاب، وفشلها بعد إعادة تشغيل worker يتيّم المهمة كلها (`eval/reports/new-book-ingestion-recovery-2026-10-09.md`). |
| `03-TESTING.md:47-51` | T-ING-02 golden على 20 صفحة، وT-ING-04 لعمودين | `tests/golden/` فارغ، وT-ING-02/04 «Not run» (`eval/reports/phase-2.md:45-47`). |
| `pdf_book.py:3` docstring | «OCR is provided by the future Docling adapter» | قديم. |
| `phase-2.md:3` مقابل `PROJECT-STATUS.md:4` | «TEMPORARILY CLOSED BY OWNER» | «Phase 2 remains open». |
| `CLAUDE.md` القاعدة 5 | LLM عبر `llm_gateway` فقط | `gemini_tables.py` يستدعي HTTP مباشرة. |

---

## 3. الجودة المقاسة

### 3.1 العيّنة
هذه كل الـ artifacts الحقيقية من `ingest-parse-pdf` في bucket `artifacts`:

| Artifact (key) | الكتاب | المحرك | الصفحات | blocks |
|---|---|---|---:|---:|
| `b2c-demo/.../e40a641e…` | Structured Analytic Techniques، 3rd ed. (يُسمّى book1 في فهرس الترجمة) | surya + pypdf | 677 | 776 |
| `b2c-demo/.../469d6f0f…` و`0d97db6f…` (نسختان متطابقتان) | From Subordination to Revolution (Gramsci)، book2 | docling-ocr 2.131.0 | 415 | 2620 |
| `phase5-live-tidx01-20261004/.../816c2606…` | Think Stats 2e | pypdf | 264 | 421 |
| `phase2-verify-20261001/.../241bd71d…` | Lane ch.5 | pypdf | 37 | 42 |

### 3.2 طريقة القياس
سكربتات Python للقراءة فقط شُغّلت داخل `b2c-worker-1`. المرجع لكتابَي Gramsci وSAT هو طبقة النص الأصلية في `source.pdf` (`b2c-demo/book-upload/c5e8ea2c…/source.pdf` و`cde04e1e…/source.pdf`) مستخرجة بـ pypdf 6.1.3. المقاييس:
- **(أ) word recall/precision:** على مستوى الكتاب (multiset للكلمات ذات 3 أحرف فأكثر)، وعلى مستوى الصفحة.
- **(ب) التحقق من الإسناد:** بحث عن عبارات مميزة بعينها.
- **(ج) الكلمات الملتصقة:** token غير متكرر يمكن تقسيمه إلى كلمتين تتكرر كل منهما 20 مرة فأكثر في مجموع الكتب الثلاثة. هذا مقياس تقريبي فيه false positives، ويعطي حدًا أعلى.
- **(د) regex** للتباعد الحرفي والواصلات والـ ligatures والترويسات.
- **(هـ) مقطوع عند فاصل صفحة:** آخر block متني في الصفحة لا ينتهي بعلامة نهاية جملة.

السكربتات موجودة في scratchpad الجلسة: `/tmp/claude-0/-root/c9ec20dc-64ad-42ec-857d-86ecef50c15c/scratchpad/{stats,deep,cmp,cmp2,find,sat,glue,glue2,more}.py`.

**تحفّظ:** طبقة نص PDF ليست human gold، فهي قد تحمل أخطاءها الخاصة. لكنها مرجع جيد جدًا للكتب الرقمية، وتكشف الفقد والإسناد الخاطئ.

### 3.3 النتائج

**أ. التغطية والإسناد (Gramsci، مسار Docling):**
- word recall على مستوى الكتاب **96.5%** وprecision **98.6%**. الكلمات المفقودة أغلبها ترويسات جارية («expansive search for consent» ×63)، وحذفها صحيح، لكنه حذف صامت غير مسجَّل.
- **213 من 403 صفحة** (53%) فيها word recall أقل من 0.9 على مستوى الصفحة نفسها، و11 صفحة ليس لها أي block: [2, 3, 7, 9, 10, 11, 58, 139, 330, 331, 403]. المحتوى موجود في الكتاب، **لكنه مسند إلى صفحة خاطئة**:
  - نص الشكر في صفحتَي PDF رقم 10 و11 («four Gramsci conferences»، «Julie Gervais») موجود في block `…:8:7`، ونصه يبدأ بـ `'c o n t e n t s This book owes much…'`. أي أن عنوان فهرس المحتويات المتباعد الحروف دُمج مع صفحة الشكر وأُسند كله إلى صفحة 8.
  - متن صفحة 58 («genus-thinking and from a hierarchical») موجود في `…:57:2`، ومتن صفحة 64 في `…:63:4`.
  - عدد الـ blocks التي لها أكثر من ref: **0**. أي أن كل فقرة تعبر الصفحات تُسند إلى أولى صفحتيها فقط.
- عدد الـ blocks التي فيها `bbox`: **0 من 2620**، وفيها `printed_label`: **0**، رغم أن الـ PDF يحمل page labels (`'Cover','i','ii',…,'x','1'`). فلا يمكن ربط إحالة «ص 47» بالصفحة الفعلية 58.

**ب. التصنيف والجهاز العلمي (Gramsci):**
- توزيع الأنواع: list_item 1485، paragraph 1011، heading 115، code 8، table 1. لا يوجد أي footnote أو page_header أو page_footer أو caption.
- **الحواشي الختامية (endnotes) فقدت أرقامها.** فقط 2 من 1485 list_item تبدأ برقم. أمثلة: `…:366:11 'Femia, Political Thought, 52, 53.'` و`'SPN, 243; Q13, §7.'`. أما علامات الإحالة في المتن فبقيت أرقامًا عارية داخل الجمل (`'neo-Kantianism. 87 In chapters'`، من فهرس الترجمة §2.9). **صار ربط الإحالة بالحاشية مستحيلًا.**
- 8 blocks من نوع `code` هي في الحقيقة حواشٍ (`…:353:7 '140. Ibid., 119-124. 141. Ibid., …'`).
- فهرس المحتويات (صفحة 8) خرج list_items تنتهي بأرقام الصفحات (`'The Originality of the Living Gramscian Tradition 15'`). والمراجع والفهرس الأبجدي خرجا list_item/paragraph، والـ `section_path` الخاص بهما هو `['conclus i on']`.
- الـ table الوحيد هو قائمة الاختصارات (`…:332:1`).

**ج. العناوين (Gramsci):**
- 115 عنوانًا، **كلها level=2**، أي بلا أي هرمية، رغم أن الـ PDF فيه 14 مدخل outline تجاهلها مسار Docling.
- **63 من 115 عنوانًا (55%) متباعدة الحروف**: `'gr amsc i : lodes tar of renewal'` و`'l i ber al i sm and r at i onal i sm'`، ومعها 49 block فيها تتابع حروف متباعدة.
- OCR غلاف أنتج `'由'` (حرف CJK) في `…:4:3` دون أي فحص للـ script.

**د. Structured Analytic Techniques (مسار surya+pypdf):**
- 659 paragraph لـ 677 صفحة. الوسيط **block واحد لكل صفحة**، و**13 سطرًا** للـ block الواحد. 21 block تتجاوز 3000 محرف، وp90 = 2030 محرفًا.
- في النص 10,025 فاصل سطر صلب. العناوين الفرعية («Origins of This Technique»، «Description») وتعليقات الأشكال («Figure 7.9 …») مدفونة داخل نص الفقرة (`book-main:346:0`).
- **25% من الصفحات (163/653)** ينتهي آخر block فيها في منتصف جملة (`book-main:3:0 '…techniques that when'` ← `4:0 'applied result in…'`).
- 66 كلمة مقطوعة بواصلة وسطر (`'well-\ningrained'`). الكلمات الملتصقة نحو **0.94% من الـ tokens**، أي قرابة 1,100 كلمة: `'createand'`، `'ArgumentMapping'`، `'century,John'`، `'techniques,discusses'`.
- 5 عناوين خاطئة متعددة الأسطر من الـ regex القديم، منها `'5.1 SORTING\nSorting is a basic technique…'` و`'11.3.1 Structuring the Data\n11.3.2 Identifying Key …'` (جزء من فهرس المحتويات)، و`'PR EF A C E'`.
- الصفحات المتناثرة 22، منها 20 بلا صور، فلم تُرسل إلى OCR. لم أتحقق إن كانت فارغة فعلًا: **UNVERIFIED**.

**هـ. Think Stats (مسار pypdf):**
- 263 paragraph لـ 263 صفحة، والوسيط 31 سطرًا للـ block.
- **243 من 263 (92%)** فقرة تبدأ بترويسة جارية فيها رقم الصفحة (`'8.5. Exponential distributions 113'`).
- **909 حرف ligature** (U+FB00 ×345، وU+FB01 ×492، وU+FB03 ×47، وU+FB02 ×20، وU+FB04 ×5)، و315 كلمة مقطوعة بواصلة، و29% من الصفحات مقطوعة في منتصف جملة.

**و. Lane ch.5:** العنوان الوحيد هو `'Online_Statistics_Education.pdf'`، وهو اسم ملف جاء من الـ bookmark.

**ز. تكرار العمل:** الملف نفسه (SHA `70c88ae9…`) عولج مرتين تحت `book-main` و`book-70c88ae9…`، فنتج artifactان متطابقان إحصائيًا (2620 block في كل منهما)، أي ضعف وقت Docling على 415 صفحة.

### 3.4 الأثر المقاس في المنتج النهائي
من `outputs/translation-review-2026-10-24/TRANSLATION-ERROR-CATALOGUE.md` §3، وهي مراجعة لكل مقطع من 3396 مقطعًا: **1091 مقطعًا (32.1%) فيها عيب في المصدر نفسه**، وكل هذه العيوب تنشأ في هذه المرحلة. وانتهى 62.6% منها بحكم major أو critical، مقابل 55.1% للمقاطع ذات المصدر السليم.

| نوع عيب المصدر | عدد المقاطع | منها major أو critical |
|---|---:|---:|
| `ocr_noise` (حروف متباعدة، كلمات ملتصقة) | 283 | 227 |
| `cut_at_page_break` | 263 | 197 |
| `reference_list` (حواشٍ ومراجع كنثر) | 188 | 76 |
| `other` (عناوين مكررة، epigraph مدموج، mini-TOC) | 181 | 110 |
| `toc_or_index` | 147 | 53 |
| `merged_columns` (جداول مسطّحة: `NeutralPositiveNeutralStrong`) | 16 | 12 |
| `header_footer` | 13 | 8 |

يضاف إلى ذلك 66 خطأ `footnote_marker`، 51 منها قُبلت خطأً، و73 خطأ `bibliographic` و23 خطأ `segmentation`. ويقرر الفهرس نفسه أن «طول المقطع هو أقوى مؤشر على الفشل»، وطول المقطع ناتج مباشرة عن blocks بحجم الصفحة.

### 3.5 ما قاسه المشروع سابقًا، ولماذا لم يكشف شيئًا من هذا
- كل جهد بوابة Phase 2 ذهب إلى **CER للتعرّف الضوئي** على صفحات اصطناعية أو عيّنات ممسوحة (`phase-2.md:9-36, 75-113`)، مثل 3.09% و5.15% لـ PP-OCRv5 العربي و3.06% لـ PP-OCRv6 الإنجليزي.
- لكن **الكتب الحقيقية الثلاثة رقمية المصدر**. نسبة الـ blocks التي مرّت بـ OCR فيها: Gramsci 6/2620، وSAT 7/776، وThink Stats 0.
- العيوب الفعلية عيوب بنية وتخطيط ونص، و`eval/suites/phase2/ocr_eval.py` لا يقيس إلا CER/WER.
- اختبارات الوحدة الأربعون تنجح (`pytest tests/unit/ingest`: **40 passed**)، لكنها تستخدم صفحات `PdfWriter` اصطناعية وmocks. لا يوجد أي اختبار end-to-end لـ `parse_pdf_with_docling`، ولا أي اختبار لدمج الصفحات أو للترويسات أو للحواشي أو للـ ligatures.

---

## 4. المشكلات مرتّبة حسب الخطورة

### حرجة (Critical)

**C1. لا توجد بوابة جودة. "COMPLETED" تعني أن المعالجة لم تنهَر فقط.**
- **الدليل:** `02-CONTRACTS.md:48`، و`ingest.py:228`، وT-ING-02/03/04 «Not run/Fail» في `phase-2.md:45-47`، والمرحلة «temporarily closed by owner».
- **الأثر:** 32.1% من مقاطع الترجمة دخلت بمصدر معيب ولم يوقفها شيء.
- **السبب الجذري:** البوابات صُمّمت حول الشكل (refs ≥ 1) وحول CER للتعرّف الضوئي، لا حول صحة البنية والإسناد.

**C2. الـ block مربوط بالصفحة لا بالفقرة.**
- **الدليل:**
  - `pdf_book.py:123-127` و`block_id=f"{book_id}:{page}:{seq}"` (`models.py:98`).
  - SAT: وسيط block واحد لكل صفحة و13 سطرًا. Think Stats: 31 سطرًا.
  - 25% و29% من الصفحات مقطوعة في منتصف جملة.
  - 263 مقطع `cut_at_page_break`، منها 197 major أو critical.
- **الأثر:** وحدات ترجمة ضخمة تحمل جملًا نصفية، وعناوين وتعليقات أشكال مدفونة في الفقرات، وفواصل أسطر صلبة يعاملها نموذج الترجمة حدودًا للجمل.
- **السبب الجذري:** الاعتماد على `extract_text()` من pypdf كأنه محلّل بنية، ومعرّف block مبني على الصفحة.

**C3. الإسناد إلى الصفحات خاطئ وناقص.**
- **الدليل:**
  - `docling_pdf.py:256, 320` يأخذ `prov[0]` فقط، و0 blocks لها أكثر من ref.
  - 53% من صفحات Gramsci فيها per-page recall أقل من 0.9، و11 صفحة بلا blocks، ونص الشكر (صفحتان) مسند إلى صفحة 8.
  - bbox يساوي 0 وprinted_label يساوي 0، رغم وجود page labels.
- **الأثر:** كل استشهاد بالصفحة في الدرس أو الـ QA أو الـ RAG قد يشير إلى صفحة خاطئة، ونطاقات صفحات دروس الـ outline غير دقيقة، ولا يمكن ربط «ص 47» المطبوعة بالمصدر.
- **السبب الجذري:** تحويل DoclingDocument إلى ParsedBook يفقد معلومات (lossy)، والعقد لا يمثّل الـ spans متعددة الصفحات.

**C4. الجهاز العلمي غير ممثَّل: الحواشي والمراجع وفهرس المحتويات والفهرس الأبجدي.**
- **الدليل:**
  - 0 footnote في كل الـ artifacts. أرقام الـ endnotes حُذفت (2 فقط من 1485 list_item تبدأ برقم)، والحواشي صُنّفت code وlist_item، وفهرس المحتويات صُنّف list_item.
  - `DEFAULT_CONTENT_LAYERS={BODY}` يُسقط furniture وnotes دون أي سجل.
  - فهرس الترجمة: footnote_marker 66، وreference_list 188، وtoc_or_index 147، وbibliographic 73.
- **الأثر:** حواشٍ تُترجم كنثر منفصل عن سياقها، وأرقام إحالة عائمة، ومراجع وفهارس تمرّ عبر نموذج الترجمة، وتلوّث في RAG وQA (مداخل «Ibid.» ×48 كفقرات).
- **السبب الجذري:** لا توجد طبقة «apparatus» ولا تصنيف front/body/back matter، ونوع `footnote` في العقد لا يحمل أي ربط بموضع الإحالة.

### عالية (High)

**H1. لا توجد طبقة تطبيع نص.**
- **الدليل:** 55% من عناوين Gramsci متباعدة الحروف، و909 ligature في Think Stats، و315 و66 كلمة مقطوعة بواصلة، ونحو 0.94% كلمات ملتصقة في SAT، وترويسات جارية في 92% من فقرات Think Stats.
- **الأثر:** 283 مقطع `ocr_noise` (227 منها major أو critical)، وكلمات لا تطابق الـ termbase ولا البحث، ونص يُنطق في الصوت أرقام صفحات وترويسات.
- **السبب الجذري:** لا توجد مرحلة post-processing حتمية ذات نسخة (versioned) بين الاستخراج والعقد.

**H2. التوجيه بين المحرّكات «كل شيء أو لا شيء» ويعطي دلالات غير متسقة.**
- **الدليل:** `pdf_book.py:231-238` و`ingest.py:202-205`. صفحة الغلاف وحدها حوّلت كتاب Gramsci كله إلى Docling. ومسار Surya يتجاوز الصفحات المتناثرة التي بلا صور (`surya_ocr.py:198`)، ومحتوى 20 صفحة في SAT **UNVERIFIED**.
- **الأثر:** الكتاب نفسه ينتج ParsedBook مختلفة جذريًا بحسب صفحة الغلاف أو متغير بيئة، فتتلقى المراحل اللاحقة حبيبية وأنواعًا مختلفة.
- **السبب الجذري:** التوجيه على مستوى الكتاب لا الصفحة، ولا يوجد محلّل layout موحّد لكل الصفحات.

**H3. البنية الهرمية للعناوين خاطئة.**
- **الدليل:**
  - مسار Docling يتجاهل الـ outline (14 مدخلًا)، وكل العناوين level=2، و`toc_source` خاطئ (`docling_pdf.py:334`).
  - مسارا Surya وOpenRouter يستخدمان `_HEADING` القديم، فنتجت عناوين خاطئة تحمل فقرات كاملة.
  - الـ bookmarks توضع أعلى الصفحة (`pdf_book.py:180`).
  - عنوان `'Online_Statistics_Education.pdf'` في Lane.
- **الأثر:** outline بدروس خاطئة الحدود، و`section_path` مضلل، فالمراجع تقع داخل «conclusion».
- **السبب الجذري:** لا توجد مصالحة بين ثلاثة مصادر للبنية: الـ PDF outline، وفهرس المحتويات المطبوع، والعناوين المكتشفة.

**H4. انحراف بين الكود المنشور والـ repo، وهوية الـ stage لا تعكس الكود.**
- **الدليل:** §2.3. أربعة ملفات مختلفة، وكلها `STAGE_VERSION="6"`، والنسخة المنشورة ترسل مفتاح Gemini في الـ URL.
- **الأثر:** لا قابلية لإعادة الإنتاج، وكاش قد يخلط ناتج كود مختلف، وثغرة تسريب مفتاح في الإنتاج.
- **السبب الجذري:** نسخة الـ stage تُرفع يدويًا، ولا يدخل git SHA أو hash الكود في `options_hash`.

**H5. التقييم يقيس الشيء الخطأ.**
- **الدليل:** `ocr_eval.py` (CER/WER فقط)، و`tests/golden/` فارغ، ولا قياس لـ reading order ولا TEDS ولا تصنيف ولا إسناد.
- **الأثر:** «تحسّنات» لا علاقة لها بالعيوب الحقيقية، وثقة زائفة.
- **السبب الجذري:** افترضت خطة Phase 2 أن المشكلة في التعرّف الضوئي، بينما الكتب الفعلية رقمية.

### متوسطة (Medium)

- **M1. الجداول والأشكال:**
  - **الدليل:** SAT فيه 0 table رغم مصفوفات مسطّحة (`book-main:348:0`)، و0 figure و0 caption في كل الـ artifacts، وبقايا تنقّل e-book مثل «Back to Figure».
  - **الأثر:** 16 مقطع `merged_columns` أغلبها حرجة.
- **M2. `confidence` لا يُملأ أبدًا.**
  - **الدليل:** 0 من كل الـ blocks.
  - **الأثر:** يستحيل التوجيه الانتقائي إلى مراجعة بشرية. هذا مذكور في `00-DECISIONS.md:178` لكنه غير منفذ.
- **M3. لا dedup على مستوى المحتوى:**
  - **الدليل:** الكاش يتضمن `book_id` و`title` (`ingest.py:124`)، فعولج الكتاب نفسه مرتين. هذا يخالف D-023.
- **M4. activity واحدة للكتاب كله** (`ingest.py:386`):
  - **الأثر:** لا checkpoint لكل دفعة صفحات، وإعادة تشغيل الـ worker تيتّم 415 صفحة (`new-book-ingestion-recovery-2026-10-09.md`). وهذا يخالف `02-CONTRACTS.md:48`.
- **M5. مسارات خارجية داخل الحزمة نفسها:**
  - `gemini_tables.py` يستدعي HTTP مباشرة، وهذا يخالف القاعدة 5.
  - OpenRouter يجعل الصفحة block واحدًا (`openrouter_ocr.py:375-379`).
  - الـ Gemini الاختياري يعيد تحليل الكتاب كله بـ Docling (`openrouter_ocr.py:326-333`).
- **M6. العقد فقير:**
  - لا يوجد: spans متعددة الصفحات، ولا `reading_order`، ولا روابط الحواشي، ولا دور المادة (front/body/back)، ولا نص خام مقابل نص مطبّع، ولا أعلام جودة، ولا لغة.
  - `parser: dict[str,str]` نوع ضعيف.

### منخفضة (Low)

- **L1.** عناوين bookmarks تحمل أسماء ملفات (Lane).
- **L2.** نماذج Docling تُنزَّل بلا revision مثبت (`docker/worker/Dockerfile:19`). الإصدار مثبت ضمنيًا عبر نسخة docling فقط، وهذا يخالف القاعدة 9.
- **L3.** كود مكرر:
  - `pdf_text.py` يكرر منطق pypdf.
  - ثلاثة adapters تبني الـ blocks بثلاث طرق.
  - مسار العربية يرمي `ValueError` غير مصنّف (`docling_pdf.py:297`)، فيُعاد المحاولة به كخطأ عابر.
- **L4.** لا فحص script أو لغة: ظهر `'由'` من OCR الغلاف.

---

## 5. التصميم المستهدف وفق الممارسة الصناعية

### 5.1 المبادئ
1. **محلّل layout واحد لكل الصفحات**، رقمية كانت أو ممسوحة. يُؤخذ النص من طبقة PDF حين تكون سليمة، ومن OCR للمناطق التي لا نص فيها أو نصها فاسد. هذا هو نهج Docling وMinerU وMarker وPP-StructureV3: layout ثم reading order ثم استخراج حسب المنطقة.
2. **تمثيل وسيط بلا فقد (lossless):** يُحفظ `DoclingDocument` JSON (أو ما يعادله) كاملًا بكل طبقاته (body وfurniture وnotes) كـ artifact ثابت. ثم يُبنى `ParsedBook` بتحويل حتمي ذي نسخة. هذا القرار متسق مع D-010.
3. **النص الخام لا يُمس أبدًا.** كل تطبيع طبقة versioned تحفظ diff، وهذا متسق مع D-024 ومع ممارسة OCR-D.
4. **التوجيه على مستوى الصفحة أو المنطقة**، بناءً على قياس جودة طبقة النص، لا على متغير بيئة.
5. **بوابات حتمية أولًا، ثم إشارات دلالية معايَرة، ثم إنسان على الحالات المرصودة فقط.**

### 5.2 المعمارية
```
source.pdf (immutable, SHA-256)
  └─ S0 Preflight (per page): validity/encryption, text-layer quality score
       (chars, (cid:)/U+FFFD rate, letter-spacing ratio, glyph→unicode sanity,
        language/script ID), image coverage, page labels, outline, tagged-PDF struct tree
       → page-triage.json  (route: native | ocr | hybrid; risk flags)
  └─ S1 Layout + reading order (all pages; batched N pages/activity, checkpointed)
       layout model class: DocLayNet-trained detector (Docling layout/Heron, PP-DocLayout, DocLayout-YOLO)
       table structure: TableFormer-class; OCR only on regions without trustworthy text
       → docling-document.json (all content layers, prov with page+bbox per fragment)
  └─ S2 Deterministic normalizers (each versioned, diff-logged)
       NFKC for ligatures only (UAX #15), de-hyphenation (book lexicon), letter-spacing collapse,
       line unwrapping, cross-page/column paragraph merge (multi-ref spans), running header/footer
       detection by cross-page repetition, page-number → printed_label (PDF /PageLabels, ISO 32000 §12.4.2)
  └─ S3 Apparatus: footnote/endnote detection + callout↔note linking (superscript glyph rise/size),
       bibliography segmentation + citation parsing (GROBID-class), TOC & index detection,
       front/body/back-matter roles
  └─ S4 Structure reconciliation: PDF outline (authoritative when present) ⨝ detected section headers
       ⨝ parsed printed TOC → heading levels, section_path
  └─ S5 QA report (per page & per book) + gates → ingest-quality.json
  └─ S6 HITL queue for flagged pages/sections → reviewer decisions as artifacts
  └─ ParsedBook v2 (contract bump)
```

### 5.3 فئات الأدوات (دون تحديد نموذج)
| الوظيفة | الفئة المقترحة | المرشحون (يُحسم بينهم بقياس على golden المشروع) |
|---|---|---|
| layout و reading order | كاشف layout مدرّب على DocLayNet أو ما يماثله، ونموذج reading order | Docling (المعتمد في D-010، [Docling Technical Report، arXiv:2408.09869](https://arxiv.org/abs/2408.09869))، وMinerU 2.5، وMarker، وPaddleOCR PP-StructureV3 |
| OCR للمناطق | مُعرِّف خطوط محلي سريع، ومسار VLM بطيء للجودة | PP-OCRv5/v6 (الحالي)، وSurya 2 (رخصته تحتاج مراجعة)، وolmOCR-class VLM |
| الجداول | TableFormer-class، وVLM محلي للجداول منخفضة الثقة | TableFormer الحالي. Gemini يبقى خارج الافتراضي (D-004) |
| المراجع والاستشهادات | نموذج تسلسلي متخصص | [GROBID](https://grobid.readthedocs.io/en/latest/Introduction/) (production-ready، ومستخدم في Semantic Scholar وOpenAlex، وF1 للمراجع نحو 0.87–0.90 بحسب توثيقه) |
| التطبيع | قواعد حتمية + معجم الكتاب | كود المشروع |

### 5.4 بوابات الجودة
**حتمية، على كل كتاب ودون نموذج:**
1. **تغطية الصفحات:** 100% من الصفحات ذات النص إما تُنتج blocks أو تُسجَّل صراحة «فارغة/زخرفية».
2. **recall مقابل طبقة النص للصفحات الرقمية:** body-word recall ≥ 99.5% وprecision ≥ 99.5% على مستوى الكتاب، و**على مستوى الصفحة بعد احترام spans متعددة الصفحات** ≥ 98% لكل صفحة.
3. **حجم الـ block:** لا block متني أطول من 2,000 محرف إلا الجداول والكود. ولا block يحتوي أكثر من عنوان واحد.
4. **الدمج عبر الصفحات:** الـ blocks المنتهية في منتصف جملة عند حد صفحة ≤ 1%.
5. **التطبيع:** ligatures = 0، وتتابعات حروف متباعدة = 0، وكلمات مقطوعة بواصلة متبقية ≤ 0.05%، وكلمات ملتصقة ≤ 0.1%، وتسرّب ترويسات وأرقام صفحات إلى المتن ≤ 0.5% من الصفحات.
6. **الجهاز العلمي:** ≥ 95% من علامات الإحالة مربوطة بحاشية، ولا حاشية بلا رقم.
7. **البنية:** ≥ 95% من مداخل الـ PDF outline مطابقة لعنوان مكتشف في الصفحة الصحيحة، ومستويات العناوين متسقة مع الـ outline.
8. **الإسناد:** كل block فيه bbox، وprinted_label حين توجد page labels.
9. **الـ script واللغة:** لا محارف من scripts غير متوقعة (CJK أو غيرها) دون علم.

**دلالية، كإشارة لا كبوابة قبول وحدها:**
- قاضٍ VLM محلي يقارن صورة الصفحة بالـ blocks المستخرجة على عيّنة طبقية من الصفحات (أعلى خطرًا أولًا)، ويحكم على: إسقاط، وترتيب، ونوع. **يُعايَر على human gold قبل اعتماد عتباته**، ولا يُرسل محتوى إلى الخارج (D-004).

### 5.5 نقاط HITL
1. **Page triage:** الصفحات التي تفشل بوابة حتمية أو درجة ثقتها منخفضة تُعرض جنبًا إلى جنب (صورة ونص) لقرار: قبول، أو تصحيح، أو إعادة بمحرك آخر.
2. **Structure sign-off:** مراجعة الـ outline، وحدود front/back matter، وسياسة الحواشي والمراجع **قبل** الترجمة. البوابة الحالية `OutlineApprovalProvenance` تُوسَّع لتشمل هذا.
3. **قرارات المراجع تُحفظ كـ artifacts،** ولا يُعدَّل الأصل بصمت.

### 5.6 المقاييس والعتبات المستهدفة (على golden المشروع)
| المقياس | المرجع | الهدف (رقمي المصدر) | الهدف (ممسوح نظيف) |
|---|---|---|---|
| Text NED على مستوى الصفحة | [OmniDocBench، CVPR 2025](https://arxiv.org/abs/2412.07626) | ≤ 0.02 | ≤ 0.05 |
| Reading-order NED | OmniDocBench | ≤ 0.05 | ≤ 0.08 |
| TEDS للجداول | OmniDocBench وPubTabNet | ≥ 0.85 | ≥ 0.80 |
| heading precision/recall | T-ING-02 | ≥ 0.95 / ≥ 0.95 | ≥ 0.90 |
| Block-type macro-F1 (footnote، caption، list، toc، index، bibliography، header/footer) | project gold | ≥ 0.90 | ≥ 0.85 |
| دقة ربط الحواشي | project gold | ≥ 0.95 | ≥ 0.90 |
| دقة إسناد الصفحة | project gold | ≥ 0.99 | ≥ 0.99 |
| CER | `ocr_eval.py` | ≤ 0.5% | ≤ 3% (T-ING-03) |
| olmOCR-Bench-style unit tests (presence، absence، order، table) | [olmOCR 2، arXiv:2510.19817](https://arxiv.org/abs/2510.19817) | ≥ 95% pass | ≥ 90% pass |

**ملاحظة:** OmniDocBench يستبعد الترويسات والتذييلات والحواشي من مقاييسه. لذلك لا يكفي وحده لكتبنا، ويجب أن يغطي golden المشروع الحواشي بمقاييس مستقلة.

### 5.7 الاختبار
- **Golden set للمشروع بثلاث طبقات:**
  1. نحو 60 صفحة مسماة يدويًا من الكتب الأربعة الموجودة (internal_test_only)، طبقية حسب: متن، عنوان، حواشٍ، endnotes، مراجع، فهرس، TOC، جدول، شكل، عمودين، عبور صفحة.
  2. صفحات بحثية من OmniDocBench وMisraj وKITAB (`phase-2-golden-dataset-audit.md`) للعربية والتخطيط.
  3. حالات سلبية مصنوعة عمدًا: أعمدة مقلوبة (T-ING-04)، وترويسة مكررة، وغلاف مصوّر في كتاب رقمي.
- **اختبارات وحدات بأسلوب olmOCR-Bench** لكل صفحة golden: «النص X موجود»، و«الترويسة Y غائبة»، و«الجملة A قبل B»، و«الحاشية 87 مرتبطة بالموضع Z». هذه رخيصة التأليف، حتمية، وتصلح لـ CI.
- **Snapshot/regression** على ParsedBook للكتب الأربعة. أي تغيّر في عدد الـ blocks أو الأنواع أو الإسناد يتطلب مراجعة صريحة.
- **Property tests:** كل block له ref صالح، ولا تداخل ولا فجوات في الـ spans، وترتيب block_ids رتيب.
- **اختبار end-to-end** لمسار Docling الحقيقي داخل الـ worker image، لا mocks.
- **CI:** golden الصغير (≤ 20 صفحة) في كل PR. وnightly على الكتب الكاملة مع تقرير جودة.

---

## 6. تحليل الفجوة وخطة الانتقال

### 6.1 ما يُحتفظ به
- فحوص سلامة المصدر: التوقيع، وSHA-256، والتشفير (`pdf_book.py:200-229`).
- آلية `stage_key` و`get_or_compute` والـ PROV، وTemporal activity مع heartbeats.
- إعادة ترتيب العربية لكل سطر عبر line boxes (`docling_pdf.py:85-150`)، وهو إصلاح مثبت بالقياس.
- رسم قصاصات الجداول (`_render_table_crop_png`)، وعميل Surya كمسار جودة اختياري.
- أدوات قياس `ocr_eval.py` (CER/WER) كجزء من حزمة تقييم أوسع.

### 6.2 ما يُعاد كتابته
- `docling_pdf.py`: تحويل DoclingDocument إلى ParsedBook v2 بكل الطبقات، وprov متعدد، وbbox، وconfidence، والمستويات من مصالحة الـ outline.
- `pdf_book.py`: يصبح preflight وtriage فقط، لا محلّل بنية.
- `orchestration/ingest.py`: دفعات صفحات مع checkpoint، وتقرير جودة، وبوابة، وكاش مبني على محتوى الملف والإعدادات دون `book_id` و`title`، وهوية الكود (git SHA أو hash الحزمة) داخل الـ stage_key.
- العقد `ParsedBook` إلى v2: spans متعددة الصفحات، و`reading_order`، و`role` (front/body/back)، و`subtype` (toc_entry وindex_entry وreference_entry وrunning_header)، و`note_links`، و`raw_text` مقابل `text`، و`quality_flags`، و`lang`. **مع SCHEMA_VERSION bump وتحديث `02-CONTRACTS.md`** (القاعدة 3).

### 6.3 ما يُحذف
- `_HEADING` القديم ومسارات بناء الـ blocks المكررة في `surya_ocr.py` و`openrouter_ocr.py`. Surya يُستدعى كمحرّك OCR للمناطق داخل المسار الموحّد.
- `pdf_text.py`، أو يُدمج في preflight.
- استدعاء HTTP المباشر في `gemini_tables.py`: إما عبر `llm_gateway`، أو يُزال من المسار الافتراضي.
- مسار OpenRouter الذي يجعل «الصفحة block واحدًا»، أو يُعزل كمحرك OCR للمناطق.

### 6.4 الخطوات بالترتيب
| # | الخطوة | يعتمد على | المخرج |
|---|---|---|---|
| 0 | **تجميد وتنظيف:** إعادة بناء الـ worker من HEAD ونشره (لإغلاق تسريب مفتاح Gemini في الـ URL)، وتسجيل أن artifacts المرحلة الحالية مبنية بكود غير موجود في الـ repo. إيقاف إدخال كتب جديدة إلى الترجمة حتى الخطوة 5. | لا شيء | image مطابق لـ HEAD |
| 1 | **تحويل قياسات هذا التدقيق إلى أداة `ingest-quality` حتمية** وتشغيلها على الـ artifacts الحالية كـ baseline. | 0 | `ingest-quality.json` لكل كتاب |
| 2 | **Golden set** (60 صفحة) مع annotations للأنواع والترتيب والحواشي والإسناد، واختبارات olmOCR-style. يحتاج مراجعًا بشريًا. | 1 | `tests/golden/ingest/*` |
| 3 | **ParsedBook v2** (schema bump)، مع adapter توافق مؤقت يُخرج v1 للمراحل الحالية. | 2 | contract وdocs |
| 4 | **المسار الموحّد:** S0 وS1 وS2 (layout لكل الصفحات، والتطبيع، والدمج عبر الصفحات، والترويسات، والـ printed labels). | 3 | parser v2 |
| 5 | **S3 وS4:** الحواشي وربطها، والمراجع، وTOC والفهرس، ومصالحة الـ outline. | 4 | البوابات 6–8 |
| 6 | **S5 وS6:** بوابة الجودة وطابور HITL، وتوسيع اعتماد الـ outline. | 5 | gate |
| 7 | **Bake-off** على golden: Docling (إعدادات بديلة) مقابل MinerU وMarker وPP-StructureV3، مع قياس الزمن والذاكرة تحت حدود الـ worker (D-007). | 2، 4 | قرار موثق في `00-DECISIONS` |
| 8 | **دفعات صفحات وcheckpoint** في Temporal، وdedup بالمحتوى (D-023). | 4 | activity لكل N صفحة |
| 9 | **إعادة استخراج الكتب الأربعة** ثم إعادة تشغيل المراحل اللاحقة. | 6 | ParsedBook v2 مُجاز |

### 6.5 الاعتماديات على المراحل الأخرى
- **الترجمة (D-028):** وحدات `TranslationUnitKind` يجب أن تُبنى من الأنواع الجديدة لا من reclassification لاحق. و`block_id` سيتغير، فـ `book-main:3:0` و`4:0` يندمجان، ويلزم جدول ربط من القديم إلى الجديد لإعادة استخدام ترجمات صحيحة إن وُجدت.
- **Outline:** حراسة التغطية (98%) يجب أن تستبعد الأدوار back/front matter بحسب السياسة، وتعتمد مستويات العناوين الجديدة.
- **Indexing/RAG:** يُعاد الـ chunking على فقرات مدمجة ونص مطبّع. والـ embeddings الحالية تصبح stale.
- **التأليف والصوت:** الاستشهاد بـ printed_label.

### 6.6 المخاطر
| الخطر | التخفيف |
|---|---|
| تكلفة CPU لتحليل layout لكل الصفحات. المقيس سابقًا: 8 صفحات في 78.22 ثانية مع OCR (`phase-2.md:98`)، أي نحو 1.8 ساعة لـ 677 صفحة إن لزم OCR. والـ layout بلا OCR أسرع (**UNVERIFIED** على هذا الخادم). | OCR فقط للمناطق التي تحتاجه، ودفعات، وقياس في الخطوة 7. |
| التطبيع يصحح أكثر من اللازم (over-correction) | الاحتفاظ بـ `raw_text` وdiff، واختبارات «النص الصحيح لا يُمس» (D-024). |
| تغيّر block_id يكسر artifacts لاحقة وتقدّم المستخدم | جدول ربط ونسخة stage جديدة، وعدم الكتابة فوق القديم. |
| الرخص: Surya OpenRAIL-M وبيانات OmniDocBench للبحث فقط | المشروع بحثي، لكن يجب توثيق ذلك في manifest الـ golden. |
| الاعتماد على golden صغير | تقسيم title-level dev/holdout، كما يفرض `ocr_eval.py`. |

---

## 7. معايير القبول («المرحلة منتهية» عندما…)

1. **قابلية إعادة الإنتاج:** الـ worker المنشور مبني من commit محدد، وهوية الكود داخل stage_key، وإعادة التشغيل تعطي SHA ParsedBook نفسه (T-ING-05 على الكتب الأربعة، لا على صفحة اصطناعية).
2. **Golden موجود ومراجَع بشريًا:** ≥ 60 صفحة، ≥ 4 كتب، كل الفئات في §5.7 ممثلة، مع تقسيم dev/holdout على مستوى العنوان. و`tests/golden/` غير فارغ ويعمل في `make check`.
3. **مقاييس holdout** تحقق عتبات §5.6 كلها للكتب رقمية المصدر، وعتبات «الممسوح» لعيّنة الصفحات الممسوحة، مع نشر التقرير بصيغة `03-TESTING.md`.
4. **البوابات الحتمية التسع في §5.4** تمر على الكتب الأربعة الحقيقية، وتقرير `ingest-quality.json` محفوظ لكل كتاب. أرقام مرجعية يجب أن تتحقق:
   - SAT: blocks مقطوعة عند حد صفحة ≤ 1% (الآن 25%).
   - Think Stats: ligatures = 0 (الآن 909)، وترويسات في المتن ≤ 0.5% (الآن 92%).
   - Gramsci: عناوين متباعدة = 0 (الآن 63)، وصفحات ذات per-page recall أقل من 0.98 = 0 (الآن 213)، وحواشٍ بلا رقم = 0، ونسبة الإحالات المربوطة ≥ 95%.
5. **الأثر النهائي:** إعادة تشغيل عيّنة مراجعة الترجمة نفسها (أو عيّنة طبقية ≥ 300 مقطع) تُظهر أن المقاطع ذات عيب المصدر ≤ 3% (الآن 32.1%)، وأن `cut_at_page_break` و`ocr_noise` و`toc_or_index` = 0 تقريبًا. هذا القياس يربط المرحلة بالمنتج، لا بمقياس شكلي.
6. **اختبار سلبي:** T-ING-04 (أعمدة مقلوبة) وكتاب رقمي بغلاف مصوّر كلاهما يُكشف أو يُعالج صحيحًا (لا تحويل للكتاب كله).
7. **HITL يعمل:** كل صفحة فشلت بوابة لها قرار مراجع محفوظ كـ artifact، ولا يدخل كتاب إلى الترجمة دون structure sign-off.
8. **التزام القواعد:** لا HTTP مباشر خارج `llm_gateway`، والنماذج مثبتة بـ revision في `04-OPERATIONS.md`، والعقد v2 موثق في `02-CONTRACTS.md`.

---

### المصادر
- [OmniDocBench (arXiv 2412.07626 / CVPR 2025)](https://arxiv.org/abs/2412.07626): NED للنص وreading order، وTEDS للجداول. يستبعد الترويسات والحواشي.
- [olmOCR 2: Unit Test Rewards for Document OCR (arXiv 2510.19817)](https://arxiv.org/abs/2510.19817): اختبارات presence وabsence وorder وtable. وفي [مراجعة LlamaIndex](https://www.llamaindex.ai/blog/olmocr-bench-review-insights-and-pitfalls-on-an-ocr-benchmark) حدودها: الحواشي والتعليقات خارج نطاقها.
- [Docling Technical Report (arXiv 2408.09869)](https://arxiv.org/abs/2408.09869) و[DoclingDocument concepts](https://docling-project.github.io/docling/concepts/docling_document/): furniture وbody والتسميات. ونطاق `DEFAULT_CONTENT_LAYERS={BODY}` تحققت منه محليًا في docling-core 2.99.0.
- [GROBID documentation](https://grobid.readthedocs.io/en/latest/Introduction/): structuring للمراجع والحواشي، production-ready.
- [OCR-D workflows](https://ocr-d.de/en/workflows) و[OCR-D eval spec](https://ocr-d.de/en/spec/ocrd_eval.html) (مذكورة سابقًا في `phase-2.md:90`): الحفاظ على النص الخام والتقييم مقابل ground truth.
- Unicode UAX #15 (NFKC) لتطبيع الـ ligatures، وISO 32000-1 §12.4.2 Page Labels: مرجعان قياسيان معروفان، لم أجلبهما في هذه الجلسة.
- ممارسات مثل MinerU 2.5 وMarker (دمج الفقرات عبر الصفحات والأعمدة) مصدرها ثانوي، فهي **UNVERIFIED** كأرقام، وتُحسم في bake-off الخطوة 7.
