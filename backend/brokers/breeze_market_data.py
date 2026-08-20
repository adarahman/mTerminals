"""ICICI Breeze market-data adapter — implements brokers.market_data's
MarketData Protocol as a second provider alongside SmartApiMarketData.

Three real gaps versus the SmartAPI implementation, called out here
rather than papered over (same "honest approximation" posture as
option_chain_json's Unusual Volume Activity card):

1. No batch quoting. SmartAPI's get_batch_quotes() fetches up to 50
   contracts in one getMarketData call; Breeze's get_quotes() is one
   contract per REST call. get_batch_quotes() below loops one call per
   pair — keep `symbol_token_pairs` short (this is why get_atm_chain()
   uses get_option_chain_quotes() instead, which DOES return a whole
   expiry's strikes in one call).

2. No expiry-list endpoint. Breeze's option-chain/quote endpoints all
   require expiry_date as an input (it cannot be left empty for NFO —
   confirmed in Breeze's own field-validation docs), so there is no
   REST call that answers "what expiries exist for this underlying" the
   way SmartAPI's ScripMaster scan does. list_expiries() below computes
   the standard NSE/BSE weekly+monthly cycle (NIFTY weekly = Tuesday,
   BANKNIFTY/FINNIFTY/MIDCPNIFTY = monthly only on the last Tuesday,
   SENSEX weekly = Thursday — current as of the Sep-2025 SEBI-mandated
   schedule) instead of reading it from the broker. This is a computed
   calendar, not broker-confirmed data — a real exchange holiday can
   shift a date by one trading day. Callers should treat a None return
   from get_atm_chain()/find_option_token() as "this expiry doesn't
   actually exist", not retry it as a transient error.

3. No F&O stock universe endpoint. get_fno_underlyings() can't derive
   the full stock list the way SmartAPI's ScripMaster scan does (that
   needs Breeze's security-master CSV download, a separate manual/daily
   artifact outside this SDK) — it returns the known index list only,
   with `stocks` deliberately left empty rather than guessed.

stock_code resolution (Breeze's own short codes, e.g. "ICIBAN" for
ICICIBANK — NOT the NSE tradingsymbol) goes through resolve_stock_code(),
disk-cached the same way brokers/smartapi_instruments.py caches its
ScripMaster, under paths.RUNTIME_DIR so it survives restarts without
living inside the package.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

try:
    from config import settings
    from paths import RUNTIME_DIR
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from backend.config import settings
    from backend.paths import RUNTIME_DIR

from brokers.breeze_client import _session, _unwrap, _iso_expiry, BrokerError
from brokers.breeze_client import resolve_option_contract as _cache_contract

logger = logging.getLogger(__name__)

_WARNING_COOLDOWN_S = 60.0
_last_warning_at: dict[str, float] = {}
_warning_lock = threading.Lock()


def _warning_once(key: str, message: str, *args) -> None:
    """Keep a dead daily Breeze session from filling the live-server log."""
    now = time.monotonic()
    with _warning_lock:
        last = _last_warning_at.get(key)
        if last is not None and now - last < _WARNING_COOLDOWN_S:
            return
        _last_warning_at[key] = now
    logger.warning(message, *args)

# Same physical strike spacing SmartAPI's STRIKE_INTERVALS uses — kept as
# an independent copy rather than importing brokers.smartapi_client (that
# module imports the SmartApi SDK at module top level, which would make a
# Breeze-only deployment depend on smartapi-python being installed just to
# read a constants dict). This is the same category of duplication as the
# two LOT_SIZES dicts already tracked as a dedup TODO elsewhere in this
# codebase — flagged here rather than silently repeated.
_STRIKE_INTERVALS = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100,
}

# Underlyings with a Breeze stock_code identical to their NSE/BSE name —
# confirmed directly against Breeze's own place_order()/get_quotes() docs
# examples (stock_code="NIFTY" for NFO futures/options). Anything not in
# this map goes through resolve_stock_code()'s get_names() lookup.
_INDEX_STOCK_CODES = {
    "NIFTY": "NIFTY", "SENSEX": "SENSEX",
    "BANKNIFTY": "CNXBAN", "FINNIFTY": "NIFFIN", "MIDCPNIFTY": "NIFMCP",
}

_STOCK_CODE_CACHE_PATH = os.path.join(RUNTIME_DIR, "breeze_cache", "stock_codes.json")
_stock_code_cache_lock = threading.Lock()
_stock_code_cache: dict | None = None


def _load_stock_code_cache() -> dict:
    global _stock_code_cache
    with _stock_code_cache_lock:
        if _stock_code_cache is not None:
            return _stock_code_cache
        try:
            with open(_STOCK_CODE_CACHE_PATH) as f:
                _stock_code_cache = json.load(f)
        except (OSError, ValueError):
            _stock_code_cache = {}
        return _stock_code_cache


def _save_stock_code_cache():
    os.makedirs(os.path.dirname(_STOCK_CODE_CACHE_PATH), exist_ok=True)
    with _stock_code_cache_lock:
        with open(_STOCK_CODE_CACHE_PATH, "w") as f:
            json.dump(_stock_code_cache, f)


def resolve_stock_code(underlying: str, exchange: str = "NSE") -> str | None:
    """NSE/BSE symbol -> Breeze's own stock_code, via get_names(), cached
    to disk (this rarely changes). Returns None if Breeze doesn't
    recognize the symbol."""
    underlying = underlying.upper()
    if underlying in _INDEX_STOCK_CODES:
        return _INDEX_STOCK_CODES[underlying]

    cache = _load_stock_code_cache()
    cache_key = f"{exchange}:{underlying}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        result = _session.api.get_names(exchange, underlying)
    except Exception as exc:
        _warning_once(
            "get_names",
            "[breeze_market_data] get_names(%s, %s) failed: %s",
            exchange,
            underlying,
            exc,
        )
        return None
    code = None
    if isinstance(result, dict):
        code = result.get("isec_stock_code") or result.get("stock_code")
    if not code:
        logger.warning("[breeze_market_data] no stock_code found for %s:%s", exchange, underlying)
        return None

    cache[cache_key] = code
    _save_stock_code_cache()
    return code


def list_expiries(underlying: str, exchange: str = "NFO") -> list:
    """Computed NSE/BSE expiry calendar — see module docstring caveat 2.
    Returns the next 6 valid cycle dates as 'DD-Mon-YYYY' strings, nearest
    first. Does not adjust for exchange holidays."""
    del exchange
    underlying = underlying.upper()
    today = datetime.now().date()

    def _next_weekday(from_date, weekday):
        # weekday: Monday=0 ... Sunday=6
        days_ahead = (weekday - from_date.weekday()) % 7
        return from_date + timedelta(days=days_ahead or 7)

    def _last_weekday_of_month(year, month, weekday):
        if month == 12:
            next_month_first = datetime(year + 1, 1, 1).date()
        else:
            next_month_first = datetime(year, month + 1, 1).date()
        d = next_month_first - timedelta(days=1)
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d

    def _monthly_cycle(weekday, count=3):
        out = []
        year, month = today.year, today.month
        for _ in range(count + 1):
            candidate = _last_weekday_of_month(year, month, weekday)
            if candidate >= today:
                out.append(candidate)
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return out[:count]

    if underlying == "NIFTY":
        weeklies = [_next_weekday(today, 1) for _ in range(1)]  # Tuesday
        # Roll forward 4 consecutive Tuesdays.
        dates = [weeklies[0] + timedelta(weeks=i) for i in range(4)]
        dates += _monthly_cycle(1, count=2)
    elif underlying == "SENSEX":
        weeklies = [_next_weekday(today, 3)]  # Thursday
        dates = [weeklies[0] + timedelta(weeks=i) for i in range(4)]
        dates += _monthly_cycle(3, count=2)
    elif underlying in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
        dates = _monthly_cycle(1, count=4)  # monthly-only, last Tuesday
    elif underlying == "BANKEX":
        dates = _monthly_cycle(3, count=4)  # monthly-only, last Thursday
    else:
        # Individual F&O stocks: NSE monthly cycle, last Tuesday.
        dates = _monthly_cycle(1, count=3)

    unique_sorted = sorted(set(dates))
    return [d.strftime("%d-%b-%Y") for d in unique_sorted]


def get_spot_quote(underlying: str):
    exchange = "BSE" if underlying.upper() == "SENSEX" else "NSE"
    stock_code = resolve_stock_code(underlying, exchange)
    if not stock_code:
        return None
    try:
        result = _session.api.get_quotes(
            stock_code=stock_code, exchange_code=exchange,
            expiry_date="", product_type="", right="", strike_price="",
        )
        rows = _unwrap(result, "get_quotes")
    except Exception as exc:
        # breeze_connect raises a bare Exception (not BrokerError) on a
        # 503/rate-limit response — see get_batch_quotes()'s identical fix
        # for why the narrower except was missing this case.
        _warning_once(
            "get_spot_quote",
            "[breeze_market_data] get_spot_quote(%s) failed: %s",
            underlying,
            exc,
        )
        return None
    row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
    if not row:
        return None
    return {
        "ltp": float(row.get("ltp") or 0),
        "open": float(row.get("open") or 0),
        "high": float(row.get("high") or 0),
        "low": float(row.get("low") or 0),
        "close": float(row.get("previous_close") or 0),
    }


def _round_to_strike(price, underlying):
    interval = _STRIKE_INTERVALS.get(underlying.upper(), 50)
    return int(round(price / interval) * interval)


def get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
    del exchange
    quote = get_spot_quote(underlying)
    if not quote:
        logger.warning("[breeze_market_data] could not fetch spot for %s", underlying)
        return None
    spot = quote["ltp"]
    atm = _round_to_strike(spot, underlying)
    interval = _STRIKE_INTERVALS.get(underlying.upper(), 50)
    strikes = {atm + i * interval for i in range(-strikes_around_atm, strikes_around_atm + 1)}

    stock_code = resolve_stock_code(underlying, "BSE" if underlying.upper() == "SENSEX" else "NSE")
    if not stock_code:
        return None
    expiry_iso = _iso_expiry(expiry_ddmmmyyyy)

    rows = []
    for right, opt_type in (("call", "CE"), ("put", "PE")):
        try:
            result = _session.api.get_option_chain_quotes(
                stock_code=stock_code, exchange_code="NFO", product_type="options",
                expiry_date=expiry_iso, right=right, strike_price="",
            )
            chain_rows = _unwrap(result, "get_option_chain_quotes") or []
        except BrokerError as exc:
            logger.warning(
                "[breeze_market_data] get_option_chain_quotes(%s, %s, %s) failed: %s",
                underlying, expiry_ddmmmyyyy, right, exc,
            )
            continue
        for row in chain_rows:
            try:
                strike_val = int(round(float(row.get("strike_price"))))
            except (TypeError, ValueError):
                continue
            if strike_val not in strikes:
                continue
            contract = _cache_contract(stock_code, expiry_ddmmmyyyy, strike_val, opt_type, "NFO")
            _, tradingsymbol, token = contract if contract else (None, None, None)
            rows.append({
                "strike": strike_val,
                "type": opt_type,
                "tradingsymbol": tradingsymbol,
                "token": token,
                "ltp": float(row.get("ltp") or 0),
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": float(row.get("previous_close") or 0),
                "oi": float(row.get("open_interest") or 0),
                "volume": float(row.get("total_quantity_traded") or 0),
                "net_change": None,
                "pct_change": float(row.get("ltp_percent_change") or 0),
            })

    if not rows:
        return None
    rows.sort(key=lambda r: (r["strike"], r["type"]))
    return {
        "underlying": underlying.upper(),
        "spot": spot,
        "atm_strike": atm,
        "expiry": expiry_ddmmmyyyy,
        "rows": rows,
    }


def find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
    del exchange
    stock_code = resolve_stock_code(underlying, "BSE" if underlying.upper() == "SENSEX" else "NSE")
    if not stock_code:
        return None
    contract = _cache_contract(stock_code, expiry_ddmmmyyyy, strike, opt_type, "NFO")
    if not contract:
        return None
    _, tradingsymbol, token = contract
    return {"tradingsymbol": tradingsymbol, "token": token}


def get_batch_quotes(exchange, symbol_token_pairs, mode="FULL"):
    """One get_quotes() REST call per pair — see module docstring caveat
    1. `symbol_token_pairs` here is (tradingsymbol, token) as returned by
    find_option_token()/resolve_option_contract(); tradingsymbol is the
    synthetic cache key, looked back up in breeze_client._CONTRACT_CACHE
    for the real stock_code/expiry/strike/right."""
    del mode
    from brokers.breeze_client import _CONTRACT_CACHE, _CONTRACT_CACHE_LOCK

    out = {}
    for tradingsymbol, _token in symbol_token_pairs:
        with _CONTRACT_CACHE_LOCK:
            contract = _CONTRACT_CACHE.get(tradingsymbol)
        if not contract:
            continue
        try:
            result = _session.api.get_quotes(
                stock_code=contract["stock_code"], exchange_code=exchange,
                expiry_date=contract["expiry_date"], product_type=contract["product"],
                right=contract["right"], strike_price=contract["strike_price"],
            )
            rows = _unwrap(result, "get_quotes")
        except Exception as exc:
            # breeze_connect's SDK raises a bare Exception (not BrokerError)
            # on a 503/rate-limit response — it wraps requests' JSONDecodeError
            # rather than surfacing the HTTP status. Catch broadly here so one
            # throttled leg doesn't abort the whole chain fetch; skip it and
            # keep going, same fail-soft posture as the BrokerError case below.
            logger.warning("[breeze_market_data] get_quotes(%s) failed: %s", tradingsymbol, exc)
            continue
        row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
        if row:
            out[tradingsymbol] = row
            
    return out


def get_batch_quotes_by_token(exchange, symbol_token_pairs, mode="FULL"):
    """Same request as get_batch_quotes(), keyed by token instead of
    tradingsymbol — matches SmartApiMarketData's distinction, though
    Breeze has no numeric token, so the synthetic tradingsymbol key is
    used for both here."""
    by_symbol = get_batch_quotes(exchange, symbol_token_pairs, mode=mode)
    return {token: by_symbol[symbol] for symbol, token in symbol_token_pairs if symbol in by_symbol}


def get_fno_underlyings(force_refresh=False):
    """Index list only — see module docstring caveat 3. `stocks` is
    deliberately empty rather than guessed; wire in a security-master CSV
    parser here if per-stock F&O coverage is needed."""
    del force_refresh
    return {"indices": ["BANKEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX"], "stocks": []}


def index_tokens():
    """Shaped like SmartApiMarketData.index_tokens(), but 'token' here is
    Breeze's stock_code (a string), not a numeric SmartAPI instrument
    token — callers that treat this dict as opaque and pass 'token'
    straight through to get_batch_quotes() (as broker_pipeline.py
    does) still work; callers that assume it's numeric will not."""
    return {
        "NIFTY": {"token": "NIFTY", "exchange": "NSE"},
        "BANKNIFTY": {"token": resolve_stock_code("BANKNIFTY") or "CNXBAN", "exchange": "NSE"},
        "FINNIFTY": {"token": resolve_stock_code("FINNIFTY") or "NIFFIN", "exchange": "NSE"},
        "MIDCPNIFTY": {"token": resolve_stock_code("MIDCPNIFTY") or "NIFMCP", "exchange": "NSE"},
        "SENSEX": {"token": "SENSEX", "exchange": "BSE"},
    }


class BreezeMarketData:
    """Adapter satisfying brokers.market_data.MarketData — see that
    module's Protocol for the interface contract."""

    def list_expiries(self, underlying, exchange="NFO"):
        return list_expiries(underlying, exchange=exchange)

    def get_atm_chain(self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
        return get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm, exchange=exchange)

    def find_option_token(self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
        return find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange=exchange)

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        return get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        return get_batch_quotes_by_token(exchange, symbol_token_pairs, mode=mode)

    def get_spot_quote(self, underlying):
        return get_spot_quote(underlying)

    def get_fno_underlyings(self, force_refresh=False):
        return get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        return index_tokens()
