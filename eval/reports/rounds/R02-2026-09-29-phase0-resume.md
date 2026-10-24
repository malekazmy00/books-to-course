# تقرير استئناف الجولة R02 — Phase 0

| البند | القيمة |
|---|---|
| التاريخ | 2026-09-29 |
| النطاق | استئناف Phase 0 فقط |
| المسار | `/root/projects/book2course`، فرع `main` |
| الحالة | T-P0-01 نجح؛ Phase 0 gate ما زال غير مكتمل ومتوقفًا عند تجهيز D-020 |
| نقطة البداية | `a6da91a Round report R01 (Phase 0, stopped pending owner actions)` |

## 1. العمل المنفذ

شُغّل السكريبت الموجود:

```text
wsl -d Ubuntu -- bash /root/projects/book2course/eval/suites/phase0/t_p0_01_imports.sh
```

النتيجة الفعلية:

- الفحص الأساسي بـ `uv run lint-imports`: **6 kept, 0 broken**، exit 0.
- الاختبار السلبي زرع مؤقتًا `import b2c_kg` داخل `b2c_ingest`: **5 kept, 1 broken**، exit 1 كما هو متوقع؛ أظهر الفحص أن `b2c_ingest` لا يجوز له استيراد `b2c_kg`.
- السكريبت أبلغ `NEGATIVE CONTROL OK`، وتم التحقق بعده من إزالة الملف المزروع.
- الدليل الخام: `eval/reports/phase-0-evidence/t_p0_01_import_linter.txt`.

## 2. فحص جاهزية التخزين قبل التشغيل

الأمر للقراءة فقط:

```text
wsl -d Ubuntu -- bash /root/projects/book2course/scripts/data-disk.sh check
```

أعاد exit 1 بالنص:

```text
ERROR: b2c data disk is not mounted at /mnt/wsl/b2cdata (or marker missing).
Windows (admin): wsl --mount --vhd F:\wsl-b2c\b2c-data.vhdx --name b2cdata
```

لم يظهر mount عند `/mnt/wsl/b2cdata`، ولم يوجد marker باسم `.b2c-data-disk`. كما أعاد فحص حاويات Compose ذات label المشروع `b2c` صفر حاويات. وجود أحرف الأقراص في Windows لا يحقق شرط D-020 وحده؛ المطلوب هو VHDX الخاص بالمشروع مركبًا كـ ext4 عند المسار المحدد.

## 3. تسجيل الاستثناء المؤقت D-021

بموافقة صاحب المشروع السابقة، سُجّل بتاريخ 2026-09-29 استثناء يسمح باستخدام CodeCraft عبر DSH Harness للمساعدة البرمجية فقط إلى أن يعود Claude Code. لا يسمح الاستثناء بإضافة provider أو مفتاح أو اتصال API إلى runtime الخاص بـ B2C، ولا يغيّر أي قاعدة تشغيل أو عزل أو resource budget أو gate.

- القرار: `docs/00-DECISIONS.md`، ‏D-021 وملحق D-004.
- تعليمات المشروع: `CLAUDE.md`، القاعدة 6.
- الاستثناء ينتهي عند عودة Claude Code.

## 4. ما لم يُنفذ

- لم تُشغّل أي حاوية أو خدمة أو أمر Compose.
- لم تُشغّل اختبارات التكامل أو بقية T-P0-02..05؛ التخزين والخدمات المطلوبة غير جاهزة، وT-P0-05 يحتاج baseline جديدًا بعد تجهيز التخزين.
- لم يُلمس أي مشروع أو حاوية خارج `b2c`.
- لم يُعلن نجاح Phase 0 gate؛ نتيجة T-P0-05 القديمة ما زالت FAIL إلى أن يعاد قياسها وفق البروتوكول.

## 5. الخطوة التالية

تجهيز `F:\wsl-b2c\b2c-data.vhdx` وتركيبه بالاسم `b2cdata`، ثم التحقق من mount وتهيئة المجلدات عبر `make data-init`. بعد ذلك فقط: قياس I/O PSI مقابل baseline، تحديث baseline لـ T-P0-05، ثم إكمال تقييم Phase 0 وتوثيق نتائجه في `eval/reports/phase-0.md`.