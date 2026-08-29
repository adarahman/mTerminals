"""Parsing and validation for untrusted order-submission payloads."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    instrument_type: str
    expiry: str
    side: str
    order_type: str
    qty_lots: int
    strike: float | None
    limit_price: float | None
    client_order_id: object
    raw: dict

    @property
    def wants_live(self) -> bool:
        return bool(self.raw.get("live")) and bool(self.raw.get("confirmed"))


def _finite_optional_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def parse_order_intent(payload: dict) -> OrderIntent:
    """Coerce malformed numbers to the sentinels validation rejects."""
    qty_value = _finite_optional_number(payload.get("qty_lots"))
    qty_lots = (
        int(qty_value)
        if qty_value is not None and qty_value.is_integer()
        else 0
    )
    return OrderIntent(
        symbol=(payload.get("symbol") or "").strip().upper(),
        instrument_type=str(payload.get("instrument_type") or "INDEX")
        .strip()
        .upper(),
        expiry=str(payload.get("expiry") or "").strip(),
        side=str(payload.get("side") or "").strip().upper(),
        order_type=str(payload.get("order_type") or "MARKET").strip().upper(),
        qty_lots=qty_lots,
        strike=_finite_optional_number(payload.get("strike")),
        limit_price=_finite_optional_number(payload.get("limit_price")),
        client_order_id=payload.get("client_order_id"),
        raw=payload,
    )


def validate_order_intent(intent: OrderIntent) -> str | None:
    """Return the rejection reason for malformed intent, otherwise None."""
    if not intent.symbol:
        return "symbol is required"
    if intent.side not in ("BUY", "SELL"):
        return f"unsupported side {intent.side or '(missing)'}"
    if intent.instrument_type not in ("CE", "PE", "FUT", "EQ", "INDEX"):
        return f"unsupported instrument_type {intent.instrument_type}"
    if intent.order_type not in ("MARKET", "LIMIT"):
        return f"unsupported order_type {intent.order_type}"
    if intent.qty_lots < 1:
        return "qty_lots must be a positive whole number"
    if intent.instrument_type in ("CE", "PE") and (
        not intent.expiry or intent.strike is None or intent.strike <= 0
    ):
        return "CE/PE orders require a valid expiry and positive strike"
    if intent.instrument_type == "FUT" and not intent.expiry:
        return "FUT orders require an expiry"
    if intent.order_type == "LIMIT" and (
        intent.limit_price is None or intent.limit_price <= 0
    ):
        return "LIMIT orders require a positive finite limit_price"
    return None
