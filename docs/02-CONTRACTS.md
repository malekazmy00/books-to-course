# 02 — العقود بين الطبقات (Layer Contracts)

المصدر الوحيد للحقيقة هو `packages/contracts/src/b2c_contracts/models.py` (SCHEMA_VERSION 1.1.0).
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
| non_retryable | `ValidationError`، `UnsupportedFormat`، `RightsNotCleared`، `ConsentMissing`، `OutlineNeedsReview` |

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
| L2 ← L5 | `CourseOutline` | الـ spans متتالية ومتداخلتش؛ لو `llm_inferred` ⇒ `requires_review` | Gate G0 |
| L3 ← L4 | `StructuredChunk[]` | مفيش chunk بيعدي حدود `section_path`، و`embedding_model` ثابت | retry |
| L4 ← L5 | LightRAG doc_ids + `ConceptEdge[]` | `doc_status=processed` لكل كتاب؛ كل edge ليه evidence | retry / G1 |
| L5 ← L6 | `LessonDraft` | كل جملة ليها citation ≥ 1 والـ citation موجود فعلًا، وfaithfulness ≥ العتبة | إعادة توليد ← مراجعة |
| L6 ← L7 | `LessonSummary`، `GlossaryTerm[]`، `Question[]` | الأسئلة عدّت الـ solver والـ linter | إعادة توليد ← G2 |
| Termbase ← L7 | `TermbaseEntry[]` | كل entry ليها `approved_by` | Gate G3 |
| L7 ← L8 | `TranslationUnit[]` | `qe.route == auto_accept` أو `review.action` موجود | G4 |
| L8 ← Release | `AudioAsset` | `acx_pass` و`asr_cer ≤` العتبة؛ والصوت المستنسخ ليه consent ساري | إعادة توليد المقطع ← G5 |
| Release | `CourseRelease` | كل الكتب `cleared`؛ والنسخ مجمّدة | non-retryable |

## 3. أمثلة JSON

### CourseOutline
```json
{"schema_version":"1.1.0","course_id":"crs_01","spine_book_id":"bk_stats",
 "supplementary_book_ids":["bk_bizstats"],"requires_review":false,
 "modules":[{"module_id":"m02","title":"Descriptive Statistics","order":2,
   "lessons":[{"lesson_id":"m02.l03","title":"Measures of the Center","order":3,
     "spine_span":{"start_block":"bk_stats:71:0","end_block":"bk_stats:78:14","pages":[71,78]},
     "provenance":"pdf_outline","prior_lessons":["m02.l01","m02.l02"]}]}]}
```

### LessonSentence
```json
{"schema_version":"1.1.0","sid":"m02.l03:s017",
 "text_en":"The median is less sensitive to outliers than the mean.",
 "citations":["c_bk_bizstats_0412"],"kind":"supplementary"}
```

### TranslationUnit (مسار المراجعة)
```json
{"tu_id":"tu_m02.l03_004","lesson_id":"m02.l03","src_sids":["m02.l03:s017"],
 "src_text":"The median is less sensitive to outliers than the mean.",
 "tgt_text":"الوسيط أقل تأثرًا بالقيم المتطرفة من الوسط الحسابي.",
 "glossary_injected":["median","outlier","mean"],
 "model":{"id":"qwen3-30b-a3b-instruct-2507-q4_k_m","prompt_version":"tr-v1"},
 "qe":{"deterministic_pass":true,"term_violations":[],"cometkiwi":0.71,"xcomet":0.83,
       "xcomet_error_spans":[],"llm_mqm":{"major":0,"minor":1},"route":"human_review"}}
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
