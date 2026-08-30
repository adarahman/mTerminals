"""Serialized execution and timeout ownership for analytics pipeline passes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any


class MarketPipelineService:
    """Serialize pipeline passes and retain timed-out worker ownership."""

    def __init__(
        self,
        *,
        run_pipeline: Callable[[], Awaitable[Any]],
        publish_status: Callable[..., Awaitable[Any]],
        pipeline_status: dict,
        timeout_seconds: float,
        delayed_reason: Callable[[float], str],
        delayed_overlay: Callable[[], str],
        source_key: Callable[[], str] | None = None,
    ):
        self._run_pipeline = run_pipeline
        self._publish_status = publish_status
        self._pipeline_status = pipeline_status
        self._timeout_seconds = timeout_seconds
        self._delayed_reason = delayed_reason
        self._delayed_overlay = delayed_overlay
        self._source_key = source_key or (lambda: "")
        self._task: asyncio.Task | None = None
        self._task_source: str | None = None

    @property
    def in_flight(self) -> bool:
        return self._task is not None

    async def cancel_in_flight(self) -> None:
        task = self._task
        self._task = None
        self._task_source = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def collect(self, tick_started_at: float):
        if self._task is None:
            self._pipeline_status["startedAt"] = (
                datetime.now().astimezone().isoformat()
            )
            self._task_source = self._source_key()
            self._task = asyncio.create_task(self._run_pipeline())
        try:
            payload = await asyncio.wait_for(
                asyncio.shield(self._task), timeout=self._timeout_seconds
            )
            task_source = self._task_source
            current_source = self._source_key()
            self._task = None
            self._task_source = None
            if task_source != current_source:
                print(
                    f"[pipeline] discarding stale {task_source} result; "
                    f"active provider is {current_source}",
                    flush=True,
                )
                return None
            timings = payload.get("pipelineTimings") if isinstance(payload, dict) else None
            stale_reason = (
                timings.get("chainStaleReason")
                if isinstance(timings, dict)
                else None
            )
            if stale_reason:
                await self._publish_status("DELAYED", stale_reason)
            else:
                await self._publish_status("LIVE")
            return payload
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - tick_started_at
            await self._publish_status(
                "DELAYED",
                self._delayed_reason(self._timeout_seconds),
                elapsed,
            )
            print(
                f"[pipeline] DELAYED after {elapsed:.2f}s — "
                f"{self._delayed_overlay()}",
                flush=True,
            )
            return None
        except Exception as exc:
            self._task = None
            self._task_source = None
            await self._publish_status(
                "DELAYED", f"Analytics pipeline failed: {exc}"
            )
            print(f"[pipeline] FAILED: {exc}", flush=True)
            return None


class SerializedPipelineExecutor:
    """Serialize blocking analytics work and related state mutations."""

    def __init__(self, *, lock=None):
        self._lock = lock or asyncio.Lock()

    @asynccontextmanager
    async def exclusive_scope(self):
        async with self._lock:
            yield

    async def run_blocking(self, operation: Callable[[], Any]):
        async with self._lock:
            return await asyncio.to_thread(operation)

    async def run_exclusive(self, operation: Callable[[], Any]):
        async with self._lock:
            return operation()
