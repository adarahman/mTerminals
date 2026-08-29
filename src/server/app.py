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
from server.bridge import DashboardBridge  # noqa: E402
from server.market_history_api import no_cache_middleware  # noqa: E402
from application.market_service import (  # noqa: E402
    DataSourceSwitcher,
    SymbolSwitcher,
)
from application.market_pipeline.futures import fetch_futures_wide  # noqa: E402
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
from server.analytics_runtime import (  # noqa: E402
    AnalyticsRuntime,
    build_broker_market_adapters,
)
from execution.paper_trading import LOT_SIZES as PT_LOT_SIZES  # noqa: E402
from execution.paper_trading import _instrument_key  # noqa: E402
from backtest.replay import run_backtest  # noqa: E402
from market.instruments.lot_sizes import configure_lot_size_resolver  # noqa: E402
from brokers.smartapi.instruments import get_lot_size as _smartapi_lot_size  # noqa: E402
from nse_eod_fetch import fetch_all_eod, is_trading_day  # noqa: E402
from analytics.nse_fii_dii_flow_fetch import record_today_flow  # noqa: E402
from oi.futures_oi_tracker import get_tracker as _get_futures_oi_tracker  # noqa: E402
from brokers.provider_registry import supports_websocket as _provider_supports_websocket  # noqa: E402
from server.cli_args import build_arg_parser  # noqa: E402
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
from server.market_runtime_assembly import build_market_runtime  # noqa: E402
from server.application_assembly import build_server_application  # noqa: E402
from server.task_callbacks import (  # noqa: E402
    eod_task_done as _eod_task_done,
    flow_task_done as _flow_task_done,
    report_failed_task as _report_failed_task,
)
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


_MARKET_RUNTIME = build_market_runtime(
    runtime_state=runtime_state,
    market_data=market_data,
    get_funds=smartapi_get_funds,
    get_order_book=smartapi_get_order_book,
    get_positions=smartapi_get_positions,
    position_reconciler=_POSITION_RECONCILER,
    position_reconcile_seconds=POSITION_RECONCILE_SECONDS,
    trading_supervisor=_TRADING_SUPERVISOR,
    auto_executor=_AUTO_EXECUTOR,
    lot_sizes=PT_LOT_SIZES,
    index_symbols=INDEX_TICKER_SYMBOLS,
    broadcast=broadcast,
    report=_REPORT,
    spawn_task=feed_manager._create_background_task,
    active_feed_managers=lambda: runtime_state.FEEDS,
    feed_allowed=feed_manager._feed_allowed,
    fetch_all_eod=fetch_all_eod,
    record_today_flow=record_today_flow,
    eod_task_done=_eod_task_done,
    flow_task_done=_flow_task_done,
    reset_futures_session=lambda: _get_futures_oi_tracker().reset_session(),
    is_trading_day=is_trading_day,
    eod_trigger_time=EOD_TRIGGER_TIME,
    run_pipeline=_RUN_PIPELINE_SERIALIZED,
    compute_diff=compute_diff,
    market_session_status=selection_state._market_session_status,
    paper_price_book=_PAPER_PRICE_BOOK,
    paper_engine=PT_ENGINE,
    paper_portfolio=_PAPER_PORTFOLIO,
)
_INDEX_QUOTE_LOOP = _MARKET_RUNTIME.index_quotes
_FUNDS_POLLER = _MARKET_RUNTIME.funds
_RECONCILIATION_LOOP = _MARKET_RUNTIME.reconciliation
_ALGO_STATUS_LOOP = _MARKET_RUNTIME.algo_status
_NODE_RELAY = _MARKET_RUNTIME.node_relay
_DAILY_MARKET_SCHEDULER = _MARKET_RUNTIME.scheduler
_MARKET_PIPELINE_SERVICE = _MARKET_RUNTIME.pipeline


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
_SERVER_APPLICATION = build_server_application(
    runtime_state=runtime_state,
    feed_manager=feed_manager,
    host=WS_HOST,
    http_port=HTTP_PORT,
    middleware=no_cache_middleware,
    dashboard_websocket=runtime_state.DASHBOARD_WS_HANDLER,
    bridge=_BRIDGE,
    broker_services_enabled=BROKER_SERVICES_ENABLED,
    index_tokens=_SMARTAPI_INDEX_TOKENS,
    get_candle_data=get_candle_data,
    get_index_candles=get_index_candles,
    run_backtest_call=lambda *args, **kwargs: run_backtest(*args, **kwargs),
    feed_allowed=feed_manager._feed_allowed,
    market_session_status=selection_state._market_session_status,
    host_is_loopback=host_is_loopback,
    index_quotes=_INDEX_QUOTE_LOOP.run,
    algo_status=_ALGO_STATUS_LOOP.run,
    reconcile=_RECONCILIATION_LOOP.run,
    live_trading_enabled=LIVE_TRADING_ENABLED,
)
_HISTORY_API = _SERVER_APPLICATION.history_api
_HEALTH_SNAPSHOT = _SERVER_APPLICATION.health_snapshot
_HTTP_RUNTIME = _SERVER_APPLICATION.http
_RUNTIME_SERVICES = _SERVER_APPLICATION.services

# Wire app-level dependencies into the feed orchestration module (kept free
# of a circular import on the websocket broadcast + paper-trading engine).
configure_feed_orchestration(
    broadcast=broadcast,
    portfolio_broadcaster=_PAPER_PORTFOLIO.broadcast_from_feed,
)


async def main():
    await _SERVER_APPLICATION.run()


if __name__ == "__main__":
    asyncio.run(main())
