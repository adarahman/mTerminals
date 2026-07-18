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
import re
import time
import json
import threading
from datetime import datetime, timedelta

import requests

# Force IPv4-only DNS resolution for this process's HTTP calls (requests/
# urllib3, which is what SmartApi's SDK uses under the hood for every
# call — see smartConnect.py's _postRequest/_request). Diagnosed
# 2026-07-17: this machine has a live IPv6 address distinct from the
# IPv4 address whitelisted on Angel One's SmartAPI developer console. If
# urllib3 prefers the IPv6 route for outbound calls, Angel's IP whitelist
# check (entered as IPv4) fails even though the IPv4 entry itself is
# correct and current — this looked identical to a rate-limit/access
# issue ("Access denied because of exceeding access rate") but had
# nothing to do with request volume. Forcing IPv4-only removes the
# ambiguity entirely rather than requiring the IPv6 address to be kept
# in sync with whatever the ISP reassigns it to.
try:
    import urllib3.util.connection as _urllib3_conn
    _urllib3_conn.HAS_IPV6 = False
except Exception:
    pass
import pyotp
from dotenv import load_dotenv
from logzero import logger
from SmartApi import SmartConnect

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover
    _orjson = None

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

# INDEX_TOKENS (index -> underlying token/exchange mapping SmartAPI expects
# for spot quotes) is built dynamically below by _build_index_tokens() from
# the live ScripMaster — see that function for the short-code alias table
# (e.g. SENSEX50) that keeps lookups working across ScripMaster refreshes.

def _build_index_tokens():
    """Dynamically build the index token map from the ScripMaster
    (instrumenttype == 'AMXIDX') instead of hardcoding entries — new or
    renamed indices (e.g. NIFTYNXT50, which was previously missing) are
    picked up automatically on the next ScripMaster refresh."""
    tokens = {}
    data = _load_scrip_master()
    for row in data:
        if row.get("instrumenttype") == "AMXIDX":
            name = row.get("name", "").upper()
            if name:
                tokens[name] = {
                    "token": row["token"],
                    "exchange": row.get("exch_seg", "NSE"),
                }

    # A few BSE indices carry a long descriptive `name` in the ScripMaster
    # (e.g. "S&P BSE SENSEX 50") instead of the short code the rest of this
    # app uses everywhere else (BSE_SCRIP_CD, LOT_SIZES, _BSE_SYMBOLS,
    # _FNO_INDEX_NAMES, etc.) — alias those here so INDEX_TOKENS.get(SYMBOL)
    # resolves regardless of which spelling the ScripMaster uses this run.
    _SHORT_CODE_ALIASES = {
        "SENSEX50": "S&P BSE SENSEX 50",
    }
    for short_code, long_name in _SHORT_CODE_ALIASES.items():
        if short_code not in tokens and long_name in tokens:
            tokens[short_code] = tokens[long_name]

    return tokens


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

        Rate-limit / access-denied responses are NOT treated as auth
        failures — re-login on those used to double the request volume
        and make Angel's rate limit worse. Those paths sleep + retry once.
        """
        _rate_limit_wait(fn_name)
        smart_api = self.ensure_session()
        method = getattr(smart_api, fn_name)
        try:
            result = method(*args, **kwargs)
        except Exception as e:
            if _is_rate_limited(e):
                return self._retry_after_rate_limit(fn_name, method, args, kwargs, e)
            if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
                # Transient network blip (timeout, connection reset) — the
                # existing session/token is fine, so retrying on the SAME
                # session is enough. Forcing a re-login here (as the generic
                # branch below does) burns a full TOTP+login round-trip for
                # something that has nothing to do with auth, and just
                # delays the actual retry.
                logger.warning(
                    f"[smartapi_client] {fn_name} network error ({e}); "
                    f"retrying once on same session (no re-login)"
                )
                time.sleep(1.0)
                _rate_limit_wait(fn_name)
                try:
                    return method(*args, **kwargs)
                except Exception as e2:
                    logger.warning(f"[smartapi_client] {fn_name} failed again after retry: {e2}")
                    return {"status": False, "message": str(e2)}
            logger.warning(f"[smartapi_client] {fn_name} raised {e}, retrying after re-login")
            with self._lock:
                self._smart_api = None
            smart_api = self.ensure_session()
            method = getattr(smart_api, fn_name)
            _rate_limit_wait(fn_name)
            result = method(*args, **kwargs)
            return result

        if isinstance(result, dict) and not result.get("status", True):
            msg = str(result.get("message", ""))
            errorcode = str(result.get("errorcode", ""))
            if _is_rate_limited(msg) or errorcode in {"AB8050", "AB8051"}:
                return self._retry_after_rate_limit(
                    fn_name, method, args, kwargs, f"code={errorcode or 'n/a'}"
                )
            if errorcode in {"AG8001", "AG8002", "AB1010", "AB1050"}:  # token/session errors
                logger.warning(f"[smartapi_client] {fn_name} token error {errorcode}, re-logging in")
                with self._lock:
                    self._smart_api = None
                smart_api = self.ensure_session()
                method = getattr(smart_api, fn_name)
                _rate_limit_wait(fn_name)
                result = method(*args, **kwargs)

        return result

    def _retry_after_rate_limit(self, fn_name, method, args, kwargs, reason):
        """Escalating backoff for rate-limited calls: 1.25s, 2.5s, 5s across
        up to _RATE_LIMIT_MAX_RETRIES attempts. Previously this was a single
        fixed-length retry, which was enough to survive an isolated blip but
        not a sustained rate-limit window — on 2026-07-17 the account stayed
        rate-limited through the first retry for every one of NIFTY,
        BANKNIFTY, MIDCPNIFTY, FINNIFTY, and the chain resolver's own spot
        quote call, all within the same second, and every one of those gave
        up after exactly one retry."""
        delay = _RATE_LIMIT_BACKOFF_S
        last_exc = None
        for attempt in range(1, _RATE_LIMIT_MAX_RETRIES + 1):
            logger.warning(
                f"[smartapi_client] {fn_name} rate-limited ({reason}); "
                f"backing off {delay}s (attempt {attempt}/{_RATE_LIMIT_MAX_RETRIES}, no re-login)"
            )
            time.sleep(delay)
            _rate_limit_wait(fn_name)
            try:
                result = method(*args, **kwargs)
            except Exception as e:
                last_exc = e
                if _is_rate_limited(e):
                    delay *= 2
                    continue
                raise
            if isinstance(result, dict) and not result.get("status", True):
                msg = str(result.get("message", ""))
                errorcode = str(result.get("errorcode", ""))
                if _is_rate_limited(msg) or errorcode in {"AB8050", "AB8051"}:
                    delay *= 2
                    continue
            return result
        logger.warning(
            f"[smartapi_client] {fn_name} still rate-limited after "
            f"{_RATE_LIMIT_MAX_RETRIES} retries; giving up for this call"
        )
        if last_exc is not None:
            return {"status": False, "message": str(last_exc)}
        return {"status": False, "message": f"rate-limited ({reason}) after retries"}


# Per-endpoint minimum spacing (seconds) between SmartAPI REST calls.
# getCandleData is the one that trips Angel's "exceeding access rate" most
# often (historical endpoint is tighter than live quotes). optionGreek is
# documented at 1 req/sec. Everything else shares a mild global ceiling so
# bursty multi-call paths (batch quotes + LTP + funds) don't stampede.
_RATE_LIMIT_MIN_INTERVAL = {
    "getCandleData": 0.40,   # ~2.5/s — under Angel's ~3/s historical cap
    "optionGreek": 1.05,     # Angel docs: 1 req/sec
    "placeOrderFullResponse": 0.35,
    "placeOrder": 0.35,
    # ltpData was previously falling through to the 25/s default, which is
    # far looser than Angel actually enforces for single-symbol LTP calls —
    # this is what produced the repeated "Access denied because of
    # exceeding access rate" bursts on 2026-07-17 when 4+ index symbols
    # were fetched back-to-back (NIFTY/BANKNIFTY/MIDCPNIFTY/FINNIFTY).
    # Prefer get_index_quotes_batch()/get_batch_quotes() (one getMarketData
    # call for many symbols) over calling ltpData per symbol wherever the
    # caller can gather symbols up front — this floor just protects the
    # cases where a single ltpData call is unavoidable.
    "ltpData": 1.0,           # ~1/s — matches Angel's documented LTP cap
    "getMarketData": 0.35,    # batch quote call — cheaper per-symbol but still capped
}
_RATE_LIMIT_DEFAULT_INTERVAL = 0.04  # ~25/s soft ceiling for RMS/orderBook/position etc
_RATE_LIMIT_BACKOFF_S = 1.25
_RATE_LIMIT_MAX_RETRIES = 3  # total attempts after the first backoff (was: 1, silently gave up after)
_rate_limit_lock = threading.Lock()
_rate_limit_last_ts: dict[str, float] = {}
_rate_limit_global_last = 0.0


def _is_rate_limited(err) -> bool:
    text = str(err).lower()
    return (
        "access rate" in text
        or "rate limit" in text
        or "too many requests" in text
        or "access denied because of exceeding" in text
    )


def _rate_limit_wait(fn_name: str) -> None:
    """Sleep just enough to respect per-endpoint + global spacing."""
    global _rate_limit_global_last
    min_gap = _RATE_LIMIT_MIN_INTERVAL.get(fn_name, _RATE_LIMIT_DEFAULT_INTERVAL)
    with _rate_limit_lock:
        now = time.monotonic()
        last_fn = _rate_limit_last_ts.get(fn_name, 0.0)
        wait_fn = min_gap - (now - last_fn)
        wait_global = _RATE_LIMIT_DEFAULT_INTERVAL - (now - _rate_limit_global_last)
        wait = max(0.0, wait_fn, wait_global)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _rate_limit_last_ts[fn_name] = now
        _rate_limit_global_last = now


# Short-TTL cache for spot/index quotes. get_atm_chain() calls get_spot_quote()
# internally, and separately ws_server_live.py's fetch_ticker_payload_smartapi
# fetches the same index quotes for the ticker strip — within one poll tick
# those are almost always the same value fetched twice. A ~1.5s TTL is short
# enough that nothing goes stale for a live feed, but long enough to collapse
# duplicate fetches happening within the same tick.
_QUOTE_CACHE_TTL_S = float(os.getenv("SMARTAPI_QUOTE_TTL_S", "1.5"))
_quote_cache_lock = threading.Lock()
_quote_cache: dict[str, tuple] = {}  # symbol -> (fetched_at_monotonic, quote_dict)


def _quote_cache_get(symbol):
    with _quote_cache_lock:
        entry = _quote_cache.get(symbol.upper())
        if entry and (time.monotonic() - entry[0]) < _QUOTE_CACHE_TTL_S:
            return entry[1]
        return None


def _quote_cache_set(symbol, quote):
    with _quote_cache_lock:
        _quote_cache[symbol.upper()] = (time.monotonic(), quote)


_session = SmartApiSession()


# ── 3. Instrument / token lookup ───────────────────────────────────────────
_scrip_master_cache = {"data": None, "fetched_at": None}
# Built once per ScripMaster load. Avoids O(n) scans over ~160k rows on
# every find_option_token / list_expiries / get_atm_chain call.
_scrip_indexes = {
    "option": {},          # (exch, name, expiry, strike_int, CE|PE) -> {token, tradingsymbol}
    "chain": {},           # (exch, name, expiry) -> {(strike_int, CE|PE): {token, tradingsymbol}}
    "expiries": {},        # (exch, name) -> sorted list of expiry strings (ddMMMyyyy)
    "equity": {},          # "RELIANCE" -> {token, tradingsymbol}  (NSE -EQ rows)
    "strikes": {},         # (exch, name, expiry) -> sorted unique strike ints
}


def _json_loads_bytes(raw: bytes):
    if _orjson is not None:
        return _orjson.loads(raw)
    return json.loads(raw)


def _json_dump_file(path: str, data) -> None:
    if _orjson is not None:
        with open(path, "wb") as f:
            f.write(_orjson.dumps(data))
        return
    with open(path, "w") as f:
        json.dump(data, f)


def _build_scrip_indexes(data) -> None:
    """One linear pass over the master → O(1) option/equity/expiry lookups."""
    option_idx = {}
    chain_idx = {}
    expiry_sets = {}
    equity_idx = {}
    strike_sets = {}

    for row in data:
        exch = row.get("exch_seg")
        name = row.get("name") or ""
        symbol = row.get("symbol") or ""
        expiry = row.get("expiry") or ""

        # Equity cash tokens (for F&O stock underlyings)
        if exch == "NSE" and symbol.endswith("-EQ") and name:
            equity_idx.setdefault(name.upper(), {
                "token": row["token"],
                "tradingsymbol": symbol,
            })

        if not name or not expiry:
            continue
        if exch not in ("NFO", "BFO"):
            continue

        name_u = name.upper()
        ek = (exch, name_u)
        expiry_sets.setdefault(ek, set()).add(expiry)

        if not (symbol.endswith("CE") or symbol.endswith("PE")):
            continue
        try:
            strike_int = int(round(float(row["strike"]) / 100))
        except (KeyError, ValueError, TypeError):
            continue
        opt_type = "CE" if symbol.endswith("CE") else "PE"
        info = {"token": row["token"], "tradingsymbol": symbol}
        option_idx[(exch, name_u, expiry, strike_int, opt_type)] = info
        chain_idx.setdefault((exch, name_u, expiry), {})[(strike_int, opt_type)] = info
        strike_sets.setdefault((exch, name_u, expiry), set()).add(strike_int)

    expiries = {}
    for key, s in expiry_sets.items():
        try:
            expiries[key] = sorted(s, key=lambda d: datetime.strptime(d, "%d%b%Y"))
        except ValueError:
            expiries[key] = sorted(s)

    strikes = {k: sorted(v) for k, v in strike_sets.items()}

    _scrip_indexes["option"] = option_idx
    _scrip_indexes["chain"] = chain_idx
    _scrip_indexes["expiries"] = expiries
    _scrip_indexes["equity"] = equity_idx
    _scrip_indexes["strikes"] = strikes
    logger.info(
        "[smartapi_client] ScripMaster indexed: %d option contracts, "
        "%d underlyings, %d equity tokens",
        len(option_idx),
        len(expiries),
        len(equity_idx),
    )


def _load_scrip_master():
    """Downloads (or reuses a local cache of) Angel One's ScripMaster —
    the master list mapping tradingsymbol -> token for every instrument.
    Builds lookup indexes on first load so hot-path resolvers don't scan
    ~160k rows per call."""
    now = datetime.now()
    if (
        _scrip_master_cache["data"] is not None
        and _scrip_master_cache["fetched_at"]
        and now - _scrip_master_cache["fetched_at"] < timedelta(hours=SCRIP_MASTER_TTL_HOURS)
    ):
        # Indexes should already exist from the load that filled the cache;
        # rebuild only if something cleared them without the row data.
        if not _scrip_indexes["option"]:
            _build_scrip_indexes(_scrip_master_cache["data"])
        return _scrip_master_cache["data"]

    if os.path.exists(SCRIP_MASTER_CACHE_PATH):
        mtime = datetime.fromtimestamp(os.path.getmtime(SCRIP_MASTER_CACHE_PATH))
        if now - mtime < timedelta(hours=SCRIP_MASTER_TTL_HOURS):
            with open(SCRIP_MASTER_CACHE_PATH, "rb") as f:
                data = _json_loads_bytes(f.read())
            _scrip_master_cache.update(data=data, fetched_at=now)
            _build_scrip_indexes(data)
            logger.info(f"[smartapi_client] ScripMaster loaded from local cache ({len(data)} rows)")
            return data

    logger.info("[smartapi_client] Downloading fresh ScripMaster (~few MB, one-time)...")
    try:
        resp = requests.get(SCRIP_MASTER_URL, timeout=(5, 20))  # (connect, read) timeouts
        resp.raise_for_status()
        data = resp.json()
        _json_dump_file(SCRIP_MASTER_CACHE_PATH, data)
        _scrip_master_cache.update(data=data, fetched_at=now)
        _build_scrip_indexes(data)
        logger.info(f"[smartapi_client] ScripMaster downloaded and cached ({len(data)} rows)")
        return data
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.warning(f"[smartapi_client] ScripMaster download failed ({e}); "
                        f"falling back to stale local cache if available")
        if os.path.exists(SCRIP_MASTER_CACHE_PATH):
            with open(SCRIP_MASTER_CACHE_PATH, "rb") as f:
                data = _json_loads_bytes(f.read())
            _scrip_master_cache.update(data=data, fetched_at=now)
            _build_scrip_indexes(data)
            mtime = datetime.fromtimestamp(os.path.getmtime(SCRIP_MASTER_CACHE_PATH))
            age_hours = (now - mtime).total_seconds() / 3600
            logger.warning(f"[smartapi_client] Using stale ScripMaster cache "
                            f"({len(data)} rows, {age_hours:.1f}h old)")
            return data
        raise

    _scrip_master_cache.update(data=data, fetched_at=now)
    _build_scrip_indexes(data)
    logger.info(f"[smartapi_client] ScripMaster downloaded and cached ({len(data)} rows)")
    return data


INDEX_TOKENS = _build_index_tokens()


def find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
    """
    Resolve a NIFTY/BANKNIFTY/SENSEX option contract to its SmartAPI token.

    underlying:   'NIFTY' | 'BANKNIFTY' | 'SENSEX' | ...
    expiry_ddmmmyyyy: e.g. '31JUL2026'  (matches ScripMaster's expiry format)
    strike:       e.g. 24200 (numeric, will be matched against strike*100 field)
    opt_type:     'CE' | 'PE'
    exchange:     'NFO' for NSE options, 'BFO' for BSE (SENSEX) options
    """
    _load_scrip_master()  # ensure indexes are warm
    try:
        strike_int = int(round(float(strike)))
    except (TypeError, ValueError):
        strike_int = int(round(strike)) if strike is not None else 0
    key = (
        exchange,
        (underlying or "").upper(),
        expiry_ddmmmyyyy,
        strike_int,
        (opt_type or "").upper(),
    )
    hit = _scrip_indexes["option"].get(key)
    if hit:
        return hit

    logger.warning(
        f"[smartapi_client] No token found for {underlying} {expiry_ddmmmyyyy} "
        f"{strike}{opt_type} on {exchange}"
    )
    return None


def list_expiries(underlying, exchange="NFO"):
    """Return sorted list of available expiry strings for an underlying."""
    _load_scrip_master()
    return list(_scrip_indexes["expiries"].get((exchange, (underlying or "").upper()), []))


# Known index underlyings among F&O contracts — used to split the full
# get_fno_underlyings() list into "indices" vs "stocks" groups for the
# frontend symbol picker (see chain-views.js renderSymbolOptions()).
_FNO_INDEX_NAMES = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "SENSEX", "BANKEX", "SENSEX50",
}

# Angel One's master carries a handful of dummy/test rows (e.g.
# "011NSETEST") mixed in with real F&O underlyings — filtered out here so
# they never show up in a user-facing dropdown.
_TEST_SYMBOL_RE = re.compile(r"NSETEST")

_fno_universe_cache = {"data": None, "built_at": None}
_FNO_UNIVERSE_TTL_HOURS = SCRIP_MASTER_TTL_HOURS  # rebuild alongside the master


def get_fno_underlyings(force_refresh=False):
    """
    Every underlying (NSE/BSE index or individual stock) that currently
    has live F&O contracts, derived straight from the ScripMaster
    (FUTSTK/FUTIDX rows on the NFO/BFO segments) instead of a hardcoded
    list — so new stocks added to the F&O segment (and old ones dropped
    from it, e.g. after an NSE quarterly review) show up automatically
    the next time the master refreshes, with zero code changes here.

    Returns {"indices": [...], "stocks": [...]}, both alphabetically
    sorted. The result is cached in-memory and rebuilt at the same
    cadence as the ScripMaster itself (SCRIP_MASTER_TTL_HOURS), since
    scanning ~160k rows on every dashboard payload build would be wasteful.
    """
    now = datetime.now()
    if (
        not force_refresh
        and _fno_universe_cache["data"] is not None
        and _fno_universe_cache["built_at"]
        and now - _fno_universe_cache["built_at"] < timedelta(hours=_FNO_UNIVERSE_TTL_HOURS)
    ):
        return _fno_universe_cache["data"]

    data = _load_scrip_master()
    names = set()
    for row in data:
        if row.get("instrumenttype") not in ("FUTSTK", "FUTIDX"):
            continue
        if row.get("exch_seg") not in ("NFO", "BFO"):
            continue
        name = (row.get("name") or "").strip().upper()
        if not name or _TEST_SYMBOL_RE.search(name):
            continue
        names.add(name)

    indices = sorted(n for n in names if n in _FNO_INDEX_NAMES)
    stocks = sorted(n for n in names if n not in _FNO_INDEX_NAMES)
    result = {"indices": indices, "stocks": stocks}

    _fno_universe_cache.update(data=result, built_at=now)
    logger.info(
        "[smartapi_client] Built F&O universe: %d indices, %d stocks",
        len(indices), len(stocks),
    )
    return result


# ── 4. Market data fetchers ────────────────────────────────────────────────
def get_index_quote(symbol):
    """LTP + basic OHLC for an index (NIFTY, BANKNIFTY, SENSEX, ...).

    Served from a short TTL cache (SMARTAPI_QUOTE_TTL_S, default 1.5s) when
    a fresh-enough value is already on hand — see _quote_cache_get/set.
    For fetching several index symbols together (e.g. the ticker strip),
    prefer get_index_quotes_batch() instead of calling this in a loop: that
    uses one getMarketData call instead of one ltpData call per symbol.
    """
    symbol = symbol.upper()
    cached = _quote_cache_get(symbol)
    if cached is not None:
        return cached

    info = INDEX_TOKENS.get(symbol)
    if not info:
        logger.warning(f"[smartapi_client] Unknown index symbol: {symbol}")
        return None

    result = _session.call(
        "ltpData", info["exchange"], symbol, info["token"]
    )
    if not result.get("status"):
        logger.warning(f"[smartapi_client] get_index_quote failed for {symbol}: {result}")
        return None

    d = result["data"]
    quote = {
        "symbol": symbol,
        "ltp": safe_float(d.get("ltp")),
        "open": safe_float(d.get("open")),
        "high": safe_float(d.get("high")),
        "low": safe_float(d.get("low")),
        "close": safe_float(d.get("close")),
    }
    _quote_cache_set(symbol, quote)
    return quote


def get_index_quotes_batch(symbols):
    """Fetch several index quotes (NIFTY, BANKNIFTY, MIDCPNIFTY, FINNIFTY, ...)
    in as few getMarketData calls as possible instead of one ltpData call per
    symbol. This is the fix for the 2026-07-17 rate-limit cascade, where
    fetch_ticker_payload_smartapi fetched 4 index symbols back-to-back via
    ltpData and tripped Angel's per-second cap on every one of them.

    symbols: iterable of index names, e.g. ['NIFTY', 'BANKNIFTY', 'MIDCPNIFTY', 'FINNIFTY']
    Returns: {symbol: quote_dict_or_None, ...} — same shape as get_index_quote(),
    one entry per input symbol (None if that symbol was unknown or Angel didn't
    return a row for it).
    """
    symbols = [s.upper() for s in symbols]
    out = {}
    still_needed = []
    for s in symbols:
        cached = _quote_cache_get(s)
        if cached is not None:
            out[s] = cached
        else:
            still_needed.append(s)

    if not still_needed:
        return out

    # Group by exchange (SENSEX etc. live on BSE, most indices on NSE) since
    # getMarketData/get_batch_quotes takes one exchange per call.
    by_exchange: dict[str, list] = {}
    unknown = []
    for s in still_needed:
        info = INDEX_TOKENS.get(s)
        if not info:
            unknown.append(s)
            continue
        by_exchange.setdefault(info["exchange"], []).append((s, info["token"]))

    for s in unknown:
        logger.warning(f"[smartapi_client] Unknown index symbol: {s}")
        out[s] = None

    for exchange, pairs in by_exchange.items():
        quotes_by_token = get_batch_quotes(
            exchange, [(sym, token) for sym, token in pairs], mode="OHLC"
        )
        # get_batch_quotes keys by tradingSymbol as Angel returns it, which
        # for indices is the plain symbol name we already passed in.
        for sym, token in pairs:
            d = quotes_by_token.get(sym)
            if not d:
                logger.warning(
                    f"[smartapi_client] get_index_quotes_batch: no row returned for {sym}"
                )
                out[sym] = None
                continue
            quote = {
                "symbol": sym,
                "ltp": safe_float(d.get("ltp")),
                "open": safe_float(d.get("open")),
                "high": safe_float(d.get("high")),
                "low": safe_float(d.get("low")),
                "close": safe_float(d.get("close")),
            }
            _quote_cache_set(sym, quote)
            out[sym] = quote

    return out


_equity_token_cache = {}  # symbol -> {"token": ..., "tradingsymbol": ...}, built lazily from ScripMaster


def _find_equity_token(symbol):
    """Resolve a stock's NSE cash-segment token from the ScripMaster
    (e.g. 'SUNPHARMA' -> its '-EQ' row), for underlyings that aren't in
    the hardcoded INDEX_TOKENS map. Indexed at ScripMaster load time."""
    symbol = symbol.upper()
    if symbol in _equity_token_cache:
        return _equity_token_cache[symbol]

    _load_scrip_master()
    info = _scrip_indexes["equity"].get(symbol)
    if info:
        _equity_token_cache[symbol] = info
        return info

    logger.warning(f"[smartapi_client] No NSE equity token found for {symbol}")
    return None


def get_equity_quote(symbol):
    """LTP + basic OHLC for an individual F&O stock (e.g. SUNPHARMA, RELIANCE),
    resolved dynamically via the ScripMaster rather than a hardcoded table.
    Counterpart to get_index_quote() for the ~200+ stock underlyings that
    aren't in INDEX_TOKENS."""
    symbol = symbol.upper()
    cached = _quote_cache_get(symbol)
    if cached is not None:
        return cached

    info = _find_equity_token(symbol)
    if not info:
        return None

    result = _session.call(
        "ltpData", "NSE", info["tradingsymbol"], info["token"]
    )
    if not result.get("status"):
        logger.warning(f"[smartapi_client] get_equity_quote failed for {symbol}: {result}")
        return None

    d = result["data"]
    quote = {
        "symbol": symbol,
        "ltp": safe_float(d.get("ltp")),
        "open": safe_float(d.get("open")),
        "high": safe_float(d.get("high")),
        "low": safe_float(d.get("low")),
        "close": safe_float(d.get("close")),
    }
    _quote_cache_set(symbol, quote)
    return quote


def get_spot_quote(underlying):
    """Dispatcher: routes to get_index_quote() for the 6 hardcoded indices,
    get_equity_quote() for everything else (individual F&O stocks). Use this
    instead of calling get_index_quote() directly whenever `underlying` might
    be a stock, e.g. in get_atm_chain()."""
    if underlying.upper() in INDEX_TOKENS:
        return get_index_quote(underlying)
    return get_equity_quote(underlying)


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

    Uses get_batch_quotes (≤50 tokens per REST call) instead of one LTP
    request per contract — the old per-strike loop was the main way to
    trip Angel's access-rate limits when building wide chains.
    """
    if not strikes:
        return []

    _load_scrip_master()
    name_u = (underlying or "").upper()
    chain_map = _scrip_indexes["chain"].get((exchange, name_u, expiry_ddmmmyyyy), {})
    pairs = []
    meta = []  # parallel to pairs: (strike, opt_type, token, tradingsymbol)
    for strike in strikes:
        try:
            strike_int = int(round(float(strike)))
        except (TypeError, ValueError):
            continue
        for opt_type in ("CE", "PE"):
            info = chain_map.get((strike_int, opt_type))
            if not info:
                continue
            pairs.append((info["tradingsymbol"], info["token"]))
            meta.append((strike_int, opt_type, info["token"], info["tradingsymbol"]))

    if not pairs:
        return []

    quotes = get_batch_quotes(exchange, pairs, mode="FULL")
    rows = []
    for strike_int, opt_type, token, tradingsymbol in meta:
        q = quotes.get(tradingsymbol) or quotes.get(token)
        if not q:
            continue
        rows.append({
            "tradingsymbol": tradingsymbol,
            "token": token,
            "strike": strike_int,
            "type": opt_type,
            "ltp": safe_float(q.get("ltp")),
            "open": safe_float(q.get("open")),
            "high": safe_float(q.get("high")),
            "low": safe_float(q.get("low")),
            "close": safe_float(q.get("close")),
        })
    return rows


STRIKE_INTERVALS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
    "SENSEX": 100,
    "BANKEX": 100,
    "SENSEX50": 50,

}


_stock_strike_interval_cache = {}  # underlying -> interval, derived from ScripMaster


def _get_strike_interval(underlying):
    """Strike spacing for `underlying`. Indices use the hardcoded
    STRIKE_INTERVALS table; stocks vary too much for a fixed default
    (e.g. a 50-point guess is wrong for most F&O names), so for anything
    else this derives the real interval from consecutive strikes on the
    nearest available expiry in the ScripMaster."""
    underlying = underlying.upper()
    if underlying in STRIKE_INTERVALS:
        return STRIKE_INTERVALS[underlying]
    if underlying in _stock_strike_interval_cache:
        return _stock_strike_interval_cache[underlying]

    _load_scrip_master()
    expiries = list_expiries(underlying, exchange="NFO")
    interval = 50  # last-resort fallback if nothing resolves below
    if expiries:
        nearest = expiries[0]
        strikes = _scrip_indexes["strikes"].get(("NFO", underlying, nearest), [])
        if len(strikes) >= 2:
            gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
            if gaps:
                interval = min(gaps)

    _stock_strike_interval_cache[underlying] = interval
    return interval


def _round_to_strike(price, underlying):
    interval = _get_strike_interval(underlying)
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
    quote = get_spot_quote(underlying)
    if not quote:
        logger.warning(f"[smartapi_client] Could not fetch spot for {underlying}")
        return None

    spot = quote["ltp"]
    atm = _round_to_strike(spot, underlying)
    interval = _get_strike_interval(underlying)
    strikes = [atm + (i * interval) for i in range(-strikes_around_atm, strikes_around_atm + 1)]
    strike_set = set(strikes)

    _load_scrip_master()
    chain_map = _scrip_indexes["chain"].get(
        (exchange, underlying.upper(), expiry_ddmmmyyyy), {}
    )
    strike_lookup = {
        key: info
        for key, info in chain_map.items()
        if key[0] in strike_set
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