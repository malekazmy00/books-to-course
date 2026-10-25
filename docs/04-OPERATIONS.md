# 04 — التشغيل والمراقبة (Operations & Observability)

## 1. البيئة (مقاسة فعليًا)
| البند | القيمة | المصدر |
|---|---|---|
| Host | Windows + Ubuntu على WSL2 | فحص فعلي |
| CPU | 2× Xeon E5-2658 v4 (Broadwell-EP)، 14 نواة لكل معالج، **28 physical / 56 logical** | Windows (WSL بيعرضهم كـ socket واحد) |
| ISA | AVX2، **مفيش AVX-512** | مواصفات المعالج |
| RAM المرئية لـ WSL | ~98 GiB (وswap ‏16 GiB) | `free` |
| الاستهلاك الحالي للمشاريع التانية | ~7.3 GiB، والـ CPU ‏~89% idle | `top`، `docker stats` |
| NUMA | عقدتين فعليًا، لكن WSL بيعرض عقدة واحدة ← **مفيش pinning** | D-008 |

## 2. ميزانية الموارد (D-007: ≤ 85 GiB و≤ 40 CPU — عُدّلت إلى 85 في 2026-09-29)

**قاعدة الـ CPU:** الخدمات التقيلة (`heavy`) ماتشتغلش مع بعض، لأن Temporal task queue `heavy` مضبوطة بـ `max_concurrent_activities=1`.
- أسوأ حالة في نفس اللحظة = أكبر خدمة heavy + كل الخفيفة = **24 + 10.5 = 34.5 CPU**، وده أقل من 40.

**قاعدة الذاكرة:** النماذج بتفضل محمّلة، عشان كده **مجموع `mem_limit` لكل الخدمات لازم يكون ≤ 85 GiB**. ده بيتختبر في T-P0-04.

**قاعدة الـ limits الصغيرة (درس Phase 0):** `mem_limit` لازم يغطي الـ working set كله، ومعاه الـ binary المحمّل في الذاكرة (page cache). الحد الأصغر من كده بيعمل thrashing: قراءات متكررة من الـ disk المشترك. اقيس `memory.stat` (anon + file) قبل ما تحدد الـ limit.

| الخدمة | نوع | mem_limit | cpus | cpu_shares | ملاحظة |
|---|---|---|---|---|---|
| llm (llama-server) | heavy | 50g | 24 | 512 | Qwen3-Coder 30B (`qwen3-coder:30b`) blob 18.56GB + KV cache; copy on B2C VHDX |
| embed-rerank | heavy | 5g | 6 | 512 | bge-m3 + reranker |
| tts | heavy | 4g | 8 | 512 | Phase 8 |
| postgres (pgvector) | light | 4g | 2 | 1024 | |
| temporal (+ui) | light | 2g | 1 | 1024 | |
| lightrag | light | 3g | 2 | 1024 | الشغل التقيل بيتعمل عن طريق llm/embed |
| langfuse (web+worker) + clickhouse + redis + object store | light | 8g | 3 | 1024 | Phase 0 |
| api + web + workers | mixed | 6g | 4 | 1024 | API 512m/0.5; worker 5g/4 including serial Docling OCR; 512m reserved for web |
| observability: otel-collector (core) + prometheus + grafana + cadvisor | light | 1.25g | 1.4 | 1024 | 256m + 384m + 384m + 256m. المقاس idle: 17 / 53 / 228 / ~20 MiB anon (Phase 0) |
| alertmanager (اختياري، `ALERTS=1`، تنبيهات تليجرام) | light | 64m | 0.1 | 1024 | أُخذ الـ 0.1 من حصة prometheus (0.5 ← 0.4) ليبقى المجموع 40 معالجًا |
| **المجموع** | | **83.25g** | | | ≤ 85 ✅ (D-007 بعد التعديل) |

`cpu_shares: 512` معناها إن الخدمة التقيلة بتاخد أولوية أقل من الافتراضي (1024) وقت التنافس، فالمشاريع التانية بتكسب. دي آلية CFS shares الرسمية في Docker.

**إجراء خاص لقياس qwen3-coder-next (~51GB):**
1. `docker compose -p b2c stop` لكل خدمات b2c ماعدا llm.
2. شغّل llm بـ `mem_limit: 56g` مؤقتًا، يعني كل الميزانية تقريبًا.
3. اعمل القياس.
4. رجّع الـ limit الأصلي.

ماتعملش القياس ده إلا وخدمات b2c التانية واقفة.

## 3. قواعد السيرفر المشترك (D-009)
- كل الأوامر لازم تبقى بـ `docker compose -p b2c -f docker/compose.yml ...`.
- **ممنوع** لمس: `firecrawl-*` (ومنها RabbitMQ)، و`ai-company-*`، و`memory-qdrant`، و`infisical-*`، و`searxng`، و`ollama`، و`playwright`، وقواعد بياناتهم.
- الـ ports: كل خدمات b2c على شبكة داخلية `b2c_internal`. اللي بيتعرّض للـ host هو `api` و`web` و`grafana` و`langfuse-web` و`temporal-ui` بس، وعلى `127.0.0.1` فقط. VERIFY إن الـ ports دي مش مستخدمة قبل ما تحجزها.
- T-P0-05 بيتأكد إن مفيش أي حاوية خارجية اتغيرت.

## 4. llama-server
- الصورة: `ghcr.io/ggml-org/llama.cpp:server` (VERIFY الـ tag والـ digest وثبّتهم).
- الـ args المبدئية (VERIFY الأسماء في `--help` للإصدار المثبت):
  ```
  -m /models/<model>.gguf --host 0.0.0.0 --port 8080
  -t <14|20|28 من T-B-01> -c 16384 --parallel 2 --jinja
  ```
- الـ threads: الأنوية الحقيقية بس، لأن الـ hyperthreads غالبًا مابتفيدش في llama.cpp. القيمة النهائية من T-B-01.
- الـ GGUF: إما من blobs بتاعة Ollama الموجود (VERIFY، وللقراءة بس، ومن غير أي تعديل على Ollama)، أو تحميل من Hugging Face بـ revision مثبت.

## 5. جدول الإصدارات المثبتة (Phase 0 verified; Phase 1+ pending)
| المكون | الإصدار / digest | تاريخ التحقق | المصدر |
|---|---|---|---|
| llama.cpp server image | `server-b11176` / `sha256:6257697a7f5d034b8fb499ddb07af3e250506352f94102054252a23f3b85e0af` | 2026-09-29 | GHCR pinned image; `llama-server --help` |
| Qwen3-Coder 30B GGUF (`qwen3-coder:30b`) | blob `sha256:1194192cf2a187eb02722edcc3f77b11d21f537048ce04b67ccf8ba78863006a` | 2026-09-29 | Ollama local manifest; SHA-256 verified copy on B2C VHDX |
| pgvector/pgvector | `0.8.6-pg17` / `sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f` | 2026-09-29 | pinned image; `image-digests.txt` |
| Temporal server | `1.32.0` / `sha256:c3e752127759616bb1615e0f9ba0e21635aeb5fdeb922de4f371c350955f46ae` | 2026-09-29 | pinned image; `image-digests.txt` |
| Temporal admin-tools / UI | `1.32.0` / `2.54.1`, digests in `image-digests.txt` | 2026-09-29 | pinned images |
| Langfuse web / worker | `4.46.0`, digests in `image-digests.txt` | 2026-09-29 | pinned images; T-P0-02 |
| ClickHouse | `25.12`, digest in `image-digests.txt` | 2026-09-29 | pinned image |
| Redis / MinIO | `7.4` / pinned digest; MinIO pinned digest (image tag `latest`) | 2026-09-29 | pinned images; `image-digests.txt` |
| pypdf | 6.1.3 (uv.lock; b2c-ingest) | 2026-09-29 | project-owned text-PDF parser and extraction fallback; scanned PDFs require Docling/OCR |
| pypdfium2 (pdfium) — second, independent text-layer extraction and raster-image boxes for the ingest v2 gate (D-043) | `pypdfium2==5.13.0` (uv.lock; `b2c-ingest[docling]` extra, already in the worker image) | 2026-10-25 | `docker exec b2c-worker-1 python -c 'import pypdfium2'` succeeds (read-only check); version recorded in every `text-layer.json` (`pdfium_version`) and in the text-layer stage key. Without it the gate fails `independent_references` instead of passing on one extraction |
| FastAPI / uvicorn / python-multipart | uv.lock (b2c-api) | 2026-09-30 | local upload/status API on 127.0.0.1:8100 |
| Docling | 2.131.0 (uv.lock; b2c-ingest[docling]) | 2026-10-01 | CPU PyTorch; OCR/layout/table models prefetched into worker image for offline runtime |
| Docling models used by ingest v2 (R3, `b2c_ingest.layout`) | HF `docling-project/docling-layout-heron` revision `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`; `docling-project/docling-layout-heron-onnx` `40bde044036bb181c130ddf6c51792187268748f`; `docling-project/docling-models` (TableFormer) `fc0f2d45e2218ea24bce5045f58a389aed16dc23`; RapidOCR PP-OCRv6 det/rec small (as fetched by `docling-tools models download` of docling 2.131.0) | 2026-10-25 | revisions read from the `.cache/huggingface/trees/<sha>.json` that `docling-tools models download` writes; the worker image fetches the same models at build time (L2 in audit 01: pin these revisions in `docker/worker/Dockerfile` when the image is next rebuilt) |
| OpenRouter test profile | `nvidia/nemotron-3-super-120b-a12b:free` | 2026-10-01 | Verified with schema-constrained JSON; free endpoint reported $0 and 86.54% availability over 3d when checked. Read-only key mount; free-provider prompts may be logged. Base compose stays local. |
| LightRAG | v1.5.7 (eval/reports/phase4) | — | VERIFY: record the image digest / lock entry used by compose.lightrag.yml |
| bge-m3 / bge-reranker-v2-m3 | not pinned | — | VERIFY: model revision missing |
| py-fsrs | `fsrs==6.3.2` (uv.lock) | 2026-10-24 | uv.lock |
| temporalio SDK | 1.33.0 (uv.lock) | 2026-10-24 | uv.lock |
| pySBD (sentence segmentation, translation units D-028) | `pysbd==0.3.4` (uv.lock; b2c-translation) | 2026-10-24 | PyPI (MIT, no dependencies, released 2021-02-11); rule-based Golden Rules segmenter, 97.92% on the English Golden Rules set (Sadvilkar & Neumann, NLP-OSS 2020, arXiv:2010.09657) |
| wordfreq (general-English reference frequencies for termbase keyness, D-028 part E; and since 2026-10-25 the English lexicon that S2 normalization uses to repair letter-spaced headings, R3, and that the S3 outline v2 title cleaner consults only for a join a stray letter already marks, R4) | `wordfreq==3.1.1` (uv.lock; b2c-translation, b2c-ingest, b2c-outline, b2c-indexing (content gate: glued-word check, R5); pulls `ftfy` 6.3.1, `locate` 1.1.1, `msgpack` 1.2.3, `wcwidth` 0.9.4, plus `langcodes`/`regex` already locked) | 2026-10-24 | https://pypi.org/project/wordfreq/3.1.1/ (released 2023-11-21, final release; upstream frozen). Code Apache-2.0; bundled frequency data CC BY-SA 4.0 (Google Books Ngrams terms apply), per the upstream README "License" section — attribution required if the data is redistributed. Used read-only, offline, via `word_frequency(word, "en")` |
| numpy / scipy (audio QA, R7: BS.1770 K-weighting via `scipy.signal.lfilter`, true peak via `resample_poly`) | `numpy==2.5.3`, `scipy==1.18.1` (uv.lock; b2c-speech, exact pins) | 2026-10-25 | already locked and present in the worker image through b2c-ingest[docling] (`docker exec b2c-worker-1 python -c 'import scipy'` → 1.18.1); no new download for the image. `pyloudnorm` was not added: it covers integrated loudness only (no true peak), still needs scipy, and its release line is stale (0.1.1, 2023); the ~40 lines it would replace are tested against the BS.1770 reference sine and cross-checked against ffmpeg `ebur128` in eval/reports/speech-v2-2026-10-25.md |
| lameenc (MP3 encoding of learner episodes, D-042: LAME, MPEG-1 Layer III mono CBR) | `lameenc==1.8.4` (uv.lock; b2c-speech; wheels for cp312 manylinux x86_64, uploaded 2026-06-27; LGPL-3.0-or-later) | 2026-10-25 | PyPI JSON (`pypi.org/pypi/lameenc/json`). 1.8.5 and 1.9.0 were uploaded 2026-10-15, younger than two weeks, so not taken. Bundles LAME, so no `ffmpeg`/`lame` binary is needed in the worker image (the worker image has no ffmpeg; only the fasih image does). Not yet in the running images: they were not rebuilt |
| mutagen (ID3v2.3 tags, MP3 frame info for verification, D-042) | `mutagen==1.48.1` (uv.lock; b2c-speech; pure Python, uploaded 2026-06-25; GPL-2.0-or-later) | 2026-10-25 | PyPI JSON. Written with `save(v2_version=3)` and explicit `Encoding.UTF16` per frame (UTF-16 with BOM, id3.org/id3v2.3.0 §3.3) |
| mtools (tests only: a FAT image without mounting, `tests/unit/speech/test_copy_course_to_card.py`; also `scripts/copy-course-to-card.py --image`) | Debian `mtools_4.0.49-1_amd64.deb`, sha256 `28edc5a0eee3f74a15135ec90ae2dee79270931df1d0c6415ab736b7eed46ad3` | 2026-10-25 | **not installed on the host**: fetched with `apt-get download` and unpacked with `dpkg-deb -x` into a session scratchpad, used through `B2C_MTOOLS_BIN`; without it the 4 FAT-image tests skip. No image or compose change |
| python base (fasih, supertonic) | `python:3.11-slim@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b` | 2026-10-24 | digest of the locally built base (`docker image inspect`) |
| python base (hy-translation) | `python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f` | 2026-10-24 | same |
| haproxy (hy-translation-proxy) | `3.2-alpine@sha256:5d97434a423c2533cfeb42874d45cf6d69a840b60334582dfbfd5008e94c80be` | 2026-10-24 | same |
| vendored BookTrans | upstream commit `fefaf9962e48ab9286992e43c24a9e6d6c8755af` | 2026-10-24 | `vendor/booktrans/UPSTREAM-COMMIT.txt` |
| Hy-MT2 1.8B (`tencent/Hy-MT2-1.8B`) + its prompt templates (D-028 `PlainMTEngine`, `b2c_translation/prompts.py` `hy-mt2.*@v1`) | revision `9a341cd1b679d3efd23b46e847b01745a71ed792`; `model.safetensors` LFS oid `sha256:29e9117a44c79f81857613601968ff482d8a23c2d6736a1710bba9e5ca4762e5` (from the HF download metadata on the data disk; local SHA-256 not yet recomputed) | 2026-10-24 | https://huggingface.co/tencent/Hy-MT2-1.8B (model card "Hy-MT2 Translation Task Instruction Examples": Default / Terminology / background-information templates; `chat_template.jinja`) — upstream README and template byte-identical to the data-disk copy; same table in https://github.com/Tencent-Hunyuan/Hy-MT2 @ `ff1903ecaa724e10951a23c16817a2413c752b35` |
| Fasih-TTS v1 / CATT / Supertonic3 / Naqta / EmbeddingGemma | **not pinned** | — | VERIFY: record revision + SHA-256 of each weights dir on the data disk |
| Gemma 4 (profile `gemma4`) | `gemma4:26b-a4b-it-qat`, weights blob `sha256:4c856523d61d77922dbc0b26753a6bf6208e5d69d80db0c04dcd776832d054c5` (14.4 GB), served as `gemma4-b2c:26b-a4b-it-qat-t18` (same weights, Modelfile `PARAMETER num_thread 18`) from `/mnt/wsl/b2cdata/models/ollama` | 2026-10-25 | ollama.com/library/gemma4; checksums verified after copy from E: |
| fasih / supertonic build context | **not in git** (`${B2C_DATA_DIR}/build-context/*`) | — | open: move into `docker/` so images are reproducible |
| b2c-research / b2c-concepts (بحث المعرفة وطبقة المفاهيم، D-034/D-035) | لا تبعيات خارجية جديدة: `httpx==0.28.1` و`pydantic` كما في uv.lock (مشتركة مع b2c-llm-gateway)؛ استخراج HTML بـ `html.parser` من المكتبة القياسية | 2026-10-25 | uv.lock |
| SearXNG (الملف الاختياري `knowledge-search`، خدمة `b2c-searxng`) | `searxng/searxng:2026.10.19-81371861f@sha256:5ed0323b5f5f8701264ef65e239a2bd83cb57098f2b971fa30bf79c06eeae271` | 2026-10-25 | Docker Hub tags API (`hub.docker.com/v2/repositories/searxng/searxng/tags`)؛ لم تُسحب الصورة ولم تُشغَّل |
| نماذج بحث المعرفة أثناء التطوير (D-029، OpenRouter) | المستخرج `deepseek/deepseek-v4-flash-0731`؛ المحكّم المستقل `google/gemini-3.1-flash-lite` (عائلة مختلفة) | 2026-10-25 | `https://openrouter.ai/api/v1/models` (التسعير والتوفر وقت التشغيل)؛ بلا revision لأن OpenRouter لا يعرضها |
| واجهات المعرفة العامة | OpenAlex REST (`api.openalex.org`، بلا مفتاح: ميزانية يومية مشتركة $0.10 لكل IP، $0.001 لكل طلب قائمة)، وWikidata/Wikipedia Action API، وOpen Library Search API | 2026-10-25 | ترويسات `x-ratelimit-*` من OpenAlex؛ Open Syllabus **غير مستخدم**: لا API عامة، والوصول البحثي باتفاق (شروطه بتاريخ 2023-05-04) |

### 5.1 قاعدة الخيوط مقابل حد المعالج (2026-10-25)
عدد خيوط الاستدلال (llama.cpp / Ollama / torch) يجب أن يكون **أقل من** قيمة `cpus` للخدمة. llama.cpp يختار افتراضيًا خيطًا لكل نواة فيزيائية (28). مع `cpus: 20` تعطّل الـ CFS quota الخيوط في 94% من الفترات، فانخفض توليد Gemma 4 إلى 0.14 token/s. بعد `num_thread 18` صار 12.3 token/s. وأوزان النماذج تُقرأ من القرص ext4 الخاص بالبيانات، لا من `/mnt/e` (9p بطيء).

### 5.2 مفتاح الاستخراج v2 (R3، 2026-10-25؛ D-043)
المسار v2 مسجَّل في الـ worker لكنه **غير مفعّل** إنتاجيًا. المفتاح الصريح `B2C_INGEST_V2_ENABLED` (`Settings.ingest_v2_enabled`) قيمته الافتراضية `false`. حين يكون `true` على الـ API، يبدأ رفع PDF إنجليزي `IngestPDFV2Workflow` بمعرّف workflow مستقل (الهوية تضيف `pipeline=ingest-v2`)، ويُسجَّل `ingest_pipeline: "v2"` في `book-library.json`. وحين يكون `false` يبقى معرّف الرفع v1 كما كان بايتًا ببايت، فلا يتغير أي مفتاح لكتاب قائم. **لا يُفعَّل قبل إغلاق قائمة الجاهزية** في `eval/reports/ingest-v2-gate.md` §5: الترجمة والأسئلة والصوت وعدة مسارات API لا تقبل إلا `ingest-parse-pdf`.
- البوابة (D-043): `ingest-quality.json` بـ `REPORT_VERSION=2`، و`text-layer.json` بصيغة `pdf-text-layer/2` (pypdf + pdfium + صناديق الصور)، والـ stage versions: text-layer 2 وparse 2. مع خدمة الترقية يُسجَّل `ingest-parse-v2` مرشحًا ويقرره `GateResult` ‏`ingest-v2.quality`.
- الـ workflow الجديد: `IngestPDFV2Workflow` (task queue `light` للـ workflow، والأنشطة `ingest_v2_text_layer` و`ingest_v2_layout_batch` و`ingest_v2_normalize` على `heavy`). المدخل `IngestV2Input(course_id, upload, source_ref, supplementary_book_ids, cache_bust, page_batch=50)`، بنفس حقول `IngestPDFInput` عدا `ocr_engine`.
- التفعيل (بعد قائمة الجاهزية): نشر worker وAPI من commit يحمل D-043، ثم `B2C_INGEST_V2_ENABLED=true` للـ API وحده. الـ workflows الجارية لا تتأثر، لأن الاسم جديد ولم يتغير تسلسل أوامر القديم. والإيقاف بإرجاع القيمة إلى `false`.
- الخيوط: `B2C_DOCLING_THREADS` (افتراضي 8) يجب أن يبقى أقل من `cpus` لخدمة الـ worker (§5.1). المقيس على هذا الخادم: 4–7 ثوانٍ للصفحة بـ 8 خيوط.
- البوابة: إذا كان `ingest-quality.json` غير مقبول ينتهي الـ workflow بـ `status=needs_review` ولا يبني مخططًا. الكتب العربية (`ocr_language=ar`) ترفض في v2 وتبقى على المسار القديم.

### 5.3 مفتاح المخطط v2 (R4، 2026-10-25)
مُوصَل لكنه **غير منشور** (لم يُعَد بناء أي صورة ولم يُعَد تشغيل أي worker).
- النشاط: `outline_v2_from_parsed_book` (`b2c_orchestration.outline_v2`، queue `heavy`، مهلة ساعة). يقرأ `parsed-book-v2.json` من artifact `ingest-parse-v2` ويكتب artifact `build-outline-v2` فيه `course-outline-v2.json` و`course-outline.json` (إسقاط v1 لنفس معرّفات `parsed-book.json`) و`outline-quality.json` و`outline-crosscheck.json` و`source-identity.json`.
- الـ workflow: `IngestPDFV2Workflow` يستدعيه خلف `workflow.patched("outline-v2-activity-v1")`؛ التواريخ التي بدأت قبل التصحيح تستدعي `outline_from_parsed_book` (v1) كما كانت. `IngestPDFWorkflow` لم يتغير.
- القبول آلي بالكامل (قرار المالك 2026-10-25: لا مراجع بشري): خدمة الترقية (R1) تسجل المخرج `candidate` ببوابتين مطلوبتين نظاميتين، `outline-v2.structure` و`outline-v2.cross-check`، ثم تقرر `accepted` أو `rejected` بسبب. يشترط القبول أيضًا `B2C_GIT_REVISION` نظيفًا للـ worker (P8)، وإلا يبقى `candidate`.
- التسليم للفهرسة: `POST /v1/ingestions/{id}/outline/approve-system` (أو `/approve`) لمخطط v2 لا يقرر شيئًا ولا يحتاج `B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL`: يكتب `approved-outline.json` (مفتاح محتوى ثابت) ويبدأ الفهرسة فقط إن كان المخرج `accepted` وكان المخطط مطابقًا تمامًا لما بُني. مخططات v1 تبقى على سلوك R0 (الاعتماد الآلي القديم معطّل افتراضيًا).
- التفعيل: نشر worker وAPI من هذا الفرع، ثم بدء `IngestPDFV2Workflow` لكتاب جديد (§5.2).

### 5.4 الكلام v2 (R7، 2026-10-25)
المسار مسجَّل في عامل الصوت (`B2C_WORKER_MODE=audio`، طابور `audio`) لكنه **غير مفعّل**: الـ API ما زال يبدأ `GenerateMP3Workflow` وحده، والـ workflows الجارية لا تتأثر (أسماء جديدة، وتسلسل أوامر القديم لم يتغير).
- الـ workflow: `GenerateSpeechV2Workflow`، والأنشطة `speech_v2_prepare` و`speech_v2_synthesize_segments` و`speech_v2_assemble_track`. المدخل `GenerateSpeechV2Input(course_id, assembly_ref, termbase_ref?, lexicon_overrides, loudness_profile, voice_id, seed, max_attempts, require_intelligibility, limits, track_ids?)`.
- الشرط: تجميعة `translate-book` **مقبولة** في سجل المخرجات، ونتائج وحداتها لم تتغير بعد التجميع (بصمة `results_digest`).
- المزود: `B2C_FASIH_TTS_URL` (افتراضي `http://fasih-tts:3006`)، مقطع واحد لكل طلب `/v1/tts`. هوية النموذج من `/info` للخدمة، والـ revision من `B2C_FASIH_MODEL_REVISION` (افتراضي `unpinned` إلى أن تُثبَّت الأوزان، انظر الجدول أعلاه).
- ملف الصوت: `app-speech-16` افتراضيًا (‎-16 LUFS، ‎-1 dBTP)، أو `acx`. قرار المالك Q2 معلّق.
- المخرجات: `speech-text` (النص المنطوق والخطة)، و`speech-segment-audio` (WAV + `qa.json` لكل مقطع، بمفتاح المحتوى)، و`speech-track` (`track.wav` و`segments.json` و`track.json` و`qa.json`). ترميز MP3 للتسليم لم يُبنَ بعد (لا encoder في صورة العامل).

### 5.5 بحث المعرفة وطبقة المفاهيم (D-034/D-035، 2026-10-25)
مُوصَلان لكنهما **غير منشورين ولا يبدآن تلقائيًا**؛ لا تغيير في الـ worker الحالي.
- الحزم: `b2c_research` (البحث الخارجي، والوحدة الوحيدة التي تستورد عميل HTTP هي `b2c_research.http`)، و`b2c_concepts` (طبقة المفاهيم). النماذج تُستدعى عبر `b2c_llm_gateway` فقط، بحقنها من orchestration.
- الأنشطة: `research_book_knowledge` (المرحلة `knowledge-research`) و`build_book_concept_layer` (المرحلة `concept-layer`) في `b2c_orchestration.knowledge`؛ والـ workflow `BookKnowledgeWorkflow` على task queue مستقل `knowledge`، يشغّله `python -m b2c_orchestration.knowledge_worker` فقط (ليس ضمن `worker.py`).
- الإعدادات: `B2C_KNOWLEDGE_EXTRACTOR_*` و`B2C_KNOWLEDGE_JUDGE_*` (`BASE_URL`، `MODEL_ID`، `FAMILY`، `API_KEY_FILE`، `ALLOW_REMOTE`)؛ يرفض الـ worker أن تكون عائلة المحكّم هي عائلة المستخرج. `B2C_KNOWLEDGE_FETCH_CACHE` لمجلد cache الجلب، و`B2C_OPENALEX_API_KEY_FILE` اختياري، و`B2C_SEARXNG_URL` (مثل `http://b2c-searxng:8080`) يفعّل بحث الويب العام، و`B2C_PAID_SEARCH_KEY_FILE` يفعّل الواجهة المدفوعة.
- SearXNG خاص بـ b2c: `make up KNOWLEDGE_SEARCH=1 PROFILES="gemma4 knowledge-search"` (يتطلب `B2C_SEARXNG_SECRET` في `.env`؛ الميزانية المعلنة مع gemma4: 45.25 GiB و36.5 CPU). لا يُستخدم SearXNG التابع لمشروع آخر (القاعدة 7).
- التشغيل اليدوي أثناء التطوير (بلا Temporal): `uv run python scripts/knowledge/run_knowledge.py --help` (التفاصيل في `eval/reports/knowledge-concepts-2026-10-25.md`).

## 6. المراقبة (Observability)

### 6.1 الأدوات
- **OpenTelemetry** لكل الخدمات، عبر OTel Collector.
  - GenAI semantic conventions لسه بحالة **Development**، فلازم نثبّت الإصدار ونستخدم `OTEL_SEMCONV_STABILITY_OPT_IN`. ده VERIFY.
- **Langfuse v4:** بيحتاج ClickHouse وRedis وobject store. نستخدم الـ compose الرسمي بتاع Langfuse، ونضيفله الـ limits من الجدول فوق.
  - **التقاط المحتوى معطّل افتراضيًا (R0، تدقيق 08 H3):** الـ `LLMGateway` لا ينسخ نص الـ prompt أو الـ completion (نص الكتاب) إلى span attributes (`langfuse.observation.input/output`) إلا إذا ضُبط `B2C_LLM_CAPTURE_CONTENT=true` صراحةً، والنقاط البعيدة لا تلتقط أبدًا. سياسة الاحتفاظ في Langfuse للبيانات القديمة لم تُحدَّد بعد.
- **Prometheus + Grafana:** للـ host والخدمات، مع cAdvisor عشان نشوف استهلاك حاويات b2c بس.
- **Alertmanager → تليجرام (اختياري):** `make up ALERTS=1`. الصورة `prom/alertmanager:v0.28.1@sha256:27c475db…` (مثبتة). التوكن ورقم المحادثة في `/root/.config/book2course/telegram-bot-token` و`telegram-chat-id` خارج الريبو. القالب `docker/alertmanager/alertmanager.tmpl.yml`؛ لا منفذ منشور، والخروج للإنترنت عبر `b2c_edge` فقط.

### 6.2 المقاييس والتنبيهات
| الطبقة | المقاييس | Alert |
|---|---|---|
| Host | CPU، وRAM، وdisk، واستهلاك b2c مقابل الميزانية | مجموع b2c > 78 GiB؛ disk > 80% |
| llm_gateway | tokens in/out، وp95 latency، وtokens/s، وcache hit، ونسبة JSON غير صالح | p95 > 2× baseline؛ JSON invalid > 5% |
| Ingest | pages/min، ونسبة OCR، والفشل | فشل > 2% |
| LightRAG | زمن الإدراج لكل كتاب، و`doc_status=failed` | أي failed |
| Synthesis | الجمل من غير citation، وfaithfulness | تحت العتبة |
| Pedagogy | نسبة رفض الأسئلة، وتوزيع Bloom | رفض > 20% |
| Translation | مخالفات المصطلحات، ونسبة `human_review` | أي مخالفة بعد QA |
| Audio | RTF، وCER، وacx_pass | acx_pass=false |
| Temporal | workflows فاشلة، والـ retries، وطول الطوابير | retries > 3 لنفس activity |
| SRS | الـ retention الفعلي مقابل `desired_retention` | انحراف > 5 نقاط |

### 6.3 منع تدهور الجودة (regression)
أي تغيير في نموذج أو prompt أو إعداد لازم:
1. يشغّل eval suite بتاعة الطبقة دي، وتتسجل كـ Langfuse dataset run.
2. يتقارن بالـ baseline المجمّد.
3. لو فيه انخفاض، **ممنوع** يتدمج.

## 7. النسخ الاحتياطي (قاعدة 3-2-1)
- `pg_dump` يومي + WAL archiving ❓ (VERIFY الإعداد).
- mirror للـ object store على قرص تاني + نسخة مشفّرة برّه الجهاز.
- **أولوية قصوى** للحاجات اللي مايتعادش إنتاجها: الـ termbase، والمراجعات البشرية، وsRS review logs، ووثائق موافقات الأصوات (مرجعها بس، والأصل عند المكتب القانوني).
- اختبار استعادة شهري (T-OPS-01).

### Arabic post-translation editing runtime (opt-in)

The B2C worker mounts the pinned local CAMeL-Lab QALB14 NoPnx/Pnx models and read-only `text-editing` source under the persistent data disk. `B2C_ARABIC_GEC_ENABLED` defaults to `false`; enable only after a representative Arabic quality review. The translation artifact keeps its original translation immutable and records decisions in `translation-arabic-editing.json`; corrections are cached independently by source, accepted translation, model manifest, and policy. The source checkout is pinned to `4d552ca3ae98029550f27fc52aa1b22883e16e61` (MIT).

## 8. الأسرار
- `.env` برّه git (فيه `.env.example` بس)، وDocker secrets للمفاتيح.
- مفتاح منفصل لكل خدمة: LightRAG API key، وLangfuse keys، وPostgres passwords.
- VERIFY: لو مشروع تاني بيستخدم Infisical، **ممنوع** نستخدمه أو نعدّل فيه من غير قرار صريح.

## 8. Windows reboot and B2C startup ordering (2026-10-05)
- `B2C-Data-Disk-Mount` runs at the signed-in user's logon with highest privilege and attaches only `F:\wsl-b2c\b2c-data.vhdx`; it verifies `/mnt/wsl/b2cdata/.b2c-data-disk` and `models/` and never formats the disk.
- `B2C-Stack-Start` runs one minute after logon as the signed-in user. It waits up to 15 minutes for the verified mount and Docker, checks the mount again with `scripts/data-disk.sh check`, then runs only Compose project `b2c` from `/root/projects/book2course`. It does not build images or remove orphan containers.
- The start script is maintained at `scripts/windows/start-b2c-stack.ps1`; the installed copy and log are under `%ProgramData%\B2C\`. It was run through Task Scheduler after the restart and returned result 0.
- After reboot, the VHDX was present and mounted at `/mnt/wsl/b2cdata` (`/dev/sde`, ext4, rw); the marker and model directory were verified. Core Compose services recovered on their existing data. LightRAG's separate experimental compose profile is not part of the default startup task.
- After `up -d`, the startup task restarts only B2C's cAdvisor so its Docker factory registers after Docker is ready; this prevents the post-reboot case where cAdvisor appeared up but exported no per-container memory series.
- The same task gates success on a Prometheus query returning at least one `container_memory_working_set_bytes` series labeled with Compose project `b2c`; the verified current count is 15. It fails visibly in its task result/log if the memory series do not return within two minutes.


## 9. Android operator access from mobile (updated 2026-10-08)
- The operator APK uses the fixed HTTPS Tailscale hostname https://desktop-3khe91s.tail7385b1.ts.net; the operator does not enter a server address. The app connection button checks /health only when pressed.
- (D-030, 2026-10-24) Funnel is OFF. The API is reachable only inside the tailnet through Tailscale Serve (HTTPS 443 and 8443 -> 127.0.0.1:8100). Keep the API bound to loopback. Do not publish port 8100 directly. Set B2C_API_AUTH_REQUIRED=true; the API bearer token remains in the server-side secret file mounted read-only at /run/secrets/b2c_mobile_api_token.
- The 32-bit debug APK does not embed the bearer token. On first protected API operation, the user enters it once; Android Keystore encrypts it locally. Treat this as a private operator credential, not a public-release login model.
- The audio generation activity is routed to the isolated audio Temporal task queue. Its single-activity worker has a 512 MiB/0.5 CPU limit and does not replace the existing HEAVY worker. Build/deploy it with the normal B2C Compose service graph and speech overlay.
- The Windows Tailscale service and B2C Docker startup task must both start at login/reboot; after a reboot verify `tailscale serve status` shows "tailnet only" and that `tailscale funnel status` lists nothing.
- APK 0.3.2 supports API 24+ and armeabi-v7a; Android 7/8 device-level E2E and learner-facing app readiness are still unverified.


### Model recovery after data-disk attachment (2026-10-09)
The Windows startup script now invokes `scripts/restore-model-services.sh` after base Compose startup. It starts only existing B2C Fasih/BGE/EmbeddingGemma containers that failed with boot bind-mount errors; manually stopped models and historical workers are preserved. The installed ProgramData script was synchronized and both parser/shell checks plus idempotent execution passed. Current reboot recovery and translation checkpoint-tail repair evidence are in `eval/reports/reboot-recovery-2026-10-09.md`.

The same post-mount task now runs `scripts/recover-lost-b2c-activities.sh`: it compares pending B2C QA/translation activity ownership with live B2C container boot times, preserves live workers, and requeues only attempts whose original worker process is proven lost. It retains source/checkpoint identity and retry budget. Model and activity recovery hooks are in the installed ProgramData copy.

## Public APK connectivity recovery — 2026-10-09

The local API and MagicDNS path returned HTTP 200, but this did not prove public access: host DNS resolved to the private tailnet IP. Forced public-DNS address tests found the default HTTPS port 443 stalling during connection/TLS. Refreshing Funnel and rebinding did not fix that path. The same persistent background Funnel proxy on HTTPS 8443 worked instead.

Superseded by D-030 (tailnet only; the APK base URL works only on devices joined to the tailnet). Historical note: public APK base URL was `https://desktop-3khe91s.tail7385b1.ts.net:8443`. Keep the existing bearer credential unchanged. Public health returned HTTP 200 from Windows; authenticated library checks from WSL, forcing each of the three public ingress addresses, all returned HTTP 200. No credential or library payload was logged. Keep port 443 configured for existing clients, but its public path remains faulty; clients must select 8443 for the verified working path. This is a public connectivity workaround, not proof of a repaired upstream 443 service. The background Funnel configuration is persistent; another full reboot has not been performed.


### Arabic speech punctuation fallback (2026-10-12)

The opt-in Naqta insertion-only speech preparation uses policy naqta-insertion-only-v2-short-terminal. It inserts a terminal period for Arabic text up to 240 characters when no terminal punctuation is present, covering short inputs where the model abstains. It does not modify words, existing terminal punctuation, English text, or longer passages. On B2C-On-D, the focused unit suite passed 4/4 and a live renderer smoke returned HTTP 200 with an ID3 MP3 (47,564 bytes); the cached prepared Arabic text had the expected final period.
