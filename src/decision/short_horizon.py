"""
Short-horizon move detector.

This module is an observation layer for the existing DecisionEngine.
It does NOT alter the existing composite decision, confidence, action,
strategy selection, or execution recommendation.

Horizon: approximately 2–3 minutes.

Price history is intentionally read from the existing minute-level spot
history maintained by analytics.oversold_oi_support; no second price-history
store is created here.
"""

from __future__ import annotations

from typing import Any

from analytics.oversold_oi_support import recent_spot_closes


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100.0


def _direction_from_score(score: float, threshold: float = 0.15) -> str:
    if score >= threshold:
        return "UP"
    if score <= -threshold:
        return "DOWN"
    return "NEUTRAL"


def detect_short_horizon(
    symbol: str,
    *,
    oi_score: float = 0.0,
    fut_score: float = 0.0,
    fut_ltp: float = 0.0,
    fut_vwap: float = 0.0,
) -> dict[str, Any]:
    """
    Detect short-horizon directional pressure from existing temporal data.

    The returned probability is an evidence score, NOT a statistically
    calibrated probability. Calibration should only be introduced after
    backtest validation.
    """
    closes = recent_spot_closes(symbol, max_points=8)

    base: dict[str, Any] = {
        "horizon": "2-3m",
        "state": "NEUTRAL",
        "direction": "NEUTRAL",
        "probability": 0,
        "probabilityCalibrated": False,
        "score": 0.0,
        "price": {
            "move1mPct": 0.0,
            "move2mPct": 0.0,
            "move3mPct": 0.0,
            "velocityPctPerMin": 0.0,
            "accelerationPctPerMin2": 0.0,
        },
        "oi": {
            "direction": _direction_from_score(float(oi_score)),
            "confirmed": abs(float(oi_score)) >= 0.15,
            "score": round(float(oi_score), 4),
        },
        "futures": {
            "direction": _direction_from_score(float(fut_score)),
            "confirmed": abs(float(fut_score)) >= 0.15,
            "score": round(float(fut_score), 4),
            "ltp": round(float(fut_ltp), 4) if fut_ltp else 0.0,
            "vwap": round(float(fut_vwap), 4) if fut_vwap else 0.0,
            "priceVsVwapPct": 0.0,
        },
        "compression": {
            "active": False,
            "expanding": False,
            "range3mPct": 0.0,
            "previousRange3mPct": 0.0,
        },
        "evidence": [],
    }

    # Four closes are the minimum required for 1m/2m/3m measurements.
    if len(closes) < 4:
        base["evidence"] = ["Insufficient 1-minute history"]
        return base

    prices = [float(price) for _, price in closes]
    latest = prices[-1]

    move1 = _pct_change(prices[-1], prices[-2])
    move2 = _pct_change(prices[-1], prices[-3])
    move3 = _pct_change(prices[-1], prices[-4])

    velocity_now = move1
    velocity_prev = _pct_change(prices[-2], prices[-3])
    acceleration = velocity_now - velocity_prev

    base["price"] = {
        "move1mPct": round(move1, 4),
        "move2mPct": round(move2, 4),
        "move3mPct": round(move3, 4),
        "velocityPctPerMin": round(velocity_now, 4),
        "accelerationPctPerMin2": round(acceleration, 4),
    }

    # Normalised price impulse. The threshold is deliberately conservative:
    # approximately 0.10% over a minute represents meaningful short-term
    # movement for an index while still allowing smaller moves to accumulate.
    price_score = _clamp(move1 / 0.10)

    if move2 != 0 and move3 != 0:
        if (move1 > 0 and move2 > 0 and move3 > 0):
            price_score = _clamp(price_score + 0.15)
        elif (move1 < 0 and move2 < 0 and move3 < 0):
            price_score = _clamp(price_score - 0.15)

    # Acceleration is only directional when current velocity is already
    # meaningful. This prevents a near-zero price move followed by a large
    # return change from manufacturing directional pressure.
    if abs(velocity_now) >= 0.03 and acceleration != 0:
        same_direction = (
            (velocity_now > 0 and acceleration > 0)
            or (velocity_now < 0 and acceleration < 0)
        )
        if same_direction:
            price_score = _clamp(
                price_score + (0.20 if velocity_now > 0 else -0.20)
            )

    # Compression / expansion using the existing minute observations.
    recent = prices[-4:]
    current_range_pct = (
        ((max(recent) - min(recent)) / latest) * 100.0
        if latest
        else 0.0
    )

    compression_active = False
    expanding = False
    previous_range_pct = 0.0

    if len(prices) >= 7:
        previous = prices[-7:-4]
        previous_mid = sum(previous) / len(previous)
        if previous_mid:
            previous_range_pct = (
                (max(previous) - min(previous)) / previous_mid
            ) * 100.0

            compression_active = previous_range_pct <= 0.12
            expanding = current_range_pct > previous_range_pct * 1.35

    base["compression"] = {
        "active": compression_active,
        "expanding": expanding,
        "range3mPct": round(current_range_pct, 4),
        "previousRange3mPct": round(previous_range_pct, 4),
    }

    # Existing OI/futures scorers are confirmations, not a second decision
    # engine. They receive smaller influence than the actual price impulse.
    oi = _clamp(float(oi_score))
    futures = _clamp(float(fut_score))

    score = _clamp(
        (price_score * 0.70)
        + (oi * 0.20)
        + (futures * 0.10)
    )

    direction = _direction_from_score(score)

    evidence: list[str] = []

    if abs(move3) >= 0.10:
        evidence.append("3m price impulse detected")

    if abs(acceleration) >= 0.03:
        evidence.append(
            "Upside acceleration" if acceleration > 0
            else "Downside acceleration"
        )

    if oi >= 0.15:
        evidence.append("5m OI velocity confirms upside")
    elif oi <= -0.15:
        evidence.append("5m OI velocity confirms downside")

    if futures >= 0.15:
        evidence.append("Futures signal confirms upside")
    elif futures <= -0.15:
        evidence.append("Futures signal confirms downside")

    price_vs_vwap_pct = 0.0
    if fut_ltp and fut_vwap:
        price_vs_vwap_pct = ((float(fut_ltp) - float(fut_vwap)) / float(fut_vwap)) * 100.0
        base["futures"]["priceVsVwapPct"] = round(price_vs_vwap_pct, 4)

        if price_vs_vwap_pct >= 0.05:
            evidence.append("Futures trading above VWAP")
        elif price_vs_vwap_pct <= -0.05:
            evidence.append("Futures trading below VWAP")

    if compression_active and expanding:
        evidence.append("Compression followed by range expansion")

    abs_score = abs(score)

    if abs_score >= 0.75 and abs(acceleration) >= 0.03:
        state = "MOVE"
    elif abs_score >= 0.55 and compression_active and expanding:
        state = "BREAKOUT_TRIGGER"
    elif abs_score >= 0.35:
        state = "PRESSURE_BUILDING"
    else:
        state = "NEUTRAL"

    # This is deliberately an evidence-normalisation score rather than a
    # calibrated probability. Cap below 100 until historical validation exists.
    probability = min(95, round(abs_score * 100))

    base.update(
        {
            "state": state,
            "direction": direction,
            "probability": probability,
            "score": round(score, 4),
            "evidence": evidence or ["No strong short-horizon pressure"],
        }
    )

    return base
