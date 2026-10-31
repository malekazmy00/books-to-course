# book2course

منصة بحثية غير تجارية تقيس أقصى ما يستطيعه الذكاء الاصطناعي وحده في تعليم متعلم لا يعرف لغة الكتاب، أو كفيف: تحويل الكتب إلى كورسات مسموعة بالعربية الفصحى **دون مراجعة بشرية في خط الإنتاج**.

- ابدأ من `docs/00-RESEARCH-CHARTER.md` (السؤال البحثي والطريقة وخريطة الوثائق)، ثم `CLAUDE.md` (قواعد التنفيذ)، ثم `docs/PROJECT-STATUS.md`.
- سجل القرارات: `docs/00-DECISIONS.md`. ترتيب العمل: خطة إعادة الهيكلة المعدَّلة في `docs/audits/`.
- العقود: `packages/contracts`.

## رفع كتاب PDF

بعد تشغيل stack الخاص بـ B2C وتركيب قرص البيانات، تتوفر الواجهة محليًا على `http://127.0.0.1:8100`.

```sh
curl -X POST http://127.0.0.1:8100/v1/books \
  -F 'metadata_json={"course_id":"stats-101","book_id":"probability-spine","title":"Probability","role":"spine","rights_status":"internal_test_only","supplementary_book_ids":[]}' \
  -F 'file=@probability.pdf;type=application/pdf'
```

الرفع محدود افتراضيًا بـ 200 MiB (`B2C_MAX_UPLOAD_BYTES`). الاستجابة تعطي `workflow_id` و`status_url`. الـ workflow يحفظ المصدر في MinIO، ثم يحلل PDF ذي طبقة نصية ويبني outline من bookmarks أو العناوين الواضحة. الـ PDF الممسوح يحتاج Docling/OCR، ورفع الكتاب وحده لا يعني اكتمال مراجعة الـ outline أو صلاحية النشر.
