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
import ipaddress
import json
import logging
import os
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from datetime import time as dtime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
# This composition root now lives under src/ (the old backend/ layout was
# migrated away). Inserting src/ keeps direct invocations working the same way
# `PYTHONPATH=src python3 -m main` does.
sys.path.insert(0, str(SCRIPT_DIR.parent))

from server.http_app import ServerConfig, create_app, start_http_server  # noqa: E402

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
from server.index_quotes import IndexQuoteFetcher  # noqa: E402
from application.runtime import (  # noqa: E402
    ApplicationLifecycle,
    build_background_jobs,
)
from application.market_service import (  # noqa: E402
    AnalyticsPipelineRunner,
    CanonicalPayloadPublisher,
    DailyMarketScheduler,
    DataSourceSwitcher,
    LiveFeedAggregatorRegistry,
    MarketEngineCycle,
    MarketPipelineService,
    MarketTickPacer,
    OiBaselineSynchronizer,
    PipelineRuntimeConfigurator,
    SerializedPipelineExecutor,
    SymbolSwitcher,
)
from server.routes import HttpRouteHandlers, ServerRoutes  # noqa: E402
from server.health_api import (
    build_health_snapshot as _build_health_response,
    health_handler as _health_response,
    metrics_handler as _metrics_response,
    broker_health as _broker_health,
    log_health_transition as _log_health_transition,
)

from server.websocket_clients import WebSocketClientHub  # noqa: E402
from server.websocket import DashboardWebSocketHandler  # noqa: E402
from server.websocket_handshake import WebSocketHandshakeSender  # noqa: E402
from server.websocket_messages import WebSocketMessageRouter  # noqa: E402
from server.websocket_query import WebSocketQueryController  # noqa: E402
from server.feeds.smartapi import (  # noqa: E402
    FeedState as _SmartApiFeedState,
    resolve_chain_tokens as _resolve_smartapi_feed_tokens,
    start_new_feed as _start_smartapi_feed_new,
    stop_feed as _stop_smartapi_feed,
    switch_existing_feed as _switch_smartapi_feed_existing,
)
from server.feeds.shoonya import (  # noqa: E402
    FeedState as _ShoonyaFeedState,
    resolve_chain_tokens as _resolve_shoonya_feed_tokens,
    start_new_feed as _start_shoonya_feed_new,
    stop_feed as _stop_shoonya_feed,
    switch_existing_feed as _switch_shoonya_feed_existing,
)
from server.feeds.upstox import (  # noqa: E402
    FeedState as _UpstoxFeedState,
    resolve_chain_tokens as _resolve_upstox_feed_tokens,
    start_new_feed as _start_upstox_feed_new,
    stop_feed as _stop_upstox_feed,
    switch_existing_feed as _switch_upstox_feed_existing,
)
from server.feeds.orchestration import (  # noqa: E402
    _smartapi_feed_state,
    _store_smartapi_feed_state,
    _smartapi_feed_start,
    _smartapi_feed_switch,
    _smartapi_feed_stop,
    _upstox_feed_state,
    _store_upstox_feed_state,
    _upstox_feed_start,
    _upstox_feed_switch,
    _upstox_feed_stop,
    _shoonya_feed_state,
    _store_shoonya_feed_state,
    _shoonya_feed_start,
    _shoonya_feed_switch,
    _shoonya_feed_stop,
    _kotak_feed_state,
    _store_kotak_feed_state,
    _kotak_feed_start,
    _kotak_feed_switch,
    _kotak_feed_stop,
    _smartapi_sync_and_broadcast,
    configure_feed_orchestration,
)
from market.providers import nse_bse_client as market_api  # noqa: E402
from application import option_chain_runtime  # noqa: E402

from operational_metrics import OperationalMetrics  # noqa: E402
from application import selection_state  # noqa: E402
from analytics.option_chain_pipeline import OptionChainPipeline  # noqa: E402
from brokers.expiry_adapter import BrokerExpiryAdapter  # noqa: E402
from brokers.option_chain_adapter import BrokerOptionChainAdapter  # noqa: E402
from market.option_chain.runtime_adapters import BrokerMarketAdapters  # noqa: E402
from server.live_feed_state import merge_live_feed_update  # noqa: E402
from server.websocket_payload import compute_diff, json_default as _json_default  # noqa: E402
from execution.paper_trading import LOT_SIZES as PT_LOT_SIZES  # noqa: E402
from execution.paper_trading import PaperTradingEngine, _instrument_key  # noqa: E402
from market.instruments.lot_sizes import configure_lot_size_resolver  # noqa: E402
from brokers.smartapi.instruments import get_lot_size as _smartapi_lot_size  # noqa: E402
from risk.account_guard import (  # noqa: E402
    LiveAccountRiskGuard,
    open_lots_from_positions,
)
from risk.live_order_store import LiveOrderStore  # noqa: E402
from risk.position_reconciler import PositionReconciler  # noqa: E402
from backtest.replay import run_backtest  # noqa: E402
from decision.auto_executor import AutoExecutor  # noqa: E402
from nse_eod_fetch import fetch_all_eod, is_trading_day  # noqa: E402
from analytics.nse_fii_dii_flow_fetch import record_today_flow  # noqa: E402
from oi.futures_oi_tracker import get_tracker as _get_futures_oi_tracker  # noqa: E402
from brokers.provider_registry import supports_websocket as _provider_supports_websocket  # noqa: E402
from server.cli_args import build_arg_parser  # noqa: E402
from server.feed_manager import BrokerFeedManager  # noqa: E402
from server.background_loops import (  # noqa: E402
    AlgoStatusLoop,
    FundsPoller,
    IndexQuoteLoop,
    NodeRelay,
    ReconciliationLoop,
)
from server.order_gateway import (  # noqa: E402
    LiveOrderGateway,
    parse_order_intent,
    validate_order_intent,
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

# Which broker's WEBSOCKET tick feed overlays fast leg-level ticks onto the
# slower NSE/BSE-polled chain — independent of execution_broker (orders) and
# market_data_provider (REST chain building). Each feed client is imported
# lazily inside its start adapter below, so deployments that don't use a
# broker never need that broker's SDK installed just to boot.
runtime_state.LIVE_FEED_PROVIDER = _broker_settings.live_feed_provider

# This module is also imported by tests and tooling. Preserve server CLI
# parsing while leaving unrelated host arguments (for example pytest flags)
# to the embedding process instead of terminating during import.
ARGS, _HOST_PROCESS_ARGS = build_arg_parser().parse_known_args()

_initial_symbol = ARGS.symbol.strip().upper()
def _resolve_default_pipeline_expiry(symbol):
    """Resolve the nearest valid exchange-calendar option expiry."""
    symbol = (symbol or "").strip().upper()
    if symbol in {"SENSEX", "BANKEX", "SENSEX50"}:
        return option_chain_runtime.BSE_EXPIRY_DEFAULT.get(
            symbol, option_chain_runtime._nearest_Thursday
        )()
    return option_chain_runtime._nearest_Tuesday()


# Manual price-source selector — "EQ" (default, cash-market spot) is the
# fixed option-pricing/decision reference; "FUT" is displayed separately
# (see option_chain_json.py's PRICE_SOURCE docstring for the 3:15-3:30
# EQ-goes-stale rationale). Legacy ?priceSource= URLs are accepted but no
# longer alter analytics.
_initial_price_source = "AUTO"
# Manual futures-expiry selector — "NEAR" (default), "NEXT", or "FAR".
# Switched via ?futuresExpiry= on the WS URL (see ws_handler) and read
# fresh into RuntimeConfig every tick by run_pipeline_once().
_initial_futures_expiry = "NEAR"
_initial_expiry = (
    ARGS.expiry.strip()
    if ARGS.expiry
    else _resolve_default_pipeline_expiry(_initial_symbol)
)
runtime_state.POLL_SECONDS = ARGS.poll_seconds
runtime_state.PIPELINE_TIMEOUT_SECONDS = max(1.0, ARGS.pipeline_timeout_seconds)
runtime_state.MIN_TICK_RECOMPUTE_SECONDS = ARGS.min_tick_recompute_seconds
WS_HOST = ARGS.host
WS_PORT = ARGS.port
HTTP_PORT = ARGS.http_port
runtime_state.USE_RELAY = ARGS.relay
runtime_state.USE_DELTA = not ARGS.no_delta
runtime_state.USE_INDEX_QUOTES = not ARGS.no_index_quotes
runtime_state.INDEX_QUOTE_SECONDS = ARGS.index_quote_seconds
runtime_state.FUNDS_POLL_SECONDS = ARGS.funds_poll_seconds
runtime_state.PORTFOLIO_POLL_SECONDS = ARGS.portfolio_poll_seconds
runtime_state.USE_SMARTAPI = BROKER_SERVICES_ENABLED
runtime_state.STRIKES_EACH_SIDE = (
    ARGS.strikes_each_side
    if ARGS.strikes_each_side is not None
    else (15 if runtime_state.USE_SMARTAPI else 50)
)

# Label maps — single source of truth for both startup banners and the
# algo-status panel (previously two divergent if/else chains).
_DATA_SOURCE_LABELS = {
    "UPSTOX": "Upstox",
    "SHOONYA": "Shoonya",
    "KITE": "Kite",
    "BREEZE": "Breeze",
    "KOTAK": "Kotak",
    "NSE_BSE": "NSE/BSE",
}
_EXECUTION_BROKER_LABELS = {
    "SHOONYA": "Shoonya",
    "UPSTOX": "Upstox",
    "KITE": "Zerodha",
    "BREEZE": "ICICI Direct",
}


# Runtime market-data source — the Dashboard's DATA SOURCE dropdown,
# switched via ?dataSource= (see switch_data_source) WITHOUT a restart.
# Process-wide, same as SYMBOL/EXPIRY; also pushed into brokers.market_data's
# runtime facade so the chain pipeline, index-quote loops, and payload all
# route consistently.
_initial_data_source = selection_state._resolve_default_data_source()
if not runtime_state.USE_SMARTAPI:
    _initial_data_source = "NSE_BSE"
_md_set_active_provider(_initial_data_source)

runtime_state.MARKET_SELECTION = selection_state.build_market_selection(
    symbol=_initial_symbol,
    expiry=_initial_expiry,
    data_source=_initial_data_source,
    price_source=_initial_price_source,
    futures_expiry=_initial_futures_expiry,
)

_md_label = _DATA_SOURCE_LABELS.get(runtime_state.MARKET_SELECTION.data_source, "SmartAPI")
if runtime_state.MARKET_SELECTION.data_source == "NSE_BSE":
    _chain_source = "NSE/BSE public REST (polling)"
    _overlay_state = "no websocket overlay"
elif runtime_state.USE_SMARTAPI:
    _chain_source = f"{_md_label} REST"
    if (
        runtime_state.MARKET_SELECTION.data_source == runtime_state.LIVE_FEED_PROVIDER
        and _provider_supports_websocket(runtime_state.MARKET_SELECTION.data_source)
    ):
        _overlay_state = f"{runtime_state.LIVE_FEED_PROVIDER} websocket overlay ENABLED"
    else:
        _overlay_state = "no websocket overlay (REST polling)"
else:
    _chain_source = "NSE/BSE public REST (public-only mode)"
    _overlay_state = "websocket overlay DISABLED (public-only mode)"
print(
    f"[feed] chain source: {_chain_source}, "
    f"analytics recompute ceiling={runtime_state.POLL_SECONDS}s floor={runtime_state.MIN_TICK_RECOMPUTE_SECONDS}s "
    f"+ {_overlay_state} "
    f"| index context via market_api.py (20s-cached)",
    flush=True,
)
print(
    f"[paper-trading] portfolio fast-path broadcast: "
    f"{'every ' + runtime_state.LIVE_FEED_PROVIDER.title() + ' tick (no throttle)' if runtime_state.PORTFOLIO_POLL_SECONDS <= 0 else f'throttled to >= {runtime_state.PORTFOLIO_POLL_SECONDS}s'}"
    + (
        ""
        if runtime_state.USE_SMARTAPI
        else " (inactive — public-only mode, falls back to --poll-seconds cadence)"
    ),
    flush=True,
)

# Top-bar ticker strip shows these five, always in this order (see
# dashboard.js INDEX_TICKER_ORDER — keep the two lists in sync). The active
# SYMBOL's own quote comes free on every regular tick, so only the OTHER
# symbols are fetched here; VIX is never the active SYMBOL.
INDEX_TICKER_SYMBOLS = ["NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX", "INDIA VIX"]
_BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}

# VIX isn't in INDEX_TOKENS (auto-built from AMXIDX ScripMaster rows — VIX
# doesn't carry that type), so it's pinned manually, same as broker_pipeline
# .py's _VIX_TOKEN. Re-verify against a fresh ScripMaster dump if quotes go
# stale/empty; nothing here will warn you if Angel reassigns the token.
_VIX_TRADINGSYMBOL = "India VIX"
_VIX_TOKEN = "99926017"  # exch_seg=NSE, verified against live ScripMaster 2026-07-14

runtime_state.DASHBOARD_CLIENTS = WebSocketClientHub()
# Compatibility alias for diagnostics and existing test seams. Connection
# ownership lives in runtime_state.DASHBOARD_CLIENTS rather than this server module.
runtime_state.CONNECTED = runtime_state.DASHBOARD_CLIENTS.clients
runtime_state.LAST_PAYLOAD = None
runtime_state.LAST_PAYLOAD_AT = None
runtime_state.LAST_SENT = None
runtime_state.BASELINE_SEQ = 0
runtime_state.BASELINE_ID = None
runtime_state.PROCESS_STARTED_AT = datetime.now().astimezone()
runtime_state.LAST_HEALTH_LOG_STATE = None
# Compatibility aliases retained during the runtime-state migration. Tests,
# diagnostics, and older extensions still inspect these names directly.
runtime_state.PIPELINE_STATUS = {
    "status": "STARTING",
    "reason": "Analytics pipeline has not completed yet",
    "startedAt": None,
    "lastSuccessAt": None,
    "elapsedSeconds": None,
}
runtime_state.METRICS = OperationalMetrics(started_at=runtime_state.PROCESS_STARTED_AT)
# Most recent real-account funds snapshot — handed to newly-connected
# clients the same way runtime_state.LAST_PAYLOAD/INDEX_QUOTES are, so the top-bar Fund
# pill doesn't sit at "n/a" until the next poll. Cleared by
# stop_funds_polling() so a reconnect while polling is stopped is never
# handed a stale real-money figure.
runtime_state.LAST_FUNDS = None

# Paper trading — single engine instance for the whole process, backed by
# SQLite (paper_trading.db) so positions/orders survive a restart. All
# access happens on the main asyncio thread (ws_handler for place_order,
# engine_loop for mark-to-market/broadcast), so no extra locking is needed
# around the sqlite3 connection.
PT_ENGINE = PaperTradingEngine()

# _build_current_prices() only ever sees ONE symbol's chain per tick. This
# cache holds the last known price per instrument_key across symbol
# switches, so a leg on a non-active symbol keeps its LTP instead of going
# blank ("—") the moment the dashboard switches symbols.
runtime_state.LAST_KNOWN_LEG_PRICES: dict = {}

# Throttle for the fast-path portfolio broadcast fired from the live-tick
# sync path (see runtime_state.PORTFOLIO_POLL_SECONDS) — separate from engine_loop()'s
# runtime_state.POLL_SECONDS-paced broadcast, which still runs as the slower fallback
# (covers public-only mode and feed-reconnect gaps).
runtime_state.LAST_PORTFOLIO_BROADCAST_TS = 0.0
EOD_TRIGGER_TIME = dtime(15, 45)  # shortly after NSE cash close (15:30)

# ── Live trading configuration ──────────────────────────────────────────
# Master switch — OFF by default; must be explicitly set to place real
# orders. Read once at process start: flipping it mid-session is a
# deliberate deploy-time decision, not a casual toggle.
LIVE_TRADING_ENABLED = (
    os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() == "true"
)

# Instant kill switch — checked on EVERY live order attempt, no restart
# needed. touch LIVE_TRADING_KILL to block all live orders in seconds
# during market hours; delete to resume.
LIVE_TRADING_KILL_SWITCH_FILE = str(PROJECT_ROOT / "LIVE_TRADING_KILL")

# Hard caps enforced SERVER-SIDE (not just in the UI) — a bug in strike/qty
# resolution on the client can't bypass them. Conservative safety net, not
# a trading limit.
LIVE_MAX_LOTS_PER_ORDER = int(os.environ.get("LIVE_MAX_LOTS_PER_ORDER", "1"))
LIVE_MAX_ORDERS_PER_MINUTE = int(os.environ.get("LIVE_MAX_ORDERS_PER_MINUTE", "5"))
_LIVE_ORDER_STORE = LiveOrderStore(max_entries=500)

if LIVE_TRADING_ENABLED:
    print(
        f"[live-trading] ENABLED — max {LIVE_MAX_LOTS_PER_ORDER} lot(s)/order, "
        f"{LIVE_MAX_ORDERS_PER_MINUTE}/min. Kill switch: touch {LIVE_TRADING_KILL_SWITCH_FILE} to disable instantly.",
        flush=True,
    )
else:
    print(
        "[live-trading] disabled (paper trading only) — set LIVE_TRADING_ENABLED=true to enable",
        flush=True,
    )

# Account-level risk guard — daily loss limit, max open exposure, and a
# drawdown-streak breaker, evaluated across the whole trading day. Trips
# the SAME kill-switch file (see risk/account_guard.py).
_ACCOUNT_GUARD = LiveAccountRiskGuard(LIVE_TRADING_KILL_SWITCH_FILE)

# Diffs the live order book against the live position book (both from the
# broker) and alerts on mismatch — same kill-switch file. Periodic sweep
# (reconcile_loop) plus a post-fill check (in the order path) — see
# risk/position_reconciler.py.
_POSITION_RECONCILER = PositionReconciler(LIVE_TRADING_KILL_SWITCH_FILE)
POSITION_RECONCILE_SECONDS = int(os.environ.get("POSITION_RECONCILE_SECONDS", "120"))

# Algo status panel refresh cadence. Deliberately NOT tick-cadence — this
# is supervisory/status info and _ACCOUNT_GUARD.get_status() does a SQLite
# read per call, so it runs on its own slow loop.
runtime_state.ALGO_STATUS_POLL_SECONDS = int(os.environ.get("runtime_state.ALGO_STATUS_POLL_SECONDS", "5"))
runtime_state.LAST_ALGO_STATUS = None
# Most recent non-clean PositionReconciler.check(), broadcast as
# reconciliationAlert and handed to new connections so a dashboard opened
# after a mismatch still sees it.
runtime_state.LAST_RECONCILIATION_ALERT = None
# Cache of the most recent live position-book fetch (reconcile_loop's own
# periodic call). _build_algo_status() reads this for current open lots
# instead of making its own broker call every 5s; the pre-trade exposure
# check still fetches fresh. None until live trading is enabled and the
# first reconcile cycle has completed.
runtime_state.LAST_LIVE_POSITIONS = None

# ── WebSocket origin allowlist ──────────────────────────────────────────
# Browsers do NOT apply same-origin restrictions to WebSocket handshakes,
# so without this ANY page in the same browser could drive the socket,
# including submitting orders (cross-site WebSocket hijacking). Origin-less
# requests are accepted only from a loopback peer, so a remote client can't
# bypass the browser-origin allowlist by omitting Origin.
_DEFAULT_ALLOWED_ORIGINS = {
    f"http://{WS_HOST}:{HTTP_PORT}",
    f"http://localhost:{HTTP_PORT}",
    f"http://127.0.0.1:{HTTP_PORT}",
}
_EXTRA_ALLOWED_ORIGINS = {
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
}
ALLOWED_ORIGINS = _DEFAULT_ALLOWED_ORIGINS | _EXTRA_ALLOWED_ORIGINS


def _host_is_loopback(host: str) -> bool:
    """Return whether a listener host is restricted to this machine."""
    normalized = str(host or "").strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _peer_is_loopback(request) -> bool:
    try:
        return ipaddress.ip_address(request.remote).is_loopback
    except (TypeError, ValueError):
        return False


def _origin_allowed(request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return _peer_is_loopback(request)
    # A dashboard opened directly from disk (file://...) gets the opaque
    # browser origin "null". Permit that development mode only from a
    # loopback peer; a remote Origin:null stays rejected.
    if origin == "null":
        return _peer_is_loopback(request)
    return origin in ALLOWED_ORIGINS


# Analytics passes are serialized with provider switches so one pass observes
# one stable provider identity from request planning through final export.
_PIPELINE_EXECUTOR = SerializedPipelineExecutor()
runtime_state.INDEX_QUOTES = {}  # {"BANKNIFTY": {"spot":.., "spotChgPct":..}, ...}
runtime_state.SYMBOL_SWITCH_EVENT = asyncio.Event()
# Set (thread-safely) by TickAggregator's flush loop on every real tick
# flush. engine_loop() waits on this OR runtime_state.SYMBOL_SWITCH_EVENT, bounded by
# runtime_state.MIN_TICK_RECOMPUTE_SECONDS (floor) and runtime_state.POLL_SECONDS (ceiling).
runtime_state.TICK_ACTIVITY_EVENT = asyncio.Event()
# Serializes the canonical full/delta stream and its backing snapshots.
# compute_diff runs in a worker thread; without this lock the async tick
# path could mutate runtime_state.LAST_SENT/runtime_state.LAST_PAYLOAD mid-traversal. New-client
# snapshot handoff uses the same lock.
runtime_state.MARKET_STREAM_LOCK = asyncio.Lock()

# Real-export capture seam: run_pipeline_once() reads the dashboard payload
# back out of mTerminals_json's own export, so the pipeline and the WS
# stream share one serialization path. The wiring now lives in
# server/payload_capture so this module stays a composition root.
from server.payload_capture import install_payload_export_capture  # noqa: E402

_PAYLOAD_EXPORT_CAPTURE = install_payload_export_capture()


# ── task plumbing ────────────────────────────────────────────────────────


def _report_failed_task(task: asyncio.Task, tag: str) -> bool:
    """Print a fire-and-forget task's exception; returns True on success."""
    if task.cancelled():
        return False
    exc = task.exception()
    if exc is not None:
        print(f"[{tag}] FAILED: {exc!r}", flush=True)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return False
    return True


def _eod_task_done(task: asyncio.Task):
    """Surface exceptions from the fire-and-forget EOD fetch (nothing
    awaits it directly, so they'd otherwise fail silently)."""
    if _report_failed_task(task, "eod"):
        print("[eod] fetch_all_eod completed successfully", flush=True)


def _flow_task_done(task: asyncio.Task):
    """Same rationale as _eod_task_done, for the cash-market FII/DII flow
    fetch — a separate NSE endpoint, so its failure isn't conflated with
    the participant-OI EOD fetch's own success/failure reporting."""
    if _report_failed_task(task, "flow"):
        ok = task.result()
        print(
            f"[flow] record_today_flow "
            f"{'succeeded' if ok else 'returned False (no data yet)'}",
            flush=True,
        )


# ── pipeline plumbing ────────────────────────────────────────────────────
async def _run_pipeline_locked():
    """Run exactly one blocking pipeline pass without permitting overlap."""
    return await _PIPELINE_EXECUTOR.run_blocking(run_pipeline_once)


async def _publish_pipeline_status(status, reason="", elapsed=None):
    """Broadcast analytics availability only when its visible state changes."""
    previous = (runtime_state.PIPELINE_STATUS.get("status"), runtime_state.PIPELINE_STATUS.get("reason"))
    runtime_state.PIPELINE_STATUS["status"] = status
    runtime_state.PIPELINE_STATUS["reason"] = reason
    runtime_state.PIPELINE_STATUS["elapsedSeconds"] = (
        round(elapsed, 3) if elapsed is not None else None
    )
    if status == "LIVE":
        runtime_state.PIPELINE_STATUS["lastSuccessAt"] = datetime.now().astimezone().isoformat()
    if (status, reason) != previous:
        await broadcast({"type": "pipelineStatus", "payload": dict(runtime_state.PIPELINE_STATUS)})


async def broadcast(message):
    if isinstance(message, dict) and message.get("type") == "full":
        runtime_state.BASELINE_SEQ += 1
        payload = message.get("payload") or {}
        runtime_state.BASELINE_ID = (
            f"{payload.get('symbol', '')}:{payload.get('expiry', '')}:{runtime_state.BASELINE_SEQ}"
        )
        message = {**message, "version": runtime_state.BASELINE_ID}
    elif isinstance(message, dict) and message.get("type") == "delta":
        if runtime_state.BASELINE_ID is None:
            print(
                "[ws] dropping delta without an established full-snapshot baseline",
                flush=True,
            )
            return
        message = {**message, "baseVersion": runtime_state.BASELINE_ID}
    msg_str = orjson.dumps(message, default=_json_default).decode()
    await runtime_state.DASHBOARD_CLIENTS.broadcast(
        msg_str, on_error=lambda error: print(f"[ws] Error broadcasting: {error}")
    )


# Dashboard-relay protocol and its independent cache/poll loop live in
# server.bridge. The live coordinator supplies only current process state.
def _fetch_bridge_futures(symbol, which, use_smartapi):
    """Composition seam for the bridge's legacy/public futures sources."""
    if use_smartapi:
        from application.market_pipeline.futures import fetch_futures_wide

        return fetch_futures_wide(symbol, which=which)
    return market_api.fetch_public_futures(symbol, which)


_BRIDGE = DashboardBridge(
    state=lambda: {
        "symbol": runtime_state.MARKET_SELECTION.symbol,
        "futures_expiry": runtime_state.MARKET_SELECTION.futures_expiry,
        "use_smartapi": runtime_state.USE_SMARTAPI,
        "last_payload": runtime_state.LAST_PAYLOAD,
        "index_quotes": runtime_state.INDEX_QUOTES,
    },
    origin_allowed=_origin_allowed,
    json_default=_json_default,
    market_api=market_api,
    futures_fetcher=_fetch_bridge_futures,
)
BRIDGE_CONNECTED = _BRIDGE.clients


async def broadcast_bridge(payload):
    await _BRIDGE.broadcast(payload)


async def bridge_ws_handler(request):
    return await _BRIDGE.handle(request)


async def bridge_loop():
    await _BRIDGE.run()


_PIPELINE_RUNTIME_CONFIGURATOR = PipelineRuntimeConfigurator(
    data_source=lambda: runtime_state.MARKET_SELECTION.data_source,
    activate_provider=_md_set_active_provider,
    resolve_default_expiry=_resolve_default_pipeline_expiry,
    apply_config=lambda config: None,
)


def _build_pipeline_runtime_config(
    symbol,
    expiry=None,
    no_extra_chains=None,
    strict_expiry=None,
    no_virtual_oi=None,
    price_source=None,
    futures_expiry=None,
):
    """Build the complete configuration passed to one analytics run."""
    return _PIPELINE_RUNTIME_CONFIGURATOR.configure(
        symbol=symbol,
        expiry=expiry,
        no_extra_chains=no_extra_chains,
        strict_expiry=strict_expiry,
        no_virtual_oi=no_virtual_oi,
        price_source=price_source,
        futures_expiry=futures_expiry,
        strikes_each_side=runtime_state.STRIKES_EACH_SIDE,
    )


def _build_broker_market_adapters():
    from application.market_pipeline.futures import fetch_futures_wide
    from application.market_pipeline.option_chain import (
        fetch_option_chain_wide,
        get_available_expiries,
    )
    from application.market_pipeline.quotes import (
        fetch_all_pills_and_vix_batched,
        fetch_sensex_ticker,
        fetch_ticker_payload,
        fetch_vix,
    )
    from application.market_pipeline.utils import _canon_underlying

    chain = BrokerOptionChainAdapter(
        fetch_chain=fetch_option_chain_wide,
        canonicalize_symbol=_canon_underlying,
    )
    expiries = BrokerExpiryAdapter(fallback=get_available_expiries)
    return BrokerMarketAdapters(
        canonicalize_symbol=chain.canonicalize,
        fetch_chain=chain.fetch,
        list_expiries=expiries.list_expiries,
        fetch_futures=lambda symbol, exchange, which: fetch_futures_wide(
            symbol, None, exchange=exchange, which=which
        ),
        warm_batch=fetch_all_pills_and_vix_batched,
        fetch_ticker_payload=fetch_ticker_payload,
        fetch_vix=fetch_vix,
        fetch_sensex_quote=fetch_sensex_ticker,
    )


_BROKER_MARKET_ADAPTERS = (
    _build_broker_market_adapters() if runtime_state.USE_SMARTAPI else None
)
_OPTION_CHAIN_PIPELINE = OptionChainPipeline(
    implementation=lambda config: option_chain_runtime.main(
        config,
        broker_adapters=_BROKER_MARKET_ADAPTERS,
        export_dashboard=_PAYLOAD_EXPORT_CAPTURE.export,
    ),
)


_ANALYTICS_PIPELINE_RUNNER = AnalyticsPipelineRunner(
    configure=lambda: _build_pipeline_runtime_config(
        runtime_state.MARKET_SELECTION.symbol,
        runtime_state.MARKET_SELECTION.expiry,
        no_extra_chains=not ARGS.extra_chains,
        strict_expiry=ARGS.strict_expiry,
        no_virtual_oi=ARGS.no_virtual_oi,
        price_source=runtime_state.MARKET_SELECTION.price_source,
        futures_expiry=runtime_state.MARKET_SELECTION.futures_expiry,
    ),
    clear_capture=_PAYLOAD_EXPORT_CAPTURE.clear,
    invoke=_OPTION_CHAIN_PIPELINE.run,
    captured_payload=lambda: _PAYLOAD_EXPORT_CAPTURE.payload,
)


def run_pipeline_once():
    """Compatibility seam for application analytics invocation."""
    return _ANALYTICS_PIPELINE_RUNNER.run_once()


def _index_quote_fetcher():
    """Build from the current provider seam (also keeps runtime switches live)."""
    return IndexQuoteFetcher(
        state=lambda: {
            "data_source": runtime_state.MARKET_SELECTION.data_source,
            "vix_symbol": _VIX_TRADINGSYMBOL,
            "vix_token": _VIX_TOKEN,
        },
        market_data=market_data,
        market_api=market_api,
    )


def fetch_nse_index_quotes_sync():
    return _index_quote_fetcher().public_nse()


def fetch_bse_index_quote_sync(symbol):
    return _index_quote_fetcher().public_bse(symbol)


def fetch_index_quotes_smartapi_sync():
    return _index_quote_fetcher().provider()


# ── Live feed state (per provider, legacy module-global seams) ───────────
# Tests and the health snapshot read these directly; BrokerFeedManager only
# touches them via the snapshot/store pairs below.
# The asyncio loop main() runs on lets a runtime switch to a provider whose
# feed was never started at boot and start that feed on the live loop.

def _print_log(message):
    print(message, flush=True)


runtime_state.FEEDS = {
    provider: BrokerFeedManager(
        provider,
        snapshot=snapshot,
        store=store,
        start=start,
        switch=switch,
        stop=stop,
        default_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        main_loop=lambda: runtime_state.MAIN_LOOP,
        log=_print_log,
    )
    for provider, snapshot, store, start, switch, stop in (
        (
            "SMARTAPI",
            _smartapi_feed_state,
            _store_smartapi_feed_state,
            _smartapi_feed_start,
            _smartapi_feed_switch,
            _smartapi_feed_stop,
        ),
        (
            "UPSTOX",
            _upstox_feed_state,
            _store_upstox_feed_state,
            _upstox_feed_start,
            _upstox_feed_switch,
            _upstox_feed_stop,
        ),
        (
            "SHOONYA",
            _shoonya_feed_state,
            _store_shoonya_feed_state,
            _shoonya_feed_start,
            _shoonya_feed_switch,
            _shoonya_feed_stop,
        ),
        (
            "KOTAK",
            _kotak_feed_state,
            _store_kotak_feed_state,
            _kotak_feed_start,
            _kotak_feed_switch,
            _kotak_feed_stop,
        ),
    )
}


# Legacy entry points, kept as thin wrappers — existing tests and external
# callers seam through these names.
def start_smartapi_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    runtime_state.FEEDS["SMARTAPI"].start(loop, underlying, strikes_around_atm, expiry)


def _switch_smartapi_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    runtime_state.FEEDS["SMARTAPI"].switch_blocking(new_symbol, strikes_around_atm, expiry)


def restart_smartapi_feed(new_symbol, new_expiry=None):
    runtime_state.FEEDS["SMARTAPI"].restart(new_symbol, new_expiry)


def _stop_smartapi_feed_blocking():
    runtime_state.FEEDS["SMARTAPI"].stop_blocking()


def start_upstox_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    runtime_state.FEEDS["UPSTOX"].start(loop, underlying, strikes_around_atm, expiry)


def _switch_upstox_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    runtime_state.FEEDS["UPSTOX"].switch_blocking(new_symbol, strikes_around_atm, expiry)


def restart_upstox_feed(new_symbol, new_expiry=None):
    runtime_state.FEEDS["UPSTOX"].restart(new_symbol, new_expiry)


def _stop_upstox_feed_blocking():
    runtime_state.FEEDS["UPSTOX"].stop_blocking()


def start_shoonya_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    runtime_state.FEEDS["SHOONYA"].start(loop, underlying, strikes_around_atm, expiry)


def _switch_shoonya_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    runtime_state.FEEDS["SHOONYA"].switch_blocking(new_symbol, strikes_around_atm, expiry)


def restart_shoonya_feed(new_symbol, new_expiry=None):
    runtime_state.FEEDS["SHOONYA"].restart(new_symbol, new_expiry)


def _stop_shoonya_feed_blocking():
    runtime_state.FEEDS["SHOONYA"].stop_blocking()


def start_kotak_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    runtime_state.FEEDS["KOTAK"].start(loop, underlying, strikes_around_atm, expiry)


def _switch_kotak_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    runtime_state.FEEDS["KOTAK"].switch_blocking(new_symbol, strikes_around_atm, expiry)


def restart_kotak_feed(new_symbol, new_expiry=None):
    runtime_state.FEEDS["KOTAK"].restart(new_symbol, new_expiry)


def _stop_kotak_feed_blocking():
    runtime_state.FEEDS["KOTAK"].stop_blocking()


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


def switch_symbol(new_symbol, new_expiry=None):
    """Compatibility seam for application-owned symbol switching."""
    return _SYMBOL_SWITCHER.switch(new_symbol, new_expiry)


_DATA_SOURCE_SWITCHER = DataSourceSwitcher(
    valid_sources=lambda: _MD_PROVIDER_KEYS,
    current_source=lambda: runtime_state.MARKET_SELECTION.data_source,
    execution_gate=_PIPELINE_EXECUTOR,
    activate_provider=_md_set_active_provider,
    stop_feed=feed_manager._stop_active_broker_feed,
    commit_source=feed_manager._commit_data_source,
    supports_websocket=_provider_supports_websocket,
    restart_feed=feed_manager._restart_live_feed,
    current_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    current_expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
    signal_refresh=runtime_state.SYMBOL_SWITCH_EVENT.set,
)


async def switch_data_source(new_source: str):
    """Compatibility seam for application-owned provider switching."""
    return await _DATA_SOURCE_SWITCHER.switch(new_source)


# ── paper-trading pricing ────────────────────────────────────────────────
def _build_current_prices(payload):
    """Build the {instrument_key: ltp} map paper_trading.py's
    check_pending_orders()/mark_to_market()/place_order() expect, from the
    SAME tick payload the dashboard renders — the paper engine is always
    priced off exactly what the user sees on screen, never a stale/separate
    fetch."""
    prices = {}
    if not payload:
        return dict(runtime_state.LAST_KNOWN_LEG_PRICES)
    symbol = payload.get("symbol")
    if not symbol:
        return dict(runtime_state.LAST_KNOWN_LEG_PRICES)

    spot = payload.get("spot")
    if spot is not None:
        prices[_instrument_key(symbol, "", None, "INDEX")] = spot

    expiry = payload.get("expiry") or ""
    fut_ltp = payload.get("futLTP")
    if fut_ltp is not None:
        prices[_instrument_key(symbol, expiry, None, "FUT")] = fut_ltp

    chains = payload.get("chains") or {}
    if not chains and expiry:
        chains = {expiry: payload.get("chain") or []}

    for exp, rows in chains.items():
        for row in rows or []:
            strike = row.get("strike")
            if strike is None:
                continue
            if row.get("ceLTP") is not None:
                prices[_instrument_key(symbol, exp, strike, "CE")] = row["ceLTP"]
            if row.get("peLTP") is not None:
                prices[_instrument_key(symbol, exp, strike, "PE")] = row["peLTP"]

    # A tick only ever prices ONE symbol's legs — merge, don't replace, so
    # positions on other symbols keep their last known price instead of
    # going blank the moment the dashboard's active symbol changes.
    runtime_state.LAST_KNOWN_LEG_PRICES.update(prices)
    return {**runtime_state.LAST_KNOWN_LEG_PRICES, **prices}


async def _broadcast_portfolio(current_prices):
    """Push fresh portfolio + orders snapshots to every connected client.
    dashboard.js's generic deepMerge branch lands these at
    _wsState.portfolio / _wsState.orders with no extra client wiring."""
    portfolio = PT_ENGINE.get_portfolio_summary(current_prices)
    orders = PT_ENGINE.get_orders()
    # Fund summary (NIFTY spot as proxy for index-margin checks when the
    # active symbol's spot is missing) keeps the frontend Fund pill synced
    # with PT_STARTING_CAPITAL and SPAN estimation.
    spot = current_prices.get(_instrument_key("NIFTY", "", None, "INDEX"))
    portfolio["funds"] = PT_ENGINE.get_fund_summary(
        spot_price=spot, current_prices=current_prices
    )
    await broadcast({"type": "portfolio", "payload": portfolio})
    await broadcast({"type": "orders", "payload": orders})


async def _feed_portfolio_broadcast(payload):
    """Paper-trading fast path fired by the live feed: reprice open legs off
    the tick payload the dashboard already shows, sweep pending LIMIT fills,
    then push portfolio/orders to clients. Injected into the feed
    orchestration module so it stays decoupled from server.app."""
    current_prices = _build_current_prices(payload)
    PT_ENGINE.check_pending_orders(current_prices)
    await _broadcast_portfolio(current_prices)


# ── live order token resolution + gateway ────────────────────────────────
def _resolve_live_order_token(symbol, instrument_type, expiry, strike):
    """Resolves (exchange, tradingsymbol, symboltoken) for a live order.
    Mirrors the tick feed's underlying/exchange logic (_BSE_SYMBOLS -> BFO,
    else NFO) so live orders target the same contract space the dashboard
    is already streaming ticks for."""
    exchange = "BFO" if symbol in _BSE_SYMBOLS else "NFO"

    if instrument_type in ("CE", "PE"):
        if _execution_resolve_option_contract is not None:
            return _execution_resolve_option_contract(
                symbol, expiry, strike, instrument_type, exchange
            )
        # expiry arrives in option_chain_json's format ("14-Jul-2026");
        # ScripMaster uses "14JUL2026" — convert before lookup.
        try:
            expiry_ddmmmyyyy = (
                datetime.strptime(expiry, "%d-%b-%Y").strftime("%d%b%Y").upper()
            )
        except (ValueError, TypeError):
            return None
        resolved = market_data.find_option_token(
            symbol, expiry_ddmmmyyyy, strike, instrument_type, exchange
        )
        if not resolved:
            return None
        return exchange, resolved["tradingsymbol"], resolved["token"]

    if instrument_type == "FUT":
        # Futures aren't resolved anywhere in this pipeline yet — refuse
        # rather than silently mis-resolving a real order's token.
        return None
    # INDEX (spot) — not a tradeable instrument on its own; refuse.
    return None


_LIVE_ORDERS = LiveOrderGateway(
    enabled=LIVE_TRADING_ENABLED,
    kill_switch_file=LIVE_TRADING_KILL_SWITCH_FILE,
    max_lots_per_order=LIVE_MAX_LOTS_PER_ORDER,
    max_orders_per_minute=LIVE_MAX_ORDERS_PER_MINUTE,
    lot_sizes=PT_LOT_SIZES,
    account_guard=_ACCOUNT_GUARD,
    position_reconciler=_POSITION_RECONCILER,
    resolve_token=_resolve_live_order_token,
    place_order=smartapi_place_order,
    get_positions=smartapi_get_positions,
    get_order_book=smartapi_get_order_book,
    order_store=_LIVE_ORDER_STORE,
)


# Legacy seams (were module-level helpers before the gateway extraction;
# tests may call these directly).
def _live_order_gate():
    return _LIVE_ORDERS.order_gate()


def _check_live_rate_limit():
    return _LIVE_ORDERS.rate_limit_allows()


def _live_trading_kill_switch_active():
    return _LIVE_ORDERS.kill_switch_active()


def _completed_live_order(client_order_id):
    return _LIVE_ORDERS.completed_order(client_order_id)


def _submit_live_order_idempotent(client_order_id, *args, **kwargs):
    return _LIVE_ORDERS.submit_idempotent(client_order_id, *args, **kwargs)


async def _handle_place_order(payload, _live_gate_acquired=False):
    """Handles an inbound {"type":"place_order", "payload":{...}} message
    from dashboard.js's sendWsMessage('place_order', ...).

    Routes to a REAL broker order ONLY if ALL of: LIVE_TRADING_ENABLED=true
    at process start; the kill-switch file absent; client sent live=true AND
    confirmed=true (deliberate per-order opt-in via the UI confirm modal —
    not a global client-side toggle); within the lot ceiling and per-minute
    rate cap; instrument resolves to a real symboltoken. Everything else —
    including any resolution failure — falls through to the paper engine
    unchanged. Prices MARKET orders off runtime_state.LAST_PAYLOAD (the tick already on
    screen), so the fill the user sees matches the LTP they clicked. Always
    re-broadcasts portfolio + orders afterward.

    Returns a status dict on EVERY path so _submit_auto_order() can tell a
    downstream rejection from an actual placement."""
    intent = parse_order_intent(payload)
    validation_reason = validate_order_intent(intent)
    if validation_reason:
        print(f"[order] REJECTED malformed intent: {validation_reason}", flush=True)
        current_prices = _build_current_prices(runtime_state.LAST_PAYLOAD)
        await _broadcast_portfolio(current_prices)
        return {"status": "rejected", "reason": validation_reason}

    current_prices = _build_current_prices(runtime_state.LAST_PAYLOAD)

    # Serialize the complete live pre-trade check and submission (see
    # LiveOrderGateway.order_gate for the TOCTOU this closes).
    if intent.wants_live and not _live_gate_acquired:
        async with _LIVE_ORDERS.order_gate():
            return await _handle_place_order(payload, _live_gate_acquired=True)

    if intent.wants_live:
        return await _LIVE_ORDERS.place_live_order(
            intent,
            current_prices,
            broadcast_portfolio=_broadcast_portfolio,
            broadcast_alert=_broadcast_reconciliation_alert,
        )

    # ── Paper trading path ───────────────────────────────────────────
    key = _instrument_key(
        intent.symbol, intent.expiry, intent.strike, intent.instrument_type
    )
    current_ltp = current_prices.get(key)
    order = PT_ENGINE.place_order(
        intent.symbol,
        intent.side,
        intent.qty_lots,
        instrument_type=intent.instrument_type,
        expiry=intent.expiry,
        strike=intent.strike,
        order_type=intent.order_type,
        limit_price=intent.limit_price,
        current_ltp=current_ltp,
        client_order_id=intent.client_order_id,
    )
    print(
        f"[paper-trading] {order.status}: {intent.symbol} {intent.side} "
        f"{intent.qty_lots} lot(s) {intent.instrument_type} {intent.expiry} "
        f"{intent.strike} "
        f"@ {order.fill_price if order.fill_price is not None else intent.limit_price}"
        + (f" — {order.reject_reason}" if order.reject_reason else ""),
        flush=True,
    )
    await _broadcast_portfolio(current_prices)
    return {
        "status": order.status,
        "reason": order.reject_reason,
        "order_id": getattr(order, "id", None),
        "client_order_id": getattr(order, "client_order_id", intent.client_order_id),
    }


async def _submit_auto_order(symbol, instrument_type, expiry, strike, side, qty_lots):
    """Bridge from decision/auto_executor.py into the manual order path —
    same payload shape as a dashboard click, with live=True/confirmed=True
    filled in on the algo's behalf; every other check (lot size, rate
    limit, account_guard exposure/trip state) still runs exactly as for a
    human-submitted order.

    Raises on rejection so AutoExecutor.maybe_execute() logs the failure
    (and records it in the auto-trade history feed) instead of reporting a
    downstream-rejected order as EXECUTED — which is how gate failures
    AFTER auto_executor's own evaluate() cleared (kill switch flipped,
    guard tripped, exposure cap hit, resolve failure) used to vanish."""
    result = await _handle_place_order(
        {
            "symbol": symbol,
            "instrument_type": instrument_type,
            "expiry": expiry,
            "strike": strike,
            "side": side,
            "order_type": "MARKET",
            "qty_lots": qty_lots,
            "client_order_id": "a" + uuid.uuid4().hex[:19],
            "live": True,
            "confirmed": True,
        }
    )
    status = (result or {}).get("status")
    if status != "placed":
        reason = (result or {}).get("reason") or (
            f"unexpected status {status!r} from _handle_place_order"
        )
        raise RuntimeError(reason)
    return result


# Strategy -> execution bridge — constructed here since it needs
# _submit_auto_order above. OFF by default (AUTO_STRATEGY_EXECUTION_ENABLED);
# independent of LIVE_TRADING_ENABLED (both must be true for an auto order
# to reach the real broker). See decision/auto_executor.py.
_AUTO_EXECUTOR = AutoExecutor(_ACCOUNT_GUARD, _submit_auto_order)


def _build_algo_status() -> dict:
    """Composes the algoStatus broadcast payload — one read-only snapshot of
    every live-trading/algo safety mechanism's state, so the dashboard shows
    a single status panel instead of requiring a server-log tail. Calling
    this never mutates guard/executor state."""
    guard_status = _ACCOUNT_GUARD.get_status()
    # current_open_lots pairs with guard_status's max_open_lots so the panel
    # can show "current / limit". Sourced from if runtime_state.LAST_LIVE_POSITIONS (the
    # reconcile loop's periodic fetch) — this is a status display, not the
    # pre-trade exposure check (which still fetches fresh).
    try:
        guard_status["current_open_lots"] = (
            open_lots_from_positions(runtime_state.LAST_LIVE_POSITIONS, PT_LOT_SIZES)
            if runtime_state.LAST_LIVE_POSITIONS is not None
            else None
        )
    except Exception as e:
        print(
            f"[algo-status] could not compute open lots from cached positions: {e}",
            flush=True,
        )
        guard_status["current_open_lots"] = None

    exec_status = _AUTO_EXECUTOR.get_status(runtime_state.MARKET_SELECTION.symbol)
    exec_status["history"] = _AUTO_EXECUTOR.get_history()[:30]

    return {
        "broker": (
            "Public Data"
            if not BROKER_SERVICES_ENABLED
            else _EXECUTION_BROKER_LABELS.get(
                _broker_settings.execution_broker, "Angel One"
            )
        ),
        "liveTradingEnabled": LIVE_TRADING_ENABLED,
        "killSwitchActive": _live_trading_kill_switch_active(),
        "maxLotsPerOrder": LIVE_MAX_LOTS_PER_ORDER,
        "maxOrdersPerMinute": LIVE_MAX_ORDERS_PER_MINUTE,
        "accountGuard": guard_status,
        "autoExecutor": exec_status,
        "symbol": runtime_state.MARKET_SELECTION.symbol,
    }


async def _broadcast_reconciliation_alert(result, source: str):
    """Turns a non-clean PositionReconciler.check() result into a
    reconciliationAlert broadcast (previously log-only, so a human watching
    the dashboard never saw below-trip-threshold mismatches — most resolve
    themselves next cycle once a fill propagates, but they should still be
    visible as they happen). No-op on a clean result. `source` distinguishes
    the fast post-fill check from the periodic sweep, for display context."""
    if result.clean:
        return
    tripped = result.max_abs_diff_lots() >= _POSITION_RECONCILER.trip_lots
    payload = {
        "ts": time.time(),
        "source": source,
        "tripped": tripped,
        "tripLots": _POSITION_RECONCILER.trip_lots,
        "mismatches": [
            {
                "symbol": m.symbol,
                "orderBookLots": m.order_book_lots,
                "positionLots": m.position_lots,
                "diffLots": m.diff_lots,
            }
            for m in result.mismatches
        ],
        "unparseableSymbols": result.unparseable_symbols,
    }
    runtime_state.LAST_RECONCILIATION_ALERT = payload
    await broadcast({"type": "reconciliationAlert", "payload": payload})


# ── background pollers ───────────────────────────────────────────────────
def _set_last_funds(value):
    runtime_state.LAST_FUNDS = value


def _set_last_live_positions(value):
    runtime_state.LAST_LIVE_POSITIONS = value


def _set_last_algo_status(value):
    runtime_state.LAST_ALGO_STATUS = value


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
    report=_print_log,
)


# Pushes {"type":"funds",...}; dashboard.js's generic handler lands it at
# wsState.funds, which paper-trading.js reads once Live mode is on.
_FUNDS_POLLER = FundsPoller(
    get_funds=smartapi_get_funds,
    broadcast=broadcast,
    set_last_funds=_set_last_funds,
    poll_seconds=runtime_state.FUNDS_POLL_SECONDS,
    spawn_task=feed_manager._create_background_task,
    report=_print_log,
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
    set_last_positions=_set_last_live_positions,
    broadcast_alert=lambda result, source: _broadcast_reconciliation_alert(
        result, source=source
    ),
    poll_seconds=POSITION_RECONCILE_SECONDS,
    report=_print_log,
)


_ALGO_STATUS_LOOP = AlgoStatusLoop(
    build_status=lambda: _build_algo_status(),
    broadcast=broadcast,
    set_last_status=_set_last_algo_status,
    poll_seconds=runtime_state.ALGO_STATUS_POLL_SECONDS,
    report=_print_log,
)


# ── node relay ───────────────────────────────────────────────────────────
runtime_state.NODE_RELAY = NodeRelay(
    enabled=runtime_state.USE_RELAY,
    report=_print_log,
)
_NODE_RELAY = runtime_state.NODE_RELAY

# ── engine loop ──────────────────────────────────────────────────────────
_LIVE_FEED_AGGREGATORS = LiveFeedAggregatorRegistry(managers=lambda: runtime_state.FEEDS)


def _live_aggregators() -> dict:
    """Compatibility seam for active application feed aggregators."""
    return _LIVE_FEED_AGGREGATORS.active()


def _schedule_eod_jobs(now):
    eod_task = asyncio.create_task(asyncio.to_thread(fetch_all_eod, now, True))
    eod_task.add_done_callback(_eod_task_done)
    flow_task = asyncio.create_task(asyncio.to_thread(record_today_flow))
    flow_task.add_done_callback(_flow_task_done)


_DAILY_MARKET_SCHEDULER = DailyMarketScheduler(
    option_aggregators=_LIVE_FEED_AGGREGATORS.active,
    reset_futures_session=lambda: _get_futures_oi_tracker().reset_session(),
    is_trading_day=is_trading_day,
    eod_trigger_time=EOD_TRIGGER_TIME,
    schedule_eod_jobs=_schedule_eod_jobs,
)


def _reset_daily_sessions(now: datetime):
    """Compatibility seam for application daily-session maintenance."""
    _DAILY_MARKET_SCHEDULER.reset_sessions(now)


def _maybe_trigger_eod(now: datetime):
    """Compatibility seam for application EOD scheduling."""
    _DAILY_MARKET_SCHEDULER.trigger_eod(now)


def _pipeline_delayed_reason(timeout_seconds):
    if runtime_state.USE_SMARTAPI:
        return (
            f"REST analytics pass exceeded {timeout_seconds:g}s; "
            "live prices continue via WebSocket"
        )
    return (
        f"Public REST analytics pass exceeded {timeout_seconds:g}s; "
        "SmartAPI remains disabled"
    )


def _pipeline_delayed_overlay():
    if runtime_state.USE_SMARTAPI and feed_manager._feed_allowed(runtime_state.LIVE_FEED_PROVIDER):
        return f"{runtime_state.LIVE_FEED_PROVIDER} websocket overlay remains active"
    return f"{runtime_state.MARKET_SELECTION.data_source} REST polling will retry"


_MARKET_PIPELINE_SERVICE = MarketPipelineService(
    run_pipeline=lambda: _run_pipeline_locked(),
    publish_status=lambda *args, **kwargs: _publish_pipeline_status(
        *args, **kwargs
    ),
    pipeline_status=runtime_state.PIPELINE_STATUS,
    timeout_seconds=runtime_state.PIPELINE_TIMEOUT_SECONDS,
    delayed_reason=_pipeline_delayed_reason,
    delayed_overlay=_pipeline_delayed_overlay,
)


async def _collect_pipeline_payload(tick_start: float):
    """Compatibility seam for application market orchestration."""
    return await _MARKET_PIPELINE_SERVICE.collect(tick_start)


_OI_BASELINE_SYNCHRONIZER = OiBaselineSynchronizer(
    aggregators=_LIVE_FEED_AGGREGATORS.active
)


def _seed_oi_baselines(payload):
    """Compatibility seam for application OI baseline synchronization."""
    _OI_BASELINE_SYNCHRONIZER.synchronize(payload)


def _store_canonical_payload(payload, published_at):
    runtime_state.LAST_PAYLOAD = payload
    runtime_state.LAST_PAYLOAD_AT = published_at


def _store_previous_payload(payload):
    runtime_state.LAST_SENT = payload


runtime_state.CANONICAL_PAYLOAD_PUBLISHER = CanonicalPayloadPublisher(
    stream_lock=runtime_state.MARKET_STREAM_LOCK,
    use_delta=lambda: runtime_state.USE_DELTA,
    previous_payload=lambda: runtime_state.LAST_SENT,
    store_payload=_store_canonical_payload,
    store_previous_payload=_store_previous_payload,
    broadcast=lambda message: broadcast(message),
    compute_diff=lambda previous, current: compute_diff(previous, current),
)


async def _publish_canonical_payload(payload):
    """Compatibility seam for canonical application publication."""
    await runtime_state.CANONICAL_PAYLOAD_PUBLISHER.publish(payload)


runtime_state.MARKET_TICK_PACER = MarketTickPacer(
    poll_seconds=runtime_state.POLL_SECONDS,
    minimum_recompute_seconds=runtime_state.MIN_TICK_RECOMPUTE_SECONDS,
    symbol_switch_event=runtime_state.SYMBOL_SWITCH_EVENT,
    tick_activity_event=runtime_state.TICK_ACTIVITY_EVENT,
)


async def _pace_until_next_tick(tick_start: float, pipeline_elapsed: float):
    """Compatibility seam for application tick pacing."""
    await runtime_state.MARKET_TICK_PACER.wait(tick_start, pipeline_elapsed)


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
    reset_daily_sessions=_reset_daily_sessions,
    trigger_eod=_maybe_trigger_eod,
    collect_pipeline=lambda tick_started_at: _collect_pipeline_payload(
        tick_started_at
    ),
    observe_pipeline=lambda success, elapsed: runtime_state.METRICS.observe_pipeline(
        success, elapsed
    ),
    market_session_status=selection_state._market_session_status,
    schedule_auto_execution=_schedule_auto_execution,
    seed_oi_baselines=_seed_oi_baselines,
    publish_payload=lambda payload: _publish_canonical_payload(payload),
    schedule_node_relay=_schedule_node_relay,
    connected_count=lambda: len(runtime_state.CONNECTED),
    build_current_prices=_build_current_prices,
    check_pending_orders=lambda prices: PT_ENGINE.check_pending_orders(prices),
    broadcast_portfolio=lambda prices: _broadcast_portfolio(prices),
    pace=lambda tick_started_at, elapsed: _pace_until_next_tick(
        tick_started_at, elapsed
    ),
)


async def engine_loop():
    """Compatibility seam for the canonical application engine cycle."""
    await runtime_state.MARKET_ENGINE_CYCLE.run_forever()


# ── websocket handler ────────────────────────────────────────────────────
def _paper_handshake_snapshot():
    prices = _build_current_prices(runtime_state.LAST_PAYLOAD)
    portfolio = PT_ENGINE.get_portfolio_summary(prices)
    spot = prices.get(_instrument_key("NIFTY", "", None, "INDEX"))
    portfolio["funds"] = PT_ENGINE.get_fund_summary(
        spot_price=spot, current_prices=prices
    )
    return portfolio, PT_ENGINE.get_orders()


runtime_state.WS_HANDSHAKE = WebSocketHandshakeSender(
    encode=lambda message: orjson.dumps(
        message, default=_json_default
    ).decode(),
    market_lock=runtime_state.MARKET_STREAM_LOCK,
    market_payload=lambda: runtime_state.LAST_PAYLOAD,
    baseline_version=lambda: runtime_state.BASELINE_ID,
    index_quotes=lambda: runtime_state.INDEX_QUOTES,
    pipeline_status=lambda: runtime_state.PIPELINE_STATUS,
    funds=lambda: runtime_state.LAST_FUNDS,
    algo_status=lambda: (
        runtime_state.LAST_ALGO_STATUS
        if runtime_state.LAST_ALGO_STATUS is not None
        else _build_algo_status()
    ),
    reconciliation_alert=lambda: runtime_state.LAST_RECONCILIATION_ALERT,
    paper_snapshot=_paper_handshake_snapshot,
)


async def _send_handshake_snapshot(ws, *, send_full: bool):
    """Compatibility seam for the canonical handshake sender."""
    await runtime_state.WS_HANDSHAKE.send(ws, send_full=send_full)


runtime_state.WS_MESSAGE_ROUTER = WebSocketMessageRouter(
    place_order=lambda payload: _handle_place_order(payload),
    cancel_order=lambda order_id: PT_ENGINE.cancel_order(order_id),
    broadcast_portfolio=lambda prices: _broadcast_portfolio(prices),
    build_current_prices=lambda payload: _build_current_prices(payload),
    last_payload=lambda: runtime_state.LAST_PAYLOAD,
    start_funds_polling=lambda: _FUNDS_POLLER.start(),
    stop_funds_polling=lambda: _FUNDS_POLLER.stop(),
)


async def _ws_dispatch_message(data):
    """Compatibility seam for the canonical message router."""
    await runtime_state.WS_MESSAGE_ROUTER.dispatch(data)


def _set_price_source(value):
    runtime_state.MARKET_SELECTION.select_price_source(value)


def _set_futures_expiry(value):
    runtime_state.MARKET_SELECTION.select_futures_expiry(value)


def _invalidate_market_baseline():
    runtime_state.LAST_SENT = None
    runtime_state.SYMBOL_SWITCH_EVENT.set()


runtime_state.WS_QUERY_CONTROLLER = WebSocketQueryController(
    current_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    switch_symbol=lambda symbol, expiry: switch_symbol(symbol, expiry),
    switch_data_source=lambda source: switch_data_source(source),
    current_price_source=lambda: runtime_state.MARKET_SELECTION.price_source,
    set_price_source=_set_price_source,
    current_futures_expiry=lambda: runtime_state.MARKET_SELECTION.futures_expiry,
    set_futures_expiry=_set_futures_expiry,
    invalidate_market_baseline=_invalidate_market_baseline,
)


runtime_state.DASHBOARD_WS_HANDLER = DashboardWebSocketHandler(
    origin_allowed=lambda request: _origin_allowed(request),
    clients=runtime_state.DASHBOARD_CLIENTS,
    connected_count=lambda: len(runtime_state.CONNECTED),
    metrics=runtime_state.METRICS,
    query_controller=runtime_state.WS_QUERY_CONTROLLER,
    send_handshake=lambda websocket, **kwargs: _send_handshake_snapshot(
        websocket, **kwargs
    ),
    has_market_payload=lambda: runtime_state.LAST_PAYLOAD is not None,
    decode=orjson.loads,
    dispatch_message=lambda data: _ws_dispatch_message(data),
    symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
    logger=logger,
)


async def ws_handler(request):
    return await runtime_state.DASHBOARD_WS_HANDLER(request)


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


async def spot_history_handler(request):
    return await runtime_state.HTTP_ROUTE_HANDLERS.spot_history(request)


async def history_handler(request):
    return await runtime_state.HTTP_ROUTE_HANDLERS.history(request)


async def backtest_handler(request):
    return await runtime_state.HTTP_ROUTE_HANDLERS.backtest(request)


async def lot_sizes_handler(request):
    return await runtime_state.HTTP_ROUTE_HANDLERS.lot_sizes(request)


def _build_health_snapshot(now=None):
    """Process, transport, and market-feed health contract. A closed
    exchange is not itself a degraded service; during an open session, a
    missing or old canonical payload makes the service degraded even when
    both listeners are reachable."""
    smartapi_connected = upstox_connected = shoonya_connected = False
    if runtime_state.USE_SMARTAPI:
        if runtime_state.LIVE_FEED_PROVIDER == "UPSTOX":
            upstox_connected = runtime_state.FEEDS["UPSTOX"].connected
        elif runtime_state.LIVE_FEED_PROVIDER == "SHOONYA":
            shoonya_connected = runtime_state.FEEDS["SHOONYA"].connected
        else:
            smartapi_connected = runtime_state.FEEDS["SMARTAPI"].connected

    return _build_health_response(
        {
            "process_started_at": runtime_state.PROCESS_STARTED_AT,
            "poll_seconds": runtime_state.POLL_SECONDS,
            "last_payload": runtime_state.LAST_PAYLOAD,
            "last_payload_at": runtime_state.LAST_PAYLOAD_AT,
            "connected_clients": len(runtime_state.CONNECTED),
            "symbol": runtime_state.MARKET_SELECTION.symbol,
            "expiry": runtime_state.MARKET_SELECTION.expiry,
            "broker_services_enabled": runtime_state.USE_SMARTAPI,
            "data_source": runtime_state.MARKET_SELECTION.data_source,
            "live_feed_provider": runtime_state.LIVE_FEED_PROVIDER,
            "live_feed_active": runtime_state.USE_SMARTAPI
            and feed_manager._feed_allowed(runtime_state.MARKET_SELECTION.data_source),
            "pipeline_status": runtime_state.PIPELINE_STATUS,
            "smartapi_connected": smartapi_connected,
            "upstox_connected": upstox_connected,
            "shoonya_connected": shoonya_connected,
        },
        selection_state._market_session_status,
        now,
    )


async def metrics_handler(request):
    return await runtime_state.HTTP_ROUTE_HANDLERS.metrics(request)

async def health_handler(request):
    return await runtime_state.HTTP_ROUTE_HANDLERS.health(request)


runtime_state.HTTP_ROUTE_HANDLERS = HttpRouteHandlers(
    history_api=_HISTORY_API,
    backtest_response=handle_backtest,
    default_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
    run_backtest=lambda *args, **kwargs: run_backtest(*args, **kwargs),
    health_response=_health_response,
    health_snapshot=lambda: _build_health_snapshot(),
    record_health_transition=lambda snapshot: _log_health_transition(snapshot),
    metrics_response=_metrics_response,
    metrics=runtime_state.METRICS,
)

# ── entry point ──────────────────────────────────────────────────────────
def _validate_server_startup():
    if not _host_is_loopback(WS_HOST):
        raise RuntimeError(
            f"refusing unsafe non-loopback bind {WS_HOST!r}: the WebSocket "
            "control channel has no remote-client authentication; use "
            "--host localhost or a loopback address"
        )


async def _start_http_runtime():
    return await start_http_server(
        ServerRoutes(
                    health=health_handler,
                    broker_health=_broker_health,
                    metrics=metrics_handler,
                    websocket=ws_handler,
                    bridge_websocket=bridge_ws_handler,
                    spot_history=spot_history_handler,
                    history=history_handler,
                    backtest=backtest_handler,
                    lot_sizes=lot_sizes_handler,
                ),
        ServerConfig(
            host=WS_HOST,
            port=HTTP_PORT,
            symbol=runtime_state.MARKET_SELECTION.symbol,
            middleware=no_cache_middleware,
        ),
    )


def _set_main_loop(loop):
    runtime_state.MAIN_LOOP = loop


def _start_live_runtime(loop):
    if runtime_state.USE_SMARTAPI and feed_manager._feed_allowed(runtime_state.LIVE_FEED_PROVIDER):
        feed_manager._start_live_feed(runtime_state.LIVE_FEED_PROVIDER, loop)
    elif runtime_state.USE_SMARTAPI:
        print(
            f"[feed] websocket overlay not started "
            f"(data source={runtime_state.MARKET_SELECTION.data_source}, "
            f"feed provider={runtime_state.LIVE_FEED_PROVIDER})",
            flush=True,
        )
    else:
        print(
            "[broker] authenticated services disabled "
            "(BROKER_SERVICES_ENABLED=false) — no broker login, account/order "
            "REST call, or websocket connection; public daily ScripMaster allowed",
            flush=True,
        )


def _runtime_background_jobs():
    return build_background_jobs(
        index_quotes=_INDEX_QUOTE_LOOP.run,
        bridge=bridge_loop,
        algo_status=_ALGO_STATUS_LOOP.run,
        reconcile=_RECONCILIATION_LOOP.run,
        live_trading_enabled=LIVE_TRADING_ENABLED,
    )


def _flush_runtime_state():
    from oi.oi_analysis import flush_history_to_disk
    flush_history_to_disk()

# Wire app-level dependencies into the feed orchestration module (kept free
# of a circular import on the websocket broadcast + paper-trading engine).
configure_feed_orchestration(
    broadcast=broadcast,
    portfolio_broadcaster=_feed_portfolio_broadcast,
)


async def main():
    from infrastructure.logging import configure_logging

    lifecycle = ApplicationLifecycle(
        validate_startup=_validate_server_startup,
        configure_logging=configure_logging,
        start_http_server=_start_http_runtime,
        set_main_loop=_set_main_loop,
        start_live_services=_start_live_runtime,
        background_jobs=_runtime_background_jobs,
        create_background_task=feed_manager._create_background_task,
        run_engine=engine_loop,
        background_tasks=lambda: runtime_state.BACKGROUND_TASKS,
        close_relay=lambda: runtime_state.NODE_RELAY.close(),
        flush_state=_flush_runtime_state,
    )
    await lifecycle.run()


if __name__ == "__main__":
    asyncio.run(main())
