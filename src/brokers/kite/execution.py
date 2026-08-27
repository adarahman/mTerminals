"""Kite Connect (Zerodha) execution adapter — brokers.kite.client,
shaped for server/app.py's EXECUTION_BROKER dispatch.

kite_client.py deliberately stays standalone and config.py-independent
(see its own module docstring), so this adapter is the seam: it pulls
the access token from config.settings, hands it to kite_client's
module-level KiteSession, and re-shapes calls to match the SAME
signature smartapi_client.py / shoonya_client.py / upstox_execution_adapter.py
already share —

    place_order(tradingsymbol, symboltoken, exchange, transaction_type,
                quantity, order_type="MARKET", product_type=None,
                price=0.0, variety="NORMAL", order_tag=None) -> str
    get_order_book() -> list
    get_positions() -> list
    get_funds() -> dict

so server/app.py can add an EXECUTION_BROKER == "KITE" branch that
imports these four names exactly like its existing SMARTAPI/SHOONYA/
UPSTOX branches do, with no changes needed anywhere else in that
dispatch.

Token refresh: like Upstox, there is no headless daily login here (see
kite_client.py's docstring — browser-redirect + request_token exchange
only, no TOTP path). ensure_session() below raises a clear BrokerError
if settings.kite_access_token is unset; it does NOT attempt to mint
one. Whatever process pastes a fresh token each morning (cron, manual
step, a small ops script driving kite_client.login_url() /
generate_session()) is expected to update KITE_ACCESS_TOKEN / call
set_session_token() before market open — this module only consumes the
token, it doesn't produce one.

order_tag: kite_client.place_order() forwards this straight through to
KiteConnect's own `tag` param, which is exactly the identity
_submit_live_order()/_LIVE_ORDER_STORE (server/app.py) needs for
its own dedupe-on-uncertain-response recovery — same role AngelOne's
order tag and Shoonya's `remarks` play for their brokers. Unlike
Shoonya, this adapter does NOT try to recover an order by tag lookup
on failure: Kite's orders() response exposes `tag` per order, so that
recovery path could be added the same way shoonya_client._find_order_by_tag
does it, but it's left out here until a real double-submit-on-timeout
case against Kite is observed — no sense guessing at the matching logic
ahead of a concrete failure mode.
"""
from __future__ import annotations

import logging
import threading
from core.errors import BrokerError

try:  # ws_server_live adds backend/ to sys.path; package-level tests do not.
    from infrastructure.config import settings
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from infrastructure.config import settings

from core.errors import KiteError
from brokers.kite.client import (
    set_session_token as _kite_set_session_token,
    place_order as _kite_place_order,
    get_order_book as _kite_get_order_book,
    get_positions as _kite_get_positions,
    get_funds as _kite_get_funds,
)

logger = logging.getLogger(__name__)


_token_lock = threading.RLock()
_token_applied = False


def set_session_token(token: str) -> None:
    """Push a freshly-minted token into kite_client's module-level
    session. Call this after a manual/automated daily re-login instead
    of restarting the process — kite_client._session is a singleton
    shared by every function in that module, so this updates all of
    them at once. Matches the set_session_token() convention already
    used for Upstox."""
    with _token_lock:
        _kite_set_session_token(token)
        global _token_applied
        _token_applied = True
    logger.info("[kite_execution_adapter] session token updated")


def ensure_session() -> None:
    """Applies settings.kite_access_token to kite_client's session
    exactly once per process (or again after set_session_token() is
    called explicitly) — matches this codebase's ensure_session()
    convention. There is no login() call to make here: Kite tokens are
    minted out-of-band via the browser-redirect flow (see
    kite_client.py's docstring)."""
    global _token_applied
    with _token_lock:
        if _token_applied:
            return
        if not settings.kite_access_token:
            raise BrokerError(
                "Missing Kite settings: KITE_ACCESS_TOKEN. Kite has no "
                "unattended daily login (see kite_client.py's docstring) — "
                "generate today's access_token via kite_client.login_url() "
                "+ generate_session(), then set KITE_ACCESS_TOKEN or call "
                "kite_execution_adapter.set_session_token()."
            )
        _kite_set_session_token(settings.kite_access_token)
        _token_applied = True


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
    """symboltoken is accepted for call-site symmetry with the SmartAPI/
    Shoonya/Upstox signature but NOT used to place the order — Kite
    identifies the contract via exchange + tradingsymbol (see
    kite_client.place_order's docstring), same reasoning
    shoonya_client.place_order() gives for ignoring `symboltoken` there.
    `variety` is likewise accepted-but-unused: kite_client.place_order()
    always submits kite.VARIETY_REGULAR, matching what the other three
    brokers' shared signature actually exposes today."""
    ensure_session()
    try:
        return _kite_place_order(
            tradingsymbol=tradingsymbol,
            symboltoken=symboltoken,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=order_type,
            product_type=product_type,
            price=price,
            variety=variety,
            order_tag=order_tag,
        )
    except KiteError as exc:
        raise BrokerError(f"Kite place_order failed: {exc}") from exc


def get_order_book():
    ensure_session()
    try:
        return _kite_get_order_book()
    except KiteError as exc:
        raise BrokerError(f"Kite get_order_book failed: {exc}") from exc


def get_positions():
    ensure_session()
    try:
        return _kite_get_positions()
    except KiteError as exc:
        raise BrokerError(f"Kite get_positions failed: {exc}") from exc


def get_funds():
    ensure_session()
    try:
        return _kite_get_funds()
    except KiteError as exc:
        raise BrokerError(f"Kite get_funds failed: {exc}") from exc
