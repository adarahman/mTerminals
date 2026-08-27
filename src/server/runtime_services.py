"""Server-specific services consumed by the application lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from application.runtime import build_background_jobs


def flush_oi_history() -> None:
    """Persist buffered OI history without loading it during server import."""
    from oi.oi_analysis import flush_history_to_disk

    flush_history_to_disk()


@dataclass(frozen=True)
class ServerRuntimeServices:
    """Own startup callbacks while keeping infrastructure dependencies explicit."""

    host: str
    runtime_state: Any
    feed_manager: Any
    host_is_loopback: Callable[[str], bool]
    index_quotes: Callable
    bridge: Callable
    algo_status: Callable
    reconcile: Callable
    live_trading_enabled: bool
    flush_history: Callable[[], Any]

    def validate_startup(self) -> None:
        if not self.host_is_loopback(self.host):
            raise RuntimeError(
                f"refusing unsafe non-loopback bind {self.host!r}: the WebSocket "
                "control channel has no remote-client authentication; use "
                "--host localhost or a loopback address"
            )

    def set_main_loop(self, loop) -> None:
        self.runtime_state.MAIN_LOOP = loop

    def start_live_services(self, loop) -> None:
        state = self.runtime_state
        if state.USE_SMARTAPI and self.feed_manager._feed_allowed(state.LIVE_FEED_PROVIDER):
            self.feed_manager._start_live_feed(state.LIVE_FEED_PROVIDER, loop)
        elif state.USE_SMARTAPI:
            print(
                f"[feed] websocket overlay not started "
                f"(data source={state.MARKET_SELECTION.data_source}, "
                f"feed provider={state.LIVE_FEED_PROVIDER})",
                flush=True,
            )
        else:
            print(
                "[broker] authenticated services disabled "
                "(BROKER_SERVICES_ENABLED=false) — no broker login, account/order "
                "REST call, or websocket connection; public daily ScripMaster allowed",
                flush=True,
            )

    def background_jobs(self):
        return build_background_jobs(
            index_quotes=self.index_quotes,
            bridge=self.bridge,
            algo_status=self.algo_status,
            reconcile=self.reconcile,
            live_trading_enabled=self.live_trading_enabled,
        )

    def flush_state(self) -> None:
        self.flush_history()
