# R05 — CodeCraft API probe + B2C gateway opt-in (2026-09-29)

| البند | النتيجة |
|---|---|
| API catalog | نجح من Windows؛ 30 موديلًا متاحًا لهذا المفتاح |
| المرشح المختار | `gpt-5.6-terra` كبداية للكود والاستدلال، بسعر كتالوج $0.0035765 لكل 1K input/output token، وسياق 1.05M |
| الطلب المباشر | نجح؛ JSON مطابق للـ schema، 306 prompt tokens + 708 completion tokens، 8.833 s (تقدير الكتالوج ≈ $0.0036) |
| تكامل B2C من WSL | Cloudflare Error 1010 `browser_signature_banned`, HTTP 403, `owner_action_required=true`, `retryable=false`; CF-Ray suffix `MRS` |
| Unit + contract | 54 passed |
| بيانات الاعتماد | لم تُكتب في المستودع أو `.env`؛ ملفات المفتاح المؤقتة في `/tmp` حُذفت بعد كل محاولة |
| B2C model service | بقيت `healthy`، بلا OOM أو إعادة تشغيل |
| AI Company | لم تُعدّل أو تُوقف أي حاوية منها |

## اختيار النموذج

اختير `gpt-5.6-terra` كمرشح عملي أول للكود والمنطق: يصفه الكتالوج بأنه نموذج استدلال وكود من الفئة العليا بسعر متوسط، ويدرج قدرات reasoning وtools وJSON. هذا الاختيار مبدئي، مبني على مواصفات الكتالوج وعلى طلب JSON مباشر واحد؛ ليس مقارنة جودة بين النماذج الثلاثين. مرجع الواجهة والتسعير: https://codecraftapi.com/docs/models.

## تغييرات B2C

- `LLMGateway` يسمح بعنوان خارجي فقط عند تمرير `allow_remote=True` صراحةً، ويشترط HTTPS ومفتاحًا. التشغيل المحلي لا يزال الافتراضي.
- مفتاح API يقرأ من ملف secret يحدده `B2C_LLM_API_KEY_FILE`؛ لا يُخزن في `.env.example` أو الكود أو التليمترية.
- أضيفت اختبارات لعزل opt-in وBearer header. 54 اختبار وحدة وعقد اجتازت.
- أضيف القرار المؤقت D-022 لاختبارات CodeCraft API، مع قصرها على بيانات اصطناعية حتى اكتمال MVP.

## نتيجة الاختبار الحي وحدوده

- الطلب المباشر من PowerShell إلى `https://codecraftapi.com/v1/chat/completions` بالموديل المختار نجح وأعاد `{"capital": "Paris", "country": "France"}` عبر `json_schema`.
- `/models` من WSL أعاد Cloudflare Error 1010: `browser_signature_banned`, `owner_action_required=true`, `retryable=false`, مع توجيه صريح بعدم إعادة المحاولة. هذا منع بصمة عميل Python/WSL عند Cloudflare قبل وصول الطلب لصيغة API JSON، وليس خطأ اعتماد أو كود B2C. نفس المفتاح نجح من Windows. smoke test completion من WSL أعاد HTML 502 من Cloudflare مرتين، لكن لم أكرر الطلب بعد ظهور توجيه 1010. لم أغيّر User-Agent أو أستخدم relay/proxy لتجاوز الرفض.
- لذلك لم أبدّل عامل B2C الجاري إلى الخدمة الخارجية، ولم أشغّل HelloPipeline عليها. إعداد التشغيل الحالي `.env` بقي كما هو محليًا. الحل عند مالك CodeCraft/Cloudflare: السماح رسميًا بتوقيع عميل API للخروج من WSL أو تزويدنا بطريقة اتصال مدعومة؛ لا يوجد تعديل محلي آمن يحل Error 1010، ولن ألتف عليه عبر relay/proxy أو تزوير User-Agent.
- الـ workflow الموجود حاليًا هو HelloPipeline الاختباري؛ مسار تحويل الكتب وRAG لم يُنفّذ بعد، فلا يمكن الادعاء باختبار تحويل كتاب كامل.

## أوامر التحقق

```sh
uv run pytest tests/unit tests/contract -q
# 54 passed

# B2C live JSON smoke from WSL: attempted twice; CodeCraft returned HTTP 502.
# GET /models from WSL: HTTP 403. Temporary secret files removed afterward.
```
