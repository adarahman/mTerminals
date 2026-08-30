"""Owned process resources used by the market-data pipeline."""

from __future__ import annotations

import atexit
import copy
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd


class ChainSnapshotStore:
    """Keep copied recent chain snapshots for timeout fallback."""

    def __init__(self, *, max_age_seconds: float, logger) -> None:
        self._max_age_seconds = max_age_seconds
        self._logger = logger
        self._cache: dict[Any, tuple[float, Any]] = {}

    def remember(self, key, value, *, now=None) -> None:
        frame = value[0] if isinstance(value, tuple) and value else None
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            self._cache[key] = (
                time.monotonic() if now is None else now,
                copy.deepcopy(value),
            )

    def load(self, key, *, source, timings=None, now=None):
        cached = self._cache.get(key)
        if cached is None:
            return None
        cached_at, value = cached
        current = time.monotonic() if now is None else now
        age = current - cached_at
        if age > self._max_age_seconds:
            return None
        reason = (
            f"Option chain refresh timed out; using {age:.1f}s-old "
            f"{source} snapshot"
        )
        if timings is not None:
            timings["chainStale"] = 1
            timings["chainStaleAgeSeconds"] = round(age, 3)
            timings["chainStaleReason"] = reason
        self._logger.warning("[chain:stale-fallback] %s", reason)
        return copy.deepcopy(value)


class RetirableExecutorPool:
    """Lazily own one executor and replace it after blocked operations."""

    def __init__(
        self,
        *,
        max_workers: int,
        factory: Callable[..., Any] = ThreadPoolExecutor,
        register_shutdown: Callable[[Callable], Any] = atexit.register,
    ) -> None:
        self._max_workers = max_workers
        self._factory = factory
        self._register_shutdown = register_shutdown
        self._executor = None

    def get(self):
        if self._executor is None:
            self._executor = self._factory(max_workers=self._max_workers)
            self._register_shutdown(self._executor.shutdown)
        return self._executor

    def retire(self) -> None:
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
