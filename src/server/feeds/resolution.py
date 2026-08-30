"""Provider-neutral token resolution adapters for websocket feeds."""
from __future__ import annotations

from market.providers.nse_bse import _BSE_SYMBOLS
from server.broker_services import market_data
from server.feeds import live_updates
from server.feeds.kotak import resolve_chain_tokens as resolve_kotak_tokens
from server.feeds.shoonya import resolve_chain_tokens as resolve_shoonya_tokens
from server.feeds.smartapi import resolve_chain_tokens as resolve_smartapi_tokens
from server.feeds.upstox import resolve_chain_tokens as resolve_upstox_tokens


def _is_bse(symbol):
    return symbol in _BSE_SYMBOLS


def _unavailable_futures_token(_symbol, _exchange):
    """SmartAPI does not yet expose a futures-token lookup for feed VWAP."""
    return None, None


def smartapi(symbol, strikes_around_atm, expiry=None, *, report=print):
    return resolve_smartapi_tokens(
        symbol,
        strikes_around_atm,
        expiry,
        market_data=market_data,
        is_bse=_is_bse,
        parse_expiry=live_updates.parse_expiry,
        resolve_futures=_unavailable_futures_token,
        report=report,
    )


def upstox(symbol, strikes_around_atm, expiry=None, *, report=print):
    return resolve_upstox_tokens(
        symbol,
        strikes_around_atm,
        expiry,
        is_bse=_is_bse,
        parse_expiry=live_updates.parse_expiry,
        report=report,
    )


def shoonya(symbol, strikes_around_atm, expiry=None, *, report=print):
    return resolve_shoonya_tokens(
        symbol,
        strikes_around_atm,
        expiry,
        _is_bse,
        live_updates.parse_expiry,
        report,
    )


def kotak(symbol, strikes_around_atm, expiry=None, *, report=print):
    return resolve_kotak_tokens(
        symbol,
        strikes_around_atm,
        expiry,
        is_bse=_is_bse,
        parse_expiry=live_updates.parse_expiry,
        report=report,
    )
