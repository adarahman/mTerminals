import argparse
import asyncio
import ipaddress
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from urllib.parse import unquote

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "backend"))

import aiohttp
import numpy as np
import orjson
import websockets
from aiohttp import web
from analytics.nse_fii_dii_flow_fetch import record_today_flow
from nse_eod_fetch import fetch_all_eod, is_trading_day
from oi.futures_oi_tracker import get_tracker as _get_futures_oi_tracker

# option_chain_json.py parses sys.argv at import time — hide our own argv
# from it so it doesn't choke on ws_server_live's server-only arguments.
_real_argv = sys.argv
sys.argv = [_real_argv[0]]
from config import settings as _broker_settings  # noqa: E402
from brokers.provider_registry import supports_websocket as _provider_supports_websocket  # noqa: E402
from server.health import log_transition as _log_server_health_transition  # noqa: E402
from server import feed_lifecycle as _feed_lifecycle  # noqa: E402
from server.feed_expiry import matches_displayed_expiry as _matches_displayed_expiry  # noqa: E402
from server.backtest_api import handle_backtest  # noqa: E402
from server.bridge import DashboardBridge  # noqa: E402
from server.market_history_api import (
    MarketHistoryApi,
    no_cache_middleware as history_no_cache_middleware,
)  # noqa: E402
from server.index_quotes import IndexQuoteFetcher  # noqa: E402
from server.health_api import build_health_snapshot as _build_health_response, health_handler as _health_response, metrics_handler as _metrics_response  # noqa: E402
from server.websocket_clients import WebSocketClientHub  # noqa: E402
from server.feeds.shoonya import (
    FeedState as _ShoonyaFeedState,
    resolve_chain_tokens as _resolve_shoonya_feed_tokens,
    start_new_feed as _start_shoonya_feed_new,
    switch_existing_feed as _switch_shoonya_feed_existing,
)  # noqa: E402
from server.feeds.upstox import resolve_chain_tokens as _resolve_upstox_feed_tokens  # noqa: E402
import market_api  # noqa: E402  (lightweight ticker-strip quotes; no argv parsing, doesn't need hiding)
import mTerminals_json  # noqa: E402
import option_chain_json  # noqa: E402

sys.argv = _real_argv  # restore for our own argparse below

from operational_metrics import OperationalMetrics  # noqa: E402
from pipeline_config import RuntimeConfig  # noqa: E402
from live_feed_state import merge_live_feed_update  # noqa: E402
from ws_payload import compute_diff, json_default as _json_default  # noqa: E402

logger = logging.getLogger("mterminals.server")

from paper_trading import LOT_SIZES as PT_LOT_SIZES
from paper_trading import PaperTradingEngine, _instrument_key  # noqa: E402

BROKER_SERVICES_ENABLED = _broker_settings.broker_services_enabled

# Which broker's WEBSOCKET tick feed overlays fast leg-level ticks onto the
# slower NSE/BSE-polled chain — independent of execution_broker (orders) and
# market_data_provider (REST chain building). See config.py's
# live_feed_provider docstring and start_*_feed() below. The feed client
# itself is imported lazily inside start_upstox_feed() so deployments that
# don't use Upstox never need upstox-python-sdk installed just to boot.
LIVE_FEED_PROVIDER = _broker_settings.live_feed_provider


def _smartapi_disabled(*_args, **_kwargs):
    raise RuntimeError("Broker services are disabled by configuration")


if BROKER_SERVICES_ENABLED:
    from brokers.market_data import market_data
    from brokers.market_data import (
        PROVIDER_CAPABILITIES as _MD_PROVIDER_CAPABILITIES,
        PROVIDER_KEYS as _MD_PROVIDER_KEYS,
        get_active_provider as _md_get_active_provider,
        provider_has_credentials as _md_provider_has_credentials,
        provider_status as _md_provider_status,
        set_active_provider as _md_set_active_provider,
    )

    from brokers.connection import get_execution_adapter

    _execution_adapter = get_execution_adapter(_broker_settings.execution_broker)
    smartapi_place_order = _execution_adapter.place_order
    smartapi_get_order_book = _execution_adapter.get_order_book
    smartapi_get_positions = _execution_adapter.get_positions
    smartapi_get_funds = _execution_adapter.get_funds
    _execution_resolve_option_contract = getattr(
        _execution_adapter, "resolve_option_contract", None
    )
    from brokers.smartapi_client import INDEX_TOKENS as _SMARTAPI_INDEX_TOKENS
    from brokers.smartapi_history import get_candle_data, get_index_candles
    from brokers.smartapi_ws_client import EXCHANGE_TYPE, SmartTickStream
    from tick_pipeline import TickAggregator
else:
    # Do not even import the broker modules: importing them initializes the
    # SDK and instrument master. Any accidentally reached broker-only path
    # fails closed instead of silently logging in.
    #
    # Public-only mode: NSE/BSE public API is the only data source. The broker
    # adapter registry still gets imported lazily by the pipeline (see
    # option_chain_json._fetch_and_parse), but ws_server_live's own helpers
    # must exist for the DATA SOURCE dropdown/reporting in this mode too.
    _MD_PROVIDER_KEYS = ("NSE_BSE",)
    _MD_PROVIDER_CAPABILITIES = {
        "NSE_BSE": {"snapshot": True, "websocket": False, "execution": False}
    }

    def _md_get_active_provider():
        return "NSE_BSE"

    def _md_provider_has_credentials(name):
        return name == "NSE_BSE"

    def _md_set_active_provider(name):
        return name

    def _md_provider_status():
        from brokers.market_data import provider_status as _ps

        return _ps()

    class _DisabledMarketData:
        index_tokens = staticmethod(lambda: {})
        list_expiries = staticmethod(_smartapi_disabled)
        get_atm_chain = staticmethod(_smartapi_disabled)
        find_option_token = staticmethod(_smartapi_disabled)
        get_batch_quotes = staticmethod(_smartapi_disabled)
        get_batch_quotes_by_token = staticmethod(_smartapi_disabled)
        get_spot_quote = staticmethod(_smartapi_disabled)

    market_data = _DisabledMarketData()
    smartapi_place_order = _smartapi_disabled
    smartapi_get_order_book = _smartapi_disabled
    smartapi_get_positions = _smartapi_disabled
    smartapi_get_funds = _smartapi_disabled
    _execution_resolve_option_contract = None
    get_index_candles = _smartapi_disabled
    get_candle_data = _smartapi_disabled
    _SMARTAPI_INDEX_TOKENS = {}
    SmartTickStream = None
    TickAggregator = None
    EXCHANGE_TYPE = {}
from backtest.replay import run_backtest
from decision.auto_executor import AutoExecutor
from risk.account_guard import (
    LiveAccountRiskGuard,
    open_lots_from_positions,
    pnl_from_positions,
    projected_open_lots_from_positions,
)
from risk.live_order_store import LiveOrderStore
from risk.position_reconciler import PositionReconciler

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

_parser = argparse.ArgumentParser()
_parser.add_argument("--symbol", default="NIFTY")
_parser.add_argument("--expiry", default=None)
_parser.add_argument("--poll-seconds", type=int, default=6)
_parser.add_argument(
    "--pipeline-timeout-seconds",
    type=float,
    default=8.0,
    help="Maximum time the live engine waits for one REST analytics pass. "
    "The blocking worker is allowed to finish safely in the background, "
    "while live websocket ticks and the dashboard remain responsive.",
)
_parser.add_argument(
    "--min-tick-recompute-seconds",
    type=float,
    default=3.0,
    help="Floor on how often live tick activity can wake engine_loop "
    "early. --poll-seconds becomes a ceiling (fires anyway if no ticks "
    "arrive — quiet market, symbol has no live feed, etc.); this is "
    "the floor (never recompute faster than this even while ticks are "
    "flooding in, since ticks arrive every ~0.25s during market hours — "
    "waking on every single one would make the heavy Greeks/OI-velocity/ "
    "GEX recompute run MORE often than the old fixed poll, not less).",
)
_parser.add_argument("--host", default="localhost")
_parser.add_argument("--port", type=int, default=8765)
_parser.add_argument(
    "--http-port", type=int, default=5500, help="HTTP static file server port"
)
_parser.add_argument(
    "--relay", action="store_true", help="Enable Node relay posting (off by default)"
)
_parser.add_argument(
    "--extra-chains",
    action="store_true",
    dest="extra_chains",
    help="Enable multi-expiry NEAR/MONTHLY chains (slower; off by default)",
)
_parser.add_argument(
    "--strict-expiry",
    action="store_true",
    help="Don't auto-resolve to different expiry if requested expiry has no data",
)
_parser.add_argument(
    "--no-virtual-oi",
    action="store_true",
    help="Disable VirtualOI model inference for faster performance",
)
_parser.add_argument(
    "--no-delta",
    action="store_true",
    help="Always broadcast full payloads instead of deltas",
)
_parser.add_argument(
    "--no-index-quotes",
    action="store_true",
    help="Disable the NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX ticker-strip background fetch",
)
_parser.add_argument(
    "--strikes-each-side",
    type=int,
    default=None,
    help="Override how many strikes each side of ATM option_chain_json computes "
    "Greeks/OI-velocity/signal analytics for (engine's n_strikes_each_side). "
    "Defaults to 50 with BROKER_SERVICES_ENABLED=false (REST-only chains have no fast overlay "
    "to compensate, so the analytics pane needs the wider engine-side window "
    "up front) and 10 with the live broker overlay enabled (matches the "
    "feed adapter's own strikes_around_atm default). Pass this explicitly "
    "to use the same value in both modes.",
)
_parser.add_argument(
    "--index-quote-seconds",
    type=int,
    default=20,
    help="How often (s) to refresh the OTHER three indices' ticker quotes. "
    "Kept separate from --poll-seconds because it runs the full pipeline "
    "once per non-active symbol and shares NSE rate limits with it.",
)
_parser.add_argument(
    "--funds-poll-seconds",
    type=int,
    default=15,
    help="How often (s) to refresh real AngelOne account funds/margin "
    "(getRMS) once funds polling is toggled on (see toggle_live_mode "
    "in ws_handler — starts/stops live over the socket when the "
    "dashboard's LIVE pill flips, no restart needed). "
    "Kept independent of --poll-seconds since RMS limits don't need "
    "tick-level freshness and this is a real network round-trip to "
    "AngelOne on top of whatever the main pipeline is already doing.",
)
_parser.add_argument(
    "--portfolio-poll-seconds",
    type=float,
    default=0.5,
    help="Minimum interval (s) between paper-trading portfolio/orders "
    "re-broadcasts triggered off the fast live tick stream (see "
    "_smartapi_sync_and_broadcast). Previously portfolio/orders only "
    "went out once per --poll-seconds, inside engine_loop()'s slower "
    "NSE/BSE REST pipeline tick — so with the live feed enabled, option "
    "chain/spot LTP updated sub-second while positions' last_price/"
    "unrealized_pnl in the Paper Trading panel stayed pinned to the "
    "much slower --poll-seconds cadence. get_portfolio_summary() is "
    "just a couple of small indexed SQLite reads (no network I/O), so "
    "this can safely run much faster than --poll-seconds — throttled "
    "here (rather than fired on every single tick) purely to avoid "
    "flooding clients with WS messages when many strikes tick in a "
    "tight burst. Set to 0 to broadcast on every live tick with no "
    "throttling at all.",
)
ARGS = _parser.parse_args()

SYMBOL = ARGS.symbol.strip().upper()
# Manual price-source selector — "EQ" (default, cash-market spot from the
# NSE option-chain response) or "FUT" (near-month futures LTP). See
# option_chain_json.py's PRICE_SOURCE docstring for the 3:15-3:30 EQ-goes-
# stale rationale. Switched the same way SYMBOL is — via ?priceSource= on
# the WS URL, see ws_handler() — and read fresh into RuntimeConfig every
# tick by run_pipeline_once() -> _configure_pipeline_globals().
PRICE_SOURCE = "EQ"
# Manual futures-expiry selector — "NEAR" (default), "NEXT", or "FAR".
# Only meaningful when PRICE_SOURCE="FUT" (see fetch_futures_wide's
# `which` param). Switched the same way PRICE_SOURCE is — via
# ?futuresExpiry= on the WS URL, see ws_handler() — and read fresh into
# RuntimeConfig every tick by run_pipeline_once() -> _configure_pipeline_globals().
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


def _resolve_default_data_source() -> str:
    """Startup default for the runtime DATA SOURCE dropdown.

    Prefers the configured MARKET_DATA_PROVIDER when that provider is
    usable (registered AND has credentials). Otherwise falls back to the
    first credentialed BROKER source (registry order), so a stale/empty
    token in .env doesn't silently strand the dashboard on the public
    NSE/BSE API and "break" a previously-working broker setup. NSE/BSE is
    only the default when NO broker has credentials at all — the true
    login-free case (fresh install, or BROKER_SERVICES_ENABLED=false forces it explicitly
    regardless)."""
    configured = _broker_settings.market_data_provider
    if configured in _MD_PROVIDER_KEYS and _md_provider_has_credentials(configured):
        return configured
    for candidate in _MD_PROVIDER_KEYS:
        if candidate == "NSE_BSE":
            continue
        if _md_provider_has_credentials(candidate):
            return candidate
    return "NSE_BSE"


# Runtime market-data source — the Dashboard's DATA SOURCE dropdown. Switched
# via ?dataSource= on the WS URL (see ws_handler() -> switch_data_source())
# WITHOUT a server restart; every connected client shares one process-wide
# value, same as SYMBOL/EXPIRY. The active provider is also pushed into
# brokers.market_data's runtime facade (set_active_provider) so the option
# chain pipeline, index-quote loops, and payload all route consistently.
DATA_SOURCE = _resolve_default_data_source()
if not USE_SMARTAPI:
    DATA_SOURCE = "NSE_BSE"  # public-only mode: NSE/BSE is the only source
_md_set_active_provider(DATA_SOURCE)

_md_label = (
    "Upstox"
    if DATA_SOURCE == "UPSTOX"
    else "Shoonya"
    if DATA_SOURCE == "SHOONYA"
    else "Kite"
    if DATA_SOURCE == "KITE"
    else "Breeze"
    if DATA_SOURCE == "BREEZE"
    else "Kotak"
    if DATA_SOURCE == "KOTAK"
    else "NSE/BSE"
    if DATA_SOURCE == "NSE_BSE"
    else "SmartAPI"
)
if DATA_SOURCE == "NSE_BSE":
    _chain_source = "NSE/BSE public REST (polling)"
    _overlay_state = "no websocket overlay"

elif USE_SMARTAPI:
    _chain_source = f"{_md_label} REST"

    _overlay_allowed = (
        DATA_SOURCE == LIVE_FEED_PROVIDER
        and _provider_supports_websocket(DATA_SOURCE)
    )

    if _overlay_allowed:
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
# dashboard.js INDEX_TICKER_ORDER — keep the two lists in sync). The
# currently-active SYMBOL's own quote already comes for free on every
# regular tick (payload["spot"]/["spotChange"]/["spotChgPct"]), so this
# loop only needs to fetch the OTHER symbols — VIX is never the active
# SYMBOL, so it's always fetched here.
INDEX_TICKER_SYMBOLS = ["NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX", "INDIA VIX"]
_BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}

# VIX isn't in INDEX_TOKENS (auto-built from ScripMaster rows where
# instrumenttype == 'AMXIDX' — VIX doesn't carry that type), so it's
# pinned manually, same as broker_pipeline.py's _VIX_TOKEN.
# Re-verify against a fresh ScripMaster dump if quotes ever go stale/empty;
# nothing here will warn you if Angel reassigns the token.
_VIX_TRADINGSYMBOL = "India VIX"  # SmartAPI's own tradingsymbol string
_VIX_TOKEN = "99926017"  # exch_seg=NSE, verified against live ScripMaster 2026-07-14

_DASHBOARD_CLIENTS = WebSocketClientHub()
# Compatibility alias for diagnostics and existing test seams.  Connection
# ownership lives in _DASHBOARD_CLIENTS rather than this server module.
CONNECTED = _DASHBOARD_CLIENTS.clients
LAST_PAYLOAD = None
LAST_PAYLOAD_AT = None
_LAST_SENT = None
_BASELINE_SEQ = 0
_BASELINE_ID = None
PROCESS_STARTED_AT = datetime.now().astimezone()
_LAST_HEALTH_LOG_STATE = None
_BACKGROUND_TASKS: set[asyncio.Task] = set()
_PIPELINE_TASK = None
_PIPELINE_STATUS = {
    "status": "STARTING",
    "reason": "Analytics pipeline has not completed yet",
    "startedAt": None,
    "lastSuccessAt": None,
    "elapsedSeconds": None,
}
METRICS = OperationalMetrics(started_at=PROCESS_STARTED_AT)
_NODE_SESSION = None
# Most recent real-account funds snapshot from _funds_poll_body() below —
# handed to newly-connected clients immediately (see ws_handler) the same
# way LAST_PAYLOAD/INDEX_QUOTES already are, so the top-bar Fund pill
# doesn't sit at "n/a" until the next FUNDS_POLL_SECONDS tick. Set back to
# None by stop_funds_polling() whenever the dashboard's LIVE pill is
# toggled off, so a client reconnecting while polling is stopped isn't
# handed a stale real-money figure that's no longer being refreshed.
LAST_FUNDS = None

# Paper trading — single engine instance for the whole process, backed by
# SQLite (paper_trading.db next to this script) so positions/orders survive
# a restart. All access happens on the main asyncio thread (ws_handler for
# place_order, engine_loop for the tick-driven mark-to-market/broadcast), so
# no extra locking is needed around the sqlite3 connection.
PT_ENGINE = PaperTradingEngine()

# _build_current_prices() only ever sees ONE symbol's chain per tick — the
# currently-active dashboard SYMBOL, since that's all a single option_chain_json
# pipeline run produces. Without this, positions on any OTHER symbol (e.g. a
# NIFTY leg opened earlier, now viewing SENSEX) silently lose their LTP the
# moment you switch symbols, showing "—" instead of their last real price.
# This cache holds the last known price per instrument_key across symbol
# switches, so a leg only ever goes blank if it's never been priced at all
# (never the case for an open position, since it had to be priced to fill).
_LAST_KNOWN_LEG_PRICES: dict[str, float] = {}

# Throttle for the fast-path portfolio broadcast fired from
# _smartapi_sync_and_broadcast (see PORTFOLIO_POLL_SECONDS) — separate from
# engine_loop()'s own POLL_SECONDS-paced broadcast, which still runs
# unconditionally as a slower fallback (covers public-only mode and any
# gap while the SmartAPI feed is (re)connecting).
_LAST_PORTFOLIO_BROADCAST_TS = 0.0
EOD_TRIGGER_TIME = dtime(15, 45)  # run shortly after NSE cash market close (15:30)

MARKET_OPEN_TIME = dtime(9, 15)
MARKET_CLOSE_TIME = dtime(15, 30)


def _market_session_status(now: datetime) -> str:
    """Best-effort NSE session label for the UI.

    Uses the same yearly holiday calendar as is_trading_day(); ad-hoc exchange
    closures still require that calendar/source to be updated.
    """
    if now.weekday() < 5 and not is_trading_day(now):
        return "HOLIDAY"
    if not is_trading_day(now):
        return "MARKET_CLOSED"
    if MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME:
        return "OPEN"
    return "MARKET_CLOSED"


_EOD_DONE_DATE = None  # tracks which date's EOD job already ran
_LAST_SESSION_DATE = (
    None  # tracks which date's SmartAPI OI baseline is currently active
)

# ── Live trading configuration ──────────────────────────────────────────
# Master switch — OFF by default. Must be explicitly set to actually place
# real orders on the AngelOne account. Read once at process start (not
# re-checked per-request) since flipping this on/off mid-session is a
# deliberate deploy-time decision, not something to toggle casually.
LIVE_TRADING_ENABLED = (
    os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() == "true"
)

# Instant kill switch — checked on EVERY live order attempt, no restart
# needed. Create this file to immediately block all live orders regardless
# of LIVE_TRADING_ENABLED, e.g.:  touch LIVE_TRADING_KILL
# Delete it to resume. This exists specifically so a misbehaving order flow
# can be shut off in seconds during market hours without a redeploy.
LIVE_TRADING_KILL_SWITCH_FILE = str(SCRIPT_DIR / "LIVE_TRADING_KILL")

# Hard caps enforced SERVER-SIDE (not just in the UI) — a bug in strike/qty
# resolution on the client can't bypass these. Override via env if needed,
# but keep these conservative; they're a safety net, not a trading limit.
LIVE_MAX_LOTS_PER_ORDER = int(os.environ.get("LIVE_MAX_LOTS_PER_ORDER", "1"))
LIVE_MAX_ORDERS_PER_MINUTE = int(os.environ.get("LIVE_MAX_ORDERS_PER_MINUTE", "5"))
_live_order_timestamps = []  # sliding window for the per-minute cap, main-thread only
_LIVE_ORDER_SUBMIT_LOCK = threading.Lock()
_LIVE_ORDER_GATE = None
_LIVE_ORDER_GATE_LOOP = None
_LIVE_ORDER_RESULTS = {}
_LIVE_ORDER_RESULTS_MAX = 500
_LIVE_ORDER_STORE = LiveOrderStore(max_entries=_LIVE_ORDER_RESULTS_MAX)

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


def _live_trading_kill_switch_active():
    return os.path.exists(LIVE_TRADING_KILL_SWITCH_FILE)


def _live_order_gate():
    """One live-order critical section per event loop.

    The gate covers the broker position read, projected-exposure check and
    submission together. Tests create multiple short-lived event loops, so
    the lock is recreated when the active loop changes.
    """
    global _LIVE_ORDER_GATE, _LIVE_ORDER_GATE_LOOP
    loop = asyncio.get_running_loop()
    if _LIVE_ORDER_GATE is None or _LIVE_ORDER_GATE_LOOP is not loop:
        _LIVE_ORDER_GATE = asyncio.Lock()
        _LIVE_ORDER_GATE_LOOP = loop
    return _LIVE_ORDER_GATE


# Account-level risk guard — daily loss limit, max open exposure, and a
# drawdown-streak breaker, all evaluated across the whole trading day
# rather than per-order. Trips the SAME kill-switch file above; see
# risk/account_guard.py's module docstring for the full design.
_ACCOUNT_GUARD = LiveAccountRiskGuard(LIVE_TRADING_KILL_SWITCH_FILE)

# Diffs the live order book against the live position book (both from
# AngelOne) and alerts on mismatch — same kill-switch file as the guard
# above. See risk/position_reconciler.py's module docstring for the full
# design and why both a periodic check (reconcile_loop below) and a
# post-fill check (in _handle_place_order) exist.
_POSITION_RECONCILER = PositionReconciler(LIVE_TRADING_KILL_SWITCH_FILE)
POSITION_RECONCILE_SECONDS = int(os.environ.get("POSITION_RECONCILE_SECONDS", "120"))

# How often the dashboard's algo status panel gets refreshed. Deliberately
# NOT tick-cadence (engine_loop can run several times a second) — this is
# supervisory/status info, not live-tick data, and _ACCOUNT_GUARD.get_status()
# does a SQLite read each call, so this runs on its own slow, independent
# loop the same way index_quote_loop()/reconcile_loop() do rather than
# piggybacking on every engine_loop tick.
ALGO_STATUS_POLL_SECONDS = int(os.environ.get("ALGO_STATUS_POLL_SECONDS", "5"))
LAST_ALGO_STATUS = None

# Most recent non-clean PositionReconciler.check() result, broadcast as
# {"type":"reconciliationAlert",...} — see _broadcast_reconciliation_alert().
# Handed to newly-connecting clients the same way LAST_ALGO_STATUS is, so a
# dashboard opened after a mismatch was found still sees it instead of
# waiting for the next drift (which may never come, if it was a one-off
# propagation-lag blip that already resolved itself).
LAST_RECONCILIATION_ALERT = None

# Cache of the most recent live position-book fetch, populated by
# reconcile_loop()'s own periodic smartapi_get_positions() call (below).
# _build_algo_status() reads this to report current open lots on the
# status panel instead of making its own separate broker API call every
# ALGO_STATUS_POLL_SECONDS (5s) — reconcile_loop's own cadence
# (POSITION_RECONCILE_SECONDS, 120s by default) is plenty fresh for a
# status display; the actual pre-trade exposure check in
# _handle_place_order still does its own live fetch, unaffected by this.
# None until live trading is enabled and reconcile_loop has completed its
# first cycle.
LAST_LIVE_POSITIONS = None

# Strategy -> execution bridge — the automated "algo" path, separate from
# and independent of LIVE_TRADING_ENABLED (both must be true for an
# auto-executed order to reach the real broker). Constructed further
# below, after _handle_place_order/_submit_auto_order are defined, since
# its submit_order_fn calls into that function. See
# decision/auto_executor.py's module docstring for the full design.


# ── WebSocket origin allowlist ──────────────────────────────────────────
# Browsers do NOT apply same-origin restrictions to WebSocket handshakes
# the way they do to fetch()/XHR, so without this check ANY page open in
# the same browser — not just this dashboard's own tab — could open
# ws://<host>:<port>/ws and drive it, including submitting orders
# (cross-site WebSocket hijacking). Only a request whose Origin header
# matches something in this allowlist is accepted. A request with NO
# Origin header at all (a plain `websockets`/python client, curl, or local
# script) is accepted only from a loopback peer. This prevents an
# origin-less remote client from bypassing the browser-origin allowlist.
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


def _origin_allowed(request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        try:
            return ipaddress.ip_address(request.remote).is_loopback
        except (TypeError, ValueError):
            return False
    # A dashboard opened directly from disk (file://...) is assigned the
    # opaque browser origin "null". Permit that development mode only when
    # the TCP peer itself is loopback. A remote machine sending Origin:null
    # remains rejected, preserving the cross-site WebSocket guard.
    if origin == "null":
        try:
            return ipaddress.ip_address(request.remote).is_loopback
        except (TypeError, ValueError):
            return False
    return origin in ALLOWED_ORIGINS


def _check_live_rate_limit():
    """Sliding 60s window cap on live order attempts, independent of
    AngelOne's own 500/min limit — this is a much tighter self-imposed
    ceiling specifically to limit blast radius of a runaway client/bug,
    not an attempt to maximize throughput against AngelOne's actual quota."""
    now = time.monotonic()
    cutoff = now - 60
    while _live_order_timestamps and _live_order_timestamps[0] < cutoff:
        _live_order_timestamps.pop(0)
    if len(_live_order_timestamps) >= LIVE_MAX_ORDERS_PER_MINUTE:
        return False
    _live_order_timestamps.append(now)
    return True


def _completed_live_order(client_order_id):
    """Returns a previously completed live submission, if any."""
    with _LIVE_ORDER_SUBMIT_LOCK:
        cached = _LIVE_ORDER_RESULTS.get(client_order_id)
        if cached is not None:
            return cached
        persisted = _LIVE_ORDER_STORE.get(client_order_id)
        if persisted is not None:
            _LIVE_ORDER_RESULTS[client_order_id] = persisted
        return persisted


def _submit_live_order_idempotent(client_order_id, *args, **kwargs):
    """Serializes live submissions and collapses retries by client ID.

    AngelOne's order tag is also set to the same identity, so the broker
    adapter can recover an accepted order after an uncertain response.
    """
    with _LIVE_ORDER_SUBMIT_LOCK:
        existing = _LIVE_ORDER_RESULTS.get(client_order_id)
        if existing is not None:
            return existing, True
        order_id = smartapi_place_order(
            *args,
            **kwargs,
            order_tag=client_order_id,
        )
        order_id = _LIVE_ORDER_STORE.record(client_order_id, order_id)
        _LIVE_ORDER_RESULTS[client_order_id] = order_id
        while len(_LIVE_ORDER_RESULTS) > _LIVE_ORDER_RESULTS_MAX:
            _LIVE_ORDER_RESULTS.pop(next(iter(_LIVE_ORDER_RESULTS)))
        return order_id, False


def _resolve_live_order_token(symbol, instrument_type, expiry, strike):
    """Resolves (exchange, tradingsymbol, symboltoken) for a live order.
    Mirrors the same underlying/exchange logic used for the SmartAPI tick
    feed (_BSE_SYMBOLS -> BFO, else NFO) so live orders target the same
    contract space the dashboard is already streaming ticks for."""
    exchange = "BFO" if symbol in _BSE_SYMBOLS else "NFO"

    if instrument_type in ("CE", "PE"):
        if _execution_resolve_option_contract is not None:
            return _execution_resolve_option_contract(
                symbol,
                expiry,
                strike,
                instrument_type,
                exchange,
            )
        # expiry here is option_chain_json's format ("14-Jul-2026"); SmartAPI's
        # ScripMaster uses "14JUL2026" (no separators) — convert before lookup.
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
        # Futures aren't resolved anywhere yet in this pipeline (only
        # options via find_option_token / get_atm_chain) — rather than
        # silently mis-resolving a token, refuse until futures token
        # lookup is actually wired up.
        return None

    # INDEX (spot) — not a tradeable instrument on its own; refuse.
    return None


# option_chain_json keeps its runtime config (SYMBOL/EXCHANGE/EXPIRY/...) as
# plain module globals, mutated in place before each main() call — that's
# fine when only engine_loop() touches it, but the index-quote loop below
# also needs to point it at three OTHER symbols on the side. Both loops run
# their pipeline call via asyncio.to_thread(), so without this lock a quote
# fetch for BANKNIFTY could interleave with the primary SYMBOL's tick and
# have them stomp on each other's globals mid-run. Every pipeline call —
# primary tick or ticker-quote fetch — must hold this for its full duration.
_PIPELINE_LOCK = asyncio.Lock()
INDEX_QUOTES = {}  # {"BANKNIFTY": {"spot":.., "spotChange":.., "spotChgPct":..}, ...}
_SYMBOL_SWITCH_EVENT = asyncio.Event()
# Set (thread-safely) by TickAggregator's flush loop on every real tick
# flush. engine_loop() waits on this OR _SYMBOL_SWITCH_EVENT, whichever
# comes first, bounded by MIN_TICK_RECOMPUTE_SECONDS as a floor and
# POLL_SECONDS as a ceiling — see engine_loop() for the full reasoning.
_TICK_ACTIVITY_EVENT = asyncio.Event()
# Serializes the canonical full/delta stream and its backing snapshots.
# compute_diff runs in a worker thread, so without this lock SmartAPI's
# async tick path could mutate _LAST_SENT/LAST_PAYLOAD while that thread
# was traversing them. New-client snapshot handoff uses the same lock.
_MARKET_STREAM_LOCK = asyncio.Lock()


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
    current = (status, reason)
    if current != previous:
        await broadcast({"type": "pipelineStatus", "payload": dict(_PIPELINE_STATUS)})


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


def _eod_task_done(task: asyncio.Task):
    """Surface exceptions from the fire-and-forget EOD fetch task, which would
    otherwise fail silently since nothing awaits it directly."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        import traceback

        print(f"[eod] FAILED: {exc!r}", flush=True)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    else:
        print("[eod] fetch_all_eod completed successfully", flush=True)


def _flow_task_done(task: asyncio.Task):
    """Surface exceptions from the fire-and-forget FII/DII flow fetch task
    (see nse_fii_dii_flow_fetch.record_today_flow), same rationale as
    _eod_task_done above."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        import traceback

        print(f"[flow] FAILED: {exc!r}", flush=True)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return
    ok = task.result()
    print(
        f"[flow] record_today_flow {'succeeded' if ok else 'returned False (no data yet)'}",
        flush=True,
    )


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

    # dashboard.js's switchActiveIndex() reconnects with ?symbol=BANKNIFTY
    # (etc) on the WS URL when a ticker pill is clicked — see
    # switch_symbol() for what this does. This is process-wide: it also
    # switches the feed for every OTHER already-connected client, since
    # there's only one engine loop backing all of CONNECTED.
    #
    # ?expiry=... is the same idea for the expiry dropdown — accepted in
    # either SmartAPI's format ('31JUL2026') or option_chain_json's format
    # ('31-Jul-2026'); switch_symbol()/_resolve_chain_tokens() both parse
    # either via _parse_any_expiry(). Previously only ?symbol= was read
    # here, so picking a different expiry never reached the backend at
    # all — the SmartAPI feed stayed pinned to whichever expiry was
    # nearest when it started, regardless of what the dashboard displayed.
    requested_symbol = request.query.get("symbol")
    requested_expiry = request.query.get("expiry")
    if requested_symbol or requested_expiry:
        switch_symbol(requested_symbol or SYMBOL, requested_expiry)

    # ?dataSource=... is the Dashboard's DATA SOURCE dropdown — the runtime
    # market-data provider (SMARTAPI/UPSTOX/KITE/SHOONYA/BREEZE/KOTAK/NSE_BSE),
    # process-wide like ?symbol=, switchable WITHOUT a server restart. See
    # switch_data_source() for the switch sequence. Unknown keys raise here
    # so a stale frontend build can't silently pick a source the backend
    # doesn't know — just log and continue on the current source.
    requested_data_source = request.query.get("dataSource")
    if requested_data_source:
        try:
            switch_data_source(requested_data_source)
        except ValueError as e:
            print(f"[ws] ignoring invalid ?dataSource={requested_data_source!r}: {e}", flush=True)

    # Legacy priceSource URLs no longer alter analytics. EQ is the fixed
    # option-pricing and decision reference; FUT is displayed separately.
    requested_price_source = request.query.get("priceSource")
    if requested_price_source:
        src = requested_price_source.strip().upper()
        if src not in ("EQ", "FUT"):
            print(
                f"[ws] ignoring invalid ?priceSource={requested_price_source!r} (must be EQ or FUT)",
                flush=True,
            )
    PRICE_SOURCE = "EQ"

    requested_futures_expiry = request.query.get("futuresExpiry")
    futures_reference_switched = False
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
                # Do not diff the newly-selected contract against a cached
                # payload from the previous contract. The next pipeline pass
                # becomes a full authoritative snapshot for every client.
                _LAST_SENT = None
                _SYMBOL_SWITCH_EVENT.set()
        else:
            print(
                f"[ws] ignoring invalid ?futuresExpiry={requested_futures_expiry!r} (must be NEAR, NEXT, or FAR)",
                flush=True,
            )
    try:
        # New clients need a full snapshot before they can apply deltas.
        # (If switch_symbol() just cleared LAST_PAYLOAD above, this is
        # skipped on purpose — better to wait for the next tick's real data
        # on the new symbol than hand back a snapshot of the old one.)
        async with _MARKET_STREAM_LOCK:
            if LAST_PAYLOAD is not None and not futures_reference_switched:
                msg_str = orjson.dumps(
                    {
                        "type": "full",
                        "payload": LAST_PAYLOAD,
                        "version": _BASELINE_ID,
                    },
                    default=_json_default,
                ).decode()
                await ws.send_str(msg_str)
        if INDEX_QUOTES:
            msg_str = orjson.dumps(
                {"type": "indexQuotes", "payload": INDEX_QUOTES}, default=_json_default
            ).decode()
            await ws.send_str(msg_str)
        # A late-joining dashboard must see an already-delayed analytics
        # pass immediately; status transitions are otherwise only broadcast
        # when they change.
        await ws.send_str(
            orjson.dumps(
                {
                    "type": "pipelineStatus",
                    "payload": _PIPELINE_STATUS,
                },
                default=_json_default,
            ).decode()
        )
        # Real account funds (Live mode) — same "hand over what we already
        # have" treatment as INDEX_QUOTES above. Stays None/skipped for the
        # life of the process when LIVE_TRADING_ENABLED is false, since
        # funds_loop() never runs in that case.
        if LAST_FUNDS is not None:
            msg_str = orjson.dumps(
                {"type": "funds", "payload": LAST_FUNDS}, default=_json_default
            ).decode()
            await ws.send_str(msg_str)
        # Algo status (live-trading/kill-switch/account-guard/auto-executor
        # state) — hand a new client whatever algo_status_loop() last
        # computed instead of leaving the status panel blank for up to
        # ALGO_STATUS_POLL_SECONDS until the next periodic broadcast.
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
        # Most recent position-reconciliation mismatch, if any — same
        # "hand over what we already have" treatment as algoStatus above,
        # so a dashboard opened after a mismatch was found doesn't sit
        # blank until the next drift happens to recur.
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
        # Hand a new client whatever paper-trading state already exists
        # (positions/orders survive process restarts via SQLite) instead of
        # leaving the panel empty until the next place_order/tick.
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
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = orjson.loads(msg.data)
                except Exception as e:
                    print(f"[ws] bad inbound message, ignoring: {e}", flush=True)
                    continue
                if data.get("type") == "place_order":
                    try:
                        await _handle_place_order(data.get("payload") or {})
                    except Exception as e:
                        import traceback

                        print(f"[paper-trading] place_order FAILED: {e}", flush=True)
                        traceback.print_exc()
                elif data.get("type") == "cancel_order":
                    try:
                        order_id = (data.get("payload") or {}).get("order_id")
                        if order_id:
                            success = PT_ENGINE.cancel_order(order_id)
                            print(
                                f"[paper-trading] CANCEL {order_id}: {'success' if success else 'failed'}",
                                flush=True,
                            )
                            current_prices = _build_current_prices(LAST_PAYLOAD)
                            await _broadcast_portfolio(current_prices)
                    except Exception as e:
                        print(f"[paper-trading] cancel_order FAILED: {e}", flush=True)
                elif data.get("type") == "toggle_live_mode":
                    # Sent by paper-trading.js's ptToggleLiveMode() whenever
                    # the dashboard's PAPER/LIVE pill is flipped. This ONLY
                    # starts/stops real-funds polling — it does NOT enable
                    # real order placement, which stays gated by
                    # LIVE_TRADING_ENABLED (restart-only, checked separately
                    # in _handle_place_order) regardless of this toggle.
                    # Process-wide, same as switch_symbol() — one client's
                    # toggle affects what every connected client sees, since
                    # there's a single funds poller backing all of CONNECTED.
                    enabled = bool((data.get("payload") or {}).get("enabled"))
                    if enabled:
                        start_funds_polling()
                    else:
                        stop_funds_polling()
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


def _build_current_prices(payload):
    """Build the {instrument_key: ltp} map paper_trading.py's
    check_pending_orders()/mark_to_market()/place_order() expect, from the
    SAME tick payload dashboard.js renders the option chain from. Keeping
    this as the one place that reads payload['chain']/['chains']/['spot']/
    ['futLTP'] means the paper trading engine is always priced off exactly
    what the user sees on screen, never a stale/separate fetch."""
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

    # This tick only ever prices ONE symbol's legs (see _LAST_KNOWN_LEG_PRICES
    # docstring above) — merge in, don't replace, so positions on other
    # symbols keep showing their last known price instead of "—" the moment
    # the dashboard's active symbol changes.
    _LAST_KNOWN_LEG_PRICES.update(prices)
    return {**_LAST_KNOWN_LEG_PRICES, **prices}


async def _broadcast_portfolio(current_prices):
    """Pushes fresh portfolio + orders snapshots to every connected client.
    dashboard.js's updateDashboard() generic branch (deepMerge(_wsState,
    {[msg.type]: msg.payload})) lands these at _wsState.portfolio /
    _wsState.orders for free — no client-side wiring needed beyond what's
    already there."""
    portfolio = PT_ENGINE.get_portfolio_summary(current_prices)
    orders = PT_ENGINE.get_orders()

    # Add fund summary (using NIFTY spot as a proxy for index-margin checks
    # if the active symbol's spot is missing) so the frontend's Fund pill
    # stays synced with the backend's PT_STARTING_CAPITAL and SPAN estimation.
    spot = current_prices.get(_instrument_key("NIFTY", "", None, "INDEX"))
    portfolio["funds"] = PT_ENGINE.get_fund_summary(
        spot_price=spot, current_prices=current_prices
    )

    await broadcast({"type": "portfolio", "payload": portfolio})
    await broadcast({"type": "orders", "payload": orders})


async def _handle_place_order(payload, _live_gate_acquired=False):
    """Handles an inbound {"type":"place_order", "payload":{...}} message
    from dashboard.js's sendWsMessage('place_order', ...) (see ptSubmitOrder
    / ptQuickSubmit).

    Routes to a REAL AngelOne order ONLY if ALL of the following hold:
      - LIVE_TRADING_ENABLED=true was set at process start
      - the kill-switch file is NOT present
      - the client explicitly sent live=true AND confirmed=true (a UI
        confirm-modal click, not the default click-to-order paper flow —
        the client must deliberately opt in per-order, this is not a
        global "everything is now live" toggle from the client's side)
      - the order is within LIVE_MAX_LOTS_PER_ORDER and the sliding
        per-minute rate cap
      - the instrument resolves to a real, known symboltoken

    Any other case — including any resolution failure or missing
    confirmation — falls through to the existing paper trading engine
    unchanged. Prices MARKET orders off LAST_PAYLOAD — the most recent
    tick already broadcast to every client — so the fill the user sees
    matches the LTP they clicked on. Always re-broadcasts portfolio +
    orders afterward, whether the order filled, queued as a pending LIMIT,
    or was rejected, so the panel's orders table shows *something*
    immediately instead of waiting on the next engine_loop tick.

    Returns a {"status": ..., "reason"/"order_id": ...} dict on every
    path (rejected/failed/placed for the live branch, the paper engine's
    own Order.status/reject_reason for the paper branch) — added so
    _submit_auto_order() (below) can tell a downstream rejection from an
    actual placement instead of assuming success just because this
    function didn't raise. Manual callers (ws_handler) don't currently
    use the return value, so this is purely additive."""
    symbol = (payload.get("symbol") or "").strip().upper()
    instrument_type = str(payload.get("instrument_type") or "INDEX").strip().upper()
    expiry = str(payload.get("expiry") or "").strip()
    side = str(payload.get("side") or "").strip().upper()
    order_type = str(payload.get("order_type") or "MARKET").strip().upper()
    client_order_id = payload.get("client_order_id")

    try:
        qty_value = float(payload.get("qty_lots") or 0)
        qty_lots = (
            int(qty_value) if np.isfinite(qty_value) and qty_value.is_integer() else 0
        )
    except (TypeError, ValueError):
        qty_lots = 0

    try:
        strike_raw = payload.get("strike")
        strike = None if strike_raw in (None, "") else float(strike_raw)
        if strike is not None and not np.isfinite(strike):
            strike = None
    except (TypeError, ValueError):
        strike = None

    try:
        limit_raw = payload.get("limit_price")
        limit_price = None if limit_raw in (None, "") else float(limit_raw)
        if limit_price is not None and not np.isfinite(limit_price):
            limit_price = None
    except (TypeError, ValueError):
        limit_price = None

    # Treat the browser as untrusted input. In particular, the old live
    # mapping interpreted every side other than BUY as SELL, so a missing or
    # malformed side could become a real sell order. Reject malformed intent
    # before building a price key, touching paper positions, consuming the
    # live rate limit, or calling the broker.
    validation_reason = None
    if not symbol:
        validation_reason = "symbol is required"
    elif side not in ("BUY", "SELL"):
        validation_reason = f"unsupported side {side or '(missing)'}"
    elif instrument_type not in ("CE", "PE", "FUT", "EQ", "INDEX"):
        validation_reason = f"unsupported instrument_type {instrument_type}"
    elif order_type not in ("MARKET", "LIMIT"):
        validation_reason = f"unsupported order_type {order_type}"
    elif qty_lots < 1:
        validation_reason = "qty_lots must be a positive whole number"
    elif instrument_type in ("CE", "PE") and (
        not expiry or strike is None or strike <= 0
    ):
        validation_reason = "CE/PE orders require a valid expiry and positive strike"
    elif instrument_type == "FUT" and not expiry:
        validation_reason = "FUT orders require an expiry"
    elif order_type == "LIMIT" and (limit_price is None or limit_price <= 0):
        validation_reason = "LIMIT orders require a positive finite limit_price"

    if validation_reason:
        print(f"[order] REJECTED malformed intent: {validation_reason}", flush=True)
        current_prices = _build_current_prices(LAST_PAYLOAD)
        await _broadcast_portfolio(current_prices)
        return {"status": "rejected", "reason": validation_reason}

    current_prices = _build_current_prices(LAST_PAYLOAD)
    key = _instrument_key(symbol, expiry, strike, instrument_type)
    current_ltp = current_prices.get(key)

    wants_live = bool(payload.get("live")) and bool(payload.get("confirmed"))

    # Serialize the complete live pre-trade check and submission. Locking
    # only smartapi_place_order leaves a TOCTOU window where two requests
    # can both observe the same position book and independently clear the
    # exposure cap before either order reaches the broker.
    if wants_live and not _live_gate_acquired:
        async with _live_order_gate():
            return await _handle_place_order(payload, _live_gate_acquired=True)

    if wants_live:
        rejection_reason = None
        if (
            not isinstance(client_order_id, str)
            or not 8 <= len(client_order_id) <= 20
            or not client_order_id.isalnum()
        ):
            rejection_reason = (
                "live orders require an 8-20 character alphanumeric client_order_id"
            )
        else:
            completed_order_id = _completed_live_order(client_order_id)
            if completed_order_id is not None:
                await _broadcast_portfolio(current_prices)
                return {
                    "status": "placed",
                    "order_id": completed_order_id,
                    "client_order_id": client_order_id,
                    "duplicate": True,
                }

        if rejection_reason is None and not LIVE_TRADING_ENABLED:
            rejection_reason = "live trading disabled on server"
        elif rejection_reason is None and _live_trading_kill_switch_active():
            rejection_reason = "live trading kill switch active"
        elif rejection_reason is None and (
            qty_lots < 1 or qty_lots > LIVE_MAX_LOTS_PER_ORDER
        ):
            rejection_reason = f"qty_lots {qty_lots} outside allowed range (1-{LIVE_MAX_LOTS_PER_ORDER})"
        elif rejection_reason is None and not _check_live_rate_limit():
            rejection_reason = f"rate limit exceeded ({LIVE_MAX_ORDERS_PER_MINUTE}/min)"
        elif rejection_reason is None and symbol not in PT_LOT_SIZES:
            # A real order's quantity = qty_lots * lot_size — silently
            # falling back to a guessed lot size (the old `.get(symbol, 65)`
            # default) for a symbol NSE's circular doesn't match here would
            # size a real order wrong with no warning. Refuse instead: add
            # the symbol to paper_trading.py's LOT_SIZES (after confirming
            # against NSE's current circular — see that dict's own comment)
            # before it can be traded live.
            rejection_reason = (
                f"no verified lot size for {symbol} — refusing to guess on a live order"
            )
        elif rejection_reason is None:
            guard_tripped, guard_trip_reason = _ACCOUNT_GUARD.is_tripped()
            if guard_tripped:
                rejection_reason = f"account risk guard tripped: {guard_trip_reason}"

        if rejection_reason:
            print(
                f"[live-trading] REJECTED: {rejection_reason} — {symbol} {side} {qty_lots} lot(s)",
                flush=True,
            )
            await _broadcast_portfolio(current_prices)
            return {"status": "rejected", "reason": rejection_reason}

        resolved = _resolve_live_order_token(symbol, instrument_type, expiry, strike)
        if resolved is None:
            reason = f"could not resolve instrument token for {symbol} {expiry} {strike}{instrument_type}"
            print(f"[live-trading] REJECTED: {reason}", flush=True)
            await _broadcast_portfolio(current_prices)
            return {"status": "rejected", "reason": reason}

        exchange, tradingsymbol, symboltoken = resolved
        # BUGFIX: this used to read option_chain_json.LOT_SIZES — a THIRD,
        # separate copy of the lot-size table from the one paper_trading.py
        # (and paper-trading.js's PT_LOT_SIZES, kept manually in sync with
        # it) already uses, with no guarantee the two agree. For the PAPER
        # path a wrong lot size only produces wrong P&L math (bad enough on
        # its own — see paper_trading.py's own LOT_SIZES comment) but for
        # this LIVE path a wrong lot size means submitting the WRONG
        # QUANTITY to the real exchange. Using the same already-vetted
        # table as everywhere else in this app means there's exactly one
        # source of truth instead of three, and the live order's sizing is
        # guaranteed consistent with what the dashboard shows the user.
        # guaranteed to be a valid key here — unknown symbols were already
        # rejected above, so no silent-fallback default is needed (or
        # wanted) at this point.
        lot_size = PT_LOT_SIZES[symbol]
        quantity = qty_lots * lot_size
        transaction_type = "BUY" if (side or "").upper() == "BUY" else "SELL"

        # Calculate exposure after applying this exact signed order. This
        # permits risk-reducing closes while still failing closed on an
        # incomplete position book. The surrounding live-order gate keeps
        # this read/check atomic with the submission below.
        try:
            live_positions = await asyncio.to_thread(smartapi_get_positions)
            projected_open_lots = projected_open_lots_from_positions(
                live_positions,
                PT_LOT_SIZES,
                tradingsymbol,
                transaction_type,
                quantity,
            )
        except Exception as e:
            print(
                f"[account_guard] could not fetch position book for exposure check: {e}",
                flush=True,
            )
            projected_open_lots = None
        allowed, exposure_reason = _ACCOUNT_GUARD.check_new_order(
            0, projected_open_lots
        )
        if not allowed:
            print(
                f"[live-trading] REJECTED: {exposure_reason} — {symbol} {side} {qty_lots} lot(s)",
                flush=True,
            )
            await _broadcast_portfolio(current_prices)
            return {"status": "rejected", "reason": exposure_reason}

        try:
            order_id, duplicate = await asyncio.to_thread(
                _submit_live_order_idempotent,
                client_order_id,
                tradingsymbol,
                symboltoken,
                exchange,
                transaction_type,
                quantity,
                order_type=order_type,
                price=limit_price or 0.0,
            )
            print(
                f"[live-trading] PLACED: {tradingsymbol} {transaction_type} {quantity} "
                f"qty (order_id={order_id})",
                flush=True,
            )
            live_result = {
                "status": "placed",
                "order_id": order_id,
                "client_order_id": client_order_id,
                "duplicate": duplicate,
            }
        except Exception as e:
            print(
                f"[live-trading] FAILED: {tradingsymbol} {transaction_type} {quantity} — {e}",
                flush=True,
            )
            live_result = {"status": "failed", "reason": str(e)}
        finally:
            try:
                post_fill_positions = await asyncio.to_thread(smartapi_get_positions)
                _ACCOUNT_GUARD.update_pnl(pnl_from_positions(post_fill_positions))
            except Exception as e:
                print(
                    f"[account_guard] could not refresh daily P&L after order: {e}",
                    flush=True,
                )
            try:
                # Fast post-fill confirmation that this order's fill is
                # actually reflected in the position book — the periodic
                # reconcile_loop below is the real safety net (catches
                # drift unrelated to this app's own order flow), this is
                # just a quicker check right after our own action, same
                # relationship update_pnl() above has to the periodic
                # daily-loss check.
                post_fill_orders = await asyncio.to_thread(smartapi_get_order_book)
                post_fill_result = _POSITION_RECONCILER.check(
                    post_fill_orders, post_fill_positions, PT_LOT_SIZES
                )
                await _broadcast_reconciliation_alert(
                    post_fill_result, source="post_fill"
                )
            except Exception as e:
                print(
                    f"[position_reconciler] could not run post-fill check: {e}",
                    flush=True,
                )
            await _broadcast_portfolio(current_prices)
        return live_result

    # ── Paper trading path (unchanged) ──────────────────────────────────
    order = PT_ENGINE.place_order(
        symbol,
        side,
        qty_lots,
        instrument_type=instrument_type,
        expiry=expiry,
        strike=strike,
        order_type=order_type,
        limit_price=limit_price,
        current_ltp=current_ltp,
        client_order_id=client_order_id,
    )
    print(
        f"[paper-trading] {order.status}: {symbol} {side} {qty_lots} lot(s) "
        f"{instrument_type} {expiry} {strike} "
        f"@ {order.fill_price if order.fill_price is not None else limit_price}"
        + (f" — {order.reject_reason}" if order.reject_reason else ""),
        flush=True,
    )

    await _broadcast_portfolio(current_prices)
    return {
        "status": order.status,
        "reason": order.reject_reason,
        "order_id": getattr(order, "id", None),
        "client_order_id": getattr(order, "client_order_id", client_order_id),
    }


async def _submit_auto_order(symbol, instrument_type, expiry, strike, side, qty_lots):
    """Bridge from decision/auto_executor.py into the existing manual
    order path. Builds the same payload shape a dashboard click sends to
    _handle_place_order, with live=True and confirmed=True filled in on
    the algo's behalf — that's the ONE difference from a manual order;
    every other check in _handle_place_order (lot size, rate limit,
    account_guard exposure/trip state) still runs exactly as it does for
    a human-submitted order.

    Raises on rejection so AutoExecutor.maybe_execute() logs the failure
    (and records it in the auto-trade history feed) instead of treating
    a downstream-rejected order as a success. This used to just call
    _handle_place_order and return — since that function only printed a
    "REJECTED"/"FAILED" server-log line and returned None either way, a
    live-trading-gate rejection that happened AFTER auto_executor's own
    evaluate() cleared (kill switch flipped, guard tripped, exposure cap
    hit, resolve failure) was silently reported as EXECUTED to both the
    log and the dashboard's status panel. _handle_place_order now returns
    a {"status": ...} dict on every path (see its own docstring) — a
    non-"placed" status here is turned into an exception, same as if
    smartapi_place_order itself had thrown."""
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
        reason = (result or {}).get(
            "reason"
        ) or f"unexpected status {status!r} from _handle_place_order"
        raise RuntimeError(reason)
    return result


# Strategy -> execution bridge — constructed here since it needs
# _submit_auto_order defined above. OFF by default
# (AUTO_STRATEGY_EXECUTION_ENABLED); see decision/auto_executor.py.
_AUTO_EXECUTOR = AutoExecutor(_ACCOUNT_GUARD, _submit_auto_order)


def _build_algo_status() -> dict:
    """Composes the {\"type\":\"algoStatus\",...} broadcast payload — one
    place that reads every live-trading/algo safety mechanism's current
    state, so the dashboard can show a single status panel instead of
    someone having to tail server logs to know whether the algo is armed,
    what it last decided, or why the kill switch is active. Pure
    read-only snapshot: calling this never changes any guard/executor
    state. See PROJECT-ARCHITECTURE.md's algo-readiness sections (§11,
    §12) for what each of these mechanisms does."""
    guard_status = _ACCOUNT_GUARD.get_status()
    # current_open_lots pairs with the max_open_lots limit already in
    # guard_status, so the panel can show "current / limit" rather than
    # just the bare cap. Sourced from LAST_LIVE_POSITIONS (reconcile_loop's
    # own periodic fetch, see that global's comment) instead of a fresh
    # broker call here — this is a status display, not the pre-trade
    # exposure check (which still fetches fresh in _handle_place_order).
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
            else "Shoonya"
            if _broker_settings.execution_broker == "SHOONYA"
            else "Upstox"
            if _broker_settings.execution_broker == "UPSTOX"
            else "Zerodha"
            if _broker_settings.execution_broker == "KITE"
            else "ICICI Direct"
            if _broker_settings.execution_broker == "BREEZE"
            else "Angel One"
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
    {"type":"reconciliationAlert",...} broadcast — previously this result
    was only ever printed to the server log (see reconcile_loop's and
    _handle_place_order's own prints), so the only way to know a mismatch
    was found — even a below-trip-threshold one — was tailing logs. These
    are cheap, low-severity signals by design (see position_reconciler.py's
    module docstring): most resolve themselves next cycle once a fill
    propagates, but a human watching the dashboard should still see them
    as they happen rather than only learning about the expensive case
    (an actual kill-switch trip) after the fact.

    No-ops on a clean result — this only fires the broadcast (and updates
    LAST_RECONCILIATION_ALERT, which new connections are handed) when
    there's actually something to show. `source` distinguishes the fast
    post-fill check from the periodic sweep, purely for display context.
    """
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
# server.bridge.  The live coordinator supplies only current process state.
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
    """Point option_chain_json's runtime config at `symbol`, via
    option_chain_json.set_runtime_config() (see pipeline_config.py). Used
    only by run_pipeline_once() for the primary --symbol's full
    option-chain pipeline run. The ticker-strip quotes for the other three
    INDEX_TICKER_SYMBOLS no longer go through option_chain_json at all (see
    fetch_nse_index_quotes_sync()/fetch_bse_index_quote_sync()), so this no
    longer needs to stay in sync with a second caller.

    Re-pushes the RUNTIME data source into both option_chain_json's
    use_smartapi gate (NSE_BSE -> public REST path, any broker -> broker
    REST path) and brokers.market_data's active-provider facade, so a
    ?dataSource= switch takes effect on the very next pipeline pass.

    No longer pokes an `exchange` — option_chain_json.main() always
    recomputes EXCHANGE locally from SYMBOL, so that poke was inert before
    this refactor too (see pipeline_config.py's module docstring). The
    `exchange` local below still exists, purely to pick the right EXPIRY
    fallback (BSE vs NSE nearest-expiry rule) exactly as before."""
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


def switch_symbol(new_symbol, new_expiry=None):
    """Runtime symbol switch — triggered by ws_handler() when a client
    (re)connects with ?symbol=... on the WS URL (see dashboard.js
    switchActiveIndex()). Changes what the NEXT engine_loop tick fetches,
    it doesn't fetch anything itself.

    EXPIRY resets to None (auto-resolve) unless a specific one is passed,
    since the old symbol's expiry string is almost never valid for the new
    one. LAST_PAYLOAD/_LAST_SENT are cleared so the next tick broadcasts a
    "full" payload instead of a compute_diff() against the old symbol's
    shape (a diff between two different symbols' payloads is really just
    the new payload with extra work), and so a client connecting mid-switch
    doesn't get handed a stale snapshot of the OLD symbol below in
    ws_handler(). Finally pokes _SYMBOL_SWITCH_EVENT so engine_loop wakes
    immediately instead of finishing out its current --poll-seconds sleep.

    This is process-wide, not per-client: every connected client shares one
    engine loop, so one browser tab switching symbol switches it for all of
    them. That matches the rest of the module (one SYMBOL/EXPIRY global,
    one broadcast to all of CONNECTED) rather than trying to serve several
    symbols out of a single process."""
    global SYMBOL, EXPIRY, LAST_PAYLOAD, _LAST_SENT
    # Defensive normalization: a stale/cached frontend bundle can send the
    # symbol still percent-encoded (double-encoded on the wire). aiohttp
    # decodes once, leaving "ZYDUS%20LIFESCIENCES%20LTD"; undo that so the
    # engine never probes a literal "%20" symbol. No-op for clean input.
    new_symbol = unquote(new_symbol)
    new_symbol = new_symbol.strip().upper()
    if new_symbol == SYMBOL and (new_expiry is None or new_expiry == EXPIRY):
        return  # already on this symbol+expiry, nothing to do
    # if new_symbol == SYMBOL and new_expiry is None:
    #     return  # already on this symbol, nothing to do
    print(f"[ws] symbol switch requested: {SYMBOL} -> {new_symbol}", flush=True)
    SYMBOL = new_symbol
    EXPIRY = new_expiry
    LAST_PAYLOAD = None
    _LAST_SENT = None
    _SYMBOL_SWITCH_EVENT.set()
    if USE_SMARTAPI:
        _restart_live_feed(LIVE_FEED_PROVIDER, new_symbol, new_expiry)


def _restart_live_feed(provider: str, symbol: str, expiry=None) -> bool:
    """Schedule the active provider's existing feed for a symbol switch.

    Socket lifecycle remains provider-native, but every orchestration call
    site uses this broker-neutral dispatch rather than duplicating a
    provider branch.
    """
    return _feed_lifecycle.restart(provider, symbol, expiry, {
        "SMARTAPI": restart_smartapi_feed,
        "UPSTOX": restart_upstox_feed,
        "SHOONYA": restart_shoonya_feed,
    })


def _start_live_feed(provider: str, loop) -> bool:
    """Offload the configured provider's blocking feed startup."""
    return _feed_lifecycle.start(provider, loop, {
        "SMARTAPI": start_smartapi_feed,
        "UPSTOX": start_upstox_feed,
        "SHOONYA": start_shoonya_feed,
    }, lambda start_callback, start_loop, task_name: _create_background_task(
        asyncio.to_thread(start_callback, start_loop), task_name
    ))


def _feed_allowed(feed_provider: str) -> bool:
    """Whether ticks from the given broker feed may still merge/broadcast.

    Returns False when a runtime DATA SOURCE switch moved away from
    `feed_provider`, or when the active source is a polling-only provider
    (KITE/BREEZE/KOTAK/NSE_BSE — no WebSocket feed in this codebase). Every
    *_sync_and_broadcast() entry point gates on this BEFORE touching
    LAST_PAYLOAD/_LAST_SENT, so a feed left running after a switch can't
    contaminate the new provider's baseline (acceptance: no cross-provider
    data mixing), and switching away from a broker feed effectively stops
    it without a restart."""
    return _feed_lifecycle.is_allowed(
        feed_provider, DATA_SOURCE, _provider_supports_websocket
    )


def _stop_active_broker_feed(provider: str) -> None:
    """Best-effort unsubscribe of the given broker feed's tokens so a
    provider switched away from stops consuming feed bandwidth. Fire-and-
    forget (daemon thread) — the real "stop" from the payload's point of
    view is _feed_allowed()'s broadcast gate, which takes effect
    synchronously on the next tick. Each feed's own switch lock serializes
    against any in-flight symbol-switch thread."""
    def _run():
        global _smartapi_tokens, _upstox_keys, _shoonya_instruments
        if provider == "SMARTAPI":
            with _smartapi_switch_lock:
                if _smartapi_stream is not None:
                    if _smartapi_tokens and _smartapi_exchange:
                        try:
                            _smartapi_stream.unsubscribe(
                                EXCHANGE_TYPE[_smartapi_exchange], _smartapi_tokens
                            )
                        except Exception:
                            pass
                    if _smartapi_index_token and _smartapi_index_exchange:
                        try:
                            _smartapi_stream.unsubscribe(
                                EXCHANGE_TYPE[_smartapi_index_exchange],
                                [_smartapi_index_token],
                            )
                        except Exception:
                            pass
                    _smartapi_tokens = None
        elif provider == "UPSTOX":
            with _upstox_switch_lock:
                if _upstox_stream is not None and _upstox_keys:
                    try:
                        _upstox_stream.unsubscribe(_upstox_keys)
                    except Exception:
                        pass
                    _upstox_keys = None
        elif provider == "SHOONYA":
            with _shoonya_switch_lock:
                if _shoonya_stream is not None and _shoonya_instruments:
                    try:
                        _shoonya_stream.unsubscribe(_shoonya_instruments)
                    except Exception:
                        pass
                    _shoonya_instruments = None

    threading.Thread(target=_run, daemon=True).start()


def switch_data_source(new_source: str) -> bool:
    """Runtime data-source switch — triggered by ws_handler() when a client
    reconnects with ?dataSource=... on the WS URL (the Dashboard's DATA
    SOURCE dropdown). Works WITHOUT a server restart: the next engine_loop
    tick builds the new source's full baseline and broadcasts it.

    Sequence (satisfies the runtime-switch acceptance criteria):
      1. validate the provider key;
      2. stop the OLD broker feed (best-effort unsubscribe + gate its
         broadcasts via _feed_allowed so its ticks can't leak into the new
         baseline);
      3. push the new provider into brokers.market_data's runtime facade
         (set_active_provider) so the chain pipeline, index-quote loops and
         payload all route to it next tick;
      4. clear LAST_PAYLOAD/_LAST_SENT so the next tick is a FULL baseline
         from the new source (never a diff against the old source's shape);
      5. start the new provider's WebSocket feed IF it has one
         (SMARTAPI/UPSTOX/SHOONYA only — KITE/BREEZE/KOTAK/NSE_BSE are
         polling-only in this codebase);
      6. poke _SYMBOL_SWITCH_EVENT so engine_loop wakes immediately.

    Process-wide, exactly like switch_symbol(): all CONNECTED clients share
    one engine loop and one DATA_SOURCE."""
    global DATA_SOURCE
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
    print(
        f"[data-source] switch requested: {old_source} -> {new_source}", flush=True
    )

    # 1. Validate/switch the market-data provider BEFORE touching the
    # currently-working source.
    try:
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
            f"[data-source] {new_source} unavailable; "
            f"remaining on {old_source}",
            flush=True,
        )
        return False

    # 2. Provider is confirmed usable. Stop the old broker feed.
    _stop_active_broker_feed(old_source)

    # 3. Commit ws_server_live's source state.
    DATA_SOURCE = new_source

    # 4. Next tick is a FULL baseline from the new source.
    global LAST_PAYLOAD, _LAST_SENT
    LAST_PAYLOAD = None
    _LAST_SENT = None

    # 5. Start the new provider's feed if it has one.
    if _provider_supports_websocket(new_source):
        _restart_live_feed(new_source, SYMBOL, EXPIRY)

    # 6. Wake engine_loop immediately.
    _SYMBOL_SWITCH_EVENT.set()

    print(f"[data-source] switched to {new_source}", flush=True)
    return True


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
    """Upstox analog of _smartapi_sync_and_broadcast() — same shared
    merge logic, gated on the Upstox feed's own expiry tracker instead
    (see _upstox_feed_matches_displayed_expiry). SmartAPI and Upstox
    feeds are mutually exclusive in practice (LIVE_FEED_PROVIDER picks
    one), so there's no risk of the two racing to merge into
    LAST_PAYLOAD/_LAST_SENT concurrently — _MARKET_STREAM_LOCK still
    serializes either way. Gated on _feed_allowed("UPSTOX") too, same
    reason as the SmartAPI gate above."""
    await _sync_live_feed_and_broadcast(
        "UPSTOX", message, _upstox_feed_matches_displayed_expiry
    )


async def _shoonya_sync_and_broadcast(message):
    """Shoonya analog of _smartapi_sync_and_broadcast() — same shared
    merge logic, gated on the Shoonya feed's own expiry tracker instead
    (see _shoonya_feed_matches_displayed_expiry). SmartAPI/Upstox/Shoonya
    feeds are mutually exclusive in practice (LIVE_FEED_PROVIDER picks
    one), so there's no risk of them racing to merge into
    LAST_PAYLOAD/_LAST_SENT concurrently — _MARKET_STREAM_LOCK still
    serializes either way. Gated on _feed_allowed("SHOONYA") too, same
    reason as the SmartAPI gate above."""
    await _sync_live_feed_and_broadcast(
        "SHOONYA", message, _shoonya_feed_matches_displayed_expiry
    )


async def _live_feed_sync_and_broadcast_locked(message, matches_expiry_fn):
    """Wraps broadcast() for a live tick-streaming feed (SmartAPI, Upstox,
    or Shoonya — matches_expiry_fn is the caller's own feed-specific gate,
    see _smartapi_sync_and_broadcast/_upstox_sync_and_broadcast/
    _shoonya_sync_and_broadcast above):
    before sending a tick delta to clients, also merges it into
    LAST_PAYLOAD/_LAST_SENT's matching chain rows (IF the feed's expiry
    matches what's currently displayed — see matches_expiry_fn). Without this,
    TickAggregator's updates were invisible to LAST_PAYLOAD/_LAST_SENT
    entirely: a newly-connecting client's initial "full" snapshot (built
    from LAST_PAYLOAD) would miss whatever SmartAPI had already pushed to
    existing clients, and the next engine_loop tick's compute_diff() could
    re-broadcast an older NSE-polled value over top of a fresher SmartAPI
    one, causing a visible flicker backward. This keeps the server's own
    bookkeeping honest about what clients actually have on screen.

    If the expiry doesn't match (feed is streaming a different expiry than
    what's displayed), the chain portion of the delta is stripped before
    broadcasting — it must NOT reach clients. applyDelta() on the client
    (market-store.js) merges keyed chain rows by strike alone, with no
    concept of expiry; strikes overlap heavily across expiries (e.g. 24000,
    24100, ...), so a stale-expiry row would get stamped straight onto the
    currently-displayed expiry's row for that strike, corrupting LTP/OI in
    the UI. This window opens right after an expiry switch — switch_symbol()
    clears LAST_PAYLOAD/EXPIRY immediately, but _switch_smartapi_symbol_
    blocking()'s background unsubscribe hasn't finished yet, so the feed can
    still emit a few ticks for the old expiry before it's cut off. Spot
    (handled separately below) isn't tied to an expiry, so it's unaffected
    and still broadcasts every time."""
    global LAST_PAYLOAD_AT
    feed_update_applied = False
    try:
        message, feed_update_applied = merge_live_feed_update(
            message, LAST_PAYLOAD, _LAST_SENT, matches_expiry_fn
        )
    except Exception as e:
        # Sync is a best-effort consistency improvement, not required for
        # the tick to reach clients — never let a sync bug block broadcast.
        print(f"[live-feed] state sync failed (broadcasting anyway): {e}", flush=True)

    if feed_update_applied and LAST_PAYLOAD is not None:
        LAST_PAYLOAD_AT = datetime.now().astimezone()
    await broadcast(message)

    # Paper trading, fast path: previously portfolio/orders only went out
    # once per --poll-seconds, inside engine_loop()'s slower NSE/BSE REST
    # pipeline tick — so with SmartAPI enabled, option chain/spot LTP moved
    # sub-second while the Paper Trading panel's positions/P&L stayed pinned
    # to the much slower --poll-seconds cadence (dashboard.js's
    # ptLiveReprice() papers over this for the visible last_price column,
    # but Realized/Unrealized/Total P&L are backend truth and can't be
    # patched client-side). LAST_PAYLOAD was just updated above (spot/chain
    # merge), so this fires off the same fresh prices the client just
    # received. Throttled (not fired on every single tick) purely to avoid
    # flooding clients with WS messages when several strikes tick in a
    # tight burst — get_portfolio_summary() itself is cheap enough to call
    # on every tick if PORTFOLIO_POLL_SECONDS is set to 0.
    global _LAST_PORTFOLIO_BROADCAST_TS
    now_ts = time.monotonic()
    if now_ts - _LAST_PORTFOLIO_BROADCAST_TS >= PORTFOLIO_POLL_SECONDS:
        _LAST_PORTFOLIO_BROADCAST_TS = now_ts
        try:
            current_prices = _build_current_prices(LAST_PAYLOAD)
            # Mirrors engine_loop()'s tick handling: also check whether this
            # fresher tick crosses any pending LIMIT orders, not just
            # index/CE/PE mark-to-market — otherwise LIMIT fills would still
            # lag behind SmartAPI's price moves by up to --poll-seconds.
            PT_ENGINE.check_pending_orders(current_prices)
            await _broadcast_portfolio(current_prices)
        except Exception as e:
            # Same best-effort posture as the sync block above — a paper
            # trading hiccup must never take down the live market-data feed.
            print(
                f"[paper-trading] fast-path portfolio broadcast failed: {e}", flush=True
            )


_MAIN_LOOP = None  # the asyncio event loop main() runs on; lets a runtime
# switch to a provider whose feed was never started at boot (e.g. switching
# to UPSTOX when LIVE_FEED_PROVIDER points elsewhere) start that feed on the
# live loop instead of silently doing nothing (the _*_loop globals are only
# captured inside start_*_feed(), so they stay None for never-started feeds).

_smartapi_stream = None
_smartapi_aggregator = None
_smartapi_loop = None  # captured once at startup, reused for symbol switches
_smartapi_exchange = (
    None  # exchange type currently subscribed (NFO/BFO), for unsubscribe
)
_smartapi_tokens = None  # token list currently subscribed, for unsubscribe
_smartapi_current_expiry = (
    None  # expiry string the SmartAPI feed is streaming, e.g. "31JUL2026"
)
_smartapi_index_token = (
    None  # underlying INDEX token currently subscribed for fast spot ticks, if any
)
_smartapi_index_exchange = None  # EXCHANGE_TYPE key ("NSE_CM"/"BSE_CM") the index token was subscribed under — DIFFERENT from _smartapi_exchange (NFO/BFO), so it needs its own unsubscribe call
_smartapi_futures_token = None  # current-month futures token subscribed for VWAP/volume, if resolved (see _resolve_futures_token)
_smartapi_futures_exchange = None  # NFO/BFO — same exchange as _smartapi_exchange, tracked separately since it's folded into _smartapi_tokens for unsubscribe but needs its own basis-calc lookup

# Serializes ALL entry points into the SmartAPI feed lifecycle —
# start_smartapi_feed() (both the initial startup call AND the fallback
# call from a switch that finds no feed running yet) and
# _switch_smartapi_symbol_blocking(). Without this covering
# start_smartapi_feed() too, the initial startup call (now backgrounded via
# asyncio.to_thread so it doesn't block the event loop) can run
# CONCURRENTLY with a switch's fallback call to start_smartapi_feed() if a
# client connects and requests a symbol switch before startup finishes —
# creating two independent SmartTickStream connections. AngelOne appears to
# allow only one live WS session per login, so the loser becomes an
# orphaned connection stuck retrying forever (visible as endless
# "Attempting to resubscribe/reconnect" warnings with nothing left
# referencing it). Reentrant (RLock) so a switch thread already holding
# the lock can call start_smartapi_feed() as its fallback without
# deadlocking itself.
_smartapi_switch_lock = threading.RLock()


def _parse_any_expiry(expiry_str):
    """Normalizes an expiry string to a date for comparison, accepting
    SmartAPI's format ('31JUL2026', no separators — used by
    list_expiries()/_smartapi_current_expiry), option_chain_json's format
    ('31-Jul-2026', dash-separated — used by the global EXPIRY/payload
    ["expiry"]), or Upstox's native ISO format ('2026-07-31' — used by
    brokers/upstox_client.py's list_expiries()/_upstox_current_expiry, see
    _resolve_upstox_chain_tokens() below). Returns None if it matches none
    of these."""
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _smartapi_feed_matches_displayed_expiry(payload_expiry_str):
    """True only if the expiry currently being streamed by the SmartAPI feed
    is the SAME expiry the dashboard is actually displaying right now.
    _resolve_chain_tokens() picks list_expiries()[0] independently of
    option_chain_json's own EXPIRY global — they usually agree (both
    default to "nearest available"), but aren't guaranteed to (e.g. a
    NEAR/MONTHLY tab being the active view). Merging ticks for the wrong
    expiry into the displayed chain would silently show the wrong
    contract's prices, so this gate must pass before any state merge."""
    return _matches_displayed_expiry(
        _smartapi_current_expiry, payload_expiry_str, _parse_any_expiry
    )


def _resolve_chain_tokens(target_symbol, strikes_around_atm, expiry=None):
    """Blocking REST calls — resolves the ATM chain for target_symbol and
    returns (exchange, token_meta, expiry_ddmmmyyyy) or None if it couldn't
    be built. The expiry is returned (not just used internally) so callers
    can track exactly which expiry the SmartAPI feed is streaming — this
    matters because list_expiries()[0] here is resolved independently of
    option_chain_json's own EXPIRY global, and the two are NOT guaranteed
    to agree (e.g. if the dashboard is showing a NEAR/MONTHLY tab rather
    than the nearest expiry). Merging SmartAPI ticks into the wrong
    expiry's displayed rows would silently corrupt the chain, so tracking
    this lets sync logic verify a match before merging (see
    _sync_smartapi_row_into_payload below).

    `expiry` (optional): a specific expiry to resolve to, accepted in
    EITHER SmartAPI's format ('31JUL2026') or option_chain_json's format
    ('31-Jul-2026') — matched via _parse_any_expiry() so callers (the
    dashboard's expiry dropdown, ws_handler's ?expiry= query param) don't
    need to know which format list_expiries() itself uses. Falls back to
    the nearest expiry (expiries[0]) if omitted, or if the requested
    expiry isn't actually available for this symbol."""
    exchange = "BFO" if target_symbol in _BSE_SYMBOLS else "NFO"

    expiries = market_data.list_expiries(target_symbol, exchange=exchange)
    if not expiries:
        print(
            f"[smartapi] No expiries found for {target_symbol}, skipping feed",
            flush=True,
        )
        return None

    if expiry:
        target_date = _parse_any_expiry(expiry)
        resolved_expiry = next(
            (e for e in expiries if _parse_any_expiry(e) == target_date), None
        )
        if resolved_expiry is None:
            print(
                f"[smartapi] Requested expiry '{expiry}' not available for "
                f"{target_symbol} (have: {expiries}) — falling back to nearest",
                flush=True,
            )
            resolved_expiry = expiries[0]
    else:
        resolved_expiry = expiries[0]

    chain = market_data.get_atm_chain(
        target_symbol, resolved_expiry, strikes_around_atm, exchange=exchange
    )
    if not chain:
        print(
            f"[smartapi] Could not build ATM chain for {target_symbol}, skipping feed",
            flush=True,
        )
        return None

    token_meta = {}
    skipped = 0
    for row in chain["rows"] or []:
        tok = row.get("token") or row.get("instrument_key")
        if not tok:
            skipped += 1
            continue
        token_meta[str(tok)] = {"strike": row.get("strike"), "option_type": row.get("type")}
    if skipped and not token_meta:
        print(
            f"[smartapi] No broker tokens resolved for {target_symbol} {resolved_expiry} "
            f"(provider returned {skipped} token-less rows) — live feed disabled, "
            "falling back to REST poll",
            flush=True,
        )

    # Also resolve the underlying's own token so the SmartAPI feed can
    # stream spot at the same tick rate as the option legs, instead of spot
    # only ever coming from run_pipeline_once()'s slower NSE/BSE REST poll
    # (POLL_SECONDS). INDEX_TOKENS is keyed by underlying symbol and holds
    # its OWN {"token":.., "exchange": "NSE"|"BSE"} — not just a bare token
    # — since the index lives on the cash exchange, not NFO/BFO like the
    # option legs above. None if this symbol has no entry there yet, in
    # which case spot just keeps falling back to the REST poll as it does
    # today.
    index_info = market_data.index_tokens().get(target_symbol)
    index_token = None
    index_exchange_type = None
    if index_info is None:
        print(
            f"[index-quote] No INDEX_TOKENS entry for {target_symbol} — "
            f"spot will only update via the slower REST poll, not the live feed",
            flush=True,
        )
    else:
        index_token = index_info["token"]
        # INDEX_TOKENS' exchange is "NSE"/"BSE" (cash market); EXCHANGE_TYPE's
        # keys for cash market are "NSE_CM"/"BSE_CM" — NOT the same strings
        # as the "NSE"/"BSE" used elsewhere for option_chain_json.EXCHANGE.
        index_exchange_type = index_info["exchange"] + "_CM"
        # Tagged "INDEX" (not "CE"/"PE") so TickAggregator.on_tick() routes
        # it to the spot buffer instead of trying to treat it as an option
        # leg keyed by strike.
        token_meta[str(index_token)] = {"strike": None, "option_type": "INDEX"}

    # Current-month futures token — subscribed alongside the index token
    # SPECIFICALLY so the chart can show a real VWAP/volume: SmartAPI's
    # index token doesn't reliably populate average_traded_price/volume
    # (OHLC comes back 0 for pure index tokens — indices aren't traded
    # instruments), but the futures contract genuinely trades and carries
    # both fields. Tagged "FUT" (not "INDEX"/"CE"/"PE") — REQUIRES
    # TickAggregator.on_tick() to actually understand this tag before any
    # tick for it does anything; see _resolve_futures_token()'s docstring.
    futures_token, futures_exchange_type = _resolve_futures_token(
        target_symbol, exchange
    )
    if futures_token:
        token_meta[str(futures_token)] = {"strike": None, "option_type": "FUT"}

    return (
        exchange,
        token_meta,
        resolved_expiry,
        index_token,
        index_exchange_type,
        futures_token,
        futures_exchange_type,
    )


def _resolve_futures_token(target_symbol, exchange):
    """Resolves target_symbol's current-month futures (exchange, token) for
    the SmartAPI feed's VWAP/volume subscription — SEPARATE from
    _resolve_live_order_token()'s FUT branch (that one's for live order
    placement and explicitly refuses/returns None today; this one's for a
    read-only tick subscription, lower stakes if it's briefly wrong).

    NOT WIRED YET: mTerminals.smartapi_client only exposes find_option_token
    (CE/PE) and INDEX_TOKENS (cash-market index) in what's imported at the
    top of this file — neither resolves a futures contract's token. Before
    this can return anything real, confirm with smartapi_client.py whether
    an equivalent (e.g. a FUTURES_TOKENS dict, or a find_future_token()
    analogous to find_option_token) already exists there, or needs adding.
    ScripMaster instrument type for index futures is "FUTIDX" if writing
    the lookup from scratch (mirrors "OPTIDX" used for CE/PE presumably).

    Returns (None, None) until that's resolved — subscription code below
    already treats a None token as "skip", so this is safe to leave as a
    no-op stub."""
    return None, None


# ── Upstox live feed (parallel to the SmartAPI block above) ───────────────
# Selected instead of the SmartAPI block via LIVE_FEED_PROVIDER=="UPSTOX"
# (see switch_symbol() and main() below) — mutually exclusive with it, not
# layered on top of it. Upstox has no separate NFO/BFO-vs-cash-exchange
# subscribe split the way SmartAPI's EXCHANGE_TYPE needs (see
# upstox_ws_client.py's module docstring): every leg, the index, and (if
# ever wired) futures all live in ONE instrument_key namespace, subscribed
# through a single call — so the state below is a strict subset of the
# SmartAPI block's (_upstox_exchange/_upstox_index_exchange have no
# equivalent here, there's nothing to track per-exchange).

_upstox_stream = None
_upstox_aggregator = None
_upstox_loop = None  # captured once at startup, reused for symbol switches
_upstox_keys = None  # instrument_key list currently subscribed, for unsubscribe
_upstox_current_expiry = None  # ISO 'YYYY-MM-DD' expiry the Upstox feed is streaming

# Same reentrant-serialization purpose as _smartapi_switch_lock above — see
# that lock's docstring for the exact race it closes (a backgrounded
# startup call racing a switch's fallback call into the same start_*_feed()).
_upstox_switch_lock = threading.RLock()


def _upstox_feed_matches_displayed_expiry(payload_expiry_str):
    """Upstox analog of _smartapi_feed_matches_displayed_expiry() above —
    same purpose (don't merge a stale-expiry tick delta into the
    currently-displayed chain), gated on _upstox_current_expiry instead.
    _parse_any_expiry() accepts Upstox's ISO expiry format alongside
    SmartAPI's (see its docstring)."""
    return _matches_displayed_expiry(
        _upstox_current_expiry, payload_expiry_str, _parse_any_expiry
    )


def _resolve_upstox_chain_tokens(target_symbol, strikes_around_atm, expiry=None):
    return _resolve_upstox_feed_tokens(
        target_symbol, strikes_around_atm, expiry,
        is_bse=lambda symbol: symbol in _BSE_SYMBOLS,
        parse_expiry=_parse_any_expiry,
        report=lambda message: print(message, flush=True),
    )


def start_upstox_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    """Upstox analog of start_smartapi_feed() below — same lifecycle and
    locking discipline (see that function's docstring for the exact race
    _upstox_switch_lock closes). Imports UpstoxTickStream lazily, here and
    only here, so booting with LIVE_FEED_PROVIDER left at its SMARTAPI
    default never requires upstox-python-sdk to be installed."""
    global _upstox_stream, _upstox_aggregator, _upstox_loop
    global _upstox_keys, _upstox_current_expiry

    from brokers.upstox_ws_client import UpstoxTickStream

    with _upstox_switch_lock:
        if _upstox_stream is not None:
            target_symbol = (underlying or SYMBOL).upper()
            print(
                f"[upstox] Feed already running, switching to {target_symbol} instead of starting a new one",
                flush=True,
            )
            _switch_upstox_symbol_blocking(target_symbol, strikes_around_atm, expiry)
            return

        _upstox_loop = loop
        target_symbol = (underlying or SYMBOL).upper()

        resolved = _resolve_upstox_chain_tokens(
            target_symbol, strikes_around_atm, expiry
        )
        if resolved is None:
            return
        token_meta, resolved_expiry, index_key = resolved

        _upstox_aggregator = TickAggregator(
            token_meta,
            loop,
            _upstox_sync_and_broadcast,
            tick_event=_TICK_ACTIVITY_EVENT,
        )
        _upstox_aggregator.start()

        _upstox_stream = UpstoxTickStream(
            on_tick=_upstox_aggregator.on_tick, mode="full"
        )
        _upstox_stream.connect()
        threading.Thread(
            target=_upstox_stream.run_forever_with_reconnect, daemon=True
        ).start()
        time.sleep(2)  # let the WS connection establish before subscribing

        keys = list(token_meta.keys())
        _upstox_stream.subscribe(keys)
        _upstox_keys = keys
        _upstox_current_expiry = resolved_expiry

        option_count = len(keys) - (1 if index_key else 0)
        print(
            f"[upstox] Streaming {option_count} {target_symbol} option legs"
            f"{' + spot' if index_key else ''} (expiry {resolved_expiry})",
            flush=True,
        )


def _switch_upstox_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    """Upstox analog of _switch_smartapi_symbol_blocking() below — reuses
    the existing WS connection, no socket close/reopen. Guarded by
    _upstox_switch_lock so two rapid switches can't interleave their
    unsubscribe/subscribe calls (same reasoning as the SmartAPI version)."""
    global _upstox_keys, _upstox_current_expiry

    with _upstox_switch_lock:
        if _upstox_stream is None or _upstox_aggregator is None:
            # Feed never started at boot (LIVE_FEED_PROVIDER pointed
            # elsewhere) — start it now on whatever loop we have rather
            # than silently no-op'ing the switch.
            loop = _upstox_loop if _upstox_loop is not None else _MAIN_LOOP
            if loop is not None:
                start_upstox_feed(loop, new_symbol, strikes_around_atm, expiry)
            return

        resolved = _resolve_upstox_chain_tokens(
            new_symbol.upper(), strikes_around_atm, expiry
        )
        if resolved is None:
            return
        new_token_meta, new_expiry, new_index_key = resolved
        new_keys = list(new_token_meta.keys())

        if _upstox_keys:
            try:
                _upstox_stream.unsubscribe(_upstox_keys)
            except Exception as e:
                print(
                    f"[upstox] Unsubscribe failed (continuing anyway): {e}", flush=True
                )

        _upstox_aggregator.update_token_meta(new_token_meta)
        _upstox_stream.subscribe(new_keys)

        _upstox_keys = new_keys
        _upstox_current_expiry = new_expiry
        option_count = len(new_keys) - (1 if new_index_key else 0)
        print(
            f"[upstox] Switched stream to {option_count} {new_symbol.upper()} option legs"
            f"{' + spot' if new_index_key else ''} (expiry {new_expiry})",
            flush=True,
        )


def restart_upstox_feed(new_symbol, new_expiry=None):
    """Upstox analog of restart_smartapi_feed() below — same fire-and-
    forget threading discipline. Call this from switch_symbol()."""
    threading.Thread(
        target=_switch_upstox_symbol_blocking,
        args=(new_symbol, 10, new_expiry),
        daemon=True,
    ).start()


# ── Shoonya live feed (parallel to the SmartAPI/Upstox blocks above) ──────
# Selected instead of either via LIVE_FEED_PROVIDER=="SHOONYA" — mutually
# exclusive with both, not layered on top. Shoonya's Noren feed identifies
# instruments as flat "EXCH|TOKEN" strings (see shoonya_ws_client.py's
# module docstring) rather than SmartAPI's per-exchangeType subscribe lists
# or Upstox's single instrument_key namespace, so the state below tracks a
# plain set of subscribed strings — no separate exchange-type bucketing
# needed the way the SmartAPI block requires.

_shoonya_stream = None
_shoonya_aggregator = None
_shoonya_loop = None  # captured once at startup, reused for symbol switches
_shoonya_instruments = (
    None  # 'EXCH|TOKEN' strings currently subscribed, for unsubscribe
)
_shoonya_current_expiry = (
    None  # expiry (Shoonya's 'DD-Mon-YYYY' format) the feed is streaming
)

# Same reentrant-serialization purpose as _smartapi_switch_lock/
# _upstox_switch_lock above.
_shoonya_switch_lock = threading.RLock()


def _shoonya_feed_state():
    """Snapshot the legacy module globals for the extracted feed service."""
    return _ShoonyaFeedState(
        stream=_shoonya_stream,
        aggregator=_shoonya_aggregator,
        loop=_shoonya_loop,
        instruments=_shoonya_instruments,
        current_expiry=_shoonya_current_expiry,
    )


def _store_shoonya_feed_state(state):
    """Reflect extracted service state back into the legacy globals."""
    global _shoonya_stream, _shoonya_aggregator, _shoonya_loop
    global _shoonya_instruments, _shoonya_current_expiry
    _shoonya_stream = state.stream
    _shoonya_aggregator = state.aggregator
    _shoonya_loop = state.loop
    _shoonya_instruments = state.instruments
    _shoonya_current_expiry = state.current_expiry


def _shoonya_feed_matches_displayed_expiry(payload_expiry_str):
    """Shoonya analog of _smartapi_feed_matches_displayed_expiry() /
    _upstox_feed_matches_displayed_expiry() above — same purpose, gated
    on _shoonya_current_expiry instead. _parse_any_expiry() accepts
    Shoonya's 'DD-Mon-YYYY' format alongside SmartAPI's/Upstox's (see
    that helper's docstring)."""
    return _matches_displayed_expiry(
        _shoonya_current_expiry, payload_expiry_str, _parse_any_expiry
    )


def _resolve_shoonya_chain_tokens(target_symbol, strikes_around_atm, expiry=None):
    """Shoonya analog of _resolve_chain_tokens()/_resolve_upstox_chain_tokens()
    above — builds the 'EXCH|TOKEN' subscribe-string set the live tick feed
    should use for target_symbol. Talks to brokers/shoonya_market_data.py
    directly (not through the `market_data` singleton) for the same reason
    _resolve_upstox_chain_tokens() does: MARKET_DATA_PROVIDER independently
    selects which REST adapter backs the singleton, and this feed should
    work correctly even when the singleton is pointed elsewhere.

    Returns (instrument_meta, expiry_ddmmmyyyy, index_instrument) or None.
    `instrument_meta` is keyed by 'EXCH|TOKEN' string (matching what
    ShoonyaTickStream.subscribe() expects and what its ticks report back
    as `token` after stripping the exchange prefix — see
    _shoonya_sync_and_broadcast() below for the strip)."""
    return _resolve_shoonya_feed_tokens(
        target_symbol,
        strikes_around_atm,
        expiry,
        lambda symbol: symbol in _BSE_SYMBOLS,
        _parse_any_expiry,
        lambda message: print(message, flush=True),
    )


def start_shoonya_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    """Shoonya analog of start_smartapi_feed()/start_upstox_feed() below —
    same lifecycle and locking discipline (see start_smartapi_feed()'s
    docstring for the exact race _shoonya_switch_lock closes)."""
    from brokers.shoonya_ws_client import ShoonyaTickStream

    with _shoonya_switch_lock:
        if _shoonya_stream is not None:
            target_symbol = (underlying or SYMBOL).upper()
            print(
                f"[shoonya] Feed already running, switching to {target_symbol} instead of starting a new one",
                flush=True,
            )
            _switch_shoonya_symbol_blocking(target_symbol, strikes_around_atm, expiry)
            return
        target_symbol = (underlying or SYMBOL).upper()
        state = _shoonya_feed_state()
        _start_shoonya_feed_new(
            state, loop, target_symbol, strikes_around_atm, expiry,
            _resolve_shoonya_chain_tokens, TickAggregator,
            _shoonya_sync_and_broadcast, _TICK_ACTIVITY_EVENT,
            ShoonyaTickStream, threading.Thread, time.sleep,
            lambda message: print(message, flush=True),
        )
        _store_shoonya_feed_state(state)


def _switch_shoonya_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    """Shoonya analog of _switch_smartapi_symbol_blocking()/
    _switch_upstox_symbol_blocking() above — reuses the existing WS
    connection, no socket close/reopen. Guarded by _shoonya_switch_lock so
    two rapid switches can't interleave their unsubscribe/subscribe calls."""
    with _shoonya_switch_lock:
        if _shoonya_stream is None or _shoonya_aggregator is None:
            # Feed never started at boot (LIVE_FEED_PROVIDER pointed
            # elsewhere) — start it now on whatever loop we have rather
            # than silently no-op'ing the switch.
            loop = _shoonya_loop if _shoonya_loop is not None else _MAIN_LOOP
            if loop is not None:
                start_shoonya_feed(
                    loop, new_symbol, strikes_around_atm, expiry
                )
            return

        state = _shoonya_feed_state()
        _switch_shoonya_feed_existing(
            state, new_symbol.upper(), strikes_around_atm, expiry,
            _resolve_shoonya_chain_tokens,
            lambda message: print(message, flush=True),
        )
        _store_shoonya_feed_state(state)


def restart_shoonya_feed(new_symbol, new_expiry=None):
    """Shoonya analog of restart_smartapi_feed()/restart_upstox_feed()
    above — same fire-and-forget threading discipline. Call this from
    switch_symbol()."""
    threading.Thread(
        target=_switch_shoonya_symbol_blocking,
        args=(new_symbol, 10, new_expiry),
        daemon=True,
    ).start()


def start_smartapi_feed(loop, underlying=None, strikes_around_atm=10, expiry=None):
    """Starts the ONE persistent SmartAPI WS connection for the life of the
    server process, and does the initial subscription for `underlying`
    (defaults to SYMBOL). Later symbol switches reuse this same connection
    via switch_smartapi_symbol() instead of reconnecting — Angel One's WS
    appears to allow only one active connection per feed session, so
    closing/reopening on every switch is both slower and riskier than just
    unsubscribing the old tokens and subscribing the new ones.

    `expiry` (optional): passed straight through to _resolve_chain_tokens()
    — see its docstring for accepted formats and fallback behavior.

    Guarded by _smartapi_switch_lock (reentrant) for its full duration —
    without this, the initial startup call (backgrounded via
    asyncio.to_thread) could run concurrently with a switch's fallback call
    into this same function if a client connects and switches symbols
    before startup finishes, creating two independent WS connections. See
    the lock's own docstring for the exact failure mode this closes."""
    global _smartapi_stream, _smartapi_aggregator, _smartapi_loop
    global _smartapi_exchange, _smartapi_tokens, _smartapi_current_expiry
    global _smartapi_index_token, _smartapi_index_exchange
    global _smartapi_futures_token, _smartapi_futures_exchange

    with _smartapi_switch_lock:
        if _smartapi_stream is not None:
            # A feed is already running (this call lost the race, or is a
            # redundant fallback invocation from a switch that arrived
            # after startup actually finished) — switch symbol on the
            # EXISTING connection instead of creating a second one.
            target_symbol = (underlying or SYMBOL).upper()
            print(
                f"[smartapi] Feed already running, switching to {target_symbol} instead of starting a new one",
                flush=True,
            )
            _switch_smartapi_symbol_blocking(target_symbol, strikes_around_atm, expiry)
            return

        _smartapi_loop = loop
        target_symbol = (underlying or SYMBOL).upper()

        resolved = _resolve_chain_tokens(target_symbol, strikes_around_atm, expiry)
        if resolved is None:
            return
        (
            exchange,
            token_meta,
            expiry,
            index_token,
            index_exchange_type,
            futures_token,
            futures_exchange_type,
        ) = resolved

        _smartapi_aggregator = TickAggregator(
            token_meta,
            loop,
            _smartapi_sync_and_broadcast,
            tick_event=_TICK_ACTIVITY_EVENT,
        )
        _smartapi_aggregator.start()

        _smartapi_stream = SmartTickStream(on_tick=_smartapi_aggregator.on_tick, mode=3)
        _smartapi_stream.connect()
        threading.Thread(
            target=_smartapi_stream.run_forever_with_reconnect, daemon=True
        ).start()
        time.sleep(2)  # let the WS connection establish before subscribing

        # Option legs subscribe under the F&O exchange (NFO/BFO) as before.
        # Futures token (if resolved) also lives under NFO/BFO, same as the
        # option legs — folded into this same subscribe call rather than a
        # separate one, since it's the same exchange type either way.
        option_tokens = [
            t
            for t in token_meta.keys()
            if t not in (str(index_token), str(futures_token))
        ]
        fo_tokens = option_tokens + ([str(futures_token)] if futures_token else [])
        _smartapi_stream.subscribe(EXCHANGE_TYPE[exchange], fo_tokens)
        _smartapi_exchange = exchange
        _smartapi_tokens = fo_tokens
        _smartapi_futures_token = str(futures_token) if futures_token else None
        _smartapi_futures_exchange = exchange if futures_token else None

        # The index token lives on its own cash exchange (NSE_CM/BSE_CM per
        # INDEX_TOKENS, not NFO/BFO) — a separate subscribe call. Tracked
        # separately from _smartapi_tokens/_smartapi_exchange so a later
        # unsubscribe (on symbol switch) targets the right exchange for
        # each rather than unsubscribing the index token under NFO/BFO,
        # which would silently no-op or error against AngelOne.
        if index_token:
            _smartapi_stream.subscribe(
                EXCHANGE_TYPE[index_exchange_type], [str(index_token)]
            )
            _smartapi_index_token = str(index_token)
            _smartapi_index_exchange = index_exchange_type
        else:
            _smartapi_index_token = None
            _smartapi_index_exchange = None

        _smartapi_current_expiry = expiry
        print(
            f"[smartapi] Streaming {len(option_tokens)} {target_symbol} option legs"
            f"{' + spot' if index_token else ''}{' + futures VWAP' if futures_token else ''} (expiry {expiry})",
            flush=True,
        )


def _switch_smartapi_symbol_blocking(new_symbol, strikes_around_atm=10, expiry=None):
    """Runs on its own thread (see restart_smartapi_feed) since
    _resolve_chain_tokens() makes blocking REST calls. Reuses the existing
    WS connection: unsubscribes the old symbol's tokens, swaps the
    aggregator's mapping, subscribes the new symbol's tokens. No socket
    close/reopen, so no reconnect race and no multi-second gap needed.

    `expiry` (optional): passed straight through to _resolve_chain_tokens()
    — lets an expiry-only switch (same symbol, different expiry) re-point
    the feed too, not just a symbol change.

    Guarded by _smartapi_switch_lock so two rapid switches can't interleave
    their unsubscribe/subscribe calls or stomp on _smartapi_tokens/
    _smartapi_exchange concurrently — see the lock's definition above for
    the failure mode this prevents."""
    global _smartapi_exchange, _smartapi_tokens, _smartapi_current_expiry
    global _smartapi_index_token, _smartapi_index_exchange
    global _smartapi_futures_token, _smartapi_futures_exchange

    with _smartapi_switch_lock:
        if _smartapi_stream is None or _smartapi_aggregator is None:
            # No feed running yet (e.g. switch happened before startup finished
            # initializing it, or this provider was never booted because
            # LIVE_FEED_PROVIDER pointed elsewhere) — fall back to a full
            # start on whatever loop we have (main loop last-resort).
            loop = _smartapi_loop if _smartapi_loop is not None else _MAIN_LOOP
            if loop is not None:
                start_smartapi_feed(
                    loop, new_symbol, strikes_around_atm, expiry
                )
            return

        resolved = _resolve_chain_tokens(new_symbol.upper(), strikes_around_atm, expiry)
        if resolved is None:
            return
        (
            new_exchange,
            new_token_meta,
            new_expiry,
            new_index_token,
            new_index_exchange_type,
            new_futures_token,
            new_futures_exchange_type,
        ) = resolved
        new_option_tokens = [
            t
            for t in new_token_meta.keys()
            if t not in (str(new_index_token), str(new_futures_token))
        ]
        new_fo_tokens = new_option_tokens + (
            [str(new_futures_token)] if new_futures_token else []
        )

        if _smartapi_tokens and _smartapi_exchange:
            try:
                _smartapi_stream.unsubscribe(
                    EXCHANGE_TYPE[_smartapi_exchange], _smartapi_tokens
                )
            except Exception as e:
                print(
                    f"[smartapi] Unsubscribe failed (continuing anyway): {e}",
                    flush=True,
                )

        # Index token was subscribed under a DIFFERENT exchange type
        # (NSE_CM/BSE_CM, not NFO/BFO) — must be unsubscribed under that
        # same exchange, or AngelOne silently ignores/errors the call.
        if _smartapi_index_token and _smartapi_index_exchange:
            try:
                _smartapi_stream.unsubscribe(
                    EXCHANGE_TYPE[_smartapi_index_exchange], [_smartapi_index_token]
                )
            except Exception as e:
                print(
                    f"[smartapi] Index unsubscribe failed (continuing anyway): {e}",
                    flush=True,
                )

        _smartapi_aggregator.update_token_meta(new_token_meta)
        _smartapi_stream.subscribe(EXCHANGE_TYPE[new_exchange], new_fo_tokens)

        if new_index_token:
            _smartapi_stream.subscribe(
                EXCHANGE_TYPE[new_index_exchange_type], [str(new_index_token)]
            )
            _smartapi_index_token = str(new_index_token)
            _smartapi_index_exchange = new_index_exchange_type
        else:
            _smartapi_index_token = None
            _smartapi_index_exchange = None

        _smartapi_futures_token = str(new_futures_token) if new_futures_token else None
        _smartapi_futures_exchange = new_exchange if new_futures_token else None
        _smartapi_exchange = new_exchange
        _smartapi_tokens = new_fo_tokens
        _smartapi_current_expiry = new_expiry
        print(
            f"[smartapi] Switched stream to {len(new_option_tokens)} {new_symbol.upper()} option legs"
            f"{' + spot' if new_index_token else ''}{' + futures VWAP' if new_futures_token else ''} (expiry {new_expiry})",
            flush=True,
        )


def restart_smartapi_feed(new_symbol, new_expiry=None):
    """Call this from switch_symbol(). Fire-and-forget: hands the actual
    unsubscribe/subscribe work to a background thread so the synchronous,
    fast-path switch_symbol() (called from inside async ws_handler())
    doesn't block on network calls. _smartapi_switch_lock (acquired inside
    _switch_smartapi_symbol_blocking) ensures that even if several of these
    threads pile up from rapid clicks, they execute one at a time in order
    rather than racing each other.

    `new_expiry` (optional): forwarded to _switch_smartapi_symbol_blocking so
    an expiry-only change (dashboard's expiry dropdown, not the symbol
    picker) actually re-points the live SmartAPI feed too — previously this
    was silently dropped here, so the feed always stayed pinned to whichever
    expiry was nearest at startup regardless of what the client requested."""
    threading.Thread(
        target=_switch_smartapi_symbol_blocking,
        args=(new_symbol, 10, new_expiry),
        daemon=True,
    ).start()


def run_pipeline_once():
    # Remap global runtime variables inside our new option_chain_json engine
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


async def index_quote_loop():
    """Periodic index quote updates — see its comment above.
    Pushes {"type": "indexQuotes", "payload": {...}} the same way
    index_quote_loop()'s dashboard.js's generic handler lands this at
    wsState.indexQuotes for free, which paper-trading.js's ptComputeIndex
    quotes reads once Live mode is on.
    """
    if not USE_INDEX_QUOTES:
        return
    others = [s for s in INDEX_TICKER_SYMBOLS if s != SYMBOL]
    if not others:
        return
    nse_others = [s for s in others if s not in _BSE_SYMBOLS]
    bse_others = [s for s in others if s in _BSE_SYMBOLS]
    while True:
        updates = {}

        for sym in others:
            try:
                raw = await asyncio.to_thread(
                    market_data.get_spot_quote,
                    sym,
                )

                if raw and raw.get("ltp") is not None:
                    ltp = float(raw["ltp"])

                    close = raw.get("close")
                    chg_pct = None

                    if close not in (None, 0, 0.0):
                        close = float(close)
                        chg_pct = ((ltp - close) / close) * 100.0

                    updates[sym] = {
                        "spot": ltp,
                        "spotChgPct": chg_pct,
                    }

            except Exception as exc:
                print(
                    f"[index-quote] {sym} broker quote failed: {exc}",
                    flush=True,
                )

        if updates:
            INDEX_QUOTES.update(updates)
            await broadcast({"type": "indexQuotes", "payload": updates})
            for sym, quote in updates.items():
                print(
                    f"[index-quote] {sym} spot={quote.get('spot')} "
                    f"chg%={quote.get('spotChgPct')}",
                    flush=True,
                )
        await asyncio.sleep(INDEX_QUOTE_SECONDS)


_funds_task = None  # the currently-running funds poll task, or None when stopped


async def _funds_poll_body():
    """One polling cycle, repeated until cancelled by stop_funds_polling().
    Pushes {"type": "funds", "payload": {...}} the same way index_quote_loop
    pushes indexQuotes — dashboard.js's generic handler lands this at
    wsState.funds for free, which paper-trading.js's ptComputeFundSummary()
    reads once Live mode is on.

    Deliberately NOT gated on LIVE_TRADING_ENABLED — that flag guards
    whether REAL ORDERS can be placed (real money, restart-only by design,
    see its own comment above), but reading account balance moves no
    money and carries no execution risk. Gating it the same way would
    mean a full server restart just to see your own funds, which is the
    actual problem this replaces: start/stop_funds_polling() below are
    called from a {"type":"toggle_live_mode",...} WS message, so flipping
    the dashboard's LIVE pill starts/stops this over the live socket
    connection instead, no restart needed — same pattern switch_symbol()
    already uses for changing the active symbol mid-session.
    """
    global LAST_FUNDS
    while True:
        try:
            # get_funds() makes a real blocking HTTP call to AngelOne (and
            # may trigger a re-login via _session.call()) — offload it the
            # same way run_pipeline_once()/fetch_nse_index_quotes_sync()
            # already are, never inline on the event loop.
            funds = await asyncio.to_thread(smartapi_get_funds)
            LAST_FUNDS = funds
            await broadcast({"type": "funds", "payload": funds})
            print(
                f"[funds] available={funds.get('available_margin')} "
                f"utilised={funds.get('utilised_margin')}",
                flush=True,
            )
        except Exception as e:
            # A failed funds poll (session hiccup, AngelOne rate limit,
            # network blip) should never take down the loop — same
            # defensive posture as engine_loop's pipeline call. Skip this
            # cycle; the frontend keeps showing the last good LAST_FUNDS
            # (or "n/a" if there's never been one) until the next cycle
            # succeeds.
            print(
                f"[funds] poll failed (will retry in {FUNDS_POLL_SECONDS}s): {e}",
                flush=True,
            )
        await asyncio.sleep(FUNDS_POLL_SECONDS)


async def reconcile_loop():
    """Periodic position reconciliation — the real safety net for drift
    that has nothing to do with this app's own order flow (a position
    closed manually via the AngelOne app, a fill that landed without this
    process seeing it). Unlike _funds_poll_body, this IS gated on
    LIVE_TRADING_ENABLED: with live trading off there are no real
    positions to reconcile, and it would just be two empty broker lists
    diffing against each other every cycle. Runs unconditionally once
    live trading is enabled — not tied to the Live-mode UI toggle the way
    start/stop_funds_polling() is, since silent drift can happen whether
    or not anyone currently has the Live pill on."""
    global LAST_LIVE_POSITIONS
    while True:
        try:
            orders = await asyncio.to_thread(smartapi_get_order_book)
            positions = await asyncio.to_thread(smartapi_get_positions)
            LAST_LIVE_POSITIONS = positions
            result = _POSITION_RECONCILER.check(orders, positions, PT_LOT_SIZES)
            if result.clean:
                print("[position_reconciler] periodic check: clean", flush=True)
            else:
                print(
                    f"[position_reconciler] periodic check: "
                    f"{len(result.mismatches)} mismatch(es), "
                    f"{len(result.unparseable_symbols)} unparseable",
                    flush=True,
                )
                await _broadcast_reconciliation_alert(result, source="periodic")
        except Exception as e:
            # Same defensive posture as _funds_poll_body/engine_loop — a
            # failed reconciliation cycle should never take down the loop,
            # just skip to the next one.
            print(
                f"[position_reconciler] periodic check failed "
                f"(will retry in {POSITION_RECONCILE_SECONDS}s): {e}",
                flush=True,
            )
        await asyncio.sleep(POSITION_RECONCILE_SECONDS)


async def algo_status_loop():
    """Periodic {\"type\":\"algoStatus\",...} broadcast — see
    _build_algo_status(). Runs unconditionally (not gated on
    LIVE_TRADING_ENABLED) so the dashboard's status panel always shows an
    accurate picture, including the common case of confirming live
    trading/auto-execution are OFF, not just when they're armed."""
    global LAST_ALGO_STATUS
    while True:
        try:
            LAST_ALGO_STATUS = _build_algo_status()
            await broadcast({"type": "algoStatus", "payload": LAST_ALGO_STATUS})
        except Exception as e:
            # Same defensive posture as every other periodic loop here —
            # a bad read (e.g. a locked SQLite file mid-write) should
            # never take the loop down, just skip to the next cycle.
            print(
                f"[algo-status] poll failed (will retry in {ALGO_STATUS_POLL_SECONDS}s): {e}",
                flush=True,
            )
        await asyncio.sleep(ALGO_STATUS_POLL_SECONDS)


def start_funds_polling():
    """Idempotent — a second toggle-on while already running is a no-op,
    not a duplicate poller."""
    global _funds_task
    if _funds_task is not None and not _funds_task.done():
        return
    print("[funds] starting funds polling (live mode toggled on)", flush=True)
    _funds_task = _create_background_task(_funds_poll_body(), "funds_poll")


def stop_funds_polling():
    global _funds_task, LAST_FUNDS
    if _funds_task is not None:
        _funds_task.cancel()
        _funds_task = None
        print("[funds] stopped funds polling (live mode toggled off)", flush=True)
    # Clear LAST_FUNDS too, not just stop broadcasting — otherwise a client
    # that reconnects while polling is off would still get handed a
    # possibly-stale real-money figure in ws_handler's "hand over what we
    # already have" init snapshot, well after it stopped being refreshed.
    LAST_FUNDS = None


async def _get_node_session():
    global _NODE_SESSION
    if _NODE_SESSION is None or _NODE_SESSION.closed:
        _NODE_SESSION = aiohttp.ClientSession()
    return _NODE_SESSION


async def _post_to_node(payload: dict):
    if not USE_RELAY:
        return
    try:
        session = await _get_node_session()
        async with session.post(
            "http://localhost:4000/api/broadcast",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=2),
        ) as resp:
            await resp.read()
    except Exception as e:
        print(f"[node-relay] failed: {e}")


async def engine_loop():
    global LAST_PAYLOAD, LAST_PAYLOAD_AT, _LAST_SENT, _EOD_DONE_DATE, _LAST_SESSION_DATE
    global _PIPELINE_TASK
    while True:
        tick_start = time.monotonic()

        now = datetime.now()

        # New trading day → reset the SmartAPI aggregator's OI session
        # baselines so yesterday's session-open OI doesn't leak into
        # today's changeinOpenInterest. Same per-day-flag pattern as
        # _EOD_DONE_DATE below.
        if _LAST_SESSION_DATE != now.date():
            _LAST_SESSION_DATE = now.date()
            for _agg_name, _agg in (
                ("smartapi", _smartapi_aggregator),
                ("upstox", _upstox_aggregator),
                ("shoonya", _shoonya_aggregator),
            ):
                if _agg is not None:
                    _agg.reset_session()
                    print(
                        f"[{_agg_name}] Reset OI session baseline for new trading day {now.date()}",
                        flush=True,
                    )
            # Futures OI session baseline (Market Regime input) — same
            # per-day reset as the option-chain aggregator above, see
            # oi/futures_oi_tracker.py's class docstring for why this is a
            # separate tracker rather than a field on _smartapi_aggregator.
            _get_futures_oi_tracker().reset_session()
            print(
                f"[futures_oi] Reset futures OI session baseline for new trading day {now.date()}",
                flush=True,
            )

        if (
            is_trading_day(now)
            and now.time() >= EOD_TRIGGER_TIME
            and _EOD_DONE_DATE != now.date()
        ):
            _EOD_DONE_DATE = (
                now.date()
            )  # set before await, so a slow fetch can't cause a double-fire
            print(f"[eod] triggering EOD fetch for {now.date()}", flush=True)
            eod_task = asyncio.create_task(
                asyncio.to_thread(fetch_all_eod, now, True)
            )  # save=True
            eod_task.add_done_callback(_eod_task_done)

            # Cash-market FII/DII net flow (₹Cr) — separate NSE endpoint from
            # fao_participant_oi above, so it's its own task/callback: a
            # failure here shouldn't be conflated with (or block) the
            # participant-OI EOD fetch's own success/failure reporting.
            flow_task = asyncio.create_task(asyncio.to_thread(record_today_flow))
            flow_task.add_done_callback(_flow_task_done)

        if _PIPELINE_TASK is None:
            _PIPELINE_STATUS["startedAt"] = datetime.now().astimezone().isoformat()
            _PIPELINE_TASK = asyncio.create_task(_run_pipeline_locked())
        try:
            # shield() is important: a timeout must not cancel to_thread's
            # worker, which cannot be stopped safely and may be updating the
            # pipeline's module-level runtime configuration. We retain the
            # task and collect its result on a later loop instead of starting
            # an overlapping REST pass.
            payload = await asyncio.wait_for(
                asyncio.shield(_PIPELINE_TASK),
                timeout=PIPELINE_TIMEOUT_SECONDS,
            )
            _PIPELINE_TASK = None
            await _publish_pipeline_status("LIVE")
        except asyncio.TimeoutError:
            payload = None
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
                f"{_md_label} websocket overlay remains active"
                if USE_SMARTAPI
                else f"public REST polling will retry; {_md_label} remains disabled"
            )
            print(
                f"[pipeline] DELAYED after {elapsed:.2f}s — {overlay_state}", flush=True
            )
        except Exception as e:
            payload = None
            _PIPELINE_TASK = None
            await _publish_pipeline_status("DELAYED", f"Analytics pipeline failed: {e}")
            print(f"[pipeline] FAILED: {e}", flush=True)
        pipeline_elapsed = time.monotonic() - tick_start
        METRICS.observe_pipeline(payload is not None, pipeline_elapsed)

        if payload is not None:
            # Transport/session context is part of the canonical payload so the
            # Dashboard can distinguish an exchange closure from a broken feed.
            payload["marketSession"] = _market_session_status(now)
            # Strategy -> execution bridge: hand this tick's decision
            # block to the auto-executor. No-op unless
            # AUTO_STRATEGY_EXECUTION_ENABLED=true; see
            # decision/auto_executor.py. Fire-and-forget task so a slow
            # broker call (position-book fetch, order placement) can't
            # delay this tick's own broadcast below.
            decision_block = payload.get("decision")
            if decision_block:
                _create_background_task(
                    _AUTO_EXECUTOR.maybe_execute(decision_block, SYMBOL, EXPIRY),
                    "auto_executor",
                )

            # Feed NSE's authoritative changeinOpenInterest into the live
            # feed aggregators' OI baselines every cycle (not just at feed
            # startup — start_*_feed() runs concurrently with this loop via
            # asyncio.to_thread, so there's no guaranteed ordering).
            # seed_session_baseline() only fills tokens without an existing
            # baseline, so calling this every cycle is safe — it just closes
            # the gap for whichever tokens ticked before NSE data was
            # available. Previously only the SmartAPI aggregator was seeded;
            # Upstox/Shoonya fell back to first-tick bootstrap, which
            # re-anchored ceDOI/peDOI on every symbol switch / feed restart
            # (the "ChgOI changing abruptly" symptom).
            #
            # Guard against r[oi_field] == 0: NSE's parse_option_chain_
            # response() (market_api.py) returns None for a strike/side it
            # has no quote for (edge-of-chain strikes missing CE or PE),
            # and mTerminals_json.py's _to_int() silently maps that None
            # to 0 — indistinguishable here from a genuinely zero-OI
            # contract. Seeding a baseline of 0 from a FALSE zero would
            # stick for the rest of the session (setdefault won't
            # overwrite it later) and make that token's DOI read as its
            # full OI instead of the true change. Skipping it here just
            # leaves on_tick()'s own bootstrap-on-first-tick fallback to
            # establish that token's baseline instead — self-correcting,
            # never wrong the way a false zero would be.
            chain_rows = payload.get("chain", [])
            for aggregator in (
                _smartapi_aggregator,
                _upstox_aggregator,
                _shoonya_aggregator,
            ):
                if aggregator is None:
                    continue
                baselines = {}
                nse_rows_by_strike = {r["strike"]: r for r in chain_rows}
                for token, meta in aggregator.token_meta.items():
                    if meta.get("option_type") not in ("CE", "PE"):
                        continue
                    r = nse_rows_by_strike.get(meta["strike"])
                    if not r:
                        continue
                    oi_field = "ceOI" if meta["option_type"] == "CE" else "peOI"
                    chg_field = "ceChgOI" if meta["option_type"] == "CE" else "peChgOI"
                    if oi_field in r and chg_field in r and r[oi_field] != 0:
                        baselines[token] = r[oi_field] - r[chg_field]
                if baselines:
                    aggregator.seed_session_baseline(baselines)

            async with _MARKET_STREAM_LOCK:
                # Publish the new canonical snapshot and derive its wire
                # update atomically relative to SmartAPI's in-place patches.
                LAST_PAYLOAD = payload
                LAST_PAYLOAD_AT = datetime.now().astimezone()
                if not USE_DELTA or _LAST_SENT is None:
                    await broadcast({"type": "full", "payload": payload})
                else:
                    diff_start = time.monotonic()
                    # compute_diff walks the ENTIRE payload (all expiries, OI
                    # velocity buckets, virtual-OI, greeks) doing recursive
                    # old==new equality checks + keyed-list reconciliation.
                    # Keep the event loop responsive by using a worker while
                    # the stream lock prevents concurrent snapshot mutation.
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
            _create_background_task(_post_to_node(payload), "node_relay")
            print(
                f"[ws] broadcast tick -> {len(CONNECTED)} client(s) "
                f"(pipeline {pipeline_elapsed:.2f}s)",
                flush=True,
            )

            # Paper trading: check whether this tick's prices cross any
            # pending LIMIT orders, then re-broadcast portfolio/orders so
            # open positions' unrealized P&L stays live tick-to-tick even
            # with zero new orders placed (mirrors what dashboard.js's
            # ptLiveReprice() does client-side, but this also catches
            # LIMIT fills, which the client can't do on its own).
            current_prices = _build_current_prices(payload)
            PT_ENGINE.check_pending_orders(current_prices)
            await _broadcast_portfolio(current_prices)

        remaining = POLL_SECONDS - (time.monotonic() - tick_start)
        if remaining > 0:
            # POLL_SECONDS is a CEILING: fires anyway if nothing happens
            # (quiet market, public-only mode, or this symbol has no SmartAPI
            # feed — spot/OI stay on the old REST-poll cadence in that
            # case). MIN_TICK_RECOMPUTE_SECONDS is a FLOOR: even with
            # ticks flooding in continuously (every ~0.25s during market
            # hours — see TickAggregator.flush_interval), this loop won't
            # re-run the heavy Greeks/OI-velocity/GEX pipeline faster than
            # this floor. Without the floor, "wake on every tick" would
            # make this run MORE often than the old fixed poll, not less.
            floor_remaining = MIN_TICK_RECOMPUTE_SECONDS - (
                time.monotonic() - tick_start
            )
            if floor_remaining > 0:
                await asyncio.sleep(min(floor_remaining, remaining))
                remaining = POLL_SECONDS - (time.monotonic() - tick_start)

            if remaining > 0:
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
                            "[ws] tick activity — ticking early "
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
        elif pipeline_elapsed > POLL_SECONDS:
            print(
                f"[ws] WARNING: pipeline took {pipeline_elapsed:.2f}s, "
                f"longer than --poll-seconds {POLL_SECONDS}s — "
                f"broadcast cadence is bottlenecked by pipeline speed, not the sleep.",
                flush=True,
            )


# HTTP history handlers are intentionally thin adapters; caching and request
# parsing live in server.market_history_api instead of the feed coordinator.
_HISTORY_API = MarketHistoryApi(
    state=lambda: {
        "symbol": SYMBOL,
        "broker_services_enabled": BROKER_SERVICES_ENABLED,
        "index_tokens": _SMARTAPI_INDEX_TOKENS,
    },
    # Resolve these at request time so test seams and runtime configuration
    # never leave the API holding a stale provider function.
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
    """Return the process, transport, and market-feed health contract.

    A closed exchange is not itself a degraded service. During an open
    session, however, a missing or old canonical payload makes the service
    degraded even when the HTTP and WebSocket listeners are reachable.
    """
    smartapi_connected = False
    upstox_connected = False
    shoonya_connected = False
    if USE_SMARTAPI and LIVE_FEED_PROVIDER == "UPSTOX" and _upstox_stream is not None:
        connected_event = getattr(_upstox_stream, "_connected", None)
        upstox_connected = bool(connected_event and connected_event.is_set())
    elif (
        USE_SMARTAPI and LIVE_FEED_PROVIDER == "SHOONYA" and _shoonya_stream is not None
    ):
        connected_event = getattr(_shoonya_stream, "_connected", None)
        shoonya_connected = bool(connected_event and connected_event.is_set())
    elif USE_SMARTAPI and _smartapi_stream is not None:
        connected_event = getattr(_smartapi_stream, "_connected", None)
        smartapi_connected = bool(connected_event and connected_event.is_set())

    return _build_health_response({
        "process_started_at": PROCESS_STARTED_AT, "poll_seconds": POLL_SECONDS,
        "last_payload": LAST_PAYLOAD, "last_payload_at": LAST_PAYLOAD_AT,
        "connected_clients": len(CONNECTED), "symbol": SYMBOL, "expiry": EXPIRY,
        "broker_services_enabled": USE_SMARTAPI, "data_source": DATA_SOURCE,
        "live_feed_provider": LIVE_FEED_PROVIDER,
        "live_feed_active": USE_SMARTAPI and _feed_allowed(DATA_SOURCE),
        "pipeline_status": _PIPELINE_STATUS, "smartapi_connected": smartapi_connected,
        "upstox_connected": upstox_connected, "shoonya_connected": shoonya_connected,
    }, _market_session_status, now)


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


async def main():
    if not _host_is_loopback(WS_HOST):
        raise RuntimeError(
            f"refusing unsafe non-loopback bind {WS_HOST!r}: the WebSocket "
            "control channel has no remote-client authentication; use "
            "--host localhost or a loopback address"
        )
    # Must run before any task that can hit a broker API timeout
    # (start_smartapi_feed(), engine_loop(), ...) gets a chance to log
    # one -- see logging_config.py's RedactSensitiveHeaders for why:
    # without this, the SmartApi SDK's own error logging dumps the live
    # session Bearer token and API private key in plaintext on every
    # request failure.
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

    loop = asyncio.get_running_loop()
    global _MAIN_LOOP
    _MAIN_LOOP = loop
    # start_smartapi_feed() makes blocking REST calls (_resolve_chain_tokens)
    # and has an internal time.sleep(2) — calling it directly here would
    # freeze the event loop (and every already-connected client's WS
    # heartbeat) for its full duration. Same discipline as offloading
    # compute_diff() in engine_loop(): anything blocking goes through a
    # thread, never runs inline on the loop.
    if USE_SMARTAPI and _feed_allowed(LIVE_FEED_PROVIDER):
        _start_live_feed(LIVE_FEED_PROVIDER, loop)

    else:
        if USE_SMARTAPI:
            print(
                f"[feed] websocket overlay not started "
                f"(data source={DATA_SOURCE}, "
                f"feed provider={LIVE_FEED_PROVIDER})",
                flush=True,
            )
        else:
            print(
                "[broker] authenticated services disabled (BROKER_SERVICES_ENABLED=false) — "
                "no broker login, account/order REST call, or websocket connection; "
                "public daily ScripMaster allowed",
                flush=True,
            )

    try:
        _create_background_task(index_quote_loop(), "index_quote_loop")
        _create_background_task(bridge_loop(), "bridge_loop")
        _create_background_task(algo_status_loop(), "algo_status_loop")
        if LIVE_TRADING_ENABLED:
            _create_background_task(reconcile_loop(), "position_reconcile_loop")
        # No funds_loop() task here anymore — funds polling starts/stops
        # live via the {"type":"toggle_live_mode",...} WS message (see
        # ws_handler + start_funds_polling()/stop_funds_polling() above),
        # not at boot. Flipping the dashboard's LIVE pill controls it
        # directly over the socket, no server restart required.
        await engine_loop()
    finally:
        background_tasks = list(_BACKGROUND_TASKS)
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        if _NODE_SESSION is not None and not _NODE_SESSION.closed:
            await _NODE_SESSION.close()
        # oi_analysis.py now keeps oi_history_log.parquet in memory and only
        # flushes to disk periodically (see _FLUSH_INTERVAL_SECONDS) instead
        # of on every tick. Force a final write here so a clean shutdown
        # (Ctrl+C, restart) never loses up to a minute of unflushed history.
        try:
            from oi_analysis import flush_history_to_disk

            flush_history_to_disk()
        except Exception as e:
            print(f"[shutdown] Could not flush OI history: {e}")


if __name__ == "__main__":
    asyncio.run(main())
