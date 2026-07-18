import argparse
import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, time as dtime

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "backend"))

from nse_eod_fetch import fetch_all_eod, is_trading_day

import numpy as np
import orjson
import aiohttp
import websockets
from aiohttp import web

# option_chain_json.py parses sys.argv at import time — hide our own argv
# from it so it doesn't choke on ws_server_live's --symbol/--poll-seconds flags.
_real_argv = sys.argv
sys.argv = [_real_argv[0]]
import option_chain_json       # noqa: E402
import mTerminals_json         # noqa: E402
import market_api              # noqa: E402  (lightweight ticker-strip quotes; no argv parsing, doesn't need hiding)
sys.argv = _real_argv          # restore for our own argparse below

from paper_trading import PaperTradingEngine, _instrument_key, LOT_SIZES as PT_LOT_SIZES  # noqa: E402
from smartapi_client import (
    get_atm_chain, list_expiries, find_option_token, get_candle_data,
    place_order as smartapi_place_order,
    get_order_book as smartapi_get_order_book,
    get_funds as smartapi_get_funds,
    INDEX_TOKENS,
)
from smartapi_ws_client import SmartTickStream, EXCHANGE_TYPE
from smartapi_feed_adapter import TickAggregator
from smartapi_history import get_index_candles

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
_parser.add_argument("--poll-seconds", type=int, default=5)
_parser.add_argument("--min-tick-recompute-seconds", type=float, default=2.0,
                      help="Floor on how often SmartAPI tick activity can wake engine_loop "
                           "early. --poll-seconds becomes a ceiling (fires anyway if no ticks "
                           "arrive — quiet market, symbol has no SmartAPI feed, etc.); this is "
                           "the floor (never recompute faster than this even while ticks are "
                           "flooding in, since ticks arrive every ~0.25s during market hours — "
                           "waking on every single one would make the heavy Greeks/OI-velocity/ "
                           "GEX recompute run MORE often than the old fixed poll, not less).")
_parser.add_argument("--host", default="localhost")
_parser.add_argument("--port", type=int, default=8765)
_parser.add_argument("--http-port", type=int, default=5500, help="HTTP static file server port")
_parser.add_argument("--relay", action="store_true", help="Enable Node relay posting (off by default)")
_parser.add_argument("--no-extra-chains", action="store_true", help="Disable multi-expiry chains for faster performance")
_parser.add_argument("--strict-expiry", action="store_true", help="Don't auto-resolve to different expiry if requested expiry has no data")
_parser.add_argument("--no-virtual-oi", action="store_true", help="Disable VirtualOI model inference for faster performance")
_parser.add_argument("--no-delta", action="store_true", help="Always broadcast full payloads instead of deltas")
_parser.add_argument("--no-index-quotes", action="store_true",
                      help="Disable the NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX ticker-strip background fetch")
_parser.add_argument("--no-smartapi", action="store_true",
                      help="Disable the AngelOne SmartAPI websocket overlay entirely — run on "
                           "market_api.py's NSE/BSE REST polling (option_chain_json's --poll-seconds "
                           "cadence) only. SmartAPI is never the source of the chain's strikes/OI "
                           "structure (that always comes from market_api.py); this flag only turns "
                           "off the faster LTP/OI overlay ticks on top of it.")
_parser.add_argument("--strikes-each-side", type=int, default=None,
                      help="Override how many strikes each side of ATM option_chain_json computes "
                           "Greeks/OI-velocity/signal analytics for (engine's n_strikes_each_side). "
                           "Defaults to 50 under --no-smartapi (REST-only chains have no fast overlay "
                           "to compensate, so the analytics pane needs the wider engine-side window "
                           "up front) and 10 with SmartAPI enabled (matches start_smartapi_feed's own "
                           "strikes_around_atm default). Pass this explicitly to use the same value "
                           "in both modes.")
_parser.add_argument("--index-quote-seconds", type=int, default=20,
                      help="How often (s) to refresh the OTHER three indices' ticker quotes. "
                           "Kept separate from --poll-seconds because it runs the full pipeline "
                           "once per non-active symbol and shares NSE rate limits with it.")
_parser.add_argument("--funds-poll-seconds", type=int, default=15,
                      help="How often (s) to refresh real AngelOne account funds/margin "
                           "(getRMS) once funds polling is toggled on (see toggle_live_mode "
                           "in ws_handler — starts/stops live over the socket when the "
                           "dashboard's LIVE pill flips, no restart needed). "
                           "Kept independent of --poll-seconds since RMS limits don't need "
                           "tick-level freshness and this is a real network round-trip to "
                           "AngelOne on top of whatever the main pipeline is already doing.")
_parser.add_argument("--portfolio-poll-seconds", type=float, default=0.5,
                      help="Minimum interval (s) between paper-trading portfolio/orders "
                           "re-broadcasts triggered off the fast SmartAPI tick stream (see "
                           "_smartapi_sync_and_broadcast). Previously portfolio/orders only "
                           "went out once per --poll-seconds, inside engine_loop()'s slower "
                           "NSE/BSE REST pipeline tick — so with SmartAPI enabled, option "
                           "chain/spot LTP updated sub-second while positions' last_price/"
                           "unrealized_pnl in the Paper Trading panel stayed pinned to the "
                           "much slower --poll-seconds cadence. get_portfolio_summary() is "
                           "just a couple of small indexed SQLite reads (no network I/O), so "
                           "this can safely run much faster than --poll-seconds — throttled "
                           "here (rather than fired on every single tick) purely to avoid "
                           "flooding clients with WS messages when many strikes tick in a "
                           "tight burst. Set to 0 to broadcast on every SmartAPI tick with no "
                           "throttling at all.")
ARGS = _parser.parse_args()

SYMBOL       = ARGS.symbol.strip().upper()
EXPIRY       = ARGS.expiry
POLL_SECONDS = ARGS.poll_seconds
MIN_TICK_RECOMPUTE_SECONDS = ARGS.min_tick_recompute_seconds
WS_HOST      = ARGS.host
WS_PORT      = ARGS.port
HTTP_PORT    = ARGS.http_port
USE_RELAY    = ARGS.relay
USE_DELTA    = not ARGS.no_delta
USE_INDEX_QUOTES     = not ARGS.no_index_quotes
INDEX_QUOTE_SECONDS  = ARGS.index_quote_seconds
FUNDS_POLL_SECONDS   = ARGS.funds_poll_seconds
PORTFOLIO_POLL_SECONDS = ARGS.portfolio_poll_seconds
USE_SMARTAPI         = not ARGS.no_smartapi
STRIKES_EACH_SIDE    = ARGS.strikes_each_side if ARGS.strikes_each_side is not None else (10 if USE_SMARTAPI else 50)
option_chain_json.STRIKES_EACH_SIDE = STRIKES_EACH_SIDE

print(
    f"[feed] chain source: SmartAPI REST (via option_chain_json.py/smartapi_pipeline_adapter.py), "
    f"analytics recompute ceiling={POLL_SECONDS}s floor={MIN_TICK_RECOMPUTE_SECONDS}s "
    f"+ {'SmartAPI websocket overlay ENABLED' if USE_SMARTAPI else 'SmartAPI overlay DISABLED (--no-smartapi)'} "
    f"| market_api.py now used only for fetch_all_indices() (ffmc/Top Drivers-Draggers, "
    f"20s-cached — see DF_IDX_TTL_SECONDS in option_chain_json.py)",
    flush=True,
)
print(
    f"[paper-trading] portfolio fast-path broadcast: "
    f"{'every SmartAPI tick (no throttle)' if PORTFOLIO_POLL_SECONDS <= 0 else f'throttled to >= {PORTFOLIO_POLL_SECONDS}s'}"
    + ("" if USE_SMARTAPI else " (inactive — --no-smartapi, falls back to --poll-seconds cadence)"),
    flush=True,
)

# Top-bar ticker strip shows these four, always in this order (see
# dashboard.js INDEX_TICKER_ORDER — keep the two lists in sync). The
# currently-active SYMBOL's own quote already comes for free on every
# regular tick (payload["spot"]/["spotChange"]/["spotChgPct"]), so this
# loop only needs to fetch the OTHER three.
INDEX_TICKER_SYMBOLS = ["NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX"]
_BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}

CONNECTED = set()
LAST_PAYLOAD = None
_LAST_SENT = None
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
# unconditionally as a slower fallback (covers --no-smartapi mode and any
# gap while the SmartAPI feed is (re)connecting).
_LAST_PORTFOLIO_BROADCAST_TS = 0.0
EOD_TRIGGER_TIME = dtime(15, 45)  # run shortly after NSE cash market close (15:30)
_EOD_DONE_DATE = None             # tracks which date's EOD job already ran

# ── Live trading configuration ──────────────────────────────────────────
# Master switch — OFF by default. Must be explicitly set to actually place
# real orders on the AngelOne account. Read once at process start (not
# re-checked per-request) since flipping this on/off mid-session is a
# deliberate deploy-time decision, not something to toggle casually.
LIVE_TRADING_ENABLED = os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() == "true"

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

if LIVE_TRADING_ENABLED:
    print(
        f"[live-trading] ENABLED — max {LIVE_MAX_LOTS_PER_ORDER} lot(s)/order, "
        f"{LIVE_MAX_ORDERS_PER_MINUTE}/min. Kill switch: touch {LIVE_TRADING_KILL_SWITCH_FILE} to disable instantly.",
        flush=True,
    )
else:
    print("[live-trading] disabled (paper trading only) — set LIVE_TRADING_ENABLED=true to enable", flush=True)


def _live_trading_kill_switch_active():
    return os.path.exists(LIVE_TRADING_KILL_SWITCH_FILE)


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


def _resolve_live_order_token(symbol, instrument_type, expiry, strike):
    """Resolves (exchange, tradingsymbol, symboltoken) for a live order.
    Mirrors the same underlying/exchange logic used for the SmartAPI tick
    feed (_BSE_SYMBOLS -> BFO, else NFO) so live orders target the same
    contract space the dashboard is already streaming ticks for."""
    exchange = "BFO" if symbol in _BSE_SYMBOLS else "NFO"

    if instrument_type in ("CE", "PE"):
        # expiry here is option_chain_json's format ("14-Jul-2026"); SmartAPI's
        # ScripMaster uses "14JUL2026" (no separators) — convert before lookup.
        try:
            expiry_ddmmmyyyy = datetime.strptime(expiry, "%d-%b-%Y").strftime("%d%b%Y").upper()
        except (ValueError, TypeError):
            return None
        resolved = find_option_token(symbol, expiry_ddmmmyyyy, strike, instrument_type, exchange)
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


def _json_default(obj):
    """orjson doesn't natively handle numpy scalars — coerce them to native Python types."""
    if isinstance(obj, np.generic):       # covers float64, int64, bool_, etc.
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Type is not JSON serializable: {type(obj)}")


def compute_diff(old, new, key_field="strike"):
    """Recursively diff `new` against `old`. Returns only changed data, or None if identical.
    For lists of dicts, matches items by `key_field` if present, else replaces the whole list."""
    if old == new:
        return None

    if isinstance(new, dict) and isinstance(old, dict):
        out = {}
        for k, v in new.items():
            if k not in old:
                out[k] = v
            else:
                d = compute_diff(old[k], v, key_field)
                if d is not None:
                    out[k] = d
        removed = [k for k in old if k not in new]
        if removed:
            out["_removed"] = removed
        return out if out else None

    if isinstance(new, list) and isinstance(old, list):
        if new and isinstance(new[0], dict) and key_field in new[0]:
            old_by_key = {row.get(key_field): row for row in old if isinstance(row, dict)}
            changed_rows = []
            for row in new:
                k = row.get(key_field)
                old_row = old_by_key.get(k)
                if old_row is None or old_row != row:
                    changed_rows.append(row)
            new_keys = {row.get(key_field) for row in new}
            removed_keys = [k for k in old_by_key if k not in new_keys]
            if not changed_rows and not removed_keys:
                return None
            result = {"_keyed": True, "_key_field": key_field, "changed": changed_rows}
            if removed_keys:
                result["_removed_keys"] = removed_keys
            return result
        else:
            return new  # unkeyed list, replace wholesale if different

    return new  # scalars or type mismatch


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


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    CONNECTED.add(ws)
    t0 = time.monotonic()
    print(f"[ws] {datetime.now().isoformat(timespec='milliseconds')} New connection! Total: {len(CONNECTED)}", flush=True)

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

    try:
        # New clients need a full snapshot before they can apply deltas.
        # (If switch_symbol() just cleared LAST_PAYLOAD above, this is
        # skipped on purpose — better to wait for the next tick's real data
        # on the new symbol than hand back a snapshot of the old one.)
        if LAST_PAYLOAD is not None:
            msg_str = orjson.dumps({"type": "full", "payload": LAST_PAYLOAD}, default=_json_default).decode()
            await ws.send_str(msg_str)
        if INDEX_QUOTES:
            msg_str = orjson.dumps({"type": "indexQuotes", "payload": INDEX_QUOTES}, default=_json_default).decode()
            await ws.send_str(msg_str)
        # Real account funds (Live mode) — same "hand over what we already
        # have" treatment as INDEX_QUOTES above. Stays None/skipped for the
        # life of the process when LIVE_TRADING_ENABLED is false, since
        # funds_loop() never runs in that case.
        if LAST_FUNDS is not None:
            msg_str = orjson.dumps({"type": "funds", "payload": LAST_FUNDS}, default=_json_default).decode()
            await ws.send_str(msg_str)
        # Hand a new client whatever paper-trading state already exists
        # (positions/orders survive process restarts via SQLite) instead of
        # leaving the panel empty until the next place_order/tick.
        try:
            init_prices = _build_current_prices(LAST_PAYLOAD)
            await ws.send_str(orjson.dumps(
                {"type": "portfolio", "payload": PT_ENGINE.get_portfolio_summary(init_prices)},
                default=_json_default).decode())
            await ws.send_str(orjson.dumps(
                {"type": "orders", "payload": PT_ENGINE.get_orders()},
                default=_json_default).decode())
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
                            print(f"[paper-trading] CANCEL {order_id}: {'success' if success else 'failed'}", flush=True)
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
            elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE, web.WSMsgType.CLOSING, web.WSMsgType.CLOSED):
                print(f"[ws] connection ended via {msg.type} close_code={ws.close_code}", flush=True)
    finally:
        CONNECTED.discard(ws)
        alive_for = time.monotonic() - t0
        print(f"[ws] {datetime.now().isoformat(timespec='milliseconds')} Client disconnected after {alive_for:.1f}s "
              f"(close_code={ws.close_code}). Total: {len(CONNECTED)}", flush=True)
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
    portfolio["funds"] = PT_ENGINE.get_fund_summary(spot_price=spot)

    await broadcast({"type": "portfolio", "payload": portfolio})
    await broadcast({"type": "orders", "payload": orders})


async def _handle_place_order(payload):
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
    immediately instead of waiting on the next engine_loop tick."""
    symbol = (payload.get("symbol") or "").strip().upper()
    instrument_type = payload.get("instrument_type") or "INDEX"
    expiry = payload.get("expiry") or ""
    strike = payload.get("strike")
    side = payload.get("side")
    order_type = payload.get("order_type") or "MARKET"
    limit_price = payload.get("limit_price")

    try:
        qty_lots = int(payload.get("qty_lots") or 0)
    except (TypeError, ValueError):
        qty_lots = 0

    current_prices = _build_current_prices(LAST_PAYLOAD)
    key = _instrument_key(symbol, expiry, strike, instrument_type)
    current_ltp = current_prices.get(key)

    wants_live = bool(payload.get("live")) and bool(payload.get("confirmed"))

    if wants_live:
        rejection_reason = None
        if not LIVE_TRADING_ENABLED:
            rejection_reason = "live trading disabled on server"
        elif _live_trading_kill_switch_active():
            rejection_reason = "live trading kill switch active"
        elif qty_lots < 1 or qty_lots > LIVE_MAX_LOTS_PER_ORDER:
            rejection_reason = f"qty_lots {qty_lots} outside allowed range (1-{LIVE_MAX_LOTS_PER_ORDER})"
        elif not _check_live_rate_limit():
            rejection_reason = f"rate limit exceeded ({LIVE_MAX_ORDERS_PER_MINUTE}/min)"
        elif symbol not in PT_LOT_SIZES:
            # A real order's quantity = qty_lots * lot_size — silently
            # falling back to a guessed lot size (the old `.get(symbol, 65)`
            # default) for a symbol NSE's circular doesn't match here would
            # size a real order wrong with no warning. Refuse instead: add
            # the symbol to paper_trading.py's LOT_SIZES (after confirming
            # against NSE's current circular — see that dict's own comment)
            # before it can be traded live.
            rejection_reason = f"no verified lot size for {symbol} — refusing to guess on a live order"

        if rejection_reason:
            print(f"[live-trading] REJECTED: {rejection_reason} — {symbol} {side} {qty_lots} lot(s)", flush=True)
            await _broadcast_portfolio(current_prices)
            return

        resolved = _resolve_live_order_token(symbol, instrument_type, expiry, strike)
        if resolved is None:
            print(f"[live-trading] REJECTED: could not resolve instrument token for "
                  f"{symbol} {expiry} {strike}{instrument_type}", flush=True)
            await _broadcast_portfolio(current_prices)
            return

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

        try:
            order_id = await asyncio.to_thread(
                smartapi_place_order,
                tradingsymbol, symboltoken, exchange, transaction_type, quantity,
                order_type=order_type, price=limit_price or 0.0,
            )
            print(f"[live-trading] PLACED: {tradingsymbol} {transaction_type} {quantity} "
                  f"qty (order_id={order_id})", flush=True)
        except Exception as e:
            print(f"[live-trading] FAILED: {tradingsymbol} {transaction_type} {quantity} — {e}", flush=True)
        finally:
            await _broadcast_portfolio(current_prices)
        return

    # ── Paper trading path (unchanged) ──────────────────────────────────
    order = PT_ENGINE.place_order(
        symbol, side, qty_lots,
        instrument_type=instrument_type, expiry=expiry, strike=strike,
        order_type=order_type, limit_price=limit_price,
        current_ltp=current_ltp,
    )
    print(f"[paper-trading] {order.status}: {symbol} {side} {qty_lots} lot(s) "
          f"{instrument_type} {expiry} {strike} "
          f"@ {order.fill_price if order.fill_price is not None else limit_price}"
          + (f" — {order.reject_reason}" if order.reject_reason else ""),
          flush=True)

    await _broadcast_portfolio(current_prices)


async def broadcast(message):
    if not CONNECTED:
        return

    msg_str = orjson.dumps(message, default=_json_default).decode()
    clients = list(CONNECTED)
    results = await asyncio.gather(
        *(ws.send_str(msg_str) for ws in clients),
        return_exceptions=True
    )
    for ws, result in zip(clients, results):
        if isinstance(result, Exception):
            print(f"[ws] Error broadcasting: {result}")
            CONNECTED.discard(ws)


def _configure_pipeline_globals(symbol, expiry=None, no_extra_chains=None, strict_expiry=None, no_virtual_oi=None):
    """Point option_chain_json's module-level runtime config at `symbol`.
    Used only by run_pipeline_once() for the primary --symbol's full
    option-chain pipeline run. The ticker-strip quotes for the other three
    INDEX_TICKER_SYMBOLS no longer go through option_chain_json at all (see
    fetch_nse_index_quotes_sync()/fetch_bse_index_quote_sync()), so this no
    longer needs to stay in sync with a second caller."""
    option_chain_json.SYMBOL   = symbol
    option_chain_json.EXCHANGE = "BSE" if symbol in _BSE_SYMBOLS else "NSE"
    option_chain_json.EXPIRY   = expiry or (
        option_chain_json.BSE_EXPIRY_DEFAULT.get(symbol, option_chain_json._nearest_Thursday)()
        if option_chain_json.EXCHANGE == "BSE" else option_chain_json._nearest_Tuesday()
    )
    if no_extra_chains is not None: option_chain_json.NO_EXTRA_CHAINS = no_extra_chains
    if strict_expiry   is not None: option_chain_json.STRICT_EXPIRY  = strict_expiry
    if no_virtual_oi   is not None: option_chain_json.NO_VIRTUAL_OI  = no_virtual_oi


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
    new_symbol = new_symbol.strip().upper()
    if new_symbol == SYMBOL and new_expiry is None:
        return  # already on this symbol, nothing to do
    print(f"[ws] symbol switch requested: {SYMBOL} -> {new_symbol}", flush=True)
    SYMBOL = new_symbol
    EXPIRY = new_expiry
    LAST_PAYLOAD = None
    _LAST_SENT = None
    _SYMBOL_SWITCH_EVENT.set()
    if USE_SMARTAPI:
        restart_smartapi_feed(new_symbol, new_expiry)


def _apply_smartapi_rows_to_chain_list(chain_rows, changed_rows):
    """Merges changed_rows (list of {"strike":.., "ceLTP":.., "peOI":.., ...},
    only the fields that actually ticked) into chain_rows (the full row list
    from LAST_PAYLOAD/_LAST_SENT) in place, matching by "strike". Rows for
    strikes not present in chain_rows are ignored — SmartAPI's ATM radius
    and the NSE pipeline's n_strikes_each_side aren't guaranteed identical,
    so a tick for a strike outside the currently-rendered range has nothing
    to merge into and is silently dropped (the client never sees it anyway,
    since it's not in the rendered chain)."""
    if not chain_rows:
        return
    by_strike = {row.get("strike"): row for row in chain_rows if isinstance(row, dict)}
    for changed in changed_rows:
        target = by_strike.get(changed.get("strike"))
        if target is None:
            continue
        for k, v in changed.items():
            if k != "strike":
                target[k] = v
        # Note: no per-row net_oi/net_oi_chg to recompute here — the
        # frontend (chain-views.js) derives totalCeOi/totalPeOi/pcr itself
        # each render, straight from ceOI/peOI across the whole chain
        # array, same as it does for volVel. ceOI/ceDOI/ceVol/ceVolChg
        # being correctly set above (by TickAggregator) is sufficient.


async def _smartapi_sync_and_broadcast(message):
    """Wraps broadcast() for the SmartAPI feed specifically: before sending
    a tick delta to clients, also merges it into LAST_PAYLOAD/_LAST_SENT's
    matching chain rows (IF the feed's expiry matches what's currently
    displayed — see _smartapi_feed_matches_displayed_expiry). Without this,
    TickAggregator's updates were invisible to LAST_PAYLOAD/_LAST_SENT
    entirely: a newly-connecting client's initial "full" snapshot (built
    from LAST_PAYLOAD) would miss whatever SmartAPI had already pushed to
    existing clients, and the next engine_loop tick's compute_diff() could
    re-broadcast an older NSE-polled value over top of a fresher SmartAPI
    one, causing a visible flicker backward. This keeps the server's own
    bookkeeping honest about what clients actually have on screen.

    If the expiry doesn't match (feed is streaming a different expiry than
    what's displayed), the merge is skipped but the tick is still broadcast
    as before — existing clients only apply it if their own rendered chain
    has a matching row (see dashboard.js's applyDelta), so this is safe,
    just without the LAST_PAYLOAD/_LAST_SENT consistency benefit for that
    edge case."""
    try:
        payload = message.get("payload") if isinstance(message, dict) else None
        chain_delta = (payload or {}).get("chain") if isinstance(payload, dict) else None
        if isinstance(chain_delta, dict) and chain_delta.get("_keyed"):
            changed_rows = chain_delta.get("changed") or []
            current_expiry = (LAST_PAYLOAD or {}).get("expiry")
            if changed_rows and _smartapi_feed_matches_displayed_expiry(current_expiry):
                if isinstance(LAST_PAYLOAD, dict):
                    _apply_smartapi_rows_to_chain_list(LAST_PAYLOAD.get("chain"), changed_rows)
                if isinstance(_LAST_SENT, dict):
                    _apply_smartapi_rows_to_chain_list(_LAST_SENT.get("chain"), changed_rows)

        # Spot isn't tied to any expiry (unlike the chain rows above), so no
        # expiry-match gate is needed here — just carry it into both
        # snapshots directly so a newly-connecting client's initial "full"
        # payload reflects the latest SmartAPI-streamed spot rather than
        # whatever run_pipeline_once() last polled.
        if isinstance(payload, dict) and "spot" in payload:
            for snapshot in (LAST_PAYLOAD, _LAST_SENT):
                if isinstance(snapshot, dict):
                    snapshot["spot"] = payload["spot"]
                    if "spotChange" in payload:
                        snapshot["spotChange"] = payload["spotChange"]
                    if "spotChgPct" in payload:
                        snapshot["spotChgPct"] = payload["spotChgPct"]

            # Futures-derived VWAP, basis-adjusted into spot's price frame —
            # see the "future vwap differs from spot" fix. PLACEHOLDER field
            # names (futLtp/futVwap/futVolume) — rename once TickAggregator
            # actually emits these for a "FUT"-tagged tick; until then this
            # branch is simply never true and is a no-op.
            if "futLtp" in payload and "futVwap" in payload:
                basis = payload["futLtp"] - payload["spot"]
                spot_vwap = payload["futVwap"] - basis
                for snapshot in (LAST_PAYLOAD, _LAST_SENT):
                    if isinstance(snapshot, dict):
                        snapshot["spotVwap"] = spot_vwap
                        if "futVolume" in payload:
                            snapshot["spotVolume"] = payload["futVolume"]
    except Exception as e:
        # Sync is a best-effort consistency improvement, not required for
        # the tick to reach clients — never let a sync bug block broadcast.
        print(f"[smartapi] state sync failed (broadcasting anyway): {e}", flush=True)

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
            # TEMP DEBUG — remove once confirmed firing at the expected
            # cadence. Only prints when there's at least one open position,
            # so this doesn't spam an idle/no-position session.
            open_count = sum(1 for p in PT_ENGINE.get_positions(current_prices) if p.get("net_qty_lots"))
            if open_count:
                print(f"[paper-trading] fast-path portfolio broadcast OK "
                      f"({open_count} open position(s), {len(current_prices)} live prices in map)", flush=True)
        except Exception as e:
            # Same best-effort posture as the sync block above — a paper
            # trading hiccup must never take down the live market-data feed.
            print(f"[paper-trading] fast-path portfolio broadcast failed: {e}", flush=True)


_smartapi_stream = None
_smartapi_aggregator = None
_smartapi_loop = None       # captured once at startup, reused for symbol switches
_smartapi_exchange = None   # exchange type currently subscribed (NFO/BFO), for unsubscribe
_smartapi_tokens = None     # token list currently subscribed, for unsubscribe
_smartapi_current_expiry = None  # expiry string the SmartAPI feed is streaming, e.g. "31JUL2026"
_smartapi_index_token = None     # underlying INDEX token currently subscribed for fast spot ticks, if any
_smartapi_index_exchange = None  # EXCHANGE_TYPE key ("NSE_CM"/"BSE_CM") the index token was subscribed under — DIFFERENT from _smartapi_exchange (NFO/BFO), so it needs its own unsubscribe call
_smartapi_futures_token = None      # current-month futures token subscribed for VWAP/volume, if resolved (see _resolve_futures_token)
_smartapi_futures_exchange = None   # NFO/BFO — same exchange as _smartapi_exchange, tracked separately since it's folded into _smartapi_tokens for unsubscribe but needs its own basis-calc lookup

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
    either SmartAPI's format ('31JUL2026', no separators — used by
    list_expiries()/_smartapi_current_expiry) or option_chain_json's format
    ('31-Jul-2026', dash-separated — used by the global EXPIRY/payload
    ["expiry"]). Returns None if it matches neither."""
    for fmt in ("%d%b%Y", "%d-%b-%Y"):
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
    if not _smartapi_current_expiry or not payload_expiry_str:
        return False
    a = _parse_any_expiry(_smartapi_current_expiry)
    b = _parse_any_expiry(payload_expiry_str)
    return a is not None and a == b


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

    expiries = list_expiries(target_symbol, exchange=exchange)
    if not expiries:
        print(f"[smartapi] No expiries found for {target_symbol}, skipping feed", flush=True)
        return None

    if expiry:
        target_date = _parse_any_expiry(expiry)
        resolved_expiry = next((e for e in expiries if _parse_any_expiry(e) == target_date), None)
        if resolved_expiry is None:
            print(f"[smartapi] Requested expiry '{expiry}' not available for "
                  f"{target_symbol} (have: {expiries}) — falling back to nearest", flush=True)
            resolved_expiry = expiries[0]
    else:
        resolved_expiry = expiries[0]

    chain = get_atm_chain(target_symbol, resolved_expiry, strikes_around_atm, exchange=exchange)
    if not chain:
        print(f"[smartapi] Could not build ATM chain for {target_symbol}, skipping feed", flush=True)
        return None

    token_meta = {
        row["token"]: {"strike": row["strike"], "option_type": row["type"]}
        for row in chain["rows"]
    }

    # Also resolve the underlying's own token so the SmartAPI feed can
    # stream spot at the same tick rate as the option legs, instead of spot
    # only ever coming from run_pipeline_once()'s slower NSE/BSE REST poll
    # (POLL_SECONDS). INDEX_TOKENS is keyed by underlying symbol and holds
    # its OWN {"token":.., "exchange": "NSE"|"BSE"} — not just a bare token
    # — since the index lives on the cash exchange, not NFO/BFO like the
    # option legs above. None if this symbol has no entry there yet, in
    # which case spot just keeps falling back to the REST poll as it does
    # today.
    index_info = INDEX_TOKENS.get(target_symbol)
    index_token = None
    index_exchange_type = None
    if index_info is None:
        print(f"[smartapi] No INDEX_TOKENS entry for {target_symbol} — "
              f"spot will only update via the slower REST poll, not SmartAPI", flush=True)
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
    futures_token, futures_exchange_type = _resolve_futures_token(target_symbol, exchange)
    if futures_token:
        token_meta[str(futures_token)] = {"strike": None, "option_type": "FUT"}

    return exchange, token_meta, resolved_expiry, index_token, index_exchange_type, futures_token, futures_exchange_type


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
            print(f"[smartapi] Feed already running, switching to {target_symbol} instead of starting a new one", flush=True)
            _switch_smartapi_symbol_blocking(target_symbol, strikes_around_atm, expiry)
            return

        _smartapi_loop = loop
        target_symbol = (underlying or SYMBOL).upper()

        resolved = _resolve_chain_tokens(target_symbol, strikes_around_atm, expiry)
        if resolved is None:
            return
        exchange, token_meta, expiry, index_token, index_exchange_type, futures_token, futures_exchange_type = resolved

        _smartapi_aggregator = TickAggregator(
            token_meta, loop, _smartapi_sync_and_broadcast,
            tick_event=_TICK_ACTIVITY_EVENT,
        )
        _smartapi_aggregator.start()

        _smartapi_stream = SmartTickStream(on_tick=_smartapi_aggregator.on_tick, mode=3)
        _smartapi_stream.connect()
        threading.Thread(target=_smartapi_stream.run_forever_with_reconnect, daemon=True).start()
        time.sleep(2)  # let the WS connection establish before subscribing

        # Option legs subscribe under the F&O exchange (NFO/BFO) as before.
        # Futures token (if resolved) also lives under NFO/BFO, same as the
        # option legs — folded into this same subscribe call rather than a
        # separate one, since it's the same exchange type either way.
        option_tokens = [t for t in token_meta.keys() if t not in (str(index_token), str(futures_token))]
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
            _smartapi_stream.subscribe(EXCHANGE_TYPE[index_exchange_type], [str(index_token)])
            _smartapi_index_token = str(index_token)
            _smartapi_index_exchange = index_exchange_type
        else:
            _smartapi_index_token = None
            _smartapi_index_exchange = None

        _smartapi_current_expiry = expiry
        print(f"[smartapi] Streaming {len(option_tokens)} {target_symbol} option legs"
              f"{' + spot' if index_token else ''}{' + futures VWAP' if futures_token else ''} (expiry {expiry})", flush=True)


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
            # initializing it) — fall back to a full start instead.
            if _smartapi_loop is not None:
                start_smartapi_feed(_smartapi_loop, new_symbol, strikes_around_atm, expiry)
            return

        resolved = _resolve_chain_tokens(new_symbol.upper(), strikes_around_atm, expiry)
        if resolved is None:
            return
        new_exchange, new_token_meta, new_expiry, new_index_token, new_index_exchange_type, new_futures_token, new_futures_exchange_type = resolved
        new_option_tokens = [t for t in new_token_meta.keys() if t not in (str(new_index_token), str(new_futures_token))]
        new_fo_tokens = new_option_tokens + ([str(new_futures_token)] if new_futures_token else [])

        if _smartapi_tokens and _smartapi_exchange:
            try:
                _smartapi_stream.unsubscribe(EXCHANGE_TYPE[_smartapi_exchange], _smartapi_tokens)
            except Exception as e:
                print(f"[smartapi] Unsubscribe failed (continuing anyway): {e}", flush=True)

        # Index token was subscribed under a DIFFERENT exchange type
        # (NSE_CM/BSE_CM, not NFO/BFO) — must be unsubscribed under that
        # same exchange, or AngelOne silently ignores/errors the call.
        if _smartapi_index_token and _smartapi_index_exchange:
            try:
                _smartapi_stream.unsubscribe(EXCHANGE_TYPE[_smartapi_index_exchange], [_smartapi_index_token])
            except Exception as e:
                print(f"[smartapi] Index unsubscribe failed (continuing anyway): {e}", flush=True)

        _smartapi_aggregator.update_token_meta(new_token_meta)
        _smartapi_stream.subscribe(EXCHANGE_TYPE[new_exchange], new_fo_tokens)

        if new_index_token:
            _smartapi_stream.subscribe(EXCHANGE_TYPE[new_index_exchange_type], [str(new_index_token)])
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
        print(f"[smartapi] Switched stream to {len(new_option_tokens)} {new_symbol.upper()} option legs"
              f"{' + spot' if new_index_token else ''}{' + futures VWAP' if new_futures_token else ''} (expiry {new_expiry})", flush=True)


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
        SYMBOL, EXPIRY,
        no_extra_chains=ARGS.no_extra_chains,
        strict_expiry=ARGS.strict_expiry,
        no_virtual_oi=ARGS.no_virtual_oi,
    )

    _CAPTURED.clear()
    try:
        option_chain_json.main()
    except Exception as e:
        print(f"[pipeline] FAILED: {e}")
        return None
    return _CAPTURED.get("payload")


def _map_market_api_quote(entry):
    """Normalize market_api's {"Symbol","Last Price","% Change","Change"}
    shape (shared by get_unified_market_data()'s ticker_payload rows and
    fetch_bse_index_quote()'s return value) into the {"spot","spotChange",
    "spotChgPct"} shape dashboard.js's indexQuotes handler expects. Keeps
    that mapping in exactly one place so NSE and BSE pills can never drift
    into different field names."""
    if not entry:
        return None
    return {
        "spot":       entry.get("Last Price"),
        "spotChange": entry.get("Change"),
        "spotChgPct": entry.get("% Change"),
    }


def fetch_nse_index_quotes_sync():
    """Single /api/allIndices round-trip covering EVERY NSE ticker symbol
    at once (NIFTY, BANKNIFTY, MIDCPNIFTY, FINNIFTY), via
    market_api.get_unified_market_data() — replaces what used to be one
    full option_chain_json.main() pipeline run PER NSE symbol just to read
    back 3 numbers. Doesn't touch option_chain_json's globals at all, so
    unlike the old fetch_index_quote_sync() this needs no _PIPELINE_LOCK
    and can't interfere with the primary --symbol tick.

    Returns {"NIFTY": {...}, "BANKNIFTY": {...}, ...} keyed by the same
    backend symbol names INDEX_TICKER_SYMBOLS uses (market_api.INDEX_RENAME
    already does NSE's raw "NIFTY 50"/"NIFTY BANK"/... -> "NIFTY"/
    "BANKNIFTY"/... renaming before this ever sees it).
    """
    try:
        _, _, ticker_payload = market_api.get_unified_market_data()
    except Exception as e:
        print(f"[index-quote] get_unified_market_data FAILED: {e}", flush=True)
        return {}
    out = {}
    for entry in ticker_payload:
        sym = entry.get("Symbol")
        quote = _map_market_api_quote(entry)
        if sym and quote is not None:
            out[sym] = quote
    return out


def fetch_bse_index_quote_sync(symbol):
    """Single getScripHeaderData round-trip for one BSE index (SENSEX/
    BANKEX), via market_api.fetch_bse_index_quote() — replaces the old
    option_chain_json.main() pipeline call that (per the no_extra_chains
    bug on the BSE path) was pulling all 3 expiry buckets just to throw
    them away. This call resolves no expiry at all, so that bug can't
    reach this code path any more."""
    try:
        entry = market_api.fetch_bse_index_quote(symbol)
    except Exception as e:
        print(f"[index-quote] {symbol} FAILED: {e}", flush=True)
        return None
    return _map_market_api_quote(entry)


async def index_quote_loop():
    """Keeps INDEX_QUOTES fresh for the ticker strip's three non-active
    symbols and pushes them to connected clients as
    {"type": "indexQuotes", "payload": {...}} — dashboard.js's generic
    message handler already merges any unrecognized `type` as
    `_wsState[type] = payload` (see updateDashboard()), which is exactly the
    d.indexQuotes shape renderIndexTicker() reads. The active SYMBOL is
    skipped here since its quote already rides along on every regular tick.

    Runs on its own --index-quote-seconds cadence (default 20s, independent
    of --poll-seconds). NSE symbols are fetched with ONE
    fetch_nse_index_quotes_sync() call regardless of how many of them are
    "others" this run (1-3 symbols, same one HTTP round-trip); BSE symbols
    still need one fetch_bse_index_quote_sync() call each since BSE has no
    all-indices equivalent. Neither touches option_chain_json's globals, so
    unlike the old version this loop no longer needs _PIPELINE_LOCK or
    serialization against the primary tick. A single slow/failed symbol
    only skips that symbol's pill for this pass; it doesn't stall the
    others or the primary tick.
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

        if nse_others:
            nse_quotes = await asyncio.to_thread(fetch_nse_index_quotes_sync)
            for sym in nse_others:
                quote = nse_quotes.get(sym)
                if quote is not None:
                    updates[sym] = quote

        for sym in bse_others:
            quote = await asyncio.to_thread(fetch_bse_index_quote_sync, sym)
            if quote is not None:
                updates[sym] = quote

        if updates:
            INDEX_QUOTES.update(updates)
            await broadcast({"type": "indexQuotes", "payload": updates})
            for sym, quote in updates.items():
                print(f"[index-quote] {sym} spot={quote.get('spot')} "
                      f"chg%={quote.get('spotChgPct')}", flush=True)
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
            print(f"[funds] available={funds.get('available_margin')} "
                  f"utilised={funds.get('utilised_margin')}", flush=True)
        except Exception as e:
            # A failed funds poll (session hiccup, AngelOne rate limit,
            # network blip) should never take down the loop — same
            # defensive posture as engine_loop's pipeline call. Skip this
            # cycle; the frontend keeps showing the last good LAST_FUNDS
            # (or "n/a" if there's never been one) until the next cycle
            # succeeds.
            print(f"[funds] poll failed (will retry in {FUNDS_POLL_SECONDS}s): {e}", flush=True)
        await asyncio.sleep(FUNDS_POLL_SECONDS)


def start_funds_polling():
    """Idempotent — a second toggle-on while already running is a no-op,
    not a duplicate poller."""
    global _funds_task
    if _funds_task is not None and not _funds_task.done():
        return
    print("[funds] starting funds polling (live mode toggled on)", flush=True)
    _funds_task = asyncio.create_task(_funds_poll_body())


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
    global LAST_PAYLOAD, _LAST_SENT, _EOD_DONE_DATE
    while True:
        tick_start = time.monotonic()

        now = datetime.now()
        if (
            is_trading_day(now)
            and now.time() >= EOD_TRIGGER_TIME
            and _EOD_DONE_DATE != now.date()
        ):
            _EOD_DONE_DATE = now.date()  # set before await, so a slow fetch can't cause a double-fire
            print(f"[eod] triggering EOD fetch for {now.date()}", flush=True)
            eod_task = asyncio.create_task(asyncio.to_thread(fetch_all_eod, now, True))  # save=True
            eod_task.add_done_callback(_eod_task_done)

        async with _PIPELINE_LOCK:
            payload = await asyncio.to_thread(run_pipeline_once)
        pipeline_elapsed = time.monotonic() - tick_start

        if payload is not None:
            LAST_PAYLOAD = payload

            if not USE_DELTA or _LAST_SENT is None:
                await broadcast({"type": "full", "payload": payload})
            else:
                diff_start = time.monotonic()
                # compute_diff walks the ENTIRE payload (all expiries, OI
                # velocity buckets, virtual-OI, greeks) doing recursive
                # old==new equality checks + keyed-list reconciliation.
                # That's real CPU time — running it inline on the event
                # loop (like before) froze WS heartbeats/broadcasts/static
                # serving for the duration. Offload it exactly like the
                # pipeline itself already is.
                diff = await asyncio.to_thread(compute_diff, _LAST_SENT, payload)
                diff_elapsed = time.monotonic() - diff_start
                if diff_elapsed > 0.25:
                    print(f"[ws] WARNING: compute_diff took {diff_elapsed:.2f}s "
                          f"— this was blocking the event loop before this fix",
                          flush=True)
                if diff is not None:
                    await broadcast({"type": "delta", "payload": diff})
                else:
                    print("[ws] tick unchanged, skipping broadcast", flush=True)

            _LAST_SENT = payload
            asyncio.create_task(_post_to_node(payload))
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
            # (quiet market, --no-smartapi, or this symbol has no SmartAPI
            # feed — spot/OI stay on the old REST-poll cadence in that
            # case). MIN_TICK_RECOMPUTE_SECONDS is a FLOOR: even with
            # ticks flooding in continuously (every ~0.25s during market
            # hours — see TickAggregator.flush_interval), this loop won't
            # re-run the heavy Greeks/OI-velocity/GEX pipeline faster than
            # this floor. Without the floor, "wake on every tick" would
            # make this run MORE often than the old fixed poll, not less.
            floor_remaining = MIN_TICK_RECOMPUTE_SECONDS - (time.monotonic() - tick_start)
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
                        print("[ws] tick activity — ticking early "
                              f"(floor={MIN_TICK_RECOMPUTE_SECONDS}s)", flush=True)
                    # else: timed out at the POLL_SECONDS ceiling, nothing to clear
                except Exception as e:
                    print(f"[ws] WARNING: wake-wait failed, falling back to plain sleep: {e}", flush=True)
                    await asyncio.sleep(remaining)
        elif pipeline_elapsed > POLL_SECONDS:
            print(
                f"[ws] WARNING: pipeline took {pipeline_elapsed:.2f}s, "
                f"longer than --poll-seconds {POLL_SECONDS}s — "
                f"broadcast cadence is bottlenecked by pipeline speed, not the sleep.",
                flush=True,
            )


@web.middleware
async def no_cache_middleware(request, handler):
    """During active dashboard development, browsers happily cache
    dashboard.js/DashboardPro.html between edits and only refetch on a
    hard reload (Cmd+Shift+R). Force revalidation on every request for
    the static files served here so a normal refresh always picks up
    the latest edit."""
    response = await handler(request)
    if request.path == '/' or request.path.endswith(('.html', '.js', '.css')):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    return response


async def spot_history_handler(request):
    """Backfills the price chart's initial candles on page load/reload.
    Called by priceChart.hydrate('/api/spot-history?minutes=N') in
    dashboard.js — see price-chart.js's hydrate() for the expected
    response shape: [{t: epoch_ms, p: price}, ...] oldest→newest.

    Sourced from SmartAPI's getCandleData against the CURRENT SYMBOL's
    own INDEX_TOKENS entry — the same underlying now streamed live via
    start_smartapi_feed()'s index-token subscription (see TickAggregator's
    INDEX branch), so the backfill and the live tail are the same
    instrument end to end.

    Always returns 200 with a (possibly empty) JSON list rather than a
    4xx/5xx — hydrate() already treats an empty response as a safe no-op
    (chart just builds up from live ticks instead), so there's nothing
    gained by turning "SmartAPI has no history yet" or "no INDEX_TOKENS
    entry for this symbol" into a client-visible error.
    """
    try:
        minutes = int(request.query.get("minutes", "15"))
    except (TypeError, ValueError):
        minutes = 15
    # Sane bounds — this is a live on-demand REST call (3 req/sec cap per
    # get_candle_data's docstring), not meant for a huge historical pull.
    minutes = max(1, min(minutes, 24 * 60))

    index_info = INDEX_TOKENS.get(SYMBOL)
    if index_info is None:
        print(f"[http] /api/spot-history: no INDEX_TOKENS entry for {SYMBOL}, returning empty", flush=True)
        return web.json_response([])

    now = datetime.now()
    fromdate = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    todate = now.strftime("%Y-%m-%d %H:%M")
    # ONE_MINUTE candles regardless of `minutes` — the client already
    # buckets ticks into whatever candle width its selected range wants
    # (see PRICE_CHART_RANGES in price-chart.js); handing it the finest
    # granularity available lets it re-bucket correctly for any range.
    interval = "ONE_MINUTE"

    try:
        # getCandleData is a blocking REST call — same discipline as
        # run_pipeline_once(): never run a blocking network call inline on
        # the event loop, or every connected client's WS heartbeat stalls
        # for its duration.
        candles = await asyncio.to_thread(
            get_candle_data, index_info["exchange"], index_info["token"],
            interval, fromdate, todate,
        )
    except Exception as e:
        print(f"[http] /api/spot-history: getCandleData failed for {SYMBOL}: {e}", flush=True)
        return web.json_response([])

    rows = []
    for c in candles or []:
        try:
            # SmartAPI returns an ISO timestamp with its own +05:30 offset
            # embedded (e.g. "2026-07-15T09:16:00+05:30") — fromisoformat
            # respects that offset directly, so this is correct regardless
            # of the server process's own local timezone.
            t_ms = int(datetime.fromisoformat(c["time"]).timestamp() * 1000)
        except (ValueError, TypeError, KeyError):
            continue
        rows.append({"t": t_ms, "p": c["close"]})

    return web.json_response(rows)


# Maps price-chart.js's PRICE_CHART_RANGES keys to a SmartAPI interval + how
# far back to request. '1d'/'all' use ONE_DAY so the lookback can span years
# without hitting Angel One's ~30-day intraday cap; get_index_candles ->
# fetch_candles_chunked already splits/stitches anything that would exceed
# the cap, so 'all' genuinely means "everything SmartAPI will hand back",
# not just what happens to be in the client's live tick buffer.
_RANGE_TO_SMARTAPI = {
    "1m":  {"interval": "ONE_MINUTE",     "days": 1},
    "5m":  {"interval": "FIVE_MINUTE",    "days": 7},
    "15m": {"interval": "FIFTEEN_MINUTE", "days": 30},
    "1h":  {"interval": "ONE_HOUR",       "days": 90},
    "1d":  {"interval": "ONE_DAY",        "days": 730},
    "all": {"interval": "ONE_DAY",        "days": 2000},  # smartapi_history's own daily-interval cap
}


async def history_handler(request):
    """Full OHLCV backfill for the price chart, sourced from SmartAPI via
    get_index_candles() (chunked to respect Angel One's ~30-day intraday
    cap). Called by priceChart.hydrateRange(rangeKey) in price-chart.js —
    replaces spot_history_handler's close-only, 24h-capped payload with
    real {t,o,h,l,c,v} bars sized to whichever range is currently selected.
    """
    range_key = request.query.get("range", "1d")
    cfg = _RANGE_TO_SMARTAPI.get(range_key, _RANGE_TO_SMARTAPI["1d"])

    if SYMBOL not in INDEX_TOKENS:
        print(f"[http] /api/history: no INDEX_TOKENS entry for {SYMBOL}, returning empty", flush=True)
        return web.json_response([])

    now = datetime.now()
    fromdate = (now - timedelta(days=cfg["days"])).strftime("%Y-%m-%d %H:%M")
    todate = now.strftime("%Y-%m-%d %H:%M")

    try:
        # get_index_candles is blocking (chunked REST calls with pacing
        # sleeps between them) — offload same as spot_history_handler does,
        # or every connected client's WS heartbeat stalls for its duration.
        candles = await asyncio.to_thread(
            get_index_candles, SYMBOL, cfg["interval"], fromdate, todate,
        )
    except Exception as e:
        print(f"[http] /api/history: get_index_candles failed for {SYMBOL} range={range_key}: {e}", flush=True)
        return web.json_response([])

    rows = []
    for c in candles or []:
        try:
            # Same +05:30-aware parse as spot_history_handler — SmartAPI's
            # timestamp already carries its own offset.
            t_ms = int(datetime.fromisoformat(c["timestamp"]).timestamp() * 1000)
        except (ValueError, TypeError, KeyError):
            continue
        rows.append({
            "t": t_ms,
            "o": c.get("open"),
            "h": c.get("high"),
            "l": c.get("low"),
            "c": c.get("close"),
            "v": c.get("volume"),
        })

    return web.json_response(rows)


async def lot_sizes_handler(request):
    """GET /api/lot-sizes → {"NIFTY": 65, "RELIANCE": 500, ...}

    Lot sizes come from FUTSTK/FUTIDX rows in the AngelOne instrument
    master (see smartapi_instruments.get_all_lot_sizes) — one futures
    contract per underlying is enough because FUT and all CE/PE share
    the same lot size for a given NSE revision. paper-trading.js calls
    this on panel init via ptRefreshLotSizes().
    """
    try:
        from smartapi_instruments import get_all_lot_sizes
        lots = await asyncio.to_thread(get_all_lot_sizes)
        return web.json_response(lots)
    except Exception as e:
        print(f"[http] /api/lot-sizes failed: {e}", flush=True)
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


async def main():
    app = web.Application(middlewares=[no_cache_middleware])
    app.router.add_get('/ws', ws_handler)
    app.router.add_get('/api/spot-history', spot_history_handler)
    app.router.add_get('/api/history', history_handler)
    app.router.add_get('/api/lot-sizes', lot_sizes_handler)
    FRONTEND_DIR = SCRIPT_DIR / "frontend"
    app.router.add_static('/', path=FRONTEND_DIR, name='static')

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, WS_HOST, HTTP_PORT)
    await site.start()
    print(f"[http] serving static files at http://{WS_HOST}:{HTTP_PORT}/")
    print(f"[http] Dashboard available at http://{WS_HOST}:{HTTP_PORT}/DashboardPro.html")
    print(f"[ws] WebSocket endpoint at ws://{WS_HOST}:{HTTP_PORT}/ws symbol={SYMBOL}")

    loop = asyncio.get_running_loop()
    # start_smartapi_feed() makes blocking REST calls (_resolve_chain_tokens)
    # and has an internal time.sleep(2) — calling it directly here would
    # freeze the event loop (and every already-connected client's WS
    # heartbeat) for its full duration. Same discipline as offloading
    # compute_diff() in engine_loop(): anything blocking goes through a
    # thread, never runs inline on the loop.
    if USE_SMARTAPI:
        asyncio.create_task(asyncio.to_thread(start_smartapi_feed, loop))
    else:
        print("[smartapi] skipped at startup (--no-smartapi) — chain served from "
              "market_api.py REST polling only, no AngelOne websocket connection made", flush=True)

    try:
        asyncio.create_task(index_quote_loop())
        # No funds_loop() task here anymore — funds polling starts/stops
        # live via the {"type":"toggle_live_mode",...} WS message (see
        # ws_handler + start_funds_polling()/stop_funds_polling() above),
        # not at boot. Flipping the dashboard's LIVE pill controls it
        # directly over the socket, no server restart required.
        await engine_loop()
    finally:
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