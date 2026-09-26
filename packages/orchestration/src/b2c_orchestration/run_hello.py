"""CLI for HelloPipeline.

  b2c-hello run --name Ada                      start and wait, print JSON result
  b2c-hello start --name Ada --delay 30         start only, print workflow id
  b2c-hello wait <workflow_id>                  wait for result
  b2c-hello history <workflow_id>               activity events (attempts, worker identity)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from temporalio.api.enums.v1 import EventType

from b2c_core import Settings

from .hello import HelloInput, HelloPipeline
from .queues import LIGHT
from .telemetry import setup_tracing
from .worker import connect


async def start(name: str, course_id: str, delay_s: float = 0.0) -> str:
    client = await connect(Settings())
    wid = f"hello-{name}-{uuid.uuid4().hex[:8]}"
    await client.start_workflow(HelloPipeline.run, HelloInput(course_id, name, delay_s),
                                id=wid, task_queue=LIGHT)
    return wid


async def wait(wid: str) -> dict[str, Any]:
    client = await connect(Settings())
    return await client.get_workflow_handle(wid).result()


async def run_pipeline(name: str, course_id: str = "crs_hello", delay_s: float = 0.0) -> dict[str, Any]:
    return await wait(await start(name, course_id, delay_s))


async def history(wid: str) -> list[dict[str, Any]]:
    client = await connect(Settings())
    out = []
    async for ev in client.get_workflow_handle(wid).fetch_history_events():
        kind = EventType.Name(ev.event_type).removeprefix("EVENT_TYPE_")
        if not kind.startswith(("ACTIVITY_TASK", "WORKFLOW_EXECUTION")):
            continue
        row: dict[str, Any] = {"id": ev.event_id, "time": ev.event_time.ToJsonString(), "event": kind}
        if kind == "ACTIVITY_TASK_SCHEDULED":
            a = ev.activity_task_scheduled_event_attributes
            row["activity"] = a.activity_type.name
            row["queue"] = a.task_queue.name
        elif kind == "ACTIVITY_TASK_STARTED":
            a = ev.activity_task_started_event_attributes
            row["attempt"], row["identity"] = a.attempt, a.identity
        elif kind == "ACTIVITY_TASK_TIMED_OUT":
            a = ev.activity_task_timed_out_event_attributes
            row["timeout"] = a.failure.message
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="b2c-hello")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("run", "start"):
        p = sub.add_parser(c)
        p.add_argument("--name", required=True)
        p.add_argument("--course-id", default="crs_hello")
        p.add_argument("--delay", type=float, default=0.0)
    for c in ("wait", "history"):
        sub.add_parser(c).add_argument("workflow_id")
    a = ap.parse_args()

    s = Settings()
    provider = setup_tracing(f"{s.service_name}-cli", s.otel_endpoint)
    try:
        if a.cmd == "run":
            res: Any = asyncio.run(run_pipeline(a.name, a.course_id, a.delay))
        elif a.cmd == "start":
            res = {"workflow_id": asyncio.run(start(a.name, a.course_id, a.delay))}
        elif a.cmd == "wait":
            res = asyncio.run(wait(a.workflow_id))
        else:
            res = asyncio.run(history(a.workflow_id))
        print(json.dumps(res, indent=2, ensure_ascii=False))
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
