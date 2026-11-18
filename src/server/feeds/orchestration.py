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

from server import feed_manager, runtime_state
from server.feeds import live_updates, provider_lifecycle, resolution

from server.feeds.smartapi import (  # noqa: E402
    FeedState as _SmartApiFeedState,
    start_new_feed as _start_smartapi_feed_new,
    stop_feed as _stop_smartapi_feed,
    switch_existing_feed as _switch_smartapi_feed_existing,
)
from market.quotes.tick_aggregator import TickAggregator
from server.broker_services import SmartTickStream, EXCHANGE_TYPE

_LOGGER = logging.getLogger("mterminals.server.feeds.orchestration")
_COMMODITY_BROADCAST = None


def configure_feed_orchestration(*, broadcast, portfolio_broadcaster):
    """Bind the app-level dependencies this module calls at runtime."""
    global _COMMODITY_BROADCAST
    _COMMODITY_BROADCAST = broadcast
    live_updates.configure(
        broadcast=broadcast, portfolio_broadcaster=portfolio_broadcaster
    )


def _print_log(message):
    print(message, flush=True)


def _resolve_chain_tokens(symbol, strikes_around_atm, expiry=None):
    return resolution.smartapi(
        symbol, strikes_around_atm, expiry, report=_print_log
    )


# Compatibility exports used by the composition root and existing tests.
_smartapi_sync_and_broadcast = live_updates.smartapi


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
    _subscribe_commodity_futures(state, loop)


def _subscribe_commodity_futures(state, loop):
    """Stream Angel MCX futures alongside its active equity option feed."""
    if state.stream is None or getattr(state, "commodity_tokens", None):
        return
    try:
        from brokers.smartapi.instruments import get_commodity_futures_contract

        contracts = {}
        for symbol in (
            "ALUMINIUM", "COPPER", "CRUDEOIL", "GOLD", "GOLDM",
            "LEAD", "NATURALGAS", "SILVER", "SILVERM", "ZINC",
        ):
            contract = get_commodity_futures_contract(symbol, which="NEAR")
            if not contract or not contract.get("token"):
                continue
            token = str(contract["token"])
            contracts[token] = {"symbol": symbol, "contract": contract}
        if not contracts:
            return

        state.commodity_tokens = contracts
        if state.commodity_listener is None:
            state.commodity_listener = lambda tick: _handle_commodity_tick(state, tick)
            state.stream.add_tick_listener(state.commodity_listener)
        state.stream.subscribe(EXCHANGE_TYPE["MCX_FO"], list(contracts))
        _print_log(f"[smartapi] Subscribed to {len(contracts)} MCX futures contracts")
    except Exception as exc:  # noqa: BLE001 - REST quote remains the fallback
        _LOGGER.warning("[smartapi] MCX subscription setup failed: %s", exc)


def _handle_commodity_tick(state, tick):
    if _COMMODITY_BROADCAST is None:
        return
    if not feed_manager._feed_allowed("SMARTAPI"):
        return
    meta = getattr(state, "commodity_tokens", {}).get(str(tick.get("token")))
    if not meta:
        return
    ltp = tick.get("last_traded_price")
    if ltp is None:
        return
    contract = meta["contract"]
    close = tick.get("closed_price")
    change = round(float(ltp) - float(close), 2) if close not in (None, 0) else None
    pct_change = round(change / float(close) * 100.0, 2) if change is not None else None
    quote = {
        "Contract": contract.get("symbol"),
        "Underlying": meta["symbol"],
        "Exchange": "MCX",
        "LotSize": contract.get("lotsize"),
        "Expiry": contract.get("expiry"),
        "LTP": ltp,
        "Change": change,
        "PctChange": pct_change,
        "Open": tick.get("open_price_of_the_day"),
        "High": tick.get("high_price_of_the_day"),
        "Low": tick.get("low_price_of_the_day"),
        "PrevClose": close,
        "Volume": tick.get("volume_trade_for_the_day"),
        "OI": tick.get("open_interest"),
        "FutSource": "SMARTAPI_WS",
    }
    import asyncio

    asyncio.run_coroutine_threadsafe(
        _COMMODITY_BROADCAST({
            "type": "commodityQuote",
            "payload": {"symbol": meta["symbol"], "provider": "SMARTAPI", "quote": quote},
        }),
        state.loop,
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
    _subscribe_commodity_futures(state, state.loop)


def _smartapi_feed_stop(state):
    _stop_smartapi_feed(
        state, exchange_types=EXCHANGE_TYPE, report=lambda m: _LOGGER.warning(m)
    )


def build_feed_managers(*, default_symbol, main_loop, log):
    """Build provider managers that directly own their mutable feed state."""
    from server.feed_manager import BrokerFeedManager

    specs = ((
        "SMARTAPI",
        _SmartApiFeedState(),
        _smartapi_feed_start,
        _smartapi_feed_switch,
        _smartapi_feed_stop,
    ), *provider_lifecycle.provider_specs())
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
