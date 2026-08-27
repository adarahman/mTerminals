"""Broker-neutral lifecycle for one provider's persistent tick feed.

The legacy server carried parallel copies of stream/aggregator/expiry state.
This manager owns one provider's mutable state and lifecycle choreography;
provider differences live entirely in injected start/switch/stop callables.

Locking: one RLock per manager, reentrant so a switch that finds no running
feed can fall back into start() while holding the lock. This closes the race
where the backgrounded startup call runs concurrently with a switch's
fallback start call — single-session brokers (e.g. AngelOne, one live WS per
login) would leave the losing socket orphaned, retrying forever with nothing
referencing it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable, Optional

from brokers.provider_registry import supports_websocket as _provider_supports_websocket
from server import feed_lifecycle, runtime_state


def _feed_allowed(feed_provider: str) -> bool:
    """Whether ticks from the given broker feed may still merge/broadcast.

    False after a runtime DATA SOURCE switch away from feed_provider, or
    when the active source is polling-only (KITE/BREEZE/KOTAK/NSE_BSE — no
    WebSocket feed in this codebase). Every *_sync_and_broadcast() gates on
    this BEFORE touching runtime_state.LAST_PAYLOAD/runtime_state.LAST_SENT, so a feed left running
    after a switch can't contaminate the new provider's baseline."""
    return feed_lifecycle.is_allowed(
        feed_provider,
        runtime_state.MARKET_SELECTION.data_source,
        _provider_supports_websocket,
    )


class BrokerFeedManager:
    def __init__(
        self,
        provider: str,
        *,
        state: object,
        start: Callable[..., None],
        switch: Callable[..., None],
        stop: Callable[..., None],
        default_symbol: Callable[[], str],
        main_loop: Callable[[], object],
        log: Callable[[str], None],
    ) -> None:
        self.provider = provider
        self._tag = provider.lower()
        self._state = state
        self._start = start
        self._switch_fn = switch
        self._stop_fn = stop
        self._default_symbol = default_symbol
        self._main_loop = main_loop
        self._log = log
        self._lock = threading.RLock()

    # ── introspection ────────────────────────────────────────────────
    @property
    def state(self):
        """Provider-owned mutable state for diagnostics and focused tests."""
        return self._state

    @property
    def running(self) -> bool:
        state = self._state
        return (
            getattr(state, "stream", None) is not None
            and getattr(state, "aggregator", None) is not None
        )

    @property
    def connected(self) -> bool:
        stream = getattr(self._state, "stream", None)
        connected_event = getattr(stream, "_connected", None)
        return bool(connected_event and connected_event.is_set())

    @property
    def current_expiry(self) -> Optional[str]:
        return getattr(self._state, "current_expiry", None)

    @property
    def aggregator(self):
        """Current normalized tick aggregator, if this feed has started."""
        return getattr(self._state, "aggregator", None)

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self, loop, underlying: str = None, strikes_around_atm: int = 10, expiry=None) -> None:
        """Start one persistent feed, or switch the running socket."""
        with self._lock:
            target = (underlying or self._default_symbol()).upper()
            if self.running:
                self._log(
                    f"[{self._tag}] Feed already running, switching to "
                    f"{target} instead of starting a new one"
                )
                self._switch_locked(target, strikes_around_atm, expiry)
                return
            self._start(self._state, loop, target, strikes_around_atm, expiry)

    def switch_blocking(self, new_symbol: str, strikes_around_atm: int = 10, expiry=None) -> None:
        """Switch subscriptions on the existing socket; start if none.

        Falls back to start() on the captured loop (or the main loop) when
        the feed was never started at boot, rather than silently no-op'ing
        the switch."""
        with self._lock:
            if not self.running:
                loop = getattr(self._state, "loop", None) or self._main_loop()
                if loop is not None:
                    self.start(loop, new_symbol, strikes_around_atm, expiry)
                return
            self._switch_locked(new_symbol.upper(), strikes_around_atm, expiry)

    def restart(self, new_symbol: str, new_expiry: str = None) -> None:
        """Fire-and-forget switch so synchronous callers (ws_handler) never
        block on network I/O; the RLock makes piled-up switches from rapid
        clicks execute one at a time, in order."""
        threading.Thread(
            target=self._restart_blocking,
            args=(new_symbol, 10, new_expiry),
            daemon=True,
            name=f"{self._tag}-feed-switch",
        ).start()

    def _restart_blocking(
        self, new_symbol: str, strikes_around_atm: int, expiry=None
    ) -> None:
        try:
            self.switch_blocking(new_symbol, strikes_around_atm, expiry)
        except Exception as exc:  # background threads must never leak tracebacks
            self._log(f"[{self._tag}] Feed switch failed: {exc}")
            logging.getLogger(__name__).warning(
                "[%s] feed switch failed", self._tag, exc_info=True
            )

    def stop_blocking(self, *stop_args, **stop_kwargs) -> None:
        """Best-effort unsubscribe while preserving the state globals."""
        with self._lock:
            self._stop_fn(self._state, *stop_args, **stop_kwargs)

    def _switch_locked(
        self,
        symbol: str,
        strikes_around_atm: int,
        expiry,
    ) -> None:
        """Switch subscriptions while the manager lifecycle lock is held."""
        self._switch_fn(self._state, symbol, strikes_around_atm, expiry)

_LOGGER = logging.getLogger("mterminals.server.feed_manager")


def _create_background_task(awaitable, task_name: str) -> asyncio.Task:
    task = asyncio.create_task(awaitable, name=task_name)
    runtime_state.BACKGROUND_TASKS.add(task)
    task.add_done_callback(lambda done: _background_task_done(done, task_name))
    return task


def _background_task_done(task: asyncio.Task, task_name: str):
    """Retain detached tasks and surface unexpected subsystem exits."""
    runtime_state.BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _LOGGER.error(
            "background task failed: %s",
            task_name,
            extra={
                "event": "background_task.failed",
                "subsystem": task_name,
                "status": "failed",
                "reason": str(exc),
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def _restart_live_feed(provider: str, symbol: str, expiry=None) -> bool:
    """Schedule the active provider's existing feed for a symbol switch.
    Socket lifecycle remains provider-native; every orchestration call site
    uses this broker-neutral dispatch rather than duplicating a provider
    branch."""
    return feed_lifecycle.restart(
        provider, symbol, expiry, {k: m.restart for k, m in runtime_state.FEEDS.items()}
    )


def _start_live_feed(provider: str, loop) -> bool:
    """Offload the configured provider's blocking feed startup."""
    return feed_lifecycle.start(
        provider,
        loop,
        {k: m.start for k, m in runtime_state.FEEDS.items()},
        lambda start_callback, start_loop, task_name: _create_background_task(
            asyncio.to_thread(start_callback, start_loop), task_name
        ),
    )


def _stop_active_broker_feed(provider: str) -> bool:
    """Best-effort unsubscribe when deactivating a streaming provider.
    The synchronous feed_manager._feed_allowed gate remains authoritative for stopping
    payloads; this cleanup releases broker subscription bandwidth."""
    return feed_lifecycle.stop(
        provider,
        {k: m.stop_blocking for k, m in runtime_state.FEEDS.items()},
        lambda callback: threading.Thread(target=callback, daemon=True).start(),
    )


def _commit_symbol_selection(new_symbol, new_expiry):
    runtime_state.MARKET_SELECTION.select_symbol(new_symbol, new_expiry)
    runtime_state.LAST_PAYLOAD = None
    runtime_state.LAST_SENT = None


def _commit_data_source(new_source):
    runtime_state.MARKET_SELECTION.select_data_source(new_source)
    runtime_state.LAST_PAYLOAD = None
    runtime_state.LAST_SENT = None
