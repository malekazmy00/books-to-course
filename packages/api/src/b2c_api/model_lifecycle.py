"""Operator model readiness. Never changes workflow inputs or stops serving models."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import APIRouter, Header, HTTPException, Request as HTTPRequest
from pydantic import BaseModel, Field, SecretStr


class Provider(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    label: str
    stages: list[Literal["ocr", "outline", "rag", "questions", "enrichment", "translation", "audio"]]
    kind: Literal["local", "external"]
    adapter: Literal["unsupported", "openai", "health", "ollama"] = "unsupported"
    translation_engine: Literal["b2c", "booktrans-pro"] = "b2c"
    load_mode: Literal["generation", "embedding"] = "generation"
    base_url: str = ""
    model: str = ""
    # Only trusted, repository-defined task queues are accepted here.
    task_queue: Literal["heavy", "external-generation", "gemma4-generation", "hy-translation"] | None = None
    option_type: Literal["model", "embedding", "retrieval_engine"] = "model"
    evaluation_status: Literal["tested", "limited_test", "not_tested"] = "not_tested"
    evaluation_note: str = ""
    key_file: str = ""
    idle_minutes: int = Field(default=5, ge=1, le=60)
    pricing_status: Literal["unknown", "verified_free", "paid"] = "unknown"
    pricing_verified_at: str = ""
    pricing_source: str = ""
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)
    languages: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    context_tokens: int | None = Field(default=None, gt=0)

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "stages": self.stages,
                "kind": self.kind, "model": self.model,
                "translation_engine": self.translation_engine,
                "activation_supported": self.adapter == "ollama",
                "resource_policy": "unload_after_idle" if self.adapter == "ollama" else "service_managed",
                "idle_minutes": self.idle_minutes if self.adapter == "ollama" else None,
                "job_switching_supported": self.task_queue is not None or (
                    self.option_type in {"embedding", "retrieval_engine"}
                    and self.adapter in {"openai", "health"} and bool(self.base_url)
                ),
                "task_queue": self.task_queue,
                "option_type": self.option_type,
                "evaluation_status": self.evaluation_status,
                "evaluation_note": self.evaluation_note,
                "pricing_status": self.pricing_status,
                "pricing_verified_at": self.pricing_verified_at,
                "pricing_source": self.pricing_source,
                "input_usd_per_million": self.input_usd_per_million,
                "output_usd_per_million": self.output_usd_per_million,
                "languages": self.languages, "capabilities": self.capabilities,
                "context_tokens": self.context_tokens}


@dataclass
class ModelState:
    state: str = "inactive"
    message: str = "لم يُفحص النموذج بعد"
    updated_at: str = ""


class HTTPAdapter:
    """Only operator-supplied URLs; callers cannot submit URLs, commands, or secrets."""

    async def request(self, provider: Provider, path: str, body: dict | None = None) -> dict:
        def execute() -> dict:
            headers = {"Content-Type": "application/json"}
            if provider.key_file:
                key = Path(provider.key_file).read_text().strip()
                if key:
                    headers["Authorization"] = "Bearer " + key
            data = json.dumps(body).encode() if body is not None else None
            request = Request(provider.base_url.rstrip("/") + path, data=data, headers=headers)
            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            with build_opener(NoRedirect()).open(request, timeout=600 if body else 10) as response:
                return json.load(response)
        return await asyncio.to_thread(execute)

    async def ready(self, provider: Provider) -> bool:
        if provider.adapter == "health":
            value = await self.request(provider, "/health")
            return value.get("status") in {"ok", "ready"}
        if provider.adapter == "openai":
            value = await self.request(provider, "/models")
            return any(row.get("id") == provider.model for row in value.get("data", []))
        if provider.adapter == "ollama":
            value = await self.request(provider, "/api/ps")
            return any(row.get("name") == provider.model or row.get("model") == provider.model
                       for row in value.get("models", []))
        return False

    async def load(self, provider: Provider, *, pinned: bool = False) -> None:
        body = {"model": provider.model,
                "keep_alive": -1 if pinned else f"{provider.idle_minutes}m"}
        if provider.load_mode == "embedding":
            body["input"] = ""
            await self.request(provider, "/api/embed", body)
        else:
            body.update({"prompt": "", "stream": False})
            await self.request(provider, "/api/generate", body)


class ModelLifecycle:
    def __init__(self, providers: list[Provider], adapter=None, preferences_file: str = "",
                 catalog_file: str = "", secrets_dir: str = ""):
        self.providers = {p.id: p for p in providers}
        if len(self.providers) != len(providers):
            raise ValueError("duplicate provider id")
        self.adapter = adapter or HTTPAdapter()
        self.route_ready = None  # API supplies an async worker-queue probe.
        self.states = {p.id: (ModelState("unsupported", "لا يوجد مسار تشغيل آمن لهذا الخيار بعد")
                             if p.adapter == "unsupported" else ModelState()) for p in providers}
        self.tasks: dict[str, asyncio.Task] = {}
        self.preferences_file = preferences_file
        self.catalog_file = catalog_file
        self.secrets_dir = secrets_dir
        self.catalog_lock = asyncio.Lock()
        self.defaults: dict[str, str] = {}
        self.default_task: asyncio.Task | None = None
        self.warm_task: asyncio.Task | None = None
        self.default_lock = asyncio.Lock()
        if preferences_file and Path(preferences_file).exists():
            self.defaults = json.loads(Path(preferences_file).read_text()).get("defaults", {})
            for stage, provider_id in self.defaults.items():
                if provider_id not in self.providers or stage not in self.providers[provider_id].stages:
                    raise ValueError("saved stage default does not support the stage")

    def provider(self, provider_id: str) -> Provider:
        if provider_id not in self.providers:
            raise HTTPException(404, "unknown provider")
        return self.providers[provider_id]

    def update(self, provider_id: str, state: str, message: str) -> None:
        self.states[provider_id] = ModelState(state, message, datetime.now(UTC).isoformat())

    def snapshot(self, provider_id: str) -> dict:
        p = self.provider(provider_id)
        state = self.states[provider_id]
        return {**p.public(), "state": state.state, "message": state.message,
                "updated_at": state.updated_at, "poll_after_ms": 2000,
                "default_for_stages": [stage for stage, selected in self.defaults.items() if selected == p.id]}

    async def ready(self, p: Provider) -> bool:
        if not await self.adapter.ready(p):
            return False
        if p.task_queue == "hy-translation":
            return bool(self.route_ready is not None and await self.route_ready(p))
        if p.translation_engine == "booktrans-pro":
            return bool(self.route_ready is not None and await self.route_ready(p))
        return True

    async def set_default(self, stage: str, provider_id: str) -> dict:
        p = self.provider(provider_id)
        if stage not in p.stages:
            raise HTTPException(409, "provider does not support selected stage")
        local_route = p.kind == "local" and p.adapter in {"ollama", "health", "openai"}
        external_route = p.kind == "external" and p.adapter == "openai" and p.base_url.startswith("https://")
        retrieval_route = (stage == "rag" and p.option_type in {"embedding", "retrieval_engine"}
                           and p.kind == "local" and p.adapter in {"openai", "health"} and bool(p.base_url))
        if not ((p.task_queue and (local_route or external_route)) or retrieval_route):
            raise HTTPException(409, "default switching requires a configured task route and readiness adapter")
        if not self.preferences_file:
            raise HTTPException(503, "durable model preferences are not configured")
        async with self.default_lock:
            previous = self.defaults.get(stage, "")
            # Set a new preference only after real loading/readiness, keeping the old default usable.
            self.update(p.id, "loading", "يجري تجهيز النموذج الافتراضي الجديد")
            try:
                if p.adapter == "ollama":
                    await self.adapter.load(p, pinned=True)
                if not await self.ready(p):
                    raise RuntimeError("readiness probe failed")
                path = Path(self.preferences_file)
                temporary = path.with_suffix(path.suffix + ".tmp")
                new_defaults = {**self.defaults, stage: p.id}
                temporary.write_text(json.dumps({"defaults": new_defaults}))
                temporary.replace(path)
            except Exception:
                if p.adapter == "ollama" and p.id not in self.defaults.values():
                    try:
                        await self.adapter.load(p, pinned=False)
                    except Exception:
                        pass
                self.update(p.id, "failed", "تعذر تحميل أو حفظ اختيار النموذج الافتراضي")
                raise HTTPException(503, "default model readiness or persistence failed") from None
            self.defaults = new_defaults
            self.update(p.id, "ready", "النموذج الافتراضي جاهز")
            if previous and previous not in self.defaults.values() and self.provider(previous).adapter == "ollama":
                # Bounded idle expiration, never a forced unload while another request is active.
                try:
                    await self.adapter.load(self.provider(previous), pinned=False)
                except Exception:
                    self.update(previous, "unavailable", "تعذر تحديث مهلة الخمول للنموذج السابق")
            self.ensure_warm_loop()
            return self.snapshot(p.id)

    def ensure_warm_loop(self) -> None:
        if any(self.provider(provider_id).adapter == "ollama" and self.provider(provider_id).task_queue for provider_id in self.defaults.values()) and (self.warm_task is None or self.warm_task.done()):
            self.warm_task = asyncio.create_task(self._keep_default_warm())

    async def _keep_default_warm(self) -> None:
        while any(self.provider(provider_id).adapter == "ollama" for provider_id in self.defaults.values()):
            async with self.default_lock:
                for provider_id in set(self.defaults.values()):
                    p = self.provider(provider_id)
                    if p.adapter != "ollama" or not p.task_queue:
                        continue
                    try:
                        await self.adapter.load(p, pinned=True)
                        ready = await self.ready(p)
                        self.update(p.id, "ready" if ready else "unavailable",
                                    "النموذج الافتراضي جاهز" if ready else "تعذر تأكيد جاهزية الافتراضي")
                    except Exception:
                        self.update(p.id, "unavailable", "تعذر إبقاء النموذج الافتراضي جاهزًا")
            await asyncio.sleep(120)

    async def choose_default(self, stage: str, provider_id: str) -> dict:
        p = self.provider(provider_id)
        local_route = p.kind == "local" and p.adapter in {"ollama", "health", "openai"}
        external_route = p.kind == "external" and p.adapter == "openai" and p.base_url.startswith("https://")
        retrieval_route = (stage == "rag" and p.option_type in {"embedding", "retrieval_engine"}
                           and p.kind == "local" and p.adapter in {"openai", "health"} and bool(p.base_url))
        if stage not in p.stages or not ((p.task_queue and (local_route or external_route)) or retrieval_route):
            raise HTTPException(409, "provider does not support a configured task route for this stage")
        if not self.preferences_file:
            raise HTTPException(503, "durable model preferences are not configured")
        if self.default_task and not self.default_task.done():
            raise HTTPException(409, "a default change is already in progress")
        async def change():
            try:
                await self.set_default(stage, provider_id)
            except HTTPException:
                pass  # state already records sanitized failure
        self.update(p.id, "starting", "بدأ إعداد النموذج الافتراضي لهذه المرحلة")
        self.default_task = asyncio.create_task(change())
        return self.snapshot(p.id)

    async def close(self) -> None:
        tasks = list(self.tasks.values()) + ([self.warm_task] if self.warm_task else []) + ([self.default_task] if self.default_task else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def register_credentials(self, provider_id: str, model: str, key: SecretStr) -> dict:
        """Configure an existing operator-approved external profile, never a caller URL."""
        p = self.provider(provider_id)
        if p.kind != "external" or p.adapter != "openai" or not p.base_url.startswith("https://"):
            raise HTTPException(409, "an approved external provider profile is required")
        if not self.catalog_file or not self.secrets_dir:
            raise HTTPException(503, "durable provider secret storage is not configured")
        async with self.catalog_lock:
            path = Path(self.secrets_dir) / (p.id + "-" + uuid.uuid4().hex + ".key")
            candidate = p.model_copy(update={"model": model, "key_file": str(path)})
            if model != p.model:
                candidate = candidate.model_copy(update={"pricing_status": "unknown",
                    "pricing_verified_at": "", "pricing_source": "",
                    "input_usd_per_million": None, "output_usd_per_million": None})
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as secret:
                    secret.write(key.get_secret_value())
                if not await self.adapter.ready(candidate):
                    raise RuntimeError("model unavailable")
                catalog = Path(self.catalog_file)
                temporary = catalog.with_suffix(catalog.suffix + ".tmp")
                entries = [candidate if existing.id == p.id else existing for existing in self.providers.values()]
                temporary.write_text(json.dumps([existing.model_dump() for existing in entries]))
                temporary.chmod(0o600)
                temporary.replace(catalog)
            except Exception:
                path.unlink(missing_ok=True)
                raise HTTPException(503, "provider authentication, model check, or persistence failed") from None
            self.providers[p.id] = candidate
            self.update(p.id, "ready", "تم حفظ الاتصال والتحقق من إتاحة النموذج؛ التكلفة بحسب آخر بيانات مسجلة")
            return self.snapshot(p.id)

    async def status(self, provider_id: str) -> dict:
        p = self.provider(provider_id)
        if self.states[provider_id].state in {"starting", "loading"}:
            return self.snapshot(provider_id)
        if p.adapter == "unsupported" or not p.base_url:
            self.update(provider_id, "unsupported", "لا يوجد مسار تشغيل آمن لهذا الخيار بعد")
        else:
            try:
                ready = await self.ready(p)
                if ready or self.states[provider_id].state != "failed":
                    self.update(provider_id, "ready" if ready else "inactive",
                                "النموذج جاهز الآن" if ready else "النموذج غير محمّل أو غير جاهز")
            except Exception:
                # Provider exceptions can contain URLs/keys; never return or log them.
                self.update(provider_id, "unavailable", "تعذر الوصول إلى خدمة النموذج")
        return self.snapshot(provider_id)

    async def activate(self, provider_id: str) -> dict:
        p = self.provider(provider_id)
        task = self.tasks.get(provider_id)
        if task is not None and not task.done():
            return self.snapshot(provider_id)
        if p.adapter != "ollama" or not p.base_url:
            value = await self.status(provider_id)
            if value["state"] != "ready":
                self.update(provider_id, "unsupported", "يحتاج تشغيل هذه الخدمة إلى إعداد مشغل آمن على الخادم")
            return self.snapshot(provider_id)
        self.update(provider_id, "starting", "بدأ طلب تجهيز النموذج")
        self.tasks[provider_id] = asyncio.create_task(self._activate(p))
        return self.snapshot(provider_id)

    async def _activate(self, p: Provider) -> None:
        try:
            self.update(p.id, "loading", "يُحمّل النموذج في الذاكرة")
            await self.adapter.load(p, pinned=p.id in self.defaults.values())
            if not await self.ready(p):
                raise RuntimeError("readiness probe failed")
            self.update(p.id, "ready", "اكتمل التحميل والنموذج جاهز الآن")
        except Exception:
            self.update(p.id, "failed", "فشل التحميل أو فحص جاهزية النموذج؛ يمكن إعادة المحاولة")


def configured_providers(settings) -> list[Provider]:
    """Optional operator JSON catalog; missing alternatives stay explicitly unsupported."""
    shared = ["questions", "translation"]
    generation_url = os.getenv("B2C_GENERATION_PROVIDER_BASE_URL", settings.llm_base_url)
    generation_model = os.getenv("B2C_GENERATION_MODEL_ID", settings.llm_model_id)
    generation_key = os.getenv("B2C_GENERATION_PROVIDER_KEY_FILE", settings.llm_api_key_file)
    generation_kind = "external" if generation_url.startswith("https://") or settings.llm_allow_remote else "local"
    providers = [Provider(id="configured-llm", label="DeepSeek V4 Flash · OpenRouter" if generation_url.startswith("https://") else "نموذج النص الحالي", stages=shared,
                         kind=generation_kind,
                         adapter="openai", base_url=generation_url,
                         model=generation_model, key_file=generation_key,
                         task_queue=os.getenv("B2C_GENERATION_TASK_QUEUE", "heavy"),
                         evaluation_status="limited_test" if generation_url.startswith("https://") else "not_tested",
                         evaluation_note="مسار DeepSeek عبر OpenRouter؛ نتيجة Phase 5 محفوظة لعينة محدودة. التحقق هنا يقرأ قائمة النماذج فقط ولا يرسل طلب توليد." if generation_url.startswith("https://") else "يستخدم إعداد النموذج الحالي على الخادم."),
                 Provider(id="rapidocr", label="RapidOCR / PP-OCRv6 المحلي", stages=["ocr"], kind="local",
                          evaluation_status="limited_test",
                          evaluation_note="مسار Docling اختُبر على عينة مطبوعة؛ نتائج الصفحات المتباينة ما زالت دون بوابة قبول عامة."),
                 Provider(id="surya", label="Surya 2 المحلي · بطيء/جودة", stages=["ocr"], kind="local",
                          evaluation_status="limited_test",
                          evaluation_note="عاد بنتائج على صفحات حديثة واختبار API؛ العينة محدودة والوقت على CPU طويل."),
                 Provider(id="hy-mt2-1-8b", label="Hy-MT2 1.8B · ترجمة محلية",
                          stages=["translation"], kind="local",
                          adapter="openai" if os.getenv("B2C_HY_TRANSLATION_ENABLED", "false").lower() == "true" else "unsupported",
                          base_url=os.getenv("B2C_HY_TRANSLATION_BASE_URL", "http://hy-translation:3007/v1"),
                          task_queue="hy-translation" if os.getenv("B2C_HY_TRANSLATION_ENABLED", "false").lower() == "true" else None,
                          model="tencent/Hy-MT2-1.8B", evaluation_status="not_tested",
                          evaluation_note="الأوزان موجودة؛ مسار CPU مستقل من compose.hy-translation.yml. لا يصبح جاهزًا إلا بعد فحص الخدمة وطابور العامل. لم يجر اختبار ترجمة حي لهذا المسار بعد.",
                          languages=["ar", "en"], capabilities=["translation"]),
                 Provider(id="fasih", label="فصيح", stages=["audio"], kind="local",
                          adapter="health", base_url="http://fasih-tts:3006", model="Fasih-TTS-V1",
                          evaluation_status="limited_test",
                          evaluation_note="اختبارات CPU محفوظة لعينات عربية/إنجليزية؛ نحو 17× زمن الصوت الحقيقي، ولا يوجد ربط إنتاجي أو بوابة استماع كاملة."),
                 Provider(id="audio-external", label="صوت خارجي", stages=["audio"], kind="external")]
    bt_enabled = os.getenv("B2C_BOOKTRANS_PRO_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    if bt_enabled and generation_url.startswith("https://"):
        providers.append(Provider(
            id="booktrans-pro-external", label="BookTrans Pro · خارجي",
            stages=["translation"], kind="external", adapter="openai",
            translation_engine="booktrans-pro", base_url=generation_url,
            model=generation_model, key_file=generation_key,
            task_queue=os.getenv("B2C_GENERATION_TASK_QUEUE", "external-generation"),
            evaluation_status="limited_test",
            evaluation_note="مسار BookTrans Pro الكامل للاستكشاف والترجمة والتحرير والتحقق، ثم بوابات B2C للأرقام والاكتمال والمصطلحات وتصحيح العربية. يعتمد على ملف المزود الخارجي المضبوط في الخادم.",
            languages=["en", "ar"],
            capabilities=["whole-book-context", "scout", "translation", "editing", "verification",
                          "arabic-gec", "numeric-guards", "contextual-glossary"],
        ))
    gemma4_enabled = os.getenv("B2C_GEMMA4_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    providers.extend([
        Provider(id="booktrans-pro-gemma4-local", label="BookTrans Pro · Gemma 4 محلي",
                 stages=["translation"], kind="local",
                 adapter="openai" if gemma4_enabled and bt_enabled else "unsupported",
                 translation_engine="booktrans-pro",
                 base_url=os.getenv("B2C_GEMMA4_OPENAI_BASE_URL", "http://gemma4-ollama:11434/v1"),
                 model=os.getenv("B2C_GEMMA4_MODEL_ID", "gemma4:e4b"),
                 task_queue="gemma4-generation" if gemma4_enabled and bt_enabled else None,
                 evaluation_status="limited_test",
                 evaluation_note="BookTrans Pro عبر واجهة Ollama المتوافقة مع OpenAI؛ يليه تحقق B2C وتصحيح العربية. يتطلب تفعيل عامل Gemma4 وBookTrans Pro.",
                 languages=["en", "ar"],
                 capabilities=["whole-book-context", "scout", "translation", "editing", "verification",
                               "arabic-gec", "numeric-guards", "contextual-glossary"]),
        Provider(id="gemma4-e4b-local", label="Gemma 4 E4B · Ollama محلي",
                 stages=["questions", "translation"], kind="local",
                 adapter="ollama", base_url="http://gemma4-ollama:11434",
                 model="gemma4:e4b", task_queue="gemma4-generation" if gemma4_enabled else None,
                 option_type="model", evaluation_status="limited_test",
                 evaluation_note="اختبار محدود محفوظ؛ للتفعيل يلزم تشغيل خدمة Ollama وعامل Gemma4 من compose.gemma4.yml. إثراء الدرس يتبع نموذج الأسئلة ولا يملك اختيارًا مستقلًا بعد. ظهر انقلاب معنى في الترجمة وصُحح مثال واحد بالقاموس؛ قارن قبل الاعتماد.",
                 languages=["ar", "en"], capabilities=["text-generation", "reasoning", "structured-output"],
                 context_tokens=131072),
        Provider(id="bge-m3", label="BGE-M3 · تضمين هجين",
                 stages=["rag"], kind="local", option_type="embedding",
                 adapter="openai" if (url := os.getenv("B2C_BGE_EMBEDDING_BASE_URL", getattr(settings, "embedding_base_url", ""))) else "unsupported",
                 base_url=url, model="BAAI/bge-m3",
                 key_file=os.getenv("B2C_BGE_EMBEDDING_API_KEY_FILE", getattr(settings, "embedding_api_key_file", "")),
                 evaluation_status="tested", evaluation_note="BGE-M3 embeddings; default requires endpoint readiness."),
        Provider(id="embeddinggemma2-lite", label="EmbeddingGemma 2 · LiteRT",
                 stages=["rag"], kind="local", option_type="embedding",
                 adapter="openai" if (url := os.getenv("B2C_EMBEDDINGGEMMA_BASE_URL", "")) else "unsupported",
                 base_url=url, model="embeddinggemma-2-text-270m",
                 key_file=os.getenv("B2C_EMBEDDINGGEMMA_API_KEY_FILE", ""),
                 evaluation_status="tested", evaluation_note="LiteRT 768d; route checks live endpoint readiness."),
        Provider(id="lightrag-157", label="LightRAG 1.5.7",
                 stages=["rag"], kind="local", option_type="retrieval_engine",
                 adapter="health" if (url := os.getenv("B2C_LIGHTRAG_SERVICE_URL", "")) else "unsupported",
                 base_url=url, model="lightrag-1.5.7",
                 evaluation_status="tested", evaluation_note="Scoped retrieval by course and generation; test a full index before adoption."),
        Provider(id="hipporag-2", label="HippoRAG 2",
                 stages=["rag"], kind="local", option_type="retrieval_engine",
                 adapter="health" if (url := os.getenv("B2C_HIPPORAG_SERVICE_URL", "")) else "unsupported",
                 base_url=url, model="hipporag-2.0.0a5",
                 evaluation_status="tested", evaluation_note="Isolated service; requires dedicated index and live readiness."),
    ])
    filename = os.getenv("B2C_MODEL_CATALOG_FILE", "")
    if filename:
        configured = [Provider.model_validate(row) for row in json.loads(Path(filename).read_text())]
        defaults = {p.id: p for p in providers}
        defaults.update({p.id: p for p in configured})
        providers = list(defaults.values())
    for p in providers:
        if p.pricing_status == "verified_free" and not (p.pricing_source and p.pricing_verified_at):
            raise ValueError("free status requires pricing verification evidence")
        if p.adapter == "unsupported":
            continue
        url = urlsplit(p.base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("invalid operator provider URL")
        if p.kind == "external" and url.scheme != "https":
            raise ValueError("external providers require HTTPS")
        if p.adapter == "ollama" and p.kind != "local":
            raise ValueError("Ollama loading is local only")
        if p.adapter in {"openai", "ollama"} and not p.model:
            raise ValueError("provider model id is required")
    return providers


def lifecycle_router(manager: ModelLifecycle, operator_token: str) -> APIRouter:
    async def authenticate(authorization: str = Header(default="")):
        if not operator_token:
            raise HTTPException(503, "operator authentication is not configured")
        if not secrets.compare_digest(authorization, "Bearer " + operator_token):
            raise HTTPException(401, "valid bearer token required")

    from fastapi import Depends
    router = APIRouter(prefix="/v1/model-providers", dependencies=[Depends(authenticate)])

    @router.get("")
    async def catalog():
        manager.ensure_warm_loop()
        return {"providers": [manager.snapshot(p) for p in manager.providers],
                "job_switching_supported": False, "defaults": manager.defaults,
                "default_switching_configured": bool(manager.preferences_file)}

    @router.get("/{provider_id}")
    async def status(provider_id: str):
        return await manager.status(provider_id)

    @router.post("/{provider_id}/activate", status_code=202)
    async def activate(provider_id: str):
        return await manager.activate(provider_id)

    @router.put("/{provider_id}/default/{stage}", status_code=202)
    async def set_default(provider_id: str, stage: str):
        return await manager.choose_default(stage, provider_id)

    @router.put("/{provider_id}/credentials")
    async def credentials(provider_id: str, body: ProviderCredentials, request: HTTPRequest):
        if request.url.scheme != "https":
            raise HTTPException(400, "provider credential registration requires HTTPS")
        return await manager.register_credentials(provider_id, body.model, body.key)

    return router


class ProviderCredentials(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")
    key: SecretStr
