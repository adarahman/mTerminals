"""Upstox execution adapter — brokers.upstox.client, shaped for
server/app.py's EXECUTION_BROKER dispatch.

upstox_client.py deliberately stays standalone and config.py-independent
(see its own module docstring), so this adapter is the seam: it pulls
the access token from config.settings, hands it to a module-level
UpstoxSession, and re-shapes calls to match the SAME signature
smartapi_client.py and shoonya_client.py already share —

    place_order(tradingsymbol, symboltoken, exchange, transaction_type,
                quantity, order_type="MARKET", product_type=None,
                price=0.0, variety="NORMAL", order_tag=None) -> str
    get_order_book() -> list
    get_positions() -> list
    get_funds() -> dict

so server/app.py can add an EXECUTION_BROKER == "UPSTOX" branch that
imports these four names exactly like its existing SMARTAPI/SHOONYA
branches do, with no changes needed anywhere else in that dispatch.

Token refresh: unlike SmartAPI/Shoonya, there is no headless daily
login here (see upstox_client.py's docstring — OAuth2 authorization-code
only). ensure_session() below raises a clear BrokerError if
settings.upstox_access_token is unset or stale; it does NOT attempt to
mint one. Whatever process pastes a fresh token each morning (cron,
manual step, a small ops script hitting exchange_code_for_token()) is
expected to update UPSTOX_ACCESS_TOKEN / call set_session_token() before
market open — this module only consumes the token, it doesn't produce one.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

try:  # ws_server_live adds backend/ to sys.path; package-level tests do not.
    from infrastructure.config import settings
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from infrastructure.config import settings

# NOTE: this used to be a bare `from upstox_client import (...)`. That's
# ambiguous the moment the official Upstox Python SDK is installed
# (pip install upstox-python-sdk — needed for upstox_ws_client.py's live
# tick feed): the SDK's own top-level package is ALSO named
# `upstox_client`, and it defines none of UpstoxError/UpstoxSession/
# PlaceOrderRequest — whichever `upstox_client` sys.path resolves first
# wins, and if it's the SDK, this import fails outright. Absolute
# `brokers.upstox.client` is unambiguous regardless of what else is
# importable as bare `upstox_client`, matching how every other cross-module
# import in this codebase's brokers/ package already resolves siblings
# (e.g. server/app.py's `from brokers.smartapi.client import ...`).
from core.errors import UpstoxError
from brokers.upstox.client import (
    UpstoxSession,
    PlaceOrderRequest,
    place_order as _upstox_place_order,
    get_order_book as _upstox_get_order_book,
    get_positions as _upstox_get_positions,
    get_funds as _upstox_get_funds,
    _session as _module_session,  # upstox_client's own module-level session
)

logger = logging.getLogger(__name__)

from core.errors import BrokerError


_token_lock = threading.RLock()
_token_applied = False


def set_session_token(token: str) -> None:
    """Push a freshly-minted token into upstox_client's module-level
    session. Call this after a manual/automated daily re-login instead
    of restarting the process — upstox_client._session is a singleton
    shared by every function in that module, so this updates all of them
    at once."""
    with _token_lock:
        _module_session.set_token(token)
        global _token_applied
        _token_applied = True
    logger.info("[upstox_execution_adapter] session token updated")


def ensure_session() -> None:
    """Applies settings.upstox_access_token to upstox_client's session
    exactly once per process (or again after set_session_token() is
    called explicitly) — matches this codebase's ensure_session()
    convention (see ShoonyaSession.ensure_session()), except there is no
    login() call to make here: Upstox tokens are minted out-of-band."""
    global _token_applied
    with _token_lock:
        if _token_applied:
            return
        if not settings.upstox_access_token:
            raise BrokerError(
                "Missing Upstox settings: UPSTOX_ACCESS_TOKEN. Upstox has no "
                "unattended daily login (see upstox_client.py's docstring) — "
                "generate today's token via build_login_url() / the app "
                "dashboard's Generate button, then set UPSTOX_ACCESS_TOKEN "
                "or call upstox_execution_adapter.set_session_token()."
            )
        _module_session.set_token(settings.upstox_access_token)
        _token_applied = True


# Broker-neutral order type / product mapping. This codebase's shared
# place_order() signature uses SmartAPI's vocabulary
# (order_type: MARKET|LIMIT, product_type: INTRADAY|DELIVERY|MARGIN|
# CARRYFORWARD) since that's the caller-facing contract every broker
# module here is adapted to. Upstox's own vocabulary differs (product:
# D|I|CO|MTF, order_type also allows SL/SL-M which the shared signature
# has no slot for — intraday/delivery only, matching what the other two
# brokers actually expose).
_PRODUCT_TYPE_TO_UPSTOX = {
    "INTRADAY": "I",
    "DELIVERY": "D",
    "MARGIN": "D",
    "CARRYFORWARD": "D",
}


def place_order(
    tradingsymbol,
    symboltoken,
    exchange,
    transaction_type,
    quantity,
    order_type="MARKET",
    product_type="INTRADAY",
    price=0.0,
    variety="NORMAL",
    order_tag=None,
):
    """symboltoken here is expected to be Upstox's instrument_key string
    (not a numeric token — Upstox has no numeric identifier; see
    market_data.py's UpstoxMarketData for where that instrument_key
    comes from). `tradingsymbol`, `exchange`, and `variety` are accepted
    for call-site symmetry with the SmartAPI/Shoonya signature but are
    NOT used to place the order — Upstox identifies the contract purely
    by instrument_key, same reasoning shoonya_client.place_order() gives
    for ignoring `symboltoken` there (each broker keys off whichever
    identifier IT actually needs)."""
    del tradingsymbol, exchange, variety
    ensure_session()

    product = _PRODUCT_TYPE_TO_UPSTOX.get((product_type or "").upper(), "I")
    req = PlaceOrderRequest(
        instrument_key=symboltoken,
        quantity=int(quantity),
        transaction_type=transaction_type.upper(),
        product=product,
        order_type=order_type.upper(),
        price=float(price) if order_type.upper() == "LIMIT" else 0.0,
        tag=order_tag,
    )
    try:
        return _upstox_place_order(req)
    except UpstoxError as exc:
        raise BrokerError(f"Upstox place_order failed: {exc}") from exc


def get_order_book():
    ensure_session()
    try:
        return _upstox_get_order_book()
    except UpstoxError as exc:
        raise BrokerError(f"Upstox get_order_book failed: {exc}") from exc


def get_positions():
    ensure_session()
    try:
        return _upstox_get_positions()
    except UpstoxError as exc:
        raise BrokerError(f"Upstox get_positions failed: {exc}") from exc


def get_funds():
    ensure_session()
    try:
        return _upstox_get_funds()
    except UpstoxError as exc:
        raise BrokerError(f"Upstox get_funds failed: {exc}") from exc
