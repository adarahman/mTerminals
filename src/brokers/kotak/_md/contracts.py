"""Kotak Neo contract resolution.

Turns an (underlying, expiry, strike, option_type) into a
``{'tradingsymbol', 'token'}`` using Kotak's own segment-aware
``search_scrip`` first, then falling back to the cached scrip master
from ``.scrip_master``. Also enumerates expiries for an underlying.
"""
from __future__ import annotations

import logging
from datetime import datetime

from brokers.kotak.client import _session
from .constants import _STRIKE_INTERVALS
from .scrip_master import _unix_to_iso, _fo_segment, _load_fo_scrips

logger = logging.getLogger(__name__)


def _normalize_scrip_row(raw: dict) -> dict | None:
    """Normalize one official SDK search_scrip() result."""
    if not isinstance(raw, dict):
        return None
    def field(*names):
        normalized = {
            str(key).lstrip("\ufeff").strip().rstrip(";").lower(): value
            for key, value in raw.items() if key
        }
        return next((normalized.get(name.rstrip(";").lower()) for name in names
                     if normalized.get(name.rstrip(";").lower()) not in (None, "")), None)
    inst_type = str(field("pInstType", "instrument_type") or "").upper()
    option_type = str(field("pOptionType", "option_type") or "").upper()
    symbol_hint = str(field("pTrdSymbol", "trading_symbol") or "").upper()
    if not inst_type:
        if symbol_hint.endswith(("CE", "PE")):
            inst_type = "OPTIDX"
        elif symbol_hint.endswith("FUT"):
            inst_type = "FUTIDX"
    if not option_type:
        if symbol_hint.endswith(("CE", "PE")):
            option_type = symbol_hint[-2:]
        elif symbol_hint.endswith("FUT"):
            option_type = "FUT"
    is_future = inst_type in {"FUTIDX", "FUTSTK"}
    if not is_future and option_type not in {"CE", "PE"}:
        return None
    try:
        expiry = _unix_to_iso(int(float(field("pExpiryDate", "expiry_date") or 0)))
        strike = round(float(field("dStrikePrice", "strike_price", "strike") or 0) / 100.0)
    except (TypeError, ValueError):
        return None
    return {
        "name": str(field("pSymbolName", "symbol_name", "underlying") or "").upper(),
        "tradingsymbol": str(field("pTrdSymbol", "trading_symbol") or "").upper(),
        "token": str(field("pSymbol", "symbol", "token") or ""),
        "option_type": "FUT" if is_future else option_type,
        "instrument_type": inst_type,
        "strike": strike,
        "expiry": expiry,
        "lot_size": int(float(field("lLotSize", "lot_size") or 0)),
    }


def _contracts_for(underlying: str) -> list[dict]:
    """Use Neo's own segment-aware search/cache before manual CSV parsing."""
    segment = _fo_segment(underlying)
    try:
        result = _session.client.search_scrip(
            exchange_segment=segment,
            symbol=underlying.upper(),
            expiry="",
            option_type="CE,PE,FUT",
            strike_price="",
            ignore_50multiple=False,
        )
        raw_rows = result if isinstance(result, list) else (result or {}).get("data", [])
        rows = [row for item in raw_rows if (row := _normalize_scrip_row(item))]
        if rows:
            return rows
    except Exception as exc:
        logger.info("[kotak_market_data] search_scrip(%s, %s) unavailable: %s", segment, underlying, exc)
    return [row for row in _load_fo_scrips(segment) if row.get("name") == underlying.upper()]


from brokers.numeric_helpers import round_to_strike as _round_to_strike  # noqa: E402  (was a local copy; see numeric_helpers.py)


def _parse_expiry_date(expiry_iso: str):
    try:
        return datetime.strptime(expiry_iso, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def list_expiries(underlying: str, exchange: str = "NFO") -> list:
    """Sorted 'DD-Mon-YYYY' expiry strings for `underlying`, nearest first,
    derived from the NFO scrip master. Returns [] if the master can't be
    fetched (callers already treat that as "no expiries right now")."""
    del exchange  # Kotak resolves the segment from the underlying itself
    rows = _contracts_for(underlying)
    if not rows:
        return []
    matches = [r for r in rows if r["name"] == underlying.upper() and r["option_type"] == "CE"]
    dates = set()
    for r in matches:
        try:
            dates.add(datetime.strptime(r["expiry"], "%d-%b-%Y").date())
        except (TypeError, ValueError):
            continue
    return [d.strftime("%d-%b-%Y") for d in sorted(dates)]


def find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
    """{'tradingsymbol', 'token'} for one contract via the scrip master, or
    None if unresolved."""
    del exchange
    rows = _contracts_for(underlying)
    if not rows:
        return None
    target_exp = _parse_expiry_date(expiry_ddmmmyyyy)
    matches = [
        r
        for r in rows
        if r["name"] == underlying.upper()
        and _parse_expiry_date(r["expiry"]) == target_exp
        and r["strike"] == int(round(float(strike)))
        and r["option_type"] == opt_type.upper()
    ]
    if not matches:
        return None
    return {"tradingsymbol": matches[0]["tradingsymbol"], "token": matches[0]["token"]}
