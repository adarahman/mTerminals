"""Read-side adapters for legacy execution and portfolio records."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from core.domain import Instrument, Order, Position
from core.enums import (
    Exchange,
    InstrumentType,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
)


_BSE_UNDERLYINGS = frozenset({"SENSEX", "BANKEX", "SENSEX50"})
_INSTRUMENT_TYPES = {
    "CE": InstrumentType.OPTION,
    "PE": InstrumentType.OPTION,
    "FUT": InstrumentType.FUTURE,
    "EQ": InstrumentType.EQUITY,
    "INDEX": InstrumentType.INDEX,
}
_ORDER_STATUSES = {
    "PENDING": OrderStatus.PENDING,
    "FILLED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
}


def _decimal(value, *, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    return result if result.is_finite() else default


def _datetime_from_epoch(value) -> datetime | None:
    parsed = _decimal(value)
    if parsed is None:
        return None
    try:
        return datetime.fromtimestamp(float(parsed), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _expiry(value) -> datetime | None:
    if not value:
        return None
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip().upper(), fmt)
        except ValueError:
            continue
    raise ValueError(f"unsupported execution expiry {value!r}")


def _instrument(record: Mapping[str, object]) -> Instrument:
    symbol = str(record.get("symbol") or "").strip().upper()
    kind = str(record.get("instrument_type") or "").strip().upper()
    if not symbol or kind not in _INSTRUMENT_TYPES:
        raise ValueError("execution record has an invalid symbol or instrument type")
    is_derivative = kind in {"CE", "PE", "FUT"}
    is_bse = symbol in _BSE_UNDERLYINGS
    exchange = (
        Exchange.BFO if is_bse else Exchange.NFO
    ) if is_derivative else (Exchange.BSE if is_bse else Exchange.NSE)
    strike = _decimal(record.get("strike")) if kind in {"CE", "PE"} else None
    if kind in {"CE", "PE"} and (strike is None or strike <= 0):
        raise ValueError("option execution record has an invalid strike")
    return Instrument(
        symbol=symbol,
        exchange=exchange,
        instrument_type=_INSTRUMENT_TYPES[kind],
        expiry=_expiry(record.get("expiry")),
        strike=strike,
        option_type=OptionType(kind) if kind in {"CE", "PE"} else None,
    )


def _quantity(lots, lot_size: int) -> int:
    parsed = _decimal(lots)
    if parsed is None or parsed != parsed.to_integral_value():
        raise ValueError("execution quantity must be a whole number of lots")
    multiplier = int(lot_size)
    if multiplier <= 0:
        raise ValueError("lot size must be positive")
    return int(parsed) * multiplier


def order_from_paper_record(
    record: Mapping[str, object], *, lot_size: int
) -> Order:
    """Convert a paper order row without importing the SQLite adapter."""
    side_raw = str(record.get("side") or "").strip().upper()
    type_raw = str(record.get("order_type") or "").strip().upper()
    status_raw = str(record.get("status") or "").strip().upper()
    try:
        side = OrderSide(side_raw)
        order_type = OrderType(type_raw)
        status = _ORDER_STATUSES[status_raw]
    except (ValueError, KeyError) as exc:
        raise ValueError("paper order has an unsupported side, type, or status") from exc
    return Order(
        instrument=_instrument(record),
        side=side,
        quantity=_quantity(record.get("qty_lots"), lot_size),
        order_type=order_type,
        price=_decimal(record.get("limit_price")),
        status=status,
        order_id=str(record.get("id")) if record.get("id") is not None else None,
        created_at=_datetime_from_epoch(record.get("timestamp")),
        filled_price=_decimal(record.get("fill_price")),
        filled_at=_datetime_from_epoch(record.get("fill_timestamp")),
        rejection_reason=(
            str(record.get("reject_reason"))
            if record.get("reject_reason") is not None
            else None
        ),
    )


def position_from_paper_record(
    record: Mapping[str, object], *, lot_size: int
) -> Position:
    """Convert a paper position row, expanding signed lots into units."""
    return Position(
        instrument=_instrument(record),
        quantity=_quantity(record.get("net_qty_lots"), lot_size),
        average_price=_decimal(record.get("avg_price"), default=Decimal("0")),
        unrealized_pnl=_decimal(
            record.get("unrealized_pnl"), default=Decimal("0")
        ),
        realized_pnl=_decimal(record.get("realized_pnl"), default=Decimal("0")),
        last_price=_decimal(record.get("last_price")),
    )
