"""Runtime-backed health snapshot assembly for the live server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class RuntimeHealthSnapshot:
    """Translate process runtime state into the generic health contract."""

    def __init__(
        self,
        *,
        runtime_state,
        feed_allowed: Callable[[str], bool],
        market_session_status: Callable[[Any], str],
        build_snapshot: Callable[..., dict],
    ) -> None:
        self._state = runtime_state
        self._feed_allowed = feed_allowed
        self._market_session_status = market_session_status
        self._build_snapshot = build_snapshot

    def _feed_connections(self) -> tuple[bool, bool, bool]:
        smartapi = upstox = shoonya = False
        if self._state.USE_SMARTAPI:
            provider = self._state.LIVE_FEED_PROVIDER
            if provider == "UPSTOX":
                upstox = self._state.FEEDS["UPSTOX"].connected
            elif provider == "SHOONYA":
                shoonya = self._state.FEEDS["SHOONYA"].connected
            else:
                smartapi = self._state.FEEDS["SMARTAPI"].connected
        return smartapi, upstox, shoonya

    def build(self, now=None) -> dict:
        state = self._state
        selection = state.MARKET_SELECTION
        smartapi, upstox, shoonya = self._feed_connections()
        return self._build_snapshot(
            {
                "process_started_at": state.PROCESS_STARTED_AT,
                "poll_seconds": state.POLL_SECONDS,
                "last_payload": state.LAST_PAYLOAD,
                "last_payload_at": state.LAST_PAYLOAD_AT,
                "connected_clients": len(state.CONNECTED),
                "symbol": selection.symbol,
                "expiry": selection.expiry,
                "broker_services_enabled": state.USE_SMARTAPI,
                "data_source": selection.data_source,
                "live_feed_provider": state.LIVE_FEED_PROVIDER,
                "live_feed_active": state.USE_SMARTAPI
                and self._feed_allowed(selection.data_source),
                "pipeline_status": state.PIPELINE_STATUS,
                "smartapi_connected": smartapi,
                "upstox_connected": upstox,
                "shoonya_connected": shoonya,
            },
            self._market_session_status,
            now,
        )
