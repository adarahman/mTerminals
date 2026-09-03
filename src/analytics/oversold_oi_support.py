"""Bounded oversold + OI-support confirmation for the Decision Engine."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from math import isfinite

import pandas as pd

RSI_PERIOD = 14
RSI_OVERSOLD = 30.0
_minute_closes: dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=120))


def _minute(timestamp: str) -> int | None:
    try:
        return int(datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).timestamp() // 60)
    except (TypeError, ValueError):
        return None


def update_spot_rsi(symbol: str, spot: float, timestamp: str) -> float | None:
    """Update one close per minute and return a 14-period RSI when ready."""
    if not symbol or not isfinite(spot) or spot <= 0:
        return None
    bucket = _minute(timestamp)
    if bucket is None:
        return None
    series = _minute_closes[symbol]
    if series and bucket < series[-1][0]:
        series.clear()
    if series and bucket == series[-1][0]:
        series[-1] = (bucket, spot)
    else:
        series.append((bucket, spot))
    if len(series) < RSI_PERIOD + 1:
        return None
    closes = [value for _, value in list(series)[-(RSI_PERIOD + 1):]]
    changes = [right - left for left, right in zip(closes, closes[1:])]
    gains = sum(max(change, 0.0) for change in changes) / RSI_PERIOD
    losses = sum(max(-change, 0.0) for change in changes) / RSI_PERIOD
    if losses == 0:
        return 100.0
    if gains == 0:
        return 0.0
    return round(100.0 - (100.0 / (1.0 + gains / losses)), 2)


def evaluate_oversold_oi_support(*, rsi: float | None, spot: float, pe_wall: float,
                                 master: pd.DataFrame | None, fut_signal: str,
                                 strike_step: float) -> dict:
    """Return one auditable state; never infer writing from OI change alone."""
    base = {"state": "unavailable", "score": 0.0, "rsi": rsi, "evidence": []}
    if rsi is None:
        base["detail"] = "Waiting for 15 one-minute spot closes"
        return base

    support_holding = pe_wall > 0 and spot >= pe_wall
    wall_broken = pe_wall > 0 and spot < pe_wall
    pe_signal = ""
    if master is not None and not master.empty and "strike" in master.columns:
        candidates = master.copy()
        candidates["_distance"] = (pd.to_numeric(candidates["strike"], errors="coerce") - pe_wall).abs()
        row = candidates.loc[candidates["_distance"].idxmin()]
        if float(row["_distance"]) <= max(float(strike_step), 1.0):
            pe_signal = str(row.get("pe_signal", ""))

    pe_writing = "writing" in pe_signal.lower()
    put_buying = "buying" in pe_signal.lower()
    short_covering = "short covering" in str(fut_signal).lower()
    evidence = []
    if pe_writing: evidence.append("PE writing near support")
    if short_covering: evidence.append("Futures short covering")
    if support_holding: evidence.append("PE wall holding")

    result = {**base, "evidence": evidence, "peSignal": pe_signal}
    if rsi <= RSI_OVERSOLD and (wall_broken or put_buying):
        result.update(state="invalidated", detail=(
            "PE wall broken" if wall_broken else "Put buying detected near PE support"
        ))
    elif rsi <= RSI_OVERSOLD and support_holding and (pe_writing or short_covering):
        result.update(state="confirmed", score=1.0, detail="Oversold price confirmed by OI support")
    elif rsi <= RSI_OVERSOLD:
        result.update(state="unconfirmed", detail="Oversold, but OI support is not confirmed")
    else:
        result.update(state="unconfirmed", detail="RSI is not oversold")
    return result


def reset_spot_rsi_history() -> None:
    """Test/session reset hook."""
    _minute_closes.clear()
