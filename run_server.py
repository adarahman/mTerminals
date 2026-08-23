"""mTerminals live server — composition root.

Wires the extracted subsystems (server.feeds.*, server.feed_manager,
server.order_gateway, server.broker_services, ...) into the running process:
the dashboard WebSocket, the analytics pipeline loop, background pollers,
and HTTP handlers. This file owns the process-wide runtime state
(SYMBOL/EXPIRY/DATA_SOURCE, the canonical payload snapshots) and the
orchestration between subsystems; the mechanics live in server/*.

Import-order note: option_chain_json parses sys.argv at import time, so the
imports below are split around a temporary argv hide.
"""

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
from urllib.parse import unquote

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "backend"))

import aiohttp
import orjson
from aiohttp import web

# ── argv-hidden import window ────────────────────────────────────────────
# option_chain_json parses sys.argv at import time — hide our own
# server-only arguments from it (and everything imported alongside it) so
# it doesn't choke on them.
_real_argv = sys.argv
sys.argv = [_real_argv[0]]
from config import settings as _broker_settings  # noqa: E402
from server import broker_services  # noqa: E402  (imports config + brokers.*)
from server.health import log_transition as _log_server_health_transition  # noqa: E402
from server import feed_lifecycle as _feed_lifecycle  # noqa: E402
from server.feed_expiry import matches_displayed_expiry as _matches_displayed_expiry  # noqa: E402
from server.backtest_api import handle_backtest  # noqa: E402
from server.bridge import DashboardBridge  # noqa: E402
from server.market_history_api import (  # noqa: E402
    MarketHistoryApi,
    no_cache_middleware as history_no_cache_middleware,
)
from server.index_quotes import IndexQuoteFetcher  # noqa: E402
from server.health_api import (  # noqa: E402
    build_health_snapshot as _build_health_response,
    health_handler as _health_response,
    metrics_handler as _metrics_response,
)
from server.websocket_clients import WebSocketClientHub  # noqa: E402
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
import market_api  # noqa: E402  (lightweight ticker-strip quotes; no argv parsing)
import mTerminals_json  # noqa: E402
import option_chain_json  # noqa: E402

sys.argv = _real_argv  # restore for anything that inspects argv later

from operational_metrics import OperationalMetrics  # noqa: E402
from pipeline_config import RuntimeConfig  # noqa: E402
from live_feed_state import merge_live_feed_update  # noqa: E402
from ws_payload import compute_diff, json_default as _json_default  # noqa: E402
from paper_trading import LOT_SIZES as PT_LOT_SIZES  # noqa: E402
from paper_trading import PaperTradingEngine, _instrument_key  # noqa: E402
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
LIVE_FEED_PROVIDER = _broker_settings.live_feed_provider

ARGS = build_arg_parser().parse_args()

SYMBOL = ARGS.symbol.strip().upper()
# Manual price-source selector — "EQ" (default, cash-market spot) is the
# fixed option-pricing/decision reference; "FUT" is displayed separately
# (see option_chain_json.py's PRICE_SOURCE docstring for the 3:15-3:30
# EQ-goes-stale rationale). Legacy ?priceSource= URLs are accepted but no
# longer alter analytics.
PRICE_SOURCE = "AUTO"
# Manual futures-expiry selector — "NEAR" (default), "NEXT", or "FAR".
# Switched via ?futuresExpiry= on the WS URL (see ws_handler) and read
# fresh into RuntimeConfig every tick by run_pipeline_once().
FUTURES_EXPIRY = "NEAR"
EXPIRY = ARGS.expiry
POLL_SECONDS = ARGS.poll_seconds
PIPELINE_TIMEOUT_SECONDS = max(1.0, ARGS.pipeline_timeout_seconds)
MIN_TICK_RECOMPUTE_SECONDS = ARGS.min_tick_recompute_seconds
WS_HOST = ARGS.host
WS_PORT = ARGS.port
HTTP_PORT = ARGS.http_port
USE_RELAY = ARGS.relay
USE_DELTA = not ARGS.no_delta
USE_INDEX_QUOTES = not ARGS.no_index_quotes
INDEX_QUOTE_SECONDS = ARGS.index_quote_seconds
FUNDS_POLL_SECONDS = ARGS.funds_poll_seconds
PORTFOLIO_POLL_SECONDS = ARGS.portfolio_poll_seconds
USE_SMARTAPI = BROKER_SERVICES_ENABLED
STRIKES_EACH_SIDE = (
    ARGS.strikes_each_side
    if ARGS.strikes_each_side is not None
    else (15 if USE_SMARTAPI else 50)
)
option_chain_json.set_runtime_config(
    RuntimeConfig(
        strikes_each_side=STRIKES_EACH_SIDE,
        use_smartapi=USE_SMARTAPI,
    )
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


def _resolve_default_data_source() -> str:
    """Startup default for the runtime DATA SOURCE dropdown.

    Prefers the configured MARKET_DATA_PROVIDER when usable (registered AND
    credentialed); otherwise the first credentialed BROKER source, so a
    stale/empty token in .env doesn't silently strand the dashboard on the
    public NSE/BSE API. NSE/BSE is the default only when NO broker has
    credentials (fresh install, or BROKER_SERVICES_ENABLED=false forces it
    explicitly regardless)."""
    configured = _broker_settings.market_data_provider
    if configured in _MD_PROVIDER_KEYS and _md_provider_has_credentials(configured):
        return configured
    for candidate in _MD_PROVIDER_KEYS:
        if candidate == "NSE_BSE":
            continue
        if _md_provider_has_credentials(candidate):
            return candidate
    return "NSE_BSE"


# Runtime market-data source — the Dashboard's DATA SOURCE dropdown,
# switched via ?dataSource= (see switch_data_source) WITHOUT a restart.
# Process-wide, same as SYMBOL/EXPIRY; also pushed into brokers.market_data's
# runtime facade so the chain pipeline, index-quote loops, and payload all
# route consistently.
DATA_SOURCE = _resolve_default_data_source()
if not USE_SMARTAPI:
    DATA_SOURCE = "NSE_BSE"  # public-only mode: NSE/BSE is the only source
_md_set_active_provider(DATA_SOURCE)

_md_label = _DATA_SOURCE_LABELS.get(DATA_SOURCE, "SmartAPI")
if DATA_SOURCE == "NSE_BSE":
    _chain_source = "NSE/BSE public REST (polling)"
    _overlay_state = "no websocket overlay"
elif USE_SMARTAPI:
    _chain_source = f"{_md_label} REST"
    if DATA_SOURCE == LIVE_FEED_PROVIDER and _provider_supports_websocket(DATA_SOURCE):
        _overlay_state = f"{LIVE_FEED_PROVIDER} websocket overlay ENABLED"
    else:
        _overlay_state = "no websocket overlay (REST polling)"
else:
    _chain_source = "NSE/BSE public REST (public-only mode)"
    _overlay_state = "websocket overlay DISABLED (public-only mode)"
print(
    f"[feed] chain source: {_chain_source}, "
    f"analytics recompute ceiling={POLL_SECONDS}s floor={MIN_TICK_RECOMPUTE_SECONDS}s "
    f"+ {_overlay_state} "
    f"| index context via market_api.py (20s-cached)",
    flush=True,
)
print(
    f"[paper-trading] portfolio fast-path broadcast: "
    f"{'every ' + LIVE_FEED_PROVIDER.title() + ' tick (no throttle)' if PORTFOLIO_POLL_SECONDS <= 0 else f'throttled to >= {PORTFOLIO_POLL_SECONDS}s'}"
    + (
        ""
        if USE_SMARTAPI
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

_DASHBOARD_CLIENTS = WebSocketClientHub()
# Compatibility alias for diagnostics and existing test seams. Connection
# ownership lives in _DASHBOARD_CLIENTS rather than this server module.
CONNECTED = _DASHBOARD_CLIENTS.clients
LAST_PAYLOAD = None
LAST_PAYLOAD_AT = None
_LAST_SENT = None
_BASELINE_SEQ = 0
_BASELINE_ID = None
PROCESS_STARTED_AT = datetime.now().astimezone()
_LAST_HEALTH_LOG_STATE = None
_BACKGROUND_TASKS: set = set()
_PIPELINE_TASK = None
_PIPELINE_STATUS = {
    "status": "STARTING",
    "reason": "Analytics pipeline has not completed yet",
    "startedAt": None,
    "lastSuccessAt": None,
    "elapsedSeconds": None,
}
METRICS = OperationalMetrics(started_at=PROCESS_STARTED_AT)
# Most recent real-account funds snapshot — handed to newly-connected
# clients the same way LAST_PAYLOAD/INDEX_QUOTES are, so the top-bar Fund
# pill doesn't sit at "n/a" until the next poll. Cleared by
# stop_funds_polling() so a reconnect while polling is stopped is never
# handed a stale real-money figure.
LAST_FUNDS = None

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
_LAST_KNOWN_LEG_PRICES: dict = {}

# Throttle for the fast-path portfolio broadcast fired from the live-tick
# sync path (see PORTFOLIO_POLL_SECONDS) — separate from engine_loop()'s
# POLL_SECONDS-paced broadcast, which still runs as the slower fallback
# (covers public-only mode and feed-reconnect gaps).
_LAST_PORTFOLIO_BROADCAST_TS = 0.0
EOD_TRIGGER_TIME = dtime(15, 45)  # shortly after NSE cash close (15:30)
MARKET_OPEN_TIME = dtime(9, 15)
MARKET_CLOSE_TIME = dtime(15, 30)
_EOD_DONE_DATE = None  # which date's EOD job already ran
_LAST_SESSION_DATE = None  # which date's OI session baseline is active


def _market_session_status(now: datetime) -> str:
    """Best-effort NSE session label for the UI. Uses the same yearly
    holiday calendar as is_trading_day(); ad-hoc exchange closures still
    require that calendar/source to be updated."""
    if now.weekday() < 5 and not is_trading_day(now):
        return "HOLIDAY"
    if not is_trading_day(now):
        return "MARKET_CLOSED"
    if MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME:
        return "OPEN"
    return "MARKET_CLOSED"


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
LIVE_TRADING_KILL_SWITCH_FILE = str(SCRIPT_DIR / "LIVE_TRADING_KILL")

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
ALGO_STATUS_POLL_SECONDS = int(os.environ.get("ALGO_STATUS_POLL_SECONDS", "5"))
LAST_ALGO_STATUS = None
# Most recent non-clean PositionReconciler.check(), broadcast as
# reconciliationAlert and handed to new connections so a dashboard opened
# after a mismatch still sees it.
LAST_RECONCILIATION_ALERT = None
# Cache of the most recent live position-book fetch (reconcile_loop's own
# periodic call). _build_algo_status() reads this for current open lots
# instead of making its own broker call every 5s; the pre-trade exposure
# check still fetches fresh. None until live trading is enabled and the
# first reconcile cycle has completed.
LAST_LIVE_POSITIONS = None

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


# option_chain_json keeps its runtime config as plain module globals mutated
# before each main() call. Both the primary tick and any other pipeline call
# run via asyncio.to_thread, so every pipeline call must hold this lock for
# its full duration or two symbols' runs would stomp on each other's globals.
_PIPELINE_LOCK = asyncio.Lock()
INDEX_QUOTES = {}  # {"BANKNIFTY": {"spot":.., "spotChgPct":..}, ...}
_SYMBOL_SWITCH_EVENT = asyncio.Event()
# Set (thread-safely) by TickAggregator's flush loop on every real tick
# flush. engine_loop() waits on this OR _SYMBOL_SWITCH_EVENT, bounded by
# MIN_TICK_RECOMPUTE_SECONDS (floor) and POLL_SECONDS (ceiling).
_TICK_ACTIVITY_EVENT = asyncio.Event()
# Serializes the canonical full/delta stream and its backing snapshots.
# compute_diff runs in a worker thread; without this lock the async tick
# path could mutate _LAST_SENT/LAST_PAYLOAD mid-traversal. New-client
# snapshot handoff uses the same lock.
_MARKET_STREAM_LOCK = asyncio.Lock()

# Real-export capture seam: run_pipeline_once() reads the dashboard payload
# back out of mTerminals_json's own export, so the pipeline and the WS
# stream share one serialization path.
_REAL_EXPORT = mTerminals_json.export_dashboard_json
_CAPTURED = {}


def _capturing_export(*args, **kwargs):
    kwargs["out_path"] = "mTerminals.json"
    result = _REAL_EXPORT(*args, **kwargs)
    if result is None:
        try:
            with open("mTerminals.json") as f:
                result = json.load(f)
        except Exception:
            pass
    _CAPTURED["payload"] = result
    return result


mTerminals_json.export_dashboard_json = _capturing_export


# ── task plumbing ────────────────────────────────────────────────────────
def _background_task_done(task: asyncio.Task, task_name: str):
    """Retain detached tasks and surface unexpected subsystem exits."""
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "background task failed: %s",
            task_name,
            extra={
                "event": "background_task.failed",
                "subsystem": task_name,
                "status": "failed",
                "reason": str(exc),
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def _create_background_task(awaitable, task_name: str) -> asyncio.Task:
    task = asyncio.create_task(awaitable, name=task_name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(lambda done: _background_task_done(done, task_name))
    return task


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
    async with _PIPELINE_LOCK:
        return await asyncio.to_thread(run_pipeline_once)


async def _publish_pipeline_status(status, reason="", elapsed=None):
    """Broadcast analytics availability only when its visible state changes."""
    previous = (_PIPELINE_STATUS.get("status"), _PIPELINE_STATUS.get("reason"))
    _PIPELINE_STATUS["status"] = status
    _PIPELINE_STATUS["reason"] = reason
    _PIPELINE_STATUS["elapsedSeconds"] = (
        round(elapsed, 3) if elapsed is not None else None
    )
    if status == "LIVE":
        _PIPELINE_STATUS["lastSuccessAt"] = datetime.now().astimezone().isoformat()
    if (status, reason) != previous:
        await broadcast({"type": "pipelineStatus", "payload": dict(_PIPELINE_STATUS)})


async def broadcast(message):
    global _BASELINE_SEQ, _BASELINE_ID
    if isinstance(message, dict) and message.get("type") == "full":
        _BASELINE_SEQ += 1
        payload = message.get("payload") or {}
        _BASELINE_ID = (
            f"{payload.get('symbol', '')}:{payload.get('expiry', '')}:{_BASELINE_SEQ}"
        )
        message = {**message, "version": _BASELINE_ID}
    elif isinstance(message, dict) and message.get("type") == "delta":
        if _BASELINE_ID is None:
            print(
                "[ws] dropping delta without an established full-snapshot baseline",
                flush=True,
            )
            return
        message = {**message, "baseVersion": _BASELINE_ID}
    msg_str = orjson.dumps(message, default=_json_default).decode()
    await _DASHBOARD_CLIENTS.broadcast(
        msg_str, on_error=lambda error: print(f"[ws] Error broadcasting: {error}")
    )


# Dashboard-relay protocol and its independent cache/poll loop live in
# server.bridge. The live coordinator supplies only current process state.
_BRIDGE = DashboardBridge(
    state=lambda: {
        "symbol": SYMBOL,
        "futures_expiry": FUTURES_EXPIRY,
        "use_smartapi": USE_SMARTAPI,
        "last_payload": LAST_PAYLOAD,
        "index_quotes": INDEX_QUOTES,
    },
    origin_allowed=_origin_allowed,
    json_default=_json_default,
    market_api=market_api,
)
BRIDGE_CONNECTED = _BRIDGE.clients


async def broadcast_bridge(payload):
    await _BRIDGE.broadcast(payload)


async def bridge_ws_handler(request):
    return await _BRIDGE.handle(request)


async def bridge_loop():
    await _BRIDGE.run()


def _configure_pipeline_globals(
    symbol,
    expiry=None,
    no_extra_chains=None,
    strict_expiry=None,
    no_virtual_oi=None,
    price_source=None,
    futures_expiry=None,
):
    """Point option_chain_json's runtime config at `symbol` via
    set_runtime_config() (see pipeline_config.py). Used only by
    run_pipeline_once() for the primary --symbol's full run — the ticker
    strip no longer goes through option_chain_json at all.

    Re-pushes the RUNTIME data source into option_chain_json's use_smartapi
    gate and brokers.market_data's active-provider facade, so a ?dataSource=
    switch takes effect on the very next pass. No longer pokes `exchange` —
    option_chain_json.main() recomputes it locally from SYMBOL (see
    pipeline_config.py's docstring); the local below only picks the right
    EXPIRY fallback (BSE vs NSE nearest-expiry rule)."""
    _md_set_active_provider(DATA_SOURCE)
    exchange = "BSE" if symbol in _BSE_SYMBOLS else "NSE"
    resolved_expiry = expiry or (
        option_chain_json.BSE_EXPIRY_DEFAULT.get(
            symbol, option_chain_json._nearest_Thursday
        )()
        if exchange == "BSE"
        else option_chain_json._nearest_Tuesday()
    )
    option_chain_json.set_runtime_config(
        RuntimeConfig(
            symbol=symbol,
            expiry=resolved_expiry,
            no_extra_chains=no_extra_chains,
            strict_expiry=strict_expiry,
            no_virtual_oi=no_virtual_oi,
            price_source=price_source,
            futures_expiry=futures_expiry,
            use_smartapi=(DATA_SOURCE != "NSE_BSE"),
        )
    )


def run_pipeline_once():
    _configure_pipeline_globals(
        SYMBOL,
        EXPIRY,
        no_extra_chains=not ARGS.extra_chains,
        strict_expiry=ARGS.strict_expiry,
        no_virtual_oi=ARGS.no_virtual_oi,
        price_source=PRICE_SOURCE,
        futures_expiry=FUTURES_EXPIRY,
    )
    _CAPTURED.clear()
    try:
        option_chain_json.main()
    except Exception as e:
        print(f"[pipeline] FAILED: {e}")
        return None
    return _CAPTURED.get("payload")


def _index_quote_fetcher():
    """Build from the current provider seam (also keeps runtime switches live)."""
    return IndexQuoteFetcher(
        state=lambda: {
            "data_source": DATA_SOURCE,
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
_MAIN_LOOP = None  # the asyncio loop main() runs on; lets a runtime switch
# to a provider whose feed was never started at boot start that feed on the
# live loop instead of silently doing nothing.

_smartapi_stream = None
_smartapi_aggregator = None
_smartapi_loop = None  # captured once at startup, reused for symbol switches
_smartapi_exchange = None  # exchange type subscribed (NFO/BFO), for unsubscribe
_smartapi_tokens = None  # token list subscribed, for unsubscribe
_smartapi_current_expiry = None  # expiry being streamed, e.g. "31JUL2026"
_smartapi_index_token = None  # underlying INDEX token for fast spot ticks
_smartapi_index_exchange = None  # "NSE_CM"/"BSE_CM" — DIFFERENT from
# _smartapi_exchange (NFO/BFO), needs its own unsubscribe call
_smartapi_futures_token = None  # current-month futures token (VWAP/volume)
_smartapi_futures_exchange = None  # NFO/BFO; folded into _smartapi_tokens
# for unsubscribe but needs its own basis-calc lookup

_upstox_stream = None
_upstox_aggregator = None
_upstox_loop = None
_upstox_keys = None  # instrument_key list subscribed, for unsubscribe
_upstox_current_expiry = None  # ISO 'YYYY-MM-DD' expiry being streamed

_shoonya_stream = None
_shoonya_aggregator = None
_shoonya_loop = None
_shoonya_instruments = None  # 'EXCH|TOKEN' strings subscribed, for unsubscribe
_shoonya_current_expiry = None  # 'DD-Mon-YYYY' expiry being streamed


def _smartapi_feed_state():
    return _SmartApiFeedState(
        stream=_smartapi_stream,
        aggregator=_smartapi_aggregator,
        loop=_smartapi_loop,
        exchange=_smartapi_exchange,
        tokens=_smartapi_tokens,
        current_expiry=_smartapi_current_expiry,
        index_token=_smartapi_index_token,
        index_exchange=_smartapi_index_exchange,
        futures_token=_smartapi_futures_token,
        futures_exchange=_smartapi_futures_exchange,
    )


def _store_smartapi_feed_state(state):
    global _smartapi_stream, _smartapi_aggregator, _smartapi_loop
    global _smartapi_exchange, _smartapi_tokens, _smartapi_current_expiry
    global _smartapi_index_token, _smartapi_index_exchange
    global _smartapi_futures_token, _smartapi_futures_exchange
    _smartapi_stream = state.stream
    _smartapi_aggregator = state.aggregator
    _smartapi_loop = state.loop
    _smartapi_exchange = state.exchange
    _smartapi_tokens = state.tokens
    _smartapi_current_expiry = state.current_expiry
    _smartapi_index_token = state.index_token
    _smartapi_index_exchange = state.index_exchange
    _smartapi_futures_token = state.futures_token
    _smartapi_futures_exchange = state.futures_exchange


def _upstox_feed_state():
    return _UpstoxFeedState(
        stream=_upstox_stream,
        aggregator=_upstox_aggregator,
        loop=_upstox_loop,
        instruments=_upstox_keys,
        current_expiry=_upstox_current_expiry,
    )


def _store_upstox_feed_state(state):
    global _upstox_stream, _upstox_aggregator, _upstox_loop
    global _upstox_keys, _upstox_current_expiry
    _upstox_stream = state.stream
    _upstox_aggregator = state.aggregator
    _upstox_loop = state.loop
    _upstox_keys = state.instruments
    _upstox_current_expiry = state.current_expiry


def _shoonya_feed_state():
    return _ShoonyaFeedState(
        stream=_shoonya_stream,
        aggregator=_shoonya_aggregator,
        loop=_shoonya_loop,
        instruments=_shoonya_instruments,
        current_expiry=_shoonya_current_expiry,
    )


def _store_shoonya_feed_state(state):
    global _shoonya_stream, _shoonya_aggregator, _shoonya_loop
    global _shoonya_instruments, _shoonya_current_expiry
    _shoonya_stream = state.stream
    _shoonya_aggregator = state.aggregator
    _shoonya_loop = state.loop
    _shoonya_instruments = state.instruments
    _shoonya_current_expiry = state.current_expiry


def _parse_any_expiry(expiry_str):
    """Normalize an expiry string to a date, accepting SmartAPI's format
    ('31JUL2026'), option_chain_json's ('31-Jul-2026'), Upstox's ISO
    ('2026-07-31'), or Shoonya's ('DD-Mon-YYYY'). None if none match."""
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _matches_current_feed_expiry(current_expiry, payload_expiry_str):
    """True only if the expiry the feed is streaming is the SAME expiry the
    dashboard is displaying right now. The feed's own expiry is picked
    independently of option_chain_json's EXPIRY global and they aren't
    guaranteed to agree (NEAR/MONTHLY tab active, etc.); merging ticks for
    the wrong expiry would silently show the wrong contract's prices, so
    this gate must pass before any state merge."""
    return _matches_displayed_expiry(
        current_expiry, payload_expiry_str, _parse_any_expiry
    )


def _smartapi_feed_matches_displayed_expiry(payload_expiry_str):
    return _matches_current_feed_expiry(_smartapi_current_expiry, payload_expiry_str)


def _upstox_feed_matches_displayed_expiry(payload_expiry_str):
    return _matches_current_feed_expiry(_upstox_current_expiry, payload_expiry_str)


def _shoonya_feed_matches_displayed_expiry(payload_expiry_str):
    return _matches_current_feed_expiry(_shoonya_current_expiry, payload_expiry_str)


def _print_log(message):
    print(message, flush=True)


def _resolve_chain_tokens(target_symbol, strikes_around_atm, expiry=None):
    return _resolve_smartapi_feed_tokens(
        target_symbol,
        strikes_around_atm,
        expiry,
        market_data=market_data,
        is_bse=lambda symbol: symbol in _BSE_SYMBOLS,
        parse_expiry=_parse_any_expiry,
        resolve_futures=_resolve_futures_token,
        report=_print_log,
    )


def _resolve_futures_token(target_symbol, exchange):
    """Resolves target_symbol's current-month futures (exchange, token) for
    the SmartAPI feed's VWAP/volume subscription — SEPARATE from
    _resolve_live_order_token()'s FUT branch (read-only subscription,
    lower stakes). NOT WIRED YET: smartapi_client exposes neither a
    FUTURES_TOKENS dict nor a find_future_token(); returns (None, None)
    until that's resolved — subscription code treats None as "skip"."""
    return None, None


def _resolve_upstox_chain_tokens(target_symbol, strikes_around_atm, expiry=None):
    return _resolve_upstox_feed_tokens(
        target_symbol,
        strikes_around_atm,
        expiry,
        is_bse=lambda symbol: symbol in _BSE_SYMBOLS,
        parse_expiry=_parse_any_expiry,
        report=_print_log,
    )


def _resolve_shoonya_chain_tokens(target_symbol, strikes_around_atm, expiry=None):
    """Builds the 'EXCH|TOKEN' subscribe-string set for target_symbol.
    Talks to brokers/shoonya_market_data.py directly (not through the
    market_data singleton) so the feed works even when MARKET_DATA_PROVIDER
    points the singleton elsewhere. Keyed by 'EXCH|TOKEN' — what
    ShoonyaTickStream.subscribe() expects and what its ticks report back as
    `token` after stripping the exchange prefix."""
    return _resolve_shoonya_feed_tokens(
        target_symbol,
        strikes_around_atm,
        expiry,
        lambda symbol: symbol in _BSE_SYMBOLS,
        _parse_any_expiry,
        _print_log,
    )


# ── live-tick merge/broadcast (shared by all providers) ──────────────────
async def _sync_live_feed_and_broadcast(provider, message, matches_expiry_fn):
    """Apply a normalized provider tick only while that provider is active."""
    if not _feed_allowed(provider):
        return
    async with _MARKET_STREAM_LOCK:
        await _live_feed_sync_and_broadcast_locked(message, matches_expiry_fn)


async def _smartapi_sync_and_broadcast(message):
    """Compatibility callback for SmartAPI's normalized tick stream."""
    await _sync_live_feed_and_broadcast(
        "SMARTAPI", message, _smartapi_feed_matches_displayed_expiry
    )


async def _upstox_sync_and_broadcast(message):
    """Upstox analog — same shared merge logic, gated on Upstox's own expiry
    tracker. Feeds are mutually exclusive (LIVE_FEED_PROVIDER picks one);
    _MARKET_STREAM_LOCK serializes regardless."""
    await _sync_live_feed_and_broadcast(
        "UPSTOX", message, _upstox_feed_matches_displayed_expiry
    )


async def _shoonya_sync_and_broadcast(message):
    """Shoonya analog — same shared merge logic, gated on Shoonya's own
    expiry tracker ('DD-Mon-YYYY')."""
    await _sync_live_feed_and_broadcast(
        "SHOONYA", message, _shoonya_feed_matches_displayed_expiry
    )


async def _live_feed_sync_and_broadcast_locked(message, matches_expiry_fn):
    """Merge a live tick delta into LAST_PAYLOAD/_LAST_SENT before
    broadcasting it.

    Without the merge, a newly-connecting client's "full" snapshot would
    miss whatever the feed already pushed to existing clients, and the next
    engine_loop tick could re-broadcast an older NSE-polled value over a
    fresher feed tick (visible flicker backward). If the feed's expiry
    doesn't match what's displayed, the chain portion of the delta is
    STRIPPED before broadcasting: applyDelta() merges keyed chain rows by
    strike alone with no concept of expiry, and strikes overlap heavily
    across expiries, so a stale-expiry row would corrupt the displayed
    contract's LTP/OI. That window opens right after a switch —
    LAST_PAYLOAD clears immediately but the background unsubscribe hasn't
    finished. Spot isn't expiry-tied and still broadcasts every time.

    The paper-trading fast path below fires off the same fresh prices the
    client just received (throttled by PORTFOLIO_POLL_SECONDS to avoid
    flooding clients during tick bursts), including a pending-LIMIT check
    so fills don't lag the feed by --poll-seconds."""
    global LAST_PAYLOAD_AT
    feed_update_applied = False
    try:
        message, feed_update_applied = merge_live_feed_update(
            message, LAST_PAYLOAD, _LAST_SENT, matches_expiry_fn,
            price_source=PRICE_SOURCE,
        )
    except Exception as e:
        # Sync is best-effort consistency — never let a sync bug block the
        # tick from reaching clients.
        print(f"[live-feed] state sync failed (broadcasting anyway): {e}", flush=True)

    if feed_update_applied and LAST_PAYLOAD is not None:
        LAST_PAYLOAD_AT = datetime.now().astimezone()
    await broadcast(message)

    global _LAST_PORTFOLIO_BROADCAST_TS
    now_ts = time.monotonic()
    if now_ts - _LAST_PORTFOLIO_BROADCAST_TS >= PORTFOLIO_POLL_SECONDS:
        _LAST_PORTFOLIO_BROADCAST_TS = now_ts
        try:
            current_prices = _build_current_prices(LAST_PAYLOAD)
            PT_ENGINE.check_pending_orders(current_prices)
            await _broadcast_portfolio(current_prices)
        except Exception as e:
            # A paper-trading hiccup must never take down the feed.
            print(
                f"[paper-trading] fast-path portfolio broadcast failed: {e}",
                flush=True,
            )


# ── provider feed adapters + managers ────────────────────────────────────
# One BrokerFeedManager per provider replaces the three previously
# copy-pasted start/switch/restart/stop blocks. The RLock-per-manager (see
# server/feed_manager.py) closes the startup-vs-switch race that could
# orphan a second socket on single-session brokers.


def _smartapi_feed_start(state, loop, symbol, strikes_around_atm, expiry):
    _start_smartapi_feed_new(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        resolve=_resolve_chain_tokens,
        aggregator_factory=TickAggregator,
        callback=_smartapi_sync_and_broadcast,
        tick_event=_TICK_ACTIVITY_EVENT,
        stream_factory=SmartTickStream,
        exchange_types=EXCHANGE_TYPE,
        spawn_thread=threading.Thread,
        wait=time.sleep,
        report=_print_log,
    )


def _smartapi_feed_switch(state, symbol, strikes_around_atm, expiry):
    _switch_smartapi_feed_existing(
        state,
        symbol,
        strikes_around_atm,
        expiry,
        resolve=_resolve_chain_tokens,
        exchange_types=EXCHANGE_TYPE,
        report=_print_log,
    )


def _smartapi_feed_stop(state):
    _stop_smartapi_feed(
        state, exchange_types=EXCHANGE_TYPE, report=lambda m: logger.warning(m)
    )


def _upstox_feed_start(state, loop, symbol, strikes_around_atm, expiry):
    from brokers.upstox_ws_client import UpstoxTickStream

    _start_upstox_feed_new(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        _resolve_upstox_chain_tokens,
        TickAggregator,
        _upstox_sync_and_broadcast,
        _TICK_ACTIVITY_EVENT,
        UpstoxTickStream,
        threading.Thread,
        time.sleep,
        _print_log,
    )


def _upstox_feed_switch(state, symbol, strikes_around_atm, expiry):
    _switch_upstox_feed_existing(
        state,
        symbol,
        strikes_around_atm,
        expiry,
        _resolve_upstox_chain_tokens,
        _print_log,
    )


def _upstox_feed_stop(state):
    _stop_upstox_feed(state, report=lambda m: logger.warning(m))


def _shoonya_feed_start(state, loop, symbol, strikes_around_atm, expiry):
    from brokers.shoonya_ws_client import ShoonyaTickStream

    _start_shoonya_feed_new(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        _resolve_shoonya_chain_tokens,
        TickAggregator,
        _shoonya_sync_and_broadcast,
        _TICK_ACTIVITY_EVENT,
        ShoonyaTickStream,
        threading.Thread,
        time.sleep,
        _print_log,
    )


def _shoonya_feed_switch(state, symbol, strikes_around_atm, expiry):
    _switch_shoonya_feed_existing(
        state,
        symbol,
        strikes_around_atm,
        expiry,
        _resolve_shoonya_chain_tokens,
        _print_log,
    )


def _shoonya_feed_stop(state):
    _stop_shoonya_feed(state, report=lambda m: logger.warning(m))


_FEEDS = {
    provider: BrokerFeedManager(
        provider,
        snapshot=snapshot,
        store=store,
        start=start,
        switch=switch,
        stop=stop,
        default_symbol=lambda: SYMBOL,
        main_loop=lambda: _MAIN_LOOP,
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
    )
}


# Legacy entry points, kept as thin wrappers — existing tests and external
# callers seam through these names.
def start_smartapi_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    _FEEDS["SMARTAPI"].start(loop, underlying, strikes_around_atm, expiry)


def _switch_smartapi_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    _FEEDS["SMARTAPI"].switch_blocking(new_symbol, strikes_around_atm, expiry)


def restart_smartapi_feed(new_symbol, new_expiry=None):
    _FEEDS["SMARTAPI"].restart(new_symbol, new_expiry)


def _stop_smartapi_feed_blocking():
    _FEEDS["SMARTAPI"].stop_blocking()


def start_upstox_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    _FEEDS["UPSTOX"].start(loop, underlying, strikes_around_atm, expiry)


def _switch_upstox_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    _FEEDS["UPSTOX"].switch_blocking(new_symbol, strikes_around_atm, expiry)


def restart_upstox_feed(new_symbol, new_expiry=None):
    _FEEDS["UPSTOX"].restart(new_symbol, new_expiry)


def _stop_upstox_feed_blocking():
    _FEEDS["UPSTOX"].stop_blocking()


def start_shoonya_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    _FEEDS["SHOONYA"].start(loop, underlying, strikes_around_atm, expiry)


def _switch_shoonya_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    _FEEDS["SHOONYA"].switch_blocking(new_symbol, strikes_around_atm, expiry)


def restart_shoonya_feed(new_symbol, new_expiry=None):
    _FEEDS["SHOONYA"].restart(new_symbol, new_expiry)


def _stop_shoonya_feed_blocking():
    _FEEDS["SHOONYA"].stop_blocking()


# ── feed orchestration dispatch (broker-neutral) ─────────────────────────
def _restart_live_feed(provider: str, symbol: str, expiry=None) -> bool:
    """Schedule the active provider's existing feed for a symbol switch.
    Socket lifecycle remains provider-native; every orchestration call site
    uses this broker-neutral dispatch rather than duplicating a provider
    branch."""
    return _feed_lifecycle.restart(
        provider, symbol, expiry, {k: m.restart for k, m in _FEEDS.items()}
    )


def _start_live_feed(provider: str, loop) -> bool:
    """Offload the configured provider's blocking feed startup."""
    return _feed_lifecycle.start(
        provider,
        loop,
        {k: m.start for k, m in _FEEDS.items()},
        lambda start_callback, start_loop, task_name: _create_background_task(
            asyncio.to_thread(start_callback, start_loop), task_name
        ),
    )


def _feed_allowed(feed_provider: str) -> bool:
    """Whether ticks from the given broker feed may still merge/broadcast.

    False after a runtime DATA SOURCE switch away from feed_provider, or
    when the active source is polling-only (KITE/BREEZE/KOTAK/NSE_BSE — no
    WebSocket feed in this codebase). Every *_sync_and_broadcast() gates on
    this BEFORE touching LAST_PAYLOAD/_LAST_SENT, so a feed left running
    after a switch can't contaminate the new provider's baseline."""
    return _feed_lifecycle.is_allowed(
        feed_provider, DATA_SOURCE, _provider_supports_websocket
    )


def _stop_active_broker_feed(provider: str) -> bool:
    """Best-effort unsubscribe when deactivating a streaming provider.
    The synchronous _feed_allowed gate remains authoritative for stopping
    payloads; this cleanup releases broker subscription bandwidth."""
    return _feed_lifecycle.stop(
        provider,
        {k: m.stop_blocking for k, m in _FEEDS.items()},
        lambda callback: threading.Thread(target=callback, daemon=True).start(),
    )


def switch_symbol(new_symbol, new_expiry=None):
    """Runtime symbol switch — triggered by ws_handler() on ?symbol=/?expiry=
    (dashboard.js switchActiveIndex()). Changes what the NEXT engine_loop
    tick fetches; fetches nothing itself.

    EXPIRY resets to None (auto-resolve) unless given, since the old
    symbol's expiry string is almost never valid for the new one.
    LAST_PAYLOAD/_LAST_SENT are cleared so the next tick is a "full"
    broadcast (diffing two symbols' payloads is just the new payload with
    extra work) and a mid-switch client never gets handed the OLD symbol's
    snapshot. Pokes _SYMBOL_SWITCH_EVENT so engine_loop wakes immediately
    instead of finishing out its --poll-seconds sleep.

    Process-wide, not per-client: one engine loop backs all of CONNECTED,
    so one browser tab switching symbols switches it for all of them."""
    global SYMBOL, EXPIRY, LAST_PAYLOAD, _LAST_SENT
    # Defensive normalization: a stale/cached frontend can send the symbol
    # still percent-encoded (aiohttp decodes once, leaving e.g.
    # "ZYDUS%20LIFESCIENCES%20LTD"); undo that so the engine never probes a
    # literal "%20" symbol. No-op for clean input.
    new_symbol = unquote(new_symbol).strip().upper()
    if new_symbol == SYMBOL and (new_expiry is None or new_expiry == EXPIRY):
        return  # already on this symbol+expiry
    print(f"[ws] symbol switch requested: {SYMBOL} -> {new_symbol}", flush=True)
    SYMBOL = new_symbol
    EXPIRY = new_expiry
    LAST_PAYLOAD = None
    _LAST_SENT = None
    _SYMBOL_SWITCH_EVENT.set()
    if USE_SMARTAPI:
        _restart_live_feed(LIVE_FEED_PROVIDER, new_symbol, new_expiry)


async def switch_data_source(new_source: str):
    """(docstring unchanged, plus:) The provider-facade flip below is
    serialized under _PIPELINE_LOCK — the same lock _run_pipeline_locked()
    holds for a full pass. A pass reads the active provider twice (expiry
    resolution at entry, batch quotes inside fetch_option_chain_wide);
    flipping the facade between those reads mixes two providers' identity
    spaces (SmartAPI tokens → Upstox 400 UDAPI1087, or zero matched
    quotes → empty chain frame → KeyError 'StrikePrice'). Worst case the
    switching client's handshake waits out one pass."""
    global DATA_SOURCE, LAST_PAYLOAD, _LAST_SENT
    new_source = (new_source or "").strip().upper()
    if new_source not in _MD_PROVIDER_KEYS:
        print(
            f"[data-source] rejecting invalid data source {new_source!r} "
            f"(valid: {sorted(_MD_PROVIDER_KEYS)})",
            flush=True,
        )
        raise ValueError(
            f"Unknown data source {new_source!r}. Valid: {sorted(_MD_PROVIDER_KEYS)}"
        )
    if new_source == DATA_SOURCE:
        return  # already on this source, nothing to do
    old_source = DATA_SOURCE
    print(f"[data-source] switch requested: {old_source} -> {new_source}", flush=True)

    # Serialized against in-flight pipeline passes (see docstring).
    try:
        async with _PIPELINE_LOCK:
            switched = _md_set_active_provider(new_source)
    except Exception as exc:
        print(
            f"[data-source] switch to {new_source} failed; "
            f"remaining on {old_source}: {exc}",
            flush=True,
        )
        return False
    if not switched:
        print(
            f"[data-source] {new_source} unavailable; remaining on {old_source}",
            flush=True,
        )
        return False

    # Everything below touches no pipeline-visible state ordering.
    _stop_active_broker_feed(old_source)
    DATA_SOURCE = new_source
    LAST_PAYLOAD = None
    _LAST_SENT = None
    if _provider_supports_websocket(new_source):
        _restart_live_feed(new_source, SYMBOL, EXPIRY)
    _SYMBOL_SWITCH_EVENT.set()
    print(f"[data-source] switched to {new_source}", flush=True)
    return True


# ── paper-trading pricing ────────────────────────────────────────────────
def _build_current_prices(payload):
    """Build the {instrument_key: ltp} map paper_trading.py's
    check_pending_orders()/mark_to_market()/place_order() expect, from the
    SAME tick payload the dashboard renders — the paper engine is always
    priced off exactly what the user sees on screen, never a stale/separate
    fetch."""
    prices = {}
    if not payload:
        return dict(_LAST_KNOWN_LEG_PRICES)
    symbol = payload.get("symbol")
    if not symbol:
        return dict(_LAST_KNOWN_LEG_PRICES)

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
    _LAST_KNOWN_LEG_PRICES.update(prices)
    return {**_LAST_KNOWN_LEG_PRICES, **prices}


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
    unchanged. Prices MARKET orders off LAST_PAYLOAD (the tick already on
    screen), so the fill the user sees matches the LTP they clicked. Always
    re-broadcasts portfolio + orders afterward.

    Returns a status dict on EVERY path so _submit_auto_order() can tell a
    downstream rejection from an actual placement."""
    intent = parse_order_intent(payload)
    validation_reason = validate_order_intent(intent)
    if validation_reason:
        print(f"[order] REJECTED malformed intent: {validation_reason}", flush=True)
        current_prices = _build_current_prices(LAST_PAYLOAD)
        await _broadcast_portfolio(current_prices)
        return {"status": "rejected", "reason": validation_reason}

    current_prices = _build_current_prices(LAST_PAYLOAD)

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
    # can show "current / limit". Sourced from LAST_LIVE_POSITIONS (the
    # reconcile loop's periodic fetch) — this is a status display, not the
    # pre-trade exposure check (which still fetches fresh).
    try:
        guard_status["current_open_lots"] = (
            open_lots_from_positions(LAST_LIVE_POSITIONS, PT_LOT_SIZES)
            if LAST_LIVE_POSITIONS is not None
            else None
        )
    except Exception as e:
        print(
            f"[algo-status] could not compute open lots from cached positions: {e}",
            flush=True,
        )
        guard_status["current_open_lots"] = None

    exec_status = _AUTO_EXECUTOR.get_status(SYMBOL)
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
        "symbol": SYMBOL,
    }


async def _broadcast_reconciliation_alert(result, source: str):
    """Turns a non-clean PositionReconciler.check() result into a
    reconciliationAlert broadcast (previously log-only, so a human watching
    the dashboard never saw below-trip-threshold mismatches — most resolve
    themselves next cycle once a fill propagates, but they should still be
    visible as they happen). No-op on a clean result. `source` distinguishes
    the fast post-fill check from the periodic sweep, for display context."""
    global LAST_RECONCILIATION_ALERT
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
    LAST_RECONCILIATION_ALERT = payload
    await broadcast({"type": "reconciliationAlert", "payload": payload})


# ── background pollers ───────────────────────────────────────────────────
def _set_last_funds(value):
    global LAST_FUNDS
    LAST_FUNDS = value


def _set_last_live_positions(value):
    global LAST_LIVE_POSITIONS
    LAST_LIVE_POSITIONS = value


def _set_last_algo_status(value):
    global LAST_ALGO_STATUS
    LAST_ALGO_STATUS = value


# Pushes {"type":"indexQuotes",...}; dashboard.js's generic handler lands it
# at wsState.indexQuotes, which paper-trading.js reads once Live mode is on.
# (VIX is never the active SYMBOL, so it's always included in "others".)
_INDEX_QUOTE_LOOP = IndexQuoteLoop(
    enabled=USE_INDEX_QUOTES,
    symbols=INDEX_TICKER_SYMBOLS,
    active_symbol=lambda: SYMBOL,
    get_spot_quote=market_data.get_spot_quote,
    broadcast=broadcast,
    index_quotes=INDEX_QUOTES,
    poll_seconds=INDEX_QUOTE_SECONDS,
    report=_print_log,
)


async def index_quote_loop():
    await _INDEX_QUOTE_LOOP.run()


# Pushes {"type":"funds",...}; dashboard.js's generic handler lands it at
# wsState.funds, which paper-trading.js reads once Live mode is on.
_FUNDS_POLLER = FundsPoller(
    get_funds=smartapi_get_funds,
    broadcast=broadcast,
    set_last_funds=_set_last_funds,
    poll_seconds=FUNDS_POLL_SECONDS,
    spawn_task=_create_background_task,
    report=_print_log,
)


def start_funds_polling():
    _FUNDS_POLLER.start()


def stop_funds_polling():
    _FUNDS_POLLER.stop()


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


async def reconcile_loop():
    await _RECONCILIATION_LOOP.run()


_ALGO_STATUS_LOOP = AlgoStatusLoop(
    build_status=lambda: _build_algo_status(),
    broadcast=broadcast,
    set_last_status=_set_last_algo_status,
    poll_seconds=ALGO_STATUS_POLL_SECONDS,
    report=_print_log,
)


async def algo_status_loop():
    await _ALGO_STATUS_LOOP.run()


# ── node relay ───────────────────────────────────────────────────────────
_NODE_RELAY = NodeRelay(enabled=USE_RELAY, report=_print_log)


async def _post_to_node(payload: dict):
    await _NODE_RELAY.post(payload)


# ── engine loop ──────────────────────────────────────────────────────────
def _live_aggregators() -> dict:
    """Non-None per-provider tick aggregators, keyed by log tag."""
    aggregators = {}
    for tag, manager in (
        ("smartapi", _FEEDS["SMARTAPI"]),
        ("upstox", _FEEDS["UPSTOX"]),
        ("shoonya", _FEEDS["SHOONYA"]),
    ):
        aggregator = manager._snapshot().aggregator
        if aggregator is not None:
            aggregators[tag] = aggregator
    return aggregators


def _reset_daily_sessions(now: datetime):
    """New trading day → reset OI session baselines so yesterday's
    session-open OI doesn't leak into today's changeinOpenInterest. Same
    per-day-flag pattern as the EOD job."""
    global _LAST_SESSION_DATE
    if _LAST_SESSION_DATE == now.date():
        return
    _LAST_SESSION_DATE = now.date()
    for tag, aggregator in _live_aggregators().items():
        aggregator.reset_session()
        print(
            f"[{tag}] Reset OI session baseline for new trading day {now.date()}",
            flush=True,
        )
    # Futures OI session baseline (Market Regime input) — a separate tracker
    # from the option-chain aggregators (see oi/futures_oi_tracker.py's
    # class docstring for why).
    _get_futures_oi_tracker().reset_session()
    print(
        f"[futures_oi] Reset futures OI session baseline for new trading day {now.date()}",
        flush=True,
    )


def _maybe_trigger_eod(now: datetime):
    """Fire the once-per-trading-day EOD fetch (participant OI) and the
    cash-market FII/DII flow fetch — separate tasks/callbacks so one NSE
    endpoint's failure isn't conflated with the other's reporting."""
    global _EOD_DONE_DATE
    if not (
        is_trading_day(now)
        and now.time() >= EOD_TRIGGER_TIME
        and _EOD_DONE_DATE != now.date()
    ):
        return
    # Date flag set BEFORE awaiting, so a slow fetch can't double-fire.
    _EOD_DONE_DATE = now.date()
    print(f"[eod] triggering EOD fetch for {now.date()}", flush=True)
    eod_task = asyncio.create_task(
        asyncio.to_thread(fetch_all_eod, now, True)  # save=True
    )
    eod_task.add_done_callback(_eod_task_done)
    flow_task = asyncio.create_task(asyncio.to_thread(record_today_flow))
    flow_task.add_done_callback(_flow_task_done)


async def _collect_pipeline_payload(tick_start: float):
    """Await the in-flight pipeline pass, shielded, and publish status.

    shield() matters: a timeout must not cancel to_thread's worker, which
    can't be stopped safely and may be updating the pipeline's module-level
    runtime configuration. On timeout the task is RETAINED and collected on
    a later loop instead of starting an overlapping REST pass."""
    global _PIPELINE_TASK
    if _PIPELINE_TASK is None:
        _PIPELINE_STATUS["startedAt"] = datetime.now().astimezone().isoformat()
        _PIPELINE_TASK = asyncio.create_task(_run_pipeline_locked())
    try:
        payload = await asyncio.wait_for(
            asyncio.shield(_PIPELINE_TASK), timeout=PIPELINE_TIMEOUT_SECONDS
        )
        _PIPELINE_TASK = None
        await _publish_pipeline_status("LIVE")
        return payload
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - tick_start
        await _publish_pipeline_status(
            "DELAYED",
            (
                f"REST analytics pass exceeded {PIPELINE_TIMEOUT_SECONDS:g}s; live prices continue via WebSocket"
                if USE_SMARTAPI
                else f"Public REST analytics pass exceeded {PIPELINE_TIMEOUT_SECONDS:g}s; SmartAPI remains disabled"
            ),
            elapsed,
        )
        overlay_state = (
            f"{LIVE_FEED_PROVIDER} websocket overlay remains active"
            if USE_SMARTAPI and _feed_allowed(LIVE_FEED_PROVIDER)
            else f"{DATA_SOURCE} REST polling will retry"
        )
        print(f"[pipeline] DELAYED after {elapsed:.2f}s — {overlay_state}", flush=True)
        return None
    except Exception as e:
        _PIPELINE_TASK = None
        await _publish_pipeline_status("DELAYED", f"Analytics pipeline failed: {e}")
        print(f"[pipeline] FAILED: {e}", flush=True)
        return None


def _seed_oi_baselines(payload):
    """Feed NSE's authoritative changeinOpenInterest into every live feed
    aggregator's OI baselines each cycle — not just at feed startup, since
    start_*_feed() runs concurrently with engine_loop via asyncio.to_thread
    and there's no guaranteed ordering. seed_session_baseline() only fills
    tokens without an existing baseline, so calling this every cycle is
    safe — previously only SmartAPI was seeded and Upstox/Shoonya fell back
    to first-tick bootstrap, re-anchoring ceDOI/peDOI on every feed restart
    (the "ChgOI changing abruptly" symptom).

    Guards r[oi_field] == 0: NSE's parser returns None for strike/sides it
    has no quote for (edge-of-chain strikes missing CE or PE), and
    _to_int() silently maps that to 0 — indistinguishable from a genuinely
    zero-OI contract. Seeding a baseline of 0 from a FALSE zero would stick
    for the rest of the session (setdefault won't overwrite later) and make
    that token's DOI read as its full OI instead of the true change.
    Skipping leaves the aggregator's own bootstrap-on-first-tick fallback
    to establish that baseline instead — self-correcting, never wrong."""
    chain_rows = payload.get("chain", [])
    rows_by_strike = {r["strike"]: r for r in chain_rows}
    for aggregator in _live_aggregators().values():
        baselines = {}
        for token, meta in aggregator.token_meta.items():
            if meta.get("option_type") not in ("CE", "PE"):
                continue
            row = rows_by_strike.get(meta["strike"])
            if not row:
                continue
            oi_field = "ceOI" if meta["option_type"] == "CE" else "peOI"
            chg_field = "ceChgOI" if meta["option_type"] == "CE" else "peChgOI"
            if oi_field in row and chg_field in row and row[oi_field] != 0:
                baselines[token] = row[oi_field] - row[chg_field]
        if baselines:
            aggregator.seed_session_baseline(baselines)


async def _publish_canonical_payload(payload):
    """Publish the new canonical snapshot and derive its wire update
    atomically relative to the live feeds' in-place patches."""
    global LAST_PAYLOAD, LAST_PAYLOAD_AT, _LAST_SENT
    async with _MARKET_STREAM_LOCK:
        LAST_PAYLOAD = payload
        LAST_PAYLOAD_AT = datetime.now().astimezone()
        if not USE_DELTA or _LAST_SENT is None:
            await broadcast({"type": "full", "payload": payload})
        else:
            # compute_diff walks the ENTIRE payload (all expiries, OI
            # velocity buckets, virtual-OI, greeks) doing recursive equality
            # checks + keyed-list reconciliation. Keep the event loop
            # responsive via a worker while the stream lock prevents
            # concurrent snapshot mutation.
            diff_start = time.monotonic()
            diff = await asyncio.to_thread(compute_diff, _LAST_SENT, payload)
            diff_elapsed = time.monotonic() - diff_start
            if diff_elapsed > 0.25:
                print(
                    f"[ws] WARNING: compute_diff took {diff_elapsed:.2f}s "
                    f"— this was blocking the event loop before this fix",
                    flush=True,
                )
            if diff is not None:
                await broadcast({"type": "delta", "payload": diff})
            else:
                print("[ws] tick unchanged, skipping broadcast", flush=True)
        _LAST_SENT = payload


async def _pace_until_next_tick(tick_start: float, pipeline_elapsed: float):
    """Sleep until the next engine tick. POLL_SECONDS is the CEILING (fires
    anyway if nothing happens — quiet market, public-only mode, or no live
    feed for this symbol). MIN_TICK_RECOMPUTE_SECONDS is the FLOOR: with
    ticks flooding in every ~0.25s during market hours, waking on every
    single one would run the heavy Greeks/OI-velocity/GEX pipeline MORE
    often than the old fixed poll, not less."""
    remaining = POLL_SECONDS - (time.monotonic() - tick_start)
    if remaining <= 0:
        if pipeline_elapsed > POLL_SECONDS:
            print(
                f"[ws] WARNING: pipeline took {pipeline_elapsed:.2f}s, "
                f"longer than --poll-seconds {POLL_SECONDS}s — "
                f"broadcast cadence is bottlenecked by pipeline speed, not the sleep.",
                flush=True,
            )
        return

    floor_remaining = MIN_TICK_RECOMPUTE_SECONDS - (time.monotonic() - tick_start)
    if floor_remaining > 0:
        await asyncio.sleep(min(floor_remaining, remaining))
        remaining = POLL_SECONDS - (time.monotonic() - tick_start)
    if remaining <= 0:
        return

    wait_switch = asyncio.create_task(_SYMBOL_SWITCH_EVENT.wait())
    wait_tick = asyncio.create_task(_TICK_ACTIVITY_EVENT.wait())
    try:
        done, pending = await asyncio.wait(
            {wait_switch, wait_tick},
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if wait_switch in done:
            _SYMBOL_SWITCH_EVENT.clear()
            print("[ws] symbol switch — ticking early", flush=True)
        elif wait_tick in done:
            _TICK_ACTIVITY_EVENT.clear()
            print(
                f"[ws] tick activity — ticking early "
                f"(floor={MIN_TICK_RECOMPUTE_SECONDS}s)",
                flush=True,
            )
        # else: timed out at the POLL_SECONDS ceiling, nothing to clear
    except Exception as e:
        print(
            f"[ws] WARNING: wake-wait failed, falling back to plain sleep: {e}",
            flush=True,
        )
        await asyncio.sleep(remaining)


async def engine_loop():
    while True:
        tick_start = time.monotonic()
        now = datetime.now()

        _reset_daily_sessions(now)
        _maybe_trigger_eod(now)

        payload = await _collect_pipeline_payload(tick_start)
        pipeline_elapsed = time.monotonic() - tick_start
        METRICS.observe_pipeline(payload is not None, pipeline_elapsed)

        if payload is not None:
            # Transport/session context is part of the canonical payload so
            # the Dashboard can distinguish an exchange closure from a
            # broken feed.
            payload["marketSession"] = _market_session_status(now)

            # Strategy -> execution bridge: hand this tick's decision block
            # to the auto-executor. Fire-and-forget so a slow broker call
            # can't delay this tick's own broadcast below. No-op unless
            # AUTO_STRATEGY_EXECUTION_ENABLED=true.
            decision_block = payload.get("decision")
            if decision_block:
                _create_background_task(
                    _AUTO_EXECUTOR.maybe_execute(decision_block, SYMBOL, EXPIRY),
                    "auto_executor",
                )

            _seed_oi_baselines(payload)
            await _publish_canonical_payload(payload)
            _create_background_task(_post_to_node(payload), "node_relay")
            print(
                f"[ws] broadcast tick -> {len(CONNECTED)} client(s) "
                f"(pipeline {pipeline_elapsed:.2f}s)",
                flush=True,
            )

            # Paper trading: catch pending LIMIT fills and keep unrealized
            # P&L live tick-to-tick even with zero new orders placed
            # (mirrors dashboard.js's ptLiveReprice(), which the client
            # can't do for LIMIT fills on its own).
            current_prices = _build_current_prices(payload)
            PT_ENGINE.check_pending_orders(current_prices)
            await _broadcast_portfolio(current_prices)

        await _pace_until_next_tick(tick_start, pipeline_elapsed)


# ── websocket handler ────────────────────────────────────────────────────
async def _send_handshake_snapshot(ws, *, send_full: bool):
    """Hand a newly-connected client everything the server already knows,
    instead of leaving panels blank until each loop's next broadcast."""
    if send_full:
        # New clients need a full snapshot before they can apply deltas.
        # Re-checked under the lock: if a switch/symbol change cleared
        # LAST_PAYLOAD meanwhile, waiting for the next real tick is better
        # than handing back a stale snapshot of the old symbol/source.
        async with _MARKET_STREAM_LOCK:
            if LAST_PAYLOAD is not None:
                await ws.send_str(
                    orjson.dumps(
                        {
                            "type": "full",
                            "payload": LAST_PAYLOAD,
                            "version": _BASELINE_ID,
                        },
                        default=_json_default,
                    ).decode()
                )
    if INDEX_QUOTES:
        await ws.send_str(
            orjson.dumps(
                {"type": "indexQuotes", "payload": INDEX_QUOTES},
                default=_json_default,
            ).decode()
        )
    # A late joiner must see an already-delayed analytics pass immediately;
    # transitions are otherwise only broadcast when they change.
    await ws.send_str(
        orjson.dumps(
            {"type": "pipelineStatus", "payload": _PIPELINE_STATUS},
            default=_json_default,
        ).decode()
    )
    # Real account funds (Live mode) — absent for the process lifetime if
    # funds polling has never been toggled on.
    if LAST_FUNDS is not None:
        await ws.send_str(
            orjson.dumps(
                {"type": "funds", "payload": LAST_FUNDS}, default=_json_default
            ).decode()
        )
    # Algo status (live-trading/kill-switch/account-guard/auto-executor
    # state) — whatever algo_status_loop() last computed, or a fresh build.
    try:
        status = (
            LAST_ALGO_STATUS
            if LAST_ALGO_STATUS is not None
            else _build_algo_status()
        )
        await ws.send_str(
            orjson.dumps(
                {"type": "algoStatus", "payload": status}, default=_json_default
            ).decode()
        )
    except Exception as e:
        print(f"[algo-status] initial snapshot failed: {e}", flush=True)
    # Most recent position-reconciliation mismatch, if any.
    if LAST_RECONCILIATION_ALERT is not None:
        try:
            await ws.send_str(
                orjson.dumps(
                    {
                        "type": "reconciliationAlert",
                        "payload": LAST_RECONCILIATION_ALERT,
                    },
                    default=_json_default,
                ).decode()
            )
        except Exception as e:
            print(
                f"[position_reconciler] initial alert snapshot failed: {e}",
                flush=True,
            )
    # Paper-trading state survives restarts via SQLite — hand over whatever
    # exists instead of leaving the panel empty until the next order/tick.
    try:
        init_prices = _build_current_prices(LAST_PAYLOAD)
        init_portfolio = PT_ENGINE.get_portfolio_summary(init_prices)
        init_spot = init_prices.get(_instrument_key("NIFTY", "", None, "INDEX"))
        init_portfolio["funds"] = PT_ENGINE.get_fund_summary(
            spot_price=init_spot, current_prices=init_prices
        )
        await ws.send_str(
            orjson.dumps(
                {"type": "portfolio", "payload": init_portfolio},
                default=_json_default,
            ).decode()
        )
        await ws.send_str(
            orjson.dumps(
                {"type": "orders", "payload": PT_ENGINE.get_orders()},
                default=_json_default,
            ).decode()
        )
    except Exception as e:
        print(f"[paper-trading] initial snapshot failed: {e}", flush=True)


async def _ws_dispatch_message(data):
    """Handle one inbound dashboard control message."""
    mtype = data.get("type")
    if mtype == "place_order":
        try:
            await _handle_place_order(data.get("payload") or {})
        except Exception as e:
            print(f"[paper-trading] place_order FAILED: {e}", flush=True)
            traceback.print_exc()
    elif mtype == "cancel_order":
        try:
            order_id = (data.get("payload") or {}).get("order_id")
            if order_id:
                success = PT_ENGINE.cancel_order(order_id)
                print(
                    f"[paper-trading] CANCEL {order_id}: "
                    f"{'success' if success else 'failed'}",
                    flush=True,
                )
                await _broadcast_portfolio(_build_current_prices(LAST_PAYLOAD))
        except Exception as e:
            print(f"[paper-trading] cancel_order FAILED: {e}", flush=True)
    elif mtype == "toggle_live_mode":
        # Sent by paper-trading.js whenever the PAPER/LIVE pill flips. This
        # ONLY starts/stops real-funds polling — real order placement stays
        # gated by LIVE_TRADING_ENABLED (restart-only, checked separately)
        # regardless of this toggle. Process-wide: one client's toggle
        # affects every connected client, since there's a single funds
        # poller backing all of CONNECTED.
        enabled = bool((data.get("payload") or {}).get("enabled"))
        if enabled:
            start_funds_polling()
        else:
            stop_funds_polling()


async def ws_handler(request):
    global PRICE_SOURCE, FUTURES_EXPIRY, _LAST_SENT
    if not _origin_allowed(request):
        print(
            f"[ws] REJECTED — disallowed Origin: {request.headers.get('Origin')!r}",
            flush=True,
        )
        return web.Response(status=403, text="Origin not allowed")

    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    _DASHBOARD_CLIENTS.add(ws)
    reconnect_attempt = request.query.get("reconnect") == "1"
    METRICS.websocket_connected(len(CONNECTED), reconnect=reconnect_attempt)
    t0 = time.monotonic()
    logger.info(
        "dashboard websocket connected",
        extra={
            "event": "websocket.connected",
            "subsystem": "websocket",
            "status": "connected",
            "connected_clients": len(CONNECTED),
            "symbol": SYMBOL,
            "expiry": EXPIRY,
        },
    )

    # ?symbol=/?expiry= — dashboard.js's switchActiveIndex() reconnects with
    # ?symbol=BANKNIFTY (etc.) on the WS URL when a ticker pill is clicked.
    # ?expiry= is accepted in either SmartAPI ('31JUL2026') or
    # option_chain_json ('31-Jul-2026') format via _parse_any_expiry.
    requested_symbol = request.query.get("symbol")
    requested_expiry = request.query.get("expiry")
    if requested_symbol or requested_expiry:
        switch_symbol(requested_symbol or SYMBOL, requested_expiry)

    # ?dataSource= — the DATA SOURCE dropdown; process-wide, no restart.
    requested_data_source = request.query.get("dataSource")
    if requested_data_source:
        try:
            await switch_data_source(requested_data_source)
        except ValueError as e:
            print(
                f"[ws] ignoring invalid ?dataSource={requested_data_source!r}: {e}",
                flush=True,
            )

    # ?priceSource= controls the analytics reference. AUTO is the safe default:
    # use live cash/index when NSE EQ is stale, otherwise EQ, and use futures
    # near/after the close when no live cash quote is available.
    requested_price_source = request.query.get("priceSource")
    if requested_price_source:
        requested_price_source = requested_price_source.strip().upper()
        if requested_price_source in ("AUTO", "EQ", "FUT"):
            if requested_price_source != PRICE_SOURCE:
                print(
                    f"[ws] price source switch requested: {PRICE_SOURCE} -> {requested_price_source}",
                    flush=True,
                )
                PRICE_SOURCE = requested_price_source
                _LAST_SENT = None
                _SYMBOL_SWITCH_EVENT.set()
        else:
            print(
                f"[ws] ignoring invalid ?priceSource={requested_price_source!r} "
                "(must be AUTO, EQ or FUT)",
                flush=True,
            )

    # ?futuresExpiry= — NEAR/NEXT/FAR futures reference selector.
    futures_reference_switched = False
    requested_futures_expiry = request.query.get("futuresExpiry")
    if requested_futures_expiry:
        fexp = requested_futures_expiry.strip().upper()
        if fexp in ("NEAR", "NEXT", "FAR"):
            if fexp != FUTURES_EXPIRY:
                print(
                    f"[ws] futures expiry switch requested: {FUTURES_EXPIRY} -> {fexp}",
                    flush=True,
                )
                FUTURES_EXPIRY = fexp
                futures_reference_switched = True
                # Never diff the newly-selected contract against a cached
                # payload from the previous one — the next pipeline pass
                # becomes a full authoritative snapshot for every client.
                _LAST_SENT = None
                _SYMBOL_SWITCH_EVENT.set()
        else:
            print(
                f"[ws] ignoring invalid ?futuresExpiry={requested_futures_expiry!r} "
                f"(must be NEAR, NEXT, or FAR)",
                flush=True,
            )

    await _send_handshake_snapshot(
        ws, send_full=LAST_PAYLOAD is not None and not futures_reference_switched
    )

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = orjson.loads(msg.data)
                except Exception as e:
                    print(f"[ws] bad inbound message, ignoring: {e}", flush=True)
                    continue
                await _ws_dispatch_message(data)
            elif msg.type in (
                web.WSMsgType.ERROR,
                web.WSMsgType.CLOSE,
                web.WSMsgType.CLOSING,
                web.WSMsgType.CLOSED,
            ):
                print(
                    f"[ws] connection ended via {msg.type} close_code={ws.close_code}",
                    flush=True,
                )
    finally:
        _DASHBOARD_CLIENTS.discard(ws)
        METRICS.websocket_disconnected(len(CONNECTED))
        alive_for = time.monotonic() - t0
        logger.info(
            "dashboard websocket disconnected",
            extra={
                "event": "websocket.disconnected",
                "subsystem": "websocket",
                "status": "disconnected",
                "connected_clients": len(CONNECTED),
                "duration_seconds": round(alive_for, 3),
                "reason": f"close_code={ws.close_code}",
                "symbol": SYMBOL,
                "expiry": EXPIRY,
            },
        )
    return ws


# ── HTTP handlers (thin adapters; logic lives in server/* modules) ───────
_HISTORY_API = MarketHistoryApi(
    state=lambda: {
        "symbol": SYMBOL,
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
    return await _HISTORY_API.spot_history(request)


async def history_handler(request):
    return await _HISTORY_API.history(request)


async def backtest_handler(request):
    return await handle_backtest(
        request, default_symbol=SYMBOL, run_backtest=run_backtest
    )


async def lot_sizes_handler(request):
    return await _HISTORY_API.lot_sizes(request)


def _build_health_snapshot(now=None):
    """Process, transport, and market-feed health contract. A closed
    exchange is not itself a degraded service; during an open session, a
    missing or old canonical payload makes the service degraded even when
    both listeners are reachable."""
    smartapi_connected = upstox_connected = shoonya_connected = False
    if USE_SMARTAPI:
        if LIVE_FEED_PROVIDER == "UPSTOX":
            upstox_connected = _FEEDS["UPSTOX"].connected
        elif LIVE_FEED_PROVIDER == "SHOONYA":
            shoonya_connected = _FEEDS["SHOONYA"].connected
        else:
            smartapi_connected = _FEEDS["SMARTAPI"].connected

    return _build_health_response(
        {
            "process_started_at": PROCESS_STARTED_AT,
            "poll_seconds": POLL_SECONDS,
            "last_payload": LAST_PAYLOAD,
            "last_payload_at": LAST_PAYLOAD_AT,
            "connected_clients": len(CONNECTED),
            "symbol": SYMBOL,
            "expiry": EXPIRY,
            "broker_services_enabled": USE_SMARTAPI,
            "data_source": DATA_SOURCE,
            "live_feed_provider": LIVE_FEED_PROVIDER,
            "live_feed_active": USE_SMARTAPI and _feed_allowed(DATA_SOURCE),
            "pipeline_status": _PIPELINE_STATUS,
            "smartapi_connected": smartapi_connected,
            "upstox_connected": upstox_connected,
            "shoonya_connected": shoonya_connected,
        },
        _market_session_status,
        now,
    )


def _log_health_transition(snapshot):
    """Log health changes once; repeated health polls remain quiet."""
    global _LAST_HEALTH_LOG_STATE
    _LAST_HEALTH_LOG_STATE = _log_server_health_transition(
        snapshot, _LAST_HEALTH_LOG_STATE, METRICS, logger
    )


async def health_handler(request):
    return await _health_response(
        request, snapshot=_build_health_snapshot, record_transition=_log_health_transition
    )


async def metrics_handler(request):
    return await _metrics_response(request, metrics=METRICS)


# ── entry point ──────────────────────────────────────────────────────────
async def main():
    if not _host_is_loopback(WS_HOST):
        raise RuntimeError(
            f"refusing unsafe non-loopback bind {WS_HOST!r}: the WebSocket "
            "control channel has no remote-client authentication; use "
            "--host localhost or a loopback address"
        )
    # Must run before any task that can hit a broker API timeout logs one —
    # see logging_config.py's RedactSensitiveHeaders: without it the SmartApi
    # SDK dumps the live session Bearer token and API private key in
    # plaintext on every request failure.
    from logging_config import configure_logging

    configure_logging()

    app = web.Application(middlewares=[no_cache_middleware])
    app.router.add_get("/health", health_handler)
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/bridge", bridge_ws_handler)
    app.router.add_get("/dashboard-relay", bridge_ws_handler)
    app.router.add_get("/api/spot-history", spot_history_handler)
    app.router.add_get("/api/history", history_handler)
    app.router.add_get("/api/backtest", backtest_handler)
    app.router.add_get("/api/lot-sizes", lot_sizes_handler)
    FRONTEND_DIR = SCRIPT_DIR / "frontend"
    app.router.add_static("/", path=FRONTEND_DIR, name="static")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WS_HOST, HTTP_PORT)
    await site.start()
    print(f"[http] serving static files at http://{WS_HOST}:{HTTP_PORT}/")
    print(
        f"[http] Dashboard available at http://{WS_HOST}:{HTTP_PORT}/dist/Dashboard/DashboardPro.html"
    )
    print(f"[ws] WebSocket endpoint at ws://{WS_HOST}:{HTTP_PORT}/ws symbol={SYMBOL}")

    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()

    # start_*_feed() makes blocking REST calls (token resolution) and has
    # internal sleeps — anything blocking goes through a thread, never runs
    # inline on the loop (same discipline as compute_diff() in the publish
    # path).
    if USE_SMARTAPI and _feed_allowed(LIVE_FEED_PROVIDER):
        _start_live_feed(LIVE_FEED_PROVIDER, _MAIN_LOOP)
    elif USE_SMARTAPI:
        print(
            f"[feed] websocket overlay not started "
            f"(data source={DATA_SOURCE}, feed provider={LIVE_FEED_PROVIDER})",
            flush=True,
        )
    else:
        print(
            "[broker] authenticated services disabled (BROKER_SERVICES_ENABLED=false) — "
            "no broker login, account/order REST call, or websocket connection; "
            "public daily ScripMaster allowed",
            flush=True,
        )

    _create_background_task(index_quote_loop(), "index_quote_loop")
    _create_background_task(bridge_loop(), "bridge_loop")
    _create_background_task(algo_status_loop(), "algo_status_loop")
    if LIVE_TRADING_ENABLED:
        _create_background_task(reconcile_loop(), "position_reconcile_loop")
    # No funds task at boot — funds polling starts/stops live via the
    # {"type":"toggle_live_mode"} WS message (see ws_handler +
    # start_funds_polling()/stop_funds_polling()), so flipping the
    # dashboard's LIVE pill controls it without a restart.
    try:
        await engine_loop()
    finally:
        background_tasks = list(_BACKGROUND_TASKS)
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await _NODE_RELAY.close()
        # oi_analysis.py buffers OI history in memory and flushes to disk
        # periodically — force a final write so a clean shutdown (Ctrl+C,
        # restart) never loses up to a minute of unflushed history.
        try:
            from oi_analysis import flush_history_to_disk

            flush_history_to_disk()
        except Exception as e:
            print(f"[shutdown] Could not flush OI history: {e}")


if __name__ == "__main__":
    asyncio.run(main())