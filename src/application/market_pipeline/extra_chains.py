"""Cached concurrent construction of secondary-expiry analytics bundles."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError, as_completed
from typing import Any

from application.pipeline_config import RuntimeConfig
from application.market_pipeline.resources import RetirableExecutorPool


class ExtraChainService:
    """Build NEAR and MONTHLY bundles concurrently and reuse fresh results."""

    def __init__(
        self,
        *,
        build_bundle: Callable[..., tuple],
        exchange_for_symbol: Callable[[str], str],
        executor_pool: RetirableExecutorPool,
        logger,
        cache_ttl_seconds: float = 45.0,
        operation_timeout_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._build_bundle = build_bundle
        self._exchange_for_symbol = exchange_for_symbol
        self._executor_pool = executor_pool
        self._logger = logger
        self._cache_ttl_seconds = cache_ttl_seconds
        self._operation_timeout_seconds = operation_timeout_seconds
        self._clock = clock
        self._cache: dict[tuple, tuple[float, Any]] = {}

    def build(self, em, runtime_config: RuntimeConfig, broker_adapters=None, timings=None):
        extra_chains = {}
        if em is None or runtime_config.no_extra_chains:
            return extra_chains
        symbol = runtime_config.symbol
        now = self._clock()
        self._cache = {
            key: value for key, value in self._cache.items() if key[0] == symbol
        }
        try:
            slots = [
                (name, slot)
                for name, slot in (
                    ("NEAR", em.context.near),
                    ("MONTHLY", em.context.monthly),
                )
                if slot and slot.date_str != str(runtime_config.expiry)
            ]
            pending = []
            for slot_name, slot in slots:
                key = (symbol, slot_name, slot.date_str)
                cached = self._cache.get(key)
                if cached and (now - cached[0]) < self._cache_ttl_seconds:
                    extra_chains[slot.date_str] = cached[1]
                    if timings is not None:
                        timings["extra" + slot_name] = 0.0
                else:
                    pending.append((slot_name, slot, key))

            futures = {}
            for slot_name, slot, key in pending:
                submitted_at = self._clock()
                future = self._executor_pool.get().submit(
                    self._build_bundle,
                    symbol,
                    slot.date_str,
                    self._exchange_for_symbol(symbol),
                    runtime_config=runtime_config,
                    broker_adapters=broker_adapters,
                )
                futures[future] = (slot_name, slot, key, submitted_at)

            try:
                for future in as_completed(
                    futures, timeout=self._operation_timeout_seconds
                ):
                    slot_name, slot, key, submitted_at = futures[future]
                    try:
                        frame, master, context, dte, _resolved = future.result()
                        value = (frame, master, context, dte)
                        extra_chains[slot.date_str] = value
                        self._cache[key] = (now, value)
                    except Exception as exc:
                        self._logger.warning(
                            "[%s] Skip extra bundle (%s)", slot_name, exc
                        )
                    if timings is not None:
                        timings["extra" + slot_name] = round(
                            self._clock() - submitted_at, 4
                        )
            except FutureTimeoutError:
                for future, (slot_name, _slot, _key, submitted_at) in futures.items():
                    if not future.done():
                        future.cancel()
                        self._logger.warning(
                            "[%s] Skip extra bundle (operation timed out)",
                            slot_name,
                        )
                        if timings is not None:
                            timings["extra" + slot_name] = round(
                                self._clock() - submitted_at, 4
                            )
                self._executor_pool.retire()
        except Exception as exc:
            self._logger.warning("[ExtraChains] Skip (%s)", exc)
        return extra_chains
