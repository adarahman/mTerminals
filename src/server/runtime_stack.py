"""Compose live trading, market cycles, dashboard transport, and HTTP runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from analytics.nse_fii_dii_flow_fetch import record_today_flow
from application import selection_state
from application.dashboard_market_metadata import get_fno_symbols
from execution.paper_trading import LOT_SIZES, _instrument_key
from nse_eod_fetch import fetch_all_eod, is_trading_day
from oi.futures_oi_tracker import get_tracker
from server.application_assembly import ServerApplication, build_server_application
from server.dashboard_transport import DashboardTransport, build_dashboard_transport
from server.feeds.orchestration import configure_feed_orchestration
from server.live_trading_runtime import LiveTradingRuntime, build_live_trading_runtime
from server.market_runtime_assembly import MarketRuntime, build_market_runtime
from server.startup_configuration import (
    BSE_SYMBOLS,
    EXECUTION_BROKER_LABELS,
    INDEX_TICKER_SYMBOLS,
)
from server.task_callbacks import eod_task_done, flow_task_done
from server.websocket_payload import compute_diff
from server.websocket_security import host_is_loopback


@dataclass(frozen=True, slots=True)
class RuntimeStack:
    live_trading: LiveTradingRuntime
    market: MarketRuntime
    dashboard: DashboardTransport
    application: ServerApplication


def build_runtime_stack(
    *,
    runtime_state: Any,
    core_runtime: Any,
    live_trading_config: Any,
    paper_engine: Any,
    paper_price_book: Any,
    eod_trigger_time: Any,
    position_reconcile_seconds: int,
    host: str,
    http_port: int,
    middleware: Any,
    origin_allowed: Callable[..., bool],
    encode: Callable[[Any], str],
    decode: Callable[[str], Any],
    broker_services: Any,
    broker_settings: Any,
    feed_manager: Any,
    logger: Any,
    report: Callable[..., Any],
    run_backtest_call: Callable[..., Any],
) -> RuntimeStack:
    live = build_live_trading_runtime(
        config=live_trading_config,
        bse_symbols=BSE_SYMBOLS,
        resolve_option_contract=broker_services.resolve_option_contract,
        find_option_token=broker_services.market_data.find_option_token,
        place_order=broker_services.place_order,
        get_positions=broker_services.get_positions,
        get_order_book=broker_services.get_order_book,
        lot_sizes=LOT_SIZES,
        paper_engine=paper_engine,
        price_book=paper_price_book,
        portfolio_broadcast=core_runtime.paper_portfolio.broadcast,
        last_payload=lambda: runtime_state.LAST_PAYLOAD,
        instrument_key=_instrument_key,
        cached_positions=lambda: runtime_state.LAST_LIVE_POSITIONS,
        symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        broker_label=lambda: (
            "Public Data"
            if not broker_services.BROKER_SERVICES_ENABLED
            else EXECUTION_BROKER_LABELS.get(
                broker_settings.execution_broker, "Angel One"
            )
        ),
        store_alert=lambda payload: setattr(
            runtime_state, "LAST_RECONCILIATION_ALERT", payload
        ),
        broadcast=core_runtime.broadcast,
        report=report,
    )
    market = build_market_runtime(
        runtime_state=runtime_state,
        market_data=broker_services.market_data,
        get_funds=broker_services.get_funds,
        get_order_book=broker_services.get_order_book,
        get_positions=broker_services.get_positions,
        position_reconciler=live.position_reconciler,
        position_reconcile_seconds=position_reconcile_seconds,
        trading_supervisor=live.supervisor,
        auto_executor=live.auto_executor,
        lot_sizes=LOT_SIZES,
        index_symbols=INDEX_TICKER_SYMBOLS,
        broadcast=core_runtime.broadcast,
        report=report,
        spawn_task=feed_manager._create_background_task,
        active_feed_managers=lambda: runtime_state.FEEDS,
        feed_allowed=feed_manager._feed_allowed,
        fetch_all_eod=fetch_all_eod,
        record_today_flow=record_today_flow,
        eod_task_done=eod_task_done,
        flow_task_done=flow_task_done,
        reset_futures_session=lambda: get_tracker().reset_session(),
        is_trading_day=is_trading_day,
        eod_trigger_time=eod_trigger_time,
        run_pipeline=core_runtime.analytics.run,
        compute_diff=compute_diff,
        market_session_status=selection_state._market_session_status,
        paper_price_book=paper_price_book,
        paper_engine=paper_engine,
        paper_portfolio=core_runtime.paper_portfolio,
    )
    dashboard = build_dashboard_transport(
        runtime_state=runtime_state,
        encode=encode,
        decode=decode,
        origin_allowed=origin_allowed,
        place_order=live.submission.handle,
        cancel_order=paper_engine.cancel_order,
        portfolio_broadcast=core_runtime.paper_portfolio.broadcast,
        build_current_prices=paper_price_book.build,
        start_funds_polling=market.funds.start,
        stop_funds_polling=market.funds.stop,
        switch_symbol=core_runtime.symbol_switcher.switch,
        switch_data_source=core_runtime.data_source_switcher.switch,
        build_algo_status=live.supervisor.build_status,
        paper_snapshot=core_runtime.paper_portfolio.handshake_snapshot,
        logger=logger,
    )
    runtime_state.WS_HANDSHAKE = dashboard.handshake
    runtime_state.WS_MESSAGE_ROUTER = dashboard.message_router
    runtime_state.WS_QUERY_CONTROLLER = dashboard.query_controller
    runtime_state.DASHBOARD_WS_HANDLER = dashboard.handler
    application = build_server_application(
        runtime_state=runtime_state,
        feed_manager=feed_manager,
        host=host,
        http_port=http_port,
        middleware=middleware,
        dashboard_websocket=dashboard.handler,
        bridge=core_runtime.bridge,
        broker_services_enabled=broker_services.BROKER_SERVICES_ENABLED,
        index_tokens=broker_services.SMARTAPI_INDEX_TOKENS,
        get_candle_data=broker_services.get_candle_data,
        get_index_candles=broker_services.get_index_candles,
        run_backtest_call=run_backtest_call,
        feed_allowed=feed_manager._feed_allowed,
        market_session_status=selection_state._market_session_status,
        host_is_loopback=host_is_loopback,
        index_quotes=market.index_quotes.run,
        algo_status=market.algo_status.run,
        reconcile=market.reconciliation.run,
        live_trading_enabled=live_trading_config.enabled,
        get_fno_symbols=get_fno_symbols,
    )
    configure_feed_orchestration(
        broadcast=core_runtime.broadcast,
        portfolio_broadcaster=core_runtime.paper_portfolio.broadcast_from_feed,
    )
    return RuntimeStack(live, market, dashboard, application)
