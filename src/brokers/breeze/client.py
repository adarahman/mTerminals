"""ICICI Breeze account and execution adapter.

Same broker-neutral function shape as brokers/shoonya_client.py — this is
the second implementation of that pattern, not a new one:
place_order/get_order_book/get_positions/get_funds all take/return the
same shapes ws_server_live.py and risk/account_guard.py, risk/
position_reconciler.py already consume from Shoonya. Swapping
EXECUTION_BROKER=BREEZE in .env is the only wiring change needed at the
call site (see ws_server_live.py's broker dispatch block).

Breeze's contract model doesn't fit that shape natively, though. SmartAPI
and Shoonya both resolve a contract to (exchange, tradingsymbol, token)
where tradingsymbol is a real, self-contained exchange symbol string.
Breeze instead wants four separate fields on every call — stock_code
(ICICI's own short code, NOT the NSE tradingsymbol), expiry_date (ISO8601,
not "DD-Mon-YYYY"), strike_price, and right ("Call"/"Put") — and has no
single string that encodes all four. resolve_option_contract() below
still returns the same 3-tuple shoonya_client.resolve_option_contract()
does (so _resolve_live_order_token() in ws_server_live.py needs no
branching beyond picking which resolver to call), but the "tradingsymbol"
it returns is a synthetic, display-shaped key (e.g. "NIFTY28AUG25024800CE")
whose four Breeze fields are stashed in an in-memory _CONTRACT_CACHE at
resolve time and looked back up by that same key inside place_order(). A
cache miss (place_order called with a key this process never resolved)
fails closed with BrokerError rather than guessing at the contract.

The synthetic key deliberately starts with the plain underlying symbol
("NIFTY", "BANKNIFTY", ...) because risk/account_guard.py's
projected_open_lots_from_positions() and risk/position_reconciler.py's
_resolve_lot_size() both key PT_LOT_SIZES by str.startswith(underlying) —
same reason Shoonya's real tsym ("NIFTY28AUG25024800CE") already works
for those two modules unmodified. Get_order_book()/get_positions() below
normalize Breeze's raw field names into that same tradingsymbol shape.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from core.errors import BrokerError

try:  # ws_server_live adds backend/ to sys.path; package-level tests do not.
    from infrastructure.config import settings
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from infrastructure.config import settings

logger = logging.getLogger(__name__)

# Breeze uses different identifiers for some BSE cash indices and their BFO
# derivatives. The official SDK's BFO SENSEX examples use BSESEN, not the
# cash quote code SENSEX.
_BFO_DERIVATIVE_STOCK_CODES = {"SENSEX": "BSESEN"}


def derivative_stock_code(symbol: str, exchange: str = "NFO") -> str:
    """Translate a dashboard underlying to Breeze's F&O stock code."""
    symbol = (symbol or "").strip().upper()
    return _BFO_DERIVATIVE_STOCK_CODES.get(symbol, symbol) if exchange.upper() == "BFO" else symbol


def _default_api_factory():
    # breeze_connect's own package contains a module also named `config`
    # (it does a bare `import config` internally, expecting its own bundled
    # module). Since ws_server_live.py adds backend/ to sys.path so this
    # codebase's own config.py can be imported as `from config import
    # settings`, that same sys.path entry causes breeze_connect's `import
    # config` to resolve to OUR config.py instead of its own — surfacing
    # as `AttributeError: module 'config' has no attribute
    # 'SECURITY_MASTER_URL'` at breeze_connect import time. Temporarily
    # scrub backend/ from sys.path and drop our cached `config` module
    # from sys.modules for the duration of this import so breeze_connect
    # resolves its own bundled config instead.
    import sys
    from pathlib import Path
    saved_config_module = sys.modules.pop("config", None)
    src_dir = str(Path(__file__).resolve().parents[2])
    removed_paths = [p for p in sys.path if str(Path(p).resolve()) == src_dir]
    for p in removed_paths:
        sys.path.remove(p)
    try:
        try:
            from breeze_connect import BreezeConnect
        except ImportError as exc:
            raise BrokerError(
                "Breeze SDK is not installed. Run `pip install breeze-connect`."
            ) from exc
        # The SDK pins its internal loggers to DEBUG at import time and lets
        # them propagate to the root logger, so every get_quotes()/
        # get_option_chain_quotes() call dumps its full JSON response to the
        # console on every tick. Override to WARNING so only real errors
        # surface; the SDK still writes its own apiLogs.log / websocketLogs.log
        # files regardless.
        for sdk_logger_name in ("APILogger", "WebsocketLogger"):
            sdk_logger = logging.getLogger(sdk_logger_name)
            sdk_logger.setLevel(logging.WARNING)
    finally:
        sys.path.extend(removed_paths)
        if saved_config_module is not None:
            sys.modules["config"] = saved_config_module
    return BreezeConnect(api_key=settings.breeze_api_key)


class BreezeSession:
    """Lazily authenticate one Breeze SDK client.

    A Breeze session token is static process configuration and expires daily.
    Retrying ``generate_session`` with the same rejected token for every
    market-data call only creates request/log storms; cache that terminal
    failure until the process is restarted with a refreshed token.
    """

    def __init__(self, api_factory=None):
        self._api_factory = api_factory or _default_api_factory
        self._api = None
        self._lock = threading.RLock()
        self._session_error: BrokerError | None = None
        # Keep the credentials that produced the current client.  A Breeze
        # session expires daily and operators commonly refresh it in the
        # running process' settings during recovery; do not keep serving the
        # old terminal error after that value has changed.
        self._credentials: tuple[str | None, str | None, str | None] | None = None

    @property
    def api(self):
        self.ensure_session()
        return self._api

    def ensure_session(self):
        with self._lock:
            credentials = (
                settings.breeze_api_key,
                settings.breeze_api_secret,
                settings.breeze_api_session,
            )
            if self._credentials != credentials:
                self._api = None
                self._session_error = None
                self._credentials = credentials
            if self._api is not None:
                return self._api
            if self._session_error is not None:
                raise self._session_error
            required = {
                "BREEZE_API_KEY": settings.breeze_api_key,
                "BREEZE_API_SECRET": settings.breeze_api_secret,
                "BREEZE_API_SESSION": settings.breeze_api_session,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise BrokerError(
                    "Missing Breeze settings: " + ", ".join(missing)
                    + " (BREEZE_API_SESSION expires daily — see config.py's"
                      " comment on how to refresh it)"
                )
            api = self._api_factory()
            try:
                api.generate_session(
                    api_secret=settings.breeze_api_secret,
                    session_token=settings.breeze_api_session,
                )
            except Exception as exc:
                self._session_error = BrokerError(
                    "Breeze session generation failed: "
                    f"{exc}. Refresh BREEZE_API_SESSION and restart the service."
                )
                raise self._session_error from exc
            self._api = api
            logger.info("[breeze_client] Session generated")
            return api

    def reset(self) -> None:
        """Forget the current SDK client and authenticate again on next use."""
        with self._lock:
            self._api = None
            self._session_error = None
            self._credentials = None


_session = BreezeSession()


def healthcheck() -> tuple[bool, str | None]:
    """Verify that the configured ICICI session can serve requests now."""
    try:
        _session.ensure_session()
    except Exception as exc:
        return False, str(exc)
    return True, None

# resolve_option_contract() -> place_order() handoff cache. See module
# docstring. Not persisted across process restarts, same lifetime as the
# rest of this module's session state.
_CONTRACT_CACHE: dict[str, dict] = {}
_CONTRACT_CACHE_LOCK = threading.Lock()


def _unwrap(result, action):
    """Breeze responses are always {'Success', 'Status', 'Error'}. Raise on
    anything that isn't a clean 200, same fail-closed posture as Shoonya's
    _rows()/stat check."""
    if not isinstance(result, dict):
        raise BrokerError(f"Breeze {action} returned an unexpected response: {result!r}")
    if result.get("Error"):
        raise BrokerError(f"Breeze {action} rejected: {result['Error']}")
    if result.get("Status") not in (200, "200", None):
        raise BrokerError(f"Breeze {action} failed (status={result.get('Status')}): {result}")
    return result.get("Success")


def _iso_expiry(expiry_ddmmmyyyy: str) -> str:
    """'28-Aug-2025' (this codebase's option_chain_json expiry format) ->
    Breeze's '2025-08-28T06:00:00.000Z'. The 06:00:00Z suffix is what
    Breeze's own docs/examples use for IST midnight-of-expiry-day; Breeze
    ignores the time-of-day component for expiry matching."""
    dt = datetime.strptime(expiry_ddmmmyyyy, "%d-%b-%Y")
    return dt.strftime("%Y-%m-%dT06:00:00.000Z")


def _find_order_by_tag(tag):
    for row in get_order_book():
        if str(row.get("ordertag") or "") == tag:
            return row.get("orderid")
    return None


def resolve_option_contract(symbol, expiry, strike, option_type, exchange="NFO"):
    """Build the synthetic (exchange, tradingsymbol, token) 3-tuple
    place_order() below expects, and cache the real Breeze fields behind
    it. `symbol` must already be Breeze's stock_code (ICICI's short code,
    e.g. "CNXBAN" for BANKNIFTY) — NOT the NSE symbol — since this module
    doesn't maintain its own get_names() symbol map; callers resolving
    from NSE-style underlyings should map through
    breeze_market_data.py's stock-code cache first.
    """
    exchange_code = (exchange or "NFO").upper()
    if exchange_code not in {"NFO", "BFO"}:
        return None
    try:
        symbol = derivative_stock_code(symbol, exchange_code)
        expiry_iso = _iso_expiry(expiry)
        strike_float = float(strike)
    except (TypeError, ValueError):
        return None
    right = "Call" if option_type.upper() == "CE" else "Put"
    expiry_compact = datetime.strptime(expiry, "%d-%b-%Y").strftime("%d%b%y").upper()
    strike_text = f"{strike_float:g}"
    key = f"{symbol}{expiry_compact}{strike_text}{option_type.upper()}"
    with _CONTRACT_CACHE_LOCK:
        _CONTRACT_CACHE[key] = {
            "stock_code": symbol,
            "exchange_code": exchange_code,
            "expiry_date": expiry_iso,
            "strike_price": strike_text,
            "right": right,
            "product": "options",
        }
    return exchange_code, key, ""


def place_order(tradingsymbol, symboltoken, exchange, transaction_type,
                quantity, order_type="MARKET", product_type=None, price=0.0,
                variety="NORMAL", order_tag=None):
    """Place one Breeze order using the dashboard's broker-neutral shape.

    `tradingsymbol` must be a key previously returned by
    resolve_option_contract() in this same process — see module
    docstring. Breeze itself has no MARKET order type (SEBI algo rules);
    passing order_type="MARKET" is submitted as Breeze's "market" order
    type, which Breeze's own backend converts to an aggressive limit order
    at submission time, not this module.
    """
    del symboltoken, variety  # Breeze identifies the contract by the 4 fields cached below.
    with _CONTRACT_CACHE_LOCK:
        contract = _CONTRACT_CACHE.get(tradingsymbol)
    if contract is None:
        raise BrokerError(
            f"no cached Breeze contract for {tradingsymbol!r} — "
            "resolve_option_contract() must be called in this process first"
        )

    tag = order_tag
    if tag:
        try:
            existing = _find_order_by_tag(tag)
        except Exception as exc:
            raise BrokerError(f"cannot verify order tag {tag} before placement: {exc}") from exc
        if existing:
            return str(existing)

    action = "buy" if transaction_type.upper() == "BUY" else "sell"
    breeze_order_type = "market" if order_type.upper() == "MARKET" else "limit"
    try:
        result = _session.api.place_order(
            stock_code=contract["stock_code"],
            exchange_code=contract["exchange_code"],
            product=product_type or contract["product"],
            action=action,
            order_type=breeze_order_type,
            stoploss="0",
            quantity=str(int(quantity)),
            price="0" if breeze_order_type == "market" else str(price),
            validity="day",
            expiry_date=contract["expiry_date"],
            right=contract["right"],
            strike_price=contract["strike_price"],
            user_remark=tag,
        )
    except Exception as exc:
        existing = _find_order_by_tag(tag) if tag else None
        if existing:
            return str(existing)
        raise BrokerError(f"Breeze place_order failed: {exc}") from exc

    try:
        success = _unwrap(result, "place_order")
    except BrokerError:
        existing = _find_order_by_tag(tag) if tag else None
        if existing:
            return str(existing)
        raise
    if not success or not success.get("order_id"):
        existing = _find_order_by_tag(tag) if tag else None
        if existing:
            return str(existing)
        raise BrokerError(f"Breeze place_order rejected: {result}")
    return str(success["order_id"])


def get_order_book():
    """Today's order list, normalized to the same field names
    shoonya_client.get_order_book()/smartapi_client.get_order_book()
    already produce (orderid, ordertag, tradingsymbol, orderstatus,
    transactiontype, filledshares) so risk/position_reconciler.py and
    risk/account_guard.py work against either broker unmodified."""
    today = datetime.now(timezone.utc)
    from_date = today.strftime("%Y-%m-%dT00:00:00.000Z")
    to_date = today.strftime("%Y-%m-%dT23:59:59.000Z")
    result = _session.api.get_order_list(
        exchange_code="NFO", from_date=from_date, to_date=to_date,
    )
    rows = _unwrap(result, "get_order_list") or []
    normalized = []
    for row in rows:
        item = dict(row)
        item.setdefault("orderid", row.get("order_id"))
        item.setdefault("ordertag", row.get("user_remark"))
        item.setdefault("tradingsymbol", row.get("stock_code"))
        item.setdefault("orderstatus", row.get("status"))
        item.setdefault("transactiontype", str(row.get("action") or "").upper())
        item.setdefault("filledshares", row.get("quantity"))
        item.setdefault("updatetime", row.get("order_datetime"))
        normalized.append(item)
    return normalized


def get_positions():
    """Live open F&O positions, normalized the same way
    shoonya_client.get_positions() is (tradingsymbol, netqty, pnl)."""
    result = _session.api.get_portfolio_positions()
    rows = _unwrap(result, "get_portfolio_positions") or []
    normalized = []
    for row in rows:
        item = dict(row)
        item.setdefault("tradingsymbol", row.get("stock_code"))
        try:
            item.setdefault("netqty", int(float(row.get("quantity") or 0)))
        except (TypeError, ValueError):
            item.setdefault("netqty", 0)
        realized = float(row.get("realized_profit") or 0)
        unrealized = float(row.get("unrealized_profit") or row.get("mtm") or 0)
        item.setdefault("pnl", realized + unrealized)
        normalized.append(item)
    return normalized


def get_funds():
    result = _session.api.get_funds()
    data = _unwrap(result, "get_funds") or {}

    def number(*keys):
        for key in keys:
            if data.get(key) not in (None, ""):
                try:
                    return float(data[key])
                except (TypeError, ValueError):
                    pass
        return 0.0

    cash = number("cash_limit")
    allocated = number("amount_allocated")
    blocked = number("block_by_trade")
    return {
        "available_cash": cash,
        "available_margin": cash - blocked,
        "available_intraday_payin": 0.0,
        "available_limit_margin": allocated,
        "collateral": number("isec_margin"),
        "utilised_margin": blocked,
        "utilised_span": 0.0,
        "utilised_exposure": 0.0,
        "m2m_unrealized": 0.0,
        "m2m_realized": 0.0,
    }
