# book2course

منصة بحثية لتحويل الكتب إلى كورسات مسرودة بالعربي الفصحى.

- ابدأ من `CLAUDE.md` (قواعد التنفيذ) ثم `docs/06-DELIVERY-PLAN.md` كخطة المشروع الجامعة، و`docs/00-DECISIONS.md` لسجل الاختيارات.
- خريطة المراحل والبوابات: `docs/05-PHASES.md`. يجوز تنفيذ بحث وتجهيز أو نماذج أولية بالتوازي بتوجيه المالك، لكنها لا تجتاز بوابة ولا تعتمد للإصدار.
- العقود: `packages/contracts`.

## رفع كتاب PDF

بعد تشغيل stack الخاص بـ B2C وتركيب قرص البيانات، تتوفر الواجهة محليًا على `http://127.0.0.1:8100`.

```sh
curl -X POST http://127.0.0.1:8100/v1/books \
  -F 'metadata_json={"course_id":"stats-101","book_id":"probability-spine","title":"Probability","role":"spine","rights_status":"internal_test_only","supplementary_book_ids":[]}' \
  -F 'file=@probability.pdf;type=application/pdf'
```

الرفع محدود افتراضيًا بـ 200 MiB (`B2C_MAX_UPLOAD_BYTES`). الاستجابة تعطي `workflow_id` و`status_url`. الـ workflow يحفظ المصدر في MinIO، ثم يحلل PDF ذي طبقة نصية ويبني outline من bookmarks أو العناوين الواضحة. الـ PDF الممسوح يحتاج Docling/OCR، ورفع الكتاب وحده لا يعني اكتمال مراجعة الـ outline أو صلاحية النشر.
