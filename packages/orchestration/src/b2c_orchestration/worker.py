"""Worker process: one Temporal Worker per task queue (light / heavy / io)."""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from b2c_core import (
    ArtifactStore,
    ProvRecorder,
    Settings,
    StageOutputRegistry,
    code_revision,
    configure_logging,
    get_logger,
)
from b2c_core.revision import log_startup_revision
from b2c_indexing import PostgresChunkStore, QueryRetrievalService
from b2c_indexing.embedding_client import OpenAIEmbeddingClient
from b2c_llm_gateway import (
    LLMGateway, PostgresFreeTokenBudget, PostgresLLMCache, PostgresOpenRouterBudget,
)
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from .hello import HelloActivities, HelloPipeline
from .indexing import IndexActivities, IndexApprovedBookWorkflow
from .audio import MP3Activities, GenerateMP3Workflow
from .speech_v2 import FasihSynthesizer, GenerateSpeechV2Workflow, SpeechV2Activities, speech_v2_activities
from .ingest import IngestPDFActivities, IngestPDFWorkflow, OutlineActivities
from .ingest_v2 import IngestPDFV2Workflow, IngestV2Activities, ingest_v2_activities
from .outline_v2 import OutlineV2Activities
from .qa import CourseQAActivities, GenerateCourseQAWorkflow
from .translation import TranslationActivities, TranslateContentWorkflow, RetranslateSegmentWorkflow
from .queues import AUDIO, HEAVY, IO, LIGHT, TRANSLATE_BOOK, max_concurrent_activities
from .translate_book import AssembleBookTranslationWorkflow, TranslateBookActivities, TranslateBookWorkflow
from .translation_profiles import ProfileRuntime, UnknownProfile, load_translation_profiles
from .termbase_store import TermbaseStore
from .translation_store import TranslationResultStore
from .promotion import PromotionService
from .telemetry import setup_tracing

log = get_logger("b2c.worker")


def translation_gateway_factory(cache, *, capture_content: bool = False):
    """llm_gateway client per translation/QE profile (rule 5); local unless the profile opts in."""
    def make(profile):
        api_key = None
        if profile.api_key_file:
            from pathlib import Path
            api_key = Path(profile.api_key_file).read_text(encoding="utf-8").strip() or None
        return LLMGateway(profile.base_url, profile.model_id, cache=cache, timeout_s=profile.timeout_s,
                          allow_remote=profile.allow_remote, api_key=api_key,
                          reasoning_effort=profile.reasoning_effort, capture_content=capture_content)
    return make


class _UnavailableProfiles:
    """Stands in when the profiles file is invalid: the other queues keep running and every
    translate-book batch fails non-retryably with the reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def fingerprint(self, *_args):
        raise UnknownProfile(f"translation profiles unavailable: {self.reason}")

    engine = estimator = term_proposer = termhood_classifier = fingerprint


def translate_book_worker(client, s, *, store, cache, executor) -> Worker:
    """Rebuilt book translation (D-028 part D) on its own queue, beside the old workflows."""
    from b2c_translation.memory import TranslationMemory

    try:
        runtime = ProfileRuntime(load_translation_profiles(s), gateway_factory=translation_gateway_factory(
            cache, capture_content=s.llm_capture_content))
    except Exception as exc:  # a bad profiles file must not take the other queues down
        log.error("translation profiles unavailable", extra={"error": type(exc).__name__})
        runtime = _UnavailableProfiles(type(exc).__name__)
    acts = TranslateBookActivities(artifact_store=store, result_store=TranslationResultStore(s.pg_dsn),
                                   runtime=runtime, memory=TranslationMemory(s.pg_dsn),
                                   termbase_store=TermbaseStore(s.pg_dsn),
                                   promotion=PromotionService(StageOutputRegistry(s.pg_dsn), code_revision=code_revision()))
    return Worker(client, task_queue=TRANSLATE_BOOK,
                  workflows=[TranslateBookWorkflow, AssembleBookTranslationWorkflow],
                  activities=[acts.prepare, acts.build_termbase, acts.check_termbase, acts.translate_batch,
                              acts.assemble],
                  activity_executor=executor, max_concurrent_activities=max_concurrent_activities(TRANSLATE_BOOK))


async def connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                data_converter=pydantic_data_converter,
                                interceptors=[TracingInterceptor()])


async def run_worker() -> None:
    s = Settings()
    configure_logging(s.log_level)
    provider = setup_tracing(f"{s.service_name}-worker", s.otel_endpoint)
    log_startup_revision(log, "worker")

    prov = ProvRecorder(s.pg_dsn)
    prov.migrate()
    cache = PostgresLLMCache(s.pg_dsn)
    cache.migrate()
    store = ArtifactStore.from_settings(s, recorder=prov)
    store.ensure_bucket()
    budget = None
    if "openrouter.ai" in s.llm_base_url and s.llm_model_id.endswith(":free"):
        budget = PostgresOpenRouterBudget(s.pg_dsn, daily_limit=s.openrouter_free_daily_limit,
            min_interval_s=s.openrouter_free_min_interval_seconds)
        budget.migrate()
    elif "api.inceptionlabs.ai" in s.llm_base_url and s.llm_model_id == "mercury-2":
        budget = PostgresFreeTokenBudget(s.pg_dsn, provider="inception-mercury-2",
            token_limit=s.inception_free_token_limit)
        budget.migrate()
    gateway = LLMGateway(s.llm_base_url, s.llm_model_id, cache=cache, timeout_s=s.llm_timeout_s,
                         reasoning_effort=s.llm_reasoning_effort, capture_content=s.llm_capture_content,
                         allow_remote=s.llm_allow_remote, api_key=s.llm_api_key,
                         request_budget=budget, free_daily_limit=s.openrouter_free_daily_limit,
                         free_min_interval_s=s.openrouter_free_min_interval_seconds,
                         page_batch_max_chars=s.openrouter_max_input_chars,
                         page_batch_max_pages=s.openrouter_max_pages_per_request)
    acts = HelloActivities(store=store, gateway=gateway, prov=prov)
    ingest_acts = IngestPDFActivities(
        store=store, prov=prov, gemini_api_key_file=s.gemini_api_key_file or None, gateway=gateway,
    )
    outline_acts = OutlineActivities(store=store, prov=prov, gateway=gateway)
    ingest_v2_acts = IngestV2Activities(  # R3; started only when the API has B2C_INGEST_V2_ENABLED (D-040)
        store=store, prov=prov,
        promotion=PromotionService(StageOutputRegistry(s.pg_dsn), code_revision=code_revision()))
    # R4: outline v2 is promoted automatically by its structure gate + cross-check (no human reviewer)
    outline_v2_acts = OutlineV2Activities(store=store, prov=prov, promotion=PromotionService(
        StageOutputRegistry(s.pg_dsn), code_revision=code_revision()))
    import psycopg
    chunk_store = PostgresChunkStore(lambda: psycopg.connect(s.pg_dsn))
    index_acts = IndexActivities(artifact_store=store, chunk_store=chunk_store,
                                 promotion=PromotionService(StageOutputRegistry(s.pg_dsn),
                                                            code_revision=code_revision()))
    retrieval_services = {"lexical": QueryRetrievalService(chunk_store)}
    retrieval = retrieval_services["lexical"]
    bge_embedding_url = os.getenv("B2C_BGE_EMBEDDING_BASE_URL", "").strip()
    if not bge_embedding_url and s.retrieval_mode == "hybrid":
        bge_embedding_url = s.embedding_base_url
    if bge_embedding_url:
        embedding_key = s.embedding_api_key
        if not embedding_key:
            raise RuntimeError("configured BGE route requires B2C_EMBEDDING_API_KEY_FILE")
        bge_model_id = os.getenv("B2C_BGE_EMBEDDING_MODEL_ID", s.embedding_model_id).strip()
        query_embedder = OpenAIEmbeddingClient(bge_embedding_url, embedding_key,
            model=bge_model_id, timeout_s=s.embedding_timeout_s)
        passage_embedder = OpenAIEmbeddingClient(bge_embedding_url, embedding_key,
            model=bge_model_id, timeout_s=s.embedding_timeout_s)
        retrieval_services["bge-m3"] = QueryRetrievalService(chunk_store,
            embed_query=query_embedder, embed_passage=passage_embedder)
        if s.retrieval_mode == "hybrid":
            retrieval = retrieval_services["bge-m3"]
    gemma_embedding_url = os.getenv("B2C_EMBEDDINGGEMMA_BASE_URL", "").strip()
    gemma_embedding_key_file = os.getenv("B2C_EMBEDDINGGEMMA_API_KEY_FILE", "").strip()
    if gemma_embedding_url and gemma_embedding_key_file:
        from pathlib import Path
        gemma_embedding_key = Path(gemma_embedding_key_file).read_text(encoding="utf-8").strip()
        if not gemma_embedding_key:
            raise RuntimeError("EmbeddingGemma API key file is empty")
        query_embedder = OpenAIEmbeddingClient(gemma_embedding_url, gemma_embedding_key,
            model="embeddinggemma-2-text-270m", purpose="query", timeout_s=s.embedding_timeout_s)
        passage_embedder = OpenAIEmbeddingClient(gemma_embedding_url, gemma_embedding_key,
            model="embeddinggemma-2-text-270m", purpose="document", timeout_s=s.embedding_timeout_s)
        retrieval_services["embeddinggemma2-lite"] = QueryRetrievalService(chunk_store,
            embed_query=query_embedder, embed_passage=passage_embedder)
    from b2c_indexing.graph_router import GraphProviderConfig
    graph_provider_configs = GraphProviderConfig.from_env()
    qa_acts = CourseQAActivities(artifact_store=store, chunk_store=chunk_store,
                                 gateway=gateway, retrieval_service=retrieval,
                                 retrieval_services=retrieval_services,
                                 graph_provider_configs=graph_provider_configs)
    translation_acts = TranslationActivities(
        artifact_store=store, gateway=gateway, booktrans_api_key=s.llm_api_key,
    )
    mp3_acts = MP3Activities(artifact_store=store)

    client = await connect(s)
    translation_acts.temporal_client = client
    if os.getenv("B2C_WORKER_MODE", "").strip().lower() == "generation":
        queue = os.getenv("B2C_GENERATION_TASK_QUEUE", "external-generation")
        executor = ThreadPoolExecutor(max_workers=1)
        worker = Worker(client, task_queue=queue,
            workflows=[GenerateCourseQAWorkflow, TranslateContentWorkflow, RetranslateSegmentWorkflow],
            activities=[qa_acts.generate, translation_acts.translate, translation_acts.retranslate_segment],
            activity_executor=executor, max_concurrent_activities=1)
        log.info("generation worker started", extra={"queue": queue, "model": s.llm_model_id})
        try:
            await worker.run()
        finally:
            executor.shutdown(wait=False)
            provider.shutdown()
        return
    executor = ThreadPoolExecutor(max_workers=sum(max_concurrent_activities(q)
                                                  for q in (LIGHT, HEAVY, IO, TRANSLATE_BOOK)))
    workers = [
        Worker(client, task_queue=LIGHT, workflows=[HelloPipeline, IngestPDFWorkflow, IngestPDFV2Workflow, IndexApprovedBookWorkflow], activities=[acts.finalize, outline_acts.build, index_acts.index_approved_book],
               activity_executor=executor, max_concurrent_activities=max_concurrent_activities(LIGHT)),
        Worker(client, task_queue=HEAVY, workflows=[GenerateCourseQAWorkflow, TranslateContentWorkflow, RetranslateSegmentWorkflow, GenerateMP3Workflow],
               # outline_acts.build stays on LIGHT too, for histories scheduled
               # before outline-heavy-queue-v1.
               activities=[acts.greet, ingest_acts.parse_pdf, *ingest_v2_activities(ingest_v2_acts), outline_v2_acts.build, outline_acts.build, qa_acts.generate, translation_acts.translate, translation_acts.retranslate_segment, mp3_acts.generate],
               activity_executor=executor, max_concurrent_activities=max_concurrent_activities(HEAVY)),
        Worker(client, task_queue=IO, activities=[acts.prepare],
               activity_executor=executor, max_concurrent_activities=max_concurrent_activities(IO)),
        translate_book_worker(client, s, store=store, cache=cache, executor=executor),
    ]
    log.info("worker started", extra={"pid": os.getpid(), "queues": [LIGHT, HEAVY, IO, TRANSLATE_BOOK],
                                      "llm": s.llm_base_url, "model": s.llm_model_id})
    try:
        await asyncio.gather(*(w.run() for w in workers))
    finally:
        provider.shutdown()


async def run_audio_worker() -> None:
    """Run only durable MP3 activities on the isolated audio queue."""
    s = Settings()
    configure_logging(s.log_level)
    provider = setup_tracing(f"{s.service_name}-audio-worker", s.otel_endpoint)
    log_startup_revision(log, "audio-worker")
    prov = ProvRecorder(s.pg_dsn)
    prov.migrate()
    store = ArtifactStore.from_settings(s, recorder=prov)
    store.ensure_bucket()
    mp3_acts = MP3Activities(artifact_store=store)
    # R7: speech v2 (new names; started only on request). Reads accepted assemblies through the
    # registry and unit results from Postgres; renders one segment per TTS request.
    speech_acts = SpeechV2Activities(
        artifact_store=store, result_store=TranslationResultStore(s.pg_dsn),
        promotion=PromotionService(StageOutputRegistry(s.pg_dsn), code_revision=code_revision()),
        synthesizer=FasihSynthesizer.from_env())
    client = await connect(s)
    queue = os.getenv("B2C_AUDIO_TASK_QUEUE", AUDIO)
    executor = ThreadPoolExecutor(max_workers=max_concurrent_activities(AUDIO))
    worker = Worker(
        client,
        task_queue=queue,
        workflows=[GenerateMP3Workflow, GenerateSpeechV2Workflow],
        activities=[mp3_acts.generate, *speech_v2_activities(speech_acts)],
        activity_executor=executor,
        max_concurrent_activities=max_concurrent_activities(AUDIO),
    )
    log.info("audio worker started", extra={"pid": os.getpid(), "queue": queue})
    try:
        await worker.run()
    finally:
        provider.shutdown()


def main() -> None:
    if os.getenv("B2C_WORKER_MODE", "").strip().lower() == "audio":
        asyncio.run(run_audio_worker())
    else:
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
