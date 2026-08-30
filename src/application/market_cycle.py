"""Per-tick market-cycle publication, pacing, and synchronization services."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any


class CanonicalPayloadPublisher:
    """Atomically store and publish full or delta market snapshots."""

    def __init__(
        self,
        *,
        stream_lock,
        use_delta: Callable[[], bool],
        previous_payload: Callable[[], Any],
        store_payload: Callable[[Any, datetime], Any],
        store_previous_payload: Callable[[Any], Any],
        broadcast: Callable[[dict], Awaitable[Any]],
        compute_diff: Callable[[Any, Any], Any],
    ):
        self._stream_lock = stream_lock
        self._use_delta = use_delta
        self._previous_payload = previous_payload
        self._store_payload = store_payload
        self._store_previous_payload = store_previous_payload
        self._broadcast = broadcast
        self._compute_diff = compute_diff

    async def publish(self, payload) -> None:
        async with self._stream_lock:
            self._store_payload(payload, datetime.now().astimezone())
            previous = self._previous_payload()
            if not self._use_delta() or previous is None:
                await self._broadcast({"type": "full", "payload": payload})
            else:
                started_at = time.monotonic()
                diff = await asyncio.to_thread(self._compute_diff, previous, payload)
                elapsed = time.monotonic() - started_at
                if elapsed > 0.25:
                    print(
                        f"[ws] WARNING: compute_diff took {elapsed:.2f}s",
                        flush=True,
                    )
                if diff is not None:
                    await self._broadcast({"type": "delta", "payload": diff})
                else:
                    print("[ws] tick unchanged, skipping broadcast", flush=True)
            self._store_previous_payload(payload)


class MarketTickPacer:
    """Coordinate polling ceilings with event-driven recompute wakeups."""

    def __init__(
        self,
        *,
        poll_seconds: float,
        minimum_recompute_seconds: float,
        symbol_switch_event: asyncio.Event,
        tick_activity_event: asyncio.Event,
    ):
        self._poll_seconds = poll_seconds
        self._minimum_recompute_seconds = minimum_recompute_seconds
        self._symbol_switch_event = symbol_switch_event
        self._tick_activity_event = tick_activity_event

    async def wait(self, tick_started_at: float, pipeline_elapsed: float) -> None:
        remaining = self._poll_seconds - (time.monotonic() - tick_started_at)
        if remaining <= 0:
            if pipeline_elapsed > self._poll_seconds:
                print(
                    f"[ws] WARNING: pipeline took {pipeline_elapsed:.2f}s, "
                    f"longer than poll interval {self._poll_seconds}s",
                    flush=True,
                )
            return
        floor_remaining = self._minimum_recompute_seconds - (
            time.monotonic() - tick_started_at
        )
        if floor_remaining > 0:
            await asyncio.sleep(min(floor_remaining, remaining))
            remaining = self._poll_seconds - (time.monotonic() - tick_started_at)
        if remaining <= 0:
            return

        switch_waiter = asyncio.create_task(self._symbol_switch_event.wait())
        tick_waiter = asyncio.create_task(self._tick_activity_event.wait())
        try:
            done, pending = await asyncio.wait(
                {switch_waiter, tick_waiter},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if switch_waiter in done:
                self._symbol_switch_event.clear()
                print("[ws] symbol switch — ticking early", flush=True)
            elif tick_waiter in done:
                self._tick_activity_event.clear()
                print(
                    f"[ws] tick activity — ticking early "
                    f"(floor={self._minimum_recompute_seconds}s)",
                    flush=True,
                )
        except Exception as exc:
            switch_waiter.cancel()
            tick_waiter.cancel()
            await asyncio.gather(
                switch_waiter, tick_waiter, return_exceptions=True
            )
            print(
                f"[ws] WARNING: wake-wait failed, falling back to plain sleep: {exc}",
                flush=True,
            )
            await asyncio.sleep(remaining)


class MarketEngineCycle:
    """Orchestrate one canonical market-processing cycle."""

    def __init__(
        self,
        *,
        reset_daily_sessions,
        trigger_eod,
        collect_pipeline,
        observe_pipeline,
        market_session_status,
        schedule_auto_execution,
        seed_oi_baselines,
        publish_payload,
        schedule_node_relay,
        connected_count,
        build_current_prices,
        check_pending_orders,
        broadcast_portfolio,
        pace,
    ):
        self._reset_daily_sessions = reset_daily_sessions
        self._trigger_eod = trigger_eod
        self._collect_pipeline = collect_pipeline
        self._observe_pipeline = observe_pipeline
        self._market_session_status = market_session_status
        self._schedule_auto_execution = schedule_auto_execution
        self._seed_oi_baselines = seed_oi_baselines
        self._publish_payload = publish_payload
        self._schedule_node_relay = schedule_node_relay
        self._connected_count = connected_count
        self._build_current_prices = build_current_prices
        self._check_pending_orders = check_pending_orders
        self._broadcast_portfolio = broadcast_portfolio
        self._pace = pace

    async def run_once(self) -> None:
        tick_started_at = time.monotonic()
        now = datetime.now()
        self._reset_daily_sessions(now)
        self._trigger_eod(now)
        payload = await self._collect_pipeline(tick_started_at)
        pipeline_elapsed = time.monotonic() - tick_started_at
        self._observe_pipeline(payload is not None, pipeline_elapsed)

        if payload is not None:
            payload["marketSession"] = self._market_session_status(now)
            decision = payload.get("decision")
            if decision:
                self._schedule_auto_execution(decision)
            self._seed_oi_baselines(payload)
            await self._publish_payload(payload)
            self._schedule_node_relay(payload)
            timings = payload.get("pipelineTimings")
            if timings:
                breakdown = " ".join(
                    f"{key}={value:.2f}"
                    for key, value in timings.items()
                    if isinstance(value, (int, float))
                )
                print(
                    f"[ws] broadcast tick -> {self._connected_count()} client(s) "
                    f"(pipeline {pipeline_elapsed:.2f}s) | {breakdown}",
                    flush=True,
                )
            else:
                print(
                    f"[ws] broadcast tick -> {self._connected_count()} client(s) "
                    f"(pipeline {pipeline_elapsed:.2f}s)",
                    flush=True,
                )
            current_prices = self._build_current_prices(payload)
            self._check_pending_orders(current_prices)
            await self._broadcast_portfolio(current_prices)
        await self._pace(tick_started_at, pipeline_elapsed)

    async def run_forever(self) -> None:
        from application.institutional_analytics_cache import warm as warm_institutional
        from application.dashboard_market_metadata import (
            get_fno_symbols as warm_fno_symbols,
            data_sources_payload as warm_data_sources,
        )

        warm_institutional()
        warm_fno_symbols()
        warm_data_sources()
        while True:
            await self.run_once()


class OiBaselineSynchronizer:
    """Seed live-feed token baselines from canonical exchange OI changes."""

    def __init__(self, *, aggregators: Callable[[], dict]):
        self._aggregators = aggregators

    def synchronize(self, payload: dict) -> None:
        rows_by_strike = {
            row["strike"]: row for row in payload.get("chain", [])
        }
        for aggregator in self._aggregators().values():
            baselines = {}
            for token, metadata in aggregator.token_meta.items():
                option_type = metadata.get("option_type")
                if option_type not in {"CE", "PE"}:
                    continue
                row = rows_by_strike.get(metadata.get("strike"))
                if not row:
                    continue
                oi_field = "ceOI" if option_type == "CE" else "peOI"
                change_field = "ceChgOI" if option_type == "CE" else "peChgOI"
                if oi_field in row and change_field in row and row[oi_field] != 0:
                    baselines[token] = row[oi_field] - row[change_field]
            if baselines:
                aggregator.seed_session_baseline(baselines)
