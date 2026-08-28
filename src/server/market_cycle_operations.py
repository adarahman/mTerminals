"""Operational policies used by the server's recurring market cycle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import datetime
from typing import Any


class MarketCycleOperations:
    """Publish pipeline health and schedule work around a market cycle."""

    def __init__(
        self,
        *,
        pipeline_status: MutableMapping[str, Any],
        broadcast: Callable[[dict[str, Any]], Awaitable[None]],
        use_broker_services: Callable[[], bool],
        live_feed_provider: Callable[[], str],
        data_source: Callable[[], str],
        feed_allowed: Callable[[str], bool],
        fetch_all_eod: Callable[..., Any],
        record_today_flow: Callable[[], Any],
        eod_task_done: Callable[[asyncio.Task[Any]], None],
        flow_task_done: Callable[[asyncio.Task[Any]], None],
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._pipeline_status = pipeline_status
        self._broadcast = broadcast
        self._use_broker_services = use_broker_services
        self._live_feed_provider = live_feed_provider
        self._data_source = data_source
        self._feed_allowed = feed_allowed
        self._fetch_all_eod = fetch_all_eod
        self._record_today_flow = record_today_flow
        self._eod_task_done = eod_task_done
        self._flow_task_done = flow_task_done
        self._now = now

    async def publish_pipeline_status(
        self, status: str, reason: str = "", elapsed: float | None = None
    ) -> None:
        """Broadcast analytics availability only when visible state changes."""
        previous = (
            self._pipeline_status.get("status"),
            self._pipeline_status.get("reason"),
        )
        self._pipeline_status["status"] = status
        self._pipeline_status["reason"] = reason
        self._pipeline_status["elapsedSeconds"] = (
            round(elapsed, 3) if elapsed is not None else None
        )
        if status == "LIVE":
            self._pipeline_status["lastSuccessAt"] = self._now().isoformat()
        if (status, reason) != previous:
            await self._broadcast(
                {"type": "pipelineStatus", "payload": dict(self._pipeline_status)}
            )

    def delayed_reason(self, timeout_seconds: float) -> str:
        if self._use_broker_services():
            return (
                f"REST analytics pass exceeded {timeout_seconds:g}s; "
                "live prices continue via WebSocket"
            )
        return (
            f"Public REST analytics pass exceeded {timeout_seconds:g}s; "
            "SmartAPI remains disabled"
        )

    def delayed_overlay(self) -> str:
        provider = self._live_feed_provider()
        if self._use_broker_services() and self._feed_allowed(provider):
            return f"{provider} websocket overlay remains active"
        return f"{self._data_source()} REST polling will retry"

    def schedule_eod_jobs(
        self, now: datetime
    ) -> tuple[asyncio.Task[Any], asyncio.Task[Any]]:
        eod_task = asyncio.create_task(
            asyncio.to_thread(self._fetch_all_eod, now, True)
        )
        eod_task.add_done_callback(self._eod_task_done)
        flow_task = asyncio.create_task(asyncio.to_thread(self._record_today_flow))
        flow_task.add_done_callback(self._flow_task_done)
        return eod_task, flow_task
