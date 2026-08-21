"""
upstox_client.py
=================
Standalone Upstox API v2/v3 adapter — market data AND execution, in one
broker boundary (unlike this codebase's SmartAPI/Shoonya split, where
market data and execution live in separate files/vendors).

STATUS: standalone / not wired in. This module does NOT import from
config.py, pipeline_config.py, or paths.py, and nothing else in the
codebase imports this yet. It reads its own env vars directly so it can
be dropped in and smoke-tested on its own. Wiring it up later means:
  - add upstox_* fields to config.py's Settings (mirroring the
    smartapi_/shoonya_ fields already there)
  - either point market_data.py's `market_data` singleton at a new
    UpstoxMarketData(MarketData) adapter class, or add an
    OrderExecution-shaped adapter next to shoonya_client.py for the
    execution half — same pattern as this codebase already uses.

AUTH MODEL (important, and different from both SmartAPI and Shoonya):
Upstox access tokens are NOT minted headlessly from a password + TOTP.
The flow is OAuth2 authorization-code:
  1. Send the user to build_login_url() in a browser/webview.
  2. They log in on upstox.com; Upstox redirects to your redirect_uri
     with a single-use `code` query param.
  3. exchange_code_for_token(code) swaps that for an access_token.
Every access_token expires at 3:30 AM IST the *following* day regardless
of when it was issued — there is no refresh-token grant on the standard
flow. In practice this means step 1-2 has to happen (semi-)manually once
a day, e.g. by pasting the token Upstox's "Generate" button on the app's
dashboard page produces. UpstoxSession below expects an already-minted
token (via UPSTOX_ACCESS_TOKEN or passed in); it does not attempt to
automate the daily login the way smartapi_client.py's TOTP-based login
does, because Upstox's flow has no equivalent unattended path.

ENDPOINTS (verified against Upstox's own docs, not memory — SDKs and
docs disagree on a few paths, e.g. cancel exists at both /v2 and /v3;
this module intentionally sticks to the ones with a directly-confirmed
example request, called out inline where it matters):
  Auth      GET  /v2/login/authorization/dialog
            POST /v2/login/authorization/token
  Market    GET  /v3/market-quote/ltp
            GET  /v2/market-quote/quotes                (full OHLC quote)
            GET  /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
            GET  /v3/historical-candle/intraday/{key}/{unit}/{interval}
  Orders    POST   https://api-hft.upstox.com/v3/order/place  (low-latency
                   host — separate from api.upstox.com; matches this
                   codebase's shoonya_client.py convention of hitting a
                   dedicated execution endpoint rather than the general one)
            PUT    /v2/order/modify
            DELETE https://api-hft.upstox.com/v2/order/cancel?order_id=..
            GET    /v2/order/retrieve-all       (order book)
            GET    /v2/order/history            (single order's history)
            GET    /v2/portfolio/short-term-positions
            GET    /v2/portfolio/long-term-holdings
            GET    /v2/user/get-funds-and-margin
  Instrument master (bulk, ungated, gzipped JSON — NOT an authenticated
  API call): https://assets.upstox.com/market-quote/instruments/exchange/
  {complete|NSE|BSE|MCX|...}.json.gz
  Known quirk (reported repeatedly on Upstox's own community forum, not
  guessed): some rows in this file omit a usable trading_symbol/name
  field. _load_instrument_dump() below tolerates that rather than
  assuming every row is well-formed.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)


class UpstoxError(RuntimeError):
    pass


# ── Config (self-contained — see module docstring re: standalone status) ──

API_BASE = "https://api.upstox.com"
HFT_BASE = "https://api-hft.upstox.com"  # dedicated low-latency order host
ASSETS_BASE = "https://assets.upstox.com/market-quote/instruments/exchange"

UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY")
UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET")
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI")
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN")

# Where the downloaded instrument master gets cached. Deliberately mirrors
# this codebase's habit (see config.py's instrument_cache_dir comment) of
# keeping broker cache files out of cwd-relative defaults.
_CACHE_DIR = Path(
    os.getenv(
        "UPSTOX_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".upstox_cache")
    )
)

# Minimum length for an F&O trading_symbol leading-token alias ("TCS",
# "INFY", "M&MFIN"). Shorter tokens (e.g. "LT") are too ambiguous — they
# only resolve via an exact trading_symbol match, never via name-prefix
# matching.
_MIN_TICKER_ALIAS_LEN = 3


def _angel_index_spot(underlying):
    """Best-effort spot for an index Upstox's own master has no spot
    instrument for (e.g. NIFTYNXT50). Only consults Angel's index tokens —
    never an arbitrary stock — so this stays a no-op unless the symbol is a
    recognized index AND the SmartAPI path is available."""
    try:
        from brokers.smartapi_client import INDEX_TOKENS, get_index_quote

        if (underlying or "").upper() not in INDEX_TOKENS:
            return None
        q = get_index_quote(underlying)
        if not q:
            return None
        return {"last_price": q.get("ltp")}
    except Exception:
        return None

# Common index instrument_keys — Upstox has no numeric "index token" the
# way SmartAPI/Shoonya do; the instrument_key IS the identifier, and for
# indices it's a fixed literal string rather than something resolved from
# the instrument master (indices aren't tradable rows in that file the
# same way option/future contracts are).
INDEX_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "SENSEX": "BSE_INDEX|SENSEX",
    "INDIAVIX": "NSE_INDEX|India VIX",
}

# unit/interval vocabulary for the V3 historical-candle endpoints.
CANDLE_UNITS = ("minutes", "hours", "days", "weeks", "months")


# ── Auth (OAuth2 authorization-code flow — see module docstring) ──────────


def build_login_url(state: str, api_key: str = None, redirect_uri: str = None) -> str:
    """URL to open in a browser/webview to start the daily login. Upstox
    redirects back to redirect_uri with `?code=...&state=...` on success;
    always send a random `state` and check it matches on the way back
    (Upstox's own docs call this out as a CSRF protection, not optional)."""
    params = {
        "response_type": "code",
        "client_id": api_key or UPSTOX_API_KEY,
        "redirect_uri": redirect_uri or UPSTOX_REDIRECT_URI,
        "state": state,
    }
    if not params["client_id"] or not params["redirect_uri"]:
        raise UpstoxError(
            "UPSTOX_API_KEY / UPSTOX_REDIRECT_URI not set (and none passed explicitly)"
        )
    return f"{API_BASE}/v2/login/authorization/dialog?{urlencode(params)}"


def exchange_code_for_token(
    code: str, api_key: str = None, api_secret: str = None, redirect_uri: str = None
) -> dict:
    """Single-use: the `code` from the login redirect can only be
    exchanged once, success or failure. Returns Upstox's raw token
    payload (includes access_token, user/profile fields, expiry info)."""
    data = {
        "code": code,
        "client_id": api_key or UPSTOX_API_KEY,
        "client_secret": api_secret or UPSTOX_API_SECRET,
        "redirect_uri": redirect_uri or UPSTOX_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    missing = [k for k in ("client_id", "client_secret", "redirect_uri") if not data[k]]
    if missing:
        raise UpstoxError(f"Missing for token exchange: {', '.join(missing)}")
    response = requests.post(
        f"{API_BASE}/v2/login/authorization/token",
        headers={
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=data,
        timeout=15,
    )
    payload = _safe_json(response)
    if response.status_code != 200 or not payload.get("access_token"):
        raise UpstoxError(f"Token exchange failed ({response.status_code}): {payload}")
    return payload


def _safe_json(response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


# ── Session / request plumbing ─────────────────────────────────────────────


class UpstoxSession:
    """Thin holder for the access_token plus a retrying request helper.
    Does NOT mint tokens itself — see module docstring for why Upstox's
    daily-refresh model doesn't support that the way SmartAPI/Shoonya do."""

    def __init__(self, access_token: str = None):
        self._token = access_token or UPSTOX_ACCESS_TOKEN
        self._lock = threading.RLock()

    @property
    def access_token(self) -> str:
        with self._lock:
            if not self._token:
                raise UpstoxError(
                    "No Upstox access token set. Generate one (build_login_url() -> "
                    "login -> exchange_code_for_token(), or paste today's token from "
                    "the app dashboard) and set UPSTOX_ACCESS_TOKEN or pass it to "
                    "UpstoxSession(access_token=...)."
                )
            return self._token

    def set_token(self, token: str) -> None:
        with self._lock:
            self._token = token

    def _headers(self, content_type="application/json") -> dict:
        return {
            "Accept": "application/json",
            "Content-Type": content_type,
            "Authorization": f"Bearer {self.access_token}",
        }

    def request(
        self,
        method: str,
        url: str,
        *,
        params=None,
        json_body=None,
        form_body=None,
        max_retries=3,
    ) -> dict:
        """One retrying HTTP round-trip. Retries only on 429/5xx (mirrors
        this codebase's general instinct — see smartapi_history.py's
        conservative pacing — of not hammering a broker endpoint that's
        already signalling trouble)."""
        content_type = (
            "application/x-www-form-urlencoded"
            if form_body is not None
            else "application/json"
        )
        last_exc = None
        for attempt in range(max_retries):
            try:
                response = requests.request(
                    method,
                    url,
                    params=params,
                    json=json_body if form_body is None else None,
                    data=form_body,
                    headers=self._headers(content_type),
                    timeout=15,
                )
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(0.5 * (2**attempt))
                continue

            if (
                response.status_code in (429, 500, 502, 503, 504)
                and attempt < max_retries - 1
            ):
                time.sleep(0.5 * (2**attempt))
                continue

            payload = _safe_json(response)
            if response.status_code >= 400:
                raise UpstoxError(f"{method}{url} -> {response.status_code}: {payload}")
            return payload

        raise UpstoxError(
            f"{method} {url} failed after {max_retries} attempts: {last_exc}"
        )


_session = UpstoxSession()


# ── Instrument master (bulk download + local cache, unauthenticated) ──────


def _scope_for_exchange(exchange: str) -> str:
    ex = (exchange or "").upper()
    if ex in ("NFO", "NSE"):
        return "NSE"
    if ex in ("BFO", "BSE"):
        return "BSE"
    return ex


def _instrument_dump_path(scope: str) -> Path:
    return _CACHE_DIR / f"{scope.upper()}.json"


def _load_instrument_dump(
    scope: str = "complete", force_refresh: bool = False, max_age_hours: float = 20.0
) -> list:
    """Download+cache the gzipped instrument master. scope is 'complete'
    or a single exchange code like 'NSE', 'BSE', 'MCX' (smaller download).
    Upstox regenerates this file around 6 AM IST; a ~20h cache window
    keeps a single overnight download from serving stale contracts into
    the next session without re-fetching on every call."""
    dump_path = _instrument_dump_path(scope)
    if not force_refresh and dump_path.exists():
        age_hours = (time.time() - dump_path.stat().st_mtime) / 3600
        if age_hours < max_age_hours:
            try:
                return json.loads(dump_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass  # fall through and re-download

    url = f"{ASSETS_BASE}/{scope.upper()}.json.gz"
    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        if dump_path.exists():
            logger.warning(
                "[upstox_client] instrument master refresh failed (%s), using stale cache",
                response.status_code,
            )
            return json.loads(dump_path.read_text(encoding="utf-8"))
        raise UpstoxError(f"Instrument master download failed: {response.status_code}")

    rows = json.loads(gzip.decompress(response.content).decode("utf-8"))
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    logger.info(
        "[upstox_client] cached %d instrument rows for scope=%s", len(rows), scope
    )
    return rows


def _row_symbol(row: dict) -> str:
    """Community reports (not a guess — see module docstring) show some
    instrument-master rows missing a trading_symbol field under some
    field-name variants. Try the known variants before giving up."""
    return (
        row.get("trading_symbol") or row.get("tradingsymbol") or row.get("symbol") or ""
    )


def _row_expiry_date(row: dict) -> Optional[str]:
    """Normalize whatever shape `expiry` shows up in (epoch millis is the
    documented shape; tolerate an already-formatted date string too)."""
    raw = row.get("expiry")
    if raw in (None, "", 0):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.utcfromtimestamp(raw / 1000).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    return str(raw)


# Cache of Upstox master full-company-name -> trading_symbol (ticker), keyed
# by the condense-normalized name. Built lazily from the (unauthenticated,
# 20h-cached) instrument dump so it survives the same refresh window; cheap
# to rebuild and never blocks a broker session.
_COMPANY_NAME_TO_TICKER_CACHE: Optional[dict] = None
_TICKER_TO_COMPANY_NAME_CACHE: dict[str, str] = {}


def get_company_name_for_ticker(ticker: str) -> Optional[str]:
    """Return the exchange-master display name for an equity ticker.

    The F&O master used for contract resolution commonly exposes only the
    short underlying (``INFY``). Upstox's public EQ master carries both that
    ticker and the legal display name (``Infosys Limited``), which is the
    right source for the dashboard's secondary symbol label.
    """
    key = (ticker or "").strip().upper()
    if not key:
        return None
    if key in _TICKER_TO_COMPANY_NAME_CACHE:
        return _TICKER_TO_COMPANY_NAME_CACHE[key] or None

    display_name = None
    prefix_candidates = {}
    for scope in ("NSE", "BSE"):
        try:
            rows = _load_instrument_dump(scope)
        except Exception:
            continue
        for row in rows:
            row_ticker = (_row_symbol(row) or "").strip().upper()
            name = (row.get("name") or "").strip()
            if row_ticker == key and name and name.upper() != key:
                display_name = name
                break
            # A few broker masters publish a shortened underlying while the
            # exchange EQ master uses the full ticker (e.g. ICICI vs
            # ICICIBANK). Accept this only if it resolves to one ticker.
            if row_ticker and (row_ticker.startswith(key) or key.startswith(row_ticker)) and name:
                prefix_candidates.setdefault(row_ticker, name)
        if display_name:
            break

    if not display_name and len(prefix_candidates) == 1:
        candidate_name = next(iter(prefix_candidates.values()))
        if candidate_name.upper() != key:
            display_name = candidate_name

    _TICKER_TO_COMPANY_NAME_CACHE[key] = display_name or ""
    return display_name


def _build_company_name_to_ticker() -> dict:
    """{condensed_full_company_name: ticker} from Upstox's master.

    Resolves free-text full-company-name inputs typed into the Dashboard's
    "Other..." prompt (which Angel's ticker-only ScripMaster can't reverse-
    engineer). Upstox's master carries the full name in `name` AND the clean
    exchange ticker in `trading_symbol` on its EQ rows (e.g.
    name="MARUTI SUZUKI INDIA LTD.", trading_symbol="MARUTI"), so EQ rows are
    the primary source — they give the unadorned ticker and cover scrips that
    have an EQ listing but no active F&O options (M&M FINANCE, etc.). F&O
    option rows are only used as a fallback so a freshly-added derivative
    name still resolves; its trading_symbol's leading token is the ticker.
    Condensed keys survive trailing "Ltd"/".", case, and whitespace noise."""
    from brokers.symbol_names import _condense

    out = {}
    # Pass 1: EQ rows (clean, unadorned tickers like "MARUTI", "ADANIENSOL").
    # These are authoritative — a ticker with no embedded strike/expiry is the
    # real exchange symbol, and they cover scrips with no active F&O options.
    for scope in ("NSE", "BSE"):
        try:
            rows = _load_instrument_dump(scope)
        except Exception:
            continue
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            ts = (_row_symbol(row) or "").upper()
            if " " not in ts and len(ts) >= _MIN_TICKER_ALIAS_LEN:
                out.setdefault(_condense(name), ts)
    # Pass 2: F&O option/future rows as a fallback ONLY — their trading_symbol
    # embeds strike+expiry and must be split to a leading token. Skipped where
    # an EQ row already established the ticker for that name.
    for scope in ("NSE", "BSE"):
        try:
            rows = _load_instrument_dump(scope)
        except Exception:
            continue
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            if row.get("segment", "").endswith("_FO") and row.get(
                "instrument_type"
            ) in ("CE", "PE", "FUT"):
                ticker = (_row_symbol(row) or "").split(" ", 1)[0].upper()
                if len(ticker) >= _MIN_TICKER_ALIAS_LEN:
                    out.setdefault(_condense(name), ticker)
    return out


def _resolve_company_name_to_ticker(underlying: str) -> Optional[str]:
    """Resolve a full company name (typed in the Dashboard's "Other..." prompt)
    to its exchange ticker using Upstox's instrument dump as a reference.

    Used by Angel/SmartAPI's canonicalization as a fallback when Angel's own
    ScripMaster (ticker-only `name` field) can't map the full company name.
    Returns the canonical ticker, or None."""
    global _COMPANY_NAME_TO_TICKER_CACHE
    req = (underlying or "").strip().upper()
    if not req:
        return None
    if _COMPANY_NAME_TO_TICKER_CACHE is None:
        try:
            _COMPANY_NAME_TO_TICKER_CACHE = _build_company_name_to_ticker()
        except Exception as exc:
            logger.warning("[upstox_client] company-name index build failed: %s", exc)
            return None
    from brokers.symbol_names import _condense, _COMMON_UNDERLYING_ALIASES

    # 1. Curated alias table FIRST — it's authoritative for well-known
    #    full-name -> ticker pairs and must win over noisier fallbacks.
    if req in _COMMON_UNDERLYING_ALIASES:
        return _COMMON_UNDERLYING_ALIASES[req]
    # 2. exact (condensed) hit against the master-derived name index.
    cond = _condense(req)
    if cond in _COMPANY_NAME_TO_TICKER_CACHE:
        return _COMPANY_NAME_TO_TICKER_CACHE[cond]
    # 3. No prefix fallback — too lossy over a multi-hundred-scrip universe.
    return None


def _canonical_name(underlying: str, rows: list, key: str = "name",
                    instrument_types=("CE", "PE")):
    """Tolerant key mapping against Upstox's own master (see
    brokers/symbol_names.py). Upstox stores full company names ("ZYDUS
    LIFESCIENCES LTD") while callers often hold the ticker ("ZYDUSLIFE"),
    and vice versa for Angel. Returns the master's exact key on a UNIQUE
    match, else None (exact-match behavior is preserved on ambiguity).

    When `key` is "name", aliases are seeded from each F&O row's
    trading_symbol leading token ("ZYDUSLIFE 960 CE 27 OCT 26" -> "ZYDUSLIFE"),
    so a ticker typed in the picker resolves to the stored company name."""
    from brokers.symbol_names import canonicalize_underlying

    mapping = {}
    for row in rows:
        if row.get("instrument_type") not in instrument_types:
            continue
        canonical = (row.get(key) or "").strip()
        if not canonical:
            continue
        mapping[canonical.upper()] = canonical
        if key == "name":
            # CE/PE trading symbols are "<TICKER> <strike> <CE|PE> <exp>",
            # so their leading token is the exchange ticker ("INFY", "TATAMOTORS")
            # — alias it to the master's full company name so a ticker picked
            # from the (Angel-sourced) dropdown still resolves on Upstox.
            ticker = (_row_symbol(row) or "").split(" ", 1)[0].upper()
            if len(ticker) >= _MIN_TICKER_ALIAS_LEN:
                mapping.setdefault(ticker, canonical)
    return canonicalize_underlying(underlying, mapping)


def list_expiries(underlying: str, exchange: str = "NFO") -> list:
    """Sorted YYYY-MM-DD expiry strings for underlying's option chain."""
    scope = _scope_for_exchange(exchange)
    rows = _load_instrument_dump(scope)
    underlying = underlying.upper()
    expiries = {
        _row_expiry_date(row)
        for row in rows
        if row.get("instrument_type") in ("CE", "PE")
        and (row.get("name") or "").upper() == underlying
        and _row_expiry_date(row)
    }
    if not expiries:
        canonical = _canonical_name(underlying, rows)
        if canonical and canonical != underlying:
            expiries = {
                _row_expiry_date(row)
                for row in rows
                if row.get("instrument_type") in ("CE", "PE")
                and (row.get("name") or "").upper() == canonical
                and _row_expiry_date(row)
            }
    return sorted(expiries)


def find_option_token(
    underlying: str, expiry: str, strike, opt_type: str, exchange: str = "NFO"
) -> Optional[dict]:
    """expiry as 'YYYY-MM-DD'. Returns {'instrument_key', 'trading_symbol',
    'lot_size'} for the exact contract, or None if unresolved."""
    scope = _scope_for_exchange(exchange)
    rows = _load_instrument_dump(scope)
    underlying = underlying.upper()
    opt_type = opt_type.upper()
    try:
        strike_val = float(strike)
    except (TypeError, ValueError):
        return None
    for row in rows:
        if (
            row.get("instrument_type") == opt_type
            and (row.get("name") or "").upper() == underlying
            and _row_expiry_date(row) == expiry
            and float(row.get("strike_price") or -1) == strike_val
        ):
            return {
                "instrument_key": row.get("instrument_key"),
                "trading_symbol": _row_symbol(row),
                "lot_size": row.get("lot_size"),
            }
    canonical = _canonical_name(underlying, rows)
    if canonical and canonical != underlying:
        for row in rows:
            if (
                row.get("instrument_type") == opt_type
                and (row.get("name") or "").upper() == canonical
                and _row_expiry_date(row) == expiry
                and float(row.get("strike_price") or -1) == strike_val
            ):
                return {
                    "instrument_key": row.get("instrument_key"),
                    "trading_symbol": _row_symbol(row),
                    "lot_size": row.get("lot_size"),
                }
    return None


def find_equity_token(symbol: str, exchange: str = "NSE") -> Optional[dict]:
    """Resolve a cash-market equity's instrument_key, e.g. find_equity_token('RELIANCE')."""
    rows = _load_instrument_dump(_scope_for_exchange(exchange))
    symbol = symbol.upper()
    for row in rows:
        if row.get("instrument_type") == "EQ" and _row_symbol(row).upper() == symbol:
            return {
                "instrument_key": row.get("instrument_key"),
                "trading_symbol": _row_symbol(row),
            }
    # Tolerant retry: "ZYDUS LIFESCIENCES LTD" -> trading symbol "ZYDUSLIFE"
    # using Upstox's own EQ rows (full name stored in `name`).
    canonical = _canonical_name(symbol, rows, key="trading_symbol", instrument_types=("EQ",))
    if canonical and canonical != symbol:
        for row in rows:
            if row.get("instrument_type") == "EQ" and _row_symbol(row).upper() == canonical:
                return {
                    "instrument_key": row.get("instrument_key"),
                    "trading_symbol": _row_symbol(row),
                }
    # Comprehensive resolver: full company name -> ticker built from the master's
    # EQ rows (covers "ADANI ENERGY SOLUTION LTD" -> ADANIENSOL, "MARUTI SUZUKI
    # INDIA LTD" -> MARUTI, etc.). This is the authoritative fallback for names
    # whose ticker isn't a token-prefix of the company name and aren't in the
    # small curated alias table. Guarded so a missing Upstox dump (e.g. network
    # blip during cache refresh) can't break the Angel path.
    ticker = _resolve_company_name_to_ticker(symbol)
    if ticker:
        for row in rows:
            if row.get("instrument_type") == "EQ" and _row_symbol(row).upper() == ticker.upper():
                return {
                    "instrument_key": row.get("instrument_key"),
                    "trading_symbol": _row_symbol(row),
                }
    return None


def index_instrument_key(underlying: str) -> Optional[str]:
    return INDEX_KEYS.get(underlying.upper())


# ── Market data ─────────────────────────────────────────────────────────


def get_ltp(instrument_keys) -> dict:
    """instrument_keys: str or list of instrument_key strings, up to 500
    per Upstox's own documented cap. Returns dict keyed by the response's
    own composite key (e.g. 'NSE_EQ:RELIANCE'), values include last_price."""
    if isinstance(instrument_keys, (list, tuple, set)):
        instrument_keys = ",".join(instrument_keys)
    payload = _session.request(
        "GET",
        f"{API_BASE}/v3/market-quote/ltp",
        params={"instrument_key": instrument_keys},
    )
    return payload.get("data", {})


def get_quotes(instrument_keys) -> dict:
    """Full OHLC + depth quote (v2 — the confirmed-working full-quote
    endpoint; v3 currently only has LTP and a separate OHLC-only variant)."""
    if isinstance(instrument_keys, (list, tuple, set)):
        instrument_keys = ",".join(instrument_keys)
    payload = _session.request(
        "GET",
        f"{API_BASE}/v2/market-quote/quotes",
        params={"instrument_key": instrument_keys},
    )
    return payload.get("data", {})


def get_spot_quote(underlying: str) -> Optional[dict]:
    """Return Upstox's raw full quote for an index or cash underlying.

    This is intentionally a module-level broker function, not the
    ``MarketData.get_spot_quote(self, underlying)`` adapter method. A prior
    copy/paste left the adapter signature here and then imported this same
    name recursively; every normal one-argument caller consequently failed
    before making a quote request. Keeping the raw Upstox shape here also
    lets ``get_atm_chain`` use ``last_price`` directly.
    """
    name = (underlying or "").strip().upper()
    if not name:
        return None

    instrument_key = index_instrument_key(name)
    if not instrument_key:
        equity = find_equity_token(name)
        instrument_key = (equity or {}).get("instrument_key")
    if not instrument_key:
        return None

    quotes = get_quotes(instrument_key)
    if not quotes:
        return None
    # Upstox can vary the separator used in its response key, but never use
    # an arbitrary first row as a fallback: doing so can label a NIFTY quote
    # as SENSEX after a broker/symbol switch. Return no quote when the
    # requested instrument cannot be identified; the normal provider
    # fallback path may then recover it without presenting false data.
    if quotes.get(instrument_key):
        return quotes[instrument_key]
    normalized_key = instrument_key.replace("|", ":").upper()
    for response_key, quote in quotes.items():
        quote_key = str(quote.get("instrument_key") or response_key).replace("|", ":").upper()
        if quote_key == normalized_key:
            return quote
    return None


def get_historical_candles(
    instrument_key: str, unit: str, interval: int, from_date: str, to_date: str = None
) -> list:
    """unit in CANDLE_UNITS, interval an int appropriate to that unit
    (e.g. unit='minutes', interval=5). Dates 'YYYY-MM-DD'. to_date
    defaults to today. NOTE the URL order is .../{to_date}/{from_date} —
    that's Upstox's own path ordering, not a typo (confirmed against
    their documented example requests)."""
    if unit not in CANDLE_UNITS:
        raise ValueError(f"unit must be one of {CANDLE_UNITS}")
    to_date = to_date or datetime.now().strftime("%Y-%m-%d")
    url = f"{API_BASE}/v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}"
    payload = _session.request("GET", url)
    return (payload.get("data") or {}).get("candles", [])


def get_intraday_candles(instrument_key: str, unit: str, interval: int) -> list:
    if unit not in CANDLE_UNITS:
        raise ValueError(f"unit must be one of {CANDLE_UNITS}")
    url = f"{API_BASE}/v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}"
    payload = _session.request("GET", url)
    return (payload.get("data") or {}).get("candles", [])


def get_atm_chain(
    underlying: str, expiry: str, strikes_around_atm: int = 10, exchange: str = "NFO"
) -> Optional[dict]:
    """Same ergonomics/return shape as this codebase's SmartAPI
    get_atm_chain(): {'underlying','spot','atm_strike','expiry','rows':[...]}.
    Built from the instrument master (for strikes) + a single batched
    quotes call (for LTP/OI per leg) rather than a chain-specific
    endpoint — Upstox doesn't expose one that returns both legs pre-paired
    the way SmartAPI's does."""
    spot = get_spot_quote(underlying)
    if not spot or not spot.get("last_price"):
        # Upstox's master can carry an index's OPTIONS without a matching
        # spot/index instrument (e.g. NIFTYNXT50) — without a spot price the
        # ATM chain can't be anchored. Borrow the spot from Angel's index
        # tokens (SmartAPI path is always available when USE_SMARTAPI), which
        # shares the same NSE-derived strike grid, so mixing sources is safe.
        spot = _angel_index_spot(underlying)
    if not spot or not spot.get("last_price"):
        return None
    spot_price = spot["last_price"]

    scope = _scope_for_exchange(exchange)
    rows = _load_instrument_dump(scope)
    name_u = _canonical_name(underlying, rows) or underlying.upper()
    legs = [
        r
        for r in rows
        if r.get("instrument_type") in ("CE", "PE")
        and (r.get("name") or "").upper() == name_u
        and _row_expiry_date(r) == expiry
    ]
    if not legs and name_u != underlying.upper():
        legs = [
            r
            for r in rows
            if r.get("instrument_type") in ("CE", "PE")
            and (r.get("name") or "").upper() == underlying.upper()
            and _row_expiry_date(r) == expiry
        ]
    if not legs:
        return None

    strikes = sorted(
        {float(r["strike_price"]) for r in legs if r.get("strike_price") is not None}
    )
    step = strikes[1] - strikes[0] if len(strikes) > 1 else 0
    atm_strike = min(strikes, key=lambda s: abs(s - spot_price)) if strikes else None
    if atm_strike is None:
        return None
    window = (
        {
            atm_strike + i * step
            for i in range(-strikes_around_atm, strikes_around_atm + 1)
        }
        if step
        else {atm_strike}
    )
    legs = [r for r in legs if float(r.get("strike_price", -1)) in window]

    keys = [r["instrument_key"] for r in legs if r.get("instrument_key")]
    quotes = get_quotes(keys) if keys else {}
    by_key = {}
    for composite, q in quotes.items():
        ik = q.get("instrument_token") or composite
        by_key[ik] = q

    out_rows = []
    for r in legs:
        q = by_key.get(r.get("instrument_key"), {})
        out_rows.append(
            {
                "strike": float(r.get("strike_price")),
                "type": r.get("instrument_type"),
                "instrument_key": r.get("instrument_key"),
                "trading_symbol": _row_symbol(r),
                "lot_size": r.get("lot_size"),
                "ltp": q.get("last_price"),
                "oi": q.get("oi"),
                "volume": q.get("volume"),
            }
        )
    out_rows.sort(key=lambda x: (x["strike"], x["type"]))
    return {
        "underlying": name_u,
        "spot": spot_price,
        "atm_strike": atm_strike,
        "expiry": expiry,
        "rows": out_rows,
    }


# ── Execution ────────────────────────────────────────────────────────────


@dataclass
class PlaceOrderRequest:
    instrument_key: str
    quantity: int
    transaction_type: str  # BUY | SELL
    product: str = "D"  # D=Delivery, I=Intraday, CO, MTF ...
    order_type: str = "MARKET"  # MARKET | LIMIT | SL | SL-M
    validity: str = "DAY"
    price: float = 0.0
    trigger_price: float = 0.0
    disclosed_quantity: int = 0
    is_amo: bool = False
    slice: bool = True  # V3's auto-slicing for freeze-qty limits
    tag: Optional[str] = None


def place_order(req: PlaceOrderRequest) -> str:
    """Places via the dedicated low-latency host (api-hft.upstox.com),
    matching this codebase's shoonya_client.py convention of hitting a
    dedicated execution endpoint. Returns Upstox's order_id."""
    body = {
        "quantity": int(req.quantity),
        "product": req.product,
        "validity": req.validity,
        "price": float(req.price),
        "tag": req.tag,
        "instrument_token": req.instrument_key,
        "order_type": req.order_type,
        "transaction_type": req.transaction_type.upper(),
        "disclosed_quantity": int(req.disclosed_quantity),
        "trigger_price": float(req.trigger_price),
        "is_amo": bool(req.is_amo),
        "slice": bool(req.slice),
    }
    payload = _session.request("POST", f"{HFT_BASE}/v3/order/place", json_body=body)
    data = payload.get("data") or {}
    order_ids = data.get("order_ids") or (
        [data["order_id"]] if data.get("order_id") else []
    )
    if not order_ids:
        raise UpstoxError(f"place_order: no order_id in response: {payload}")
    return str(order_ids[0])


def modify_order(
    order_id: str,
    *,
    quantity: int = None,
    order_type: str = None,
    price: float = None,
    trigger_price: float = None,
    validity: str = None,
    disclosed_quantity: int = None,
) -> dict:
    """Only pass the fields you want changed — omitted fields keep the
    order's existing values (Upstox's own documented modify semantics)."""
    body = {"order_id": order_id}
    for key, val in (
        ("quantity", quantity),
        ("order_type", order_type),
        ("price", price),
        ("trigger_price", trigger_price),
        ("validity", validity),
        ("disclosed_quantity", disclosed_quantity),
    ):
        if val is not None:
            body[key] = val
    return _session.request("PUT", f"{API_BASE}/v2/order/modify", json_body=body)


def cancel_order(order_id: str) -> dict:
    return _session.request(
        "DELETE", f"{HFT_BASE}/v2/order/cancel", params={"order_id": order_id}
    )


def get_order_book() -> list:
    """Normalized toward this codebase's broker-neutral order shape (see
    shoonya_client.get_order_book()) — orderid/tradingsymbol/orderstatus
    keys alongside Upstox's own field names, so a future adapter can
    treat both broker modules the same way."""
    payload = _session.request("GET", f"{API_BASE}/v2/order/retrieve-all")
    normalized = []
    for row in payload.get("data") or []:
        item = dict(row)
        item.setdefault("orderid", row.get("order_id"))
        item.setdefault("tradingsymbol", row.get("trading_symbol"))
        item.setdefault("orderstatus", row.get("status"))
        item.setdefault("ordertag", row.get("tag"))
        normalized.append(item)
    return normalized


def get_positions() -> list:
    payload = _session.request("GET", f"{API_BASE}/v2/portfolio/short-term-positions")
    normalized = []
    for row in payload.get("data") or []:
        item = dict(row)
        item.setdefault("tradingsymbol", row.get("trading_symbol"))
        item.setdefault(
            "pnl",
            (
                row.get("pnl")
                if row.get("pnl") is not None
                else (row.get("unrealised") or 0) + (row.get("realised") or 0)
            ),
        )
        normalized.append(item)
    return normalized


def get_holdings() -> list:
    payload = _session.request("GET", f"{API_BASE}/v2/portfolio/long-term-holdings")
    return payload.get("data") or []


def get_funds() -> dict:
    """Normalized toward this codebase's broker-neutral funds shape (see
    shoonya_client.get_funds()). Note: from 19-Jul-2025 Upstox merged
    equity+commodity funds into the 'equity' object of the response
    (their own changelog, not a guess) — this reads from there."""
    payload = _session.request("GET", f"{API_BASE}/v2/user/get-funds-and-margin")
    equity = (payload.get("data") or {}).get("equity") or {}
    return {
        "available_cash": equity.get("available_margin", 0.0),
        "available_margin": equity.get("available_margin", 0.0),
        "utilised_margin": equity.get("used_margin", 0.0),
        "utilised_span": equity.get("span_margin", 0.0),
        "utilised_exposure": equity.get("exposure_margin", 0.0),
        "payin": equity.get("payin_amount", 0.0),
        "raw": payload.get("data"),
    }


# ── __main__ smoke test ─────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s"
    )

    print("=" * 60)
    print("upstox_client.py — smoke test")
    print("=" * 60)

    if not UPSTOX_ACCESS_TOKEN:
        print(
            "\nNo UPSTOX_ACCESS_TOKEN set — showing the login URL you'd open instead:"
        )
        try:
            print(build_login_url(state="smoke-test"))
        except UpstoxError as exc:
            print(f"(can't build one either: {exc})")
        raise SystemExit(0)

    print("\nFetching NIFTY 50 LTP...")
    try:
        print(get_spot_quote("NIFTY"))
    except UpstoxError as exc:
        print(f"LTP fetch failed: {exc}")

    print("\nFetching last 3 days of NIFTY 50 daily candles...")
    try:
        fromdate = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        candles = get_historical_candles(INDEX_KEYS["NIFTY"], "days", 1, fromdate)
        print(f"Got {len(candles)} candles")
        for c in candles[:3]:
            print(c)
    except UpstoxError as exc:
        print(f"Candle fetch failed: {exc}")

    print("\nFetching funds...")
    try:
        print(get_funds())
    except UpstoxError as exc:
        print(f"Funds fetch failed: {exc}")

    print("\nFetching order book...")
    try:
        book = get_order_book()
        print(f"{len(book)} orders on the book")
    except UpstoxError as exc:
        print(f"Order book fetch failed: {exc}")
