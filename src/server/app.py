"""mTerminals live server — composition root.

Wires the extracted subsystems (server.feeds.*, server.feed_manager,
server.order_gateway, server.broker_services, ...) into the running process:
the dashboard WebSocket, the analytics pipeline loop, background pollers,
and HTTP handlers. This file owns the process-wide runtime state
(market selection state, the canonical payload snapshots) and the
orchestration between subsystems; the mechanics live in server/*.

The canonical option-chain runtime is import-safe and receives explicit
configuration from the application layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from functools import partial
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
# This composition root now lives under src/ (the old backend/ layout was
# migrated away). Inserting src/ keeps direct invocations working the same way
# `PYTHONPATH=src python3 -m main` does.
sys.path.insert(0, str(SCRIPT_DIR.parent))

import aiohttp
import orjson
from aiohttp import web
from server import runtime_state

from infrastructure.config import settings as _broker_settings  # noqa: E402
from server import broker_services  # noqa: E402  (imports config + brokers.*)
from server.health import log_transition as _log_server_health_transition  # noqa: E402
from server import feed_lifecycle as _feed_lifecycle  # noqa: E402
from server import feed_manager  # noqa: E402
from server.feed_expiry import matches_displayed_expiry as _matches_displayed_expiry  # noqa: E402
from server.backtest_api import handle_backtest  # noqa: E402
from server.bridge import DashboardBridge  # noqa: E402
from server.market_history_api import (  # noqa: E402
    MarketHistoryApi,
    no_cache_middleware as history_no_cache_middleware,
)
from application.runtime import (  # noqa: E402
    ApplicationLifecycle,
)
from application.market_service import (  # noqa: E402
    CanonicalPayloadPublisher,
    DailyMarketScheduler,
    DataSourceSwitcher,
    LiveFeedAggregatorRegistry,
    MarketEngineCycle,
    MarketPipelineService,
    MarketTickPacer,
    OiBaselineSynchronizer,
    SymbolSwitcher,
)
from application.market_pipeline.futures import fetch_futures_wide  # noqa: E402
from server.routes import HttpRouteHandlers  # noqa: E402
from server.http_runtime import build_http_runtime  # noqa: E402
from server.health_api import (
    build_health_snapshot as _build_health_response,
    health_handler as _health_response,
    metrics_handler as _metrics_response,
    broker_health as _broker_health,
    log_health_transition as _log_health_transition,
)

from server.websocket_security import (  # noqa: E402
    build_allowed_origins,
    host_is_loopback,
    origin_allowed,
)
from server.feeds.orchestration import (  # noqa: E402
    _smartapi_sync_and_broadcast,
    build_feed_managers,
    configure_feed_orchestration,
)
from market.providers import nse_bse_client as market_api  # noqa: E402
from application import option_chain_runtime  # noqa: E402

from application import selection_state  # noqa: E402
from server.live_feed_state import merge_live_feed_update  # noqa: E402
from server.websocket_payload import compute_diff, json_default as _json_default  # noqa: E402
from server.paper_portfolio import PaperPortfolioService  # noqa: E402
from server.health_runtime import RuntimeHealthSnapshot  # noqa: E402
from server.market_cycle_operations import MarketCycleOperations  # noqa: E402
from server.analytics_runtime import (  # noqa: E402
    AnalyticsRuntime,
    build_broker_market_adapters,
)
from execution.paper_trading import LOT_SIZES as PT_LOT_SIZES  # noqa: E402
from execution.paper_trading import _instrument_key  # noqa: E402
from market.instruments.lot_sizes import configure_lot_size_resolver  # noqa: E402
from brokers.smartapi.instruments import get_lot_size as _smartapi_lot_size  # noqa: E402
from backtest.replay import run_backtest  # noqa: E402
from nse_eod_fetch import fetch_all_eod, is_trading_day  # noqa: E402
from analytics.nse_fii_dii_flow_fetch import record_today_flow  # noqa: E402
from oi.futures_oi_tracker import get_tracker as _get_futures_oi_tracker  # noqa: E402
from brokers.provider_registry import supports_websocket as _provider_supports_websocket  # noqa: E402
from server.cli_args import build_arg_parser  # noqa: E402
from server.background_loops import (  # noqa: E402
    AlgoStatusLoop,
    FundsPoller,
    IndexQuoteLoop,
    NodeRelay,
    ReconciliationLoop,
)
from server.live_trading_runtime import (  # noqa: E402
    LiveTradingConfig,
    build_live_trading_runtime,
)
from server.startup_configuration import (  # noqa: E402
    BSE_SYMBOLS as _BSE_SYMBOLS,
    EXECUTION_BROKER_LABELS as _EXECUTION_BROKER_LABELS,
    INDEX_TICKER_SYMBOLS,
    VIX_TOKEN as _VIX_TOKEN,
    VIX_TRADINGSYMBOL as _VIX_TRADINGSYMBOL,
    configure_startup,
    resolve_default_pipeline_expiry as _resolve_default_pipeline_expiry,
)
from server.dashboard_transport import (  # noqa: E402
    DashboardBroadcaster,
    build_dashboard_transport,
)
from server.runtime_bootstrap import initialize_runtime_state  # noqa: E402
from server.task_callbacks import (  # noqa: E402
    eod_task_done as _eod_task_done,
    flow_task_done as _flow_task_done,
    report_failed_task as _report_failed_task,
)
from server.runtime_services import ServerRuntimeServices, flush_oi_history  # noqa: E402
logger = logging.getLogger("mterminals.server")
configure_lot_size_resolver(_smartapi_lot_size)

# Broker SDK surface (see server/broker_services.py). Aliased back to the
# historical underscored names so existing test seams keep working.
BROKER_SERVICES_ENABLED = broker_services.BROKER_SERVICES_ENABLED
market_data = broker_services.market_data
smartapi_place_order = broker_services.smartapi_place_order
smartapi_get_order_book = broker_services.smartapi_get_order_book
smartapi_get_positions = broker_services.smartapi_get_positions
smartapi_get_funds = broker_services.smartapi_get_funds
get_candle_data = broker_services.get_candle_data
get_index_candles = broker_services.get_index_candles
SmartTickStream = broker_services.SmartTickStream
TickAggregator = broker_services.TickAggregator
EXCHANGE_TYPE = broker_services.EXCHANGE_TYPE
_MD_PROVIDER_KEYS = broker_services.MD_PROVIDER_KEYS
_MD_PROVIDER_CAPABILITIES = broker_services.MD_PROVIDER_CAPABILITIES
_md_get_active_provider = broker_services.md_get_active_provider
_md_provider_has_credentials = broker_services.md_provider_has_credentials
_md_set_active_provider = broker_services.md_set_active_provider
_md_provider_status = broker_services.md_provider_status
_execution_resolve_option_contract = broker_services.resolve_option_contract
_SMARTAPI_INDEX_TOKENS = broker_services.SMARTAPI_INDEX_TOKENS

# This module is also imported by tests and tooling. Preserve server CLI
# parsing while leaving unrelated host arguments (for example pytest flags)
# to the embedding process instead of terminating during import.
ARGS, _HOST_PROCESS_ARGS = build_arg_parser().parse_known_args()

_STARTUP_CONFIGURATION = configure_startup(
    args=ARGS,
    runtime_state=runtime_state,
    broker_services_enabled=BROKER_SERVICES_ENABLED,
    live_feed_provider=_broker_settings.live_feed_provider,
    activate_provider=_md_set_active_provider,
    supports_websocket=_provider_supports_websocket,
)
WS_HOST = _STARTUP_CONFIGURATION.host
WS_PORT = _STARTUP_CONFIGURATION.websocket_port
HTTP_PORT = _STARTUP_CONFIGURATION.http_port
print(_STARTUP_CONFIGURATION.feed_summary, flush=True)
print(_STARTUP_CONFIGURATION.portfolio_summary, flush=True)

_RUNTIME_BOOTSTRAP = initialize_runtime_state(
    runtime_state=runtime_state,
    instrument_key=_instrument_key,
)
PT_ENGINE = _RUNTIME_BOOTSTRAP.paper_engine
_PAPER_PRICE_BOOK = _RUNTIME_BOOTSTRAP.paper_price_book
EOD_TRIGGER_TIME = _RUNTIME_BOOTSTRAP.eod_trigger_time

_LIVE_TRADING_CONFIG = LiveTradingConfig.from_environment(PROJECT_ROOT)
LIVE_TRADING_ENABLED = _LIVE_TRADING_CONFIG.enabled
LIVE_TRADING_KILL_SWITCH_FILE = _LIVE_TRADING_CONFIG.kill_switch_file
LIVE_MAX_LOTS_PER_ORDER = _LIVE_TRADING_CONFIG.max_lots_per_order
LIVE_MAX_ORDERS_PER_MINUTE = _LIVE_TRADING_CONFIG.max_orders_per_minute
POSITION_RECONCILE_SECONDS = _LIVE_TRADING_CONFIG.reconcile_seconds
_LIVE_TRADING_CONFIG.report(lambda message: print(message, flush=True))

# ── WebSocket origin allowlist ──────────────────────────────────────────
# Browsers do NOT apply same-origin restrictions to WebSocket handshakes,
# so without this ANY page in the same browser could drive the socket,
# including submitting orders (cross-site WebSocket hijacking). Origin-less
# requests are accepted only from a loopback peer, so a remote client can't
# bypass the browser-origin allowlist by omitting Origin.
ALLOWED_ORIGINS = build_allowed_origins(
    WS_HOST,
    HTTP_PORT,
    os.environ.get("ALLOWED_ORIGINS", "").split(","),
)
_ORIGIN_POLICY = partial(origin_allowed, allowed_origins=ALLOWED_ORIGINS)


# Real-export capture seam: AnalyticsPipelineRunner reads the dashboard payload
# back out of mTerminals_json's own export, so the pipeline and the WS
# stream share one serialization path. The wiring now lives in
# server/payload_capture so this module stays a composition root.
from server.payload_capture import install_payload_export_capture  # noqa: E402

_PAYLOAD_EXPORT_CAPTURE = install_payload_export_capture()


_DASHBOARD_BROADCASTER = DashboardBroadcaster(
    runtime_state=runtime_state,
    encode=lambda message: orjson.dumps(message, default=_json_default).decode(),
    report=lambda message: print(message, flush=True),
)
broadcast = _DASHBOARD_BROADCASTER.broadcast


_PAPER_PORTFOLIO = PaperPortfolioService(
    engine=PT_ENGINE,
    price_book=_PAPER_PRICE_BOOK,
    instrument_key=_instrument_key,
    broadcast=broadcast,
    last_payload=lambda: runtime_state.LAST_PAYLOAD,
)


_BRIDGE = DashboardBridge(
    state=lambda: {
        "symbol": runtime_state.MARKET_SELECTION.symbol,
        "futures_expiry": runtime_state.MARKET_SELECTION.futures_expiry,
        "use_smartapi": runtime_state.USE_SMARTAPI,
        "last_payload": runtime_state.LAST_PAYLOAD,
        "index_quotes": runtime_state.INDEX_QUOTES,
    },
    origin_allowed=_ORIGIN_POLICY,
    json_default=_json_default,
    market_api=market_api,
    broker_futures_fetcher=lambda symbol, which: fetch_futures_wide(
        symbol, which=which
    ),
    public_futures_fetcher=market_api.fetch_public_futures,
)
BRIDGE_CONNECTED = _BRIDGE.clients

_ANALYTICS_RUNTIME = AnalyticsRuntime(
    symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
    data_source=lambda: runtime_state.MARKET_SELECTION.data_source,
    price_source=lambda: runtime_state.MARKET_SELECTION.price_source,
    futures_expiry=lambda: runtime_state.MARKET_SELECTION.futures_expiry,
    strikes_each_side=lambda: runtime_state.STRIKES_EACH_SIDE,
    activate_provider=_md_set_active_provider,
    resolve_default_expiry=_resolve_default_pipeline_expiry,
    apply_config=lambda config: None,
    clear_capture=_PAYLOAD_EXPORT_CAPTURE.clear,
    captured_payload=lambda: _PAYLOAD_EXPORT_CAPTURE.payload,
    export_dashboard=_PAYLOAD_EXPORT_CAPTURE.export,
    invoke_analytics=option_chain_runtime.main,
    broker_adapters=(
        build_broker_market_adapters() if runtime_state.USE_SMARTAPI else None
    ),
    extra_chains=ARGS.extra_chains,
    strict_expiry=ARGS.strict_expiry,
    no_virtual_oi=ARGS.no_virtual_oi,
)
_RUN_PIPELINE_SERIALIZED = _ANALYTICS_RUNTIME.run


# ── Live feed state ──────────────────────────────────────────────────────
# Each BrokerFeedManager owns its provider's mutable state. The asyncio loop
# captured by main() lets a runtime switch start a feed that was not active
# at boot.
_REPORT = partial(print, flush=True)


runtime_state.FEEDS = build_feed_managers(
    default_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    main_loop=lambda: runtime_state.MAIN_LOOP,
    log=_REPORT,
)


# ── feed orchestration dispatch (broker-neutral) ─────────────────────────
_SYMBOL_SWITCHER = SymbolSwitcher(
    current_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    current_expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
    commit_selection=feed_manager._commit_symbol_selection,
    signal_refresh=runtime_state.SYMBOL_SWITCH_EVENT.set,
    live_feed_enabled=lambda: runtime_state.USE_SMARTAPI,
    live_feed_provider=lambda: runtime_state.LIVE_FEED_PROVIDER,
    restart_feed=feed_manager._restart_live_feed,
)


_DATA_SOURCE_SWITCHER = DataSourceSwitcher(
    valid_sources=lambda: _MD_PROVIDER_KEYS,
    current_source=lambda: runtime_state.MARKET_SELECTION.data_source,
    execution_gate=_ANALYTICS_RUNTIME.execution_gate,
    activate_provider=_md_set_active_provider,
    stop_feed=feed_manager._stop_active_broker_feed,
    commit_source=feed_manager._commit_data_source,
    supports_websocket=_provider_supports_websocket,
    restart_feed=feed_manager._restart_live_feed,
    current_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    current_expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
    signal_refresh=runtime_state.SYMBOL_SWITCH_EVENT.set,
)


_LIVE_TRADING_RUNTIME = build_live_trading_runtime(
    config=_LIVE_TRADING_CONFIG,
    bse_symbols=_BSE_SYMBOLS,
    resolve_option_contract=_execution_resolve_option_contract,
    find_option_token=market_data.find_option_token,
    place_order=smartapi_place_order,
    get_positions=smartapi_get_positions,
    get_order_book=smartapi_get_order_book,
    lot_sizes=PT_LOT_SIZES,
    paper_engine=PT_ENGINE,
    price_book=_PAPER_PRICE_BOOK,
    portfolio_broadcast=_PAPER_PORTFOLIO.broadcast,
    last_payload=lambda: runtime_state.LAST_PAYLOAD,
    instrument_key=_instrument_key,
    cached_positions=lambda: runtime_state.LAST_LIVE_POSITIONS,
    symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    broker_label=lambda: (
        "Public Data"
        if not BROKER_SERVICES_ENABLED
        else _EXECUTION_BROKER_LABELS.get(
            _broker_settings.execution_broker, "Angel One"
        )
    ),
    store_alert=lambda payload: setattr(
        runtime_state, "LAST_RECONCILIATION_ALERT", payload
    ),
    broadcast=broadcast,
    report=_REPORT,
)
_ACCOUNT_GUARD = _LIVE_TRADING_RUNTIME.account_guard
_POSITION_RECONCILER = _LIVE_TRADING_RUNTIME.position_reconciler
_LIVE_ORDERS = _LIVE_TRADING_RUNTIME.orders
_ORDER_SUBMISSION = _LIVE_TRADING_RUNTIME.submission
_AUTO_EXECUTOR = _LIVE_TRADING_RUNTIME.auto_executor
_TRADING_SUPERVISOR = _LIVE_TRADING_RUNTIME.supervisor
_resolve_live_order_token = _LIVE_TRADING_RUNTIME.resolve_token


# ── background pollers ───────────────────────────────────────────────────
# Pushes {"type":"indexQuotes",...}; dashboard.js's generic handler lands it
# at wsState.indexQuotes, which paper-trading.js reads once Live mode is on.
# (VIX is never the active SYMBOL, so it's always included in "others".)
_INDEX_QUOTE_LOOP = IndexQuoteLoop(
    enabled=runtime_state.USE_INDEX_QUOTES,
    symbols=INDEX_TICKER_SYMBOLS,
    active_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    get_spot_quote=market_data.get_spot_quote,
    broadcast=broadcast,
    index_quotes=runtime_state.INDEX_QUOTES,
    poll_seconds=runtime_state.INDEX_QUOTE_SECONDS,
    report=_REPORT,
)


# Pushes {"type":"funds",...}; dashboard.js's generic handler lands it at
# wsState.funds, which paper-trading.js reads once Live mode is on.
_FUNDS_POLLER = FundsPoller(
    get_funds=smartapi_get_funds,
    broadcast=broadcast,
    set_last_funds=lambda value: setattr(runtime_state, "LAST_FUNDS", value),
    poll_seconds=runtime_state.FUNDS_POLL_SECONDS,
    spawn_task=feed_manager._create_background_task,
    report=_REPORT,
)


# Gated on LIVE_TRADING_ENABLED at the main()/task-creation call site (with
# live trading off there's nothing real to reconcile), but NOT tied to the
# Live-mode UI toggle — silent drift happens whether or not anyone
# currently has the pill on.
_RECONCILIATION_LOOP = ReconciliationLoop(
    get_order_book=smartapi_get_order_book,
    get_positions=smartapi_get_positions,
    reconciler=_POSITION_RECONCILER,
    lot_sizes=PT_LOT_SIZES,
    set_last_positions=lambda value: setattr(
        runtime_state, "LAST_LIVE_POSITIONS", value
    ),
    broadcast_alert=_TRADING_SUPERVISOR.publish_reconciliation_alert,
    poll_seconds=POSITION_RECONCILE_SECONDS,
    report=_REPORT,
)


_ALGO_STATUS_LOOP = AlgoStatusLoop(
    build_status=_TRADING_SUPERVISOR.build_status,
    broadcast=broadcast,
    set_last_status=lambda value: setattr(runtime_state, "LAST_ALGO_STATUS", value),
    poll_seconds=runtime_state.ALGO_STATUS_POLL_SECONDS,
    report=_REPORT,
)


# ── node relay ───────────────────────────────────────────────────────────
runtime_state.NODE_RELAY = NodeRelay(
    enabled=runtime_state.USE_RELAY,
    report=_REPORT,
)
_NODE_RELAY = runtime_state.NODE_RELAY

# ── engine loop ──────────────────────────────────────────────────────────
_LIVE_FEED_AGGREGATORS = LiveFeedAggregatorRegistry(managers=lambda: runtime_state.FEEDS)


_MARKET_CYCLE_OPERATIONS = MarketCycleOperations(
    pipeline_status=runtime_state.PIPELINE_STATUS,
    broadcast=broadcast,
    use_broker_services=lambda: runtime_state.USE_SMARTAPI,
    live_feed_provider=lambda: runtime_state.LIVE_FEED_PROVIDER,
    data_source=lambda: runtime_state.MARKET_SELECTION.data_source,
    feed_allowed=feed_manager._feed_allowed,
    fetch_all_eod=fetch_all_eod,
    record_today_flow=record_today_flow,
    eod_task_done=_eod_task_done,
    flow_task_done=_flow_task_done,
)


_DAILY_MARKET_SCHEDULER = DailyMarketScheduler(
    option_aggregators=_LIVE_FEED_AGGREGATORS.active,
    reset_futures_session=lambda: _get_futures_oi_tracker().reset_session(),
    is_trading_day=is_trading_day,
    eod_trigger_time=EOD_TRIGGER_TIME,
    schedule_eod_jobs=_MARKET_CYCLE_OPERATIONS.schedule_eod_jobs,
)

_MARKET_PIPELINE_SERVICE = MarketPipelineService(
    run_pipeline=_RUN_PIPELINE_SERIALIZED,
    publish_status=_MARKET_CYCLE_OPERATIONS.publish_pipeline_status,
    pipeline_status=runtime_state.PIPELINE_STATUS,
    timeout_seconds=runtime_state.PIPELINE_TIMEOUT_SECONDS,
    delayed_reason=_MARKET_CYCLE_OPERATIONS.delayed_reason,
    delayed_overlay=_MARKET_CYCLE_OPERATIONS.delayed_overlay,
)


_OI_BASELINE_SYNCHRONIZER = OiBaselineSynchronizer(
    aggregators=_LIVE_FEED_AGGREGATORS.active
)

runtime_state.CANONICAL_PAYLOAD_PUBLISHER = CanonicalPayloadPublisher(
    stream_lock=runtime_state.MARKET_STREAM_LOCK,
    use_delta=lambda: runtime_state.USE_DELTA,
    previous_payload=lambda: runtime_state.LAST_SENT,
    store_payload=runtime_state.store_canonical_payload,
    store_previous_payload=runtime_state.store_previous_payload,
    broadcast=lambda message: broadcast(message),
    compute_diff=lambda previous, current: compute_diff(previous, current),
)


runtime_state.MARKET_TICK_PACER = MarketTickPacer(
    poll_seconds=runtime_state.POLL_SECONDS,
    minimum_recompute_seconds=runtime_state.MIN_TICK_RECOMPUTE_SECONDS,
    symbol_switch_event=runtime_state.SYMBOL_SWITCH_EVENT,
    tick_activity_event=runtime_state.TICK_ACTIVITY_EVENT,
)


def _schedule_auto_execution(decision):
    feed_manager._create_background_task(
        _AUTO_EXECUTOR.maybe_execute(
            decision, runtime_state.MARKET_SELECTION.symbol, runtime_state.MARKET_SELECTION.expiry
        ),
        "auto_executor",
    )


def _schedule_node_relay(payload):
    feed_manager._create_background_task(runtime_state.NODE_RELAY.post(payload), "node_relay")


runtime_state.MARKET_ENGINE_CYCLE = MarketEngineCycle(
    reset_daily_sessions=_DAILY_MARKET_SCHEDULER.reset_sessions,
    trigger_eod=_DAILY_MARKET_SCHEDULER.trigger_eod,
    collect_pipeline=_MARKET_PIPELINE_SERVICE.collect,
    observe_pipeline=lambda success, elapsed: runtime_state.METRICS.observe_pipeline(
        success, elapsed
    ),
    market_session_status=selection_state._market_session_status,
    schedule_auto_execution=_schedule_auto_execution,
    seed_oi_baselines=_OI_BASELINE_SYNCHRONIZER.synchronize,
    publish_payload=runtime_state.CANONICAL_PAYLOAD_PUBLISHER.publish,
    schedule_node_relay=_schedule_node_relay,
    connected_count=lambda: len(runtime_state.CONNECTED),
    build_current_prices=_PAPER_PRICE_BOOK.build,
    check_pending_orders=lambda prices: PT_ENGINE.check_pending_orders(prices),
    broadcast_portfolio=_PAPER_PORTFOLIO.broadcast,
    pace=runtime_state.MARKET_TICK_PACER.wait,
)


_DASHBOARD_TRANSPORT = build_dashboard_transport(
    runtime_state=runtime_state,
    encode=lambda message: orjson.dumps(message, default=_json_default).decode(),
    decode=orjson.loads,
    origin_allowed=_ORIGIN_POLICY,
    place_order=_ORDER_SUBMISSION.handle,
    cancel_order=lambda order_id: PT_ENGINE.cancel_order(order_id),
    portfolio_broadcast=_PAPER_PORTFOLIO.broadcast,
    build_current_prices=_PAPER_PRICE_BOOK.build,
    start_funds_polling=_FUNDS_POLLER.start,
    stop_funds_polling=_FUNDS_POLLER.stop,
    switch_symbol=_SYMBOL_SWITCHER.switch,
    switch_data_source=_DATA_SOURCE_SWITCHER.switch,
    build_algo_status=_TRADING_SUPERVISOR.build_status,
    paper_snapshot=_PAPER_PORTFOLIO.handshake_snapshot,
    logger=logger,
)
runtime_state.WS_HANDSHAKE = _DASHBOARD_TRANSPORT.handshake
runtime_state.WS_MESSAGE_ROUTER = _DASHBOARD_TRANSPORT.message_router
runtime_state.WS_QUERY_CONTROLLER = _DASHBOARD_TRANSPORT.query_controller
runtime_state.DASHBOARD_WS_HANDLER = _DASHBOARD_TRANSPORT.handler


# ── HTTP handlers (thin adapters; logic lives in server/* modules) ───────
_HISTORY_API = MarketHistoryApi(
    state=lambda: {
        "symbol": runtime_state.MARKET_SELECTION.symbol,
        "broker_services_enabled": BROKER_SERVICES_ENABLED,
        "index_tokens": _SMARTAPI_INDEX_TOKENS,
    },
    # Resolved at request time so test seams and runtime configuration never
    # leave the API holding a stale provider function.
    get_candle_data=lambda *args, **kwargs: get_candle_data(*args, **kwargs),
    get_index_candles=lambda *args, **kwargs: get_index_candles(*args, **kwargs),
)
no_cache_middleware = history_no_cache_middleware


_HEALTH_SNAPSHOT = RuntimeHealthSnapshot(
    runtime_state=runtime_state,
    feed_allowed=feed_manager._feed_allowed,
    market_session_status=selection_state._market_session_status,
    build_snapshot=_build_health_response,
)


runtime_state.HTTP_ROUTE_HANDLERS = HttpRouteHandlers(
    history_api=_HISTORY_API,
    backtest_response=handle_backtest,
    default_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    run_backtest=lambda *args, **kwargs: run_backtest(*args, **kwargs),
    health_response=_health_response,
    health_snapshot=_HEALTH_SNAPSHOT.build,
    record_health_transition=lambda snapshot: _log_health_transition(snapshot),
    metrics_response=_metrics_response,
    metrics=runtime_state.METRICS,
)

# ── entry point ──────────────────────────────────────────────────────────
_HTTP_RUNTIME = build_http_runtime(
    health=runtime_state.HTTP_ROUTE_HANDLERS.health,
    broker_health=_broker_health,
    metrics=runtime_state.HTTP_ROUTE_HANDLERS.metrics,
    websocket=runtime_state.DASHBOARD_WS_HANDLER,
    bridge_websocket=_BRIDGE.handle,
    spot_history=runtime_state.HTTP_ROUTE_HANDLERS.spot_history,
    history=runtime_state.HTTP_ROUTE_HANDLERS.history,
    backtest=runtime_state.HTTP_ROUTE_HANDLERS.backtest,
    lot_sizes=runtime_state.HTTP_ROUTE_HANDLERS.lot_sizes,
    host=WS_HOST,
    port=HTTP_PORT,
    symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    middleware=no_cache_middleware,
)

_RUNTIME_SERVICES = ServerRuntimeServices(
    host=WS_HOST,
    runtime_state=runtime_state,
    feed_manager=feed_manager,
    host_is_loopback=host_is_loopback,
    index_quotes=_INDEX_QUOTE_LOOP.run,
    bridge=_BRIDGE.run,
    algo_status=_ALGO_STATUS_LOOP.run,
    reconcile=_RECONCILIATION_LOOP.run,
    live_trading_enabled=LIVE_TRADING_ENABLED,
    flush_history=flush_oi_history,
)

# Wire app-level dependencies into the feed orchestration module (kept free
# of a circular import on the websocket broadcast + paper-trading engine).
configure_feed_orchestration(
    broadcast=broadcast,
    portfolio_broadcaster=_PAPER_PORTFOLIO.broadcast_from_feed,
)


async def main():
    from infrastructure.logging import configure_logging

    lifecycle = ApplicationLifecycle(
        validate_startup=_RUNTIME_SERVICES.validate_startup,
        configure_logging=configure_logging,
        start_http_server=_HTTP_RUNTIME.start,
        set_main_loop=_RUNTIME_SERVICES.set_main_loop,
        start_live_services=_RUNTIME_SERVICES.start_live_services,
        background_jobs=_RUNTIME_SERVICES.background_jobs,
        create_background_task=feed_manager._create_background_task,
        run_engine=runtime_state.MARKET_ENGINE_CYCLE.run_forever,
        background_tasks=lambda: runtime_state.BACKGROUND_TASKS,
        close_relay=lambda: runtime_state.NODE_RELAY.close(),
        flush_state=_RUNTIME_SERVICES.flush_state,
    )
    await lifecycle.run()


if __name__ == "__main__":
    asyncio.run(main())
