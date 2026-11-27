"""Black-76 implied volatility and Greeks for MCX options on futures.

MCX options settle into the underlying futures contract, so they are priced
with Black-76: the shared Black-Scholes engine with the futures price as S
and carry q = r.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from application.payload_builders.common import nullable_rounded_number
from oi.chain_metrics import _build_greeks_table
from oi.pricing import ANNUAL_RISK_FREE_RATE, bs_call, bs_put, solve_iv

IST = ZoneInfo("Asia/Kolkata")
_IV_LOW, _IV_HIGH = 0.01, 5.0
_REPRICE_ABS_TOL = 1e-4


def _num(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _expiry_time(value):
    if value is None or str(value).strip() == "":
        value = os.getenv("MCX_OPTION_EXPIRY_TIME", "23:30")
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except ValueError:
        return None


def years_to_expiry(expiry, now=None, expiry_time=None):
    """Return year fraction to the configured contract expiry time (IST).

    A provider may supply `expiry_time` as HH:MM. Otherwise
    `MCX_OPTION_EXPIRY_TIME` is used, with 23:30 IST as the compatibility
    default. The effective time should match the specific contract's rules.
    """
    try:
        day = datetime.strptime(str(expiry).strip().upper(), "%d%b%Y")
    except ValueError:
        return None
    close_time = _expiry_time(expiry_time)
    if close_time is None:
        return None
    expires_at = day.replace(
        hour=close_time.hour,
        minute=close_time.minute,
        second=close_time.second,
        microsecond=close_time.microsecond,
        tzinfo=IST,
    )
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    seconds = (expires_at - current.astimezone(IST)).total_seconds()
    return seconds / (365.0 * 86400.0) if seconds > 0 else None


def black76_price(futures, strike, t, sigma, opt_type, r=ANNUAL_RISK_FREE_RATE):
    price_fn = bs_call if opt_type == "C" else bs_put
    return price_fn(futures, strike, t, r, sigma, r)


def black76_iv(price, futures, strike, t, opt_type, r=ANNUAL_RISK_FREE_RATE):
    """Implied volatility (decimal) or None when the premium can't be inverted."""
    if not all(v is not None and v > 0 for v in (price, futures, strike, t)):
        return None
    discount = math.exp(-r * t)
    intrinsic = discount * (max(futures - strike, 0.0) if opt_type == "C"
                            else max(strike - futures, 0.0))
    if price <= intrinsic:
        return None

    def model(sigma):
        return black76_price(futures, strike, t, sigma, opt_type, r)

    def close_enough(sigma):
        return abs(model(sigma) - price) <= _REPRICE_ABS_TOL

    sigma = solve_iv(price, futures, strike, t, r, opt_type, q=r)
    if _IV_LOW < sigma < _IV_HIGH and close_enough(sigma):
        return sigma
    lo, hi = _IV_LOW, _IV_HIGH
    if not model(lo) <= price <= model(hi):
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if model(mid) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-9:
            break
    sigma = (lo + hi) / 2
    return sigma if close_enough(sigma) else None


def _strike_value(strike):
    return int(strike) if float(strike).is_integer() else strike


def attach_commodity_greeks(chain, now=None, r=ANNUAL_RISK_FREE_RATE):
    """Add per-contract `iv` (%), `greeks` rows and `atm_iv` (%) to a chain."""
    chain["greeks"] = []
    chain["atm_iv"] = None
    futures = _num(chain.get("spot"))
    t = years_to_expiry(
        chain.get("expiry"), now, expiry_time=chain.get("expiry_time")
    )
    if not futures or futures <= 0 or not t:
        return chain

    by_strike = {}
    for row in chain.get("rows") or []:
        strike = _num(row.get("strike"))
        side = row.get("type")
        if not strike or side not in ("CE", "PE"):
            continue
        leg = side.lower()
        entry = by_strike.setdefault(
            strike, {"strike": strike, "ce_oi": 0.0, "pe_oi": 0.0, "ce_iv": None, "pe_iv": None}
        )
        entry[f"{leg}_oi"] = _num(row.get("oi")) or 0.0
        iv = black76_iv(_num(row.get("ltp")), futures, strike, t,
                        "C" if side == "CE" else "P", r)
        if iv is not None:
            row["iv"] = round(iv * 100, 2)
            entry[f"{leg}_iv"] = iv

    with_iv = [e for e in by_strike.values() if e["ce_iv"] or e["pe_iv"]]
    if not with_iv:
        return chain
    nearest = min(with_iv, key=lambda e: abs(e["strike"] - futures))
    atm_legs = [v for v in (nearest["ce_iv"], nearest["pe_iv"]) if v]
    atm_iv = sum(atm_legs) / len(atm_legs)
    chain["atm_iv"] = round(atm_iv * 100, 2)

    frame = pd.DataFrame([
        {
            "strike": e["strike"],
            "ce_oi": e["ce_oi"],
            "pe_oi": e["pe_oi"],
            # Percent units: _build_greeks_table reads values > 1 as percent.
            "ce_iv": 100 * (e["ce_iv"] or e["pe_iv"] or atm_iv),
            "pe_iv": 100 * (e["pe_iv"] or e["ce_iv"] or atm_iv),
        }
        for _, e in sorted(by_strike.items())
    ])
    lot_size = _num(chain.get("lot_size")) or 1
    table = _build_greeks_table(frame, futures, atm_iv, t * 365.0, lot_size, q=r)
    chain["greeks"] = [
        {
            "strike": _strike_value(row["Strike"]),
            "iv": nullable_rounded_number(row["ce_iv"] * 100, 2),
            "cDelta": nullable_rounded_number(row["cDelta"], 4),
            "cGamma": nullable_rounded_number(row["cGamma"], 6),
            "cTheta": nullable_rounded_number(row["cTheta"], 4),
            "cVega": nullable_rounded_number(row["cVega"], 4),
            "pDelta": nullable_rounded_number(row["pDelta"], 4),
            "pGamma": nullable_rounded_number(row["pGamma"], 6),
            "pTheta": nullable_rounded_number(row["pTheta"], 4),
            "pVega": nullable_rounded_number(row["pVega"], 4),
            "netGEX": nullable_rounded_number(row["netGEX"], 4),
        }
        for row in table.to_dict("records")
    ]
    return chain
