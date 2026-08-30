"""Assembly boundary for HTTP APIs and the server application lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from application.lifecycle import ApplicationLifecycle
from server.backtest_api import handle_backtest
from server.health_api import (
    broker_health,
    build_health_snapshot,
    health_handler,
    log_health_transition,
    metrics_handler,
)
from server.health_runtime import RuntimeHealthSnapshot
from server.http_runtime import HttpRuntime, build_http_runtime
from server.market_history_api import MarketHistoryApi
from server.routes import HttpRouteHandlers
from server.runtime_services import ServerRuntimeServices, flush_oi_history


@dataclass(frozen=True, slots=True)
class ServerApplication:
    runtime_state: Any
    feed_manager: Any
    health_snapshot: RuntimeHealthSnapshot
    history_api: MarketHistoryApi
    http: HttpRuntime
    services: ServerRuntimeServices

    async def run(self) -> None:
        from infrastructure.logging import configure_logging

        lifecycle = ApplicationLifecycle(
            validate_startup=self.services.validate_startup,
            configure_logging=configure_logging,
            start_http_server=self.http.start,
            set_main_loop=self.services.set_main_loop,
            start_live_services=self.services.start_live_services,
            background_jobs=self.services.background_jobs,
            create_background_task=self.feed_manager._create_background_task,
            run_engine=self.runtime_state.MARKET_ENGINE_CYCLE.run_forever,
            background_tasks=lambda: self.runtime_state.BACKGROUND_TASKS,
            close_relay=lambda: self.runtime_state.NODE_RELAY.close(),
            flush_state=self.services.flush_state,
        )
        await lifecycle.run()


def build_server_application(
    *,
    runtime_state: Any,
    feed_manager: Any,
    host: str,
    http_port: int,
    middleware: Any,
    dashboard_websocket: Callable[..., Any],
    bridge: Any,
    broker_services_enabled: bool,
    index_tokens: dict,
    get_candle_data: Callable[..., Any],
    get_index_candles: Callable[..., Any],
    run_backtest_call: Callable[..., Any],
    feed_allowed: Callable[[str], bool],
    market_session_status: Callable[..., Any],
    host_is_loopback: Callable[[str], bool],
    index_quotes: Callable[..., Any],
    algo_status: Callable[..., Any],
    reconcile: Callable[..., Any],
    live_trading_enabled: bool,
) -> ServerApplication:
    history_api = MarketHistoryApi(
        state=lambda: {
            "symbol": runtime_state.MARKET_SELECTION.symbol,
            "broker_services_enabled": broker_services_enabled,
            "index_tokens": index_tokens,
        },
        get_candle_data=lambda *args, **kwargs: get_candle_data(*args, **kwargs),
        get_index_candles=lambda *args, **kwargs: get_index_candles(*args, **kwargs),
    )
    health_snapshot = RuntimeHealthSnapshot(
        runtime_state=runtime_state,
        feed_allowed=feed_allowed,
        market_session_status=market_session_status,
        build_snapshot=build_health_snapshot,
    )
    handlers = HttpRouteHandlers(
        history_api=history_api,
        backtest_response=handle_backtest,
        default_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        run_backtest=run_backtest_call,
        health_response=health_handler,
        health_snapshot=health_snapshot.build,
        record_health_transition=log_health_transition,
        metrics_response=metrics_handler,
        metrics=runtime_state.METRICS,
    )
    runtime_state.HTTP_ROUTE_HANDLERS = handlers
    http = build_http_runtime(
        health=handlers.health,
        broker_health=broker_health,
        metrics=handlers.metrics,
        websocket=dashboard_websocket,
        bridge_websocket=bridge.handle,
        spot_history=handlers.spot_history,
        history=handlers.history,
        backtest=handlers.backtest,
        lot_sizes=handlers.lot_sizes,
        host=host,
        port=http_port,
        symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        middleware=middleware,
    )
    services = ServerRuntimeServices(
        host=host,
        runtime_state=runtime_state,
        feed_manager=feed_manager,
        host_is_loopback=host_is_loopback,
        index_quotes=index_quotes,
        bridge=bridge.run,
        algo_status=algo_status,
        reconcile=reconcile,
        live_trading_enabled=live_trading_enabled,
        flush_history=flush_oi_history,
    )
    return ServerApplication(
        runtime_state=runtime_state,
        feed_manager=feed_manager,
        health_snapshot=health_snapshot,
        history_api=history_api,
        http=http,
        services=services,
    )
