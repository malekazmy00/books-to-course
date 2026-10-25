# 02 — العقود بين الطبقات (Layer Contracts)

المصدر الوحيد للحقيقة هو `packages/contracts/src/b2c_contracts/models.py` (SCHEMA_VERSION 1.7.0). سجل الإصدارات: 1.3.0 أضاف قبول المخرجات (R1، §7)، و1.4.0 أضاف ParsedBook v2 (R3، §8) بعد دمج العملين، و1.5.0 أضاف CourseOutline v2 (R4، §9)، و1.5.1 غيّر دلالة قبوله: بوابات نظامية لا قرار بشري (قرار المالك 2026-10-25، لا يوجد مراجع بشري في المنتج).، و1.6.0 دمج ذلك مع عقود الفهرسة v2 (R5، §10): provenance الـ chunk وتقرير بوابة الفهرسة وحكم الربط المساند، و1.7.0 أضاف `KnowledgeDossier` (بحث المعرفة) و`ConceptLayer` (طبقة المفاهيم) بالإنجليزية (D-032..D-035، §11).
الوثيقة دي بتشرح **إزاي** كل طبقة بتسلّم للطبقة اللي بعدها. والتعريفات نفسها موجودة في الكود.

> الكود ده اتشغّل واتختبر: `tests/contract/test_contracts_invariants.py` ونتيجته 8 passed. الاختبارات دي بتتأكد من القيود الأساسية (negative controls).

## 1. قواعد عامة

### 1.1 Idempotency
```
key = stage_key(stage, stage_version, input_shas, config, model_id, prompt_version)
```
- قبل ما أي activity تبدأ، بتحسب الـ key. لو الـ artifact موجود بالفعل، بترجّع `ArtifactRef` من غير ما تعيد الحساب.
- ده بيحقق شرط Temporal إن الـ activities تكون **آمنة لإعادة التنفيذ**، وبيخلّي الاستئناف بعد أي عطل رخيص.

### 1.2 مسار التخزين
```
s3://artifacts/{course_id}/{stage}/{key}/<files>
```
كل artifact بيتسجّل في جدول `artifacts` ومعاه سجل provenance (W3C PROV: `wasGeneratedBy`، و`used`، و`wasDerivedFrom`).

### 1.3 سياسة إعادة المحاولة (Temporal RetryPolicy)
| البند | القيمة |
|---|---|
| initial_interval | 5s |
| backoff_coefficient | 2.0 |
| maximum_interval | 10m |
| maximum_attempts | 5 |
| non_retryable | `ValidationError`، `UnsupportedFormat`، `RightsNotCleared`، `ConsentMissing`، `OutlineNeedsReview`، `OpenRouterQuotaExceeded`، `OpenRouterRateLimited`، `LLMOutputTruncated`، `FreeTokenQuotaExceeded`، `InvalidLLMOutput` |

المصدر: `packages/orchestration/src/b2c_orchestration/retry.py` (`DEFAULT_RETRY`)، و`tests/unit/orchestration/test_retry_policy_docs.py` بيتأكد إن الجدول ده مطابق للكود. الأنواع المضافة كلها فشل حتمي: إعادة المحاولة مش هتغيّر النتيجة (`FreeTokenQuotaExceeded` حد tokens مدى الحياة ومش بيتجدد، و`InvalidLLMOutput` بيترفع بعد محاولات الإصلاح الداخلية في الـ gateway عند `temperature=0`).
بعض الـ activities ليها سياسة خاصة بيها: QA والترجمة (3 محاولات)، والـ MP3 (3 محاولات، `MP3_RETRY` في `audio.py`).

### 1.4 مخرجات الـ LLM
- كل مخرج بيتطلب **structured output** (JSON schema مولّد من Pydantic) عبر `llm_gateway`. VERIFY دعم `response_format` / `json_schema` في إصدار llama-server المثبت.
- المخرج المرفوض بيتعاد توليده مرتين، وبعدها بيروح لطابور مراجعة. **ممنوع** القبول الصامت لأي مخرج غير صالح.
- الـ cache: `sha256(model_id + prompt + params)` في Postgres، و`temperature=0` لكل المهام الحتمية.

### 1.5 الوقت
كل الـ datetimes لازم تكون UTC وفيها timezone. الـ datetime اللي من غير timezone بيترفض (py-fsrs بيشترط UTC).

## 2. التسليم بين الطبقات

| من ← إلى | ما يُسلَّم | شرط القبول قبل التسليم | الفشل |
|---|---|---|---|
| Upload ← L1 | `BookUpload` | `rights_status` موجود، والـ mime مدعوم | non-retryable |
| L1 ← L2/L3 | `ParsedBook` | كل block ليه `refs` ≥ 1، و`page_count` صحيح | retry على دفعات صفحات |
| L1 v2 ← L2/L3 (1.3.0، R3) | `ParsedBookV2` + `IngestQualityReport` | عقد v2 صالح، و`IngestQualityReport.accepted` (البوابات الحتمية في §7). التقرير غير المقبول لا يمر للمخطط | `accepted=false` يُحفظ ويُوقف المسار (مراجعة) |
| L2 v2 ← L5 (1.5.1، R4) | `CourseOutlineV2` + `OutlineQualityReport` | العقد يفرض تقسيم كل الكتل إلى دروس ونطاقات مستبعدة بسبب (§9)، وحالته `accepted` في سجل المخرجات: بوابة البنية الحتمية **و**الفحص المتقاطع المستقل ناجحان (كلاهما `GateResult` بقرار `system`). لا مراجع بشري في المنتج | مخرج مرفوض بسبب مسجل؛ لا فهرسة |
| L2 ← L5 | `CourseOutline` | الـ spans متتالية ومتداخلتش؛ لو `llm_inferred` ⇒ `requires_review` | Gate G0 |
| L3 ← L4 | `StructuredChunk[]` + `IndexQualityReport` | مفيش chunk بيعدي حدود `section_path` أو الدرس، و`embedding_model` ثابت، و`IndexQualityReport.accepted` (§9) | البوابة الفاشلة: لا كتابة في الفهرس، والمخرج `rejected` |
| L4 ← L5 | LightRAG doc_ids + `ConceptEdge[]` | `doc_status=processed` لكل كتاب؛ كل edge ليه evidence | retry / G1 |
| L5 ← L6 | `LessonDraft` | كل جملة ليها citation ≥ 1 والـ citation موجود فعلًا، وfaithfulness ≥ العتبة | إعادة توليد ← مراجعة |
| L6 ← L7 | `LessonSummary`، `GlossaryTerm[]`، `Question[]` | الأسئلة عدّت الـ solver والـ linter | إعادة توليد ← G2 |
| Termbase ← L7 | `TermbaseEntry[]` | كل entry ليها `approved_by` | Gate G3 |
| L7 ← L8 | `UnitTranslation[]` (لكل `TranslationUnit`) | `decision == accepted`: بلا issue major/critical، ومخرج المحرك مقبول آليًا فقط بعد QE دلالي؛ وإلا `needs_review` (D-028) | G4 |
| L8 ← Release | `AudioAsset` | `acx_pass` و`asr_cer ≤` العتبة؛ والصوت المستنسخ ليه consent ساري | إعادة توليد المقطع ← G5 |
| Release | `CourseRelease` | كل الكتب `cleared`؛ والنسخ مجمّدة | non-retryable |

## 3. أمثلة JSON

### CourseOutline
```json
{"schema_version":"1.2.0","course_id":"crs_01","spine_book_id":"bk_stats",
 "supplementary_book_ids":["bk_bizstats"],"requires_review":false,
 "modules":[{"module_id":"m02","title":"Descriptive Statistics","order":2,
   "lessons":[{"lesson_id":"m02.l03","title":"Measures of the Center","order":3,
     "spine_span":{"start_block":"bk_stats:71:0","end_block":"bk_stats:78:14","pages":[71,78]},
     "provenance":"pdf_outline","prior_lessons":["m02.l01","m02.l02"]}]}]}
```

### LessonSentence
```json
{"schema_version":"1.2.0","sid":"m02.l03:s017",
 "text_en":"The median is less sensitive to outliers than the mean.",
 "citations":["c_bk_bizstats_0412"],"kind":"supplementary"}
```

### TranslationUnit و UnitTranslation (D-028، SCHEMA 1.2.0)
`TranslationUnit` وحدة ترجمة واحدة: لا تعبر حدود الفقرة، وتنتهي دائمًا عند علامة حاشية، فتُعاد العلامات بإلحاقها بنهاية الترجمة.
`kind` يحدد المسار: `prose|heading|list_item|caption|table_cell` للمحرك، و`reference|toc|index|footnote` بقواعد، و`noise` يُسقط.
```json
{"schema_version":"1.2.0","unit_id":"bk_stats:u00412","book_id":"bk_stats",
 "block_ids":["bk_stats:112:3"],"kind":"prose",
 "source_text":"The median is less sensitive to outliers than the mean.",
 "footnote_markers":["12"],"pages":[112],"section_path":["Ch 4"],
 "prev_unit_id":"bk_stats:u00411","next_unit_id":"bk_stats:u00413",
 "normalization":["joined_page_break"]}
```
`UnitTranslation` نتيجة الوحدة. ثوابت يفرضها العقد:
- `accepted` لا يحمل أي `QAIssue` بخطورة major/critical.
- مخرج المحرك (`origin=engine`) لا يُقبل بقرار `policy` إلا ومعه `qe` (فحص دلالي). بدون QE تكون الحالة `needs_review`.
- ترجمة بشرية تُقبل بقرار `human` فقط. `dropped` بلا نص، و`rejected` قد يكون بلا نص (1.2.1: المحرك لم يُرجع شيئًا صالحًا).
```json
{"schema_version":"1.2.0","unit_id":"bk_stats:u00412",
 "target_text":"الوسيط أقل تأثرًا بالقيم المتطرفة من الوسط الحسابي.12",
 "origin":"engine","engine":{"provider":"local","model_id":"<model>","revision":"<sha>","prompt_version":"tr-v2.1"},
 "attempts":1,"issues":[{"check_id":"fluency.punct","category":"fluency_style","severity":"minor","origin":"deterministic","evidence":{}}],
 "qe":{"deterministic_pass":true,"term_violations":[],"cometkiwi":0.84,"xcomet":null,"xcomet_error_spans":[],"llm_mqm":null,"route":"auto_accept"},
 "decision":"accepted","decided_by":"policy","decided_at":"2026-10-24T12:00:00Z"}
```

### Provenance (جملة عربية ← مصدرها)
```json
{"entity":"sent:m02.l03:s017:ar:v1","wasDerivedFrom":"sent:m02.l03:s017:en:v2",
 "wasGeneratedBy":{"activity":"act:translate:7f3a","agent":"qwen3-30b-a3b@<sha>","prompt_version":"tr-v1"},
 "en_citations":[{"chunk_id":"c_bk_bizstats_0412","book_id":"bk_bizstats","pages":[112]}],
 "audio":{"segment_id":"m02.l03.seg017","start_s":143.2,"end_s":149.8}}
```

## 4. واجهة TTS (Protocol)
```python
class TTSProvider(Protocol):
    name: str
    def capabilities(self) -> dict: ...
        # {"cpu": bool, "gpu": bool, "cloning": bool, "max_chars": int,
        #  "license": "CC-BY-NC-4.0", "commercial": False, "attribution": "..."}
    def synthesize(self, req: TTSRequest) -> TTSResult: ...
```
أي provider جديد لازم يعدّي نفس الـ contract tests (T-TTS-01) قبل ما يتسجّل.

## 5. واجهة GraphRetriever (عشان مقارنة HippoRAG 2 في Phase 6)
```python
class GraphRetriever(Protocol):
    def insert_book(self, chunks: list[StructuredChunk]) -> str: ...     # returns doc_id
    def delete_book(self, doc_id: str) -> None: ...
    def retrieve(self, query: str, top_k: int) -> list[tuple[str, float]]: ...  # (chunk_id, score)
```


## 6. Provenance للموافقة على المخطط
`OutlineApprovalProvenance` يميز صراحةً بين `human` و`system`. المسار البشري يتطلب `reviewer_id` حقيقيًا. المراجع النظامي يحمل `system_id` وإصدار السياسة وبصمة دليل، ولا يقبل أي `reviewer_id`. سياسة `b2c-outline-structural-review` تتحقق من مصدر غير مستنتج آليًا، وعناوين دروس موجودة بالنص، وتغطية متصلة كاملة للكتل، واتساق الصفحات والمتطلبات السابقة. نجاحها يثبت سلامة البنية فقط؛ لا يعني اعتمادًا تربويًا أو مراجعة بشرية.

## 7. قبول مخرجات المراحل: `GateResult` ودورة الحالة (SCHEMA 1.3.0، R1)
وجود artifact لا يعني قبوله (تدقيق 08، C1). كل مخرج مرحلة يُسجَّل في سجل المخرجات (`stage_outputs` في PostgreSQL) بحالة صريحة، ولا يقرأ المستهلك اللاحق إلا ما حالته `accepted`.

- `GateResult`: قرار بوابة واحدة على artifact واحد. حقوله: `stage` (يطابق `artifact.stage`)، و`gate_id` و`gate_version` (إصدار السياسة أو بصمة فحوصها)، و`kind` ‏(`deterministic|semantic|human`)، و`passed`، و`metrics`، و`evidence_ref`، و`decider` ‏(`system|human`)، و`decided_by`، و`decided_at` ‏(UTC).
  - البوابة البشرية لا يقررها إلا إنسان، والبوابات الآلية لا يقررها إنسان.
- `ProducerIdentity`: هوية نموذج أو prompt أو أداة ساهمت في المخرج (`role`، `model_id`، `revision`، `prompt_version`).
- `StageOutputRecord`: الـ artifact، والمدخلات (`inputs`: مراجع الـ artifacts الأم)، و`code_revision` (sha الـ commit الذي بُنيت منه الصورة العاملة، أو `<sha>-dirty`، أو `unknown`)، و`config`، و`identities`، و`required_gates`، و`gate_results`، و`status`.
- **دورة الحالة** (`STAGE_OUTPUT_TRANSITIONS`):

| من | إلى |
|---|---|
| `candidate` | `accepted` أو `rejected` |
| `accepted` | `rejected` (سحب القبول فقط) |
| `rejected` | لا شيء؛ الإصلاح يُنتج artifact جديدًا |
| `quarantined` (مخرجات قديمة قبل البوابات) | `candidate` (إعادة الفحص)، ولا ترقية مباشرة |

- **ثوابت يفرضها العقد:**
  - `accepted` يتطلب `required_gates` غير فارغة، وأن تكون آخر نتيجة لكل بوابة مطلوبة `passed=true`.
  - `accepted` يتطلب `code_revision` نظيفًا ومعروفًا (P8): لا `unknown` ولا `-dirty`.
  - `rejected` و`quarantined` يتطلبان `status_reason`.
  - كل `GateResult` في السجل يخص الـ artifact نفسه (uri وkey وsha256).
- السجل append-only لتغييرات الحالة ونتائج البوابات (`stage_output_events` و`stage_output_gates`). التفاصيل في `docs/audits/2026-10-25-R1-platform.md`.

## 8. ParsedBook v2 (SCHEMA 1.4.0، R3)
المرجع: `docs/audits/2026-10-24/01-ingest.md` §5–6. الـ block في v2 وحدة منطقية (فقرة، عنوان، حاشية، مدخل)، لا شريحة صفحة. لا يُسقط شيء رآه المحلل: الترويسات وأرقام الصفحات تبقى blocks في المنطقة `furniture`.

| الحقل | المعنى |
|---|---|
| `BlockV2.block_id` | `f"{book_id}:b{seq:05d}"` بترتيب القراءة، غير مربوط بصفحة |
| `type` | الأنواع الأحد عشر في v1 مع `endnote`، و`bibliography_entry`، و`toc_entry`، و`index_entry`، و`running_header`، و`running_footer`، و`page_number` (بدل `page_header`/`page_footer`) |
| `zone` | `front_matter` أو `body` أو `back_matter` أو `furniture`. ‏`furniture` يساوي بالضبط أنواع الترويسة والتذييل ورقم الصفحة |
| `refs` | كل أجزاء الـ block بترتيب القراءة: صفحة أو أكثر، مع `bbox` (أعلى-يسار، نقاط PDF) و`printed_label` من `/PageLabels` |
| `raw_text` / `text` | نص المحلل كما هو (الأجزاء مفصولة بـ `\n`)، ونتيجة التطبيع الحتمي |
| `level` + `level_source` | للعناوين فقط: `pdf_outline` أو `layout` أو `numbering` أو `section_word` |
| `note_refs[]` | علامات الإحالة: تُزال من `text` ويُحفظ موضعها `offset` والـ `note_id` المربوط (أو `null` إن لم تُربط) |
| `note_label` | الرقم المطبوع لحاشية أو endnote |
| `normalization[]` / `quality_flags[]` | خطوات التطبيع التي غيّرت الـ block، والعيوب المتبقية المرصودة |
| `ParsedBookV2.pages[]` | كل صفحة: `printed_label` و`status ∈ {content, furniture_only, empty}` |
| `outline[]` | الـ bookmarks ومعها `block_id` العنوان المطابق (أو `null`) |
| `notes[]` | `Note(note_id, kind, label, block_ids, cited_by)` |
| `normalizer` | اسم وإصدار التطبيع؛ إعادة التطبيع من نفس الوسيط تعطي نفس الـ SHA |

الثوابت التي يفرضها العقد: الصفحات `1..page_count` بالترتيب، و`block_id` و`note_id` فريدان، وكل ref داخل الكتاب نفسه، وكل رابط حاشية أو مدخل outline يشير إلى block موجود، والحاشية تشير إلى block من نوع footnote/endnote.

**التوافق مع v1:** `parsed_book_v1_from_v2(book)` إسقاط بلا استنتاج. يحوّل الأنواع الجديدة إلى أقرب نوع v1، ويعيد علامات الحواشي ملتصقة بموضعها، ويعيد رقم الحاشية في أول نصها، ويجعل المعرّف `f"{book_id}:{first_page}:{seq}"`. نشاط الاستخراج v2 يحفظ `parsed-book.json` (v1) بجانب `parsed-book-v2.json` حتى تنتقل المراحل اللاحقة.

**تقرير الجودة:** `IngestQualityReport(metrics, checks[QualityCheck], accepted)`. كل `QualityCheck` يحمل القيمة والعتبة والحكم، والعقد يرفض حكمًا لا يطابق القيمة، ويرفض `accepted=true` إن فشل أي فحص أو لم ينجح أي فحص. الفحص غير القابل للقياس (مثل ربط الحواشي في كتاب بلا حواشٍ) قيمته `null` وحكمه `null`.

## 9. CourseOutline v2 (SCHEMA 1.5.0، R4)
المرجع: `docs/audits/2026-10-24/02-outline.md` §5–7، و`00-RESTRUCTURE-PLAN.md` §R4. يُبنى من `ParsedBookV2` حتميًا (`b2c_outline.v2`)، ولا يقبل نفسه: `requires_review` ثابتة `true` في المسودة، والقبول يُقرَّر في سجل المخرجات (§7) ببوابتين نظاميتين (1.5.1، قرار المالك 2026-10-25: لا مراجع بشري): بوابة البنية الحتمية (`outline-v2.structure`) والفحص المتقاطع المستقل (`outline-v2.cross-check`: الفهرس المطبوع وعناوين التخطيط مقابل البنية المختارة). عند اختلاف المصادر تُعتمد الأسبقية bookmarks > الفهرس المطبوع > عناوين التخطيط، ويُقبل المخطط مع علامة وسبب مسجلين في `GateResult.metrics` ما دامت التغطية 100%، وإلا يُرفض بسبب.

| الحقل | المعنى |
|---|---|
| `block_count` + الـ spans | الدروس (`LessonV2.spine_span`) والنطاقات المستبعدة (`excluded_ranges`) **تقسّم** كتل الكتاب `0..block_count-1` بلا فجوة ولا تداخل: المدرج + المستبعد صراحةً = 100% بالبناء. الـ span نطاق شامل من معرّفات v2 (`{book_id}:bNNNNN`)، فترتيب القراءة مقروء من المعرّف |
| `ExcludedRange` | `zone` (`front_matter` أو `body` أو `back_matter`)، و`reason` بمفردات قريبة من EPUB SSV (`copyright`، `toc`، `acknowledgments`، `preface`، `endnotes`، `bibliography`، `index`، `figure_descriptions`…)، و`title` (العنوان الذي فتح النطاق)، و`rule` (القاعدة الحتمية التي استبعدته) |
| `ModuleV2` / `LessonV2` | `level` (العمق في بنية الكتاب)، و`source` (دليل الحد: `pdf_outline` أو `printed_toc` أو `heading` أو `llm_inferred`)، و`source_title` (كما في المصدر) و`display_title` (سطر واحد منظَّف)، و`title_source`، و`anchor_block` (كتلة العنوان التي تفتح العقدة) |
| `LessonV2.kind` | `section`، أو `chapter_intro` (نص الفصل قبل أول قسم، درس مستقل بعنوان الفصل وليس مخفيًا تحت عنوان القسم الأول)، أو `chapter` (فصل بلا أقسام) |
| `word_count` / `size_status` / `size_exception` | الحجم بالكلمات مقابل `size_policy` (حدود Q9 يقرها المالك). الدرس خارج الحدود يحتاج `size_exception` موثقًا |
| `subheadings` | عناوين داخل الدرس ليست حدود دروس (مثل «When to Use It») |
| `prior_lessons` | فارغة ما لم يوجد دليل (P12) |
| المعرّفات | مشتقة من بنية الكتاب (العنوان المطبَّع ومساره، مع بصمة قصيرة)، لا من معرّفات الكتل ولا من ترتيب الوحدة، فإعادة التحليل تحفظها |

الثوابت التي يفرضها العقد: التقسيم الكامل أعلاه، وترتيب الشجرة = ترتيب القراءة، و`order` كثيف 1..n، وتفرّد المعرّفات، وكل span من كتاب الـ spine، والمرساة داخل الـ span، والعنوان المعروض سطر واحد، و`prior_lessons` تشير إلى دروس سابقة.

**`OutlineQualityReport`:** نفس شكل تقرير الاستخراج (`QualityCheck`): `metrics` و`checks` و`accepted` (البوابة الآلية فقط) و`reference` (المرجع الداخلي المقارن به، مثل `pdf_outline`). العقد يرفض `accepted=true` مع فحص فاشل.

**التوافق مع v1:** `course_outline_v1_from_v2(outline, book)` إسقاط بلا استنتاج: معرّفات الكتل تصبح معرّفات `parsed_book_v1_from_v2(book)` (نفس الكتل بنفس الترتيب)، والعنوان = `display_title`، و`prior_lessons` فارغة. النطاقات المستبعدة قبل أول درس تصبح `excluded_prefix_block_ids`، وما بعدها فجوات بين دروس v1 (لا حقل لها في v1، والمرجع يبقى v2). نشاط المخطط v2 يحفظ `course-outline.json` (v1) بجانب `course-outline-v2.json` حتى تنتقل الفهرسة والأسئلة.

## 10. الفهرسة v2 (SCHEMA 1.6.0، R5): `StructuredChunk`، و`IndexQualityReport`، و`SupplementLinkJudgement` (R5)
المرجع: `docs/audits/2026-10-24/03-indexing-retrieval.md` §5–7. الحقول الجديدة كلها لها قيم افتراضية، فالـ chunks المخزنة بإصدار 1.4.0 أو أقدم ما زالت صالحة (بلا `parts`).

| الحقل في `StructuredChunk` | المعنى |
|---|---|
| `evidence_class` | ‏`body` (محتوى تعليمي) أو `note` (حاشية/endnote: دليل منفصل، لا يدخل محتوى الدرس) |
| `lesson_id` | الدرس الذي يقع الـ chunk داخل نطاقه في المخطط المعتمد (الحاشية: درس أول block يستشهد بها) |
| `parts[]` | `ChunkPart(block_id, start, end)`: المدى `[start, end)` من نص الـ block المفهرس. ترتيب الـ blocks في `parts` يساوي `block_ids` |
| `text_sha256` | بصمة `text`، والعقد يرفض بصمة لا تطابق النص |
| `chunker_version` | إصدار الـ chunker، ويدخل في `chunk_id` مع بصمة النص و`parts` |
| `context_header` | `Book › Module › Lesson` لنص الاسترجاع، لا للعرض |
| `quality_flags` | أعلام النص المشبوه (مثل `glued_words`) أو `short_section` |

**ثابت التغطية (parity):** كل حرف من نص كل block من نوع body داخل نطاق درس يقع في `parts` لـ chunk واحد بالضبط، إلا الـ blocks المحجورة (نص تالف) فتُذكر بالاسم في التقرير. أي block غير مغطى أو مغطى مرتين يُفشل البوابة.

`IndexQualityReport(book_id, role, report_version, chunker_version, source_format, outline_sha256, metrics, checks[QualityCheck], accepted)` بنفس قاعدة تقرير الاستخراج: لا قبول مع فحص فاشل. نشاط الفهرسة يحفظه بجانب `chunks.json`، ويسجل الـ snapshot مرشحًا في سجل المخرجات (§7) بالبوابة `index.content-gate`. عند الفشل لا يُكتب شيء في جدول الفهرس.

`SupplementLinkJudgement(lesson_id, chunk_id, book_id, rank, method, score, threshold, verdict, calibrated, policy_version)`: حكم صلة مسجل لكل مرشح مساند. ‏`verdict = linked` إذا وفقط إذا `score ≥ threshold`. ‏«لا رابط» نتيجة مقبولة. ‏`calibrated=false` تعني أن العتبة لم تُعاير بعد مقابل أحكام بشرية.

## 11. بحث المعرفة وطبقة المفاهيم (SCHEMA 1.7.0؛ D-032..D-035)
المرجع: `docs/proposals/2026-10-25-learning-companion.md` §2 و§6 و§10–§12. المرحلتان تُنتجان **بالإنجليزية** من الكتاب الإنجليزي، ثم تُترجمان لاحقًا بمسار الترجمة وقاموسه. الأصوات الثلاثة منفصلة في العقد: صوت المؤلف (مواضع الكتاب `BookSpan`)، وصوت الكورس (`gloss`)، والمصادر الخارجية (ادعاءات الملف بمصدرها).

### 11.1 `KnowledgeDossier` (المرحلة `knowledge-research`)
| الحقل | المعنى |
|---|---|
| `ResearchSource` | وثيقة مجلوبة: `source_id` (`src_` + 16 hex)، و`url` (https فقط)، و`provider` (`openalex`، `wikidata`، `wikipedia`، `openlibrary`، `web:<engine>`)، و`tier` (1 أكاديمي/موسوعي/ناشر، 2 مراجعات وحوارات منشورة، 3 مدونات)، و`fetched_at` (UTC)، و`content_sha256` للنص المنقّى، و`flags` (مثل `injection_removed:N`) |
| `Evidence` | مقتطف حرفي (`quote`) من مصدر، مع نسخة من `url` و`tier` و`fetched_at`، وحكمَي المدقق: `quote_found` (المقتطف موجود في النص المنقّى بعد توحيد المسافات وعلامات الاقتباس) و`supports` (محكّم من عائلة نماذج مختلفة) |
| `DossierClaim` | `section` ∈ `book_card`/`author`/`context`/`reception`/`concept_in_field`/`further_reading`، و`kind` ‏(`fact`/`viewpoint`)، و`status` ‏(`verified`/`attributed`/`rejected`)، و`attributed_to`، و`concept` (لملاحظات المفهوم في مجاله)، و`conflict_group` |
| `FurtherReading` / `RejectedItem` | قراءات تالية بمصدرها؛ وما رُفض (سياسة النطاق، الجلب، تعليمات محقونة أُزيلت، ادعاء فشل التحقق) |
| `extractor` / `judge` + `*_family` | هوية المستخرج والمحكّم وعائلتاهما |

ثوابت يفرضها العقد: الادعاء المنشور (غير `rejected`) كل أدلته `quote_found` و`supports=true`؛ و`verified` حقيقة فقط، ولها مصدر من الدرجة الأولى أو مصدران مختلفان؛ والرأي (`viewpoint`) والادعاء المتعارض (`conflict_group`) لا يكونان `verified` أبدًا بل `attributed` باسم قائله؛ و`rejected` يحتاج سببًا؛ وكل دليل يشير إلى مصدر مدرج بنفس `url` و`tier` و`fetched_at`؛ وعائلة المحكّم ≠ عائلة المستخرج؛ ومعرّفات المصادر والادعاءات فريدة. ‏`published()` يعيد ما يُعرض فقط.

### 11.2 `ConceptLayer` (المرحلة `concept-layer`)
| الحقل | المعنى |
|---|---|
| `BookSpan` | `block_id` و`pages` و`quote` حرفي من نص الـ block (يتحقق المدقق في `b2c_concepts.grounding` من وجوده) |
| `ConceptEntry` | `term` و`kind` ‏(`term`/`acronym`)، و`termbase_status` ‏(`approved` من القاموس المعتمد، أو `candidate` إن لم يوجد قاموس معتمد)، و`introduced_at` (أول ورود في نص الدروس)، و`first_lesson_id`، و`occurrences`، و`author_definition` (مواضع + `gloss` بصوت الكورس)، و`relations`، و`novelty` + `external_notes` |
| `ConceptRelation` | `prerequisite`/`related`/`part_of`/`contrasts_with` نحو مفهوم آخر في الطبقة، بدليل من الكتاب (`evidence ≥ 1`) |
| `ExternalConceptNote` | `voice="external"` دائمًا: ادعاء منشور من الملف (`claim_id` + روابط)؛ لا يُعرض بصوت المؤلف |
| `LessonConceptList` | لكل درس: المفاهيم التي يرد أول ذكر لها **في هذا الدرس** بترتيب القراءة (لـ «المصطلح بالأصل عند أول ذكر في كل درس») |

ثوابت يفرضها العقد: كل تعريف وعلاقة لهما موضع واحد على الأقل؛ وأهداف العلاقات موجودة وليست المفهوم نفسه؛ ورسم المتطلبات السابقة بلا دورات (`prerequisite_cycle`)؛ و`novelty` غير `unknown` يحتاج ملاحظة خارجية من الملف (لا ادعاء جِدّة بلا مصدر)؛ والمفهوم يُذكر أولًا مرة واحدة في الدرس ويشير إلى مفهوم معروف؛ والمعرّفات والمصطلحات (بلا حساسية لحالة الأحرف) فريدة.

### 11.3 القبول (§7)
- `knowledge-research`: بوابتان نظاميتان مطلوبتان، `knowledge.citations` (`semantic`: نسبة دعم الإسناد الخام، وصحة كل ادعاء منشور، وحد أدنى من الادعاءات المنشورة) و`knowledge.tier-coverage` (`deterministic`: بطاقة الكتاب والمؤلف مسنودتان بمصدر من الدرجة الأولى، وحصة الادعاءات المسنودة بالدرجة الثالثة وحدها). العتبات في `b2c_research.gates`.
- `concept-layer`: بوابتان `concepts.grounding` (كل موضع لكل عبارة بصوت المؤلف موجود حرفيًا في الكتاب المحلَّل، بإعادة الفحص عند البوابة) و`concepts.coverage` (تغطية مفاهيم القاموس المعتمد، أو المرشحين المختارين إن لم يوجد قاموس معتمد). العتبات في `b2c_concepts.gates`.
