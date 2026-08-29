"""Concurrency, rate-limit, and idempotency controls for live orders."""
from __future__ import annotations

import asyncio
import threading
import time


class LiveOrderControls:
    def __init__(self, *, place_order, order_store, results_max=500):
        self._place_order = place_order
        self._store = order_store
        self._results_max = results_max
        self._timestamps: list[float] = []
        self._submit_lock = threading.Lock()
        self._results: dict = {}
        self._gate: asyncio.Lock | None = None
        self._gate_loop = None

    def rate_limit_allows(self, maximum: int) -> bool:
        """Consume one slot from a sliding 60-second submission window."""
        now = time.monotonic()
        cutoff = now - 60
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.pop(0)
        if len(self._timestamps) >= maximum:
            return False
        self._timestamps.append(now)
        return True

    def completed_order(self, client_order_id):
        with self._submit_lock:
            cached = self._results.get(client_order_id)
            if cached is not None:
                return cached
            persisted = self._store.get(client_order_id)
            if persisted is not None:
                self._results[client_order_id] = persisted
            return persisted

    def submit_idempotent(self, client_order_id, *args, **kwargs):
        """Serialize broker submissions and collapse retries by client ID."""
        with self._submit_lock:
            existing = self._results.get(client_order_id)
            if existing is not None:
                return existing, True
            order_id = self._place_order(*args, **kwargs, order_tag=client_order_id)
            order_id = self._store.record(client_order_id, order_id)
            self._results[client_order_id] = order_id
            while len(self._results) > self._results_max:
                self._results.pop(next(iter(self._results)))
            return order_id, False

    def order_gate(self) -> asyncio.Lock:
        """Return one critical-section lock per active event loop."""
        loop = asyncio.get_running_loop()
        if self._gate is None or self._gate_loop is not loop:
            self._gate = asyncio.Lock()
            self._gate_loop = loop
        return self._gate
