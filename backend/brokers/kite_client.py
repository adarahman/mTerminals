"""
kite_client.py
===============
Kite Connect (Zerodha) data-fetch + execution layer, kept standalone and
config.py-independent — same convention upstox_client.py's own docstring
establishes (see config.py's comment on upstox_access_token): this module
should stay smoke-testable on its own via KITE_* environment variables,
with kite_execution_adapter.py as the seam that wires it into config.py
and ws_server_live.py's shared broker-neutral signatures.

Token lifecycle note (the one real divergence from SmartAPI/Shoonya):
Kite's access_token expires daily and there is NO headless re-login —
Kite Connect's auth is a browser-redirect + request_token exchange, not
a TOTP-based programmatic login. This module therefore has no
_login()/ensure_session() that can silently re-authenticate the way
SmartApiSession/ShoonyaSession do. Whatever process refreshes the token
each morning (manual paste, a small script driving the login redirect)
is expected to call set_session_token() — this module only CONSUMES a
token, same division of responsibility as upstox_client.py already has.

Sections
--------
  1. Imports & shared constants
  2. Session (token holder only — no login flow)
  3. Instrument lookup — symbol/expiry/strike -> instrument_token
  4. Market data — quotes, ATM chain, spot
  5. Orders — place/order book/positions/funds
"""

# ── 1. Imports & shared constants ──────────────────────────────────────
import os
import threading
import time
from datetime import datetime
from typing import Optional

from kiteconnect import KiteConnect

# Rate limiting configuration for Kite Connect API calls
# Kite Connect has documented rate limits of ~3-10 requests per second depending on endpoint
_KITE_RATE_LIMIT_MIN_INTERVAL = {
    "quote": 0.15,           # ~6-7 calls per second for quotes
    "orders": 0.25,          # ~4 calls per second for orders
    "positions": 0.30,       # ~3 calls per second for positions
    "holdings": 0.50,        # ~2 calls per second for holdings
    "margins": 0.50,         # ~2 calls per second for margins
    "instruments": 2.0,      # ~0.5 calls per second for instruments (expensive call)
}
_KITE_RATE_LIMIT_DEFAULT_INTERVAL = 0.12  # ~8 calls per second default
_KITE_RATE_LIMIT_BACKOFF_S = 1.5
_KITE_RATE_LIMIT_MAX_RETRIES = 3
_kite_rate_limit_lock = threading.Lock()
_kite_rate_limit_last_ts: dict[str, float] = {}
_kite_rate_limit_global_last = 0.0


def _is_rate_limited(err) -> bool:
    """Check if an error indicates rate limiting."""
    text = str(err).lower()
    return (
        "rate limit" in text
        or "too many requests" in text
        or "access denied" in text
        or "exceed" in text
        or "429" in text  # HTTP 429 Too Many Requests
    )


def _kite_rate_limit_wait(fn_name: str) -> None:
    """Sleep just enough to respect per-endpoint + global spacing for Kite."""
    global _kite_rate_limit_global_last
    min_gap = _KITE_RATE_LIMIT_MIN_INTERVAL.get(fn_name, _KITE_RATE_LIMIT_DEFAULT_INTERVAL)
    with _kite_rate_limit_lock:
        now = time.monotonic()
        last_fn = _kite_rate_limit_last_ts.get(fn_name, 0.0)
        wait_fn = min_gap - (now - last_fn)
        wait_global = _KITE_RATE_LIMIT_DEFAULT_INTERVAL - (now - _kite_rate_limit_global_last)
        wait = max(0.0, wait_fn, wait_global)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _kite_rate_limit_last_ts[fn_name] = now
        _kite_rate_limit_global_last = now


def _kite_call_with_retry(fn_name, api_method, *args, **kwargs):
    """Execute a Kite API call with rate limiting and retry logic."""
    _kite_rate_limit_wait(fn_name)
    delay = _KITE_RATE_LIMIT_BACKOFF_S
    last_exc = None
    
    for attempt in range(1, _KITE_RATE_LIMIT_MAX_RETRIES + 1):
        try:
            result = api_method(*args, **kwargs)
            return result
        except Exception as e:
            last_exc = e
            if _is_rate_limited(e):
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    f"[kite_client] {fn_name} rate-limited ({e}); "
                    f"backing off {delay}s (attempt {attempt}/{_KITE_RATE_LIMIT_MAX_RETRIES})"
                )
                if attempt < _KITE_RATE_LIMIT_MAX_RETRIES:
                    time.sleep(delay)
                    delay *= 2
                    _kite_rate_limit_wait(fn_name)
                    continue
            # Non-rate-limit errors, raise immediately
            raise KiteError(f"Kite {fn_name} failed: {e}") from e
    
    # Should not reach here, but just in case
    if last_exc:
        raise KiteError(f"Kite {fn_name} failed after retries: {last_exc}")
    raise KiteError(f"Kite {fn_name} failed with unknown error")

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover
    _orjson = None

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")  # pasted fresh each trading day

INSTRUMENT_CACHE_DIR = os.getenv("KITE_INSTRUMENT_CACHE_DIR", ".kite_cache")


class KiteError(RuntimeError):
    pass


# ── 2. Session ───────────────────────────────────────────────────────────
class KiteSession:
    """Holds a KiteConnect instance + access_token. Unlike
    SmartApiSession/ShoonyaSession, this has no login() to call
    automatically — see module docstring. ensure_session() only checks
    that a token has been supplied, it never mints one."""

    def __init__(self):
        self._lock = threading.Lock()
        self._kite: Optional[KiteConnect] = None
        self._token_set_at: Optional[datetime] = None

    def ensure_session(self) -> KiteConnect:
        with self._lock:
            if self._kite is not None:
                return self._kite
            if not API_KEY:
                raise KiteError("Missing KITE_API_KEY")
            if not ACCESS_TOKEN:
                raise KiteError(
                    "Missing KITE_ACCESS_TOKEN — Kite has no headless daily "
                    "login (unlike SmartAPI/Shoonya). Generate today's "
                    "request_token via the login redirect, exchange it for "
                    "an access_token, then set KITE_ACCESS_TOKEN or call "
                    "kite_client.set_session_token()."
                )
            kite = KiteConnect(api_key=API_KEY)
            kite.set_access_token(ACCESS_TOKEN)
            self._kite = kite
            self._token_set_at = datetime.now()
            return kite

    def set_session_token(self, access_token: str) -> None:
        """Push a freshly-minted token (e.g. after the daily login
        redirect completes) without restarting the process. Matches the
        set_session_token() convention already used for Upstox."""
        with self._lock:
            kite = self._kite or KiteConnect(api_key=API_KEY)
            kite.set_access_token(access_token)
            self._kite = kite
            self._token_set_at = datetime.now()

    def login_url(self) -> str:
        if not API_KEY:
            raise KiteError("Missing KITE_API_KEY")
        return KiteConnect(api_key=API_KEY).login_url()

    def generate_session(self, request_token: str, api_secret: Optional[str] = None) -> str:
        """Exchanges request_token for an access_token and applies it.
        Returns the access_token so a caller can persist it (env var,
        secrets store, etc) for the rest of the trading day."""
        secret = api_secret or API_SECRET
        if not secret:
            raise KiteError("Missing KITE_API_SECRET")
        kite = KiteConnect(api_key=API_KEY)
        data = kite.generate_session(request_token, api_secret=secret)
        access_token = data["access_token"]
        kite.set_access_token(access_token)
        with self._lock:
            self._kite = kite
            self._token_set_at = datetime.now()
        return access_token


_session = KiteSession()
set_session_token = _session.set_session_token
login_url = _session.login_url
generate_session = _session.generate_session


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ── 3. Instrument lookup ────────────────────────────────────────────────
# Kite has no exchange-provided numeric "token" concept exposed the way
# SmartAPI's ScripMaster is — kiteconnect calls it instrument_token, and
# it IS numeric (unlike Upstox's string instrument_key), but the dump
# format/fields differ from SmartAPI's ScripMaster. Cache it the same
# way (once/day) rather than hitting kite.instruments() per lookup —
# it's a large CSV-shaped response.
_instrument_cache = {"date": None, "by_symbol": {}, "rows": []}


def _load_instruments(exchange: str = "NFO", force_refresh: bool = False) -> list:
    today = datetime.now().date()
    if not force_refresh and _instrument_cache["date"] == today and _instrument_cache["rows"]:
        return _instrument_cache["rows"]

    kite = _session.ensure_session()
    rows = _kite_call_with_retry("instruments", kite.instruments, exchange)
    _instrument_cache["date"] = today
    _instrument_cache["rows"] = rows
    _instrument_cache["by_symbol"] = {r["tradingsymbol"]: r for r in rows}
    return rows


def list_expiries(underlying: str, exchange: str = "NFO") -> list:
    """Sorted expiry strings in SmartAPI's DDMMMYYYY convention (e.g.
    '31JUL2026'), matching the MarketData Protocol's documented contract
    — same conversion boundary UpstoxMarketData draws for its own ISO
    dates, done here so this module's OWN callers get a consistent shape
    without needing a market_data.py adapter in between."""
    rows = _load_instruments(exchange)
    expiries = sorted({
        r["expiry"] for r in rows
        if r.get("name", "").upper() == underlying.upper() and r.get("expiry")
    })
    return [e.strftime("%d%b%Y").upper() if hasattr(e, "strftime") else e for e in expiries]


def find_option_token(underlying: str, expiry_ddmmmyyyy: str, strike, opt_type: str,
                       exchange: str = "NFO") -> Optional[dict]:
    """{'tradingsymbol', 'token'} for one contract, matching
    smartapi_client.py's find_option_token() return shape — 'token'
    here is Kite's numeric instrument_token (int), NOT a string like
    Upstox's instrument_key."""
    rows = _load_instruments(exchange)
    expiry_dt = datetime.strptime(expiry_ddmmmyyyy, "%d%b%Y").date()
    strike_f = float(strike)
    for r in rows:
        if (r.get("name", "").upper() == underlying.upper()
                and r.get("expiry") == expiry_dt
                and r.get("instrument_type") == opt_type.upper()
                and float(r.get("strike") or 0) == strike_f):
            return {"tradingsymbol": r["tradingsymbol"], "token": r["instrument_token"]}
    return None


def get_atm_chain(underlying: str, expiry_ddmmmyyyy: str, strikes_around_atm: int = 10,
                   exchange: str = "NFO") -> Optional[dict]:
    """{'underlying', 'spot', 'atm_strike', 'expiry', 'rows': [...]},
    matching smartapi_client.py's get_atm_chain() shape exactly so
    KiteMarketData (market_data.py) is a thin pass-through, same as
    SmartApiMarketData is over this module's SmartAPI equivalent."""
    spot = get_spot_quote(underlying)
    if not spot or not spot.get("ltp"):
        return None
    spot_ltp = spot["ltp"]

    rows = _load_instruments(exchange)
    expiry_dt = datetime.strptime(expiry_ddmmmyyyy, "%d%b%Y").date()
    chain_rows = [
        r for r in rows
        if r.get("name", "").upper() == underlying.upper() and r.get("expiry") == expiry_dt
    ]
    if not chain_rows:
        return None

    strikes = sorted({float(r["strike"]) for r in chain_rows})
    atm_strike = min(strikes, key=lambda s: abs(s - spot_ltp))
    atm_idx = strikes.index(atm_strike)
    window = set(strikes[max(0, atm_idx - strikes_around_atm): atm_idx + strikes_around_atm + 1])

    out_rows = [
        {
            "strike": float(r["strike"]),
            "type": r["instrument_type"],
            "token": r["instrument_token"],
            "tradingsymbol": r["tradingsymbol"],
        }
        for r in chain_rows
        if float(r["strike"]) in window
    ]
    return {
        "underlying": underlying.upper(),
        "spot": spot_ltp,
        "atm_strike": atm_strike,
        "expiry": expiry_ddmmmyyyy,
        "rows": out_rows,
    }


# ── 4. Market data ───────────────────────────────────────────────────────
def get_quotes(exchange_tradingsymbol_pairs: list) -> dict:
    """exchange_tradingsymbol_pairs: list of 'EXCHANGE:TRADINGSYMBOL'
    strings (Kite's own quote() key format) — up to 500 per call vs
    SmartAPI's 50, no batching loop needed at typical chain sizes."""
    if not exchange_tradingsymbol_pairs:
        return {}
    kite = _session.ensure_session()
    return _kite_call_with_retry("quote", kite.quote, exchange_tradingsymbol_pairs)


def get_spot_quote(underlying: str) -> Optional[dict]:
    """LTP + OHLC for one underlying index, matching
    smartapi_client.py's get_spot_quote() shape ({'ltp', 'close', ...})."""
    index_key = f"NSE:{underlying.upper()}"
    kite = _session.ensure_session()
    try:
        quotes = _kite_call_with_retry("quote", kite.quote, [index_key])
        data = quotes.get(index_key)
    except Exception as exc:
        raise KiteError(f"Kite get_spot_quote failed: {exc}") from exc
    if not data:
        return None
    ohlc = data.get("ohlc") or {}
    return {
        "ltp": data.get("last_price"),
        "close": ohlc.get("close"),
        "open": ohlc.get("open"),
        "high": ohlc.get("high"),
        "low": ohlc.get("low"),
    }


def get_fno_underlyings(force_refresh: bool = False) -> dict:
    rows = _load_instruments("NFO", force_refresh=force_refresh)
    names = {
        r.get("name", "").strip().upper()
        for r in rows
        if r.get("instrument_type") in ("CE", "PE") and r.get("name")
    }
    # Same index-name partition convention as UpstoxMarketData —
    # duplicate the actual INDEX_KEYS set at the call site (market_data.py)
    # rather than hardcoding a second copy of it here.
    return {"names": sorted(names)}


# ── 5. Orders ─────────────────────────────────────────────────────────
def place_order(tradingsymbol, symboltoken, exchange, transaction_type,
                 quantity, order_type="MARKET", product_type="INTRADAY",
                 price=0.0, variety="NORMAL", order_tag=None):
    """Broker-neutral shared signature (matches smartapi_client.py /
    shoonya_client.py / upstox_execution_adapter.py). Kite identifies
    the contract via exchange + tradingsymbol (unlike Upstox's
    instrument_key-only model) — symboltoken is accepted for call-site
    symmetry but unused, same reasoning shoonya_client.py gives."""
    del symboltoken, variety
    kite = _session.ensure_session()

    product_map = {"INTRADAY": kite.PRODUCT_MIS, "DELIVERY": kite.PRODUCT_CNC,
                   "MARGIN": kite.PRODUCT_CNC, "CARRYFORWARD": kite.PRODUCT_NRML}
    order_type_map = {"MARKET": kite.ORDER_TYPE_MARKET, "LIMIT": kite.ORDER_TYPE_LIMIT}

    try:
        order_id = _kite_call_with_retry("place_order", kite.place_order,
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=(kite.TRANSACTION_TYPE_BUY if transaction_type.upper() == "BUY"
                               else kite.TRANSACTION_TYPE_SELL),
            quantity=int(quantity),
            variety=kite.VARIETY_REGULAR,
            order_type=order_type_map.get(order_type.upper(), kite.ORDER_TYPE_MARKET),
            product=product_map.get((product_type or "").upper(), kite.PRODUCT_MIS),
            price=float(price) if order_type.upper() == "LIMIT" else None,
            tag=order_tag,
        )
    except Exception as exc:
        raise KiteError(f"Kite place_order failed: {exc}") from exc
    return str(order_id)


def get_order_book():
    kite = _session.ensure_session()
    try:
        return _kite_call_with_retry("orders", kite.orders)
    except Exception as exc:
        raise KiteError(f"Kite get_order_book failed: {exc}") from exc


def get_positions():
    kite = _session.ensure_session()
    try:
        data = _kite_call_with_retry("positions", kite.positions)
    except Exception as exc:
        raise KiteError(f"Kite get_positions failed: {exc}") from exc
    return data.get("net", [])


def get_funds():
    kite = _session.ensure_session()
    try:
        margins = _kite_call_with_retry("margins", kite.margins)
    except Exception as exc:
        raise KiteError(f"Kite get_funds failed: {exc}") from exc
    equity = margins.get("equity", {}) if isinstance(margins, dict) else {}
    available = equity.get("available", {})
    utilised = equity.get("utilised", {})
    return {
        "available_cash": safe_float(available.get("live_balance")),
        "available_margin": safe_float(available.get("cash")),
        "available_intraday_payin": safe_float(available.get("intraday_payin")),
        "available_limit_margin": safe_float(available.get("collateral")),
        "collateral": safe_float(available.get("collateral")),
        "utilised_margin": safe_float(utilised.get("debits")),
        "utilised_span": safe_float(utilised.get("span")),
        "utilised_exposure": safe_float(utilised.get("exposure")),
        "m2m_unrealized": safe_float(utilised.get("m2m_unrealised")),
        "m2m_realized": safe_float(utilised.get("m2m_realised")),
    }


# ── __main__ smoke-test ─────────────────────────────────────────────────
if __name__ == "__main__":
    print("Login URL:", login_url())
    print("Paste the request_token from the redirect, then run:")
    print("  kite_client.generate_session('<request_token>')")
