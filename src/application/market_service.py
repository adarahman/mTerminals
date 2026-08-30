"""Application-level orchestration for the blocking analytics pipeline."""
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
        """Cancel the current analytics pass, e.g. after provider switch."""
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
        """Collect one pass without overlapping a timed-out worker."""
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
        """Async scope that holds the pipeline lock for the duration.

        Lets non-pipeline mutations (e.g. a provider switch) run mutually
        exclusive with the analytics pass, which also acquires this lock via
        :meth:`run_blocking`.
        """
        async with self._lock:
            yield

    async def run_blocking(self, operation: Callable[[], Any]):
        async with self._lock:
            return await asyncio.to_thread(operation)

    async def run_exclusive(self, operation: Callable[[], Any]):
        async with self._lock:
            return operation()


class DailyMarketScheduler:
    """Own per-day OI resets and once-per-trading-day EOD scheduling."""

    def __init__(
        self,
        *,
        option_aggregators: Callable[[], dict],
        reset_futures_session: Callable[[], Any],
        is_trading_day: Callable[[datetime], bool],
        eod_trigger_time,
        schedule_eod_jobs: Callable[[datetime], Any],
    ):
        self._option_aggregators = option_aggregators
        self._reset_futures_session = reset_futures_session
        self._is_trading_day = is_trading_day
        self._eod_trigger_time = eod_trigger_time
        self._schedule_eod_jobs = schedule_eod_jobs
        self._session_date = None
        self._eod_date = None

    def reset_sessions(self, now: datetime) -> None:
        if self._session_date == now.date():
            return
        self._session_date = now.date()
        for tag, aggregator in self._option_aggregators().items():
            aggregator.reset_session()
            print(
                f"[{tag}] Reset OI session baseline for new trading day "
                f"{now.date()}",
                flush=True,
            )
        self._reset_futures_session()
        print(
            f"[futures_oi] Reset futures OI session baseline for new trading "
            f"day {now.date()}",
            flush=True,
        )

    def trigger_eod(self, now: datetime) -> None:
        if not (
            self._is_trading_day(now)
            and now.time() >= self._eod_trigger_time
            and self._eod_date != now.date()
        ):
            return
        # Mark before scheduling so a slow job cannot double-fire.
        self._eod_date = now.date()
        print(f"[eod] triggering EOD fetch for {now.date()}", flush=True)
        self._schedule_eod_jobs(now)


class LiveFeedAggregatorRegistry:
    """Expose active feed aggregators without leaking manager state internals."""

    def __init__(self, *, managers: Callable[[], dict]):
        self._managers = managers

    def active(self) -> dict:
        aggregators = {}
        for provider, manager in self._managers().items():
            aggregator = manager.aggregator
            if aggregator is not None:
                aggregators[provider.lower()] = aggregator
        return aggregators
