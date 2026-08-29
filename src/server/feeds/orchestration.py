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

from server import runtime_state
from server.feeds import live_updates, resolution

from server.feeds.smartapi import (  # noqa: E402
    FeedState as _SmartApiFeedState,
    start_new_feed as _start_smartapi_feed_new,
    stop_feed as _stop_smartapi_feed,
    switch_existing_feed as _switch_smartapi_feed_existing,
)
from server.feeds.shoonya import (  # noqa: E402
    FeedState as _ShoonyaFeedState,
    start_new_feed as _start_shoonya_feed_new,
    stop_feed as _stop_shoonya_feed,
    switch_existing_feed as _switch_shoonya_feed_existing,
)
from server.feeds.upstox import (  # noqa: E402
    FeedState as _UpstoxFeedState,
    start_new_feed as _start_upstox_feed_new,
    stop_feed as _stop_upstox_feed,
    switch_existing_feed as _switch_upstox_feed_existing,
)
from server.feeds.kotak import (  # noqa: E402
    FeedState as _KotakFeedState,
    start_new_feed as _start_kotak_feed_new,
    stop_feed as _stop_kotak_feed,
    switch_existing_feed as _switch_kotak_feed_existing,
)

from market.quotes.tick_aggregator import TickAggregator
from server.broker_services import SmartTickStream, EXCHANGE_TYPE

_LOGGER = logging.getLogger("mterminals.server.feeds.orchestration")


def configure_feed_orchestration(*, broadcast, portfolio_broadcaster):
    """Bind the app-level dependencies this module calls at runtime."""
    live_updates.configure(
        broadcast=broadcast, portfolio_broadcaster=portfolio_broadcaster
    )


def _print_log(message):
    print(message, flush=True)


def _resolve_chain_tokens(symbol, strikes_around_atm, expiry=None):
    return resolution.smartapi(
        symbol, strikes_around_atm, expiry, report=_print_log
    )


def _resolve_upstox_chain_tokens(symbol, strikes_around_atm, expiry=None):
    return resolution.upstox(symbol, strikes_around_atm, expiry, report=_print_log)


def _resolve_shoonya_chain_tokens(symbol, strikes_around_atm, expiry=None):
    return resolution.shoonya(symbol, strikes_around_atm, expiry, report=_print_log)


# Compatibility exports used by the composition root and existing tests.
_smartapi_sync_and_broadcast = live_updates.smartapi
_upstox_sync_and_broadcast = live_updates.upstox
_shoonya_sync_and_broadcast = live_updates.shoonya


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
def _resolve_kotak_chain_tokens(target_symbol, strikes_around_atm, expiry=None):
    return resolution.kotak(
        target_symbol, strikes_around_atm, expiry, report=_print_log
    )


_kotak_sync_and_broadcast = live_updates.kotak


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
