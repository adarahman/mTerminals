"""Adapters from existing broker payloads to the canonical quote model."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional

from core.domain import Instrument, Quote
from core.enums import Exchange, InstrumentType


_BSE_INDICES = frozenset({"SENSEX", "BANKEX", "SENSEX50"})


def _decimal(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _integer(value) -> int:
    parsed = _decimal(value)
    return int(parsed) if parsed is not None else 0


def quote_from_legacy(
    symbol: str,
    payload: Mapping[str, object] | None,
    *,
    exchange: Exchange | None = None,
    token: str | None = None,
    timestamp: datetime | None = None,
) -> Quote | None:
    """Convert the established ``{'ltp', 'open', ...}`` broker shape.

    Invalid or missing LTP remains ``None``, matching the legacy callers'
    existing "quote unavailable" behavior. No provider SDK is imported here.
    """
    if not payload:
        return None
    last_price = _decimal(payload.get("ltp"))
    if last_price is None:
        return None

    normalized_symbol = symbol.strip().upper()
    resolved_exchange = exchange or (
        Exchange.BSE if normalized_symbol in _BSE_INDICES else Exchange.NSE
    )
    instrument = Instrument(
        symbol=normalized_symbol,
        exchange=resolved_exchange,
        instrument_type=InstrumentType.INDEX,
        token=token,
    )
    return Quote(
        instrument=instrument,
        timestamp=timestamp or datetime.now(timezone.utc),
        last_price=last_price,
        bid_price=_decimal(payload.get("best_bid") or payload.get("bid")),
        ask_price=_decimal(payload.get("best_ask") or payload.get("ask")),
        open_price=_decimal(payload.get("open")),
        high_price=_decimal(payload.get("high")),
        low_price=_decimal(payload.get("low")),
        previous_close=_decimal(payload.get("close")),
        volume=_integer(payload.get("volume")),
        open_interest=_integer(payload.get("oi")),
    )
