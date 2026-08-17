"""Shoonya account and execution adapter.

The dashboard continues to use its established SmartAPI market-data feed.
This module implements the smaller broker boundary needed for live account
state and execution, using Shoonya's official Noren Python API.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
from datetime import datetime

import pyotp

try:  # ws_server_live adds backend/ to sys.path; package-level tests do not.
    from config import settings
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from backend.config import settings

logger = logging.getLogger(__name__)


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

    @property
    def api(self):
        self.ensure_session()
        return self._api

    def ensure_session(self):
        with self._lock:
            if self._api is not None:
                return self._api
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
                result = api.login(
                    userid=settings.shoonya_user_id,
                    password=settings.shoonya_password,
                    twoFA=two_fa,
                    vendor_code=settings.shoonya_vendor_code,
                    api_secret=settings.shoonya_api_secret,
                    imei=settings.shoonya_imei,
                )
            except Exception as exc:
                raise BrokerError(f"Shoonya login failed: {exc}") from exc
            if not result or result.get("stat") != "Ok":
                raise BrokerError(f"Shoonya login rejected: {(result or {}).get('emsg', 'unknown error')}")
            self._api = api
            logger.info("[shoonya_client] Logged in, session established")
            return api


_session = ShoonyaSession()


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
        result = _session.api.place_order(
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
    normalized = []
    for row in _rows(_session.api.get_order_book()):
        item = dict(row)
        item.setdefault("orderid", _order_id(row))
        item.setdefault("ordertag", row.get("remarks"))
        item.setdefault("tradingsymbol", row.get("tsym"))
        item.setdefault("orderstatus", row.get("status"))
        normalized.append(item)
    return normalized


def get_positions():
    normalized = []
    for row in _rows(_session.api.get_positions()):
        item = dict(row)
        item.setdefault("tradingsymbol", row.get("tsym"))
        realized = float(row.get("rpnl") or 0)
        unrealized = float(row.get("urmtom") or row.get("unrpnl") or 0)
        item.setdefault("pnl", realized + unrealized)
        normalized.append(item)
    return normalized


def get_funds():
    data = _session.api.get_limits() or {}
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
