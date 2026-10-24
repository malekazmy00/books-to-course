"""Liveness heartbeats for long synchronous activities.

Long activities (PDF parsing/OCR, QA generation, indexing, MP3 rendering,
translation) spend most of their time inside blocking calls that cannot report
progress. Workflows give those activities a short ``heartbeat_timeout`` so a
dead worker is detected in minutes instead of after the 24h start-to-close
deadline; the pump below keeps a healthy attempt alive in the meantime.
Durable progress still comes from the artifacts each activity writes.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import threading
from collections.abc import Iterator
from datetime import timedelta

from temporalio import activity

# Workflows schedule long activities with this heartbeat timeout. The pump
# interval must stay well below it (see heartbeat_interval_seconds).
LONG_ACTIVITY_HEARTBEAT_TIMEOUT = timedelta(minutes=5)
DEFAULT_INTERVAL_SECONDS = 30.0


def heartbeat_interval_seconds(env_name: str = "B2C_ACTIVITY_HEARTBEAT_INTERVAL_SECONDS",
                               default: float = DEFAULT_INTERVAL_SECONDS) -> float:
    try:
        configured = float(os.getenv(env_name, str(default)))
    except ValueError:
        configured = default
    return min(120.0, max(5.0, configured))


def start_activity_heartbeat_pump(workflow_id: str, *, interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
                                  kind: str = "liveness") -> tuple[threading.Event, threading.Thread]:
    """Heartbeat from a helper thread that runs in a copy of the activity context.

    Progress callbacks cannot heartbeat while a blocking provider call is
    waiting, so an otherwise healthy activity would time out. The pump never
    outlives the activity: it stops when ``stop`` is set or the activity
    context disappears.
    """
    stop = threading.Event()
    activity_context = contextvars.copy_context()

    def pump() -> None:
        while not stop.wait(interval_seconds):
            try:
                activity_context.run(activity.heartbeat, {"workflow_id": workflow_id, "kind": kind})
            except RuntimeError:
                # The activity context is gone or the task already completed.
                return
            except Exception:
                # A transient heartbeat transport failure is retried on the next
                # tick; it must not mask or replace the activity's own errors.
                continue

    thread = threading.Thread(target=pump, name=f"b2c-{kind}-heartbeat", daemon=True)
    thread.start()
    return stop, thread


@contextlib.contextmanager
def activity_heartbeats(kind: str, *, interval_seconds: float | None = None) -> Iterator[None]:
    """Run the enclosed block with a liveness pump; no-op outside an activity."""
    try:
        workflow_id = activity.info().workflow_id
    except RuntimeError:
        # Direct unit invocation outside a Temporal activity.
        yield
        return
    stop, thread = start_activity_heartbeat_pump(
        workflow_id or "", kind=kind,
        interval_seconds=interval_seconds if interval_seconds is not None else heartbeat_interval_seconds())
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2.0)
