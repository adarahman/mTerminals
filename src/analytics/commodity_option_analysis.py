"""Transparent, rule-based context for the visible MCX option-chain window."""

from __future__ import annotations

import math


def _finite_number(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def analyze_commodity_option_chain(chain: dict) -> dict:
    """Compare visible-window put/call OI with the underlying futures move."""
    rows = chain.get("rows") or []
    calls = [row for row in rows if row.get("type") == "CE"]
    puts = [row for row in rows if row.get("type") == "PE"]

    def total(contracts, key):
        values = [
            value
            for value in (_finite_number(contract.get(key)) for contract in contracts)
            if value is not None
        ]
        return round(sum(values), 2) if values else None

    def wall(contracts):
        valid = [
            (value, contract.get("strike"))
            for contract in contracts
            if (value := _finite_number(contract.get("oi"))) is not None and value > 0
        ]
        return max(valid, key=lambda item: item[0])[1] if valid else None

    call_oi = total(calls, "oi")
    put_oi = total(puts, "oi")
    call_volume = total(calls, "volume")
    put_volume = total(puts, "volume")
    raw_pcr = put_oi / call_oi if call_oi and put_oi is not None else None
    pcr = round(raw_pcr, 2) if raw_pcr is not None else None
    futures_change = chain.get("future_change_pct")
    try:
        futures_change = float(futures_change)
    except (TypeError, ValueError):
        futures_change = None
    if futures_change is not None and not math.isfinite(futures_change):
        futures_change = None

    verdict = "WAIT"
    if raw_pcr is None or futures_change is None:
        reason = "Insufficient option OI or futures-change data to form a directional view."
    elif raw_pcr > 1 and futures_change > 0:
        verdict = "BULLISH"
        reason = "Put OI is higher than call OI and the underlying futures are up on the day."
    elif raw_pcr < 1 and futures_change < 0:
        verdict = "BEARISH"
        reason = "Call OI is higher than put OI and the underlying futures are down on the day."
    else:
        verdict = "NEUTRAL"
        reason = "Option positioning and the underlying futures move do not confirm the same direction."

    return {
        "verdict": verdict,
        "reason": reason,
        "put_call_oi_ratio": pcr,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_oi_wall": wall(calls),
        "put_oi_wall": wall(puts),
        "futures_change_pct": futures_change,
        "scope": "Visible ATM ±10 strikes; OI is current positioning, not OI change.",
        "method": (
            "Bullish: PCR > 1 and futures up; bearish: PCR < 1 and futures down; "
            "conflicting or flat: neutral; missing inputs: wait."
        ),
    }
