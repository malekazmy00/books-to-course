"""Companion worker (knowledge queue): the stages CoursePipelineWorkflow runs after translation.

    BookKnowledgeWorkflow             knowledge research + concept layer (EN, D-034/D-035)
    BuildFigureDescriptionsWorkflow   figure/table descriptions for listening (D-044)
    BuildEpisodeScriptsWorkflow       episode scripts, EN then AR (D-036..D-038)

The generator of figures and episodes is the extractor role; the episode validator is the judge
role (another model family). Their Arabic translation uses the translation profiles of the worker
(B2C_TRANSLATION_PROFILES_FILE), the book run's stored results and the translation memory.

Not part of ``worker.py``; compose runs it as ``companion-worker`` (docker/compose.companion.yml), or run it explicitly
(``uv run b2c-knowledge-worker`` / ``python -m b2c_orchestration.knowledge_worker``) when knowledge
research should run. Model roles come from the environment; remote endpoints need explicit opt-in
and a key file outside the repository (D-029, CLAUDE.md rule 6). The citation judge must be from a
different model family than the extractor (learning-companion §2.5).

    B2C_KNOWLEDGE_{EXTRACTOR,JUDGE}_{BASE_URL,MODEL_ID,FAMILY,API_KEY_FILE,ALLOW_REMOTE,REASONING_EFFORT}
    B2C_KNOWLEDGE_CONCEPTS_MODEL_ID      default: the extractor model (same endpoint)
    B2C_KNOWLEDGE_FETCH_CACHE            on-disk fetch cache (keeps original fetch dates)
    B2C_OPENALEX_API_KEY_FILE            optional OpenAlex key; B2C_OPENALEX_MAILTO optional
    B2C_SEARXNG_URL                      b2c-owned SearXNG (compose profile knowledge-search)
    B2C_PAID_SEARCH_KEY_FILE             paid web search API key (not configured by default)
    B2C_KNOWLEDGE_TASK_QUEUE             default "knowledge"
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from b2c_concepts import ConceptModel
from b2c_contracts import ProducerIdentity
from b2c_core import ArtifactStore, ProvRecorder, Settings, StageOutputRegistry, code_revision, configure_logging, get_logger
from b2c_core.revision import log_startup_revision
from b2c_llm_gateway import LLMGateway, PostgresLLMCache
from b2c_research import ModelRole
from b2c_research.http import FileFetchCache, HttpxFetcher
from b2c_research.providers import standard_providers
from b2c_research.sources import PaidSearchAPI, SearchNotConfigured, SearxngSearch
from temporalio.worker import Worker

from .episodes import BuildEpisodeScriptsWorkflow, EpisodeActivities
from .figures import BuildFigureDescriptionsWorkflow, FigureActivities, figure_activities
from .knowledge import BookKnowledgeWorkflow, KnowledgeActivities
from .promotion import PromotionService
from .queues import KNOWLEDGE, max_concurrent_activities
from .telemetry import setup_tracing
from .worker import connect

log = get_logger("b2c.knowledge-worker")
PREFIX = "B2C_KNOWLEDGE_"


def _key(path: str | None) -> str | None:
    return Path(path).read_text(encoding="utf-8").strip() or None if path else None


def model_role(name: str, *, cache: Any = None, env: Mapping[str, str] | None = None,
               role: str | None = None, model_override: str | None = None) -> ModelRole:
    env = os.environ if env is None else env
    p = f"{PREFIX}{name.upper()}_"
    base_url, model_id = env[p + "BASE_URL"], model_override or env[p + "MODEL_ID"]
    family = env.get(p + "FAMILY") or model_id.split("/")[0]
    gateway = LLMGateway(base_url, model_id, cache=cache, allow_remote=env.get(p + "ALLOW_REMOTE", "") == "1",
                         api_key=_key(env.get(p + "API_KEY_FILE")), timeout_s=float(env.get(p + "TIMEOUT_S", "300")),
                         reasoning_effort=env.get(p + "REASONING_EFFORT") or None)
    provider = "openrouter" if "openrouter.ai" in base_url else gateway.provider_name
    return ModelRole(gateway, ProducerIdentity(role=role or name, provider=provider, model_id=model_id), family)


def build_activities(*, store: Any, cache: Any, promotion: PromotionService | None, prov: Any | None = None,
                     env: Mapping[str, str] | None = None, transport: Any = None) -> KnowledgeActivities:
    env = os.environ if env is None else env
    extractor = model_role("extractor", cache=cache, env=env)
    judge = model_role("judge", cache=cache, env=env)
    concepts_role = model_role("extractor", cache=cache, env=env, role="concepts",
                               model_override=env.get(PREFIX + "CONCEPTS_MODEL_ID"))
    cache_dir = env.get(PREFIX + "FETCH_CACHE")
    fetcher = HttpxFetcher(cache=FileFetchCache(cache_dir) if cache_dir else None, transport=transport)
    web = None
    if env.get("B2C_SEARXNG_URL"):
        web = SearxngSearch.internal(env["B2C_SEARXNG_URL"])
    elif env.get("B2C_PAID_SEARCH_KEY_FILE"):
        try:
            web = PaidSearchAPI.from_key_file(env["B2C_PAID_SEARCH_KEY_FILE"])
        except SearchNotConfigured as exc:
            log.warning("paid web search not configured", extra={"reason": str(exc)})
    openalex_key = _key(env.get("B2C_OPENALEX_API_KEY_FILE"))
    mailto = env.get("B2C_OPENALEX_MAILTO")

    def providers(builder, meta):
        return standard_providers(builder, meta, fetcher=fetcher, openalex_api_key=openalex_key, mailto=mailto,
                                  web_search=web)

    return KnowledgeActivities(artifact_store=store, extractor=extractor, judge=judge,
                               concept_model=ConceptModel(concepts_role.llm, concepts_role.identity),
                               providers=providers, promotion=promotion, prov=prov, web_enabled=web is not None)


def _episode_role(role: ModelRole) -> Any:
    from b2c_episodes import ModelRole as EpisodeRole
    return EpisodeRole(role.llm, role.identity, role.family)


def build_companion(*, store: Any, cache: Any, promotion: PromotionService | None, prov: Any | None = None,
                    env: Mapping[str, str] | None = None, transport: Any = None, runtime: Any = None,
                    memory: Any = None, result_store: Any = None) -> tuple[list[Any], list[Any]]:
    """Workflows and activities of the companion queue (knowledge, figures, episode scripts)."""
    env = os.environ if env is None else env
    knowledge = build_activities(store=store, cache=cache, promotion=promotion, prov=prov, env=env,
                                 transport=transport)
    generator = _episode_role(model_role("extractor", cache=cache, env=env, role="generator"))
    judge = _episode_role(model_role("judge", cache=cache, env=env, role="validator"))
    figures = FigureActivities(artifact_store=store, generator=generator, runtime=runtime, memory=memory,
                               promotion=promotion)

    def results(run_id: str) -> dict[str, Any]:
        return {r.unit_id: r.translation for r in result_store.fetch_run(run_id)}

    def drafts(run_id: str) -> dict[str, Any]:
        return {r.unit_id: r.drafts for r in result_store.fetch_run(run_id)}

    episodes = EpisodeActivities(artifact_store=store, generator=generator, judge=judge, runtime=runtime,
                                 memory=memory, book_results=results, book_drafts=drafts, promotion=promotion)
    workflows = [BookKnowledgeWorkflow, BuildFigureDescriptionsWorkflow, BuildEpisodeScriptsWorkflow]
    activities = [knowledge.research, knowledge.concepts, *figure_activities(figures), episodes.build_en,
                  episodes.translate]
    return workflows, activities


async def run_knowledge_worker() -> None:
    s = Settings()
    configure_logging(s.log_level)
    provider = setup_tracing(f"{s.service_name}-knowledge-worker", s.otel_endpoint)
    log_startup_revision(log, "knowledge-worker")
    prov = ProvRecorder(s.pg_dsn)
    prov.migrate()
    cache = PostgresLLMCache(s.pg_dsn)
    cache.migrate()
    store = ArtifactStore.from_settings(s, recorder=prov)
    store.ensure_bucket()
    from b2c_translation.memory import TranslationMemory

    from .translation_profiles import ProfileRuntime, load_translation_profiles
    from .translation_store import TranslationResultStore
    from .worker import translation_gateway_factory
    runtime = ProfileRuntime(load_translation_profiles(s), gateway_factory=translation_gateway_factory(
        cache, capture_content=s.llm_capture_content))
    workflows, activities = build_companion(
        store=store, cache=cache, prov=prov, runtime=runtime, memory=TranslationMemory(s.pg_dsn),
        result_store=TranslationResultStore(s.pg_dsn),
        promotion=PromotionService(StageOutputRegistry(s.pg_dsn), code_revision=code_revision()))
    client = await connect(s)
    queue = os.getenv(PREFIX + "TASK_QUEUE", KNOWLEDGE)
    worker = Worker(client, task_queue=queue, workflows=workflows, activities=activities,
                    activity_executor=ThreadPoolExecutor(max_workers=max_concurrent_activities(KNOWLEDGE)),
                    max_concurrent_activities=max_concurrent_activities(KNOWLEDGE))
    log.info("companion worker started", extra={"pid": os.getpid(), "queue": queue,
                                                "workflows": [w.__name__ for w in workflows]})
    try:
        await worker.run()
    finally:
        provider.shutdown()


def main() -> None:
    asyncio.run(run_knowledge_worker())


if __name__ == "__main__":
    main()
