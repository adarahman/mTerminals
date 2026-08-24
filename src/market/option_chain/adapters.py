"""Normalize existing provider chain payloads into domain objects."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping

from core.domain import Instrument, OptionChain, OptionContract
from core.enums import Exchange, InstrumentType, OptionType


_BSE_UNDERLYINGS = frozenset({"SENSEX", "BANKEX", "SENSEX50"})
_EXPIRY_FORMATS = ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d")


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _integer(value) -> int:
    parsed = _decimal(value)
    return int(parsed) if parsed is not None else 0


def _expiry(value) -> datetime:
    normalized = str(value or "").strip().upper()
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError(f"unsupported option-chain expiry {value!r}")


def option_chain_from_legacy(
    payload: Mapping[str, object] | None,
    *,
    exchange: Exchange | None = None,
) -> OptionChain | None:
    """Convert the providers' normalized ``get_atm_chain`` dictionary.

    Bad individual rows are skipped, matching the current providers' partial
    chain behavior. A missing chain remains ``None``; malformed chain-level
    identity or expiry fails explicitly.
    """
    if not payload:
        return None
    underlying_symbol = str(payload.get("underlying") or "").strip().upper()
    if not underlying_symbol:
        raise ValueError("option chain is missing its underlying")
    expiry = _expiry(payload.get("expiry"))
    cash_exchange = (
        Exchange.BSE if underlying_symbol in _BSE_UNDERLYINGS else Exchange.NSE
    )
    derivatives_exchange = exchange or (
        Exchange.BFO if cash_exchange is Exchange.BSE else Exchange.NFO
    )
    underlying = Instrument(
        symbol=underlying_symbol,
        exchange=cash_exchange,
        instrument_type=InstrumentType.INDEX,
    )

    contracts: list[OptionContract] = []
    rows = payload.get("rows") or []
    if not isinstance(rows, (list, tuple)):
        raise ValueError("option chain rows must be a sequence")
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        strike = _decimal(row.get("strike"))
        side = str(row.get("type") or "").strip().upper()
        if strike is None or strike <= 0 or side not in {"CE", "PE"}:
            continue
        trading_symbol = row.get("tradingsymbol") or row.get("trading_symbol")
        token = row.get("token") or row.get("instrument_key")
        instrument = Instrument(
            symbol=underlying_symbol,
            exchange=derivatives_exchange,
            instrument_type=InstrumentType.OPTION,
            token=str(token) if token not in (None, "") else None,
            trading_symbol=str(trading_symbol) if trading_symbol else None,
            expiry=expiry,
            strike=strike,
            option_type=OptionType(side),
        )
        contracts.append(
            OptionContract(
                instrument=instrument,
                strike=strike,
                option_type=OptionType(side),
                expiry=expiry,
                ltp=_decimal(row.get("ltp")),
                open_interest=_integer(row.get("oi")),
                volume=_integer(row.get("volume")),
                open_price=_decimal(row.get("open")),
                high_price=_decimal(row.get("high")),
                low_price=_decimal(row.get("low")),
                previous_close=_decimal(row.get("close")),
                net_change=_decimal(row.get("net_change")),
                percent_change=_decimal(row.get("pct_change")),
            )
        )

    return OptionChain(
        underlying=underlying,
        expiry=expiry,
        contracts=contracts,
        spot_price=_decimal(payload.get("spot")),
        atm_strike=_decimal(payload.get("atm_strike")),
    )
