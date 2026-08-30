"""Stale-while-revalidate cache for the shared index market snapshot."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from storage.caches import TTLSlot


class IndexSnapshotCache:
    """Return cached indices immediately while refreshing stale data once."""

    def __init__(
        self,
        *,
        fetch: Callable[[], Any],
        ttl_seconds: float,
        logger: logging.Logger,
        start_background: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._fetch = fetch
        self._slot = TTLSlot(ttl_seconds=ttl_seconds, clock="epoch")
        self._logger = logger
        self._lock = threading.Lock()
        self._refreshing = False
        self._start_background = start_background or self._start_thread

    @staticmethod
    def _start_thread(target: Callable[[], None]) -> None:
        threading.Thread(
            target=target,
            daemon=True,
            name="index_snapshot_refresh",
        ).start()

    def get(self):
        if self._slot.value is None:
            self._slot.set(self._fetch())
            return self._slot.value

        if not self._slot.is_fresh():
            with self._lock:
                should_refresh = not self._refreshing
                if should_refresh:
                    self._refreshing = True
            if should_refresh:
                self._start_background(self._refresh)

        return self._slot.value

    def _refresh(self) -> None:
        try:
            self._slot.set(self._fetch())
        except Exception as exc:
            self._logger.error("index snapshot refresh failed: %s", exc)
        finally:
            with self._lock:
                self._refreshing = False
