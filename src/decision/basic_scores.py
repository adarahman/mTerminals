"""Reusable direction scores for option positioning and market bias."""

from __future__ import annotations

from decision.types import T


def score_pcr(pcr: float) -> float:
    """
    PCR = PE_OI / CE_OI
    HIGH → put writers dominant → bulls expect floor to hold → BULLISH (+)
    LOW  → call writers dominant → bears cap upside → BEARISH (-)
    """
    if pcr >= T.PCR_BULL_EXTREME:
        return 1.0
    if pcr >= T.PCR_BULL:
        return 0.65
    if pcr <= T.PCR_BEAR_EXTREME:
        return -1.0
    if pcr <= T.PCR_BEAR:
        return -0.65
    return (pcr - 1.0) / (T.PCR_NEUTRAL_HI - 1.0) * 0.35


def score_engine_bias(bias_str: str) -> float:
    bias = bias_str.lower()
    if "strong bull" in bias:
        return 1.0
    if "bull" in bias:
        return 0.60
    if "strong bear" in bias:
        return -1.0
    if "bear" in bias:
        return -0.60
    return 0.0
