# R06 — تجهيز مصادر Phase 1 واستخراج PDF من داخل المشروع (2026-09-29)

| البند | الحالة |
|---|---|
| الهدف | تجهيز فصلين متقاربين بالاحتمالات للاختبار، والتأكد أن B2C نفسه يستطيع استخراج نص PDF |
| المصادر | Lane, Chapter 5 (أساسي) + Think Stats 2e, Chapter 3 (مساند) |
| الحقوق | Lane: public domain حسب سجل المصدر؛ Think Stats: CC BY-NC-SA 4.0. كلاهما `internal_test_only`، ولا نشر حتى مراجعة D-002 |
| عزل المشاريع | كل أوامر Docker استهدفت `b2c-llm-1` فقط؛ لا تعديل لحاويات أو ملفات مشروع آخر |

## تنفيذ مؤكد

- أضيف `b2c-ingest.pdf_text` إلى المشروع، مع اعتماد `pypdf==6.1.3` مثبت في `packages/ingest/pyproject.toml` و`uv.lock`.
- أداة المشروع تقرأ PDF نصيًا، تحفظ رقم كل صفحة مادي، وتوفر تحديد نطاق صفحات. لا تنفذ OCR؛ PDF بلا طبقة نصية يحتاج Docling/OCR.
- استخراج الفصل الأساسي من PDF فصل مستقل:
  - الأمر: `uv run python -m b2c_ingest.pdf_text /mnt/wsl/b2cdata/phase1-sources/lane-chapter5-probability.pdf /mnt/wsl/b2cdata/phase1-sources/lane-chapter5-probability.txt`
  - الناتج: `extracted_pages=37 words=11840`.
- استخراج PDF المساعد بالنطاق الفعلي 51–64 (الفصل 3، وأرقام الصفحات المطبوعة 31–43):
  - الأمر: `uv run python -m b2c_ingest.pdf_text /mnt/wsl/b2cdata/phase1-sources/thinkstats2.pdf /mnt/wsl/b2cdata/phase1-sources/thinkstats2-chapter3-pmf.txt --start-page 51 --end-page 64`
  - الناتج: `extracted_pages=14 words=2710`.
- أضيف `eval/suites/phase1/prepare_chunks.py` لتوليد 30 chunk أساسي و7 مساندة من ملفات استخراج المشروع، مع page references و`rights_status=internal_test_only`.
  - الناتج: الأساسي `chunks=30 words=11766`؛ المساند `chunks=7 words=2682`.
- مصادر الملفات وتراخيصها وSHA-256 وطريقة الاستخراج موثقة في `eval/golden/phase1/source-manifest.json`. نصوص PDF والـ chunks على `/mnt/wsl/b2cdata/phase1-sources/`، وليس في Git.
- حدّثت D-017 وخطة Phase 1 وإستراتيجية golden fixtures لتزيل الافتراض القديم الخاطئ عن تراخيص OpenStax، وتوثق استخدام المصدرين الحاليين.

## T-B-02: تجربة JSON المحلية

بدأت المحاولة الأولى وارتفع I/O؛ أُوقفت حينها يدويًا وسُجلت كغير مكتملة. بعد تجهيز استدعاء التقييم القابل للاستئناف/الحفظ التدريجي، أُعيد التشغيل بناءً على طلب المالك واكتمل على 30 chunk من الفصل الأساسي عبر `LLMGateway` إلى النموذج المحلي الحالي `qwen3-coder-30b`.

الأمر:

```sh
uv run python eval/suites/phase1/evaluate_json_extraction.py \
  --chunks /mnt/wsl/b2cdata/phase1-sources/lane-primary-30-chunks.jsonl \
  --output /mnt/wsl/b2cdata/phase1-sources/phase1-tb02-qwen3-coder-30b.jsonl \
  --base-url http://172.25.0.8:8080/v1 --model-id qwen3-coder-30b --limit 30
```

الناتج الفعلي: `valid=30/30 rate=100.000% elapsed_seconds=2089.9`. ملخص ملف الإخراج بعد انتهاء الأمر: متوسط 69.66 ثانية/طلب، median 58.80 ثانية، min 34.21، max 268.88؛ 171 كيانًا و126 علاقة في المجموع. صحة الـJSON Schema لا تثبت صحة/اكتمال الكيانات أو العلاقات؛ لا توجد بعد تسميات مرجعية لـT-B-03. Hash ملف النتائج: `34c40e6ec7c50a362b08add9e984227b6ee524484f06763f341fcd0d34bdfb66`.

أثناء التشغيل، سجلت قراءات متكررة لحاوية B2C فقط: CPU بلغ نحو 20 logical CPU من 24؛ الذاكرة ارتفعت من 14.65 إلى 18.68 GiB من حد 26 GiB؛ `/proc/pressure/io` بعد بداية التشغيل ظل غالبًا دون 1% avg10 (قراءة قرب نهاية آخر chunk `full avg10=0.05%`؛ القراءة اللاحقة بعد اكتمال التشغيل كانت `full avg10=3.71%`). حاوية `b2c-llm-1` بقيت عاملة ولم تُعد تشغيلها. لا دليل في هذه الجولة على تأثر حاوية خارج B2C؛ لم تُفحص أو تُنفذ أوامر عليها.

**النتيجة:** T-B-02 حقق حد JSON validity الأولي (≥95%) لهذا النموذج وحده، لكنه نموذج B2C الحالي الاستطلاعي، وليس اختيارًا نهائيًا من مرشحي D-005.

## لم يُنفذ / ما يزال مفتوحًا

- T-B-02 مكتمل للنموذج الحالي فقط؛ يجب تكراره على المرشحين الرسميين قبل اعتماده للـ gate.
- لم تُجهز العشر عينات المرجعية البشرية؛ لذلك T-B-03 مفتوح.
- لم تُجر قياسات T-B-01 أو T-B-04..06، ولم يُعتمد أي اختيار نموذج.
- لا توجد نتيجة Phase 1 gate؛ ضغط I/O يجعل تكرار الاستدلال على جميع chunks غير مناسب قبل تعديل الخطة أو وجود نافذة حمل هادئة.
- لا اختبارات pytest أُضيفت أو شُغلت في هذه الجولة.

## القرار

إعداد مصدر قابل لإعادة التنفيذ داخل B2C اكتمل. استخراج PDF وتجهيز العينات وإعادة T-B-02 للنموذج الحالي اكتملت. Phase 1 ككل ما يزال **غير مكتمل**: لا يصدر 100% JSON validity حكمًا على جودة الاستخراج، ويلزم T-B-03 يدويًا، وبقية النماذج والقياسات.
