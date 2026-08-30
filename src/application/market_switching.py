"""Application services for provider and market-symbol switching."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import unquote


class DataSourceSwitcher:
    """Atomically coordinate a runtime market-data provider switch."""

    def __init__(
        self,
        *,
        valid_sources: Callable[[], Any],
        current_source: Callable[[], str],
        execution_gate: Any,
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
            print(f"[data-source] switched to {new_source}", flush=True)
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
