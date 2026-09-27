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

## 2. ميزانية الموارد (D-007: ≤ 61 GiB و≤ 40 CPU — عُدّلت من 60 في 2026-09-27)

**قاعدة الـ CPU:** الخدمات التقيلة (`heavy`) ماتشتغلش مع بعض، لأن Temporal task queue `heavy` مضبوطة بـ `max_concurrent_activities=1`.
- أسوأ حالة في نفس اللحظة = أكبر خدمة heavy + كل الخفيفة = **24 + 10.5 = 34.5 CPU**، وده أقل من 40.

**قاعدة الذاكرة:** النماذج بتفضل محمّلة، عشان كده **مجموع `mem_limit` لكل الخدمات لازم يكون ≤ 61 GiB**. ده بيتختبر في T-P0-04.

**قاعدة الـ limits الصغيرة (درس Phase 0):** `mem_limit` لازم يغطي الـ working set كله، ومعاه الـ binary المحمّل في الذاكرة (page cache). الحد الأصغر من كده بيعمل thrashing: قراءات متكررة من الـ disk المشترك. اقيس `memory.stat` (anon + file) قبل ما تحدد الـ limit.

| الخدمة | نوع | mem_limit | cpus | cpu_shares | ملاحظة |
|---|---|---|---|---|---|
| llm (llama-server) | heavy | 26g | 24 | 512 | Qwen3-30B-A3B Q4 ≈ 18.6GB + KV cache |
| embed-rerank | heavy | 5g | 6 | 512 | bge-m3 + reranker |
| docling-worker | heavy | 5g | 8 | 512 | Phase 2+ |
| tts | heavy | 4g | 8 | 512 | Phase 8 |
| postgres (pgvector) | light | 4g | 2 | 1024 | |
| temporal (+ui) | light | 2g | 1 | 1024 | |
| lightrag | light | 3g | 2 | 1024 | الشغل التقيل بيتعمل عن طريق llm/embed |
| langfuse (web+worker) + clickhouse + redis + object store | light | 8g | 3 | 1024 | Phase 0 |
| api + web + workers | light | 2g | 1 | 1024 | |
| observability: otel-collector (core) + prometheus + grafana + cadvisor | light | 1.25g | 1.5 | 1024 | 256m + 384m + 384m + 256m. المقاس idle: 17 / 53 / 228 / ~20 MiB anon (Phase 0) |
| **المجموع** | | **60.25g** | | | ≤ 61 ✅ (D-007 بعد التعديل) |

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

## 5. جدول الإصدارات المثبتة (يتملى في Phase 0/1)
| المكون | الإصدار / digest | تاريخ التحقق | المصدر |
|---|---|---|---|
| llama.cpp server image | | | |
| Qwen3-30B-A3B-Instruct-2507 GGUF | | | |
| pgvector/pgvector | | | |
| Temporal server | | | temporalio docs |
| Langfuse | v4.x | | langfuse self-hosting docs |
| Docling | | | |
| LightRAG | | | |
| bge-m3 / bge-reranker-v2-m3 | revision | | |
| py-fsrs | 6.x | | |

## 6. المراقبة (Observability)

### 6.1 الأدوات
- **OpenTelemetry** لكل الخدمات، عبر OTel Collector.
  - GenAI semantic conventions لسه بحالة **Development**، فلازم نثبّت الإصدار ونستخدم `OTEL_SEMCONV_STABILITY_OPT_IN`. ده VERIFY.
- **Langfuse v4:** بيحتاج ClickHouse وRedis وobject store. نستخدم الـ compose الرسمي بتاع Langfuse، ونضيفله الـ limits من الجدول فوق.
- **Prometheus + Grafana:** للـ host والخدمات، مع cAdvisor عشان نشوف استهلاك حاويات b2c بس.

### 6.2 المقاييس والتنبيهات
| الطبقة | المقاييس | Alert |
|---|---|---|
| Host | CPU، وRAM، وdisk، واستهلاك b2c مقابل الميزانية | مجموع b2c > 55 GiB؛ disk > 80% |
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

## 8. الأسرار
- `.env` برّه git (فيه `.env.example` بس)، وDocker secrets للمفاتيح.
- مفتاح منفصل لكل خدمة: LightRAG API key، وLangfuse keys، وPostgres passwords.
- VERIFY: لو مشروع تاني بيستخدم Infisical، **ممنوع** نستخدمه أو نعدّل فيه من غير قرار صريح.
