import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from b2c_api.app import create_app
from b2c_api.model_lifecycle import ModelLifecycle, Provider, configured_providers
from b2c_contracts import ArtifactRef
from b2c_core import Settings


class Adapter:
    async def ready(self, provider):
        return True


class Temporal:
    namespace = "b2c"
    def __init__(self, pollers=True):
        self.workflow_service = self
        self.pollers = pollers
        self.started = []
        self.queue_checks = []
    async def describe_task_queue(self, request):
        self.queue_checks.append(request)
        available = self.pollers.get(request.task_queue_type, False) if isinstance(self.pollers, dict) else self.pollers
        return SimpleNamespace(pollers=[object()] if available else [])
    async def start_workflow(self, run, inp, **kwargs):
        self.started.append((inp, kwargs))


class Store:
    bucket = "artifacts"
    def __init__(self, original=None):
        self.s3 = self
        self.original = original
        self.writes = []
    def put_object(self, **kwargs):
        self.writes.append(kwargs)
    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(self.original.model_dump_json().encode())}
    def read(self, *args):
        # Book attribution is tested separately; this does not mutate any book.
        raise ValueError("no parsed book fixture")
    def exists(self, course_id, stage, key):
        # The source parsed book is a committed artifact of course "c".
        return SOURCE if (course_id, stage, key) == ("c", SOURCE.stage, SOURCE.key) else None


SOURCE = ArtifactRef(stage="ingest-parse-pdf", key="1"*64, uri="s3://artifacts/c/ingest-parse-pdf/"+"1"*64+"/",
    sha256="a"*64, created_at=datetime.now(UTC))


def manager():
    return ModelLifecycle([Provider(id="hy-mt2-1-8b", label="Hy", stages=["translation"],
        kind="local", adapter="openai", base_url="http://hy-translation:3007/v1",
        model="tencent/Hy-MT2-1.8B", task_queue="hy-translation")], Adapter())


@pytest.mark.asyncio
@pytest.mark.parametrize("pollers", [True, False])
async def test_explicit_hy_route_requires_model_and_workflow_poller(pollers):
    temporal = Temporal(pollers)
    async def connect(_): return temporal
    app = create_app(settings=Settings(), artifact_store=Store(), temporal_connect=connect, model_lifecycle=manager())
    request = {"source_artifact_ref":SOURCE.model_dump(mode="json"), "source_language":"en",
               "target_language":"ar", "provider_id":"hy-mt2-1-8b"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        result = await client.post("/v1/courses/c/translations",json=request)
    assert result.status_code == (202 if pollers else 503), result.text
    assert len(temporal.started) == int(pollers)
    if pollers:
        inp, kwargs = temporal.started[0]
        assert inp.selected_model_id == "tencent/Hy-MT2-1.8B"
        assert inp.selected_provider_id == "hy-mt2-1-8b"
        assert inp.activity_task_queue == kwargs["task_queue"] == "hy-translation"
        assert len(temporal.queue_checks) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pollers", [True, False])
async def test_booktrans_pro_route_is_dispatched_only_when_its_queue_is_polling(pollers):
    temporal = Temporal(pollers)
    async def connect(_): return temporal
    provider = Provider(
        id="booktrans-pro-external", label="BookTrans Pro",
        stages=["translation"], kind="external", adapter="openai",
        translation_engine="booktrans-pro",
        base_url="https://api.example.test/v1", model="model-small",
        task_queue="external-generation", evaluation_status="limited_test",
    )
    app = create_app(settings=Settings(), artifact_store=Store(), temporal_connect=connect,
                     model_lifecycle=ModelLifecycle([provider], Adapter()))
    request = {"source_artifact_ref":SOURCE.model_dump(mode="json"), "source_language":"en",
               "target_language":"ar", "provider_id":"booktrans-pro-external"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        result = await client.post("/v1/courses/c/translations",json=request)
    assert result.status_code == (202 if pollers else 503), result.text
    if pollers:
        inp, kwargs = temporal.started[0]
        assert inp.selected_provider_id == "booktrans-pro-external"
        assert inp.selected_translation_engine == "booktrans-pro"
        assert inp.activity_task_queue == kwargs["task_queue"] == "external-generation"


@pytest.mark.asyncio
async def test_resume_can_explicitly_switch_hy_without_discarding_checkpoint(monkeypatch):
    import importlib
    from b2c_orchestration.translation import TranslateContentInput
    module=importlib.import_module("b2c_api.routers.translations")
    monkeypatch.setattr(module,"translation_manual_overrides",lambda *args: [])
    original = TranslateContentInput(course_id="c", source_artifact_ref=SOURCE.model_dump(mode="json"),
        source_kind="book",source_language="en",target_language="ar",max_tokens=8192,
        activity_task_queue="external-generation",selected_model_id="deepseek",selected_provider_id="configured-llm")
    temporal=Temporal()
    async def connect(_): return temporal
    app=create_app(settings=Settings(),artifact_store=Store(original),temporal_connect=connect,model_lifecycle=manager())
    workflow="b2c-translate-"+"1"*64
    request={"source_artifact_ref":SOURCE.model_dump(mode="json"),"source_language":"en","target_language":"ar",
        "provider_id":"hy-mt2-1-8b","resume_from_workflow_id":workflow}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        result=await client.post("/v1/courses/c/translations",json=request)
    assert result.status_code == 202,result.text
    inp,_=temporal.started[0]
    assert inp.resume_from_workflow_id == workflow
    assert inp.selected_model_id == "tencent/Hy-MT2-1.8B"
    assert app.state.model_lifecycle.defaults == {}


def test_hy_catalog_is_not_claimed_tested_or_enabled_without_service(monkeypatch):
    monkeypatch.delenv("B2C_MODEL_CATALOG_FILE",raising=False)
    monkeypatch.delenv("B2C_HY_TRANSLATION_ENABLED",raising=False)
    provider=next(x for x in configured_providers(Settings()) if x.id == "hy-mt2-1-8b")
    assert provider.task_queue is None and provider.adapter == "unsupported"
    assert provider.evaluation_status == "not_tested"
    monkeypatch.setenv("B2C_HY_TRANSLATION_ENABLED","true")
    provider=next(x for x in configured_providers(Settings()) if x.id == "hy-mt2-1-8b")
    assert provider.task_queue == "hy-translation" and provider.adapter == "openai"
    assert provider.evaluation_status == "not_tested"

async def _ready():
    return True

@pytest.mark.asyncio
async def test_hy_status_not_ready_without_worker_probe():
    m=manager()
    assert (await m.status("hy-mt2-1-8b"))["state"] == "inactive"
    m.route_ready=lambda _: _ready()
    assert (await m.status("hy-mt2-1-8b"))["state"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_poller,activity_poller", [(False,False),(True,False),(False,True),(True,True)])
async def test_catalog_hy_readiness_requires_workflow_poller(workflow_poller,activity_poller):
    from temporalio.api.enums.v1 import TaskQueueType
    temporal=Temporal({TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW: workflow_poller,
                       TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY: activity_poller})
    async def connect(_): return temporal
    app=create_app(settings=Settings(),artifact_store=Store(),temporal_connect=connect,model_lifecycle=manager())
    status=await app.state.model_lifecycle.status("hy-mt2-1-8b")
    assert status["state"] == ("ready" if workflow_poller else "inactive")
    assert temporal.started == []
    assert len(temporal.queue_checks) == 1
    check = temporal.queue_checks[0]
    assert check.task_queue.name == "hy-translation" and check.namespace == "b2c"
    assert check.task_queue_type == TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW

@pytest.mark.asyncio
async def test_contextual_glossary_is_persisted_and_changes_workflow_identity():
    from b2c_translation.glossary import ContextualGlossary
    temporal = Temporal()
    store = Store()
    async def connect(_): return temporal
    app = create_app(settings=Settings(), artifact_store=store,
        temporal_connect=connect, model_lifecycle=manager())
    base = {"source_artifact_ref": SOURCE.model_dump(mode="json"),
        "source_language":"en", "target_language":"ar", "provider_id":"hy-mt2-1-8b"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        for domain, term in (("intelligence-analysis","استخبارات"),("artificial-intelligence","ذكاء")):
            request={**base, "contextual_glossary":{"domain_id":domain,"mappings":{"intelligence":term}}}
            response=await client.post("/v1/courses/c/translations",json=request)
            assert response.status_code == 202, response.text
    assert len(temporal.started) == 2
    first, second = [item[0] for item in temporal.started]
    assert first.contextual_glossary.mappings["intelligence"] == "استخبارات"
    assert second.contextual_glossary.mappings["intelligence"] == "ذكاء"
    assert temporal.started[0][1]["id"] != temporal.started[1][1]["id"]
    persisted=[json.loads(x["Body"]) for x in store.writes if x["Key"].startswith("_system/translation-requests/")]
    assert persisted[0]["contextual_glossary"]["sha256"] == first.contextual_glossary.sha256

@pytest.mark.asyncio
async def test_resume_cannot_change_contextual_glossary(monkeypatch):
    from b2c_orchestration.translation import TranslateContentInput
    from b2c_translation.glossary import ContextualGlossary
    original=TranslateContentInput(course_id="c", source_artifact_ref=SOURCE.model_dump(mode="json"),
        source_kind="book",source_language="en",target_language="ar",
        contextual_glossary=ContextualGlossary(domain_id="intelligence-analysis",mappings={"intelligence":"استخبارات"}))
    temporal=Temporal()
    async def connect(_): return temporal
    app=create_app(settings=Settings(),artifact_store=Store(original),temporal_connect=connect,model_lifecycle=manager())
    request={"source_artifact_ref":SOURCE.model_dump(mode="json"),"source_language":"en","target_language":"ar",
        "provider_id":"hy-mt2-1-8b","resume_from_workflow_id":"b2c-translate-"+"1"*64,
        "contextual_glossary":{"domain_id":"artificial-intelligence","mappings":{"intelligence":"ذكاء"}}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.post("/v1/courses/c/translations",json=request)
    assert response.status_code == 422
    assert temporal.started == []
