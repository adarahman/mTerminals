"""Lifecycle adapters for non-SmartAPI websocket providers."""
from __future__ import annotations

import logging
import threading
import time

from market.quotes.tick_aggregator import TickAggregator
from server import runtime_state
from server.feeds import live_updates, resolution
from server.feeds.kotak import (
    FeedState as KotakFeedState,
    start_new_feed as start_kotak,
    stop_feed as stop_kotak,
    switch_existing_feed as switch_kotak,
)
from server.feeds.shoonya import (
    FeedState as ShoonyaFeedState,
    start_new_feed as start_shoonya,
    stop_feed as stop_shoonya,
    switch_existing_feed as switch_shoonya,
)
from server.feeds.upstox import (
    FeedState as UpstoxFeedState,
    start_new_feed as start_upstox,
    stop_feed as stop_upstox,
    switch_existing_feed as switch_upstox,
)


_LOGGER = logging.getLogger("mterminals.server.feeds.provider_lifecycle")


def _report(message):
    print(message, flush=True)


def _resolve(provider, symbol, strikes_around_atm, expiry=None):
    resolver = getattr(resolution, provider.lower())
    return resolver(symbol, strikes_around_atm, expiry, report=_report)


def _start_upstox(state, loop, symbol, strikes_around_atm, expiry):
    from brokers.upstox.websocket import UpstoxTickStream

    start_upstox(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        lambda *args: _resolve("UPSTOX", *args),
        TickAggregator,
        live_updates.upstox,
        runtime_state.TICK_ACTIVITY_EVENT,
        UpstoxTickStream,
        threading.Thread,
        time.sleep,
        _report,
    )


def _switch_upstox(state, symbol, strikes_around_atm, expiry):
    switch_upstox(
        state,
        symbol,
        strikes_around_atm,
        expiry,
        lambda *args: _resolve("UPSTOX", *args),
        _report,
    )


def _start_shoonya(state, loop, symbol, strikes_around_atm, expiry):
    from brokers.shoonya.websocket import ShoonyaTickStream

    start_shoonya(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        lambda *args: _resolve("SHOONYA", *args),
        TickAggregator,
        live_updates.shoonya,
        runtime_state.TICK_ACTIVITY_EVENT,
        ShoonyaTickStream,
        threading.Thread,
        time.sleep,
        _report,
    )


def _switch_shoonya(state, symbol, strikes_around_atm, expiry):
    switch_shoonya(
        state,
        symbol,
        strikes_around_atm,
        expiry,
        lambda *args: _resolve("SHOONYA", *args),
        _report,
    )


def _start_kotak(state, loop, symbol, strikes_around_atm, expiry):
    from brokers.kotak.websocket import KotakTickStream

    start_kotak(
        state,
        loop,
        symbol,
        strikes_around_atm,
        expiry,
        lambda *args: _resolve("KOTAK", *args),
        TickAggregator,
        live_updates.kotak,
        runtime_state.TICK_ACTIVITY_EVENT,
        KotakTickStream,
        threading.Thread,
        time.sleep,
        _report,
    )


def _switch_kotak(state, symbol, strikes_around_atm, expiry):
    switch_kotak(
        state,
        symbol,
        strikes_around_atm,
        expiry,
        lambda *args: _resolve("KOTAK", *args),
        _report,
    )


def provider_specs():
    """Return manager specifications for non-SmartAPI providers."""
    def warning(message):
        _LOGGER.warning(message)

    return (
        (
            "UPSTOX",
            UpstoxFeedState(),
            _start_upstox,
            _switch_upstox,
            lambda state: stop_upstox(state, report=warning),
        ),
        (
            "SHOONYA",
            ShoonyaFeedState(),
            _start_shoonya,
            _switch_shoonya,
            lambda state: stop_shoonya(state, report=warning),
        ),
        (
            "KOTAK",
            KotakFeedState(),
            _start_kotak,
            _switch_kotak,
            lambda state: stop_kotak(state, report=warning),
        ),
    )
