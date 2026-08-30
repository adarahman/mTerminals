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
import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
# This composition root now lives under src/ (the old backend/ layout was
# migrated away). Inserting src/ keeps direct invocations working the same way
# `PYTHONPATH=src python3 -m main` does.
sys.path.insert(0, str(SCRIPT_DIR.parent))

import orjson
from server import runtime_state

from infrastructure.config import settings as _broker_settings  # noqa: E402
from server import broker_services  # noqa: E402  (imports config + brokers.*)
from server import feed_manager  # noqa: E402
from server.market_history_api import no_cache_middleware  # noqa: E402
from server.feeds.orchestration import _smartapi_sync_and_broadcast  # noqa: E402,F401
from market.providers import nse_bse_client as market_api  # noqa: E402
from application import option_chain_runtime  # noqa: E402

from server.websocket_payload import json_default as _json_default  # noqa: E402
from execution.paper_trading import LOT_SIZES as PT_LOT_SIZES  # noqa: E402,F401
from execution.paper_trading import _instrument_key  # noqa: E402
from backtest.replay import run_backtest  # noqa: E402
from brokers.provider_registry import supports_websocket as _provider_supports_websocket  # noqa: E402
from server.startup_configuration import resolve_default_pipeline_expiry as _resolve_default_pipeline_expiry  # noqa: E402
from server.core_runtime_assembly import build_core_runtime  # noqa: E402
from server.runtime_stack import build_runtime_stack  # noqa: E402
from server.process_bootstrap import bootstrap_process  # noqa: E402
logger = logging.getLogger("mterminals.server")


def _broker_lot_size(symbol):
    """Load broker instrument metadata only when a live lookup is requested."""
    if not broker_services.BROKER_SERVICES_ENABLED:
        raise RuntimeError("broker lot-size resolver is disabled")
    from brokers.smartapi.instruments import get_lot_size

    return get_lot_size(symbol)


def _fetch_broker_futures(symbol, which):
    """Keep broker-heavy futures modules outside public-only startup."""
    from application.market_pipeline.futures import fetch_futures_wide

    return fetch_futures_wide(symbol, which=which)

BROKER_SERVICES_ENABLED = broker_services.BROKER_SERVICES_ENABLED
_MD_PROVIDER_KEYS = broker_services.MD_PROVIDER_KEYS
_md_set_active_provider = broker_services.md_set_active_provider

_PROCESS_BOOTSTRAP = bootstrap_process(
    project_root=PROJECT_ROOT,
    runtime_state=runtime_state,
    broker_services=broker_services,
    broker_settings=_broker_settings,
    instrument_key=_instrument_key,
    lot_size_resolver=_broker_lot_size,
    supports_websocket=_provider_supports_websocket,
)
ARGS = _PROCESS_BOOTSTRAP.args
_HOST_PROCESS_ARGS = _PROCESS_BOOTSTRAP.host_process_args
_STARTUP_CONFIGURATION = _PROCESS_BOOTSTRAP.startup
WS_HOST = _STARTUP_CONFIGURATION.host
WS_PORT = _STARTUP_CONFIGURATION.websocket_port
HTTP_PORT = _STARTUP_CONFIGURATION.http_port
_RUNTIME_BOOTSTRAP = _PROCESS_BOOTSTRAP.runtime
PT_ENGINE = _RUNTIME_BOOTSTRAP.paper_engine
_PAPER_PRICE_BOOK = _RUNTIME_BOOTSTRAP.paper_price_book
EOD_TRIGGER_TIME = _RUNTIME_BOOTSTRAP.eod_trigger_time

_LIVE_TRADING_CONFIG = _PROCESS_BOOTSTRAP.live_trading
LIVE_TRADING_ENABLED = _LIVE_TRADING_CONFIG.enabled
LIVE_TRADING_KILL_SWITCH_FILE = _LIVE_TRADING_CONFIG.kill_switch_file
LIVE_MAX_LOTS_PER_ORDER = _LIVE_TRADING_CONFIG.max_lots_per_order
LIVE_MAX_ORDERS_PER_MINUTE = _LIVE_TRADING_CONFIG.max_orders_per_minute
POSITION_RECONCILE_SECONDS = _LIVE_TRADING_CONFIG.reconcile_seconds
ALLOWED_ORIGINS = _PROCESS_BOOTSTRAP.allowed_origins
_ORIGIN_POLICY = _PROCESS_BOOTSTRAP.origin_policy
_REPORT = _PROCESS_BOOTSTRAP.report
_CORE_RUNTIME = build_core_runtime(
    runtime_state=runtime_state,
    args=ARGS,
    paper_engine=PT_ENGINE,
    paper_price_book=_PAPER_PRICE_BOOK,
    instrument_key=_instrument_key,
    origin_allowed=_ORIGIN_POLICY,
    json_default=_json_default,
    encode=lambda message: orjson.dumps(message, default=_json_default).decode(),
    market_api=market_api,
    broker_futures_fetcher=_fetch_broker_futures,
    activate_provider=_md_set_active_provider,
    resolve_default_expiry=_resolve_default_pipeline_expiry,
    invoke_analytics=option_chain_runtime.main,
    broker_services_enabled=runtime_state.USE_SMARTAPI,
    provider_keys=_MD_PROVIDER_KEYS,
    supports_websocket=_provider_supports_websocket,
    feed_manager=feed_manager,
    report=_REPORT,
)
_PAYLOAD_EXPORT_CAPTURE = _CORE_RUNTIME.payload_capture
_DASHBOARD_BROADCASTER = _CORE_RUNTIME.broadcaster
broadcast = _CORE_RUNTIME.broadcast
_PAPER_PORTFOLIO = _CORE_RUNTIME.paper_portfolio
_BRIDGE = _CORE_RUNTIME.bridge
BRIDGE_CONNECTED = _BRIDGE.clients
_ANALYTICS_RUNTIME = _CORE_RUNTIME.analytics
_RUN_PIPELINE_SERIALIZED = _ANALYTICS_RUNTIME.run
_SYMBOL_SWITCHER = _CORE_RUNTIME.symbol_switcher
_DATA_SOURCE_SWITCHER = _CORE_RUNTIME.data_source_switcher


_RUNTIME_STACK = build_runtime_stack(
    runtime_state=runtime_state,
    core_runtime=_CORE_RUNTIME,
    live_trading_config=_LIVE_TRADING_CONFIG,
    paper_engine=PT_ENGINE,
    paper_price_book=_PAPER_PRICE_BOOK,
    eod_trigger_time=EOD_TRIGGER_TIME,
    position_reconcile_seconds=POSITION_RECONCILE_SECONDS,
    host=WS_HOST,
    http_port=HTTP_PORT,
    middleware=no_cache_middleware,
    origin_allowed=_ORIGIN_POLICY,
    encode=lambda message: orjson.dumps(message, default=_json_default).decode(),
    decode=orjson.loads,
    broker_services=broker_services,
    broker_settings=_broker_settings,
    feed_manager=feed_manager,
    logger=logger,
    report=_REPORT,
    run_backtest_call=lambda *args, **kwargs: run_backtest(*args, **kwargs),
)
_LIVE_TRADING_RUNTIME = _RUNTIME_STACK.live_trading
_ACCOUNT_GUARD = _LIVE_TRADING_RUNTIME.account_guard
_POSITION_RECONCILER = _LIVE_TRADING_RUNTIME.position_reconciler
_LIVE_ORDERS = _LIVE_TRADING_RUNTIME.orders
_ORDER_SUBMISSION = _LIVE_TRADING_RUNTIME.submission
_AUTO_EXECUTOR = _LIVE_TRADING_RUNTIME.auto_executor
_TRADING_SUPERVISOR = _LIVE_TRADING_RUNTIME.supervisor
_resolve_live_order_token = _LIVE_TRADING_RUNTIME.resolve_token


_MARKET_RUNTIME = _RUNTIME_STACK.market
_INDEX_QUOTE_LOOP = _MARKET_RUNTIME.index_quotes
_FUNDS_POLLER = _MARKET_RUNTIME.funds
_RECONCILIATION_LOOP = _MARKET_RUNTIME.reconciliation
_ALGO_STATUS_LOOP = _MARKET_RUNTIME.algo_status
_NODE_RELAY = _MARKET_RUNTIME.node_relay
_DAILY_MARKET_SCHEDULER = _MARKET_RUNTIME.scheduler
_MARKET_PIPELINE_SERVICE = _MARKET_RUNTIME.pipeline


_DASHBOARD_TRANSPORT = _RUNTIME_STACK.dashboard
_SERVER_APPLICATION = _RUNTIME_STACK.application
_HISTORY_API = _SERVER_APPLICATION.history_api
_HEALTH_SNAPSHOT = _SERVER_APPLICATION.health_snapshot
_HTTP_RUNTIME = _SERVER_APPLICATION.http
_RUNTIME_SERVICES = _SERVER_APPLICATION.services

async def main():
    await _SERVER_APPLICATION.run()


if __name__ == "__main__":
    asyncio.run(main())
