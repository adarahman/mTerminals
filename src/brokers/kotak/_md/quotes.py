"""Kotak Neo quote normalization and live-quote fetch.

Holds the quote helpers (_ohlc_val / _quote_token / _unwrap_quotes) plus the
spot / chain-batch / futures quote functions. The chain orchestration itself
lives in the public ``market_data`` module so its globals resolve through that
module (the test suite monkeypatches kotak._spot_quote / kotak._contracts_for
on the package object).
"""
from __future__ import annotations

import logging

from brokers.kotak.client import _session
from .constants import _INDEX_NAMES, _BSE_INDEX_NAMES, _INDEX_EXCHANGE
from .contracts import _contracts_for, _parse_expiry_date
from .scrip_master import _fo_segment

logger = logging.getLogger(__name__)


def _ohlc_val(row, key, alt_key="open"):
    """Extract one OHLC component from Kotak's quote row, which nests OHLC
    inside 'ohlc' when quote_type='all' but may also carry flat fields."""
    ohlc = row.get("ohlc")
    if isinstance(ohlc, dict):
        val = ohlc.get(key)
        if val is not None:
            return float(val)
    flat = row.get(key) if key != "close" else row.get("previous_close")
    if flat is not None:
        return float(flat)
    return float(alt_key and row.get(alt_key) or 0)


def _quote_token(row) -> str:
    """Token identifier returned by Kotak quote responses.

    Neo quote responses use exchange_token, while some SDK/API
    versions may expose instrument_token. Support both.
    """
    return str(
        row.get("exchange_token")
        or row.get("instrument_token")
        or row.get("token")
        or ""
    ).strip()


def _unwrap_quotes(result):
    """Kotak's quotes() returns the raw JSON body; the payload can be a
    bare list (v2) or wrapped in 'data'/'message' (v1-era). Normalize to a
    list of row dicts."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("data", "message", "quotes"):
            val = result.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                # Single row wrapped in a dict.
                return [val]
    return []


def _spot_quote(underlying: str) -> dict | None:
    symbol = underlying.upper()

    if symbol in _BSE_INDEX_NAMES:
        index_token = _BSE_INDEX_NAMES[symbol]
        segment = "bse_cm"
    else:
        index_token = _INDEX_NAMES.get(symbol)
        segment = "nse_cm"

    if not index_token:
        return None

    try:
        result = _session.client.quotes(
            instrument_tokens=[
                {
                    "instrument_token": index_token,
                    "exchange_segment": segment,
                }
            ],
            quote_type="all",
        )
    except Exception as exc:
        logger.warning(
            "[kotak_market_data] index quote %s failed: %s",
            underlying,
            exc,
        )
        return None

    rows = _unwrap_quotes(result)
    if not rows:
        return None

    row = rows[0]

    return {
        "ltp": float(
            row.get("ltp")
            or row.get("last_traded_price")
            or 0
        ),
        "open": _ohlc_val(row, "open"),
        "high": _ohlc_val(row, "high"),
        "low": _ohlc_val(row, "low"),
        "close": _ohlc_val(row, "close", alt_key=None),
    }


def get_spot_quote(underlying):
    return _spot_quote(underlying)


def get_batch_quotes(exchange, symbol_token_pairs, mode="FULL"):
    """One quotes() call for up to all pairs. `symbol_token_pairs` is
    (tradingsymbol, token) as returned by find_option_token(); the response
    is keyed by the numeric instrument token, so it's re-keyed by the
    caller's tradingsymbol here to match the Protocol contract."""
    del mode
    seg = _INDEX_EXCHANGE.get(str(exchange).upper(), "nse_fo")
    tokens = [
        {"instrument_token": str(token), "exchange_segment": seg}
        for _symbol, token in symbol_token_pairs
        if token
    ]
    if not tokens:
        return {}
    try:
        result = _session.client.quotes(instrument_tokens=tokens, quote_type="all")
        qrows = _unwrap_quotes(result)
    except Exception as exc:
        logger.warning("[kotak_market_data] batch quotes failed: %s", exc)
        return {}
    by_token = {
        _quote_token(q): q
        for q in qrows
        if _quote_token(q)
    }
    out = {}
    for symbol, token in symbol_token_pairs:
        q = by_token.get(str(token))
        if q:
            out[symbol] = q
    return out


def get_batch_quotes_by_token(exchange, symbol_token_pairs, mode="FULL"):
    """Same request as get_batch_quotes(), keyed by str(token) instead of
    tradingsymbol — matches the SmartAPI/Breeze convention."""
    by_symbol = get_batch_quotes(exchange, symbol_token_pairs, mode=mode)
    return {str(token): by_symbol[symbol] for symbol, token in symbol_token_pairs if symbol in by_symbol}


def get_futures_quote(underlying: str, which: str = "NEAR") -> dict | None:
    """Resolve and quote one Kotak F&O future for an underlying.

    This is especially important for SENSEX/BANKEX: BSE's public futures
    table can omit the last-traded price even though Kotak's BFO quote feed
    has it. Returns the standard futures row shape used by broker_pipeline.
    """
    segment = _fo_segment(underlying)
    today = __import__("datetime").datetime.now().date()
    contracts = []
    for row in _contracts_for(underlying):
        if row.get("name") != underlying.upper() or row.get("option_type") != "FUT":
            continue
        expiry = _parse_expiry_date(row.get("expiry"))
        if expiry:
            contracts.append((expiry, row))
    contracts = [(expiry, row) for expiry, row in contracts if expiry >= today] or contracts
    if not contracts:
        return None
    contracts.sort(key=lambda item: item[0])
    slot = {"NEAR": 0, "NEXT": 1, "FAR": 2}.get((which or "NEAR").upper(), 0)
    expiry, contract = contracts[min(slot, len(contracts) - 1)]
    try:
        result = _session.client.quotes(
            instrument_tokens=[
                {"instrument_token": contract["token"], "exchange_segment": segment}
            ],
            quote_type="all",
        )
        qrows = _unwrap_quotes(result)
    except Exception as exc:
        logger.warning("[kotak_market_data] future quote failed for %s: %s", underlying, exc)
        return None
    quote = next((q for q in qrows if _quote_token(q) == str(contract["token"])), None)
    if not quote:
        return None
    ltp = quote.get("ltp") if quote.get("ltp") is not None else quote.get("last_traded_price")
    try:
        ltp = float(ltp)
    except (TypeError, ValueError):
        return None
    if ltp <= 0:
        return None
    spot_quote = _spot_quote(underlying)
    spot = spot_quote.get("ltp") if spot_quote else None
    return {
        "Contract": contract["tradingsymbol"],
        "Underlying": underlying.upper(),
        "Expiry": expiry.strftime("%d-%b-%Y"),
        "LTP": ltp,
        "Change": quote.get("net_change", quote.get("change")),
        "PctChange": quote.get("per_change", quote.get("net_change_percentage")),
        "Open": _ohlc_val(quote, "open"),
        "High": _ohlc_val(quote, "high"),
        "Low": _ohlc_val(quote, "low"),
        "PrevClose": _ohlc_val(quote, "close", alt_key=None),
        "Volume": quote.get("last_volume", quote.get("volume")),
        "OI": quote.get("open_int", quote.get("open_interest")),
        "Spot": spot,
        "Basis": round(ltp - spot, 2) if spot else None,
    }
