"""Broker-neutral lifecycle for one provider's persistent tick feed.

ws_server_live used to carry three parallel copies (SmartAPI/Upstox/Shoonya)
of identical choreography: module-global stream/aggregator/expiry state, an
RLock serializing lifecycle entry points, start-or-switch, switch-or-start,
and a fire-and-forget restart thread. This manager owns that choreography
once; provider differences live entirely in injected callables:

- snapshot()/store(): read/write the provider's legacy state globals (kept
  as ws_server_live module globals because tests seam through them).
- start()/switch()/stop(): call the provider's extracted service functions
  (server.feeds.*) with whatever argument shape each exposes.

Locking: one RLock per manager, reentrant so a switch that finds no running
feed can fall back into start() while holding the lock. This closes the race
where the backgrounded startup call runs concurrently with a switch's
fallback start call — single-session brokers (e.g. AngelOne, one live WS per
login) would leave the losing socket orphaned, retrying forever with nothing
referencing it.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class BrokerFeedManager:
    def __init__(
        self,
        provider: str,
        *,
        snapshot: Callable[[], object],
        store: Callable[[object], None],
        start: Callable[..., None],
        switch: Callable[..., None],
        stop: Callable[..., None],
        default_symbol: Callable[[], str],
        main_loop: Callable[[], object],
        log: Callable[[str], None],
    ) -> None:
        self.provider = provider
        self._tag = provider.lower()
        self._snapshot = snapshot
        self._store = store
        self._start = start
        self._switch_fn = switch
        self._stop_fn = stop
        self._default_symbol = default_symbol
        self._main_loop = main_loop
        self._log = log
        self._lock = threading.RLock()

    # ── introspection ────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        state = self._snapshot()
        return (
            getattr(state, "stream", None) is not None
            and getattr(state, "aggregator", None) is not None
        )

    @property
    def connected(self) -> bool:
        stream = getattr(self._snapshot(), "stream", None)
        connected_event = getattr(stream, "_connected", None)
        return bool(connected_event and connected_event.is_set())

    @property
    def current_expiry(self) -> Optional[str]:
        return getattr(self._snapshot(), "current_expiry", None)

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
            state = self._snapshot()
            self._start(state, loop, target, strikes_around_atm, expiry)
            self._store(state)

    def switch_blocking(self, new_symbol: str, strikes_around_atm: int = 10, expiry=None) -> None:
        """Switch subscriptions on the existing socket; start if none.

        Falls back to start() on the captured loop (or the main loop) when
        the feed was never started at boot, rather than silently no-op'ing
        the switch."""
        with self._lock:
            if not self.running:
                loop = getattr(self._snapshot(), "loop", None) or self._main_loop()
                if loop is not None:
                    self.start(loop, new_symbol, strikes_around_atm, expiry)
                return
            self._switch_locked(new_symbol.upper(), strikes_around_atm, expiry)

    def restart(self, new_symbol: str, new_expiry: str = None) -> None:
        """Fire-and-forget switch so synchronous callers (ws_handler) never
        block on network I/O; the RLock makes piled-up switches from rapid
        clicks execute one at a time, in order."""
        threading.Thread(
            target=self.switch_blocking,
            args=(new_symbol, 10, new_expiry),
            daemon=True,
            name=f"{self._tag}-feed-switch",
        ).start()

    def stop_blocking(self, *stop_args, **stop_kwargs) -> None:
        """Best-effort unsubscribe while preserving the state globals."""
        with self._lock:
            state = self._snapshot()
            self._stop_fn(state, *stop_args, **stop_kwargs)
            self._store(state)

    # ── internals ────────────────────────────────────────────────────
    def _switch_locked(self, symbol: str, strikes_around_atm: int, expiry) -> None:
        state = self._snapshot()
        self._switch_fn(state, symbol, strikes_around_atm, expiry)
        self._store(state)