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
    """LTP-oriented convenience wrapper matching this codebase's
    get_spot_quote() ergonomics (see market_data.py's MarketData protocol)."""
    key = index_instrument_key(underlying) or (find_equity_token(underlying) or {}).get(
        "instrument_key"
    )
    if not key:
        return None
    data = get_ltp(key)
    return next(iter(data.values()), None)


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
        return None
    spot_price = spot["last_price"]

    scope = _scope_for_exchange(exchange)
    rows = _load_instrument_dump(scope)
    underlying_u = underlying.upper()
    legs = [
        r
        for r in rows
        if r.get("instrument_type") in ("CE", "PE")
        and (r.get("name") or "").upper() == underlying_u
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
                "ltp": q.get("last_price"),
                "oi": q.get("oi"),
                "volume": q.get("volume"),
            }
        )
    out_rows.sort(key=lambda x: (x["strike"], x["type"]))
    return {
        "underlying": underlying_u,
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
