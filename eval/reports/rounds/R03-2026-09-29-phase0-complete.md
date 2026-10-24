# تقرير الجولة R03 — إكمال Phase 0

| البند | القيمة |
|---|---|
| التاريخ | 2026-09-29 |
| النطاق | تجهيز D-020 وإكمال Phase 0 فقط |
| المشروع | `/root/projects/book2course`، فرع `main` |
| النتيجة | Phase 0 PASS؛ لم يبدأ Phase 1 |

## التخزين والنموذج المحلي

- أُنشئ `F:\wsl-b2c\b2c-data.vhdx` بحجم افتراضي expandable قدره 300 GiB، وتهيأ ext4 بالوسم `b2cdata` ثم رُبط بـ `/mnt/wsl/b2cdata`.
- اجتاز `e2fsck -fn` الفحص، وثبت marker ومجلدات الخدمات عبر `scripts/data-disk.sh check`.
- Gemma4 لم يعمل مع صورة llama.cpp المثبتة: خطأ عدد tensors نفسه موثق upstream (#27349)، والملف لم يتغير.
- نُسخ Qwen3-Coder 30B الموجود مسبقًا في Ollama إلى `/mnt/wsl/b2cdata/models`، وبصمة SHA-256 والحجم طابقا manifest. بقي مخزن E: الأصلي للقراءة فقط.
- يعمل `b2c-llm` بصحة سليمة، بلا إعادة تشغيل، واستهلك 14.82 GiB من حد 26 GiB. اختبار محلي: 90 token في 6.43 ثانية (14 token/s)، ونموذج Langfuse generation سجّل latency قدرها 2.899 ثانية لمخرجات 39 token.

## إصلاحات أثناء التحقق

- أضيف alias داخلي `langfuse-internal` لأن اسم الخدمة كان يُحل من عامل الاختبار إلى عنوان شبكة edge غير المتاحة له.
- استُبدل Docker filter `label!=` غير المدعوم بفحص label لكل حاوية في T-P0-05.
- أُصلح تنسيق f-string المقتبس في T-P0-04.

## نتائج البوابات

- `make eval PHASE=0`: exit 0.
- Contract/unit: 52 passed؛ integration: 5 passed.
- T-P0-01..05: كلها PASS، مع negative control صحيح في import-linter.
- T-P0-04: مجموع limits ‏42.250 GiB من حد 61 GiB؛ كل الحاويات محدد لها Memory/NanoCpus/CpuShares.
- T-P0-05: 11 حاوية خارج B2C بنفس IDs وStartedAt قبل التشغيل وبعده.
- V-04: `llama-server --help` نجح داخل الصورة المثبتة.
- PSI بعد الإقلاع والتحميل استقر قرب baseline ~3%؛ القفزة أثناء نقل ملف النموذج كانت مؤقتة.

## حدود النطاق والسلامة

- لم تتغير أي حاوية أو إعداد أو ملف تابع لـ AI Company؛ سجل T-P0-05 تطابق الحاويات الـ11 ووقت تشغيلها.
- تبقى Phase 1 والـ benchmark والجودة الموسعة مؤجلة حتى اعتماد Phase 0.
- واجهة DSH رُفض الوصول إليها من بوابة المتصفح، كما أن قائمة بيئات Codex Tasks فارغة؛ لم نحاول تجاوز الرفض، واستمر العمل محليًا ضمن التفويض السابق.

الأدلة التفصيلية في `eval/reports/phase-0.md` و`eval/reports/phase-0-evidence/`.
