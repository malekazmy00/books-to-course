"""HelloPipeline: 3 idempotent activities used to prove the Phase 0 plumbing.

prepare (io) -> greet (heavy, calls the LLM through llm_gateway) -> finalize (light).
Every activity computes a stage_key first and returns the stored artifact if it already exists,
so re-running after a crash never recomputes finished work (docs/02 §1.1).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import BaseModel
from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import ArtifactRef, stage_key

    from .queues import HEAVY, IO, LIGHT
    from .retry import DEFAULT_RETRY

STAGE_VERSION = "1"
PROMPT_VERSION = "hello-v1"


@dataclass
class HelloInput:
    course_id: str
    name: str
    delay_s: float = 0.0  # test hook for T-P0-03: pause inside `greet` (first attempt only)


class Greeting(BaseModel):
    name: str
    greeting: str


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HelloActivities:
    def __init__(self, store: Any, gateway: Any, prov: Any | None) -> None:
        self.store = store
        self.gateway = gateway
        self.prov = prov

    def _record(self, stage: str, ref: ArtifactRef, inputs: list[str], agent: str | None,
                prompt_version: str | None) -> None:
        if self.prov is None:
            return
        act = self.prov.start_activity(stage=stage, stage_version=STAGE_VERSION, agent=agent,
                                       prompt_version=prompt_version,
                                       activity_id=f"act:{stage}:{ref.key[:16]}")
        for i in inputs:
            self.prov.used(act, i)
            self.prov.derived(ref.uri, i)
        self.prov.generated(ref.uri, act)
        self.prov.end_activity(act)

    def _log(self, stage: str, ref: ArtifactRef, hit: bool) -> None:
        activity.logger.info("stage done", extra={"stage": stage, "key": ref.key, "cache_hit": hit,
                                                   "attempt": activity.info().attempt})

    @activity.defn(name="hello_prepare")
    def prepare(self, inp: HelloInput) -> dict[str, Any]:
        payload = json.dumps({"name": inp.name}, ensure_ascii=False).encode()
        key = stage_key("hello-prepare", STAGE_VERSION, [_sha(payload)], {})
        ref, hit = self.store.get_or_compute(inp.course_id, "hello-prepare", key,
                                             lambda: {"input.json": payload})
        if not hit:
            self._record("hello-prepare", ref, [], None, None)
        self._log("hello-prepare", ref, hit)
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit}

    @activity.defn(name="hello_greet")
    def greet(self, inp: HelloInput, prev: dict[str, Any]) -> dict[str, Any]:
        src = ArtifactRef.model_validate(prev)
        key = stage_key("hello-greet", STAGE_VERSION, [src.sha256], {"max_tokens": 64},
                        self.gateway.model_id, PROMPT_VERSION)

        def compute() -> dict[str, bytes]:
            if inp.delay_s and activity.info().attempt == 1:
                end = time.monotonic() + inp.delay_s
                while time.monotonic() < end:  # heartbeat so a dead worker is detected quickly
                    activity.heartbeat("sleeping")
                    time.sleep(1)
            name = json.loads(self.store.read(src, "input.json"))["name"]
            g = self.gateway.structured(
                [{"role": "system", "content": "You write one short friendly greeting."},
                 {"role": "user", "content": f"Greet the person named '{name}'. Return JSON with "
                                             f"`name` set exactly to '{name}' and `greeting`."}],
                Greeting, prompt_version=PROMPT_VERSION, max_tokens=64)
            return {"greeting.json": g.model_dump_json().encode()}

        ref, hit = self.store.get_or_compute(inp.course_id, "hello-greet", key, compute)
        if not hit:
            self._record("hello-greet", ref, [src.uri], self.gateway.model_id, PROMPT_VERSION)
        self._log("hello-greet", ref, hit)
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit}

    @activity.defn(name="hello_finalize")
    def finalize(self, inp: HelloInput, prev: dict[str, Any]) -> dict[str, Any]:
        src = ArtifactRef.model_validate(prev)
        key = stage_key("hello-finalize", STAGE_VERSION, [src.sha256], {})

        def compute() -> dict[str, bytes]:
            g = Greeting.model_validate_json(self.store.read(src, "greeting.json"))
            if g.name != inp.name:
                raise ValueError(f"LLM changed the name: {g.name!r} != {inp.name!r}")
            return {"result.json": json.dumps(g.model_dump(), ensure_ascii=False).encode()}

        ref, hit = self.store.get_or_compute(inp.course_id, "hello-finalize", key, compute)
        if not hit:
            self._record("hello-finalize", ref, [src.uri], None, None)
        self._log("hello-finalize", ref, hit)
        return {"ref": ref.model_dump(mode="json"), "cache_hit": hit,
                "result": json.loads(self.store.read(ref, "result.json"))}


@workflow.defn
class HelloPipeline:
    @workflow.run
    async def run(self, inp: HelloInput) -> dict[str, Any]:
        p = await workflow.execute_activity(
            "hello_prepare", inp, task_queue=IO, retry_policy=DEFAULT_RETRY,
            start_to_close_timeout=timedelta(minutes=1))
        g = await workflow.execute_activity(
            "hello_greet", args=[inp, p["ref"]], task_queue=HEAVY, retry_policy=DEFAULT_RETRY,
            start_to_close_timeout=timedelta(minutes=15), heartbeat_timeout=timedelta(seconds=10))
        f = await workflow.execute_activity(
            "hello_finalize", args=[inp, g["ref"]], task_queue=LIGHT, retry_policy=DEFAULT_RETRY,
            start_to_close_timeout=timedelta(minutes=1))
        return {"result": f["result"], "refs": [p["ref"], g["ref"], f["ref"]],
                "cache_hits": [p["cache_hit"], g["cache_hit"], f["cache_hit"]]}
