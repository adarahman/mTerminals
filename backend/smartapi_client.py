"""
smartapi_client.py
===================
SmartAPI (Angel One) data-fetch layer for mTerminals.
Mirrors the shape of market_api.py's NSE/BSE fetchers so it can be
dropped in as an additional data source without changing call patterns
elsewhere in the pipeline (ws_server_live.py, engine.py, etc).

Sections
--------
  1. Imports & shared constants
  2. Session management       — login, token cache, auto re-login
  3. Instrument/token lookup  — symbol -> token resolution via ScripMaster
  4. Market data fetchers     — LTP, index quote, option chain, futures
  5. __main__ smoke-test
"""

# ── 1. Imports & shared constants ──────────────────────────────────────────
import os
import time
import json
import threading
from datetime import datetime, timedelta

import requests
import pyotp
from dotenv import load_dotenv
from logzero import logger
from SmartApi import SmartConnect

# Load .env from mTerminals/ (the parent of this file's backend/ folder),
# regardless of what directory the process was launched from.
_ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)
load_dotenv(_ENV_PATH)

API_KEY = os.getenv("SMARTAPI_KEY")
CLIENT_CODE = os.getenv("SMARTAPI_CLIENT_CODE")
PIN = os.getenv("SMARTAPI_PIN")
TOTP_SECRET = os.getenv("SMARTAPI_TOTP_SECRET")

SCRIP_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)
SCRIP_MASTER_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_scrip_master_cache.json"
)
SCRIP_MASTER_TTL_HOURS = 20  # refresh once a day; contracts don't change intraday

# Index -> underlying token/exchange mapping SmartAPI expects for spot quotes
INDEX_TOKENS = {
    "NIFTY":      {"token": "99926000", "exchange": "NSE"},
    "BANKNIFTY":  {"token": "99926009", "exchange": "NSE"},
    "FINNIFTY":   {"token": "99926037", "exchange": "NSE"},
    "MIDCPNIFTY": {"token": "99926074", "exchange": "NSE"},
    "SENSEX":     {"token": "99919000", "exchange": "BSE"},
    "BANKEX":     {"token": "99919012", "exchange": "BSE"},
}


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ── 2. Session management ──────────────────────────────────────────────────
class SmartApiSession:
    """
    Thread-safe singleton-style session wrapper. Handles login, caches
    auth/feed/refresh tokens in memory, and re-authenticates automatically
    if a call fails with a token-expiry error.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._smart_api = None
        self._auth_token = None
        self._refresh_token = None
        self._feed_token = None
        self._login_time = None

    def _login(self):
        missing = [
            name for name, val in [
                ("SMARTAPI_KEY", API_KEY),
                ("SMARTAPI_CLIENT_CODE", CLIENT_CODE),
                ("SMARTAPI_PIN", PIN),
                ("SMARTAPI_TOTP_SECRET", TOTP_SECRET),
            ] if not val
        ]
        if missing:
            raise RuntimeError(f"Missing .env values: {', '.join(missing)}")

        smart_api = SmartConnect(API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = smart_api.generateSession(CLIENT_CODE, PIN, totp)

        if not data.get("status"):
            raise RuntimeError(f"SmartAPI login failed: {data}")

        self._smart_api = smart_api
        self._auth_token = data["data"]["jwtToken"]
        self._refresh_token = data["data"]["refreshToken"]
        self._feed_token = smart_api.getfeedToken()
        self._login_time = datetime.now()
        logger.info("[smartapi_client] Logged in, session established")

    def ensure_session(self):
        """Login if we don't yet have a session. SmartAPI sessions are
        valid till midnight, so we also force re-login once a stale day
        boundary is crossed."""
        with self._lock:
            stale = (
                self._login_time is None
                or self._login_time.date() != datetime.now().date()
            )
            if self._smart_api is None or stale:
                self._login()
        return self._smart_api

    @property
    def feed_token(self):
        self.ensure_session()
        return self._feed_token

    @property
    def auth_token(self):
        self.ensure_session()
        return self._auth_token

    def call(self, fn_name, *args, **kwargs):
        """
        Call a SmartConnect method by name, auto re-logging in once on
        auth-type failures (expired/invalid token) before giving up.
        """
        smart_api = self.ensure_session()
        method = getattr(smart_api, fn_name)
        try:
            result = method(*args, **kwargs)
        except Exception as e:
            logger.warning(f"[smartapi_client] {fn_name} raised {e}, retrying after re-login")
            with self._lock:
                self._smart_api = None
            smart_api = self.ensure_session()
            method = getattr(smart_api, fn_name)
            result = method(*args, **kwargs)
            return result

        if isinstance(result, dict) and not result.get("status", True):
            errorcode = str(result.get("errorcode", ""))
            if errorcode in {"AG8001", "AG8002", "AB1010", "AB1050"}:  # token/session errors
                logger.warning(f"[smartapi_client] {fn_name} token error {errorcode}, re-logging in")
                with self._lock:
                    self._smart_api = None
                smart_api = self.ensure_session()
                method = getattr(smart_api, fn_name)
                result = method(*args, **kwargs)

        return result


_session = SmartApiSession()


# ── 3. Instrument / token lookup ───────────────────────────────────────────
_scrip_master_cache = {"data": None, "fetched_at": None}


def _load_scrip_master():
    """Downloads (or reuses a local cache of) Angel One's ScripMaster —
    the master list mapping tradingsymbol -> token for every instrument."""
    now = datetime.now()
    if (
        _scrip_master_cache["data"] is not None
        and _scrip_master_cache["fetched_at"]
        and now - _scrip_master_cache["fetched_at"] < timedelta(hours=SCRIP_MASTER_TTL_HOURS)
    ):
        return _scrip_master_cache["data"]

    if os.path.exists(SCRIP_MASTER_CACHE_PATH):
        mtime = datetime.fromtimestamp(os.path.getmtime(SCRIP_MASTER_CACHE_PATH))
        if now - mtime < timedelta(hours=SCRIP_MASTER_TTL_HOURS):
            with open(SCRIP_MASTER_CACHE_PATH, "r") as f:
                data = json.load(f)
            _scrip_master_cache.update(data=data, fetched_at=now)
            logger.info(f"[smartapi_client] ScripMaster loaded from local cache ({len(data)} rows)")
            return data

    logger.info("[smartapi_client] Downloading fresh ScripMaster (~few MB, one-time)...")
    resp = requests.get(SCRIP_MASTER_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    with open(SCRIP_MASTER_CACHE_PATH, "w") as f:
        json.dump(data, f)

    _scrip_master_cache.update(data=data, fetched_at=now)
    logger.info(f"[smartapi_client] ScripMaster downloaded and cached ({len(data)} rows)")
    return data


def find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
    """
    Resolve a NIFTY/BANKNIFTY/SENSEX option contract to its SmartAPI token.

    underlying:   'NIFTY' | 'BANKNIFTY' | 'SENSEX' | ...
    expiry_ddmmmyyyy: e.g. '31JUL2026'  (matches ScripMaster's expiry format)
    strike:       e.g. 24200 (numeric, will be matched against strike*100 field)
    opt_type:     'CE' | 'PE'
    exchange:     'NFO' for NSE options, 'BFO' for BSE (SENSEX) options
    """
    data = _load_scrip_master()

    for row in data:
        if (
            row.get("exch_seg") == exchange
            and row.get("name") == underlying
            and row.get("expiry") == expiry_ddmmmyyyy
            and row.get("symbol", "").endswith(opt_type)
        ):
            try:
                row_strike = round(float(row["strike"]) / 100)
            except (KeyError, ValueError, TypeError):
                continue
            if row_strike == int(round(strike)):
                return {"token": row["token"], "tradingsymbol": row["symbol"]}

    logger.warning(
        f"[smartapi_client] No token found for {underlying} {expiry_ddmmmyyyy} "
        f"{strike}{opt_type} on {exchange}"
    )
    return None


def list_expiries(underlying, exchange="NFO"):
    """Return sorted list of available expiry strings for an underlying."""
    data = _load_scrip_master()
    expiries = sorted(
        {
            row["expiry"]
            for row in data
            if row.get("exch_seg") == exchange and row.get("name") == underlying and row.get("expiry")
        },
        key=lambda d: datetime.strptime(d, "%d%b%Y"),
    )
    return expiries


# ── 4. Market data fetchers ────────────────────────────────────────────────
def get_index_quote(symbol):
    """LTP + basic OHLC for an index (NIFTY, BANKNIFTY, SENSEX, ...)."""
    info = INDEX_TOKENS.get(symbol.upper())
    if not info:
        logger.warning(f"[smartapi_client] Unknown index symbol: {symbol}")
        return None

    result = _session.call(
        "ltpData", info["exchange"], symbol.upper(), info["token"]
    )
    if not result.get("status"):
        logger.warning(f"[smartapi_client] get_index_quote failed for {symbol}: {result}")
        return None

    d = result["data"]
    return {
        "symbol": symbol.upper(),
        "ltp": safe_float(d.get("ltp")),
        "open": safe_float(d.get("open")),
        "high": safe_float(d.get("high")),
        "low": safe_float(d.get("low")),
        "close": safe_float(d.get("close")),
    }


def get_ltp(exchange, tradingsymbol, token):
    """Generic LTP fetch for any resolved instrument (option, future, stock)."""
    result = _session.call("ltpData", exchange, tradingsymbol, token)
    if not result.get("status"):
        logger.warning(f"[smartapi_client] get_ltp failed for {tradingsymbol}: {result}")
        return None
    return safe_float(result["data"].get("ltp"))


def get_option_chain_row(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
    """Fetch a single CE/PE row's live quote (LTP, OI, volume) for one strike."""
    resolved = find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange)
    if not resolved:
        return None

    result = _session.call(
        "ltpData", exchange, resolved["tradingsymbol"], resolved["token"]
    )
    if not result.get("status"):
        logger.warning(f"[smartapi_client] chain row fetch failed: {result}")
        return None

    d = result["data"]
    return {
        "tradingsymbol": resolved["tradingsymbol"],
        "token": resolved["token"],
        "strike": strike,
        "type": opt_type,
        "ltp": safe_float(d.get("ltp")),
        "open": safe_float(d.get("open")),
        "high": safe_float(d.get("high")),
        "low": safe_float(d.get("low")),
        "close": safe_float(d.get("close")),
    }


def get_full_option_chain(underlying, expiry_ddmmmyyyy, strikes, exchange="NFO"):
    """
    Fetch CE + PE rows for a list of strikes. Returns a list of dicts,
    shaped to be easy to merge into your existing option-chain DataFrame
    builders in mTerminals_json.py / engine.py.
    """
    rows = []
    for strike in strikes:
        for opt_type in ("CE", "PE"):
            row = get_option_chain_row(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange)
            if row:
                rows.append(row)
    return rows


STRIKE_INTERVALS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
    "SENSEX": 100,
    "BANKEX": 100,
}


def _round_to_strike(price, underlying):
    interval = STRIKE_INTERVALS.get(underlying.upper(), 50)
    return int(round(price / interval) * interval)


def get_batch_quotes(exchange, symbol_token_pairs, mode="FULL"):
    """
    Fetch quotes for up to 50 (tradingsymbol, token) pairs in ONE API call,
    instead of one call per symbol. This is what get_full_option_chain uses
    under the hood — call directly if you need custom batches (e.g. futures
    + a handful of strikes together).

    symbol_token_pairs: list of (tradingsymbol, token) tuples
    mode: 'LTP' | 'OHLC' | 'FULL'  (FULL includes OI, volume, depth)
    Returns: dict keyed by tradingsymbol -> quote data
    """
    if not symbol_token_pairs:
        return {}

    results = {}
    # SmartAPI batch endpoint caps at 50 symbols per exchange per call
    for i in range(0, len(symbol_token_pairs), 50):
        chunk = symbol_token_pairs[i:i + 50]
        tokens = [token for _, token in chunk]
        response = _session.call(
            "getMarketData", mode, {exchange: tokens}
        )
        if not response.get("status"):
            logger.warning(f"[smartapi_client] getMarketData batch failed: {response}")
            continue

        fetched = response.get("data", {}).get("fetched", [])
        for row in fetched:
            results[row.get("tradingSymbol", row.get("symbolToken"))] = row

    return results


def get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
    """
    Fetch a live CE/PE option chain centered on the current ATM strike.

    underlying:          'NIFTY' | 'BANKNIFTY' | 'SENSEX' | ...
    expiry_ddmmmyyyy:     e.g. '31JUL2026' — use list_expiries() to get valid values
    strikes_around_atm:   how many strikes on each side of ATM to include
                          (10 => 21 total strikes => 42 rows CE+PE)
    exchange:             'NFO' for NSE options, 'BFO' for BSE/SENSEX options

    Returns: {
        'underlying': ..., 'spot': ..., 'atm_strike': ...,
        'expiry': ..., 'rows': [ {strike, type, ltp, oi, volume, ...}, ... ]
    }
    """
    quote = get_index_quote(underlying)
    if not quote:
        logger.warning(f"[smartapi_client] Could not fetch spot for {underlying}")
        return None

    spot = quote["ltp"]
    atm = _round_to_strike(spot, underlying)
    interval = STRIKE_INTERVALS.get(underlying.upper(), 50)
    strikes = [atm + (i * interval) for i in range(-strikes_around_atm, strikes_around_atm + 1)]

    data = _load_scrip_master()
    strike_lookup = {}
    for row in data:
        if (
            row.get("exch_seg") == exchange
            and row.get("name") == underlying.upper()
            and row.get("expiry") == expiry_ddmmmyyyy
        ):
            try:
                strike_val = int(round(float(row["strike"]) / 100))
            except (KeyError, ValueError):
                continue
            symbol = row.get("symbol", "")
            opt_type = "CE" if symbol.endswith("CE") else "PE" if symbol.endswith("PE") else None
            if opt_type and strike_val in strikes:
                strike_lookup[(strike_val, opt_type)] = {
                    "token": row["token"],
                    "tradingsymbol": symbol,
                }

    pairs = [
        (info["tradingsymbol"], info["token"])
        for info in strike_lookup.values()
    ]
    if not pairs:
        logger.warning(
            f"[smartapi_client] No contracts resolved for {underlying} {expiry_ddmmmyyyy} "
            f"around ATM {atm} — check expiry format matches list_expiries() output"
        )
        return None

    quotes = get_batch_quotes(exchange, pairs, mode="FULL")

    rows = []
    for (strike_val, opt_type), info in strike_lookup.items():
        q = quotes.get(info["tradingsymbol"])
        if not q:
            continue
        rows.append({
            "strike": strike_val,
            "type": opt_type,
            "tradingsymbol": info["tradingsymbol"],
            "token": info["token"],
            "ltp": safe_float(q.get("ltp")),
            "open": safe_float(q.get("open")),
            "high": safe_float(q.get("high")),
            "low": safe_float(q.get("low")),
            "close": safe_float(q.get("close")),
            "oi": safe_float(q.get("opnInterest")),
            "volume": safe_float(q.get("tradeVolume")),
            "net_change": safe_float(q.get("netChange")),
            "pct_change": safe_float(q.get("percentChange")),
        })

    rows.sort(key=lambda r: (r["strike"], r["type"]))

    return {
        "underlying": underlying.upper(),
        "spot": spot,
        "atm_strike": atm,
        "expiry": expiry_ddmmmyyyy,
        "rows": rows,
    }


def get_option_greeks(underlying, expiry_ddmmmyyyy):
    """Fetch Angel One's OWN computed Greeks (delta/gamma/theta/vega/IV) for
    every strike of one expiry in a single call — genuinely independent of
    engine.py's Black-Scholes calc, which derives Greeks from IV that comes
    from NSE's option-chain response via market_api.py. This is Angel's own
    number, not a derivation of it.

    underlying:       'NIFTY' | 'BANKNIFTY' | 'SENSEX' | ...
    expiry_ddmmmyyyy: e.g. '31JUL2026' — must match list_expiries() output

    Returns: {(strike, opt_type): {"delta":.., "gamma":.., "theta":.., "vega":.., "iv":..}, ...}
    keyed the same way as get_atm_chain()'s rows (strike, type), so callers
    can merge straight onto them — see get_atm_chain_with_greeks() below.

    Rate limit note: Angel's optionGreek endpoint is capped at 1 req/sec.
    This is ONE call per expiry (not per strike), so it's safe to call at
    normal poll cadence — just don't call it in a per-strike loop.
    """
    result = _session.call(
        "optionGreek", {"name": underlying.upper(), "expirydate": expiry_ddmmmyyyy}
    )
    if not result.get("status"):
        logger.warning(
            f"[smartapi_client] optionGreek failed for {underlying} {expiry_ddmmmyyyy}: {result}"
        )
        return {}

    out = {}
    for row in result.get("data", []) or []:
        try:
            strike = int(round(float(row.get("strikePrice"))))
        except (TypeError, ValueError):
            continue
        opt_type = row.get("optionType")
        if opt_type not in ("CE", "PE"):
            continue
        out[(strike, opt_type)] = {
            "delta": safe_float(row.get("delta")),
            "gamma": safe_float(row.get("gamma")),
            "theta": safe_float(row.get("theta")),
            "vega":  safe_float(row.get("vega")),
            "iv":    safe_float(row.get("impliedVolatility")),
        }
    return out


def get_atm_chain_with_greeks(underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
    """get_atm_chain() (LTP/OI/volume) + get_option_greeks() (delta/gamma/
    theta/vega/IV) merged onto the same rows by (strike, type). This is the
    shape a fully-independent SmartAPI pipeline needs: LTP, OI, AND Greeks
    from AngelOne alone, no dependency on market_api.py/NSE at all.

    Two separate API calls under the hood (getMarketData for quotes,
    optionGreek for Greeks) — Angel doesn't return both from one endpoint.
    If optionGreek doesn't have a strike (illiquid far leg, or Angel just
    hasn't priced it), that row's Greek fields are left as None rather than
    silently defaulted to 0, so callers can tell "no data" apart from a
    genuinely flat Greek.
    """
    chain = get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm, exchange)
    if not chain:
        return None

    greeks_by_key = get_option_greeks(underlying, expiry_ddmmmyyyy)
    for row in chain["rows"]:
        g = greeks_by_key.get((row["strike"], row["type"]))
        if g:
            row.update(g)
        else:
            row.setdefault("delta", None)
            row.setdefault("gamma", None)
            row.setdefault("theta", None)
            row.setdefault("vega", None)
            row.setdefault("iv", None)

    return chain


def get_candle_data(exchange, symboltoken, interval, fromdate, todate):
    """Historical OHLCV candles for one instrument — an index (see
    INDEX_TOKENS), a future, or a resolved option token from
    find_option_token(). Useful for backfilling price/OI history rather
    than waiting for oi_analysis.py's snapshot-based history to accumulate
    live.

    exchange:    'NSE' | 'NFO' | 'BSE' | 'BFO'
    symboltoken: token string, e.g. INDEX_TOKENS['NIFTY']['token'] or a
                 find_option_token() result's 'token'
    interval:    one of SmartAPI's fixed set — 'ONE_MINUTE', 'FIVE_MINUTE',
                 'FIFTEEN_MINUTE', 'THIRTY_MINUTE', 'ONE_HOUR', 'ONE_DAY'
    fromdate/todate: 'YYYY-MM-DD HH:MM' strings (SmartAPI's required format)

    Rate limit note: capped at 3 req/sec — fine for on-demand/backfill use,
    not meant to be called every engine tick.
    """
    result = _session.call(
        "getCandleData",
        {
            "exchange": exchange,
            "symboltoken": symboltoken,
            "interval": interval,
            "fromdate": fromdate,
            "todate": todate,
        },
    )
    if not result.get("status"):
        logger.warning(f"[smartapi_client] getCandleData failed for {symboltoken}: {result}")
        return []

    # Angel returns each candle as [timestamp, open, high, low, close, volume]
    candles = []
    for c in result.get("data", []) or []:
        candles.append({
            "time":   c[0],
            "open":   safe_float(c[1]),
            "high":   safe_float(c[2]),
            "low":    safe_float(c[3]),
            "close":  safe_float(c[4]),
            "volume": safe_float(c[5]),
        })
    return candles


# ── 6. Live order placement (real money — see caller-side safeguards in
#      ws_server_live.py: LIVE_TRADING_ENABLED, kill-switch file, lot caps
#      before assuming this is safe to call directly) ────────────────────
def place_order(
    tradingsymbol,
    symboltoken,
    exchange,
    transaction_type,          # "BUY" | "SELL"
    quantity,
    order_type="MARKET",       # "MARKET" | "LIMIT"
    product_type="INTRADAY",   # "INTRADAY" | "DELIVERY" | "MARGIN" | "CARRYFORWARD"
    price=0.0,
    variety="NORMAL",
):
    """
    Places a REAL order on the logged-in AngelOne account via the same
    session smartapi_client.py already uses for quotes/ticks. Returns the
    order ID on success, raises RuntimeError on rejection.

    Uses _session.call() so it gets the same auto-relogin-on-token-error
    behavior as every other call in this file — but note placeOrder itself
    is NOT idempotent: a retry after a network timeout could double-order
    if the first attempt actually succeeded server-side before the
    response was lost. _session.call()'s retry-once-on-exception behavior
    (see SmartApiSession.call) applies here same as anywhere else — worth
    being aware of for this specific call, even though it's not special-
    cased differently.
    """
    order_params = {
        "variety": variety,
        "tradingsymbol": tradingsymbol,
        "symboltoken": symboltoken,
        "transactiontype": transaction_type,
        "exchange": exchange,
        "ordertype": order_type,
        "producttype": product_type,
        "duration": "DAY",
        "price": str(price) if order_type == "LIMIT" else "0",
        "squareoff": "0",
        "stoploss": "0",
        "quantity": str(quantity),
    }
    result = _session.call("placeOrderFullResponse", order_params)
    if not result.get("status"):
        raise RuntimeError(f"placeOrder failed: {result}")
    return result["data"]["orderid"]


def get_order_book():
    """Live order book from the AngelOne account — use to confirm whether
    a placed order actually filled, rather than assuming placeOrder's
    immediate return means it executed (it only means it was ACCEPTED)."""
    result = _session.call("orderBook")
    return result.get("data") or []


def get_positions():
    """Live open positions on the AngelOne account."""
    result = _session.call("position")
    return result.get("data") or []


def get_funds():
    """Live account funds/margin (AngelOne RMS limits) for the logged-in
    account — available cash, margin currently utilised, and realized/
    unrealized M2M. This was the missing piece behind the dashboard's
    "Fund" readout always showing the paper-trading estimate even in
    Live mode: nothing in this file called rmsLimit() at all, so there
    was nothing for ws_server_live.py to broadcast. Powers
    paper-trading.js's ptComputeFundSummary() once ws_server_live.py
    calls this and pushes it as a `{type:"funds", payload:{...}}` WS
    message (see the note in that file — not added here since this repo
    doesn't have ws_server_live.py's dispatch loop to hook into safely
    without seeing it).

    rmsLimit()'s response `data` fields come back as strings; safe_float()
    normalizes them the same way the rest of this file does for
    quote/chain data.
    """
    result = _session.call("rmsLimit")
    data = result.get("data") or {}
    return {
        "available_cash":     safe_float(data.get("availablecash")),
        "available_margin":   safe_float(data.get("net")),
        "available_intraday_payin": safe_float(data.get("availableintradaypayin")),
        "available_limit_margin":   safe_float(data.get("availablelimitmargin")),
        "collateral":         safe_float(data.get("collateral")),
        "utilised_margin":    safe_float(data.get("utiliseddebits")),
        "utilised_span":      safe_float(data.get("utilisedspan")),
        "utilised_exposure":  safe_float(data.get("utilisedexposure")),
        "m2m_unrealized":     safe_float(data.get("m2munrealized")),
        "m2m_realized":       safe_float(data.get("m2mrealized")),
    }


# ── 5. __main__ smoke-test ─────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("smartapi_client.py — smoke test")
    print("=" * 60)

    _session.ensure_session()
    print("Feed token:", _session.feed_token)

    funds = get_funds()
    print("\nAccount funds:", funds)

    nifty = get_index_quote("NIFTY")
    print("\nNIFTY quote:", nifty)

    sensex = get_index_quote("SENSEX")
    print("SENSEX quote:", sensex)

    expiries = list_expiries("NIFTY", exchange="NFO")
    print(f"\nNext 5 NIFTY expiries: {expiries[:5]}")

    nearest_expiry = expiries[0]
    print(f"\nFetching ATM chain for NIFTY {nearest_expiry} (±5 strikes)...")
    chain = get_atm_chain("NIFTY", nearest_expiry, strikes_around_atm=5, exchange="NFO")
    if chain:
        print(f"Spot: {chain['spot']}  ATM: {chain['atm_strike']}  Rows: {len(chain['rows'])}")
        for row in chain["rows"][:6]:
            print(row)
