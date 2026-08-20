"""Shoonya account and execution adapter.

The dashboard continues to use its established SmartAPI market-data feed.
This module implements the smaller broker boundary needed for live account
state and execution, using Shoonya's official Noren Python API.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
from datetime import datetime

import time

import pyotp
import requests

# Rate limiting configuration for Shoonya API calls
# Shoonya generally has more lenient rate limits than SmartAPI,
# but we still implement sensible throttling to avoid issues
_SHOONYA_RATE_LIMIT_MIN_INTERVAL = {
    "get_quotes": 0.20,     # ~5 calls per second for quotes
    "get_order_book": 0.30, # ~3 calls per second for order book
    "get_positions": 0.30,  # ~3 calls per second for positions  
    "get_limits": 0.50,     # ~2 calls per second for limits
    "place_order": 0.35,    # ~3 calls per second for orders
    "searchscrip": 0.25,    # ~4 calls per second for symbol search
}
_SHOONYA_RATE_LIMIT_DEFAULT_INTERVAL = 0.15  # ~6-7 calls per second default
_SHOONYA_RATE_LIMIT_BACKOFF_S = 1.5
_SHOONYA_RATE_LIMIT_MAX_RETRIES = 3
_shoonya_rate_limit_lock = threading.Lock()
_shoonya_rate_limit_last_ts: dict[str, float] = {}
_shoonya_rate_limit_global_last = 0.0


def _is_rate_limited(err) -> bool:
    """Check if an error indicates rate limiting."""
    text = str(err).lower()
    return (
        "rate limit" in text
        or "too many requests" in text
        or "access denied" in text
        or "exceed" in text
    )


def _shoonya_rate_limit_wait(fn_name: str) -> None:
    """Sleep just enough to respect per-endpoint + global spacing for Shoonya."""
    global _shoonya_rate_limit_global_last
    min_gap = _SHOONYA_RATE_LIMIT_MIN_INTERVAL.get(fn_name, _SHOONYA_RATE_LIMIT_DEFAULT_INTERVAL)
    with _shoonya_rate_limit_lock:
        now = time.monotonic()
        last_fn = _shoonya_rate_limit_last_ts.get(fn_name, 0.0)
        wait_fn = min_gap - (now - last_fn)
        wait_global = _SHOONYA_RATE_LIMIT_DEFAULT_INTERVAL - (now - _shoonya_rate_limit_global_last)
        wait = max(0.0, wait_fn, wait_global)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _shoonya_rate_limit_last_ts[fn_name] = now
        _shoonya_rate_limit_global_last = now


def _shoonya_call_with_retry(fn_name, api_method, *args, **kwargs):
    """Execute a Shoonya API call with rate limiting and retry logic."""
    _shoonya_rate_limit_wait(fn_name)
    delay = _SHOONYA_RATE_LIMIT_BACKOFF_S
    last_exc = None
    
    for attempt in range(1, _SHOONYA_RATE_LIMIT_MAX_RETRIES + 1):
        try:
            result = api_method(*args, **kwargs)
            # Check if result indicates an error
            if isinstance(result, dict) and result.get("stat") == "Not_Ok":
                emsg = str(result.get("emsg", ""))
                if _is_rate_limited(emsg):
                    logger.warning(
                        f"[shoonya_client] {fn_name} rate-limited ({emsg}); "
                        f"backing off {delay}s (attempt {attempt}/{_SHOONYA_RATE_LIMIT_MAX_RETRIES})"
                    )
                    if attempt < _SHOONYA_RATE_LIMIT_MAX_RETRIES:
                        time.sleep(delay)
                        delay *= 2
                        _shoonya_rate_limit_wait(fn_name)
                        continue
                    else:
                        logger.warning(
                            f"[shoonya_client] {fn_name} still rate-limited after "
                            f"{_SHOONYA_RATE_LIMIT_MAX_RETRIES} retries; giving up"
                        )
                        raise BrokerError(f"Rate-limited after retries: {emsg}")
                # Other errors, raise immediately
                raise BrokerError(f"Shoonya {fn_name} rejected: {emsg}")
            return result
        except Exception as e:
            last_exc = e
            if _is_rate_limited(e):
                logger.warning(
                    f"[shoonya_client] {fn_name} rate-limited ({e}); "
                    f"backing off {delay}s (attempt {attempt}/{_SHOONYA_RATE_LIMIT_MAX_RETRIES})"
                )
                if attempt < _SHOONYA_RATE_LIMIT_MAX_RETRIES:
                    time.sleep(delay)
                    delay *= 2
                    _shoonya_rate_limit_wait(fn_name)
                    continue
            # Non-rate-limit errors, raise immediately
            raise BrokerError(f"Shoonya {fn_name} failed: {e}") from e
    
    # Should not reach here, but just in case
    if last_exc:
        raise BrokerError(f"Shoonya {fn_name} failed after retries: {last_exc}")
    raise BrokerError(f"Shoonya {fn_name} failed with unknown error")

try:  # ws_server_live adds backend/ to sys.path; package-level tests do not.
    from config import settings
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from backend.config import settings

logger = logging.getLogger(__name__)

# How long a failed login is cached before the next attempt is allowed.
# Without this, every call site that touches `_session.api` (searchscrip,
# get_quotes, get_order_book, ...) re-runs the full login handshake from
# scratch on every single invocation whenever the previous attempt failed,
# since `self._api` is only ever set on success. Observed in practice:
# ~8 login attempts/second, every one generating a fresh TOTP code and
# POSTing a new login request. Besides being wasteful, hammering a
# broker's login endpoint at that rate is a plausible reason it starts
# returning empty/non-JSON responses in the first place — a client-side
# retry storm masquerading as a broker-side outage.
_LOGIN_RETRY_COOLDOWN_SEC = 30

_LOGIN_TIMEOUT_S = 15


def _login_url(api) -> str:
    """Authorize endpoint for the Noren/Shoonya REST API.

    The official SDK keeps host + routes in a name-mangled private attr
    (`_NorenApi__service_config`); read it when present and fall back to
    the well-known production endpoint only if the SDK layout ever changes.
    """
    config = getattr(api, "_NorenApi__service_config", None) or {}
    host = config.get("host") or "https://api.shoonya.com/NorenWClientTP/"
    route = (config.get("routes") or {}).get("authorize") or "/QuickAuth"
    return f"{host}{route}"


def _authorize_login(api, userid, password, two_fa, vendor_code, api_secret, imei):
    """Perform the Noren QuickAuth handshake with real diagnostics.

    The stock SDK's login() does `json.loads(res.text)` with no timeout and
    no status/body inspection, so a dead or blocked endpoint surfaces as a
    cryptic "Expecting value: line 1 column 1 (char 0)". This replicates the
    exact same request/response contract (SHA-256 password/appkey, jData
    form body) but reports WHAT actually came back — HTTP status, content
    type, and a body snippet — so an outage (HTML/empty/non-200) is
    distinguishable from a credentials rejection (JSON stat=Not_Ok). On
    success it seeds the SDK session via set_session(), matching what
    login() would have set internally.
    """
    pwd = hashlib.sha256(password.encode("utf-8")).hexdigest()
    app_key = hashlib.sha256(f"{userid}|{api_secret}".encode("utf-8")).hexdigest()
    values = {
        "source": "API",
        "apkversion": "1.0.0",
        "uid": userid,
        "pwd": pwd,
        "factor2": two_fa,
        "vc": vendor_code,
        "appkey": app_key,
        "imei": imei,
    }
    url = _login_url(api)
    try:
        res = requests.post(url, data="jData=" + json.dumps(values), timeout=_LOGIN_TIMEOUT_S)
    except requests.RequestException as exc:
        raise BrokerError(
            f"Shoonya login request to {url} failed: {exc} "
            "(endpoint unreachable — check network/VPN or Shoonya status)"
        ) from exc

    body = res.text or ""
    if res.status_code != 200:
        raise BrokerError(
            f"Shoonya login rejected with HTTP {res.status_code} from {url} — "
            f"body: {body[:200]!r}. Non-200/HTML/empty responses indicate a "
            "Shoonya-side outage or network block, not bad credentials."
        )
    if not body.strip():
        raise BrokerError(
            f"Shoonya login returned an empty body from {url} — Shoonya-side "
            "outage or network block."
        )
    try:
        result = json.loads(body)
    except ValueError:
        raise BrokerError(
            f"Shoonya login returned non-JSON from {url} "
            f"(content-type={res.headers.get('content-type', 'unknown')!r}, "
            f"body={body[:200]!r}) — Shoonya-side outage or network block."
        ) from None
    if not isinstance(result, dict) or result.get("stat") != "Ok":
        raise BrokerError(
            "Shoonya login rejected: "
            f"{(result or {}).get('emsg', 'unknown error')}"
        )
    api.set_session(userid, password, result["susertoken"])
    return result


class BrokerError(RuntimeError):
    pass


def _default_api_factory():
    # Shoonya's official repository is a source checkout rather than a
    # conventional PyPI distribution. The setup command places it under
    # runtime/ (gitignored); deployments may override that location.
    sdk_path = os.getenv(
        "SHOONYA_SDK_PATH",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "runtime", "shoonya_api")),
    )
    if os.path.isfile(os.path.join(sdk_path, "api_helper.py")) and sdk_path not in sys.path:
        sys.path.insert(0, sdk_path)
    try:
        from api_helper import ShoonyaApiPy
    except ImportError as exc:
        raise BrokerError(
            "Shoonya SDK is not installed. Run scripts/setup_shoonya.sh or "
            "set SHOONYA_SDK_PATH to an official ShoonyaApi-py checkout."
        ) from exc
    return ShoonyaApiPy()


class ShoonyaSession:
    def __init__(self, api_factory=None):
        self._api_factory = api_factory or _default_api_factory
        self._api = None
        self._lock = threading.RLock()
        self._last_login_attempt = 0.0  # time.monotonic() of the last attempt
        self._last_login_error: BrokerError | None = None

    @property
    def api(self):
        self.ensure_session()
        return self._api

    def ensure_session(self):
        with self._lock:
            if self._api is not None:
                return self._api

            now = time.monotonic()
            if (self._last_login_error is not None
                    and (now - self._last_login_attempt) < _LOGIN_RETRY_COOLDOWN_SEC):
                # A recent attempt already failed — re-raise the cached
                # error instead of re-running the login handshake (fresh
                # TOTP code + login POST) on every single caller. Callers
                # see the same BrokerError they would have gotten from a
                # real attempt; they just don't each trigger one.
                raise self._last_login_error

            self._last_login_attempt = now
            try:
                required = {
                    "SHOONYA_USER_ID": settings.shoonya_user_id,
                    "SHOONYA_PASSWORD": settings.shoonya_password,
                    "SHOONYA_TOTP_SECRET": settings.shoonya_totp_secret,
                    "SHOONYA_VENDOR_CODE": settings.shoonya_vendor_code,
                    "SHOONYA_API_SECRET": settings.shoonya_api_secret,
                }
                missing = [name for name, value in required.items() if not value]
                if missing:
                    raise BrokerError("Missing Shoonya settings: " + ", ".join(missing))
                api = self._api_factory()
                try:
                    two_fa = pyotp.TOTP(settings.shoonya_totp_secret).now()
                    result = _authorize_login(
                        api,
                        userid=settings.shoonya_user_id,
                        password=settings.shoonya_password,
                        two_fa=two_fa,
                        vendor_code=settings.shoonya_vendor_code,
                        api_secret=settings.shoonya_api_secret,
                        imei=settings.shoonya_imei,
                    )
                except BrokerError:
                    raise
                except Exception as exc:
                    raise BrokerError(f"Shoonya login failed: {exc}") from exc
                if not result or result.get("stat") != "Ok":
                    raise BrokerError(f"Shoonya login rejected: {(result or {}).get('emsg', 'unknown error')}")
            except BrokerError as err:
                self._last_login_error = err
                logger.warning(
                    "[shoonya_client] login attempt failed, will not retry for "
                    f"{_LOGIN_RETRY_COOLDOWN_SEC}s: {err}"
                )
                raise

            self._api = api
            self._last_login_error = None
            logger.info("[shoonya_client] Logged in, session established")
            return api


_session = ShoonyaSession()


def healthcheck():
    """
    Test whether a usable Shoonya session exists.

    Returns:
        (True, None) on success
        (False, error_message) on failure.
    """
    try:
        _session.ensure_session()
        return True, None
    except BrokerError as exc:
        return False, str(exc)


def _rows(result):
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and result.get("stat") == "Not_Ok":
        message = str(result.get("emsg", ""))
        if "no data" in message.lower():
            return []
        raise BrokerError(f"Shoonya request rejected: {message or result}")
    return [result] if isinstance(result, dict) else []


def _order_id(row):
    return row.get("norenordno") or row.get("orderid")


def _find_order_by_tag(tag):
    for row in get_order_book():
        if str(row.get("remarks") or row.get("ordertag") or "") == tag:
            return _order_id(row)
    return None


def place_order(tradingsymbol, symboltoken, exchange, transaction_type,
                quantity, order_type="MARKET", product_type=None, price=0.0,
                variety="NORMAL", order_tag=None):
    """Place one Shoonya order using the dashboard's broker-neutral shape."""
    del symboltoken, variety  # Shoonya identifies the contract by tsym.
    tag = order_tag
    if tag:
        try:
            existing = _find_order_by_tag(tag)
        except Exception as exc:
            raise BrokerError(f"cannot verify order tag {tag} before placement: {exc}") from exc
        if existing:
            return str(existing)

    price_type = "MKT" if order_type.upper() == "MARKET" else "LMT"
    try:
        result = _shoonya_call_with_retry("place_order", _session.api.place_order,
            buy_or_sell="B" if transaction_type.upper() == "BUY" else "S",
            product_type=product_type or settings.shoonya_product_type,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            quantity=int(quantity),
            discloseqty=0,
            price_type=price_type,
            price=0.0 if price_type == "MKT" else float(price),
            trigger_price=None,
            retention="DAY",
            amo="NO",
            remarks=tag,
        )
    except Exception as exc:
        existing = _find_order_by_tag(tag) if tag else None
        if existing:
            return str(existing)
        raise BrokerError(f"Shoonya place_order failed: {exc}") from exc
    if not result or result.get("stat") != "Ok" or not result.get("norenordno"):
        existing = _find_order_by_tag(tag) if tag else None
        if existing:
            return str(existing)
        raise BrokerError(f"Shoonya place_order rejected: {result}")
    return str(result["norenordno"])


def get_order_book():
    result = _shoonya_call_with_retry("get_order_book", _session.api.get_order_book)
    normalized = []
    for row in _rows(result):
        item = dict(row)
        item.setdefault("orderid", _order_id(row))
        item.setdefault("ordertag", row.get("remarks"))
        item.setdefault("tradingsymbol", row.get("tsym"))
        item.setdefault("orderstatus", row.get("status"))
        normalized.append(item)
    return normalized


def get_positions():
    result = _shoonya_call_with_retry("get_positions", _session.api.get_positions)
    normalized = []
    for row in _rows(result):
        item = dict(row)
        item.setdefault("tradingsymbol", row.get("tsym"))
        realized = float(row.get("rpnl") or 0)
        unrealized = float(row.get("urmtom") or row.get("unrpnl") or 0)
        item.setdefault("pnl", realized + unrealized)
        normalized.append(item)
    return normalized


def get_funds():
    data = _shoonya_call_with_retry("get_limits", _session.api.get_limits) or {}
    if data.get("stat") == "Not_Ok":
        raise BrokerError(f"Shoonya get_limits rejected: {data.get('emsg', data)}")
    def number(*keys):
        for key in keys:
            if data.get(key) not in (None, ""):
                try:
                    return float(data[key])
                except (TypeError, ValueError):
                    pass
        return 0.0
    cash = number("cash", "cashmarginavailable")
    used = number("marginused", "marginusedprevday")
    return {
        "available_cash": cash,
        "available_margin": number("marginavailable", "cashmarginavailable") or cash,
        "available_intraday_payin": number("payin"),
        "available_limit_margin": number("marginavailable"),
        "collateral": number("collateral"),
        "utilised_margin": used,
        "utilised_span": number("span"),
        "utilised_exposure": number("expo"),
        "m2m_unrealized": number("urmtom"),
        "m2m_realized": number("rpnl"),
    }


def resolve_option_contract(symbol, expiry, strike, option_type, exchange="NFO"):
    """Resolve an exact Shoonya tsym; return the standard 3-tuple or None."""
    try:
        expiry_dt = datetime.strptime(expiry, "%d-%b-%Y")
        strike_text = f"{float(strike):g}"
    except (TypeError, ValueError):
        return None
    expiry_tokens = {
        expiry_dt.strftime("%d%b%y").upper(),
        expiry_dt.strftime("%d%b%Y").upper(),
    }
    query = f"{symbol} {expiry_dt.strftime('%d%b%y').upper()} {strike_text} {option_type}"
    result = _session.api.searchscrip(exchange=exchange, searchtext=query)
    values = (result or {}).get("values") or []
    for row in values:
        haystack = re.sub(r"[^A-Z0-9.]", "", f"{row.get('tsym', '')}{row.get('dname', '')}".upper())
        if (symbol.upper() in haystack and option_type.upper() in haystack
                and any(token in haystack for token in expiry_tokens)
                and strike_text.replace(".", "") in haystack.replace(".", "")):
            return exchange, row.get("tsym"), str(row.get("token") or "")
    return None
