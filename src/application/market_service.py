"""Application-level orchestration for the blocking analytics pipeline."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import unquote

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


class DataSourceSwitcher:
    """Atomically coordinate a runtime market-data provider switch."""

    def __init__(
        self,
        *,
        valid_sources: Callable[[], Any],
        current_source: Callable[[], str],
        execution_gate: SerializedPipelineExecutor,
        activate_provider: Callable[[str], Any],
        stop_feed: Callable[[str], Any],
        commit_source: Callable[[str], Any],
        supports_websocket: Callable[[str], bool],
        restart_feed: Callable[[str, str, Any], Any],
        current_symbol: Callable[[], str],
        current_expiry: Callable[[], Any],
        signal_refresh: Callable[[], Any],
    ):
        self._valid_sources = valid_sources
        self._current_source = current_source
        self._execution_gate = execution_gate
        self._activate_provider = activate_provider
        self._stop_feed = stop_feed
        self._commit_source = commit_source
        self._supports_websocket = supports_websocket
        self._restart_feed = restart_feed
        self._current_symbol = current_symbol
        self._current_expiry = current_expiry
        self._signal_refresh = signal_refresh

    async def switch(self, requested_source: str):
        new_source = (requested_source or "").strip().upper()
        # Run the whole switch under the same exclusive gate the analytics
        # pipeline uses, so a provider switch cannot interleave with an
        # in-flight (even timed-out) analytics pass. Previously the gate was
        # accepted but never used, allowing the switch to mutate the active
        # provider/feed while the previous pipeline was still consuming broker
        # capacity and shared caches.
        async with self._execution_gate.exclusive_scope():
            valid_sources = set(self._valid_sources())
            if new_source not in valid_sources:
                print(
                    f"[data-source] rejecting invalid data source {new_source!r} "
                    f"(valid: {sorted(valid_sources)})",
                    flush=True,
                )
                raise ValueError(
                    f"Unknown data source {new_source!r}. "
                    f"Valid: {sorted(valid_sources)}"
                )

            old_source = self._current_source()
            if new_source == old_source:
                return None
            print(
                f"[data-source] switch requested: {old_source} -> {new_source}",
                flush=True,
            )

            try:
                switched = self._activate_provider(new_source)
            except Exception as exc:
                print(
                    f"[data-source] switch to {new_source} failed; "
                    f"remaining on {old_source}: {exc}",
                    flush=True,
                )
                self._signal_refresh()
                return False

            if not switched:
                print(
                    f"[data-source] {new_source} unavailable; "
                    f"remaining on {old_source}",
                    flush=True,
                )
                self._signal_refresh()
                return False

            self._stop_feed(old_source)
            self._commit_source(new_source)

            if self._supports_websocket(new_source):
                self._restart_feed(
                    new_source,
                    self._current_symbol(),
                    self._current_expiry(),
                )

            self._signal_refresh()

            print(
                f"[data-source] switched to {new_source}",
                flush=True,
            )

            return True

class SymbolSwitcher:
    """Coordinate process-wide symbol and option-expiry changes."""

    def __init__(
        self,
        *,
        current_symbol: Callable[[], str],
        current_expiry: Callable[[], Any],
        commit_selection: Callable[[str, Any], Any],
        signal_refresh: Callable[[], Any],
        live_feed_enabled: Callable[[], bool],
        live_feed_provider: Callable[[], str],
        restart_feed: Callable[[str, str, Any], Any],
    ):
        self._current_symbol = current_symbol
        self._current_expiry = current_expiry
        self._commit_selection = commit_selection
        self._signal_refresh = signal_refresh
        self._live_feed_enabled = live_feed_enabled
        self._live_feed_provider = live_feed_provider
        self._restart_feed = restart_feed

    def switch(self, requested_symbol: str, requested_expiry=None):
        new_symbol = unquote(requested_symbol).strip().upper()
        old_symbol = self._current_symbol()
        if new_symbol == old_symbol and (
            requested_expiry is None
            or requested_expiry == self._current_expiry()
        ):
            return None

        print(
            f"[ws] symbol switch requested: {old_symbol} -> {new_symbol}",
            flush=True,
        )
        self._commit_selection(new_symbol, requested_expiry)
        self._signal_refresh()
        if self._live_feed_enabled():
            self._restart_feed(
                self._live_feed_provider(), new_symbol, requested_expiry
            )
        return True


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
                    f"{k}={v:.2f}"
                    for k, v in timings.items()
                    if isinstance(v, (int, float))
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
        from application.institutional_analytics_cache import warm as _warm_institutional_caches
        from application.dashboard_market_metadata import (
            get_fno_symbols as _warm_fno_symbols,
            data_sources_payload as _warm_ds_payload,
        )

        _warm_institutional_caches()
        _warm_fno_symbols()
        _warm_ds_payload()
        while True:
            await self.run_once()


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
                # A parsed missing side appears as zero. Do not freeze that
                # false zero as the session baseline.
                if (
                    oi_field in row
                    and change_field in row
                    and row[oi_field] != 0
                ):
                    baselines[token] = row[oi_field] - row[change_field]
            if baselines:
                aggregator.seed_session_baseline(baselines)


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
