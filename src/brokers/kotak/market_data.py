"""Kotak Neo market-data adapter — implements brokers.market_data's
MarketData Protocol as a runtime-selectable provider alongside SmartAPI/
Upstox/Shoonya/Kite/Breeze/NSE_BSE.

Two real gaps versus the SmartAPI implementation, called out here
rather than papered over (same "honest approximation" posture as
option_chain_json's Unusual Volume Activity card):

1. No live option-chain endpoint. Kotak's NEO API has no "whole expiry
   chain" REST call (Kotak's own support page: "Option Chain API is
   unavailable at the moment" — see the kotakneo.com FAQ). The adapter
   therefore builds the ATM window the same way Kite does: resolve the
   contract set from the NFO scrip master (search_scrip / scrip_master
   CSV), then pull live ltp/oi/volume per token with quotes(). This is
   a real architectural difference, not a data gap — get_atm_chain()
   below returns rows WITH live quotes (like Breeze/Upstox), so the
   pipeline's fetch_option_chain_wide() treats it like the other
   quote-carrying providers rather than the Kite metadata-only path.

2. Two-step TOTP+MPIN login, no long-lived token. See
   brokers.kotak.client's module docstring for the full reasoning.
   The session auto-logs-in from KOTAK_TOTP_SECRET + KOTAK_MPIN on
   first use, exactly like shoonya_client.py does for Shoonya.

Scrip-master caching: the NFO scrip master CSV (a few thousand rows for
the index options + all F&O stocks) is the resolution source for both
list_expiries() and find_option_token(). It is re-downloaded at most
every 6 hours (Kotak's file-paths endpoint is the authoritative listing,
and contracts roll over at the start of each expiry cycle) and cached
under paths.RUNTIME_DIR the same way brokers/breeze_market_data.py caches
its stock-code map — so it survives restarts without living inside the
package.

Strike-price encoding: the CSV stores dStrikePrice in PAISE (1755000
for strike 17550.00), matching the SDK's own search_scrip filter math
(dStrikePrice * 100). The adapter normalizes to the pipeline's rupee
convention.

The implementation is split across the private ``_md`` subpackage
(constants / scrip master / contracts / quotes / symbols); this module
is the public surface and defines ``get_atm_chain`` here so its globals
resolve through this module (the test suite monkeypatches
kotak._spot_quote / kotak._contracts_for on the package object).
"""
from __future__ import annotations

import logging

from brokers.kotak.client import _session

logger = logging.getLogger(__name__)
from ._md.constants import (
    _STRIKE_INTERVALS,
    _INDEX_NAMES,
    _BSE_INDEX_NAMES,
    _INDEX_EXCHANGE,
)
from ._md.scrip_master import (
    _FO_CSV_TTL_S,
    _FO_CACHE_DIR,
    _fo_cache_lock,
    _fo_segment,
    _fo_cache_path,
    _load_fo_scrips,
    _download_fo_scrips,
    _write_fo_csv,
    _parse_fo_csv_file,
    _parse_fo_csv_text,
    _unix_to_iso,
)
from ._md.contracts import (
    _normalize_scrip_row,
    _contracts_for,
    _round_to_strike,
    _parse_expiry_date,
    list_expiries,
    find_option_token,
)
from ._md.quotes import (
    _ohlc_val,
    _quote_token,
    _unwrap_quotes,
    _spot_quote,
    get_spot_quote,
    get_batch_quotes,
    get_batch_quotes_by_token,
    get_futures_quote,
)
from ._md.symbols import get_fno_underlyings, index_tokens


def get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
    """Resolve the ATM window from the scrip master, then overlay live
    ltp/oi/volume per token via quotes(). Returns the chain dict shape
    from the MarketData Protocol ('underlying', 'spot', 'atm_strike',
    'expiry', 'rows') or None."""
    del exchange
    quote = _spot_quote(underlying)
    if not quote:
        logger.warning("[kotak_market_data] could not fetch spot for %s", underlying)
        return None
    spot = quote["ltp"]
    atm = _round_to_strike(spot, underlying)
    interval = _STRIKE_INTERVALS.get(underlying.upper(), 50)
    strikes = {atm + i * interval for i in range(-strikes_around_atm, strikes_around_atm + 1)}

    segment = _fo_segment(underlying)
    rows = _contracts_for(underlying)
    if not rows:
        return None
    target_exp = _parse_expiry_date(expiry_ddmmmyyyy)
    matches = [
        r for r in rows
        if r["name"] == underlying.upper()
        and _parse_expiry_date(r["expiry"]) == target_exp
        and r["strike"] in strikes
    ]
    if not matches:
        return None

    # Batch-quote all CE+PE tokens in one quotes() call.
    tokens = [{"instrument_token": m["token"], "exchange_segment": segment} for m in matches]
    try:
        qresult = _session.client.quotes(instrument_tokens=tokens, quote_type="all")
        qrows = _unwrap_quotes(qresult)
    except Exception as exc:
        logger.warning("[kotak_market_data] batch quote failed: %s", exc)
        qrows = []
    quote_by_token = {}

    for q in qrows:
        tok = _quote_token(q)
        if tok:
            quote_by_token[tok] = q

    out = []
    for m in matches:
        q = quote_by_token.get(m["token"]) or {}
        ltp = float(q.get("ltp") or q.get("last_traded_price") or 0)
        close = _ohlc_val(q, "close", alt_key=None)
        net_change = q.get("net_change")
        if net_change is None:
            net_change = q.get("change")
        if net_change is None and ltp and close:
            net_change = ltp - float(close)
        pct_change = q.get("per_change")
        if pct_change is None:
            pct_change = q.get("percent_change")
        if pct_change is None:
            pct_change = q.get("net_change_percentage")
        if pct_change is None and net_change is not None and close:
            pct_change = float(net_change) / float(close) * 100.0
        out.append(
            {
                "strike": m["strike"],
                "type": m["option_type"],
                "tradingsymbol": m["tradingsymbol"],
                "token": m["token"],
                "ltp": ltp,
                "open": _ohlc_val(q, "open"),
                "high": _ohlc_val(q, "high"),
                "low": _ohlc_val(q, "low"),
                "close": close,
                "oi": float(
                    q.get("open_int")
                    or q.get("open_interest")
                    or 0
                ),
                "volume": float(
                    q.get("volume")
                    or q.get("volume_traded_today")
                    or q.get("last_volume")
                    or 0
                ),
                # Neo response shapes vary by SDK version: v2 commonly
                # uses `change`, while others expose `net_change`. Preserve
                # an explicit zero rather than treating it as missing.
                "net_change": net_change,
                "pct_change": pct_change,
                "lot_size": m["lot_size"],
            }
        )

    out.sort(key=lambda r: (r["strike"], r["type"]))
    return {
        "underlying": underlying.upper(),
        "spot": spot,
        "atm_strike": atm,
        "expiry": expiry_ddmmmyyyy,
        "rows": out,
    }


# Note: the MarketData Protocol adapter class itself lives in
# brokers.kotak.adapter.KotakMarketData (the canonical one used by the
# registry). A duplicate wrapper class that previously lived in this file
# has been removed — it was dead code shadowed by the adapter module.
