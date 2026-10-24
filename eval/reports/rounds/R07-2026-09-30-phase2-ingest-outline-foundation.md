# R07 — بناء أساس Ingestion وOutline (2026-09-30)

| البند | الحالة |
|---|---|
| الهدف | بدء بناء B2C نفسه بناءً على طلب المالك، مع تعليق تقييم النماذج |
| النطاق | PDF رقمي ذو طبقة نصية؛ لا تغيير في حاويات Docker ولا في مشروع آخر |
| المرحلة | أساس Phase 2؛ غير مكتمل وغير مؤهل للـ gate |

## المنفذ

- أضيف `b2c_ingest.parse_pdf_book`: يتحقق من نوع PDF وتوقيعه وبصمة SHA-256 مقابل `BookUpload`، ويحوّل نص الصفحات إلى `Block` مع مراجع صفحات مادية و`section_path`.
- تُقرأ bookmarks من PDF كعناوين منظمة؛ في غيابها، تُلتقط فقط عناوين مرقمة أو عناوين uppercase الواضحة لتجنب تحويل النص العادي إلى TOC.
- الصفحات ذات طبقة نصية ناقصة ترفض بـ `OCRRequired` وتحدد أرقامها بدل إنتاج كتاب ناقص. هذا backend ليس OCR.
- أضيف `b2c_outline.outline_from_book`: يبني modules/lessons وspans ومقدمات الدروس السابقة من العناوين، دون استدعاء نموذج. النتيجة مسودة حتمية؛ لا توجد مراجعة بشرية داخل التطبيق بعد.
- أضيفت exports للحزمتين، وTemporal workflow يمرر PDF عبر طابور `heavy` ثم يبني outline للكتاب الأساسي على `light`، ويحفظ الناتجين في ArtifactStore بمفاتيح idempotent وسجلات provenance. أضيف endpoint رفع ينشئ source artifact ويبدأ Temporal workflow، مع endpoint لمتابعة الحالة.

## اختبارات

- `uv run pytest tests/unit tests/contract -q`: 67 passed, no warnings.
- `uv run lint-imports`: 6 contracts kept.
- `docker compose ... config --services`: نجح مع ملفي compose الخاصين بـ B2C؛ التكوين يحوي 17 خدمة ومجموع حدود الذاكرة 66.75 GiB من سقف 85 GiB.
- لم يُشغل integration مع MinIO/Temporal ولم يُبنَ أو يُشغل Docker API؛ قرص بيانات B2C غير متاح في WSL حاليًا.

## ما لم يُنفذ

- Docling مع OCR، واستخراج الجداول/الأشكال/الترتيب المكاني، ودعم EPUB وDOCX.
- outline fallback عبر LLM عند غياب bookmarks/headings، ومراجعة outline بشرية.
- Golden fixtures لصفحات معلّمة وقياس جودة parsing.
- تشغيل اختبارات أو معالجة ملفات مستخدم فعلية في هذه الجولة.
- لم تُشغل أو تُعدّل حاويات؛ خدمة LLM بقيت خارج نطاق هذا العمل. ولم تُشغّل اختبارات في هذه الجولة.

## القرار والحالة

تم إنشاء أساس برمجي أولي لـ L1/L2 للـ PDF الرقمي فقط. هذا لا يكمل Phase 2 ولا يحقق أي gate. الخطوة التالية في البناء: إضافة Docling/OCR ثم golden fixtures ومراجعة outline. تشغيل الخدمة يتطلب إعادة تركيب قرص بيانات B2C بسبب تعطل MinIO/llama mount الحالي؛ الاختبارات unit لا تعتمد على الحاويات.
