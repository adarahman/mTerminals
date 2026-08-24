"""Adapters from broker resolution results to canonical instruments."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from core.domain import Instrument
from core.enums import Exchange, InstrumentType, OptionType


_INSTRUMENT_TYPES = {
    "CE": InstrumentType.OPTION,
    "PE": InstrumentType.OPTION,
    "FUT": InstrumentType.FUTURE,
    "EQ": InstrumentType.EQUITY,
    "INDEX": InstrumentType.INDEX,
}

_EXPIRY_FORMATS = ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d")


def _expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().upper()
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError(f"unsupported instrument expiry {value!r}")


def _strike(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid instrument strike {value!r}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"invalid instrument strike {value!r}")
    return parsed


def instrument_from_execution_resolution(
    underlying: str,
    instrument_type: str,
    expiry: str | None,
    strike,
    resolved,
) -> Instrument:
    """Build an ``Instrument`` from ``(exchange, trading_symbol, token)``.

    The tuple is the established execution-resolver contract. Keeping its
    interpretation here prevents server and application code from learning
    each broker's identifier vocabulary.
    """
    if not isinstance(resolved, (tuple, list)) or len(resolved) != 3:
        raise ValueError("instrument resolution must contain exchange, symbol, token")
    exchange_raw, trading_symbol, token = resolved
    kind = str(instrument_type or "").strip().upper()
    if kind not in _INSTRUMENT_TYPES:
        raise ValueError(f"unsupported instrument type {instrument_type!r}")
    if not trading_symbol or token in (None, ""):
        raise ValueError("instrument resolution returned an empty symbol or token")
    try:
        exchange = Exchange(str(exchange_raw).strip().upper())
    except ValueError as exc:
        raise ValueError(f"unsupported instrument exchange {exchange_raw!r}") from exc

    is_option = kind in {"CE", "PE"}
    return Instrument(
        symbol=underlying.strip().upper(),
        exchange=exchange,
        instrument_type=_INSTRUMENT_TYPES[kind],
        token=str(token),
        trading_symbol=str(trading_symbol),
        expiry=_expiry(expiry),
        strike=_strike(strike) if is_option else None,
        option_type=OptionType(kind) if is_option else None,
    )
