"""Feed orchestration: per-provider live-feed state, token resolution,
start/switch/stop adapters, and the shared live-tick merge/broadcast path.

Everything that drives a broker's websocket feed used to live in
``server/app.py``. It is gathered here so the composition root only has to
wire ``runtime_state.FEEDS`` against these entry points.

``broadcast`` (the websocket fan-out) and the paper-trading portfolio
fast-path are injected via :func:`configure_feed_orchestration` to keep this
module free of a circular import back into ``server.app``.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from server import feed_manager
from server import runtime_state
from server.feed_expiry import matches_displayed_expiry as _matches_displayed_expiry
from server.live_feed_state import merge_live_feed_update

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
from server.feeds.kotak import (  # noqa: E402
    FeedState as _KotakFeedState,
    resolve_chain_tokens as _resolve_kotak_feed_tokens,
    start_new_feed as _start_kotak_feed_new,
    stop_feed as _stop_kotak_feed,
    switch_existing_feed as _switch_kotak_feed_existing,
)

from market.quotes.tick_aggregator import TickAggregator
from market.providers.nse_bse import _BSE_SYMBOLS
from brokers.market_data import market_data
from server.broker_services import SmartTickStream, EXCHANGE_TYPE

# Injected by the composition root at startup (server/app.py) to avoid a
# circular import on the websocket broadcast + paper-trading engine.
_BROADCAST = None
_PORTFOLIO_BROADCASTER = None

_LOGGER = logging.getLogger("mterminals.server.feeds.orchestration")


def configure_feed_orchestration(*, broadcast, portfolio_broadcaster):
    """Bind the app-level dependencies this module calls at runtime."""
    global _BROADCAST, _PORTFOLIO_BROADCASTER
    _BROADCAST = broadcast
    _PORTFOLIO_BROADCASTER = portfolio_broadcaster


def _print_log(message):
    print(message, flush=True)


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
    independently of the application runtime's selected expiry and they aren't
    guaranteed to agree (NEAR/MONTHLY tab active, etc.); merging ticks for
    the wrong expiry would silently show the wrong contract's prices, so
    this gate must pass before any state merge."""
    return _matches_displayed_expiry(
        current_expiry, payload_expiry_str, _parse_any_expiry
    )


def _smartapi_feed_matches_displayed_expiry(payload_expiry_str):
    return _matches_current_feed_expiry(
        _current_feed_expiry("SMARTAPI"), payload_expiry_str
    )


def _upstox_feed_matches_displayed_expiry(payload_expiry_str):
    return _matches_current_feed_expiry(
        _current_feed_expiry("UPSTOX"), payload_expiry_str
    )


def _shoonya_feed_matches_displayed_expiry(payload_expiry_str):
    return _matches_current_feed_expiry(
        _current_feed_expiry("SHOONYA"), payload_expiry_str
    )


def _current_feed_expiry(provider):
    manager = runtime_state.FEEDS.get(provider)
    return manager.current_expiry if manager is not None else None


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
    if not feed_manager._feed_allowed(provider):
        return
    async with runtime_state.MARKET_STREAM_LOCK:
        await _live_feed_sync_and_broadcast_locked(message, matches_expiry_fn)


async def _smartapi_sync_and_broadcast(message):
    """Compatibility callback for SmartAPI's normalized tick stream."""
    await _sync_live_feed_and_broadcast(
        "SMARTAPI", message, _smartapi_feed_matches_displayed_expiry
    )


async def _upstox_sync_and_broadcast(message):
    """Upstox analog — same shared merge logic, gated on Upstox's own expiry
    tracker. Feeds are mutually exclusive (runtime_state.LIVE_FEED_PROVIDER picks one);
    runtime_state.MARKET_STREAM_LOCK serializes regardless."""
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
    """Merge a live tick delta into runtime_state.LAST_PAYLOAD/runtime_state.LAST_SENT before
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
    runtime_state.LAST_PAYLOAD clears immediately but the background unsubscribe hasn't
    finished. Spot isn't expiry-tied and still broadcasts every time.

    The paper-trading fast path below fires off the same fresh prices the
    client just received (throttled by runtime_state.PORTFOLIO_POLL_SECONDS to avoid
    flooding clients during tick bursts), including a pending-LIMIT check
    so fills don't lag the feed by --poll-seconds."""
    if _BROADCAST is None:
        return
    feed_update_applied = False
    try:
        message, feed_update_applied = merge_live_feed_update(
            message, runtime_state.LAST_PAYLOAD, runtime_state.LAST_SENT, matches_expiry_fn,
            price_source=runtime_state.MARKET_SELECTION.price_source,
        )
    except Exception as e:
        # Sync is best-effort consistency — never let a sync bug block the
        # tick from reaching clients.
        print(f"[live-feed] state sync failed (broadcasting anyway): {e}", flush=True)

    if feed_update_applied and runtime_state.LAST_PAYLOAD is not None:
        runtime_state.LAST_PAYLOAD_AT = datetime.now().astimezone()
    await _BROADCAST(message)

    now_ts = time.monotonic()
    if now_ts - runtime_state.LAST_PORTFOLIO_BROADCAST_TS >= runtime_state.PORTFOLIO_POLL_SECONDS:
        runtime_state.LAST_PORTFOLIO_BROADCAST_TS = now_ts
        try:
            if _PORTFOLIO_BROADCASTER is not None:
                await _PORTFOLIO_BROADCASTER(runtime_state.LAST_PAYLOAD)
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
        tick_event=runtime_state.TICK_ACTIVITY_EVENT,
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
        state, exchange_types=EXCHANGE_TYPE, report=lambda m: _LOGGER.warning(m)
    )


def _upstox_feed_start(state, loop, symbol, strikes_around_atm, expiry):
    from brokers.upstox.websocket import UpstoxTickStream

    _start_upstox_feed_new(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        _resolve_upstox_chain_tokens,
        TickAggregator,
        _upstox_sync_and_broadcast,
        runtime_state.TICK_ACTIVITY_EVENT,
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
    _stop_upstox_feed(state, report=lambda m: _LOGGER.warning(m))


def _shoonya_feed_start(state, loop, symbol, strikes_around_atm, expiry):
    from brokers.shoonya.websocket import ShoonyaTickStream

    _start_shoonya_feed_new(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        _resolve_shoonya_chain_tokens,
        TickAggregator,
        _shoonya_sync_and_broadcast,
        runtime_state.TICK_ACTIVITY_EVENT,
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
    _stop_shoonya_feed(state, report=lambda m: _LOGGER.warning(m))


# ── Kotak Neo feed adapters ────────────────────────────────────────────────
def _kotak_feed_matches_displayed_expiry(payload_expiry_str):
    return _matches_current_feed_expiry(
        _current_feed_expiry("KOTAK"), payload_expiry_str
    )


def _resolve_kotak_chain_tokens(target_symbol, strikes_around_atm, expiry=None):
    return _resolve_kotak_feed_tokens(
        target_symbol,
        strikes_around_atm,
        expiry,
        is_bse=lambda symbol: symbol in _BSE_SYMBOLS,
        parse_expiry=_parse_any_expiry,
        report=_print_log,
    )


async def _kotak_sync_and_broadcast(message):
    """Kotak Neo analog — shared merge logic, gated on Kotak's own expiry
    tracker."""
    await _sync_live_feed_and_broadcast(
        "KOTAK", message, _kotak_feed_matches_displayed_expiry
    )


def _kotak_feed_start(state, loop, symbol, strikes_around_atm, expiry):
    from brokers.kotak.websocket import KotakTickStream

    _start_kotak_feed_new(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        _resolve_kotak_chain_tokens,
        TickAggregator,
        _kotak_sync_and_broadcast,
        runtime_state.TICK_ACTIVITY_EVENT,
        KotakTickStream,
        threading.Thread,
        time.sleep,
        _print_log,
    )


def _kotak_feed_switch(state, symbol, strikes_around_atm, expiry):
    _switch_kotak_feed_existing(
        state,
        symbol,
        strikes_around_atm,
        expiry,
        _resolve_kotak_chain_tokens,
        _print_log,
    )


def _kotak_feed_stop(state):
    _stop_kotak_feed(state, report=lambda m: _LOGGER.warning(m))


def build_feed_managers(*, default_symbol, main_loop, log):
    """Build provider managers that directly own their mutable feed state."""
    from server.feed_manager import BrokerFeedManager

    specs = (
        (
            "SMARTAPI",
            _SmartApiFeedState(),
            _smartapi_feed_start,
            _smartapi_feed_switch,
            _smartapi_feed_stop,
        ),
        (
            "UPSTOX",
            _UpstoxFeedState(),
            _upstox_feed_start,
            _upstox_feed_switch,
            _upstox_feed_stop,
        ),
        (
            "SHOONYA",
            _ShoonyaFeedState(),
            _shoonya_feed_start,
            _shoonya_feed_switch,
            _shoonya_feed_stop,
        ),
        (
            "KOTAK",
            _KotakFeedState(),
            _kotak_feed_start,
            _kotak_feed_switch,
            _kotak_feed_stop,
        ),
    )
    return {
        provider: BrokerFeedManager(
            provider,
            state=state,
            start=start,
            switch=switch,
            stop=stop,
            default_symbol=default_symbol,
            main_loop=main_loop,
            log=log,
        )
        for provider, state, start, switch, stop in specs
    }
