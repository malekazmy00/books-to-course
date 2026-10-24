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
| observability: otel-collector (core) + prometheus + grafana + cadvisor | light | 1.25g | 1.5 | 1024 | 256m + 384m + 384m + 256m. المقاس idle: 17 / 53 / 228 / ~20 MiB anon (Phase 0) |
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
| FastAPI / uvicorn / python-multipart | uv.lock (b2c-api) | 2026-09-30 | local upload/status API on 127.0.0.1:8100 |
| Docling | 2.131.0 (uv.lock; b2c-ingest[docling]) | 2026-10-01 | CPU PyTorch; OCR/layout/table models prefetched into worker image for offline runtime |
| OpenRouter test profile | `nvidia/nemotron-3-super-120b-a12b:free` | 2026-10-01 | Verified with schema-constrained JSON; free endpoint reported $0 and 86.54% availability over 3d when checked. Read-only key mount; free-provider prompts may be logged. Base compose stays local. |
| LightRAG | v1.5.7 (eval/reports/phase4) | — | VERIFY: record the image digest / lock entry used by compose.lightrag.yml |
| bge-m3 / bge-reranker-v2-m3 | not pinned | — | VERIFY: model revision missing |
| py-fsrs | `fsrs==6.3.2` (uv.lock) | 2026-10-24 | uv.lock |
| temporalio SDK | 1.33.0 (uv.lock) | 2026-10-24 | uv.lock |
| pySBD (sentence segmentation, translation units D-028) | `pysbd==0.3.4` (uv.lock; b2c-translation) | 2026-10-24 | PyPI (MIT, no dependencies, released 2021-02-11); rule-based Golden Rules segmenter, 97.92% on the English Golden Rules set (Sadvilkar & Neumann, NLP-OSS 2020, arXiv:2010.09657) |
| python base (fasih, supertonic) | `python:3.11-slim@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b` | 2026-10-24 | digest of the locally built base (`docker image inspect`) |
| python base (hy-translation) | `python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f` | 2026-10-24 | same |
| haproxy (hy-translation-proxy) | `3.2-alpine@sha256:5d97434a423c2533cfeb42874d45cf6d69a840b60334582dfbfd5008e94c80be` | 2026-10-24 | same |
| vendored BookTrans | upstream commit `fefaf9962e48ab9286992e43c24a9e6d6c8755af` | 2026-10-24 | `vendor/booktrans/UPSTREAM-COMMIT.txt` |
| Hy-MT2 1.8B / Fasih-TTS v1 / CATT / Supertonic3 / Naqta / EmbeddingGemma | **not pinned** | — | VERIFY: record revision + SHA-256 of each weights dir on the data disk |
| fasih / supertonic build context | **not in git** (`${B2C_DATA_DIR}/build-context/*`) | — | open: move into `docker/` so images are reproducible |

## 6. المراقبة (Observability)

### 6.1 الأدوات
- **OpenTelemetry** لكل الخدمات، عبر OTel Collector.
  - GenAI semantic conventions لسه بحالة **Development**، فلازم نثبّت الإصدار ونستخدم `OTEL_SEMCONV_STABILITY_OPT_IN`. ده VERIFY.
- **Langfuse v4:** بيحتاج ClickHouse وRedis وobject store. نستخدم الـ compose الرسمي بتاع Langfuse، ونضيفله الـ limits من الجدول فوق.
- **Prometheus + Grafana:** للـ host والخدمات، مع cAdvisor عشان نشوف استهلاك حاويات b2c بس.

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
- Keep the API bound to loopback; Funnel is the HTTPS path to the computer. Do not publish port 8100 directly. Set B2C_API_AUTH_REQUIRED=true; the API bearer token remains in the server-side secret file mounted read-only at /run/secrets/b2c_mobile_api_token.
- The 32-bit debug APK does not embed the bearer token. On first protected API operation, the user enters it once; Android Keystore encrypts it locally. Treat this as a private operator credential, not a public-release login model.
- The audio generation activity is routed to the isolated audio Temporal task queue. Its single-activity worker has a 512 MiB/0.5 CPU limit and does not replace the existing HEAVY worker. Build/deploy it with the normal B2C Compose service graph and speech overlay.
- The Windows Tailscale service and B2C Docker startup task must both start at login/reboot; verify the Funnel after reboot before considering remote access persistent.
- APK 0.3.2 supports API 24+ and armeabi-v7a; Android 7/8 device-level E2E and learner-facing app readiness are still unverified.


### Model recovery after data-disk attachment (2026-10-09)
The Windows startup script now invokes `scripts/restore-model-services.sh` after base Compose startup. It starts only existing B2C Fasih/BGE/EmbeddingGemma containers that failed with boot bind-mount errors; manually stopped models and historical workers are preserved. The installed ProgramData script was synchronized and both parser/shell checks plus idempotent execution passed. Current reboot recovery and translation checkpoint-tail repair evidence are in `eval/reports/reboot-recovery-2026-10-09.md`.

The same post-mount task now runs `scripts/recover-lost-b2c-activities.sh`: it compares pending B2C QA/translation activity ownership with live B2C container boot times, preserves live workers, and requeues only attempts whose original worker process is proven lost. It retains source/checkpoint identity and retry budget. Model and activity recovery hooks are in the installed ProgramData copy.

## Public APK connectivity recovery — 2026-10-09

The local API and MagicDNS path returned HTTP 200, but this did not prove public access: host DNS resolved to the private tailnet IP. Forced public-DNS address tests found the default HTTPS port 443 stalling during connection/TLS. Refreshing Funnel and rebinding did not fix that path. The same persistent background Funnel proxy on HTTPS 8443 worked instead.

Public APK base URL: `https://desktop-3khe91s.tail7385b1.ts.net:8443`. Keep the existing bearer credential unchanged. Public health returned HTTP 200 from Windows; authenticated library checks from WSL, forcing each of the three public ingress addresses, all returned HTTP 200. No credential or library payload was logged. Keep port 443 configured for existing clients, but its public path remains faulty; clients must select 8443 for the verified working path. This is a public connectivity workaround, not proof of a repaired upstream 443 service. The background Funnel configuration is persistent; another full reboot has not been performed.


### Arabic speech punctuation fallback (2026-10-12)

The opt-in Naqta insertion-only speech preparation uses policy naqta-insertion-only-v2-short-terminal. It inserts a terminal period for Arabic text up to 240 characters when no terminal punctuation is present, covering short inputs where the model abstains. It does not modify words, existing terminal punctuation, English text, or longer passages. On B2C-On-D, the focused unit suite passed 4/4 and a live renderer smoke returned HTTP 200 with an ID3 MP3 (47,564 bytes); the cached prepared Arabic text had the expected final period.
