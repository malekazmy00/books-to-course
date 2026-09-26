"""Worker process: one Temporal Worker per task queue (light / heavy / io)."""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from b2c_core import ArtifactStore, ProvRecorder, Settings, configure_logging, get_logger
from b2c_llm_gateway import LLMGateway, PostgresLLMCache

from .hello import HelloActivities, HelloPipeline
from .queues import HEAVY, IO, LIGHT, max_concurrent_activities
from .telemetry import setup_tracing

log = get_logger("b2c.worker")


async def connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                interceptors=[TracingInterceptor()])


async def run_worker() -> None:
    s = Settings()
    configure_logging(s.log_level)
    provider = setup_tracing(f"{s.service_name}-worker", s.otel_endpoint)

    prov = ProvRecorder(s.pg_dsn)
    prov.migrate()
    cache = PostgresLLMCache(s.pg_dsn)
    cache.migrate()
    store = ArtifactStore.from_settings(s, recorder=prov)
    store.ensure_bucket()
    gateway = LLMGateway(s.llm_base_url, s.llm_model_id, cache=cache, timeout_s=s.llm_timeout_s)
    acts = HelloActivities(store=store, gateway=gateway, prov=prov)

    client = await connect(s)
    executor = ThreadPoolExecutor(max_workers=sum(max_concurrent_activities(q) for q in (LIGHT, HEAVY, IO)))
    workers = [
        Worker(client, task_queue=LIGHT, workflows=[HelloPipeline], activities=[acts.finalize],
               activity_executor=executor, max_concurrent_activities=max_concurrent_activities(LIGHT)),
        Worker(client, task_queue=HEAVY, activities=[acts.greet],
               activity_executor=executor, max_concurrent_activities=max_concurrent_activities(HEAVY)),
        Worker(client, task_queue=IO, activities=[acts.prepare],
               activity_executor=executor, max_concurrent_activities=max_concurrent_activities(IO)),
    ]
    log.info("worker started", extra={"pid": os.getpid(), "queues": [LIGHT, HEAVY, IO],
                                      "llm": s.llm_base_url, "model": s.llm_model_id})
    try:
        await asyncio.gather(*(w.run() for w in workers))
    finally:
        provider.shutdown()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
